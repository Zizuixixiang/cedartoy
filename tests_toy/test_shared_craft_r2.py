"""No sockets/credentials: fake S3 wire, real room handlers, disposable stores."""
import hashlib
import hmac
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from shared_craft_adapter import object_storage as objects
from scripts import shared_craft_r2 as migration
from tests_toy import test_shared_craft
from tests_toy.test_shared_craft_sharing import seed_legacy
from tests_toy import test_shared_craft_sharing, test_shared_craft_framer


class FakeS3:
    def __init__(self):
        self.objects, self.calls = {}, []
        self.fail = False
        self.corrupt = False

    def get(self, key):
        objects.validate_key(key)
        self.calls.append(("GET", key))
        if self.fail:
            raise OSError("offline")
        return self.objects.get(key)

    def put(self, key, raw):
        objects.validate_key(key)
        self.calls.append(("PUT", key))
        if self.fail:
            raise OSError("offline")
        self.objects[key] = b"corrupt" if self.corrupt else raw

    def close(self):
        pass


class FakeConnection:
    """Transport stub verifies SigV4 independently; never binds a socket."""
    def __init__(self, host, port=None, timeout=None):
        self.host = host
        self.objects = {}
        self.requests = []
        self.status_override = None
        self.closed = False

    def request(self, method, uri, body, headers):
        self.requests.append((method, uri, body, headers))
        auth = headers["authorization"]
        fields = dict(part.split("=", 1) for part in auth.split(" ", 1)[1].split(","))
        access, day, region, service, terminator = fields["Credential"].split("/")
        assert access == "fake-access" and service == "s3" and terminator == "aws4_request"
        assert headers["x-amz-content-sha256"] == hashlib.sha256(body).hexdigest()
        assert headers["host"] == self.host
        names = fields["SignedHeaders"].split(";")
        canonical = f"{method}\n{uri}\n\n"
        canonical += "".join(name + ":" + headers[name] + "\n" for name in names)
        canonical += "\n" + ";".join(names) + "\n" + hashlib.sha256(body).hexdigest()
        scope = "/".join((day, region, service, terminator))
        signing_key = b"AWS4fake-secret"
        for part in (day, region, service, terminator):
            signing_key = hmac.digest(signing_key, part.encode(), "sha256")
        message = ("AWS4-HMAC-SHA256\n" + headers["x-amz-date"] + "\n" + scope
                   + "\n" + hashlib.sha256(canonical.encode()).hexdigest())
        assert hmac.compare_digest(fields["Signature"], hmac.digest(
            signing_key, message.encode(), "sha256").hex())
        assert uri.startswith("/cedarclio-backup/shared-craft/v1/rooms/")
        assert "x-amz-acl" not in headers
        if method == "PUT":
            assert headers["content-type"] == "image/png"
            self.objects[uri] = body
            self.response = io.BytesIO(b"")
            self.response.status = 200
        else:
            self.response = io.BytesIO(self.objects.get(uri, b""))
            self.response.status = 200 if uri in self.objects else 404
        if self.status_override is not None:
            self.response.status = self.status_override

    def getresponse(self):
        return self.response

    def close(self):
        self.closed = True


