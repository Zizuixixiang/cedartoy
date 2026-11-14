"""Transport evidence only: disposable accounts, loopback server, no live traffic."""
import asyncio
import hashlib
import json
import sqlite3
import threading
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
import server
import duel_async_gateway as gateway
from cedar_backend import mcp_diagnostics as diag
from tests_toy import test_mcp_diagnostics as fixtures


class DeliveryTests(unittest.TestCase):
    setUp = fixtures.DiagnosticTests.setUp
    records = fixtures.DiagnosticTests.records
    handler = fixtures.DiagnosticTests.handler
    secret = "synthetic-gateway-secret"

    def trace(self, transport="edge", token=None):
        trace = diag.start(self.sink, transport, "POST")
        trace.identify(self.db, self.token if token is None else token)
        return trace

    def test_signed_link_is_account_bound_local_expiring_and_not_authentication(self):
        edge = self.trace()
        headers = edge.forward_headers(self.secret)
        before = self.db.read_bytes()
        for changes, accepted in (({}, True), ({"shared_secret": "wrong"}, False),
                                   ({"peer_host": "192.0.2.1"}, False),
                                   ({"upstream_headers": {diag.TRACE_ID_HEADER: self.token}}, False)):
            main = diag.start(self.sink, "main", "POST")
            options = dict(upstream_headers=headers, shared_secret=self.secret, peer_host="127.0.0.1")
            main.identify(self.db, self.token, **(options | changes))
            self.assertEqual(main.delivery.get("gateway_link_verified", False), accepted)
            self.assertEqual(main.fields["trace_id"] == edge.fields["trace_id"], accepted)
            main.finish()
        with patch.object(diag.time, "time", return_value=time.time() + 301):
            self.assertIsNone(diag._verified_trace_id(headers, self.secret, 7, "127.0.0.1"))
        self.assertIsNone(diag._verified_trace_id(headers, self.secret, 8, "127.0.0.1"))
        unknown = diag.start(self.sink, "main", "POST")
        unknown.identify(self.db, "unknown", upstream_headers=headers,
                         shared_secret=self.secret, peer_host="127.0.0.1")
        self.assertNotIn("trace_id", unknown.fields)
        self.assertEqual(unknown.forward_headers(self.secret), {})
        self.assertEqual(before, self.db.read_bytes())
        raw = self.sink.path.read_text()
        for private in (self.secret, self.token, headers[diag.TRACE_PROOF_HEADER]):
            self.assertNotIn(private, raw)

    def test_proxy_strips_client_trace_headers_even_without_diagnostics(self):
        from starlette.requests import Request
        for trace in (None, self.trace(), self.trace(token="unknown")):
            scope = {"type": "http", "headers": [
                (diag.TRACE_ID_HEADER.lower().encode(), b"client-controlled"),
                (diag.TRACE_PROOF_HEADER.lower().encode(), b"private-proof"),
                (b"authorization", b"Bearer fixture"), (b"accept", b"application/json")],
                "app": SimpleNamespace(state=SimpleNamespace(shared_secret=self.secret))}
            if trace is not None:
                scope["mcp_diagnostic"] = trace
            forwarded = dict(gateway._forward_request_headers(Request(scope)))
            self.assertEqual(forwarded[b"authorization"], b"Bearer fixture")
            self.assertNotIn(b"client-controlled", forwarded.values())
            self.assertNotIn(b"private-proof", forwarded.values())
            self.assertEqual(diag.TRACE_ID_HEADER.lower().encode() in {k.lower() for k in forwarded},
                             trace is not None and trace.target)

    def test_main_header_and_body_write_failures_are_distinct(self):
        for phase in ("headers", "body"):
            with self.subTest(phase=phase), patch.object(diag, "MAIN_SINK", self.sink), patch.object(
                    server, "TURTLE_DB_PATH", self.db), patch.object(
                    server, "_request_rate_limit_identity", return_value="fixture"), patch.object(
                    server, "_check_request_rate_limit", return_value=True):
                handler = self.handler()
                if phase == "headers":
                    handler.end_headers.side_effect = BrokenPipeError("private-failure")
                else:
                    handler.wfile.write = Mock(side_effect=ConnectionResetError("private-failure"))
                with self.assertRaises((BrokenPipeError, ConnectionResetError)):
                    handler.do_POST()
                record = self.records()[-1]
                self.assertEqual(record["stage"], "disconnected")
                self.assertIn("main_" + phase + "_write_failed", record["boundary_ms"])
                self.assertNotIn("main_body_write_returned", record["boundary_ms"])
                self.assertNotIn("private-failure", json.dumps(self.records()))

    def test_upstream_partial_body_failure_keeps_received_byte_count(self):
        class BrokenStream(httpx.AsyncByteStream):
            async def __aiter__(self):
                yield b"private-body"
                raise httpx.ReadError("private-error")
        async def run():
            trace = self.trace()
            async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, stream=BrokenStream()))) as client:
                with self.assertRaises(httpx.ReadError):
                    await gateway._raw_http_request(client, "POST", "http://fixture/mcp",
                        body=b"private-request", headers={}, timeout=1, trace=trace)
            trace.finish("exception")
        asyncio.run(run())
        record = self.records()[-1]
        self.assertIn("upstream_headers_received", record["boundary_ms"])
        self.assertIn("upstream_read_failed", record["boundary_ms"])
        self.assertNotIn("upstream_body_read_complete", record["boundary_ms"])
        self.assertEqual(record["delivery"]["upstream_body_bytes"], len(b"private-body"))
        self.assertNotIn("private-", json.dumps(self.records()))

    def test_asgi_send_failure_cancellation_and_disconnect_have_no_false_completion(self):
        async def run(part, error):
            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                await send({"type": "http.response.body", "body": b"private-body", "more_body": False})
            async def send(message):
                if message["type"] == "http.response." + part:
                    raise error
            wrapper = diag.Middleware(app, sink=self.sink, db_path=self.db, candidate=lambda path: True)
            with self.assertRaises(type(error)):
                await wrapper({"type": "http", "method": "POST", "path": "/mcp",
                               "headers": [(b"authorization", ("Bearer " + self.token).encode())]}, None, send)
        for part in ("start", "body"):
            for error in (ConnectionResetError("private-error"), asyncio.CancelledError()):
                asyncio.run(run(part, error))
                record = self.records()[-1]
                phase = "headers" if part == "start" else "body"
                ending = "cancelled" if isinstance(error, asyncio.CancelledError) else "failed"
                self.assertIn(f"asgi_{phase}_send_{ending}", record["boundary_ms"])
                self.assertNotIn("asgi_final_body_send_returned", record["boundary_ms"])
                self.assertNotIn("asgi_body_bytes", record["delivery"])
        self.assertNotIn("private-", json.dumps(self.records()))

    def test_streaming_summary_is_bounded_and_final_body_is_required(self):
        self.sink.persistent = True
        async def run(disconnect):
            async def app(scope, receive, send):
                await send({"type": "http.response.start", "status": 200, "headers": []})
                for _ in range(500):
                    await send({"type": "http.response.body", "body": b"x", "more_body": True})
                if disconnect:
                    await receive()
                else:
                    await send({"type": "http.response.body", "body": b"", "more_body": False})
            async def receive(): return {"type": "http.disconnect"}
            async def send(message): pass
            wrapper = diag.Middleware(app, sink=self.sink, db_path=self.db, candidate=lambda path: True)
            await wrapper({"type": "http", "method": "POST", "path": "/mcp",
                           "headers": [(b"authorization", ("Bearer " + self.token).encode())]}, receive, send)
        for disconnected in (False, True):
            asyncio.run(run(disconnected))
            record = self.records()[-1]
            self.assertEqual(record["delivery"]["asgi_body_bytes"], 500)
            self.assertEqual("asgi_final_body_send_returned" in record["boundary_ms"], not disconnected)
            self.assertEqual("asgi_disconnect_observed" in record["boundary_ms"], disconnected)
            self.assertEqual("delivery_incomplete" in record["reasons"], disconnected)
            self.assertLess(len(record["boundary_ms"]), 8)
        self.assertLess(len(self.records()), 8)

    def test_unmonitored_identity_never_gains_boundary_or_link_fields(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO toy_users VALUES(8,1,2,NULL,NULL,NULL,'unchanged')")
            conn.execute("INSERT INTO ai_access_tokens VALUES(?,8,2,NULL)",
                         (hashlib.sha256(b"other-fixture").hexdigest(),))
        for token in ("other-fixture", "unknown"):
            trace = self.trace(token=token)
            trace.boundary("asgi_final_body_send_returned")
            trace.finish()
            self.assertFalse(trace.boundaries)
            self.assertEqual(trace.forward_headers(self.secret), {})
        for record in self.records():
            self.assertNotIn("trace_id", record)
            self.assertNotIn("delivery", record)
            self.assertNotIn("boundary_ms", record)


class RoundTripTests(unittest.TestCase):
    setUp = fixtures.DiagnosticTests.setUp
    records = fixtures.DiagnosticTests.records
    secret = "synthetic-gateway-secret"

    def test_standard_and_error_roundtrips_correlate_without_changing_wire_body(self):
        edge_sink = diag.Sink(self.root / "edge.jsonl", account_id=7, deadline=time.time() + 3600)
        patches = [patch.object(diag, "MAIN_SINK", self.sink), patch.object(diag, "EDGE_SINK", edge_sink),
                   patch.object(server, "TURTLE_DB_PATH", self.db),
                   patch.object(server, "DUEL_GATEWAY_SHARED_SECRET", self.secret),
                   patch.object(server, "_request_rate_limit_identity", return_value="fixture"),
                   patch.object(server, "_check_request_rate_limit", return_value=True),
                   patch.object(server, "_authenticated_ai_player_id", return_value=None),
                   patch.object(server, "_mcp_forced_announcement", return_value=""),
                   patch.object(server, "_feedback_unread_reminder", return_value=""),
                   patch.object(server.CedarToyHandler, "log_message"),
                   patch.dict("os.environ", {"TURTLE_SOUP_DB": str(self.db)})]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=2)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        def close():
            httpd.shutdown()
            httpd.server_close()
            thread.join(5)
        self.addCleanup(close)
        patcher = patch.object(gateway, "CEDARTOY_ORIGIN", f"http://127.0.0.1:{httpd.server_port}")
        patcher.start()
        self.addCleanup(patcher.stop)
        async def run():
            async with httpx.AsyncClient(trust_env=False) as backend:
                app = gateway.create_app(http_client=backend, shared_secret=self.secret, diagnostics=True)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://fixture") as client:
                    for method, params in (("tools/list", {}), ("tools/call", {"name": "get_guide", "arguments": {"game": "duel"}}),
                                           ("tools/call", {"name": "list_games", "arguments": {}}),
                                           ("get_guide", {"game": "duel"}),
                                           ("get_guide", {"arguments": {"game": "duel"}}),
                                           ("get_guide", {"game": 123}),
                                           ("list_games", {}), ("unknown", {})):
                        for path, headers in (("/mcp", {"Authorization": "Bearer " + self.token}), ("/" + self.token, {})):
                            payload = {"jsonrpc": "2.0", "id": "private-rpc-id", "method": method, "params": params}
                            standard = payload
                            if method == "get_guide":
                                standard = {**payload, "method": "tools/call", "params": {
                                    "name": "get_guide", "arguments": params.get("arguments", params)}}
                            expected = server._handle_root_mcp(standard)
                            response = await asyncio.wait_for(client.post(path, json=payload, headers=headers), 3)
                            self.assertEqual(response.status_code, 200)
                            self.assertEqual(response.content, json.dumps(expected, ensure_ascii=False).encode())
                            self.assertNotIn(diag.TRACE_ID_HEADER, response.headers)
                            self.assertNotIn(diag.TRACE_PROOF_HEADER, response.headers)
                            edge = json.loads(edge_sink.path.read_text().splitlines()[-1])
                            # The edge can receive bytes before the main worker
                            # has appended its final diagnostic record.
                            deadline = time.monotonic() + 2
                            while True:
                                completed = [r for r in self.records() if r.get("trace_id") == edge["trace_id"]
                                             and r["stage"] == "completed"]
                                if completed or time.monotonic() >= deadline:
                                    break
                                await asyncio.sleep(0.005)
                            self.assertTrue(completed, "main completion diagnostic missing")
                            main = completed[-1]
                            self.assertNotEqual(main["request_id"], edge["request_id"])
                            self.assertEqual(main["trace_id"], edge["trace_id"])
                            self.assertTrue(main["delivery"]["gateway_link_verified"])
                            self.assertTrue(edge["delivery"]["upstream_trace_match"])
                            self.assertIn("main_body_write_returned", main["boundary_ms"])
                            self.assertIn("upstream_body_read_complete", edge["boundary_ms"])
                            self.assertIn("asgi_final_body_send_returned", edge["boundary_ms"])
                            for size in (main["delivery"]["main_body_written_bytes"], edge["delivery"]["upstream_body_bytes"],
                                         edge["delivery"]["asgi_body_bytes"]):
                                self.assertEqual(size, len(response.content))
                            if method == "get_guide":
                                for record in (main, edge):
                                    self.assertEqual(record["method"], "get_guide")
                                    self.assertEqual(record["tool"], "get_guide")
                                self.assertEqual(response.json()["result"]["isError"], params.get("game") == 123)
                            elif method in {"list_games", "unknown"}:
                                self.assertEqual(response.json()["error"]["code"], -32601)
        asyncio.run(run())
        raw = self.sink.path.read_text() + edge_sink.path.read_text()
        for secret in (self.token, self.secret, "private-rpc-id", "arguments", '"params"'):
            self.assertNotIn(secret, raw)


if __name__ == "__main__":
    unittest.main()
