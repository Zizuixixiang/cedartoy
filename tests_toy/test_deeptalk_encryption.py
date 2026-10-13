"""Real store/HTTP/MCP, disposable keys and databases only."""
import base64
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch

from deeptalk.catalog import DeeptalkError
from deeptalk.crypto import Cipher, KEY_ENV, PREFIX, STORAGE_ERROR, answer_context, digest_context
from deeptalk.migrate import migrate
from deeptalk.store import Store
from tests_toy import test_deeptalk as fixtures
from tests_toy.test_deeptalk import HUMAN, AI, OTHER_HUMAN, OTHER_AI, ROOT
import server


class EncryptionTests(unittest.TestCase):
    setUp = fixtures.DeeptalkTests.setUp
    connect = fixtures.DeeptalkTests.connect
    call = fixtures.DeeptalkTests.call
    http = fixtures.DeeptalkTests.http
    mcp = fixtures.DeeptalkTests.mcp

    def params(self, **updates):
        return dict(round_id=self.rid, question_id='q1', revision=0,
                    request_id='crypto_request_1', text='private-marker-敏感答案-9afec', **updates)

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return (dict(conn.execute('SELECT * FROM deeptalk_rounds WHERE id=?', (self.rid,)).fetchone()),
                    [dict(r) for r in conn.execute('SELECT * FROM deeptalk_requests ORDER BY request_key')])

    def legacy(self):
        """Reconstruct pre-encryption records without changing their SHA256 hashes."""
        row, requests = self.rows()
        cipher = Cipher()
        with sqlite3.connect(self.db) as conn:
            for role in ('human', 'ai'):
                value = cipher.decrypt(row[role + '_answers'], answer_context(row, role))
                conn.execute(f'UPDATE deeptalk_rounds SET {role}_answers=? WHERE id=?', (value, self.rid))
            for request in requests:
                value = cipher.decrypt(request['digest'], digest_context(row, request['role'], request['request_key']))
                conn.execute('UPDATE deeptalk_requests SET digest=? WHERE round_id=? AND role=? AND request_key=?',
                             (value, self.rid, request['role'], request['request_key']))

    def test_new_answers_and_receipts_never_reach_db_wal_or_journal_as_plaintext(self):
        for revision, journal in enumerate(('DELETE', 'WAL')):
            with self.subTest(journal=journal), sqlite3.connect(self.db) as keeper:
                keeper.execute('PRAGMA journal_mode=' + journal)
                keeper.execute('PRAGMA wal_autocheckpoint=0')
                if journal == 'WAL':
                    keeper.execute('BEGIN')
                    keeper.execute('SELECT COUNT(*) FROM deeptalk_rounds').fetchone()
                params = self.params() | {'revision': revision, 'request_id': f'disk_check_{journal}'}
                state = self.call(HUMAN, 'answer', **params)
                ai_params = params | {'text': 'ai-sensitive-marker-f0b83'}
                self.call(AI, 'answer', **ai_params)
                self.assertEqual(state['revision'], revision + 1)
                row, receipts = self.rows()
                for value in (row['human_answers'], row['ai_answers'], *(r['digest'] for r in receipts)):
                    self.assertTrue(value.startswith(PREFIX))
                digest = hashlib.sha256(json.dumps(['q1', {'text': params['text'], 'skipped': False}, revision],
                                                   ensure_ascii=False, sort_keys=True).encode()).hexdigest()
                found_wal = False
                for path in self.root.glob('sessions.db*'):
                    found_wal |= path.name.endswith('-wal')
                    data = path.read_bytes()
                    for marker in (params['text'], ai_params['text'], digest):
                        self.assertNotIn(marker.encode(), data)
                        self.assertNotIn(json.dumps(marker)[1:-1].encode(), data)
                if journal == 'WAL':
                    self.assertTrue(found_wal)
                self.assertEqual(self.call(HUMAN, 'question', round_id=self.rid)['questions'][0]['own']['text'], params['text'])

    def test_random_nonces_and_aad_reject_swaps_and_old_revisions(self):
        cipher = Cipher()
        row, _ = self.rows()
        context = answer_context(row, 'human')
        values = {cipher.encrypt('{}', context) for _ in range(100)}
        self.assertEqual(len(values), 100)
        nonces = {base64.b64decode(v[len(PREFIX):])[:12] for v in values}
        self.assertEqual(len(nonces), 100)
        value = values.pop()
        for change in ({'id': 'another-round'}, {'human_id': 102}, {'ai_id': 202}, {'human_revision': 1}):
            with self.subTest(change=change), self.assertRaises(DeeptalkError):
                cipher.decrypt(value, answer_context(row | change, 'human'))
        with self.assertRaises(DeeptalkError):
            cipher.decrypt(value, answer_context(row, 'ai'))
        receipt = cipher.encrypt('a' * 64, digest_context(row, 'human', 'request_1'))
        with self.assertRaises(DeeptalkError):
            cipher.decrypt(receipt, digest_context(row, 'human', 'request_2'))

    def test_missing_invalid_wrong_or_insecure_key_fails_without_changes(self):
        self.call(HUMAN, 'answer', **self.params())
        before = self.rows()
        bad = self.root / 'bad.key'
        for content, mode in ((b'short', 0o600), (os.urandom(32), 0o644), (os.urandom(32), 0o600)):
            bad.write_bytes(content); bad.chmod(mode)
            with patch.dict(os.environ, {KEY_ENV: str(bad)}):
                self.assert_storage_failure()
        with patch.dict(os.environ):
            os.environ.pop(KEY_ENV, None)
            self.assert_storage_failure()
            self.assertEqual(self.http(HUMAN, 'create', post=True, topic_id='relations_communication',
                                       request_id='missing_key_create')[0], 503)
            # History/source/discovery have no sensitive payload and keep working.
            self.assertEqual(self.call(HUMAN, 'history')['total'], 1)
            self.assertIn('topics', self.call(AI, 'topics'))
        self.assertEqual(self.rows(), before)

    def assert_storage_failure(self):
        status, body, headers = self.http(HUMAN, round_id=self.rid)
        self.assertEqual((status, body), (503, {'error': STORAGE_ERROR}))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        for path in (True, False):
            result = self.mcp(AI, 'question', round_id=self.rid, path=path)['result']
            self.assertTrue(result['isError'])
            self.assertIn(STORAGE_ERROR, result['content'][0]['text'])
        self.assertEqual(self.http(OTHER_HUMAN, round_id=self.rid)[0], 404)
        self.assertEqual(self.http(HUMAN, 'answer', post=True, **(self.params() | {
            'revision': 1, 'request_id': 'crypto_next_request'}))[0], 503)

    def test_corrupt_ciphertext_and_plaintext_fail_closed_and_roll_back(self):
        self.call(HUMAN, 'answer', **self.params())
        original, _ = self.rows()
        encoded = original['ai_answers']
        data = bytearray(base64.b64decode(encoded[len(PREFIX):])); data[-1] ^= 1
        for bad in ('{}', '{broken', 'dtenc:v2:AAAA', PREFIX + '!!', PREFIX + 'AAAA',
                    PREFIX + base64.b64encode(data).decode(), original['human_answers']):
            with self.subTest(value=bad[:12]):
                with sqlite3.connect(self.db) as conn:
                    conn.execute('UPDATE deeptalk_rounds SET ai_answers=? WHERE id=?', (bad, self.rid))
                before = self.rows()
                self.assert_storage_failure()  # Peer corruption also rolls back an attempted own edit.
                self.assertEqual(self.rows(), before)

    def test_receipt_corruption_never_overwrites_or_accepts_retry(self):
        params = self.params()
        self.call(HUMAN, 'answer', **params)
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE deeptalk_requests SET digest=?', ('0' * 64,))
        before = self.rows()
        self.assertEqual(self.http(HUMAN, 'answer', post=True, **params)[0], 503)
        self.assertEqual(self.rows(), before)

    def test_same_account_new_token_reads_strangers_cannot(self):
        params = self.params()
        self.mcp(AI, 'answer', **params)
        old_token = self.tokens[AI['id']]
        with self.connect() as conn:
            conn.execute('UPDATE toy_users SET ai_token_version=1 WHERE id=?', (AI['id'],))
        self.assertTrue(self.mcp(AI, 'question', round_id=self.rid)['result']['isError'])
        self.tokens[AI['id']] = server._create_account_jwt(AI | {'ai_token_version': 1})
        self.assertNotEqual(old_token, self.tokens[AI['id']])
        for path in (True, False):
            response = self.mcp(AI, 'question', round_id=self.rid, path=path)['result']
            self.assertFalse(response['isError'])
            self.assertEqual(json.loads(response['content'][0]['text'])['questions'][0]['own']['text'], params['text'])
            self.assertTrue(self.mcp(OTHER_AI, 'question', round_id=self.rid, path=path)['result']['isError'])
        self.assertEqual(self.http(OTHER_HUMAN, round_id=self.rid)[0], 404)

    def test_migration_preserves_old_digest_retries_even_after_answer_edit(self):
        original = self.params()
        self.call(HUMAN, 'answer', **original)
        self.call(HUMAN, 'answer', **(original | {'revision': 1, 'request_id': 'edited_request', 'text': 'later answer'}))
        self.legacy()
        before, requests = self.rows()
        with sqlite3.connect(self.db) as conn:
            conn.execute('CREATE TABLE unrelated (value TEXT)')
            conn.execute("INSERT INTO unrelated VALUES ('other game unchanged')")
        checked = migrate(self.db)
        self.assertEqual((checked['answer_fields'], checked['digests'], checked['applied']), (2, 2, False))
        self.assertEqual(self.rows(), (before, requests))
        self.assertEqual(self.http(HUMAN, round_id=self.rid)[0], 503)
        migrated = migrate(self.db, apply=True)
        self.assertEqual(migrated['digests'], 2)
        after = self.rows()
        repeated = migrate(self.db, apply=True)
        self.assertEqual((repeated['answer_fields'], repeated['digests'], repeated['encrypted_fields']), (0, 0, 4))
        self.assertEqual(self.rows(), after)
        for key in before:
            if key not in ('human_answers', 'ai_answers'):
                self.assertEqual(before[key], after[0][key])
        self.assertEqual(self.call(HUMAN, 'answer', **original)['revision'], 2)
        self.assertEqual(self.http(HUMAN, 'answer', post=True, **(original | {'text': 'different'}))[0], 409)
        self.assertEqual(self.call(HUMAN, 'question', round_id=self.rid)['questions'][0]['own']['text'], 'later answer')
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT * FROM unrelated').fetchall(), [('other game unchanged',)])
        # Explicit sanitation demonstration on THIS DISPOSABLE fixture only.
        # Migration itself deliberately never rewrites the shared SQLite file.
        with sqlite3.connect(self.db) as conn:
            conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            conn.execute('VACUUM')
        for path in self.root.glob('sessions.db*'):
            raw = path.read_bytes()
            self.assertNotIn(original['text'].encode(), raw)
            self.assertNotIn(b'later answer', raw)
            for request in requests:
                self.assertNotIn(request['digest'].encode(), raw)

    def test_migration_mixed_records_concurrent_idempotence_and_rollback(self):
        self.call(HUMAN, 'answer', **self.params())
        self.legacy()
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE deeptalk_requests SET digest='invalid'")
        before = self.rows()
        with self.assertRaises(DeeptalkError):
            migrate(self.db, apply=True)
        self.assertEqual(self.rows(), before)  # Answer updates rolled back after invalid receipt.
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE deeptalk_requests SET digest=?', ('a' * 64,))
        # Mixed legacy and encrypted fields are valid during a controlled migration.
        row, _ = self.rows()
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE deeptalk_rounds SET ai_answers=?',
                         (Cipher().encrypt('{}', answer_context(row, 'ai')),))
        with ThreadPoolExecutor(max_workers=2) as pool:
            counts = list(pool.map(lambda _: migrate(self.db, apply=True), range(2)))
        self.assertEqual(sorted(c['answer_fields'] for c in counts), [0, 1])
        after = self.rows()
        wrong = self.root / 'wrong.key'; wrong.write_bytes(os.urandom(32)); wrong.chmod(0o600)
        with patch.dict(os.environ, {KEY_ENV: str(wrong)}), self.assertRaises(DeeptalkError):
            migrate(self.db, apply=True)
        self.assertEqual(self.rows(), after)

    def test_legacy_wal_residue_requires_separate_offline_sanitation(self):
        marker = self.params()['text']
        self.call(HUMAN, 'answer', **self.params())
        keeper = sqlite3.connect(self.db)
        try:
            keeper.execute('PRAGMA journal_mode=WAL')
            keeper.execute('PRAGMA wal_autocheckpoint=0')
            keeper.execute('BEGIN')
            keeper.execute('SELECT * FROM deeptalk_rounds').fetchall()
            self.legacy()
            migrate(self.db, apply=True)
            self.assertTrue(self.rows()[0]['human_answers'].startswith(PREFIX))
            # A reader pins legacy WAL frames: field migration is not disk erasure.
            wal = self.db.with_name(self.db.name + '-wal')
            self.assertTrue(marker.encode() in wal.read_bytes(), 'Expected pinned legacy plaintext in WAL')
            keeper.rollback()
            checkpoint = keeper.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()
            self.assertEqual(checkpoint[0], 0)
            keeper.execute('VACUUM')
            self.assertEqual(keeper.execute('PRAGMA wal_checkpoint(TRUNCATE)').fetchone()[0], 0)
        finally:
            keeper.close()
        for path in self.root.glob('sessions.db*'):
            self.assertNotIn(marker.encode(), path.read_bytes())
        self.assertEqual(self.call(HUMAN, 'question', round_id=self.rid)['questions'][0]['own']['text'], marker)

    def test_legacy_schema_and_malformed_migration_preserve_data(self):
        self.call(HUMAN, 'answer', **self.params())
        self.legacy()
        with sqlite3.connect(self.db) as conn:
            conn.execute('ALTER TABLE deeptalk_rounds DROP COLUMN mode')
            conn.execute('ALTER TABLE deeptalk_rounds DROP COLUMN sharing_mode')
        migrate(self.db, apply=True)
        state = self.call(HUMAN, 'question', round_id=self.rid)
        self.assertEqual((state['mode'], state['sharing_mode']), ('blind', 'single_ai'))
        self.assertEqual(state['questions'][0]['own']['text'], self.params()['text'])
        for bad in ('[]', '{broken', '{"q1":{"skipped":1}}', 'dtenc:v99:AAAA'):
            with sqlite3.connect(self.db) as conn:
                conn.execute('UPDATE deeptalk_rounds SET human_answers=?', (bad,))
            before = self.rows()
            with self.assertRaises(DeeptalkError):
                migrate(self.db, apply=True)
            self.assertEqual(self.rows(), before)

    def test_cli_requires_explicit_existing_db_and_does_not_dump_secrets(self):
        script = str(ROOT / 'scripts/migrate_deeptalk_encryption.py')
        run = subprocess.run([sys.executable, script], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        nonexistent = self.root / 'never-created.db'
        run = subprocess.run([sys.executable, script, '--db', str(nonexistent), '--apply'], capture_output=True, text=True)
        self.assertNotEqual(run.returncode, 0)
        self.assertFalse(nonexistent.exists())
        self.call(HUMAN, 'answer', **self.params())
        self.legacy()
        before = self.rows()
        run = subprocess.run([sys.executable, script, '--db', str(self.db)], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(run.stdout)['answer_fields'], 2)
        self.assertEqual(self.rows(), before)
        run = subprocess.run([sys.executable, script, '--db', str(self.db), '--apply'], capture_output=True, text=True, check=True)
        self.assertNotIn(self.params()['text'], run.stdout + run.stderr)
        self.assertEqual(json.loads(run.stdout)['answer_fields'], 2)


if __name__ == '__main__':
    unittest.main()
