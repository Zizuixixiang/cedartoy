"""Primary-mode fault injection: no credentials, sockets, or production data."""
import base64
import io
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from PIL import Image
from shared_craft_adapter import object_storage as objects
from shared_craft_adapter import primary
from shared_craft_adapter.service import create_app
from scripts import shared_craft_r2 as phase1
from scripts import shared_craft_r2_primary as migration
from tests_toy import test_shared_craft, test_shared_craft_framer, test_shared_craft_sharing
from tests_toy.test_shared_craft_r2 import FakeS3
from tests_toy.test_shared_craft_sharing import seed_legacy


def png(color='red'):
    stream = io.BytesIO()
    Image.new('RGBA', (128, 128), color).save(stream, format='PNG')
    return stream.getvalue()


def data_url(raw):
    return 'data:image/png;base64,' + base64.b64encode(raw).decode()


def encoded_image(fmt, color='red', size=(32, 32)):
    stream = io.BytesIO()
    Image.new('RGB', size, color).save(stream, format=fmt)
    return stream.getvalue()


class ReferenceDecoderTests(unittest.TestCase):
    def test_supported_mislabeled_images_preserve_bytes_and_detect_actual_mime(self):
        for mime, fmt in primary.MIMES.items():
            raw = encoded_image(fmt)
            for declared in primary.MIMES:
                with self.subTest(actual=mime, declared=declared):
                    url = f'data:{declared};base64,' + base64.b64encode(raw).decode()
                    self.assertEqual(primary.decode_data_url(url), (mime, raw))

    def test_invalid_base64_images_formats_and_limits_still_rejected(self):
        oversized = io.BytesIO()
        Image.new('1', (4097, 4097)).save(oversized, format='PNG')
        for bad in (data_url(b'not an image'), data_url(encoded_image('BMP')),
                    data_url(encoded_image('TIFF')), data_url(png()).replace('image/png', 'image/svg+xml'),
                    data_url(png()).replace('image/png', 'application/octet-stream'),
                    data_url(png()) + '\n', 'data:image/png;base64,%%%',
                    data_url(png()[:-20]), data_url(oversized.getvalue()),
                    'data:image/png;base64,' + 'A' * 1_500_000):
            with self.assertRaises(ValueError):
                primary.decode_data_url(bad)


class PrimaryTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeS3()
        self.from_env = objects.Images.from_env
        self.images = []
        def factory(root, env=None):
            images = objects.Images(root, 'primary', self.backend)
            self.images.append(images)
            return images
        patcher = patch.object(objects.Images, 'from_env', side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = test_shared_craft.SharedCraftTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(lambda: [images.close() for images in self.images])
        self.root = self.f.root / 'craft'

    def inventory(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and p.suffix != '.db'}

    def test_gallery_shares_restart_permissions_delete_and_no_full_local_image(self):
        f = self.f
        f.draw(2)
        item = f.gallery()
        route = 'api/gallery/' + item['id'] + '.png'
        raw = f.request(route).content
        self.assertTrue(raw.startswith(b'\x89PNG'))
        sid = f.play('share_work', 2, item_id=item['id'])['id']
        for path in self.root.rglob('*.png'):
            self.assertEqual(path.stat().st_size, 75)
            self.assertTrue(objects.reference_sha(path.read_bytes()))
        with sqlite3.connect(f.app.state.shares.path) as conn:
            marker = conn.execute('SELECT image FROM submissions').fetchone()[0]
            self.assertEqual(len(marker), 75)
        f.grant()
        restarted = create_app(self.root, f.accounts, f.grants, 'test-secret-' * 4)
        self.assertEqual(f.request(route, app=restarted).content, raw)
        self.assertEqual(f.request('author/' + sid, uid=6, app=restarted).content, raw)
        before = len(self.backend.calls)
        for uid in (1, 2, 3, 4, 5):
            self.assertEqual(f.request('author/' + sid, uid=uid).status_code, 403)
        self.assertEqual(f.request(route, uid=4).status_code, 404)
        self.assertEqual(len(self.backend.calls), before)
        f.request('api/gallery/' + item['id'], 'DELETE')
        self.assertFalse(list((self.root / 'rooms/1/gallery').glob('*.png')))
        self.assertEqual(f.request('author/' + sid, uid=6).content, raw)
        self.assertEqual(len(self.backend.objects), 2)

    def test_new_save_share_and_reference_fail_closed_and_saturation(self):
        f = self.f
        f.draw()
        before = self.inventory()
        for corrupt in (False, True):
            self.backend.fail, self.backend.corrupt = not corrupt, corrupt
            response = f.request('api/gallery', 'POST', {'kind': 'sketch'})
            self.assertEqual(response.status_code, 503, response.text)
            self.assertIn('R2', response.json()['error'])
            self.assertEqual(self.inventory(), before)
        self.backend.fail = self.backend.corrupt = False
        # Corrupt attempted objects stay untouched; new gallery IDs get fresh keys.
        item = f.gallery()
        self.backend.fail = True
        response = f.request('api/shares', 'POST', {'item_id': item['id']})
        self.assertEqual(response.status_code, 503)
        with sqlite3.connect(f.app.state.shares.path) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM submissions').fetchone()[0], 0)
        self.backend.fail = False
        slots = self.images[0].primary_slots
        for _ in range(8):
            slots.acquire()
        try:
            response = f.request('api/gallery', 'POST', {'kind': 'sketch'})
            self.assertEqual(response.status_code, 503)
            self.assertIn('繁忙', response.json()['error'])
        finally:
            for _ in range(8):
                slots.release()
        self.assertEqual(len(f.app.state.rooms['1'].gallery.list_items()), 1)
        self.images[0].backend = None
        self.assertEqual(f.request('api/gallery', 'POST', {'kind': 'sketch'}).status_code, 503)
        with patch.object(objects.S3, 'from_env', side_effect=OSError('secret')):
            with self.assertRaises(objects.StorageUnavailable):
                self.from_env(self.root, {'CRAFT_R2_MODE': 'primary'})

    def test_reference_upload_generated_plan_permissions_size_and_restart(self):
        f = self.f
        bid = f.play('new_bead_board', 2, title='gift', mode='gift', size=8)['id']
        route = 'api/bead-boards/' + bid
        url = data_url(png())
        response = f.request(route, 'POST', {'action': 'reference', 'data_url': url}, 2)
        self.assertEqual(response.status_code, 200, response.text)
        path = self.root / 'rooms/1/beads.json'
        value = json.loads(path.read_bytes())['boards'][0]['reference_image']
        self.assertIsInstance(value, dict)
        self.assertNotIn('base64', path.read_text())
        before = len(self.backend.calls)
        self.assertEqual(f.request(route, uid=3).status_code, 400)
        self.assertEqual(f.request('api/bead-boards', uid=3).json()['boards'], [])
        self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(f.request(route, uid=2).json()['board']['reference_image'], url)
        response = f.request('play', 'POST', {'action': 'open_bead_board', 'params': {'board_id': bid}}, 2)
        self.assertEqual(base64.b64decode(response.json()['content'][1]['data']), png())
        before = path.read_bytes()
        for bad in ('data:image/svg+xml;base64,AAAA', data_url(b'bad'), 'x'*1_500_001):
            self.assertEqual(f.request(route, 'POST', {'action': 'reference', 'data_url': bad}, 2).status_code, 400)
            self.assertEqual(path.read_bytes(), before)
        self.backend.fail = True
        self.assertEqual(f.request(route, 'POST', {'action': 'reference', 'data_url': data_url(png('blue'))}, 2).status_code, 503)
        self.assertEqual(path.read_bytes(), before)
        self.backend.fail = False
        f.play('set_bead_work_plan', 2, board_id=bid, beads=[{'x': 1, 'y': 1, 'color': 'F14'}])
        self.assertNotIn('base64', path.read_text())
        restarted = create_app(self.root, f.accounts, f.grants, 'test-secret-' * 4)
        self.assertTrue(f.request(route, uid=2, app=restarted).json()['board']['reference_image'].startswith('data:image/png;base64,'))
        self.backend.fail = True
        self.assertEqual(f.request(route, uid=2).status_code, 503)

    def test_decoration_second_upload_failure_preserves_both_old_images_and_metadata(self):
        f = self.f
        bid = f.play('new_bead_board', mode='together', size=8, title='test')['id']
        room = f.app.state.rooms['1']
        data = room.beads._load()
        data['boards'][0]['craft']['stage'] = 'finished'
        room.beads._save(data)
        room.beads.save_decoration('1', bid, data_url(png()), data_url(png('blue')))
        before = self.inventory()
        original_put = self.backend.put
        def failing_put(key, raw):
            if '-piece/' in key:
                raise OSError('offline')
            original_put(key, raw)
        with patch.object(self.backend, 'put', side_effect=failing_put):
            with self.assertRaises(objects.StorageUnavailable):
                room.beads.save_decoration('1', bid, data_url(png('green')), data_url(png('yellow')))
        self.assertEqual(self.inventory(), before)
        self.assertEqual(room.beads.decorated_bytes('1', bid), png())
        self.assertEqual(room.beads.display_piece_bytes('1', bid), png('blue'))
        room.beads.save_decoration('1', bid, data_url(png('green')), data_url(png('yellow')))
        self.assertEqual(room.beads.decorated_bytes('1', bid), png('green'))
        self.assertTrue(all(p.stat().st_size == 75 for p in self.root.rglob('*.png')))

    def test_upstream_upload_action_uses_primary_reference_permissions_and_failure_guards(self):
        f = self.f
        bid = f.play('new_bead_board', 2, title='upload', mode='gift', size=8)['id']
        room = f.app.state.rooms['1']
        path = self.root / 'rooms/1/beads.json'
        before, calls = path.read_bytes(), len(self.backend.calls)
        payload = {'action': 'upload_bead_reference', 'params': {'board_id': bid, 'data_url': data_url(png())}}
        if 'upload_bead_reference' not in {tool['name'] for tool in room.ns['TOOLS']}:
            # The old vendor must not silently gain a new action from the Guide.
            self.assertEqual(f.request('play', 'POST', payload, 2).status_code, 400)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(self.backend.calls), calls)
            return
        for uid in (1, 3):
            self.assertEqual(f.request('play', 'POST', payload, uid).status_code, 400)
            self.assertEqual(path.read_bytes(), before)
            self.assertEqual(len(self.backend.calls), calls)
        for mime, fmt in (('image/png', 'PNG'), ('image/jpeg', 'JPEG'), ('image/webp', 'WEBP')):
            for declared in dict.fromkeys((mime, 'image/png')):
                raw = encoded_image(fmt)
                url = f'data:{declared};base64,' + base64.b64encode(raw).decode()
                response = f.request('play', 'POST', {**payload, 'params': {'board_id': bid, 'data_url': url}}, 2)
                self.assertEqual(response.status_code, 200, response.text)
                record = json.loads(path.read_bytes())['boards'][0]['reference_image']
                self.assertEqual(record['mime'], mime)
                self.assertEqual(record['sha256'], objects.digest(raw))
                key = objects.object_key('1', 'references', bid, record['sha256'])
                self.assertEqual(self.backend.objects[key], raw)
                self.assertNotIn('base64', path.read_text())
                opened = f.request('play', 'POST', {'action': 'open_bead_board', 'params': {'board_id': bid}}, 2)
                self.assertEqual(opened.json()['content'][1]['mimeType'], mime)
                self.assertEqual(base64.b64decode(opened.json()['content'][1]['data']), raw)
                restored = f.request('api/bead-boards/' + bid, uid=2).json()['board']['reference_image']
                self.assertEqual(restored, f'data:{mime};base64,' + base64.b64encode(raw).decode())
        before, calls = path.read_bytes(), len(self.backend.calls)
        self.assertEqual(f.request('api/bead-boards/' + bid, uid=3).status_code, 400)
        self.assertEqual(len(self.backend.calls), calls)
        payload['params']['data_url'] = data_url(encoded_image('JPEG', 'blue'))
        for fault in ('fail', 'corrupt'):
            setattr(self.backend, fault, True)
            self.assertEqual(f.request('play', 'POST', payload, 2).status_code, 503)
            self.assertEqual(path.read_bytes(), before)
            setattr(self.backend, fault, False)
        self.backend.fail = True
        plan = {'action': 'pin_bead_reference', 'params': {'board_id': bid, 'beads': [{'x': 0, 'y': 0, 'color': 'F14'}]}}
        self.assertEqual(f.request('play', 'POST', plan, 2).status_code, 503)
        self.assertEqual(path.read_bytes(), before)

    def test_primary_references_survive_a_fresh_process_and_fail_closed_when_off(self):
        f = self.f
        f.draw(2)
        item = f.gallery()
        sid = f.play('share_work', 2, item_id=item['id'])['id']
        f.grant()
        bid = f.play('new_bead_board', mode='together', title='ref', size=8)['id']
        f.request('api/bead-boards/' + bid, 'POST', {'action': 'reference', 'data_url': data_url(png())})
        disk = f.root / 'fake-r2'
        disk.mkdir()
        for key, raw in self.backend.objects.items():
            (disk / objects.digest(key.encode())).write_bytes(raw)
        script = r"""
import hashlib, json, sys
from pathlib import Path
from shared_craft_adapter.object_storage import Images
from shared_craft_adapter.service import create_app
from tests_toy.test_shared_craft import SharedCraftTests
root, item, sid, bid = Path(sys.argv[1]), *sys.argv[2:]
class DiskS3:
    def get(self, key):
        return (root / 'fake-r2' / hashlib.sha256(key.encode()).hexdigest()).read_bytes()
f = SharedCraftTests()
f.app = create_app(root / 'craft', root / 'accounts.db', root / 'sessions.db',
                   'test-secret-' * 4, images=Images(root / 'craft', 'primary', DiskS3()))
responses = [f.request('api/gallery/' + item + '.png'), f.request('author/' + sid, uid=6)]
assert all(r.status_code == 200 for r in responses)
assert responses[0].content == responses[1].content
assert f.request('api/bead-boards/' + bid).json()['board']['reference_image'].startswith('data:image/png;base64,')
print(hashlib.sha256(responses[0].content).hexdigest())
"""
        result = subprocess.run([sys.executable, '-c', script, str(f.root), item['id'], sid, bid],
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = f.request('api/gallery/' + item['id'] + '.png').content
        self.assertEqual(result.stdout.strip(), objects.digest(expected))
        before = self.inventory()
        self.images[0].mode = 'off'
        response = f.request('api/gallery/' + item['id'] + '.png')
        self.assertEqual(response.status_code, 503)
        self.assertEqual(before, self.inventory())
        self.images[0].mode = 'primary'
        key = next(key for key in self.backend.objects if '/gallery/' in key)
        self.backend.objects[key] = b'bad hash'
        self.assertEqual(f.request('api/gallery/' + item['id'] + '.png').status_code, 503)
        self.assertEqual(before, self.inventory())

    def test_publication_interruption_recovers_on_restart(self):
        f = self.f
        f.draw()
        original = primary.atomic_bytes
        def interrupted(path, raw):
            if Path(path).suffix == '.png':
                raise KeyboardInterrupt()
            original(path, raw)
        with patch.object(primary, 'atomic_bytes', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                f.gallery()
        journal = self.root / 'rooms/1/.r2-commit.json'
        self.assertTrue(journal.exists())
        self.assertNotIn('base64', journal.read_text())
        restarted = create_app(self.root, f.accounts, f.grants, 'test-secret-' * 4)
        result = f.request('api/gallery', app=restarted)
        self.assertEqual(result.status_code, 200, result.text)
        item = result.json()['items'][0]
        self.assertTrue(f.request('api/gallery/' + item['id'] + '.png', app=restarted).content.startswith(b'\x89PNG'))
        self.assertFalse(journal.exists())

    def test_existing_lifecycle_and_sharing_suite_under_primary(self):
        suite = unittest.TestSuite([
            test_shared_craft_framer.SharedCraftFramerTests('test_finished_decoration_and_gallery_survive_app_restart'),
            *unittest.defaultTestLoader.loadTestsFromTestCase(test_shared_craft_sharing.SharedCraftSharingTests),
        ])
        result = unittest.TestResult()
        suite.run(result)
        self.assertEqual(result.errors + result.failures, [])


class PrimaryMigrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.dict('os.environ', {'CRAFT_R2_MODE': 'off'})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = test_shared_craft.SharedCraftTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        f = self.f
        self.root = f.root / 'craft'
        self.backend = FakeS3()
        self.manifest = f.root / 'primary.jsonl'
        f.draw(2)
        self.item = f.gallery()
        self.sids = [seed_legacy(f, self.item, status, status) for status in ('pending', 'shared', 'declined')]
        bid = f.play('new_bead_board', 2, mode='gift', size=8, title='ref')['id']
        f.request('api/bead-boards/' + bid, 'POST', {'action': 'reference', 'data_url': data_url(png())}, 2)
        self.bid = bid
        folder = self.root / 'rooms/1/shared-bead-finished'
        folder.mkdir()
        (folder / 'abc.png').write_bytes(png('blue'))
        (folder / 'abc-piece.png').write_bytes(png('green'))
        self.backup = f.root / 'backup'
        phase1.snapshot(self.root, self.backup)

    def migrate(self, **kwargs):
        return migration.migrate(self.root, self.manifest, allowed_root=self.root,
                                 backup=self.backup, backend=self.backend, **kwargs)

    def rows(self):
        with sqlite3.connect(self.root / 'shares.db') as conn:
            return conn.execute('SELECT * FROM submissions ORDER BY id').fetchall()

    def inventory(self):
        return {str(p.relative_to(self.root)): p.read_bytes()
                for p in self.root.rglob('*') if p.is_file() and p.suffix != '.db'}

    def test_mislabeled_legacy_references_dry_run_faults_migrate_and_restore(self):
        f = self.f
        second = f.play('new_bead_board', 2, mode='gift', size=8, title='webp')['id']
        originals = {self.bid: ('image/jpeg', encoded_image('JPEG')),
                     second: ('image/webp', encoded_image('WEBP'))}
        for bid, (_, raw) in originals.items():
            response = f.request('api/bead-boards/' + bid, 'POST',
                                 {'action': 'reference', 'data_url': data_url(raw)}, 2)
            self.assertEqual(response.status_code, 200)
        self.backup = f.root / 'mislabeled-backup'
        phase1.snapshot(self.root, self.backup)
        files, rows = self.inventory(), self.rows()
        counts = self.migrate()
        self.assertEqual((counts['planned'], counts['failed']), (8, 0))
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.rows(), rows)
        for fault in ('fail', 'corrupt'):
            setattr(self.backend, fault, True)
            self.assertEqual(self.migrate(apply=True)['failed'], 8)
            self.assertEqual(self.inventory(), files)
            self.assertEqual(self.rows(), rows)
            setattr(self.backend, fault, False)
        # Only discard corrupt objects in this disposable fake backend.
        self.backend.objects.clear()
        counts = self.migrate(apply=True)
        self.assertEqual((counts['converted'], counts['failed']), (8, 0))
        self.assertEqual(self.migrate(verify_only=True)['verified'], 8)
        self.assertEqual(self.migrate(apply=True)['unchanged'], 8)
        path = self.root / 'rooms/1/beads.json'
        records = {b['id']: b['reference_image'] for b in json.loads(path.read_bytes())['boards']}
        images = objects.Images(self.root, 'primary', self.backend)
        self.addCleanup(images.close)
        app = create_app(self.root, f.accounts, f.grants, 'test-secret-' * 4, images=images)
        for bid, (mime, raw) in originals.items():
            record = records[bid]
            self.assertEqual(record, primary.ref_record(mime, raw))
            key = objects.object_key('1', 'references', bid, record['sha256'])
            self.assertEqual(self.backend.objects[key], raw)
            calls = len(self.backend.calls)
            self.assertEqual(f.request('api/bead-boards/' + bid, uid=3, app=app).status_code, 400)
            self.assertEqual(len(self.backend.calls), calls)
            restored = f.request('api/bead-boards/' + bid, uid=2, app=app).json()['board']['reference_image']
            self.assertEqual(restored, f'data:{mime};base64,' + base64.b64encode(raw).decode())
        self.assertEqual(self.migrate(restore=True, apply=True)['converted'], 8)
        restored = {b['id']: b['reference_image'] for b in json.loads(path.read_bytes())['boards']}
        for bid, (mime, raw) in originals.items():
            self.assertEqual(restored[bid], f'data:{mime};base64,' + base64.b64encode(raw).decode())
        self.assertEqual(self.rows(), rows)

    def test_dry_run_all_legacy_convert_verify_resume_restore_and_phase1_backfill(self):
        files, rows = self.inventory(), self.rows()
        self.assertEqual(self.migrate()['planned'], 7)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.rows(), rows)
        counts = self.migrate(apply=True, compact_db=True)
        self.assertEqual(counts['converted'], 7)
        self.assertEqual(counts['failed'], 0)
        self.assertLess(counts['committed_after_bytes'], counts['source_bytes'])
        for row in self.rows():
            old = next(r for r in rows if r[0] == row[0])
            self.assertEqual(row[:6] + row[7:], old[:6] + old[7:])
            self.assertEqual(len(row[6]), 75)
        self.assertEqual(self.migrate(apply=True)['unchanged'], 7)
        self.assertEqual(self.migrate(verify_only=True)['verified'], 7)
        before = self.inventory()
        counts = phase1.migrate(self.root, self.f.root / 'phase1.jsonl', apply=True, backend=self.backend)
        self.assertEqual(counts['existing'], 6)
        self.assertEqual(self.inventory(), before)
        self.f.grant()
        images = objects.Images(self.root, 'primary', self.backend)
        self.addCleanup(images.close)
        app = create_app(self.root, self.f.accounts, self.f.grants, 'test-secret-' * 4, images=images)
        raw = next(row[6] for row in rows if row[0] == self.sids[1])
        self.assertEqual(self.f.request('author/' + self.sids[1], uid=6, app=app).content, raw)
        before_calls = len(self.backend.calls)
        self.assertEqual(self.f.request('author/' + self.sids[0], uid=6, app=app).status_code, 403)
        self.assertEqual(self.f.request('api/bead-boards/' + self.bid, uid=3, app=app).status_code, 400)
        self.assertEqual(len(self.backend.calls), before_calls)
        self.assertEqual(self.f.request('api/bead-boards/' + self.bid, uid=2, app=app).json()['board']['reference_image'], data_url(png()))
        self.assertEqual(self.migrate(restore=True, apply=True)['converted'], 7)
        self.assertEqual(self.rows(), rows)
        for path, raw in files.items():
            if path.endswith('beads.json'):
                self.assertEqual(json.loads((self.root / path).read_bytes()), json.loads(raw))
            else:
                self.assertEqual((self.root / path).read_bytes(), raw)
        self.assertEqual(self.migrate(restore=True, apply=True)['unchanged'], 7)
        # No R2 key is removed during either direction.
        self.assertEqual(len(self.backend.objects), 7)

    def test_faults_retain_originals_and_interruption_resume(self):
        files, rows = self.inventory(), self.rows()
        self.backend.fail = True
        self.assertEqual(self.migrate(apply=True)['failed'], 7)
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.rows(), rows)
        self.backend.fail = False
        self.backend.corrupt = True
        self.assertEqual(self.migrate(apply=True)['failed'], 7)
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.rows(), rows)
        # Isolated fake damage is repaired here; production script never overwrites it.
        self.backend.corrupt = False
        self.backend.objects.clear()
        original_put = self.backend.put
        def interrupted(key, raw):
            original_put(key, raw)
            raise KeyboardInterrupt()
        with patch.object(self.backend, 'put', side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.migrate(apply=True)
        self.assertEqual(self.inventory(), files)
        self.assertEqual(self.migrate(apply=True)['converted'], 7)
        key = next(iter(self.backend.objects))
        self.backend.objects[key] = b'corrupt'
        self.assertEqual(self.migrate(verify_only=True)['failed'], 1)
        before = self.inventory()
        self.assertEqual(self.migrate(restore=True, apply=True)['failed'], 1)
        path = next(p for p in self.root.rglob('*.png') if p.read_bytes().startswith(objects.REF))
        self.assertEqual(path.read_bytes(), before[str(path.relative_to(self.root))])

    def test_canary_never_rewrites_other_room_and_stale_snapshot_preserves_new_images(self):
        f = self.f
        f.draw(4)
        item = f.gallery(4)
        path = self.root / 'rooms/4/gallery' / (item['id'] + '.png')
        raw = path.read_bytes()
        self.assertEqual(self.migrate(apply=True, rooms={'1'})['converted'], 7)
        self.assertEqual(path.read_bytes(), raw)
        self.assertTrue(all('/rooms/1/' in key for key in self.backend.objects))
        counts = self.migrate(apply=True)
        self.assertEqual(counts['failed'], 1, 'new original absent from backup must not be discarded')
        self.assertEqual(path.read_bytes(), raw)
        self.backup = f.root / 'fresh-backup'
        phase1.snapshot(self.root, self.backup)
        self.assertEqual(self.migrate(apply=True)['converted'], 1)
        self.assertEqual(path.stat().st_size, 75)

    def test_cli_dry_run_never_loads_credentials_or_truncates_unrelated_manifest(self):
        before, rows = self.inventory(), self.rows()
        with patch.object(migration, 'PRODUCTION_ROOT', self.root), \
                patch.object(objects.S3, 'from_env', side_effect=AssertionError('credentials forbidden')):
            self.assertEqual(migration.main(['--data-dir', str(self.root), '--manifest', str(self.manifest)]), 0)
        self.assertEqual(self.inventory(), before)
        self.assertEqual(self.rows(), rows)
        unrelated = self.f.root / 'not-a-manifest'
        unrelated.write_bytes(b'unrelated bytes without a newline')
        with self.assertRaises(ValueError):
            migration.migrate(self.root, unrelated, allowed_root=self.root)
        self.assertEqual(unrelated.read_bytes(), b'unrelated bytes without a newline')

    def test_scope_snapshot_and_symlink_guards(self):
        with self.assertRaises(ValueError):
            migration.migrate(self.root, self.manifest)
        with self.assertRaises(ValueError):
            migration.migrate(self.root, self.manifest, allowed_root=self.root, apply=True, backend=self.backend)
        (self.root / 'rooms/1/gallery/def.png').symlink_to(self.f.accounts)
        with self.assertRaises(ValueError):
            self.migrate(apply=True)
        self.assertTrue(all(method in {'GET', 'PUT'} for method, _ in self.backend.calls))


if __name__ == '__main__':
    unittest.main()
