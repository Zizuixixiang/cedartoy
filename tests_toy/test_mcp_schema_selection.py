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
        stack.enter_context(patch.object(server, "_feedback_unread_reminder", return_value=""))

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
            for path in ("/", "/mcp", "/mcp/", "/fixture-path-token"):
                for query, expected in (
                    ("", standard), ("?schema=legacy", compatibility),
                    ("?probe=1&schema=legacy", compatibility),
                    ("?schema=unknown", standard), ("?schema=standard", standard),
                    ("?schema=", standard), ("?schema=LEGACY", standard),
                    ("?schema=legacy&schema=unknown", standard),
                    ("?schema=unknown&schema=legacy", standard),
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
                                {"type": "text", "text": text} for text in ("body", "notice")
                            ]})
                            identity.assert_called_with(token)
                            if name == "get_guide":
                                tool.assert_called_once_with(arguments, path_token=token)
                            elif name == "list_games":
                                tool.assert_called_once_with(path_token=token)
                            elif name == "account":
                                tool.assert_called_once_with(arguments, user_agent="Kelivo/1.3.1",
                                                             path_token=token, client_ip="127.0.0.1")
                            else:
                                tool.assert_called_once_with(arguments, path_token=token)

    def test_strict_client_credentials_reach_real_account_validation(self):
        # A deliberately too-short password stops before DB access or token issuance.
        # Keep HTTP routing, root dispatch, _tool_account and credential validation real.
        with (
            patch.object(server, "_authenticated_ai_player_id", return_value=None),
            patch.object(server, "_duel_unread_request_reminder", return_value=""),
            patch.object(server, "_mcp_forced_announcement", return_value=None),
        ):
            for mode in ("", "?schema=standard", "?schema=legacy"):
                path = "/mcp" + mode
                tools = self.post(path, "StrictClient/1.0")["result"]["tools"]
                self.assertEqual({t["name"] for t in tools}, {"list_games", "get_guide", "play", "account"})
                properties = next(t for t in tools if t["name"] == "account")["inputSchema"]["properties"]
                for action, handler in (("login_or_register", "_login_or_register_ai"),
                                        ("login", "_login_existing_account")):
                    arguments = {"action": action, "username": "SchemaFixture", "password": "x"}
                    # Reproduce the old action-only schema as a strict client saw it.
                    for declared, expected in (({"action": {}}, "username 和 password 必填"),
                                               (properties, "密码至少 6 位")):
                        filtered = {k: v for k, v in arguments.items() if k in declared}
                        with self.subTest(mode=mode, action=action, declared=list(declared)), patch.object(
                            server, handler, wraps=getattr(server, handler)
                        ) as account_handler:
                            result = self.post(path, "StrictClient/1.0", {
                                "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                "params": {"name": "account", "arguments": filtered},
                            })["result"]
                            self.assertTrue(result["isError"])
                            self.assertIn(expected, result["content"][0]["text"])
                            kwargs = {"client_ip": "127.0.0.1"}
                            if action == "login_or_register":
                                kwargs["avatar"] = None
                            account_handler.assert_called_once_with(
                                filtered.get("username"), filtered.get("password"), **kwargs)

    def test_strict_client_preserves_all_public_account_action_arguments(self):
        # Independent action inventory: mocking only business handlers prevents writes,
        # while real HTTP/root/account dispatch verifies each declared field's routing.
        token = "schema-fixture-token"
        cases = [
            ("login_or_register", {"username": "Fixture", "password": "fixture-pass", "avatar": "🤖"},
             "_login_or_register_ai", ("Fixture", "fixture-pass"), {"client_ip": "127.0.0.1", "avatar": "🤖"}),
            ("login", {"username": "Fixture", "password": "fixture-pass"},
             "_login_existing_account", ("Fixture", "fixture-pass"), {"client_ip": "127.0.0.1"}),
            ("guest_claim_code", {"player_id": "guest:schema-fixture"},
             "_guest_claim_code_for_player_id", ("guest:schema-fixture",), {}),
            ("rename_self", {"new_username": "NewFixture"}, "_rename_self", (token, "NewFixture"), {}),
            ("set_avatar", {"avatar": "🤖"}, "_set_avatar", (token, "🤖"), {}),
            ("rename_bound_machine", {"ai_user_id": 42, "new_username": "NewFixture"},
             "_rename_bound_machine", (token, 42, "NewFixture"), {}),
            ("reset_machine_password", {"ai_user_id": 42, "new_password": "new-fixture-pass"},
             "_reset_machine_password", (token, 42, "new-fixture-pass"), {}),
            ("claim", {"claim_code": "fixture-code", "slot": 2},
             "_claim_guest_saves", (token, "fixture-code"), {"slot": 2}),
            ("my_saves", {"human": True, "username": "HumanFixture"},
             "_account_my_saves", (token,), {"human": True, "username": "HumanFixture"}),
            ("delete_save", {"game": "eco", "slot": 3, "confirm": True}, "_delete_save",
             ({"action": "delete_save", "token": token, "game": "eco", "slot": 3, "confirm": True}, token), {}),
            ("change_password", {"old_password": "old-fixture-pass", "new_password": "new-fixture-pass"},
             "_change_password", (token, "old-fixture-pass", "new-fixture-pass"), {}),
            ("delete_account", {"confirm": True, "current_password": "fixture-pass"},
             "_delete_account", (token, True, "fixture-pass"), {}),
        ] + [(action, {}, handler, (token,), {}) for action, handler in (
            ("generate_binding_token", "_generate_binding_token"), ("rotate_token", "_rotate_ai_token"),
            ("get_bindings", "_get_bindings"), ("get_profile", "_get_profile"),
            ("deletion_status", "_account_deletion_status"), ("cancel_delete_account", "_cancel_account_deletion"),
        )]
        with (
            patch.object(server, "_authenticated_ai_player_id", return_value=None),
            patch.object(server, "_duel_unread_request_reminder", return_value=""),
            patch.object(server, "_mcp_forced_announcement", return_value=None),
        ):
            for mode in ("", "?schema=standard", "?schema=legacy"):
                path = "/mcp" + mode
                tools = self.post(path, "StrictClient/1.0")["result"]["tools"]
                properties = next(t for t in tools if t["name"] == "account")["inputSchema"]["properties"]
                for action, fields, handler, args, kwargs in cases:
                    arguments = {"action": action, "token": token, **fields}
                    filtered = {k: v for k, v in arguments.items() if k in properties}
                    with self.subTest(mode=mode, action=action), patch.object(
                        server, handler, return_value={"fixture": "handled"}
                    ) as account_handler:
                        self.assertEqual(filtered, arguments)
                        result = self.post(path, "StrictClient/1.0", {
                            "jsonrpc": "2.0", "id": 2, "method": "tools/call",
                            "params": {"name": "account", "arguments": filtered},
                        })["result"]
                        self.assertFalse(result["isError"], result)
                        self.assertEqual(json.loads(result["content"][0]["text"]), {"fixture": "handled"})
                        account_handler.assert_called_once_with(*args, **kwargs)
