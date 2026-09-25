import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import Mock, patch

import announcements
import server
from eco_adapter import handler as eco_handler


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-announcements-")
        self.db_path = Path(self.temp_dir.name) / "sessions.db"
        self.db_patch = patch.object(announcements, "DB_PATH", str(self.db_path))
        self.server_db_patch = patch.object(server, "SESSIONS_DB_PATH", self.db_path)
        self.db_patch.start()
        self.server_db_patch.start()
        with sqlite3.connect(self.db_path) as conn:
            announcements.init_db(conn)

    def tearDown(self):
        self.server_db_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _insert(self, rows):
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                """
                INSERT INTO announcements
                    (id, type, title, content, options, multiple,
                     target_game, created_at, expires_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    def _mark_seen(self, player_id, announcement_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO announcement_reads"
                " (player_id, announcement_id, votes, read_at)"
                " VALUES (?, ?, NULL, '2026-09-15 12:00:00')",
                (player_id, announcement_id),
            )

    def _enable_feedback(self, announcement_id):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE announcements SET allow_feedback = 1 WHERE id = ?",
                (announcement_id,),
            )

    def _force_mcp_push(self, announcement_id):
        return announcements.set_force_mcp_push(announcement_id)

    @staticmethod
    def _notice(number, *, target="eco", expires_at=None):
        return (
            f"notice-{number}",
            "notice",
            f"标题{number}",
            f"内容{number}",
            None,
            0,
            target,
            f"2026-08-{number:02d} 12:00:00",
            expires_at,
        )

    @staticmethod
    def _poll(ann_id, title, day, *, multiple=False):
        return (
            ann_id,
            "poll",
            title,
            "请选择",
            json.dumps(["甲", "乙", "丙"], ensure_ascii=False),
            1 if multiple else 0,
            "eco",
            f"2026-08-{day:02d} 12:00:00",
            None,
        )

    def test_new_player_gets_latest_three_once_and_archives_older(self):
        self._insert(
            [self._notice(number) for number in range(1, 6)]
            + [
                self._notice(6, target="fishing"),
                self._notice(7, expires_at="2026-08-08 00:00:00"),
            ]
        )

        first = announcements.check_announcements("42:3", "eco")

        self.assertNotIn("标题1", first)
        self.assertNotIn("标题2", first)
        self.assertNotIn("标题6", first)
        self.assertNotIn("标题7", first)
        self.assertLess(first.index("标题5"), first.index("标题4"))
        self.assertLess(first.index("标题4"), first.index("标题3"))
        self.assertTrue(
            first.endswith('另有 2 条旧公告；action="announcements" 可查看。')
        )
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads WHERE player_id = '42'"
                ).fetchone()[0],
                5,
            )
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads"
                    " WHERE player_id = '42' AND read_at LIKE 'archived:%'"
                ).fetchone()[0],
                2,
            )

        self.assertEqual(announcements.check_announcements("42", "eco"), "")

    def test_auto_push_with_three_or_fewer_is_unchanged(self):
        self._insert([self._notice(number) for number in range(1, 4)])

        text = announcements.check_announcements("7", "eco")

        self.assertLess(text.index("标题3"), text.index("标题2"))
        self.assertLess(text.index("标题2"), text.index("标题1"))
        self.assertNotIn("旧公告", text)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads WHERE player_id = '7'"
                ).fetchone()[0],
                3,
            )

    def test_flagged_archived_poll_is_forced_once_without_using_latest_three_quota(self):
        self._insert(
            [self._poll("important-old", "重要旧投票", 1)]
            + [self._notice(number) for number in range(2, 5)]
        )

        ordinary = announcements.check_announcements("already-archived", "eco")
        self.assertNotIn("重要旧投票", ordinary)
        self.assertEqual(ordinary.count("【系统通知】"), 3)
        with sqlite3.connect(self.db_path) as conn:
            self.assertTrue(
                conn.execute(
                    "SELECT read_at FROM announcement_reads"
                    " WHERE player_id = 'already-archived'"
                    " AND announcement_id = 'important-old'"
                ).fetchone()[0].startswith("archived:")
            )

        self.assertEqual(self._force_mcp_push("important-old"), "important-old")
        self.assertEqual(self._force_mcp_push("important-old"), "important-old")
        forced = announcements.check_announcements(
            "already-archived", "eco", include_forced_mcp=True
        )
        self.assertIn(announcements.IMPORTANT_MCP_HEADING, forced)
        self.assertIn("重要旧投票", forced)
        self.assertEqual(forced.count("【系统通知】"), 1)
        self.assertEqual(
            announcements.check_announcements(
                "already-archived", "eco", include_forced_mcp=True
            ),
            "",
        )

        fresh = announcements.check_announcements(
            "fresh-machine", "eco", include_forced_mcp=True
        )
        self.assertIn("重要旧投票", fresh)
        self.assertEqual(fresh.count("【系统通知】"), 4)
        self.assertNotIn("旧公告", fresh)

        human = announcements.check_announcements("human:7", "eco")
        self.assertNotIn("重要旧投票", human)
        self.assertEqual(
            announcements.check_forced_mcp_announcements("human:7", "eco"), ""
        )
        with sqlite3.connect(self.db_path) as conn:
            archived_read = conn.execute(
                "SELECT read_at FROM announcement_reads"
                " WHERE player_id = 'human:7' AND announcement_id = 'important-old'"
            ).fetchone()[0]
            machine_read = conn.execute(
                "SELECT read_at FROM announcement_reads"
                " WHERE player_id = 'already-archived'"
                " AND announcement_id = 'important-old'"
            ).fetchone()[0]
        self.assertTrue(archived_read.startswith("archived:"))
        self.assertFalse(machine_read.startswith("archived:"))

    def test_seen_flagged_poll_is_not_forced_again(self):
        self._insert([self._poll("already-seen", "已经展示", 1)])
        self._mark_seen("seen-machine", "already-seen")
        self._force_mcp_push("already-seen")

        self.assertEqual(
            announcements.check_forced_mcp_announcements(
                "seen-machine", "eco"
            ),
            "",
        )

    def test_concurrent_forced_push_claims_poll_once(self):
        self._insert([self._poll("important-race", "并发重要投票", 1)])
        self._force_mcp_push("important-race")
        barrier = threading.Barrier(2)

        def check():
            barrier.wait()
            return announcements.check_forced_mcp_announcements(
                "forced-racer", "eco"
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _unused: check(), range(2)))

        self.assertEqual(sum("并发重要投票" in result for result in results), 1)

    def test_authenticated_mcp_tool_call_forces_global_poll_without_gameplay(self):
        self._insert([self._poll("root-important", "根入口重要投票", 1)])
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE announcements SET target_game = 'all' WHERE id = ?",
                ("root-important",),
            )
        self._force_mcp_push("root-important")
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "list_games", "arguments": {}},
        }

        with (
            patch.object(server, "_authenticated_ai_player_id", return_value="77"),
            patch.object(server, "_tool_list_games", return_value="游戏列表"),
            patch.object(server, "_duel_unread_request_reminder", return_value=""),
        ):
            first = server._handle_root_mcp(payload, path_token="ai-token")
            second = server._handle_root_mcp(payload, path_token="ai-token")

        self.assertEqual(first["result"]["content"][0]["text"], "游戏列表")
        self.assertEqual(len(first["result"]["content"]), 2)
        self.assertIn("根入口重要投票", first["result"]["content"][1]["text"])
        self.assertEqual(
            second["result"]["content"],
            [{"type": "text", "text": "游戏列表"}],
        )

    def test_archived_old_poll_becomes_votable_after_history_displays_it(self):
        self._insert(
            [
                self._poll("poll-old", "较早投票", 1),
                self._notice(2),
                self._notice(3),
                self._notice(4),
                self._poll("poll-new", "最新投票", 5),
            ]
        )
        vote_hint = lambda ann_id, multiple: f"vote:{ann_id}:{int(multiple)}"

        automatic = announcements.check_announcements(
            "auto-player", "eco", vote_hint=vote_hint
        )
        self.assertIn("最新投票", automatic)
        self.assertIn("vote:poll-new:0", automatic)
        automatic_vote = announcements.record_vote("auto-player", "poll-new", [2])
        self.assertIn("poll-new", automatic_vote)
        self.assertIn("2. 乙", automatic_vote)

        with self.assertRaisesRegex(announcements.AnnouncementError, "还没推送给你"):
            announcements.record_vote("auto-player", "poll-old", [1])

        history = announcements.list_announcements(
            "auto-player", "eco", vote_hint=vote_hint
        )
        self.assertEqual(len(history["blocks"]), 5)
        self.assertIn("较早投票", history["blocks"][-1])
        self.assertIn("vote:poll-old:0", history["blocks"][-1])
        history_vote = announcements.record_vote("auto-player", "poll-old", [1])
        self.assertIn("poll-old", history_vote)
        self.assertIn("1. 甲", history_vote)

    def test_concurrent_auto_push_claims_each_announcement_once(self):
        self._insert([self._notice(number) for number in range(1, 6)])
        barrier = threading.Barrier(2)

        def check():
            barrier.wait()
            return announcements.check_announcements("concurrent", "eco")

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _unused: check(), range(2)))

        self.assertEqual(sum(bool(result) for result in results), 1)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads"
                    " WHERE player_id = 'concurrent'"
                ).fetchone()[0],
                5,
            )

    def test_history_returns_ten_then_before_page_without_final_hint(self):
        self._insert([self._notice(number) for number in range(1, 13)])

        first = server._tool_play_announcement_history("eco", "history", {})

        self.assertTrue(first["ok"])
        self.assertEqual(first["text"].count("【系统通知】"), 10)
        self.assertLess(first["text"].index("标题12"), first["text"].index("标题11"))
        self.assertIn("【系统通知】标题3", first["text"])
        self.assertNotIn("【系统通知】标题2", first["text"])
        self.assertTrue(
            first["text"].endswith(
                '还有更早公告：params={"before":"notice-3"}'
            )
        )

        second = server._tool_play_announcement_history(
            "eco", "history", {"before": "notice-3"}
        )

        self.assertEqual(second["text"].count("【系统通知】"), 2)
        self.assertLess(second["text"].index("标题2"), second["text"].index("标题1"))
        self.assertNotIn("还有更早公告", second["text"])

    def test_play_announcements_dispatches_without_backend_or_auto_prepend(self):
        expected = {"ok": True, "text": "公告"}
        with (
            patch.object(server, "_current_account", return_value={"id": 42, "is_ai": 1}),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(
                server, "_tool_play_announcement_history", return_value=expected
            ) as history,
            patch.object(server, "_play_announcements") as auto_prepend,
        ):
            raw = server._tool_play_inner(
                {
                    "game": "eco",
                    "action": "announcements",
                    "params": {"before": "notice-3"},
                },
                path_token="token",
            )

        self.assertEqual(json.loads(raw), expected)
        game, player_id, params = history.call_args.args
        self.assertEqual((game, player_id), ("eco", "42"))
        self.assertEqual(params["before"], "notice-3")
        auto_prepend.assert_not_called()

    def test_guests_do_not_receive_or_query_announcements(self):
        self._insert([self._notice(1)])

        self.assertEqual(server._play_announcements("guest:test", "eco", "status"), "")
        self.assertFalse(
            server._tool_play_announcement_history("eco", "guest:test", {})["ok"]
        )
        with (
            patch.object(server, "_reject_claimed_guest"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_tool_play_vote") as vote,
        ):
            result = json.loads(
                server._tool_play_inner(
                    {
                        "game": "eco",
                        "action": "vote",
                        "params": {
                            "player_id": "visitor",
                            "announcement_id": "anything",
                            "options": "1",
                        },
                    }
                )
            )
        self.assertFalse(result["ok"])
        vote.assert_not_called()
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0],
                0,
            )

    def test_legacy_schema_migration_is_idempotent_and_preserves_votes_json(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        with sqlite3.connect(legacy_path) as conn:
            conn.executescript(
                """
                CREATE TABLE announcements (
                    id TEXT PRIMARY KEY,
                    type TEXT NOT NULL DEFAULT 'notice',
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    options TEXT,
                    multiple INTEGER DEFAULT 0,
                    target_game TEXT NOT NULL DEFAULT 'all',
                    created_at TEXT NOT NULL,
                    expires_at TEXT
                );
                CREATE TABLE announcement_reads (
                    player_id TEXT NOT NULL,
                    announcement_id TEXT NOT NULL,
                    votes TEXT,
                    read_at TEXT NOT NULL,
                    PRIMARY KEY (player_id, announcement_id)
                );
                INSERT INTO announcements
                    (id, type, title, content, options, multiple, target_game, created_at)
                VALUES ('legacy-poll', 'poll', '旧投票', '旧内容', '["甲","乙"]', 0,
                        'all', '2026-09-01 12:00:00');
                INSERT INTO announcement_reads
                    (player_id, announcement_id, votes, read_at)
                VALUES ('9', 'legacy-poll', '[2]', '2026-09-01 12:01:00');
                """
            )

        with patch.object(announcements, "DB_PATH", str(legacy_path)):
            before = announcements.get_poll_results("legacy-poll")
            self.assertEqual(before["valid_participants"]["total"], 1)
            with sqlite3.connect(legacy_path) as conn:
                announcements.init_db(conn)
                announcements.init_db(conn)
                announcement_columns = {
                    row[1] for row in conn.execute("PRAGMA table_info(announcements)")
                }
                read_columns = {
                    row[1]
                    for row in conn.execute("PRAGMA table_info(announcement_reads)")
                }
                self.assertIn("allow_feedback", announcement_columns)
                self.assertIn("force_mcp_push", announcement_columns)
                self.assertIn("target_identity", announcement_columns)
                self.assertIn("feedback", read_columns)
                self.assertEqual(
                    conn.execute(
                        "SELECT votes, feedback FROM announcement_reads"
                        " WHERE player_id = '9' AND announcement_id = 'legacy-poll'"
                    ).fetchone(),
                    ("[2]", None),
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT allow_feedback, force_mcp_push, target_identity FROM announcements"
                        " WHERE id = 'legacy-poll'"
                    ).fetchone(),
                    (0, 0, None),
                )
                self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")

            with self.assertRaisesRegex(
                announcements.AnnouncementError, "已提交有效选票.*不可修改"
            ):
                announcements.record_vote("9", "legacy-poll", [1])
            with sqlite3.connect(legacy_path) as conn:
                self.assertEqual(
                    conn.execute(
                        "SELECT votes, feedback, read_at FROM announcement_reads"
                        " WHERE player_id = '9' AND announcement_id = 'legacy-poll'"
                    ).fetchone(),
                    ("[2]", None, "2026-09-01 12:01:00"),
                )

    def _create_targeted(self, ann_id="private", identity="human:10000", *, poll=False):
        return announcements.create_announcement(
            ann_id, "poll" if poll else "notice", ann_id, "定向内容", "all",
            target_identity=identity,
            options=["甲", "乙"] if poll else None,
            force_mcp_push=poll,
        )

    def test_web_targeted_notice_visibility_and_read_authorization(self):
        self._create_targeted()
        self._create_targeted("global", None)
        users = {
            "A": {"id": 10000, "is_ai": 0},
            "B": {"id": 10001, "is_ai": 0},
        }
        with patch.object(server, "_current_account", side_effect=users.__getitem__):
            for token, expected in (("A", {"private", "global"}),
                                    ("B", {"global"}), (None, {"global"})):
                with self.subTest(token=token):
                    result = server._web_announcements(token)
                    self.assertEqual({a["id"] for a in result["announcements"]}, expected)
                    self.assertEqual(result["unread_count"], len(expected) if token else 0)
            self.assertEqual(
                server._mark_web_announcements_read("B", ["private"]), {"marked": 0}
            )
            self.assertEqual(
                server._mark_web_announcements_read("B", ["private", "global"]),
                {"marked": 1},
            )
            self.assertEqual(
                server._mark_web_announcements_read("A", ["private", "global"]),
                {"marked": 2},
            )
            self.assertEqual(server._web_announcements("A")["unread_count"], 0)
            self.assertEqual(server._web_announcements("B")["unread_count"], 0)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                set(conn.execute("SELECT player_id, announcement_id FROM announcement_reads")),
                {("human:10000", "private"), ("human:10000", "global"),
                 ("human:10001", "global")},
            )
            self.assertEqual(
                dict(conn.execute("SELECT id, target_identity FROM announcements")),
                {"private": "human:10000", "global": None},
            )

    def test_hidden_notice_existing_archived_receipt_is_not_promoted(self):
        self._create_targeted()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO announcement_reads VALUES (?, ?, ?, ?, ?)",
                ("human:10001", "private", "[]", "旧意见", "archived:old"),
            )
            before = conn.execute("SELECT * FROM announcement_reads").fetchall()
        with patch.object(server, "_current_account", return_value={"id": 10001, "is_ai": 0}):
            self.assertEqual(server._mark_web_announcements_read("B", ["private"]), {"marked": 0})
            self.assertEqual(server._web_announcements("B")["announcements"], [])
        self.assertEqual(announcements.check_announcements("human:10001", "eco"), "")
        self.assertEqual(announcements.list_announcements("human:10001", "eco")["blocks"], [])
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT * FROM announcement_reads").fetchall(), before)

    def test_targeted_auto_push_archive_and_history_are_identity_scoped(self):
        self._insert([self._notice(n, target="all") for n in range(1, 6)])
        self._create_targeted()
        other = announcements.check_announcements("human:10001", "eco")
        self.assertNotIn("private", other)
        self.assertIn("另有 2 条旧公告", other)
        target = announcements.check_announcements("human:10000", "eco")
        self.assertIn("private", target)
        self.assertIn("另有 3 条旧公告", target)
        # The numeric machine account must not share the human account's notice.
        self.assertNotIn("private", announcements.check_announcements("10000:2", "eco"))
        for identity, expected in (("human:10001", 5), ("10000:3", 5), ("human:10000", 6)):
            with self.subTest(identity=identity):
                with patch.object(announcements, "HISTORY_PAGE_LIMIT", 2):
                    blocks, before = [], None
                    while True:
                        page = announcements.list_announcements(identity, "eco", before=before)
                        blocks.extend(page["blocks"])
                        if not page["has_more"]:
                            break
                        before = page["next_before"]
                self.assertEqual(len(blocks), expected)
                self.assertEqual("private" in "\n".join(blocks), identity == "human:10000")
        with self.assertRaisesRegex(announcements.AnnouncementError, "游标无效"):
            announcements.list_announcements("human:10001", "eco", before="private")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT player_id FROM announcement_reads WHERE announcement_id = 'private'").fetchall(),
                [("human:10000",)],
            )

    def test_targeted_forced_poll_uses_normalized_machine_identity(self):
        self._create_targeted("machine-private", "42:2", poll=True)
        self._create_targeted("human-private", poll=True)
        self._create_targeted("global", None, poll=True)
        other = announcements.check_forced_mcp_announcements("43")
        self.assertIn("global", other)
        self.assertNotIn("private", other)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT target_identity FROM announcements WHERE id = 'machine-private'"
            ).fetchone(), ("42",))
            conn.execute(
                "INSERT INTO announcement_reads (player_id, announcement_id, read_at) VALUES (?, ?, ?)",
                ("42", "machine-private", "archived:old"),
            )
        own = announcements.check_announcements("42:3", "eco", include_forced_mcp=True)
        self.assertIn("machine-private", own)
        self.assertIn("global", own)
        self.assertNotIn("human-private", own)
        self.assertEqual(announcements.check_forced_mcp_announcements("42:4"), "")
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT player_id FROM announcement_reads WHERE announcement_id = 'machine-private'"
            ).fetchall(), [("42",)])
            self.assertEqual(conn.execute(
                "SELECT COUNT(*) FROM announcement_reads WHERE announcement_id = 'human-private'"
            ).fetchone()[0], 0)

    def test_targeted_poll_rejects_non_target_votes_even_with_existing_receipt(self):
        self._create_targeted(poll=True)
        for mark_seen in (False, True):
            with self.assertRaisesRegex(announcements.AnnouncementError, "没有编号"):
                announcements.submit_vote("human:10001", "private", [1], mark_seen=mark_seen)
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0], 0)
        self._mark_seen("human:10001", "private")
        with patch.object(server, "_current_account", return_value={"id": 10001, "is_ai": 0}):
            with self.assertRaisesRegex(server._McpError, "没有编号"):
                server._submit_web_announcement_vote("B", "private", [1])
        with self.assertRaisesRegex(announcements.AnnouncementError, "没有编号"):
            announcements.record_vote("human:10001", "private", [1])
        own = announcements.submit_vote("human:10000", "private", [1], mark_seen=True)
        self.assertEqual(own["options"], [1])
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute(
                "SELECT votes, read_at FROM announcement_reads WHERE player_id = 'human:10001'"
            ).fetchone(), (None, "2026-09-15 12:00:00"))

    def test_effective_vote_locks_options_feedback_and_timestamp(self):
        self._insert([self._poll("feedback-poll", "意见投票", 1, multiple=True)])
        self._enable_feedback("feedback-poll")
        self._mark_seen("42", "feedback-poll")
        self._mark_seen("human:8", "feedback-poll")
        self._mark_seen("human:9", "feedback-poll")
        self._mark_seen("11", "feedback-poll")

        first = announcements.record_vote(
            "42:3", "feedback-poll", [1, 2], feedback="  <b>纯文本</b>  "
        )
        self.assertIn("保存了补充意见", first)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE announcement_reads SET read_at = '2000-01-01 00:00:00'"
                " WHERE player_id = '42' AND announcement_id = 'feedback-poll'"
            )
            original = conn.execute(
                "SELECT votes, feedback, read_at FROM announcement_reads"
                " WHERE player_id = '42' AND announcement_id = 'feedback-poll'"
            ).fetchone()

        for options, feedback in (
            ([3], "改选项"),
            ([1, 2], "只改意见"),
            ([0], None),
            ([1, 2], ""),
        ):
            with self.assertRaisesRegex(
                announcements.AnnouncementError, "已提交有效选票.*不可修改"
            ):
                announcements.record_vote(
                    "42", "feedback-poll", options, feedback=feedback
                )

        announcements.record_vote("human:8", "feedback-poll", [1], feedback="人类意见")
        announcements.record_vote("human:9", "feedback-poll", [0], feedback="跳过但留言")

        results = announcements.get_poll_results("feedback-poll")
        self.assertEqual(
            [option["votes"] for option in results["options"]], [2, 1, 0]
        )
        self.assertEqual(
            results["valid_participants"], {"total": 2, "human": 1, "machine": 1}
        )
        self.assertEqual(results["skipped_responses"], 1)
        self.assertEqual(len(results["feedback"]), 3)
        machine_feedback = next(
            item for item in results["feedback"] if item["identity"] == "42"
        )
        self.assertEqual(machine_feedback["feedback"], "<b>纯文本</b>")
        self.assertEqual(machine_feedback["identity_type"], "machine")
        skipped_feedback = next(
            item for item in results["feedback"] if item["identity"] == "human:9"
        )
        self.assertFalse(skipped_feedback["effective_vote"])

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM announcement_reads"
                    " WHERE player_id = '42' AND announcement_id = 'feedback-poll'"
                ).fetchone()[0],
                1,
            )
            unchanged = conn.execute(
                "SELECT votes, feedback, read_at FROM announcement_reads"
                " WHERE player_id = '42' AND announcement_id = 'feedback-poll'"
            ).fetchone()
            self.assertEqual(unchanged, original)

    def test_read_and_skip_do_not_lock_later_first_effective_vote(self):
        self._insert([self._poll("first-vote", "首次有效票", 1, multiple=True)])
        self._enable_feedback("first-vote")
        self._mark_seen("read-only", "first-vote")
        self._mark_seen("skipper", "first-vote")

        read_vote = announcements.submit_vote(
            "read-only", "first-vote", [1], feedback="已读后首投"
        )
        self.assertTrue(read_vote["locked"])

        skipped = announcements.submit_vote(
            "skipper", "first-vote", [0], feedback="先跳过"
        )
        self.assertTrue(skipped["skipped"])
        self.assertFalse(skipped["locked"])
        later_vote = announcements.submit_vote(
            "skipper", "first-vote", [2, 3], feedback="之后正式投"
        )
        self.assertEqual(later_vote["options"], [2, 3])
        self.assertTrue(later_vote["locked"])

        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT votes, feedback FROM announcement_reads"
                    " WHERE player_id = 'skipper' AND announcement_id = 'first-vote'"
                ).fetchone(),
                ("[2, 3]", "之后正式投"),
            )

    def test_concurrent_first_effective_vote_only_writes_one_submission(self):
        self._insert([self._poll("race-poll", "并发首投", 1)])
        self._enable_feedback("race-poll")
        self._mark_seen("racer", "race-poll")
        barrier = threading.Barrier(2)

        def cast_vote(payload):
            option, feedback = payload
            barrier.wait()
            try:
                announcements.submit_vote(
                    "racer", "race-poll", [option], feedback=feedback
                )
                return ("ok", option, feedback)
            except announcements.AnnouncementError as exc:
                return ("error", str(exc))

        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(cast_vote, [(1, "第一份"), (2, "第二份")]))

        successes = [item for item in outcomes if item[0] == "ok"]
        failures = [item for item in outcomes if item[0] == "error"]
        self.assertEqual(len(successes), 1, outcomes)
        self.assertEqual(len(failures), 1, outcomes)
        self.assertIn("已提交有效选票", failures[0][1])
        winner = successes[0]
        with sqlite3.connect(self.db_path) as conn:
            stored = conn.execute(
                "SELECT votes, feedback FROM announcement_reads"
                " WHERE player_id = 'racer' AND announcement_id = 'race-poll'"
            ).fetchone()
        self.assertEqual(stored, (json.dumps([winner[1]]), winner[2]))

    def test_feedback_length_option_modes_skip_expiry_and_notice_validation(self):
        self._insert(
            [
                self._poll("single", "单选", 1),
                self._poll("multi", "多选", 2, multiple=True),
                (
                    "expired",
                    "poll",
                    "过期",
                    "请选择",
                    json.dumps(["甲", "乙"], ensure_ascii=False),
                    0,
                    "eco",
                    "2026-09-01 12:00:00",
                    "2026-09-02 12:00:00",
                ),
                self._notice(3),
            ]
        )
        for ann_id in ("single", "multi", "expired", "notice-3"):
            self._mark_seen("validate", ann_id)
        self._enable_feedback("single")

        with self.assertRaisesRegex(announcements.AnnouncementError, "只能选一个"):
            announcements.record_vote("validate", "single", [1, 2])
        with self.assertRaisesRegex(announcements.AnnouncementError, "超出范围"):
            announcements.record_vote("validate", "multi", [4])
        with self.assertRaisesRegex(announcements.AnnouncementError, "不能和其他选项"):
            announcements.record_vote("validate", "multi", [0, 1])
        with self.assertRaisesRegex(announcements.AnnouncementError, "已经结束"):
            announcements.record_vote("validate", "expired", [1])
        with self.assertRaisesRegex(announcements.AnnouncementError, "不是投票"):
            announcements.record_vote("validate", "notice-3", [1])
        with self.assertRaisesRegex(announcements.AnnouncementError, "最多 500 字"):
            announcements.record_vote(
                "validate", "single", [1], feedback="字" * 501
            )
        with self.assertRaisesRegex(announcements.AnnouncementError, "未开放文字反馈"):
            announcements.record_vote("validate", "multi", [1], feedback="不应保存")

    def test_create_announcement_optional_switches_default_off_for_notices(self):
        poll_id = announcements.create_announcement(
            ann_id="temporary-poll",
            ann_type="poll",
            title="临时测试投票",
            content="仅写入隔离临时库",
            target_game="all",
            options=["甲", "乙"],
            multiple=False,
            allow_feedback=True,
        )
        forced_poll_id = announcements.create_announcement(
            ann_id="temporary-important-poll",
            ann_type="poll",
            title="临时重要投票",
            content="仅写入隔离临时库",
            target_game="all",
            options=["甲", "乙"],
            force_mcp_push=True,
        )
        notice_id = announcements.create_announcement(
            ann_id="temporary-notice",
            ann_type="notice",
            title="临时测试通知",
            content="普通通知",
            target_game="all",
            allow_feedback=True,
            force_mcp_push=True,
        )
        self.assertEqual(
            (poll_id, forced_poll_id, notice_id),
            ("temporary-poll", "temporary-important-poll", "temporary-notice"),
        )
        with sqlite3.connect(self.db_path) as conn:
            rows = {
                row[0]: row[1:]
                for row in conn.execute(
                    "SELECT id, allow_feedback, force_mcp_push FROM announcements"
                    " WHERE id IN ('temporary-poll', 'temporary-important-poll',"
                    " 'temporary-notice')"
                ).fetchall()
            }
        self.assertEqual(
            rows,
            {
                "temporary-poll": (1, 0),
                "temporary-important-poll": (0, 1),
                "temporary-notice": (0, 0),
            },
        )

    def test_web_vote_marks_seen_without_race_and_keeps_human_votes_private(self):
        self._insert([self._poll("web-poll", "网页投票", 1, multiple=True), self._notice(2)])
        self._enable_feedback("web-poll")

        users = {
            "human-one": {"id": 21, "username": "人类一", "is_ai": 0},
            "human-two": {"id": 22, "username": "人类二", "is_ai": 0},
            "machine": {"id": 23, "username": "小机", "is_ai": 1},
        }
        with patch.object(server, "_current_account", side_effect=lambda token: users[token]):
            first = server._web_announcements("human-one")
            poll = next(item for item in first["announcements"] if item["id"] == "web-poll")
            notice = next(item for item in first["announcements"] if item["id"] == "notice-2")
            self.assertIsNone(poll["my_vote"])
            self.assertTrue(poll["allow_feedback"])
            self.assertEqual(notice["type"], "notice")
            self.assertFalse(notice["allow_feedback"])

            submitted = server._submit_web_announcement_vote(
                "human-one", "web-poll", [1, 2], feedback="<img src=x onerror=alert(1)>"
            )
            self.assertTrue(submitted["ok"])
            self.assertTrue(submitted["vote"]["locked"])
            self.assertEqual(
                server._mark_web_announcements_read("human-one", ["web-poll"])["marked"],
                0,
            )
            for options, feedback in (
                ([2], "<b>修改后</b>"),
                ([1, 2], "只改意见"),
                ([0], None),
            ):
                with self.assertRaisesRegex(server._McpError, "不可修改"):
                    server._submit_web_announcement_vote(
                        "human-one", "web-poll", options, feedback=feedback
                    )
            own = server._web_announcements("human-one")
            own_poll = next(item for item in own["announcements"] if item["id"] == "web-poll")
            self.assertEqual(own_poll["my_vote"]["options"], [1, 2])
            self.assertTrue(own_poll["my_vote"]["locked"])
            self.assertEqual(
                own_poll["my_vote"]["feedback"], "<img src=x onerror=alert(1)>"
            )

            other = server._web_announcements("human-two")
            other_poll = next(item for item in other["announcements"] if item["id"] == "web-poll")
            self.assertIsNone(other_poll["my_vote"])
            self.assertNotIn("<img", json.dumps(other, ensure_ascii=False))

            with self.assertRaisesRegex(server._McpError, "仅供已登录的人类"):
                server._submit_web_announcement_vote("machine", "web-poll", [1])

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT player_id, votes, feedback FROM announcement_reads"
                " WHERE announcement_id = 'web-poll'"
            ).fetchone()
        self.assertEqual(
            row,
            ("human:21", "[1, 2]", "<img src=x onerror=alert(1)>"),
        )

    def test_mcp_feedback_hints_schema_and_identity_are_synchronized(self):
        self._insert(
            [
                self._poll("hint-old", "旧投票", 1),
                self._poll("hint-feedback", "开放意见", 2, multiple=True),
            ]
        )
        self._enable_feedback("hint-feedback")
        text = announcements.check_announcements(
            "hint-player",
            "eco",
            vote_hint=server._announcement_vote_hint("eco"),
            feedback_hint=server._announcement_feedback_hint("eco"),
        )
        old_block, feedback_block = sorted(
            text.split("\n\n"), key=lambda block: "旧投票" not in block
        )
        self.assertIn('"options": [1]', old_block)
        self.assertNotIn('"feedback"', old_block)
        self.assertIn('"options": [1,2]', feedback_block)
        self.assertIn('"feedback": "我的意见"', feedback_block)
        self.assertIn("有效选项提交后不可修改", old_block)
        self.assertIn("有效选项提交后不可修改", feedback_block)

        default_text = announcements.check_announcements(
            "default-hint-player", "eco"
        )
        default_old, default_feedback = sorted(
            default_text.split("\n\n"), key=lambda block: "旧投票" not in block
        )
        self.assertNotIn("feedback=", default_old)
        self.assertIn('feedback="我的意见"', default_feedback)
        self.assertIn("有效选项提交后不可修改", default_old)

        result = server._tool_play_vote(
            "eco",
            "hint-player",
            {
                "announcement_id": "hint-feedback",
                "options": "2,3",
                "feedback": "机器意见",
            },
        )
        self.assertTrue(result["ok"])
        self.assertIn("补充意见", result["text"])
        self.assertEqual(
            announcements.get_poll_results("hint-feedback")["options"][1][
                "votes"
            ],
            1,
        )
        with self.assertRaisesRegex(server._McpError, "不可修改"):
            server._tool_play_vote(
                "eco",
                "hint-player",
                {
                    "announcement_id": "hint-feedback",
                    "options": "1",
                    "feedback": "试图改意见",
                },
            )

        self._mark_seen("eco-compat", "hint-feedback")
        eco_result = eco_handler._record_vote(
            "eco-compat",
            "hint-feedback",
            {"options": [1, 3], "feedback": "eco 兼容意见"},
        )
        self.assertIn("补充意见", eco_result)
        with self.assertRaisesRegex(eco_handler.JsonRpcError, "不可修改"):
            eco_handler._record_vote(
                "eco-compat",
                "hint-feedback",
                {"options": [2], "feedback": "试图覆盖"},
            )
        self._mark_seen("eco-old-option", "hint-old")
        self.assertIn(
            "1. 甲",
            eco_handler._record_vote(
                "eco-old-option", "hint-old", {"option": 1}
            ),
        )

        self._mark_seen("77", "hint-feedback")
        with (
            patch.object(
                server,
                "_current_account",
                return_value={"id": 77, "username": "小机", "is_ai": 1},
            ),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_play_announcements", return_value=""),
        ):
            eco_compat_raw = server._tool_play_inner(
                {
                    "game": "eco",
                    "action": "eco_act",
                    "params": {
                        "action": "choose",
                        "announcement": "hint-feedback",
                        "options": [2],
                        "feedback": "根入口兼容意见",
                    },
                },
                path_token="ai-token",
            )
        eco_compat_payload = json.loads(eco_compat_raw)
        self.assertFalse(eco_compat_payload["result"].get("isError", False))
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT votes, feedback FROM announcement_reads"
                    " WHERE player_id = '77' AND announcement_id = 'hint-feedback'"
                ).fetchone(),
                ("[2]", "根入口兼容意见"),
            )

        play = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "play")
        params = play["inputSchema"]["properties"]["params"]["properties"]
        self.assertEqual(params["feedback"]["maxLength"], 500)
        self.assertIn("options", params)
        self.assertIn("不可修改", params["options"]["description"])
        eco_act = next(tool for tool in eco_handler.TOOLS if tool["name"] == "eco_act")
        self.assertEqual(
            eco_act["inputSchema"]["properties"]["feedback"]["maxLength"], 500
        )
        guide = json.loads(server._tool_get_guide({"game": "eco"}))["guide"]
        self.assertIn("feedback", guide)
        self.assertIn("不可修改", guide)
        self.assertIn("单选 options=[1]", guide)
        self.assertIn("多选 options=[1,2]", guide)
        self.assertNotIn('options="1"', guide)

        soup_guide = json.loads(
            server._tool_get_guide({"game": "turtle_soup"})
        )["platform_announcements"]
        self.assertIn('"options":[1]', soup_guide["single"])
        self.assertIn('"options":[1,2]', soup_guide["multiple"])
        self.assertIn('"options":[0]', soup_guide["skip"])

        captured = {}
        with (
            patch.object(
                server,
                "_current_account",
                return_value={"id": 31, "username": "网页人类", "is_ai": 0},
            ),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(
                server,
                "_tool_play_vote",
                side_effect=lambda game, player_id, params: captured.update(
                    game=game, player_id=player_id, params=params
                ) or {"ok": True},
            ),
        ):
            raw = server._tool_play_inner(
                {
                    "game": "eco",
                    "action": "vote",
                    "player_id": "victim",
                    "params": {
                        "player_id": "other-victim",
                        "announcement_id": "hint-feedback",
                        "options": "1",
                    },
                },
                path_token="human-token",
            )
        self.assertTrue(json.loads(raw)["ok"])
        self.assertEqual(captured["player_id"], "human:31")

    def test_homepage_poll_markup_escapes_feedback_and_notices_have_no_controls(self):
        page = Path(server.TOY_INDEX_PATH).read_text(encoding="utf-8")
        self.assertIn('fetch("/api/announcements/vote"', page)
        self.assertIn("data-poll-option", page)
        self.assertIn("data-poll-feedback", page)
        self.assertIn("escapeHtml(submittedAnnouncementVoteText(item))", page)
        self.assertIn('item.type === "poll" ? renderAnnouncementPoll', page)
        self.assertIn("已读不等于投票", page)
        self.assertIn("hasEffectiveVote", page)
        self.assertIn("提交后不可修改", page)
        self.assertNotIn("更新投票", page)
        self.assertNotIn("可随时修改", page)

    def test_web_vote_http_handler_only_forwards_vote_fields(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": "Bearer signed-human-token"}
        handler._read_json_body = lambda: {
            "announcement_id": "poll-id",
            "options": [1],
            "feedback": "意见",
            "player_id": "human:999",
        }
        handler._send_json = Mock()
        with patch.object(
            server,
            "_submit_web_announcement_vote",
            return_value={"ok": True, "vote": {"options": [1]}},
        ) as submit:
            handler._handle_api_announcement_vote()

        submit.assert_called_once_with(
            "signed-human-token", "poll-id", [1], feedback="意见"
        )
        handler._send_json.assert_called_once()

    def test_persistent_play_schema_keeps_announcement_wording_short(self):
        play = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "play")
        properties = play["inputSchema"]["properties"]
        action_description = properties["action"]["description"]

        self.assertIn("announcements（查看公告）", action_description)
        self.assertNotIn("分页", action_description)
        self.assertNotIn("announcements", properties["params"]["description"])

        kelivo_play = next(
            tool for tool in server._build_kelivo_platform_tools() if tool["name"] == "play"
        )
        before = kelivo_play["inputSchema"]["properties"]["params"]["properties"][
            "before"
        ]
        self.assertEqual(before["description"], "公告游标。")


if __name__ == "__main__":
    unittest.main()
