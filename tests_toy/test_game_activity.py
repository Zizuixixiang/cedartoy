import contextlib
import json
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import admin_dashboard
import game_activity as activity
import server
from vendor_cmd_adapter import base


class GameActivityTests(unittest.TestCase):
    NOW = 1790000000

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="cedartoy-activity-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.db = self.root / "sessions.db"
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.db))
        self.stack.enter_context(patch.object(activity.time, "time", return_value=self.NOW))
        for name in ("_auto_migrate_legacy_account_saves", "_stamp_save_owner"):
            self.stack.enter_context(patch.object(server, name))
        for name in ("_anti_addiction_context", "_anti_addiction_preflight", "_anti_addiction_record_success", "_play_announcements"):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        with sqlite3.connect(self.db) as conn:
            activity.init_db(conn)
        self.ai = {"id": 17, "is_ai": True, "username": "FixtureAI"}
        self.human = {"id": 17, "is_ai": False, "username": "FixtureHuman"}

    def events(self):
        with sqlite3.connect(self.db) as conn:
            return conn.execute("SELECT * FROM game_activity_events").fetchall()

    def record(self, game="puzzle_box", action="submit", user=None, **kwargs):
        activity.record(self.db, game, action, user or self.ai, {"ok": True}, **kwargs)

    def overview(self, range_key="1h", catalog=None, stats=None):
        return admin_dashboard._collect_overview(
            self.db,
            admin_dashboard.range_window(range_key, datetime.fromtimestamp(self.NOW + 1, timezone.utc)),
            catalog or server._activity_catalog(), lambda: stats or {},
        )

    def play(self, action, **params):
        return json.loads(server._tool_play(
            {"game": "puzzle_box", "action": action, "params": params},
            authenticated_account=self.ai,
        ))

    def test_additive_init_twice_keeps_existing_data_and_integrity(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE old_saves (value TEXT)")
            conn.execute("INSERT INTO old_saves VALUES ('untouched')")
            activity.init_db(conn)
            activity.init_db(conn)
            self.assertEqual(conn.execute("SELECT * FROM old_saves").fetchall(), [("untouched",)])
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(len(conn.execute("PRAGMA table_info(game_activity_events)").fetchall()), 5)

    def test_success_failure_auth_and_first_open(self):
        self.play("open", puzzle_id="N01")
        self.play("open", puzzle_id="N01")  # rereading does not play again
        self.play("progress")
        self.assertFalse(self.play("submit", puzzle_id="N02", answer="SECRET")["ok"])
        with self.assertRaises(server._McpError):
            self.play("open", puzzle_id="invalid")
        with self.assertRaises(server._McpError):
            server._tool_play({"game": "puzzle_box", "action": "draw"})
        # An accepted wrong answer is still gameplay, not a failed API call.
        self.assertFalse(self.play("submit", puzzle_id="N01", answer="SECRET")["correct"])
        self.assertEqual([row[4] for row in self.events()], ["open", "submit"])
        self.assertNotIn("SECRET", repr(self.events()))

    def test_structured_errors_and_duplicate_are_excluded(self):
        failures = [
            {"error": "bad"}, {"ok": False}, {"success": False}, {"isError": True},
            {"result": {"isError": True}}, {"duplicate": True},
            {"result": {"content": [{"type": "text", "text": '{"error":"bad"}'}]}},
        ]
        for response in failures:
            activity.record(self.db, "duel", "move", self.ai, response)
        activity.record(self.db, "duel", "move", None, {"ok": True})
        self.assertEqual(self.events(), [])

    def test_dedup_types_operations_ranges_and_catalog(self):
        for seconds in (60, 1200, 7200, 28800, 46800, 90000):
            with patch.object(activity.time, "time", return_value=self.NOW - seconds):
                self.record()
        self.record(user=self.human)
        self.record()
        for range_key, expected in {"10m": 3, "1h": 4, "6h": 5, "12h": 6, "24h": 7}.items():
            data = self.overview(range_key)
            self.assertTrue(data["ok"])
            item = data["games"][0]
            self.assertEqual(item["game"], "puzzle_box")
            self.assertEqual((item["active_users"], item["human_users"], item["ai_users"]), (2, 1, 1))
            self.assertEqual(item["operations"], expected)
            self.assertEqual({g["game"] for g in data["games"]}, {g["game"] for g in server._activity_catalog()})
            self.assertTrue(all(g["operations"] == 0 for g in data["games"][1:]))
        self.assertTrue(server.IDENTITY_GAMES <= {g["game"] for g in data["games"]})
        names = {g["game"]: g["name"] for g in data["games"]}
        self.assertEqual(names["eco"], "瓶中生态")
        self.assertEqual(names["turtle_soup"], "海龟汤")
        self.assertNotIn("admin", names)
        serialized = json.dumps(data)
        for key in ("identity_id", "action", "player_id", "username", "answer", "params", "room_id"):
            self.assertNotIn('"' + key + '"', serialized)

    def test_exact_half_open_range_boundaries(self):
        window = admin_dashboard.range_window("10m", datetime.fromtimestamp(self.NOW, timezone.utc))
        for when in (self.NOW - 601, self.NOW - 600, self.NOW - 1, self.NOW, self.NOW + 1):
            with patch.object(activity.time, "time", return_value=when):
                self.record()
        data = admin_dashboard._collect_overview(self.db, window, server._activity_catalog(), None)
        self.assertEqual(data["games"][0]["operations"], 2)

    def test_polling_and_business_semantics(self):
        reads = [
            ("duel", "state", {}), ("duel", "chips", {"op": "status"}),
            ("eco", "eco_info", {"action": "status"}),
            ("eco", "eco_observe", {"action": "look"}),
            ("mbti", "mbti_get_result", {}), ("ciyuwu", "ciyuwu_info", {}),
            ("fishing", "cmd", {"command": "status; shop\nlook secret"}),
            ("garden_cat", "notes", {}), ("bar", "call", {"function": "summary"}),
            ("puzzle_box", "progress", {}), ("duel", "get_guide", {}),
            ("ai_life", "current_decision", {}), ("camping_plaza", "query_debt", {}),
            ("eco", "arbitrary", {"method": "tools/list"}),
        ]
        for game, action, params in reads:
            self.record(game, action, params=params)
        self.assertEqual(self.events(), [])
        for game, action, params in [
            ("eco", "eco_observe", {"action": "wait"}),
            ("fishing", "cmd", {"command": "open chest-secret"}),
            ("duel", "chips", {"op": "check_in"}),
            ("duel", "chips", {"op": "exchange", "exchange_action": "create"}),
        ]:
            self.record(game, action, params=params)
        self.assertEqual(len(self.events()), 4)
        self.assertNotIn("secret", repr(self.events()))

    def test_real_opaque_fishing_commands_only_count_changed_progress(self):
        self.stack.enter_context(patch.object(base, "SAVE_ROOT", self.root / "saves"))
        for action, params in [
            ("new", {}), ("cmd", {"command": "status"}),
            ("cmd", {"command": "not-a-command"}),
            ("cmd", {"command": "buy unknown_bait 1"}),
            ("cmd", {"command": "cast"}),
        ]:
            server._tool_play({"game": "fishing", "action": action, "params": params}, authenticated_account=self.ai)
        self.assertEqual([row[4] for row in self.events()], ["new", "cmd"])

    def test_deferred_duel_records_only_on_successful_completion(self):
        prepared = server._DeferredDuelCall(
            backend_payload={}, game="duel", action="move", account_user=self.ai,
            account_player_id="17:2", guest_player_id=None, slot=2,
            anti_context=None, announce_player_id="17",
        )
        self.assertEqual(self.events(), [])
        server._finalize_deferred_duel_call(prepared, {"ok": True})
        server._finalize_deferred_duel_call(prepared, {"ok": False})
        self.assertEqual(len(self.events()), 1)
        self.assertEqual(self.events()[0][2:4], ("17", "ai"))

    def test_web_proxy_success_failure_polling_and_human_identity(self):
        for game, path in (("garden_cat", "/web/cmd"), ("workkk", "/shop/buy"),
                           ("camping_plaza", "/api/turn/advance"), ("detroit", "action")):
            for method, status, response in (("GET", 200, {"ok": True}), ("POST", 400, {"ok": True}),
                                              ("POST", 200, {"ok": False}), ("POST", 200, {"ok": True})):
                server._record_web_game_activity(game, method, path, status, json.dumps(response), self.human,
                                                 json.dumps({"command": "water SECRET", "move": "SECRET"}))
        self.assertEqual(len(self.events()), 4)
        self.assertEqual({row[3] for row in self.events()}, {"human"})
        self.assertNotIn("SECRET", repr(self.events()))

    def test_actual_human_test_entry_records_own_identity_and_not_result_retry(self):
        with patch.object(server.mbti_handler, "DB_PATH", str(self.db)), patch.object(server, "_current_account", return_value=self.human):
            server._human_test_action("mbti", "start", "token", {"edition": "quick", "player_id": "spoof"})
            with self.assertRaises(server._McpError):
                server._human_test_action("mbti", "answer_batch", "token", {"answers": []})
            server._human_test_action("mbti", "answer_batch", "token", {"answers": [3] * 16})
            server._human_test_action("mbti", "answer_batch", "token", {"answers": [3] * 16})
            server._human_test_action("mbti", "result", "token", {})
        self.assertEqual([(row[2], row[3], row[4]) for row in self.events()],
                         [("17", "human", "start"), ("17", "human", "answer_batch")])

    def test_future_homepage_game_is_included_without_activity_or_save_support(self):
        home = self.root / "index.html"
        home.write_text('const games = [\n      {id: "future_game", name: "未来游戏"}\n    ];')
        with patch.object(server, "TOY_INDEX_PATH", home):
            item = next(item for item in self.overview()["games"] if item["game"] == "future_game")
        self.assertEqual(item["name"], "未来游戏")
        self.assertEqual(item["active_users"], 0)
        self.assertIsNone(item["save_count"])

    def test_failed_play_result_never_records_at_common_boundary(self):
        with patch.object(server, "_play_vendor_cmd", return_value={"ok": False}):
            server._tool_play({"game": "ai_life", "action": "submit_action"}, authenticated_account=self.ai)
        with patch.object(server, "_play_vendor_cmd", side_effect=server._McpError(-32602, "invalid")):
            with self.assertRaises(server._McpError):
                server._tool_play({"game": "ai_life", "action": "submit_action"}, authenticated_account=self.ai)
        self.assertEqual(self.events(), [])

    def test_save_counts_reuse_public_stats_and_unknown_is_null(self):
        self.play("open", puzzle_id="N01")
        self.play("open", puzzle_id="N02")
        self.stack.enter_context(patch.object(server, "VENDOR_SAVE_ROOT", self.root / "saves"))
        self.stack.enter_context(patch.object(server, "CAMPING_PLAZA_DB_PATH", self.root / "missing-camp.db"))
        self.stack.enter_context(patch.object(server, "count_saved_tarot_sessions", return_value=None))
        for directory in (self.root / "saves" / "fishing" / "empty", self.root / "saves" / "fishing" / "17:2"):
            directory.mkdir(parents=True)
            (directory / ".lock").touch()
        (directory / "fishing_save.json").write_text("{}")
        stats = server._public_game_stats(strict=True)
        rows = {row["game"]: row for row in self.overview(stats=stats)["games"]}
        self.assertEqual(rows["puzzle_box"]["save_count"], 1)
        self.assertEqual(rows["fishing"]["save_count"], 1)
        self.assertEqual(rows["ciyuwu"]["save_count"], stats["ciyuwu"]["save_count"])
        for game in ("duel", "turtle_soup", "mbti", "tarot", "attribute"):
            self.assertIsNone(rows[game]["save_count"])

    def test_retention_and_best_effort_locked_database(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO game_activity_events VALUES (?, 'duel', '1', 'ai', 'move')", (self.NOW - activity.RETENTION_SECONDS - 1,))
        self.record()
        self.assertEqual(len(self.events()), 1)
        with sqlite3.connect(self.db) as locked:
            locked.execute("BEGIN IMMEDIATE")
            with self.assertLogs("game_activity", level="WARNING"):
                self.record()
        self.assertEqual(len(self.events()), 1)

    def test_overview_failure_isolated_from_existing_modules(self):
        good = {"ok": True}
        with patch.object(admin_dashboard, "_collect_duel", return_value=good), patch.object(admin_dashboard, "_collect_turtle", return_value=good):
            for db, stats in ((self.root / "missing.db", lambda: {}),
                              (self.db, lambda: (_ for _ in ()).throw(RuntimeError("broken")))):
                with self.assertLogs("admin_dashboard", level="ERROR"):
                    data = admin_dashboard.build_activity_dashboard(
                        "unused", "unused", sessions_db_path=db,
                        catalog_provider=server._activity_catalog, save_stats_provider=stats)
                self.assertEqual(data["overview"]["games"], [])
                self.assertFalse(data["overview"]["ok"])
                self.assertTrue(data["duel"]["ok"] and data["turtle"]["ok"])
        with patch.object(admin_dashboard, "_collect_duel", side_effect=RuntimeError()), patch.object(admin_dashboard, "_collect_turtle", return_value=good), self.assertLogs("admin_dashboard", level="ERROR"):
            data = admin_dashboard.build_activity_dashboard("unused", "unused", sessions_db_path=self.db,
                catalog_provider=server._activity_catalog, save_stats_provider=lambda: {})
        self.assertTrue(data["overview"]["ok"] and data["turtle"]["ok"])
        self.assertFalse(data["duel"]["ok"])

    def test_missing_schema_initially_empty_without_query_side_effects(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("DROP TABLE game_activity_events")
        self.assertTrue(self.overview()["ok"])
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [])


if __name__ == "__main__":
    unittest.main()
