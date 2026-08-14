import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from eco_adapter import capacity_alerts
from eco_adapter import handler
from scripts import clean_guest_saves


class EcoRetentionCapacityTests(unittest.TestCase):
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
                INSERT INTO eco_sessions
                    (player_id, save_data, created_at, last_active, user_id)
                VALUES (?, '{}', ?, ?, ?)
                """,
                rows,
            )

    def test_cleanup_only_deletes_expired_explicit_guests(self):
        now = time.time()
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        recent = handler._now_iso(now - 29 * 24 * 60 * 60)
        self._insert_many(
            [
                ("101", old, old, 101),
                ("guest:recent", recent, recent, None),
                ("guest:expired", old, old, None),
                ("legacy-row", old, old, None),
                ("guest:owned", old, old, 202),
                ("Guest:case-unknown", old, old, None),
            ]
        )

        with sqlite3.connect(self.db_path) as conn:
            handler._cleanup_expired(conn, now)
            remaining = {
                row[0] for row in conn.execute("SELECT player_id FROM eco_sessions")
            }

        self.assertNotIn("guest:expired", remaining)
        self.assertIn("101", remaining)
        self.assertIn("guest:recent", remaining)
        self.assertIn("legacy-row", remaining)
        self.assertIn("guest:owned", remaining)
        self.assertIn("Guest:case-unknown", remaining)

    def test_capacity_recovery_deletes_only_eligible_guest(self):
        now = time.time()
        active = handler._now_iso(now)
        old = handler._now_iso(now - 31 * 24 * 60 * 60)
        rows = [(str(i + 1), active, active, i + 1) for i in range(2999)]
        rows.append(("guest:expired", old, old, None))
        self._insert_many(rows)

        with mock.patch.object(handler, "_engine_new", return_value=("{}", "intro")), mock.patch.object(
            capacity_alerts, "dispatch_alert"
        ):
            text = handler.eco_new({"player_id": "newpond"})

        self.assertIn("新池初成", text)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM eco_sessions").fetchone()[0], 3000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM eco_sessions WHERE user_id IS NOT NULL").fetchone()[0], 2999)
            self.assertIsNone(conn.execute("SELECT 1 FROM eco_sessions WHERE player_id='guest:expired'").fetchone())
            self.assertIsNotNone(conn.execute("SELECT 1 FROM eco_sessions WHERE player_id='newpond'").fetchone())

    def test_full_capacity_never_silently_evicts_registered_save(self):
        active = handler._now_iso(time.time())
        self._insert_many(
            [(str(i + 1), active, active, i + 1) for i in range(3000)]
        )

        with mock.patch.object(capacity_alerts, "dispatch_alert"):
            with self.assertRaises(handler.JsonRpcError) as raised:
                handler.eco_new({"player_id": "guest:new"})
            existing = handler.eco_new({"player_id": "1"})

        self.assertIn("容量已满", raised.exception.message)
        self.assertIn("已有池塘", raised.exception.message)
        self.assertIn("确认覆盖", existing)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM eco_sessions").fetchone()[0], 3000)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM eco_sessions WHERE user_id IS NOT NULL").fetchone()[0], 3000)
            self.assertIsNone(conn.execute("SELECT 1 FROM eco_sessions WHERE player_id='guest:new'").fetchone())

    def test_alert_dedupes_and_rearms_after_count_drops(self):
        with sqlite3.connect(":memory:") as conn:
            first = capacity_alerts.update_alert_state(conn, 2400)
            duplicate = capacity_alerts.update_alert_state(conn, 2500)
            ninety = capacity_alerts.update_alert_state(conn, 2700)
            dropped = capacity_alerts.update_alert_state(conn, 2600)
            crossed_again = capacity_alerts.update_alert_state(conn, 2700)
            ninety_five = capacity_alerts.update_alert_state(conn, 2850)
            ninety_five_duplicate = capacity_alerts.update_alert_state(conn, 2999)
            full = capacity_alerts.update_alert_state(conn, 3000)
            capacity_alerts.rearm_after_count_drop(conn, 2399)
            rearmed_eighty = capacity_alerts.update_alert_state(conn, 2400)

        self.assertEqual(first.percent, 80)
        self.assertIsNone(duplicate)
        self.assertEqual(ninety.percent, 90)
        self.assertIsNone(dropped)
        self.assertEqual(crossed_again.percent, 90)
        self.assertEqual(ninety_five.percent, 95)
        self.assertIsNone(ninety_five_duplicate)
        self.assertEqual(full.percent, 100)
        self.assertIn("游客新建池塘已受限", full.text)
        self.assertEqual(rearmed_eighty.percent, 80)

    def test_notification_launch_failure_does_not_break_creation_or_repeat(self):
        active = handler._now_iso(time.time())
        self._insert_many(
            [(str(i + 1), active, active, i + 1) for i in range(2399)]
        )

        with self.assertLogs(capacity_alerts.logger, level="ERROR") as logs:
            with mock.patch.object(handler, "_engine_new", return_value=("{}", "intro")), mock.patch.object(
                capacity_alerts.subprocess, "Popen", side_effect=OSError("notifier unavailable")
            ) as popen:
                text = handler.eco_new({"player_id": "newpond"})
                confirmation = handler.eco_new({"player_id": "newpond"})

        self.assertIn("新池初成", text)
        self.assertIn("确认覆盖", confirmation)
        self.assertEqual(popen.call_count, 1)
        self.assertTrue(any("notification launch failed" in line for line in logs.output))
        with sqlite3.connect(self.db_path) as conn:
            self.assertIsNotNone(conn.execute("SELECT 1 FROM eco_sessions WHERE player_id='newpond'").fetchone())

    def test_daily_cleanup_uses_30_days_and_requires_safe_eco_identity(self):
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
            rows = clean_guest_saves.collect_table_rows(180, eco_days=30)

        self.assertEqual(
            [(row["table"], row["player_id"]) for row in rows],
            [("eco_sessions", "guest:expired")],
        )


if __name__ == "__main__":
    unittest.main()
