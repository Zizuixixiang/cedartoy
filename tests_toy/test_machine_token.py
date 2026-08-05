import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class MachineTokenTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-machine-token-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.db_patch.start()
        with self._connect() as conn:
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
                CREATE TABLE binding_tokens (
                    token TEXT NOT NULL,
                    ai_user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0
                );
                """
            )
        self.password = "machine-pass"
        self.machine_id = self._add_user("MachineOne", is_ai=True)
        self.human_id = self._add_user("HumanOne", is_ai=False)
        self.other_human_id = self._add_user("OtherHuman", is_ai=False)
        self.human_token = self._token_for(self.human_id)
        self.machine_token = self._token_for(self.machine_id)

    def tearDown(self):
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _add_user(self, username, *, is_ai):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, ?)",
                (username, server._hash_password(self.password), 1 if is_ai else 0),
            )
            return cur.lastrowid

    def _token_for(self, user_id):
        with self._connect() as conn:
            user = dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        return server._create_account_token(user)

    def _error(self, username, password, **kwargs):
        with self.assertRaises(server._McpError) as raised:
            server._machine_account_token(username, password, **kwargs)
        return raised.exception

    def test_correct_ai_credentials_return_existing_ai_token(self):
        result = server._machine_account_token("MachineOne", self.password)

        self.assertEqual(result["user"]["id"], self.machine_id)
        self.assertTrue(result["user"]["is_ai"])
        self.assertNotIn("password_hash", result["user"])
        payload = server._jwt_decode(result["token"])
        self.assertEqual(payload["user_id"], self.machine_id)
        self.assertTrue(payload["is_ai"])
        self.assertNotIn("exp", payload)

    def test_wrong_credentials_are_rejected_without_registering(self):
        error = self._error("MachineOne", "wrong-pass")
        self.assertEqual(error.code, -32001)
        self.assertEqual(error.message, "用户名或密码错误")

        missing = self._error("MissingBot", self.password)
        self.assertEqual(missing.message, "用户名或密码错误")
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM toy_users").fetchone()[0], 3)

    def test_human_credentials_are_rejected(self):
        error = self._error("HumanOne", self.password)
        self.assertEqual(error.message, "该账号不是小机账号")

    def test_bind_requires_logged_in_human(self):
        error = self._error("MachineOne", self.password, bind=True)
        self.assertEqual(error.code, -32001)
        self.assertIn("请先登录人类账号", error.message)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM user_bindings").fetchone()[0], 0)

    def test_bind_with_human_login_succeeds_and_is_idempotent(self):
        first = server._machine_account_token(
            "MachineOne", self.password, bind=True, human_token=self.human_token
        )
        second = server._machine_account_token(
            "MachineOne", self.password, bind=True, human_token=self.human_token
        )

        self.assertTrue(first["bound"])
        self.assertTrue(second["bound"])
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT human_user_id, ai_user_id FROM user_bindings"
            ).fetchall()
        self.assertEqual([tuple(row) for row in rows], [(self.human_id, self.machine_id)])

    def test_bind_rejects_ai_login(self):
        error = self._error(
            "MachineOne", self.password, bind=True, human_token=self.machine_token
        )
        self.assertIn("只有人类账号可以绑定", error.message)

    def test_bind_rejects_machine_owned_by_another_human(self):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (self.other_human_id, self.machine_id),
            )

        error = self._error(
            "MachineOne", self.password, bind=True, human_token=self.human_token
        )
        self.assertIn("已被其他人类账号绑定", error.message)

    def test_existing_binding_token_flow_still_works(self):
        binding_token = "b" * 32
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO binding_tokens (token, ai_user_id, expires_at, used)
                VALUES (?, ?, '2999-01-01 00:00:00', 0)
                """,
                (binding_token, self.machine_id),
            )

        self.assertEqual(server._bind_account(self.human_token, binding_token), {"ok": True})
        with self._connect() as conn:
            binding = conn.execute(
                "SELECT human_user_id, ai_user_id FROM user_bindings"
            ).fetchone()
            used = conn.execute(
                "SELECT used FROM binding_tokens WHERE token = ?", (binding_token,)
            ).fetchone()[0]
        self.assertEqual(tuple(binding), (self.human_id, self.machine_id))
        self.assertEqual(used, 1)

    def test_rest_handler_returns_token_and_does_not_echo_password(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {}
        handler._read_json_body = lambda: {
            "username": "MachineOne",
            "password": self.password,
        }
        sent = []
        handler._send_json = lambda payload, status=200, **_kwargs: sent.append((status, payload))

        handler._handle_api_machine_token()

        self.assertEqual(sent[-1][0], 200)
        self.assertEqual(sent[-1][1]["user"]["id"], self.machine_id)
        self.assertNotIn(self.password, repr(sent[-1][1]))

    def test_post_route_dispatches_to_machine_token_handler(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/api/auth/machine-token"
        handler.headers = {}
        handler.client_address = ("127.0.0.1", 12345)
        handler._is_soup_path = lambda: False
        dispatched = []
        handler._handle_api_machine_token = lambda: dispatched.append(True)

        handler.do_POST()

        self.assertEqual(dispatched, [True])

    def test_web_entry_has_optional_binding_full_token_and_copy_controls(self):
        html = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn('id="machineTokenFromLogin"', html)
        self.assertIn('id="mineMachineToken"', html)
        self.assertIn('id="machineTokenBindField" hidden', html)
        self.assertIn('id="machineTokenValue"', html)
        self.assertIn('id="machineTokenCopy"', html)
        self.assertIn('fetch("/api/auth/machine-token"', html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
