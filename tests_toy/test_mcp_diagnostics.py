"""Synthetic credentials and temporary databases only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

import httpx
import server
import duel_async_gateway as gateway
from cedar_backend import mcp_diagnostics as diag

class DiagnosticTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="cedar-mcp-diagnostic-")
        self.addCleanup(temp.cleanup)
        target_patch = patch.object(diag, "TARGET_ACCOUNT_ID", 7)
        target_patch.start()
        self.addCleanup(target_patch.stop)
        self.root = Path(temp.name)
        self.db = self.root / "accounts.db"
        self.token = "fixture-current-not-a-real-credential"
        self.legacy = "fixture-legacy-not-a-real-credential"
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE toy_users(id INTEGER PRIMARY KEY, is_ai INTEGER, ai_token_version INTEGER,
                  deleted_at TEXT, deletion_requested_at_epoch INTEGER, scheduled_delete_at_epoch INTEGER,
                  last_active_at TEXT);
                CREATE TABLE ai_access_tokens(token_hash TEXT PRIMARY KEY, user_id INTEGER, generation INTEGER,
                  revoked_at_epoch INTEGER);
                CREATE TABLE legacy_ai_token_hashes(token_hash TEXT PRIMARY KEY, user_id INTEGER, token_version INTEGER);
                INSERT INTO toy_users VALUES(7,1,2,NULL,NULL,NULL,'unchanged');
            """)
            conn.execute("INSERT INTO ai_access_tokens VALUES(?,7,2,NULL)", (hashlib.sha256(self.token.encode()).hexdigest(),))
            conn.execute("INSERT INTO legacy_ai_token_hashes VALUES(?,7,2)", (hashlib.sha256(self.legacy.encode()).hexdigest(),))
        self.sink = diag.Sink(self.root / "events.jsonl", per_minute=10000, deadline=time.time()+86400)

    def records(self):
        paths = [self.sink.path, self.sink.path.with_name(self.sink.path.name + ".unknown")]
        return [json.loads(line) for path in paths if path.exists() for line in path.read_text().splitlines()]

    def test_hash_match_only_readonly_and_account_states(self):
        before = self.db.read_bytes()
        for token in (self.token, self.legacy):
            self.assertEqual(diag.match_account(self.db, token), {"identity":"current_hash_match", "account_id":7})
        for token in ("ey.fake-claims-user-7.signature", "unknown", ""):
            self.assertNotIn("account_id", diag.match_account(self.db, token))
        self.assertEqual(before, self.db.read_bytes())
        for sql, state in (
            ("UPDATE ai_access_tokens SET revoked_at_epoch=1", "revoked"),
            ("UPDATE ai_access_tokens SET revoked_at_epoch=NULL,generation=1", "generation_mismatch"),
            ("UPDATE toy_users SET deletion_requested_at_epoch=1", "pending_deletion"),
            ("UPDATE toy_users SET deleted_at='deleted'", "deleted")):
            with sqlite3.connect(self.db) as conn:
                conn.execute(sql)
            self.assertEqual(diag.match_account(self.db,self.token)["identity"], state)

    def test_missing_db_and_invalid_input_fail_closed(self):
        missing = self.root / "missing.db"
        self.assertEqual(diag.match_account(missing,self.token)["identity"],"lookup_unavailable")
        self.assertFalse(missing.exists())
        self.assertNotIn("account_id",diag.match_account(self.db,"x"*8193))
        self.assertNotIn("account_id",diag.match_account(self.db,"\ud800"))

    def test_record_privacy_allowlist_and_generated_request_id(self):
        trace = diag.start(self.sink,"main","POST")
        trace.identify(self.db,self.token)
        trace.payload({"method":self.token, "id":self.token, "params":{"secret":self.token}})
        trace.response({"error":{"message":self.token,"code":self.token}})
        trace.finish()
        raw = self.sink.path.read_text()
        for secret in (self.token,hashlib.sha256(self.token.encode()).hexdigest(),"secret","params"):
            self.assertNotIn(secret,raw)
        records = self.records()
        self.assertEqual(records[-1]["method"],"other")
        self.assertEqual(records[-1]["rpc_result"],"rpc_error")
        self.assertEqual(len({r["request_id"] for r in records}),1)

    def test_concurrent_records_rate_limit_rotation_and_sink_failure(self):
        def one(_):
            trace=diag.start(self.sink,"edge","POST")
            trace.identify(self.db, self.token)
            trace.finish()
            return trace.fields["request_id"]
        with ThreadPoolExecutor(max_workers=12) as pool:
            ids=list(pool.map(one,range(50)))
        self.assertEqual(len(set(ids)),50)
        self.assertEqual(len(self.records()),150)
        bounded=diag.Sink(self.root/"bounded.jsonl",max_bytes=800,per_minute=3, deadline=time.time()+86400)
        for _ in range(5):
            trace=diag.start(bounded,"edge","POST")
            trace.identify(self.db, self.token)
            trace.finish()
        self.assertFalse(bounded.admit("target"))
        self.assertLessEqual(len(list(self.root.glob("bounded.jsonl*"))),3)
        self.assertTrue(all(p.stat().st_size<=800 for p in self.root.glob("bounded.jsonl*")))
        bad=diag.Sink(self.root/"absent"/"events", deadline=time.time()+86400)
        trace=diag.start(bad,"edge","POST")
        trace.identify(self.db,self.token)
        trace.finish()

    def handler(self,raw=b'{"id":1,"method":"initialize"}',path="/mcp",method="POST"):
        handler=object.__new__(server.CedarToyHandler)
        handler.command=method
        handler.path=path
        handler.client_address=("127.0.0.1",123)
        handler.headers={"Content-Length":str(len(raw)),"Authorization":"Bearer "+self.token}
        handler.rfile,handler.wfile=io.BytesIO(raw),io.BytesIO()
        handler.send_response=Mock(side_effect=lambda code,*args: setattr(handler._mcp_diagnostic,"http_status",code))
        handler.send_header=Mock()
        handler.end_headers=Mock()
        return handler

    def test_main_initialize_no_authentication_or_active_time_write(self):
        before=self.db.read_bytes()
        with patch.object(diag,"MAIN_SINK",self.sink), patch.object(server,"TURTLE_DB_PATH",self.db), patch.object(
                server,"_request_rate_limit_identity",return_value="fixture"), patch.object(
                server,"_check_request_rate_limit",return_value=True), patch.object(
                server,"_current_account",side_effect=AssertionError("must not authenticate")):
            handler=self.handler(path="/"+self.token+"?private=never-log")
            handler.do_POST()
        self.assertIn("result",json.loads(handler.wfile.getvalue()))
        self.assertEqual(before,self.db.read_bytes())
        record=self.records()[-1]
        self.assertEqual(record["account_id"],7)
        self.assertEqual(record["method"],"initialize")
        self.assertEqual(record["http_status"],200)
        self.assertNotIn("never-log",self.sink.path.read_text())

    def test_main_parse_notification_rate_limit_and_sse_failure(self):
        with patch.object(diag,"MAIN_SINK",self.sink), patch.object(server,"TURTLE_DB_PATH",self.db), patch.object(
                server,"_request_rate_limit_identity",return_value="fixture"), patch.object(
                server,"_check_request_rate_limit",return_value=False):
            for raw,status in ((b"{bad",400),(b'{"method":"notifications/initialized"}',202),
                               (b'{"method":"tools/list","id":1}',429)):
                handler=self.handler(raw)
                handler.do_POST()
                self.assertEqual(self.records()[-1]["http_status"],status)
            handler=self.handler(method="GET")
            handler.headers["Accept"]="text/event-stream"
            handler.do_GET()
            self.assertEqual(self.records()[-1]["http_status"],405)

    def test_main_body_read_exception_records_entered_and_failure(self):
        with patch.object(diag,"MAIN_SINK",self.sink), patch.object(server,"TURTLE_DB_PATH",self.db):
            handler=self.handler()
            handler.rfile.read=Mock(side_effect=ConnectionResetError("sensitive-error"))
            with self.assertRaises(ConnectionResetError):
                handler.do_POST()
        self.assertEqual(self.records()[0]["stage"],"entered")
        self.assertEqual(self.records()[-1]["stage"],"disconnected")
        self.assertNotIn("sensitive-error",self.sink.path.read_text())

    def test_edge_before_body_no_response_changes_and_unknown_not_attributed(self):
        async def run():
            async def app(scope,receive,send):
                self.assertEqual(self.records()[0]["stage"],"entered")
                trace=scope["mcp_diagnostic"]
                trace.payload({"method":"initialize","id":self.token})
                await send({"type":"http.response.start","status":400,"headers":[]})
                await send({"type":"http.response.body","body":b"unchanged"})
            wrapper=diag.Middleware(app,sink=self.sink,db_path=self.db,candidate=lambda path:True)
            sent=[]
            async def send(message): sent.append(message)
            await wrapper({"type":"http","method":"POST","path":"/unknown",
                           "query_string":self.token.encode(),"headers":[(b"authorization",("Bearer "+self.token).encode())]},
                          None,send)
            self.assertEqual(sent[-1]["body"],b"unchanged")
        asyncio.run(run())
        self.assertEqual(self.records()[-1]["identity"],"unknown")
        self.assertNotIn("account_id",self.records()[-1])
        self.assertEqual(self.records()[-1]["http_status"],400)

    def test_edge_cancellation_and_exception_are_recorded(self):
        async def run(error):
            async def app(*args): raise error
            wrapper=diag.Middleware(app,sink=self.sink,db_path=self.db,candidate=lambda path:True)
            with self.assertRaises(type(error)):
                await wrapper({"type":"http","method":"POST","path":"/mcp","headers":[]},None,None)
        asyncio.run(run(RuntimeError("do-not-log")))
        self.assertEqual(self.records()[-1]["stage"],"exception")
        asyncio.run(run(asyncio.CancelledError()))
        self.assertEqual(self.records()[-1]["stage"],"cancelled")
        self.assertNotIn("do-not-log",str(self.records()))

    def test_real_gateway_proxy_error_and_rpc_response(self):
        async def run():
            async def upstream(request):
                return httpx.Response(200,json={"jsonrpc":"2.0","id":1,"error":{"code":-32001,"message":self.token}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
                with patch.object(diag,"EDGE_SINK",self.sink), patch.dict("os.environ",{"TURTLE_SOUP_DB":str(self.db)}):
                    app=gateway.create_app(http_client=upstream_client,shared_secret="fixture",diagnostics=True)
                    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                        response=await client.post("/mcp",json={"id":1,"method":"initialize"},
                                                   headers={"Authorization":"Bearer "+self.token})
                        self.assertEqual(response.status_code,200)
                        self.assertEqual(response.json()["error"]["message"],self.token)
        asyncio.run(run())
        self.assertEqual(self.records()[-1]["rpc_result"],"rpc_error")
        self.assertEqual(self.records()[-1]["account_id"],7)

    def test_unrelated_flood_cannot_consume_target_budget_or_rotate_target_files(self):
        other = "fixture-other-account"
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO toy_users VALUES(8,1,2,NULL,NULL,NULL,'unchanged')")
            conn.execute("INSERT INTO ai_access_tokens VALUES(?,8,2,NULL)",
                         (hashlib.sha256(other.encode()).hexdigest(),))
        target = diag.start(self.sink,"edge","POST")
        target.identify(self.db,self.token)
        target.finish()
        target_bytes = self.sink.path.read_bytes()
        for token in [other]*220 + ["unknown"]*220:
            trace=diag.start(self.sink,"edge","POST")
            trace.identify(self.db,token)
            trace.payload({"method":"initialize"})
            trace.finish()
        self.assertEqual(self.sink.path.read_bytes(),target_bytes)
        self.assertEqual(self.sink.count,{"target":1,"unattributed":10})
        target = diag.start(self.sink,"edge","POST")
        target.identify(self.db,self.token)
        target.finish()
        self.assertTrue(target.active)
        self.assertEqual(self.sink.count["target"],2)
        self.assertFalse(any(row.get("account_id")==8 for row in self.records()))
        for row in self.records():
            if row["scope"]=="unattributed":
                self.assertNotIn("account_id",row)

    def test_target_budget_is_bounded_independently_and_concurrent(self):
        self.sink.per_minute=3
        def request(token):
            trace=diag.start(self.sink,"edge","POST")
            trace.identify(self.db,token)
            trace.finish()
            return trace.active
        with ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(request,["unknown"]*30+[self.token]*10))
        self.assertEqual(self.sink.count,{"target":3,"unattributed":10})
        target_records=[r for r in self.records() if r["scope"]=="target"]
        self.assertEqual(len({r["request_id"] for r in target_records}),3)

    def test_default_disabled_and_expiry_stops_lookups_and_writes(self):
        disabled=diag.Sink(self.root/"disabled.jsonl")
        self.assertIsNone(diag.start(disabled,"edge","POST"))
        trace=diag.start(self.sink,"edge","POST")
        trace.identify(self.db,self.token)
        before=self.sink.path.read_bytes()
        waiting = diag.start(self.sink, "main", "POST")
        with patch.object(diag.time,"time",return_value=self.sink.deadline+1):
            self.assertIsNone(diag.start(self.sink,"edge","POST"))
            trace.finish()
            self.assertFalse(self.sink.admit("target"))
            with patch.object(diag, "match_account") as lookup:
                waiting.identify(self.db, self.token)
                lookup.assert_not_called()
        self.assertEqual(self.sink.path.read_bytes(),before)
        self.assertFalse(disabled.path.exists())

    def test_activation_deadline_persists_across_restart_and_invalid_is_off(self):
        directory=self.root/"data"
        directory.mkdir()
        marker=directory/"mcp_diagnostics_activation.json"
        now=int(time.time())
        with patch.object(diag,"ROOT",self.root):
            self.assertEqual(diag.activation_deadline(),0)
            marker.write_text(json.dumps({"enabled_at":now,"expires_at":now+86400,"account_id":7}))
            self.assertEqual(diag.activation_deadline(),now+86400)
            with patch.object(diag.time,"time",return_value=now+3600):
                self.assertEqual(diag.activation_deadline(),now+86400)
            with patch.object(diag.time,"time",return_value=now+86401):
                self.assertEqual(diag.activation_deadline(),0)
            marker.write_text(json.dumps({"enabled_at":now,"expires_at":now+86401,"account_id":7}))
            self.assertEqual(diag.activation_deadline(),0)

    def test_standard_method_allowlist_and_untrusted_methods_stay_other(self):
        trace=diag.start(self.sink,"main","POST")
        trace.identify(self.db,self.token)
        for method in ("resources/list","resources/templates/list","resources/read",
                       "resources/subscribe","resources/unsubscribe","prompts/list","prompts/get",
                       "completion/complete","logging/setLevel","notifications/progress"):
            trace.payload({"method":method})
            self.assertEqual(self.records()[-1]["method"],method)
        for method in (self.token, "resources/list?secret="+self.token, {}, None, ["prompts/list"]):
            trace.payload({"method":method})
            self.assertEqual(self.records()[-1]["method"],"other")
        self.assertNotIn(self.token,self.sink.path.read_text())

    def test_rpc_code_is_integer_only_bounded_and_does_not_capture_error_text(self):
        trace=diag.start(self.sink,"main","POST")
        trace.identify(self.db,self.token)
        for code in (-32601,-32602,-32700,-32001,-32800,123):
            trace.response({"error":{"code":code,"message":self.token,"data":{"private":self.token}}})
            trace.finish()
            self.assertEqual(self.records()[-1]["rpc_error_code"],code)
        for code in (True,False,-32601.0,"-32601",self.token,{},None,2**31,-(2**31)-1):
            trace.response({"error":{"code":code,"message":self.token}})
            trace.finish()
            self.assertIsNone(self.records()[-1]["rpc_error_code"])
        trace.response({"error":{"code":-32601}})
        trace.response({"result":{"isError":False}})
        trace.finish()
        self.assertIsNone(self.records()[-1]["rpc_error_code"])
        self.assertNotIn(self.token,self.sink.path.read_text())

    def persistent_sink(self, **kwargs):
        self.sink = diag.Sink(self.root / "persistent.jsonl", persistent=True, account_id=7, **kwargs)
        return self.sink

    def trace(self, token=None):
        trace = diag.start(self.sink, "main", "POST")
        trace.identify(self.db, self.token if token is None else token)
        return trace

    def test_persistent_configuration_is_explicit_strict_and_restart_scoped(self):
        directory = self.root / "data"
        directory.mkdir()
        legacy = directory / "mcp_diagnostics_activation.json"
        config = directory / "mcp_diagnostics_config.json"
        now = int(time.time())
        with patch.object(diag, "ROOT", self.root):
            self.assertEqual(diag.configuration(), {"deadline": 0})
            legacy.write_text(json.dumps({"account_id": 7, "enabled_at": now-90000, "expires_at": now-3600}))
            self.assertEqual(diag.configuration(), {"deadline": 0})
            legacy.write_text(json.dumps({"account_id": 7, "enabled_at": now, "expires_at": now+86400}))
            self.assertEqual(diag.configuration(), {"deadline": now+86400})
            valid = {"mode": "persistent", "enabled": True, "account_id": 8}
            config.write_text(json.dumps(valid))
            running = diag.Sink(self.root / "configured.jsonl", **diag.configuration())
            self.assertEqual(running.account_id, 8)
            with patch.object(diag.time, "time", return_value=now+86400*365):
                self.assertTrue(running.enabled())
            invalid = [None, [], {}, {**valid, "enabled": False}, {**valid, "enabled": 1},
                       {**valid, "mode": "always"}, {**valid, "account_id": "8"},
                       {**valid, "account_id": True}, {**valid, "account_id": 0},
                       {**valid, "account_id": -1}, {**valid, "account_id": 2**63},
                       {**valid, "extra": "typo"}]
            for record in invalid:
                with self.subTest(config=record):
                    config.write_text(json.dumps(record))
                    self.assertEqual(diag.configuration(), {})  # Never fall back to active legacy.
                    stopped = diag.Sink(self.root / "off.jsonl", **diag.configuration())
                    with patch.object(diag, "match_account") as lookup:
                        self.assertIsNone(diag.start(stopped, "main", "POST"))
                        lookup.assert_not_called()
            for raw in ('{bad', ' ' * 4097, '{"mode":"persistent","enabled":false,"enabled":true,"account_id":8}'):
                config.write_text(raw)
                self.assertEqual(diag.configuration(), {})
            self.assertTrue(running.enabled())  # Config edits are applied only on restart.
            config.unlink()
            config.symlink_to(directory / "missing.json")
            self.assertEqual(diag.configuration(), {})
            config.unlink()
            config.mkdir()
            self.assertEqual(diag.configuration(), {})

    def test_persistent_cache_limits_db_cost_and_never_logs_known_other_accounts(self):
        self.persistent_sink()
        other = "fixture-other-account"
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO toy_users VALUES(8,1,2,NULL,NULL,NULL,'unchanged')")
            conn.execute("INSERT INTO ai_access_tokens VALUES(?,8,2,NULL)",
                         (hashlib.sha256(other.encode()).hexdigest(),))
        before = self.db.read_bytes()
        with patch.object(diag.time, "monotonic", return_value=1000), patch.object(
                diag, "match_account", wraps=diag.match_account) as lookup:
            for _ in range(500):
                self.trace().finish()
                trace = self.trace(other)
                self.assertFalse(trace.active)
                trace.http_status = 500
                trace.finish("exception")
            self.assertEqual(lookup.call_count, 2)
            self.assertEqual(len(self.records()), 3)  # One sampled successful request.
            self.assertEqual({r["account_id"] for r in self.records()}, {7})
        self.assertEqual(before, self.db.read_bytes())
        self.assertNotIn(self.token, repr(self.sink._cache))
        # Changing the configured target is sufficient; no source change needed.
        self.sink = diag.Sink(self.root / "other-target.jsonl", persistent=True, account_id=8)
        self.assertFalse(self.trace().active)
        self.trace(other).finish()
        self.assertEqual({r["account_id"] for r in self.records()}, {8})

    def test_persistent_rotation_and_ownership_changes_refresh_after_five_seconds(self):
        self.persistent_sink()
        rotated = "fixture-rotated-token"
        with patch.object(diag.time, "monotonic", return_value=1000) as clock:
            self.assertEqual(self.sink.identity(self.db, self.token)["identity"], "current_hash_match")
            self.assertEqual(self.sink.identity(self.db, self.legacy)["identity"], "current_hash_match")
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE ai_access_tokens SET revoked_at_epoch=1")
                conn.execute("UPDATE toy_users SET ai_token_version=3")
                conn.execute("INSERT INTO ai_access_tokens VALUES(?,7,3,NULL)",
                             (hashlib.sha256(rotated.encode()).hexdigest(),))
            clock.return_value = 1004
            cached = self.sink.identity(self.db, self.token)
            self.assertEqual(cached["identity"], "current_hash_match")
            self.assertEqual(cached["identity_age_ms"], 4000)
            clock.return_value = 1005
            self.assertEqual(self.sink.identity(self.db, self.token)["identity"], "revoked")
            self.assertEqual(self.sink.identity(self.db, self.legacy)["identity"], "generation_mismatch")
            self.assertEqual(self.sink.identity(self.db, rotated)["identity"], "current_hash_match")
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=1")
            clock.return_value = 1010
            self.assertEqual(self.sink.identity(self.db, rotated)["identity"], "pending_deletion")
            with sqlite3.connect(self.db) as conn:
                conn.execute("INSERT INTO toy_users VALUES(8,1,3,NULL,NULL,NULL,'unchanged')")
                conn.execute("UPDATE ai_access_tokens SET user_id=8")
            clock.return_value = 1015
            self.assertFalse(self.trace(rotated).active)

    def test_persistent_lookup_flood_is_bounded_and_target_refresh_reserved(self):
        self.persistent_sink()
        with patch.object(diag.time, "monotonic", return_value=1000) as clock, patch.object(
                diag, "match_account", wraps=diag.match_account) as lookup:
            self.trace().finish()
            for n in range(1000):
                self.trace("fixture-unknown-" + str(n)).finish("exception")
            self.assertEqual(lookup.call_count, 60)
            self.assertIsNone(self.sink.identity(self.db, "fixture-new-rotated"))
            clock.return_value = 1005
            self.assertEqual(self.sink.identity(self.db, self.token)["account_id"], 7)
            self.assertEqual(lookup.call_count, 61)
            for _ in range(11):
                self.assertTrue(self.sink.budget("target_refresh", 12))
            clock.return_value = 1010
            self.assertIsNone(self.sink.identity(self.db, self.token))  # No expired fallback.
            self.assertEqual(lookup.call_count, 61)
            unknown = [r for r in self.records() if r["scope"] == "unattributed"]
            self.assertEqual(len(unknown), 3)
            self.assertFalse(any("account_id" in r for r in unknown))

    def test_persistent_cache_memory_bound_and_nonblocking_single_flight(self):
        self.persistent_sink()
        with patch.object(diag.time, "monotonic", return_value=1000) as clock, patch.object(
                diag, "match_account", return_value={"identity": "current_hash_match", "account_id": 8}):
            # Populate beyond the cap across many windows, without real DB I/O.
            for n in range(700):
                clock.return_value = 1000 + n * 6
                self.sink.identity(self.db, "fixture-" + str(n))
            self.assertEqual(len(self.sink._cache), diag.CACHE_SIZE)
        entered, release = threading.Event(), threading.Event()
        def blocked(*args):
            entered.set()
            self.assertTrue(release.wait(5))
            return {"identity": "current_hash_match", "account_id": 7}
        with patch.object(diag, "match_account", side_effect=blocked) as lookup, ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(self.sink.identity, self.db, self.token)
            try:
                self.assertTrue(entered.wait(5))
                self.assertIsNone(self.sink.identity(self.db, self.legacy))
                self.assertEqual(lookup.call_count, 1)
            finally:
                release.set()
            self.assertEqual(pending.result()["account_id"], 7)

    def test_persistent_unsampled_errors_slow_threshold_and_phase_evidence(self):
        self.persistent_sink()
        with patch.object(diag.time, "monotonic", return_value=1000):
            self.trace().finish()  # Consume entry sample, not the anomaly budget.
            baseline = len(self.records())
            for outcome, status, payload, elapsed, reasons in (
                    ("completed", 200, {"result": {}}, 4999, []),
                    ("completed", 200, {"result": {}}, 5000, ["slow"]),
                    ("completed", 429, {}, 0, ["http_error"]),
                    ("completed", 200, {"error": {"code": -32601, "message": self.token}}, 0, ["rpc_error"]),
                    ("completed", 200, {"result": {"isError": True}}, 0, ["tool_error"]),
                    ("completed", 502, {"error": "private"}, 0, ["http_error", "transport_error"]),
                    ("exception", None, {}, 0, ["exception"]),
                    ("disconnected", None, {}, 0, ["disconnected"]),
                    ("cancelled", None, {}, 0, ["cancelled"])):
                trace = self.trace()
                self.assertFalse(trace.sampled)
                trace.payload({"method": "tools/call", "params": {"private": self.token}})
                trace.emit("proxy_dispatch")
                trace.http_status = status
                trace.response(payload)
                trace.started -= elapsed / 1000
                trace.finish(outcome)
                trace.finish(outcome)  # No duplicate terminal records.
                if reasons:
                    baseline += 1
                    record = self.records()[-1]
                    self.assertEqual(record["reasons"], reasons)
                    self.assertFalse(record["entry_sampled"])
                    self.assertIn("parsed", record["phases_ms"])
                    self.assertIn("proxy_dispatch", record["phases_ms"])
                    self.assertEqual(record["stage"], outcome)
                self.assertEqual(len(self.records()), baseline)
            for secret in (self.token, hashlib.sha256(self.token.encode()).hexdigest(), "private", str(self.db)):
                self.assertNotIn(secret, self.sink.path.read_text())

    def test_persistent_sampling_anomaly_and_unknown_budgets_are_independent(self):
        self.persistent_sink()
        with patch.object(diag.time, "monotonic", return_value=1200) as clock:
            for _ in range(100):
                self.trace("").finish("exception")
            for _ in range(100):
                self.trace().finish("exception")
            target = [r for r in self.records() if r["scope"] == "target"]
            self.assertEqual(len(target), 3 + 12)  # One entry group plus independent anomalies.
            self.assertEqual(len(self.records()) - len(target), 3)
            # Anomaly saturation cannot suppress a sampled in-flight marker.
            clock.return_value = 1210
            trace = self.trace()
            self.assertTrue(trace.sampled)
            trace.payload({"method": "initialize"})
            trace.emit("proxy_dispatch")
            self.assertEqual(self.records()[len(target) + 3]["stage"], "proxy_dispatch")
            self.assertNotIn("completed", [r["stage"] for r in self.records() if r["request_id"] == trace.fields["request_id"]])
            self.assertFalse(self.trace().sampled)
            clock.return_value = 1499
            self.assertFalse(self.trace("").active)
            clock.return_value = 1500
            self.assertTrue(self.trace("").active)

    def test_persistent_rotation_permissions_record_size_and_io_backoff(self):
        self.persistent_sink(max_bytes=2048)
        with patch.object(diag.time, "monotonic", return_value=1200) as clock:
            for n in range(120):
                clock.return_value = 1200 + n * 10
                self.trace().finish("exception")
                self.trace("").finish("exception")
            paths = list(self.root.glob("persistent.jsonl*"))
            self.assertEqual(len(paths), 6)
            self.assertLessEqual(sum(p.stat().st_size for p in paths), 6 * 2048)
            for path in paths:
                self.assertLessEqual(path.stat().st_size, 2048)
                self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                for line in path.read_text().splitlines():
                    json.loads(line)
            before = self.sink.path.read_bytes()
            self.sink.write({"scope": "target", "oversize": "x" * 2048})
            self.assertEqual(self.sink.path.read_bytes(), before)
            with patch.object(diag.os, "open", side_effect=OSError("private-file-error")) as opened:
                for n in range(4):
                    clock.return_value = 3000 + n * 10
                    self.trace().finish("exception")
                self.assertEqual(opened.call_count, 2)  # At 0 and 30s, never per stage.

    def test_persistent_delayed_finishes_and_write_contention_remain_bounded(self):
        self.persistent_sink()
        with patch.object(diag.time, "monotonic", return_value=1200) as clock:
            pending = []
            for n in range(6):
                clock.return_value = 1200 + n * 10
                pending.append(self.trace())
            before = len(self.records())
            for n in range(6):
                clock.return_value = 1260 + n * 10
                trace = self.trace()
                for stage in diag.STAGES:
                    trace.emit(stage)
                trace.finish()
            for trace in pending:
                trace.finish()
            self.assertEqual(len(self.records()) - before, 48)
            for _ in range(20):
                self.trace().finish("exception")
            self.assertEqual(len(self.records()) - before, 60)
            # A stuck writer does not hold rate/cache locks or wait other calls.
            clock.return_value = 1320
            with self.sink._write_lock, patch.object(diag.os, "open") as opened:
                self.trace().finish("exception")
                opened.assert_not_called()

    def test_persistent_main_and_gateway_keep_existing_transport_and_privacy_contracts(self):
        for n, check in enumerate((self.test_main_initialize_no_authentication_or_active_time_write,
                                   self.test_main_body_read_exception_records_entered_and_failure,
                                   self.test_real_gateway_proxy_error_and_rpc_response)):
            with self.subTest(check=check.__name__):
                self.sink = diag.Sink(self.root / ("integration-%s.jsonl" % n), persistent=True, account_id=7)
                check()

    def test_persistent_main_http_errors_survive_success_sampling(self):
        self.persistent_sink()
        with patch.object(diag, "MAIN_SINK", self.sink), patch.object(server, "TURTLE_DB_PATH", self.db), patch.object(
                server, "_request_rate_limit_identity", return_value="fixture"), patch.object(
                server, "_check_request_rate_limit", return_value=False):
            for raw, status in ((b"{bad", 400), (b'{"method":"notifications/initialized"}', 202),
                                (b'{"method":"tools/list","id":1}', 429)):
                before = len(self.records())
                handler = self.handler(raw)
                handler.do_POST()
                handler.send_response.assert_called_once_with(status)
                if status == 202:
                    self.assertEqual(len(self.records()), before)
                else:
                    self.assertEqual(self.records()[-1]["http_status"], status)
                    self.assertIn("http_error", self.records()[-1]["reasons"])
            handler = self.handler(method="GET")
            handler.headers["Accept"] = "text/event-stream"
            handler.do_GET()
            self.assertEqual(self.records()[-1]["http_status"], 405)

    def test_persistent_db_and_log_io_failure_does_not_change_main_response(self):
        self.persistent_sink()
        before = self.db.read_bytes()
        with patch.object(diag, "MAIN_SINK", self.sink), patch.object(server, "TURTLE_DB_PATH", self.db), patch.object(
                diag.sqlite3, "connect", side_effect=sqlite3.OperationalError("private-db-error")), patch.object(
                diag.os, "open", side_effect=OSError("private-log-error")), patch.object(
                server, "_request_rate_limit_identity", return_value="fixture"), patch.object(
                server, "_check_request_rate_limit", return_value=True):
            handler = self.handler()
            handler.do_POST()
            self.assertIn("result", json.loads(handler.wfile.getvalue()))
            handler.send_response.assert_called_once_with(200)
        self.assertEqual(before, self.db.read_bytes())
        self.assertEqual(self.records(), [])

    def test_persistent_edge_skips_unselected_unknown_inspection_and_preserves_failures(self):
        self.persistent_sink()
        async def run():
            async def app(scope, receive, send):
                trace = scope.get("mcp_diagnostic")
                if trace is not None:
                    trace.payload({"method": "initialize"})
                await send({"type": "http.response.start", "status": 200})
                await send({"type": "http.response.body", "body": b"unchanged"})
            wrapper = diag.Middleware(app, sink=self.sink, db_path=self.db, candidate=lambda path: True)
            async def send(message):
                pass
            for _ in range(2):
                scope = {"type": "http", "method": "POST", "path": "/mcp", "headers": []}
                await wrapper(scope, None, send)
            self.assertNotIn("mcp_diagnostic", scope)
            for error, stage in ((asyncio.CancelledError(), "cancelled"),
                                 (RuntimeError("private"), "exception")):
                async def failing(scope, receive, send):
                    raise error
                wrapper.app = failing
                scope = {"type": "http", "method": "POST", "path": "/" + self.token, "headers": []}
                with self.assertRaises(type(error)):
                    await wrapper(scope, None, send)
                self.assertEqual([r for r in self.records() if r["scope"] == "target"][-1]["stage"], stage)
        asyncio.run(run())
