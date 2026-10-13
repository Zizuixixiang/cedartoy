import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

import candle_feedback
import server
from vendor_cmd_adapter import base, candle
from vendor_cmd_adapter.base import VendorCmdError


class CandleTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="candle-test-")))
        self.db = self.temp / "sessions.db"
        self.root = self.temp / "saves"
        self.stack.enter_context(patch.object(base, "SAVE_ROOT", self.root))
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.db))
        self.stack.enter_context(patch.dict(os.environ, {"SESSIONS_DB": str(self.db), "CANDLE_TEST_SECRET": "must-not-be-in-child"}))

    def play(self, action, player="41", **params):
        return candle.play({"action": action, "player_id": player, **params})["text"]

    def finish(self, player="41"):
        self.play("start", player)
        for _ in range(12):
            self.play("next", player)

    def data(self, player="41"):
        return json.loads((self.root / "candle" / player / candle.SAVE_NAME).read_text())

    def rows(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute("SELECT * FROM candle_feedback ORDER BY id")]

    def test_progress_review_and_identity_slot_isolation(self):
        self.assertIn("请先调用 start", self.play("next"))
        for player in ("41", "42", "41:2", "guest:41"):
            self.assertIn("第 1 幕", self.play("start", player))
        self.assertIn("第 2 幕", self.play("next"))
        self.assertIn("第 1 幕", self.play("review", act=1))
        self.assertIn("不能提前看", self.play("review", act=3))
        self.assertIn("你已看过的 2 幕", self.play("recall"))
        self.assertEqual(self.data()["state"]["act"], 2)
        for player in ("42", "41:2", "guest:41"):
            self.assertEqual(self.data(player)["state"]["act"], 1)
        with self.assertRaises(VendorCmdError):
            self.play("start", "../41")

    def test_choose_zero_and_positive_only_once_comment_private_and_restart(self):
        self.play("start")
        self.assertIn("还没落幕", self.play("choose", act=0))
        self.assertIn("还没落幕", self.play("comment", text="提前留言"))
        for _ in range(12):
            self.play("next")
        self.assertIn("全剧终", self.play("next"))
        self.assertIn("弃权", self.play("cmd", command="choose 0"))
        self.assertIn("已经选过", self.play("choose", act=5))
        self.assertIn("已经选过", self.play("choose", act=0))
        text = '<script>alert(1)</script> $(touch impossible) 写给作者'
        self.assertIn(text, self.play("comment", text=text, visibility="public"))
        self.assertIn("太长", self.play("comment", text="字" * 301))
        rows = self.rows()
        self.assertEqual([(r["kind"], r["chosen"]) for r in rows], [("choose", 0), ("comment", 0)])
        self.assertEqual(rows[1]["comment"], text)
        self.assertEqual(rows[1]["visibility"], "private_to_author")
        self.assertEqual(candle.save_summary("41"), {"act": 13, "finished": True})
        old_run = rows[0]["run_id"]
        for action, params in (("start", {}), ("new", {}), ("reset", {}), ("cmd", {"command": "START"}), ("cmd", {"command": "清场"})):
            with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
                self.play(action, **params)
        self.play("start", confirm=True)
        self.assertNotEqual(self.data()["run_id"], old_run)
        self.assertEqual(self.data()["state"]["act"], 1)
        self.assertEqual(len(self.rows()), 2)
        self.finish("42")
        self.assertIn("最喜欢第 5 幕", self.play("choose", "42", act=5))
        self.assertIn("已经选过", self.play("choose", "42", act=0))

    def test_concurrent_next_choose_and_start_confirmation(self):
        self.play("start")
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda _: self.play("next"), range(12)))
            results = list(pool.map(lambda act: self.play("choose", act=act), (0, 1, 2, 3)))
        self.assertEqual(self.data()["state"]["act"], 13)
        self.assertEqual(sum("已经选过" in result for result in results), 3)
        self.assertEqual(len(self.rows()), 1)
        def start(_):
            try:
                self.play("start", "43")
                return True
            except VendorCmdError:
                return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            self.assertEqual(sum(pool.map(start, (1, 2))), 1)

    def test_outbox_recovers_db_failure_once_and_preserves_feedback_on_delete(self):
        self.finish()
        with patch.object(candle, "feedback_db", return_value=self.temp / "missing" / "db"):
            with self.assertRaisesRegex(VendorCmdError, "写入失败"):
                self.play("choose", act=0)
        pending = self.data()["pending"]
        self.assertEqual(len(pending), 1)
        # Simulate DB commit followed by death before the outbox clear.
        candle_feedback.append_events(self.db, pending)
        self.play("status")
        self.assertEqual(self.data()["pending"], [])
        self.assertEqual(len(self.rows()), 1)
        self.assertTrue(candle.delete_save("41"))
        self.assertIn("请先调用 start", self.play("status"))
        self.assertEqual(len(self.rows()), 1)

    def test_corrupt_save_preserved_and_no_silent_reset(self):
        self.play("start")
        path = self.root / "candle/41" / candle.SAVE_NAME
        path.write_text('{"truncated":')
        with self.assertRaisesRegex(VendorCmdError, "存档损坏"):
            self.play("start", confirm=True)
        self.assertEqual(path.read_text(), '{"truncated":')
        backups = list(path.parent.glob("*.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_bytes(), path.read_bytes())

    def test_atomic_failure_leaves_original_and_unreviewed_source_is_rejected(self):
        self.play("start")
        path = self.root / "candle/41" / candle.SAVE_NAME
        before = path.read_bytes()
        with patch.object(candle.os, "replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                candle._atomic_save(path, {"unexpected": "replacement"})
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob(".candle-*.tmp")))
        vendor = self.temp / "unreviewed"
        vendor.mkdir()
        (vendor / "candle_player.py").write_text("raise RuntimeError('must never execute')")
        with patch.object(candle.GAME, "vendor_dir", vendor):
            with self.assertRaisesRegex(VendorCmdError, "源码版本变动"):
                self.play("next")
        self.assertEqual(path.read_bytes(), before)

    def test_clean_subprocess_environment_and_unmodified_vendor(self):
        before = sorted(p.name for p in candle.GAME.vendor_dir.iterdir())
        original = base.subprocess.run
        def run(*args, **kwargs):
            self.assertEqual(set(kwargs["env"]), {"LANG", "PYTHONDONTWRITEBYTECODE", "PYTHONIOENCODING"})
            self.assertEqual(kwargs["cwd"], str(self.root / "candle/41"))
            return original(*args, **kwargs)
        with patch.object(base.subprocess, "run", side_effect=run):
            self.finish()
            self.play("comment", text="原样保存")
        self.assertEqual(before, sorted(p.name for p in candle.GAME.vendor_dir.iterdir()))
        self.assertFalse((candle.GAME.vendor_dir / "candle_save.json").exists())
        self.assertFalse(list((self.root / "candle/41").glob(".candle-*.tmp")))

    def test_author_read_grant_is_empty_and_never_inherits_admin_or_binding(self):
        self.finish()
        self.play("comment", text="仅作者")
        for user in (None, {"id": 41}, {"id": 1, "is_admin": True}, {"id": 2, "bound_ai": 41}):
            with self.assertRaises(PermissionError):
                candle_feedback.read_for_author(self.db, user)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors(user_id) VALUES (99)")
        result = candle_feedback.read_for_author(self.db, {"id": 99})
        self.assertEqual(result["items"][0]["comment"], "仅作者")
        self.assertEqual(result["total"], 1)
        self.assertFalse(result["has_next"])
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM candle_authors WHERE user_id=99")
        with self.assertRaises(PermissionError):
            candle_feedback.read_for_author(self.db, {"id": 99})

    def test_http_feedback_requires_authenticated_author_and_never_caches(self):
        self.finish()
        self.play("comment", text="私有反馈")
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors VALUES (99)")
        for user, status in (({"id": 41}, 403), ({"id": 1, "is_admin": True}, 403), ({"id": 99}, 200)):
            handler = object.__new__(server.CedarToyHandler)
            handler.headers = {"Authorization": "Bearer fixture"}
            handler._send_json = Mock()
            with patch.object(server, "_current_account", return_value=user) as auth:
                handler._handle_candle_feedback({"player_id": ["99"], "game": ["other"]})
            auth.assert_called_once_with("fixture")
            call = handler._send_json.call_args
            self.assertEqual(call.kwargs.get("status", 200), status)
            self.assertEqual(call.kwargs["extra_headers"]["Cache-Control"], "no-store")
            if status != 200:
                self.assertNotIn("私有反馈", json.dumps(call.args[0], ensure_ascii=False))
        with patch.object(server, "_current_account", side_effect=server._McpError(-32001, "请登录")):
            handler._handle_candle_feedback({})
        self.assertEqual(handler._send_json.call_args.kwargs["status"], 401)

    def test_root_routing_overrides_spoofed_player_and_slot(self):
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, "_current_account", return_value={"id": 41, "is_ai": True}))
            stack.enter_context(patch.object(server, "_auto_migrate_legacy_account_saves"))
            stack.enter_context(patch.object(server, "_anti_addiction_context", return_value=None))
            stack.enter_context(patch.object(server, "_anti_addiction_preflight", return_value=None))
            stack.enter_context(patch.object(server, "_finalize_play_response", side_effect=lambda response, **kw: response))
            stack.enter_context(patch.object(server, "_authenticated_ai_player_id", return_value="41"))
            stack.enter_context(patch.object(server, "_duel_unread_request_reminder", return_value=""))
            stack.enter_context(patch.object(server, "_mcp_forced_announcement", return_value=""))
            for path_token, bearer, action in (("fixture", "", "start"), ("", "fixture", "next")):
                response = server._handle_root_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "play", "arguments": {"game": "candle", "action": action,
                        "player_id": "42", "params": {"player_id": "43", "slot": 2}}}},
                    path_token=path_token, bearer_token=bearer)
                self.assertFalse(response["result"]["isError"])
                result = json.loads(response["result"]["content"][0]["text"])
        self.assertEqual(result["player_id"], "41:2")
        self.assertEqual(self.data("41:2")["state"]["act"], 2)
        self.assertFalse((self.root / "candle/42").exists())
        self.assertFalse((self.root / "candle/43").exists())

    def test_discovery_guide_and_homepage_use_normal_game_surface(self):
        self.assertIn("candle·文字观影·略略", server._tool_list_games())
        guide = json.loads(server._tool_get_guide({"game": "candle"}))["guide"]
        for text in ("观众", "act=0", "仅一次", "写给作者", "confirm", "slot", "略略", "414714065"):
            self.assertIn(text, guide)
        self.assertNotIn("export/import", guide)
        for games in (server.IDENTITY_GAMES, server.PERSISTENT_SAVE_GAMES, server.VENDOR_GAMES):
            self.assertIn("candle", games)
        source = Path("index.html").read_text()
        card = source.split('id: "candle",', 1)[1].split('id: "white_room",', 1)[0]
        self.assertIn('category: "mini"', card)
        self.assertNotIn("nsfw", card.lower())

    def test_public_stats_count_main_saves_not_lock_or_backup_files(self):
        with patch.object(server, "VENDOR_SAVE_ROOT", self.root):
            self.assertEqual(server._vendor_save_stats("candle")["save_count"], 0)
            empty = self.root / "candle/empty"
            empty.mkdir(parents=True)
            (empty / ".lock").touch()
            (empty / "candle_save.json.corrupt-1").write_text("{}")
            self.assertEqual(server._vendor_save_stats("candle")["save_count"], 0)
            self.play("start", "41")
            self.play("start", "41:2")
            self.assertEqual(server._vendor_save_stats("candle")["save_count"], 2)
            self.play("reset", "41:2", confirm=True)
            self.assertEqual(server._vendor_save_stats("candle")["save_count"], 1)

    def test_access_probe_does_not_create_db_or_grant_id_one(self):
        user = {"id": 1, "username": "author-admin-fixture", "is_admin": True}
        self.assertFalse(candle_feedback.author_access(self.db, user))
        self.assertFalse(self.db.exists())
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE unrelated (id INTEGER)")
        self.assertFalse(candle_feedback.author_access(self.db, user))
        with sqlite3.connect(self.db) as conn:
            self.assertIsNone(conn.execute("SELECT name FROM sqlite_master WHERE name='candle_authors'").fetchone())
            candle_feedback.initialize(conn)
        self.assertFalse(candle_feedback.author_access(self.db, user))
        with self.assertRaises(PermissionError):
            candle_feedback.read_for_author(self.db, user)
        # Explicit grant in this test's temporary database, never production.
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors VALUES (1)")
        self.assertTrue(candle_feedback.author_access(self.db, user))
        self.assertEqual(candle_feedback.read_for_author(self.db, user)["items"], [])
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": "Bearer fixture"}
        handler._send_json = Mock()
        with patch.object(server, "_current_account", return_value=user):
            handler._handle_candle_feedback({"access": ["1"]})
        self.assertEqual(handler._send_json.call_args.args[0], {"allowed": True})
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM candle_authors WHERE user_id=1")
        with patch.object(server, "_current_account", return_value=user):
            handler._handle_candle_feedback({"access": ["1"]})
        self.assertEqual(handler._send_json.call_args.args[0], {"allowed": False})
        with self.assertRaises(PermissionError):
            candle_feedback.read_for_author(self.db, user)

    def test_feedback_aggregates_runs_and_latest_comment_without_leaking_ids(self):
        def event(name, run, kind, chosen, comment, at, player="private-player"):
            return dict(event_id=name, run_id=run, player_id=player, kind=kind,
                        chosen=chosen, comment=comment, created_at=at, source_commit="private-commit")
        events = [
            event("a1", "private-a", "choose", 6, None, 10),
            event("a2", "private-a", "comment", 6, "旧影评", 11),
            event("a3", "private-a", "comment", 6, "最新影评", 12),
            event("b1", "private-b", "comment", None, "先写影评", 13),
            event("b2", "private-b", "choose", 0, None, 14),
            event("c1", "private-c", "choose", 13, None, 15),
            event("d1", "private-d", "comment", None, "只写影评", 16),
            # Even an accidental equal run ID cannot combine different players.
            event("e1", "private-a", "comment", None, "另一玩家", 17, "private-other"),
        ]
        candle_feedback.append_events(self.db, events)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors VALUES (1)")
        result = candle_feedback.read_for_author(self.db, {"id": 1})
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["items"], [
            {"created_at": 17, "chosen": None, "comment": "另一玩家"},
            {"created_at": 16, "chosen": None, "comment": "只写影评"},
            {"created_at": 15, "chosen": 13, "comment": None},
            {"created_at": 14, "chosen": 0, "comment": "先写影评"},
            {"created_at": 12, "chosen": 6, "comment": "最新影评"},
        ])
        self.assertNotIn("private-", json.dumps(result))
        self.assertEqual(len(self.rows()), len(events), "read must not rewrite audit events")
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": "Bearer fixture"}
        handler._send_json = Mock()
        with patch.object(server, "_current_account", return_value={"id": 1}):
            handler._handle_candle_feedback({"page": ["2"], "limit": ["2"]})
        response = handler._send_json.call_args.args[0]
        self.assertEqual(response["total"], 5)
        self.assertEqual(response["page"], 2)
        self.assertEqual([row["chosen"] for row in response["items"]], [13, 0])
        self.assertEqual(set(response["items"][0]), {"created_at", "chosen", "comment"})
        self.assertNotIn("private-", json.dumps(response))

    def test_feedback_pages_sort_and_validate(self):
        events = [dict(event_id=f"event{i}", run_id=f"private-run{i}", player_id="private-player",
                       kind="comment" if i % 2 else "choose", chosen=i % 14,
                       comment=f"影评{i}" if i % 2 else None,
                       created_at=1700000000+i // 2, source_commit="private-commit")
                  for i in range(23)]
        candle_feedback.append_events(self.db, events)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors VALUES (1)")
        results = [candle_feedback.read_for_author(self.db, {"id": 1}, page=n) for n in (1, 2, 3)]
        self.assertEqual([len(r["items"]) for r in results], [10, 10, 3])
        self.assertEqual([r["page"] for r in results], [1, 2, 3])
        self.assertTrue(all(r["pages"] == 3 and r["total"] == 23 for r in results))
        self.assertEqual([(r["has_prev"], r["has_next"]) for r in results], [(False, True), (True, True), (True, False)])
        actual = [row for result in results for row in result["items"]]
        self.assertEqual(actual, [{k: e[k] for k in ("created_at", "chosen", "comment")} for e in reversed(events)])
        self.assertEqual(candle_feedback.read_for_author(self.db, {"id": 1}, page=999)["page"], 3)
        for kwargs in ({"page": 0}, {"page": "bad"}, {"limit": 0}, {"limit": 21}):
            with self.assertRaises(ValueError):
                candle_feedback.read_for_author(self.db, {"id": 1}, **kwargs)
        for user in ({"id": 2}, {"id": 3, "is_admin": True}):
            with self.assertRaises(PermissionError):
                candle_feedback.read_for_author(self.db, user, page="bad")
        with sqlite3.connect(self.db) as conn:
            conn.execute("DELETE FROM candle_feedback")
        empty = candle_feedback.read_for_author(self.db, {"id": 1}, page=99)
        self.assertEqual((empty["total"], empty["page"], empty["pages"], empty["items"]), (0, 1, 1, []))

    def test_mcp_feedback_author_ai_tokens_slots_pagination_and_privacy(self):
        events = [dict(event_id=f"private-event{i}", run_id=f"private-run{i}",
                       player_id="private-player", kind="comment", chosen=0,
                       comment=f"影评{i}", created_at=1700000000+i,
                       source_commit="private-commit") for i in range(11)]
        candle_feedback.append_events(self.db, events)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO candle_authors VALUES (71010)")
        with ExitStack() as stack:
            account = stack.enter_context(patch.object(server, "_current_account", return_value={"id": 71010, "is_ai": True}))
            migrate = stack.enter_context(patch.object(server, "_auto_migrate_legacy_account_saves"))
            finalize = stack.enter_context(patch.object(server, "_finalize_play_response"))
            stack.enter_context(patch.object(server, "_authenticated_ai_player_id", return_value="71010"))
            stack.enter_context(patch.object(server, "_duel_unread_request_reminder", return_value=""))
            stack.enter_context(patch.object(server, "_mcp_forced_announcement", return_value=""))
            for path_token, bearer, page in (("fixture", "", 1), ("", "fixture", 2)):
                params = {"slot": 3, "player_id": "999", "id": 999, "user_id": 999, "limit": 100}
                if page == 2:
                    params["page"] = page
                response = server._handle_root_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                    "params": {"name": "play", "arguments": {"game": "candle", "action": "feedback",
                        "player_id": "999", "params": params}}}, path_token=path_token, bearer_token=bearer)
                self.assertFalse(response["result"]["isError"], response)
                result = json.loads(response["result"]["content"][0]["text"])
                self.assertEqual(set(result), {"page", "pages", "total", "items"})
                self.assertEqual((result["page"], result["pages"], result["total"]), (page, 2, 11))
                self.assertEqual(len(result["items"]), 10 if page == 1 else 1)
                self.assertEqual(result["items"][0]["comment"], "影评10" if page == 1 else "影评0")
                for item in result["items"]:
                    self.assertEqual(set(item), {"created_at", "chosen", "comment"})
                    self.assertEqual(item["chosen"], 0)
                self.assertNotIn("private-", json.dumps(response))
            self.assertEqual(account.call_count, 2)
            account.assert_called_with("fixture")
            migrate.assert_not_called()
            finalize.assert_not_called()
        self.assertFalse(self.root.exists(), "feedback reads must not create saves")
        guide = json.loads(server._tool_get_guide({"game": "candle"}))["guide"]
        self.assertNotIn("feedback", guide)
        self.assertNotIn("feedback", server._tool_list_games())

    def test_mcp_feedback_rejects_ungranted_ai_admin_and_spoofed_guest(self):
        with sqlite3.connect(self.db) as conn:
            candle_feedback.initialize(conn)
            conn.execute("INSERT INTO candle_authors VALUES (71020)")
        # A bound human grant does not authorize the AI; each account needs a row.
        for user in ({"id": 71010, "is_ai": True, "bound_human": 71020},
                     {"id": 1, "is_admin": True}, None):
            with patch.object(server, "_current_account", return_value=user):
                with self.assertRaises(server._McpError) as exc:
                    server._tool_play({"game": "candle", "action": "feedback", "player_id": "71020",
                        "params": {"player_id": "71020", "id": 71020, "user_id": 71020,
                                   "authenticated_account": {"id": 71020}, "trusted": True}},
                        path_token="fixture" if user else None)
                self.assertIn("已授权的登录账号", exc.exception.message)
        self.assertFalse(self.root.exists())

    def test_candle_author_guide_uses_mcp_tokens_and_current_exact_grant(self):
        with sqlite3.connect(self.db) as conn:
            candle_feedback.initialize(conn)
            conn.executemany("INSERT INTO candle_authors VALUES (?)", [(71010,), (71020,), (1,)])
        public = server._tool_get_guide({"game": "candle"})
        with ExitStack() as stack:
            auth = stack.enter_context(patch.object(server, "_current_account"))
            stack.enter_context(patch.object(server, "_authenticated_ai_player_id", return_value=None))
            stack.enter_context(patch.object(server, "_duel_unread_request_reminder", return_value=""))
            stack.enter_context(patch.object(server, "_mcp_forced_announcement", return_value=""))
            for user in ({"id": 71010, "is_ai": True}, {"id": 71020, "is_ai": False}, {"id": 1, "is_admin": True}):
                auth.return_value = user
                for path_token, bearer in (("path-fixture", "ignored-bearer"), ("", "bearer-fixture")):
                    auth.reset_mock()
                    response = server._handle_root_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                        "params": {"name": "get_guide", "arguments": {"game": "candle",
                            "id": 999, "player_id": "999", "params": {"user_id": 999}}}},
                        path_token=path_token, bearer_token=bearer)
                    self.assertFalse(response["result"]["isError"], response)
                    auth.assert_called_once_with(path_token or bearer)
                    data = json.loads(response["result"]["content"][0]["text"])
                    public_guide = json.loads(public)["guide"]
                    self.assertTrue(data["guide"].startswith(public_guide + "\n\n【作者功能】"))
                    self.assertEqual(data["guide"].count("【作者功能】"), 1)
                    self.assertIn('play(game="candle", action="feedback", params={"page":1})', data["guide"])
                    self.assertTrue(data["guide"].endswith("每页10条。"))
            with sqlite3.connect(self.db) as conn:
                conn.execute("DELETE FROM candle_authors WHERE user_id=1")
            self.assertEqual(server._tool_get_guide({"game": "candle"}, path_token="fixture"), public)
            auth.side_effect = sqlite3.OperationalError("unavailable")
            self.assertEqual(server._tool_get_guide({"game": "candle"}, path_token="fixture"), public)
        self.assertEqual(server._tool_get_guide({"game": "candle"}), public)

    def test_candle_author_guide_hidden_from_guests_ungranted_and_other_games(self):
        with sqlite3.connect(self.db) as conn:
            candle_feedback.initialize(conn)
            conn.execute("INSERT INTO candle_authors VALUES (71020)")
        public = server._tool_get_guide({"game": "candle"})
        arguments = {"game": "candle", "id": 71020, "user_id": 71020,
                     "player_id": "71020", "path_token": "forged", "bearer_token": "forged",
                     "params": {"user_id": 71020, "authenticated_account": {"id": 71020}}}
        with patch.object(server, "_current_account") as auth:
            self.assertEqual(server._tool_get_guide(arguments), public)
            auth.assert_not_called()
            for user in ({"id": 42, "is_ai": False}, {"id": 71010, "is_ai": True, "bound_human": 71020},
                         {"id": 1, "is_admin": True}):
                auth.return_value = user
                self.assertEqual(server._tool_get_guide(arguments, path_token="fixture"), public)
            auth.side_effect = server._McpError(-32001, "请登录")
            self.assertEqual(server._tool_get_guide(arguments, path_token="invalid"), public)
        for game in ("white_room", "moonlit", "duel"):
            original = server._tool_get_guide({"game": game})
            with patch.object(server, "_current_account") as auth:
                self.assertEqual(server._tool_get_guide({"game": game}, path_token="author-token"), original)
                auth.assert_not_called()
        self.assertNotIn("【作者功能】", public)
        self.assertNotIn("feedback", server.VENDOR_CMD_GUIDES["candle"])
        self.assertNotIn("feedback", server._tool_list_games())

    def test_guest_cannot_address_account_or_account_slot(self):
        self.play("start", "41")
        self.play("start", "41:2")
        with ExitStack() as stack:
            stack.enter_context(patch.object(server, "_reject_claimed_guest"))
            stack.enter_context(patch.object(server, "_anti_addiction_context", return_value=None))
            stack.enter_context(patch.object(server, "_anti_addiction_preflight", return_value=None))
            stack.enter_context(patch.object(server, "_finalize_play_response", side_effect=lambda response, **kw: response))
            result = json.loads(server._tool_play({"game": "candle", "action": "start", "player_id": "41"}))
            self.assertEqual(result["player_id"], "guest:41")
            for raw in ("41:2", "../41", "guest:../41"):
                with self.assertRaises(server._McpError):
                    server._tool_play({"game": "candle", "action": "next", "params": {"player_id": raw}})
        self.assertEqual(self.data("41")["state"]["act"], 1)
        self.assertEqual(self.data("41:2")["state"]["act"], 1)


if __name__ == "__main__":
    unittest.main()
