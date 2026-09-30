"""Recovery tickets use a temporary account DB; no production data or mail."""
import concurrent.futures
import sqlite3
import time
import unittest
from unittest.mock import Mock, patch

import server
from tests_toy import test_account_security_round2 as fixtures


class AccountRecoveryTests(unittest.TestCase):
    def setUp(self):
        fixtures.AccountSecurityRoundTwoTests.setUp(self)
        with self._connect() as conn:
            server.account_deletion.init_schema(conn)

    tearDown = fixtures.AccountSecurityRoundTwoTests.tearDown
    _connect = fixtures.AccountSecurityRoundTwoTests._connect
    _add_user = fixtures.AccountSecurityRoundTwoTests._add_user
    _user = fixtures.AccountSecurityRoundTwoTests._user
    _token_for = fixtures.AccountSecurityRoundTwoTests._token_for
    _bind_email = fixtures.AccountSecurityRoundTwoTests._bind_email
    _latest_code = fixtures.AccountSecurityRoundTwoTests._latest_code
    _age_all_codes = fixtures.AccountSecurityRoundTwoTests._age_all_codes

    def submit(self, account="Human", ip="192.0.2.10", **extra):
        body = dict(account_kind="username", account=account, machine="Machine / 3",
                    registered_about="2026 年 8 月", games="海龟汤、花园", explanation="记不清日期")
        body.update(extra)
        result = server._submit_recovery_ticket(body, ip)
        body["query_code"] = result["query_code"]
        with self._connect() as conn:
            row = conn.execute("SELECT id FROM account_recovery_tickets WHERE query_code_hash=?",
                               (server._recovery_query_hash(body),)).fetchone()
        self.assertEqual(result["ticket_id"], row[0])
        self.assertRegex(result["query_code"], r"^[A-Za-z0-9]{8}$")
        self.assertEqual(result["message"], "请保存查询码，用于查看审核结果；一般会在24小时内完成审核")
        return body, row[0]

    def approve(self, ticket_id, decision="approved", note="核验通过"):
        return server._review_recovery_ticket(ticket_id, {"decision": decision, "admin_note": note}, {"id": 999})

    def tokens(self):
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM password_reset_tokens ORDER BY id")]

    def test_schema_is_idempotent_and_does_not_rewrite_existing_data(self):
        body, _ = self.submit()
        with self._connect() as conn:
            server._init_password_reset_tokens_table(conn)
            server._init_password_reset_tokens_table(conn)
            self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        self.assertEqual(server._query_recovery_ticket(body)["status"], "pending")

    def test_unfinished_schema_upgrade_preserves_rows_on_consistent_copy(self):
        self.submit()
        with self._connect() as source, sqlite3.connect(":memory:") as snapshot:
            source.backup(snapshot)
            snapshot.execute("ALTER TABLE account_recovery_tickets RENAME COLUMN query_code_hash TO access_hash")
            before = snapshot.execute("SELECT * FROM account_recovery_tickets").fetchall()
            server._init_account_recovery_table(snapshot)
            server._init_account_recovery_table(snapshot)
            self.assertEqual(snapshot.execute("SELECT * FROM account_recovery_tickets").fetchall(), before)
            self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            columns = {row[1] for row in snapshot.execute("PRAGMA table_info(account_recovery_tickets)")}
            self.assertIn("query_code_hash", columns)
            self.assertNotIn("access_hash", columns)

    def test_approval_lazily_issues_hashed_24h_token_and_completes(self):
        body, ticket_id = self.submit()
        self.assertEqual(server._query_recovery_ticket(body)["status"], "pending")
        self.approve(ticket_id)
        self.assertEqual(self.tokens(), [])
        result = server._query_recovery_ticket(body)
        token = result["reset_url"].split("=", 1)[1]
        self.assertAlmostEqual(result["expires_at_epoch"] - time.time(), 86400, delta=3)
        self.assertAlmostEqual(result["claim_until_epoch"] - time.time(), 7 * 86400, delta=3)
        self.assertEqual(server._query_recovery_ticket(body), result)
        self.assertEqual(len(self.tokens()), 1)
        stored = self.tokens()[0]["token"]
        self.assertEqual(stored, server._reset_token_hash(token))
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(stored, "unsafe-hash-pass")
        with self._connect() as conn:
            ticket = dict(conn.execute("SELECT * FROM account_recovery_tickets").fetchone())
        self.assertNotIn(body["query_code"], ticket.values())
        self.assertNotIn(token, ticket.values())
        server._reset_password_by_token(token, "recovered-pass")
        self.assertTrue(server._verify_password("recovered-pass", self._user(self.human_id)["password_hash"]))
        self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")
        self.assertNotIn("reset_url", server._query_recovery_ticket(body))
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(token, "reused-pass")
        self.assertEqual(len(self.tokens()), 1)

    def test_expiry_reissues_atomically_and_invalidates_old_link(self):
        body, tid = self.submit()
        self.approve(tid)
        first = server._query_recovery_ticket(body)
        with self._connect() as conn:
            conn.execute("UPDATE password_reset_tokens SET expires_at=datetime('now', '-1 second')")
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(first["reset_url"].split("=", 1)[1], "expired-pass")
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: server._query_recovery_ticket(body), range(2)))
        self.assertEqual(results[0]["reset_url"], results[1]["reset_url"])
        self.assertNotEqual(first["reset_url"], results[0]["reset_url"])
        self.assertEqual(len(self.tokens()), 2)
        self.assertEqual(self.tokens()[0]["used"], 1)
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(first["reset_url"].split("=", 1)[1], "old-pass")

    def test_window_expiry_prevents_new_tokens_but_preserves_issued_24h(self):
        body, tid = self.submit()
        self.approve(tid)
        first = server._query_recovery_ticket(body)
        with self._connect() as conn:
            conn.execute("UPDATE account_recovery_tickets SET claim_until_epoch=?", (int(time.time()),))
        self.assertEqual(server._query_recovery_ticket(body)["reset_url"], first["reset_url"])
        with self._connect() as conn:
            conn.execute("UPDATE password_reset_tokens SET expires_at=datetime('now')")
        self.assertEqual(server._query_recovery_ticket(body)["status"], "expired")
        self.assertEqual(len(self.tokens()), 1)
        fresh, fresh_id = self.submit(query_code=body["query_code"])
        self.assertNotEqual(tid, fresh_id)
        self.assertNotEqual(body["query_code"], fresh["query_code"])

    def test_unclaimed_approval_expires_without_issuing_any_token(self):
        body, tid = self.submit()
        self.approve(tid)
        with self._connect() as conn:
            conn.execute("UPDATE account_recovery_tickets SET claim_until_epoch=?", (int(time.time()),))
        self.assertEqual(server._query_recovery_ticket(body)["status"], "expired")
        self.assertEqual(self.tokens(), [])

    def test_missing_wrong_cross_account_credentials_reveal_nothing(self):
        body, tid = self.submit()
        self.approve(tid, note="仅本人可见的备注")
        other, _ = self.submit("OtherHuman", ip="192.0.2.11")
        messages = []
        for account, credential in [("Human", ""), ("Human", "Wrong123"), ("Human", other["query_code"]),
                                    ("OtherHuman", body["query_code"]), ("Missing", body["query_code"]), ("", body["query_code"]), ("Human", None), ("Human", "x" * 43)]:
            with self.assertRaises(server._McpError) as caught:
                server._query_recovery_ticket({"account": account, "query_code": credential})
            messages.append(caught.exception.message)
        self.assertEqual(len(set(messages)), 1)
        self.assertEqual(self.tokens(), [])
        self.assertEqual(server._query_recovery_ticket(body)["admin_note"], "仅本人可见的备注")

    def test_submit_nonexistent_and_duplicate_without_secret_is_indistinguishable(self):
        body, tid = self.submit()
        duplicate, duplicate_id = self.submit()
        missing, missing_id = self.submit("Missing")
        self.assertNotEqual(tid, duplicate_id)
        self.assertNotEqual(body["query_code"], duplicate["query_code"])
        self.assertEqual(server._query_recovery_ticket(duplicate)["status"], "pending")
        self.assertEqual(server._query_recovery_ticket(missing)["status"], "pending")
        with self.assertRaises(server._McpError):
            self.approve(missing_id)
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_recovery_tickets").fetchone()[0], 3)

    def test_duplicate_with_secret_reuses_pending_and_approved(self):
        body, tid = self.submit()
        for _ in range(8):
            self.assertEqual(self.submit(query_code=body["query_code"])[1], tid)
        self.approve(tid)
        self.assertEqual(self.submit(query_code=body["query_code"])[1], tid)
        self.assertEqual(self.tokens(), [])

    def test_ip_limit_persists_and_is_not_account_based(self):
        for i in range(5):
            self.submit("Human" if i % 2 else "Missing")
        for account in ("Human", "Missing"):
            with self.assertRaises(server._McpError) as caught:
                self.submit(account)
            self.assertEqual(caught.exception.code, server.RATE_LIMIT_ERROR_CODE)
        self.submit("Human", ip="192.0.2.20")

    def test_rejection_note_and_reapplication(self):
        body, tid = self.submit()
        self.approve(tid, "rejected", "请补充常玩的游戏")
        self.assertEqual(server._query_recovery_ticket(body), {"ok": True, "status": "rejected", "admin_note": "请补充常玩的游戏"})
        self.assertEqual(self.tokens(), [])
        with self.assertRaises(server._McpError):
            self.approve(tid)
        self.assertNotEqual(self.submit(query_code=body["query_code"])[1], tid)

    def test_explicit_id_disambiguates_numeric_username(self):
        numeric = self._add_user(str(self.human_id))
        body, tid = self.submit(str(self.human_id))
        self.approve(tid)
        by_id, id_tid = self.submit(str(self.human_id), account_kind="id")
        self.approve(id_tid)
        with self._connect() as conn:
            ids = [r[0] for r in conn.execute("SELECT user_id FROM account_recovery_tickets ORDER BY id")]
        self.assertEqual(ids, [numeric, self.human_id])
        server._query_recovery_ticket(body)
        server._query_recovery_ticket(by_id)

    def test_ai_and_pending_deletion_cannot_be_approved_or_claimed(self):
        _, tid = self.submit("Machine")
        with self.assertRaises(server._McpError):
            self.approve(tid)
        body, human_tid = self.submit()
        self.approve(human_tid)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=? WHERE id=?", (int(time.time()), self.human_id))
        self.assertEqual(server._query_recovery_ticket(body)["status"], "unavailable")
        self.assertEqual(self.tokens(), [])
        _, tid = self.submit()
        with self.assertRaises(server._McpError):
            self.approve(tid)

    def test_manual_one_hour_and_legacy_plaintext_links(self):
        generated = server._generate_reset_link(self.human_id)
        self.assertEqual(generated["expires_in"], "1小时")
        with self._connect() as conn:
            ttl = conn.execute("SELECT CAST(strftime('%s', expires_at) AS INTEGER)-CAST(strftime('%s', 'now') AS INTEGER) FROM password_reset_tokens").fetchone()[0]
            self.assertAlmostEqual(ttl, 3600, delta=3)
            conn.execute("INSERT INTO password_reset_tokens(user_id,token,expires_at) VALUES (?, 'legacy-token', datetime('now','+1 hour'))", (self.human_id,))
        server._reset_password_by_token("legacy-token", "legacy-pass")
        server._reset_password_by_token(generated["reset_url"].split("=", 1)[1], "manual-pass")

    def test_email_recovery_closes_approved_tickets_and_revokes_links(self):
        self._bind_email()
        self._age_all_codes()
        body, tid = self.submit()
        self.approve(tid)
        server._query_recovery_ticket(body)
        server._start_password_recovery("Human", client_ip="192.0.2.30")
        server._reset_human_password_by_email("Human", self._latest_code(), "email-pass", client_ip="192.0.2.30")
        self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")
        self.assertEqual(self.tokens()[0]["used"], 1)

    def test_admin_endpoint_auth_and_public_no_store(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.headers = {}
        handler.path = "/api/admin/recovery"
        handler._send_json = Mock()
        handler._read_json_body = Mock(return_value={})
        for review in (False, True):
            handler._handle_admin_recovery(review=review)
            self.assertEqual(handler._send_json.call_args.kwargs["status"], 401)
        handler.headers = {"Authorization": "Bearer " + self.human_token}
        handler._handle_admin_recovery()
        self.assertEqual(handler._send_json.call_args.kwargs["status"], 403)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (self.human_id,))
        handler._handle_admin_recovery()
        self.assertIn("tickets", handler._send_json.call_args.args[0])
        body, tid = self.submit()
        handler._read_json_body = Mock(return_value={"ticket_id": tid, "decision": "approved", "admin_note": "通过"})
        handler._handle_admin_recovery(review=True)
        self.assertEqual(handler._send_json.call_args.args[0], {"ok": True})
        self.assertEqual(self.tokens(), [])
        handler._client_ip = lambda: "192.0.2.80"
        handler._read_json_body = Mock(return_value=body)
        handler._handle_api_recovery("/api/auth/recovery/query")
        self.assertEqual(handler._send_json.call_args.kwargs["extra_headers"]["Cache-Control"], "no-store")
        self.assertIn("reset_url", handler._send_json.call_args.args[0])
        listing = server._admin_recovery_tickets("processed")
        self.assertNotIn("query_code_hash", listing["tickets"][0])
        self.assertNotIn("reset_nonce", listing["tickets"][0])

    def test_manual_reset_completes_all_approved_tickets_even_unclaimed(self):
        first, first_id = self.submit()
        second, second_id = self.submit(ip="192.0.2.99")
        self.approve(first_id)
        self.approve(second_id)
        old_url = server._query_recovery_ticket(first)["reset_url"]
        server._admin_reset_user_password(self.human_id, {"password": "manual-reset-pass"})
        for body in (first, second):
            self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(old_url.split("=", 1)[1], "stale-pass")
        self.assertEqual(len(self.tokens()), 1)

    def test_lists_separate_pending_processed_and_paginate_without_secrets(self):
        for i in range(22):
            self.submit(ip=f"192.0.2.{i}")
        self.approve(1, "rejected", "信息不足")
        first = server._admin_recovery_tickets()
        second = server._admin_recovery_tickets(page=2)
        self.assertEqual(first["pending_count"], 21)
        self.assertEqual(first["total"], 21)
        self.assertEqual(len(first["tickets"]), 20)
        self.assertEqual(len(second["tickets"]), 1)
        self.assertFalse(set(r["id"] for r in first["tickets"]) & set(r["id"] for r in second["tickets"]))
        self.assertEqual(server._admin_recovery_tickets("processed")["tickets"][0]["admin_note"], "信息不足")

    def test_query_code_is_not_stored_in_plaintext_and_old_access_token_is_rejected(self):
        body, tid = self.submit()
        with self._connect() as conn:
            ticket = dict(conn.execute("SELECT * FROM account_recovery_tickets WHERE id=?", (tid,)).fetchone())
            self.assertNotIn(body["query_code"], "\n".join(conn.iterdump()))
        self.assertEqual(ticket["query_code_hash"], server._recovery_query_hash(body))
        self.assertNotEqual(ticket["query_code_hash"], server._reset_token_hash(body["query_code"]))
        with self.assertRaises(server._McpError) as caught:
            server._query_recovery_ticket({"account": "Human", "access_token": body["query_code"]})
        self.assertEqual(caught.exception.message, server._RECOVERY_MISSING)
        # No cookie, token or original submission fields are needed on another device.
        result = server._query_recovery_ticket({"account": "Human", "query_code": body["query_code"]})
        self.assertEqual(result["status"], "pending")

    def test_processed_records_keep_all_statuses_notes_and_review_time(self):
        rejected, rid = self.submit()
        approved, aid = self.submit("OtherHuman")
        completed, cid = self.submit()
        self.approve(rid, "rejected", "请补充资料")
        self.approve(aid, note="核验通过待领取")
        self.approve(cid, note="核验通过已领取")
        token = server._query_recovery_ticket(completed)["reset_url"].split("=", 1)[1]
        server._reset_password_by_token(token, "completed-pass")
        listing = server._admin_recovery_tickets("processed")
        records = {r["id"]: r for r in listing["tickets"]}
        self.assertEqual(listing["total"], 3)
        for tid, status, note in [(rid, "rejected", "请补充资料"), (aid, "approved", "核验通过待领取"),
                                  (cid, "completed", "核验通过已领取")]:
            self.assertEqual(records[tid]["status"], status)
            self.assertEqual(records[tid]["admin_note"], note)
            self.assertIsInstance(records[tid]["reviewed_at_epoch"], int)
            self.assertNotIn("query_code_hash", records[tid])
        self.assertEqual(server._admin_recovery_tickets()["tickets"], [])

    def test_query_rate_limits_apply_to_missing_accounts_and_distributed_attempts(self):
        handler = object.__new__(server.CedarToyHandler)
        handler._send_json = Mock()
        handler._client_ip = lambda: "192.0.2.200"
        handler._read_json_body = Mock(return_value={"account": "Missing", "query_code": "Wrong123"})
        with patch.object(server, "_REQUEST_RATE_LIMIT", {}):
            for i in range(10):
                handler._client_ip = lambda i=i: f"192.0.2.{i}"
                handler._handle_api_recovery("/api/auth/recovery/query")
                self.assertEqual(handler._send_json.call_args.args[0], {"error": server._RECOVERY_MISSING})
            handler._handle_api_recovery("/api/auth/recovery/query")
            self.assertEqual(handler._send_json.call_args.kwargs["status"], 429)
        handler._client_ip = lambda: "192.0.2.200"
        with patch.object(server, "_REQUEST_RATE_LIMIT", {}):
            for i in range(31):
                handler._read_json_body.return_value = {"account": f"Missing{i}", "query_code": "Wrong123"}
                handler._handle_api_recovery("/api/auth/recovery/query")
            self.assertEqual(handler._send_json.call_args.kwargs["status"], 429)
            self.assertEqual(handler._send_json.call_args.kwargs["extra_headers"], {"Cache-Control": "no-store"})

    def test_parallel_reviews_only_one_decision_wins(self):
        body, tid = self.submit()
        def review(decision):
            try:
                self.approve(tid, decision, "已核验")
                return True
            except server._McpError:
                return False
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(review, ("approved", "rejected")))
        self.assertEqual(sum(outcomes), 1)
        self.assertEqual(self.tokens(), [])

    def test_routes(self):
        for path, method, target in [
            ("/api/auth/recovery/submit", "do_POST", "_handle_api_recovery"),
            ("/api/auth/recovery/query", "do_POST", "_handle_api_recovery"),
            ("/api/admin/recovery/review", "do_POST", "_handle_admin_recovery"),
            ("/api/admin/recovery", "do_GET", "_handle_admin_recovery"),
        ]:
            handler = object.__new__(server.CedarToyHandler)
            handler.path, handler.headers = path, {}
            handler.client_address = ("127.0.0.1", 12345)
            handler._is_soup_path = lambda: False
            setattr(handler, target, Mock())
            getattr(handler, method)()
            getattr(handler, target).assert_called_once()


if __name__ == "__main__":
    unittest.main()
