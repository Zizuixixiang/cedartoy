from __future__ import annotations

import importlib.util
import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import call, patch

from fastapi import HTTPException
from starlette.requests import Request

import server


TOY_ROOT = Path(__file__).resolve().parents[1]
WORKKK_ROOT = TOY_ROOT / "vendor" / "workkk"


def _load_workkk_module():
    spec = importlib.util.spec_from_file_location("workkk_test_main", WORKKK_ROOT / "main.py")
    module = importlib.util.module_from_spec(spec)
    previous_cwd = Path.cwd()
    try:
        os.chdir(WORKKK_ROOT)
        spec.loader.exec_module(module)
    finally:
        os.chdir(previous_cwd)
    return module


workkk_main = _load_workkk_module()


class GardenCatSaveSummaryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="garden-summary-")
        self.root = Path(self.tempdir.name)
        self.root_patch = patch.object(server, "VENDOR_SAVE_ROOT", self.root)
        self.root_patch.start()

    def tearDown(self):
        self.root_patch.stop()
        self.tempdir.cleanup()

    def _write_state(self, player_id, state):
        save_dir = self.root / "garden_cat" / player_id
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "state.json").write_text(
            json.dumps(state, ensure_ascii=False), encoding="utf-8"
        )

    def test_summary_is_read_only_and_returns_stable_fields(self):
        player_id = "987654321:3"
        self._write_state(
            player_id,
            {
                "money": 88,
                "encyclopedia": ["daisy", "tulip"],
                "cat": {"name": "栗子"},
                "last_active_at": 1_700_000_000,
            },
        )
        state_path = self.root / "garden_cat" / player_id / "state.json"
        before = state_path.read_bytes()

        summary = server._garden_cat_save_summary(player_id)

        self.assertEqual(
            summary,
            {
                "money": 88,
                "encyclopedia_count": 2,
                "has_cat": True,
                "last_active": server._epoch_to_local_str(1_700_000_000),
            },
        )
        self.assertEqual(state_path.read_bytes(), before)

    def test_missing_save_returns_none_without_creating_a_directory(self):
        player_id = "987654321:4"
        self.assertIsNone(server._garden_cat_save_summary(player_id))
        self.assertFalse((self.root / "garden_cat" / player_id).exists())

    def test_bad_save_logs_warning_and_returns_none(self):
        save_dir = self.root / "garden_cat" / "987654321:5"
        save_dir.mkdir(parents=True)
        (save_dir / "state.json").write_text("{not-json", encoding="utf-8")
        with self.assertLogs(server.logger, level="WARNING") as captured:
            summary = server._garden_cat_save_summary("987654321:5")
        self.assertIsNone(summary)
        self.assertIn("garden_cat save summary skipped unreadable save", captured.output[0])

    def test_account_my_saves_registry_includes_garden_cat_slot(self):
        player_id = "987654321:2"
        self._write_state(
            player_id,
            {
                "money": 61,
                "encyclopedia": ["daisy"],
                "cat": None,
                "last_active_at": 1_700_000_100,
            },
        )
        account_db = self.root / "accounts.db"
        sqlite3.connect(account_db).close()
        missing_sessions = self.root / "missing-sessions.db"

        def connect():
            conn = sqlite3.connect(account_db)
            conn.row_factory = sqlite3.Row
            return conn

        user = {"id": 987654321, "username": "summaryuser", "is_ai": True}
        with (
            patch.object(server, "_db_connect", side_effect=connect),
            patch.object(server, "SESSIONS_DB_PATH", missing_sessions),
        ):
            result = server._account_saves_for_user(user, migrate_legacy=False)

        self.assertEqual(
            result["saves"]["garden_cat"]["slots"],
            [
                {
                    "slot": 2,
                    "money": 61,
                    "encyclopedia_count": 1,
                    "has_cat": False,
                    "last_active": server._epoch_to_local_str(1_700_000_100),
                }
            ],
        )


class WorkkkSatelliteSaveManagementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="workkk-satellite-")
        self.save_root = Path(self.tempdir.name) / "workkk"
        self.save_root.mkdir()
        self.old_root = workkk_main._SAVE_ROOT
        workkk_main._SAVE_ROOT = str(self.save_root)
        workkk_main._STATE_CACHE.clear()
        workkk_main._STATE_WARNINGS.clear()

    def tearDown(self):
        workkk_main._STATE_CACHE.clear()
        workkk_main._STATE_WARNINGS.clear()
        workkk_main._SAVE_ROOT = self.old_root
        self.tempdir.cleanup()

    def _first_work_action(self, player_id):
        with workkk_main._state_context(player_id):
            result = workkk_main.work_action("get_status", "第一次打卡")
        self.assertTrue(result["入职手册"])

    def test_migrate_moves_disk_and_cache_without_guest_writeback(self):
        self._first_work_action("guest:claimme")
        source_state = workkk_main._STATE_CACHE["guest:claimme"]

        response = workkk_main._migrate_player_save("guest:claimme", "51")

        self.assertTrue(response["migrated"])
        self.assertFalse((self.save_root / "guest:claimme").exists())
        self.assertTrue((self.save_root / "51" / "game_state.json").is_file())
        self.assertNotIn("guest:claimme", workkk_main._STATE_CACHE)
        self.assertIs(workkk_main._STATE_CACHE["51"], source_state)
        with workkk_main._state_context("51"):
            self.assertTrue(workkk_main._s["onboarded"])
        self.assertFalse((self.save_root / "guest:claimme").exists())

    def test_migrate_rejects_cached_target_without_touching_either_save(self):
        self._first_work_action("guest:source")
        self._first_work_action("52")

        with self.assertRaises(HTTPException) as raised:
            workkk_main._migrate_player_save("guest:source", "52")

        self.assertEqual(raised.exception.status_code, 409)
        self.assertTrue((self.save_root / "guest:source" / "game_state.json").is_file())
        self.assertTrue((self.save_root / "52" / "game_state.json").is_file())
        self.assertIn("guest:source", workkk_main._STATE_CACHE)
        self.assertIn("52", workkk_main._STATE_CACHE)

    def test_delete_detaches_cached_slot_and_does_not_regenerate_it(self):
        self._first_work_action("73:5")
        self.assertIn("73:5", workkk_main._STATE_CACHE)

        response = workkk_main._delete_player_save("73:5")

        self.assertTrue(response["deleted"])
        self.assertFalse((self.save_root / "73:5").exists())
        self.assertNotIn("73:5", workkk_main._STATE_CACHE)
        with workkk_main._state_context("74"):
            self.assertFalse(workkk_main._s["onboarded"])
        self.assertFalse((self.save_root / "73:5").exists())

    def test_internal_endpoints_reject_non_loopback_clients(self):
        route_paths = {route.path for route in workkk_main.app.routes}
        self.assertIn("/internal/saves/migrate", route_paths)
        self.assertIn("/internal/saves/delete", route_paths)
        remote_request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/saves/delete",
                "headers": [],
                "client": ("203.0.113.9", 50000),
            }
        )
        with self.assertRaises(HTTPException) as raised:
            workkk_main._require_loopback(remote_request)
        self.assertEqual(raised.exception.status_code, 403)
        loopback_request = Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/internal/saves/delete",
                "headers": [],
                "client": ("127.0.0.1", 50000),
            }
        )
        workkk_main._require_loopback(loopback_request)


class WorkkkPlatformAccountTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="workkk-platform-")
        self.root = Path(self.tempdir.name)
        self.account_db = self.root / "accounts.db"
        self.sessions_db = self.root / "missing-sessions.db"
        self.save_root = self.root / "vendor_saves"
        self.save_root.mkdir()
        with sqlite3.connect(self.account_db) as conn:
            server._init_guest_claim_table(conn)
        self.patches = [
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
            patch.object(server, "SESSIONS_DB_PATH", self.sessions_db),
            patch.object(server, "_db_connect", side_effect=self._connect),
        ]
        for active in self.patches:
            active.start()
        self.user = {"id": 81, "username": "workuser", "is_ai": True}

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.tempdir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.account_db)
        conn.row_factory = sqlite3.Row
        return conn

    def _write_workkk_save(self, player_id, day=1):
        save_dir = self.save_root / "workkk" / player_id
        save_dir.mkdir(parents=True, exist_ok=True)
        (save_dir / "game_state.json").write_text(
            json.dumps({"day_count": day, "salary_balance": day * 10}),
            encoding="utf-8",
        )

    def _insert_claim_code(self, code, player_id):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO guest_claim_codes (code, guest_player_id) VALUES (?, ?)",
                (code, player_id),
            )
            conn.commit()

    def _rename_workkk(self, old_player_id, target_player_id):
        (self.save_root / "workkk" / old_player_id).rename(
            self.save_root / "workkk" / target_player_id
        )
        return True

    def _rename_garden(self, old_player_id, target_player_id):
        (self.save_root / "garden_cat" / old_player_id).rename(
            self.save_root / "garden_cat" / target_player_id
        )
        return True

    def test_guest_first_successful_work_action_gets_claim_code(self):
        response = {
            "jsonrpc": "2.0",
            "id": "workkk-test",
            "result": {"content": [{"type": "text", "text": "ok"}]},
        }
        with (
            patch.object(server, "_play_workkk", return_value=response) as play_mock,
            patch.object(server, "_ensure_guest_claim_code", return_value="claim-once") as code_mock,
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
        ):
            result = json.loads(
                server._tool_play_inner(
                    {
                        "game": "workkk",
                        "action": "work_action",
                        "params": {
                            "player_id": "firstvisit",
                            "action": "get_status",
                            "thought": "第一次打卡",
                        },
                    }
                )
            )

        self.assertIn("一次性认领码：claim-once", result["guest_save_notice"])
        code_mock.assert_called_once_with("guest:firstvisit")
        self.assertEqual(play_mock.call_args.args[0]["params"]["player_id"], "guest:firstvisit")
        self.assertIn("workkk", server.PERSISTENT_SAVE_GAMES)
        self.assertNotIn("workkk", server.VENDOR_GAMES)

    def test_claim_success_migrates_workkk_and_consumes_code(self):
        self._write_workkk_save("guest:claimok", day=3)
        self._insert_claim_code("ok-code", "guest:claimok")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_workkk_save", side_effect=self._rename_workkk),
        ):
            result = server._claim_guest_saves("token", "ok-code")

        self.assertIn("vendor_saves/workkk", result["migrated"])
        self.assertEqual(result["slot"], 1)
        self.assertEqual(result["target_player_id"], "81")
        self.assertFalse((self.save_root / "workkk" / "guest:claimok").exists())
        self.assertTrue((self.save_root / "workkk" / "81" / "game_state.json").is_file())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT claimed_by, claimed_slot FROM guest_claim_codes WHERE code = 'ok-code'"
            ).fetchone()
        self.assertEqual(row["claimed_by"], 81)
        self.assertEqual(row["claimed_slot"], 1)

    def test_claim_slot_two_uses_canonical_target_and_returns_it(self):
        self._write_workkk_save("guest:slot2", day=3)
        self._insert_claim_code("slot2-code", "guest:slot2")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_workkk_save", side_effect=self._rename_workkk),
        ):
            result = server._claim_guest_saves("token", "slot2-code", slot=2)

        self.assertEqual(result["slot"], 2)
        self.assertEqual(result["target_player_id"], "81:2")
        self.assertTrue((self.save_root / "workkk" / "81:2" / "game_state.json").is_file())

    def test_claim_rejects_non_integer_and_out_of_range_slots(self):
        for slot in (0, 6, "2", True, None):
            with (
                self.subTest(slot=slot),
                patch.object(server, "_current_account", return_value=self.user),
            ):
                with self.assertRaises(server._McpError) as raised:
                    server._claim_guest_saves("token", "unused", slot=slot)
                self.assertIn("slot", raised.exception.message)

    def test_other_slot_conflict_does_not_block_empty_selected_slot(self):
        self._write_workkk_save("guest:choose2", day=2)
        self._write_workkk_save("81", day=9)
        self._insert_claim_code("choose2-code", "guest:choose2")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_workkk_save", side_effect=self._rename_workkk),
        ):
            result = server._claim_guest_saves("token", "choose2-code", slot=2)

        self.assertEqual(result["target_player_id"], "81:2")
        self.assertTrue((self.save_root / "workkk" / "81").is_dir())
        self.assertTrue((self.save_root / "workkk" / "81:2").is_dir())

    def test_selected_slot_conflict_cancels_and_does_not_consume_code(self):
        self._write_workkk_save("guest:slotconflict", day=2)
        self._write_workkk_save("81:2", day=9)
        self._insert_claim_code("slot-conflict-code", "guest:slotconflict")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_workkk_save") as migrate_mock,
        ):
            with self.assertRaises(server._McpError):
                server._claim_guest_saves("token", "slot-conflict-code", slot=2)
        migrate_mock.assert_not_called()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT claimed_by FROM guest_claim_codes WHERE code = 'slot-conflict-code'"
            ).fetchone()
        self.assertIsNone(row["claimed_by"])

    def test_claim_conflict_is_all_or_nothing_and_code_remains_unused(self):
        self._write_workkk_save("guest:conflict", day=2)
        self._write_workkk_save("81", day=9)
        other_save = self.save_root / "garden_cat" / "guest:conflict" / "state.json"
        other_save.parent.mkdir(parents=True)
        other_save.write_text('{"money": 50}', encoding="utf-8")
        self._insert_claim_code("conflict-code", "guest:conflict")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_workkk_save") as migrate_mock,
        ):
            with self.assertRaises(server._McpError) as raised:
                server._claim_guest_saves("token", "conflict-code")

        self.assertIn("workkk", raised.exception.message)
        migrate_mock.assert_not_called()
        self.assertTrue((self.save_root / "workkk" / "guest:conflict" / "game_state.json").is_file())
        self.assertTrue((self.save_root / "workkk" / "81" / "game_state.json").is_file())
        self.assertTrue(other_save.is_file())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT claimed_by FROM guest_claim_codes WHERE code = 'conflict-code'"
            ).fetchone()
        self.assertIsNone(row["claimed_by"])

    def test_claim_stops_before_other_games_when_satellite_is_unavailable(self):
        self._write_workkk_save("guest:offline", day=2)
        other_save = self.save_root / "garden_cat" / "guest:offline" / "state.json"
        other_save.parent.mkdir(parents=True)
        other_save.write_text('{"money": 50}', encoding="utf-8")
        self._insert_claim_code("offline-code", "guest:offline")

        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_garden_cat_save", side_effect=self._rename_garden) as garden_mock,
            patch.object(
                server,
                "_migrate_workkk_save",
                side_effect=server._McpError(-32603, "satellite unavailable"),
            ),
        ):
            with self.assertRaises(server._McpError):
                server._claim_guest_saves("token", "offline-code")

        self.assertEqual(
            garden_mock.call_args_list,
            [
                call("guest:offline", "81"),
                call("81", "guest:offline"),
            ],
        )
        self.assertTrue(other_save.is_file())
        self.assertFalse((self.save_root / "garden_cat" / "81").exists())
        with self._connect() as conn:
            row = conn.execute(
                "SELECT claimed_by FROM guest_claim_codes WHERE code = 'offline-code'"
            ).fetchone()
        self.assertIsNone(row["claimed_by"])

    def test_garden_failure_happens_before_workkk_and_keeps_code(self):
        self._write_workkk_save("guest:gardenoffline", day=2)
        garden_state = self.save_root / "garden_cat" / "guest:gardenoffline" / "state.json"
        garden_state.parent.mkdir(parents=True)
        garden_state.write_text('{"money": 50}', encoding="utf-8")
        self._insert_claim_code("garden-offline-code", "guest:gardenoffline")
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(
                server,
                "_migrate_garden_cat_save",
                side_effect=server._McpError(-32603, "garden unavailable"),
            ),
            patch.object(server, "_migrate_workkk_save") as workkk_mock,
        ):
            with self.assertRaises(server._McpError):
                server._claim_guest_saves("token", "garden-offline-code")
        workkk_mock.assert_not_called()
        self.assertTrue(garden_state.is_file())
        with self._connect() as conn:
            claimed_by = conn.execute(
                "SELECT claimed_by FROM guest_claim_codes WHERE code = 'garden-offline-code'"
            ).fetchone()["claimed_by"]
        self.assertIsNone(claimed_by)

    def test_later_directory_failure_rolls_back_both_resident_services(self):
        self._write_workkk_save("guest:rollback", day=2)
        garden_state = self.save_root / "garden_cat" / "guest:rollback" / "state.json"
        garden_state.parent.mkdir(parents=True)
        garden_state.write_text('{"money": 50}', encoding="utf-8")
        arcade_dir = self.save_root / "arcade" / "guest:rollback"
        arcade_dir.mkdir(parents=True)
        (arcade_dir / "save.json").write_text("{}", encoding="utf-8")
        self._insert_claim_code("rollback-code", "guest:rollback")
        service_calls = []

        def managed_move(game, source, target):
            service_calls.append((game, source, target))
            os.rename(self.save_root / game / source, self.save_root / game / target)
            return True

        original_rename = Path.rename

        def fail_arcade_rename(path, target):
            if path == arcade_dir:
                raise OSError("injected ordinary directory failure")
            return original_rename(path, target)

        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(
                server,
                "_migrate_garden_cat_save",
                side_effect=lambda source, target: managed_move("garden_cat", source, target),
            ),
            patch.object(
                server,
                "_migrate_workkk_save",
                side_effect=lambda source, target: managed_move("workkk", source, target),
            ),
            patch.object(Path, "rename", new=fail_arcade_rename),
        ):
            with self.assertRaises(OSError):
                server._claim_guest_saves("token", "rollback-code")

        self.assertEqual(
            service_calls,
            [
                ("garden_cat", "guest:rollback", "81"),
                ("workkk", "guest:rollback", "81"),
                ("workkk", "81", "guest:rollback"),
                ("garden_cat", "81", "guest:rollback"),
            ],
        )
        self.assertTrue(garden_state.is_file())
        self.assertTrue((self.save_root / "workkk" / "guest:rollback").is_dir())
        with self._connect() as conn:
            claimed_by = conn.execute(
                "SELECT claimed_by FROM guest_claim_codes WHERE code = 'rollback-code'"
            ).fetchone()["claimed_by"]
        self.assertIsNone(claimed_by)

    def test_legacy_username_migration_uses_satellite_helper_only(self):
        self._write_workkk_save("workuser", day=4)
        with patch.object(
            server,
            "_migrate_workkk_save",
            side_effect=server._McpError(-32603, "satellite unavailable"),
        ) as migrate_mock:
            migrated = server._auto_migrate_legacy_username_saves(
                self.user, "workuser"
            )

        migrate_mock.assert_called_once_with("workuser", "81")
        self.assertNotIn("vendor_saves/workkk", migrated)
        self.assertTrue(
            (self.save_root / "workkk" / "workuser" / "game_state.json").is_file()
        )
        self.assertFalse((self.save_root / "workkk" / "81").exists())

    def test_delete_save_uses_only_current_account_slot_identities_1_to_5(self):
        seen = []

        def delete(player_id):
            seen.append(player_id)
            return {"target": f"vendor_saves/workkk/{player_id}", "rows": 1}

        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_delete_workkk_save", side_effect=delete),
        ):
            results = [
                server._delete_save(
                    {"game": "workkk", "slot": slot, "confirm": True}, "token"
                )
                for slot in range(1, 6)
            ]

        self.assertEqual(seen, ["81", "81:2", "81:3", "81:4", "81:5"])
        self.assertEqual([item["player_id"] for item in results], seen)
        self.assertTrue(all(item["message"] == "已删除存档。" for item in results))

    def test_garden_delete_save_uses_selected_account_slot_helper(self):
        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(
                server,
                "_delete_garden_cat_save",
                return_value={"target": "vendor_saves/garden_cat/81:2", "rows": 1},
            ) as delete_mock,
        ):
            result = server._delete_save(
                {"game": "garden_cat", "slot": 2, "confirm": True}, "token"
            )

        delete_mock.assert_called_once_with("81:2")
        self.assertEqual(result["slot"], 2)
        self.assertEqual(result["player_id"], "81:2")

    def test_multi_game_claim_uses_one_selected_canonical_target(self):
        self._write_workkk_save("guest:multi", day=4)
        garden_state = self.save_root / "garden_cat" / "guest:multi" / "state.json"
        garden_state.parent.mkdir(parents=True)
        garden_state.write_text('{"money": 22}', encoding="utf-8")
        market_dir = self.save_root / "market" / "guest:multi"
        market_dir.mkdir(parents=True)
        (market_dir / "save.json").write_text("{}", encoding="utf-8")
        self._insert_claim_code("multi-code", "guest:multi")
        seen = []

        def garden_move(source, target):
            seen.append(("garden_cat", source, target))
            return self._rename_garden(source, target)

        def workkk_move(source, target):
            seen.append(("workkk", source, target))
            return self._rename_workkk(source, target)

        with (
            patch.object(server, "_current_account", return_value=self.user),
            patch.object(server, "_migrate_garden_cat_save", side_effect=garden_move),
            patch.object(server, "_migrate_workkk_save", side_effect=workkk_move),
        ):
            result = server._claim_guest_saves("token", "multi-code", slot=4)

        self.assertEqual(result["target_player_id"], "81:4")
        self.assertEqual(
            seen,
            [
                ("garden_cat", "guest:multi", "81:4"),
                ("workkk", "guest:multi", "81:4"),
            ],
        )
        self.assertTrue((self.save_root / "market" / "81:4" / "save.json").is_file())


class ClaimedGuestTombstoneTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="guest-tombstone-")
        self.root = Path(self.tempdir.name)
        self.account_db = self.root / "accounts.db"
        self.save_root = self.root / "vendor_saves"
        self.save_root.mkdir()
        with sqlite3.connect(self.account_db) as conn:
            server._init_guest_claim_table(conn)
        self.patches = [
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
            patch.object(server, "SESSIONS_DB_PATH", self.root / "missing.db"),
            patch.object(server, "_db_connect", side_effect=self._connect),
        ]
        for active in self.patches:
            active.start()
        self.user = {"id": 81, "username": "workuser", "is_ai": True}
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO guest_claim_codes
                    (code, guest_player_id, claimed_by, claimed_slot)
                VALUES ('claimed-code', 'guest:retired', 81, 3)
                """
            )
            conn.commit()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.tempdir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.account_db)
        conn.row_factory = sqlite3.Row
        return conn

    def test_claimed_guest_is_rejected_before_three_game_dispatches(self):
        dispatches = (
            ("garden_cat", "status", "_play_garden_cat"),
            ("workkk", "work_action", "_play_workkk"),
            ("market", "status", "_play_vendor_cmd"),
        )
        for game, action, dispatch_name in dispatches:
            with self.subTest(game=game), patch.object(server, dispatch_name) as dispatch:
                with self.assertRaises(server._McpError) as raised:
                    server._tool_play_inner(
                        {
                            "game": game,
                            "action": action,
                            "params": {"player_id": "retired"},
                        }
                    )
                self.assertIn("该游客身份已认领", raised.exception.message)
                dispatch.assert_not_called()
        self.assertFalse(any(self.save_root.rglob("retired")))

    def test_unclaimed_guest_and_token_account_still_dispatch(self):
        response = {"result": {"content": [{"type": "text", "text": "ok"}]}}
        common_patches = (
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_ensure_guest_claim_code", return_value=None),
        )
        for active in common_patches:
            active.start()
        try:
            with patch.object(server, "_play_vendor_cmd", return_value=response) as dispatch:
                server._tool_play_inner(
                    {"game": "market", "action": "status", "params": {"player_id": "fresh"}}
                )
                self.assertEqual(dispatch.call_args.args[1]["params"]["player_id"], "guest:fresh")
            with (
                patch.object(server, "_current_account", return_value=self.user),
                patch.object(server, "_auto_migrate_legacy_account_saves"),
                patch.object(server, "_play_vendor_cmd", return_value=response) as dispatch,
            ):
                server._tool_play_inner(
                    {
                        "game": "market",
                        "action": "status",
                        "params": {"player_id": "retired", "slot": 3},
                    },
                    path_token="token",
                )
                self.assertEqual(dispatch.call_args.args[1]["params"]["player_id"], "81:3")
        finally:
            for active in reversed(common_patches):
                active.stop()


if __name__ == "__main__":
    unittest.main()
