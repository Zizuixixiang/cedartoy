import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class HumanTestWebTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "sessions.db")
        self.mbti_db = patch.object(server.mbti_handler, "DB_PATH", self.db_path)
        self.dnd_db = patch.object(server.dnd_handler, "DB_PATH", self.db_path)
        self.mbti_db.start()
        self.dnd_db.start()

    def tearDown(self):
        self.dnd_db.stop()
        self.mbti_db.stop()
        self.temp_dir.cleanup()

    def test_guest_must_use_web_namespace(self):
        with self.assertRaises(server._McpError):
            server._human_test_player_id("mbti", "", "guest:machine")
        with self.assertRaises(server._McpError):
            server._human_test_player_id("mbti", "", "123")
        player_id, identity = server._human_test_player_id("mbti", "", "guest:webabc123")
        self.assertEqual(player_id, "guest:webabc123")
        self.assertEqual(identity, "guest")

    def test_human_token_overrides_reported_guest_and_ai_is_rejected(self):
        with patch.object(server, "_current_account", return_value={"id": 42, "is_ai": False}):
            self.assertEqual(
                server._human_test_player_id("dnd", "token", "guest:webspoof"),
                ("42", "account"),
            )
        with patch.object(server, "_current_account", return_value={"id": 9, "is_ai": True}):
            with self.assertRaises(server._McpError):
                server._human_test_player_id("dnd", "token", "guest:webabc")

    def test_result_on_fresh_database_is_not_a_server_error(self):
        with self.assertRaises(server.dnd_handler.JsonRpcError) as raised:
            server._human_test_action("dnd", "result", "", {"player_id": "guest:webfresh1"})
        self.assertEqual(raised.exception.code, -32003)

    def test_mbti_quick_returns_all_public_questions_and_resumes(self):
        player_id = "guest:webmbti1"
        state = server._human_test_action(
            "mbti", "start", "", {"player_id": player_id, "edition": "quick"}
        )
        self.assertEqual(state["edition"], "quick")
        self.assertEqual(state["total"], 16)
        self.assertEqual(len(state["questions"]), 16)
        self.assertNotIn("mode", state)
        self.assertIn("option_a", state["questions"][0])
        self.assertNotIn("dimension", state["questions"][0])

        resumed = server._human_test_action("mbti", "result", "", {"player_id": player_id})
        self.assertEqual(resumed["edition"], "quick")
        self.assertEqual(len(resumed["questions"]), 16)

    def test_mbti_quick_completes_with_one_web_batch(self):
        player_id = "guest:webquick1"
        server._human_test_action(
            "mbti", "start", "", {"player_id": player_id, "edition": "quick"}
        )
        completed = server._human_test_action(
            "mbti", "answer_batch", "", {"player_id": player_id, "answers": [3] * 16}
        )
        self.assertTrue(completed["complete"])
        self.assertIn("快速版", completed["result"])
        self.assertNotIn("short", completed["result"])
        result_data = completed["result_data"]
        self.assertEqual(result_data["kind"], "mbti")
        self.assertEqual(len(result_data["type"]), 4)
        self.assertEqual(len(result_data["dimensions"]), 4)
        self.assertTrue(all("left_percent" in axis for axis in result_data["dimensions"]))
        self.assertTrue(result_data["description"])
        self.assertTrue(result_data["strengths"])
        self.assertTrue(result_data["weaknesses"])

        historical = server._human_test_action("mbti", "result", "", {"player_id": player_id})
        self.assertEqual(historical["result_data"]["type"], result_data["type"])

    def test_mbti_complete_web_batch_uses_existing_handler_chunks(self):
        player_id = "guest:webcomplete1"
        state = server._human_test_action(
            "mbti", "start", "", {"player_id": player_id, "edition": "complete"}
        )
        self.assertEqual(state["total"], 93)
        original = server.mbti_handler.mbti_answer_batch
        with patch.object(server.mbti_handler, "mbti_answer_batch", wraps=original) as answer_batch:
            completed = server._human_test_action(
                "mbti", "answer_batch", "", {"player_id": player_id, "answers": [3] * 93}
            )
        self.assertTrue(completed["complete"])
        self.assertEqual(answer_batch.call_count, 6)
        self.assertIn("完整版", completed["result"])
        self.assertNotIn("full_fast", completed["result"])

    def test_dnd_has_one_web_version_and_completes_in_one_handler_batch(self):
        player_id = "guest:webdnd1"
        state = server._human_test_action("dnd", "start", "", {"player_id": player_id})
        self.assertEqual(state["edition"], "standard")
        self.assertEqual(state["total"], 36)
        self.assertEqual(len(state["questions"]), 36)
        self.assertNotIn("bucket", str(state["questions"]))
        self.assertIn("家族长辈", state["questions"][0]["text"])
        self.assertEqual(len(server.dnd_web_questions.QUESTIONS), 36)

        original = server.dnd_handler.dnd_answer_batch
        with patch.object(server.dnd_handler, "dnd_answer_batch", wraps=original) as answer_batch:
            completed = server._human_test_action(
                "dnd", "answer_batch", "", {"player_id": player_id, "answers": [1] * 36}
            )
        self.assertEqual(answer_batch.call_count, 1)
        self.assertTrue(completed["complete"])
        self.assertIn("九阵营测试完成", completed["result"])
        self.assertNotIn("DND", completed["result"])
        self.assertNotIn("full_fast", completed["result"])
        result_data = completed["result_data"]
        self.assertEqual(result_data["kind"], "dnd")
        self.assertTrue(result_data["name_zh"])
        self.assertTrue(result_data["name_en"])
        self.assertEqual(len(result_data["axes"]), 2)
        self.assertEqual(set(result_data["raw_buckets"]), {"lx", "nx", "cx", "xg", "xn", "xe"})
        self.assertIn("\n\n", result_data["description"])

    def test_template_index_and_sources(self):
        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn("__TEST_GAME_CONFIG__", template)
        self.assertIn('api("answer_batch"', template)
        self.assertIn("localStorage", template)
        self.assertIn("上一题", template)
        self.assertIn("返回修改", template)
        self.assertIn("进度保留 24 小时", template)
        self.assertNotIn("进度保存在本机", template)
        self.assertIn('className = "mbti-scale-track"', template)
        self.assertIn('ends.innerHTML = \'<span>B</span>', template)
        self.assertIn('<span>A</span>\';', template)
        self.assertIn('[0, "完全", "完全偏向 B"]', template)
        self.assertIn('[5, "完全", "完全偏向 A"]', template)
        self.assertIn("min-height: 48px", template)
        self.assertIn("question.options.forEach((option) => addAnswer", template)
        self.assertNotIn('[[5,"完全 A"]', template)
        pole_css = template.split(".pole {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-columns: 24px minmax(0, 1fr)", pole_css)
        self.assertIn("background: transparent", pole_css)
        self.assertIn("border: 0", pole_css)
        self.assertIn("cursor: default", pole_css)
        self.assertNotIn("padding: 14px", pole_css)
        self.assertNotIn("var(--purple-light)", pole_css)
        self.assertIn('<div class="pole"><b>A</b><span></span></div>', template)
        self.assertNotIn(".pole:last-child { background", template)
        self.assertIn("已答完 ${activeState.total} 题，确认提交？", template)
        self.assertIn('class="actions review-actions"', template)
        self.assertIn(".review-actions { display: flex; flex-wrap: nowrap", template)
        self.assertNotIn("reviewGrid", template)
        self.assertNotIn("review-list", template)
        self.assertNotIn("review-item", template)
        self.assertIn("game: CONFIG.game", template)
        self.assertIn("edition: activeState.edition", template)
        self.assertIn('renderState(await api("answer_batch", {answers: [...answers]}))', template)
        self.assertEqual(template.count('api("answer_batch"'), 1)
        self.assertNotIn("submitBatchWithSessionReplay", template)
        self.assertNotIn("isMissingSessionError", template)
        self.assertNotIn("replayEditionFromDraft", template)
        self.assertNotIn("--screen-bg", template)
        self.assertIn('class="result-structured"', template)
        self.assertIn("renderMbtiResult", template)
        self.assertIn("renderDndResult", template)
        self.assertIn("appendResultAxes", template)
        self.assertIn("result-footnote", template)
        self.assertIn("fallback.hidden = rendered", template)
        for internal_mode in ("short_fast", "full_fast"):
            self.assertNotIn(internal_mode, template)

        index = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        for game in ("mbti", "dnd"):
            block = index.split(f'id: "{game}"', 1)[1].split("ranks: []", 1)[0]
            self.assertNotIn("comingSoon", block)
            self.assertIn(f'url: "/{game}"', block)
        self.assertIn('name: "九阵营"', index)
        self.assertIn('name: "属性测试"', index)
        self.assertIn('badge: "ATTR"', index)
        self.assertIn("本测试含成人内容，请确认已满18岁", index)
        self.assertIn('https://bdsmtest.org/?lang=zh_CN', index)

        self.assertEqual(
            server.HUMAN_TEST_GAMES["mbti"]["source"],
            "题库整理自网络公开题目",
        )
        self.assertEqual(
            server.HUMAN_TEST_GAMES["dnd"]["source"],
            "题库与阵营描述译自 easydamus.com",
        )
        mbti_guide = (Path("turtle-soup/backend/guides/mbti.md")).read_text(encoding="utf-8")
        dnd_guide = (Path("turtle-soup/backend/guides/dnd.md")).read_text(encoding="utf-8")
        self.assertIn("## 来源\n\n题库整理自网络公开题目。", mbti_guide)
        self.assertIn("## 来源\n\n题库与阵营描述译自 easydamus.com（阵营测试）。", dnd_guide)


if __name__ == "__main__":
    unittest.main()
