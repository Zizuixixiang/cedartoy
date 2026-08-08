from __future__ import annotations

import importlib
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


TOY_ROOT = Path(__file__).resolve().parents[1]
ENGINE_ROOT = TOY_ROOT / "vendor" / "Garden-Cat-Engine"
sys.path.insert(0, str(ENGINE_ROOT))


class GardenCatSaveManagementTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="garden-management-")
        self.root = Path(self.tempdir.name)
        self.save_root = self.root / "saves"
        os.environ["GARDEN_CAT_SAVE_ROOT"] = str(self.save_root)
        import game_api

        self.api = importlib.reload(game_api)
        self.api.NOTE_DB_PATH = str(self.root / "garden_notes.db")
        self.client = self.api.app.test_client()

    def tearDown(self):
        self.api.STORE.clear_cache_for_test()
        self.tempdir.cleanup()

    def _write_state(self, player_id, money):
        with self.api.STORE.player_state(player_id) as state:
            state["money"] = money
            state["encyclopedia"] = ["daisy", "tulip"]
            self.api.STORE.save(player_id, state)
            return state

    def _add_note(self, player_id, *, content="今天开花", author_name="阿橘", created_at=123):
        self.api.ensure_notes_schema()
        with sqlite3.connect(self.api.NOTE_DB_PATH) as conn:
            conn.execute(
                """
                INSERT INTO garden_notes
                    (session_id, author_type, author_name, content, created_at)
                VALUES (?, 'ai', ?, ?, ?)
                """,
                (player_id, author_name, content, created_at),
            )
            conn.commit()

    def _notes(self):
        with sqlite3.connect(self.api.NOTE_DB_PATH) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute(
                """
                SELECT session_id, author_type, author_name, content, created_at
                FROM garden_notes ORDER BY id
                """
            )]

    def test_loaded_cache_and_notes_migrate_then_source_cannot_revive(self):
        source = "guest:gardenmove"
        target = "81:2"
        stale_state = self._write_state(source, 137)
        self._add_note(source)

        response = self.client.post(
            "/internal/saves/migrate",
            json={"source_player_id": source, "target_player_id": target},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(response.get_json()["notes_migrated"], 1)
        self.assertNotIn(source, self.api.STORE._cache)
        self.assertIn(target, self.api.STORE._cache)
        self.assertFalse((self.save_root / source).exists())
        self.assertTrue((self.save_root / target / "state.json").is_file())
        with self.api.STORE.player_state(target) as target_state:
            self.assertEqual(target_state["money"], 137)
            self.assertEqual(target_state["encyclopedia"], ["daisy", "tulip"])
        self.assertEqual(
            self._notes(),
            [{
                "session_id": target,
                "author_type": "ai",
                "author_name": "阿橘",
                "content": "今天开花",
                "created_at": 123,
            }],
        )

        with self.assertRaises(self.api.RetiredPlayerId):
            self.api.STORE.save(source, stale_state)
        stale_request = self.client.get(
            "/api/status",
            headers={"X-Player-Id": source},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(stale_request.status_code, 409)
        self.assertFalse((self.save_root / source).exists())

    def test_target_notes_conflict_rolls_back_without_changing_either_side(self):
        source = "guest:notesconflict"
        target = "81:3"
        self._write_state(source, 52)
        self._add_note(source, content="来源", created_at=10)
        self._add_note(target, content="目标", created_at=20)
        before = (self.save_root / source / "state.json").read_bytes()

        response = self.client.post(
            "/internal/saves/migrate",
            json={"source_player_id": source, "target_player_id": target},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 409, response.get_json())
        self.assertEqual((self.save_root / source / "state.json").read_bytes(), before)
        self.assertFalse((self.save_root / target).exists())
        self.assertIn(source, self.api.STORE._cache)
        self.assertNotIn(target, self.api.STORE._cache)
        self.assertEqual(
            [(row["session_id"], row["content"]) for row in self._notes()],
            [(source, "来源"), (target, "目标")],
        )

    def test_export_import_round_trip_replaces_cache_but_not_notes(self):
        player_id = "81:2"
        self._write_state(player_id, 137)
        self._add_note(player_id, content="不要跟着存档走")

        response = self.client.post(
            "/internal/saves/export",
            json={"player_id": player_id},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 200, response.get_json())
        exported = response.get_json()["save_data"]
        self.assertEqual(exported["money"], 137)

        blocked = self.client.post(
            "/internal/saves/import",
            json={"player_id": player_id, "save_data": {**exported, "money": 1}},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(blocked.status_code, 409, blocked.get_json())

        imported = self.client.post(
            "/internal/saves/import",
            json={
                "player_id": player_id,
                "save_data": {**exported, "money": 44},
                "confirm": True,
            },
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(imported.status_code, 200, imported.get_json())
        with self.api.STORE.player_state(player_id) as state:
            self.assertEqual(state["money"], 44)
        disk = json.loads((self.save_root / player_id / "state.json").read_text(encoding="utf-8"))
        self.assertEqual(disk["money"], 44)
        self.assertEqual(
            [(row["session_id"], row["content"]) for row in self._notes()],
            [(player_id, "不要跟着存档走")],
        )

    def test_export_missing_save_returns_404_without_creating_slot(self):
        player_id = "81:4"
        response = self.client.post(
            "/internal/saves/export",
            json={"player_id": player_id},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertEqual(response.status_code, 404, response.get_json())
        self.assertFalse((self.save_root / player_id).exists())

    def test_delete_clears_disk_cache_and_notes_without_stale_regeneration(self):
        player_id = "81:5"
        self._write_state(player_id, 99)
        self._add_note(player_id)
        self.assertIn(player_id, self.api.STORE._cache)

        response = self.client.post(
            "/internal/saves/delete",
            json={"player_id": player_id},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertTrue(response.get_json()["deleted"])
        self.assertEqual(response.get_json()["notes_deleted"], 1)
        self.assertNotIn(player_id, self.api.STORE._cache)
        self.assertFalse((self.save_root / player_id).exists())
        self.assertEqual(self._notes(), [])

        self.client.get(
            "/api/status",
            headers={"X-Player-Id": "81:4"},
            environ_base={"REMOTE_ADDR": "127.0.0.1"},
        )
        self.assertFalse((self.save_root / player_id).exists())

    def test_internal_management_rejects_non_loopback(self):
        response = self.client.post(
            "/internal/saves/delete",
            json={"player_id": "81"},
            environ_base={"REMOTE_ADDR": "203.0.113.9"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("loopback-only", response.get_json()["detail"])


if __name__ == "__main__":
    unittest.main()
