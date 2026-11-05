"""Synthetic credentials and temporary databases only."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
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
        with patch.object(diag.time,"time",return_value=self.sink.deadline+1):
            self.assertIsNone(diag.start(self.sink,"edge","POST"))
            trace.finish()
            self.assertFalse(self.sink.admit("target"))
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
