"""CedarToy-side Garden-Cat proxy policy tests; no service is started."""

from __future__ import annotations

import json
import sys
import sqlite3
import tempfile
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch


TOY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TOY_ROOT))

import server  # noqa: E402


class GardenCatProxyPolicyTests(unittest.TestCase):
    def test_proxy_method_and_path_whitelists_are_exact(self):
        self.assertTrue(server._garden_cat_proxy_allowed("GET", "/"))
        self.assertTrue(server._garden_cat_proxy_allowed("GET", "/web/status"))
        self.assertTrue(server._garden_cat_proxy_allowed("GET", "/web/catalog"))
        self.assertTrue(server._garden_cat_proxy_allowed("GET", "/web/notes"))
        self.assertTrue(server._garden_cat_proxy_allowed("GET", "/static/human.js"))
        self.assertTrue(server._garden_cat_proxy_allowed("POST", "/web/water"))
        self.assertTrue(server._garden_cat_proxy_allowed("POST", "/web/pet_cat"))
        self.assertTrue(server._garden_cat_proxy_allowed("POST", "/web/notes"))
        for path in ("/api/status", "/api/cmd", "/web/cmd", "/web/new_game", "/web/delete"):
            self.assertFalse(server._garden_cat_proxy_allowed("GET", path), path)
            self.assertFalse(server._garden_cat_proxy_allowed("POST", path), path)
        self.assertFalse(server._garden_cat_proxy_allowed("PUT", "/web/water"))

    def test_proxy_path_mapping_is_explicit(self):
        self.assertEqual(server._garden_cat_upstream_path("/"), "/web/")
        self.assertEqual(server._garden_cat_upstream_path("/web/status"), "/web/status")
        self.assertEqual(server._garden_cat_upstream_path("/static/human.js"), "/static/human.js")

    def test_notes_forwarder_injects_trusted_machine_name(self):
        class FakeResponse:
            status_code = 200

            @staticmethod
            def json():
                return {"ok": True}

        with patch.object(server.httpx, "request", return_value=FakeResponse()) as request_mock:
            server._play_garden_cat(
                {
                    "game": "garden_cat",
                    "action": "notes",
                    "player_id": "42:3",
                    "params": {"content": "今天也浇水啦"},
                },
                owner_name="阿橘",
            )

        self.assertEqual(request_mock.call_args.args[0], "POST")
        self.assertEqual(request_mock.call_args.args[1], f"{server.GARDEN_CAT_BASE}/api/notes")
        self.assertEqual(request_mock.call_args.kwargs["json"], {"content": "今天也浇水啦"})
        self.assertEqual(request_mock.call_args.kwargs["headers"]["X-Garden-Owner-Name"], "阿橘")

        with patch.object(server.httpx, "request", return_value=FakeResponse()) as request_mock:
            server._play_garden_cat(
                {
                    "game": "garden_cat",
                    "action": "notes",
                    "player_id": "42:3",
                    "params": {"page": 2},
                },
                owner_name="阿橘",
            )

        self.assertEqual(request_mock.call_args.args[0], "GET")
        self.assertEqual(request_mock.call_args.args[1], f"{server.GARDEN_CAT_BASE}/api/notes?page=2")
        self.assertIsNone(request_mock.call_args.kwargs["json"])

    def test_non_static_requests_reject_logged_out_and_unbound_humans(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/garden-cat/web/status?player=42:2"
        handler.headers = {}
        sent = []
        handler._send_json = lambda payload, status=200, **_kwargs: sent.append((status, payload))

        with patch.object(server, "_current_account", side_effect=server._McpError(-32001, "bad token")):
            handler._handle_garden_cat_proxy("GET")
        self.assertEqual(sent[-1][0], 401)

        handler._garden_cat_bound_target = lambda _user, _player: None
        with patch.object(server, "_current_account", return_value={"id": 1, "is_ai": False}):
            handler._handle_garden_cat_proxy("GET")
        self.assertEqual(sent[-1][0], 403)

    def test_proxy_handler_uses_logged_in_human_name(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/garden-cat/web/notes?player=42:2"
        handler.headers = {"Authorization": "Bearer trusted"}
        target = {"player": "42:2", "owner_name": "阿橘", "slot": 2}
        handler._garden_cat_bound_target = lambda _user, _player: target
        captured = {}
        handler._proxy_to_garden_cat = lambda *args, **kwargs: captured.update(args=args, kwargs=kwargs)

        with patch.object(
            server,
            "_current_account",
            return_value={"id": 1, "username": "小满", "is_ai": False},
        ):
            handler._handle_garden_cat_proxy("GET")

        self.assertEqual(captured["args"][:2], ("GET", "/web/notes"))
        self.assertEqual(captured["kwargs"]["target"], target)
        self.assertEqual(captured["kwargs"]["human_name"], "小满")

    def test_homepage_entry_loads_existing_gardens_and_has_empty_copy(self):
        homepage = (TOY_ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "garden_cat"', homepage)
        self.assertIn('fetch("/api/garden-cat/gardens"', homepage)
        self.assertIn("if (!gardens.length)", homepage)
        self.assertIn("你家小机还没有开花园", homepage)
        self.assertIn('play(game=&quot;garden_cat&quot;, action=&quot;status&quot;)', homepage)
        self.assertNotIn("[1, 2, 3, 4, 5].map((slot)", homepage)
        self.assertIn("/garden-cat/?player=", homepage)

    def test_garden_list_only_returns_existing_saves_for_bound_machines(self):
        with tempfile.TemporaryDirectory(prefix="garden-picker-") as tempdir:
            root = Path(tempdir)
            db_path = root / "toy.db"
            save_root = root / "saves"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    is_ai INTEGER NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES (1, '人类', 0, NULL);
                INSERT INTO toy_users VALUES (42, '阿橘', 1, NULL);
                INSERT INTO toy_users VALUES (43, '陌生小机', 1, NULL);
                INSERT INTO user_bindings VALUES (1, 42);
                """
            )
            conn.commit()
            conn.close()

            states = {
                "42": {"garden_name": "", "money": 47, "encyclopedia": ["daisy"], "cat": None},
                "42:3": {"garden_name": "月光花园", "money": 88, "encyclopedia": ["daisy", "tulip"], "cat": {"name": "栗子"}},
                "43": {"garden_name": "不该看见", "money": 999, "encyclopedia": [], "cat": None},
            }
            for player_id, state in states.items():
                player_dir = save_root / player_id
                player_dir.mkdir(parents=True)
                (player_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

            def connect():
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            with patch.object(server, "_db_connect", side_effect=connect):
                gardens = server._garden_cat_watchable_gardens_for_user(
                    {"id": 1, "is_ai": False}, save_root=save_root
                )

        self.assertEqual([item["slot"] for item in gardens], [1, 3])
        self.assertEqual([item["ai_user_id"] for item in gardens], [42, 42])
        self.assertEqual(gardens[0]["garden_name"], "未命名花园")
        self.assertEqual(gardens[0]["money"], 47)
        self.assertEqual(gardens[0]["encyclopedia_count"], 1)
        self.assertFalse(gardens[0]["has_cat"])
        self.assertEqual(gardens[1]["garden_name"], "月光花园")
        self.assertEqual(gardens[1]["encyclopedia_count"], 2)
        self.assertTrue(gardens[1]["has_cat"])
        self.assertNotIn(43, {item["ai_user_id"] for item in gardens})

    def test_garden_list_endpoint_requires_login_and_can_return_empty(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {}
        sent = []
        handler._send_json = lambda payload, status=200, **_kwargs: sent.append((status, payload))

        with patch.object(server, "_garden_cat_watchable_gardens", side_effect=server._McpError(-32001, "未登录")):
            handler._handle_api_garden_cat_gardens()
        self.assertEqual(sent[-1], (401, {"error": "未登录"}))

        with patch.object(server, "_garden_cat_watchable_gardens", return_value={"gardens": []}):
            handler._handle_api_garden_cat_gardens()
        self.assertEqual(sent[-1], (200, {"gardens": []}))

    def test_proxy_drops_browser_identity_before_injecting_canonical_player(self):
        captured = {}

        class FakeResponse:
            status = 200
            reason = "OK"

            @staticmethod
            def read():
                return b"{}"

            @staticmethod
            def getheaders():
                return [("Content-Type", "application/json")]

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
        handler.headers = {
            "Content-Length": "0",
            "X-Player-Id": "victim",
            "X-Garden-Human-Name": "伪造人类",
            "Authorization": "Bearer stolen",
            "Cookie": "garden_cat_token=stolen",
        }
        handler.rfile = BytesIO()
        handler.wfile = BytesIO()
        handler.client_address = ("127.0.0.9", 1234)
        handler.command = "POST"
        handler.send_response = lambda *_args, **_kwargs: None
        handler.send_header = lambda *_args, **_kwargs: None
        handler.end_headers = lambda: None

        with patch.object(server.http.client, "HTTPConnection", FakeConnection):
            handler._proxy_to_garden_cat(
                "POST",
                "/web/pet_cat",
                "token=secret&player=victim&kept=yes",
                target={"player": "42:3", "owner_name": "阿橘", "slot": 3},
                human_name="小满",
            )

        self.assertEqual(captured["target"], "/web/pet_cat?kept=yes")
        self.assertEqual(captured["headers"]["X-Player-Id"], "42:3")
        self.assertEqual(captured["headers"]["X-Garden-Player"], "42:3")
        self.assertEqual(captured["headers"]["X-Garden-Owner-Name"], "阿橘")
        self.assertEqual(captured["headers"]["X-Garden-Human-Name"], "小满")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertNotIn("Cookie", captured["headers"])

    def test_only_a_real_human_binding_resolves_to_a_canonical_slot_player(self):
        with tempfile.TemporaryDirectory(prefix="garden-proxy-db-") as tempdir:
            db_path = Path(tempdir) / "toy.db"
            conn = sqlite3.connect(db_path)
            conn.executescript(
                """
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    is_ai INTEGER NOT NULL,
                    deleted_at TEXT
                );
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES (1, '人类', 0, NULL);
                INSERT INTO toy_users VALUES (42, '阿橘', 1, NULL);
                INSERT INTO toy_users VALUES (43, '陌生小机', 1, NULL);
                INSERT INTO user_bindings VALUES (1, 42);
                """
            )
            conn.commit()
            conn.close()

            def connect():
                opened = sqlite3.connect(db_path)
                opened.row_factory = sqlite3.Row
                return opened

            handler = object.__new__(server.CedarToyHandler)
            with patch.object(server, "_db_connect", side_effect=connect):
                target = handler._garden_cat_bound_target({"id": 1, "is_ai": False}, "42:3")
                slot_one = handler._garden_cat_bound_target({"id": 1, "is_ai": False}, "42")
                self.assertIsNone(handler._garden_cat_bound_target({"id": 1, "is_ai": False}, "43:3"))
                self.assertIsNone(handler._garden_cat_bound_target({"id": 1, "is_ai": False}, "42:6"))
                self.assertIsNone(handler._garden_cat_bound_target({"id": 42, "is_ai": True}, "42:3"))

        self.assertEqual(target, {"player": "42:3", "owner_name": "阿橘", "slot": 3})
        self.assertEqual(slot_one, {"player": "42", "owner_name": "阿橘", "slot": 1})


if __name__ == "__main__":
    unittest.main(verbosity=2)
