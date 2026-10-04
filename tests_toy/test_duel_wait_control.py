import asyncio
import json
import unittest
from unittest.mock import patch

import httpx
import server
import duel_async_gateway as gateway
import duel_wait_control as control


def prepared_call(action="state", **params):
    return server._DeferredDuelCall(
        backend_payload={"action": action, "player_id": "wait-test-ai", "room_id": "WAITTEST", **params},
        game="duel", action=action, account_user=None, account_player_id=None,
        guest_player_id=None, slot=1, anti_context=None, announce_player_id=None,
    )


class WaitFinalizationTests(unittest.TestCase):
    def setUp(self):
        self.tickets = []

    def tearDown(self):
        for ticket in self.tickets:
            server._discard_duel_gateway_ticket(ticket)

    def ticket(self, action="state", **params):
        call = prepared_call(action, **params)
        ticket = server._store_duel_gateway_ticket(1, call)
        self.tickets.append(ticket)
        return ticket, call

    def finalize(self, ticket):
        response = {"ok": True, "status": "playing", "room_id": "WAITTEST", "your_turn": True, "revision": 3}
        with patch.object(server, "_finalize_play_response", side_effect=lambda response, **kwargs: response):
            result = server._finalize_duel_gateway_rpc(ticket, {"kind": "response", "status_code": 200, "data": response})
        return json.loads(result["body"]["result"]["content"][0]["text"])

    def test_buffered_turn_response_is_cancelled_at_finalize(self):
        first, _ = self.ticket(wait=True)
        self.ticket("cancel_wait")
        payload = self.finalize(first)
        self.assertEqual(payload["status"], "wait_cancelled")
        self.assertNotIn("your_turn", payload)
        self.assertNotIn("next_call", payload)

    def test_supersede_and_old_abandon_cannot_invalidate_new_wait(self):
        first, _ = self.ticket(wait=True)
        second, _ = self.ticket(wait=True)
        self.assertEqual(self.finalize(first)["status"], "wait_cancelled")
        server._discard_duel_gateway_ticket(first)
        self.assertTrue(self.finalize(second)["your_turn"])

    def test_cancel_during_response_finalization_is_rechecked(self):
        first, _ = self.ticket(wait=True)
        def cancel_during_format(prepared, response):
            self.ticket("cancel_wait")
            return json.dumps(response)
        with patch.object(server, "_finalize_deferred_duel_call", side_effect=cancel_during_format):
            self.assertEqual(self.finalize(first)["status"], "wait_cancelled")

    def test_cancelled_backend_errors_cannot_prompt_old_chain_to_retry(self):
        for completion in (
            {"kind": "transport_error", "message": "disconnected"},
            {"kind": "response", "status_code": 409, "data": {"message": "revision conflict"}},
        ):
            with self.subTest(completion=completion):
                first, _ = self.ticket(wait=True)
                self.ticket("cancel_wait")
                result = server._finalize_duel_gateway_rpc(first, completion)["body"]["result"]
                self.assertFalse(result["isError"])
                payload = json.loads(result["content"][0]["text"])
                self.assertEqual(payload["status"], "wait_cancelled")
                self.assertNotIn("next_call", payload)

    def test_cancel_then_new_wait_recovers_and_nonwait_invalidates(self):
        for action in ("state", "move", "resign", "leave", "cancel_wait"):
            with self.subTest(action=action):
                first, _ = self.ticket(wait=True)
                self.ticket(action)
                self.assertEqual(self.finalize(first)["status"], "wait_cancelled")
                second, _ = self.ticket(wait=True)
                self.assertTrue(self.finalize(second)["your_turn"])

    def test_sync_fallback_guard_and_canonical_control_fields(self):
        def backend(payload):
            control.begin({"action": "cancel_wait", "player_id": payload["player_id"], "room_id": payload["room_id"]})
            return {"ok": True, "status": "playing", "room_id": "WAITTEST", "your_turn": True}
        with patch.object(server, "_request_duel_backend", side_effect=backend):
            response = server._play_duel({"action": "state", "player_id": "wait-test-ai", "room_id": "waittest", "wait": True})
        self.assertEqual(response["status"], "wait_cancelled")
        payload = server._prepare_duel_payload({"action": "cancel_wait", "room_id": "WAITTEST", "player_id": "forged", "wait_generation": 1, "wait_resume": True, "message": "must not post"}, trusted_player_id="real-ai")
        self.assertEqual(payload, {"action": "cancel_wait", "room_id": "WAITTEST", "player_id": "real-ai"})

    def test_cancel_prepare_uses_path_or_bearer_identity_even_during_cooldown(self):
        rpc = {"id": 2, "method": "tools/call", "params": {"name": "play", "arguments": {
            "game": "duel", "action": "cancel_wait", "params": {
                "room_id": "WAITTEST", "player_id": "victim", "wait_generation": 1,
            },
        }}}
        for path, bearer in (("/path-token", None), ("/mcp", "bearer-token")):
            with self.subTest(path=path), patch.object(server, "_check_request_rate_limit", return_value=True), \
                 patch.object(server, "_path_token_user_id", return_value=42), \
                 patch.object(server, "_current_account", return_value={"id": 42, "is_ai": 1}) as account, \
                 patch.object(server, "_auto_migrate_legacy_account_saves"), \
                 patch.object(server, "_anti_addiction_context", return_value=None), \
                 patch.object(server, "_anti_addiction_preflight", return_value={"status": "cooldown"}) as preflight:
                result = server._prepare_duel_gateway_request(rpc, original_path=path, bearer_token=bearer)
                self.assertEqual(result["kind"], "ready")
                self.tickets.append(result["ticket"])
                self.assertEqual(result["backend_payload"]["player_id"], "42")
                self.assertGreater(result["backend_payload"]["wait_generation"], 1)
                account.assert_called_once_with(bearer or "path-token")
                preflight.assert_not_called()

    def test_cancel_response_has_no_gameplay_finalization_side_effects(self):
        payload = control.cancelled_response("WAITTEST")
        call = prepared_call("cancel_wait")
        with patch.object(server.game_activity, "record") as record, \
             patch.object(server, "_play_announcements") as announcements:
            self.assertEqual(json.loads(server._finalize_deferred_duel_call(call, payload)), payload)
            record.assert_not_called()
            announcements.assert_not_called()

    def test_buffered_response_skips_gameplay_side_effects_after_cancel(self):
        first, _ = self.ticket(wait=True)
        self.ticket("cancel_wait")
        with patch.object(server.game_activity, "record") as record, \
             patch.object(server, "_play_announcements") as announcements:
            result = server._finalize_duel_gateway_rpc(first, {
                "kind": "response", "status_code": 200,
                "data": {"ok": True, "status": "playing", "your_turn": True},
            })
        payload = json.loads(result["body"]["result"]["content"][0]["text"])
        self.assertEqual(payload["status"], "wait_cancelled")
        record.assert_not_called()
        announcements.assert_not_called()


