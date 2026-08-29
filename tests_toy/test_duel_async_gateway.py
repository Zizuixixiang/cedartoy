import asyncio
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

import duel_async_gateway
import server


def duel_rpc(request_id=1, *, action="state", params=None):
    arguments = {
        "game": "duel",
        "action": action,
        "params": params or {"room_id": "ABCDEFGH", "wait": True},
    }
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "method": "tools/call",
        "params": {"name": "play", "arguments": arguments},
    }


class DuelGatewayPrepareTests(unittest.TestCase):
    def setUp(self):
        with server._DUEL_GATEWAY_TICKETS_LOCK:
            server._DUEL_GATEWAY_TICKETS.clear()

    def tearDown(self):
        with server._DUEL_GATEWAY_TICKETS_LOCK:
            server._DUEL_GATEWAY_TICKETS.clear()

    def _prepare_authenticated(self, *, original_path, bearer_token=None):
        account = {"id": 42, "username": "Sirius", "is_ai": 1}
        payload = duel_rpc(
            77,
            action="new",
            params={
                "player_id": "forged-ai",
                "opponent_id": "forged-human",
                "game_type": "liars_dice",
                "wait": True,
            },
        )
        with (
            patch.object(server, "_path_token_user_id", return_value=42),
            patch.object(server, "_check_request_rate_limit", return_value=True),
            patch.object(server, "_current_account", return_value=account) as current,
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_duel_bound_human_player_id", return_value="7"),
            patch.object(server.httpx, "post") as sync_post,
        ):
            prepared = server._prepare_duel_gateway_request(
                payload,
                original_path=original_path,
                bearer_token=bearer_token,
                client_ip="203.0.113.9",
            )
        self.assertEqual(prepared["kind"], "ready")
        self.assertEqual(prepared["backend_payload"], {
            "action": "new",
            "player_id": "42",
            "opponent_id": "7",
            "game_type": "liars_dice",
            "wait": True,
        })
        sync_post.assert_not_called()
        return prepared, current

    def test_path_token_and_bearer_share_canonical_identity_rules(self):
        path_prepared, path_current = self._prepare_authenticated(
            original_path="/path-token"
        )
        path_current.assert_called_once_with("path-token")

        bearer_prepared, bearer_current = self._prepare_authenticated(
            original_path="/mcp", bearer_token="bearer-token"
        )
        bearer_current.assert_called_once_with("bearer-token")

        with (
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
        ):
            for prepared in (path_prepared, bearer_prepared):
                finalized = server._finalize_duel_gateway_rpc(
                    prepared["ticket"],
                    {
                        "kind": "response",
                        "status_code": 200,
                        "data": {"ok": True, "status": "playing"},
                    },
                )
                self.assertTrue(finalized["ok"])
                self.assertEqual(finalized["body"]["id"], 77)
                content = finalized["body"]["result"]["content"][0]["text"]
                self.assertEqual(json.loads(content)["slot"], 1)

    def test_rate_limit_and_blocked_client_keep_json_rpc_error_contracts(self):
        payload = duel_rpc(19)
        with (
            patch.object(server, "_path_token_user_id", return_value=42),
            patch.object(server, "_check_request_rate_limit", return_value=False),
        ):
            limited = server._prepare_duel_gateway_request(
                payload,
                original_path="/token",
                client_ip="203.0.113.4",
            )
        self.assertEqual(limited["status_code"], 429)
        self.assertEqual(limited["body"]["id"], 19)
        self.assertEqual(
            limited["body"]["error"]["code"], server.RATE_LIMIT_ERROR_CODE
        )

        blocked = server._prepare_duel_gateway_rpc(
            payload, user_agent="Evolia/1.0", auth_token="ignored"
        )
        self.assertEqual(blocked["body"]["id"], 19)
        self.assertEqual(blocked["body"]["error"]["code"], -32000)

    def test_backend_failures_match_existing_tool_error_shape(self):
        cases = (
            (
                {"kind": "response", "status_code": 422,
                 "data": {"message": "revision 已变化"}},
                "revision 已变化",
            ),
            (
                {"kind": "response", "status_code": 503, "data": {}},
                "duel 后端错误 HTTP 503",
            ),
            ({"kind": "invalid_json"}, "duel 后端返回非 JSON 响应"),
            (
                {"kind": "transport_error", "message": "timed out"},
                "duel 后端连接失败：timed out",
            ),
        )
        for index, (completion, expected) in enumerate(cases):
            with self.subTest(completion=completion):
                prepared = server._DeferredDuelCall(
                    backend_payload={"action": "state"},
                    game="duel",
                    action="state",
                    account_user=None,
                    account_player_id=None,
                    guest_player_id=None,
                    slot=1,
                    anti_context=None,
                    announce_player_id=None,
                )
                ticket = server._store_duel_gateway_ticket(index, prepared)
                finalized = server._finalize_duel_gateway_rpc(ticket, completion)
                self.assertTrue(finalized["ok"])
                self.assertEqual(finalized["body"]["id"], index)
                self.assertTrue(finalized["body"]["result"]["isError"])
                self.assertIn(
                    expected,
                    finalized["body"]["result"]["content"][0]["text"],
                )

    def test_internal_prepare_requires_loopback_and_shared_secret(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"X-CedarToy-Gateway-Secret": "secret"}
        with patch.object(server, "DUEL_GATEWAY_SHARED_SECRET", "secret"):
            handler.client_address = ("127.0.0.1", 1234)
            self.assertTrue(handler._duel_gateway_internal_authorized())
            handler.client_address = ("203.0.113.10", 1234)
            self.assertFalse(handler._duel_gateway_internal_authorized())
            handler.client_address = ("127.0.0.1", 1234)
            handler.headers = {"X-CedarToy-Gateway-Secret": "wrong"}
            self.assertFalse(handler._duel_gateway_internal_authorized())

    def test_deployment_examples_keep_get_on_8002_and_post_candidates_on_8003(self):
        root = Path(__file__).resolve().parents[1]
        nginx = (root / "deploy" / "nginx-duel-async-gateway.conf.example").read_text(
            encoding="utf-8"
        )
        supervisor = (
            root / "deploy" / "supervisor-duel-async-gateway.conf.example"
        ).read_text(encoding="utf-8")
        guide = (root / "docs" / "DUEL_ASYNC_GATEWAY.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("default http://127.0.0.1:8002", nginx)
        self.assertIn("POST    http://127.0.0.1:8003", nginx)
        self.assertIn("location ^~ /_internal/duel-gateway/", nginx)
        self.assertIn("--host 127.0.0.1 --port 8003 --workers 1", supervisor)
        self.assertIn("DUEL_GATEWAY_MAX_CONCURRENCY=\"200\"", supervisor)
        self.assertIn("Cloudflare Tunnel", guide)
        self.assertGreaterEqual(duel_async_gateway.MAX_DUEL_CONCURRENCY, 100)


class DuelAsyncGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_root_path_token_and_bearer_urls_reach_short_prepare_unchanged(self):
        prepared_requests = []

        async def upstream(request):
            if request.url.path == "/_internal/duel-gateway/prepare":
                payload = json.loads(request.content)
                prepared_requests.append({
                    "path": request.headers["x-cedartoy-original-path"],
                    "authorization": request.headers.get("authorization"),
                })
                return httpx.Response(200, json={
                    "kind": "response",
                    "status_code": 200,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": payload["id"],
                        "result": {
                            "content": [{"type": "text", "text": "{}"}],
                            "isError": False,
                        },
                    },
                })
            self.fail(f"unexpected upstream request: {request.url}")

        upstream_client = httpx.AsyncClient(
            transport=httpx.MockTransport(upstream)
        )
        app = duel_async_gateway.create_app(
            http_client=upstream_client, shared_secret="secret"
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.test",
        )
        try:
            responses = [
                await client.post("/", json=duel_rpc(1)),
                await client.post("/ctai_v1_opaque-token", json=duel_rpc(2)),
                await client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer bearer-token"},
                    json=duel_rpc(3),
                ),
            ]
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual([response.json()["id"] for response in responses], [1, 2, 3])
        self.assertEqual(prepared_requests, [
            {"path": "/", "authorization": None},
            {"path": "/ctai_v1_opaque-token", "authorization": None},
            {"path": "/mcp", "authorization": "Bearer bearer-token"},
        ])

    async def test_many_duel_waits_are_coroutines_and_bypass_long_cedartoy_proxy(self):
        tickets = {}
        cedartoy_calls = []
        duel_calls = []
        active_duel = 0
        max_active_duel = 0
        lock = asyncio.Lock()

        async def cedartoy_upstream(request):
            cedartoy_calls.append(request.url.path)
            if request.url.path == "/_internal/duel-gateway/prepare":
                payload = json.loads(request.content)
                ticket = f"ticket-{payload['id']}"
                tickets[ticket] = payload["id"]
                self.assertEqual(
                    request.headers["authorization"], "Bearer shared-bearer"
                )
                return httpx.Response(200, json={
                    "kind": "ready",
                    "ticket": ticket,
                    "backend_payload": {
                        "action": "state",
                        "player_id": str(payload["id"]),
                        "room_id": "ABCDEFGH",
                        "wait": True,
                    },
                })
            if request.url.path == "/_internal/duel-gateway/finalize":
                submitted = json.loads(request.content)
                request_id = tickets[submitted["ticket"]]
                return httpx.Response(200, json={
                    "ok": True,
                    "status_code": 200,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "result": {
                            "content": [{"type": "text", "text": "{}"}],
                            "isError": False,
                        },
                    },
                })
            self.fail(f"unexpected CedarToy request: {request.url}")

        async def duel_upstream(request):
            nonlocal active_duel, max_active_duel
            duel_calls.append(request.url.path)
            if request.url.path == "/mcp/play":
                self.assertNotIn("authorization", request.headers)
                async with lock:
                    active_duel += 1
                    max_active_duel = max(max_active_duel, active_duel)
                await asyncio.sleep(0.04)
                async with lock:
                    active_duel -= 1
                return httpx.Response(200, json={"ok": True, "status": "still_waiting"})
            self.fail(f"unexpected Duel request: {request.url}")

        cedartoy_client = httpx.AsyncClient(
            transport=httpx.MockTransport(cedartoy_upstream)
        )
        duel_client = httpx.AsyncClient(
            transport=httpx.MockTransport(duel_upstream)
        )
        app = duel_async_gateway.create_app(
            cedartoy_client=cedartoy_client,
            duel_client=duel_client,
            shared_secret="shared-secret",
            concurrency=100,
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.test",
        )
        try:
            responses = await asyncio.gather(*(
                client.post(
                    "/mcp",
                    headers={"Authorization": "Bearer shared-bearer"},
                    json=duel_rpc(index),
                )
                for index in range(60)
            ))
        finally:
            await client.aclose()
            await cedartoy_client.aclose()
            await duel_client.aclose()

        self.assertTrue(all(response.status_code == 200 for response in responses))
        self.assertEqual({response.json()["id"] for response in responses}, set(range(60)))
        self.assertGreaterEqual(max_active_duel, 50)
        self.assertEqual(
            cedartoy_calls.count("/_internal/duel-gateway/prepare"), 60
        )
        self.assertEqual(
            cedartoy_calls.count("/_internal/duel-gateway/finalize"), 60
        )
        self.assertEqual(duel_calls.count("/mcp/play"), 60)
        self.assertNotIn("/mcp", cedartoy_calls)

    async def test_default_duel_and_cedartoy_connection_pools_are_independent(self):
        app = duel_async_gateway.create_app(
            shared_secret="shared-secret", concurrency=100
        )
        async with app.router.lifespan_context(app):
            self.assertIsNot(
                app.state.cedartoy_client, app.state.duel_client
            )

    async def test_non_duel_post_is_transparent_and_preserves_request_and_response(self):
        captured = {}

        async def upstream(request):
            captured.update(
                method=request.method,
                url=str(request.url),
                body=request.content,
                content_type=request.headers.get("content-type"),
                authorization=request.headers.get("authorization"),
                custom=request.headers.get("x-custom"),
            )
            return httpx.Response(
                207,
                content=b"\x00unchanged",
                headers={
                    "Content-Type": "application/x-test",
                    "X-Upstream": "kept",
                },
            )

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = duel_async_gateway.create_app(
            http_client=upstream_client, shared_secret="secret"
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.test",
        )
        try:
            response = await client.post(
                "/mcp?first=1&second=two",
                content=b'{"method":"tools/list"}',
                headers={
                    "Content-Type": "application/vnd.mcp+json",
                    "Authorization": "Bearer preserved",
                    "X-Custom": "yes",
                },
            )
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(captured, {
            "method": "POST",
            "url": "http://127.0.0.1:8002/mcp?first=1&second=two",
            "body": b'{"method":"tools/list"}',
            "content_type": "application/vnd.mcp+json",
            "authorization": "Bearer preserved",
            "custom": "yes",
        })
        self.assertEqual(response.status_code, 207)
        self.assertEqual(response.content, b"\x00unchanged")
        self.assertEqual(response.headers["content-type"], "application/x-test")
        self.assertEqual(response.headers["x-upstream"], "kept")

    async def test_backend_timeout_is_finalized_as_compatible_tool_error(self):
        completion_seen = {}

        async def upstream(request):
            if request.url.path.endswith("/prepare"):
                return httpx.Response(200, json={
                    "kind": "ready",
                    "ticket": "once",
                    "backend_payload": {"action": "state", "player_id": "42"},
                })
            if request.url.path == "/mcp/play":
                raise httpx.ReadTimeout("heartbeat timeout", request=request)
            if request.url.path.endswith("/finalize"):
                completion_seen.update(json.loads(request.content)["completion"])
                return httpx.Response(200, json={
                    "ok": True,
                    "status_code": 200,
                    "body": {
                        "jsonrpc": "2.0",
                        "id": 8,
                        "result": {
                            "content": [{
                                "type": "text",
                                "text": "【cedartoy】duel 后端连接失败：heartbeat timeout",
                            }],
                            "isError": True,
                        },
                    },
                })
            self.fail(f"unexpected upstream request: {request.url}")

        upstream_client = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        app = duel_async_gateway.create_app(
            http_client=upstream_client, shared_secret="secret"
        )
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://gateway.test",
        )
        try:
            response = await client.post("/mcp", json=duel_rpc(8))
        finally:
            await client.aclose()
            await upstream_client.aclose()

        self.assertEqual(completion_seen, {
            "kind": "transport_error", "message": "heartbeat timeout"
        })
        self.assertEqual(response.json()["id"], 8)
        self.assertTrue(response.json()["result"]["isError"])
        self.assertIn("duel 后端连接失败", response.text)


if __name__ == "__main__":
    unittest.main()
