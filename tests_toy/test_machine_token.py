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
            server._init_account_security_schema(conn)
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

    def _issue_parallel_machine_token(self):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            user = dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ?", (self.machine_id,)
            ).fetchone())
            token = server._issue_ai_token_in_transaction(conn, user)
            conn.commit()
        return token

    def _active_machine_token_count(self):
        with self._connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM ai_access_tokens WHERE user_id = ? AND revoked_at_epoch IS NULL",
                (self.machine_id,),
            ).fetchone()[0]

    def _bind_machine(self, human_id=None):
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (human_id or self.human_id, self.machine_id),
            )

    def test_manual_credentials_replace_existing_ai_token(self):
        old_token = self.machine_token
        result = server._machine_account_token("MachineOne", self.password)

        self.assertEqual(result["user"]["id"], self.machine_id)
        self.assertTrue(result["user"]["is_ai"])
        self.assertNotIn("password_hash", result["user"])
        self.assertTrue(result["token"].startswith(server.AI_OPAQUE_TOKEN_PREFIX))
        self.assertNotEqual(result["token"], old_token)
        with self.assertRaises(server._McpError):
            server._current_account(old_token)
        self.assertEqual(server._current_account(result["token"])["id"], self.machine_id)
        self.assertEqual(self._active_machine_token_count(), 1)

    def test_wrong_credentials_are_rejected_without_registering(self):
        error = self._error("MachineOne", "wrong-pass")
        self.assertEqual(error.code, -32001)
        self.assertEqual(error.message, "用户名或密码错误")

        missing = self._error("MissingBot", self.password)
        self.assertEqual(missing.message, "用户名或密码错误")
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM toy_users").fetchone()[0], 3)
        self.assertEqual(server._current_account(self.machine_token)["id"], self.machine_id)

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

    def test_bound_get_rejects_missing_machine_password_without_revoking(self):
        self._bind_machine()

        error = self._error(
            "",
            "",
            ai_user_id=self.machine_id,
            human_token=self.human_token,
        )

        self.assertEqual(error.code, -32602)
        self.assertEqual(error.message, "小机密码必填")
        self.assertEqual(server._current_account(self.machine_token)["id"], self.machine_id)

    def test_bound_get_rejects_wrong_machine_password_without_revoking(self):
        self._bind_machine()

        error = self._error(
            "",
            "wrong-pass",
            ai_user_id=self.machine_id,
            human_token=self.human_token,
            client_ip="127.0.0.1",
        )

        self.assertEqual(error.code, -32001)
        self.assertEqual(error.message, "用户名或密码错误")
        self.assertEqual(server._current_account(self.machine_token)["id"], self.machine_id)

    def test_bound_get_twice_replaces_previous_and_keeps_one_active_token(self):
        self._bind_machine()

        first = server._machine_account_token(
            "",
            self.password,
            ai_user_id=self.machine_id,
            human_token=self.human_token,
            client_ip="127.0.0.1",
        )
        second = server._machine_account_token(
            "",
            self.password,
            ai_user_id=self.machine_id,
            human_token=self.human_token,
            client_ip="127.0.0.1",
        )

        self.assertTrue(first["rotated"])
        self.assertTrue(second["rotated"])
        self.assertNotEqual(first["token"], second["token"])
        with self.assertRaises(server._McpError):
            server._current_account(self.machine_token)
        with self.assertRaises(server._McpError):
            server._current_account(first["token"])
        self.assertEqual(server._current_account(second["token"])["id"], self.machine_id)
        self.assertEqual(self._active_machine_token_count(), 1)

    def test_bound_get_rejects_machine_not_owned_by_human(self):
        self._bind_machine(self.other_human_id)

        error = self._error(
            "",
            self.password,
            ai_user_id=self.machine_id,
            human_token=self.human_token,
        )

        self.assertEqual(error.message, "该小机未绑定到你的账号")
        self.assertEqual(server._current_account(self.machine_token)["id"], self.machine_id)

    def test_manual_get_revokes_all_legacy_parallel_tokens(self):
        legacy_tokens = [self.machine_token]
        legacy_tokens.extend(self._issue_parallel_machine_token() for _ in range(4))

        result = server._machine_account_token("MachineOne", self.password)

        for old_token in legacy_tokens:
            with self.assertRaises(server._McpError):
                server._current_account(old_token)
        self.assertEqual(server._current_account(result["token"])["id"], self.machine_id)
        self.assertEqual(self._active_machine_token_count(), 1)

    def test_manual_get_issue_failure_rolls_back_existing_token(self):
        old_token = self.machine_token

        with patch.object(
            server,
            "_issue_ai_token_in_transaction",
            side_effect=RuntimeError("simulated token allocation failure"),
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated token allocation failure"):
                server._machine_account_token("MachineOne", self.password)

        self.assertEqual(server._current_account(old_token)["id"], self.machine_id)
        self.assertEqual(self._active_machine_token_count(), 1)

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
        handler.client_address = ("127.0.0.1", 12345)
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
        with self.assertRaises(server._McpError):
            server._current_account(self.machine_token)

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

    def test_web_mine_entry_has_optional_binding_full_token_and_copy_controls(self):
        html = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn('id="mineMachineToken"', html)
        self.assertIn('id="machineTokenBindField" hidden', html)
        self.assertIn('id="machineTokenBoundField" hidden', html)
        self.assertIn('id="machineTokenUrlValue"', html)
        self.assertIn('id="machineTokenUrlCopy"', html)
        self.assertIn('id="machineTokenValue"', html)
        self.assertIn('id="machineTokenCopy"', html)
        self.assertIn('fetch("/api/auth/machine-token"', html)
        self.assertNotIn('id="machineTokenModeRotate"', html)
        self.assertIn("获取新 Token 后，该小机此前所有旧 Token 都会失效，请使用新 Token 重新设置 MCP 地址。", html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
