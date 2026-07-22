import json
import re
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from ecr import handler as ecr_handler
from ecr import questions as ecr_questions
from ecr import scoring as ecr_scoring
from humanity import handler as humanity_handler
from humanity import questions as humanity_questions
from humanity import scoring as humanity_scoring
from love import handler as love_handler
from love import questions as love_questions
from love import scoring as love_scoring


def _markdown_sections(document, heading_level):
    marker = "#" * heading_level
    matches = list(re.finditer(rf"^{marker} (.+)$", document, re.MULTILINE))
    return [
        (
            match.group(1),
            document[
                match.end() : matches[index + 1].start()
                if index + 1 < len(matches)
                else len(document)
            ].strip(),
        )
        for index, match in enumerate(matches)
    ]


class LoveEcrTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = str(Path(self.temp_dir.name) / "sessions.db")
        self.account_db_path = str(Path(self.temp_dir.name) / "accounts.db")
        self.patches = [
            patch.object(love_handler, "DB_PATH", self.db_path),
            patch.object(ecr_handler, "DB_PATH", self.db_path),
            patch.object(humanity_handler, "DB_PATH", self.db_path),
            patch.object(love_handler, "ACCOUNT_DB_PATH", self.account_db_path),
            patch.object(ecr_handler, "ACCOUNT_DB_PATH", self.account_db_path),
        ]
        for active_patch in self.patches:
            active_patch.start()
        with sqlite3.connect(self.account_db_path) as conn:
            conn.execute(
                "CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, deleted_at TEXT)"
            )
            conn.executemany(
                "INSERT INTO toy_users (id, username, deleted_at) VALUES (?, ?, NULL)",
                ((101, "测试_用户"), (102, "robot_2")),
            )

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def test_ecr_item_text_matches_design_document_exactly(self):
        document = Path("docs/ecr_attachment_design.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\| (\d+) \| (.*?) \| (?:回避|焦虑) \| (?:正向|R) \|$", document, re.MULTILINE)
        self.assertEqual(tuple(text for _, text in rows), ecr_questions.ITEM_TEXTS)
        self.assertEqual([int(number) for number, _ in rows], list(range(1, 37)))

    def test_love_option_text_matches_design_and_order_is_fixed_mixed(self):
        document = Path("docs/love_language_design.md").read_text(encoding="utf-8")
        rows = re.findall(r"^\d+\. Ⓐ (.*?) / Ⓑ (.*?)$", document, re.MULTILINE)
        self.assertEqual(len(rows), 30)
        for question, expected in zip(love_questions.QUESTIONS, rows):
            actual_texts = {option["text"] for option in question["options"]}
            self.assertEqual(actual_texts, set(expected))
        first_dimensions = [question["options"][0]["dimension"] for question in love_questions.QUESTIONS]
        original_first = [row[0] for row in love_questions._RAW]
        self.assertTrue(any(actual == expected for actual, expected in zip(first_dimensions, original_first)))
        self.assertTrue(any(actual != expected for actual, expected in zip(first_dimensions, original_first)))

    def test_love_and_ecr_enriched_primary_copy_matches_document_exactly(self):
        document = Path("docs/enriched_copy_love_ecr.md").read_text(encoding="utf-8")
        love_section = document.split("## 一、爱之语 · 主语言描述", 1)[1].split(
            "## 二、爱之语 · 次语言描述", 1
        )[0]
        love_expected = {
            heading[0]: body
            for heading, body in _markdown_sections(love_section, 3)
        }
        self.assertEqual(love_scoring.DESCRIPTIONS, love_expected)
        self.assertTrue(all("\n\n" in text for text in love_expected.values()))

        ecr_section = document.split("## 三、依恋类型 · 类型描述", 1)[1]
        ecr_sections = dict(_markdown_sections(ecr_section, 3))
        headings = {
            "secure": "安全型（低焦虑低回避）",
            "preoccupied": "迷恋型（高焦虑低回避）",
            "dismissive": "冷漠型（低焦虑高回避）",
            "fearful": "恐惧型（高焦虑高回避）",
        }
        self.assertEqual(
            ecr_scoring.TYPE_DESCRIPTIONS,
            {attachment_type: ecr_sections[heading] for attachment_type, heading in headings.items()},
        )
        self.assertTrue(all("\n\n" in text for text in ecr_scoring.TYPE_DESCRIPTIONS.values()))

    def test_ecr_combination_copy_matches_document_and_keeps_paragraphs(self):
        document = Path("docs/ecr_combination_copy.md").read_text(encoding="utf-8")
        document = document.split("\n---\n\n# 爱之语", 1)[0]
        expected = {}
        for heading, body in _markdown_sections(document, 2):
            match = re.fullmatch(r"(secure|preoccupied|dismissive|fearful) × (secure|preoccupied|dismissive|fearful)（.*）", heading)
            if match is None:
                continue
            expected[tuple(sorted(match.groups()))] = body

        actual = {
            tuple(sorted(pair)): message
            for pair, message in ecr_scoring._PAIR_MESSAGES.items()
        }
        self.assertEqual(set(actual), set(expected))
        self.assertEqual(len(actual), 10)
        for pair, document_copy in expected.items():
            message = actual[pair]
            self.assertEqual(message.replace("\n\n", ""), document_copy)
            self.assertEqual(len(message.split("\n\n")), 3)

        comparison = ecr_scoring.build_compare_data(
            "guest:a",
            "secure",
            {"avoidance": 2.0, "anxiety": 2.0},
            "guest:b",
            "fearful",
            {"avoidance": 5.0, "anxiety": 5.0},
        )
        self.assertEqual(
            comparison["message"],
            actual[tuple(sorted(("secure", "fearful")))],
        )
        mcp_text = ecr_scoring.format_compare(comparison)
        self.assertIn(comparison["message"], mcp_text)
        self.assertNotIn("<p", mcp_text)

        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        ecr_compare = template.split("function renderEcrCompare", 1)[1].split(
            "function renderResult", 1
        )[0]
        self.assertIn(
            'appendResultTextSection(root, "组合提示", data.message)',
            ecr_compare,
        )
        text_helper = template.split("function appendResultTextSection", 1)[1].split(
            "function renderMbtiResult", 1
        )[0]
        self.assertIn("split(/\\n\\s*\\n/)", text_helper)

    def test_love_relation_copy_matches_document_and_all_branches_use_it(self):
        document = Path("docs/ecr_combination_copy.md").read_text(encoding="utf-8")
        love_section = document.split("# 爱之语 · 三种对测关系文案", 1)[1]
        expected = dict(_markdown_sections(love_section, 2))
        self.assertEqual(love_scoring.RELATION_MESSAGES, expected)
        self.assertEqual(set(expected), {"同频", "互译", "错频"})
        self.assertTrue(all(len(message.split("\n\n")) == 2 for message in expected.values()))

        def detail(primary, ranking):
            return {
                "scores": {code: 5 - ranking.index(code) for code in love_questions.DIMENSIONS},
                "ranking": ranking,
                "primary": [primary],
                "secondary": [],
            }

        cases = (
            (
                "同频",
                detail("A", ["A", "B", "C", "D", "E"]),
                detail("A", ["A", "B", "C", "D", "E"]),
            ),
            (
                "互译",
                detail("A", ["A", "B", "C", "D", "E"]),
                detail("B", ["B", "A", "C", "D", "E"]),
            ),
            (
                "错频",
                detail("A", ["A", "B", "C", "D", "E"]),
                detail("B", ["B", "C", "D", "A", "E"]),
            ),
        )
        for relation, detail_a, detail_b in cases:
            comparison = love_scoring.build_compare_data(
                "guest:a", "A", detail_a, "guest:b", detail_b["primary"][0], detail_b
            )
            self.assertEqual(comparison["relation"], relation)
            self.assertEqual(comparison["message"], expected[relation])
            mcp_text = love_scoring.format_compare(comparison)
            self.assertIn(expected[relation], mcp_text)
            self.assertNotIn("<p", mcp_text)

        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        love_compare = template.split("function renderLoveCompare", 1)[1].split(
            "function renderEcrCompare", 1
        )[0]
        self.assertIn(
            'appendResultTextSection(root, "关系提示", data.message)',
            love_compare,
        )

    def test_love_compare_advice_uses_other_primary_then_secondary(self):
        scores = {"A": 8, "B": 7, "C": 6, "D": 5, "E": 4}
        detail_a = {
            "scores": scores,
            "ranking": ["A", "B", "C", "D", "E"],
            "primary": ["A"],
            "secondary": ["B", "C"],
        }
        detail_b = {
            "scores": scores,
            "ranking": ["D", "E", "A", "B", "C"],
            "primary": ["D"],
            "secondary": ["E"],
        }
        comparison = love_scoring.build_compare_data(
            "guest:a", "A", detail_a, "guest:b", "D", detail_b
        )
        self.assertEqual(
            comparison["advice_for_a"],
            [love_scoring.PRACTICES["D"], love_scoring.PRACTICES["E"]],
        )
        self.assertEqual(
            comparison["advice_for_b"],
            [
                love_scoring.PRACTICES["A"],
                love_scoring.PRACTICES["B"],
                love_scoring.PRACTICES["C"],
            ],
        )
        mcp_text = love_scoring.format_compare(comparison)
        self.assertLess(
            mcp_text.index(love_scoring.PRACTICES["D"]),
            mcp_text.index(love_scoring.PRACTICES["E"]),
        )
        self.assertLess(
            mcp_text.index(love_scoring.PRACTICES["A"]),
            mcp_text.index(love_scoring.PRACTICES["B"]),
        )

        no_secondary = {**detail_b, "secondary": []}
        without_fallback = love_scoring.build_compare_data(
            "guest:a", "A", detail_a, "guest:b", "D", no_secondary
        )
        self.assertEqual(
            without_fallback["advice_for_a"], [love_scoring.PRACTICES["D"]]
        )

        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        love_compare = template.split("function renderLoveCompare", 1)[1].split(
            "function renderEcrCompare", 1
        )[0]
        self.assertIn('(data.advice_for_a || []).join("\\n\\n")', love_compare)
        self.assertIn('(data.advice_for_b || []).join("\\n\\n")', love_compare)

    def test_humanity_questions_weights_and_order_match_design_exactly(self):
        document = Path("docs/humanity_design.md").read_text(encoding="utf-8")
        expected = []
        current = None
        for line in document.splitlines():
            heading = re.fullmatch(r"\*\*(\d+)\. (.*?)\*\*", line)
            if heading:
                current = {"number": int(heading.group(1)), "text": heading.group(2), "options": []}
                expected.append(current)
                continue
            option = re.fullmatch(r"- (.*)（(\d)）", line)
            if option and current is not None:
                current["options"].append((option.group(1), int(option.group(2))))
        self.assertEqual([item["number"] for item in expected], list(range(1, 21)))
        self.assertEqual(len(expected), len(humanity_questions.QUESTIONS))
        for question, item in zip(humanity_questions.QUESTIONS, expected):
            self.assertEqual(question["text"], item["text"])
            self.assertEqual(
                [(option["text"], option["weight"]) for option in question["options"]],
                item["options"],
            )

    def test_humanity_enrichment_copy_and_selection_match_design_exactly(self):
        document = Path("docs/report_enrichment.md").read_text(encoding="utf-8")
        high_section = document.split("### 3 分选项短评表", 1)[1].split(
            "### 0 分选项短评表", 1
        )[0]
        zero_section = document.split("### 0 分选项短评表", 1)[1].split(
            "注：", 1
        )[0]

        def comments(section):
            return {
                (int(question_id), int(option_id)): comment
                for question_id, option_id, _text, comment in re.findall(
                    r"^- (\d+)-(\d+) (.*?) → (.*?)$", section, re.MULTILINE
                )
            }

        self.assertEqual(
            humanity_scoring.HUMAN_HIGHLIGHT_COMMENTS, comments(high_section)
        )
        self.assertEqual(
            humanity_scoring.CYBER_EVIDENCE_COMMENTS, comments(zero_section)
        )
        for mapping, weight in (
            (humanity_scoring.HUMAN_HIGHLIGHT_COMMENTS, 3),
            (humanity_scoring.CYBER_EVIDENCE_COMMENTS, 0),
        ):
            for (question_id, option_id) in mapping:
                option = humanity_questions.QUESTIONS[question_id - 1]["options"][option_id - 1]
                self.assertEqual(option["weight"], weight)

        answers = [
            next(option["value"] for option in question["options"] if option["weight"] in {1, 2})
            for question in humanity_questions.QUESTIONS
        ]
        answers[0:6] = [1, 4, 1, 2, 1, 4]
        result = humanity_scoring.score_answers(humanity_questions.QUESTIONS, answers)
        self.assertEqual(
            [entry["question_id"] for entry in result["human_highlights"]],
            [1, 3, 5],
        )
        self.assertEqual(
            [entry["question_id"] for entry in result["cyber_evidence"]],
            [2, 4, 6],
        )
        self.assertEqual(len(result["human_highlights"]), 3)
        self.assertEqual(len(result["cyber_evidence"]), 3)
        text = humanity_scoring.format_result("full_fast", result)
        self.assertIn("━━━ 人味高光 ━━━", text)
        self.assertIn("直接发，有错字也发 → 错字是活人认证水印", text)
        self.assertIn("━━━ 赛博铁证 ━━━", text)
        self.assertIn("等回过神已经写了三段 → 扩写冲动，机之本能", text)

        player_id = "guest:humanityEnrichment"
        humanity_handler.humanity_start({"player_id": player_id, "mode": "full_fast"})
        completed = humanity_handler.humanity_answer_batch(
            {"player_id": player_id, "answers": answers}
        )
        historical = humanity_handler.humanity_get_result({"player_id": player_id})
        for report in (completed, historical):
            self.assertIn("━━━ 人味高光 ━━━", report)
            self.assertIn("━━━ 赛博铁证 ━━━", report)
            self.assertIn("错字是活人认证水印", report)
            self.assertIn("扩写冲动，机之本能", report)

    def test_humanity_band_copy_and_bullet_newlines_match_document_exactly(self):
        document = Path("docs/humanity_enriched_copy.md").read_text(encoding="utf-8")
        sections = dict(_markdown_sections(document, 2))
        headings = {
            "certified_carbon": "90~100% · 认证碳基",
            "human_flavor": "70~89% · 人味充足",
            "mixed_signal": "50~69% · 混合信号",
            "cyber_infiltration": "30~49% · 赛博渗透中",
            "check_cooling": "0~29% · 建议自查散热",
        }
        self.assertEqual(
            humanity_scoring.BAND_DESCRIPTIONS,
            {band: sections[heading] for band, heading in headings.items()},
        )
        for description in humanity_scoring.BAND_DESCRIPTIONS.values():
            paragraphs = description.split("\n\n")
            self.assertEqual(len(paragraphs), 5)
            bullet_lines = paragraphs[2].split("\n")
            self.assertEqual(len(bullet_lines), 5)
            self.assertTrue(all(line.startswith("· ") for line in bullet_lines))

    def test_love_enrichment_copy_and_dynamic_lowest_match_design_exactly(self):
        document = Path("docs/enriched_copy_love_ecr.md").read_text(encoding="utf-8")
        secondary_section = document.split("## 二、爱之语 · 次语言描述", 1)[1].split(
            "## 三、依恋类型 · 类型描述", 1
        )[0]
        expected = {
            code: text
            for code, text in re.findall(
                r"^- ([A-E])·.*?（次）：(.*?)$", secondary_section, re.MULTILINE
            )
        }
        self.assertEqual(love_scoring.SECONDARY_DESCRIPTIONS, expected)

        result = {
            "result_value": "A",
            "scores": {"A": 10, "B": 7, "C": 7, "D": 3, "E": 3},
            "ranking": ["A", "B", "C", "D", "E"],
            "primary": ["A"],
            "secondary": ["B", "C"],
            "lowest": ["D", "E"],
        }
        text = love_scoring.format_result("full_fast", result)
        self.assertIn("━━━ 次语言描述 ━━━", text)
        self.assertIn(
            f"B·优质时间（次）：{love_scoring.SECONDARY_DESCRIPTIONS['B']}", text
        )
        self.assertIn(
            f"C·服务行动（次）：{love_scoring.SECONDARY_DESCRIPTIONS['C']}", text
        )
        reminder_document = Path("docs/report_enrichment.md").read_text(encoding="utf-8")
        reminder_template = re.search(
            r"「(你最收不到的频道是 \{末位维度名\}。如果对方恰好爱用它表达，别怪 TA 没说——帮彼此翻译一下。)」",
            reminder_document,
        ).group(1)
        reminder = reminder_template.replace("{末位维度名}", "赠送礼物、身体接触")
        self.assertEqual(love_scoring.reminder_for_scores(result["scores"]), reminder)
        self.assertIn(reminder, text)
        self.assertNotIn("末位语言 ≠ 不需要，只是优先级低", text)

    def test_ecr_axis_interpretation_uses_document_rule_order_and_exact_copy(self):
        document = Path("docs/report_enrichment.md").read_text(encoding="utf-8")
        expected = re.findall(r"^\d+\. .*? → 「(.*?)」$", document, re.MULTILINE)
        self.assertEqual(len(expected), 7)
        cases = (
            (2.0, 2.0),
            (5.0, 5.0),
            (4.0, 5.0),
            (3.0, 3.5),
            (5.0, 4.0),
            (3.5, 3.0),
        )
        self.assertEqual(
            [ecr_scoring.axis_interpretation(*case) for case in cases], expected[:6]
        )
        self.assertEqual(ecr_scoring.axis_interpretation(2.8, 2.8), expected[-1])
        both_axes_elevated = "两轴都不低——想靠近又想退开，两股力气同时在拉，拧巴本身就是你的状态。"
        self.assertEqual(ecr_scoring.axis_interpretation(4.0, 4.0), both_axes_elevated)
        result = ecr_scoring.score_answers(ecr_questions.QUESTIONS, [4] * 36)
        text = ecr_scoring.format_result("full_fast", result)
        self.assertIn(f"轴解读：{both_axes_elevated}", text)

    def test_love_batch_total_is_always_30_and_compare_runs(self):
        players = ("guest:lovetestA", "guest:lovetestB")
        answer_sets = ([1] * 30, [2] * 30)
        for player_id, answers in zip(players, answer_sets):
            love_handler.love_start({"player_id": player_id, "mode": "full_fast"})
            completed = love_handler.love_answer_batch(
                {"player_id": player_id, "answers": answers}
            )
            self.assertIn("━━━ 次语言描述 ━━━", completed)
            self.assertIn("你最收不到的频道是 ", completed)
            with sqlite3.connect(self.db_path) as conn:
                detail_json = conn.execute(
                    "SELECT result_detail FROM test_results WHERE player_id = ? AND game = 'love'",
                    (player_id,),
                ).fetchone()[0]
            detail = json.loads(detail_json)
            self.assertEqual(sum(detail["scores"].values()), 30)
        comparison = love_handler.love_compare_data(
            {"player_id_a": players[0], "player_id_b": players[1]}
        )
        self.assertEqual(comparison["data"]["kind"], "love_compare")
        self.assertIn(comparison["data"]["relation"], {"同频", "互译", "错频"})
        self.assertNotIn("次语言描述", comparison["text"])

    def test_ecr_all_four_has_both_means_four_and_exact_fisher_values(self):
        result = ecr_scoring.score_answers(ecr_questions.QUESTIONS, [4] * 36)
        self.assertEqual(result["avoidance"], 4.0)
        self.assertEqual(result["anxiety"], 4.0)
        self.assertEqual(
            result["discriminants"],
            {
                "secure": 23.516662299999997,
                "fearful": 29.303682600000002,
                "preoccupied": 26.082357999999996,
                "dismissive": 26.990555200000003,
            },
        )
        self.assertEqual(result["result_value"], "fearful")

    def test_ecr_batch_persists_source_and_compare_runs(self):
        players = ("guest:ecrtestA", "guest:ecrtestB")
        for player_id in players:
            ecr_handler.ecr_start({"player_id": player_id, "mode": "full_fast"})
            text = ecr_handler.ecr_answer_batch({"player_id": player_id, "answers": [4] * 36})
            self.assertIn("回避均分 A：4.00", text)
            self.assertIn("焦虑均分 B：4.00", text)
            self.assertIn("Brennan, Clark & Shaver (1998)", text)
            self.assertIn("轴解读：两轴都不低——想靠近又想退开，两股力气同时在拉，拧巴本身就是你的状态。", text)
        comparison = ecr_handler.ecr_compare_data(
            {"player_id_a": players[0], "player_id_b": players[1]}
        )
        self.assertEqual(comparison["data"]["kind"], "ecr_compare")
        self.assertIn("恐惧型 × 恐惧型", comparison["text"])
        self.assertNotIn("轴解读", comparison["text"])

    def test_love_and_ecr_compare_resolve_account_username_id_and_guest(self):
        for game, handler, total, answer in (
            ("love", love_handler, 30, 1),
            ("ecr", ecr_handler, 36, 4),
        ):
            for player_id in ("101", "999", "guest:webcompareGuest"):
                getattr(handler, f"{game}_start")(
                    {"player_id": player_id, "mode": "full_fast"}
                )
                getattr(handler, f"{game}_answer_batch")(
                    {"player_id": player_id, "answers": [answer] * total}
                )

            by_username = getattr(handler, f"{game}_compare_data")(
                {"player_id_a": "测试_用户", "player_id_b": "guest:webcompareGuest"}
            )
            by_id = getattr(handler, f"{game}_compare_data")(
                {"player_id_a": "101", "player_id_b": "guest:webcompareGuest"}
            )
            self.assertEqual(by_username["data"], by_id["data"])
            self.assertEqual(by_username["data"]["player_a"]["player_id"], "101")
            self.assertEqual(
                by_username["data"]["player_a"]["display_name"], "测试_用户"
            )
            self.assertEqual(
                by_username["data"]["player_b"]["display_name"],
                "guest:webcompareGuest",
            )
            self.assertIn("A · 测试_用户", by_username["text"])
            self.assertIn("B · guest:webcompareGuest", by_username["text"])
            if game == "love":
                self.assertIn("给 测试_用户 的实践建议", by_username["text"])
                self.assertIn(
                    "给 guest:webcompareGuest 的实践建议", by_username["text"]
                )

            mcp_response = handler.handle_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": f"{game}-display-name",
                    "method": "tools/call",
                    "params": {
                        "name": f"{game}_compare",
                        "arguments": {
                            "player_id_a": "101",
                            "player_id_b": "guest:webcompareGuest",
                        },
                    },
                }
            )
            mcp_text = mcp_response["result"]["content"][0]["text"]
            self.assertIn("A · 测试_用户", mcp_text)
            self.assertIn("B · guest:webcompareGuest", mcp_text)

            numeric_fallback = getattr(handler, f"{game}_compare_data")(
                {"player_id_a": "999", "player_id_b": "guest:webcompareGuest"}
            )
            self.assertEqual(
                numeric_fallback["data"]["player_a"]["display_name"], "999"
            )
            self.assertIn("A · 999", numeric_fallback["text"])

            with self.assertRaises(handler.JsonRpcError) as raised:
                getattr(handler, f"{game}_compare_data")(
                    {"player_id_a": "不存在_用户", "player_id_b": "101"}
                )
            self.assertIn("用户名或账号 id", raised.exception.message)

            web_comparison = server._human_test_action(
                game,
                "compare",
                "",
                {
                    "player_id": "guest:webcompareGuest",
                    "player_id_b": "测试_用户",
                },
            )
            self.assertTrue(web_comparison["comparison"])
            self.assertEqual(web_comparison["result_data"]["player_b"]["player_id"], "101")
            self.assertEqual(
                web_comparison["result_data"]["player_b"]["display_name"], "测试_用户"
            )
            self.assertIn("B · 测试_用户", web_comparison["result"])

    def test_root_account_completion_ignores_reported_id_and_echoes_real_slot(self):
        user = {"id": 101, "username": "测试_用户", "is_ai": True}
        common_patches = (
            patch.object(server, "_current_account", return_value=user),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_play_announcements", return_value=""),
        )
        for active_patch in common_patches:
            active_patch.start()
        try:
            server._tool_play_inner(
                {
                    "game": "love",
                    "action": "love_start",
                    "params": {"player_id": "spoofed", "mode": "full_fast", "slot": 3},
                },
                path_token="token",
            )
            response = json.loads(
                server._tool_play_inner(
                    {
                        "game": "love",
                        "action": "love_answer_batch",
                        "params": {"player_id": "spoofed", "answers": [1] * 30, "slot": 3},
                    },
                    path_token="token",
                )
            )
        finally:
            for active_patch in reversed(common_patches):
                active_patch.stop()
        text = response["result"]["content"][0]["text"]
        self.assertIn("存档身份：账号 测试_用户（id 101，槽 3）", text)
        self.assertNotIn("存档身份：spoofed", text)
        with sqlite3.connect(self.db_path) as conn:
            stored_ids = {
                row[0]
                for row in conn.execute(
                    "SELECT player_id FROM test_results WHERE game = 'love'"
                )
            }
        self.assertEqual(stored_ids, {"101:3"})

    def test_humanity_highest_is_100_lowest_is_zero_and_has_no_compare(self):
        highest = [max(question["options"], key=lambda option: option["weight"])["value"] for question in humanity_questions.QUESTIONS]
        lowest = [min(question["options"], key=lambda option: option["weight"])["value"] for question in humanity_questions.QUESTIONS]
        high_result = humanity_scoring.score_answers(humanity_questions.QUESTIONS, highest)
        low_result = humanity_scoring.score_answers(humanity_questions.QUESTIONS, lowest)
        self.assertEqual((high_result["total_score"], high_result["concentration"]), (60, 100))
        self.assertEqual((low_result["total_score"], low_result["concentration"]), (0, 0))
        self.assertNotIn("humanity_compare", {tool["name"] for tool in humanity_handler.TOOLS})

    def test_three_scale_lifecycles_run_in_single_and_batch_modes(self):
        cases = (
            ("love", love_handler, 30, 1),
            ("ecr", ecr_handler, 36, 4),
            ("humanity", humanity_handler, 20, 1),
        )
        completed_players = {}
        for game, handler, total, answer in cases:
            single_player = f"guest:{game}single"
            batch_player = f"guest:{game}batch"
            getattr(handler, f"{game}_start")({"player_id": single_player, "mode": "full"})
            single_text = ""
            for _ in range(total):
                single_text = getattr(handler, f"{game}_answer")(
                    {"player_id": single_player, "answer": answer}
                )
            self.assertIn(f"存档身份：{single_player}", single_text)
            single_result = getattr(handler, f"{game}_get_result")(
                {"player_id": single_player}
            )
            self.assertIn(f"存档身份：{single_player}", single_result)

            getattr(handler, f"{game}_start")({"player_id": batch_player, "mode": "full_fast"})
            batch_text = getattr(handler, f"{game}_answer_batch")(
                {"player_id": batch_player, "answers": [answer] * total}
            )
            self.assertIn(f"存档身份：{batch_player}", batch_text)
            batch_result = getattr(handler, f"{game}_get_result")(
                {"player_id": batch_player}
            )
            self.assertIn(f"存档身份：{batch_player}", batch_result)
            completed_players[game] = (single_player, batch_player)

        for game, handler in (("love", love_handler), ("ecr", ecr_handler)):
            player_a, player_b = completed_players[game]
            self.assertTrue(
                getattr(handler, f"{game}_compare")(
                    {"player_id_a": player_a, "player_id_b": player_b}
                )
            )

    def test_human_web_and_root_registry_include_all_three_games(self):
        for game, total in (("love", 30), ("ecr", 36), ("humanity", 20)):
            player_id = f"guest:web{game}test"
            state = server._human_test_action(game, "start", "", {"player_id": player_id})
            self.assertEqual(state["total"], total)
            self.assertEqual(len(state["questions"]), total)
            self.assertTrue(state["instructions"])
            completed = server._human_test_action(
                game,
                "answer_batch",
                "",
                {"player_id": player_id, "answers": [4 if game == "ecr" else 1] * total},
            )
            self.assertEqual(completed["result_data"]["kind"], game)
            self.assertIn(f"存档身份：{player_id}", completed["result"])
            if game == "love":
                self.assertTrue(completed["result_data"]["secondary_descriptions"])
                self.assertIn("你最收不到的频道是 ", completed["result_data"]["reminder"])
            elif game == "ecr":
                self.assertEqual(
                    completed["result_data"]["axis_interpretation"],
                    "两轴都不低——想靠近又想退开，两股力气同时在拉，拧巴本身就是你的状态。",
                )
            else:
                self.assertLessEqual(len(completed["result_data"]["human_highlights"]), 3)
                self.assertLessEqual(len(completed["result_data"]["cyber_evidence"]), 3)
                self.assertTrue(completed["result_data"]["human_highlights"])
        catalog = server._tool_list_games()
        self.assertIn("love·爱之语测试", catalog)
        self.assertIn("ecr·依恋类型测试", catalog)
        self.assertIn("humanity·人类浓度检测", catalog)
        self.assertIn("love_compare", json.loads(server._tool_get_guide({"game": "love"}))["guide"])
        self.assertIn("ecr_compare", json.loads(server._tool_get_guide({"game": "ecr"}))["guide"])
        self.assertIn("不提供 compare", json.loads(server._tool_get_guide({"game": "humanity"}))["guide"])
        homepage = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        for game in ("love", "ecr", "humanity"):
            block = homepage.split(f'id: "{game}"', 1)[1].split("ranks: []", 1)[0]
            self.assertIn(f'url: "/{game}"', block)
        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn('id="comparePlayerId"', template)
        self.assertIn('api("compare", {player_id_b: playerId})', template)
        self.assertIn("对方用户名或账号 id", template)
        self.assertIn('<p class="entertainment-badge">仅供娱乐</p>', template)
        public_humanity = server._human_test_public_questions("humanity", "full_fast")
        self.assertTrue(all(set(option) == {"value", "text"} for question in public_humanity for option in question["options"]))
        self.assertNotIn('test: { title: "神秘新测试" }', homepage)
        self.assertIn('appendResultTextSection(root, "轴解读", data.axis_interpretation)', template)
        self.assertIn('"人味高光"', template)
        self.assertIn('"赛博铁证"', template)
        self.assertIn("data.secondary_descriptions || []", template)

    def test_ecr_web_uses_seven_point_horizontal_scale_only(self):
        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        render_question = template.split("function renderQuestion()", 1)[1].split(
            "function renderMbtiScale()", 1
        )[0]
        self.assertIn('else if (CONFIG.game === "ecr")', render_question)
        self.assertIn("renderEcrScale(question);", render_question)
        guest_id_function = template.split("function guestPlayerId()", 1)[1].split(
            "function identityBody", 1
        )[0]
        self.assertNotIn("renderEcrScale", guest_id_function)
        self.assertIn('className = "mbti-scale-track ecr-scale-track"', template)
        self.assertIn("[1, 2, 3, 4, 5, 6, 7].forEach", template)
        self.assertIn('aria-label", "从非常不同意到非常同意"', template)
        self.assertIn('<span class="ecr-neutral">中立</span>', template)

        ecr_track_css = template.split(".ecr-scale-track {", 1)[1].split("}", 1)[0]
        self.assertIn("grid-template-columns: repeat(7, minmax(0, 1fr))", ecr_track_css)
        ecr_ends_css = template.split(".ecr-scale-ends {", 1)[1].split("}", 1)[0]
        self.assertIn("white-space: nowrap", ecr_ends_css)
        mobile_css = template.split("@media (max-width: 680px)", 1)[1]
        self.assertIn(".ecr-scale-track .scale-step { min-width: 0", mobile_css)

        # love 与 humanity 仍落在原有的纵向选项按钮分支。
        generic_branch = render_question.split('else if (CONFIG.game === "ecr")', 1)[1]
        self.assertIn("question.options.forEach((option) => addAnswer", generic_branch)

        # compare 卡片与 love 建议标题优先使用后端给出的友好展示名。
        self.assertIn("person.display_name || person.player_id", template)
        self.assertIn("data.player_a.display_name || data.player_a.player_id", template)

    def test_compare_web_cards_separate_scores_from_bold_primary_result(self):
        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        compare_helper = template.split("function appendComparePeople", 1)[1].split(
            "function renderLoveCompare", 1
        )[0]
        self.assertIn('makeResultElement("p", "compare-score-line", details.score)', compare_helper)
        self.assertIn(
            'makeResultElement("p", "compare-emphasis-line", details.emphasis)',
            compare_helper,
        )
        self.assertIn("font-weight: 700", template.split(".compare-emphasis-line", 1)[1].split("}", 1)[0])

        love_compare = template.split("function renderLoveCompare", 1)[1].split(
            "function renderEcrCompare", 1
        )[0]
        self.assertIn('`${code}${person.scores[code]}`', love_compare)
        self.assertIn('emphasis: `主语言：${person.primary_names.join("、")}`', love_compare)

        ecr_compare = template.split("function renderEcrCompare", 1)[1].split(
            "function renderResult", 1
        )[0]
        self.assertIn("score: `回避 ${Number(person.avoidance).toFixed(2)}", ecr_compare)
        self.assertIn("emphasis: `类型：${person.type_name}`", ecr_compare)

        # MCP 文本仍由后端 scoring.format_compare 生成，不包含网页样式字段。
        self.assertNotIn("compare-emphasis-line", love_scoring.format_compare({
            "player_a": {
                "player_id": "a", "scores": {code: 1 for code in love_questions.DIMENSIONS},
                "primary": ["A"],
            },
            "player_b": {
                "player_id": "b", "scores": {code: 1 for code in love_questions.DIMENSIONS},
                "primary": ["B"],
            },
            "relation": "同频", "message": "测试", "advice_for_a": [], "advice_for_b": [],
        }))

    def test_scale_result_pages_and_compare_cards_use_progress_bars(self):
        template = server.TEST_GAME_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn("function appendMetricBars", template)
        self.assertIn('role", "progressbar"', template)
        self.assertIn(".metric-bars-large .metric-bar-track", template)
        self.assertIn(".metric-bars-compact .metric-bar-track", template)

        love_result = template.split("function renderLoveResult", 1)[1].split(
            "function renderEcrResult", 1
        )[0]
        self.assertIn("appendMetricBars(root", love_result)
        self.assertIn("max: 12", love_result)
        self.assertNotIn("appendScoreGrid", love_result)

        ecr_result = template.split("function renderEcrResult", 1)[1].split(
            "function renderHumanityResult", 1
        )[0]
        self.assertIn("appendMetricBars(root", ecr_result)
        self.assertIn("min: 1, max: 7", ecr_result)
        self.assertNotIn("appendScoreGrid", ecr_result)

        humanity_result = template.split("function renderHumanityResult", 1)[1].split(
            "function appendComparePeople", 1
        )[0]
        self.assertIn('max: 100', humanity_result)
        self.assertIn('"metric-bars-large"', humanity_result)
        self.assertIn("humanityFillClasses[data.band]", humanity_result)

        compare_helper = template.split("function appendComparePeople", 1)[1].split(
            "function renderLoveCompare", 1
        )[0]
        self.assertIn("details.bars", compare_helper)
        self.assertIn('"metric-bars-compact"', compare_helper)
        self.assertIn('bars: ["A", "B", "C", "D", "E"]', template)
        self.assertIn('{label: "回避", value: person.avoidance, min: 1, max: 7', template)

        # 单换行项目由网页 CSS 保留；MCP 仍是没有 HTML/CSS 标记的纯文本。
        self.assertIn(".result-copy-paragraph { white-space: pre-line; }", template)
        humanity_text = humanity_scoring.format_result(
            "full_fast",
            humanity_scoring.score_answers(humanity_questions.QUESTIONS, [1] * 20),
        )
        self.assertIn("\n· ", humanity_text)
        self.assertNotIn("metric-bar", humanity_text)


if __name__ == "__main__":
    unittest.main()
