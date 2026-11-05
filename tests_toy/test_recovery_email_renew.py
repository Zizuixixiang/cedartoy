"""Isolated renewal coverage; never contacts SMTP or production data."""
import sqlite3
import threading
import time
import unittest
from unittest.mock import patch
import server
from scripts import recovery_email_ops as ops
from tests_toy import test_recovery_email_ops as base_tests

class RecoveryEmailRenewTests(unittest.TestCase):
    ticket = base_tests.RecoveryEmailOpsTests.ticket
    run_cli = base_tests.RecoveryEmailOpsTests.run_cli

    def setUp(self):
        base_tests.RecoveryEmailOpsTests.setUp(self)
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE account_recovery_tickets SET email_status='sent', email_error=''")
            conn.execute("UPDATE password_reset_tokens SET expires_at=datetime('now','-1 second')")
        self.old_id = self.ticket()["reset_token_id"]

    def renew(self):
        return self.run_cli("renew", "6", "--reviewer", "id:3", "--expected-token-id",
                            str(self.old_id), "--send")

    def test_dry_run_no_mutation_or_secret(self):
        before = self.db.read_bytes()
        with patch.object(server, "_send_recovery_email") as send:
            code, result = self.run_cli("renew", "6", "--reviewer", "id:3")
        self.assertEqual(code, 0)
        self.assertEqual(result["expected_token_id"], self.old_id)
        self.assertTrue(result["dry_run"])
        self.assertNotIn("reset_url", result)
        self.assertNotIn("fixture@example.invalid", str(result))
        self.assertEqual(before, self.db.read_bytes())
        send.assert_not_called()

    def test_success_48h_preserves_approval_and_original_recipient(self):
        before = self.ticket()
        with patch.object(server, "_send_recovery_email") as send:
            code, result = self.renew()
            self.assertEqual(code, 0)
            send.assert_called_once()
            self.assertEqual(send.call_args.args[0], before["contact_email"])
            token = send.call_args.args[3].split("reset_token=")[1]
        after = self.ticket()
        for key in ("status", "reviewed_by", "reviewed_at_epoch", "claim_until_epoch",
                    "admin_note", "contact_email", "user_id", "completed_at_epoch"):
            self.assertEqual(before[key], after[key], key)
        self.assertNotEqual(before["reset_nonce"], after["reset_nonce"])
        self.assertNotEqual(self.old_id, after["reset_token_id"])
        self.assertEqual(server._reset_password_token_info(token)["username"], "Amber")
        with sqlite3.connect(self.db) as conn:
            old_used = conn.execute("SELECT used FROM password_reset_tokens WHERE id=?", (self.old_id,)).fetchone()[0]
            stored, ttl = conn.execute("SELECT token, strftime('%s',expires_at)-strftime('%s','now') FROM password_reset_tokens WHERE id=?",
                                       (after["reset_token_id"],)).fetchone()
        self.assertEqual(old_used, 1)
        self.assertEqual(stored, server._reset_token_hash(token))
        self.assertNotIn(token, str(result))
        self.assertTrue(172795 <= ttl <= 172800)
        self.assertEqual(self.renew()[0], 1)
        # Even after the renewed link expires, the same command cannot create another.
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE password_reset_tokens SET expires_at=datetime('now','-1 second')")
        with patch.object(server, "_send_recovery_email") as send:
            self.assertEqual(self.renew()[0], 1)
            send.assert_not_called()

    def test_rejection_conditions_leave_database_unchanged(self):
        original = self.db.read_bytes()
        cases = [
            "UPDATE account_recovery_tickets SET status='completed'",
            "UPDATE account_recovery_tickets SET completed_at_epoch=1",
            "UPDATE account_recovery_tickets SET claim_until_epoch=1",
            "UPDATE account_recovery_tickets SET reviewed_at_epoch=1",
            "UPDATE account_recovery_tickets SET email_status='sending'",
            "UPDATE account_recovery_tickets SET email_status='failed'",
            "UPDATE account_recovery_tickets SET email_error='email_delivery_unavailable'",
            "UPDATE account_recovery_tickets SET contact_email=NULL",
            "UPDATE account_recovery_tickets SET reset_token_version=1",
            "UPDATE password_reset_tokens SET used=1",
            "UPDATE password_reset_tokens SET expires_at=datetime('now','+1 hour')",
            "UPDATE password_reset_tokens SET token='mismatch'",
            "UPDATE password_reset_tokens SET user_id=2",
            "UPDATE toy_users SET deletion_requested_at_epoch=1 WHERE id=1",
            "UPDATE toy_users SET is_ai=1 WHERE id=1",
            "UPDATE toy_users SET is_admin=0 WHERE id=3",
        ]
        for sql in cases:
            with self.subTest(sql=sql):
                self.db.write_bytes(original)
                with sqlite3.connect(self.db) as conn:
                    conn.execute(sql)
                before = self.db.read_bytes()
                with patch.object(server, "_send_recovery_email") as send:
                    self.assertEqual(self.renew()[0], 1)
                    send.assert_not_called()
                self.assertEqual(before, self.db.read_bytes())

    def test_preflight_and_missing_or_stale_id_no_mutation(self):
        before = self.db.read_bytes()
        self.assertEqual(self.run_cli("renew", "6", "--send")[0], 1)
        self.assertEqual(self.run_cli("renew", "6", "--send", "--expected-token-id", "99999")[0], 1)
        with patch.object(server, "_smtp_config", side_effect=server._McpError(-1, "missing")):
            self.assertEqual(self.renew()[0], 1)
        self.assertEqual(before, self.db.read_bytes())

    def test_failed_send_sanitized_and_cannot_repeat(self):
        with patch.object(server, "_send_recovery_email", side_effect=RuntimeError("private-secret")) as send:
            code, result = self.renew()
            self.assertEqual(code, 1)
            self.assertEqual(result["email_status"], "failed")
            self.assertNotIn("private-secret", str(result))
            self.assertEqual(self.renew()[0], 1)
            send.assert_called_once()

    def test_crash_after_reservation_remains_uncertain_no_repeat(self):
        with patch.object(server, "_deliver_recovery_notification", side_effect=RuntimeError("crash")):
            with self.assertRaises(RuntimeError):
                ops.renew(server, 6, {"id":3}, expected_token_id=self.old_id, send=True)
        self.assertEqual(self.ticket()["email_status"], "sending")
        with patch.object(server, "_send_recovery_email") as send:
            self.assertEqual(self.renew()[0], 1)
            send.assert_not_called()

    def test_token_creation_failure_rolls_back(self):
        before = self.db.read_bytes()
        with patch.object(server, "_create_reset_token", side_effect=sqlite3.OperationalError("failure")):
            self.assertEqual(self.renew()[0], 1)
        self.assertEqual(before, self.db.read_bytes())

    def test_concurrent_renew_only_one_send(self):
        entered, release = threading.Event(), threading.Event()
        results = []
        def send(*args, **kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
        def first():
            results.append(ops.renew(server, 6, {"id":3}, expected_token_id=self.old_id, send=True))
        with patch.object(server, "_send_recovery_email", side_effect=send) as sender:
            thread = threading.Thread(target=first)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(ops.MailOperationError):
                    ops.renew(server, 6, {"id":3}, expected_token_id=self.old_id, send=True)
            finally:
                release.set()
                thread.join(5)
            sender.assert_called_once()
        self.assertTrue(results[0]["ok"])
