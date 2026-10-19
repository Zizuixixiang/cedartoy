"""Unread rollout, transactional cursors and real HTTP/MCP auth in disposable DBs."""
import concurrent.futures
import json
import multiprocessing
import sqlite3
import threading
import unittest
from contextlib import closing
from unittest.mock import patch

import server
from cedar_backend.feedback_tickets import FeedbackStore, init_schema
from cedar_backend.errors import _McpError
from tests_toy import test_feedback as fixtures


def process_operation(args):
    path, owner, ticket, operation = args
    def connect():
        conn = sqlite3.connect(path, timeout=15)
        conn.row_factory = sqlite3.Row
        return conn
    store = FeedbackStore(connect, _McpError, lambda ip: ip)
    if operation == "claim":
        return store.claim_notification(owner)
    if operation == "read":
        return store.detail(ticket, owner)
    if operation == "reply":
        return store.review({"ticket_id": ticket, "reply": "process reply"}, {"id": 999})
    return store.unread(owner)


class FeedbackUnreadTests(unittest.TestCase):
    setUp = fixtures.FeedbackTests.setUp
    _connect = fixtures.FeedbackTests._connect
    _add_user = fixtures.FeedbackTests._add_user
    _user = fixtures.FeedbackTests._user
    _token_for = fixtures.FeedbackTests._token_for
    submit = fixtures.FeedbackTests.submit
    call = fixtures.FeedbackTests.call
    http = fixtures.FeedbackTests.http

    def reply(self, ticket):
        self.store.review({"ticket_id": ticket, "reply": "PRIVATE ADMIN REPLY"}, {"id": self.admin_id})

    def count(self, owner=None):
        return self.store.unread(self.human_id if owner is None else owner)["unread_count"]

    def mcp(self, arguments=None, *, token=None, transport="path_token", name="account", method="tools/call", request_id=1):
        payload = {"jsonrpc": "2.0", "method": method, "params": {"name": name, "arguments": arguments or {"action": "feedback_list"}}}
        if request_id is not None:
            payload["id"] = request_id
        with patch.object(server, "_duel_unread_request_reminder", return_value="GAME HINT"), patch.object(server, "_mcp_forced_announcement", return_value="ANNOUNCEMENT"):
            return server._handle_root_mcp(payload, **{transport: token})

    def hints(self, result):
        return [c["text"] for c in result.get("result", {}).get("content", []) if "有反馈未读" in c["text"]]

    def test_only_new_admin_messages_list_read_independence_and_repeat(self):
        a, b = [self.submit(self.human_id)["ticket_id"] for _ in range(2)]
        self.assertEqual(self.count(), 0)
        for body in ({"status": "processing"}, {"status": "closed"}, {"internal_note": "secret"}):
            self.store.review({"ticket_id": a, **body}, {"id": self.admin_id})
            self.assertEqual(self.count(), 0)
        self.store.append({"ticket_id": a, "content": "player followup"}, self.human_id, "192.0.2.8")
        self.assertEqual(self.count(), 0)
        self.reply(a); self.reply(b)
        self.assertEqual(self.count(), 2)
        self.assertTrue(self.store.claim_notification(self.human_id))
        self.assertFalse(self.store.claim_notification(self.human_id))
        self.assertTrue(all(t["unread"] for t in self.store.listing(self.human_id)["tickets"]))
        self.assertEqual(self.count(), 2)
        self.store.detail(a, admin=True)  # Admin inspection does not acknowledge the owner's reply.
        self.assertEqual(self.count(), 2)
        status, detail = self.http("detail", {"ticket_id": a}, self.human_token)
        self.assertEqual((status, detail["unread_count"], self.count()), (200, 1, 1))
        self.assertEqual(self.http("detail", {"ticket_id": b}, self.human_token)[1]["unread_count"], 0)
        self.reply(a)
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.store.claim_notification(self.human_id))
        self.reply(a)  # An unread but already-notified thread can receive a new hint.
        self.assertTrue(self.store.claim_notification(self.human_id))
        self.assertFalse(self.store.claim_notification(self.human_id))
        self.store.detail(a, self.human_id, before_id=2**62)
        self.assertEqual(self.count(), 1, "History requests cannot clear a new reply")
        with self.assertRaises(_McpError): self.store.detail(a, self.human_id, before_id=True)
        self.assertEqual(self.count(), 1)
        self.store.detail(a, self.human_id)
        self.assertEqual(self.count(), 0)

    def test_atomic_rollout_baseline_ignores_history_and_is_not_reinitialized(self):
        a = self.submit(self.human_id)["ticket_id"]
        self.reply(a)
        with self._connect() as conn:
            # Model the pre-feature append-only schema, using only this fixture.
            conn.execute("DROP TABLE feedback_read_cursors")
            conn.execute("DROP TABLE feedback_unread_baseline")
        with self._connect() as source, sqlite3.connect(":memory:") as copy:
            source.backup(copy)
            before = copy.execute("SELECT * FROM feedback_messages").fetchall()
            copy.execute("BEGIN")
            init_schema(copy)
            copy.rollback()
            self.assertFalse(copy.execute("SELECT 1 FROM sqlite_master WHERE name='feedback_unread_baseline'").fetchone())
            init_schema(copy); init_schema(copy)
            self.assertEqual(copy.execute("SELECT * FROM feedback_messages").fetchall(), before)
            self.assertEqual(copy.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(self.count(), 0)
        self.assertFalse(self.store.claim_notification(self.human_id))
        self.reply(a)
        self.assertEqual(self.count(), 1)
        self.assertTrue(self.store.claim_notification(self.human_id))
        with self._connect() as conn: init_schema(conn)
        self.assertEqual(self.count(), 1)
        self.assertFalse(server._feedback_store().claim_notification(self.human_id))

    def test_bound_human_ai_other_ai_anonymous_and_forged_identity_isolation(self):
        other_ai = self._add_user("OtherMachine", is_ai=True)
        other_token = self._token_for(other_ai)
        owners = [self.human_id, self.ai_id, other_ai, self.other_human_id]
        tickets = [self.submit(owner, ip=f"192.0.2.{i+40}")["ticket_id"] for i, owner in enumerate(owners)]
        anon = self.submit(ip="192.0.2.99")
        for tid in tickets + [anon["ticket_id"]]: self.reply(tid)
        for owner, token in zip(owners, [self.human_token, self.ai_token, other_token, self.other_human_token]):
            data = self.call("feedback_list", token=token)
            self.assertEqual([t["id"] for t in data["tickets"]], [tickets[owners.index(owner)]])
            self.assertEqual(data["unread_count"], 1)
            for tid in tickets + [anon["ticket_id"]]:
                if tid != tickets[owners.index(owner)]:
                    with self.assertRaises(_McpError): self.call("feedback_detail", token=token, ticket_id=tid)
            for field, value in (("owner_id", self.human_id), ("username", "Human"), ("player_id", self.human_id), ("query_code", anon["query_code"])):
                with self.assertRaises(_McpError): self.call("feedback_list", token=token, **{field: value})
        self.assertEqual(self.http("unread")[0], 401)
        self.assertEqual(self.http("unread", token="expired")[0], 401)
        self.assertEqual(self.http("unread", {"owner_id": self.ai_id}, self.human_token)[1], {"unread_count": 1})
        with self.assertRaises(_McpError):
            server._tool_account({"action": "feedback_detail", "ticket_id": tickets[1], "token": self.ai_token}, path_token=self.human_token)
        self.assertNotIn("unread_count", self.store.query(anon["query_code"]))
        self.assertTrue(all(self.count(owner) == 1 for owner in owners))
        with self.assertRaises(_McpError): self.store.detail(anon["ticket_id"], self.ai_id)
        self.assertFalse(server._feedback_unread_reminder(self.human_token))
        self.assertFalse(server._feedback_unread_reminder("expired"))

    def test_process_restart_and_concurrent_read_write_and_single_claim(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        self.reply(tid)
        args = (str(self.db_path), self.ai_id, tid)
        with concurrent.futures.ProcessPoolExecutor(max_workers=4, mp_context=multiprocessing.get_context("spawn")) as pool:
            self.assertEqual(sum(pool.map(process_operation, [(*args, "claim")]*8)), 1)

            self.assertEqual(pool.submit(process_operation, (*args, "unread")).result(), {"unread_count": 1})
            results = list(pool.map(process_operation, [(*args, "read"), (*args, "reply")]))
            seen = max(m["id"] for m in results[0]["messages"] if m["role"] == "admin")
            with self._connect() as conn:
                latest = conn.execute("SELECT max(id) FROM feedback_messages WHERE ticket_id=? AND role='admin'", (tid,)).fetchone()[0]
                cursor = conn.execute("SELECT read_id FROM feedback_read_cursors WHERE ticket_id=?", (tid,)).fetchone()[0]
            self.assertEqual(cursor, seen)
            self.assertEqual(self.count(self.ai_id), int(latest > seen))
            pool.submit(process_operation, (*args, "read")).result()
            self.assertEqual(pool.submit(process_operation, (*args, "unread")).result(), {"unread_count": 0})
            pool.submit(process_operation, (*args, "reply")).result()
            self.assertEqual(sum(pool.map(process_operation, [(*args, "claim")]*8)), 1)

    def test_no_pending_notification_only_selects_under_delete_mode_write_lock(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        other = self.submit(self.human_id)["ticket_id"]
        self.reply(other)  # Another identity's unread reply must not trigger a write.
        statements = []

        def connect():
            conn = sqlite3.connect(self.db_path)  # Real default timeout: 5 seconds.
            conn.row_factory = sqlite3.Row
            self.assertEqual(conn.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
            conn.set_trace_callback(statements.append)
            return conn

        store = FeedbackStore(connect, _McpError, lambda ip: ip)
        for state in ("no_reply", "already_notified", "already_read"):
            with self.subTest(state=state):
                if state == "already_notified":
                    self.reply(tid)
                    self.assertTrue(self.store.claim_notification(self.ai_id))
                elif state == "already_read":
                    self.reply(tid)
                    self.store.detail(tid, self.ai_id)
                statements.clear()
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    with closing(self._connect()) as writer:
                        self.assertEqual(writer.execute("PRAGMA journal_mode=DELETE").fetchone()[0], "delete")
                        writer.execute("BEGIN IMMEDIATE")
                        try:
                            # Must finish while the writer still owns its lock,
                            # well before the default SQLite busy timeout.
                            self.assertFalse(pool.submit(store.claim_notification, self.ai_id).result(timeout=1))
                        finally:
                            writer.rollback()
                self.assertTrue(statements)
                self.assertTrue(all(sql.lstrip().upper().startswith("SELECT ") for sql in statements), statements)

    def test_concurrent_claims_all_pass_precheck_but_only_one_claims(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        self.reply(tid)
        barrier = threading.Barrier(4, timeout=5)
        local = threading.local()
        unread_rows = self.store._unread_rows

        def synchronized_precheck(conn, owner):
            rows = unread_rows(conn, owner)
            if not getattr(local, "checked", False):
                local.checked = True
                self.assertFalse(conn.in_transaction)
                self.assertTrue(any(r["latest_id"] > r["notified_id"] for r in rows))
                barrier.wait()
            return rows

        with patch.object(self.store, "_unread_rows", side_effect=synchronized_precheck):
            with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
                self.assertEqual(sum(pool.map(self.store.claim_notification, [self.ai_id]*4)), 1)
        self.assertEqual(self.count(self.ai_id), 1)
        self.assertFalse(self.store.claim_notification(self.ai_id))

    def test_claim_rechecks_detail_read_and_new_reply_after_precheck(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        unread_rows = self.store._unread_rows
        for new_reply in (False, True):
            with self.subTest(new_reply=new_reply):
                self.reply(tid)
                checked = False
                seen = 0

                def interleave(conn, owner):
                    nonlocal checked, seen
                    rows = unread_rows(conn, owner)
                    if not checked:
                        checked = True
                        self.assertFalse(conn.in_transaction)
                        self.assertTrue(any(r["latest_id"] > r["notified_id"] for r in rows))
                        # Commit on separate connections between precheck and lock.
                        detail = server._feedback_store().detail(tid, self.ai_id)
                        seen = max(m["id"] for m in detail["messages"] if m["role"] == "admin")
                        if new_reply:
                            self.reply(tid)
                    return rows

                with patch.object(self.store, "_unread_rows", side_effect=interleave):
                    self.assertEqual(self.store.claim_notification(self.ai_id), new_reply)
                self.assertEqual(self.count(self.ai_id), int(new_reply))
                self.assertFalse(self.store.claim_notification(self.ai_id))
                with closing(self._connect()) as conn:
                    row = conn.execute("SELECT read_id,notified_id FROM feedback_read_cursors WHERE ticket_id=? AND owner_id=?",
                                       (tid, self.ai_id)).fetchone()
                    self.assertEqual(row["read_id"], seen)
                    if new_reply:
                        latest = conn.execute("SELECT max(id) FROM feedback_messages WHERE ticket_id=?", (tid,)).fetchone()[0]
                        self.assertGreater(latest, seen)
                        self.assertEqual(row["notified_id"], latest)
                    else:
                        self.assertEqual(row["notified_id"], 0)

    def test_reply_after_empty_precheck_is_claimed_on_next_call(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        unread_rows = self.store._unread_rows

        def reply_after_select(conn, owner):
            rows = unread_rows(conn, owner)
            self.assertEqual(rows, [])
            self.assertFalse(conn.in_transaction)
            self.reply(tid)
            return rows

        with patch.object(self.store, "_unread_rows", side_effect=reply_after_select):
            self.assertFalse(self.store.claim_notification(self.ai_id))
        self.assertTrue(self.store.claim_notification(self.ai_id))
        self.assertFalse(self.store.claim_notification(self.ai_id))

    def test_reminder_rejects_revoked_deleted_pending_or_wrong_type_credentials(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        self.reply(tid)
        wrong_type = server._jwt_encode({"user_id": self.ai_id, "username": "Human", "is_ai": False})
        with patch.object(server, "_feedback_store", wraps=server._feedback_store) as factory:
            for token in (None, "expired", self.human_token, wrong_type):
                self.assertEqual(server._feedback_unread_reminder(token), "")
            factory.assert_not_called()
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET ai_token_version=coalesce(ai_token_version,0)+1 WHERE id=?", (self.ai_id,))
        self.assertEqual(server._feedback_unread_reminder(self.ai_token), "")
        with self.assertRaises(_McpError): self.call("feedback_detail", token=self.ai_token, ticket_id=tid)
        self.assertEqual(self.count(self.ai_id), 1)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET ai_token_version=ai_token_version-1, deletion_requested_at_epoch=1, scheduled_delete_at_epoch=9999999999 WHERE id=?", (self.ai_id,))
        self.assertEqual(server._feedback_unread_reminder(self.ai_token), "")
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=NULL, scheduled_delete_at_epoch=NULL, deleted_at='fixture' WHERE id=?", (self.ai_id,))
        self.assertEqual(server._feedback_unread_reminder(self.ai_token), "")

    def test_mcp_one_hint_error_protocol_and_other_notifications_compatible(self):
        tid = self.submit(self.ai_id, content="PRIVATE SUBJECT")["ticket_id"]
        self.reply(tid)
        for method in ("initialize", "tools/list"):
            self.assertEqual(self.hints(self.mcp(method=method, token=self.ai_token)), [])
        self.assertEqual(self.hints(self.mcp(token=self.ai_token, name="missing", request_id=None)), [])
        first = self.mcp(token=self.ai_token, name="missing")
        self.assertTrue(first["result"]["isError"])
        self.assertEqual(len(self.hints(first)), 1)
        self.assertNotIn("PRIVATE", json.dumps(first))
        self.assertEqual([c["text"] for c in first["result"]["content"]][1:3], ["ANNOUNCEMENT", "GAME HINT"])
        self.assertEqual(self.hints(self.mcp(token=self.ai_token)), [])
        self.assertEqual(self.count(self.ai_id), 1, "A hint or list is not a detail read")
        detail = self.mcp({"action": "feedback_detail", "ticket_id": tid}, token=self.ai_token, transport="bearer_token")
        self.assertFalse(detail["result"]["isError"])
        self.assertEqual(self.count(self.ai_id), 0)
        self.reply(tid)
        with patch.object(server, "_tool_list_games", side_effect=RuntimeError("fixture failure")):
            generic = self.mcp(name="list_games", token=self.ai_token, transport="bearer_token")
        self.assertTrue(generic["result"]["isError"])
        self.assertEqual(len(self.hints(generic)), 1)
        self.assertEqual(self.hints(self.mcp(token=self.ai_token)), [])
        self.store.detail(tid, self.ai_id); self.reply(tid)
        # Reading first must suppress the not-yet-delivered hint as well.
        self.assertEqual(self.hints(self.mcp({"action": "feedback_detail", "ticket_id": tid}, token=self.ai_token)), [])
        self.reply(tid)
        with patch.object(FeedbackStore, "claim_notification", side_effect=sqlite3.OperationalError("fixture locked")):
            failed_hint = self.mcp(token=self.ai_token)
        self.assertFalse(failed_hint["result"]["isError"])
        self.assertEqual(len(self.hints(self.mcp(token=self.ai_token))), 1)
        self.assertIn("feedback_detail", server._tool_get_guide({"game": "account"}))
        properties = next(t for t in server._root_tools() if t["name"] == "account")["inputSchema"]["properties"]
        self.assertTrue({"page", "ticket_id", "before_id"}.isdisjoint(properties))

    def test_real_query_actions_path_bearer_explicit_and_legacy_fields(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        for transport in ("path_token", "bearer_token"):
            self.reply(tid)
            listing = self.mcp(token=self.ai_token, transport=transport)["result"]
            self.assertFalse(listing["isError"])
            self.assertEqual(json.loads(listing["content"][0]["text"])["unread_count"], 1)
            result = self.mcp({"action": "feedback_detail", "ticket_id": tid}, token=self.ai_token, transport=transport)["result"]
            self.assertFalse(result["isError"])
            self.assertEqual(json.loads(result["content"][0]["text"])["unread_count"], 0)
        self.reply(tid)
        legacy = next(t for t in server._root_tools(schema_mode="legacy") if t["name"] == "account")["inputSchema"]["properties"]
        params = {"action": "feedback_detail", "ticket_id": tid, "token": self.ai_token}
        forwarded = {k: v for k, v in params.items() if k in legacy}
        result = self.mcp(forwarded)["result"]
        self.assertFalse(result["isError"])
        self.assertEqual(json.loads(result["content"][0]["text"])["unread_count"], 0)


if __name__ == "__main__":
    unittest.main()
