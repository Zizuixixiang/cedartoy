import sqlite3
import unittest
from unittest.mock import patch

import avatar_appearances as appearances
import server
from tests_toy import test_account_avatar as avatar_tests


class OneWAppearanceTests(unittest.TestCase):
    setUp = avatar_tests.AccountAvatarTests.setUp
    tearDown = avatar_tests.AccountAvatarTests.tearDown
    _connect = avatar_tests.AccountAvatarTests._connect

    def user(self, conn, user_id, *, ai=False, deleted=False):
        conn.execute(
            "INSERT INTO toy_users (id, username, password_hash, is_ai, deleted_at) VALUES (?, ?, 'unused', ?, ?)",
            (user_id, f"OneW{user_id}", int(ai), "2026-09-25 10:00:00" if deleted else None),
        )

    def bind(self, conn, human, ai, date="2026-09-25 23:59:59"):
        conn.execute(
            "INSERT INTO user_bindings (human_user_id, ai_user_id, created_at) VALUES (?, ?, ?)",
            (human, ai, date),
        )

    def recipients(self, conn, key):
        return {row[0] for row in conn.execute(
            "SELECT user_id FROM account_avatar_inventory WHERE item_key = ?", (key,)
        )}

    def test_late_9997_through_10000_and_deleted_and_10001(self):
        with self._connect() as conn:
            self.user(conn, 9996, deleted=True)
            appearances.grant_one_w(conn)
            for user_id in range(9997, 10002):
                self.user(conn, user_id)
                appearances.grant_one_w(conn)
            self.assertEqual(self.recipients(conn, "cedartoy_1w_decoration"), {9997, 9998, 9999, 10000})
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})
            self.assertEqual(conn.execute("SELECT * FROM account_avatar_selection").fetchall(), [])

    def test_human_anchor_late_binding_boundary_and_restart(self):
        with self._connect() as conn:
            self.user(conn, 10000)
            for user_id in (10001, 10002, 10003):
                self.user(conn, user_id, ai=True)
            appearances.grant_one_w(conn)
            self.bind(conn, 10000, 10001)
            self.bind(conn, 10000, 10002, "2026-09-26 00:00:00")
            self.bind(conn, 10000, 10003, "2026-09-26 08:00:00")
        # A fresh connection after the event still recovers pre-deadline bindings.
        with patch.object(appearances.time, "time", return_value=appearances.ONE_W_END_EPOCH + 86400):
            appearances.watch_one_w(self.db_path)
        with self._connect() as conn:
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000, 10001})
            self.assertEqual(self.recipients(conn, "cedartoy_1w_decoration"), {10000})

    def test_ai_anchor_owner_and_siblings_including_late_sibling(self):
        with self._connect() as conn:
            self.user(conn, 50)
            for user_id in (9999, 10000, 10001, 10002, 10003):
                self.user(conn, user_id, ai=True, deleted=user_id == 10003)
            self.bind(conn, 50, 9999, "2026-09-24 12:00:00")
            appearances.grant_one_w(conn)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})
            self.bind(conn, 50, 10000, "2026-09-25 20:00:00")
            appearances.grant_one_w(conn)
            self.bind(conn, 50, 10001)
            self.bind(conn, 50, 10002, "2026-09-26 00:00:00")
            self.bind(conn, 50, 10003)
            appearances.grant_one_w(conn)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {50, 9999, 10000, 10001})

    def test_ai_anchor_owner_link_after_deadline_excludes_whole_group(self):
        with self._connect() as conn:
            self.user(conn, 50)
            self.user(conn, 9999, ai=True)
            self.user(conn, 10000, ai=True)
            self.bind(conn, 50, 9999)
            self.bind(conn, 50, 10000, "2026-09-26 00:00:00")
            appearances.grant_one_w(conn)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})

    def test_idempotence_preserves_inventory_timestamps_and_all_selection_rows(self):
        with self._connect() as conn:
            for user_id in (9998, 9999, 10000):
                self.user(conn, user_id)
            appearances.grant(conn, [9998, 9999], appearances.CATALOG)
            appearances.select(conn, 9998, "cedartoy_1w")
            appearances.select(conn, 9999, None)
            selections = [tuple(r) for r in conn.execute("SELECT * FROM account_avatar_selection ORDER BY user_id")]
            appearances.grant_one_w(conn)
            inventory = [tuple(r) for r in conn.execute("SELECT * FROM account_avatar_inventory ORDER BY user_id, item_key")]
        for _ in range(2):
            self.assertEqual(appearances.reconcile_one_w(self.db_path), {"decoration": 0, "frame": 0})
        with self._connect() as conn:
            self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM account_avatar_selection ORDER BY user_id")], selections)
            self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM account_avatar_inventory ORDER BY user_id, item_key")], inventory)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_real_registration_paths_grant_before_return(self):
        with self._connect() as conn:
            self.user(conn, 9996)
        results = [
            server._register_human("MilestoneHuman", "secret-pass", client_ip="192.0.2.1"),
            server._login_or_register_ai("MilestoneAI", "secret-pass", client_ip="192.0.2.2"),
            server._login_or_register("LegacyMilestone", "secret-pass", is_ai=True, client_ip="192.0.2.3"),
            server._register_human("TenThousand", "secret-pass", client_ip="192.0.2.4"),
            server._login_or_register_ai("AfterMilestone", "secret-pass", client_ip="192.0.2.5"),
        ]
        self.assertEqual([r["user"]["id"] for r in results], list(range(9997, 10002)))
        with self._connect() as conn:
            self.assertEqual(self.recipients(conn, "cedartoy_1w_decoration"), {9997, 9998, 9999, 10000})
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})

    def test_binding_hook_grants_atomically_and_respects_actual_created_at(self):
        with self._connect() as conn:
            self.user(conn, 10000)
            self.user(conn, 10001, ai=True)
            # Model the production AFTER INSERT localtime trigger at the boundary.
            conn.execute("""
                CREATE TRIGGER binding_test_time AFTER INSERT ON user_bindings
                BEGIN
                    UPDATE user_bindings SET created_at = '2026-09-25 23:59:59'
                    WHERE id = NEW.id;
                END
            """)
            server._ensure_ai_binding(conn, 10000, 10001)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000, 10001})
            server._ensure_ai_binding(conn, 10000, 10001)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000, 10001})
            conn.rollback()
        with self._connect() as conn:
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), set())

    def test_startup_reconciles_without_starting_expired_watcher(self):
        with self._connect() as conn:
            self.user(conn, 10000)
        with patch.object(server, "Thread") as thread, \
             patch.object(server, "ThreadPoolHTTPServer") as httpd, \
             patch.object(server, "_init_announcement_tables"), \
             patch.object(server.time, "time", return_value=appearances.ONE_W_END_EPOCH + 1):
            server.main()
            thread.assert_not_called()
            httpd.return_value.serve_forever.assert_called_once()
        with self._connect() as conn:
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})

    def test_startup_starts_temporary_watcher_during_window(self):
        with patch.object(server, "Thread") as thread, \
             patch.object(server, "ThreadPoolHTTPServer"), \
             patch.object(server, "_init_announcement_tables"), \
             patch.object(server.time, "time", return_value=appearances.ONE_W_END_EPOCH - 1):
            server.main()
            thread.assert_called_once_with(
                target=appearances.watch_one_w, args=(self.db_path,),
                name="avatar-1w-catchup", daemon=True,
            )
            thread.return_value.start.assert_called_once()

    def test_deleted_anchor_never_grants_and_post_deadline_binding_hook_excludes(self):
        with self._connect() as conn:
            self.user(conn, 10000, deleted=True)
            self.user(conn, 10001, ai=True)
            self.bind(conn, 10000, 10001)
            appearances.grant_one_w(conn)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), set())
            conn.execute("UPDATE toy_users SET deleted_at=NULL WHERE id=10000")
            conn.execute("UPDATE user_bindings SET created_at='2026-09-26 00:00:00'")
            server._ensure_ai_binding(conn, 10000, 10001)
            self.assertEqual(self.recipients(conn, "cedartoy_1w"), {10000})

    def test_watch_final_sweep_and_date_guard(self):
        end = appearances.ONE_W_END_EPOCH
        with patch.object(appearances, "reconcile_one_w", return_value={}) as sweep, \
             patch.object(appearances.time, "time", side_effect=[end - 1, end - 1, end]), \
             patch.object(appearances.time, "sleep") as sleep:
            appearances.watch_one_w(self.db_path)
            self.assertEqual(sweep.call_count, 2)
            sleep.assert_called_once_with(1)
        with patch.object(appearances, "reconcile_one_w", return_value={}) as sweep, \
             patch.object(appearances.time, "time", return_value=end + 86400), \
             patch.object(appearances.time, "sleep") as sleep:
            appearances.watch_one_w(self.db_path)
            sweep.assert_called_once()
            sleep.assert_not_called()

    def test_missing_database_is_not_created(self):
        missing = self.db_path.parent / "wrong.db"
        with self.assertRaises(sqlite3.OperationalError):
            appearances.reconcile_one_w(missing)
        self.assertFalse(missing.exists())

    def test_watch_retries_failure_and_surfaces_failed_final_sweep(self):
        end = appearances.ONE_W_END_EPOCH
        with patch.object(appearances, "reconcile_one_w", side_effect=[sqlite3.OperationalError("locked"), {}]) as sweep, \
             patch.object(appearances.time, "time", side_effect=[end - 1, end - 1, end]), \
             patch.object(appearances.time, "sleep"), \
             patch.object(appearances.logging, "exception"):
            appearances.watch_one_w(self.db_path)
            self.assertEqual(sweep.call_count, 2)
        with patch.object(appearances, "reconcile_one_w", side_effect=sqlite3.OperationalError("locked")), \
             patch.object(appearances.time, "time", return_value=end), \
             patch.object(appearances.logging, "exception"), \
             self.assertRaises(sqlite3.OperationalError):
            appearances.watch_one_w(self.db_path)


if __name__ == "__main__":
    unittest.main()
