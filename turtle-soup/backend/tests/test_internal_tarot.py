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
judge_stub.tarot_reading_chat = AsyncMock()
module_name = "tarot_internal_bridge_under_test"
module_path = BACKEND_DIR / "routers" / "internal_tarot.py"
spec = importlib.util.spec_from_file_location(module_name, module_path)
internal_tarot = importlib.util.module_from_spec(spec)
sys.modules[module_name] = internal_tarot
with patch.dict(sys.modules, {"judge": judge_stub}):
    spec.loader.exec_module(internal_tarot)


class TarotBridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        app = FastAPI()
        app.include_router(internal_tarot.router)
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://bridge.test",
        )
        self.payload = {
            "messages": [
                {"role": "system", "content": "原版 ARCANUM · 星轨塔罗圣仪提示"},
                {"role": "user", "content": "仅含当前会话的规范化牌面"},
            ],
            "max_tokens": 4096,
            "timeout": 20,
        }

    async def asyncTearDown(self):
        await self.client.aclose()

    async def test_requires_server_only_token_before_pool_access(self):
        chat = AsyncMock()
        with (
            patch.dict("os.environ", {}, clear=True),
            patch.object(internal_tarot, "tarot_reading_chat", chat),
        ):
            missing = await self.client.post(
                "/internal/tarot/reading", json=self.payload
            )
        self.assertEqual(missing.status_code, 503)

        with (
            patch.dict("os.environ", {"TAROT_BRIDGE_TOKEN": "bridge-secret"}),
            patch.object(internal_tarot, "tarot_reading_chat", chat),
        ):
            wrong = await self.client.post(
                "/internal/tarot/reading",
                headers={"Authorization": "Bearer browser-value"},
                json=self.payload,
            )
        self.assertEqual(wrong.status_code, 401)
        chat.assert_not_awaited()

    async def test_reports_and_forwards_the_exact_fixed_model(self):
        for model in ("gemini-3.5-flash", "gemini-3.1-pro-preview"):
            with self.subTest(model=model):
                chat = AsyncMock(return_value="### 牌阵总览\n本次解读")
                with (
                    patch.dict("os.environ", {"TAROT_BRIDGE_TOKEN": "bridge-secret"}),
                    patch.object(internal_tarot, "tarot_reading_chat", chat),
                ):
                    response = await self.client.post(
                        "/internal/tarot/reading",
                        headers={"Authorization": "Bearer bridge-secret"},
                        json={**self.payload, "model": model},
                    )
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["pool"], "tarot")
                self.assertEqual(response.json()["model"], model)
                chat.assert_awaited_once_with(
                    self.payload["messages"],
                    model=model,
                    max_tokens=4096,
                    timeout=20.0,
                )

    async def test_schema_is_bounded_and_extra_provider_fields_are_rejected(self):
        chat = AsyncMock()
        with (
            patch.dict("os.environ", {"TAROT_BRIDGE_TOKEN": "bridge-secret"}),
            patch.object(internal_tarot, "tarot_reading_chat", chat),
        ):
            too_large = await self.client.post(
                "/internal/tarot/reading",
                headers={"Authorization": "Bearer bridge-secret"},
                json={
                    **self.payload,
                    "messages": [{"role": "user", "content": "x" * 50001}],
                },
            )
            injected = await self.client.post(
                "/internal/tarot/reading",
                headers={"Authorization": "Bearer bridge-secret"},
                json={**self.payload, "provider": {"apiKey": "attacker"}},
            )
            arbitrary_model = await self.client.post(
                "/internal/tarot/reading",
                headers={"Authorization": "Bearer bridge-secret"},
                json={**self.payload, "model": "attacker-model"},
            )
        self.assertEqual(too_large.status_code, 422)
        self.assertEqual(injected.status_code, 422)
        self.assertEqual(arbitrary_model.status_code, 422)
        chat.assert_not_awaited()

    async def test_upstream_details_are_not_leaked(self):
        cases = (
            (RuntimeError("private provider detail"), 502),
            (ValueError("private prompt detail"), 422),
            (HTTPException(status_code=503, detail="Tarot API 池繁忙"), 503),
        )
        for error, expected in cases:
            with self.subTest(expected=expected):
                with (
                    patch.dict(
                        "os.environ", {"TAROT_BRIDGE_TOKEN": "bridge-secret"}
                    ),
                    patch.object(
                        internal_tarot,
                        "tarot_reading_chat",
                        AsyncMock(side_effect=error),
                    ),
                ):
                    response = await self.client.post(
                        "/internal/tarot/reading",
                        headers={"Authorization": "Bearer bridge-secret"},
                        json=self.payload,
                    )
                self.assertEqual(response.status_code, expected, response.text)
                self.assertNotIn("private provider detail", response.text)
                self.assertNotIn("private prompt detail", response.text)


if __name__ == "__main__":
    unittest.main()
