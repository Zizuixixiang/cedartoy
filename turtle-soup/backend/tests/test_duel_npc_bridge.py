import asyncio
import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import FastAPI, HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

judge_stub = types.ModuleType("judge")
judge_stub.npc_decision_chat = AsyncMock()
judge_stub.npc_speech_chat = AsyncMock()
module_name = "duel_internal_bridge_under_test"
module_path = BACKEND_DIR / "routers" / "internal_duel.py"
spec = importlib.util.spec_from_file_location(module_name, module_path)
internal_duel = importlib.util.module_from_spec(spec)
sys.modules[module_name] = internal_duel
with patch.dict(sys.modules, {"judge": judge_stub}):
    spec.loader.exec_module(internal_duel)


class DuelNpcBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(internal_duel.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://bridge.test",
        )
        self.payload = {
            "task": "decision",
            "messages": [
                {"role": "system", "content": "只返回合法行动 JSON"},
                {"role": "user", "content": "private-test-state"},
            ],
            "max_tokens": 123,
            "timeout": 7,
        }

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_unconfigured_or_wrong_token_never_reaches_shared_pool(self):
        decision_chat = AsyncMock()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(internal_duel, "npc_decision_chat", decision_chat),
        ):
            missing = await self.client.post(
                "/internal/duel/npc-decision", json=self.payload
            )
        self.assertEqual(missing.status_code, 503)

        with (
            patch.dict(
                "os.environ", {"DUEL_NPC_BRIDGE_TOKEN": "server-only-token"}
            ),
            patch.object(internal_duel, "npc_decision_chat", decision_chat),
        ):
            wrong = await self.client.post(
                "/internal/duel/npc-decision",
                headers={"Authorization": "Bearer browser-token"},
                json=self.payload,
            )
        self.assertEqual(wrong.status_code, 401)
        decision_chat.assert_not_awaited()

    async def test_task_routes_to_separate_bounded_internal_entries(self):
        decision_chat = AsyncMock(return_value='{"action_id":"a_step"}')
        speech_chat = AsyncMock(return_value='{"message":"落子了"}')
        with (
            patch.dict(
                "os.environ", {"DUEL_NPC_BRIDGE_TOKEN": "server-only-token"}
            ),
            patch.object(
                internal_duel, "npc_decision_chat", decision_chat
            ),
            patch.object(internal_duel, "npc_speech_chat", speech_chat),
        ):
            decision_response = await self.client.post(
                "/internal/duel/npc-decision",
                headers={"Authorization": "Bearer server-only-token"},
                json=self.payload,
            )
            speech_response = await self.client.post(
                "/internal/duel/npc-decision",
                headers={"Authorization": "Bearer server-only-token"},
                json={**self.payload, "task": "speech"},
            )
        self.assertEqual(
            decision_response.status_code, 200, decision_response.text
        )
        self.assertEqual(
            decision_response.json(), {"content": '{"action_id":"a_step"}'}
        )
        self.assertEqual(speech_response.status_code, 200, speech_response.text)
        self.assertEqual(
            speech_response.json(), {"content": '{"message":"落子了"}'}
        )
        decision_chat.assert_awaited_once_with(
            self.payload["messages"], max_tokens=123, timeout=7.0
        )
        speech_chat.assert_awaited_once_with(
            self.payload["messages"], max_tokens=123, timeout=7.0
        )
        self.assertNotIn("server-only-token", decision_response.text)
        self.assertNotIn("private-test-state", decision_response.text)

    async def test_timeout_and_upstream_errors_are_safely_mapped(self):
        cases = (
            (asyncio.TimeoutError(), 504, "请求超时"),
            (RuntimeError("secret provider failure"), 502, "上游失败"),
            (ValueError("private invalid detail"), 422, "格式无效"),
            (HTTPException(status_code=429, detail="NPC API 池繁忙"), 429, "繁忙"),
        )
        for error, status, detail in cases:
            with self.subTest(status=status):
                with (
                    patch.dict(
                        "os.environ",
                        {"DUEL_NPC_BRIDGE_TOKEN": "server-only-token"},
                    ),
                    patch.object(
                        internal_duel,
                        "npc_decision_chat",
                        AsyncMock(side_effect=error),
                    ),
                ):
                    response = await self.client.post(
                        "/internal/duel/npc-decision",
                        headers={"Authorization": "Bearer server-only-token"},
                        json=self.payload,
                    )
                self.assertEqual(response.status_code, status, response.text)
                self.assertIn(detail, response.text)
                self.assertNotIn("secret provider failure", response.text)
                self.assertNotIn("private invalid detail", response.text)

    async def test_schema_rejects_unbounded_messages_before_pool_call(self):
        decision_chat = AsyncMock()
        with (
            patch.dict(
                "os.environ", {"DUEL_NPC_BRIDGE_TOKEN": "server-only-token"}
            ),
            patch.object(internal_duel, "npc_decision_chat", decision_chat),
        ):
            response = await self.client.post(
                "/internal/duel/npc-decision",
                headers={"Authorization": "Bearer server-only-token"},
                json={
                    "task": "decision",
                    "messages": [{"role": "user", "content": "x" * 4001}],
                    "max_tokens": 5000,
                    "timeout": 61,
                },
            )
        self.assertEqual(response.status_code, 422)
        decision_chat.assert_not_awaited()

    async def test_schema_requires_strict_task_enum(self):
        decision_chat = AsyncMock()
        speech_chat = AsyncMock()
        with (
            patch.dict(
                "os.environ", {"DUEL_NPC_BRIDGE_TOKEN": "server-only-token"}
            ),
            patch.object(internal_duel, "npc_decision_chat", decision_chat),
            patch.object(internal_duel, "npc_speech_chat", speech_chat),
        ):
            for payload in (
                {key: value for key, value in self.payload.items() if key != "task"},
                {**self.payload, "task": "npc"},
            ):
                response = await self.client.post(
                    "/internal/duel/npc-decision",
                    headers={"Authorization": "Bearer server-only-token"},
                    json=payload,
                )
                self.assertEqual(response.status_code, 422, response.text)
        decision_chat.assert_not_awaited()
        speech_chat.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
