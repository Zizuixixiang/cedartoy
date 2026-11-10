"""Incident telemetry boundaries; only disposable accounts and stores."""
import io
import json
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import server
from cedar_backend import garden_diagnostics as diag
from tests_toy import test_garden_picker_diagnostics as fixtures
from tests_toy.test_garden_picker_diagnostics import MemorySocket


class GardenRequestDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.GardenPickerDiagnosticsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.activation = self.fixture.root / "activation.json"
        self.until = int(time.time()) + 600
        self.activation.write_text(json.dumps({"enabled_at": int(time.time()) - 1,
                                             "expires_at": self.until, "account_id": 1}))
        for name, value in (("ACTIVATION", self.activation), ("TARGET_ACCOUNT_ID", 1), ("_counts", {})):
            p = patch.object(diag, name, value);p.start();self.addCleanup(p.stop)

    def test_expiry_target_and_budgets(self):
        self.assertEqual(diag.deadline(1), self.until)
        self.assertEqual(diag.deadline(3), 0)
        with patch.object(diag.time, "monotonic", return_value=100):
            self.assertEqual(sum(diag.admit(self.until, "server") for _ in range(100)), 60)
            self.assertTrue(diag.admit(self.until, "browser"))
        with patch.object(diag.time, "time", return_value=self.until):
            self.assertEqual(diag.deadline(1), 0)
            self.assertFalse(diag.admit(self.until, "browser"))
        self.activation.write_text('{"enabled_at":0,"expires_at":9999999999,"account_id":1}')
        self.assertEqual(diag.deadline(1), 0)

    def test_browser_allowlist_never_logs_injected_text(self):
        self.assertIsNone(diag.number(10**1000))
        report = {"request_id": "a" * 32, "page_id": "b" * 32, "route": "cmd",
                  "total_ms": 12.3, "headers_ms": float("inf"), "body_ms": True,
                  "visibility": "private-secret", "outcome": {"secret": "private-secret"},
                  "token": "private-secret", "url": "private-secret", "command": "private-secret",
                  "resources": {"js": {"cache": "private-secret", "duration_ms": 5},
                                "private-secret": {"private-secret": 1}}}
        with self.assertLogs(server.logger, level="INFO") as logs:
            diag.log_browser(server.logger, 1, report)
        text = "\n".join(logs.output)
        self.assertNotIn("private-secret", text)
        data = json.loads(logs.records[0].getMessage().split(" ", 1)[1])
        self.assertIsNone(data["headers_ms"])
        self.assertIsNone(data["body_ms"])
        self.assertEqual(data["resources"]["js"]["cache"], "unknown")
        self.assertEqual(data["total_ms"], 12.3)
        with patch.object(server.logger, "info") as log:
            diag.log_browser(server.logger, 3, report)
            log.assert_not_called()

    def test_real_auth_proxy_correlation_and_spoofed_headers(self):
        captured = []
        class Response:
            status, reason = 200, "OK"
            def read(self): return b'{"ok":true}'
            def getheaders(self): return [("Content-Type", "application/json")]
        class Connection:
            def __init__(self, *args, **kwargs): pass
            def request(self, method, path, body, headers): captured.append(headers)
            def getresponse(self): return Response()
            def close(self): pass
        for uid, player in ((1, 2), (3, 4)):
            body = b'{"command":"private-command"}'
            req = (f"POST /garden-cat/web/cmd?player={player} HTTP/1.1\r\n"
                   f"Authorization: Bearer {self.fixture.token_for(uid)}\r\n"
                   "X-Garden-Diagnostic-Until: 9999999999\r\nX-Garden-Request-Id: " + "a"*32 + "\r\n"
                   f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n").encode() + body
            sock = MemorySocket(req)
            with redirect_stdout(io.StringIO()), patch.object(server.http.client, "HTTPConnection", Connection), \
                    self.assertLogs(server.logger, level="INFO") if uid == 1 else patch.object(server.logger, "info") as logs:
                server.CedarToyHandler(sock, ("127.0.0.1", 0), object())
            if uid == 1:
                self.assertEqual(captured[-1]["X-Garden-Diagnostic-Until"], str(self.until))
                self.assertEqual(captured[-1]["X-Garden-Request-Id"], "a"*32)
                text = "\n".join(logs.output)
                self.assertNotIn("private-command", text)
                for key in ("auth_ms", "binding_ms", "body_read_ms", "upstream_headers_ms", "response_write_ms"):
                    self.assertIn(key, text)
            else:
                self.assertFalse(any(k.lower().startswith("x-garden-diagnostic") or k.lower() == "x-garden-request-id" for k in captured[-1]))


if __name__ == "__main__":
    unittest.main()
