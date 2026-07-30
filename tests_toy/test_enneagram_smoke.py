import tempfile
import unittest
from pathlib import Path

from enneagram import handler, questions, scoring
from enneagram.profiles import TYPE_PROFILES


class EnneagramSmokeTest(unittest.TestCase):
    def setUp(self):
        self._old_db_path = handler.DB_PATH
        self._tmpdir = tempfile.TemporaryDirectory()
        handler.DB_PATH = str(Path(self._tmpdir.name) / "sessions.db")

    def tearDown(self):
        handler.DB_PATH = self._old_db_path
        self._tmpdir.cleanup()

    def test_question_banks_are_complete_bilingual_and_have_only_final_modes(self):
        self.assertEqual(
            questions.VALID_MODES,
            ("quick", "quick_fast", "full", "full_fast"),
        )
        self.assertEqual(len(questions.QUICK_QUESTIONS), 36)
        self.assertEqual(len(questions.FULL_QUESTIONS), 180)
        for question in questions.QUICK_QUESTIONS + questions.FULL_QUESTIONS:
            self.assertTrue(question["text"])
            self.assertTrue(question["text_zh"])
            self.assertTrue(all(option["text"] for option in question["options"]))
            self.assertTrue(all(option["text_zh"] for option in question["options"]))
        self.assertEqual(questions.QUICK_COLUMNS["A"], 9)
        self.assertEqual(questions.QUICK_COLUMNS["I"], 7)
        get_result_tool = next(
            tool for tool in handler.TOOLS if tool["name"] == "enneagram_get_result"
        )
        self.assertEqual(
            get_result_tool["inputSchema"]["properties"]["detail"]["enum"],
            ["full"],
        )

    def test_all_expanded_profiles_follow_required_shape_and_length(self):
        arrows = {
            1: (7, 4), 2: (4, 8), 3: (6, 9), 4: (1, 2), 5: (8, 7),
            6: (9, 3), 7: (5, 1), 8: (2, 5), 9: (3, 6),
        }
        for type_number, profile in TYPE_PROFILES.items():
            compact = "\n".join(scoring._compact_profile_lines(type_number))
            full = "\n".join(
                scoring._full_profile_lines(type_number, include_wings=True)
            )
            self.assertGreaterEqual(len(compact), 450)
            self.assertLessEqual(len(compact), 650)
            self.assertGreaterEqual(len(full), 1500)
            self.assertLessEqual(len(full), 2500)
            self.assertGreaterEqual(len(profile["full_description"]), 400)
            self.assertLessEqual(len(profile["full_description"]), 600)
            growth, stress = arrows[type_number]
            self.assertIn(
                f"成长时你会长出{growth}号健康面的",
                profile["arrows"]["growth"],
            )
            self.assertIn(
                f"压力下会滑向{stress}号",
                profile["arrows"]["stress"],
            )
            self.assertEqual(len(profile["wings"]), 2)
            self.assertGreaterEqual(len(profile["growth_tips"]), 3)
            self.assertGreaterEqual(len(profile["strengths"]), 3)
            self.assertGreaterEqual(len(profile["weaknesses"]), 3)

    def test_quick_start_answer_and_result(self):
        player_id = "guest:enneaquick"
        text = handler.enneagram_start({"player_id": player_id, "mode": "quick"})
        self.assertIn("共36题", text)
        self.assertIn("1. A — I've been romantic and imaginative.", text)
        self.assertNotIn("我一直比较浪漫", text)

        for _ in range(36):
            text = handler.enneagram_answer(
                {"player_id": player_id, "answer": 1}
            )

        self.assertIn("九型人格测试完成", text)
        self.assertIn("三中心相对分（36分制）", text)
        self.assertIn("名词小课堂", text)
        self.assertNotIn("侧翼：", text)
        self.assertNotIn("tritype", text.lower())
        self.assertNotIn("Tritype 推测：", text)
        self.assertIn("两套分数不可直接比较", text)
        self.assertIn("detail=full", text)

        stored = handler.enneagram_get_result({"player_id": player_id})
        self.assertIn("九型人格历史结果", stored)
        self.assertIn("三中心相对分（36分制）", stored)
        self.assertIn("存档身份：guest:enneaquick", stored)
        self.assertNotIn("两个侧翼的差异", stored)
        full = handler.enneagram_get_result(
            {"player_id": player_id, "detail": "full"}
        )
        self.assertIn("主型深度描述", full)
        self.assertIn("关键动机", full)
        self.assertNotIn("两个侧翼的差异", full)
        self.assertNotIn("tritype", full.lower())

    def test_quick_fast_start_batch_and_result(self):
        player_id = "guest:enneaqfast"
        text = handler.enneagram_start(
            {"player_id": player_id, "mode": "quick_fast"}
        )
        self.assertIn("一次性提交 36 个答案", text)
        text = handler.enneagram_answer_batch(
            {"player_id": player_id, "answers": [2] * 36}
        )
        self.assertIn("九型人格测试完成", text)
        self.assertIn("36分制", text)
        self.assertIn("九型人格历史结果", handler.enneagram_get_result(
            {"player_id": player_id}
        ))

    def test_full_start_answer_and_result(self):
        player_id = "guest:enneafull"
        text = handler.enneagram_start({"player_id": player_id, "mode": "full"})
        self.assertIn("共180题", text)
        self.assertIn("Creative and have an artistic view of life.", text)
        self.assertNotIn("我富有创造力", text)

        for _ in range(180):
            text = handler.enneagram_answer(
                {"player_id": player_id, "answer": 4}
            )

        self.assertIn("侧翼：", text)
        self.assertIn("Tritype 推测：", text)
        self.assertIn("两套分数不可直接比较", text)
        stored = handler.enneagram_get_result({"player_id": player_id})
        self.assertIn("九型人格历史结果", stored)
        self.assertIn("侧翼：", stored)
        self.assertNotIn("关键动机", stored)
        full = handler.enneagram_get_result(
            {"player_id": player_id, "detail": "full"}
        )
        self.assertIn("关键动机", full)
        self.assertIn("健康 / 一般 / 不健康状态", full)
        self.assertIn("两个侧翼的差异", full)
        self.assertIn("成长建议", full)
        self.assertIn("优势", full)
        self.assertIn("盲点", full)

    def test_full_fast_start_batch_and_result(self):
        player_id = "guest:enneaffast"
        text = handler.enneagram_start(
            {"player_id": player_id, "mode": "full_fast"}
        )
        self.assertIn("共180题", text)
        progress = 0
        while progress < 180:
            batch_size = min(16, 180 - progress)
            text = handler.enneagram_answer_batch(
                {"player_id": player_id, "answers": [4] * batch_size}
            )
            progress += batch_size

        self.assertIn("九型人格测试完成", text)
        self.assertIn("侧翼：", text)
        self.assertIn("Tritype 推测：", text)
        self.assertIn("九型人格历史结果", handler.enneagram_get_result(
            {"player_id": player_id}
        ))


if __name__ == "__main__":
    unittest.main()
