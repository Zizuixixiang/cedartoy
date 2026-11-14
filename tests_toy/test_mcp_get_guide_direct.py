"""Narrow get_guide alias through real HTTP, using disposable account databases."""
import asyncio
from contextlib import asynccontextmanager
import json
import threading
import unittest
from unittest.mock import patch

import httpx
import server
import duel_async_gateway as gateway
from cedar_backend import mcp_diagnostics as diag
from tests_toy import test_account_security_round4 as accounts


class DirectGuideTests(unittest.TestCase):
    def setUp(self):
        self.accounts = accounts.AccountSecurityRoundFourTests()
        self.accounts.setUp()
        self.addCleanup(self.accounts.tearDown)
        patches = [
            patch.object(diag, "MAIN_SINK", diag.Sink(self.accounts.db_path.parent / "disabled.jsonl")),
            patch.object(server, "_request_rate_limit_identity", return_value="fixture"),
            patch.object(server, "_check_request_rate_limit", return_value=True),
            patch.object(server, "_mcp_forced_announcement", return_value="fixture-announcement"),
            patch.object(server, "_feedback_unread_reminder", return_value="fixture-reminder"),
            patch.object(server.CedarToyHandler, "log_message"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        self.httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=2)
        thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        thread.start()
        def close():
            self.httpd.shutdown()
            self.httpd.server_close()
            thread.join(5)
        self.addCleanup(close)
        self.origin = f"http://127.0.0.1:{self.httpd.server_port}"
        patcher = patch.object(gateway, "CEDARTOY_ORIGIN", self.origin)
        patcher.start()
        self.addCleanup(patcher.stop)

    @asynccontextmanager
    async def client(self, via_gateway):
        async with httpx.AsyncClient(base_url=self.origin, trust_env=False, timeout=3) as backend:
            if not via_gateway:
                yield backend
            else:
                app = gateway.create_app(http_client=backend, shared_secret="fixture-secret")
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                             base_url="http://fixture", timeout=3) as edge:
                    yield edge

    async def post(self, client, method, params, *, rpc_id="guide-id", path="/mcp", headers=None):
        response = await asyncio.wait_for(client.post(path, headers=headers, json={
            "jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params}), 3)
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], rpc_id)
        return body

    def test_valid_invalid_and_wrapped_arguments_match_standard_http(self):
        async def run():
            async with self.client(False) as client:
                for args in ({"game": "duel"}, {"game": "autochess"}, {}, {"game": 12},
                             {"game": "fixture-unknown-game"}, ["invalid"], "invalid", None):
                    standard = await self.post(client, "tools/call", {"name": "get_guide", "arguments": args})
                    for params in (args, {"arguments": args}):
                        direct = await self.post(client, "get_guide", params)
                        self.assertEqual(direct, standard)
                    self.assertEqual(standard["result"]["isError"],
                                     args not in ({"game": "duel"}, {"game": "autochess"}))
                for rpc_id in (0, 17, None):
                    await self.post(client, "get_guide", {"game": "duel"}, rpc_id=rpc_id)
        asyncio.run(run())

    def test_permissions_revoked_tokens_and_path_precedence_through_both_entries(self):
        ordinary_id = self.accounts._add_user("GuideOrdinary", is_ai=True)
        admin_id = self.accounts._add_user("GuideAdmin", is_ai=True)
        with self.accounts._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (admin_id,))
        ordinary = self.accounts._opaque(ordinary_id)
        admin = self.accounts._opaque(admin_id)
        revoked = self.accounts._opaque(admin_id)
        with self.accounts._connect() as conn:
            conn.execute("UPDATE ai_access_tokens SET revoked_at_epoch=1 WHERE token_hash=?",
                         (server._opaque_ai_token_hash(revoked),))
        appendix = (server.GUIDE_DIR / "_feedback_admin.md").read_text(encoding="utf-8")
        async def run():
            for via_gateway in (False, True):
                async with self.client(via_gateway) as client:
                    cases = [("/mcp", {}, False)]
                    for token, allowed in ((ordinary, False), (admin, True), (revoked, False),
                                           ("ctai_v1_invalid_fixture", False)):
                        cases.extend((("/mcp", {"Authorization": "Bearer " + token}, allowed),
                                      ("/" + token, {}, allowed)))
                    cases.append(("/" + ordinary, {"Authorization": "Bearer " + admin}, False))
                    for path, headers, allowed in cases:
                        standard = await self.post(client, "tools/call", {
                            "name": "get_guide", "arguments": {"game": "feedback"}}, path=path, headers=headers)
                        for params in ({"game": "feedback"}, {"arguments": {"game": "feedback"}}):
                            direct = await self.post(client, "get_guide", params, path=path, headers=headers)
                            self.assertEqual(direct, standard)
                            self.assertFalse(direct["result"]["isError"])
                            guide = json.loads(direct["result"]["content"][0]["text"])["guide"]
                            self.assertEqual(appendix in guide, allowed)
        asyncio.run(run())

    def test_notifications_execute_neither_guide_nor_notices(self):
        async def run():
            for via_gateway in (False, True):
                async with self.client(via_gateway) as client:
                    for method, params in (("get_guide", {"game": "duel"}),
                                           ("get_guide", {"arguments": {"game": "autochess"}}),
                                           ("tools/call", {"name": "get_guide", "arguments": {"game": "duel"}})):
                        for path in ("/mcp", "/ctai_v1_fixture"):
                            response = await asyncio.wait_for(client.post(path, json={
                                "jsonrpc": "2.0", "method": method, "params": params}), 3)
                            self.assertEqual(response.status_code, 202)
                            self.assertEqual(response.content, b"")
        with patch.object(server, "_tool_get_guide") as guide, patch.object(
                server, "_mcp_forced_announcement") as announcement, patch.object(
                server, "_feedback_unread_reminder") as reminder:
            asyncio.run(run())
            guide.assert_not_called()
            announcement.assert_not_called()
            reminder.assert_not_called()

    def test_alias_executes_once_with_notices_and_cannot_select_another_tool(self):
        async def run():
            async with self.client(True) as client:
                with patch.object(server, "_tool_get_guide", wraps=server._tool_get_guide) as guide:
                    result = await self.post(client, "get_guide", {
                        "name": "play", "arguments": {"game": "duel"}})
                    guide.assert_called_once_with({"game": "duel"}, path_token="")
                self.assertFalse(result["result"]["isError"])
                self.assertEqual(result["result"]["content"][1:], [
                    {"type": "text", "text": "fixture-announcement"},
                    {"type": "text", "text": "fixture-reminder"}])
                for method in ("list_games", "play", "account", "feedback", "unknown"):
                    result = await self.post(client, method, {"game": "duel"})
                    self.assertEqual(result["error"]["code"], -32601)
        with (patch.object(server, "_tool_play") as play,
              patch.object(server, "_tool_account") as account,
              patch.object(server, "_tool_feedback") as feedback):
            asyncio.run(run())
            play.assert_not_called()
            account.assert_not_called()
            feedback.assert_not_called()

    def test_blocked_client_policy_is_identical(self):
        async def run():
            async with self.client(False) as client:
                direct = await self.post(client, "get_guide", {"game": "duel"})
                standard = await self.post(client, "tools/call", {
                    "name": "get_guide", "arguments": {"game": "duel"}})
                self.assertEqual(direct, standard)
                self.assertEqual(direct["error"]["code"], -32000)
        with patch.object(server, "_blocked_mcp_client_message", return_value="fixture-blocked"), patch.object(
                server, "_tool_get_guide") as guide:
            asyncio.run(run())
            guide.assert_not_called()


if __name__ == "__main__":
    unittest.main()
