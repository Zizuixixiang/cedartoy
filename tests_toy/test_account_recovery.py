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
                               (server._recovery_query_hash(body["query_code"]),)).fetchone()
        self.assertEqual(result["ticket_id"], row[0])
        self.assertRegex(result["query_code"], r"^[A-HJ-NP-Z2-9]{4}(?:-[A-HJ-NP-Z2-9]{4}){2}$")
        self.assertEqual(result["message"], "请保存查询码，用于查看审核结果；一般会在24小时内完成审核")
        return {"query_code": result["query_code"]}, row[0]

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

    def test_missing_wrong_codes_reveal_nothing_and_valid_codes_are_isolated(self):
        body, tid = self.submit()
        self.approve(tid, note="仅本人可见的备注")
        other, _ = self.submit("OtherHuman", ip="192.0.2.11")
        messages = []
        for credential in ("", "Wrong123", None, 123456, [], {}, "x" * 43,
                           "ABCD-EFGH-JKLM", "ABCD--EFGH-JKLM", "ABCD EFGH JKLM",
                           "OOOO-OOOO-OOOO", "ＡＢＣＤ-EFGH-JKLM"):
            with self.assertRaises(server._McpError) as caught:
                server._query_recovery_ticket({"query_code": credential})
            messages.append((caught.exception.code, caught.exception.message))
        self.assertEqual(set(messages), {(-32602, server._RECOVERY_MISSING)})
        self.assertEqual(self.tokens(), [])
        self.assertEqual(server._query_recovery_ticket(other),
                         {"ok": True, "status": "pending", "admin_note": ""})
        self.assertEqual(server._query_recovery_ticket(body)["admin_note"], "仅本人可见的备注")

    def test_exact_username_exists_and_duplicates_get_independent_tickets(self):
        body, tid = self.submit()
        duplicate, duplicate_id = self.submit()
        self.assertNotEqual(tid, duplicate_id)
        self.assertNotEqual(body["query_code"], duplicate["query_code"])
        self.assertEqual(server._query_recovery_ticket(duplicate)["status"], "pending")
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_recovery_tickets").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT account FROM account_recovery_tickets WHERE id=?", (tid,)).fetchone()[0], "Human")

    def test_invalid_username_returns_same_error_without_creating_ticket(self):
        deleted = self._add_user("DeletedHuman")
        deleting = self._add_user("DeletingHuman")
        scheduled = self._add_user("ScheduledHuman")
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (deleted,))
            conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=? WHERE id=?", (int(time.time()), deleting))
            conn.execute("UPDATE toy_users SET scheduled_delete_at_epoch=? WHERE id=?", (int(time.time()) + 86400, scheduled))
        for account in ("human", "HUMAN", "Missing", "Machine", "DeletedHuman", "DeletingHuman", "ScheduledHuman"):
            with self.subTest(account=account), self.assertRaises(server._McpError) as caught:
                self.submit(account)
            self.assertEqual((caught.exception.code, caught.exception.message), (-32602, "账号名不存在"))
        with self._connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_recovery_tickets").fetchone()[0], 0)

    def test_username_check_uses_current_database_name(self):
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET username='RenamedHuman' WHERE id=?", (self.human_id,))
        with self.assertRaises(server._McpError) as caught:
            self.submit("Human")
        self.assertEqual(caught.exception.message, "账号名不存在")
        self.submit("RenamedHuman")

    def test_numeric_id_submission_still_accepts_missing_ai_and_deleted(self):
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET deleted_at=CURRENT_TIMESTAMP WHERE id=?", (self.other_human_id,))
        for account in (self.human_id, self.ai_id, self.other_human_id, 99999999):
            with self.subTest(account=account):
                body, _ = self.submit(str(account), account_kind="id")
                self.assertEqual(server._query_recovery_ticket(body)["status"], "pending")

    def test_submission_ignores_old_client_query_code_and_still_counts_toward_limit(self):
        body, tid = self.submit()
        self.approve(tid)
        ids = {tid}
        for code in (body["query_code"], "Wrong123", None, {"invalid": "code"}):
            fresh, fresh_id = self.submit(query_code=code)
            self.assertNotIn(fresh_id, ids)
            self.assertNotEqual(fresh["query_code"], body["query_code"])
            ids.add(fresh_id)
        with self.assertRaises(server._McpError) as caught:
            self.submit(query_code=body["query_code"])
        self.assertEqual(caught.exception.code, server.RATE_LIMIT_ERROR_CODE)
        self.assertEqual(self.tokens(), [])

    def test_ip_limit_persists_and_is_not_account_based(self):
        for i in range(5):
            self.submit("Human" if i % 2 else "OtherHuman")
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
        _, tid = self.submit(str(self.ai_id), account_kind="id")
        with self.assertRaises(server._McpError):
            self.approve(tid)
        body, human_tid = self.submit()
        self.approve(human_tid)
        with self._connect() as conn:
            conn.execute("UPDATE toy_users SET deletion_requested_at_epoch=? WHERE id=?", (int(time.time()), self.human_id))
        self.assertEqual(server._query_recovery_ticket(body)["status"], "unavailable")
        self.assertEqual(self.tokens(), [])
        _, tid = self.submit(str(self.human_id), account_kind="id")
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
            self.assertNotIn(body["query_code"].replace("-", ""), "\n".join(conn.iterdump()))
        self.assertEqual(ticket["query_code_hash"], server._recovery_query_hash(body["query_code"]))
        self.assertNotEqual(ticket["query_code_hash"], server._reset_token_hash(body["query_code"]))
        with patch.object(server, "TOY_SECRET", "different-application-key"):
            self.assertNotEqual(ticket["query_code_hash"], server._recovery_query_hash(body["query_code"]))
        with self.assertRaises(server._McpError) as caught:
            server._query_recovery_ticket({"account": "Human", "access_token": body["query_code"]})
        self.assertEqual(caught.exception.message, server._RECOVERY_MISSING)
        # No cookie, token or original submission fields are needed on another device.
        result = server._query_recovery_ticket({"query_code": body["query_code"]})
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

    def test_query_rate_limits_apply_to_missing_codes_and_distributed_attempts(self):
        handler = object.__new__(server.CedarToyHandler)
        handler._send_json = Mock()
        handler._client_ip = lambda: "192.0.2.200"
        handler._read_json_body = Mock(return_value={"query_code": "Wrong123"})
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
                handler._read_json_body.return_value = {"query_code": f"Wrong{i:03}"}
                handler._handle_api_recovery("/api/auth/recovery/query")
                if i < 30:
                    self.assertEqual(handler._send_json.call_args.kwargs["status"], 400)
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

    def test_new_code_variants_reconstruct_the_same_reset_link(self):
        body, tid = self.submit()
        code = body["query_code"]
        self.approve(tid)
        first = server._query_recovery_ticket({"query_code": "  " + code.lower() + "\n"})
        for variant in (code, code.lower(), code.replace("-", ""), " " + code.replace("-", "").lower() + " "):
            self.assertEqual(server._query_recovery_ticket({"query_code": variant}), first)
        self.assertEqual(len(self.tokens()), 1)
        server._reset_password_by_token(first["reset_url"].split("=", 1)[1], "variant-pass")
        self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")

    def legacy_ticket(self, code, **kwargs):
        # Historical tickets could name nonexistent users; seed that old state
        # without expecting the new submit endpoint to accept it.
        missing = kwargs.get("account") == "Missing"
        _, tid = self.submit(**({**kwargs, "account": "Human"} if missing else kwargs))
        with self._connect() as conn:
            if missing:
                conn.execute("UPDATE account_recovery_tickets SET account='Missing' WHERE id=?", (tid,))
            row = conn.execute("SELECT * FROM account_recovery_tickets WHERE id=?", (tid,)).fetchone()
            legacy_hash = server._email_hmac("recovery-query-v1", row["account_kind"], row["account"], code)
            conn.execute("UPDATE account_recovery_tickets SET query_code_hash=? WHERE id=?", (legacy_hash, tid))
        return {"query_code": code}, tid, legacy_hash

    def test_legacy_codes_migrate_atomically_preserving_case_and_other_rows(self):
        for code, identity in [("Ab0I9z", {}), ("aB1O9zQ", {"account": "Missing"}),
                               ("Ab3X9k2Q", {"account": str(self.human_id), "account_kind": "id"})]:
            with self.subTest(code=code):
                body, tid, legacy_hash = self.legacy_ticket(code, **identity)
                with self._connect() as conn:
                    before = [dict(r) for r in conn.execute("SELECT * FROM account_recovery_tickets ORDER BY id")]
                for wrong in (code.upper(), code.lower()):
                    with self.assertRaises(server._McpError) as caught:
                        server._query_recovery_ticket({"query_code": wrong})
                    self.assertEqual(caught.exception.message, server._RECOVERY_MISSING)
                with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
                    results = list(executor.map(lambda _: server._query_recovery_ticket(body), range(2)))
                self.assertEqual(results, [{"ok": True, "status": "pending", "admin_note": ""}] * 2)
                with self._connect() as conn:
                    after = [dict(r) for r in conn.execute("SELECT * FROM account_recovery_tickets ORDER BY id")]
                    self.assertNotIn(code, "\n".join(conn.iterdump()))
                    self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
                expected = [dict(row, query_code_hash=server._recovery_query_hash(code)) if row["id"] == tid else row
                            for row in before]
                self.assertEqual(after, expected)
                self.assertNotEqual(expected[-1]["query_code_hash"], legacy_hash)
                self.assertEqual(server._query_recovery_ticket({"query_code": " " + code + " "}), results[0])
                with self.assertRaises(server._McpError):
                    server._query_recovery_ticket({"query_code": code.upper()})

    def test_legacy_already_issued_link_survives_migration_and_reissue(self):
        body, tid, _ = self.legacy_ticket("Ab0I9zQ2")
        self.approve(tid)
        nonce = "legacy-reset-nonce"
        token = server._email_hmac("recovery-reset-v1", body["query_code"], nonce)
        with self._connect() as conn:
            _, token_id, expiry = server._create_reset_token(conn, self.human_id, lifetime_seconds=86400, token=token)
            conn.execute("UPDATE account_recovery_tickets SET reset_token_id=?, reset_nonce=? WHERE id=?",
                         (token_id, nonce, tid))
        first = server._query_recovery_ticket({"query_code": " " + body["query_code"] + " "})
        self.assertEqual(first["reset_url"], server._reset_url(token))
        self.assertEqual(first["expires_at_epoch"], expiry)
        self.assertEqual(server._query_recovery_ticket(body), first)
        self.assertEqual(len(self.tokens()), 1)
        with self._connect() as conn:
            conn.execute("UPDATE password_reset_tokens SET expires_at=datetime('now','-1 second')")
        fresh = server._query_recovery_ticket(body)
        self.assertNotEqual(fresh["reset_url"], first["reset_url"])
        self.assertEqual(server._query_recovery_ticket(body), fresh)
        with self.assertRaises(server._McpError):
            server._reset_password_by_token(token, "old-link-pass")
        server._reset_password_by_token(fresh["reset_url"].split("=", 1)[1], "legacy-new-pass")
        self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")

    def test_legacy_migration_on_consistent_snapshot_preserves_usable_old_link(self):
        body, tid, legacy_hash = self.legacy_ticket("aB0I1zQ9")
        self.approve(tid)
        nonce = "snapshot-legacy-nonce"
        token = server._email_hmac("recovery-reset-v1", body["query_code"], nonce)
        with self._connect() as conn:
            _, token_id, _ = server._create_reset_token(conn, self.human_id, lifetime_seconds=86400, token=token)
            conn.execute("UPDATE account_recovery_tickets SET reset_token_id=?, reset_nonce=? WHERE id=?",
                         (token_id, nonce, tid))
        snapshot_path = self.db_path.with_name("migration-snapshot.db")
        with self._connect() as source, sqlite3.connect(snapshot_path) as snapshot:
            source.backup(snapshot)
        with patch.object(server, "TURTLE_DB_PATH", snapshot_path):
            first = server._query_recovery_ticket(body)
            with sqlite3.connect(snapshot_path) as snapshot:
                migrated = list(snapshot.iterdump())
            self.assertEqual(server._query_recovery_ticket(body), first)
            self.assertEqual(first["reset_url"], server._reset_url(token))
            with sqlite3.connect(snapshot_path) as snapshot:
                self.assertEqual(list(snapshot.iterdump()), migrated)
                self.assertEqual(snapshot.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            # The exact credential issued before migration still resets the password.
            server._reset_password_by_token(token, "snapshot-legacy-pass")
            self.assertEqual(server._query_recovery_ticket(body)["status"], "completed")
        with self._connect() as source:
            row = source.execute("SELECT query_code_hash, status FROM account_recovery_tickets WHERE id=?", (tid,)).fetchone()
            self.assertEqual(tuple(row), (legacy_hash, "approved"))
        self.assertEqual(self.tokens()[0]["used"], 0)

    def test_legacy_duplicate_codes_do_not_expose_either_ticket(self):
        body, tid, first_hash = self.legacy_ticket("Ab3X9k2Q")
        _, other_id, other_hash = self.legacy_ticket("Ab3X9k2Q", account="OtherHuman")
        self.approve(tid, note="private first")
        self.approve(other_id, note="private second")
        with self.assertRaises(server._McpError) as caught:
            server._query_recovery_ticket(body)
        self.assertEqual(caught.exception.message, server._RECOVERY_MISSING)
        self.assertEqual(self.tokens(), [])
        with self._connect() as conn:
            self.assertEqual([r[0] for r in conn.execute("SELECT query_code_hash FROM account_recovery_tickets ORDER BY id")],
                             [first_hash, other_hash])

    def test_query_code_rate_limit_normalizes_new_code_variants(self):
        body, _ = self.submit()
        code = body["query_code"]
        handler = object.__new__(server.CedarToyHandler)
        handler._send_json = Mock()
        handler._read_json_body = Mock()
        with patch.object(server, "_REQUEST_RATE_LIMIT", {}) as limits:
            for i in range(11):
                handler._client_ip = lambda i=i: f"192.0.2.{i}"
                variant = (code, code.replace("-", "").lower(), " " + code + " ")[i % 3]
                handler._read_json_body.return_value = {"query_code": variant}
                handler._handle_api_recovery("/api/auth/recovery/query")
                if i < 10:
                    self.assertEqual(handler._send_json.call_args.args[0]["status"], "pending")
            self.assertEqual(handler._send_json.call_args.kwargs["status"], 429)
            self.assertNotIn(code, repr(limits))
            self.assertNotIn(code.replace("-", ""), repr(limits))

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
