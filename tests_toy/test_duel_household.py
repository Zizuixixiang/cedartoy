"""Public MCP discovery -> play, with real auth and disposable account/game DBs.

The Duel subprocess uses its own interpreter; no production HTTP, accounts,
tokens, or NPC providers are touched. Only unrelated platform side effects and
NPC scheduling are stubbed.
"""
import json
import os
import sqlite3
import subprocess
import tempfile
import time
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

import server


BACKEND = """
import asyncio,json,sys
from unittest.mock import AsyncMock
from app import database,main
from app.models import McpPlayBody
from app.npc_personas import NpcPersona
database.init_db()
main._schedule_current_system_npc = AsyncMock()
main.select_personas = lambda n: [NpcPersona('test-'+str(i),'测试NPC','isolated') for i in range(n)]
async def run():
    print('ready',flush=True)
    for line in sys.stdin:
        try:
            result=await main.mcp_play(McpPlayBody(**json.loads(line)))
        except main.DuelError as exc:
            result={'error':str(exc)}
        print(json.dumps(result,ensure_ascii=False),flush=True)
asyncio.run(run())
"""


class DuelHouseholdTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix="duel-household-")))
        self.accounts = self.root / "accounts.db"
        self.tokens = {}
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT,
                    is_ai INTEGER, deleted_at TEXT, last_active_at TEXT,
                    ai_token_version INTEGER DEFAULT 0, private_note TEXT);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                CREATE TABLE ai_access_tokens (token_hash TEXT, user_id INTEGER,
                    generation INTEGER, format_version INTEGER, revoked_at_epoch INTEGER);
                INSERT INTO toy_users(id,username,is_ai,private_note) VALUES
                    (710,'主人',0,'private'),(720,'别人',0,'private'),
                    (701,'甲',1,'private'),(702,'Sirius',1,'private'),
                    (703,'别家',1,'private'),(704,'已注销',1,'private'),(705,'独行',1,'private');
                UPDATE toy_users SET deleted_at='deleted' WHERE id=704;
                INSERT INTO user_bindings VALUES (710,701),(710,702),(710,704),(720,703);
            """)
            for uid in (701, 702, 703, 704, 705):
                token = server.AI_OPAQUE_TOKEN_PREFIX + "household-test-" + str(uid)
                self.tokens[str(uid)] = token
                conn.execute("INSERT INTO ai_access_tokens VALUES (?,?,0,?,NULL)",
                    (server._opaque_ai_token_hash(token), uid, server.AI_OPAQUE_TOKEN_FORMAT_VERSION))
        self.tokens["710"] = server._jwt_encode({"user_id": 710, "is_ai": False, "exp": int(time.time()) + 600})
        self.stack.enter_context(patch.object(server, "_db_connect", side_effect=self.connect))
        self.stack.enter_context(patch.object(server, "SESSIONS_DB_PATH", self.root / "sessions.db"))
        self.stack.enter_context(patch.object(server.game_activity, "record"))
        for name, value in (("_auto_migrate_legacy_account_saves", None), ("_stamp_save_owner", None),
                            ("_anti_addiction_context", None), ("_anti_addiction_preflight", None),
                            ("_anti_addiction_record_success", ""), ("_play_announcements", ""),
                            ("_mcp_forced_announcement", ""), ("_feedback_unread_reminder", ""),
                            ("_game_maintenance", None)):
            self.stack.enter_context(patch.object(server, name, return_value=value))
        self.backend = self.stack.enter_context(patch.object(server, "_request_duel_backend", side_effect=self.forward))
        self.process = None

    def connect(self):
        conn = sqlite3.connect(self.accounts)
        conn.row_factory = sqlite3.Row
        return conn

    def stop_backend(self):
        self.process.stdin.close()
        try:
            self.process.wait(timeout=10)
        finally:
            if self.process.poll() is None:
                self.process.kill()
                self.process.wait()
            self.process.stdout.close()
            self.process.stderr.close()

    def forward(self, payload):
        if self.process is None:
            duel = Path(server.__file__).parent / "vendor" / "duel"
            self.process = subprocess.Popen([str(duel / ".venv/bin/python"), "-u", "-c", BACKEND],
                cwd=duel, env={**os.environ, "DUEL_DB_PATH": str(self.root / "duel.db"), "PYTHONDONTWRITEBYTECODE": "1"},
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            self.stack.callback(self.stop_backend)
            self.assertEqual(self.process.stdout.readline().strip(), "ready")
        self.process.stdin.write(json.dumps(payload) + "\n")
        self.process.stdin.flush()
        data = json.loads(self.process.stdout.readline())
        if "error" in data:
            raise server._McpError(-32602, data["error"])
        return data

    def call(self, action, params=None, uid="701", bearer=False, error=False, token=None):
        credential = self.tokens.get(uid, "") if token is None else token
        response = server._handle_root_mcp({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "play", "arguments": {"game": "duel", "action": action, "params": params or {}}}},
            path_token="" if bearer else credential, bearer_token=credential if bearer else "")
        self.assertNotIn("error", response, response)
        self.assertEqual(response["result"]["isError"], error, response)
        text = response["result"]["content"][0]["text"]
        data = text if error else json.loads(text)
        if not error and action != "household":
            self.assertNotIn('"members"', text)
            self.assertNotIn('"household_ais"', text)
        return data

    def test_public_discovery_auth_filters_spoofing_and_gateway_immediate_response(self):
        expected = {"self_id": "701", "members": [{"id": "701", "name": "甲"}, {"id": "702", "name": "Sirius"}]}
        for bearer in (False, True):
            self.assertEqual(self.call("household", {"player_id": "703", "human_id": 720,
                "household_id": 720, "opponent_id": "720", "slot": 2}, bearer=bearer), expected)
        prepared = server._tool_play({"game": "duel", "action": "household"},
                                    path_token=self.tokens["701"], defer_duel=True)
        self.assertEqual(json.loads(prepared), expected)
        self.assertEqual(self.call("household", uid="705"),
                         {"self_id": "705", "members": [{"id": "705", "name": "独行"}]})
        for uid in ("710", "704", "guest"):
            self.call("household", uid=uid, error=True)
        self.call("household", token=server.AI_OPAQUE_TOKEN_PREFIX + "invalid", error=True)
        with self.connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE ai_user_id=702")
        self.assertEqual(self.call("household")["members"], expected["members"][:1])
        with self.connect() as conn:
            conn.execute("INSERT INTO user_bindings VALUES (720,701)")
        self.assertIn("多个人类", self.call("household", error=True))
        self.backend.assert_not_called()

    def test_public_discovery_then_two_ai_games_zero_and_one_chip(self):
        for stake in (0, 1):
            with self.subTest(stake=stake):
                family = self.call("household", bearer=True)
                caller = family["self_id"]
                other = next(m["id"] for m in family["members"] if m["id"] != caller)
                data = self.call("new", {"game_type": "tictactoe", "include_human": False,
                    "ai_players": [other], "stake": stake}, uid=caller)
                room_id = data.get("room", data)["room_id"]
                self.assertEqual(data["status"], "pending" if stake else "playing")
                if stake:
                    self.call("move", {"room_id": room_id, "revision": 0, "move": {"row": 0, "col": 0}}, error=True)
                    data = self.call("accept", {"room_id": room_id}, uid=other, bearer=True)
                    self.assertEqual(data["status"], "playing")
                for i, (row, col) in enumerate(((0, 0), (1, 0), (0, 1), (1, 1), (0, 2))):
                    uid = caller if i % 2 == 0 else other
                    state = self.call("state", {"room_id": room_id, "full_state": True}, uid=uid)
                    data = self.call("move", {"room_id": room_id, "revision": state["snapshot"]["revision"],
                        "move": {"row": row, "col": col}}, uid=uid)
                self.assertEqual(data["status"], "finished")

    def test_public_self_only_discovery_then_npc_games_zero_and_one_chip(self):
        for stake in (0, 1):
            with self.subTest(stake=stake):
                family = self.call("household", uid="705")
                self.assertEqual(len(family["members"]), 1)
                caller = family["self_id"]
                data = self.call("new", {"game_type": "bomb_plane", "include_human": False,
                    "target_player_count": 2, "fill_with_npcs": True, "stake": stake}, uid=caller)
                self.assertEqual(data["room"]["status"], "playing")
                self.assertEqual(data["room"]["pending_for"], [])
                if stake:
                    self.assertTrue(all(c["decision"] == "accepted" for c in data["room"]["confirmations"]))
                room_id = data.get("room", data)["room_id"]
                # NPC confirmation is automatic; no provider or AI automation runs.
                self.call("state", {"room_id": room_id, "full_state": True}, uid=caller)
                terminal = self.call("resign", {"room_id": room_id}, uid=caller)
                self.assertEqual(terminal["status"], "finished")
