"""Real routing + HTTP completion path, synthetic content and no production I/O."""
import asyncio
import json
import unittest
from contextlib import ExitStack
from unittest.mock import AsyncMock, patch

import httpx

from tests_toy.test_chenmo_sse import load_judge, event, delta, MODEL


GOOGLE_REFUSAL = (
    "The prompt could not be submitted. The prompt contains sensitive words "
    "that violate Google's policy. PRIVATE_RESPONSE"
)
CHINESE_REFUSAL = "很抱歉，我无法处理您的请求，因为它违反了安全政策。"
MESSAGES = [{"role": "user", "content": "synthetic task PRIVATE_PROMPT"}]
GUESS = json.dumps({"success": False, "score": 50, "missing_core_count": 2,
                    "core_checks": [], "public_answer": None})
HINT = "【提示】请留意故事发生时周围的环境"


def config(cid, priority, purpose="both", **kwargs):
    return dict(id=cid, name=f"fixture-{cid}", api_url=f"https://node{cid}.test/v1",
                api_key="PRIVATE_KEY", model="gemini-3.1-flash-lite", enabled=1,
                priority=priority, purpose=purpose, **kwargs)


def completion(text, *, finish="stop", refusal=None):
    return {"choices": [{"message": {"content": text, "refusal": refusal},
                         "finish_reason": finish}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20}}


class ContentFallbackTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.judge = load_judge()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.configs = [config(33, 0), config(11, 1)]
        self.outcomes = []
        self.attempts = []
        self.payloads = []
        self.events = []
        j = self.judge
        self.stack.enter_context(patch.object(j, "fetch_all", AsyncMock(side_effect=lambda *_: self.configs)))
        for name in ("_get_judge_prompt", "_get_judge_prompt_guess", "_get_judge_prompt_hint"):
            self.stack.enter_context(patch.object(j, name, AsyncMock(return_value="synthetic system")))
        self.stack.enter_context(patch.object(j.api_costs, "record", side_effect=self.record))
        real_client = httpx.AsyncClient
        self.stack.enter_context(patch.object(j.httpx, "AsyncClient", side_effect=lambda **kw: real_client(
            transport=httpx.MockTransport(self.handle), **kw)))

    def record(self, *args):
        self.events.append(self.judge.api_costs.make_event(*args))

    async def handle(self, request):
        cfg = next(c for c in self.configs if request.url.host == httpx.URL(c["api_url"]).host)
        self.attempts.append(cfg["id"])
        self.payloads.append(json.loads(request.content))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        if isinstance(outcome, httpx.Response):
            return outcome
        return httpx.Response(200, json=completion(outcome) if isinstance(outcome, str) else outcome)

    async def ask(self):
        return await self.judge.judge_ask("synthetic surface", "synthetic answer", "synthetic question")

    async def guess(self):
        return await self.judge.judge_guess("synthetic surface", "synthetic answer", "完整的合成故事还原")

    async def hint(self):
        return await self.judge.generate_hint("synthetic surface", "synthetic answer", [])

    def assert_fallback(self):
        self.assertEqual(self.attempts, [33, 11])
        self.assertEqual([e["status"] for e in self.events], ["failed", "success"])
        self.assertEqual([e["http_status"] for e in self.events], [200, 200])
        self.assertEqual(self.events[0]["counts"][:2], (100, 20))
        status = self.judge.get_config_runtime_status(33)
        self.assertEqual(status["consecutive_failures"], 0)
        self.assertIsNone(status["last_success_at"])
        self.assertIsNotNone(self.judge.get_config_runtime_status(11)["last_success_at"])
        self.assertEqual(self.payloads[0]["messages"], self.payloads[1]["messages"])
        self.assertNotIn("PRIVATE", json.dumps(self.events))

    async def test_google_http200_rejection_really_calls_33_then_11(self):
        self.outcomes = [GOOGLE_REFUSAL, "是"]
        with self.assertLogs(self.judge.logger, level="WARNING") as logs:
            result = await self.ask()
        self.assertEqual(result["judgment"], "yes")
        self.assert_fallback()
        self.assertIn("config_id=33 reason=refusal_text", " ".join(logs.output))
        self.assertNotIn("PRIVATE", " ".join(logs.output))

    async def test_chinese_apology_falls_back(self):
        self.outcomes = [CHINESE_REFUSAL, "不是"]
        self.assertEqual((await self.ask())["judgment"], "no")
        self.assert_fallback()

    async def test_invalid_ask_format_falls_back_without_relaxing_validator(self):
        self.outcomes = ["yes", "是也不是"]
        self.assertEqual((await self.ask())["judgment"], "partial")
        self.assert_fallback()

    async def test_invalid_guess_json_falls_back_and_retains_audit(self):
        self.outcomes = ["{broken JSON", GUESS]
        result = await self.guess()
        self.assertFalse(result["success"])
        self.assertEqual(result["score"], 50)
        self.assertEqual(result["raw_response"], GUESS)
        self.assertEqual(result["request_messages"], self.payloads[1]["messages"])
        self.assert_fallback()

    async def test_invalid_hint_format_falls_back(self):
        self.outcomes = ["look around", HINT]
        self.assertEqual(await self.hint(), HINT[len("【提示】"):])
        self.assert_fallback()

    async def test_npc_decision_refusal_falls_back(self):
        self.configs = [config(33, 0, "npc"), config(11, 1, "npc")]
        self.outcomes = [GOOGLE_REFUSAL, '{"action":"wait"}']
        self.assertEqual(await self.judge.npc_decision_chat(MESSAGES), '{"action":"wait"}')
        self.assert_fallback()

    async def test_npc_speech_refusal_falls_back(self):
        self.configs = [config(33, 0, "npc"), config(11, 1, "npc")]
        self.outcomes = ["I'm sorry, but I can't assist with this request.", "抱歉，刚才走神了，现在轮到我出牌。"]
        self.assertEqual(await self.judge.npc_speech_chat(MESSAGES), "抱歉，刚才走神了，现在轮到我出牌。")
        self.assert_fallback()

    async def test_tarot_refusal_falls_back_with_actual_model(self):
        self.configs = [config(33, 0, "tarot"), config(11, 1, "tarot")]
        self.configs[1]["model"] = MODEL
        self.outcomes = [CHINESE_REFUSAL, "牌面提示你先整理已有资源。"]
        result = await self.judge.tarot_reading_chat(MESSAGES)
        self.assertEqual(result, {"content": "牌面提示你先整理已有资源。", "model": MODEL})
        self.assert_fallback()

    async def test_all_ask_formats_fail_returns_original_notice(self):
        self.outcomes = [GOOGLE_REFUSAL, "yes"]
        result = await self.ask()
        self.assertEqual(result["content_override"], self.judge.SYSTEM_BUSY_NOTICE)
        self.assertEqual(self.attempts, [33, 11])
        self.assertEqual([e["status"] for e in self.events], ["failed", "failed"])
        self.assertTrue(all(self.judge.get_config_runtime_status(i)["last_success_at"] is None for i in (33, 11)))

    async def test_all_guess_formats_fail_retains_last_raw_response_for_audit(self):
        self.outcomes = [GOOGLE_REFUSAL, "invalid JSON"]
        result = await self.guess()
        self.assertEqual(result["error"], self.judge.SYSTEM_BUSY_NOTICE)
        self.assertEqual(result["raw_response"], "invalid JSON")
        self.assertEqual(result["request_messages"], self.payloads[-1]["messages"])
        self.assertFalse(result["success"])
        self.assertEqual(self.attempts, [33, 11])

    async def test_all_hint_formats_fail_returns_none(self):
        self.outcomes = [GOOGLE_REFUSAL, "invalid hint"]
        self.assertIsNone(await self.hint())
        self.assertEqual(self.attempts, [33, 11])

    async def test_npc_and_tarot_all_refused_keep_503(self):
        for purpose in ("npc", "tarot"):
            with self.subTest(purpose=purpose):
                self.configs = [config(33, 0, purpose), config(11, 1, purpose)]
                self.outcomes = [GOOGLE_REFUSAL, CHINESE_REFUSAL]
                call = self.judge.npc_speech_chat if purpose == "npc" else self.judge.tarot_reading_chat
                with self.assertRaises(self.judge.HTTPException) as raised:
                    await call(MESSAGES)
                self.assertEqual(raised.exception.status_code, 503)
                self.assertEqual(self.attempts[-2:], [33, 11])

    async def test_transport_failure_after_rejection_keeps_audit_and_failure_health(self):
        self.outcomes = [GOOGLE_REFUSAL, httpx.ReadTimeout("fixture timeout")]
        result = await self.guess()
        self.assertEqual(result["error"], self.judge.SYSTEM_BUSY_NOTICE)
        self.assertEqual(result["raw_response"], GOOGLE_REFUSAL)
        self.assertEqual(self.judge.get_config_runtime_status(33)["consecutive_failures"], 0)
        self.assertEqual(self.judge.get_config_runtime_status(11)["consecutive_failures"], 1)

    async def test_transport_only_exhaustion_preserves_503(self):
        self.outcomes = [httpx.ReadTimeout("fixture"), httpx.Response(503)]
        with self.assertRaises(self.judge.HTTPException) as raised:
            await self.ask()
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(self.attempts, [33, 11])

    async def test_content_filter_and_structured_refusal_skip_without_health_penalty(self):
        for bad in (completion(None, finish="content_filter"), completion(None, refusal="policy"),
                    {"choices": [{"finish_reason": "content_filter"}]}):
            with self.subTest(bad=bad):
                self.outcomes = [bad, "是"]
                self.assertEqual((await self.ask())["judgment"], "yes")
                self.assertEqual(self.attempts[-2:], [33, 11])
                self.assertEqual(self.events[-2]["status"], "failed")
                self.assertEqual(self.judge.get_config_runtime_status(33)["consecutive_failures"], 0)

    async def test_half_open_rejection_preserves_prior_health_and_releases_probe(self):
        j = self.judge
        j.register_config_runtime_node(self.configs[0])
        state = j._state(33)
        state.consecutive_failures, state.cooldown_stage = 3, 1
        state.cooldown_until = j._now() - 1
        state.last_error, state.last_success_at = "timeout: old", "old success"
        before = vars(state).copy()
        self.outcomes = [GOOGLE_REFUSAL, "是"]
        await self.ask()
        self.assertEqual(vars(state), before)

    async def test_request_local_exclusion_does_not_disable_next_request(self):
        self.outcomes = [GOOGLE_REFUSAL, "是", "不是"]
        await self.ask()
        self.assertEqual((await self.ask())["judgment"], "no")
        self.assertEqual(self.attempts, [33, 11, 33])

    async def test_duplicate_rows_do_not_retry_same_physical_node(self):
        self.configs.insert(1, {**self.configs[0], "id": 34})
        self.outcomes = [GOOGLE_REFUSAL, "是"]
        await self.ask()
        self.assert_fallback()

    async def test_budget_bounds_calls_when_many_real_channels_fail(self):
        self.configs = [config(i, i) for i in range(10)]
        self.outcomes = [GOOGLE_REFUSAL] * 10
        result = await self.ask()
        self.assertEqual(result["content_override"], self.judge.SYSTEM_BUSY_NOTICE)
        self.assertEqual(self.attempts, list(range(8)))
        self.assertEqual(len(self.events), 8)

    async def test_same_priority_round_robin_still_works(self):
        self.configs[1]["priority"] = 0
        self.outcomes = ["是", "不是"]
        await self.ask()
        await self.ask()
        self.assertEqual(self.attempts, [11, 33])  # sorted IDs, then existing RR

    async def test_chenmo_sse_refusal_is_checked_after_merge(self):
        self.configs[0].update(api_url="https://api.chenmoai.com/v1", model=MODEL)
        self.outcomes = [httpx.Response(200, content=event(delta(GOOGLE_REFUSAL, "stop")),
                                       headers={"content-type": "text/event-stream"}), "是"]
        self.assertEqual((await self.ask())["judgment"], "yes")
        self.assertEqual(self.attempts, [33, 11])
        self.assertTrue(self.payloads[0]["stream"])
        self.assertEqual([e["status"] for e in self.events], ["failed", "success"])

    async def test_chenmo_sse_policy_stop_preserves_usage_and_node_health(self):
        self.configs[0].update(api_url="https://api.chenmoai.com/v1", model=MODEL)
        body = (event(delta(None, "content_filter"))
                + event({"usage": {"prompt_tokens": 100, "completion_tokens": 20}}))
        self.outcomes = [httpx.Response(200, content=body, headers={"content-type": "text/event-stream"}), "是"]
        self.assertEqual((await self.ask())["judgment"], "yes")
        self.assert_fallback()

    async def test_admin_probe_cannot_report_refusal_as_success(self):
        self.outcomes = [GOOGLE_REFUSAL]
        result = await self.judge.test_config(self.configs[0])
        self.assertFalse(result["success"])
        self.assertEqual(self.events[0]["status"], "failed")
        self.assertNotIn("PRIVATE", result["message"])

    def test_conservative_refusal_patterns(self):
        for text in (GOOGLE_REFUSAL, CHINESE_REFUSAL, "Sorry, I cannot provide that content.",
                     "Sorry, I can't help with that.", "抱歉，无法处理该请求。", "抱歉，我无法回答这个问题。",
                     "抱歉，我无法满足这个请求。", "根据安全政策，我无法回答该问题。"):
            with self.subTest(text=text):
                self.assertTrue(self.judge._is_refusal(text))
        for text in ("抱歉，刚才走神了，现在轮到我出牌。", "抱歉，我不能帮你开门。钥匙在楼下。",
                     "抱歉，我无法替你做决定，但牌面提示先整理资源。",
                     "Sorry, I lost the game. Let's play again.",
                     '角色说：“抱歉，我无法处理您的请求。”然后转身离开。',
                     '{"speech":"Sorry, I cannot help with this request."}',
                     "是\n【线索公布】他说抱歉然后离开", "【提示】留意他说抱歉时身边的物品", GUESS):
            with self.subTest(text=text):
                self.assertFalse(self.judge._is_refusal(text))


if __name__ == "__main__":
    unittest.main()
