"""Runtime patch compatibility of the extracted announcement coordination layer."""

import copy
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import announcements
import server


class AnnouncementDeliveryTests(unittest.TestCase):
    def assert_history_hint(self, text):
        self.assertEqual(text.count('action="announcements"'), 1)
        self.assertTrue(text.endswith('查看历史公告：action="announcements"。'))
        self.assertNotIn("不回也没关系", text)
        self.assertNotIn("这条通知不会再弹", text)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="announcement-delivery-")
        self.addCleanup(temporary.cleanup)
        self.sessions = Path(temporary.name) / "sessions.db"
        self.store = Path(temporary.name) / "announcements.db"
        for target, name, value in (
            (server, "SESSIONS_DB_PATH", self.sessions),
            (announcements, "DB_PATH", str(self.store)),
        ):
            patcher = patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        for db, title in ((self.sessions, "网页库"), (self.store, "公告库")):
            with patch.object(announcements, "DB_PATH", str(db)):
                announcements.create_announcement(
                    "poll", "poll", title, "请选择", "all",
                    options=["甲", "乙"], allow_feedback=True, force_mcp_push=True,
                )

    def test_web_reads_use_sessions_without_rebinding_announcement_store(self):
        with patch.object(server, "_current_account", return_value={"id": 21, "is_ai": 0}):
            self.assertEqual(server._web_announcements("token")["announcements"][0]["title"], "网页库")
            self.assertEqual(server._mark_web_announcements_read("token", ["poll"]), {"marked": 1})
        self.assertEqual(announcements.DB_PATH, str(self.store))
        with sqlite3.connect(self.store) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0], 0)

    def test_ordinary_mcp_calls_honor_independently_patched_store(self):
        self.assertIn("公告库", server._play_announcements("42", "eco", "status"))
        self.assertIn("公告库", server._tool_play_announcement_history("eco", "42", {})["text"])
        self.assertTrue(server._tool_play_vote("eco", "42", {
            "announcement_id": "poll", "options": [1], "feedback": "原路径",
        })["ok"])
        self.assertEqual(announcements.DB_PATH, str(self.store))
        with sqlite3.connect(self.sessions) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0], 0)

    def test_initialization_web_vote_and_forced_push_rebind_current_sessions_path(self):
        server._init_announcement_tables()
        self.assertEqual(announcements.DB_PATH, str(self.sessions))
        announcements.DB_PATH = str(self.store)
        with patch.object(server, "_current_account", return_value={"id": 21, "is_ai": 0}):
            self.assertTrue(server._submit_web_announcement_vote("token", "poll", [2])["ok"])
        self.assertEqual(announcements.DB_PATH, str(self.sessions))
        announcements.DB_PATH = str(self.store)
        forced = server._mcp_forced_announcement("42")
        self.assertIn("网页库", forced)
        self.assert_history_hint(forced)
        self.assertEqual(announcements.DB_PATH, str(self.sessions))
        self.assertEqual(server._mcp_forced_announcement("42:2"), "")
        with sqlite3.connect(self.store) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0], 0)

    def test_runtime_poll_and_feedback_hints_are_local_to_each_announcement(self):
        for kind, feedback in (("notice", False), ("notice", True), ("poll", False), ("poll", True)):
            with self.subTest(kind=kind, feedback=feedback), patch.object(
                announcements, "DB_PATH", str(self.store.with_name(f"{kind}-{feedback}.db"))
            ):
                announcements.create_announcement(
                    "item", kind, "标题", "内容", "all",
                    options=["甲", "乙"] if kind == "poll" else None,
                    allow_feedback=feedback,
                )
                text = server._play_announcements("42", "eco", "status")
                self.assert_history_hint(text)
                self.assertEqual('action="vote"' in text, kind == "poll")
                self.assertEqual("有效选项提交后不可修改" in text, kind == "poll")
                self.assertEqual('"feedback"' in text, kind == "poll" and feedback)
                self.assertNotIn("未读公告", text)
                self.assertEqual(server._play_announcements("42", "eco", "status"), "")
                history = server._tool_play_announcement_history("eco", "42", {})["text"]
                self.assertNotIn("查看历史公告", history)

    def test_vote_hints_keep_only_voting_rules(self):
        for hint, action in (
            (server._announcement_vote_hint("eco"), 'action="vote"'),
            (server.eco_handler._eco_vote_hint, 'action="choose"'),
        ):
            for multiple in (False, True):
                with self.subTest(action=action, multiple=multiple):
                    text = hint("poll-id", multiple, 2)
                    for required in (action, "poll-id", "多选" if multiple else "单选",
                                     "[1,2]" if multiple else "[1]", "options=[0]",
                                     "有效选项提交后不可修改", "跳过后仍可再投"):
                        self.assertIn(required, text)
                    for absent in ("不回也没关系", "这条通知不会再弹", "feedback", "查看历史公告"):
                        self.assertNotIn(absent, text)

    def test_runtime_history_hint_is_once_after_forced_and_ordinary_notices(self):
        for forced_count in (0, 1, 2):
            for notice_count in (0, 1, 3, 5):
                with self.subTest(forced=forced_count, notices=notice_count), patch.object(
                    announcements, "DB_PATH",
                    str(self.store.with_name(f"combined-{forced_count}-{notice_count}.db")),
                ):
                    for number in range(forced_count):
                        announcements.create_announcement(
                            f"poll-{number}", "poll", "投票", "请选择", "all",
                            options=["甲", "乙"], allow_feedback=number == 0,
                            force_mcp_push=True,
                        )
                    for number in range(notice_count):
                        announcements.create_announcement(
                            f"notice-{number}", "notice", "普通公告", "内容", "all",
                        )
                    text = server._play_announcements("42", "eco", "status")
                    if not forced_count and not notice_count:
                        self.assertEqual(text, "")
                        continue
                    self.assert_history_hint(text)
                    self.assertEqual(text.count("【系统通知】"), forced_count + min(notice_count, 3))
                    self.assertEqual("另外有 2 条未读公告。" in text, notice_count == 5)
                    for block in text.split("【系统通知】")[1:]:
                        if block.startswith("普通公告"):
                            self.assertNotIn('action="vote"', block)
                            self.assertNotIn("有效选项提交后不可修改", block)
                            self.assertNotIn('"feedback"', block)
                    self.assertEqual(server._play_announcements("42", "eco", "status"), "")

    def test_web_callbacks_are_resolved_from_server_at_call_time(self):
        user = {"id": 21, "is_ai": 0}
        connect = server._sessions_db_connect
        with (
            patch.object(server, "_current_human_account", return_value=user) as current,
            patch.object(server, "_human_announcement_identity", return_value="human:99") as identity,
            patch.object(server, "_sessions_db_connect", wraps=connect) as connection,
            patch.object(server, "_announcement_options_for_web", return_value=["patched"]),
            patch.object(server, "_announcement_vote_for_web", return_value={"patched": True}),
        ):
            self.assertEqual(server._account_announcement_identity(user, "21"), "human:99")
            item = server._web_announcements("token")["announcements"][0]
            self.assertEqual(item["options"], ["patched"])
            self.assertEqual(item["my_vote"], {"patched": True})
            self.assertEqual(server._mark_web_announcements_read("token", ["poll"]), {"marked": 1})
            server._submit_web_announcement_vote("token", "poll", [1])
            current.assert_called_with("token")
            identity.assert_called_with(user)
            self.assertEqual(connection.call_count, 2)
        with sqlite3.connect(self.sessions) as conn:
            self.assertEqual(conn.execute(
                "SELECT player_id, votes FROM announcement_reads"
            ).fetchall(), [("human:99", "[1]")])

    def test_hint_and_suppression_patches_remain_effective(self):
        with (
            patch.object(server, "_announcement_vote_hint", return_value=lambda *args: "<vote>"),
            patch.object(server, "_announcement_feedback_hint", return_value=lambda *args: "<feedback>"),
            patch.object(server, "_announcement_more_hint", return_value=lambda count: "<more>"),
        ):
            for number in range(4):
                announcements.create_announcement(str(number), "notice", str(number), "内容", "all")
            ordinary = server._play_announcements("42", "eco", "status")
            self.assertIn("<more>", ordinary)
            history = server._tool_play_announcement_history("eco", "42", {})["text"]
            forced = server._mcp_forced_announcement("43")
            for text in (ordinary, history, forced):
                self.assertIn("<vote>", text)
                self.assertIn("<feedback>", text)
        with (
            patch.object(server, "GUEST_PREFIX", "temporary:"),
            patch.object(server, "_ANNOUNCEMENT_META_ACTIONS", {"status"}),
            patch.object(announcements, "check_announcements") as check,
        ):
            self.assertEqual(server._play_announcements("44", "eco", "status"), "")
            self.assertEqual(server._play_announcements("temporary:x", "eco", "look"), "")
            self.assertFalse(server._tool_play_vote("eco", "temporary:x", {})["ok"])
            self.assertFalse(server._tool_play_announcement_history("eco", "temporary:x", {})["ok"])
            check.assert_not_called()

    def test_best_effort_delivery_and_explicit_request_errors(self):
        for method, invoke in (
            ("check_announcements", lambda: server._play_announcements("42", "eco", "status")),
            ("check_forced_mcp_announcements", lambda: server._mcp_forced_announcement("42")),
        ):
            with self.subTest(method=method), patch.object(announcements, method, side_effect=sqlite3.OperationalError("unavailable")):
                self.assertEqual(invoke(), "")
        with patch.object(server, "_current_human_account", return_value={"id": 21}):
            for method, invoke in (
                ("list_announcements", lambda: server._tool_play_announcement_history("eco", "42", {})),
                ("record_vote", lambda: server._tool_play_vote("eco", "42", {"announcement_id": "poll", "options": [1]})),
                ("submit_vote", lambda: server._submit_web_announcement_vote("token", "poll", [1])),
            ):
                with self.subTest(method=method), patch.object(announcements, method, side_effect=announcements.AnnouncementError("invalid")):
                    with self.assertRaises(server._McpError) as caught:
                        invoke()
                    self.assertEqual((caught.exception.code, caught.exception.message), (-32602, "invalid"))
        with patch.object(server, "_current_account", return_value={"id": 42, "is_ai": 1}):
            with self.assertRaises(server._McpError) as caught:
                server._current_human_account("ai")
            self.assertEqual(caught.exception.code, -32001)

    def test_web_mapping_and_prepend_preserve_payloads(self):
        self.assertEqual(server._announcement_options_for_web('{"bad":1}'), [])
        self.assertEqual(server._announcement_options_for_web('[1,"甲"]'), ["1", "甲"])
        self.assertIsNone(server._announcement_vote_for_web(None, "意见"))
        self.assertEqual(server._announcement_vote_for_web('[true,0,"2",2,"bad",1]', "意见"), {
            "options": [2, 1], "skipped": False, "feedback": "意见", "locked": True,
        })
        self.assertEqual(server._announcement_vote_for_web('invalid', 7), {
            "options": [], "skipped": True, "feedback": None, "locked": False,
        })
        response = {"result": {"content": [{"type": "text", "text": " body"}, {"type": "image"}]}}
        original = copy.deepcopy(response)
        delivered = server._prepend_play_text(response, "公告")
        self.assertEqual(delivered["result"]["content"][0]["text"], "公告\n\nbody")
        self.assertEqual(response, original)
        self.assertIs(server._prepend_play_text(response, ""), response)
        self.assertEqual(server._prepend_play_text({"ok": True}, "公告"), {"ok": True, "announcement_notice": "公告"})


if __name__ == "__main__":
    unittest.main()
