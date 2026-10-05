import json
import re
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

import server
from vendor_cmd_adapter import base as vendor_base


_KELIVO_126_SCHEMA_KEYS = {
    "type",
    "description",
    "properties",
    "required",
    "items",
    "enum",
    "additionalProperties",
}


def _kelivo_126_sanitize_node(node):
    """Equivalent to Kelivo v1.2.6's OpenAI/Claude schema sanitizer."""
    if isinstance(node, list):
        return [_kelivo_126_sanitize_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    sanitized = dict(node)
    sanitized.pop("$schema", None)
    if "const" in sanitized:
        value = sanitized.pop("const")
        if isinstance(value, (str, int, float, bool)):
            sanitized["enum"] = [value]
            if sanitized.get("type") is None:
                if isinstance(value, bool):
                    sanitized["type"] = "boolean"
                elif isinstance(value, int):
                    sanitized["type"] = "integer"
                elif isinstance(value, float):
                    sanitized["type"] = "number"
                else:
                    sanitized["type"] = "string"

    for keyword in ("anyOf", "oneOf", "allOf", "any_of", "one_of", "all_of"):
        branches = sanitized.get(keyword)
        if isinstance(branches, list) and branches:
            flattened = _kelivo_126_sanitize_node(branches[0])
            sanitized.pop(keyword, None)
            if isinstance(flattened, dict):
                sanitized.pop("type", None)
                sanitized.pop("properties", None)
                sanitized.pop("items", None)
                sanitized.update(flattened)

    schema_type = sanitized.get("type")
    if isinstance(schema_type, list) and schema_type:
        sanitized["type"] = str(schema_type[0])
    items = sanitized.get("items")
    if isinstance(items, list) and items:
        sanitized["items"] = items[0]
    if isinstance(sanitized.get("items"), dict):
        sanitized["items"] = _kelivo_126_sanitize_node(sanitized["items"])
    if isinstance(sanitized.get("properties"), dict):
        sanitized["properties"] = {
            name: _kelivo_126_sanitize_node(property_schema)
            for name, property_schema in sanitized["properties"].items()
        }
    if isinstance(sanitized.get("additionalProperties"), dict):
        sanitized["additionalProperties"] = _kelivo_126_sanitize_node(
            sanitized["additionalProperties"]
        )
    return {
        key: value
        for key, value in sanitized.items()
        if key in _KELIVO_126_SCHEMA_KEYS
    }


class RootMcpProtocolTests(unittest.TestCase):
    def _initialize(self, params=None):
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
        }
        if params is not None:
            payload["params"] = params
        return server._handle_root_mcp(payload)

    def test_supported_protocol_versions_are_echoed(self):
        for protocol_version in (
            "2024-11-05",
            "2025-03-26",
            "2025-06-18",
            "2025-11-25",
        ):
            with self.subTest(protocol_version=protocol_version):
                response = self._initialize({"protocolVersion": protocol_version})

                self.assertNotIn("error", response)
                self.assertEqual(
                    response["result"]["protocolVersion"], protocol_version
                )

    def test_missing_or_unknown_protocol_version_uses_legacy_version(self):
        for params in (
            None,
            {},
            {"protocolVersion": "unknown"},
            {"protocolVersion": "2026-07-28"},
        ):
            with self.subTest(params=params):
                response = self._initialize(params)

                self.assertNotIn("error", response)
                self.assertEqual(
                    response["result"]["protocolVersion"], "2024-11-05"
                )

    def test_existing_tools_list_and_tools_call_still_work(self):
        listed = server._handle_root_mcp(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}
        )
        self.assertEqual(
            {tool["name"] for tool in listed["result"]["tools"]},
            {"list_games", "get_guide", "play", "account"},
        )

        with patch.object(server, "_tool_list_games", return_value="game catalog"):
            called = server._handle_root_mcp(
                {
                    "jsonrpc": "2.0",
                    "id": 3,
                    "method": "tools/call",
                    "params": {"name": "list_games", "arguments": {}},
                }
            )

        self.assertNotIn("error", called)
        self.assertFalse(called["result"]["isError"])
        self.assertEqual(
            called["result"]["content"],
            [{"type": "text", "text": "game catalog"}],
        )

    @staticmethod
    def _play_schema(user_agent):
        listed = server._handle_root_mcp(
            {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
            user_agent=user_agent,
        )
        return next(
            tool["inputSchema"]
            for tool in listed["result"]["tools"]
            if tool["name"] == "play"
        )

    def test_all_root_clients_get_play_schema_without_root_all_of(self):
        for user_agent in (
            "ExampleMcpClient/1.0",
            "Aru/1.0",
            "Kelivo/1.2.6",
            "Dart/3.9 (dart:io)",
            "ktor-client/3.0",
        ):
            with self.subTest(user_agent=user_agent):
                schema = self._play_schema(user_agent)
                self.assertNotIn("allOf", schema)
                sanitized = _kelivo_126_sanitize_node(schema)
                self.assertEqual(sanitized["type"], "object")
                self.assertEqual(
                    set(sanitized["properties"]), {"game", "action", "params"}
                )
                self.assertEqual(sanitized["required"], ["game", "action"])
                self.assertIs(sanitized["additionalProperties"], True)
                sanitized_params = sanitized["properties"]["params"]
                self.assertEqual(sanitized_params["type"], "object")
                self.assertIs(sanitized_params["additionalProperties"], True)
                self.assertEqual(
                    set(sanitized_params["properties"]),
                    set(schema["properties"]["params"]["properties"]),
                )
                if server._is_kelivo_user_agent(user_agent):
                    for field in (
                        "room_id", "move", "question", "revision", "game_action",
                        "command", "puzzle_id", "checkpoint_id", "answer",
                        "distance_km", "direction", "traveler_name", "invite_code",
                    ):
                        self.assertIn(field, sanitized_params["properties"])
                    options_schema = schema["properties"]["params"]["properties"]["options"]
                    self.assertEqual(options_schema["type"], "array")
                    self.assertEqual(options_schema["items"], {"type": "integer", "minimum": 0})
                    self.assertNotIn("anyOf", options_schema)
                    self.assertIn("单选如 [1]", options_schema["description"])
                else:
                    self.assertEqual(set(sanitized_params["properties"]), {"slot", "command"})

                source_schema = next(
                    tool["inputSchema"]
                    for tool in server._PLATFORM_TOOLS
                    if tool["name"] == "play"
                )
                if server._is_kelivo_user_agent(user_agent):
                    for name in ("game", "action", "params"):
                        self.assertEqual(
                            schema["properties"][name]["description"],
                            source_schema["properties"][name]["description"],
                        )

    def test_ordinary_play_copy_keeps_guide_first_contract_without_game_examples(self):
        for user_agent in ("", "ExampleMcpClient/1.0", "Aru/1.0"):
            with self.subTest(user_agent=user_agent):
                listed = server._handle_root_mcp(
                    {"jsonrpc": "2.0", "id": 4, "method": "tools/list"},
                    user_agent=user_agent,
                )
                tool = next(t for t in listed["result"]["tools"] if t["name"] == "play")
                description = tool["description"]
                self.assertIn("需要时用 list_games", description)
                self.assertIn("调用 play 前先读 get_guide(game)", description)
                self.assertIn("action 与参数以 guide 为准", description)
                self.assertIn("业务参数放 params", description)
                properties = tool["inputSchema"]["properties"]
                self.assertIn("游戏名", properties["game"]["description"])
                self.assertIn("按 get_guide(game)", properties["action"]["description"])
                params_copy = properties["params"]["description"]
                for guidance in ("业务参数对象", "get_guide(game)", "可选 slot=1..5", "平台存档槽"):
                    self.assertIn(guidance, params_copy)
                serialized = json.dumps(tool, ensure_ascii=False)
                for example in ("duel", "nowhere", "detroit", "tarot", "ai_life", "turtle_soup",
                                "room_id", "move", "revision", "distance_km", "export", "import",
                                "open_door", "cancel_wait", "start_game", "record_choice"):
                    self.assertNotIn(example, serialized)

    def test_play_schemas_keep_gemini_numeric_enum_compatibility(self):
        def check_enums(node):
            if isinstance(node, dict):
                if "enum" in node:
                    self.assertTrue(all(isinstance(value, str) for value in node["enum"]), node)
                for value in node.values():
                    check_enums(value)
            elif isinstance(node, list):
                for value in node:
                    check_enums(value)

        for user_agent in ("", "Kelivo/1.2.6", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            with self.subTest(user_agent=user_agent):
                schema = self._play_schema(user_agent)
                check_enums(schema)
                if user_agent:
                    takeover = schema["properties"]["params"]["properties"]["timeout_takeover_seconds"]
                    self.assertEqual(takeover["type"], "integer")
                    self.assertNotIn("enum", takeover)

    def test_removed_play_examples_remain_discoverable_in_each_guide(self):
        # Keep this inventory independent of the shortened schema so deleting
        # an action from both surfaces cannot make the coverage check pass.
        examples = {
            "nowhere": "open_door walk continue_journey schema to direction distance_km",
            "duel": "invite join start chat reclaim new move state rooms cancel_wait "
                    "room_id revision wait full_state message game_type invite_code",
            "turtle_soup": "join ask guess status room_id content",
            "ai_life": "start_game current_decision submit_action decision_id game_action",
            "detroit": "list_saves create_save read_current_scene record_choice play_step "
                       "continue_scene read_progress read_record_card save_chapter_reflection "
                       "start_next_chapter revision node_id label reason reflection",
            "tarot": "invite status result history history_detail request_id question session_id",
            "forest": "lines start observe choose status line content option",
            "crucible_echoes": "new state spin choose skip reroll remove inventory use index item_id",
            "mbti": "mbti_start",
            "dnd": "dnd_start",
        }
        catalog = server._tool_list_games()
        for game, terms in examples.items():
            with self.subTest(game=game):
                self.assertIn(game + "·", catalog)
                guide = server._tool_get_guide({"game": game})
                for term in terms.split():
                    self.assertRegex(guide, r"\b" + re.escape(term) + r"\b")
                self.assertIn("params", guide)

    def test_removed_export_import_support_and_usage_remain_in_guides(self):
        for game in (
            "ai_life", "arcade", "bar", "burger", "camping_plaza", "crucible_echoes",
            "delve", "fishing", "forest", "imitator_td", "leek", "market", "memoria",
            "moonlit", "travel", "white_room",
        ):
            with self.subTest(game=game):
                guide = server._tool_get_guide({"game": game})
                for term in ("export", "import", "save_data", "confirm", "slot"):
                    self.assertRegex(guide, r"\b" + term + r"\b")
                self.assertIn("覆盖", guide)

    def test_platform_actions_and_slot_remain_discoverable_outside_play(self):
        # Rest is platform-wide: one catalog sentence avoids repeating it in
        # every guide, and still makes it discoverable before any lock notice.
        catalog = server._tool_list_games()
        self.assertIn('play(game="当前游戏", action="rest")', catalog)
        self.assertIn("休息", catalog)
        self.assertIn("能否重置按人类设置", catalog)
        account_guide = server._tool_get_guide({"game": "account"})
        self.assertIn("slot=1-5", account_guide)
        self.assertIn("缺省1", account_guide)
        for game in ("account", "duel", "nowhere", "turtle_soup", "mbti", "eco"):
            with self.subTest(game=game):
                guide = server._tool_get_guide({"game": game})
                for term in ("announcements", "vote", "announcement_id", "options", "feedback"):
                    self.assertRegex(guide, r"\b" + term + r"\b")

    def test_shared_difficulty_schema_preserves_each_games_values(self):
        schema = self._play_schema("Kelivo/1.2.6")
        difficulty = schema["properties"]["params"]["properties"]["difficulty"]
        string_branch = next(
            branch for branch in difficulty["anyOf"] if branch.get("type") == "string"
        )
        integer_branch = next(
            branch for branch in difficulty["anyOf"] if branch.get("type") == "integer"
        )

        self.assertEqual(
            set(string_branch["enum"]),
            {
                "casual",
                "experienced",
                "hardcore",
                "normal",
                "hard",
                "hell",
            },
        )
        self.assertEqual(integer_branch["minimum"], 1)
        self.assertEqual(integer_branch["maximum"], 10)

    def test_ordinary_play_params_only_declare_slot_and_command_and_allow_game_parameters(self):
        for user_agent in ("", "ExampleMcpClient/1.0", "Aru/1.0"):
            with self.subTest(user_agent=user_agent):
                schema = self._play_schema(user_agent)
                params = schema["properties"]["params"]
                self.assertEqual(set(schema["properties"]), {"game", "action", "params"})
                self.assertEqual(set(params["properties"]), {"slot", "command"})
                self.assertIs(params["additionalProperties"], True)
                self.assertEqual(params["properties"]["slot"]["type"], "integer")
                self.assertEqual(params["properties"]["command"]["type"], "string")
                validator = Draft202012Validator(schema)
                validator.validate({"game": "fishing", "action": "cmd", "params": {"command": "cast"}})
                for slot in range(1, 6):
                    validator.validate({"game": "puzzle_box", "action": "open", "params": {
                        "slot": slot, "puzzle_id": "N10",
                    }})
                for slot in (0, 6):
                    self.assertFalse(validator.is_valid({"game": "puzzle_box", "action": "open",
                                                         "params": {"slot": slot}}))

    def test_kelivo_keeps_shared_definitions_and_all_compatibility_fields(self):
        shared = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "play")
        shared_params = shared["inputSchema"]["properties"]["params"]["properties"]
        for user_agent in ("Kelivo/1.2.6", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            with self.subTest(user_agent=user_agent):
                params = self._play_schema(user_agent)["properties"]["params"]["properties"]
                self.assertGreaterEqual(len(params), 104)
                tool = next(t for t in server._root_tools(user_agent) if t["name"] == "play")
                self.assertEqual(tool["description"], shared["description"])
                for name, definition in shared_params.items():
                    expected = dict(definition)
                    if name == "timeout_takeover_seconds":
                        expected.pop("enum")  # Existing Gemini numeric-enum workaround.
                    if name in {"is_locked", "include_finished"}:
                        expected.pop("default")  # Do not inject cross-game defaults.
                    self.assertEqual(params[name], expected, name)
                for name in ("command", "species", "a_scores", "answers", "before", "page",
                             "to", "direction", "distance_km", "traveler_name", "cotraveler",
                             "blind", "key", "intent", "topic", "volume", "place", "hours"):
                    self.assertIn(name, params)
                self.assertEqual(params["command"], {"type": "string", "description": "命令文本"})
                for seed in (42, "existing-string-seed"):
                    self.assertTrue(Draft202012Validator(params["seed"]).is_valid(seed))

    def test_compatibility_schema_does_not_inject_turtle_soup_defaults(self):
        for user_agent in ("Kelivo/1.2.6", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            params = self._play_schema(user_agent)["properties"]["params"]["properties"]
            for name in ("is_locked", "include_finished"):
                self.assertNotIn("default", params[name])
                self.assertEqual(params[name]["type"], "boolean")
                self.assertIn("默认 false", params[name]["description"])
        self.assertEqual(set(self._play_schema("ExampleMcpClient/1.0")["properties"]["params"]["properties"]), {"slot", "command"})

    def test_fishing_tool_play_uses_params_command_and_rejects_missing_command(self):
        guide = json.loads(server._tool_get_guide({"game": "fishing"}))["guide"]
        self.assertIn('play(game="fishing", action="cmd", params={"command": "cast 10"})', guide)
        player_id = "guest:fishingcmdschema"
        with (
            tempfile.TemporaryDirectory(prefix="fishing-schema-") as tmp,
            patch.object(vendor_base, "SAVE_ROOT", Path(tmp)),
            patch.object(server.fishing_adapter, "SAVE_ROOT", Path(tmp)),
            patch.object(server, "SESSIONS_DB_PATH", Path(tmp) / "sessions.db"),
            patch.object(server, "_reject_claimed_guest"),
            patch.object(server, "_ensure_guest_claim_code", return_value=None),
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_anti_addiction_context", return_value=None),
        ):
            def play(action, **params):
                return json.loads(server._tool_play({
                    "game": "fishing", "action": action,
                    "params": {"player_id": player_id, **params},
                }))

            created = play("new", seed=42)
            self.assertEqual(created["player_id"], player_id)
            save = Path(tmp) / "fishing" / player_id / "fishing_save.json"
            self.assertEqual(json.loads(save.read_text())["stats"]["total_casts"], 0)
            result = play("cmd", command="cast")
            self.assertEqual(result["game"], "fishing")
            self.assertEqual(result["player_id"], player_id)
            self.assertTrue(result["text"])
            self.assertEqual(json.loads(save.read_text())["stats"]["total_casts"], 1)
            before = save.read_bytes()
            with self.assertRaises(server._McpError) as caught:
                play("cmd")
            self.assertEqual(caught.exception.code, -32602)
            self.assertEqual(caught.exception.message, "command 参数必填")
            self.assertEqual(save.read_bytes(), before)

    def test_kelivo_puzzle_id_and_answer_accept_all_supported_types(self):
        for user_agent in ("Kelivo/1.2.6", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            with self.subTest(user_agent=user_agent):
                schema = self._play_schema(user_agent)
                Draft202012Validator.check_schema(schema)
                params = schema["properties"]["params"]["properties"]
                for name, accepted, rejected in (
                    ("puzzle_id", ["N10", "H06", 10], [None, {}, [], 1.5, True]),
                    ("answer", ["答案", 42, ["断句一", "断句二"]], [None, {}, [1], 1.5, True]),
                ):
                    validator = Draft202012Validator(params[name])
                    for value in accepted:
                        self.assertTrue(validator.is_valid(value), (name, value))
                    for value in rejected:
                        self.assertFalse(validator.is_valid(value), (name, value))
                # v1.2.6 flattens anyOf to its first branch; N10 must still be a string.
                sanitized = _kelivo_126_sanitize_node(schema)
                Draft202012Validator(sanitized).validate({
                    "game": "puzzle_box", "action": "open", "params": {"puzzle_id": "N10"},
                })

    def test_backend_still_rejects_missing_tarot_and_detroit_parameters(self):
        tarot_store = Mock()
        tarot_ai = {"id": 201, "is_ai": 1}
        with (
            patch.object(server, "_tarot_bound_human_user_id", return_value=101),
            patch.object(server, "get_tarot_store", return_value=tarot_store),
        ):
            for arguments, message_fragment in (
                ({"action": "invite", "question": "问题"}, "request_id"),
                (
                    {"action": "invite", "request_id": "tarot_invite_01"},
                    "必须填写",
                ),
            ):
                with self.subTest(game="tarot", arguments=arguments):
                    with self.assertRaises(server._McpError) as caught:
                        server._play_tarot(arguments, tarot_ai)
                    self.assertEqual(caught.exception.code, -32602)
                    self.assertIn(message_fragment, caught.exception.message)
        tarot_store.create_invite.assert_not_called()

        with (
            patch.object(server.detroit_adapter, "_locked", return_value=nullcontext()),
            patch.object(
                server.detroit_adapter,
                "_read_state_unlocked",
                return_value=None,
            ),
            patch.object(
                server.detroit_adapter,
                "_ensure_owner_and_connection_unlocked",
                return_value={},
            ),
        ):
            for arguments, message_fragment in (
                ({}, "name"),
                ({"name": "测试周目"}, "difficulty"),
                (
                    {"name": "测试周目", "difficulty": "normal"},
                    "casual、experienced 或 hardcore",
                ),
            ):
                with self.subTest(game="detroit", arguments=arguments):
                    with self.assertRaises(
                        server.detroit_adapter.DetroitError
                    ) as caught:
                        server.detroit_adapter.play("42", "create_save", arguments)
                    self.assertEqual(caught.exception.status, 400)
                    self.assertIn(message_fragment, caught.exception.message)


if __name__ == "__main__":
    unittest.main()
