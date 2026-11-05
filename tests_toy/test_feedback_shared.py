"""Shared feedback through real auth/HTTP/MCP, using only disposable account DBs."""
import concurrent.futures
import io
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from jsonschema import Draft202012Validator

import server
from cedar_backend import feedback_tickets
from tests_toy import test_feedback as fixtures
from tests_toy import test_feedback_unread as unread_fixtures


class SharedFeedbackTests(unittest.TestCase):
    setUp = fixtures.FeedbackTests.setUp
    _connect = fixtures.FeedbackTests._connect
    _add_user = fixtures.FeedbackTests._add_user
    _user = fixtures.FeedbackTests._user
    _token_for = fixtures.FeedbackTests._token_for
    submit = fixtures.FeedbackTests.submit
    http = fixtures.FeedbackTests.http
    mcp = unread_fixtures.FeedbackUnreadTests.mcp
    hints = unread_fixtures.FeedbackUnreadTests.hints

    def feedback(self, action, params=None, token=None, transport="path_token", legacy=False):
        args = {"action": action, "params": json.dumps(params or {}) if legacy else params or {}}
        result = self.mcp(args, token=token or self.ai_token, transport=transport, name="feedback")["result"]
        self.assertFalse(result["isError"], result)
        return json.loads(result["content"][0]["text"])

    def unread(self, actor):
        return self.store.unread(actor)["unread_count"]

    def test_human_submit_machine_read_reply_human_close_and_reverse(self):
        status, ticket = self.http("submit", {"category": "bug", "content": "人类提问"}, self.human_token)
        self.assertEqual(status, 200)
        tid = ticket["ticket_id"]
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (0, 1))
        self.assertEqual(self.feedback("list")["tickets"][0]["id"], tid)
        result = self.feedback("detail", {"ticket_id": tid})
        self.assertEqual([m["content"] for m in result["messages"]], ["人类提问"])
        self.assertEqual(result["messages"][0]["actor_kind"], "human")
        self.assertEqual(result["unread_count"], 0)
        self.feedback("reply", {"ticket_id": tid, "content": "小机补充"}, transport="bearer_token")
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (1, 0))
        status, detail = self.http("detail", {"ticket_id": tid}, self.human_token)
        self.assertEqual(status, 200)
        self.assertEqual(detail["messages"][-1]["actor_kind"], "ai")
        self.assertFalse(detail["messages"][-1]["is_self"])
        self.assertEqual(self.http("close", {"ticket_id": tid, "confirm": True}, self.human_token)[0], 200)
        receipt = self.feedback("submit", {"category": "suggestion", "content": "小机发起"}, legacy=True)
        tid2 = receipt["ticket_id"]
        self.assertIn(tid2, [t["id"] for t in self.http("list", token=self.human_token)[1]["tickets"]])
        self.assertEqual(self.http("message", {"ticket_id": tid2, "content": "人类补充"}, self.human_token)[0], 200)
        self.feedback("close", {"ticket_id": tid2, "confirm": True})
        self.assertEqual(self.http("detail", {"ticket_id": tid2}, self.human_token)[1]["ticket"]["status"], "closed")
        with self._connect() as conn:
            self.assertEqual([tuple(r) for r in conn.execute(
                "SELECT actor_id,actor_kind FROM feedback_messages WHERE ticket_id=? ORDER BY id", (tid2,))],
                [(self.ai_id, "ai"), (self.human_id, "human")])
            self.assertEqual(conn.execute("SELECT owner_id FROM feedback_tickets WHERE id=?", (tid2,)).fetchone()[0], self.ai_id)

    def test_many_bindings_one_hop_legacy_tickets_unbind_and_capability_isolation(self):
        # A-X-B and A-Y: X sees A/B tickets, B cannot see A/Y; X cannot see Y.
        other_ai = self._add_user("SecondMachine", is_ai=True)
        with self._connect() as conn:
            conn.execute("INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)", (self.other_human_id, self.ai_id))
            conn.execute("INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)", (self.human_id, other_ai))
        owners = [self.human_id, self.other_human_id, self.ai_id, other_ai]
        tickets = [self.submit(owner, ip=f"198.51.100.{i}")["ticket_id"] for i, owner in enumerate(owners)]
        expected = {self.human_id: {tickets[0], tickets[2], tickets[3]},
                    self.other_human_id: {tickets[1], tickets[2]},
                    self.ai_id: set(tickets[:3]), other_ai: {tickets[0], tickets[3]}}
        for viewer, visible in expected.items():
            self.assertEqual({t["id"] for t in self.store.listing(viewer)["tickets"]}, visible)
            for tid in set(tickets) - visible:
                for call in (lambda: self.store.detail(tid, viewer),
                             lambda: self.store.append({"ticket_id": tid, "content": "forged"}, viewer, "test"),
                             lambda: self.store.close({"ticket_id": tid, "confirm": True}, viewer)):
                    with self.assertRaises(server._McpError): call()
        self.store.append({"ticket_id": tickets[0], "content": "X写在A的工单"}, self.ai_id, "test")
        self.assertNotIn(tickets[0], {t["id"] for t in self.store.listing(self.other_human_id)["tickets"]})
        with self._connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE human_user_id=? AND ai_user_id=?", (self.human_id, self.ai_id))
        self.assertNotIn(tickets[0], {t["id"] for t in self.feedback("list")["tickets"]})
        for action, params in (("detail", {}), ("reply", {"content": "after unbind"}), ("close", {"confirm": True})):
            result = self.mcp({"action": action, "params": {"ticket_id": tickets[0], **params}}, name="feedback", token=self.ai_token)
            self.assertTrue(result["result"]["isError"])
        # Binding after creation grants access to historical tickets; no ownership migration.
        with self._connect() as conn:
            conn.execute("INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)", (self.human_id, self.ai_id))
        self.assertEqual(len(self.feedback("detail", {"ticket_id": tickets[0], "mode": "history"})["messages"]), 2)
        anon = self.submit(ip="198.51.100.90")
        for owner in owners:
            with self.assertRaises(server._McpError): self.store.detail(anon["ticket_id"], owner)
        self.assertEqual(self.store.query(anon["query_code"])["ticket"]["id"], anon["ticket_id"])
        self.assertEqual(self.http("message", {"ticket_id": tickets[0], "content": "fake", "query_code": anon["query_code"]}, self.human_token)[0], 404)

    def test_new_pages_skip_self_not_other_messages_and_history_does_not_swallow(self):
        tid = self.submit(self.ai_id)["ticket_id"]
        expected = []
        # Use real append/review with time moving past per-ticket rate windows.
        now = 1800000000
        for i in range(23):
            with patch.object(feedback_tickets.time, "time", return_value=now + i * 3700):
                if i % 2:
                    self.store.review({"ticket_id": tid, "reply": f"admin-{i}"}, {"id": self.admin_id})
                else:
                    self.store.append({"ticket_id": tid, "content": f"human-{i}"}, self.human_id, "human-ip")
                expected.append(self.store.detail(tid, admin=True)["messages"][-1]["id"])
                self.store.append({"ticket_id": tid, "content": f"self-{i}"}, self.ai_id, "ai-ip")
        self.assertEqual(self.unread(self.ai_id), 1)
        received = []
        for count in (10, 10, 3):
            data = self.feedback("detail", {"ticket_id": tid})
            self.assertEqual(len(data["messages"]), count)
            self.assertTrue(all(not m["is_self"] for m in data["messages"]))
            received.extend(m["id"] for m in data["messages"])
            self.assertEqual(data["has_more_new"], count == 10)
            self.assertEqual(self.unread(self.ai_id), int(count == 10))
        self.assertEqual(received, expected)
        self.assertEqual(self.feedback("detail", {"ticket_id": tid})["messages"], [])
        history = self.feedback("detail", {"ticket_id": tid, "mode": "history"})
        self.store.review({"ticket_id": tid, "reply": "after latest page"}, {"id": self.admin_id})
        all_ids = [m["id"] for m in history["messages"]]
        while history["next_before_id"]:
            history = self.feedback("detail", {"ticket_id": tid, "mode": "history", "before_id": history["next_before_id"]})
            all_ids = [m["id"] for m in history["messages"]] + all_ids
            self.assertEqual(self.unread(self.ai_id), 1)
        self.assertEqual(len(all_ids), 47)
        self.assertEqual(len(set(all_ids)), 47)
        self.assertEqual([m["content"] for m in self.feedback("detail", {"ticket_id": tid})["messages"]], ["after latest page"])

    def test_independent_read_notification_and_multiple_speakers(self):
        tid = self.submit(self.ai_id, content="PRIVATE SUBJECT")["ticket_id"]
        self.store.detail(tid, self.human_id)
        self.store.append({"ticket_id": tid, "content": "PRIVATE HUMAN"}, self.human_id, "human")
        self.store.review({"ticket_id": tid, "reply": "PRIVATE ADMIN"}, {"id": self.admin_id})
        first = self.mcp(name="missing", token=self.ai_token)
        self.assertEqual(len(self.hints(first)), 1)
        self.assertNotIn("PRIVATE", json.dumps(first))
        self.assertEqual(self.hints(self.mcp(name="missing", token=self.ai_token)), [])
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (1, 1))
        self.http("detail", {"ticket_id": tid}, self.human_token)
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (0, 1))
        # Another direct participant (legacy multi-binding) replies to the AI's ticket.
        self.store.append({"ticket_id": tid, "content": "participant admin, not review"}, self.admin_id, "other-human")
        self.assertEqual(len(self.hints(self.mcp(name="missing", token=self.ai_token))), 1)
        new = self.feedback("detail", {"ticket_id": tid})
        self.assertEqual([m["actor_kind"] for m in new["messages"]], ["human", "admin", "human"])
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (1, 0))
        self.store.append({"ticket_id": tid, "content": "self"}, self.ai_id, "ai")
        self.assertEqual(self.hints(self.mcp(name="missing", token=self.ai_token)), [])
        # Unbinding before a claim denies both notification and read, despite stored cursors.
        self.store.detail(tid, self.human_id)
        human_ticket = self.submit(self.human_id)["ticket_id"]
        with self._connect() as conn:
            conn.execute("DELETE FROM user_bindings WHERE human_user_id=? AND ai_user_id=?", (self.human_id, self.ai_id))
        self.assertEqual(self.unread(self.ai_id), 0)
        self.assertFalse(self.store.claim_notification(self.ai_id))
        with self.assertRaises(server._McpError): self.store.detail(human_ticket, self.ai_id)

    def test_parallel_new_reads_deliver_disjoint_batches_and_actual_actor_rate_limit(self):
        tid = self.submit(self.human_id)["ticket_id"]
        for i in range(24):
            self.store.review({"ticket_id": tid, "reply": f"reply-{i}"}, {"id": self.admin_id})
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            batches = list(pool.map(lambda _: self.store.detail(tid, self.ai_id, mode="new"), range(4)))
        ids = [m["id"] for b in batches for m in b["messages"]]
        self.assertEqual((len(ids), len(set(ids))), (25, 25))
        self.assertEqual((self.unread(self.human_id), self.unread(self.ai_id)), (1, 0))
        # Shared messages are rate-limited on their actual speaker, across owners/IPs.
        now = 1800000000
        with patch.object(feedback_tickets.time, "time", return_value=now):
            tids = [tid] + [self.submit(self.human_id, ip=f"new-{i}")["ticket_id"] for i in range(2)]
            for i in range(20):
                self.store.append({"ticket_id": tids[i % 3], "content": "machine"}, self.ai_id, f"ip-{i}")
            with self.assertRaises(server._McpError) as caught:
                self.store.append({"ticket_id": tids[0], "content": "over limit"}, self.ai_id, "new-ip")
            self.assertEqual(caught.exception.code, -32029)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM feedback_messages WHERE actor_id=?", (self.ai_id,)).fetchone()[0], 20)

    def test_strict_mcp_parameters_auth_http_path_bearer_and_legacy(self):
        tid = self.submit(self.human_id)["ticket_id"]
        for token in (None, "expired", server._jwt_encode({"user_id": self.ai_id, "is_ai": False})):
            result = self.mcp({"action": "submit", "params": {"category": "bug", "content": "fake"}}, name="feedback", token=token)
            self.assertTrue(result["result"]["isError"])
        for args in ({"action": "list", "token": self.human_token},
                     {"action": "list", "params": {"owner_id": self.human_id}},
                     {"action": "reply", "params": {"ticket_id": tid, "content": "fake", "actor_id": self.human_id}},
                     {"action": "detail", "params": {"ticket_id": tid, "query_code": "x" * 43}},
                     {"action": "detail", "params": {"ticket_id": tid, "mode": "new", "before_id": 999}},
                     {"action": "close", "params": {"ticket_id": tid, "confirm": 1}},
                     {"action": [], "params": {}}, {"action": "list", "params": "null"},
                     {"action": "list", "params": "bad json"}, {"action": "list", "params": []}):
            self.assertTrue(self.mcp(args, name="feedback", token=self.ai_token)["result"]["isError"], args)
        # Actual root HTTP transport, including legacy schema selection; no listening socket.
        for path, bearer, legacy in (("/" + self.ai_token, "", False), ("/mcp", self.ai_token, False),
                                      ("/mcp?schema=legacy", self.ai_token, True)):
            params = {"ticket_id": tid, "mode": "history"}
            raw = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
                "name": "feedback", "arguments": {"action": "detail", "params": json.dumps(params) if legacy else params}}}).encode()
            handler = object.__new__(server.CedarToyHandler)
            handler.path, handler.client_address = path, ("192.0.2.10", 1234)
            handler.headers = {"Content-Length": str(len(raw)), "Content-Type": "application/json"}
            if bearer: handler.headers["Authorization"] = "Bearer " + bearer
            handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
            handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
            with patch.object(server, "_check_request_rate_limit", return_value=True), patch.object(server, "_mcp_forced_announcement", return_value=None):
                handler.do_POST()
            result = json.loads(handler.wfile.getvalue())["result"]
            self.assertFalse(result["isError"], result)
            self.assertEqual(json.loads(result["content"][0]["text"])["ticket"]["id"], tid)

    def test_upgrade_keeps_old_cursor_baseline_and_adds_actor_kind_atomically(self):
        tid = self.submit(self.human_id)["ticket_id"]
        self.store.detail(tid, self.ai_id)
        with self._connect() as source, sqlite3.connect(":memory:") as snapshot:
            source.backup(snapshot)
            snapshot.execute("ALTER TABLE feedback_messages DROP COLUMN actor_kind")
            snapshot.commit()
            old = snapshot.execute("SELECT * FROM feedback_read_cursors").fetchall()
            baseline = snapshot.execute("SELECT * FROM feedback_unread_baseline").fetchall()
            snapshot.execute("BEGIN")
            feedback_tickets.init_schema(snapshot)
            snapshot.rollback()
            self.assertNotIn("actor_kind", {r[1] for r in snapshot.execute("PRAGMA table_info(feedback_messages)")})
            feedback_tickets.init_schema(snapshot)
            feedback_tickets.init_schema(snapshot)
            self.assertEqual(snapshot.execute("SELECT actor_kind FROM feedback_messages").fetchone()[0], "human")
            self.assertEqual(snapshot.execute("SELECT * FROM feedback_read_cursors").fetchall(), old)
            self.assertEqual(snapshot.execute("SELECT * FROM feedback_unread_baseline").fetchall(), baseline)
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(snapshot.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_account_feedback_actions_removed_and_guides_separated(self):
        for token in (None, self.human_token, self.ai_token, self.admin_token):
            for action in ("feedback_list", "feedback_detail", "admin_feedback_list",
                           "admin_feedback_detail", "admin_feedback_review", "admin_list", "admin_detail", "admin_review"):
                with self.subTest(action=action, token=bool(token)), self.assertRaises(server._McpError) as caught:
                    server._tool_account({"action": action, "token": token, "ticket_id": 1}, path_token=token)
                self.assertEqual(caught.exception.message, "未知 account action")
            guide = server._tool_get_guide({"game": "account"}, path_token=token)
            self.assertNotIn("feedback", guide)
            self.assertNotIn("反馈", guide)
        for mode in ("standard", "legacy"):
            tool = next(t for t in server._root_tools(schema_mode=mode) if t["name"] == "account")
            self.assertNotIn("feedback", json.dumps(tool))
            self.assertTrue({"status", "reply", "internal_note", "before_id"}.isdisjoint(tool["inputSchema"]["properties"]))
        public = server._tool_get_guide({"game": "feedback"})
        for token in (self.human_token, self.ai_token, "expired"):
            self.assertEqual(server._tool_get_guide({"game": "feedback", "token": self.admin_token}, path_token=token), public)
        self.assertNotIn("admin_", public)
        self.assertNotIn("internal_note", public)
        admin = server._tool_get_guide({"game": "feedback"}, path_token=self.admin_token)
        for action in ("admin_list", "admin_detail", "admin_review"):
            self.assertIn(action, admin)
        self.assertIn("internal_note", admin)
        with patch.object(server, "_require_admin_account", side_effect=sqlite3.OperationalError("fixture")):
            self.assertEqual(server._tool_get_guide({"game": "feedback"}, path_token=self.admin_token), public)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=0 WHERE id=?", (self.admin_id,))
        self.assertEqual(server._tool_get_guide({"game": "feedback"}, path_token=self.admin_token), public)
        for game in ("_feedback_admin", "feedback_admin"):
            with self.assertRaises(server._McpError): server._tool_get_guide({"game": game})

    def test_admin_feedback_tool_standard_legacy_privacy_and_real_authorization(self):
        tid = self.submit(self.human_id)["ticket_id"]
        for legacy in (False, True):
            for transport in ("path_token", "bearer_token"):
                cases = (("admin_list", {}), ("admin_detail", {"ticket_id": tid}),
                         ("admin_review", {"ticket_id": tid, "confirm": True, "reply": "public reply", "internal_note": "PRIVATE NOTE"}))
                for action, params in cases:
                    for token in (self.human_token, self.ai_token, self.admin_token):
                        args = {"action": action, "params": json.dumps(params) if legacy else params}
                        with patch.object(server, "_require_admin_account", wraps=server._require_admin_account) as require:
                            result = self.mcp(args, token=token, transport=transport, name="feedback")["result"]
                            require.assert_called_once_with(token)
                        self.assertEqual(result["isError"], token != self.admin_token, result)
                        if token != self.admin_token: self.assertNotIn("PRIVATE NOTE", json.dumps(result))
                public = self.feedback("detail", {"ticket_id": tid, "mode": "history"})
                self.assertNotIn("PRIVATE NOTE", json.dumps(public))
                private = self.feedback("admin_detail", {"ticket_id": tid}, token=self.admin_token, legacy=legacy, transport=transport)
                self.assertEqual(private["ticket"]["internal_note"], "PRIVATE NOTE")
        for params in ({"ticket_id": tid, "status": "closed"},
                       {"ticket_id": tid, "status": "closed", "confirm": True, "token": self.admin_token},
                       {"ticket_id": tid, "status": "closed", "confirm": True, "action": "list"}):
            result = self.mcp({"action": "admin_review", "params": params}, token=self.admin_token, name="feedback")
            self.assertTrue(result["result"]["isError"])
        # No aliases remain in the new tool either.
        self.assertTrue(self.mcp({"action": "admin_feedback_list"}, token=self.admin_token, name="feedback")["result"]["isError"])

    def test_unread_list_pagination_and_schema_thin_shell(self):
        with self._connect() as conn:
            feedback_tickets.init_schema(conn)
            for i in range(25):
                tid = conn.execute("INSERT INTO feedback_tickets(owner_id,ip_hash,category,content,created_at_epoch) VALUES (?,'fixture','bug','Q',1)", (self.human_id,)).lastrowid
                self.store._message(conn, tid, "player", self.human_id, "Q", 1)
        first = self.feedback("list", {"unread_only": True})
        second = self.feedback("list", {"unread_only": True, "page": 2})
        self.assertEqual((first["total"], first["unread_count"], len(first["tickets"]), len(second["tickets"])), (25, 25, 20, 5))
        self.assertEqual(self.feedback("unread")["unread_count"], 25)
        guide = server._tool_get_guide({"game": "feedback"})
        for mode in ("", "legacy"):
            tools = server._root_tools(schema_mode=mode)
            tool = next(t for t in tools if t["name"] == "feedback")
            self.assertIn('get_guide(game="feedback")', tool["description"])
            schema = tool["inputSchema"]
            self.assertEqual(set(schema["properties"]), {"action", "params"})
            self.assertEqual(schema["properties"]["params"]["type"], "string" if mode else "object")
            validator = Draft202012Validator(schema)
            # Open business fields survive schema validation; identity stays outside the envelope.
            params = {"ticket_id": 123, "confirm": True, "content": "正文", "unread_only": False}
            validator.validate({"action": "unread"})
            validator.validate({"action": "close", "params": json.dumps(params) if mode else params})
            for invalid in ({}, {"action": 1}, {"action": "list", "token": "forged"},
                            {"action": "list", "params": {} if mode else "{}"}):
                self.assertFalse(validator.is_valid(invalid), invalid)
            self.assertLess(len(json.dumps(schema)), 500)
            self.assertEqual(sum(t["name"] == "feedback" for t in tools), 1)
        for field in ("submit", "list", "unread", "detail", "reply", "close", "ticket_id", "before_id", "mode", "confirm"):
            self.assertIn(field, guide)


if __name__ == "__main__":
    unittest.main()
