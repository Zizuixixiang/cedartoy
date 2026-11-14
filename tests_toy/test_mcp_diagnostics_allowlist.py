"""Multi-account diagnostics: synthetic credentials, temporary files, no live I/O."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
import os
import sqlite3
import threading
import unittest
from unittest.mock import Mock, patch

import httpx
import duel_async_gateway as gateway
import server
from cedar_backend import mcp_diagnostics as diag
from scripts import mcp_diagnostics_config as config_cli
from tests_toy import test_mcp_diagnostics as base_tests


class AllowlistTests(unittest.TestCase):
    setUp = base_tests.DiagnosticTests.setUp
    handler = base_tests.DiagnosticTests.handler

    def runtime(self, **kwargs):
        self.now = 1800000000
        clock_patch = patch.object(diag.time, "time", return_value=self.now)
        self.clock = clock_patch.start()
        self.addCleanup(clock_patch.stop)
        self.mono_patch = patch.object(diag.time, "monotonic", return_value=1200)
        self.mono = self.mono_patch.start()
        self.addCleanup(self.mono_patch.stop)
        (self.root / "data").mkdir()
        self.config = self.root / "data/mcp_diagnostics_config.json"
        self.tokens = {7: self.token, 8: "fixture-account-eight", 9: "fixture-nontarget-nine"}
        with sqlite3.connect(self.db) as conn:
            for account in (8, 9):
                conn.execute("INSERT INTO toy_users VALUES(?,1,2,NULL,NULL,NULL,'unchanged')", (account,))
                conn.execute("INSERT INTO ai_access_tokens VALUES(?,?,2,NULL)",
                             (hashlib.sha256(self.tokens[account].encode()).hexdigest(), account))
        self.sink = diag.RuntimeSink(self.root / "events.jsonl", root=self.root, **kwargs)
        self.replace({7: self.now + 3600, 8: self.now + 3600})
        return self.sink

    def replace(self, accounts):
        config_cli.atomic_write(self.config, {"mode": "allowlist", "enabled": True,
                                "accounts": [{"account_id": a, "expires_at": t} for a, t in accounts.items()]})

    def trace(self, account=7, token=None):
        trace = diag.start(self.sink, "main", "POST")
        if trace:
            trace.identify(self.db, self.tokens.get(account, "") if token is None else token)
        return trace

    def records(self, suffix="*"):
        return [json.loads(line) for path in sorted(self.root.glob("events.jsonl" + suffix))
                for line in path.read_text().splitlines()]

    def tick(self, seconds=5):
        self.clock.return_value += seconds
        self.mono.return_value += seconds

    def test_strict_allowlist_and_helper_absolute_default(self):
        valid = config_cli.update(None, "set", [7, 8], now=1800000000)
        self.assertEqual(diag.validate_configuration(valid)["accounts"], {7: 1800604800, 8: 1800604800})
        self.assertEqual(set(diag.validate_configuration(config_cli.update(
            None, "set", [10604, 13410], now=1800000000))["accounts"]), {10604, 13410})
        self.assertEqual(config_cli.update(valid, "renew", [7], now=1800000100)["accounts"][1], valid["accounts"][1])
        with self.assertRaises(ValueError):
            config_cli.update(valid, "add", [7], now=1800000100)
        for bad in (True, "7", 7.0, 0, -1, 2**63):
            with self.subTest(account=bad), self.assertRaises(ValueError):
                diag.validate_configuration({**valid, "accounts": [{"account_id": bad, "expires_at": 1800000000}]})
        for bad in (True, "1800000000", 1800000000.0, 0, -1, 253402300800, None):
            with self.subTest(expiry=bad), self.assertRaises(ValueError):
                diag.validate_configuration({**valid, "accounts": [{"account_id": 7, "expires_at": bad}]})
        for entries in ([valid["accounts"][0]] * 2, [{"account_id": 7}],
                        [{**valid["accounts"][0], "typo": 1}], {}, None,
                        [{"account_id": n, "expires_at": 1} for n in range(1, 10)]):
            with self.subTest(entries=entries), self.assertRaises(ValueError):
                diag.validate_configuration({**valid, "accounts": entries})
        for record in ({**valid, "extra": 1}, {**valid, "enabled": 1}):
            with self.assertRaises(ValueError):
                diag.validate_configuration(record)
        self.assertEqual(diag.validate_configuration({**valid, "enabled": False}), {})
        self.assertEqual(diag.validate_configuration({**valid, "accounts": []}), {"persistent": True, "accounts": {}})
        # Past grants parse as absolute times, but cannot become live on restart.
        self.runtime()
        self.replace({7: self.now - 1})
        self.assertFalse(self.sink.enabled())
        self.assertFalse(diag.RuntimeSink(self.sink.path, root=self.root).enabled())
        with patch("sys.stdout", new_callable=io.StringIO):
            config_cli.main(["--config", str(self.config), "renew", "7"])
        self.assertEqual(diag.read_config(self.config)["accounts"][0]["expires_at"], self.now + 7 * 86400)
        self.assertEqual(self.config.stat().st_mode & 0o777, 0o600)
        self.assertFalse(list(self.config.parent.glob(".mcp-diagnostics-*")))
        with patch("sys.stdout", new_callable=io.StringIO):
            config_cli.main(["--config", str(self.config), "add", "8"])
            config_cli.main(["--config", str(self.config), "remove", "7"])
        self.assertEqual([a["account_id"] for a in diag.read_config(self.config)["accounts"]], [8])

    def test_parallel_targets_privacy_and_independent_quotas(self):
        self.runtime()
        before = self.db.read_bytes()
        for account in (7, 8):
            self.trace(account).finish()
        def request(account):
            trace = self.trace(account)
            trace.payload({"method": "tools/call", "params": {"account_id": 7, "secret": self.token}})
            trace.response({"error": {"code": -32000, "message": self.token}})
            trace.finish("exception")
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(request, [7, 8, 9, None] * 50))
        # Complete quotas deterministically after best-effort contended writes.
        self.tick(60)
        for account in (7, 8):
            for _ in range(100):
                self.trace(account).finish("exception")
        for account in (7, 8):
            rows = self.records(".account-" + str(account))
            self.assertTrue(rows)
            self.assertEqual({row["account_id"] for row in rows}, {account})
            recent = [row for row in rows if row["time_epoch"] == self.clock.return_value]
            self.assertEqual(len(recent), 15)
        unknown = self.records(".allowlist.unknown")
        self.assertTrue(unknown)
        self.assertTrue(all("account_id" not in row for row in unknown))
        raw = repr(self.records())
        for value in (*self.tokens.values(), hashlib.sha256(self.token.encode()).hexdigest(), "secret", "params", str(self.db)):
            self.assertNotIn(value, raw)
        self.assertFalse(self.sink.path.exists())
        self.assertEqual(before, self.db.read_bytes())

    def test_expiry_renewal_hot_add_remove_and_inflight_revocation(self):
        self.runtime()
        self.replace({7: self.now + 2, 8: self.now + 3600})
        pending = self.trace(7)
        baseline = len(self.records())
        self.tick(2)  # Expiry is checked even before the next configuration read.
        pending.finish("exception")
        self.assertEqual(len(self.records()), baseline)
        self.assertFalse(self.trace(7).active)
        self.assertTrue(self.trace(8).active)
        self.replace({7: self.now + 500, 9: self.now + 500})
        self.tick(3)
        self.assertTrue(self.trace(7).active)
        self.assertFalse(self.trace(8).active)
        self.assertTrue(self.trace(9).active)
        pending = self.trace(7)
        self.replace({9: self.now + 500})
        self.tick()
        baseline = len(self.records())
        pending.finish("exception")
        self.assertEqual(len(self.records()), baseline)
        self.replace({7: self.now + 600})
        self.tick()
        baseline = len(self.records())
        pending.emit("parsed")  # Removed/re-added grants cannot revive old traces.
        self.assertEqual(len(self.records()), baseline)
        self.assertTrue(self.trace(7).active)

    def test_atomic_reload_read_bound_failure_backoff_and_no_legacy_revival(self):
        self.runtime()
        legacy = self.config.with_name("mcp_diagnostics_activation.json")
        legacy.write_text(json.dumps({"account_id": 7, "enabled_at": self.now, "expires_at": self.now + 86400}))
        with patch.object(diag, "read_config", wraps=diag.read_config) as read:
            pending = self.trace()
            for _ in range(1000):
                self.trace(9).finish()
            self.assertEqual(read.call_count, 1)
            self.config.write_text('{"mode":"allowlist","mode":"persistent"}')
            self.tick()
            self.assertIsNone(self.trace())
            baseline = len(self.records())
            pending.finish("exception")
            self.assertEqual(len(self.records()), baseline)
            self.assertEqual(read.call_count, 2)
            for _ in range(100):
                self.assertIsNone(self.trace())
            self.assertEqual(read.call_count, 2)
            self.replace({8: self.now + 1000})
            self.tick(10)
            self.assertTrue(self.trace(8).active)
            self.assertFalse(self.trace(7).active)
            self.config.unlink()
            self.tick()
            self.assertIsNone(self.trace())  # Does not revive active old 24h file.
        self.replace({7: self.now + 1000})
        self.tick(30)
        self.assertTrue(self.trace().active)
        with patch.object(diag, "read_config", side_effect=PermissionError("private")) as read:
            for _ in range(100):
                self.tick(1)
                self.trace()
            self.assertLessEqual(read.call_count, 5)
        self.tick(30)
        self.assertTrue(self.trace().active)
        self.config.unlink()
        os.mkfifo(self.config)
        self.tick()
        self.assertIsNone(self.trace())  # Nonregular input cannot block a worker.

    def test_refresh_contention_fails_closed_and_io_locks_do_not_queue(self):
        self.runtime()
        pending = self.trace()
        entered, release = threading.Event(), threading.Event()
        original = diag.configuration
        def blocked(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        self.tick()
        with patch.object(diag, "configuration", side_effect=blocked), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(self.sink.refresh)
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.trace())
                baseline = len(self.records())
                pending.finish("exception")
                self.assertEqual(len(self.records()), baseline)
            finally:
                release.set()
            future.result()
        with self.sink._write_lock, patch.object(diag.os, "open") as opened:
            self.trace(8).finish("exception")
            opened.assert_not_called()
        self.tick(10)
        with self.sink.lock:
            self.assertFalse(self.sink.budget("test", 1))

    def test_gateway_coalesces_refresh_workers_and_executor_failure_is_optional(self):
        self.runtime()
        self.mono_patch.stop()
        entered, release = threading.Event(), threading.Event()
        original = diag.configuration
        main_thread = threading.get_ident()
        def blocked(*args, **kwargs):
            self.assertNotEqual(threading.get_ident(), main_thread)
            entered.set()
            self.assertTrue(release.wait(5))
            return original(*args, **kwargs)
        async def run():
            with patch.object(diag, "configuration", side_effect=blocked), patch.object(
                    diag.asyncio, "to_thread", wraps=asyncio.to_thread) as handoff:
                first = asyncio.create_task(self.sink.refresh_async())
                try:
                    for _ in range(100):
                        if entered.is_set():
                            break
                        await asyncio.sleep(0.01)
                    self.assertTrue(entered.is_set())
                    await asyncio.gather(*(self.sink.refresh_async() for _ in range(100)))
                    self.assertEqual(handoff.call_count, 1)
                    self.assertIsNone(diag.start(self.sink, "edge", "POST", refresh=False))
                finally:
                    release.set()
                    await first
            self.sink._next_config = 0
            sent = []
            async def app(scope, receive, send):
                self.assertNotIn("mcp_diagnostic", scope)
                await send({"type": "http.response.body", "body": b"unchanged"})
            async def send(message):
                sent.append(message)
            wrapper = diag.Middleware(app, sink=self.sink, db_path=self.db, candidate=lambda path: True)
            with patch.object(diag.asyncio, "to_thread", side_effect=RuntimeError("private executor failure")):
                await wrapper({"type": "http", "method": "POST", "path": "/mcp"}, None, send)
            self.assertEqual(sent, [{"type": "http.response.body", "body": b"unchanged"}])
        asyncio.run(run())

    def test_global_records_rotation_disk_cap_and_legacy_preservation(self):
        self.runtime(max_bytes=2048, total_bytes=16000)
        legacy = {}
        for suffix in ("", ".1", ".2", ".unknown", ".unknown.1", ".unknown.2"):
            path = self.root / ("events.jsonl" + suffix)
            path.write_bytes(b"legacy evidence\n")
            legacy[path] = path.read_bytes()
        self.replace({n: self.now + 10000 for n in range(1, 9)})
        self.assertTrue(self.sink.enabled())
        # Exercise actual record quotas across all targets without DB fixtures.
        with patch.object(diag, "match_account", side_effect=lambda db, token: {"account_id": int(token), "identity": "current_hash_match"}):
            for n in range(1, 9):
                for _ in range(50):
                    self.trace(token=str(n)).finish("exception")
            rows = [json.loads(line) for path in self.root.glob("events.jsonl.account-*") for line in path.read_text().splitlines()]
            self.assertLessEqual(len(rows), diag.TOTAL_RECORDS)
            for n in range(100):
                self.tick(10)
                self.trace(token=str(n % 8 + 1)).finish("exception")
                self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob("events.jsonl*")), 16000)
        for path, content in legacy.items():
            self.assertEqual(path.read_bytes(), content)
        self.assertTrue(list(self.root.glob("events.jsonl.account-*.2")))
        for path in self.root.glob("events.jsonl.account-*"):
            self.assertLessEqual(path.stat().st_size, 2048)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        # Churning the allowlist cannot bypass the total, nor grow in-memory maps.
        for n in range(100, 120):
            self.replace({n: self.now + 10000})
            self.tick(5)
            with patch.object(diag, "match_account", return_value={"account_id": n, "identity": "current_hash_match"}):
                self.trace(token=str(n)).finish()
            self.assertLessEqual(len(self.sink._snapshot[1]), 2)
            self.assertLessEqual(len(self.sink._budgets), 3)
            self.assertLessEqual(sum(p.stat().st_size for p in self.root.glob("events.jsonl*")), 16000)

    def test_global_record_ceiling_and_io_failure_backoff(self):
        self.runtime(total_bytes=1000000)
        self.replace({n: self.now + 10000 for n in range(1, 9)})
        with patch.object(diag, "match_account", side_effect=lambda db, token: {"account_id": int(token), "identity": "current_hash_match"}):
            # 8 accounts * (6 sampled requests * 8 phases + 12 anomalies) > total.
            for step in range(6):
                for account in range(1, 9):
                    trace = self.trace(token=str(account))
                    for phase in diag.STAGES:
                        trace.emit(phase)
                    trace.finish()
                    for _ in range(12):
                        self.trace(token=str(account)).finish("exception")
                self.tick(10)
        self.assertEqual(len(self.records()), diag.TOTAL_RECORDS)
        self.tick(60)
        # Fail the actual appends, not config reads. No per-account retry storm.
        with patch.object(self.sink, "_scan_bytes", side_effect=OSError("private")) as scan:
            for n in range(20):
                self.trace(7).finish("exception")
                self.trace(8).finish("exception")
                self.tick(1)
            self.assertEqual(scan.call_count, 1)
        self.assertNotIn("private", repr(self.records()))

    def test_file_failure_isolated_and_existing_disk_excess_never_grows(self):
        self.runtime()
        original = diag.os.open
        def failing(path, *args, **kwargs):
            if str(path).endswith(".account-7"):
                raise PermissionError("private write error")
            return original(path, *args, **kwargs)
        with patch.object(diag.os, "open", side_effect=failing) as opened:
            self.trace(7).finish("exception")
            self.trace(8).finish()
            for _ in range(20):
                self.trace(7).finish("exception")
            self.assertEqual(sum(str(call.args[0]).endswith(".account-7") for call in opened.call_args_list), 1)
        self.assertFalse(self.records(".account-7"))
        self.assertTrue(self.records(".account-8"))
        old = self.root / "events.jsonl.2"
        old.write_bytes(b"preserved old evidence")
        self.sink.total_bytes = old.stat().st_size
        self.tick(60)
        before = {p.name: p.read_bytes() for p in self.root.glob("events.jsonl*")}
        self.trace(7).finish("exception")
        self.trace(8).finish("exception")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.glob("events.jsonl*")})

    def test_runtime_failures_do_not_change_main_response_or_touch_database(self):
        self.runtime()
        before = self.db.read_bytes()
        with patch.object(diag, "MAIN_SINK", self.sink), patch.object(server, "TURTLE_DB_PATH", self.db), patch.object(
                server, "_request_rate_limit_identity", return_value="fixture"), patch.object(
                server, "_check_request_rate_limit", return_value=True):
            for target, attribute in ((diag, "read_config"), (diag.sqlite3, "connect"), (self.sink, "_scan_bytes")):
                self.tick(60)
                with patch.object(target, attribute, side_effect=OSError("private failure")):
                    handler = self.handler()
                    handler.send_response = Mock()
                    handler.do_POST()
                    handler.send_response.assert_called_once_with(200)
                    self.assertIn("result", json.loads(handler.wfile.getvalue()))
        self.assertEqual(before, self.db.read_bytes())
        self.assertTrue(all(row["scope"] == "unattributed" and "account_id" not in row for row in self.records()))
        self.assertNotIn("private failure", repr(self.records()))

    def test_shared_lookup_ceiling_preserves_each_targets_refresh_quota(self):
        self.runtime()
        with patch.object(diag, "match_account", wraps=diag.match_account) as lookup:
            self.trace(7)
            self.trace(8)
            for n in range(100):
                self.trace(token="unrecognized-fixture-" + str(n))
            self.assertEqual(lookup.call_count, 60)
            self.tick(5)
            self.assertTrue(self.trace(7).active)
            child = self.sink.for_account(7)
            for _ in range(11):
                self.assertTrue(child.budget("identity_refresh", 12))
            self.tick(5)
            self.assertFalse(self.trace(7).active)
            self.assertTrue(self.trace(8).active)
            self.assertEqual(lookup.call_count, 62)

    def test_unknown_privacy_budget_survives_config_failure_and_mode_change(self):
        self.runtime()
        self.trace(None).finish()
        self.assertEqual(len(self.records()), 3)
        self.config.write_text("invalid")
        self.tick()
        self.assertIsNone(self.trace(None))
        self.replace({8: self.now + 3600})
        self.tick(10)
        self.assertFalse(self.trace(None).active)
        config_cli.atomic_write(self.config, {"mode": "persistent", "enabled": True, "account_id": 8})
        self.tick()
        self.assertFalse(self.trace(None).active)
        self.assertEqual(len(self.records()), 3)
        self.tick(300)
        self.trace(None).finish()
        self.assertEqual(len(self.records()), 6)

    def test_runtime_single_and_24h_compatibility_without_restart_extension(self):
        self.runtime()
        config_cli.atomic_write(self.config, {"mode": "persistent", "enabled": True, "account_id": 8})
        self.trace(8).finish()
        self.assertEqual({r["account_id"] for r in self.records("")}, {8})
        self.tick(100000)
        self.assertTrue(self.sink.enabled())
        self.config.unlink()
        legacy = self.config.with_name("mcp_diagnostics_activation.json")
        legacy.write_text(json.dumps({"account_id": 7, "enabled_at": self.clock.return_value,
                                     "expires_at": self.clock.return_value + 86400}))
        self.sink = diag.RuntimeSink(self.root / "legacy.jsonl", root=self.root)
        trace = self.trace(7)
        self.assertFalse(trace.sink.persistent)
        trace.finish()
        self.tick(86400)
        with patch.object(diag, "match_account") as lookup:
            self.assertIsNone(self.trace())
            self.assertFalse(diag.RuntimeSink(self.sink.path, root=self.root).enabled())
            lookup.assert_not_called()

    def test_real_main_and_gateway_path_bearer_and_payload_cannot_change_owner(self):
        self.runtime()
        before = self.db.read_bytes()
        raw = b'{"id":1,"method":"initialize","params":{"account_id":9,"player_id":9}}'
        with patch.object(diag, "MAIN_SINK", self.sink), patch.object(server, "TURTLE_DB_PATH", self.db), patch.object(
                server, "_request_rate_limit_identity", return_value="fixture"), patch.object(
                server, "_check_request_rate_limit", return_value=True), patch.object(
                server, "_current_account", side_effect=AssertionError("diagnostics must not authenticate")):
            for account in (7, 8):
                handler = self.handler(raw, path="/" + self.tokens[account] if account == 7 else "/mcp")
                handler.headers["Authorization"] = "Bearer " + self.tokens[8]
                handler.do_POST()
                self.assertIn("result", json.loads(handler.wfile.getvalue()))
                handler.send_response.assert_called_once_with(200)
        self.tick(10)
        # Stop mocking monotonic for asyncio; production deadlines remain fixed.
        self.mono_patch.stop()
        async def run():
            async def upstream(request):
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})
            with patch.object(diag, "EDGE_SINK", self.sink), patch.dict(os.environ, {"TURTLE_SOUP_DB": str(self.db)}):
                async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as client:
                    app = gateway.create_app(http_client=client, shared_secret="fixture", diagnostics=True)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as edge:
                        for account in (7, 8):
                            response = await edge.post("/" + self.tokens[7] if account == 7 else "/mcp", content=raw,
                                                       headers={"Authorization": "Bearer " + self.tokens[8]})
                            self.assertEqual(response.json()["result"], {"ok": True})
        asyncio.run(run())
        for account in (7, 8):
            records = self.records(".account-" + str(account))
            self.assertEqual({r["account_id"] for r in records}, {account})
            self.assertEqual({r["transport"] for r in records}, {"main", "edge"})
        self.assertEqual(before, self.db.read_bytes())
