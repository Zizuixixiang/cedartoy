import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, memoria


class MemoriaDifficultyRestartTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.patches = (
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(memoria, "SAVE_ROOT", self.save_root),
        )
        for active_patch in self.patches:
            active_patch.start()

    def tearDown(self):
        for active_patch in reversed(self.patches):
            active_patch.stop()
        self.temp_dir.cleanup()

    def _save_path(self, player_id, level):
        config = memoria.LEVELS[level]
        return (
            self.save_root
            / "memoria"
            / player_id
            / f"level{level}"
            / config["save"]
        )

    def _state(self, player_id, level):
        return json.loads(self._save_path(player_id, level).read_text(encoding="utf-8"))

    def _mark_completed(self, player_id, level):
        path = self._save_path(player_id, level)
        state = json.loads(path.read_text(encoding="utf-8"))
        state["game_ended"] = True
        state["ending"] = "isolated_test_completion"
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def test_completed_normal_games_restart_hard_and_reload_hard(self):
        for level in ("1", "2", "3", "4"):
            with self.subTest(level=level):
                player_id = f"memoriarestart{level}"
                memoria.play(
                    {
                        "action": "new",
                        "player_id": player_id,
                        "level": level,
                        "difficulty": "normal",
                    }
                )
                self.assertEqual(self._state(player_id, level)["difficulty"], "normal")
                self._mark_completed(player_id, level)

                memoria.play(
                    {
                        "action": "cmd",
                        "player_id": player_id,
                        "level": level,
                        "command": "new_game hard",
                    }
                )
                restarted = self._state(player_id, level)
                self.assertEqual(restarted["difficulty"], "hard")
                self.assertFalse(restarted.get("game_ended", False))

                # A second adapter call launches a fresh runner process and
                # therefore verifies that hard was persisted, not just held in memory.
                memoria.play(
                    {
                        "action": "cmd",
                        "player_id": player_id,
                        "level": level,
                        "command": "status",
                    }
                )
                self.assertEqual(self._state(player_id, level)["difficulty"], "hard")

    def test_formal_new_action_restarts_hard_for_levels_one_through_four(self):
        for level in ("1", "2", "3", "4"):
            with self.subTest(level=level):
                player_id = f"memoriaformal{level}"
                memoria.play(
                    {
                        "action": "new",
                        "player_id": player_id,
                        "level": level,
                        "difficulty": "normal",
                    }
                )
                memoria.play(
                    {
                        "action": "new",
                        "player_id": player_id,
                        "level": level,
                        "difficulty": "hard",
                        "confirm": True,
                    }
                )
                self.assertEqual(self._state(player_id, level)["difficulty"], "hard")

                memoria.play(
                    {
                        "action": "cmd",
                        "player_id": player_id,
                        "level": level,
                        "command": "status",
                    }
                )
                self.assertEqual(self._state(player_id, level)["difficulty"], "hard")

    def test_invalid_restart_difficulty_is_rejected_without_changing_save(self):
        player_id = "memoriainvalid"
        level = "2"
        memoria.play(
            {
                "action": "new",
                "player_id": player_id,
                "level": level,
                "difficulty": "normal",
            }
        )
        path = self._save_path(player_id, level)
        before = path.read_bytes()

        with self.assertRaisesRegex(memoria.VendorCmdError, "normal/hard/hell"):
            memoria.play(
                {
                    "action": "cmd",
                    "player_id": player_id,
                    "level": level,
                    "command": "new_game nightmare",
                }
            )
        self.assertEqual(path.read_bytes(), before)

        with self.assertRaisesRegex(memoria.VendorCmdError, "normal/hard/hell"):
            memoria.play(
                {
                    "action": "new",
                    "player_id": "memoriainvalidformal",
                    "level": "1",
                    "difficulty": "nightmare",
                }
            )


if __name__ == "__main__":
    unittest.main()
