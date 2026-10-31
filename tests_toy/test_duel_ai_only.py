"""Authenticated ordinary AI rooms; all account data is disposable."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DuelAiOnlyPlatformTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="duel-ai-accounts-")
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "accounts.db"
        with self.connect() as conn:
            conn.executescript("""
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT, is_ai INTEGER, deleted_at TEXT);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users VALUES
                  (710,'主人',0,NULL),(720,'别人',0,NULL),(701,'甲',1,NULL),
                  (702,'乙',1,NULL),(703,'别家',1,NULL),(704,'已注销',1,'deleted'),
                  (705,'未绑定',1,NULL);
                INSERT INTO user_bindings VALUES (710,701),(710,702),(720,703),(710,704);
            """)
        self.account = {"id": 701, "username": "甲", "is_ai": 1}
        patches = {
            "_db_connect": {"side_effect": self.connect},
            "_current_account": {"side_effect": lambda _: self.account},
            "_auto_migrate_legacy_account_saves": {},
            "_anti_addiction_context": {"return_value": None},
            "_stamp_save_owner": {},
            "_anti_addiction_record_success": {"return_value": ""},
            "_play_announcements": {"return_value": ""},
            "_feedback_unread_reminder": {"return_value": ""},
            "_request_duel_backend": {"return_value": {"ok": True}},
        }
        for name, kwargs in patches.items():
            p = patch.object(server, name, **kwargs)
            mock = p.start()
            self.addCleanup(p.stop)
            if name == "_request_duel_backend":
                self.backend = mock

    def connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def call(self, params, action="new", defer=False, token="authenticated"):
        return server._tool_play_inner(
            {"game": "duel", "action": action, "params": params},
            path_token=token, defer_duel=defer,
        )

    def test_roster_and_names_come_from_binding_not_reported_identity(self):
        self.call({"game_type": "uno", "include_human": False, "ai_players": ["702:3"],
                   "slot": 2, "player_id": "703", "opponent_id": "720",
                   "participant_ids": ["703"], "household_ais": {"703": "伪造"},
                   "display_name": "冒名"})
        payload = self.backend.call_args.args[0]
        self.assertEqual(payload["player_id"], "701:2")
        self.assertEqual(payload["participant_ids"], ["701:2", "702:3"])
        self.assertEqual(payload["household_ais"], {"701": "甲", "702": "乙"})
        self.assertNotIn("display_name", payload)
        self.assertIs(payload["include_human"], False)
        self.assertNotIn("opponent_id", payload)
        self.assertNotIn("ai_players", payload)

    def test_rejects_foreign_deleted_duplicate_and_invalid_selections(self):
        for selected in (["703"], ["704"], ["710"], ["701"], ["701:3"],
                         ["702", "702:2"], ["702:6"], [702], "702", None,
                         ["npc:fake"], ["702"] * 6):
            with self.subTest(selected=selected), self.assertRaises(server._McpError):
                self.call({"game_type": "uno", "include_human": False, "ai_players": selected})
        self.backend.assert_not_called()

    def test_rechecks_live_binding_on_each_new_and_rematch(self):
        self.call({"game_type": "uno", "include_human": False, "ai_players": ["702"]})
        with self.connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE ai_user_id=702")
        with self.assertRaises(server._McpError):
            self.call({"game_type": "uno", "include_human": False, "ai_players": ["702"]})
        self.call({"room_id": "ABCDEFGH", "household_ais": {"702": "伪造"}}, "rematch")
        self.assertEqual(self.backend.call_args.args[0]["household_ais"], {"701": "甲"})

    def test_deleted_human_cannot_authorize_siblings_and_multiple_bindings_fail(self):
        with self.connect() as conn:
            conn.execute("UPDATE toy_users SET deleted_at='deleted' WHERE id=710")
        self.assertEqual(server._duel_household_ai_names(self.account), {"701": "甲"})
        with self.connect() as conn:
            conn.execute("UPDATE toy_users SET deleted_at=NULL WHERE id=710")
            conn.execute("INSERT INTO user_bindings VALUES (720,701)")
        with self.assertRaises(server._McpError):
            self.call({"game_type": "uno", "include_human": False})

    def test_unbound_caller_can_request_npcs_but_not_other_accounts(self):
        self.account = {"id": 705, "username": "未绑定", "is_ai": 1}
        self.call({"game_type": "uno", "include_human": False,
                   "target_player_count": 2, "fill_with_npcs": True})
        payload = self.backend.call_args.args[0]
        self.assertEqual(payload["participant_ids"], ["705"])
        self.assertEqual(payload["household_ais"], {"705": "未绑定"})
        with self.assertRaises(server._McpError):
            self.call({"game_type": "uno", "include_human": False, "ai_players": ["702"]})

    def test_default_and_explicit_true_keep_human_pair(self):
        for extra in ({}, {"include_human": True}):
            self.call({"game_type": "tictactoe", "opponent_id": "720", **extra})
            self.assertEqual(self.backend.call_args.args[0], {
                "action": "new", "game_type": "tictactoe", "player_id": "701",
                "opponent_id": "710",
            })

    def test_explicit_boolean_and_action_scope(self):
        for value in (0, 1, "false", None):
            with self.subTest(value=value), self.assertRaises(server._McpError):
                self.call({"game_type": "uno", "include_human": value})
        with self.assertRaises(server._McpError):
            self.call({"game_type": "uno", "ai_players": ["702"]})
        with self.assertRaises(server._McpError):
            self.call({"room_id": "ABCDEFGH", "include_human": False}, "state")

    def test_invitation_semantics_still_only_seat_caller(self):
        with patch.object(server, "_duel_household_ai_names", side_effect=AssertionError("unused")):
            for action, params in (("invite", {"game_type": "uno", "target_player_count": 4}),
                                   ("join", {"invite_code": "ABCDEF123456"})):
                self.call({**params, "ai_players": ["703"], "participant_ids": ["703"],
                           "opponent_id": "720", "household_ais": {"703": "假"}}, action)
                self.assertEqual(self.backend.call_args.args[0], {
                    "action": action, "player_id": "701", "display_name": "甲", **params,
                })

    def test_deferred_gateway_uses_same_trusted_roster(self):
        params = {"game_type": "uno", "include_human": False, "ai_players": ["702"]}
        self.call(params)
        synchronous = self.backend.call_args.args[0]
        result = self.call(params, defer=True)
        self.assertEqual(result.backend_payload, synchronous)

    def test_human_and_guest_cannot_use_ai_only_new(self):
        self.account = {"id": 710, "username": "主人", "is_ai": 0}
        for token in ("authenticated", None):
            with self.subTest(token=token), self.assertRaises(server._McpError):
                self.call({"game_type": "uno", "include_human": False}, token=token)
        self.backend.assert_not_called()

    def test_path_and_bearer_auth_reach_canonical_roster(self):
        arguments = {"game": "duel", "action": "new", "params": {
            "game_type": "uno", "include_human": False, "ai_players": ["702"], "player_id": "703"}}
        request = {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                   "params": {"name": "play", "arguments": arguments}}
        for auth in ({"path_token": "path"}, {"bearer_token": "bearer"}):
            with self.subTest(auth=auth), \
                    patch.object(server, "_authenticated_ai_player_id", return_value="701"), \
                    patch.object(server, "_mcp_forced_announcement", return_value=""):
                response = server._handle_root_mcp(request, **auth)
                self.assertNotIn("error", response, json.dumps(response))
                self.assertEqual(self.backend.call_args.args[0]["participant_ids"], ["701", "702"])
