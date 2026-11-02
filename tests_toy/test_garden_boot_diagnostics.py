"""Startup reports exercise real HTTP/auth against disposable account/save fixtures."""
from contextlib import redirect_stdout
import io
import json
import unittest
from unittest.mock import patch
import server
from tests_toy.test_garden_picker_diagnostics import MemorySocket
from tests_toy import test_garden_picker_diagnostics as picker_fixtures


class GardenBootDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = picker_fixtures.GardenPickerDiagnosticsTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.report = dict(request_id="a" * 32, event="error", phase="script_load",
                           elapsed_ms=12, error_name="SyntaxError",
                           error_code="unexpected_token", line=135, column=52,
                           asset="v5.2.0-1793610000")

    def request(self, body=None, player="2", token=None, length=None):
        raw = json.dumps(self.report if body is None else body).encode()
        token = self.fixture.token if token is None else token
        head = (f"POST /garden-cat/web/boot-report?player={player} HTTP/1.1\r\n"
                "Host: isolated.test\r\nConnection: close\r\n"
                f"Cookie: garden_cat_token={token}\r\n"
                "User-Agent: private-user-agent Chrome/79.0.3945.116\r\n"
                f"Content-Length: {len(raw) if length is None else length}\r\n\r\n")
        sock = MemorySocket(head.encode() + raw)
        with redirect_stdout(io.StringIO()), patch.object(
            server.CedarToyHandler, "_proxy_to_garden_cat"
        ) as upstream:
            server.CedarToyHandler(sock, ("127.0.0.1", 0), object())
        upstream.assert_not_called()
        return int(bytes(sock.output).split(b" ", 2)[1])

    def test_reports_verified_identity_without_touching_game(self):
        with self.assertLogs(server.logger, level="INFO") as logs:
            self.assertEqual(self.request(), 200)
        text = "\n".join(logs.output)
        for field in ("garden_boot", "user_id=1", "player=2", "phase=script_load",
                      "error_name=SyntaxError", "line=135", "browser=Chrome/79.0.3945.116"):
            self.assertIn(field, text)
        self.assertNotIn(self.fixture.token, text)
        self.assertNotIn("private-", text)
        self.assertEqual(self.fixture.saved,
                         {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.fixture.saved})

    def test_unauthenticated_ai_and_unbound_are_rejected(self):
        self.assertEqual(self.request(token="invalid"), 401)
        self.assertEqual(self.request(token=self.fixture.token_for(2)), 401)
        self.assertEqual(self.request(player="4"), 403)

    def test_untrusted_text_is_never_logged(self):
        self.report.update(error_name="private-error", error_code="private-code",
                           asset="private-asset", line="private-line",
                           column=-1, message="private-token-secret",
                           user_id=999, player="private-player")
        with self.assertLogs(server.logger, level="INFO") as logs:
            self.assertEqual(self.request(), 200)
        text = "\n".join(logs.output)
        self.assertNotIn("private-", text)
        self.assertNotIn("999", text)
        self.assertIn("user_id=1 player=2", text)

    def test_malformed_and_oversized_reports_are_rejected(self):
        for change in ({"event": []}, {"phase": {}}, {"request_id": "bad"},
                       {"event": "private-event"}, {"phase": "private-phase"}):
            with self.subTest(change=change):
                self.assertEqual(self.request(body={**self.report, **change}), 400)
        self.assertEqual(self.request(body=[]), 400)
        self.assertEqual(self.request(length=10000000), 400)

    def test_all_lifecycle_events_are_accepted(self):
        for event, phase in (("page_start", "script_load"), ("timeout", "script_start"),
                             ("error", "catalog_request"), ("error", "catalog_ready"),
                             ("error", "state_request"), ("ready", "ready")):
            with self.subTest(event=event, phase=phase), self.assertLogs(server.logger, level="INFO"):
                self.assertEqual(self.request(body={**self.report, "event": event, "phase": phase}), 200)
