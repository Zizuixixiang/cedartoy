"""Admin-only revisions: disposable account DBs and mocked email delivery."""
import concurrent.futures
import json
import sqlite3
import unittest
from unittest.mock import patch

import server
from cedar_backend import feedback_tickets
from tests_toy import test_feedback as fixtures


class AdminMessageTests(unittest.TestCase):
    _connect = fixtures.FeedbackTests._connect
    _add_user = fixtures.FeedbackTests._add_user
    _user = fixtures.FeedbackTests._user
    _token_for = fixtures.FeedbackTests._token_for
    setUp = fixtures.FeedbackTests.setUp
    submit = fixtures.FeedbackTests.submit
    call = fixtures.FeedbackTests.call
    http = fixtures.FeedbackTests.http

    def conversation(self, **extra):
        tid = self.submit(self.human_id, **extra)["ticket_id"]
        self.call("admin_review", ticket_id=tid, reply="原始答复：错称呼", confirm=True)
        data = self.call("admin_detail", ticket_id=tid)
        return tid, data["messages"][-1]["id"]

    def params(self, tid, mid, version=1, **extra):
        return dict(ticket_id=tid, message_id=mid, expected_version=version, confirm=True, **extra)

    def snapshot(self):
        with self._connect() as conn:
            # Normal token authentication records account activity; mutations
            # must leave every feedback table (including rate/cursors) alone.
            return '\n'.join(line for line in conn.iterdump() if 'feedback' in line)

    def test_edit_withdraw_audit_and_public_projection(self):
        tid, mid = self.conversation()
        before = self.store.detail(tid, self.human_id)
        self.assertTrue(self.call("admin_detail", ticket_id=tid)["messages"][-1]["can_manage"])
        result = self.call("admin_edit", **self.params(tid, mid, content="更正称呼"))
        self.assertEqual(result["message"]["version"], 2)
        self.assertTrue(result["message"]["edited"])
        public = self.store.detail(tid, self.human_id)
        self.assertEqual(public["messages"][-1]["content"], "更正称呼")
        self.assertEqual([m["id"] for m in before["messages"]], [m["id"] for m in public["messages"]])
        self.assertEqual(before["ticket"], public["ticket"])
        self.assertNotIn("原始答复", json.dumps(public, ensure_ascii=False))
        self.assertTrue({"version", "can_manage", "actor_id", "history"}.isdisjoint(public["messages"][-1]))
        code, result = self.http("withdraw", self.params(tid, mid, 2), self.admin_token, admin=True)
        self.assertEqual(code, 200)
        self.assertFalse(result["message"]["can_manage"])
        public = self.call("detail", token=self.human_token, ticket_id=tid)
        self.assertTrue(public["messages"][-1]["withdrawn"])
        self.assertEqual(public["messages"][-1]["content"], "此回复已撤回")
        audit = self.call("admin_detail", ticket_id=tid, message_id=mid)["history"]
        self.assertEqual([r["content"] for r in audit], ["原始答复：错称呼", "更正称呼", "此回复已撤回"])
        self.assertEqual([r["action"] for r in audit], ["original", "edit", "withdraw"])
        self.assertTrue(all(r["actor_id"] == self.admin_id and r["created_at_epoch"] for r in audit))
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT content FROM feedback_messages WHERE id=?", (mid,)).fetchone()[0], "原始答复：错称呼")

    def test_nonadmin_http_mcp_and_forgery_fail_before_any_store_access(self):
        tid, mid = self.conversation()
        before = self.snapshot()
        for operation in ("edit", "withdraw"):
            for token, expected in ((None, 401), ("invalid", 401), (self.human_token, 403), (self.ai_token, 403)):
                for target in (mid, mid + 9999):
                    body = self.params(tid, target, content="forged") if operation == "edit" else self.params(tid, target)
                    body.update(user_id=self.admin_id, actor_id=self.admin_id, role="admin", is_admin=True)
                    with self.subTest(operation=operation, token=token, target=target):
                        code, result = self.http(operation, body, token, admin=True)
                        self.assertEqual(code, expected)
                        self.assertNotIn("原始答复", str(result))
                        with self.assertRaises(server._McpError) as exc:
                            server._tool_feedback({"action": "admin_" + operation, "params": body}, path_token=token)
                        self.assertIn(exc.exception.code, (-32001, -32003))
        self.assertEqual(before, self.snapshot())

    def test_other_admin_player_messages_and_unknown_targets_have_same_denial(self):
        tid, mid = self.conversation()
        other = self._add_user("OtherAdmin")
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (other,))
        other_token = self._token_for(other)
        player = self.call("admin_detail", ticket_id=tid)["messages"][0]["id"]
        own_tid = self.submit(self.admin_id)["ticket_id"]
        own_player = self.call("admin_detail", ticket_id=own_tid)["messages"][0]["id"]
        # Even an administrator cannot alter their own player-role submission.
        before = self.snapshot()
        for operation in ("edit", "withdraw"):
            errors = []
            for token, target, ticket in ((other_token, mid, tid), (self.admin_token, player, tid),
                                          (self.admin_token, own_player, own_tid), (self.admin_token, 999999, tid),
                                          (self.admin_token, mid, tid+999)):
                body = self.params(ticket, target, **({"content": "bad"} if operation == "edit" else {}))
                status, error = self.http(operation, body, token, admin=True)
                self.assertEqual(status, 403)
                errors.append(error)
            self.assertTrue(all(error == errors[0] for error in errors))
        self.assertEqual(before, self.snapshot())

    def test_admin_identity_revocation_and_forged_fields(self):
        tid, mid = self.conversation()
        before = self.snapshot()
        for extra in ({"is_admin": True}, {"role": "admin"}, {"actor_id": self.admin_id}, {"user_id": self.admin_id}):
            status, _ = self.http("edit", self.params(tid, mid, content="bad", **extra), self.admin_token, admin=True)
            self.assertEqual(status, 400)
        self.assertEqual(before, self.snapshot())
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=0 WHERE id=?", (self.admin_id,))
        before = self.snapshot()
        self.assertEqual(self.http("withdraw", self.params(tid, mid), self.admin_token, admin=True)[0], 403)
        with self.assertRaises(server._McpError):
            self.store.revise_message(self.params(tid, mid), {"id": self.admin_id, "is_admin": True}, withdraw=True)
        self.assertEqual(before, self.snapshot())

    def test_actual_ai_admin_can_manage_own_reply_but_binding_does_not_grant_permission(self):
        tid = self.submit(self.human_id)["ticket_id"]
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (self.ai_id,))
        self.call("admin_review", token=self.ai_token, ticket_id=tid, reply="AI管理员原文", confirm=True)
        mid = self.call("admin_detail", token=self.ai_token, ticket_id=tid)["messages"][-1]["id"]
        self.call("admin_edit", token=self.ai_token, **self.params(tid, mid, content="AI管理员更正"))
        # The bound human still has only ordinary participant permissions.
        self.assertEqual(self.http("withdraw", self.params(tid, mid, 2), self.human_token, admin=True)[0], 403)
        self.call("admin_withdraw", token=self.ai_token, **self.params(tid, mid, 2))

    def test_rejected_mutation_never_runs_expiry_maintenance(self):
        tid, mid = self.conversation()
        before = self.snapshot()
        with patch.object(feedback_tickets, "time", **{"time.return_value": int(feedback_tickets.time.time()) + 30*86400}):
            self.assertEqual(self.http("withdraw", self.params(tid, mid+999), self.admin_token, admin=True)[0], 403)
        self.assertEqual(before, self.snapshot())

    def test_versions_concurrency_and_terminal_withdraw(self):
        tid, mid = self.conversation()
        def edit(text):
            return self.http("edit", self.params(tid, mid, content=text), self.admin_token, admin=True)[0]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            self.assertEqual(sorted(pool.map(edit, ["A", "B"])), [200, 409])
        before = self.snapshot()
        self.assertEqual(self.http("withdraw", self.params(tid, mid), self.admin_token, admin=True)[0], 409)
        self.assertEqual(before, self.snapshot())
        self.call("admin_withdraw", **self.params(tid, mid, 2))
        before = self.snapshot()
        for action in ("admin_edit", "admin_withdraw"):
            with self.assertRaises(server._McpError) as exc:
                self.call(action, **self.params(tid, mid, 3, **({"content": "restore"} if action == "admin_edit" else {})))
            self.assertEqual(exc.exception.code, -32009)
        self.assertEqual(before, self.snapshot())

    def test_validation_and_rate_limit_are_atomic(self):
        tid, mid = self.conversation()
        before = self.snapshot()
        for extra in ({"content": ""}, {"content": "  "}, {"content": "x"*4001}, {"content": "x\0"},
                      {"expected_version": True}, {"expected_version": 0}, {"confirm": 1}, {"message_id": True}):
            body = self.params(tid, mid, content="valid"); body.update(extra)
            self.assertEqual(self.http("edit", body, self.admin_token, admin=True)[0], 400)
        self.assertEqual(before, self.snapshot())
        with self._connect() as conn:
            now = int(feedback_tickets.time.time())
            conn.executemany("INSERT INTO feedback_review_limits VALUES (?,?)", [(self.admin_id, now)] * 120)
        before = self.snapshot()
        self.assertEqual(self.http("withdraw", self.params(tid, mid), self.admin_token, admin=True)[0], 429)
        self.assertEqual(before, self.snapshot())

    def test_originals_and_audit_are_append_only_and_migration_idempotent(self):
        tid, mid = self.conversation()
        self.call("admin_edit", **self.params(tid, mid, content="edit"))
        with self._connect() as conn:
            backup = sqlite3.connect(":memory:")
            self.addCleanup(backup.close)
            conn.backup(backup)
        before = '\n'.join(backup.iterdump())
        feedback_tickets.init_schema(backup)
        feedback_tickets.init_schema(backup)
        self.assertEqual(before, '\n'.join(backup.iterdump()))
        for sql in ("UPDATE feedback_messages SET content='overwrite'", "DELETE FROM feedback_messages",
                    "UPDATE feedback_messages SET created_at_epoch=0",
                    "INSERT OR REPLACE INTO feedback_messages SELECT * FROM feedback_messages",
                    "INSERT OR REPLACE INTO feedback_message_revisions SELECT * FROM feedback_message_revisions",
                    "UPDATE feedback_message_revisions SET content='overwrite'", "DELETE FROM feedback_message_revisions"):
            with self.assertRaises(sqlite3.IntegrityError):
                backup.execute(sql)
        self.assertEqual(before, '\n'.join(backup.iterdump()))

    def test_pre_revision_snapshot_upgrade_preserves_messages_cursors_and_rolls_back(self):
        tid, mid = self.conversation()
        self.store.detail(tid, self.human_id)
        with self._connect() as conn, sqlite3.connect(":memory:") as snapshot:
            conn.backup(snapshot)
            # Model the previous deployed schema, using only a disposable copy.
            for name, in snapshot.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'feedback_%'").fetchall():
                snapshot.execute(f'DROP TRIGGER {name}')
            snapshot.execute("DROP TABLE feedback_message_revisions")
            snapshot.commit()
            before = '\n'.join(snapshot.iterdump())
            snapshot.execute("BEGIN")
            feedback_tickets.init_schema(snapshot)
            snapshot.rollback()
            self.assertEqual(before, '\n'.join(snapshot.iterdump()))
            preserved = {name: snapshot.execute(f'SELECT * FROM {name}').fetchall() for name in
                         ('feedback_messages', 'feedback_tickets', 'feedback_read_cursors', 'feedback_unread_baseline')}
            feedback_tickets.init_schema(snapshot)
            feedback_tickets.init_schema(snapshot)
            for name, rows in preserved.items():
                self.assertEqual(rows, snapshot.execute(f'SELECT * FROM {name}').fetchall())
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(snapshot.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_audit_pagination_and_no_audit_through_public_detail_or_query(self):
        receipt = self.submit()
        tid = receipt["ticket_id"]
        self.call("admin_review", ticket_id=tid, reply="original", confirm=True)
        mid = self.call("admin_detail", ticket_id=tid)["messages"][-1]["id"]
        for version in range(1, 13):
            self.call("admin_edit", **self.params(tid, mid, version, content=f"revision {version+1}"))
        latest = self.call("admin_detail", ticket_id=tid, message_id=mid)
        older = self.call("admin_detail", ticket_id=tid, message_id=mid, before_version=latest["next_before_version"])
        self.assertEqual([r["version"] for r in older["history"]+latest["history"]], list(range(1,14)))
        self.assertIsNone(older["next_before_version"])
        self.call("admin_withdraw", **self.params(tid, mid, 13))
        public = self.http("query", {"query_code": receipt["query_code"], "message_id": mid, "is_admin": True})[1]
        self.assertNotIn("original", json.dumps(public))
        self.assertNotIn("revision", json.dumps(public))
        self.assertNotIn("history", public)
        for token in (self.human_token, self.ai_token):
            self.assertEqual(self.http("detail", {"ticket_id": tid, "message_id": mid}, token, admin=True)[0], 403)

    def test_unread_order_preview_and_mail_remain_stable(self):
        with patch.object(server, "_send_feedback_email") as send:
            self.store = server._feedback_store()
            tid, mid = self.conversation(notify_email=True, contact_email="fixture@example.invalid")
            send.assert_called_once()
            args = send.call_args
            unread = self.store.unread(self.human_id)
            listing = self.store.listing(self.human_id)
            with self._connect() as conn:
                mail = list(conn.execute("SELECT * FROM feedback_email_events"))
            self.call("admin_edit", **self.params(tid, mid, content="edited"))
            self.assertEqual(unread, self.store.unread(self.human_id))
            self.assertEqual(listing, self.store.listing(self.human_id))
            self.store.detail(tid, self.human_id)
            self.call("admin_withdraw", **self.params(tid, mid, 2))
            self.assertEqual(self.store.unread(self.human_id)["unread_count"], 0)
            self.assertEqual(self.store.detail(tid, self.human_id, mode="new")["messages"], [])
            with self._connect() as conn:
                self.assertEqual(mail, list(conn.execute("SELECT * FROM feedback_email_events")))
            send.assert_called_once_with(*args.args)

    def test_root_mcp_path_and_bearer_and_no_public_discovery(self):
        tid, mid = self.conversation()
        with patch.object(server, "_duel_unread_request_reminder", return_value=""), \
             patch.object(server, "_feedback_unread_reminder", return_value=""), \
             patch.object(server, "_mcp_forced_announcement", return_value=None):
            for i, transport in enumerate(("path_token", "bearer_token"), 1):
                for action in ("admin_edit", "admin_withdraw"):
                    params = self.params(tid, mid, i, **({"content": "updated"} if action == "admin_edit" else {}))
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                        "name": "feedback", "arguments": {"action": action, "params": params}}}
                    for token in (self.human_token, self.ai_token):
                        self.assertTrue(server._handle_root_mcp(payload, **{transport: token})["result"]["isError"])
                    if (i, action) in ((1, "admin_edit"), (2, "admin_withdraw")):
                        self.assertFalse(server._handle_root_mcp(payload, **{transport: self.admin_token})["result"]["isError"])
        for mode in ("standard", "legacy"):
            schema = json.dumps(server._root_tools(schema_mode=mode))
            for private in ("admin_edit", "admin_withdraw", "expected_version", "before_version"):
                self.assertNotIn(private, schema)
        for token in (None, self.human_token, self.ai_token):
            guide = server._tool_get_guide({"game": "feedback"}, path_token=token)
            for private in ("admin_edit", "admin_withdraw", "expected_version", "before_version"):
                self.assertNotIn(private, guide)


if __name__ == "__main__":
    unittest.main()
