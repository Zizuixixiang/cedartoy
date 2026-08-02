import sqlite3
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class AccountRenameTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-rename-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.db_patch.start()
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys = ON;
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_ai INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    deleted_at TEXT
                );
                CREATE TABLE user_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    human_user_id INTEGER NOT NULL REFERENCES toy_users(id),
                    ai_user_id INTEGER NOT NULL REFERENCES toy_users(id),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (human_user_id, ai_user_id),
                    CHECK (human_user_id <> ai_user_id)
                );
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    user_id INTEGER REFERENCES toy_users(id),
                    is_guest INTEGER DEFAULT 0,
                    is_ai INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    game_count INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    ask_count INTEGER DEFAULT 0,
                    ask_count_y INTEGER DEFAULT 0,
                    ask_count_n INTEGER DEFAULT 0,
                    ask_count_u INTEGER DEFAULT 0,
                    ask_count_p INTEGER DEFAULT 0
                );
                """
            )
            server._init_username_changes_table(conn)
            # The migration is intentionally safe to run more than once.
            server._init_username_changes_table(conn)

        self.password = "rename-pass"
        self.human_id = self._add_user("HumanOne", is_ai=False, with_player=True)
        self.machine_id = self._add_user("MachineOne", is_ai=True, with_player=True)
        self.other_machine_id = self._add_user("OtherBot", is_ai=True)
        self.other_human_id = self._add_user("OtherHuman", is_ai=False)
        self.conflict_id = self._add_user("ExistingName", is_ai=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (self.human_id, self.machine_id),
            )
        self.human_token = self._token_for(self.human_id)
        self.machine_token = self._token_for(self.machine_id)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _add_user(self, username, *, is_ai, with_player=False):
        password_hash = server._hash_password(self.password)
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, ?)",
                (username, password_hash, 1 if is_ai else 0),
            )
            user_id = cur.lastrowid
            if with_player:
                conn.execute(
                    """
                    INSERT INTO players
                        (username, user_id, is_ai, game_count, win_count, ask_count)
                    VALUES (?, ?, ?, 7, 3, 11)
                    """,
                    (username, user_id, 1 if is_ai else 0),
                )
        return user_id

    def _token_for(self, user_id):
        with self._connect() as conn:
            user = dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        return server._create_account_token(user)

    def test_rename_self_preserves_identity_token_stats_and_login(self):
        result = server._rename_self(self.human_token, "HumanTwo")
        self.assertTrue(result["renamed"])
        self.assertEqual(result["previous_username"], "HumanOne")
        self.assertEqual(result["user"]["id"], self.human_id)
        self.assertEqual(result["user"]["username"], "HumanTwo")
        self.assertEqual(server._current_account(self.human_token)["username"], "HumanTwo")

        logged_in = server._login_existing_account("HumanTwo", self.password)
        self.assertEqual(logged_in["user"]["id"], self.human_id)
        with self.assertRaises(server._McpError):
            server._login_existing_account("HumanOne", self.password)
        with self.assertRaises(server._McpError):
            server._login_or_register_human("HumanOne", self.password, client_ip="127.0.0.1")

        with self._connect() as conn:
            player = conn.execute(
                "SELECT username, user_id, game_count FROM players WHERE user_id = ?",
                (self.human_id,),
            ).fetchone()
            binding = conn.execute(
                "SELECT human_user_id, ai_user_id FROM user_bindings WHERE human_user_id = ?",
                (self.human_id,),
            ).fetchone()
            user = dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (self.human_id,)).fetchone())
            stats = server._turtle_soup_stats(conn, user)
        self.assertEqual(dict(player), {"username": "HumanTwo", "user_id": self.human_id, "game_count": 7})
        self.assertEqual(tuple(binding), (self.human_id, self.machine_id))
        self.assertEqual(stats["game_count"], 7)
        self.assertIn("HumanOne", server._account_username_aliases(user))

    def test_bound_machine_rename_preserves_user_id_and_binding(self):
        result = server._rename_bound_machine(self.human_token, self.machine_id, "MachineTwo")
        self.assertTrue(result["renamed"])
        self.assertEqual(result["user"]["id"], self.machine_id)
        self.assertEqual(server._current_account(self.machine_token)["username"], "MachineTwo")
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT b.human_user_id, b.ai_user_id, ai.username, p.user_id, p.game_count
                FROM user_bindings b
                JOIN toy_users ai ON ai.id = b.ai_user_id
                JOIN players p ON p.user_id = ai.id
                WHERE b.human_user_id = ? AND b.ai_user_id = ?
                """,
                (self.human_id, self.machine_id),
            ).fetchone()
        self.assertEqual(tuple(row), (self.human_id, self.machine_id, "MachineTwo", self.machine_id, 7))

    def test_ai_can_rename_self_through_mcp_path_token(self):
        result = json.loads(server._tool_account(
            {"action": "rename_self", "new_username": "MachineSelf"},
            path_token=self.machine_token,
        ))
        self.assertEqual(result["user"]["id"], self.machine_id)
        self.assertEqual(result["user"]["username"], "MachineSelf")

    def test_rest_handler_returns_success_and_structured_cooldown(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {"Authorization": f"Bearer {self.human_token}"}
        sent = []
        handler._send_json = lambda payload, status=200, **_kwargs: sent.append((status, payload))
        handler._read_json_body = lambda: {
            "action": "rename_self",
            "new_username": "RestHuman",
        }
        handler._handle_api_rename()
        self.assertEqual(sent[-1][0], 200)
        self.assertEqual(sent[-1][1]["user"]["username"], "RestHuman")

        handler._read_json_body = lambda: {
            "action": "rename_self",
            "new_username": "RestAgain",
        }
        handler._handle_api_rename()
        self.assertEqual(sent[-1][0], 429)
        self.assertEqual(sent[-1][1]["reason"], "rename_cooldown")
        self.assertGreater(sent[-1][1]["remaining_seconds"], 0)

    def test_registration_rejects_trimmed_exact_duplicate_but_allows_other_case(self):
        with self.assertRaises(server._McpError) as raised:
            server._login_or_register_ai(
                "  ExistingName  ",
                self.password,
                client_ip=self._testMethodName,
            )
        self.assertEqual(raised.exception.message, "用户名已存在")

        result = server._login_or_register_ai(
            "existingname",
            self.password,
            client_ip=self._testMethodName,
        )
        self.assertNotEqual(result["user"]["id"], self.conflict_id)
        self.assertEqual(result["user"]["username"], "existingname")

        upper_login = server._login_existing_account("ExistingName", self.password)
        lower_login = server._login_existing_account("existingname", self.password)
        self.assertEqual(upper_login["user"]["id"], self.conflict_id)
        self.assertEqual(lower_login["user"]["id"], result["user"]["id"])
        with self.assertRaises(server._McpError):
            server._login_existing_account("EXISTINGNAME", self.password)

    def test_rename_rejects_exact_duplicate_but_allows_other_case(self):
        with self.assertRaises(server._McpError) as raised:
            server._rename_self(self.human_token, "ExistingName")
        self.assertEqual(raised.exception.message, "用户名已存在")
        self.assertEqual(raised.exception.details.get("reason"), "username_conflict")
        self.assertEqual(server._current_account(self.human_token)["username"], "HumanOne")

        result = server._rename_self(self.human_token, "existingname")
        self.assertTrue(result["renamed"])
        self.assertEqual(result["user"]["username"], "existingname")

    def test_case_only_rename_succeeds_and_starts_cooldown(self):
        result = server._rename_self(self.human_token, "humanone")
        self.assertTrue(result["renamed"])
        self.assertEqual(result["previous_username"], "HumanOne")
        self.assertEqual(result["user"]["username"], "humanone")

        logged_in = server._login_existing_account("humanone", self.password)
        self.assertEqual(logged_in["user"]["id"], self.human_id)
        with self.assertRaises(server._McpError):
            server._login_existing_account("HumanOne", self.password)
        with self.assertRaises(server._McpError) as raised:
            server._rename_self(self.human_token, "HumanOne")
        self.assertEqual(raised.exception.details.get("reason"), "rename_cooldown")

        with self._connect() as conn:
            change = conn.execute(
                """
                SELECT old_username, new_username
                FROM account_username_changes
                WHERE user_id = ?
                """,
                (self.human_id,),
            ).fetchone()
        self.assertEqual(tuple(change), ("HumanOne", "humanone"))

    def test_historical_name_reservation_is_case_sensitive(self):
        server._rename_self(self.human_token, "HumanTwo")
        with self.assertRaises(server._McpError) as raised:
            server._login_or_register_ai(
                "HumanOne",
                self.password,
                client_ip=self._testMethodName,
            )
        self.assertEqual(raised.exception.message, "用户名已存在")

        result = server._login_or_register_ai(
            "humanone",
            self.password,
            client_ip=self._testMethodName,
        )
        self.assertEqual(result["user"]["username"], "humanone")

    def test_username_indexes_use_default_binary_semantics(self):
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT name, sql
                FROM sqlite_master
                WHERE type = 'index'
                  AND tbl_name IN ('toy_users', 'account_username_changes')
                """
            ).fetchall()
        index_sql = "\n".join((row["sql"] or "") for row in rows)
        self.assertNotIn("NOCASE", index_sql.upper())
        self.assertIn("idx_account_username_changes_old_username", {row["name"] for row in rows})

    def test_second_rename_inside_72_hours_returns_cooldown_details(self):
        server._rename_self(self.human_token, "HumanTwo")
        with self.assertRaises(server._McpError) as raised:
            server._rename_self(self.human_token, "HumanThree")
        self.assertEqual(raised.exception.details.get("reason"), "rename_cooldown")
        self.assertGreater(raised.exception.details.get("remaining_seconds", 0), 0)
        self.assertTrue(raised.exception.details.get("next_allowed_at"))
        self.assertEqual(server._current_account(self.human_token)["username"], "HumanTwo")

    def test_unbound_machine_and_wrong_account_types_fail(self):
        with self.assertRaises(server._McpError):
            server._rename_bound_machine(self.human_token, self.other_machine_id, "NoAccess")
        with self.assertRaises(server._McpError):
            server._rename_bound_machine(self.machine_token, self.machine_id, "NoAccess")

        # Even a malformed legacy binding row cannot turn a human into a machine target.
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (self.human_id, self.other_human_id),
            )
        with self.assertRaises(server._McpError) as raised:
            server._rename_bound_machine(self.human_token, self.other_human_id, "StillHuman")
        self.assertIn("不是小机", raised.exception.message)


if __name__ == "__main__":
    unittest.main(verbosity=2)
