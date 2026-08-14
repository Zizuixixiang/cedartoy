import base64
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from ciyuwu_adapter import handler
from scripts import clean_guest_saves


class CiyuwuRetentionCapacityTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "sessions.db"
        self.db_patch = mock.patch.object(handler, "DB_PATH", str(self.db_path))
        self.db_patch.start()
        with sqlite3.connect(self.db_path) as conn:
            handler._init_db(conn)

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def _insert_many(self, rows):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO ciyuwu_sessions
                    (player_id, save_data, meta_data, created_at, last_active, user_id)
                VALUES (?, '{}', '{}', ?, ?, ?)
                """,
                rows,
            )

    def test_cleanup_retains_registered_after_30_days(self):
        now = time.time()
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        self._insert_many([("101", old, old, 101)])

        with sqlite3.connect(self.db_path) as conn:
            handler._cleanup_expired(conn, now)
            remaining = conn.execute(
                "SELECT player_id FROM ciyuwu_sessions"
            ).fetchall()

        self.assertEqual(remaining, [("101",)])

    def test_cleanup_keeps_29_day_guest_and_deletes_31_day_guest(self):
        now = time.time()
        recent = handler._now_iso(now - 29 * 24 * 60 * 60)
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        self._insert_many(
            [
                ("guest:recent", recent, recent, None),
                ("guest:expired", old, old, None),
            ]
        )

        with sqlite3.connect(self.db_path) as conn:
            handler._cleanup_expired(conn, now)
            remaining = {
                row[0] for row in conn.execute("SELECT player_id FROM ciyuwu_sessions")
            }

        self.assertEqual(remaining, {"guest:recent"})

    def test_cleanup_conservatively_retains_legacy_or_ambiguous_rows(self):
        now = time.time()
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        self._insert_many(
            [
                ("legacy-row", old, old, None),
                ("guest:owned", old, old, 202),
                ("guest:", old, old, None),
                ("Guest:wrongcase", old, old, None),
            ]
        )

        with sqlite3.connect(self.db_path) as conn:
            handler._cleanup_expired(conn, now)
            remaining = {
                row[0] for row in conn.execute("SELECT player_id FROM ciyuwu_sessions")
            }

        self.assertEqual(
            remaining,
            {"legacy-row", "guest:owned", "guest:", "Guest:wrongcase"},
        )

    def test_capacity_recovery_deletes_only_eligible_guest(self):
        now = time.time()
        active = handler._now_iso(now)
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        rows = [(str(i + 1), active, active, i + 1) for i in range(2999)]
        rows.append(("guest:expired", old, old, None))
        self._insert_many(rows)

        with mock.patch.object(
            handler,
            "_engine_new",
            return_value=({"runs": 0}, "intro"),
        ):
            text = handler.ciyuwu_new({"player_id": "guest:new"})

        self.assertIn("新局已开", text)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ciyuwu_sessions").fetchone()[0],
                3000,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM ciyuwu_sessions WHERE user_id IS NOT NULL"
                ).fetchone()[0],
                2999,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM ciyuwu_sessions WHERE player_id='guest:expired'"
                ).fetchone()
            )

    def test_full_capacity_does_not_evict_registered_and_existing_is_accessible(self):
        active = handler._now_iso(time.time())
        self._insert_many(
            [(str(i + 1), active, active, i + 1) for i in range(3000)]
        )

        with self.assertRaises(handler.JsonRpcError) as raised:
            handler.ciyuwu_new({"player_id": "guest:new"})
        with mock.patch.object(
            handler,
            "_engine_new",
            return_value=({"runs": 1}, "intro"),
        ):
            existing = handler.ciyuwu_new({"player_id": "1"})

        self.assertIn("容量已满", raised.exception.message)
        self.assertIn("已有存档", raised.exception.message)
        self.assertIn("新局已开", existing)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ciyuwu_sessions").fetchone()[0],
                3000,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM ciyuwu_sessions WHERE user_id IS NOT NULL"
                ).fetchone()[0],
                3000,
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM ciyuwu_sessions WHERE player_id='guest:new'"
                ).fetchone()
            )

    def test_import_cannot_bypass_full_capacity(self):
        active = handler._now_iso(time.time())
        self._insert_many(
            [(str(i + 1), active, active, i + 1) for i in range(3000)]
        )
        blob = base64.b64encode(
            json.dumps({"v": 1, "game": "ciyuwu", "run": {}}).encode()
        ).decode()

        with self.assertRaises(handler.JsonRpcError) as raised:
            handler.ciyuwu_save(
                {"player_id": "guest:new", "action": "import", "save_data": blob}
            )

        self.assertIn("容量已满", raised.exception.message)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM ciyuwu_sessions").fetchone()[0],
                3000,
            )

    def test_daily_cleanup_uses_30_days_and_safe_ciyuwu_identity(self):
        now = time.time()
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        recent = handler._now_iso(now - 29 * 24 * 60 * 60)
        self._insert_many(
            [
                ("registered", old, old, 1),
                ("guest:expired", old, old, None),
                ("guest:recent", recent, recent, None),
                ("legacy", old, old, None),
            ]
        )
        with mock.patch.object(clean_guest_saves, "SESSIONS_DB", self.db_path):
            rows = clean_guest_saves.collect_table_rows(
                180,
                eco_days=30,
                ciyuwu_days=30,
            )

        self.assertEqual(
            [(row["table"], row["player_id"]) for row in rows],
            [("ciyuwu_sessions", "guest:expired")],
        )


if __name__ == "__main__":
    unittest.main()