class ObjectStorageTests(unittest.TestCase):
    def test_wire_signing_connection_reuse_and_no_remote_namespace_escape(self):
        with patch.object(objects.http.client, "HTTPSConnection", side_effect=FakeConnection) as factory:
            s3 = objects.S3("https://account.r2.cloudflarestorage.com",
                            "fake-access", "fake-secret")
            raw = b"\x89PNG\r\nfake"
            key = objects.object_key("1", "gallery", "abcd", objects.digest(raw))
            self.assertEqual(objects.ensure_object(s3, key, raw), "uploaded")
            self.assertEqual(objects.ensure_object(s3, key, raw), "existing")
            self.assertEqual(factory.call_count, 1)
            self.assertEqual([r[0] for r in s3.connection.requests], ["GET", "PUT", "GET", "GET"])
            for bad in ("cedartoy-backups/x", "shared-craft/probes/a",
                        key.replace("/rooms/1/", "/rooms/../"),
                        key.replace("abcd/", "%2e%2e/"), key + "/../../x"):
                with self.assertRaises(ValueError):
                    s3.put(bad, raw)
            with self.assertRaises(ValueError):
                s3._request("DELETE", key)
            with self.assertRaisesRegex(ValueError, "key/hash"):
                s3.put(key, b"other payload")
            connection = s3.connection
            connection.status_override = 403
            with self.assertRaisesRegex(OSError, "403"):
                s3.get(key)
            self.assertTrue(connection.closed)
            self.assertIsNone(s3.connection)
            s3.close()

    def test_default_off_never_loads_config_and_enabled_loads_once(self):
        config = ("[cloudflare_r2]\ntype = s3\nprovider = Cloudflare\n"
                  "endpoint = https://account.r2.cloudflarestorage.com\n"
                  "access_key_id = fake-access\nsecret_access_key = fake-secret\n")
        with patch.object(objects.subprocess, "run", return_value=subprocess.CompletedProcess(
                [], 0, config, "")) as run:
            images = objects.Images.from_env("/unused", {})
            self.assertEqual(images.mode, "off")
            run.assert_not_called()
            images = objects.Images.from_env("/unused", {"CRAFT_R2_MODE": "mirror"})
            self.addCleanup(images.close)
            self.assertEqual(run.call_count, 1)
            self.assertEqual(run.call_args.args[0],
                             ["/snap/bin/rclone", "config", "show", "cloudflare_r2"])
            self.assertEqual(images.backend.region, "auto")
        with patch.object(objects.subprocess, "run", side_effect=OSError("secret fake")):
            with self.assertLogs(objects.LOG, "WARNING") as logs:
                images = objects.Images.from_env("/unused", {"CRAFT_R2_MODE": "prefer-r2"})
            self.assertIsNone(images.backend)
            self.assertNotIn("secret fake", "\n".join(logs.output))

    def test_sha_mismatch_not_overwritten_and_failed_readback(self):
        backend = FakeS3()
        raw = b"PNG original"
        key = objects.object_key("1", "shares", "abc", objects.digest(raw))
        backend.objects[key] = b"changed"
        with self.assertRaisesRegex(ValueError, "SHA256"):
            objects.ensure_object(backend, key, raw)
        self.assertFalse(any(method == "PUT" for method, _ in backend.calls))
        backend.objects.clear()
        backend.corrupt = True
        with self.assertRaisesRegex(ValueError, "SHA256"):
            objects.ensure_object(backend, key, raw)
        with self.assertRaises(ValueError):
            objects.verified_get(backend, key, "0" * 64)

    def test_path_validation_and_room_key_isolation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            good = root / "rooms/1/gallery/abc.png"
            good.parent.mkdir(parents=True)
            good.write_bytes(b"png")
            self.assertEqual(objects.file_identity(root, good), ("1", "gallery", "abc"))
            for name in ("rooms/1/gallery/../abc.png", "rooms/2/../../accounts.db",
                         "rooms/1/gallery/%2e.png", "rooms/01/gallery/a.png"):
                with self.assertRaises(ValueError):
                    objects.file_identity(root, root / name)
            (good.parent / "def.png").symlink_to(good)
            with self.assertRaises(ValueError):
                objects.file_identity(root, good.parent / "def.png")
            self.assertNotEqual(objects.object_key("1", "gallery", "abc", "0" * 64),
                                objects.object_key("2", "gallery", "abc", "0" * 64))

    def test_async_queue_is_bounded_and_shutdown_is_idempotent(self):
        backend = FakeS3()
        images = objects.Images("/unused", "mirror", backend)
        entered, release = threading.Event(), threading.Event()
        original_get = backend.get
        def blocking_get(key):
            entered.set()
            release.wait(timeout=5)
            return original_get(key)
        backend.get = blocking_get
        try:
            images.mirror("1", "gallery", "a", b"first")
            self.assertTrue(entered.wait(timeout=2))
            with self.assertLogs(objects.LOG, "WARNING"):
                for i in range(12):
                    images.mirror("1", "gallery", format(i + 16, "x"), b"queued")
            self.assertEqual(images.queue.qsize(), 8)
            # mirror mode reads remain local even while writer is stalled.
            self.assertEqual(images.read("1", "gallery", "a", b"local"), b"local")
        finally:
            release.set()
            images.close()
        images.close()
        self.assertFalse(images.worker.is_alive())


