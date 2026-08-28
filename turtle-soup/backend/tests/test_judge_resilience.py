import asyncio
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException


BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

database_stub = types.ModuleType("database")
database_stub.DEFAULT_SETTINGS = {
    "judge_prompt": "judge",
    "generate_prompt": "generate",
}
database_stub.fetch_all = AsyncMock(return_value=[])
database_stub.fetch_one = AsyncMock(return_value=None)
database_stub.execute = AsyncMock(return_value=1)
auth_utils_stub = types.ModuleType("auth_utils")
auth_utils_stub.admin_player = lambda: None
auth_utils_stub.verify_password = lambda *_args: True
sys.modules["database"] = database_stub
sys.modules["auth_utils"] = auth_utils_stub

import judge  # noqa: E402
from routers import admin as admin_router  # noqa: E402


CONFIG = {
    "id": 7,
    "name": "primary",
    "api_url": "https://example.test/v1",
    "api_key": "secret-key-value",
    "model": "judge-model",
    "purpose": "both",
    "enabled": 1,
    "priority": 0,
}
MESSAGES = [{"role": "user", "content": "hello"}]


def response(status: int, *, headers=None, content="ok") -> httpx.Response:
    request = httpx.Request("POST", "https://example.test/v1/chat/completions")
    body = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": content},
            }
        ]
    }
    return httpx.Response(status, headers=headers, json=body, request=request)


class FakeClient:
    outcomes = []
    timeouts = []
    payloads = []
    urls = []

    def __init__(self, *, timeout):
        self.timeouts.append(timeout)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def post(self, url, *, headers, json):
        del headers
        self.urls.append(url)
        self.payloads.append(json)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


class ClueParsingTests(unittest.IsolatedAsyncioTestCase):
    ANSWER = (
        "汤底正文\n"
        "【线索公布】第一条线索【线索公布结束】\n"
        "中间内容\n"
        "【线索公布】第二条具体线索【线索公布结束】"
    )

    async def test_empty_clue_marker_keeps_judgment_without_publishing(self):
        with (
            patch.object(judge, "_get_judge_prompt", AsyncMock(return_value="judge")),
            patch.object(judge, "_get_judge_prompt_clue", AsyncMock(return_value="clue")),
            patch.object(
                judge,
                "_chat_validated",
                AsyncMock(return_value="是\n【线索公布】"),
            ),
        ):
            result = await judge.judge_ask("汤面", self.ANSWER, "问题")

        self.assertEqual(result["judgment"], "yes")
        self.assertIsNone(result["clue"])
        self.assertIsNone(judge._extract_clue_from_ask("是\n【线索公布】"))

    def test_specific_clue_matches_second_closed_block(self):
        clue = judge._extract_clue_from_answer(self.ANSWER, "第二条具体线索")
        self.assertEqual(clue, "第二条具体线索")

    def test_unmatched_specific_clue_does_not_fallback_to_first(self):
        clue = judge._extract_clue_from_answer(self.ANSWER, "不存在的模糊线索")
        self.assertIsNone(clue)

    def test_normal_closed_clue_block_remains_available(self):
        clue = judge._extract_clue_from_answer(self.ANSWER, "第一条线索")
        self.assertEqual(clue, "第一条线索")

    def test_legacy_unclosed_clue_format_remains_available(self):
        answer = "汤底\n【线索公布】旧格式具体线索\n【隐藏后台设定】不要公开"
        clue = judge._extract_clue_from_answer(answer, "旧格式具体线索")
        self.assertEqual(clue, "旧格式具体线索")


class JudgeResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        judge.reset_fail_counts()
        judge._config_locks.clear()
        judge._rr_index = {"judge": {}, "hint": {}}
        judge._rr_locks = {"judge": asyncio.Lock(), "hint": asyncio.Lock()}
        FakeClient.outcomes = []
        FakeClient.timeouts = []
        FakeClient.payloads = []
        FakeClient.urls = []
        self.clock = 1_000.0
        self.now_patcher = patch.object(judge, "_now", side_effect=lambda: self.clock)
        self.now_patcher.start()
        self.addCleanup(self.now_patcher.stop)

    async def _chat_with(self, outcome, **kwargs):
        return await self._chat_with_configs([dict(CONFIG)], [outcome], **kwargs)

    async def _chat_with_configs(self, configs, outcomes, **kwargs):
        FakeClient.outcomes.extend(outcomes)
        with (
            patch.object(judge, "fetch_all", AsyncMock(return_value=configs)),
            patch.object(judge.httpx, "AsyncClient", FakeClient),
        ):
            return await judge._chat(MESSAGES, **kwargs)

    async def test_three_failures_cool_then_half_open_success_recovers(self):
        for expected_failures in (1, 2, 3):
            with self.assertRaises(HTTPException):
                await self._chat_with(response(503))
            status = judge.get_config_runtime_status(CONFIG["id"])
            self.assertEqual(status["consecutive_failures"], expected_failures)

        status = judge.get_config_runtime_status(CONFIG["id"])
        self.assertEqual(status["runtime_status"], "cooling")
        self.assertEqual(status["cooldown_remaining_seconds"], 60)
        self.assertTrue(status["last_error"].startswith("http_5xx: HTTP 503"))

        self.clock += 60
        self.assertEqual(
            judge.get_config_runtime_status(CONFIG["id"])["runtime_status"],
            "half_open",
        )
        result = await self._chat_with(response(200, content="recovered"))
        self.assertEqual(result, "recovered")

        status = judge.get_config_runtime_status(CONFIG["id"])
        self.assertEqual(status["runtime_status"], "healthy")
        self.assertEqual(status["consecutive_failures"], 0)
        self.assertEqual(status["cooldown_remaining_seconds"], 0)
        self.assertIsNone(status["last_error"])
        self.assertIsNotNone(status["last_success_at"])

    async def test_half_open_failures_advance_to_120_then_300_seconds(self):
        state = judge._state(CONFIG["id"])
        state.consecutive_failures = 3
        state.cooldown_stage = 0
        state.cooldown_until = self.clock

        with self.assertRaises(HTTPException):
            await self._chat_with(httpx.ReadTimeout("slow"))
        status = judge.get_config_runtime_status(CONFIG["id"])
        self.assertEqual(status["cooldown_remaining_seconds"], 120)
        self.assertTrue(status["last_error"].startswith("timeout: ReadTimeout"))

        self.clock += 120
        with self.assertRaises(HTTPException):
            await self._chat_with(httpx.ConnectError("offline"))
        status = judge.get_config_runtime_status(CONFIG["id"])
        self.assertEqual(status["cooldown_remaining_seconds"], 300)
        self.assertTrue(status["last_error"].startswith("connect: ConnectError"))

    async def test_429_cools_immediately_and_honors_longer_retry_after(self):
        with self.assertRaises(HTTPException):
            await self._chat_with(response(429, headers={"Retry-After": "240"}))

        status = judge.get_config_runtime_status(CONFIG["id"])
        self.assertEqual(status["runtime_status"], "cooling")
        self.assertEqual(status["consecutive_failures"], 1)
        self.assertEqual(status["cooldown_remaining_seconds"], 240)
        self.assertTrue(status["last_error"].startswith("429: HTTP 429"))

        judge.reset_fail_counts(CONFIG["id"])
        with self.assertRaises(HTTPException):
            await self._chat_with(response(429, headers={"Retry-After": "30"}))
        self.assertEqual(
            judge.get_config_runtime_status(CONFIG["id"])["cooldown_remaining_seconds"],
            120,
        )

    async def test_only_one_request_can_use_a_half_open_probe(self):
        state = judge._state(CONFIG["id"])
        state.consecutive_failures = 3
        state.cooldown_stage = 0
        state.cooldown_until = self.clock
        started = asyncio.Event()
        release = asyncio.Event()

        class BlockingClient:
            def __init__(self, *, timeout):
                del timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, *, headers, json):
                del headers, json
                started.set()
                await release.wait()
                return response(200, content="probe-ok")

        with (
            patch.object(judge, "fetch_all", AsyncMock(return_value=[dict(CONFIG)])),
            patch.object(judge.httpx, "AsyncClient", BlockingClient),
        ):
            first = asyncio.create_task(judge._chat(MESSAGES))
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertTrue(judge._state(CONFIG["id"]).probe_in_flight)
            with self.assertRaises(HTTPException):
                await judge._chat(MESSAGES)
            release.set()
            self.assertEqual(await first, "probe-ok")

        self.assertEqual(len(FakeClient.payloads), 0)
        self.assertEqual(
            judge.get_config_runtime_status(CONFIG["id"])["runtime_status"],
            "healthy",
        )

    def test_error_categories_are_stable_for_admin_diagnostics(self):
        request = httpx.Request("POST", "https://example.test/v1/chat/completions")
        for status, category in ((400, "http_4xx"), (500, "http_5xx")):
            exc = httpx.HTTPStatusError(
                "failed",
                request=request,
                response=httpx.Response(status, request=request),
            )
            self.assertEqual(judge._classify_error(exc)[0], category)
        self.assertEqual(judge._classify_error(httpx.ReadTimeout("slow"))[0], "timeout")
        self.assertEqual(judge._classify_error(httpx.ConnectError("down"))[0], "connect")

    async def test_openrouter_gpt_oss_uses_low_excluded_reasoning(self):
        cfg = {
            **CONFIG,
            "api_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-oss-20b:free",
        }
        await self._chat_with_configs([cfg], [response(200)])
        self.assertEqual(
            FakeClient.payloads[0]["reasoning"],
            {"effort": "low", "exclude": True},
        )

    async def test_groq_gpt_oss_does_not_get_openrouter_reasoning(self):
        cfg = {
            **CONFIG,
            "api_url": "https://api.groq.com/openai/v1",
            "model": "openai/gpt-oss-120b",
        }
        await self._chat_with_configs([cfg], [response(200)])
        self.assertNotIn("reasoning", FakeClient.payloads[0])

    async def test_openrouter_ordinary_model_has_no_reasoning_override(self):
        cfg = {
            **CONFIG,
            "api_url": "https://openrouter.ai/api/v1",
            "model": "openai/gpt-4o-mini",
        }
        await self._chat_with_configs([cfg], [response(200)])
        self.assertNotIn("reasoning", FakeClient.payloads[0])

    async def test_official_deepseek_v4_has_30_second_node_hard_limit(self):
        cfg = {
            **CONFIG,
            "api_url": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-preview",
        }
        timeout = judge._request_timeout(cfg, 20)
        self.assertIsInstance(timeout, httpx.Timeout)
        self.assertEqual(timeout.connect, 20)
        self.assertEqual(timeout.write, 20)
        self.assertEqual(timeout.pool, 20)
        self.assertEqual(timeout.read, 30)
        self.assertEqual(judge._request_max_tokens(cfg, 1024), 8192)

        hard_limits = []

        async def passthrough(awaitable, *, timeout):
            hard_limits.append(timeout)
            return await awaitable

        with patch.object(judge.asyncio, "wait_for", side_effect=passthrough):
            result = await self._chat_with_configs([cfg], [response(200, content="ok")])
        self.assertEqual(result, "ok")
        self.assertEqual(hard_limits, [30])

        other = {**cfg, "api_url": "https://relay.example/v1"}
        self.assertEqual(judge._request_timeout(other, 20), 20)
        self.assertEqual(judge._request_max_tokens(other, 1024), 1024)

    async def test_deepseek_timeout_continues_to_next_available_node(self):
        deepseek = {
            **CONFIG,
            "id": 1,
            "name": "deepseek",
            "api_url": "https://api.deepseek.com/v1",
            "model": "deepseek-v4-preview",
            "priority": 0,
        }
        fallback = {
            **CONFIG,
            "id": 2,
            "name": "fallback",
            "api_url": "https://fallback.example/v1",
            "priority": 10,
        }
        result = await self._chat_with_configs(
            [fallback, deepseek],
            [httpx.ReadTimeout("deepseek slow"), response(200, content="fallback-ok")],
        )
        self.assertEqual(result, "fallback-ok")
        self.assertEqual(
            FakeClient.urls,
            [
                "https://api.deepseek.com/v1/chat/completions",
                "https://fallback.example/v1/chat/completions",
            ],
        )

    async def test_busy_is_returned_only_after_every_available_node_fails(self):
        configs = [
            {**CONFIG, "id": 1, "api_url": "https://first.example/v1", "priority": 0},
            {**CONFIG, "id": 2, "api_url": "https://second.example/v1", "priority": 5},
        ]
        with self.assertRaises(HTTPException) as caught:
            await self._chat_with_configs(
                configs,
                [httpx.ReadTimeout("slow"), response(503)],
            )
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(len(FakeClient.urls), 2)

    async def test_priority_layers_round_robin_without_using_healthy_fallback(self):
        primary_a = {
            **CONFIG,
            "id": 1,
            "name": "primary-a",
            "api_url": "https://primary-a.example/v1",
            "priority": 0,
        }
        primary_b = {
            **CONFIG,
            "id": 2,
            "name": "primary-b",
            "api_url": "https://primary-b.example/v1",
            "priority": 0,
        }
        fallback = {
            **CONFIG,
            "id": 3,
            "name": "fallback",
            "api_url": "https://fallback.example/v1",
            "priority": 10,
        }
        configs = [fallback, primary_b, primary_a]
        for content in ("one", "two", "three"):
            self.assertEqual(
                await self._chat_with_configs(configs, [response(200, content=content)]),
                content,
            )
        self.assertEqual(
            FakeClient.urls,
            [
                "https://primary-a.example/v1/chat/completions",
                "https://primary-b.example/v1/chat/completions",
                "https://primary-a.example/v1/chat/completions",
            ],
        )

    async def test_priority_layer_is_exhausted_before_fallback(self):
        configs = [
            {**CONFIG, "id": 1, "api_url": "https://primary-a.example/v1", "priority": 0},
            {**CONFIG, "id": 2, "api_url": "https://primary-b.example/v1", "priority": 0},
            {**CONFIG, "id": 3, "api_url": "https://fallback.example/v1", "priority": 10},
        ]
        result = await self._chat_with_configs(
            configs,
            [response(503), response(400), response(200, content="fallback")],
        )
        self.assertEqual(result, "fallback")
        self.assertEqual(
            FakeClient.urls,
            [
                "https://primary-a.example/v1/chat/completions",
                "https://primary-b.example/v1/chat/completions",
                "https://fallback.example/v1/chat/completions",
            ],
        )

    async def test_cooling_primary_layer_uses_next_priority_layer(self):
        state = judge._state(1)
        state.consecutive_failures = 3
        state.cooldown_stage = 0
        state.cooldown_until = self.clock + 60
        configs = [
            {**CONFIG, "id": 1, "api_url": "https://primary.example/v1", "priority": 0},
            {**CONFIG, "id": 2, "api_url": "https://fallback.example/v1", "priority": 10},
        ]
        result = await self._chat_with_configs(
            configs,
            [response(200, content="fallback")],
        )
        self.assertEqual(result, "fallback")
        self.assertEqual(
            FakeClient.urls,
            ["https://fallback.example/v1/chat/completions"],
        )


class AdminApiConfigRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        judge.reset_fail_counts()

    async def test_api_configs_masks_key_and_adds_runtime_fields(self):
        rows = [
            {
                **CONFIG,
                "api_key": "sk-this-must-not-leak",
                "created_at": "2026-08-11 12:00:00",
            }
        ]
        runtime = {
            "runtime_status": "cooling",
            "consecutive_failures": 3,
            "cooldown_remaining_seconds": 42,
            "last_error": "timeout: ReadTimeout",
            "last_success_at": "2026-08-11T11:59:00+08:00",
        }
        with (
            patch.object(admin_router, "fetch_all", AsyncMock(return_value=rows)),
            patch.object(admin_router, "get_config_runtime_status", return_value=runtime),
        ):
            result = await admin_router.api_configs(admin={"id": 1})

        self.assertNotIn("sk-this-must-not-leak", str(result))
        self.assertEqual(result[0]["api_key"], "sk-t...leak")
        for field, expected in runtime.items():
            self.assertEqual(result[0][field], expected)

    def test_disabled_status_does_not_change_persisted_enabled_value(self):
        runtime = judge.get_config_runtime_status(CONFIG["id"], enabled=False)
        self.assertEqual(runtime["runtime_status"], "disabled")
        self.assertEqual(CONFIG["enabled"], 1)


class NpcPoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        judge.reset_fail_counts()
        judge._config_locks.clear()
        judge._rr_index = {pool: {} for pool in judge.POOL_NAMES}
        judge._rr_locks = {
            pool: asyncio.Lock() for pool in judge.POOL_NAMES
        }
        judge._npc_semaphore = None
        judge._npc_semaphore_loop = None
        judge._npc_active = 0
        judge._npc_waiting = 0
        judge._priority_waiters = 0
        FakeClient.outcomes = []
        FakeClient.timeouts = []
        FakeClient.payloads = []
        FakeClient.urls = []

    def test_purpose_matching_preserves_both_and_adds_explicit_all(self):
        self.assertTrue(judge._purpose_matches("judge", "judge"))
        self.assertTrue(judge._purpose_matches("hint", "hint"))
        self.assertTrue(judge._purpose_matches("npc", "npc"))
        self.assertTrue(judge._purpose_matches("both", "judge"))
        self.assertTrue(judge._purpose_matches("both", "hint"))
        self.assertFalse(judge._purpose_matches("both", "npc"))
        self.assertTrue(all(
            judge._purpose_matches("all", pool) for pool in judge.POOL_NAMES
        ))
        self.assertEqual(admin_router.normalize_api_config_purpose("npc"), "npc")
        self.assertEqual(admin_router.normalize_api_config_purpose("all"), "all")

    async def test_duplicate_rows_share_health_and_node_serialization_identity(self):
        npc_cfg = {**CONFIG, "id": 1, "purpose": "npc"}
        judge_cfg = {**CONFIG, "id": 2, "purpose": "judge"}
        self.assertEqual(
            judge.register_config_runtime_node(npc_cfg),
            judge.register_config_runtime_node(judge_cfg),
        )
        judge._record_failure(1, httpx.ReadTimeout("shared"), was_probe=False)
        self.assertEqual(
            judge.get_config_runtime_status(2)["consecutive_failures"], 1
        )
        self.assertIs(judge._config_lock(npc_cfg), judge._config_lock(judge_cfg))

    async def test_paid_deepseek_npc_node_is_allowed_without_real_request(self):
        paid = {
            **CONFIG,
            "id": 11,
            "purpose": "npc",
            "api_url": "https://api.deepseek.com/v1",
            "model": "deepseek-chat",
        }
        FakeClient.outcomes = [response(200, content="chosen")]
        with (
            patch.object(judge, "fetch_all", AsyncMock(return_value=[paid])),
            patch.object(judge.httpx, "AsyncClient", FakeClient),
        ):
            result = await judge.npc_chat(
                [{"role": "user", "content": " compact "}],
                max_tokens=64,
                timeout=5,
            )
        self.assertEqual(result, "chosen")
        self.assertEqual(FakeClient.payloads[0]["messages"], [
            {"role": "user", "content": "compact"}
        ])
        self.assertEqual(FakeClient.payloads[0]["max_tokens"], 64)

    async def test_different_nodes_run_in_parallel_under_global_limit(self):
        configs = [
            {**CONFIG, "id": 21, "purpose": "npc", "api_url": "https://a.test/v1"},
            {**CONFIG, "id": 22, "purpose": "npc", "api_url": "https://b.test/v1"},
        ]
        started = asyncio.Event()
        release = asyncio.Event()
        active = 0
        maximum = 0

        class ParallelClient:
            def __init__(self, *, timeout):
                del timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, *, headers, json):
                nonlocal active, maximum
                del headers, json
                active += 1
                maximum = max(maximum, active)
                if active == 2:
                    started.set()
                await release.wait()
                active -= 1
                return response(200, content="ok")

        with (
            patch.object(judge, "fetch_all", AsyncMock(return_value=configs)),
            patch.object(judge.httpx, "AsyncClient", ParallelClient),
        ):
            tasks = [asyncio.create_task(judge.npc_chat(MESSAGES)) for _ in range(2)]
            await asyncio.wait_for(started.wait(), timeout=1)
            self.assertEqual(judge.get_pool_runtime_status()["npc"]["active"], 2)
            release.set()
            self.assertEqual(await asyncio.gather(*tasks), ["ok", "ok"])
        self.assertEqual(maximum, 2)

    async def test_configurable_global_npc_limit_rejects_queue_overflow(self):
        cfg = {**CONFIG, "id": 25, "purpose": "npc"}
        started = asyncio.Event()
        release = asyncio.Event()
        calls = 0

        class LimitedClient:
            def __init__(self, *, timeout):
                del timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, *, headers, json):
                nonlocal calls
                del headers, json
                calls += 1
                started.set()
                await release.wait()
                return response(200, content="done")

        with (
            patch.object(judge, "NPC_API_MAX_CONCURRENCY", 1),
            patch.object(judge, "NPC_API_QUEUE_TIMEOUT_SECONDS", 0.01),
            patch.object(judge, "fetch_all", AsyncMock(return_value=[cfg])),
            patch.object(judge.httpx, "AsyncClient", LimitedClient),
        ):
            judge._npc_semaphore = None
            first = asyncio.create_task(judge.npc_chat(MESSAGES))
            await asyncio.wait_for(started.wait(), timeout=1)
            with self.assertRaises(HTTPException) as overflow:
                await judge.npc_chat(MESSAGES)
            self.assertEqual(overflow.exception.status_code, 503)
            self.assertEqual(calls, 1)
            release.set()
            self.assertEqual(await first, "done")

    async def test_waiting_judge_prevents_new_npc_from_entering_shared_node(self):
        shared = {**CONFIG, "id": 31, "purpose": "all"}
        first_started = asyncio.Event()
        release_first = asyncio.Event()
        calls = 0

        class PriorityClient:
            def __init__(self, *, timeout):
                del timeout

            async def __aenter__(self):
                return self

            async def __aexit__(self, *_args):
                return None

            async def post(self, _url, *, headers, json):
                nonlocal calls
                del headers, json
                calls += 1
                if calls == 1:
                    first_started.set()
                    await release_first.wait()
                    return response(200, content="npc-first")
                return response(200, content="judge-next")

        with (
            patch.object(judge, "fetch_all", AsyncMock(return_value=[shared])),
            patch.object(judge.httpx, "AsyncClient", PriorityClient),
        ):
            first_npc = asyncio.create_task(judge.npc_chat(MESSAGES))
            await asyncio.wait_for(first_started.wait(), timeout=1)
            judge_task = asyncio.create_task(judge._chat(MESSAGES, pool="judge"))
            await asyncio.sleep(0)
            self.assertGreater(judge.get_pool_runtime_status()["priority_waiters"], 0)
            with self.assertRaises(HTTPException) as blocked:
                await judge.npc_chat(MESSAGES)
            self.assertEqual(blocked.exception.status_code, 503)
            self.assertEqual(calls, 1)
            release_first.set()
            self.assertEqual(await first_npc, "npc-first")
            self.assertEqual(await judge_task, "judge-next")
        self.assertEqual(calls, 2)

    async def test_npc_chat_rejects_noncompact_messages_before_scheduling(self):
        with self.assertRaisesRegex(ValueError, "只能包含"):
            await judge.npc_chat([
                {"role": "user", "content": "hello", "viewer": "other"}
            ])
        with self.assertRaisesRegex(ValueError, "max_tokens"):
            await judge.npc_chat(MESSAGES, max_tokens=10000)


if __name__ == "__main__":
    unittest.main(verbosity=2)
