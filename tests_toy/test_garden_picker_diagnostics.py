"""Real HTTP parsing/auth/SQLite/response path, using only disposable fixtures."""

from contextlib import ExitStack, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import server


class MemorySocket:
    def __init__(self, request, fail_body=False):
        self.request = request
        self.output = bytearray()
        self.fail_body = fail_body
        self.writes = 0

    def makefile(self, *_args):
        return io.BytesIO(self.request)

    def sendall(self, data):
        self.writes += 1
        if self.fail_body and self.writes >= 2:
            raise BrokenPipeError("private-exception-sentinel")
        self.output.extend(data)


class GardenPickerDiagnosticsTests(unittest.TestCase):
    request_id = "0123456789abcdef0123456789abcdef"

    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="garden-diag-test-")))
        self.db = self.root / "accounts.db"
        self.stack.enter_context(patch.object(server, "TURTLE_DB_PATH", self.db))
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.root / "sessions.db"))
        self.stack.enter_context(patch.object(server, "VENDOR_SAVE_ROOT", self.root / "saves"))
        self.stack.enter_context(patch.object(server, "TOY_SECRET", "isolated-garden-test-key"))
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE toy_users(id INTEGER PRIMARY KEY, username TEXT,
                    is_ai INTEGER, deleted_at TEXT, last_active_at TEXT,
                    ai_token_version INTEGER DEFAULT 0,
                    deletion_requested_at_epoch INTEGER, scheduled_delete_at_epoch INTEGER);
                CREATE TABLE user_bindings(human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users(id,username,is_ai,last_active_at) VALUES
                    (1,'private-human-name',0,'before'),(2,'private-ai-name',1,'before'),
                    (3,'control-human',0,'before'),(4,'control-ai',1,'before');
                INSERT INTO user_bindings VALUES(1,2),(3,4);
            """)
        for uid in (2, 4):
            p = self.root / "saves" / "garden_cat" / str(uid) / "state.json"
            p.parent.mkdir(parents=True)
            p.write_text(json.dumps({"garden_name": "private-garden-name", "money": 12,
                                     "encyclopedia": [], "cat": None}))
        self.saved = {p: (p.read_bytes(), p.stat().st_mtime_ns)
                      for p in (self.root / "saves").rglob("state.json")}
        self.token = self.token_for(1)

    def token_for(self, uid, **extra):
        return server._jwt_encode({"user_id": uid, "is_ai": False,
                                   "exp": int(time.time()) + 60, **extra})

    def connect_fast(self):
        conn = sqlite3.connect(self.db, timeout=0.05)
        conn.row_factory = sqlite3.Row
        return conn

    def request(self, token=None, request_id=None, fail_body=False):
        raw = ("GET /api/garden-cat/gardens?private-query-sentinel HTTP/1.1\r\n"
               "Host: isolated.test\r\nAuthorization: Bearer " + (self.token if token is None else token)
               + "\r\nCookie: private-cookie-sentinel\r\nX-Garden-Request-Id: "
               + (self.request_id if request_id is None else request_id)
               + "\r\nConnection: close\r\n\r\n")
        sock = MemorySocket(raw.encode(), fail_body=fail_body)
        with redirect_stdout(io.StringIO()):
            server.CedarToyHandler(sock, ("127.0.0.1", 0), object())
        head, body = bytes(sock.output).split(b"\r\n\r\n", 1)
        headers = dict(line.split(": ", 1) for line in head.decode().split("\r\n")[1:])
        return int(head.split()[1]), headers, body, sock

    def records(self, captured):
        return [dict(part.split("=", 1) for part in record.getMessage().split()[1:])
                for record in captured.records if record.getMessage().startswith("garden_picker ")]

    def assert_private(self, captured):
        logs = "\n".join(captured.output)
        for secret in (self.token, "private-query-sentinel", "private-cookie-sentinel",
                       "private-exception-sentinel", "private-human-name", "private-ai-name",
                       "private-garden-name", "Authorization", "Bearer"):
            self.assertNotIn(secret, logs)

    def test_full_http_auth_write_list_response_and_control(self):
        for uid, ai in ((1, 2), (3, 4)):
            with self.subTest(uid=uid), self.assertLogs(server.logger, level="INFO") as logs:
                status, headers, body, _ = self.request(token=self.token_for(uid))
            self.assertEqual(status, 200)
            self.assertEqual(headers["X-Garden-Request-Id"], self.request_id)
            self.assertEqual(int(headers["Content-Length"]), len(body))
            self.assertEqual([g["ai_user_id"] for g in json.loads(body)["gardens"]], [ai])
            records = self.records(logs)
            self.assertEqual(records[0]["event"], "enter")
            verified = next(r for r in records if r["event"] == "identity_verified")
            self.assertEqual(verified["user_id"], str(uid))
            self.assertTrue(any(r["event"] == "activity_committed" for r in records))
            self.assertEqual(records[-1]["outcome"], "ok")
            self.assertTrue(all(r["request_id"] == self.request_id for r in records))
            self.assertTrue(all(float(r["phase_ms"]) >= 0 for r in records))
            self.assert_private(logs)
            with sqlite3.connect(self.db) as conn:
                self.assertNotEqual(conn.execute("SELECT last_active_at FROM toy_users WHERE id=?", (uid,)).fetchone()[0], "before")
        self.assertEqual(self.saved, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.saved})

    def test_untrusted_or_expired_identity_is_not_logged_and_does_not_write(self):
        forged = self.token_for(1)[:-4] + "AAAA"
        for token in ("", "invalid", forged, self.token_for(1, exp=1), self.token_for(2)):
            with self.subTest(token_kind=bool(token)), self.assertLogs(server.logger, level="INFO") as logs:
                status, _, body, _ = self.request(token=token)
            self.assertEqual(status, 401)
            self.assertIn("error", json.loads(body))
            self.assertTrue(all(r["user_id"] == "None" for r in self.records(logs)))
            self.assertEqual(self.records(logs)[-1]["outcome"], "http_error")
            self.assert_private(logs)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT last_active_at FROM toy_users WHERE id=1").fetchone()[0], "before")

    def test_database_read_and_activity_write_locks_have_distinct_phases(self):
        for mode, phase, uid in (("IMMEDIATE", "activity_update", "1"),
                                 ("EXCLUSIVE", "account_lookup", "None")):
            with self.subTest(mode=mode), sqlite3.connect(self.db) as blocker:
                blocker.execute("BEGIN " + mode)
                try:
                    with patch.object(server, "_db_connect", side_effect=self.connect_fast), self.assertLogs(server.logger, level="INFO") as logs:
                        status, headers, body, _ = self.request()
                finally:
                    blocker.rollback()
            self.assertEqual(status, 500)
            self.assertEqual(json.loads(body)["detail"], "database is locked")
            error = next(r for r in self.records(logs) if r["event"] == "error")
            self.assertEqual((error["phase"], error["user_id"]), (phase, uid))
            self.assertEqual(error["error_type"], "OperationalError")
            self.assertEqual(error["sqlite_busy"], "True")
            self.assertGreaterEqual(float(error["phase_ms"]), 40)
            self.assertEqual(headers["X-Garden-Request-Id"], self.request_id)
            self.assertEqual(self.records(logs)[-1]["outcome"], "http_error")

    def test_enter_is_emitted_while_authentication_is_still_blocked(self):
        entered, release = threading.Event(), threading.Event()
        decode = server._jwt_decode
        def blocked(token):
            entered.set()
            if not release.wait(2):
                raise AssertionError("test release missing")
            return decode(token)
        result = []
        with patch.object(server, "_jwt_decode", side_effect=blocked), self.assertLogs(server.logger, level="INFO") as logs:
            thread = threading.Thread(target=lambda: result.append(self.request()))
            thread.start()
            try:
                self.assertTrue(entered.wait(1))
                records = self.records(logs)
                self.assertEqual([r["event"] for r in records], ["enter", "auth_start"])
                self.assertTrue(all(r["user_id"] == "None" for r in records))
                self.assertTrue(all(r["request_id"] == self.request_id for r in records))
            finally:
                release.set()
                thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result[0][0], 200)

    def test_list_failure_keeps_verified_identity_without_logging_exception_text(self):
        with patch.object(server, "_garden_cat_watchable_gardens_for_user", side_effect=ValueError("private-exception-sentinel")), self.assertLogs(server.logger, level="INFO") as logs:
            status, _, _, _ = self.request()
        self.assertEqual(status, 500)
        error = next(r for r in self.records(logs) if r["event"] == "error")
        self.assertEqual((error["phase"], error["user_id"]), ("list_read", "1"))
        self.assert_private(logs)

    def test_broken_pipe_has_terminal_record_and_no_second_http_response(self):
        with self.assertLogs(server.logger, level="INFO") as logs:
            status, _, body, sock = self.request(fail_body=True)
        self.assertEqual(status, 200)  # Headers alone do not prove body delivery.
        self.assertEqual(body, b"")
        self.assertEqual(sock.writes, 2)
        self.assertEqual(bytes(sock.output).count(b"HTTP/1."), 1)
        end = self.records(logs)[-1]
        self.assertEqual((end["event"], end["outcome"], end["error_type"]),
                         ("end", "write_failed", "BrokenPipeError"))
        self.assert_private(logs)

    def test_invalid_request_id_is_replaced_not_logged(self):
        with self.assertLogs(server.logger, level="INFO") as logs:
            status, headers, _, _ = self.request(request_id="private-cookie-sentinel")
        self.assertEqual(status, 200)
        rid = headers["X-Garden-Request-Id"]
        self.assertRegex(rid, r"^[0-9a-f]{32}$")
        self.assertNotEqual(rid, self.request_id)
        self.assertTrue(all(r["request_id"] == rid for r in self.records(logs)))
        self.assert_private(logs)

    def test_other_account_calls_do_not_emit_picker_logs(self):
        with patch.object(server.logger, "info") as log:
            self.assertEqual(server._current_account(self.token)["id"], 1)
        log.assert_not_called()

    def test_worker_exhaustion_can_reply_before_picker_entry_or_auth(self):
        # A missing enter marker is not proof the connection missed the server:
        # admission control runs before HTTP parsing and therefore has no ID.
        sock = MemorySocket(b"")
        pool = SimpleNamespace(worker_slots=threading.BoundedSemaphore(0),
                               executor=Mock(), close_request=Mock(),
                               _send_busy=server.http_server.send_busy)
        with patch.object(server.logger, "info") as log:
            server.http_server.process_request(pool, sock, ("127.0.0.1", 0), QUEUE_TIMEOUT_SECONDS=0)
        self.assertTrue(bytes(sock.output).startswith(b"HTTP/1.1 503"))
        pool.executor.submit.assert_not_called()
        pool.close_request.assert_called_once_with(sock)
        log.assert_not_called()


if __name__ == "__main__":
    unittest.main()
