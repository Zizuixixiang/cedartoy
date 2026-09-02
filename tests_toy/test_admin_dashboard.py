import json
import re
import sqlite3
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import admin_dashboard
import server


class AdminDashboardFixtureTests(unittest.TestCase):
    NOW = datetime(2026, 9, 1, 10, 0, 0, tzinfo=timezone.utc)

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="cedartoy-admin-dashboard-")
        root = Path(self.temp_dir.name)
        self.duel_db = root / "duel.db"
        self.turtle_db = root / "turtle.db"
        self._create_duel_fixture()
        self._create_turtle_fixture()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create_duel_fixture(self):
        with sqlite3.connect(self.duel_db) as conn:
            conn.executescript(
                """
                CREATE TABLE rooms (
                    room_id TEXT PRIMARY KEY,
                    game_type TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    stake INTEGER NOT NULL,
                    terminal_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_move_at TEXT NOT NULL
                );
                CREATE INDEX idx_rooms_updated_at ON rooms(updated_at);
                CREATE INDEX idx_rooms_last_move_at ON rooms(status, last_move_at);
                CREATE TABLE room_participants (
                    room_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    participant_kind TEXT NOT NULL,
                    npc_persona_id TEXT,
                    join_status TEXT NOT NULL
                );
                CREATE TABLE chip_wallets (
                    id INTEGER PRIMARY KEY,
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    balance INTEGER NOT NULL,
                    bankruptcy_badge_active INTEGER NOT NULL
                );
                CREATE TABLE chip_ledger (
                    id INTEGER PRIMARY KEY,
                    wallet_id INTEGER NOT NULL,
                    transaction_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE chip_settlement_batches (
                    idempotency_key TEXT PRIMARY KEY,
                    reference_type TEXT,
                    reference_id TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE exchange_requests (
                    request_id TEXT PRIMARY KEY,
                    human_id TEXT NOT NULL,
                    ai_id TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    item_title TEXT NOT NULL,
                    request_note TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE loans (
                    loan_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    accepted_at TEXT,
                    repaid_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE TABLE achievement_unlocks (
                    subject_type TEXT NOT NULL,
                    subject_id TEXT NOT NULL,
                    reward INTEGER NOT NULL,
                    unlocked_at TEXT NOT NULL
                );
                CREATE TABLE room_messages (
                    room_id TEXT,
                    content TEXT
                );
                """
            )
            conn.executemany(
                "INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    ("ACTIVE01", "gomoku", 1, "playing", 10, None, "2026-09-01T09:40:00+00:00", "2026-09-01T09:55:00+00:00", "2026-09-01T09:55:00+00:00"),
                    ("OLDPLAY1", "gomoku", 3, "playing", 0, None, "2026-09-01T07:30:00+00:00", "2026-09-01T08:00:00+00:00", "2026-09-01T08:00:00+00:00"),
                    ("PENDING1", "othello", 0, "pending", 0, None, "2026-09-01T09:57:00+00:00", "2026-09-01T09:58:00+00:00", "2026-09-01T09:58:00+00:00"),
                    ("FINISH01", "othello", 2, "finished", 20, "2026-09-01T09:30:00+00:00", "2026-09-01T09:00:00+00:00", "2026-09-01T09:30:00+00:00", "2026-09-01T09:30:00+00:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO room_participants VALUES (?,?,?,?,?)",
                [
                    ("ACTIVE01", "h1", "human", None, "joined"),
                    ("ACTIVE01", "npc-owl", "system_npc", "owl", "joined"),
                    ("OLDPLAY1", "h-old", "human", None, "joined"),
                    ("OLDPLAY1", "b-old", "bound_machine", None, "joined"),
                    ("PENDING1", "h2", "human", None, "joined"),
                    ("PENDING1", "b2", "bound_machine", None, "joined"),
                    ("FINISH01", "h3", "human", None, "joined"),
                    ("FINISH01", "b3", "bound_machine", None, "left"),
                    ("FINISH01", "never-joined", "bound_machine", None, "invited"),
                ],
            )
            conn.executemany(
                "INSERT INTO chip_wallets VALUES (?,?,?,?,?)",
                [(1, "human", "h1", -5, 1), (2, "ai", "b2", 50, 0)],
            )
            conn.executemany(
                "INSERT INTO chip_ledger VALUES (?,?,?,?)",
                [
                    (1, 1, "daily_check_in", "2026-09-01T08:00:00+00:00"),
                    (2, 1, "daily_check_in", "2026-09-01T09:00:00+00:00"),
                    (3, 2, "daily_check_in", "2026-09-01T09:05:00+00:00"),
                ],
            )
            conn.execute(
                "INSERT INTO chip_settlement_batches VALUES (?,?,?,?)",
                ("settle-1", "duel_room", "FINISH01", "2026-09-01T09:31:00+00:00"),
            )
            conn.executemany(
                "INSERT INTO exchange_requests VALUES (?,?,?,?,?,?,?,?)",
                [
                    ("ex-1", "h1", "b2", "tea", "一杯茶", "SECRET_REQUEST_NOTE", "completed", "2026-09-01T08:10:00+00:00"),
                    ("ex-2", "h1", "b2", "tea", "一杯茶", "SECRET_REQUEST_NOTE", "pending", "2026-09-01T08:20:00+00:00"),
                    ("ex-3", "h3", "b3", "walk", "一起散步", "SECRET_REQUEST_NOTE", "withdrawn", "2026-09-01T08:30:00+00:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO loans VALUES (?,?,?,?,?)",
                [
                    ("loan-1", "active", "2026-09-01T08:05:00+00:00", None, "2026-09-01T08:00:00+00:00"),
                    ("loan-2", "repaid", "2026-09-01T08:15:00+00:00", "2026-09-01T09:00:00+00:00", "2026-09-01T08:10:00+00:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO achievement_unlocks VALUES (?,?,?,?)",
                [
                    ("human", "h1", 5, "2026-09-01T08:00:00+00:00"),
                    ("ai", "b2", 10, "2026-09-01T09:00:00+00:00"),
                ],
            )
            conn.execute(
                "INSERT INTO room_messages VALUES (?,?)",
                ("ACTIVE01", "SECRET_DUEL_MESSAGE"),
            )

    def _create_turtle_fixture(self):
        with sqlite3.connect(self.turtle_db) as conn:
            conn.executescript(
                """
                CREATE TABLE toy_users (
                    id INTEGER PRIMARY KEY,
                    username TEXT NOT NULL,
                    is_ai INTEGER NOT NULL DEFAULT 0,
                    is_admin INTEGER NOT NULL DEFAULT 0,
                    ai_token_version INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT,
                    last_active_at TEXT,
                    deleted_at TEXT,
                    deletion_requested_at_epoch INTEGER,
                    scheduled_delete_at_epoch INTEGER
                );
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY,
                    is_ai INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE rooms (
                    id TEXT PRIMARY KEY,
                    surface TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_by INTEGER,
                    winner_id INTEGER,
                    created_at TEXT NOT NULL,
                    finished_at TEXT
                );
                CREATE TABLE room_presence (
                    room_id TEXT NOT NULL,
                    player_id INTEGER NOT NULL,
                    joined_at TEXT NOT NULL,
                    last_active_at TEXT NOT NULL
                );
                CREATE TABLE game_logs (
                    id INTEGER PRIMARY KEY,
                    room_id TEXT NOT NULL,
                    player_id INTEGER,
                    type TEXT NOT NULL,
                    content TEXT,
                    judgment TEXT,
                    hint_text TEXT,
                    created_at TEXT NOT NULL
                );
                """
            )
            conn.executemany(
                "INSERT INTO toy_users VALUES (?,?,?,?,?,?,?,?,?,?)",
                [
                    (101, "Admin", 0, 1, 0, "2026-09-01 08:00:00", "2026-09-01 08:00:00", None, None, None),
                    (102, "Reader", 0, 0, 0, "2026-09-01 08:00:00", "2026-09-01 08:00:00", None, None, None),
                ],
            )
            conn.executemany("INSERT INTO players VALUES (?,?)", [(1, 0), (2, 1), (3, 0)])
            conn.executemany(
                "INSERT INTO rooms VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    ("SOUPACTIVE", "SECRET_SURFACE", "SECRET_ANSWER", "SECRET_TITLE", "playing", 1, None, "2026-09-01 17:40:00", None),
                    ("SOUPOLD", "SECRET_SURFACE", "SECRET_ANSWER", "SECRET_TITLE", "playing", 3, None, "2026-09-01 16:00:00", None),
                    ("SOUPDONE", "SECRET_SURFACE", "SECRET_ANSWER", "SECRET_TITLE", "finished", 3, 3, "2026-09-01 17:00:00", "2026-09-01 17:30:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO room_presence VALUES (?,?,?,?)",
                [
                    ("SOUPACTIVE", 1, "2026-09-01 17:40:00", "2026-09-01 17:56:00"),
                    ("SOUPOLD", 3, "2026-09-01 16:00:00", "2026-09-01 16:30:00"),
                ],
            )
            conn.executemany(
                "INSERT INTO game_logs VALUES (?,?,?,?,?,?,?,?)",
                [
                    (1, "SOUPACTIVE", 1, "ask", "SECRET_QUESTION", "yes", None, "2026-09-01 17:56:00"),
                    (2, "SOUPACTIVE", 2, "ask", "SECRET_AI_QUESTION", "no", None, "2026-09-01 17:57:00"),
                    (3, "SOUPOLD", 3, "ask", "SECRET_OLD_QUESTION", "unknown", None, "2026-09-01 16:30:00"),
                    (4, "SOUPDONE", 3, "guess", "SECRET_GUESS", "correct", "SECRET_HINT", "2026-09-01 17:30:00"),
                ],
            )

    def _dashboard(self, range_key="24h"):
        return admin_dashboard.build_activity_dashboard(
            self.duel_db,
            self.turtle_db,
            range_key,
            now=self.NOW,
        )

    def test_duel_active_filter_npc_and_started_room_metrics(self):
        data = self._dashboard()["duel"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["realtime"], {
            "active_rooms": 3,
            "current_open_rooms": 3,
            "active_humans": 3,
            "active_bound_machines": 2,
            "active_npc_rooms": 1,
        })
        self.assertEqual(data["range"]["new_rooms"], 4)
        self.assertEqual(data["range"]["started_rooms"], 3)
        self.assertEqual(data["range"]["completed_rooms"], 1)
        self.assertEqual(data["range"]["participants"], {"human": 3, "bound_machine": 2})
        self.assertEqual(data["npc"]["rooms"], 1)
        self.assertEqual(data["npc"]["participant_occurrences"], 1)
        self.assertEqual(data["npc"]["distinct_personas"], 1)
        self.assertAlmostEqual(data["npc"]["started_room_share"], 1 / 3, places=4)
        self.assertNotIn("recent_rooms", data)

    def test_chip_active_data_is_counted_without_double_counting_ledger_stakes(self):
        chips = self._dashboard()["duel"]["chips"]
        self.assertEqual(chips["daily_check_ins"]["count"], 3)
        self.assertEqual(chips["daily_check_ins"]["distinct_subjects"], 2)
        self.assertEqual(chips["settlements"], {
            "rooms": 1,
            "total_stake": 20,
            "median_stake": 20.0,
            "participants": {"human": 1, "bound_machine": 1},
        })
        self.assertEqual(chips["exchange"]["requests"], 3)
        self.assertEqual(chips["exchange"]["distinct_pairs"], 2)
        self.assertEqual(chips["exchange"]["status_counts"]["completed"], 1)
        self.assertEqual(chips["loans"]["created"], 2)
        self.assertEqual(chips["loans"]["accepted"], 2)
        self.assertEqual(chips["loans"]["repaid"], 1)
        self.assertEqual(chips["achievements"]["unlocks"], 2)
        self.assertEqual(chips["achievements"]["reward_chips"], 15)
        self.assertTrue(chips["achievements"]["automatic"])
        self.assertEqual(chips["wallets_current"]["bankruptcy_badges"]["total"], 1)
        self.assertEqual(chips["wallets_current"]["negative_balances"]["total"], 1)

    def test_turtle_activity_uses_selected_window_evidence_and_finished_timestamps(self):
        data = self._dashboard()["turtle"]
        self.assertTrue(data["ok"])
        self.assertEqual(data["realtime"], {
            "active_rooms": 2,
            "active_humans": 2,
            "active_ai": 1,
        })
        self.assertEqual(data["range"]["new_rooms"], 3)
        self.assertEqual(data["range"]["completed_rooms"], 1)
        self.assertEqual(data["range"]["solved_rooms"], 1)
        self.assertEqual(data["range"]["question_count"], 3)
        self.assertEqual(data["range"]["ended_without_winner"], 0)
        self.assertEqual(data["range"]["participants"], {"human": 2, "ai": 1})
        self.assertEqual(data["range"]["finished_duration_minutes"], {
            "average": 30.0,
            "median": 30.0,
            "sample": 1,
        })

    def test_selected_range_controls_duel_and_turtle_activity_window(self):
        expected = {
            "10m": {
                "duel": (2, 2, 1, 1),
                "turtle": (1, 1, 1),
            },
            "1h": {
                "duel": (2, 2, 1, 1),
                "turtle": (1, 1, 1),
            },
            "6h": {
                "duel": (3, 3, 2, 1),
                "turtle": (2, 2, 1),
            },
            "12h": {
                "duel": (3, 3, 2, 1),
                "turtle": (2, 2, 1),
            },
            "24h": {
                "duel": (3, 3, 2, 1),
                "turtle": (2, 2, 1),
            },
        }
        for range_key, counts in expected.items():
            with self.subTest(range_key=range_key):
                data = self._dashboard(range_key)
                duel = data["duel"]["realtime"]
                turtle = data["turtle"]["realtime"]
                self.assertEqual(
                    (
                        duel["active_rooms"],
                        duel["active_humans"],
                        duel["active_bound_machines"],
                        duel["active_npc_rooms"],
                    ),
                    counts["duel"],
                )
                self.assertEqual(duel["current_open_rooms"], duel["active_rooms"])
                self.assertEqual(
                    (turtle["active_rooms"], turtle["active_humans"], turtle["active_ai"]),
                    counts["turtle"],
                )
                self.assertNotIn("realtime_window_minutes", data)

    def test_range_whitelist_module_degradation_and_private_fields(self):
        expected_starts = {
            "10m": "2026-09-01T09:50:00Z",
            "1h": "2026-09-01T09:00:00Z",
            "6h": "2026-09-01T04:00:00Z",
            "12h": "2026-08-31T22:00:00Z",
            "24h": "2026-08-31T10:00:00Z",
        }
        for key, expected_start in expected_starts.items():
            data = self._dashboard(key)
            self.assertEqual(data["range"]["key"], key)
            self.assertEqual(data["range"]["start_at"], expected_start)
            self.assertEqual(data["range"]["end_at"], "2026-09-01T10:00:00Z")
        for removed_or_invalid in ("today", "7d", "7d' OR 1=1 --"):
            with self.subTest(range_key=removed_or_invalid), self.assertRaises(ValueError):
                self._dashboard(removed_or_invalid)

        with self.assertLogs("admin_dashboard", level="ERROR"):
            degraded = admin_dashboard.build_activity_dashboard(
                Path(self.temp_dir.name) / "missing.db",
                self.turtle_db,
                "1h",
                now=self.NOW,
            )
        self.assertFalse(degraded["duel"]["ok"])
        self.assertTrue(degraded["turtle"]["ok"])
        with self.assertLogs("admin_dashboard", level="ERROR"):
            turtle_degraded = admin_dashboard.build_activity_dashboard(
                self.duel_db,
                Path(self.temp_dir.name) / "missing-turtle.db",
                "1h",
                now=self.NOW,
            )
        self.assertTrue(turtle_degraded["duel"]["ok"])
        self.assertFalse(turtle_degraded["turtle"]["ok"])

        serialized = json.dumps(self._dashboard(), ensure_ascii=False)
        for secret in (
            "SECRET_DUEL_MESSAGE",
            "SECRET_REQUEST_NOTE",
            "SECRET_SURFACE",
            "SECRET_ANSWER",
            "SECRET_TITLE",
            "SECRET_QUESTION",
            "SECRET_AI_QUESTION",
            "SECRET_GUESS",
            "SECRET_HINT",
        ):
            self.assertNotIn(secret, serialized)
        for forbidden_key in ('"request_note"', '"surface"', '"answer"', '"content"', '"hint_text"'):
            self.assertNotIn(forbidden_key, serialized)
        self.assertNotIn('"recent_rooms"', serialized)
        self.assertNotIn('"room_id"', serialized)
        for developer_term in (
            "playing/pending",
            "revision>0",
            "terminal_at",
            "participant_kind=system_npc",
            "players.is_ai",
            "chip_settlement_batches",
        ):
            self.assertNotIn(developer_term, serialized)
        for room_id in ("ACTIVE01", "OLDPLAY1", "PENDING1", "FINISH01", "SOUPACTIVE", "SOUPOLD", "SOUPDONE"):
            self.assertNotIn(room_id, serialized)

    def test_admin_handler_requires_admin_and_rejects_unknown_range(self):
        def call(token, range_key=None):
            handler = object.__new__(server.CedarToyHandler)
            handler.path = "/api/admin/activity"
            if range_key is not None:
                handler.path += f"?range={range_key}"
            handler.headers = {"Authorization": f"Bearer {token}"} if token else {}
            sent = []
            handler._send_json = lambda payload, status=200, **kwargs: sent.append((status, payload, kwargs))
            handler._handle_admin_activity()
            return sent[-1]

        with patch.object(server, "TURTLE_DB_PATH", self.turtle_db), patch.object(server, "DUEL_DB_PATH", self.duel_db):
            self.assertEqual(call("")[0], 401)
            regular_token = server._create_account_jwt({"id": 102, "username": "Reader", "is_ai": 0, "is_admin": 0})
            self.assertEqual(call(regular_token)[0], 403)
            admin_token = server._create_account_jwt({"id": 101, "username": "Admin", "is_ai": 0, "is_admin": 1})
            status, payload, kwargs = call(admin_token)
            self.assertEqual(status, 200)
            self.assertEqual(payload["range"]["key"], "1h")
            self.assertTrue(payload["duel"]["ok"])
            self.assertEqual(kwargs["extra_headers"], {"Cache-Control": "no-cache, no-store"})
            self.assertEqual(call(admin_token, "forever")[0], 400)
            self.assertEqual(call(admin_token, "today")[0], 400)
            self.assertEqual(call(admin_token, "7d")[0], 400)

    def test_get_route_and_frontend_dashboard_controls_are_wired(self):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = "/api/admin/activity?range=24h"
        handler.headers = {}
        handler._is_soup_path = lambda: False
        dispatched = []
        handler._handle_admin_activity = lambda params: dispatched.append(params)
        handler.do_GET()
        self.assertEqual(dispatched[0]["range"], ["24h"])

        html = server.ADMIN_INDEX_PATH.read_text(encoding="utf-8")
        for marker in (
            'id="dashboardTab"',
            'id="dashboardPanel"',
            'id="dashboardRange"',
            "/api/admin/activity?range=",
            "DASHBOARD_REFRESH_MS = 30000",
            'document.addEventListener("visibilitychange"',
        ):
            self.assertIn(marker, html)
        select_html = re.search(
            r'<select id="dashboardRange">(.*?)</select>',
            html,
            flags=re.DOTALL,
        ).group(1)
        options = re.findall(
            r'<option value="([^"]+)"([^>]*)>([^<]+)</option>',
            select_html,
        )
        self.assertEqual(
            [(value, label) for value, _, label in options],
            [
                ("10m", "10分钟"),
                ("1h", "1小时"),
                ("6h", "6小时"),
                ("12h", "12小时"),
                ("24h", "24小时"),
            ],
        )
        self.assertIn("selected", options[1][1])
        self.assertNotIn('id="realtimeTitle"', html)
        self.assertNotIn('id="realtimeMetrics"', html)
        self.assertNotIn("近10分钟实时总览", html)
        self.assertNotIn("近10分钟实时", html)
        self.assertNotIn("function renderRealtime", html)
        self.assertNotIn('id="chipsDashboard"', html)
        self.assertEqual(html.count('<section class="panel dashboard-section"'), 2)

        duel_section = re.search(
            r'<section class="panel dashboard-section" id="duelDashboard".*?</section>',
            html,
            flags=re.DOTALL,
        ).group(0)
        turtle_section = re.search(
            r'<section class="panel dashboard-section" id="turtleDashboard".*?</section>',
            html,
            flags=re.DOTALL,
        ).group(0)
        self.assertIn("双弈 · 所选范围活跃", duel_section)
        self.assertIn('id="duelActivityRangeLabel"', duel_section)
        self.assertIn('id="duelActivityMetrics"', duel_section)
        self.assertIn('id="duelContent"', duel_section)
        self.assertIn('id="chipsContent"', duel_section)
        self.assertNotIn('id="turtleActivityMetrics"', duel_section)
        self.assertLess(duel_section.index('id="duelActivityMetrics"'), duel_section.index('id="duelContent"'))
        self.assertLess(duel_section.index('id="duelContent"'), duel_section.index('id="chipsContent"'))

        self.assertIn("海龟汤 · 所选范围活跃", turtle_section)
        self.assertIn('id="turtleActivityRangeLabel"', turtle_section)
        self.assertIn('id="turtleActivityMetrics"', turtle_section)
        self.assertIn('id="turtleContent"', turtle_section)
        self.assertNotIn('id="duelActivityMetrics"', turtle_section)
        self.assertLess(turtle_section.index('id="turtleActivityMetrics"'), turtle_section.index('id="turtleContent"'))
        self.assertIn('$("duelActivityRangeLabel").textContent = rangeLabel', html)
        self.assertIn('$("turtleActivityRangeLabel").textContent = rangeLabel', html)
        for developer_term in (
            "playing/pending",
            "revision>0",
            "terminal_at",
            "participant_kind=system_npc",
            "players.is_ai",
            "system_npc",
            "settlement batch",
            "distinct persona",
            "人机 pair",
            "当前破产徽章",
            "当前负余额钱包",
        ):
            self.assertNotIn(developer_term, html)
        for removed_dashboard_detail in ("最近活跃房间", "dashboard-table", "recent_rooms", "room_id"):
            self.assertNotIn(removed_dashboard_detail, html)
        for mobile_marker in (
            "@media (max-width: 420px)",
            "@media (max-width: 370px)",
            "grid-template-columns: repeat(2, minmax(0, 1fr))",
            "grid-template-columns: repeat(3, minmax(0, 1fr))",
            'class="dashboard-content" id="duelContent"',
            'class="aggregate-list"',
        ):
            self.assertIn(mobile_marker, html)


if __name__ == "__main__":
    unittest.main(verbosity=2)