class R2ServiceTests(unittest.TestCase):
    def setUp(self):
        self.backend = FakeS3()
        self.images = []
        def factory(root, env=None):
            images = objects.Images(root, "prefer-r2", self.backend)
            self.images.append(images)
            return images
        patcher = patch.object(objects.Images, "from_env", side_effect=factory)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = test_shared_craft.SharedCraftTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(lambda: [images.close() for images in self.images])

    def drain(self):
        for images in self.images:
            images.queue.join()

    def test_authorized_web_mcp_immutable_share_revocation_and_other_room(self):
        f = self.f
        f.draw(2)
        item = f.gallery()
        sid = f.play("share_work", 2, item_id=item["id"])["id"]
        self.drain()
        self.assertEqual(len(self.backend.objects), 2)
        f.grant()
        expected = f.request("api/gallery/" + item["id"] + ".png").content
        self.assertEqual(f.request("author/" + sid, uid=6).content, expected)
        before = len(self.backend.calls)
        for uid in (1, 2, 3, 4, 5):
            self.assertEqual(f.request("author/" + sid, uid=uid).status_code, 403)
        f.request("api/gallery/" + item["id"] + ".png", uid=4)
        self.assertEqual(len(self.backend.calls), before, "denied requests must not read R2")
        response = f.request("play", "POST", {"action": "author_image",
                             "params": {"submission_id": sid}}, 6)
        import base64
        self.assertEqual(base64.b64decode(response.json()["content"][1]["data"]), expected)
        f.app.state.rooms["1"].gallery.image_path(item["id"]).write_bytes(b"new live image")
        f.request("api/gallery/" + item["id"], "DELETE")
        self.assertEqual(f.request("author/" + sid, uid=6).content, expected)
        with sqlite3.connect(f.app.state.shares.path) as conn:
            self.assertEqual(conn.execute("SELECT image FROM submissions").fetchone()[0], expected)
        with sqlite3.connect(f.grants) as conn:
            conn.execute("DELETE FROM shared_craft_authors")
        before = len(self.backend.calls)
        self.assertEqual(f.request("author/" + sid, uid=6).status_code, 403)
        self.assertEqual(len(self.backend.calls), before)

    def test_failure_corruption_rollback_and_room_canary(self):
        f = self.f
        f.draw()
        item = f.gallery()
        self.drain()
        route = "api/gallery/" + item["id"] + ".png"
        expected = f.request(route).content
        images = self.images[0]
        self.backend.fail = True
        with self.assertLogs(objects.LOG, "WARNING"):
            self.assertEqual(f.request(route).content, expected)
        before = len(self.backend.calls)
        self.assertEqual(f.request(route).content, expected)
        self.assertEqual(len(self.backend.calls), before, "circuit breaker should avoid repeated stalls")
        # New local save still succeeds during outage and is recoverable by backfill.
        f.gallery()
        self.drain()
        self.backend.fail = False
        images.retry_at = 0
        for key in self.backend.objects:
            self.backend.objects[key] = b"wrong bytes"
        with self.assertLogs(objects.LOG, "WARNING"):
            self.assertEqual(f.request(route).content, expected)
        images.mode = "off"
        before = len(self.backend.calls)
        self.assertEqual(f.request(route).content, expected)
        self.assertEqual(len(self.backend.calls), before)
        images.mode, images.rooms, images.retry_at = "prefer-r2", frozenset({"4"}), 0
        self.assertEqual(f.request(route).content, expected)
        self.assertEqual(len(self.backend.calls), before)

    def test_gift_visibility_checked_before_decorated_image_read(self):
        f = self.f
        bid = f.play("new_bead_board", 2, title="private gift", mode="gift", size=8)["id"]
        room = f.app.state.rooms["1"]
        with room.lock:
            data = room.beads._load()
            data["boards"][0]["craft"] = {"decorated": True, "stage": "finished"}
            folder = room.beads.path.with_name("shared-bead-finished")
            folder.mkdir()
            (folder / (bid + ".png")).write_bytes(b"full gift")
            (folder / (bid + "-piece.png")).write_bytes(b"transparent gift")
            room.beads._save(data)
        self.drain()
        for method in (room.beads.decorated_bytes, room.beads.display_piece_bytes):
            before = len(self.backend.calls)
            with self.assertRaises(ValueError):
                method("3", bid)
            self.assertEqual(len(self.backend.calls), before)
        self.assertEqual(room.beads.decorated_bytes("2", bid), b"full gift")
        self.assertEqual(room.beads.display_piece_bytes("2", bid), b"transparent gift")

    def test_existing_framer_full_piece_restart_and_sharing_regressions_with_r2(self):
        # Execute the existing real decoration lifecycle and historical snapshot
        # regressions with this fake-backed factory, not a simplified reimplementation.
        suite = unittest.TestSuite([
            test_shared_craft_framer.SharedCraftFramerTests(
                "test_finished_decoration_and_gallery_survive_app_restart"),
            *unittest.defaultTestLoader.loadTestsFromTestCase(
                test_shared_craft_sharing.SharedCraftSharingTests),
        ])
        result = unittest.TestResult()
        suite.run(result)
        self.drain()
        self.assertEqual(result.errors + result.failures, [])
        kinds = {key.split("/")[4] for key in self.backend.objects}
        self.assertEqual(kinds, {"gallery", "finished", "shares"})
        names = [key.split("/")[5] for key in self.backend.objects]
        self.assertTrue(any(name.endswith("-piece") for name in names))
        self.assertTrue(any(name.endswith("-display") for name in names))


