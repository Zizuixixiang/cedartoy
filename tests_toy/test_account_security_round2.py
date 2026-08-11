import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class AccountSecurityRoundTwoTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-account-round2-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.secret_patch = patch.object(server, "TOY_SECRET", "round-two-test-secret")
        self.real_smtp_config = server._smtp_config
        self.db_patch.start()
        self.secret_patch.start()
        self.sent_codes = []
        self.smtp_patch = patch.object(
            server,
            "_smtp_config",
            return_value={
                "host": "smtp.invalid",
                "port": 587,
                "sender": "noreply@example.invalid",
                "username": "",
                "password": "",
                "security": "starttls",
            },
        )
        self.send_patch = patch.object(
            server,
            "_send_verification_email",
            side_effect=lambda email, code, purpose, _config: self.sent_codes.append(
                {"email": email, "code": code, "purpose": purpose}
            ),
        )
        self.smtp_patch.start()
        self.send_patch.start()
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
            server._init_password_reset_tokens_table(conn)
            server._init_account_security_schema(conn)
            server._init_account_email_schema(conn)
            server._init_account_email_schema(conn)

        self.password = "human-pass"
        self.human_id = self._add_user("Human", password=self.password)
        self.other_human_id = self._add_user("OtherHuman", password="other-pass")
        self.ai_id = self._add_user("Machine", is_ai=True, password="machine-pass")
        self.human_token = self._token_for(self.human_id)
        self.other_human_token = self._token_for(self.other_human_id)
        self.ai_token = self._token_for(self.ai_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
                (self.human_id, self.ai_id),
            )

    def tearDown(self):
        self.send_patch.stop()
        self.smtp_patch.stop()
        self.secret_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def _add_user(self, username, *, is_ai=False, password="secret-pass"):
        with self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, ?)",
                (username, server._hash_password(password), 1 if is_ai else 0),
            )
            return int(cursor.lastrowid)

    def _user(self, user_id):
        with self._connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ?", (int(user_id),)
            ).fetchone())

    def _token_for(self, user_id):
        return server._create_account_token(self._user(user_id))

    def _latest_code(self):
        return self.sent_codes[-1]["code"]

    def _age_all_codes(self, seconds=3700):
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE email_verification_codes
                SET created_at_epoch = created_at_epoch - ?,
                    delivered_at_epoch = delivered_at_epoch - ?,
                    expires_at_epoch = expires_at_epoch - ?
                """,
                (seconds, seconds, seconds),
            )
            conn.execute(
                """
                UPDATE email_verification_attempts
                SET attempted_at_epoch = attempted_at_epoch - ?
                """,
                (seconds,),
            )

    def _bind_email(self, email="Human@Example.COM"):
        server._send_account_email_code(self.human_token, email, client_ip="1.1.1.1")
        return server._confirm_account_email(
            self.human_token,
            email,
            self._latest_code(),
            client_ip="1.1.1.1",
        )

    def test_schema_human_only_normalization_and_unique_email(self):
        with self._connect() as conn:
            tables = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            indexes = {row["name"] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )}
        self.assertIn("account_emails", tables)
        self.assertIn("email_verification_codes", tables)
        self.assertIn("email_verification_attempts", tables)
        self.assertIn("idx_account_emails_unique_normalized", indexes)

        with self.assertRaises(server._McpError) as ai_rejected:
            server._send_account_email_code(
                self.ai_token, "machine@example.com", client_ip="2.2.2.2"
            )
        self.assertEqual(ai_rejected.exception.code, -32003)

        result = self._bind_email("  Human@Example.COM  ")
        self.assertTrue(result["verified"])
        self.assertNotIn("human@example.com", result["masked_email"])
        with self._connect() as conn:
            row = conn.execute(
                "SELECT email_normalized FROM account_emails WHERE user_id = ?",
                (self.human_id,),
            ).fetchone()
            stored_code = conn.execute(
                "SELECT code_hash, code_salt FROM email_verification_codes ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self.assertEqual(row["email_normalized"], "human@example.com")
        self.assertNotEqual(stored_code["code_hash"], self._latest_code())
        self.assertNotIn(self._latest_code(), stored_code["code_hash"])

        with self.assertRaises(server._McpError) as duplicate:
            server._send_account_email_code(
                self.other_human_token,
                "HUMAN@example.com",
                client_ip="3.3.3.3",
            )
        self.assertEqual(duplicate.exception.details["reason"], "email_in_use")

        with self._connect() as conn:
            with self.assertRaises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO account_emails
                        (user_id, email_normalized, verified_at_epoch)
                    VALUES (?, 'machine@example.com', CAST(strftime('%s', 'now') AS INTEGER))
                    """,
                    (self.ai_id,),
                )
            conn.execute(
                "UPDATE toy_users SET is_ai = 1 WHERE id = ?",
                (self.human_id,),
            )
            self.assertIsNone(conn.execute(
                "SELECT 1 FROM account_emails WHERE user_id = ?",
                (self.human_id,),
            ).fetchone())

    def test_expired_wrong_locked_and_single_use_codes(self):
        server._send_account_email_code(
            self.human_token, "expire@example.com", client_ip="4.4.4.4"
        )
        expired_code = self._latest_code()
        with self._connect() as conn:
            conn.execute(
                "UPDATE email_verification_codes SET expires_at_epoch = ?",
                (int(time.time()) - 1,),
            )
        with self.assertRaises(server._McpError) as expired:
            server._confirm_account_email(
                self.human_token,
                "expire@example.com",
                expired_code,
                client_ip="4.4.4.4",
            )
        self.assertIn("已过期", expired.exception.message)

        self._age_all_codes()
        server._send_account_email_code(
            self.human_token, "retry@example.com", client_ip="4.4.4.4"
        )
        valid_code = self._latest_code()
        for attempt in range(server.EMAIL_CODE_MAX_ATTEMPTS):
            with self.assertRaises(server._McpError) as wrong:
                server._confirm_account_email(
                    self.human_token,
                    "retry@example.com",
                    "000000" if valid_code != "000000" else "999999",
                    client_ip="4.4.4.4",
                )
            if attempt == server.EMAIL_CODE_MAX_ATTEMPTS - 1:
                self.assertEqual(wrong.exception.code, server.RATE_LIMIT_ERROR_CODE)
        with self.assertRaises(server._McpError):
            server._confirm_account_email(
                self.human_token,
                "retry@example.com",
                valid_code,
                client_ip="4.4.4.4",
            )

        self._age_all_codes()
        server._send_account_email_code(
            self.human_token, "once@example.com", client_ip="4.4.4.4"
        )
        once_code = self._latest_code()
        server._confirm_account_email(
            self.human_token,
            "once@example.com",
            once_code,
            client_ip="4.4.4.4",
        )
        with self.assertRaises(server._McpError):
            server._confirm_account_email(
                self.human_token,
                "once@example.com",
                once_code,
                client_ip="4.4.4.4",
            )

    def test_send_cooldown_and_verification_rate_limit_boundaries(self):
        server._send_account_email_code(
            self.human_token, "cooldown@example.com", client_ip="5.5.5.5"
        )
        with self.assertRaises(server._McpError) as cooldown:
            server._send_account_email_code(
                self.human_token, "other@example.com", client_ip="5.5.5.5"
            )
        self.assertEqual(cooldown.exception.code, server.RATE_LIMIT_ERROR_CODE)

        self._age_all_codes()
        with patch.object(server, "EMAIL_CODE_MAX_ATTEMPTS", 20), patch.object(
            server, "EMAIL_VERIFY_MAX_PER_ACCOUNT", 2
        ):
            server._send_account_email_code(
                self.human_token, "verify@example.com", client_ip="6.6.6.6"
            )
            valid_code = self._latest_code()
            wrong_code = "000000" if valid_code != "000000" else "999999"
            for _ in range(2):
                with self.assertRaises(server._McpError):
                    server._confirm_account_email(
                        self.human_token,
                        "verify@example.com",
                        wrong_code,
                        client_ip="6.6.6.6",
                    )
            with self.assertRaises(server._McpError) as limited:
                server._confirm_account_email(
                    self.human_token,
                    "verify@example.com",
                    valid_code,
                    client_ip="6.6.6.6",
                )
            self.assertEqual(limited.exception.code, server.RATE_LIMIT_ERROR_CODE)

    def test_bind_change_and_password_protected_unbind(self):
        bound = self._bind_email()
        self.assertTrue(bound["bound"])
        self._age_all_codes()
        server._send_account_email_code(
            self.human_token, "new@example.com", client_ip="7.7.7.7"
        )
        self.assertEqual(self.sent_codes[-1]["purpose"], "change")
        changed = server._confirm_account_email(
            self.human_token,
            "new@example.com",
            self._latest_code(),
            client_ip="7.7.7.7",
        )
        self.assertIn("更换", changed["message"])
        status = server._account_email_status(self.human_token)
        self.assertTrue(status["bound"])

        with self.assertRaises(server._McpError) as wrong_password:
            server._unbind_account_email(self.human_token, "wrong-password")
        self.assertIn("密码错误", wrong_password.exception.message)
        self.assertTrue(server._account_email_status(self.human_token)["bound"])
        server._unbind_account_email(self.human_token, self.password)
        self.assertFalse(server._account_email_status(self.human_token)["bound"])

    def test_password_recovery_guidance_and_human_reset_preserves_machine_token(self):
        no_email = server._start_password_recovery("OtherHuman", client_ip="8.8.8.8")
        self.assertEqual(no_email["state"], "unavailable")
        self.assertIn("未绑定邮箱", no_email["message"])
        missing = server._start_password_recovery("Missing", client_ip="8.8.8.8")
        self.assertEqual(missing, no_email)

        machine = server._start_password_recovery("Machine", client_ip="8.8.8.8")
        self.assertEqual(machine["state"], "machine")
        self.assertIn("我的 -> 我的小机 -> 重置密码", machine["message"])
        self.assertIn("未绑定的小机请联系管理员", machine["message"])

        self._bind_email("recover@example.com")
        self._age_all_codes()
        old_human_token = self.human_token
        old_machine_token = self.ai_token
        reset_started = server._start_password_recovery("Human", client_ip="8.8.8.8")
        self.assertEqual(reset_started["state"], "code_sent")
        self.assertEqual(self.sent_codes[-1]["purpose"], "reset")
        server._reset_human_password_by_email(
            "Human",
            self._latest_code(),
            "new-human-pass",
            client_ip="8.8.8.8",
        )
        self.assertEqual(server._current_account(old_human_token)["id"], self.human_id)
        self.assertEqual(server._current_account(old_machine_token)["id"], self.ai_id)
        self.assertEqual(self._user(self.ai_id)["ai_token_version"], 0)
        self.assertEqual(
            server._login_human("Human", "new-human-pass", client_ip="9.9.9.9")["user"]["id"],
            self.human_id,
        )
        with self._connect() as conn:
            binding = conn.execute(
                "SELECT 1 FROM user_bindings WHERE human_user_id = ? AND ai_user_id = ?",
                (self.human_id, self.ai_id),
            ).fetchone()
        self.assertIsNotNone(binding)

    def test_admin_one_hour_reset_link_still_works(self):
        generated = server._generate_reset_link(self.other_human_id)
        reset_token = generated["reset_url"].split("reset_token=", 1)[1]
        server._reset_password_by_token(reset_token, "admin-reset-pass")
        logged_in = server._login_human(
            "OtherHuman", "admin-reset-pass", client_ip="10.10.10.10"
        )
        self.assertEqual(logged_in["user"]["id"], self.other_human_id)
        with self.assertRaises(server._McpError) as reused:
            server._reset_password_by_token(reset_token, "another-pass")
        self.assertIn("已使用", reused.exception.message)

    def test_unconfigured_provider_is_diagnostic(self):
        with patch.dict("os.environ", {}, clear=True), patch.object(
            server, "_smtp_config", side_effect=self.real_smtp_config
        ):
            with self.assertRaises(server._McpError) as raised:
                server._send_account_email_code(
                    self.human_token,
                    "provider@example.com",
                    client_ip="11.11.11.11",
                )
        self.assertEqual(raised.exception.code, server.EMAIL_PROVIDER_ERROR_CODE)
        self.assertEqual(
            raised.exception.details["reason"], "email_provider_not_configured"
        )
        with self._connect() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM email_verification_codes WHERE email_normalized = ?",
                ("provider@example.com",),
            ).fetchone()[0]
        self.assertEqual(count, 0)

    def test_routes_and_frontend_security_flows_are_wired(self):
        post_routes = (
            ("/api/account/email/send-code", "_handle_api_account_email_send"),
            ("/api/account/email/confirm", "_handle_api_account_email_confirm"),
            ("/api/auth/forgot-password", "_handle_api_forgot_password"),
            ("/api/auth/forgot-password/reset", "_handle_api_forgot_password_reset"),
        )
        for path, method_name in post_routes:
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

        for verb, method_name in (
            ("do_GET", "_handle_api_account_email_status"),
            ("do_DELETE", "_handle_api_account_email_unbind"),
        ):
            handler = object.__new__(server.CedarToyHandler)
            handler.path = "/api/account/email"
            handler.headers = {}
            handler.client_address = ("127.0.0.1", 12345)
            handler._is_soup_path = lambda: False
            dispatched = []
            setattr(handler, method_name, lambda name=method_name: dispatched.append(name))
            getattr(handler, verb)()
            self.assertEqual(dispatched, [method_name])

        html = server.TOY_INDEX_PATH.read_text(encoding="utf-8")
        for marker in (
            'id="forgotPasswordOpen"',
            'id="forgotPasswordModal"',
            'id="emailSecurityModal"',
            '"/api/auth/forgot-password/reset"',
            '"/api/account/email/send-code"',
            'method: "DELETE"',
            "系统会根据账号类型提供找回方式",
        ):
            self.assertIn(marker, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
