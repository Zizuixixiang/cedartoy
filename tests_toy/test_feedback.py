"""Feedback security and transport tests; only disposable databases are touched."""
import concurrent.futures
import hashlib
import io
import json
import secrets
import sqlite3
import time
import unittest
from unittest.mock import Mock, patch

import admin_feedback_mcp
import server
from cedar_backend import feedback_tickets
from tests_toy import test_account_recovery as fixtures


class FeedbackTests(unittest.TestCase):
    _connect = fixtures.AccountRecoveryTests._connect
    _add_user = fixtures.AccountRecoveryTests._add_user
    _user = fixtures.AccountRecoveryTests._user
    _token_for = fixtures.AccountRecoveryTests._token_for

    def setUp(self):
        fixtures.AccountRecoveryTests.setUp(self)
        self.addCleanup(fixtures.AccountRecoveryTests.tearDown, self)
        self.store = server._feedback_store()
        self.admin_id = self._add_user("Admin")
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (self.admin_id,))
            conn.execute("INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)", (self.admin_id, self.ai_id))
        self.admin_token = self._token_for(self.admin_id)

    def submit(self, owner=None, ip="192.0.2.1", **extra):
        return self.store.submit({"category": "bug", "content": "问题 <script>alert(1)</script>", **extra}, owner, ip)

    def call(self, action, token=None, **params):
        return json.loads(server._tool_account({"action": action, **params}, path_token=token or self.admin_token))

    def http(self, operation, body=None, token=None, *, admin=False, rate=True, headers=None):
        raw = json.dumps(body or {}).encode()
        handler = object.__new__(server.CedarToyHandler)
        handler.path = ("/api/admin/feedback/" if admin else "/api/feedback/") + operation
        handler.headers = {"Content-Type": "application/json", "Content-Length": str(len(raw)), **(headers or {})}
        if token: handler.headers["Authorization"] = "Bearer " + token
        handler.rfile = io.BytesIO(raw)
        handler._send_json = Mock()
        handler._client_ip = Mock(return_value="192.0.2.9")
        with patch.object(server, "_check_request_rate_limit", return_value=rate):
            handler.do_POST()
        args = handler._send_json.call_args
        self.assertEqual(args.kwargs["extra_headers"]["Cache-Control"], "no-store")
        return args.kwargs.get("status", 200), args.args[0]

    def test_actual_login_ownership_ignores_self_report_and_binding(self):
        status, a = self.http("submit", {"category": "account", "content": "A", "contact": "B", "user_id": self.other_human_id,
                                      "owner_id": self.other_human_id, "player_id": str(self.ai_id)}, self.human_token)
        self.assertEqual(status, 200)
        self.assertNotIn("query_code", a)
        b = self.submit(self.other_human_id)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT owner_id FROM feedback_tickets WHERE id=?", (a["ticket_id"],)).fetchone()[0], self.human_id)
        self.assertEqual(self.http("list", {"owner_id": self.other_human_id}, self.human_token)[1]["total"], 1)
        self.assertEqual(self.http("detail", {"ticket_id": b["ticket_id"]}, self.human_token)[0], 404)
        self.assertEqual(self.http("detail", {"ticket_id": a["ticket_id"]}, self.ai_token)[0], 404)
        self.assertEqual(self.http("detail", {"ticket_id": a["ticket_id"]}, self.human_token)[0], 200)
        self.assertEqual(self.http("list", {}, None)[0], 401)
        self.assertEqual(self.http("detail", {"ticket_id": a["ticket_id"]})[0], 401)
        self.assertEqual(self.http("submit", {"category": "bug", "content": "expired"}, "invalid")[0], 401)

    def test_anonymous_code_is_high_entropy_hashed_and_single_ticket(self):
        receipt = self.submit()
        code = receipt["query_code"]
        self.assertRegex(code, r"^[A-Za-z0-9_-]{43}$")
        with self._connect() as conn:
            row = dict(conn.execute("SELECT * FROM feedback_tickets").fetchone())
            self.assertEqual(row["query_code_hash"], hashlib.sha256(code.encode()).hexdigest())
            self.assertNotIn(code, row.values())
        result = self.store.query(code)
        self.assertEqual(result["ticket"]["id"], receipt["ticket_id"])
        self.assertNotIn("tickets", result)
        for key in ("query_code_hash", "ip_hash", "internal_note", "owner_id", "reviewed_by"):
            self.assertNotIn(key, result["ticket"])
        for bad in (None, "", code[:-1], secrets.token_urlsafe(32), row["query_code_hash"], [code]):
            with self.assertRaises(server._McpError) as error:
                self.store.query(bad)
            self.assertEqual(error.exception.message, "未找到反馈或无权查看")
        self.assertEqual(self.http("query", {"query_code": code}, "expired")[0], 200)

    def test_admin_boundary_before_lookup_for_all_actions_and_revocation(self):
        for token in (None, "invalid", self.human_token, self.ai_token):
            for action in (*admin_feedback_mcp.ACTIONS, "admin_feedback_unknown"):
                with self.subTest(token=bool(token), action=action), patch.object(server, "_feedback_store") as factory:
                    with self.assertRaises(server._McpError) as error:
                        server._tool_account({"action": action, "ticket_id": 1, "is_admin": True}, path_token=token)
                    self.assertIn(error.exception.code, (-32001, -32003))
                    factory.return_value.listing.assert_not_called()
                    factory.return_value.detail.assert_not_called()
                    factory.return_value.review.assert_not_called()
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=0 WHERE id=?", (self.admin_id,))
        with self.assertRaises(server._McpError): self.call("admin_feedback_list")
        for action in ("list", "detail", "review"):
            self.assertEqual(self.http(action, {"ticket_id": 1}, self.human_token, admin=True)[0], 403)

    def test_review_public_reply_internal_note_and_state(self):
        receipt = self.submit()
        tid = receipt["ticket_id"]
        self.call("admin_feedback_review", ticket_id=tid, internal_note="私密备注", confirm=True)
        self.assertEqual(self.store.query(receipt["query_code"])["ticket"]["status"], "pending")
        for i in range(3):
            self.call("admin_feedback_review", ticket_id=tid, reply=f"公开回复 {i} <img src=x onerror=alert(1)>", confirm=True)
        public = self.store.query(receipt["query_code"])
        self.assertEqual(public["ticket"]["status"], "processing")
        self.assertEqual([m["role"] for m in public["messages"]], ["player", "admin", "admin", "admin"])
        self.assertEqual(public["message_count"], 4)
        self.assertNotIn("私密备注", json.dumps(public, ensure_ascii=False))
        private = self.call("admin_feedback_detail", ticket_id=tid)["ticket"]
        self.assertEqual(private["internal_note"], "私密备注")
        self.assertEqual(private["reviewed_by"], self.admin_id)
        self.assertNotIn("query_code_hash", private)
        self.assertNotIn("ip_hash", private)
        self.assertEqual(self.call("admin_feedback_list", status="processing")["total"], 1)
        self.assertIn("不可信", self.call("admin_feedback_list")["untrusted_text_notice"])
        self.assertEqual(self.http("review", {"ticket_id": tid, "status": "closed", "confirm": True}, self.admin_token, admin=True)[0], 200)

    def test_qa_rounds_close_reopen_and_login_anonymous_isolation(self):
        for owner, token in ((None, None), (self.human_id, self.human_token)):
            receipt = self.submit(owner, ip="192.0.2.3")
            tid = receipt["ticket_id"]
            capability = {"query_code": receipt["query_code"]} if owner is None else {}
            read = lambda: self.store.query(receipt["query_code"]) if owner is None else self.store.detail(tid, owner)
            initial = read()["messages"]
            self.call("admin_feedback_review", ticket_id=tid, reply="回答一", confirm=True)
            self.assertEqual(read()["ticket"]["status"], "processing")
            payload = {"ticket_id": tid, "content": "追问一", **capability}
            self.assertEqual(self.http("message", payload, token)[0], 200)
            self.assertEqual(read()["ticket"]["status"], "pending")
            for text in ("回答二", "补充答复"):
                self.call("admin_feedback_review", ticket_id=tid, reply=text, confirm=True)
            self.call("admin_feedback_review", ticket_id=tid, status="closed", confirm=True)
            self.assertEqual(read()["ticket"]["status"], "closed")
            self.assertEqual(self.http("message", {**payload, "content": "重新提问"}, token)[0], 200)
            data = read()
            self.assertEqual(data["ticket"]["status"], "pending")
            self.assertEqual(data["messages"][:1], initial)
            self.assertEqual([m["content"] for m in data["messages"]][1:], ["回答一", "追问一", "回答二", "补充答复", "重新提问"])
            self.assertEqual([m["id"] for m in data["messages"]], sorted(m["id"] for m in data["messages"]))
            self.call("admin_feedback_review", ticket_id=tid, status="closed", confirm=True)
            self.call("admin_feedback_review", ticket_id=tid, reply="继续答复", confirm=True)
            self.assertEqual(read()["ticket"]["status"], "processing")
        owned = self.submit(self.human_id, ip="192.0.2.5")["ticket_id"]
        anonymous = self.submit(ip="192.0.2.6")
        other = self.submit(ip="192.0.2.7")
        for tid in (owned, anonymous["ticket_id"]):
            self.assertEqual(self.http("message", {"ticket_id": tid, "content": "forged"}, self.ai_token)[0], 404)
            self.assertEqual(self.http("message", {"ticket_id": tid, "content": "anonymous"})[0], 401)
        for code in (None, "", "x"*43, other["query_code"]):
            self.assertEqual(self.http("message", {"ticket_id": anonymous["ticket_id"], "content": "bad", "query_code": code})[0], 404)
        for key in ("role", "owner_id", "status", "is_admin", "actor_id"):
            self.assertEqual(self.http("message", {"ticket_id": owned, "content": "fake admin", key: "admin"}, self.human_token)[0], 400)
        self.assertEqual(self.http("message", {"ticket_id": anonymous["ticket_id"], "query_code": anonymous["query_code"], "content": "原码继续"}, "expired")[0], 200)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT actor_id FROM feedback_messages WHERE ticket_id=? ORDER BY id DESC LIMIT 1", (anonymous["ticket_id"],)).fetchone()[0], None)
        for content in ("", " ", 123, "x"*4001):
            self.assertEqual(self.http("message", {"ticket_id": owned, "content": content}, self.human_token)[0], 400)

    def test_message_cursor_pagination_survives_new_messages(self):
        receipt = self.submit()
        tid = receipt["ticket_id"]
        for i in range(24): self.call("admin_feedback_review", ticket_id=tid, reply=str(i), confirm=True)
        first = self.call("admin_feedback_detail", ticket_id=tid)
        self.assertEqual(len(first["messages"]), 10)
        self.assertEqual(first["message_count"], 25)
        seen = list(first["messages"])
        self.call("admin_feedback_review", ticket_id=tid, reply="new during pagination", confirm=True)
        cursor = first["next_before_id"]
        while cursor:
            page = self.store.query(receipt["query_code"], before_id=cursor)
            seen = page["messages"] + seen
            cursor = page["next_before_id"]
        self.assertEqual(len(seen), 25)
        self.assertEqual(len({m["id"] for m in seen}), 25)
        self.assertEqual(seen[-1]["content"], "23")
        for bad in (True, 0, "1", 2**63):
            with self.assertRaises(server._McpError): self.call("admin_feedback_detail", ticket_id=tid, before_id=bad)
        foreign = self.submit(self.other_human_id)
        self.assertEqual(self.http("detail", {"ticket_id": foreign["ticket_id"], "before_id": 999}, self.human_token)[0], 404)

    def test_message_and_admin_edit_limits_are_persistent_atomic(self):
        receipt = self.submit()
        body = {"ticket_id": receipt["ticket_id"], "query_code": receipt["query_code"], "content": "追问"}
        def append(_):
            try: server._feedback_store().append(body, None, "192.0.2.1"); return True
            except server._McpError as exc:
                self.assertEqual(exc.code, -32029); return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as executor:
            self.assertEqual(sum(executor.map(append, range(12))), 9)
        with self.assertRaises(server._McpError): self.store.append(body, None, "192.0.2.2")
        with self._connect() as conn:
            conn.executemany("INSERT INTO feedback_review_limits VALUES (?,?)", [(self.admin_id,int(time.time()))]*120)
        with self.assertRaises(server._McpError) as error:
            self.call("admin_feedback_review", ticket_id=receipt["ticket_id"], status="closed", confirm=True)
        self.assertEqual(error.exception.code, -32029)
        self.assertEqual(self.store.query(receipt["query_code"])["ticket"]["status"], "pending")

    def test_player_message_limits_cannot_be_bypassed_by_new_tickets_or_ip_changes(self):
        a = self.submit(self.human_id)
        b = self.submit(self.human_id, ip="192.0.2.2")
        for ticket in (a, b):
            for _ in range(9):
                self.store.append({"ticket_id": ticket["ticket_id"], "content": "补充"}, self.human_id, "192.0.2.3")
        # 20 player messages including both initial questions; new ticket/IP cannot bypass owner limit.
        with self.assertRaises(server._McpError): self.submit(self.human_id, ip="192.0.2.4")
        with self._connect() as conn:
            conn.execute("UPDATE feedback_messages SET created_at_epoch=?", (int(time.time())-7200,))
            for _ in range(80):
                self.store._message(conn, a["ticket_id"], "player", self.human_id, "day", int(time.time())-7200, "other")
        with self.assertRaises(server._McpError):
            self.store.append({"ticket_id": b["ticket_id"], "content": "daily"}, self.human_id, "new-ip")

    def test_strict_fields_lengths_pagination_and_confirm(self):
        tid = self.submit()["ticket_id"]
        for action, params in (
            ("admin_feedback_list", {"page": True}), ("admin_feedback_list", {"page": "1"}),
            ("admin_feedback_list", {"page": 0}), ("admin_feedback_list", {"page": 1000001}),
            ("admin_feedback_list", {"status": []}), ("admin_feedback_list", {"user_id": 1}),
            ("admin_feedback_detail", {"ticket_id": True}), ("admin_feedback_detail", {"ticket_id": 2**63}),
            ("admin_feedback_review", {"ticket_id": tid, "status": "closed", "confirm": 1}),
            ("admin_feedback_review", {"ticket_id": tid, "status": "replied", "confirm": True}),
            ("admin_feedback_review", {"ticket_id": tid, "status": "closed", "confirm": True, "reply": None}),
            ("admin_feedback_review", {"ticket_id": tid, "status": "closed", "confirm": True, "reply": "x"*4001}),
            ("admin_feedback_review", {"ticket_id": tid, "status": "closed", "confirm": True, "reviewed_by": 2}),
        ):
            with self.subTest(action=action, params=str(params)[:100]), self.assertRaises(server._McpError): self.call(action, **params)
        for extra in ({"content": " "}, {"content": "x"*4001}, {"contact": "x"*121}, {"category": []}, {"content": 123}):
            with self.assertRaises(server._McpError): self.submit(**extra)
        for i in range(21): self.submit(ip=f"192.0.2.{i+30}")
        first, second = self.call("admin_feedback_list"), self.call("admin_feedback_list", page=2)
        self.assertEqual((first["total"], len(first["tickets"]), len(second["tickets"])), (22, 20, 2))
        self.assertFalse({t["id"] for t in first["tickets"]} & {t["id"] for t in second["tickets"]})
        self.assertTrue(all(len(t["preview"]) <= 120 for t in first["tickets"]))
        self.assertEqual(self.http("list", {"page": False}, self.human_token)[0], 400)

    def test_persistent_submission_limits_atomic_concurrency_and_request_limits(self):
        def attempt(_):
            try: self.submit(); return True
            except server._McpError as exc:
                self.assertEqual(exc.code, -32029); return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
            self.assertEqual(sum(executor.map(attempt, range(8))), 5)
        with self.assertRaises(server._McpError): server._feedback_store().submit({"category": "bug", "content": "restart"}, None, "192.0.2.1")
        for i in range(5): self.submit(self.human_id, ip=f"192.0.2.{i+30}")
        with self.assertRaises(server._McpError): self.submit(self.human_id, ip="198.51.100.2")
        self.assertEqual(self.http("query", {"query_code": "x"}, rate=False)[0], 429)
        self.assertEqual(self.http("submit", headers={"Content-Length": "65537"})[0], 413)
        self.assertEqual(self.http("submit", headers={"Content-Length": "-1"})[0], 413)
        self.assertEqual(self.http("submit", headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.http("submit", headers={"Transfer-Encoding": "chunked"})[0], 400)
        self.assertEqual(self.http("submit", headers={"Content-Length": "bad"})[0], 400)

    def test_daily_limit_and_invalid_json_shape(self):
        receipt = self.submit()
        with self._connect() as conn:
            conn.execute("UPDATE feedback_tickets SET created_at_epoch=?", (int(time.time())-7200,))
            row = conn.execute("SELECT ip_hash FROM feedback_tickets").fetchone()
            for _ in range(19):
                conn.execute("INSERT INTO feedback_tickets(ip_hash,category,content,created_at_epoch) VALUES (?,'bug','daily',?)",
                             (row["ip_hash"], int(time.time())-7200))
        with self.assertRaises(server._McpError) as error: self.submit()
        self.assertEqual(error.exception.code, server.RATE_LIMIT_ERROR_CODE)
        self.assertEqual(self.http("submit", ["not an object"])[0], 400)
        self.assertEqual(self.store.query(receipt["query_code"])["ticket"]["status"], "pending")

    def test_idempotent_migration_snapshot_preserves_recovery(self):
        old = server._submit_recovery_ticket({"account_kind": "username", "account": "Human", "machine": "Machine", "registered_about": "2026", "games": "花园", "explanation": "忘记"}, "192.0.2.6")
        receipt = self.submit()
        with self._connect() as source, sqlite3.connect(":memory:") as snapshot:
            source.backup(snapshot)
            before = snapshot.execute("SELECT * FROM account_recovery_tickets").fetchall()
            feedback_before = snapshot.execute("SELECT * FROM feedback_tickets").fetchall()
            feedback_tickets.init_schema(snapshot); feedback_tickets.init_schema(snapshot)
            self.assertEqual(snapshot.execute("SELECT * FROM account_recovery_tickets").fetchall(), before)
            self.assertEqual(snapshot.execute("SELECT * FROM feedback_tickets").fetchall(), feedback_before)
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(server._query_recovery_ticket({"query_code": old["query_code"]})["status"], "pending")
        self.assertEqual(self.store.query(receipt["query_code"])["ticket"]["status"], "pending")

    def test_v1_upgrade_preserves_all_states_messages_and_capabilities(self):
        # This is the actual initial schema, not derived from the new schema.
        with self._connect() as conn:
            conn.execute("""CREATE TABLE feedback_tickets (
                id INTEGER PRIMARY KEY AUTOINCREMENT, owner_id INTEGER, query_code_hash TEXT UNIQUE,
                ip_hash TEXT NOT NULL, category TEXT NOT NULL, content TEXT NOT NULL,
                contact TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','processing','replied','closed')),
                reply TEXT NOT NULL DEFAULT '', internal_note TEXT NOT NULL DEFAULT '', reviewed_by INTEGER,
                created_at_epoch INTEGER NOT NULL, updated_at_epoch INTEGER)""")
            codes = [secrets.token_urlsafe(32) for _ in range(4)]
            for i, status in enumerate(("pending", "processing", "replied", "closed")):
                conn.execute("INSERT INTO feedback_tickets VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (i+1,None,hashlib.sha256(codes[i].encode()).hexdigest(),'ip','bug','原问题','参考账号',status,
                     '原回答' if i else '', '仅内部', self.admin_id,1700000000,1700000001))
        with self._connect() as conn:
            conn.execute("UPDATE sqlite_sequence SET seq=900 WHERE name='feedback_tickets'")
        with self._connect() as source, sqlite3.connect(":memory:") as snapshot:
            source.backup(snapshot)
            snapshot.execute("PRAGMA foreign_keys=ON")
            snapshot.execute("BEGIN")
            feedback_tickets.init_schema(snapshot)
            snapshot.rollback()
            self.assertIn('reply', {row[1] for row in snapshot.execute("PRAGMA table_info(feedback_tickets)")})
            feedback_tickets.init_schema(snapshot)
            before = snapshot.execute("SELECT * FROM feedback_messages").fetchall()
            feedback_tickets.init_schema(snapshot)
            self.assertEqual(snapshot.execute("SELECT * FROM feedback_messages").fetchall(), before)
            self.assertEqual(len(before), 7)
            self.assertEqual(snapshot.execute("SELECT seq FROM sqlite_sequence WHERE name='feedback_tickets'").fetchone()[0], 900)
            self.assertEqual(snapshot.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertNotIn('reply', {row[1] for row in snapshot.execute("PRAGMA table_info(feedback_tickets)")})
            with self.assertRaises(sqlite3.IntegrityError): snapshot.execute("UPDATE feedback_tickets SET status='replied'")
        for i, code in enumerate(codes):
            data = self.store.query(code)
            self.assertEqual(data["ticket"]["id"], i+1)
            self.assertEqual(data["ticket"]["status"], ("pending", "processing", "processing", "closed")[i])
            self.assertEqual([m["content"] for m in data["messages"]], ["原问题", "原回答"] if i else ["原问题"])
            self.assertNotIn("仅内部", json.dumps(data,ensure_ascii=False))
        self.store.append({"ticket_id": 4,"query_code":codes[3],"content":"旧码追问"},None,"192.0.2.99")
        self.assertEqual(self.store.query(codes[3])["ticket"]["status"], "pending")

    def test_migration_failure_rolls_back_without_losing_legacy_table(self):
        # Force an error after copying rows, so atomicity covers rename/drop as well.
        with sqlite3.connect(":memory:") as conn:
            conn.execute("""CREATE TABLE feedback_tickets (
                id INTEGER PRIMARY KEY,owner_id INTEGER,query_code_hash TEXT,ip_hash TEXT,
                category TEXT,content TEXT,contact TEXT,status TEXT,reply TEXT,internal_note TEXT,
                reviewed_by INTEGER,created_at_epoch INTEGER,updated_at_epoch INTEGER)""")
            conn.execute("INSERT INTO feedback_tickets VALUES (1,NULL,'hash','ip','bug','Q','','replied','A','private',2,1,2)")
            conn.commit()
            before = conn.execute("SELECT * FROM feedback_tickets").fetchall()
            conn.set_authorizer(lambda action, arg1, *args: sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_DROP_TABLE else sqlite3.SQLITE_OK)
            with self.assertRaises(sqlite3.DatabaseError): feedback_tickets.init_schema(conn)
            conn.set_authorizer(lambda *args: sqlite3.SQLITE_OK)
            self.assertEqual(conn.execute("SELECT * FROM feedback_tickets").fetchall(), before)
            self.assertEqual(conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(), [('feedback_tickets',)])
            feedback_tickets.init_schema(conn)
            self.assertEqual(conn.execute("SELECT content FROM feedback_messages ORDER BY id").fetchall(), [('Q',), ('A',)])

    def test_mcp_path_bearer_and_guide_compact_schema(self):
        tid = self.submit()["ticket_id"]
        with patch.object(server, "_duel_unread_request_reminder", return_value=""), patch.object(server, "_mcp_forced_announcement", return_value=None):
            for transport in ("path_token", "bearer_token"):
                for arguments in ({"action": "admin_feedback_list"}, {"action": "admin_feedback_detail", "ticket_id": tid},
                                  {"action": "admin_feedback_review", "ticket_id": tid, "status": "closed", "confirm": True}):
                    payload = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "account", "arguments": arguments}}
                    self.assertTrue(server._handle_root_mcp(payload, **{transport: self.ai_token})["result"]["isError"])
                    self.assertFalse(server._handle_root_mcp(payload, **{transport: self.admin_token})["result"]["isError"])
        public = server._tool_get_guide({"game": "account"})
        guide = server._tool_get_guide({"game": "account"}, path_token=self.admin_token)
        for action in admin_feedback_mcp.ACTIONS:
            self.assertNotIn(action, public)
            self.assertIn(action, guide)
        root = next(t for t in server._root_tools() if t["name"] == "account")
        self.assertEqual(set(root["inputSchema"]["properties"]), {"action"})
        self.assertNotIn("admin_feedback", json.dumps(root))
        legacy = next(t for t in server._root_tools(schema_mode="legacy") if t["name"] == "account")
        for name in ("status", "reply", "internal_note"):
            self.assertEqual(legacy["inputSchema"]["properties"][name]["type"], "string")


if __name__ == "__main__": unittest.main()
