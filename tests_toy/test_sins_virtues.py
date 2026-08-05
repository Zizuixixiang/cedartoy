import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from sins_virtues import handler, questions, scoring


def _extreme_answers(maximum):
    return [
        (1 if maximum else 5) if question["direction"] == "reverse" else (5 if maximum else 1)
        for question in questions.QUESTIONS
    ]


class SinsVirtuesTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "sessions.db")
        self.db_patch = patch.object(handler, "DB_PATH", self.db_path)
        self.db_patch.start()

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_question_count_dimensions_and_fixed_shuffle(self):
        self.assertEqual(len(questions.QUESTIONS), 35)
        self.assertEqual(len({question["text"] for question in questions.QUESTIONS}), 35)
        self.assertEqual(set(scoring.DIMENSION_NAMES), {*questions.SINS, *questions.VIRTUES})
        self.assertEqual(len(scoring.DIMENSION_NAMES), 14)
        for pair, sin, virtue in questions.PAIRS:
            pair_items = [question for question in questions.QUESTIONS if question["pair"] == pair]
            self.assertEqual(len(pair_items), 5)
            self.assertEqual(
                {question["direction"] for question in pair_items},
                {"direct", "reverse", "coexistence"},
            )
            self.assertEqual(sum(sin in question["loadings"] for question in pair_items), 3)
            self.assertEqual(sum(virtue in question["loadings"] for question in pair_items), 3)
        self.assertTrue(
            all(
                questions.QUESTIONS[index]["pair"] != questions.QUESTIONS[index + 1]["pair"]
                for index in range(34)
            )
        )

    def test_scoring_boundaries_are_independent_and_stable(self):
        minimum = scoring.score_answers(questions.QUESTIONS, _extreme_answers(False))
        maximum = scoring.score_answers(questions.QUESTIONS, _extreme_answers(True))
        self.assertEqual(set(minimum["scores"].values()), {0.0})
        self.assertEqual(set(maximum["scores"].values()), {100.0})
        for pair in maximum["pairs"]:
            self.assertEqual(pair["sin_score"], 100.0)
            self.assertEqual(pair["virtue_score"], 100.0)
        self.assertIn("不做互补归一", maximum["scoring_note"])
        with self.assertRaises(ValueError):
            scoring.score_answers(questions.QUESTIONS, [3] * 34)

    def test_step_answer_batch_result_and_mcp_tool_shapes(self):
        step_start = handler.sins_virtues_start({"player_id": "guest:step", "mode": "full"})
        self.assertIn(questions.DISCLAIMER, step_start)
        self.assertIn("第1题", step_start)
        second = handler.sins_virtues_answer({"player_id": "guest:step", "answer": 3})
        self.assertIn("第2题", second)

        fast_start = handler.sins_virtues_start({"player_id": "guest:batch", "mode": "full_fast"})
        self.assertIn("快速批量", fast_start)
        completed = handler.sins_virtues_answer_batch(
            {"player_id": "guest:batch", "answers": [3] * 35}
        )
        self.assertIn("七宗罪 VS 七美德完成", completed)
        self.assertIn(questions.DISCLAIMER, completed)
        historical = handler.sins_virtues_get_result({"player_id": "guest:batch"})
        self.assertIn("历史结果", historical)
        self.assertIn(questions.DISCLAIMER, historical)

        tool_names = {tool["name"] for tool in handler.TOOLS}
        self.assertEqual(
            tool_names,
            {
                "sins_virtues_start",
                "sins_virtues_answer",
                "sins_virtues_answer_batch",
                "sins_virtues_get_result",
            },
        )
        self.assertTrue(
            all(questions.DISCLAIMER in tool["description"] for tool in handler.TOOLS)
        )

    def test_human_action_returns_fourteen_dimension_result_structure(self):
        player_id = "guest:websins1"
        state = server._human_test_action(
            "sins_virtues", "start", "", {"player_id": player_id}
        )
        self.assertEqual(state["edition"], "standard")
        self.assertEqual(state["total"], 35)
        self.assertEqual(len(state["questions"]), 35)
        self.assertIn(questions.DISCLAIMER, state["instructions"])
        self.assertNotIn("loadings", str(state["questions"]))

        completed = server._human_test_action(
            "sins_virtues",
            "answer_batch",
            "",
            {"player_id": player_id, "answers": [3] * 35},
        )
        data = completed["result_data"]
        self.assertTrue(completed["complete"])
        self.assertEqual(data["kind"], "sins_virtues")
        self.assertEqual(len(data["scores"]), 14)
        self.assertEqual(len(data["pairs"]), 7)
        self.assertEqual(data["disclaimer"], questions.DISCLAIMER)
        self.assertTrue(data["dominant_pair_name"])

    def test_human_http_api_smoke(self):
        def dispatch(path, payload):
            body = json.dumps(payload).encode("utf-8")
            request = object.__new__(server.CedarToyHandler)
            request.path = path
            request.headers = {"Content-Length": str(len(body)), "Content-Type": "application/json"}
            request.rfile = io.BytesIO(body)
            request.client_address = ("127.0.0.1", 12345)
            captured = {}
            request._send_json = lambda data, status=200, extra_headers=None: captured.update(
                data=data, status=status, headers=extra_headers or {}
            )
            request.do_POST()
            return captured

        def dispatch_get(path):
            request = object.__new__(server.CedarToyHandler)
            request.path = path
            request.headers = {}
            request.client_address = ("127.0.0.1", 12345)
            captured = {}
            request._send_json = lambda data, status=200, extra_headers=None: captured.update(
                data=data, status=status, headers=extra_headers or {}
            )
            request.do_GET()
            return captured

        started = dispatch(
            "/api/sins_virtues/start", {"player_id": "guest:webhttp1"}
        )
        self.assertEqual(started["status"], 200)
        self.assertEqual(started["data"]["total"], 35)

        completed = dispatch(
            "/api/sins_virtues/answer_batch",
            {"player_id": "guest:webhttp1", "answers": [3] * 35},
        )
        self.assertEqual(completed["status"], 200)
        self.assertEqual(completed["data"]["result_data"]["kind"], "sins_virtues")

        result = dispatch_get("/api/sins_virtues/result?player_id=guest:webhttp1")
        self.assertEqual(result["status"], 200)
        self.assertTrue(result["data"]["complete"])
        self.assertEqual(result["data"]["result_data"]["disclaimer"], questions.DISCLAIMER)

    def test_root_mcp_list_games_and_play_smoke(self):
        self.assertIn("sins_virtues·七宗罪 VS 七美德", server._tool_list_games())
        with (
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_reject_claimed_guest", return_value=None),
        ):
            started = json.loads(
                server._tool_play(
                    {
                        "game": "sins_virtues",
                        "action": "sins_virtues_start",
                        "params": {"player_id": "mcpsins", "mode": "full_fast"},
                    }
                )
            )
            self.assertIn("快速批量", started["result"]["content"][0]["text"])
            completed = json.loads(
                server._tool_play(
                    {
                        "game": "sins_virtues",
                        "action": "sins_virtues_answer_batch",
                        "params": {"player_id": "mcpsins", "answers": [3] * 35},
                    }
                )
            )
        text = completed["result"]["content"][0]["text"]
        self.assertIn("七宗罪 VS 七美德完成", text)
        self.assertIn(questions.DISCLAIMER, text)
        with (
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_reject_claimed_guest", return_value=None),
        ):
            historical = json.loads(
                server._tool_play(
                    {
                        "game": "sins_virtues",
                        "action": "sins_virtues_get_result",
                        "params": {"player_id": "mcpsins"},
                    }
                )
            )
        self.assertIn("历史结果", historical["result"]["content"][0]["text"])

    def test_web_entry_result_renderer_identity_stats_and_guide(self):
        self.assertIn("sins_virtues", server.IDENTITY_GAMES)
        self.assertIn("sins_virtues", server.ANTI_ADDICTION_TEST_GAMES)
        self.assertIn("sins_virtues", server.HUMAN_TEST_GAMES)
        self.assertIn(questions.DISCLAIMER, dict(server.GAME_RECOMMENDATIONS)["sins_virtues"])
        self.assertIn(questions.DISCLAIMER, server._tool_list_games())

        index = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        card = index.split('id: "sins_virtues"', 1)[1].split("ranks: []", 1)[0]
        self.assertIn('url: "/sins_virtues"', card)
        self.assertIn(questions.DISCLAIMER.split("；", 1)[1], card)

        page = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn("renderSinsVirtuesResult", page)
        self.assertIn("function drawSinsVirtuesRadar", page)
        self.assertIn("drawDataset(\"sin\"", page)
        self.assertIn("drawDataset(\"virtue\"", page)
        self.assertIn("[20, 40, 60, 80, 100]", page)
        self.assertIn("window.devicePixelRatio", page)
        self.assertIn("context.setTransform(pixelRatio", page)
        self.assertIn("ResizeObserver", page)
        self.assertIn("width: min(100%, 520px)", page)
        self.assertIn("data.pairs.flatMap", page)
        self.assertIn("你的七宗罪 VS 七美德结果", page)
        self.assertIn("最高罪", page)
        self.assertIn("最高德", page)
        self.assertIn("axis-card sins-virtues-pair", page)
        self.assertIn("sins-virtues-pair-tracks", page)
        self.assertIn("七组成对分数（两侧各自 0—100）", page)
        self.assertIn("罪侧独立分 0—100", page)
        self.assertIn("德侧独立分 0—100", page)
        self.assertIn("两侧分数彼此独立，不强制合计 100", page)
        self.assertIn("CONFIG.disclaimer", page)
        self.assertIn('CONFIG.game === "sins_virtues"', page)
        self.assertNotRegex(page, r'<script[^>]+\bsrc=["\']')
        self.assertNotIn("new Chart(", page)
        self.assertNotIn("chart.js", page.lower())

        guide = Path("turtle-soup/backend/guides/sins_virtues.md").read_text(encoding="utf-8")
        self.assertIn(questions.DISCLAIMER, guide)
        self.assertIn("不做互补归一", guide)
        leaderboard = Path("turtle-soup/backend/routers/leaderboard.py").read_text(encoding="utf-8")
        self.assertIn('"sins_virtues"', leaderboard)


if __name__ == "__main__":
    unittest.main()
