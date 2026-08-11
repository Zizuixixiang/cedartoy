import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class AccountSecurityRoundOneTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-account-round1-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.secret_patch = patch.object(server, "TOY_SECRET", "round-one-new-secret")
        self.db_patch.start()
        self.secret_patch.start()
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
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
                    UNIQUE (human_user_id, ai_user_id)
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
                    is_admin INTEGER DEFAULT 0
                );
                """
            )
            server._init_registration_events_table(conn)
            server._init_username_changes_table(conn)
            server._init_account_security_schema(conn)
            server._init_account_security_schema(conn)

    def tearDown(self):
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        self.secret_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _add_user(self, username, *, is_ai=False, is_admin=False, password="secret-pass"):
        with self._connect() as conn:
            cur = conn.execute(
                """
                INSERT INTO toy_users (username, password_hash, is_ai, is_admin)
                VALUES (?, ?, ?, ?)
                """,
                (
                    username,
                    server._hash_password(password),
                    1 if is_ai else 0,
                    1 if is_admin else 0,
                ),
            )
            return cur.lastrowid

    def _user(self, user_id):
        with self._connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ?", (user_id,)
            ).fetchone())

    def test_schema_migration_is_idempotent_and_adds_required_index(self):
        with self._connect() as conn:
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(toy_users)")}
            indexes = {row["name"] for row in conn.execute("PRAGMA index_list(players)")}
            tables = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
        self.assertIn("ai_token_version", columns)
        self.assertIn("idx_players_user_id", indexes)
        self.assertIn("legacy_ai_token_hashes", tables)
        self.assertIn("ai_access_tokens", tables)

    def test_human_login_and_registration_never_fall_through(self):
        registered = server._register_human("NewHuman", "secret-pass", client_ip="1.1.1.1")
        self.assertFalse(registered["user"]["is_ai"])
        logged_in = server._login_human("NewHuman", "secret-pass", client_ip="1.1.1.1")
        self.assertEqual(logged_in["user"]["id"], registered["user"]["id"])

        with self.assertRaises(server._McpError) as missing:
            server._login_human("MissingHuman", "secret-pass", client_ip="1.1.1.1")
        self.assertEqual(missing.exception.message, "用户名或密码错误")
        with self.assertRaises(server._McpError) as duplicate:
            server._register_human("NewHuman", "other-pass", client_ip="1.1.1.1")
        self.assertIn("已存在", duplicate.exception.message)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM toy_users").fetchone()[0], 1)

    def test_split_rest_routes_and_frontend_do_not_use_legacy_endpoint(self):
        for path, method_name in (
            ("/api/auth/login", "_handle_api_login"),
            ("/api/auth/register", "_handle_api_register"),
        ):
            with self.subTest(path=path):
                handler = object.__new__(server.CedarToyHandler)
                handler.path = path
                handler.headers = {}
                handler.client_address = ("127.0.0.1", 12345)
                handler._is_soup_path = lambda: False
                dispatched = []
                setattr(handler, method_name, lambda name=method_name: dispatched.append(name))
                handler.do_POST()
                self.assertEqual(dispatched, [method_name])

        html = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        self.assertIn('fetch(endpoint', html)
        self.assertIn('"/api/auth/login"', html)
        self.assertIn('"/api/auth/register"', html)
        self.assertNotIn('fetch("/api/auth/login_or_register"', html)

    def test_failed_login_limit_boundary_normalization_and_success_clear(self):
        self._add_user("CaseUser", password="secret-pass")
        with patch.object(server, "FAILED_LOGIN_MAX", 3):
            for typed_name in ("CaseUser", "caseuser"):
                with self.assertRaises(server._McpError) as raised:
                    server._login_human(typed_name, "wrong-pass", client_ip="2.2.2.2")
                self.assertEqual(raised.exception.code, -32001)
            with self.assertRaises(server._McpError) as limited:
                server._login_human("CASEUSER", "wrong-pass", client_ip="2.2.2.2")
            self.assertEqual(limited.exception.code, server.RATE_LIMIT_ERROR_CODE)

            # The same username on another IP is an independent bucket.
            result = server._login_human("CaseUser", "secret-pass", client_ip="3.3.3.3")
            self.assertEqual(result["user"]["username"], "CaseUser")

            identity = server._failed_login_identity("2.2.2.2", "CaseUser")
            server._FAILED_LOGIN_RATE_LIMIT[identity] = [
                0.0,
                0.0,
                0.0,
            ]
            result = server._login_human("CaseUser", "secret-pass", client_ip="2.2.2.2")
            self.assertEqual(result["user"]["username"], "CaseUser")
            self.assertNotIn(identity, server._FAILED_LOGIN_RATE_LIMIT)

            # A timestamp exactly one full window old has expired.
            server._FAILED_LOGIN_RATE_LIMIT[identity] = [400.0, 999.0, 999.0]
            self.assertFalse(server._failed_login_is_limited(
                "2.2.2.2",
                "caseuser",
                now=1000.0,
            ))

    def test_ai_rotate_invalidates_old_token_without_changing_password(self):
        user_id = self._add_user("RotateBot", is_ai=True)
        old_token = server._create_account_token(self._user(user_id))
        old_password_hash = self._user(user_id)["password_hash"]

        rotated = json.loads(server._tool_account(
            {"action": "rotate_token"},
            path_token=old_token,
        ))

        with self.assertRaises(server._McpError):
            server._current_account(old_token)
        self.assertEqual(server._current_account(rotated["token"])["id"], user_id)
        self.assertEqual(self._user(user_id)["password_hash"], old_password_hash)
        self.assertIn("旧 Token 已失效", rotated["message"])

    def test_bound_human_can_rotate_without_machine_password(self):
        human_id = self._add_user("Owner", password="human-pass")
        machine_id = self._add_user("BoundBot", is_ai=True, password="machine-pass")
        human_token = server._create_account_token(self._user(human_id))
        old_machine_token = server._create_account_token(self._user(machine_id))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (human_id, machine_id),
            )

        result = server._machine_account_token(
            "",
            "",
            rotate=True,
            ai_user_id=machine_id,
            human_token=human_token,
        )
        self.assertTrue(result["rotated"])
        with self.assertRaises(server._McpError):
            server._current_account(old_machine_token)
        self.assertEqual(server._current_account(result["token"])["id"], machine_id)

    def test_machine_password_reset_does_not_rotate_token(self):
        human_id = self._add_user("ResetOwner", password="human-pass")
        machine_id = self._add_user("ResetBot", is_ai=True, password="machine-pass")
        human_token = server._create_account_token(self._user(human_id))
        machine_token = server._create_account_token(self._user(machine_id))
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (human_id, machine_id),
            )

        server._reset_machine_password(human_token, machine_id, "new-machine-pass")

        self.assertEqual(server._current_account(machine_token)["id"], machine_id)
        self.assertEqual(self._user(machine_id)["ai_token_version"], 0)

    def test_legacy_allowlist_accepts_only_exact_hash_and_obeys_rotation(self):
        user_id = self._add_user("LegacyBot", is_ai=True)
        legacy_payload = {
            "user_id": user_id,
            "username": "LegacyBot",
            "is_ai": True,
            "is_admin": False,
        }
        with patch.object(server, "TOY_SECRET", "legacy-public-secret"):
            exact_token = server._jwt_encode(legacy_payload)
            unlisted_variant = server._jwt_encode({**legacy_payload, "is_admin": True})
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO legacy_ai_token_hashes (token_hash, user_id, token_version, source)
                VALUES (?, ?, 0, 'test_verified')
                """,
                (server._legacy_ai_token_hash(exact_token), user_id),
            )

        self.assertEqual(server._current_account(exact_token)["id"], user_id)
        with self.assertRaises(ValueError):
            server._jwt_decode(unlisted_variant)

        rotated = server._rotate_ai_token(exact_token)
        with self.assertRaises(server._McpError):
            server._current_account(exact_token)
        self.assertEqual(server._current_account(rotated["token"])["id"], user_id)

    def test_request_log_redacts_path_jwt_and_sensitive_query_values(self):
        user_id = self._add_user("LogBot", is_ai=True)
        raw_token = server._create_account_token(self._user(user_id))
        handler = object.__new__(server.CedarToyHandler)
        handler.client_address = ("127.0.0.1", 12345)
        handler.log_date_time_string = lambda: "now"
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            handler.log_message('"%s" %s %s', f"POST /{raw_token} HTTP/1.1", "200", "12")
            handler.log_message(
                '"%s" %s %s',
                "GET /?reset_token=do-not-print&token=also-secret HTTP/1.1",
                "200",
                "12",
            )
        logged = output.getvalue()
        self.assertNotIn(raw_token, logged)
        self.assertNotIn("do-not-print", logged)
        self.assertNotIn("also-secret", logged)
        self.assertIn("/<TOKEN_REDACTED>", logged)

    def test_admin_pagination_search_and_handler_parameters(self):
        admin_id = self._add_user("Admin", is_admin=True)
        for index in range(1, 121):
            user_id = self._add_user(f"User{index:03d}", is_ai=index % 2 == 0)
            if index % 10 == 0:
                with self._connect() as conn:
                    conn.execute(
                        "INSERT INTO players (username, user_id) VALUES (?, ?)",
                        (f"Soup{index:03d}", user_id),
                    )

        page = server._admin_user_page(2, 50)
        self.assertEqual(page["total"], 121)
        self.assertEqual(page["page"], 2)
        self.assertEqual(len(page["users"]), 50)
        named = server._admin_user_page(1, 50, "User073")
        self.assertEqual(named["total"], 1)
        self.assertEqual(named["users"][0]["username"], "User073")
        by_id = server._admin_user_page(1, 50, str(admin_id))
        self.assertTrue(any(user["id"] == admin_id for user in by_id["users"]))
        with self.assertRaises(ValueError):
            server._admin_user_page(0, 50)
        with self.assertRaises(ValueError):
            server._admin_user_page(1, server.ADMIN_USERS_MAX_PAGE_SIZE + 1)

        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/api/admin/users?page=2&page_size=25&search=User"
        handler.headers = {"Authorization": f"Bearer {server._create_account_token(self._user(admin_id))}"}
        sent = []
        handler._send_json = lambda payload, status=200, **kwargs: sent.append((status, payload))
        handler._handle_admin_users()
        self.assertEqual(sent[-1][0], 200)
        self.assertEqual(sent[-1][1]["page"], 2)
        self.assertEqual(sent[-1][1]["page_size"], 25)
        self.assertEqual(sent[-1][1]["total"], 120)


if __name__ == "__main__":
    unittest.main(verbosity=2)