class GatewayWaitControlTests(unittest.IsolatedAsyncioTestCase):
    async def test_heartbeats_keep_generation_and_cleanup_is_conditional(self):
        seen = []
        call = prepared_call("move", wait=True, move={"row": 0, "col": 0}, revision=1, message="once")
        ticket = server._store_duel_gateway_ticket(1, call)
        async def platform(request):
            data = json.loads(request.content)
            if request.url.path.endswith("prepare"):
                return httpx.Response(200, json={"kind": "ready", "ticket": ticket, "backend_payload": call.backend_payload})
            with patch.object(server, "_finalize_play_response", side_effect=lambda response, **kwargs: response):
                result = server._finalize_duel_gateway_rpc(data["ticket"], data["completion"])
            return httpx.Response(200, json=result)
        async def backend(request):
            data = json.loads(request.content)
            seen.append(data)
            if data["action"] == "cancel_wait":
                return httpx.Response(200, json={"ok": True, "status": "wait_cancelled"})
            return httpx.Response(200, json={"ok": True, "status": "still_waiting" if len(seen) < 3 else "playing", "room_id": "WAITTEST", "revision": 2, "your_turn": len(seen) == 3})
        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as pc, httpx.AsyncClient(transport=httpx.MockTransport(backend)) as bc:
            app = gateway.create_app(cedartoy_client=pc, duel_client=bc, shared_secret="test")
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await client.post("/mcp", json={"id": 1, "method": "tools/call", "params": {"name": "play", "arguments": {"game": "duel", "action": "move", "params": {"wait": True}}}})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(seen), 4)
        generation = seen[0]["wait_generation"]
        for heartbeat in seen[1:3]:
            self.assertEqual(heartbeat, {"action": "state", "player_id": "wait-test-ai", "room_id": "WAITTEST", "wait": True, "wait_generation": generation, "wait_resume": True})
        self.assertEqual(seen[3], {"action": "cancel_wait", "player_id": "wait-test-ai", "room_id": "WAITTEST", "wait_generation": generation})
        self.assertNotIn(ticket, server._DUEL_GATEWAY_TICKETS)

    async def test_cancel_does_not_queue_behind_full_wait_slots(self):
        async def platform(request):
            return httpx.Response(200, json={"kind": "response", "body": {"ok": True}})
        async with httpx.AsyncClient(transport=httpx.MockTransport(platform)) as upstream:
            app = gateway.create_app(http_client=upstream, shared_secret="test")
            app.state.duel_slots = asyncio.Semaphore(0)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                response = await asyncio.wait_for(client.post("/mcp", json={"id": 1, "method": "tools/call", "params": {"name": "play", "arguments": {"game": "duel", "action": "cancel_wait", "params": {"room_id": "WAITTEST"}}}}), 1)
        self.assertEqual(response.json(), {"ok": True})
