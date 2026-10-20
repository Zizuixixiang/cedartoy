"""Author reads through real root auth/client/satellite, using temporary data only."""
import asyncio
import base64
from contextlib import ExitStack
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

import server
from shared_craft_adapter import client, web
from shared_craft_adapter.identity import initialize_author_grants
from shared_craft_adapter.service import create_app


class SharedCraftAuthorMcpTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="craft-author-test-")))
        self.accounts, self.grants = self.root / "accounts.db", self.root / "grants.db"
        self.secret = "temporary-craft-secret-" * 2
        self.tokens = {}
        with sqlite3.connect(self.accounts) as conn:
            conn.executescript("""
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT,
                    is_ai INTEGER, is_admin INTEGER, deleted_at TEXT,
                    deletion_requested_at_epoch INTEGER, scheduled_delete_at_epoch INTEGER,
                    last_active_at TEXT, ai_token_version INTEGER DEFAULT 0);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                CREATE TABLE ai_access_tokens (token_hash TEXT, user_id INTEGER,
                    generation INTEGER, format_version INTEGER, revoked_at_epoch INTEGER);
                INSERT INTO toy_users(id,username,is_ai,is_admin) VALUES
                    (1,'授权人类管理员',0,1),(2,'授权绑定AI',1,1),(3,'未授权绑定AI管理员',1,1),
                    (4,'未授权人类',0,0),(5,'未绑定原作者AI',1,0),(6,'未绑定原作者人类',0,0),
                    (19,'未授权管理员',1,1),(20,'未授权人类管理员',0,1);
                INSERT INTO user_bindings VALUES (1,2),(1,3),(20,19);
            """)
            for uid in (2, 3, 5, 19):
                token = server.AI_OPAQUE_TOKEN_PREFIX + "author-test-" + str(uid)
                self.tokens[uid] = token
                conn.execute("INSERT INTO ai_access_tokens VALUES (?,?,0,?,NULL)",
                             (server._opaque_ai_token_hash(token), uid, server.AI_OPAQUE_TOKEN_FORMAT_VERSION))
        for uid in (1, 4, 6, 20):
            self.tokens[uid] = server._jwt_encode(
                {"user_id": uid, "is_ai": False, "exp": int(time.time()) + 600})
        with sqlite3.connect(self.grants) as conn:
            initialize_author_grants(conn)
            conn.executemany("INSERT INTO shared_craft_authors VALUES (?)", [(1,), (2,), (5,), (6,)])
        self.app = create_app(self.root / "craft", self.accounts, self.grants, self.secret)
        self.stack.enter_context(patch.object(server, "_db_connect", side_effect=self.connect))
        self.stack.enter_context(patch.object(server, "TURTLE_DB_PATH", self.accounts))
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.grants))
        self.stack.enter_context(patch.object(client, "request", side_effect=self.satellite))
        # Keep unrelated platform reminders from touching live databases.
        for name, value in (("_authenticated_ai_player_id", None), ("_duel_unread_request_reminder", ""),
                            ("_mcp_forced_announcement", ""), ("_feedback_unread_reminder", "")):
            self.stack.enter_context(patch.object(server, name, return_value=value))
        self.no_game_calls = [self.stack.enter_context(patch.object(
            server, name, side_effect=AssertionError("author reads must not enter " + name)))
            for name in ("_anti_addiction_context", "_anti_addiction_preflight",
                         "_finalize_play_response", "_auto_migrate_legacy_account_saves")]

    def connect(self):
        conn = sqlite3.connect(self.accounts)
        conn.row_factory = sqlite3.Row
        return conn

    def request(self, path, method="GET", body=None, uid=1, secret=None):
        async def run():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),
                                        base_url="http://test") as transport:
                return await transport.request(method, "/" + path, json=body, headers={
                    "X-Craft-User": str(uid),
                    "X-Craft-Secret": self.secret if secret is None else secret})
        return asyncio.run(run())

    def satellite(self, uid, method, path, *, body=None, query=""):
        return self.request(path + ("?" + query if query else ""), method,
                            json.loads(body) if body else None, uid)

    def service_play(self, action, uid=1, **params):
        response = self.request("play", "POST", {"action": action, "params": params}, uid)
        self.assertEqual(response.status_code, 200, response.text)
        return json.loads(response.json()["content"][0]["text"])

    def root_call(self, name, uid=1, *, action=None, params=None, bearer=False, credential=None, **extra):
        arguments = {"game": "shared_craft", **extra}
        if action is not None:
            arguments["action"] = action
        if params is not None:
            arguments["params"] = params
        credential = self.tokens[uid] if credential is None else credential
        return server._handle_root_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": name, "arguments": arguments}},
            path_token="" if bearer else credential, bearer_token=credential if bearer else "")

    def result(self, response):
        self.assertFalse(response["result"]["isError"], response)
        return json.loads(response["result"]["content"][0]["text"])

    def make_submissions(self):
        self.service_play("draw_path", points=[{"x": 10, "y": 10}], color="#123456")
        item = self.request("api/gallery", "POST", {"kind": "sketch", "title": "已分享作品"}).json()["item"]
        shared = [self.service_play("share_work", item_id=item["id"], note=f"附言{i}", confirm=True)["id"]
                  for i in range(11)]
        snapshot = self.request("author/" + shared[0]).content
        self.service_play("draw_path", uid=2, points=[{"x": 20, "y": 20}], color="#123456")
        joint = self.request("api/gallery", "POST", {"kind": "sketch", "title": "私密共同作品"}, 2).json()["item"]
        pending = self.service_play("share_work", uid=2, item_id=joint["id"], note="待确认秘密", confirm=True)["id"]
        declined = self.service_play("share_work", uid=2, item_id=joint["id"], note="拒绝秘密", confirm=True)["id"]
        self.assertEqual(self.request("api/shares/" + declined, "POST", {"confirm": False}).status_code, 200)
        return shared, snapshot, (pending, declined, joint["id"])

    def test_real_root_author_reads_human_ai_unbound_and_png(self):
        shared, snapshot, hidden = self.make_submissions()
        self.assertTrue(snapshot.startswith(b"\x89PNG\r\n\x1a\n"))
        files_before = {p.relative_to(self.root): p.read_bytes()
                        for p in (self.root / "craft").rglob("*") if p.is_file()}
        for uid in (1, 2, 5, 6):
            for bearer in (False, True):
                with self.subTest(uid=uid, bearer=bearer):
                    rows = []
                    for page in (1, 2):
                        data = self.result(self.root_call("play", uid, action="author_inbox",
                                                         params={"page": page}, bearer=bearer))
                        self.assertEqual((data["page"], data["total"], data["pages"]), (page, 11, 2))
                        self.assertEqual(len(data["items"]), 10 if page == 1 else 1)
                        for item in data["items"]:
                            self.assertEqual(set(item), {"id", "title", "note", "created_at"})
                            self.assertEqual(item["title"], "已分享作品")
                            self.assertIsInstance(item["created_at"], int)
                        rows += data["items"]
                    self.assertEqual({r["id"] for r in rows}, set(shared))
                    response = self.root_call("play", uid, action="author_image",
                                              params={"submission_id": rows[0]["id"]}, bearer=bearer)
                    self.assertEqual(self.result(response), {"submission_id": rows[0]["id"]})
                    content = response["result"]["content"]
                    self.assertEqual(len(content), 2)
                    self.assertEqual(content[1]["type"], "image")
                    self.assertEqual(content[1]["mimeType"], "image/png")
                    self.assertEqual(base64.b64decode(content[1]["data"], validate=True), snapshot)
                    self.assertNotIn(content[1]["data"], content[0]["text"])
        self.assertEqual(files_before, {p.relative_to(self.root): p.read_bytes()
                                       for p in (self.root / "craft").rglob("*") if p.is_file()})
        self.assertEqual(set(self.app.state.rooms), {"1"})
        for call in self.no_game_calls:
            call.assert_not_called()
        for submission in hidden:
            denied = self.root_call("play", 5, action="author_image", params={"submission_id": submission})
            self.assertTrue(denied["result"]["isError"], denied)

    def test_ungranted_admins_and_binding_do_not_inherit_or_spoof_identity(self):
        spoof = {"user_id": 1, "actor": "1", "actor_id": "1", "player_id": "1", "room": "1",
                 "room_id": "1", "slot": 3, "token": self.tokens[1], "path_token": self.tokens[1],
                 "bearer_token": self.tokens[1], "authenticated_account": {"id": 1}, "is_admin": True}
        for uid in (3, 4, 19, 20):
            for action in ("author_inbox", "author_image"):
                response = self.root_call("play", uid, action=action, params=spoof, **spoof)
                self.assertTrue(response["result"]["isError"], response)
                # Bypass the client's field filtering as well.
                raw = self.request("play", "POST", {"action": action, "params": spoof, **spoof}, uid)
                self.assertEqual(raw.status_code, 403, raw.text)
            self.assertEqual(self.request("author", uid=uid).status_code, 403)
        response = self.root_call("play", 5, action="author_inbox", params={**spoof, "user_id": 19})
        self.assertEqual(self.result(response)["total"], 0)
        self.assertFalse((self.root / "craft/rooms").exists())
        self.assertEqual(self.request("play", "POST", {"action": "author_inbox"}, 1,
                                      secret="wrong").status_code, 404)

    def test_revoke_immediately_in_guide_root_and_web_and_after_gateway_auth(self):
        self.assertIn("author_inbox", self.result(self.root_call("get_guide", 5))["guide"])
        current = server._current_account(self.tokens[5])
        with sqlite3.connect(self.grants) as conn:
            conn.execute("DELETE FROM shared_craft_authors WHERE user_id=5")
        self.assert_public(self.root_call("get_guide", 5))
        self.assertTrue(self.root_call("play", 5, action="author_inbox")["result"]["isError"])
        with self.assertRaises(server._McpError):
            server._tool_play({"game": "shared_craft", "action": "author_inbox"}, authenticated_account=current)
        self.assertFalse(self.request("author?access=1", uid=5).json()["allowed"])
        self.assertEqual(self.request("author", uid=5).status_code, 403)

    def test_deleted_and_pending_deletion_accounts_rejected_by_satellite_too(self):
        for statement in ("UPDATE toy_users SET deleted_at='now' WHERE id=5",
                          "UPDATE toy_users SET deletion_requested_at_epoch=1, scheduled_delete_at_epoch=2 WHERE id=5",
                          "DELETE FROM toy_users WHERE id=5"):
            with self.subTest(statement=statement):
                current = server._current_account(self.tokens[5])
                with sqlite3.connect(self.accounts) as conn:
                    conn.execute(statement)
                self.assert_public(self.root_call("get_guide", 5))
                self.assertTrue(self.root_call("play", 5, action="author_inbox")["result"]["isError"])
                with self.assertRaises(server._McpError):
                    server._tool_play({"game": "shared_craft", "action": "author_inbox"}, authenticated_account=current)
                for path in ("author", "author?access=1", "author/" + "a" * 32):
                    self.assertEqual(self.request(path, uid=5).status_code, 403)
                with sqlite3.connect(self.accounts) as conn:
                    conn.execute("UPDATE toy_users SET deleted_at=NULL, deletion_requested_at_epoch=NULL WHERE id=5")

    def assert_public(self, response):
        self.assertEqual(self.result(response),
                         {"game": "shared_craft", "guide": Path("shared_craft_adapter/guide.txt").read_text()})

    def test_exact_public_private_guides_and_token_precedence(self):
        public = Path("shared_craft_adapter/guide.txt").read_text()
        appendix = Path("shared_craft_adapter/guide_author.txt").read_text().strip()
        for uid in (1, 2, 5, 6):
            for bearer in (False, True):
                guide = self.result(self.root_call("get_guide", uid, bearer=bearer))["guide"]
                self.assertEqual(guide, public + "\n\n" + appendix)
                self.assertEqual(guide.count("【作者/管理查看】"), 1)
                for call in ('play(game="shared_craft", action="author_inbox", params={"page":1})',
                             'play(game="shared_craft", action="author_image", params={"submission_id":'):
                    self.assertIn(call, guide)
        for uid in (3, 4, 19, 20):
            self.assert_public(self.root_call("get_guide", uid))
        forged = {"user_id": 1, "player_id": "1", "path_token": self.tokens[1],
                  "bearer_token": self.tokens[1], "params": {"user_id": 1}}
        self.assert_public(self.root_call("get_guide", credential="", **forged))
        response = server._handle_root_mcp(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
             "params": {"name": "get_guide", "arguments": {"game": "shared_craft"}}},
            path_token="wrong", bearer_token=self.tokens[1])
        self.assert_public(response)
        for action in ("author_inbox", "author_image"):
            self.assertNotIn(action, server._tool_list_games())
            self.assertNotIn(action, json.dumps(server._PLATFORM_TOOLS))

    def test_wrong_missing_expired_and_revoked_tokens(self):
        expired = server._jwt_encode({"user_id": 1, "is_ai": False, "exp": 1})
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("UPDATE ai_access_tokens SET revoked_at_epoch=1 WHERE user_id=2")
            conn.execute("UPDATE toy_users SET ai_token_version=1 WHERE id=5")
        for token in ("", "invalid", expired, self.tokens[2], self.tokens[5]):
            for bearer in (False, True):
                self.assert_public(self.root_call("get_guide", credential=token, bearer=bearer))
                response = self.root_call("play", action="author_inbox", credential=token, bearer=bearer,
                                          params={"user_id": 1, "token": self.tokens[1]})
                self.assertTrue(response["result"]["isError"], response)

    def test_missing_and_corrupt_databases_fail_closed(self):
        for name in ("missing.db", "empty.db", "corrupt.db"):
            path = self.root / name
            if name == "empty.db":
                with sqlite3.connect(path):
                    pass
            elif name == "corrupt.db":
                path.write_bytes(b"not a sqlite database")
            with patch.object(server, "SESSIONS_DB_PATH", path):
                self.assert_public(self.root_call("get_guide", 1))
            self.assertEqual(path.exists(), name != "missing.db")
        with patch.object(server, "_db_connect", side_effect=sqlite3.DatabaseError("broken accounts")):
            self.assert_public(self.root_call("get_guide", 1))
        self.grants.write_bytes(b"not a sqlite database")
        self.assert_public(self.root_call("get_guide", 1))
        with self.assertLogs("shared_craft_adapter.service", level="ERROR"):
            self.assertTrue(self.root_call("play", action="author_inbox")["result"]["isError"])

    def test_web_author_reads_keep_exact_grants_and_immutable_snapshots(self):
        shared, snapshot, hidden = self.make_submissions()
        self.service_play("new_canvas", confirm=True)
        self.service_play("draw_path", points=[{"x": 50, "y": 50}])

        class Http:
            command = "GET"
            rfile = io.BytesIO()
            def __init__(self, path, token):
                self.path = "/shared-craft/" + path
                self.headers = {"Authorization": "Bearer " + token, "X-Craft-User": "1"}
                self.wfile, self.response_headers = io.BytesIO(), {}
            def send_response(self, status): self.status = status
            def send_header(self, name, value): self.response_headers[name] = value
            def end_headers(self): pass

        for uid in (1, 2, 5, 6):
            for path in ("author?access=1", "author?page=2", "author/" + shared[0]):
                http = Http(path, self.tokens[uid])
                web.serve(http, server)
                self.assertEqual(http.status, 200)
                self.assertEqual(http.response_headers["Cache-Control"], "no-store")
                if path.startswith("author/"):
                    self.assertEqual(http.wfile.getvalue(), snapshot)
                    self.assertEqual(http.response_headers["Content-Type"], "image/png")
        for uid in (3, 19, 20):
            http = Http("author/" + shared[0], self.tokens[uid])
            web.serve(http, server)
            self.assertEqual(http.status, 403)
        for submission in hidden:
            http = Http("author/" + submission, self.tokens[5])
            web.serve(http, server)
            self.assertEqual(http.status, 403)
        image = self.root_call("play", 5, action="author_image", params={"submission_id": shared[0]})
        self.assertEqual(base64.b64decode(image["result"]["content"][1]["data"]), snapshot)

    def test_argument_validation_and_empty_page_without_opening_rooms(self):
        for params in ({}, {"page": 99}):
            data = self.result(self.root_call("play", 5, action="author_inbox", params=params))
            self.assertEqual((data["items"], data["page"], data["pages"], data["total"]), ([], 1, 1, 0))
        for page in (0, -1, "bad"):
            self.assertTrue(self.root_call("play", 5, action="author_inbox",
                                          params={"page": page})["result"]["isError"])
        for submission in (None, "", "../../draft.png", [], "a" * 32):
            self.assertTrue(self.root_call("play", 5, action="author_image",
                                          params={"submission_id": submission})["result"]["isError"])
        result = self.root_call("play", 5, action="author_inbox", params=json.dumps(json.dumps({"page": 1})))
        self.assertEqual(self.result(result)["total"], 0)
        self.assertFalse((self.root / "craft/rooms").exists())


if __name__ == "__main__":
    unittest.main()
