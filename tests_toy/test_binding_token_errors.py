import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class BindingTokenErrorTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-bind-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.db_patch.start()
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE binding_tokens (
                    token TEXT NOT NULL,
                    ai_user_id INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    used INTEGER DEFAULT 0
                );
                CREATE TABLE user_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    human_user_id INTEGER NOT NULL,
                    ai_user_id INTEGER NOT NULL,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE (human_user_id, ai_user_id)
                );
                """
            )
        self.account_patch = patch.object(
            server, "_current_account", return_value={"id": 10, "is_ai": 0}
        )
        self.account_patch.start()

    def tearDown(self):
        self.account_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _insert(self, token, *, expires_at, used=0):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO binding_tokens (token, ai_user_id, expires_at, used) VALUES (?, 20, ?, ?)",
                (token, expires_at, used),
            )

    def _error(self, token):
        with self.assertRaises(server._McpError) as raised:
            server._bind_account("human-token", token)
        return raised.exception.message

    def test_format_error_mentions_copy_artifacts(self):
        message = self._error(" `" + "a" * 32 + "` ")
        self.assertIn("格式不正确", message)
        self.assertIn("空格", message)
        self.assertIn("换行", message)
        self.assertIn("隐藏字符", message)

    def test_unknown_token_is_distinct_from_expired(self):
        message = self._error("x" * 32)
        self.assertIn("不存在", message)
        self.assertNotIn("已过期", message)

    def test_expired_token_has_explicit_expiry_message(self):
        token = "e" * 32
        self._insert(token, expires_at="2000-01-01 00:00:00")
        message = self._error(token)
        self.assertIn("已过期", message)
        self.assertIn("10分钟", message)

    def test_used_token_has_explicit_used_message(self):
        token = "u" * 32
        self._insert(token, expires_at="2999-01-01 00:00:00", used=1)
        message = self._error(token)
        self.assertIn("已使用", message)

    def test_active_token_still_binds(self):
        token = "v" * 32
        self._insert(token, expires_at="2999-01-01 00:00:00")
        self.assertEqual(server._bind_account("human-token", token), {"ok": True})
        with sqlite3.connect(self.db_path) as conn:
            self.assertEqual(conn.execute("SELECT used FROM binding_tokens WHERE token = ?", (token,)).fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT human_user_id, ai_user_id FROM user_bindings").fetchone(), (10, 20))


if __name__ == "__main__":
    unittest.main()
