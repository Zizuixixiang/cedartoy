import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import account_deletion
import server


class OperitIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-operit-")
        self.db_path = Path(self.temp_dir.name) / "accounts.db"
        self.db_patch = patch.object(server, "TURTLE_DB_PATH", self.db_path)
        self.secret_patch = patch.object(server, "TOY_SECRET", "operit-test-secret")
        self.db_patch.start()
        self.secret_patch.start()
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        server._REQUEST_RATE_LIMIT.clear()
        with self._connect() as conn:
            conn.executescript(
                """
                PRAGMA foreign_keys=ON;
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    is_ai INTEGER DEFAULT 0,
                    is_admin INTEGER DEFAULT 0,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    last_active_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    deleted_at TEXT
                );
                CREATE TABLE user_bindings (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    human_user_id INTEGER NOT NULL REFERENCES toy_users(id),
                    ai_user_id INTEGER NOT NULL REFERENCES toy_users(id),
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(human_user_id, ai_user_id),
                    CHECK(human_user_id <> ai_user_id)
                );
                CREATE TABLE binding_tokens (
                    token TEXT PRIMARY KEY,
                    ai_user_id INTEGER,
                    expires_at TEXT,
                    used INTEGER DEFAULT 0
                );
                """
            )
            server._init_registration_events_table(conn)
            server._init_username_changes_table(conn)
            server._init_password_reset_tokens_table(conn)
            server._init_account_security_schema(conn)
            server._init_operit_schema(conn)
            server._init_anti_addiction_tables(conn)
            account_deletion.init_schema(conn)
        self.password = "operit-pass"

    def tearDown(self):
        server._FAILED_LOGIN_RATE_LIMIT.clear()
        server._REGISTER_RATE_LIMIT.clear()
        server._REQUEST_RATE_LIMIT.clear()
        self.secret_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _add_user(self, username, *, is_ai):
        with self._connect() as conn:
            cur = conn.execute(
                "INSERT INTO toy_users(username,password_hash,is_ai) VALUES (?,?,?)",
                (username, server._hash_password(self.password), 1 if is_ai else 0),
            )
            return int(cur.lastrowid)

    def _user(self, user_id):
        with self._connect() as conn:
            return dict(conn.execute(
                "SELECT * FROM toy_users WHERE id=?", (int(user_id),)
            ).fetchone())

    def _mcp_token(self, user_id):
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            token = server._issue_ai_token_in_transaction(
                conn, self._user(user_id)
            )
            conn.commit()
        return token

    def _human_token(self, user_id):
        return server._create_account_jwt(self._user(user_id))

    def _mcp_rows(self):
        with self._connect() as conn:
            return [dict(row) for row in conn.execute(
                "SELECT * FROM ai_access_tokens ORDER BY token_hash"
            )]

    def test_existing_ai_login_does_not_change_or_revoke_mcp_token(self):
        ai_id = self._add_user("OperitExisting", is_ai=True)
        mcp_token = self._mcp_token(ai_id)
        before = self._mcp_rows()

        result = server._create_operit_ai_session(
            "login", "OperitExisting", self.password, "caller-card-a",
            client_ip="127.0.0.1",
        )

        self.assertTrue(result["session_token"].startswith("ctop_v1_"))
        self.assertEqual(result["user"]["id"], ai_id)
        self.assertEqual(self._mcp_rows(), before)
        self.assertEqual(server._current_account(mcp_token)["id"], ai_id)

    def test_mcp_rotation_does_not_revoke_operit_session(self):
        ai_id = self._add_user("IndependentRotate", is_ai=True)
        mcp_token = self._mcp_token(ai_id)
        operit = server._create_operit_ai_session(
            "login", "IndependentRotate", self.password, "rotate-card"
        )

        replacement = server._rotate_ai_token(mcp_token)

        self.assertNotEqual(replacement["token"], mcp_token)
        self.assertEqual(
            server._current_account(replacement["token"])["id"], ai_id
        )
        self.assertEqual(
            server._current_operit_ai(
                operit["session_token"], "rotate-card"
            )["id"],
            ai_id,
        )

    def test_machine_password_reset_revokes_only_new_operit_session(self):
        human_id = self._add_user("ResetOwner", is_ai=False)
        ai_id = self._add_user("ResetMachine", is_ai=True)
        mcp_token = self._mcp_token(ai_id)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)",
                (human_id, ai_id),
            )
        operit = server._create_operit_ai_session(
            "login", "ResetMachine", self.password, "reset-card"
        )

        server._reset_machine_password(
            self._human_token(human_id), ai_id, "new-operit-pass"
        )

        with self.assertRaises(server._McpError):
            server._current_operit_ai(operit["session_token"], "reset-card")
        self.assertEqual(server._current_account(mcp_token)["id"], ai_id)

    def test_operit_registration_uses_account_rules_without_mcp_credential(self):
        result = server._create_operit_ai_session(
            "register", "OperitFresh", self.password, "caller-card-new",
            client_ip="10.0.0.8", avatar="🌲",
        )

        self.assertTrue(result["user"]["is_ai"])
        self.assertEqual(result["user"]["avatar"]["value"], "🌲")
        self.assertEqual(self._mcp_rows(), [])
        with self._connect() as conn:
            self.assertEqual(
                conn.execute("SELECT COUNT(*) FROM account_registration_events").fetchone()[0],
                1,
            )

    def test_direct_binding_requires_human_login_and_literal_confirmation(self):
        human_id = self._add_user("OperitHuman", is_ai=False)
        ai_id = self._add_user("OperitBound", is_ai=True)
        human_token = self._human_token(human_id)

        with self.assertRaises(server._McpError):
            server._create_operit_ai_session(
                "login", "OperitBound", self.password, "caller-bind",
                bind_to_human=True, confirm_binding=False,
                human_token=human_token,
            )
        result = server._create_operit_ai_session(
            "login", "OperitBound", self.password, "caller-bind",
            bind_to_human=True, confirm_binding=True,
            human_token=human_token,
        )

        self.assertTrue(result["bound"])
        with self._connect() as conn:
            row = conn.execute(
                "SELECT human_user_id,ai_user_id FROM user_bindings"
            ).fetchone()
        self.assertEqual(tuple(row), (human_id, ai_id))

    def test_two_caller_cards_keep_independent_ai_sessions(self):
        first_id = self._add_user("OperitFirst", is_ai=True)
        second_id = self._add_user("OperitSecond", is_ai=True)
        first = server._create_operit_ai_session(
            "login", "OperitFirst", self.password, "caller-card-1"
        )
        second = server._create_operit_ai_session(
            "login", "OperitSecond", self.password, "caller-card-2"
        )

        self.assertEqual(
            server._current_operit_ai(first["session_token"], "caller-card-1")["id"],
            first_id,
        )
        self.assertEqual(
            server._current_operit_ai(second["session_token"], "caller-card-2")["id"],
            second_id,
        )
        with self.assertRaises(server._McpError):
            server._current_operit_ai(first["session_token"], "caller-card-2")
        with self.assertRaises(server._McpError):
            server._current_operit_ai(second["session_token"], "caller-card-1")

    def test_same_caller_switches_machine_without_affecting_other_caller(self):
        first_id = self._add_user("SwitchFirst", is_ai=True)
        second_id = self._add_user("SwitchSecond", is_ai=True)
        first = server._create_operit_ai_session(
            "login", "SwitchFirst", self.password, "switch-card"
        )
        unaffected = server._create_operit_ai_session(
            "login", "SwitchFirst", self.password, "other-card"
        )
        replacement = server._create_operit_ai_session(
            "login", "SwitchSecond", self.password, "switch-card"
        )

        with self.assertRaises(server._McpError):
            server._current_operit_ai(first["session_token"], "switch-card")
        self.assertEqual(
            server._current_operit_ai(
                replacement["session_token"], "switch-card"
            )["id"],
            second_id,
        )
        self.assertEqual(
            server._current_operit_ai(
                unaffected["session_token"], "other-card"
            )["id"],
            first_id,
        )

    def test_duel_uses_canonical_ai_and_keeps_privacy_at_backend(self):
        human_id = self._add_user("DuelHuman", is_ai=False)
        ai_id = self._add_user("DuelMachine", is_ai=True)
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO user_bindings(human_user_id,ai_user_id) VALUES (?,?)",
                (human_id, ai_id),
            )
        session = server._create_operit_ai_session(
            "login", "DuelMachine", self.password, "duel-card"
        )
        captured = []
        backend_view = {
            "room_id": "ROOM1234", "status": "playing", "your_turn": True,
            "legal_actions": [{"action": "roll"}],
        }

        def fake_backend(payload):
            captured.append(dict(payload))
            return dict(backend_view)

        with (
            patch.object(server, "_request_duel_backend", side_effect=fake_backend),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
        ):
            result = server._operit_duel_call(
                session["session_token"],
                "duel-card",
                "state",
                {
                    "room_id": "ROOM1234",
                    "player_id": "forged-player",
                    "opponent_id": "forged-opponent",
                    "viewer_id": "forged-viewer",
                    "full_state": True,
                },
            )

        self.assertEqual(
            {key: result[key] for key in backend_view}, backend_view
        )
        self.assertEqual(result["slot"], 1)
        self.assertEqual(captured[0]["player_id"], str(ai_id))
        self.assertEqual(captured[0]["room_id"], "ROOM1234")
        self.assertTrue(captured[0]["full_state"])
        self.assertNotIn("opponent_id", captured[0])
        self.assertNotIn("viewer_id", captured[0])

    def test_web_ticket_is_short_lived_one_time_and_sets_duel_cookie(self):
        human_id = self._add_user("TicketHuman", is_ai=False)
        issued = server._issue_operit_web_ticket(
            self._human_token(human_id), confirm=True
        )
        self.assertEqual(issued["expires_in"], 60)
        self.assertIn("web_ticket=ctow_v1_", issued["ticket_path"])
        self.assertNotIn(self._human_token(human_id), issued["ticket_path"])

        class RecordingHandler:
            def __init__(self, path):
                self.path = path
                self.headers = {}
                self.status = None
                self.response_headers = {}
                self.json_payload = None

            def send_response(self, status):
                self.status = status

            def send_header(self, name, value):
                self.response_headers[name.lower()] = value

            def end_headers(self):
                pass

            def _send_json(self, payload, status=200, extra_headers=None):
                self.status = status
                self.json_payload = payload
                self.response_headers.update({
                    key.lower(): value for key, value in (extra_headers or {}).items()
                })

        handler = RecordingHandler(issued["ticket_path"])
        server.CedarToyHandler._handle_duel_proxy(handler, "GET")
        self.assertEqual(handler.status, 303)
        self.assertEqual(handler.response_headers["location"], "/duel/")
        self.assertIn("duel_token=", handler.response_headers["set-cookie"])
        self.assertIn("HttpOnly", handler.response_headers["set-cookie"])
        self.assertNotIn(issued["ticket"], handler.response_headers["location"])

        replay = RecordingHandler(issued["ticket_path"])
        server.CedarToyHandler._handle_duel_proxy(replay, "GET")
        self.assertEqual(replay.status, 401)

    def test_web_ticket_rejects_expiry(self):
        human_id = self._add_user("ExpiredHuman", is_ai=False)
        issued = server._issue_operit_web_ticket(
            self._human_token(human_id), confirm=True, now_epoch=100
        )
        with self.assertRaises(server._McpError):
            server._consume_operit_web_ticket(issued["ticket"], now_epoch=161)

    def test_sensitive_operit_values_are_redacted_from_http_logs(self):
        session = "ctop_v1_" + "a" * 43
        ticket = "ctow_v1_" + "b" * 32
        redacted = server._redact_http_log_text(
            f'POST /api x={session} GET /duel/?web_ticket={ticket}'
        )
        self.assertNotIn(session, redacted)
        self.assertNotIn(ticket, redacted)
        self.assertIn("<TOKEN_REDACTED>", redacted)

    def test_operit_post_routes_dispatch_without_touching_mcp_dispatch(self):
        routes = {
            "/api/operit/session": "_handle_api_operit_session",
            "/api/operit/bind": "_handle_api_operit_bind",
            "/api/operit/duel": "_handle_api_operit_duel",
            "/api/operit/web-ticket": "_handle_api_operit_web_ticket",
        }
        for path, method_name in routes.items():
            with self.subTest(path=path):
                handler = object.__new__(server.CedarToyHandler)
                handler.path = path
                handler.headers = {}
                handler.client_address = ("127.0.0.1", 12345)
                handler._is_soup_path = lambda: False
                dispatched = []
                setattr(handler, method_name, lambda name=method_name: dispatched.append(name))
                handler.do_POST()
                self.assertEqual(dispatched, [method_name])


if __name__ == "__main__":
    unittest.main()
