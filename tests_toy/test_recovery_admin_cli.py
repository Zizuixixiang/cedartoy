"""CLI tests use isolated account DBs and saves; never review production tickets."""
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import server
from scripts import recovery_admin_cli as cli


class RecoveryAdminCliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cedar-recovery-cli-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.db = self.root / "accounts.db"
        for name, value in (("TURTLE_DB_PATH", self.db), ("VENDOR_SAVE_ROOT", self.root / "saves")):
            p = patch.object(server, name, value)
            p.start()
            self.addCleanup(p.stop)
        p = patch.dict(os.environ, {"CEDARTOY_RECOVERY_REVIEWER": ""})
        p.start()
        self.addCleanup(p.stop)
        os.environ.pop("CEDARTOY_RECOVERY_REVIEWER")
        with sqlite3.connect(self.db) as conn:
            conn.executescript("""
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE, created_at TEXT,
                    is_ai INTEGER DEFAULT 0, is_admin INTEGER DEFAULT 0, deleted_at TEXT,
                    deletion_requested_at_epoch INTEGER, scheduled_delete_at_epoch INTEGER,
                    password_hash TEXT DEFAULT 'SECRET_PASSWORD', token TEXT DEFAULT 'SECRET_TOKEN');
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                INSERT INTO toy_users(id, username, created_at) VALUES(1, 'Amber', '2026-08-01 12:34:56');
                INSERT INTO toy_users(id, username, created_at, is_ai)
                    VALUES(2, 'Virel小羽', '2026-08-02 12:34:56', 1);
                INSERT INTO toy_users(id, username, is_admin) VALUES(3, 'Admin', 1);
                INSERT INTO user_bindings VALUES(1, 2);
            """)
            server._init_account_recovery_table(conn)
            conn.execute("""INSERT INTO account_recovery_tickets
                (id, account_kind, account, machine, registered_about, games, explanation,
                 query_code_hash, ip_hash, created_at_epoch, reset_nonce)
                VALUES (6, 'username', 'Amber', 'Virel小羽', '八月', '花园与猫咪', '申报文本',
                        'SECRET_QUERY', 'SECRET_IP', 1, 'SECRET_NONCE')""")

    def run_cli(self, *args):
        out = io.StringIO()
        with redirect_stdout(out):
            code = cli.main(list(args), backend=server)
        return code, json.loads(out.getvalue())

    def ticket(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM account_recovery_tickets WHERE id=6").fetchone())

    def test_list_inspect_allowlist_and_read_only_evidence(self):
        save = server.VENDOR_SAVE_ROOT / "garden_cat" / "2:2" / "state.json"
        save.parent.mkdir(parents=True)
        save.write_text(json.dumps({"cat": {"private": "SECRET_CAT"}, "encyclopedia": [1, 2],
                                    "token": "SECRET_SAVE_TOKEN"}), encoding="utf-8")
        before = self.db.read_bytes(), save.read_bytes()
        with patch.object(server, "_review_recovery_ticket") as review:
            for command in (("pending",), ("list",), ("inspect", "6")):
                code, result = self.run_cli(*command)
                self.assertEqual(code, 0)
                encoded = json.dumps(result)
                self.assertNotIn("SECRET_", encoded)
                for field in ("query_code_hash", "ip_hash", "password_hash", "reset_nonce", "reset_token_id", '"token"'):
                    self.assertNotIn(field, encoded)
            review.assert_not_called()
        self.assertEqual(before, (self.db.read_bytes(), save.read_bytes()))
        self.assertEqual(result["target_account"]["account"]["created_at"], "2026-08-01 12:34:56")
        self.assertTrue(result["target_account"]["recoverable"])
        self.assertEqual(result["current_bound_machines"], [
            {"id": 2, "username": "Virel小羽", "created_at": "2026-08-02 12:34:56"}])
        garden = result["save_evidence"]["games"][0]
        self.assertEqual(garden["status"], "verified_present")
        self.assertTrue(garden["checks"][1]["has_cat"])
        self.assertEqual(garden["checks"][1]["encyclopedia_count"], 2)

    def test_missing_and_corrupt_save_are_unavailable(self):
        for content in (None, "bad json"):
            if content:
                save = server.VENDOR_SAVE_ROOT / "garden_cat" / "2" / "state.json"
                save.parent.mkdir(parents=True)
                save.write_text(content)
            code, result = self.run_cli("inspect", "6")
            self.assertEqual(code, 0)
            self.assertEqual(result["save_evidence"]["games"][0]["status"], "unavailable")
            self.assertEqual(result["save_evidence"]["other_games"], "unavailable")

    def test_ambiguous_admin_fails_closed_then_explicit_reviewer_succeeds(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO toy_users(id, username, is_admin) VALUES(4, 'Second', 1)")
        with patch.object(server, "_review_recovery_ticket", wraps=server._review_recovery_ticket) as review:
            code, result = self.run_cli("approve", "6", "--note", "核验通过")
            self.assertEqual(code, 1)
            self.assertIn("--reviewer", result["error"])
            review.assert_not_called()
            self.assertEqual(self.ticket()["status"], "pending")
            code, result = self.run_cli("approve", "6", "--reviewer", "username:Second", "--note", "核验通过")
            self.assertEqual(code, 0)
            review.assert_called_once_with(6, {"decision": "approved", "admin_note": "核验通过"},
                                           {"id": 4, "username": "Second"})
        self.assertEqual(result["reviewer"]["id"], 4)
        ticket = self.ticket()
        self.assertEqual((ticket["status"], ticket["user_id"], ticket["reviewed_by"]), ("approved", 1, 4))
        self.assertEqual(ticket["claim_until_epoch"] - ticket["reviewed_at_epoch"], 7 * 86400)
        self.assertIsNone(ticket["reset_token_id"])
        for command in ("approve", "reject"):
            code, result = self.run_cli(command, "6", "--reviewer", "4", "--note", "重复")
            self.assertEqual(code, 1)
            self.assertIn("已处理", result["error"])
            self.assertEqual(self.ticket(), ticket)

    def test_reject_uses_same_logic_and_env_audit_reviewer(self):
        with patch.dict(os.environ, {"CEDARTOY_RECOVERY_REVIEWER": "id:3"}), patch.object(
            server, "_review_recovery_ticket", wraps=server._review_recovery_ticket,
        ) as review:
            code, result = self.run_cli("reject", "6", "--note", "信息不足")
        self.assertEqual(code, 0)
        review.assert_called_once_with(6, {"decision": "rejected", "admin_note": "信息不足"},
                                       {"id": 3, "username": "Admin"})
        self.assertNotIn("SECRET_", json.dumps(result))
        ticket = self.ticket()
        self.assertEqual((ticket["status"], ticket["reviewed_by"]), ("rejected", 3))
        self.assertIsNone(ticket["user_id"])
        self.assertIsNone(ticket["claim_until_epoch"])
        _, processed = self.run_cli("list", "--view", "processed")
        self.assertEqual(processed["tickets"][0]["reviewed_by"], 3)
        self.assertEqual(processed["pending_count"], 0)

    def test_inactive_missing_and_non_admin_reviewers_are_rejected(self):
        for field in ("deleted_at", "deletion_requested_at_epoch", "scheduled_delete_at_epoch"):
            with sqlite3.connect(self.db) as conn:
                conn.execute(f"UPDATE toy_users SET {field}=1 WHERE id=3")
            for selector in (None, "id:3", "Amber", "999", ""):
                with self.subTest(field=field, selector=selector), patch.object(server, "_review_recovery_ticket") as review:
                    args = [] if selector is None else ["--reviewer", selector]
                    code, _ = self.run_cli("approve", "6", "--note", "核验", *args)
                    self.assertEqual(code, 1)
                    review.assert_not_called()
            with sqlite3.connect(self.db) as conn:
                conn.execute(f"UPDATE toy_users SET {field}=NULL WHERE id=3")

    def test_numeric_username_ambiguity_and_explicit_override(self):
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO toy_users(id, username, is_admin) VALUES(4, '3', 1)")
        with cli.read_only(self.db) as conn:
            with self.assertRaises(cli.CliError):
                cli.resolve_reviewer(conn, "3")
            self.assertEqual(cli.resolve_reviewer(conn, "id:3")["id"], 3)
            self.assertEqual(cli.resolve_reviewer(conn, "username:3")["id"], 4)
        with patch.dict(os.environ, {"CEDARTOY_RECOVERY_REVIEWER": "username:3"}):
            _, result = self.run_cli("reject", "6", "--reviewer", "id:3", "--note", "核验")
        self.assertEqual(result["reviewer"]["id"], 3)

    def test_ineligible_and_missing_targets_and_ticket(self):
        for account in ("Missing", "Virel小羽"):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE account_recovery_tickets SET account=?", (account,))
            _, result = self.run_cli("inspect", "6")
            self.assertFalse(result["target_account"]["recoverable"])
            self.assertEqual(result["target_account"]["exists"], account != "Missing")
            code, _ = self.run_cli("approve", "6", "--note", "核验")
            self.assertEqual(code, 1)
            self.assertEqual(self.ticket()["status"], "pending")
        self.assertEqual(self.run_cli("inspect", "999")[0], 1)

    def test_real_entrypoint_from_other_cwd_and_missing_db_not_created(self):
        env = {**os.environ, "TURTLE_SOUP_DB": str(self.db)}
        command = [sys.executable, str(cli.ROOT / "scripts/recovery_admin_cli.py"), "pending"]
        result = subprocess.run(command, env=env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["tickets"][0]["id"], 6)
        missing = self.root / "missing.db"
        env["TURTLE_SOUP_DB"] = str(missing)
        result = subprocess.run(command, env=env, cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 1)
        self.assertFalse(json.loads(result.stdout)["ok"])
        self.assertFalse(missing.exists())


if __name__ == "__main__":
    unittest.main()
