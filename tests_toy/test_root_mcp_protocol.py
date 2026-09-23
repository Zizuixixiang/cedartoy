import unittest
from contextlib import nullcontext
from unittest.mock import Mock, patch

import server


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
                for field in (
                    "room_id",
                    "question",
                    "revision",
                    "game_action",
                ):
                    self.assertIn(
                        field,
                        sanitized_params["properties"],
                    )
                if server._is_kelivo_user_agent(user_agent):
                    self.assertIn("command", sanitized_params["properties"])

                options_schema = schema["properties"]["params"]["properties"][
                    "options"
                ]
                self.assertEqual(options_schema["type"], "array")
                self.assertEqual(
                    options_schema["items"],
                    {"type": "integer", "minimum": 0},
                )
                self.assertNotIn("anyOf", options_schema)
                self.assertIn("单选如 [1]", options_schema["description"])

                source_schema = next(
                    tool["inputSchema"]
                    for tool in server._PLATFORM_TOOLS
                    if tool["name"] == "play"
                )
                self.assertEqual(
                    schema["properties"]["action"]["description"],
                    source_schema["properties"]["action"]["description"],
                )
                self.assertEqual(
                    schema["properties"]["params"]["description"],
                    source_schema["properties"]["params"]["description"],
                )

    def test_shared_difficulty_schema_preserves_each_games_values(self):
        schema = self._play_schema("ExampleMcpClient/1.0")
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
