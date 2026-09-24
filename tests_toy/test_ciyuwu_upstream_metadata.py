"""Compatibility regressions for the September 2026 vendor update."""
import copy
import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from ciyuwu_adapter import handler


class CiyuwuUpstreamMetadataTests(unittest.TestCase):
    META = {"bonus_max_hp": 8, "bonus_word_slots": 2, "deform_resist": 15,
            "_tavern_regular_visits": 4}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = Path(self.tmp.name) / "sessions.db"
        self.patch = patch.object(handler, "DB_PATH", str(self.db))
        self.patch.start()
        self.addCleanup(self.patch.stop)
        with sqlite3.connect(self.db) as conn:
            handler._init_db(conn)

    def test_extra_permanent_metadata_is_extracted(self):
        self.assertEqual(handler._extract_meta(self.META), self.META)

    def test_new_game_and_command_preserve_permanent_metadata(self):
        state, _ = handler._engine_new(42, copy.deepcopy(self.META))
        state, _, _ = handler._engine_run(state, "新角")
        state, meta, _ = handler._engine_run(state, "确认")
        for key, expected in self.META.items():
            self.assertEqual(state[key], expected, key)
            self.assertEqual(meta[key], expected, key)
        self.assertEqual(state["max_hp"], 30 + state["stats"]["体"] * 2 + 8)
        self.assertEqual(state["word_slots"], 7)

    def test_old_snapshot_metadata_survives_first_new_game(self):
        state, _ = handler._engine_new(42, {})
        state.update(self.META)
        timestamp = handler._now_iso(time.time())
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ciyuwu_sessions (player_id,save_data,meta_data,created_at,last_active) VALUES (?,?,?,?,?)",
                         ("101", json.dumps(state), "{}", timestamp, timestamp))
        handler.ciyuwu_new({"player_id": "101", "seed": 42})
        with sqlite3.connect(self.db) as conn:
            row = conn.execute("SELECT save_data,meta_data FROM ciyuwu_sessions WHERE player_id='101'").fetchone()
        for blob in row:
            data = json.loads(blob)
            for key, expected in self.META.items():
                self.assertEqual(data[key], expected, key)

    def test_authoritative_meta_wins_over_old_snapshot(self):
        state, _ = handler._engine_new(42, self.META)
        timestamp = handler._now_iso(time.time())
        authoritative = dict(self.META, bonus_max_hp=12)
        with sqlite3.connect(self.db) as conn:
            conn.execute("INSERT INTO ciyuwu_sessions (player_id,save_data,meta_data,created_at,last_active) VALUES (?,?,?,?,?)",
                         ("101", json.dumps(state), json.dumps(authoritative), timestamp, timestamp))
        handler.ciyuwu_new({"player_id": "101", "seed": 42})
        with sqlite3.connect(self.db) as conn:
            state = json.loads(conn.execute("SELECT save_data FROM ciyuwu_sessions WHERE player_id='101'").fetchone()[0])
        self.assertEqual(state["bonus_max_hp"], 12)

    def test_tavern_conversation_is_not_blocked_as_unearned_reward(self):
        old = {"echoes": 3, "_tavern_regular_visits": 2}
        new = {"echoes": 99, "_tavern_regular_visits": 3, "bonus_max_hp": 8}
        clean = handler._meta_without_unearned_rewards(old, new)
        self.assertEqual(clean["echoes"], 3)
        self.assertEqual(clean["_tavern_regular_visits"], 3)
        self.assertNotIn("bonus_max_hp", clean)
        self.assertFalse(handler._meta_has_unearned_rewards(old, dict(old, _tavern_regular_visits=3)))

    def test_players_do_not_share_permanent_metadata(self):
        handler._engine_new(42, self.META)
        other, _ = handler._engine_new(42, {})
        for key in self.META:
            self.assertEqual(other[key], 0, key)

    def test_rng_snapshot_continues_exact_sequence(self):
        rng = handler.engine._DetRandom(42)
        for _ in range(10):
            rng.random()
        resumed = handler.engine._DetRandom(rng._state)
        self.assertEqual([rng.random() for _ in range(20)], [resumed.random() for _ in range(20)])

    def test_engine_does_not_mutate_input_snapshot(self):
        state, _ = handler._engine_new(42, {})
        before = copy.deepcopy(state)
        handler._engine_run(state, "帮助")
        self.assertEqual(state, before)


if __name__ == "__main__":
    unittest.main()
