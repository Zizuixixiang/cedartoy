import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_deletion
import server


class AccountAvatarTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-avatar-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.secret_patch = patch.object(server, "TOY_SECRET", "avatar-test-secret")
        self.db_patch.start()
        self.secret_patch.start()
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys=ON;
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
                    UNIQUE(human_user_id, ai_user_id)
                );
                CREATE TABLE binding_tokens (
                    token TEXT PRIMARY KEY,
                    ai_user_id INTEGER,
                    expires_at TEXT,
                    used INTEGER DEFAULT 0
                );
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE,
                    user_id INTEGER,
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
            server._init_registration_events_table(conn)
            server._init_username_changes_table(conn)
            server._init_account_security_schema(conn)
            server._init_account_security_schema(conn)
            account_deletion.init_schema(conn)

    def tearDown(self):
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        self.secret_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _user(self, user_id):
        with self._connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ?", (int(user_id),)
            ).fetchone())

    def test_migration_is_idempotent_and_future_extensible(self):
        server._migrate_platform_timestamps()
        server._migrate_platform_timestamps()
        with self._connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(toy_users)")}
        self.assertIn("avatar_type", columns)
        self.assertIn("avatar_value", columns)

    def test_old_accounts_and_old_registration_calls_get_safe_defaults(self):
        with self._connect() as conn:
            human_id = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, 0)",
                ("LegacyHuman", server._hash_password("secret-pass")),
            ).lastrowid
            ai_id = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, 1)",
                ("LegacyBot", server._hash_password("secret-pass")),
            ).lastrowid
        self.assertEqual(server._public_user(self._user(human_id))["avatar"]["value"], "🙂")
        self.assertEqual(server._public_user(self._user(ai_id))["avatar"]["value"], "🤖")

        human = server._register_human("NoAvatarHuman", "secret-pass", client_ip="1.1.1.1")
        machine = server._login_or_register_ai("NoAvatarBot", "secret-pass", client_ip="2.2.2.2")
        self.assertEqual(human["user"]["avatar"]["value"], "🙂")
        self.assertEqual(machine["user"]["avatar"]["value"], "🤖")

    def test_registration_and_set_avatar_validate_emoji(self):
        human = server._register_human(
            "AvatarHuman",
            "secret-pass",
            client_ip="3.3.3.2",
            avatar="🌿",
        )
        self.assertEqual(human["user"]["avatar"]["value"], "🌿")

        registered = server._login_or_register_ai(
            "AvatarBot",
            "secret-pass",
            client_ip="3.3.3.3",
            avatar="🧑🏽‍💻",
        )
        self.assertEqual(registered["user"]["avatar"]["value"], "🧑🏽‍💻")

        updated = json.loads(server._tool_account(
            {"action": "set_avatar", "avatar": "🐢🌲"},
            path_token=registered["token"],
        ))
        self.assertEqual(updated["user"]["avatar"], {
            "type": "emoji",
            "value": "🐢🌲",
            "is_default": False,
        })
        profile = json.loads(server._tool_account(
            {"action": "get_profile"},
            path_token=registered["token"],
        ))
        self.assertEqual(profile["avatar"]["value"], "🐢🌲")

        rpc_response = server._handle_root_mcp(
            {
                "jsonrpc": "2.0",
                "id": "avatar-bearer",
                "method": "tools/call",
                "params": {
                    "name": "account",
                    "arguments": {"action": "set_avatar", "avatar": "🌙"},
                },
            },
            bearer_token=registered["token"],
        )
        rpc_text = rpc_response["result"]["content"][0]["text"]
        self.assertFalse(rpc_response["result"]["isError"])
        self.assertEqual(json.loads(rpc_text)["user"]["avatar"]["value"], "🌙")

        for invalid in ("hello", "https://example.com/avatar.png", "────", "", "😀" * 17):
            with self.subTest(invalid=invalid):
                with self.assertRaises(server._McpError):
                    server._tool_account(
                        {"action": "set_avatar", "avatar": invalid},
                        path_token=registered["token"],
                    )

        with self.assertRaises(server._McpError):
            server._login_or_register_ai(
                "TextAvatarBot",
                "secret-pass",
                client_ip="3.3.3.4",
                avatar="普通文字",
            )

    def test_account_schema_and_guide_document_optional_avatar(self):
        account_tool = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "account")
        schema = account_tool["inputSchema"]["properties"]
        self.assertEqual(schema["avatar"]["maxLength"], 16)
        self.assertNotIn("avatar", account_tool["inputSchema"].get("required", []))
        self.assertIn("set_avatar", schema["action"]["description"])

        guide = json.loads(server._tool_get_guide({"game": "account"}))["guide"]
        self.assertIn("set_avatar", guide)
        self.assertIn("为空默认🤖", guide)
        self.assertIn("get_profile", guide)


if __name__ == "__main__":
    unittest.main()
