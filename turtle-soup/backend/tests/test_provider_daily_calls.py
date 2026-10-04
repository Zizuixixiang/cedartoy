"""Wallet guard regressions: temporary SQLite only, mocked HTTP transport."""

import asyncio
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from functools import partial
import multiprocessing
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

import httpx

import test_judge_resilience as fixtures
import provider_daily_calls as cap

judge = fixtures.judge
OFFICIAL = {
    **fixtures.CONFIG, "id": 11, "api_url": "https://api.deepseek.com/v1",
    "api_key": "test-only-deepseek-key", "model": "deepseek-chat",
}
NPC = {**OFFICIAL, "id": 17, "purpose": "npc", "model": "deepseek-reasoner"}
FALLBACK = {**fixtures.CONFIG, "id": 99, "purpose": "all", "priority": 10}


def reserve_in_worker(_):
    try:
        cap.reserve_deepseek_call(OFFICIAL["api_url"], OFFICIAL["api_key"])
        return True
    except cap.ProviderDailyCallsUnavailable:
        return False


class ProviderDailyCallsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.db_path = Path(temp.name) / "accounts.db"
        self.patch(patch.object(cap.database, "DB_PATH", self.db_path, create=True))
        judge.reset_fail_counts()
        judge._config_locks.clear()
        judge._rr_index = {pool: {} for pool in judge.POOL_NAMES}
        judge._rr_locks = {pool: asyncio.Lock() for pool in judge.POOL_NAMES}
        self.requests = []
        self.outcome = 200
        client = partial(httpx.AsyncClient, transport=httpx.MockTransport(self.transport))
        self.patch(patch.object(judge.httpx, "AsyncClient", client))

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    async def transport(self, request):
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return httpx.Response(self.outcome, json={
            "data": [{"id": "deepseek-chat"}],
            "choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}],
        })

    def reserve(self):
        cap.reserve_deepseek_call(OFFICIAL["api_url"], OFFICIAL["api_key"])

    def seed(self, calls):
        self.reserve()
        with sqlite3.connect(self.db_path) as db:
            db.execute("UPDATE provider_daily_calls SET calls = ?", (calls,))

    def count(self):
        with sqlite3.connect(self.db_path) as db:
            return db.execute("SELECT COALESCE(SUM(calls), 0) FROM provider_daily_calls").fetchone()[0]

    async def test_250_allowed_251_skipped_and_fallback_used(self):
        self.seed(249)
        self.patch(patch.object(judge, "fetch_all", AsyncMock(return_value=[OFFICIAL, FALLBACK])))
        self.assertEqual(await judge._chat(fixtures.MESSAGES), "ok")
        self.assertEqual(self.count(), 250)
        self.assertEqual(await judge._chat(fixtures.MESSAGES), "ok")
        self.assertEqual([r.url.host for r in self.requests], ["api.deepseek.com", "example.test"])
        self.assertEqual(self.count(), 250)
        self.assertEqual(judge.get_config_runtime_status(11)["consecutive_failures"], 0)

    async def test_judge_hint_npc_and_admin_share_quota_across_models_and_ids(self):
        self.seed(246)
        configs = [OFFICIAL, NPC, {**FALLBACK, "purpose": "both"},
                   {**FALLBACK, "id": 100, "purpose": "npc"}]
        self.patch(patch.object(judge, "fetch_all", AsyncMock(return_value=configs)))
        for pool in ("judge", "hint", "npc_decision", "npc_speech"):
            self.assertEqual(await judge._chat(fixtures.MESSAGES, pool=pool), "ok")
        result = await judge.test_config(NPC)
        self.assertFalse(result["success"])
        self.assertIn("250", result["message"])
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.count(), 250)
        await judge._chat(fixtures.MESSAGES, pool="npc_decision")
        self.assertEqual(self.requests[-1].url.host, "example.test")

    async def test_admin_consumes_same_last_slot_and_models_do_not_count(self):
        self.seed(249)
        self.assertTrue((await judge.test_config(NPC))["success"])
        self.assertFalse((await judge.test_config(OFFICIAL))["success"])
        self.assertTrue((await judge.list_models(OFFICIAL))["success"])
        self.assertEqual([r.method for r in self.requests], ["POST", "GET"])
        self.assertEqual(self.count(), 250)

    async def test_all_nodes_capped_returns_existing_unavailable_error(self):
        self.seed(250)
        self.patch(patch.object(judge, "fetch_all", AsyncMock(return_value=[OFFICIAL])))
        with self.assertRaises(fixtures.HTTPException) as caught:
            await judge._chat(fixtures.MESSAGES)
        self.assertEqual(caught.exception.status_code, 503)
        self.assertEqual(self.requests, [])

    def test_concurrent_processes_cannot_exceed_cap(self):
        self.seed(240)
        with ProcessPoolExecutor(max_workers=8, mp_context=multiprocessing.get_context("fork")) as pool:
            results = list(pool.map(reserve_in_worker, range(40)))
        self.assertEqual(sum(results), 10)
        self.assertEqual(self.count(), 250)

    async def test_beijing_midnight_resets_without_health_cooldown(self):
        self.patch(patch.object(judge, "fetch_all", AsyncMock(return_value=[OFFICIAL])))
        with patch.object(cap, "datetime") as clock:
            clock.now.side_effect = lambda tz: datetime(2026, 10, 5, 15, 59, 59, tzinfo=timezone.utc).astimezone(tz)
            self.seed(250)
            self.assertFalse((await judge.test_config(OFFICIAL))["success"])
            clock.now.side_effect = lambda tz: datetime(2026, 10, 5, 16, 0, 0, tzinfo=timezone.utc).astimezone(tz)
            self.assertEqual(await judge._chat(fixtures.MESSAGES), "ok")
        with sqlite3.connect(self.db_path) as db:
            rows = db.execute("SELECT beijing_day, calls FROM provider_daily_calls ORDER BY beijing_day").fetchall()
        self.assertEqual(rows, [("2026-10-05", 250), ("2026-10-06", 1)])

    async def test_sent_failures_and_timeouts_still_count(self):
        for outcome in (429, 500, httpx.ReadTimeout("slow"), httpx.ConnectTimeout("slow connect")):
            with self.subTest(outcome=outcome):
                self.outcome = outcome
                self.assertFalse((await judge.test_config(OFFICIAL))["success"])
        self.assertEqual(len(self.requests), 4)
        self.assertEqual(self.count(), 4)

    async def test_local_preparation_failures_and_missing_fields_do_not_count(self):
        self.seed(249)
        with self.assertRaises(TypeError):
            await judge._post_chat_completion(OFFICIAL, {"messages": object()}, 5)
        self.assertFalse((await judge.test_config({**OFFICIAL, "model": ""}))["success"])
        with patch.object(judge.httpx, "AsyncClient", side_effect=RuntimeError("client init")):
            self.assertFalse((await judge.test_config(OFFICIAL))["success"])
        self.assertEqual(self.count(), 249)
        self.assertEqual(self.requests, [])

    async def test_cancelled_in_flight_request_still_counts(self):
        started = asyncio.Event()

        async def slow_transport(request):
            self.requests.append(request)
            started.set()
            await asyncio.Event().wait()

        client = partial(httpx.AsyncClient, transport=httpx.MockTransport(slow_transport))
        with patch.object(judge.httpx, "AsyncClient", client):
            task = asyncio.create_task(judge._post_chat_completion(OFFICIAL, {}, 5))
            await asyncio.wait_for(started.wait(), timeout=2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.count(), 1)

    async def test_non_deepseek_unaffected_and_storage_failure_fails_closed(self):
        self.patch(patch.object(cap.database, "DB_PATH", self.db_path / "missing"))
        self.patch(patch.object(judge, "fetch_all", AsyncMock(return_value=[OFFICIAL, FALLBACK])))
        self.assertEqual(await judge._chat(fixtures.MESSAGES), "ok")
        for host in ("sukaka.example", "catiecli.example", "api.deepseek.com.example"):
            result = await judge.test_config({**OFFICIAL, "api_url": f"https://{host}/v1"})
            self.assertTrue(result["success"])
        self.assertFalse((await judge.test_config(OFFICIAL))["success"])
        self.assertNotIn("api.deepseek.com", [r.url.host for r in self.requests])

    def test_endpoint_aliases_share_but_separate_credentials_do_not(self):
        self.seed(250)
        for endpoint in ("https://api.deepseek.com", "https://API.DEEPSEEK.COM/v1/", "https://api.deepseek.com/v1/chat/completions"):
            with self.assertRaises(cap.ProviderDailyCallsUnavailable):
                cap.reserve_deepseek_call(endpoint, OFFICIAL["api_key"])
        cap.reserve_deepseek_call(OFFICIAL["api_url"], "another-test-key")
        self.assertEqual(self.count(), 251)
        with sqlite3.connect(self.db_path) as db:
            dump = "\n".join(db.iterdump())
        self.assertNotIn(OFFICIAL["api_key"], dump)
        self.assertNotIn("another-test-key", dump)

    def test_fresh_process_retains_count_and_schema_is_idempotent(self):
        self.seed(249)
        code = """
import sys, types
from pathlib import Path
sys.modules['database'] = types.SimpleNamespace(DB_PATH=Path(sys.argv[1]))
from provider_daily_calls import reserve_deepseek_call, ProviderDailyCallsUnavailable
try:
    reserve_deepseek_call('https://api.deepseek.com/v1', 'test-only-deepseek-key')
except ProviderDailyCallsUnavailable:
    sys.exit(3)
"""
        for expected in (0, 3):
            result = subprocess.run([sys.executable, "-c", code, str(self.db_path)],
                                    cwd=fixtures.BACKEND_DIR, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr)
        self.assertEqual(self.count(), 250)
        with sqlite3.connect(self.db_path) as db:
            self.assertEqual(db.execute("PRAGMA integrity_check").fetchone()[0], "ok")


if __name__ == "__main__":
    unittest.main()
