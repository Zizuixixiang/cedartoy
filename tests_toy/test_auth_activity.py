"""Activity writes are optional; account/token checks are never optional."""
import datetime
import logging
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from cedar_backend import auth, auth_activity
from cedar_backend.errors import _McpError

class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "accounts.db"
        self.conns = []
        self.addCleanup(lambda: [c.close() for c in self.conns])
        self.conn = self.connect()
        self.conn.executescript("""
            CREATE TABLE toy_users(id INTEGER PRIMARY KEY, is_ai INTEGER,
              ai_token_version INTEGER, deleted_at TEXT,
              deletion_requested_at_epoch INTEGER, scheduled_delete_at_epoch INTEGER,
              last_active_at TEXT);
            INSERT INTO toy_users VALUES(7,0,0,NULL,NULL,NULL,'2000-01-01 00:00:00');
            CREATE TABLE ai_access_tokens(token_hash TEXT, user_id INTEGER,
              generation INTEGER, format_version INTEGER, revoked_at_epoch INTEGER);
        """)
        self.conn.commit()
        self.payload = {"user_id": 7, "is_ai": False}
        auth_activity._LOG_COUNT = 0

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        self.conns.append(conn)
        return conn

    def login(self, token="PRIVATE_TOKEN", **overrides):
        deps = dict(allow_pending_deletion=False, AI_OPAQUE_TOKEN_FORMAT_VERSION=1,
            AI_OPAQUE_TOKEN_PREFIX="opaque:", _McpError=_McpError,
            _db_connect=self.connect, _jwt_decode=lambda raw: self.payload,
            _jwt_unverified_payload=lambda raw: self.payload,
            _opaque_ai_token_hash=lambda raw: "PRIVATE_HASH",
            _row_dict=lambda row: dict(row) if row else None, time=time)
        deps.update(overrides)
        return auth._current_account(token, **deps)

    def lock_writer(self):
        other = self.connect()
        other.execute("BEGIN IMMEDIATE")
        other.execute("UPDATE toy_users SET last_active_at=last_active_at WHERE id=7")
        return other

    def test_normal_login_updates(self):
        result = self.login()
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["_auth_token_format"], "jwt")
        self.assertNotEqual(result["last_active_at"], "2000-01-01 00:00:00")

    def test_busy_activity_does_not_break_verified_login(self):
        lock = self.lock_writer()
        started = time.monotonic()
        with self.assertLogs("cedartoy.auth_activity", logging.WARNING) as logs:
            result = self.login()
        self.assertLess(time.monotonic() - started, 1.0)
        self.assertEqual(result["id"], 7)
        self.assertEqual(result["last_active_at"], "2000-01-01 00:00:00")
        text = "\n".join(logs.output)
        for label in ("stage=update", "pid=", "native_tid=", "elapsed_ms="):
            self.assertIn(label, text)
        for secret in ("PRIVATE_TOKEN", "PRIVATE_HASH", "UPDATE", "2000-01-01"):
            self.assertNotIn(secret, text)
        self.assertFalse(self.conns[-1].in_transaction)
        self.assertEqual(self.conns[-1].execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        lock.rollback()

    def test_logging_failure_does_not_break_deferred_auth(self):
        lock = self.lock_writer()
        with patch.object(auth_activity._LOG, "warning", side_effect=OSError("log unavailable")):
            self.assertEqual(self.login()["id"], 7)
        lock.rollback()

    def test_busy_commit_rolls_back_and_restores_timeout(self):
        reader = self.connect()
        reader.execute("BEGIN")
        reader.execute("SELECT * FROM toy_users").fetchall()
        with self.assertLogs("cedartoy.auth_activity") as logs:
            self.assertFalse(auth_activity.update_activity(self.conn, 7, None))
        self.assertIn("stage=commit", "\n".join(logs.output))
        self.assertFalse(self.conn.in_transaction)
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
        reader.rollback()
        self.assertEqual(self.conn.execute("SELECT last_active_at FROM toy_users").fetchone()[0],
                         "2000-01-01 00:00:00")

    def test_recent_activity_avoids_write_but_not_auth_checks(self):
        now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("UPDATE toy_users SET last_active_at=?", (now,))
        self.conn.commit()
        lock = self.lock_writer()
        started = time.monotonic()
        self.assertEqual(self.login()["id"], 7)
        self.assertLess(time.monotonic() - started, 0.1)
        lock.rollback()
        self.conn.execute("UPDATE toy_users SET deleted_at='deleted'"); self.conn.commit()
        with self.assertRaises(_McpError):
            self.login()

    def test_other_sql_errors_are_not_swallowed(self):
        self.conn.execute("DROP TABLE toy_users"); self.conn.commit()
        with self.assertRaises(sqlite3.OperationalError):
            auth_activity.update_activity(self.conn, 7, None)
        self.assertEqual(self.conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)

    def test_does_not_rollback_unrelated_transaction(self):
        self.conn.execute("BEGIN IMMEDIATE")
        with self.assertRaises(RuntimeError):
            auth_activity.update_activity(self.conn, 7, None)
        self.assertTrue(self.conn.in_transaction)
        self.conn.rollback()

    def test_invalid_token_rejected_before_activity(self):
        def invalid(raw): raise ValueError("PRIVATE_TOKEN")
        with patch.object(auth, "update_activity", side_effect=AssertionError("not reached")):
            with self.assertRaises(_McpError):
                self.login(_jwt_decode=invalid)
            with self.assertRaises(_McpError):
                self.login("")

    def test_revoked_opaque_token_rejected(self):
        with patch.object(auth, "update_activity", side_effect=AssertionError("not reached")):
            with self.assertRaises(_McpError):
                self.login("opaque:PRIVATE_TOKEN")

    def test_ai_generation_mismatch_rejected(self):
        self.conn.execute("UPDATE toy_users SET is_ai=1,ai_token_version=2")
        self.conn.commit()
        self.payload.update(is_ai=True, token_version=1)
        with patch.object(auth, "update_activity", side_effect=AssertionError("not reached")):
            with self.assertRaises(_McpError):
                self.login()

    def test_pending_deletion_keeps_rejection_and_allowed_path(self):
        self.conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=1,scheduled_delete_at_epoch=2")
        self.conn.commit()
        with patch.object(auth, "update_activity", side_effect=AssertionError("not reached")):
            with self.assertRaises(_McpError):
                self.login()
            self.assertEqual(self.login(allow_pending_deletion=True)["id"], 7)

    def test_future_bad_and_old_timestamps_are_not_cached(self):
        for value in (None, "bad", "2000-01-01 00:00:00", "2999-01-01 00:00:00"):
            self.assertFalse(auth_activity._recent(value))

if __name__ == "__main__":
    unittest.main()
