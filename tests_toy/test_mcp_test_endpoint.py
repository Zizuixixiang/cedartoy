"""Real loopback HTTP tests; never import server.py or access production data."""

import http.client
import json
import threading
import unittest
from unittest.mock import patch

from scripts import mcp_test_probe as probe


class McpTestEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.logs = patch.object(probe.ProbeHandler, "log_message")
        cls.logs.start()
        cls.httpd = probe.ThreadingHTTPServer((probe.HOST, 0), probe.ProbeHandler)
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=5)
        cls.logs.stop()

    def request(self, payload=None, path="/mcp-test", method="POST", headers=None, chunked=False):
        request_headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "MCP-Protocol-Version": "2025-11-25",
            **(headers or {}),
        }
        body = None if payload is None else json.dumps(payload).encode()
        if chunked:
            body = iter([body])
        connection = http.client.HTTPConnection(probe.HOST, self.httpd.server_port, timeout=5)
        try:
            connection.request(method, path, body, request_headers, encode_chunked=chunked)
            response = connection.getresponse()
            raw = response.read()
            return response.status, dict(response.getheaders()), json.loads(raw) if raw else None
        finally:
            connection.close()

    def test_initialize_negotiation(self):
        for version in (*probe.VERSIONS, "unknown"):
            status, headers, result = self.request({
                "jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": version, "capabilities": {},
                           "clientInfo": {"name": "local-test", "version": "1"}},
            })
            self.assertEqual(status, 200)
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(result["result"]["protocolVersion"],
                             version if version in probe.VERSIONS else probe.VERSIONS[-1])
            self.assertEqual(result["result"]["serverInfo"]["name"], "cedartoy-mcp-test-probe")
            self.assertEqual(result["result"]["capabilities"], {"tools": {}})
            self.assertNotIn("Mcp-Session-Id", headers)

    def test_discovery_is_one_anonymous_read_only_tool(self):
        for path in ("/mcp-test", "/mcp-test/", "/mcp-test?probe=1"):
            status, _, result = self.request({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, path)
            self.assertEqual(status, 200)
            tools = result["result"]["tools"]
            self.assertEqual([tool["name"] for tool in tools], ["test_ping"])
            tool = tools[0]
            self.assertEqual(tool["securitySchemes"], [{"type": "noauth"}])
            self.assertEqual(tool["_meta"]["securitySchemes"], tool["securitySchemes"])
            self.assertEqual(tool["annotations"], {
                "readOnlyHint": True, "destructiveHint": False, "openWorldHint": False,
            })
            self.assertEqual(tool["inputSchema"], {
                "type": "object", "properties": {}, "additionalProperties": False,
            })

    def test_tool_call_anonymous_and_with_ignored_bearer(self):
        for headers in ({}, {"Authorization": "Bearer intentionally-invalid-test-token"}):
            for chunked in (False, True):
                status, _, result = self.request({
                    "jsonrpc": "2.0", "id": 3, "method": "tools/call",
                    "params": {"name": "test_ping", "arguments": {}},
                }, headers=headers, chunked=chunked)
                self.assertEqual(status, 200)
                self.assertEqual(result["result"], {
                    "content": [{"type": "text", "text": "pong"}], "isError": False,
                })

    def test_rejects_formal_tools_and_nonempty_arguments(self):
        for params in (
            *({"name": name, "arguments": {}} for name in ("list_games", "get_guide", "play", "account")),
            {"name": "test_ping", "arguments": {"game": "duel"}},
            {"name": "test_ping", "arguments": []},
            None,
        ):
            _, _, result = self.request({"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": params})
            self.assertEqual(result["error"]["code"], -32602)

    def test_notification_and_optional_get_stream(self):
        status, _, result = self.request({"jsonrpc": "2.0", "method": "notifications/initialized"})
        self.assertEqual((status, result), (202, None))
        for path in ("/mcp-test", "/mcp-test/"):
            status, headers, _ = self.request(path=path, method="GET")
            self.assertEqual(status, 405)
            self.assertEqual(headers["Allow"], "POST, OPTIONS")

    def test_protocol_ping_and_unknown_method(self):
        status, _, result = self.request({"jsonrpc": "2.0", "id": "ping-1", "method": "ping"})
        self.assertEqual(status, 200)
        self.assertEqual(result, {"jsonrpc": "2.0", "id": "ping-1", "result": {}})
        _, _, result = self.request({"jsonrpc": "2.0", "id": 5, "method": "resources/list"})
        self.assertEqual(result["error"]["code"], -32601)

    def test_only_probe_paths_and_loopback(self):
        self.assertEqual(self.httpd.server_address[0], "127.0.0.1")
        for path in ("/", "/mcp", "/account", "/mcp-test/other"):
            status, _, _ = self.request({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}, path)
            self.assertEqual(status, 404)

    def test_invalid_requests_origin_and_version(self):
        for payload in ([], {"method": "ping", "id": 1}, {"jsonrpc": "2.0", "id": True, "method": "ping"}):
            status, _, result = self.request(payload)
            self.assertEqual(status, 400)
            self.assertEqual(result["error"]["code"], -32600)
        payload = {"jsonrpc": "2.0", "id": 1, "method": "ping"}
        for headers, expected in (
            ({"Origin": "https://toy.cedarstar.org"}, 200),
            ({"Origin": "https://invalid.example"}, 403),
            ({"MCP-Protocol-Version": "unknown"}, 400),
        ):
            status, _, _ = self.request(payload, headers=headers)
            self.assertEqual(status, expected)


if __name__ == "__main__":
    unittest.main()
