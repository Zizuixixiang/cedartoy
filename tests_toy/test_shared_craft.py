"""Temporary accounts/storage only; original engine and real ASGI handlers."""
import asyncio
import base64
import json
import io
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx

from shared_craft_adapter.identity import initialize_author_grants
from shared_craft_adapter.service import create_app, UPSTREAM
from shared_craft_adapter.client import ContentResult


class SharedCraftTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cedar-shared-craft-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.accounts, self.grants = self.root / "accounts.db", self.root / "sessions.db"
        with sqlite3.connect(self.accounts) as conn:
            conn.executescript("""
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER,
                  deleted_at TEXT, deletion_requested_at_epoch INTEGER,
                  last_active_at TEXT, ai_token_version INTEGER DEFAULT 0);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users(id,username,is_ai) VALUES
                  (1,'人类',0),(2,'小杉',1),(3,'小雪',1),(4,'别人',0),(5,'未绑定AI',1),(6,'作者',0);
                INSERT INTO user_bindings VALUES (1,2),(1,3);
            """)
        self.app = create_app(self.root / "craft", self.accounts, self.grants, "test-secret-" * 4)

    def request(self, path, method="GET", body=None, uid=1, secret=True, app=None):
        async def run():
            headers = {"X-Craft-User": str(uid)}
            if secret:
                headers["X-Craft-Secret"] = "test-secret-" * 4
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app or self.app),
                                        base_url="http://test", headers=headers) as client:
                return await client.request(method, "/" + path, json=body)
        return asyncio.run(run())

    def play(self, name, uid=1, ok=True, **params):
        response = self.request("play", "POST", {"action": name, "params": params}, uid)
        if not ok:
            self.assertGreaterEqual(response.status_code, 400, response.text)
            return response.json()
        self.assertEqual(response.status_code, 200, response.text)
        return json.loads(response.json()["content"][0]["text"])

    def draw(self, uid=1):
        return self.play("draw_path", uid, points=[{"x": 100+uid, "y": 100}], color="#123456")

    def gallery(self, uid=1):
        response = self.request("api/gallery", "POST", {"kind": "sketch", "title": "我们的画"}, uid)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["item"]

    def grant(self, uid=6):
        with sqlite3.connect(self.grants) as conn:
            initialize_author_grants(conn)
            conn.execute("INSERT INTO shared_craft_authors VALUES (?)", (uid,))

    def test_three_actors_share_canvas_distinct_undo_and_room_isolation(self):
        for uid in (1, 2, 3):
            self.draw(uid)
        data = self.request("api/canvas", uid=3).json()
        self.assertEqual({s["actor"] for s in data["canvas"]["strokes"]}, {"1", "2", "3"})
        self.assertEqual({p["id"] for p in data["participants"]}, {"1", "2", "3"})
        self.assertEqual(len(data["presence"]), 2)
        self.play("undo_canvas", 2)
        self.assertEqual({s["actor"] for s in self.request("api/canvas").json()["canvas"]["strokes"]}, {"1", "3"})
        self.assertEqual(self.request("api/canvas", uid=4).json()["canvas"]["strokes"], [])
        self.assertEqual(self.request("api/canvas", uid=5).status_code, 403)
        self.assertEqual(self.request("api/canvas", secret=False).status_code, 404)
        self.assertEqual(self.request("setup/anything").status_code, 404)
        self.assertEqual(self.request("mcp/anything", "POST", {}).status_code, 404)

    def test_unbind_rebind_delete_and_queue_revocation(self):
        result = self.play("queue_canvas_strokes", 2, strokes=[
            {"points": [{"x": i, "y": 100}]} for i in range(12)], interval_ms=1200)
        room = self.app.state.rooms["1"]
        for _ in range(100):
            if room.canvas.state()["strokes"]:
                break
            time.sleep(.01)
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("DELETE FROM user_bindings WHERE ai_user_id=2")
        self.assertEqual(self.request("api/canvas", uid=2).status_code, 403)
        count = len(room.canvas.state()["strokes"])
        time.sleep(1.3)
        self.assertEqual(len(room.canvas.state()["strokes"]), count)
        self.assertEqual(room.ns["jobs"].status("2", result["job_id"])["status"], "failed")
        self.assertEqual(self.gallery()["authors"], ["小杉"])
        self.assertNotIn("2", {p["id"] for p in self.request("api/canvas").json()["participants"]})
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("INSERT INTO user_bindings VALUES(4,2)")
        self.assertEqual(self.request("api/canvas", uid=2).json()["canvas"]["strokes"], [])
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("UPDATE toy_users SET deleted_at='now' WHERE id=4")
        self.assertEqual(self.request("api/canvas", uid=2).status_code, 403)

    def test_canvas_original_colors_fill_png_and_confirmed_new(self):
        self.play("select_canvas_color", 2, color="#abcdef")
        self.draw(2)
        self.play("brush_stroke", 3, points=[{"x": 5, "y": 5}], color="#33423b", brush="pencil")
        self.play("fill_region", 1, x=300, y=300, color="#ffffff")
        for action in ("get_canvas_snapshot", "export_image"):
            content = self.request("play", "POST", {"action": action}, 2).json()["content"]
            self.assertEqual(content[1]["type"], "image")
            self.assertTrue(base64.b64decode(content[1]["data"]).startswith(b"\x89PNG"))
            self.assertNotIn("_image_", content[0]["text"])
            self.assertLess(len(content[0]["text"]), 300)
        self.play("new_canvas", 1, ok=False)
        self.assertEqual(self.request("api/canvas", "POST", {"action": "clear"}).status_code, 400)
        self.play("new_canvas", 1, confirm=True, orientation="landscape")
        state = self.request("api/canvas").json()["canvas"]
        self.assertEqual((state["width"], state["height"], len(state["strokes"])), (1200, 800, 0))

    def test_three_bead_modes_inventory_handful_and_hidden_gift(self):
        for mode in ("together", "separate", "gift"):
            board = self.play("new_bead_board", 2, title=mode, mode=mode, size=8)
            bid = board["id"]
            self.play("select_bead_color", 2, board_id=bid, color="F14")
            self.play("place_beads", 2, board_id=bid, beads=[{"x": 0, "y": 0, "color": "F14"}])
            self.play("place_beads", 2, ok=False, board_id=bid, beads=[])
            self.play("place_beads", 2, ok=False, board_id=bid,
                      beads=[{"x": i, "y": 1, "color": "F14"} for i in range(6)])
            if mode == "gift":
                self.play("open_bead_board", 1, ok=False, board_id=bid)
                self.assertEqual(self.play("list_bead_boards", 4)["boards"], [])
                self.play("reveal_bead_gift", 3, ok=False, board_id=bid)
                self.play("reveal_bead_gift", 2, board_id=bid)
            for uid in (1, 3):
                self.play("place_beads", uid, board_id=bid,
                          beads=[{"x": uid, "y": 0, "color": "F14"}])
            current = self.play("open_bead_board", 3, board_id=bid)
            self.assertEqual(set(current["inventories"]), {"1", "2", "3"})
            self.assertEqual([current["inventories"][str(i)]["#ffa9ad"] for i in (1,2,3)], [79,79,79])
            self.play("take_bead", 1, ok=mode != "separate", board_id=bid, x=0, y=0)
            self.play("refill_beads", 3, ok=False, board_id=bid, color="F14")
            self.play("open_bead_board", 4, ok=False, board_id=bid)

    def test_original_plan_queue_and_finish_decoration_gallery(self):
        from shared_craft_room import beads as engine
        bid = self.play("new_bead_board", 2, title="豆", mode="together", size=8)["id"]
        self.play("set_bead_work_plan", 2, board_id=bid,
                  beads=[{"x": i, "y": 0, "color": "F14"} for i in range(8)], title="一行")
        content = self.request("play", "POST", {"action": "open_bead_board",
                                               "params": {"board_id": bid}}, 2).json()["content"]
        self.assertEqual(content[1]["type"], "image")
        self.assertNotIn("base64,", content[0]["text"])
        self.assertTrue(self.request("api/bead-boards/" + bid, uid=2).json()["board"]["reference_image"].startswith("data:image/png"))
        job = self.play("continue_bead_work", 2, board_id=bid, handfuls=2, interval_ms=250)
        for _ in range(100):
            status = self.play("get_craft_job", 2, job_id=job["job_id"])
            if status["status"] in {"completed", "failed"}:
                break
            time.sleep(.02)
        self.assertEqual(status["status"], "completed")
        board = self.play("open_bead_board", 2, board_id=bid)
        self.assertEqual(len(board["cells"]), 8)
        self.assertIsNone(board["work_session"])
        with patch.object(engine, "unix_now", return_value=100):
            self.play("work_bead_finish", 2, board_id=bid, action="cover")
            self.play("work_bead_finish", 2, board_id=bid, action="heat")
            self.play("work_bead_finish", 2, ok=False, board_id=bid, action="start_iron")
        with patch.object(engine, "unix_now", return_value=105):
            self.play("work_bead_finish", 1, board_id=bid, action="start_iron")
        with patch.object(engine, "unix_now", return_value=109):
            self.play("work_bead_finish", 3, board_id=bid, action="finish_iron")
        with patch.object(engine, "unix_now", return_value=113):
            finished = self.play("work_bead_finish", 2, board_id=bid, action="lift")
        self.assertEqual(finished["craft"]["quality"], "good")
        png = self.request("api/bead-boards/" + bid + ".png", uid=2).content
        url = "data:image/png;base64," + base64.b64encode(png).decode()
        decorated = self.request("api/bead-boards/" + bid, "POST",
                                 {"action": "decoration", "data_url": url, "piece_data_url": url, "has_pouch": True}, 2)
        self.assertEqual(decorated.status_code, 200, decorated.text)
        result = self.request("api/gallery", "POST",
                              {"kind": "bead", "source_id": bid, "piece_columns": 8, "piece_rows": 1}, 2)
        self.assertEqual(result.status_code, 200, result.text)
        item = result.json()["item"]
        self.assertEqual(set(item["actor_ids"]), {"1", "2", "3"})
        self.assertEqual(self.request("api/gallery/" + item["id"] + ".png", uid=3).content, png)

    def test_private_default_explicit_human_and_joint_ai_confirmation(self):
        for uid in (1,2,3):
            self.draw(uid)
        item = self.gallery()
        self.grant()
        self.assertEqual(self.request("author", uid=6).json()["total"], 0)
        self.play("share_work", 2, ok=False, item_id=item["id"], note="hello")
        request = self.play("share_work", 2, item_id=item["id"], note="给作者 <script>", confirm=True)
        self.assertEqual(request["status"], "pending")
        self.assertEqual(self.request("author", uid=6).json()["total"], 0)
        self.assertEqual(self.request("author/" + request["id"], uid=6).status_code, 403)
        self.assertEqual(self.request("api/shares/" + request["id"], "POST", {"confirm": True}, 3).status_code, 403)
        self.assertEqual(self.request("api/shares/" + request["id"], "POST", {"confirm": True}, 4).status_code, 403)
        pending = self.request("api/gallery").json()["pending_shares"]
        self.assertEqual(pending[0]["note"], "给作者 <script>")
        snapshot = self.request("api/shares/" + request["id"] + ".png").content
        self.draw(3)  # Approval must refer to the submitted image, not live canvas.
        self.request("api/gallery/" + item["id"], "DELETE")
        approved = self.request("api/shares/" + request["id"], "POST", {"confirm": True}).json()
        self.assertEqual(approved["status"], "shared")
        self.assertEqual(self.request("author/" + request["id"], uid=6).content, snapshot)
        rows = self.request("author", uid=6).json()["items"]
        self.assertEqual(set(rows[0]), {"id", "title", "note", "created_at"})
        self.assertEqual(rows[0]["note"], "给作者 <script>")
        for uid in (1,2,3,4):
            self.assertEqual(self.request("author", uid=uid).status_code, 403)
        self.assertEqual(self.request("api/canvas", uid=6).json()["canvas"]["strokes"], [])
        with sqlite3.connect(self.grants) as conn:
            conn.execute("DELETE FROM shared_craft_authors")
        self.assertEqual(self.request("author/" + request["id"], uid=6).status_code, 403)

    def test_solo_ai_share_human_share_decline_and_idempotence(self):
        self.draw(2)
        item = self.gallery(2)
        self.play("share_work", 3, ok=False, item_id=item["id"], confirm=True)
        first = self.play("share_work", 2, item_id=item["id"], confirm=True)
        self.assertEqual(first["status"], "shared")
        self.assertEqual(first, self.play("share_work", 2, item_id=item["id"], confirm=True))
        self.draw(1)
        item = self.gallery()
        pending = self.play("share_work", 2, item_id=item["id"], confirm=True)
        declined = self.request("api/shares/" + pending["id"], "POST", {"confirm": False}).json()
        self.assertEqual(declined["status"], "declined")
        self.assertEqual(self.play("share_work", 1, item_id=item["id"], note="自愿", confirm=True)["status"], "shared")
        self.grant()
        self.assertEqual(self.request("author", uid=6).json()["total"], 2)

    def test_corrupt_storage_retained_and_read_write_fail_closed(self):
        self.draw()
        for name, invalid in (("canvas", b"bad json"), ("beads", b'{"boards":false}'), ("gallery", b"{}")):
            path = self.root / "craft/rooms/1" / (name + ".json")
            path.write_bytes(invalid)
            route = {"canvas": "api/canvas", "beads": "api/bead-boards", "gallery": "api/gallery"}[name]
            response = self.request(route)
            self.assertEqual(response.status_code, 400)
            self.assertIn("存档损坏", response.text)
            self.assertEqual(path.read_bytes(), invalid)
            self.assertEqual(len(list(path.parent.glob(path.name + ".corrupt-*"))), 1)

    def test_atomic_write_failure_reports_error_and_retains_save(self):
        self.draw()
        path = self.root / "craft/rooms/1/canvas.json"
        before = path.read_bytes()
        with patch("shared_craft_adapter.storage.os.replace", side_effect=OSError("test disk failure")):
            response = self.request("play", "POST", {"action": "draw_path",
                                                     "params": {"points": [{"x": 1, "y": 1}]}}, 2)
        self.assertEqual(response.status_code, 503)
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(path.parent.glob(".canvas.json*")))

    def test_restart_and_cross_process_concurrency(self):
        self.draw(1)
        item = self.gallery()
        submission = self.play("share_work", item_id=item["id"], note="持久化", confirm=True)
        bid = self.play("new_bead_board", 2, title="存档", mode="together", size=8)["id"]
        self.play("place_beads", 3, board_id=bid, beads=[{"x": 0, "y": 0, "color": "F14"}])
        new_app = create_app(self.root / "craft", self.accounts, self.grants, "test-secret-" * 4)
        self.assertEqual(len(self.request("api/canvas", app=new_app).json()["canvas"]["strokes"]), 1)
        self.assertEqual(self.request("api/gallery", app=new_app).json()["items"][0]["id"], item["id"])
        self.assertEqual(self.request("api/bead-boards/" + bid, uid=2, app=new_app).json()["board"]["cells"]["0,0"]["actor"], "3")
        self.grant()
        self.assertEqual(self.request("author", uid=6, app=new_app).json()["items"][0]["id"], submission["id"])
        code = """from shared_craft_adapter.service import Room
from pathlib import Path
import sys
r=Room(Path(sys.argv[1]),'1',sys.argv[2])
for i in range(8):
 with r.lock:
  r.check(sys.argv[3])
  r.canvas.draw_path(sys.argv[3],[{'x':i,'y':20}])
"""
        processes = [subprocess.Popen([sys.executable, "-c", code, str(self.root / "craft"), str(self.accounts), str(uid)],
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE) for uid in (1,2,3)]
        for process in processes:
            _, error = process.communicate(timeout=30)
            self.assertEqual(process.returncode, 0, error.decode())
        strokes = self.request("api/canvas").json()["canvas"]["strokes"]
        self.assertEqual(len(strokes), 25)
        self.assertEqual({s["actor"] for s in strokes}, {"1","2","3"})

    def test_assets_unchanged_except_scoped_ui_and_all_actions_in_guide(self):
        guide = Path("shared_craft_adapter/guide.txt").read_text()
        self.request("api/canvas")
        for tool in self.app.state.rooms["1"].ns["TOOLS"]:
            self.assertIn(tool["name"], guide)
        for name in ("craft-gallery-pouches-v2.webp", "bead-shelf-room-v2.webp", "crafts.css", "sketch.css"):
            self.assertEqual(self.request("assets/" + name).content, (UPSTREAM / "static" / name).read_bytes())
        for page in ("sketch", "beads", "crafts"):
            self.assertEqual(self.request(page + "/").status_code, 200)
            js = self.request("assets/" + page + ".js")
            result = subprocess.run(["node", "--check"], input=js.content, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertLess(len(guide), 2100)

    def test_real_platform_tokens_root_and_web_interoperate(self):
        import server
        from contextlib import ExitStack
        from shared_craft_adapter import web

        def connect():
            conn = sqlite3.connect(self.accounts)
            conn.row_factory = sqlite3.Row
            return conn

        tokens = {}
        with sqlite3.connect(self.accounts) as conn:
            conn.execute("""CREATE TABLE ai_access_tokens
                (token_hash TEXT, user_id INTEGER, generation INTEGER,
                 format_version INTEGER, revoked_at_epoch INTEGER)""")
            for uid in (2, 3):
                tokens[uid] = server.AI_OPAQUE_TOKEN_PREFIX + "shared-craft-fixture-" + str(uid)
                conn.execute("INSERT INTO ai_access_tokens VALUES (?,?,0,?,NULL)",
                             (server._opaque_ai_token_hash(tokens[uid]), uid, server.AI_OPAQUE_TOKEN_FORMAT_VERSION))
        tokens[1] = server._jwt_encode({"user_id": 1, "is_ai": False, "exp": int(time.time())+60})

        def satellite(uid, method, path, body=None, query=""):
            return self.request(path + ("?" + query if query else ""), method,
                                json.loads(body) if body else None, uid)

        class Http:
            command = "GET"
            path = "/shared-craft/api/canvas"
            headers = {"Authorization": "Bearer " + tokens[1], "Host": "craft.test",
                       "X-Craft-User": "4"}
            rfile = io.BytesIO()
            def __init__(self):
                self.wfile = io.BytesIO()
                self.response_headers = {}
            def send_response(self, status): self.status = status
            def send_header(self, name, value): self.response_headers[name] = value
            def end_headers(self): pass

        with ExitStack() as stack:
            stack.enter_context(patch.object(server, "_db_connect", side_effect=connect))
            stack.enter_context(patch.object(server, "TURTLE_DB_PATH", self.accounts))
            stack.enter_context(patch.object(server.shared_craft_client, "request", side_effect=satellite))
            for name, result in (("_anti_addiction_context", {}), ("_anti_addiction_preflight", None),
                                 ("_authenticated_ai_player_id", None), ("_duel_unread_request_reminder", ""),
                                 ("_mcp_forced_announcement", ""), ("_feedback_unread_reminder", "")):
                stack.enter_context(patch.object(server, name, return_value=result))
            stack.enter_context(patch.object(server, "_finalize_play_response", side_effect=lambda response, **kw: response))
            for uid in (2,3):
                result = server._handle_root_mcp({"jsonrpc": "2.0", "id": uid, "method": "tools/call",
                    "params": {"name": "play", "arguments": {"game": "shared_craft", "action": "draw_path",
                        "player_id": "4", "params": {"player_id": "4", "points": [{"x": uid, "y": 1}]}}}},
                    path_token=tokens[uid] if uid == 2 else None,
                    bearer_token=tokens[uid] if uid == 3 else None)
                self.assertFalse(result["result"]["isError"], result)
            http = Http()
            web.serve(http, server)
            self.assertEqual(http.status, 200)
            self.assertEqual({s["actor"] for s in json.loads(http.wfile.getvalue())["canvas"]["strokes"]}, {"2","3"})
            http = Http()
            http.command, http.path = "POST", "/shared-craft/session"
            http.headers = {**Http.headers, "Origin": "https://craft.test", "X-Forwarded-Proto": "https"}
            web.serve(http, server)
            self.assertEqual(http.status, 200)
            cookie = http.response_headers["Set-Cookie"]
            self.assertIn("HttpOnly; SameSite=Strict; Path=/shared-craft/; Secure", cookie)
            http = Http()
            http.headers = {"Cookie": cookie.split(";",1)[0]}
            web.serve(http, server)
            self.assertEqual(http.status, 200)
            with sqlite3.connect(self.accounts) as conn:
                conn.execute("DELETE FROM user_bindings WHERE ai_user_id=2")
            result = server._tool_play({"game":"shared_craft","action":"get_canvas_snapshot"}, path_token=tokens[3])
            self.assertEqual(result.content[1]["type"], "image")
            with self.assertRaises(server._McpError):
                server._tool_play({"game":"shared_craft","action":"get_canvas_snapshot"}, path_token=tokens[2])
            http = Http()
            http.headers = {}
            web.serve(http, server)
            self.assertEqual(http.status, 401)


class SharedCraftGatewayTests(unittest.TestCase):
    def test_root_path_and_bearer_return_real_image_content(self):
        import server
        image = {"type": "image", "data": base64.b64encode(b"\x89PNGtest").decode(), "mimeType": "image/png"}
        with patch.object(server, "_current_account", return_value={"id": 2, "is_ai": True}), \
             patch.object(server, "_anti_addiction_context", return_value={}), \
             patch.object(server, "_anti_addiction_preflight", return_value=None), \
             patch.object(server, "_finalize_play_response", side_effect=lambda response, **kw: response), \
             patch.object(server, "_authenticated_ai_player_id", return_value=None), \
             patch.object(server, "_duel_unread_request_reminder", return_value=""), \
             patch.object(server, "_mcp_forced_announcement", return_value=""), \
             patch.object(server, "_feedback_unread_reminder", return_value=""), \
             patch.object(server.shared_craft_client, "play", return_value=ContentResult([
                 {"type": "text", "text": '{"revision":1}'}, image])) as play:
            for path, bearer in (("path-token", ""), ("", "bearer-token")):
                result = server._handle_root_mcp(
                    {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "play", "arguments": {"game": "shared_craft", "action": "get_canvas_snapshot",
                                                     "player_id": "4", "params": {"player_id": "4"}}}},
                    path_token=path, bearer_token=bearer)
                self.assertEqual(result["result"]["content"][1], image)
                self.assertFalse(result["result"]["isError"])
                self.assertEqual(play.call_args.args[0]["id"], 2)
            self.assertIn("shared_craft·共同手作·嘞噫黎", server._tool_list_games())
            guide = json.loads(server._tool_get_guide({"game": "shared_craft"}))["guide"]
            self.assertIn("MIT", guide)
            self.assertNotIn("存档槽", guide)

    def test_client_discards_claimed_identity_keeps_original_nested_action(self):
        from shared_craft_adapter import client
        response = httpx.Response(200, json={"content": [{"type": "text", "text": "{}"}]})
        with patch.object(client, "request", return_value=response) as request:
            client.play({"id": 2}, {"action": "work_bead_finish",
                                   "player_id": "99", "params": {"action": "heat", "board_id": "abc",
                                                                "actor": "99", "room": "99", "player_id": "99"}})
            self.assertEqual(request.call_args.args[:3], (2, "POST", "play"))
            self.assertEqual(json.loads(request.call_args.kwargs["body"])["params"], {"action": "heat", "board_id": "abc"})


if __name__ == "__main__":
    unittest.main()
