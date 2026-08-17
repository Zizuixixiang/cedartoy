from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException, Request

import camping_plaza_adapter as adapter
import account_deletion
import server


def _loopback_request() -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [], "client": ("127.0.0.1", 1234)})


class CampingPlazaAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="camping-plaza-")
        self.db_path = Path(self.tempdir.name) / "camping.db"
        self.old_db_path = adapter.upstream.DB_PATH
        adapter.upstream.DB_PATH = str(self.db_path)

    def tearDown(self):
        adapter.upstream.DB_PATH = self.old_db_path
        self.tempdir.cleanup()

    def _create_save(self, player_id="42:2"):
        token = adapter._PLAYER_ID.set(player_id)
        try:
            state = adapter.upstream.mcp_state()
        finally:
            adapter._PLAYER_ID.reset(token)
        adapter._remember_identity(player_id)
        return state

    def _manage(self, action, **payload):
        return adapter.manage_save(
            action,
            adapter.SaveAdminRequest(**payload),
            _loopback_request(),
        )

    def test_identity_is_stable_private_and_first_state_creates_save(self):
        first = adapter.session_id_for_player("42:2")
        self.assertEqual(first, adapter.session_id_for_player("42:2"))
        self.assertNotEqual(first, adapter.session_id_for_player("42:3"))
        self.assertRegex(first, r"^sess_[0-9a-f]{32}$")
        self.assertNotIn("42:2", first)

        state = self._create_save("42:2")
        self.assertIn("onboarding", state)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute("SELECT session_id FROM runtime_snapshot").fetchall()
            identities = conn.execute(
                "SELECT session_id, player_id FROM cedartoy_identity"
            ).fetchall()
        self.assertEqual(rows, [(first,)])
        self.assertEqual(identities, [(first, "42:2")])

    def test_export_import_migrate_delete_round_trip(self):
        self._create_save("guest:camping")
        exported = self._manage("export", player_id="guest:camping")
        self.assertTrue(exported["exported"])
        self.assertEqual(exported["save_data"]["format"], adapter.SAVE_FORMAT)
        snapshot = exported["save_data"]["snapshot"]
        snapshot["state"]["balance"] = 4321

        with self.assertRaises(HTTPException) as conflict:
            self._manage(
                "import",
                player_id="guest:camping",
                save_data=exported["save_data"],
            )
        self.assertEqual(conflict.exception.status_code, 409)

        imported = self._manage(
            "import",
            player_id="guest:camping",
            save_data=exported["save_data"],
            confirm=True,
        )
        self.assertTrue(imported["imported"])
        self.assertEqual(
            self._manage("summary", player_id="guest:camping")["summary"]["balance"],
            4321,
        )

        migrated = self._manage(
            "migrate",
            source_player_id="guest:camping",
            target_player_id="81:4",
        )
        self.assertTrue(migrated["migrated"])
        with self.assertRaises(HTTPException) as missing:
            self._manage("summary", player_id="guest:camping")
        self.assertEqual(missing.exception.status_code, 404)
        self.assertEqual(self._manage("summary", player_id="81:4")["summary"]["balance"], 4321)

        deleted = self._manage("delete", player_id="81:4")
        self.assertTrue(deleted["deleted"])
        self.assertFalse(self._manage("delete", player_id="81:4")["deleted"])

    def test_import_rejects_malformed_snapshot_before_touching_target(self):
        with self.assertRaises(HTTPException) as raised:
            self._manage(
                "import",
                player_id="42",
                save_data={"format": adapter.SAVE_FORMAT, "snapshot": {"state": {}}},
            )
        self.assertEqual(raised.exception.status_code, 400)
        self.assertFalse(self.db_path.exists())


