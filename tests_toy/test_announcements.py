import json
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import announcements
import server


class AnnouncementTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-announcements-")
        self.db_path = Path(self.temp_dir.name) / "sessions.db"
        self.db_patch = patch.object(announcements, "DB_PATH", str(self.db_path))
        self.db_patch.start()
        with sqlite3.connect(self.db_path) as conn:
            announcements.init_db(conn)

    def tearDown(self):
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
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0],
                0,
            )

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
