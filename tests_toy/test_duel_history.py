from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server


class DuelHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="duel-history-")
        self.db_path = Path(self.tempdir.name) / "duel.db"
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE rooms (
                    room_id TEXT PRIMARY KEY,
                    game_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    winner TEXT,
                    winner_player_id TEXT,
                    result_json TEXT,
                    terminal_reason TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    terminal_at TEXT
                );
                CREATE TABLE room_participants (
                    room_id TEXT NOT NULL,
                    player_id TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    role TEXT NOT NULL,
                    participant_kind TEXT NOT NULL,
                    seat_index INTEGER NOT NULL,
                    join_status TEXT NOT NULL
                );
                """
            )
        self.path_patch = patch.object(server, "DUEL_DB_PATH", self.db_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        self.tempdir.cleanup()

    def _room(
        self,
        room_id,
        game_type,
        status,
        *,
        winner=None,
        winner_player_id=None,
        result=None,
        terminal_reason=None,
        minute=0,
    ):
        created_at = f"2026-09-01T08:{minute:02d}:00+00:00"
        terminal_at = (
            f"2026-09-01T09:{minute:02d}:00+00:00"
            if status in {"finished", "archived"}
            else None
        )
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO rooms (
                    room_id, game_type, status, winner, winner_player_id,
                    result_json, terminal_reason, created_at, updated_at,
                    terminal_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    game_type,
                    status,
                    winner,
                    winner_player_id,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    terminal_reason,
                    created_at,
                    terminal_at or created_at,
                    terminal_at,
                ),
            )

    def _participant(
        self,
        room_id,
        player_id,
        display_name,
        role,
        kind,
        seat,
        *,
        join_status="joined",
    ):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT INTO room_participants (
                    room_id, player_id, display_name, role,
                    participant_kind, seat_index, join_status
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    room_id,
                    str(player_id),
                    display_name,
                    role,
                    kind,
                    seat,
                    join_status,
                ),
            )

    def _human_and_machine(self, room_id, *, machine_status="joined"):
        self._participant(room_id, 10, "小杉", "human", "human", 0)
        self._participant(
            room_id,
            11,
            "青团机",
            "ai",
            "bound_machine",
            1,
            join_status=machine_status,
        )

    def test_reads_real_rooms_with_subject_relative_results(self):
        self._room(
            "WINROOM1",
            "gomoku",
            "finished",
            winner="human",
            winner_player_id="10",
            result={"winner_player_id": "10", "draw": False},
            minute=1,
        )
        self._human_and_machine("WINROOM1")

        self._room(
            "BLACKJ21",
            "blackjack",
            "finished",
            winner="draw",
            result={
                "draw": True,
                "outcomes_by_player": {
                    "10": {"outcome": "loss", "result_text": "爆牌，负"},
                    "11": {"outcome": "push", "result_text": "与庄家同点，推和"},
                },
            },
            minute=2,
        )
        self._human_and_machine("BLACKJ21")

        self._room(
            "TEAMROOM",
            "doudizhu",
            "finished",
            winner="ai",
            winner_player_id="11",
            result={
                "winner_player_id": "11",
                "winning_player_ids": ["11", "npc:cedar"],
                "draw": False,
            },
            minute=3,
        )
        self._human_and_machine("TEAMROOM")
        self._participant(
            "TEAMROOM", "npc:cedar", "雪松 NPC", "ai", "system_npc", 2
        )

        self._room(
            "STALEOLD",
            "tictactoe",
            "archived",
            winner="draw",
            result={"draw": True},
            terminal_reason="stale_archive",
            minute=4,
        )
        self._human_and_machine("STALEOLD")

        self._room("PENDING1", "xiangqi", "pending", minute=5)
        self._human_and_machine("PENDING1", machine_status="invited")

        human = server._duel_history_for_user(
            {"id": 10, "username": "小杉", "is_ai": False}
        )
        machine = server._duel_history_for_user(
            {"id": 11, "username": "青团机", "is_ai": True}
        )

        self.assertTrue(human["available"])
        self.assertEqual(human["total"], 5)
        human_by_room = {item["room_id"]: item for item in human["matches"]}
        self.assertEqual(human_by_room["WINROOM1"]["outcome"], "win")
        self.assertEqual(human_by_room["BLACKJ21"]["outcome"], "loss")
        self.assertEqual(human_by_room["BLACKJ21"]["result_detail"], "爆牌，负")
        self.assertEqual(human_by_room["TEAMROOM"]["outcome"], "loss")
        self.assertEqual(
            human_by_room["TEAMROOM"]["opponents"], ["青团机", "雪松 NPC"]
        )
        self.assertEqual(human_by_room["STALEOLD"]["outcome"], "archived")
        self.assertEqual(human_by_room["PENDING1"]["outcome"], "pending")
        self.assertNotIn("slot", human_by_room["WINROOM1"])

        self.assertEqual(machine["total"], 4)
        machine_by_room = {item["room_id"]: item for item in machine["matches"]}
        self.assertNotIn("PENDING1", machine_by_room)
        self.assertEqual(machine_by_room["WINROOM1"]["outcome"], "loss")
        self.assertEqual(machine_by_room["BLACKJ21"]["outcome"], "draw")
        self.assertEqual(machine_by_room["TEAMROOM"]["outcome"], "win")
        self.assertEqual(machine_by_room["WINROOM1"]["opponents"], ["小杉"])

    def test_limit_keeps_total_count(self):
        for index in range(3):
            room_id = f"LIMIT{index:03d}"
            self._room(
                room_id,
                "gomoku",
                "finished",
                winner="human",
                winner_player_id="10",
                result={"winner_player_id": "10", "draw": False},
                minute=index,
            )
            self._human_and_machine(room_id)

        result = server._duel_history_for_user(
            {"id": 10, "username": "小杉", "is_ai": False}, limit=2
        )

        self.assertEqual(result["total"], 3)
        self.assertEqual(result["limit"], 2)
        self.assertEqual(len(result["matches"]), 2)

    def test_history_payload_keeps_duel_separate_from_saves(self):
        base = {
            "user": {"id": 10},
            "self": {
                "username": "小杉",
                "user": {"id": 10, "is_ai": False},
                "saves": {"forest": {"slots": [{"slot": 1}]}},
            },
            "machines": [
                {
                    "username": "青团机",
                    "user": {"id": 11, "is_ai": True},
                    "saves": {},
                }
            ],
        }
        with (
            patch.object(server, "_account_web_saves", return_value=base),
            patch.object(
                server,
                "_duel_history_for_user",
                side_effect=[
                    {"available": True, "total": 1, "matches": [{"room_id": "A"}]},
                    {"available": True, "total": 2, "matches": [{"room_id": "B"}]},
                ],
            ),
        ):
            result = server._account_web_history("token")

        self.assertIn("forest", result["self"]["saves"])
        self.assertNotIn("duel", result["self"]["saves"])
        self.assertEqual(result["self"]["duel_history"]["total"], 1)
        self.assertEqual(result["machines"][0]["duel_history"]["total"], 2)


if __name__ == "__main__":
    unittest.main()