class CampingPlazaCedarToyTests(unittest.TestCase):
    def test_proxy_policy_excludes_native_mcp_and_internal_admin(self):
        self.assertTrue(server._camping_plaza_proxy_allowed("GET", "/"))
        self.assertTrue(server._camping_plaza_proxy_allowed("GET", "/api/state"))
        self.assertTrue(server._camping_plaza_proxy_allowed("GET", "/scripts/overview.js"))
        self.assertTrue(server._camping_plaza_proxy_allowed("POST", "/api/turn/plan"))
        self.assertFalse(server._camping_plaza_proxy_allowed("GET", "/mcp/state"))
        self.assertFalse(server._camping_plaza_proxy_allowed("POST", "/internal/saves/delete"))
        self.assertFalse(server._camping_plaza_proxy_allowed("DELETE", "/api/state"))

    def test_ai_forwarder_injects_only_canonical_player_and_maps_plan(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"success": True}

        arguments = {
            "game": "camping_plaza",
            "action": "execute_turn_plan",
            "player_id": "42:3",
            "session_id": "attacker",
            "params": {
                "session_id": "also-attacker",
                "free_actions": [],
                "actions": [{"action": "improve_service", "params": {}}],
            },
        }
        with patch.object(server.httpx, "request", return_value=FakeResponse()) as request_mock:
            result = server._play_camping_plaza(arguments)

        self.assertTrue(result["success"])
        call = request_mock.call_args
        self.assertEqual(call.args[:2], ("POST", f"{server.CAMPING_PLAZA_BASE}/api/turn/plan"))
        self.assertEqual(call.kwargs["headers"], {"X-Player-Id": "42:3"})
        self.assertEqual(
            call.kwargs["json"],
            {"free_actions": [], "actions": [{"action": "improve_service", "params": {}}]},
        )

    def test_browser_proxy_drops_identity_and_rewrites_root_api_js(self):
        captured = {}

        class FakeResponse:
            status = 200
            reason = "OK"

            @staticmethod
            def read():
                return b"fetch('/api/state'); fetch(\"/api/actions\"); const icon = 'assets/camp.png'"

            @staticmethod
            def getheaders():
                return [("Content-Type", "application/javascript")]

        class FakeConnection:
            def __init__(self, *_args, **_kwargs):
                pass

            def request(self, method, target, body=None, headers=None):
                captured.update(method=method, target=target, body=body, headers=headers)

            @staticmethod
            def getresponse():
                return FakeResponse()

            @staticmethod
            def close():
                pass

        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Content-Length": "0", "X-Player-Id": "victim", "Authorization": "Bearer stolen"}
        handler.rfile = BytesIO()
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.9", 1)
        handler.command = "GET"
        handler.send_response = lambda *_args, **_kwargs: None
        handler.send_header = lambda *_args, **_kwargs: None
        handler.end_headers = lambda: None

        with patch.object(server.http.client, "HTTPConnection", FakeConnection):
            handler._proxy_to_camping_plaza(
                "GET",
                "/scripts/overview.js",
                "token=bad&player=victim",
                target={"player": "42:3"},
            )

        self.assertEqual(captured["target"], "/scripts/overview.js")
        self.assertEqual(captured["headers"]["X-Player-Id"], "42:3")
        self.assertNotIn("Authorization", captured["headers"])
        body = handler.wfile.getvalue()
        self.assertIn(b"/camping-plaza/api/state", body)
        self.assertIn(b"/camping-plaza/api/actions", body)
        self.assertIn(b"/camping-plaza/assets/camp.png", body)

    def test_homepage_and_guide_keep_author_and_repository_attribution(self):
        homepage = (Path(__file__).resolve().parents[1] / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "camping_plaza"', homepage)
        self.assertIn("/camping-plaza/?player=", homepage)
        self.assertIn("github.com/racy1501/Camping-Plaza", homepage)
        self.assertIn("PolyForm Noncommercial 1.0.0", homepage)
        guide = json.loads(server._tool_get_guide({"game": "camping_plaza"}))["guide"]
        self.assertIn("乐诶雷女士", guide)
        self.assertIn("https://github.com/racy1501/Camping-Plaza", guide)
        self.assertIn("客户端自报 session_id 会被忽略", guide)

    def test_guest_claim_uses_managed_camping_migration_and_slot(self):
        with tempfile.TemporaryDirectory(prefix="camping-claim-") as tempdir:
            missing_sessions = Path(tempdir) / "missing-sessions.db"
            save_root = Path(tempdir) / "vendor-saves"
            save_root.mkdir()
            with (
                patch.object(server, "SESSIONS_DB_PATH", missing_sessions),
                patch.object(server, "VENDOR_SAVE_ROOT", save_root),
                patch.object(
                    server,
                    "_collect_player_saves",
                    return_value=({"camping_plaza": {"summary": {"day": 2}}}, []),
                ),
                patch.object(server, "_migrate_camping_plaza_save", return_value=True) as migrate,
            ):
                result = server._migrate_player_saves("guest:camp", 81, slot=3)

        migrate.assert_called_once_with("guest:camp", "81:3")
        self.assertEqual(result["target_player_id"], "81:3")
        self.assertIn("camping_plaza", result["migrated"])

    def test_account_purge_invokes_managed_camping_delete_for_every_slot(self):
        calls = []
        with tempfile.TemporaryDirectory(prefix="camping-purge-") as tempdir:
            counts = account_deletion._delete_managed_saves(
                Path(tempdir),
                ["81", "81:2"],
                workkk_delete=None,
                garden_delete=None,
                camping_delete=lambda player_id: calls.append(player_id) or (
                    {"rows": 1} if player_id == "81:2" else None
                ),
            )
        self.assertEqual(calls, ["81", "81:2"])
        self.assertEqual(counts["camping_plaza"], 1)


if __name__ == "__main__":
    unittest.main()
