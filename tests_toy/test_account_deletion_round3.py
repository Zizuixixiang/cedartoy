import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_deletion
import server


class AccountDeletionRound3Tests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.account_db = self.root / "accounts.db"
        self.sessions_db = self.root / "sessions.db"
        self.duel_db = self.root / "duel.db"
        self.notes_db = self.root / "notes.db"
        self.legacy_garden_db = self.root / "legacy_garden.db"
        self.save_root = self.root / "vendor_saves"
        self.save_root.mkdir()
        self._make_account_db()
        self._make_sessions_db()
        self._make_duel_db()
        with sqlite3.connect(self.notes_db) as conn:
            conn.execute(
                "CREATE TABLE garden_notes (id INTEGER PRIMARY KEY, session_id TEXT, author_type TEXT, author_name TEXT)"
            )
        with sqlite3.connect(self.legacy_garden_db) as conn:
            conn.execute("CREATE TABLE garden_saves (session_id TEXT PRIMARY KEY, state TEXT)")
        self.patches = [
            patch.object(server, "TURTLE_DB_PATH", self.account_db),
            patch.object(server, "SESSIONS_DB_PATH", self.sessions_db),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
            patch.object(server, "DUEL_DB_PATH", self.duel_db),
            patch.object(server, "GARDEN_NOTES_DB_PATH", self.notes_db),
            patch.object(server, "GARDEN_LEGACY_DB_PATH", self.legacy_garden_db),
            patch.object(server, "TOY_SECRET", "round3-test-secret"),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def _make_account_db(self):
        with sqlite3.connect(self.account_db) as conn:
            conn.row_factory = sqlite3.Row
            conn.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_ai INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT,
                    last_active_at TEXT,
                    deleted_at TEXT,
                    ai_token_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE user_bindings (
                    id INTEGER PRIMARY KEY,
                    human_user_id INTEGER NOT NULL REFERENCES toy_users(id),
                    ai_user_id INTEGER NOT NULL REFERENCES toy_users(id)
                );
                CREATE TABLE binding_tokens (token TEXT PRIMARY KEY, ai_user_id INTEGER, expires_at TEXT, used INTEGER);
                CREATE TABLE password_reset_tokens (id INTEGER PRIMARY KEY, user_id INTEGER, token TEXT);
                CREATE TABLE legacy_ai_token_hashes (token_hash TEXT PRIMARY KEY, user_id INTEGER, token_version INTEGER, source TEXT);
                CREATE TABLE account_registration_events (id INTEGER PRIMARY KEY, user_id INTEGER, username TEXT, client_ip TEXT);
                CREATE TABLE account_username_changes (id INTEGER PRIMARY KEY, user_id INTEGER, old_username TEXT, new_username TEXT, changed_at_epoch INTEGER);
                CREATE TABLE anti_addiction_settings (ai_user_id INTEGER PRIMARY KEY, enabled INTEGER);
                CREATE TABLE anti_addiction_states (player_id TEXT PRIMARY KEY, streak INTEGER);
                CREATE TABLE guest_claim_codes (code TEXT PRIMARY KEY, guest_player_id TEXT, claimed_by INTEGER);
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT,
                    is_guest INTEGER, is_ai INTEGER, is_admin INTEGER, source TEXT,
                    user_id INTEGER REFERENCES toy_users(id)
                );
                CREATE TABLE game_logs (id INTEGER PRIMARY KEY, player_id INTEGER, content TEXT);
                CREATE TABLE flagged_content (id INTEGER PRIMARY KEY, type TEXT, ref_id INTEGER, reason TEXT);
                CREATE TABLE account_emails (user_id INTEGER PRIMARY KEY REFERENCES toy_users(id), email_normalized TEXT);
                CREATE TABLE email_verification_codes (id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES toy_users(id), code_hash TEXT);
                CREATE TABLE email_verification_attempts (id INTEGER PRIMARY KEY, code_id INTEGER, user_id INTEGER REFERENCES toy_users(id), email_hash TEXT);
                """
            )
            server._init_account_security_schema(conn)
            account_deletion.init_schema(conn)
            conn.commit()

    def _make_sessions_db(self):
        with sqlite3.connect(self.sessions_db) as conn:
            conn.executescript(
                """
                CREATE TABLE test_sessions (player_id TEXT, game TEXT, user_id INTEGER, PRIMARY KEY(player_id, game));
                CREATE TABLE test_results (player_id TEXT, game TEXT, user_id INTEGER, PRIMARY KEY(player_id, game));
                CREATE TABLE eco_sessions (player_id TEXT PRIMARY KEY, save_data TEXT, user_id INTEGER);
                CREATE TABLE ciyuwu_sessions (player_id TEXT PRIMARY KEY, save_data TEXT, user_id INTEGER);
                CREATE TABLE announcement_reads (player_id TEXT, announcement_id TEXT, PRIMARY KEY(player_id, announcement_id));
                """
            )

    def _make_duel_db(self):
        with sqlite3.connect(self.duel_db) as conn:
            conn.executescript(
                """
                CREATE TABLE room_participants (
                    room_id TEXT, player_id TEXT, role TEXT, display_name TEXT,
                    PRIMARY KEY(room_id, player_id)
                );
                CREATE TABLE room_messages (
                    id INTEGER PRIMARY KEY, room_id TEXT, sender_player_id TEXT, text TEXT
                );
                """
            )

    def _create_user(self, username="human", is_ai=0, deleted_at=None):
        with sqlite3.connect(self.account_db) as conn:
            cur = conn.execute(
                """
                INSERT INTO toy_users (
                    username, password_hash, is_ai, is_admin, created_at,
                    last_active_at, deleted_at
                ) VALUES (?, ?, ?, 0, '2026-01-01', '2026-01-01', ?)
                """,
                (username, server._hash_password("secret1"), is_ai, deleted_at),
            )
            return cur.lastrowid

    def _user(self, user_id):
        with sqlite3.connect(self.account_db) as conn:
            conn.row_factory = sqlite3.Row
            return dict(conn.execute("SELECT * FROM toy_users WHERE id=?", (user_id,)).fetchone())

    def _token(self, user_id):
        return server._create_account_token(self._user(user_id))

    def _request(self, user_id, now):
        with sqlite3.connect(self.account_db) as conn:
            conn.row_factory = sqlite3.Row
            return account_deletion.request_deletion(conn, user_id, now_epoch=now)

    def _cancel(self, user_id, now):
        with sqlite3.connect(self.account_db) as conn:
            conn.row_factory = sqlite3.Row
            return account_deletion.cancel_deletion(conn, user_id, now_epoch=now)

    def test_wait_is_exact_and_never_accumulates(self):
        uid = self._create_user()
        first = self._request(uid, 1_000_000)
        self.assertEqual(first["scheduled_delete_at_epoch"], 1_000_000 + 72 * 3600)

        cancelled = self._cancel(uid, 1_000_000 + 48 * 3600)
        self.assertTrue(cancelled["cancelled"])
        row = self._user(uid)
        self.assertIsNone(row["deletion_requested_at_epoch"])
        self.assertIsNone(row["scheduled_delete_at_epoch"])
        self.assertIsNone(row["deletion_job_id"])

        second_now = 2_000_000
        second = self._request(uid, second_now)
        self.assertEqual(second["deletion_requested_at_epoch"], second_now)
        self.assertEqual(second["scheduled_delete_at_epoch"], second_now + 72 * 3600)
        self.assertNotEqual(first["deletion_job_id"], second["deletion_job_id"])

    def test_not_due_cannot_purge_and_due_cannot_cancel(self):
        uid = self._create_user()
        status = self._request(uid, 10_000)
        result = account_deletion.purge_account(
            account_db=self.account_db,
            sessions_db=self.sessions_db,
            vendor_save_root=self.save_root,
            duel_db=self.duel_db,
            garden_notes_db=self.notes_db,
            user_id=uid,
            now_epoch=status["scheduled_delete_at_epoch"] - 1,
        )
        self.assertEqual(result["status"], "pending")
        self.assertIsNotNone(self._user(uid))
        with self.assertRaises(account_deletion.DeletionError) as raised:
            self._cancel(uid, status["scheduled_delete_at_epoch"])
        self.assertEqual(raised.exception.reason, "deletion_due")

    def test_human_password_confirmation_and_pending_auth_restriction(self):
        uid = self._create_user()
        token = self._token(uid)
        with self.assertRaises(server._McpError) as raised:
            server._delete_account(token, True, "wrong-password")
        self.assertEqual(raised.exception.details["reason"], "password_mismatch")

        result = server._delete_account(token, True, "secret1")
        self.assertTrue(result["pending"])
        relogin = server._login_human("human", "secret1")
        self.assertTrue(relogin["pending_deletion"])
        self.assertIn("deletion", relogin["user"])
        with self.assertRaises(server._McpError) as blocked:
            server._current_account(token)
        self.assertEqual(blocked.exception.details["reason"], "pending_deletion")
        with self.assertRaises(server._McpError) as play_blocked:
            server._tool_play(
                {"game": "eco", "action": "eco_status", "params": {}},
                path_token=token,
            )
        self.assertEqual(play_blocked.exception.details["reason"], "pending_deletion")
        me = server._account_me(token)
        self.assertTrue(me["pending_deletion"])
        self.assertEqual(me["bindings"], [])
        self.assertEqual(server._account_deletion_status(token)["status"], "pending")
        self.assertTrue(server._cancel_account_deletion(token)["cancelled"])
        self.assertEqual(server._current_account(token)["id"], uid)

    def test_ai_token_request_and_cancel(self):
        uid = self._create_user("machine", is_ai=1)
        token = self._token(uid)
        requested = json.loads(
            server._tool_account(
                {"action": "delete_account", "confirm": True}, path_token=token
            )
        )
        self.assertTrue(requested["pending"])
        status = json.loads(
            server._tool_account({"action": "deletion_status"}, path_token=token)
        )
        self.assertTrue(status["can_cancel"])
        cancelled = json.loads(
            server._tool_account({"action": "cancel_delete_account"}, path_token=token)
        )
        self.assertTrue(cancelled["cancelled"])

    def test_due_purge_removes_private_data_and_anonymizes_shared_history(self):
        uid = self._create_user("purgee", is_ai=1)
        other = self._create_user("other")
        with sqlite3.connect(self.account_db) as conn:
            conn.executescript(
                f"""
                INSERT INTO user_bindings VALUES (1, {other}, {uid});
                INSERT INTO binding_tokens VALUES ('bind-secret', {uid}, '2099-01-01', 0);
                INSERT INTO password_reset_tokens VALUES (1, {uid}, 'reset-secret');
                INSERT INTO ai_access_tokens (token_hash, user_id, generation, format_version)
                VALUES ('{'b' * 64}', {uid}, 0, 1);
                INSERT INTO legacy_ai_token_hashes VALUES ('{'a' * 64}', {uid}, 0, 'test');
                INSERT INTO account_registration_events VALUES (1, {uid}, 'purgee', '1.2.3.4');
                INSERT INTO account_username_changes VALUES (1, {uid}, 'oldpurgee', 'purgee', 1);
                INSERT INTO anti_addiction_settings VALUES ({uid}, 1);
                INSERT INTO anti_addiction_states VALUES ('{uid}', 9);
                INSERT INTO guest_claim_codes VALUES ('claim-secret', 'guest:x', {uid});
                INSERT INTO players VALUES (10, 'soup-name', 'soup-password', 0, 1, 0, 'web', {uid});
                INSERT INTO game_logs VALUES (1, 10, 'shared question');
                INSERT INTO flagged_content VALUES (1, 'username', 10, 'old username');
                INSERT INTO account_emails VALUES ({uid}, 'private@example.com');
                INSERT INTO email_verification_codes VALUES (1, {uid}, 'code-secret');
                INSERT INTO email_verification_attempts VALUES (1, 1, {uid}, 'email-hash');
                """
            )
        with sqlite3.connect(self.sessions_db) as conn:
            conn.executescript(
                f"""
                INSERT INTO test_sessions VALUES ('{uid}', 'mbti', {uid});
                INSERT INTO test_results VALUES ('legacy-key', 'mbti', {uid});
                INSERT INTO eco_sessions VALUES ('{uid}:2', '{{}}', {uid});
                INSERT INTO ciyuwu_sessions VALUES ('{uid}', '{{}}', {uid});
                INSERT INTO announcement_reads VALUES ('human:{uid}', 'notice');
                INSERT INTO test_results VALUES ('other', 'mbti', {other});
                """
            )
        with sqlite3.connect(self.duel_db) as conn:
            conn.executescript(
                f"""
                INSERT INTO room_participants VALUES ('room', '{uid}', 'ai', 'purgee');
                INSERT INTO room_participants VALUES ('room', '{other}', 'human', 'other');
                INSERT INTO room_messages VALUES (1, 'room', '{uid}', 'shared hello');
                """
            )
        with sqlite3.connect(self.notes_db) as conn:
            conn.execute("INSERT INTO garden_notes VALUES (1, ?, 'ai', 'purgee')", (f"{uid}:3",))
        for game in ("bar", "workkk", "garden_cat"):
            for player_id in (str(uid), f"{uid}:2", f"{uid}:3", f"{uid}:4", f"{uid}:5"):
                directory = self.save_root / game / player_id
                directory.mkdir(parents=True)
                filename = "game_state.json" if game == "workkk" else "state.json" if game == "garden_cat" else "save.json"
                (directory / filename).write_text("{}", encoding="utf-8")

        request = self._request(uid, 100)

        def managed_delete(game, player_id):
            target = self.save_root / game / player_id
            if target.is_dir():
                import shutil
                shutil.rmtree(target)

        result = account_deletion.purge_account(
            account_db=self.account_db,
            sessions_db=self.sessions_db,
            vendor_save_root=self.save_root,
            duel_db=self.duel_db,
            garden_notes_db=self.notes_db,
            user_id=uid,
            now_epoch=request["scheduled_delete_at_epoch"],
            workkk_delete=lambda pid: managed_delete("workkk", pid),
            garden_delete=lambda pid: managed_delete("garden_cat", pid),
        )
        self.assertEqual(result["status"], "complete")
        with sqlite3.connect(self.account_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM toy_users WHERE id=?", (uid,)).fetchone()[0], 0)
            for table in (
                "user_bindings", "binding_tokens", "password_reset_tokens",
                "ai_access_tokens", "legacy_ai_token_hashes", "account_registration_events",
                "account_username_changes", "anti_addiction_settings",
                "anti_addiction_states", "guest_claim_codes", "account_emails",
                "email_verification_codes", "email_verification_attempts",
            ):
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0], 0, table)
            player = conn.execute("SELECT username,password_hash,user_id FROM players WHERE id=10").fetchone()
            self.assertTrue(player[0].startswith("deleted-"))
            self.assertIsNone(player[1])
            self.assertIsNone(player[2])
            self.assertEqual(conn.execute("SELECT content FROM game_logs WHERE id=1").fetchone()[0], "shared question")
        with sqlite3.connect(self.sessions_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_results").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT player_id FROM test_results").fetchone()[0], "other")
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM test_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM eco_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ciyuwu_sessions").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM announcement_reads").fetchone()[0], 0)
        with sqlite3.connect(self.duel_db) as conn:
            self.assertEqual(conn.execute("SELECT display_name FROM room_participants WHERE player_id=?", (str(uid),)).fetchone()[0], "已注销用户")
            self.assertEqual(conn.execute("SELECT text FROM room_messages").fetchone()[0], "shared hello")
        for game in ("bar", "workkk", "garden_cat"):
            for player_id in (str(uid), f"{uid}:2", f"{uid}:3", f"{uid}:4", f"{uid}:5"):
                self.assertFalse((self.save_root / game / player_id).exists())

        again = account_deletion.purge_account(
            account_db=self.account_db,
            sessions_db=self.sessions_db,
            vendor_save_root=self.save_root,
            duel_db=self.duel_db,
            garden_notes_db=self.notes_db,
            job_id=request["deletion_job_id"],
            now_epoch=request["scheduled_delete_at_epoch"] + 1,
        )
        self.assertEqual(again["status"], "complete")
        self.assertFalse(again["purged"])

    def test_human_name_in_bound_ai_garden_is_anonymized_not_deleted(self):
        human = self._create_user("gardener")
        machine = self._create_user("gardenbot", is_ai=1)
        with sqlite3.connect(self.account_db) as conn:
            conn.execute("INSERT INTO user_bindings VALUES (1, ?, ?)", (human, machine))
        with sqlite3.connect(self.notes_db) as conn:
            conn.execute(
                "INSERT INTO garden_notes VALUES (1, ?, 'human', 'gardener')",
                (str(machine),),
            )
            conn.execute(
                "INSERT INTO garden_notes VALUES (2, ?, 'ai', 'gardenbot')",
                (str(machine),),
            )
        request = self._request(human, 100)
        account_deletion.purge_account(
            account_db=self.account_db,
            sessions_db=self.sessions_db,
            vendor_save_root=self.save_root,
            duel_db=self.duel_db,
            garden_notes_db=self.notes_db,
            user_id=human,
            now_epoch=request["scheduled_delete_at_epoch"],
        )
        with sqlite3.connect(self.notes_db) as conn:
            rows = conn.execute("SELECT author_type, author_name FROM garden_notes ORDER BY id").fetchall()
        self.assertEqual(rows, [("human", "已注销用户"), ("ai", "gardenbot")])

    def test_retry_after_managed_delete_crash_is_safe(self):
        uid = self._create_user("retrybot", is_ai=1)
        target = self.save_root / "workkk" / str(uid)
        target.mkdir(parents=True)
        (target / "game_state.json").write_text("{}", encoding="utf-8")
        request = self._request(uid, 100)

        calls = []
        def delete_then_crash(player_id):
            calls.append(player_id)
            import shutil
            shutil.rmtree(self.save_root / "workkk" / player_id)
            raise RuntimeError("simulated post-delete crash")

        with self.assertRaises(RuntimeError):
            account_deletion.purge_account(
                account_db=self.account_db,
                sessions_db=self.sessions_db,
                vendor_save_root=self.save_root,
                duel_db=self.duel_db,
                garden_notes_db=self.notes_db,
                user_id=uid,
                now_epoch=request["scheduled_delete_at_epoch"],
                workkk_delete=delete_then_crash,
            )
        self.assertFalse(target.exists())
        self.assertIsNotNone(self._user(uid))

        completed = account_deletion.purge_account(
            account_db=self.account_db,
            sessions_db=self.sessions_db,
            vendor_save_root=self.save_root,
            duel_db=self.duel_db,
            garden_notes_db=self.notes_db,
            user_id=uid,
            now_epoch=(
                request["scheduled_delete_at_epoch"]
                + account_deletion.PURGE_LEASE_SECONDS + 1
            ),
            workkk_delete=lambda player_id: self.fail("missing save must not be deleted twice"),
        )
        self.assertEqual(completed["status"], "complete")
        self.assertEqual(calls, [str(uid)])
        with sqlite3.connect(self.account_db) as conn:
            job = conn.execute(
                "SELECT status, phase, user_id FROM account_deletion_jobs WHERE job_id=?",
                (request["deletion_job_id"],),
            ).fetchone()
        self.assertEqual(job, ("complete", "complete", 0))

    def test_legacy_soft_delete_is_not_migrated_or_scheduled(self):
        uid = self._create_user("legacy", deleted_at="2026-01-01 00:00:00")
        with sqlite3.connect(self.account_db) as conn:
            conn.row_factory = sqlite3.Row
            account_deletion.init_schema(conn)
            account_deletion.init_schema(conn)
            conn.commit()
            row = conn.execute("SELECT * FROM toy_users WHERE id=?", (uid,)).fetchone()
            self.assertIsNotNone(row["deleted_at"])
            self.assertIsNone(row["deletion_requested_at_epoch"])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM account_deletion_jobs").fetchone()[0], 0)
        self.assertIn("default=180", (Path(server.__file__).parent / "scripts" / "clean_guest_saves.py").read_text(encoding="utf-8"))

    def test_registered_account_inactivity_never_schedules_deletion(self):
        uid = self._create_user("inactive-registered".replace("-", "_"))
        with sqlite3.connect(self.account_db) as conn:
            conn.execute(
                "UPDATE toy_users SET last_active_at='2020-01-01 00:00:00' WHERE id=?",
                (uid,),
            )
        self.assertEqual(
            account_deletion.due_jobs(self.account_db, now_epoch=9_999_999_999),
            [],
        )
        self.assertEqual(self._user(uid)["username"], "inactive_registered")
        guest_script = (Path(server.__file__).parent / "scripts" / "clean_guest_saves.py").read_text(encoding="utf-8")
        self.assertIn("default=180", guest_script)
        self.assertIn("guest:", guest_script)

    def test_admin_page_reports_pending_status_without_clearing_it(self):
        uid = self._create_user()
        request = self._request(uid, 5_000)
        page = server._admin_user_page(page=1, page_size=50)
        row = next(item for item in page["users"] if item["id"] == uid)
        self.assertEqual(row["scheduled_delete_at_epoch"], request["scheduled_delete_at_epoch"])
        self.assertEqual(page["total"], 1)

    def test_admin_immediate_release_uses_complete_purge(self):
        admin_id = self._create_user("admin")
        target_id = self._create_user("release-me", is_ai=1)
        target_token = self._token(target_id)
        with sqlite3.connect(self.account_db) as conn:
            conn.execute("UPDATE toy_users SET is_admin=1 WHERE id=?", (admin_id,))
            conn.execute(
                "INSERT INTO players VALUES (10, 'release-player', 'secret', 0, 0, 0, 'web', ?)",
                (target_id,),
            )
        result = server._admin_release_user(target_id, self._user(admin_id))
        self.assertTrue(result["ok"])
        with self.assertRaises(server._McpError):
            server._current_account(target_token)
        with sqlite3.connect(self.account_db) as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM toy_users WHERE id=?", (target_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ai_access_tokens WHERE user_id=?", (target_id,)).fetchone()[0], 0)
            player = conn.execute("SELECT username,password_hash,user_id FROM players WHERE id=10").fetchone()
        self.assertTrue(player[0].startswith("deleted-"))
        self.assertIsNone(player[1])
        self.assertIsNone(player[2])

    def test_frontend_keeps_email_ui_disabled_and_wires_deletion_recovery(self):
        html = (Path(server.__file__).parent / "index.html").read_text(encoding="utf-8")
        self.assertIn("const EMAIL_SECURITY_UI_ENABLED = false", html)
        self.assertIn('/api/auth/delete-account', html)
        self.assertIn('/api/auth/cancel-delete-account', html)
        self.assertIn("账号待注销", html)
        self.assertIn("再次输入当前密码", html)


if __name__ == "__main__":
    unittest.main()
