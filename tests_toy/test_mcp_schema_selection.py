"""Real root HTTP/dispatch/schema chain, without player data or game calls."""

import io
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import server


class McpSchemaSelectionTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(patch.object(server.sqlite3, "connect",
                                         side_effect=AssertionError("unexpected database access")))
        stack.enter_context(patch.object(server, "_request_rate_limit_identity", return_value="fixture"))
        stack.enter_context(patch.object(server, "_check_request_rate_limit", return_value=True))

    def post(self, path, user_agent, payload=None, bearer=""):
        if payload is None:
            payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        raw = json.dumps(payload).encode()
        handler = object.__new__(server.CedarToyHandler)
        handler.path = path
        handler.client_address = ("127.0.0.1", 12345)
        handler.headers = {"Content-Length": str(len(raw)), "User-Agent": user_agent}
        if bearer:
            handler.headers["Authorization"] = "Bearer " + bearer
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response = Mock()
        handler.send_header = Mock()
        handler.end_headers = Mock()
        handler.do_POST()
        self.assertEqual(handler.send_response.call_args.args[0], 200)
        return json.loads(handler.wfile.getvalue())

    def test_default_and_explicit_schema_for_all_clients(self):
        standard = [t for t in server._ROOT_PLATFORM_TOOLS if t["name"] in server._ROOT_TOOL_NAMES]
        compatibility = [t for t in server._KELIVO_PLATFORM_TOOLS if t["name"] in server._ROOT_TOOL_NAMES]
        self.assertNotEqual(standard, compatibility)
        for ua in ("", "ExampleMcpClient/1.0", "Kelivo/1.3.1", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
            default = compatibility if ua.startswith(("Kelivo", "Dart", "ktor")) else standard
            for path in ("/", "/mcp", "/mcp/", "/fixture-path-token"):
                for query, expected in (
                    ("", default), ("?schema=legacy", compatibility),
                    ("?probe=1&schema=legacy", compatibility),
                    ("?schema=unknown", default), ("?schema=standard", standard),
                    ("?schema=", default), ("?schema=LEGACY", default),
                    ("?schema=legacy&schema=unknown", default),
                    ("?schema=unknown&schema=legacy", default),
                ):
                    with self.subTest(ua=ua, path=path, query=query):
                        self.assertEqual(self.post(path + query, ua)["result"]["tools"], expected)

    def test_authentication_and_tool_dispatch_are_unchanged(self):
        with (
            patch.object(server, "_authenticated_ai_player_id", return_value="42") as identity,
            patch.object(server, "_duel_unread_request_reminder", return_value="reminder"),
            patch.object(server, "_mcp_forced_announcement", return_value="notice"),
        ):
            for path, bearer, token in (
                ("/fixture-path-token", "", "fixture-path-token"),
                ("/mcp", "fixture-bearer-token", "fixture-bearer-token"),
                ("/fixture-path-token", "fixture-bearer-token", "fixture-path-token"),
            ):
                for mode in ("", "?schema=legacy", "?schema=standard", "?schema=unknown"):
                    for name in ("list_games", "get_guide", "play", "account"):
                        arguments = {"fixture": "unchanged"}
                        payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                   "params": {"name": name, "arguments": arguments}}
                        with self.subTest(path=path, mode=mode, name=name), patch.object(
                            server, "_tool_" + name, return_value="body"
                        ) as tool:
                            result = self.post(path + mode, "Kelivo/1.3.1", payload, bearer)["result"]
                            self.assertEqual(result, {"isError": False, "content": [
                                {"type": "text", "text": text} for text in ("body", "notice", "reminder")
                            ]})
                            identity.assert_called_with(token)
                            if name == "get_guide":
                                tool.assert_called_once_with(arguments)
                            elif name == "list_games":
                                tool.assert_called_once_with(path_token=token)
                            elif name == "account":
                                tool.assert_called_once_with(arguments, user_agent="Kelivo/1.3.1",
                                                             path_token=token, client_ip="127.0.0.1")
                            else:
                                tool.assert_called_once_with(arguments, path_token=token)