class MigrationTests(unittest.TestCase):
    def setUp(self):
        # Explicit off ensures old fixtures are truly pre-migration.
        patcher = patch.dict(os.environ, {"CRAFT_R2_MODE": "off"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.f = test_shared_craft.SharedCraftTests()
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.root = self.f.root / "craft"
        self.manifest = self.f.root / "migration.jsonl"
        self.backend = FakeS3()
        self.f.draw(2)
        self.item = self.f.gallery()
        self.sid = seed_legacy(self.f, self.item)
        self.f.draw(4)
        self.f.gallery(4)
        folder = self.root / "rooms/1/shared-bead-finished"
        folder.mkdir()
        (folder / "ab.png").write_bytes(b"historical full")
        (folder / "ab-piece.png").write_bytes(b"historical transparent")

    def inventory(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob("*") if path.is_file()}

    def test_dry_run_apply_repeat_verify_all_history_preserved(self):
        before = self.inventory()
        with patch.object(objects.S3, "from_env", side_effect=AssertionError("network forbidden")):
            self.assertEqual(migration.main(["backfill", "--data-dir", str(self.root),
                                           "--manifest", str(self.manifest)]), 0)
        self.assertEqual(before, self.inventory())
        # Old files and pending BLOBs can be read through the same authorized
        # routes after switching on; no schema migration or publication needed.
        from shared_craft_adapter.service import create_app
        images = objects.Images(self.root, "prefer-r2", self.backend)
        self.addCleanup(images.close)
        app = create_app(self.root, self.f.accounts, self.f.grants, "test-secret-" * 4,
                         images=images)
        gallery_route = "api/gallery/" + self.item["id"] + ".png"
        expected = self.f.request(gallery_route).content
        self.assertEqual(self.f.request(gallery_route, app=app).content, expected)
        before_calls = len(self.backend.calls)
        self.assertEqual(self.f.request("api/shares/" + self.sid + ".png",
                                       uid=4, app=app).status_code, 403)
        self.assertEqual(len(self.backend.calls), before_calls)
        self.assertEqual(self.f.request("api/shares/" + self.sid + ".png", app=app).content, expected)
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend)
        self.assertEqual(counts["uploaded"], 5)
        before_calls = len(self.backend.calls)
        self.assertEqual(self.f.request(gallery_route, app=app).content, expected)
        self.assertGreater(len(self.backend.calls), before_calls)
        self.assertEqual(before, self.inventory())
        keys = set(self.backend.objects)
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend)
        self.assertEqual(counts["existing"], 5)
        self.assertEqual(set(self.backend.objects), keys)
        self.assertEqual(sum(method == "PUT" for method, _ in self.backend.calls), 5)
        counts = migration.migrate(self.root, self.manifest, verify_only=True, backend=self.backend)
        self.assertEqual(counts["verified"], 5)
        entries = [json.loads(line) for line in self.manifest.read_text().splitlines()]
        for entry in entries:
            if entry.get("status") in {"uploaded", "existing", "verified"}:
                self.assertEqual(objects.digest(self.backend.objects[entry["key"]]), entry["sha256"])
        # Migrating pending BLOBs never publishes them or modifies the SQLite schema.
        self.f.grant()
        self.assertEqual(self.f.request("author", uid=6).json()["total"], 0)
        self.assertEqual(before, self.inventory())

    def test_interrupted_upload_and_partial_manifest_resume(self):
        original_put = self.backend.put
        def interrupted(key, raw):
            original_put(key, raw)
            raise KeyboardInterrupt()
        self.backend.put = interrupted
        with self.assertRaises(KeyboardInterrupt):
            migration.migrate(self.root, self.manifest, apply=True, backend=self.backend)
        self.backend.put = original_put
        with self.manifest.open("ab") as stream:
            stream.write(b'{"interrupted":')
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend)
        self.assertEqual((counts["existing"], counts["uploaded"]), (1, 4))
        for line in self.manifest.read_text().splitlines():
            json.loads(line)
        self.assertEqual(sum(m == "PUT" for m, _ in self.backend.calls), 5)

    def test_failure_resume_canary_corrupt_remote_and_verify_missing(self):
        self.backend.fail = True
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend, rooms={"1"})
        self.assertEqual(counts["failed"], 4)
        self.backend.fail = False
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend, rooms={"1"})
        self.assertEqual(counts["uploaded"], 4)
        self.assertTrue(all("/rooms/1/" in key for key in self.backend.objects))
        key = next(iter(self.backend.objects))
        self.backend.objects[key] = b"corrupted remote"
        counts = migration.migrate(self.root, self.manifest, verify_only=True, backend=self.backend)
        self.assertEqual(counts["failed"], 2)  # corrupt room 1 + missing room 4
        counts = migration.migrate(self.root, self.manifest, apply=True, backend=self.backend)
        self.assertEqual(counts["failed"], 1)
        self.assertEqual(self.backend.objects[key], b"corrupted remote", "do not overwrite bad evidence")

    def test_snapshot_consistent_db_and_symlink_source_rejected(self):
        with sqlite3.connect(self.root / "shares.db") as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("UPDATE submissions SET note='WAL snapshot'")
        destination = self.f.root / "backup"
        migration.snapshot(self.root, destination)
        self.assertTrue((destination / "SNAPSHOT.json").is_file())
        with sqlite3.connect(destination / "shares.db") as conn:
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("SELECT note FROM submissions").fetchone()[0], "WAL snapshot")
        counts = migration.migrate(destination, self.manifest, apply=True, backend=self.backend)
        self.assertEqual(counts["uploaded"], 5)
        with self.assertRaises(ValueError):
            migration.migrate(self.root, self.manifest)
        with self.assertRaises(ValueError):
            migration.migrate(self.root, self.root / "manifest.jsonl")
        (self.root / "rooms/1/gallery/bad.png").symlink_to(self.root / "shares.db")
        with self.assertRaises(ValueError):
            list(migration.sources(self.root))
        with self.assertRaises(ValueError):
            migration.snapshot(self.root, self.f.root / "bad-backup")


if __name__ == "__main__":
    unittest.main()
