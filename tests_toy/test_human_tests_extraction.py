"""Regression coverage for server's human-test dependency/patch boundary."""

import re
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server


class HumanTestsExtractionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="human-tests-extraction-")
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "sessions.db"
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.db))
        for config in server.HUMAN_TEST_GAMES.values():
            self.stack.enter_context(patch.object(config["handler"], "DB_PATH", str(self.db)))

    def test_replaced_game_mapping_owns_database_and_question_source(self):
        other_db = Path(self.temp.name) / "other.db"
        with sqlite3.connect(other_db) as conn:
            conn.execute("CREATE TABLE test_sessions (player_id, game, mode, current_question)")
            conn.execute("INSERT INTO test_sessions VALUES ('guest:webfixture', 'mbti', 'patched', 0)")
            conn.execute("CREATE TABLE test_results (player_id, game, result_value, result_detail)")
            conn.execute("INSERT INTO test_results VALUES ('guest:webfixture', 'mbti', 'unknown', '{}')")
        questions = Mock()
        questions.get_questions.return_value = [
            {"text": "patched question", "option_a": "A", "option_b": "B"},
        ]
        handler = SimpleNamespace(DB_PATH=str(other_db))
        with patch.object(server, "HUMAN_TEST_GAMES", {"mbti": {"handler": handler, "questions": questions}}):
            self.assertEqual(
                server._human_test_active_session("mbti", "guest:webfixture"),
                {"mode": "patched", "progress": 0, "total": 1},
            )
            self.assertEqual(
                server._human_test_public_questions("mbti", "patched"),
                [{"number": 1, "text": "patched question", "option_a": "A", "option_b": "B"}],
            )
            self.assertIsNone(server._human_test_result_data("mbti", "guest:webfixture"))
        self.assertFalse(self.db.exists())

    def test_replaced_identity_rules_editions_and_callbacks_are_live(self):
        with patch.object(server, "WEB_GUEST_PLAYER_ID_RE", re.compile(r"guest:custom")):
            self.assertEqual(server._human_test_player_id("mbti", "", "guest:custom"), ("guest:custom", "guest"))
            with self.assertRaises(server._McpError) as error:
                server._human_test_player_id("mbti", "", "guest:webfixture")
            self.assertEqual(error.exception.code, -32602)
        with patch.object(server, "_human_test_player_context", return_value=("99", "account", {})) as context:
            self.assertEqual(server._human_test_player_id("mbti", "token", "spoof"), ("99", "account"))
            context.assert_called_once_with("mbti", "token", "spoof")
        with patch.object(server, "HUMAN_TEST_PUBLIC_EDITIONS", {"mbti": {"custom": "patched"}}):
            self.assertEqual(server._human_test_public_edition("mbti", "patched"), "custom")
        with (
            patch.object(server, "_human_test_public_edition", return_value="custom"),
            patch.object(server, "_human_test_public_questions", return_value=[{"text": "patched"}]),
        ):
            state = server._human_test_public_state("mbti", "99", "account", {"mode": "patched", "total": 1})
        self.assertEqual(state["edition"], "custom")
        self.assertEqual(state["questions"], [{"text": "patched"}])

    def test_action_uses_patched_mapping_callbacks_and_activity_path(self):
        handler = SimpleNamespace(mbti_start=Mock())
        user = {"id": 99, "username": "fixture", "is_ai": False}
        session = {"mode": "patched", "total": 1, "progress": 0}
        with (
            patch.object(server, "HUMAN_TEST_GAMES", {"mbti": {"handler": handler}}),
            patch.object(server, "HUMAN_TEST_PUBLIC_EDITIONS", {"mbti": {"custom": "patched"}}),
            patch.object(server, "_human_test_player_context", return_value=("99", "account", user)),
            patch.object(server, "_storage_identity_line", return_value="identity") as identity,
            patch.object(server, "_human_test_active_session", return_value=session) as active,
            patch.object(server, "_human_test_public_state", return_value={"patched": True}) as state,
            patch.object(server.game_activity, "record") as record,
        ):
            self.assertEqual(server._human_test_action("mbti", "start", "token", {"edition": "custom"}), {"patched": True})
        handler.mbti_start.assert_called_once_with({"player_id": "99", "mode": "patched"})
        identity.assert_called_once_with("99", user)
        active.assert_called_once_with("mbti", "99")
        state.assert_called_once_with("mbti", "99", "account", session)
        record.assert_called_once_with(self.db, "mbti", "start", user, {"ok": True})

    def test_result_retry_uses_server_conversion_callbacks(self):
        with (
            patch.object(server.mbti_handler, "mbti_get_result", return_value="raw") as get_result,
            patch.object(server, "_human_test_active_session", return_value=None),
            patch.object(server, "_replace_storage_identity_text", return_value="identity replaced") as replace,
            patch.object(server, "_human_test_public_result", return_value="public") as public,
            patch.object(server, "_human_test_result_data", return_value={"fixture": True}) as data,
        ):
            for action in ("result", "answer_batch"):
                with self.subTest(action=action):
                    result = server._human_test_action("mbti", action, "", {"player_id": "guest:webfixture", "answers": [3]})
                    self.assertEqual(result["result"], "public")
                    self.assertEqual(result["result_data"], {"fixture": True})
                    get_result.assert_called_with({"player_id": "guest:webfixture"})
                    replace.assert_called_with("raw", "存档身份：guest:webfixture")
                    public.assert_called_with("mbti", "identity replaced")
                    data.assert_called_with("mbti", "guest:webfixture")

    def test_result_conversion_uses_replaced_scoring_module(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE test_results (player_id, game, result_value, result_detail)")
            conn.execute(
                "INSERT INTO test_results VALUES (?, ?, ?, ?)",
                ("guest:webfixture", "humanity", "fixture", '{"concentration": 42}'),
            )
        scoring = SimpleNamespace(BAND_NAMES={"fixture": "name"}, BAND_DESCRIPTIONS={"fixture": "description"}, FOOTNOTE="footnote")
        with patch.object(server, "humanity_scoring", scoring):
            self.assertEqual(server._human_test_result_data("humanity", "guest:webfixture"), {
                "kind": "humanity", "concentration": 42, "band": "fixture", "band_name": "name",
                "description": "description", "human_highlights": [], "cyber_evidence": [], "footnote": "footnote",
            })

    def test_missing_tables_are_tolerated_but_invalid_schema_is_not(self):
        for function in (server._human_test_active_session, server._human_test_result_data):
            self.assertIsNone(function("mbti", "guest:webfixture"))
        with sqlite3.connect(self.db) as conn:
            conn.execute("CREATE TABLE test_sessions (player_id, game)")
            conn.execute("CREATE TABLE test_results (player_id, game)")
        for function in (server._human_test_active_session, server._human_test_result_data):
            with self.assertRaises(sqlite3.OperationalError):
                function("mbti", "guest:webfixture")
