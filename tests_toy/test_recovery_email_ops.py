"""Isolated retry tests: no production database or SMTP traffic."""
import os
from pathlib import Path
import sqlite3
import threading
import unittest
from unittest.mock import patch
import server
from scripts import recovery_email_ops as ops
from tests_toy import test_recovery_admin_cli as base_tests

class RecoveryEmailOpsTests(unittest.TestCase):
    ticket = base_tests.RecoveryAdminCliTests.ticket
    run_cli = base_tests.RecoveryAdminCliTests.run_cli

    def setUp(self):
        base_tests.RecoveryAdminCliTests.setUp(self)
        self.smtp = patch.object(server, "_smtp_config", return_value={})
        self.smtp.start()
        self.addCleanup(self.smtp.stop)
        with sqlite3.connect(self.db) as conn:
            server._init_password_reset_tokens_table(conn)
            conn.execute("UPDATE account_recovery_tickets SET contact_email='fixture@example.invalid' WHERE id=6")
        with patch.object(server, "_send_recovery_email"):
            server._review_recovery_ticket(6, {"decision": "approved", "admin_note": "approved"},
                                           {"id": 3, "username": "Admin"})
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE account_recovery_tickets SET email_status='failed', email_error='email_provider_not_configured' WHERE id=6")

    def test_dry_run_preserves_all_data(self):
        before = self.db.read_bytes()
        with patch.object(server, "_send_recovery_email") as send:
            code, result = self.run_cli("resend", "6", "--reviewer", "id:3")
        self.assertEqual(code, 0)
        self.assertTrue(result["original_token_valid"])
        self.assertTrue(result["dry_run"])
        self.assertNotIn("reset_url", result)
        self.assertNotIn("example.invalid", str(result))
        send.assert_not_called()
        self.assertEqual(before, self.db.read_bytes())

    def test_success_preserves_approval_and_token_and_blocks_repeat(self):
        before = self.ticket()
        with patch.object(server, "_send_recovery_email") as send:
            code, result = self.run_cli("resend", "6", "--reviewer", "id:3", "--send")
            self.assertEqual(code, 0)
            self.assertEqual(result["email_status"], "sent")
            self.assertEqual(self.run_cli("resend", "6", "--send")[0], 1)
            send.assert_called_once()
        after = self.ticket()
        for key in ("reset_token_id", "reset_nonce", "reset_token_version", "reviewed_by",
                    "reviewed_at_epoch", "claim_until_epoch", "status", "contact_email", "admin_note"):
            self.assertEqual(before[key], after[key], key)

    def test_delivery_failure_is_not_retryable(self):
        with patch.object(server, "_send_recovery_email", side_effect=RuntimeError("private-secret")) as send:
            code, result = self.run_cli("resend", "6", "--send")
            self.assertEqual(code, 1)
            self.assertEqual(result["email_error"], "email_delivery_unavailable")
            self.assertNotIn("private-secret", str(result))
            self.assertEqual(self.run_cli("resend", "6", "--send")[0], 1)
            send.assert_called_once()

    def test_pending_and_uncertain_or_sent_are_refused(self):
        for state, error in (("sending", ""), ("sent", ""), ("failed", "email_delivery_unavailable")):
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE account_recovery_tickets SET email_status=?, email_error=? WHERE id=6", (state,error))
            with patch.object(server, "_send_recovery_email") as send:
                self.assertEqual(self.run_cli("resend", "6", "--send")[0], 1)
                send.assert_not_called()

    def test_invalid_used_expired_mismatched_token_refused(self):
        with sqlite3.connect(self.db) as conn:
            conn.row_factory=sqlite3.Row
            reset=dict(conn.execute("SELECT * FROM password_reset_tokens").fetchone())
        for sql in ("UPDATE password_reset_tokens SET used=1",
                    "UPDATE password_reset_tokens SET expires_at='2000-01-01 00:00:00'",
                    "UPDATE password_reset_tokens SET token='wrong'"):
            with sqlite3.connect(self.db) as conn:
                conn.execute(sql)
            with patch.object(server, "_send_recovery_email") as send:
                self.assertEqual(self.run_cli("resend", "6", "--send")[0], 1)
                send.assert_not_called()
            with sqlite3.connect(self.db) as conn:
                conn.execute("UPDATE password_reset_tokens SET used=?, expires_at=?, token=?",
                             (reset["used"],reset["expires_at"],reset["token"]))

    def test_config_failure_does_not_change_approval_or_retry_state(self):
        before=self.db.read_bytes()
        with patch.object(server, "_smtp_config", side_effect=server._McpError(-1,"config missing")):
            self.assertEqual(self.run_cli("resend","6","--send")[0],1)
        self.assertEqual(before,self.db.read_bytes())
        with sqlite3.connect(self.db) as conn:
            conn.execute("UPDATE account_recovery_tickets SET status='pending' WHERE id=6")
        before=self.db.read_bytes()
        with patch.object(server, "_smtp_config", side_effect=server._McpError(-1,"config missing")):
            self.assertEqual(self.run_cli("approve","6","--note","ok")[0],1)
        self.assertEqual(before,self.db.read_bytes())

    def test_concurrent_resend_has_one_sender(self):
        entered=threading.Event()
        release=threading.Event()
        results=[]
        def send(*args,**kwargs):
            entered.set()
            self.assertTrue(release.wait(5))
        def first():
            results.append(ops.resend(server,6,{"id":3},send=True))
        with patch.object(server,"_send_recovery_email",side_effect=send) as sender:
            thread=threading.Thread(target=first)
            thread.start()
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(ops.MailOperationError):
                    ops.resend(server,6,{"id":3},send=True)
            finally:
                release.set()
                thread.join(5)
            sender.assert_called_once()
        self.assertTrue(results[0]["ok"])

    def test_loader_failure_sanitized_and_no_environment_change(self):
        before=dict(os.environ)
        with patch.object(ops.subprocess,"check_output",return_value="0"):
            with self.assertRaises(ops.MailOperationError):
                ops.load_service_mail_environment(Path("/not-service"), self.db)
        self.assertEqual(before,dict(os.environ))

    def test_loader_allowlist_and_reject_database_mismatch(self):
        root=Path("/fake-root")
        payload=(b"CEDARTOY_SMTP_HOST=smtp.invalid\0CEDARTOY_SMTP_FROM=test@example.invalid\0"
                 b"CEDARTOY_SMTP_PASSWORD=secret-fixture\0TURTLE_SOUP_DB="+str(self.db).encode()
                 +b"\0UNRELATED_SECRET=not-copied\0")
        def read(path):
            return b"python3\0server.py\0" if path.name=="cmdline" else payload
        original_resolve=Path.resolve
        def resolve(path,*args,**kwargs):
            return root if str(path)=="/proc/123/cwd" else original_resolve(path,*args,**kwargs)
        with patch.object(ops.subprocess,"check_output",return_value="123"), patch.object(
                Path,"read_bytes",read), patch.object(Path,"resolve",resolve), patch.dict(os.environ,{},clear=True):
            ops.load_service_mail_environment(root,self.db)
            self.assertEqual(os.environ["CEDARTOY_SMTP_HOST"],"smtp.invalid")
            self.assertNotIn("UNRELATED_SECRET",os.environ)
            before=dict(os.environ)
            with self.assertRaises(ops.MailOperationError):
                ops.load_service_mail_environment(root,self.root/"other.db")
            self.assertEqual(before,dict(os.environ))
