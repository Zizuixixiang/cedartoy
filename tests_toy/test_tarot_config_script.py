import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.configure_tarot_pro import (
    ConfigurationError,
    FLASH_MODEL,
    PRO_MODEL,
    configure,
)


SCHEMA = """
CREATE TABLE judge_api_configs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    api_url TEXT NOT NULL,
    api_key TEXT NOT NULL,
    model TEXT NOT NULL,
    purpose TEXT DEFAULT 'judge',
    enabled INTEGER DEFAULT 1,
    priority INTEGER DEFAULT 0,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
)
"""


class TarotProConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="tarot-pro-config-")
        self.db_path = Path(self.temp_dir.name) / "copy.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(SCHEMA)
            conn.execute(
                """
                INSERT INTO judge_api_configs(
                    id,name,api_url,api_key,model,purpose,enabled,priority
                ) VALUES(19,'flash source','https://provider.test/v1',?,?,'tarot',1,7)
                """,
                ("fixture-secret-never-print", FLASH_MODEL),
            )

    def tearDown(self):
        self.temp_dir.cleanup()

    def rows(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            return conn.execute(
                "SELECT * FROM judge_api_configs ORDER BY id"
            ).fetchall()

    def test_dry_run_then_two_apply_runs_are_idempotent(self):
        preview = configure(self.db_path)
        self.assertEqual(preview["action"], "would_insert")
        self.assertEqual(len(self.rows()), 1)

        created = configure(self.db_path, apply=True)
        replay = configure(self.db_path, apply=True)
        self.assertEqual(created["action"], "insert")
        self.assertEqual(created["integrity"], "ok")
        self.assertEqual(replay["action"], "unchanged")
        self.assertEqual(replay["config_id"], created["config_id"])

        rows = self.rows()
        self.assertEqual(len(rows), 2)
        pro = rows[1]
        self.assertEqual(pro["model"], PRO_MODEL)
        self.assertEqual(pro["purpose"], "tarot")
        self.assertEqual(pro["enabled"], 1)
        self.assertEqual(pro["priority"], 7)
        self.assertEqual(pro["api_key"], rows[0]["api_key"])

    def test_enabled_cross_purpose_node_conflict_is_rejected(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO judge_api_configs(
                    name,api_url,api_key,model,purpose,enabled,priority
                ) VALUES('conflict','https://provider.test/v1/chat/completions',?,?,'npc',1,0)
                """,
                ("fixture-secret-never-print", PRO_MODEL),
            )
        with self.assertRaises(ConfigurationError) as raised:
            configure(self.db_path, apply=True)
        self.assertIn("非 tarot", str(raised.exception))
        self.assertNotIn("fixture-secret-never-print", str(raised.exception))
        self.assertEqual(len(self.rows()), 2)

    def test_disabled_tarot_row_is_reused_instead_of_duplicated(self):
        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute(
                """
                INSERT INTO judge_api_configs(
                    name,api_url,api_key,model,purpose,enabled,priority
                ) VALUES('old pro','https://provider.test/v1',?,?,'tarot',0,99)
                """,
                ("fixture-secret-never-print", PRO_MODEL),
            )
            old_id = cursor.lastrowid
        result = configure(self.db_path, apply=True)
        self.assertEqual(result["action"], "enable")
        self.assertEqual(result["config_id"], old_id)
        self.assertEqual(len(self.rows()), 2)


if __name__ == "__main__":
    unittest.main()
