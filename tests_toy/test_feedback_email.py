"""Opt-in feedback mail: isolated DBs, mocked delivery, no external messages."""
import unittest
from unittest.mock import patch

import server
from cedar_backend import account_email
from tests_toy import test_feedback as fixtures


class FeedbackEmailTests(unittest.TestCase):
    _connect = fixtures.FeedbackTests._connect
    _add_user = fixtures.FeedbackTests._add_user
    _user = fixtures.FeedbackTests._user
    _token_for = fixtures.FeedbackTests._token_for
    setUp = fixtures.FeedbackTests.setUp
    submit = fixtures.FeedbackTests.submit
    call = fixtures.FeedbackTests.call

    def _events(self, ticket_id):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT kind,status,error FROM feedback_email_events WHERE ticket_id=? ORDER BY id",
                (ticket_id,),
            )]

    def test_opt_in_only_public_reply_or_new_closure(self):
        with patch.object(server, "_send_feedback_email") as send:
            self.store = server._feedback_store()
            ticket = self.submit(notify_email=True, contact_email=" 1452010907@QQ.COM ")
            tid = ticket["ticket_id"]
            with self._connect() as conn:
                row = conn.execute("SELECT contact_email FROM feedback_tickets WHERE id=?", (tid,)).fetchone()
                self.assertEqual(row[0], "1452010907@qq.com")
            self.assertNotIn("contact_email", self.store.query(ticket["query_code"])["ticket"])
            self.call("admin_feedback_review", ticket_id=tid, internal_note="内部", confirm=True)
            self.call("admin_feedback_review", ticket_id=tid, status="processing", confirm=True)
            send.assert_not_called()
            self.assertEqual(self._events(tid), [])

            self.call("admin_feedback_review", ticket_id=tid, reply="已修好", confirm=True)
            send.assert_called_once_with("1452010907@qq.com", tid, "已修好", False)
            self.call("admin_feedback_review", ticket_id=tid, status="closed", confirm=True)
            self.call("admin_feedback_review", ticket_id=tid, status="closed", confirm=True)
            self.assertEqual(send.call_count, 2)
            send.assert_any_call("1452010907@qq.com", tid, None, True)

            self.call("admin_feedback_review", ticket_id=tid, reply="补充说明", status="closed", confirm=True)
            send.assert_any_call("1452010907@qq.com", tid, "补充说明", True)
            self.assertEqual([(e["kind"], e["status"]) for e in self._events(tid)],
                             [("reply", "sent"), ("closed", "sent"), ("reply", "sent")])

    def test_unchecked_never_sends_and_invalid_address_rejected(self):
        with patch.object(server, "_send_feedback_email") as send:
            self.store = server._feedback_store()
            ticket = self.submit(notify_email=False, contact_email="not-an-email")
            self.call("admin_feedback_review", ticket_id=ticket["ticket_id"], reply="答复", confirm=True)
            self.assertEqual(self._events(ticket["ticket_id"]), [])
            send.assert_not_called()
            for args in (
                {"notify_email": "true", "contact_email": "a@example.com"},
                {"notify_email": True, "contact_email": ""},
                {"notify_email": True, "contact_email": "invalid"},
            ):
                with self.subTest(args=args), self.assertRaises(server._McpError):
                    self.submit(**args)

    def test_delivery_failure_keeps_review_and_records_reason(self):
        with patch.object(server, "_send_feedback_email", side_effect=OSError("private provider detail")) as send:
            self.store = server._feedback_store()
            ticket = self.submit(notify_email=True, contact_email="nanshan2368@gmail.com")
            tid = ticket["ticket_id"]
            self.assertEqual(self.call("admin_feedback_review", ticket_id=tid,
                                       reply="已处理", confirm=True)["ok"], True)
            send.assert_called_once()
            self.assertEqual(self._events(tid)[0]["status"], "failed")
            self.assertEqual(self._events(tid)[0]["error"], "email_delivery_unavailable")
            detail = self.store.query(ticket["query_code"])
            self.assertEqual(detail["ticket"]["status"], "processing")
            self.assertEqual(detail["messages"][-1]["content"], "已处理")

    def test_template_uses_separate_sender_and_no_internal_data(self):
        config = {"sender": "4399_notice@cedarstar.org", "security": "ssl"}
        with patch.object(server, "_smtp_config", return_value=config), patch.object(
            account_email, "_send_email_message"
        ) as transport:
            server._send_feedback_email("nanshan2368@gmail.com", 37, "处理完毕", True)
            message = transport.call_args.args[0]
            self.assertEqual(message["From"], "4399_notice@cedarstar.org")
            self.assertEqual(message["To"], "nanshan2368@gmail.com")
            self.assertIn("37", message["Subject"])
            self.assertIn("处理完毕", message.get_content())
            self.assertIn("已结束", message.get_content())


if __name__ == "__main__":
    unittest.main()
