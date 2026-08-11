import contextlib
import asyncio
import io
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_deletion
import server


class AccountSecurityRoundFourTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-account-round4-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.sessions_path = Path(self.temp_dir.name) / "sessions.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.sessions_patch = patch.object(server, "SESSIONS_DB_PATH", self.sessions_path)
        self.secret_patch = patch.object(server, "TOY_SECRET", "round-four-test-secret")
        self.db_patch.start()
        self.sessions_patch.start()
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
            server._init_password_reset_tokens_table(conn)
            server._init_account_security_schema(conn)
            account_deletion.init_schema(conn)

    def tearDown(self):
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        self.secret_patch.stop()
        self.sessions_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _add_user(self, username, *, is_ai=False, password="secret-pass"):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO toy_users (username,password_hash,is_ai) VALUES (?,?,?)",
                (username, server._hash_password(password), 1 if is_ai else 0),
            )
            return int(cur.lastrowid)

    def _user(self, user_id):
        with self._connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM toy_users WHERE id=?", (int(user_id),)
            ).fetchone())

    def _opaque(self, user_id):
        return server._create_account_token(self._user(user_id))

    def _legacy(self, user_id):
        return server._create_account_jwt(self._user(user_id))

    def _assert_rejected(self, token):
        with self.assertRaises(server._McpError):
            server._current_account(token)

    def test_opaque_tokens_are_random_and_database_stores_only_hash(self):
        user_id = self._add_user("HashBot", is_ai=True)
        first = self._opaque(user_id)
        second = self._opaque(user_id)

        self.assertNotEqual(first, second)
        self.assertTrue(first.startswith("ctai_v1_"))
        self.assertEqual(len(first.removeprefix("ctai_v1_")), 43)
        with self._connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(ai_access_tokens)")}
            rows = [dict(row) for row in conn.execute("SELECT * FROM ai_access_tokens ORDER BY created_at_epoch")]
        self.assertNotIn("token", columns)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["token_hash"], server._opaque_ai_token_hash(first))
        self.assertNotIn(first, repr(rows))
        self.assertNotIn(second, repr(rows))

    def test_path_and_bearer_authenticate_opaque_token(self):
        user_id = self._add_user("DualChannelBot", is_ai=True)
        token = self._opaque(user_id)
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "account", "arguments": {"action": "get_bindings"}},
        }
        by_path = server._handle_root_mcp(payload, path_token=token)
        by_bearer = server._handle_root_mcp(payload, bearer_token=token)
        for response in (by_path, by_bearer):
            self.assertFalse(response["result"]["isError"])
            self.assertEqual(json.loads(response["result"]["content"][0]["text"]), {"bindings": []})

    def test_legacy_flag_can_disable_ai_jwt_without_affecting_opaque_or_human(self):
        ai_id = self._add_user("LegacyFlagBot", is_ai=True)
        human_id = self._add_user("LegacyFlagHuman")
        opaque = self._opaque(ai_id)
        legacy = self._legacy(ai_id)
        human = self._legacy(human_id)

        with patch.object(server, "LEGACY_AI_JWT_COMPAT_ENABLED", True):
            self.assertEqual(server._current_account(legacy)["id"], ai_id)
        with patch.object(server, "LEGACY_AI_JWT_COMPAT_ENABLED", False):
            self._assert_rejected(legacy)
            self.assertEqual(server._current_account(opaque)["id"], ai_id)
            self.assertEqual(server._current_account(human)["id"], human_id)
        with patch.object(server, "TOY_SECRET", "an-unrelated-secret"):
            self.assertEqual(server._current_account(opaque)["id"], ai_id)

    def test_new_registration_login_and_web_fetch_issue_opaque_without_revoking_existing(self):
        registered = server._login_or_register_ai(
            "FreshBot", "secret-pass", client_ip="10.0.0.1"
        )
        first = registered["token"]
        logged_in = server._login_existing_account(
            "FreshBot", "secret-pass", client_ip="10.0.0.1"
        )
        web = server._machine_account_token("FreshBot", "secret-pass")
        for token in (first, logged_in["token"], web["token"]):
            self.assertTrue(token.startswith(server.AI_OPAQUE_TOKEN_PREFIX))
            self.assertEqual(server._current_account(token)["id"], registered["user"]["id"])
        self.assertEqual(len({first, logged_in["token"], web["token"]}), 3)
        with self._connect() as conn:
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM ai_access_tokens WHERE user_id=? AND revoked_at_epoch IS NULL",
                    (registered["user"]["id"],),
                ).fetchone()[0],
                3,
            )

    def test_active_token_limit_refuses_new_login_without_evicting_any_token(self):
        user_id = self._add_user("LimitBot", is_ai=True)
        tokens = [self._opaque(user_id) for _ in range(server.AI_OPAQUE_TOKEN_MAX_ACTIVE)]
        with self.assertRaises(server._McpError) as limited:
            server._login_existing_account("LimitBot", "secret-pass")
        self.assertEqual(limited.exception.details["reason"], "active_token_limit")
        for token in tokens:
            self.assertEqual(server._current_account(token)["id"], user_id)

    def test_rotate_revokes_all_opaque_and_legacy_then_returns_one_new_opaque(self):
        user_id = self._add_user("RotateAllBot", is_ai=True)
        first = self._opaque(user_id)
        second = self._opaque(user_id)
        legacy = self._legacy(user_id)

        rotated = server._rotate_ai_token(first)

        self._assert_rejected(first)
        self._assert_rejected(second)
        self._assert_rejected(legacy)
        self.assertEqual(server._current_account(rotated["token"])["id"], user_id)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT generation,revoked_at_epoch FROM ai_access_tokens WHERE user_id=?",
                (user_id,),
            ).fetchall()
        self.assertEqual(self._user(user_id)["ai_token_version"], 1)
        self.assertEqual(sum(row["revoked_at_epoch"] is None for row in rows), 1)
        self.assertEqual(sum(row["revoked_at_epoch"] is not None for row in rows), 2)

    def test_bound_human_rotate_keeps_experience_and_is_atomic(self):
        human_id = self._add_user("Owner", password="human-pass")
        ai_id = self._add_user("OwnedBot", is_ai=True, password="machine-pass")
        human_token = self._legacy(human_id)
        old_tokens = [self._opaque(ai_id), self._opaque(ai_id)]
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id,ai_user_id) VALUES (?,?)",
                (human_id, ai_id),
            )

        result = server._machine_account_token(
            "", "", rotate=True, ai_user_id=ai_id, human_token=human_token
        )
        self.assertTrue(result["rotated"])
        for token in old_tokens:
            self._assert_rejected(token)
        self.assertEqual(server._current_account(result["token"])["id"], ai_id)

    def test_password_reset_and_rename_do_not_invalidate_opaque(self):
        human_id = self._add_user("ResetOwner", password="human-pass")
        ai_id = self._add_user("StableBot", is_ai=True, password="machine-pass")
        human_token = self._legacy(human_id)
        opaque = self._opaque(ai_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id,ai_user_id) VALUES (?,?)",
                (human_id, ai_id),
            )

        server._reset_machine_password(human_token, ai_id, "new-machine-pass")
        server._rename_self(opaque, "StableBotRenamed")

        self.assertEqual(server._current_account(opaque)["username"], "StableBotRenamed")
        self.assertEqual(self._user(ai_id)["ai_token_version"], 0)

    def test_opaque_rechecks_is_ai_and_pending_deletion_like_legacy(self):
        ai_id = self._add_user("StateBot", is_ai=True)
        opaque = self._opaque(ai_id)
        legacy = self._legacy(ai_id)
        server._delete_account(opaque, True)
        for token in (opaque, legacy):
            with self.assertRaises(server._McpError) as pending:
                server._current_account(token)
            self.assertEqual(pending.exception.details["reason"], "pending_deletion")
            self.assertEqual(
                server._current_account(token, allow_pending_deletion=True)["id"], ai_id
            )
        server._cancel_account_deletion(opaque)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_ai=0 WHERE id=?", (ai_id,))
        self._assert_rejected(opaque)

    def test_profile_reports_migration_only_for_legacy_and_logs_redact_opaque(self):
        ai_id = self._add_user("ProfileBot", is_ai=True)
        opaque = self._opaque(ai_id)
        legacy = self._legacy(ai_id)
        opaque_profile = server._get_profile(opaque)
        legacy_profile = server._get_profile(legacy)
        self.assertEqual(opaque_profile["token_format"], "opaque_v1")
        self.assertFalse(opaque_profile["token_migration_recommended"])
        self.assertEqual(legacy_profile["token_format"], "legacy_jwt")
        self.assertTrue(legacy_profile["token_migration_recommended"])

        handler = object.__new__(server.CedarToyHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.log_date_time_string = lambda: "now"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            handler.log_message('"%s" %s %s', f"POST /{opaque} HTTP/1.1", "200", "12")
        self.assertNotIn(opaque, output.getvalue())
        self.assertIn("/<TOKEN_REDACTED>", output.getvalue())

    def test_legacy_soup_entry_uses_same_opaque_and_legacy_policy(self):
        backend_dir = str(Path(server.__file__).parent / "turtle-soup" / "backend")
        if backend_dir not in sys.path:
            sys.path.insert(0, backend_dir)
        try:
            soup_mcp = importlib.import_module("mcp_app")
        except ModuleNotFoundError as exc:
            self.skipTest(f"turtle-soup runtime dependency unavailable: {exc.name}")

        class AsyncCursor:
            def __init__(self, cursor):
                self.cursor = cursor
                self.lastrowid = cursor.lastrowid

            def __await__(self):
                async def ready():
                    return self
                return ready().__await__()

            async def __aenter__(self):
                return self

            async def __aexit__(self, _exc_type, _exc, _tb):
                self.cursor.close()

            async def fetchone(self):
                return self.cursor.fetchone()

        class AsyncDb:
            def __init__(self, path):
                self.conn = sqlite3.connect(path)
                self.conn.row_factory = sqlite3.Row
                self.conn.execute("PRAGMA foreign_keys=ON")

            def execute(self, sql, args=()):
                return AsyncCursor(self.conn.execute(sql, args))

            async def commit(self):
                self.conn.commit()

            async def rollback(self):
                self.conn.rollback()

            async def close(self):
                self.conn.close()

        ai_id = self._add_user("SoupCompatBot", is_ai=True)
        legacy = soup_mcp._jwt_encode({
            "user_id": ai_id,
            "username": "SoupCompatBot",
            "is_ai": True,
            "is_admin": False,
            "token_version": 0,
        })

        async def scenario():
            issue_db = AsyncDb(self.db_path)
            try:
                opaque = await soup_mcp._issue_ai_token(issue_db, self._user(ai_id))
                await issue_db.commit()
            finally:
                await issue_db.close()

            auth_db = AsyncDb(self.db_path)
            try:
                self.assertEqual((await soup_mcp._account_user(auth_db, opaque))["id"], ai_id)
                with patch.object(soup_mcp, "LEGACY_AI_JWT_COMPAT_ENABLED", True):
                    self.assertEqual((await soup_mcp._account_user(auth_db, legacy))["id"], ai_id)
                with patch.object(soup_mcp, "LEGACY_AI_JWT_COMPAT_ENABLED", False):
                    with self.assertRaises(soup_mcp.HTTPException):
                        await soup_mcp._account_user(auth_db, legacy)
                    self.assertEqual((await soup_mcp._account_user(auth_db, opaque))["id"], ai_id)
            finally:
                await auth_db.close()

            async def fake_get_db():
                return AsyncDb(self.db_path)

            with patch.object(soup_mcp, "get_db", fake_get_db):
                registered = await soup_mcp._register_toy_user(
                    "SoupFreshBot", "secret-pass"
                )
            self.assertTrue(registered["token"].startswith(server.AI_OPAQUE_TOKEN_PREFIX))

        asyncio.run(scenario())

if __name__ == "__main__":
    unittest.main(verbosity=2)
