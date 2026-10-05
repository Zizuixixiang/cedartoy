"""Legacy Camping checks without importing vendor engines or using live services."""

from pathlib import Path
import re
import sqlite3
import tempfile
from threading import Lock
import unittest
from unittest.mock import Mock, call

from cedar_backend import save_management


class McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


class CampingLegacyMigrationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="camping-legacy-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.summary = Mock(return_value=None)
        self.migrate = Mock(return_value=True)
        self.clock = Mock()
        self.clock.monotonic.return_value = 100
        self.cache = {}
        self.dependencies = dict(
            DIRECTORY_VENDOR_GAMES=(),
            GAME_PLAYER_ID_RE=re.compile(r"^[a-zA-Z0-9]{1,10}$"),
            SESSIONS_DB_PATH=self.root / "missing-sessions.db",
            VENDOR_SAVE_ROOT=self.root,
            _CAMPING_LEGACY_MISS_TTL=300,
            _CAMPING_LEGACY_MISSES=self.cache,
            _CAMPING_LEGACY_MISSES_LOCK=Lock(),
            _McpError=McpError,
            _camping_plaza_save_summary=self.summary,
            _directory_vendor_save_exists=Mock(return_value=False),
            _migrate_camping_plaza_save=self.migrate,
            _migrate_garden_cat_save=Mock(),
            _migrate_workkk_save=Mock(return_value=True),
            _sessions_db_connect=Mock(side_effect=AssertionError("unexpected DB access")),
            _sessions_table_columns=Mock(),
            _table_exists=Mock(),
            logger=Mock(),
            nowhere_storage=Mock(),
            sqlite3=sqlite3,
            time=self.clock,
        )

    def check(self, username="OldName", user_id=81):
        return save_management._auto_migrate_legacy_username_saves(
            {"id": user_id}, username, **self.dependencies
        )

    def test_missing_legacy_skips_target_and_reuses_negative_until_expiry(self):
        self.assertEqual(self.check(), [])
        self.summary.assert_called_once_with("OldName")
        self.clock.monotonic.return_value = 399
        self.assertEqual(self.check(), [])
        self.summary.assert_called_once_with("OldName")
        self.migrate.assert_not_called()

    def test_ttl_expires_without_being_extended_by_cache_hits(self):
        self.check()
        self.clock.monotonic.return_value = 399
        self.check()
        self.clock.monotonic.return_value = 400
        self.summary.side_effect = [{}, None]
        self.assertEqual(self.check(), ["camping_plaza"])
        self.assertEqual(self.summary.call_args_list, [call("OldName"), call("OldName"), call("81")])
        self.migrate.assert_called_once_with("OldName", "81")

    def test_aliases_and_accounts_do_not_share_negative_cache(self):
        for username, user_id in (("OldName", 81), ("NewName", 81), ("oldname", 81), ("OldName", 82)):
            self.check(username, user_id)
            self.check(username, user_id)
        self.assertEqual(self.summary.call_args_list,
                         [call("OldName"), call("NewName"), call("oldname"), call("OldName")])

    def test_existing_legacy_checks_target_and_preserves_migration_result(self):
        # An empty summary is still an existing save. Neither success nor a
        # false migration result may create a negative cache entry.
        self.summary.side_effect = [{}, None, {}, None]
        self.assertEqual(self.check(), ["camping_plaza"])
        self.migrate.return_value = False
        self.assertEqual(self.check(), [])
        self.assertEqual(self.summary.call_args_list,
                         [call("OldName"), call("81"), call("OldName"), call("81")])
        self.assertEqual(self.migrate.call_args_list, [call("OldName", "81")] * 2)
        self.assertEqual(self.cache, {})

    def test_existing_target_skips_migration_without_caching(self):
        self.summary.return_value = {}
        self.assertEqual(self.check(), [])
        self.assertEqual(self.check(), [])
        self.assertEqual(self.summary.call_args_list, [call("OldName"), call("81")] * 2)
        self.migrate.assert_not_called()
        self.assertEqual(self.cache, {})

    def test_summary_errors_are_not_cached(self):
        for failing_player in ("OldName", "81"):
            with self.subTest(failing_player=failing_player):
                self.summary.reset_mock()
                def summary(player):
                    if player == failing_player:
                        raise McpError(-32603, "unavailable")
                    return {}
                self.summary.side_effect = summary
                self.assertEqual(self.check(), [])
                self.assertEqual(self.check(), [])
                expected = [call("OldName")] if failing_player == "OldName" else [call("OldName"), call("81")]
                self.assertEqual(self.summary.call_args_list, expected * 2)
                self.assertEqual(self.cache, {})
        self.migrate.assert_not_called()

    def test_unexpected_summary_exception_is_not_cached(self):
        self.summary.side_effect = RuntimeError("unexpected")
        for _ in range(2):
            with self.assertRaises(RuntimeError):
                self.check()
        self.assertEqual(self.summary.call_args_list, [call("OldName")] * 2)
        self.assertEqual(self.cache, {})

    def test_migration_conflict_or_error_is_retried(self):
        for code in (-32602, -32603):
            with self.subTest(code=code):
                self.summary.reset_mock()
                self.migrate.reset_mock()
                self.summary.side_effect = [{}, None, {}, None]
                self.migrate.side_effect = McpError(code, "migration deferred")
                self.assertEqual(self.check(), [])
                self.assertEqual(self.check(), [])
                self.assertEqual(self.summary.call_args_list, [call("OldName"), call("81")] * 2)
                self.assertEqual(self.migrate.call_args_list, [call("OldName", "81")] * 2)
                self.assertEqual(self.cache, {})

    def test_cached_camping_miss_does_not_skip_other_game_migration(self):
        self.check()
        state = self.root / "workkk" / "OldName" / "game_state.json"
        state.parent.mkdir(parents=True)
        state.write_text("{}", encoding="utf-8")
        self.assertEqual(self.check(), ["vendor_saves/workkk"])
        self.dependencies["_migrate_workkk_save"].assert_called_once_with("OldName", "81")
        self.summary.assert_called_once_with("OldName")

    def test_summary_missing_error_still_maps_to_none(self):
        db_path = self.root / "camping.db"
        db_path.touch()
        summary = save_management._camping_plaza_save_summary(
            "OldName", timeout=20, CAMPING_PLAZA_DB_PATH=db_path,
            _McpError=McpError,
            _camping_plaza_save_admin=Mock(side_effect=McpError(-32004, "missing")),
        )
        self.assertIsNone(summary)


if __name__ == "__main__":
    unittest.main()
