import tempfile
import unittest
from pathlib import Path

from enneagram import handler, questions


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
        self.assertIn("侧翼与 tritype 仅 full 档提供", text)
        self.assertNotIn("Tritype 推测：", text)
        self.assertIn("两套分数不可直接比较", text)

        stored = handler.enneagram_get_result({"player_id": player_id})
        self.assertIn("九型人格历史结果", stored)
        self.assertIn("三中心相对分（36分制）", stored)
        self.assertIn("存档身份：guest:enneaquick", stored)

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
