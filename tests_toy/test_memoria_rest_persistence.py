import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, memoria


class MemoriaRestPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="memoria-rest-")
        self.addCleanup(self.temp_dir.cleanup)
        self.save_root = Path(self.temp_dir.name)
        for module in (base, memoria):
            active_patch = patch.object(module, "SAVE_ROOT", self.save_root)
            active_patch.start()
            self.addCleanup(active_patch.stop)
        # Slot 1 has no suffix. Both this account and slot 2 exist only in tmp.
        self.player_id = "999021"
        self.path = self.save_root / "memoria" / self.player_id / "level3" / memoria.LEVELS["3"]["save"]

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def seed(self, **changes):
        memoria.play({"action": "new", "player_id": self.player_id,
                      "level": 3, "difficulty": "normal", "confirm": True})
        state = self.state()
        state.update(changes)
        self.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def cmd(self, command):
        # Each call uses the real adapter and a fresh runner process.
        return memoria.play({"action": "cmd", "player_id": self.player_id,
                             "command": command})["text"]

    def test_repeated_rest_survives_reload_and_opens_reasoning_in_both_scenes(self):
        for scene in ("waiting_room", "platform"):
            with self.subTest(scene=scene):
                self.seed(current_scene=scene, offset_value=10, rest_count=9, turn=20,
                          timeline_unlocked={f"{track}{i}": True
                                             for track in ("A", "B") for i in range(1, 7)},
                          combination_history=["handwriting_check", "burial_time_estimation", "ink_analysis"])
                for before, after in ((10, 8), (8, 6)):
                    self.assertIn(f"偏移值 {before}→{after}", self.cmd("rest"))
                    self.assertEqual(self.state()["offset_value"], after)
                    heartbeat = json.loads((self.path.parent / "_heartbeat_l3.json").read_text())
                    self.assertEqual(heartbeat["offset_value"], after)
                    self.assertNotIn("混沌", self.cmd("status"))
                    self.assertEqual(self.state()["offset_value"], after)
                self.assertEqual(self.state()["rest_count"], 11)
                self.assertNotIn("尚未完全解锁", self.cmd("reasoning"))
                self.assertNotIn("偏移值不在解锁区间", self.cmd("timeline"))
                self.assertEqual(self.state()["offset_value"], 6)
                destination = "月台" if scene == "waiting_room" else "候车室"
                self.cmd(f"go {destination}")
                self.assertEqual(self.state()["current_scene"],
                                 "platform" if scene == "waiting_room" else "waiting_room")
                self.assertEqual(self.state()["offset_value"], 6)

    def test_chained_rest_persists_final_offset_without_touching_another_slot(self):
        memoria.play({"action": "new", "player_id": self.player_id + ":2", "level": 3})
        other = self.path.parents[1].with_name(self.player_id + ":2") / "level3" / self.path.name
        before = other.read_bytes()
        self.seed(offset_value=10, rest_count=9, turn=20)
        result = self.cmd("rest; rest; status")
        self.assertIn("偏移值 10→8", result)
        self.assertIn("偏移值 8→6", result)
        self.assertEqual(self.state()["offset_value"], 6)
        self.cmd("status")
        self.assertEqual(self.state()["offset_value"], 6)
        self.assertEqual(other.read_bytes(), before)

    def test_original_rest_strength_and_zero_floor_are_preserved(self):
        for offset, rest_count, expected in ((10, 0, 5), (1, 0, 0), (0, 9, 0)):
            with self.subTest(offset=offset, rest_count=rest_count):
                self.seed(offset_value=offset, rest_count=rest_count, turn=20)
                self.assertIn(f"偏移值 {offset}→{expected}", self.cmd("rest"))
                self.cmd("status")
                self.assertEqual(self.state()["offset_value"], expected)
                self.assertEqual(self.state()["rest_count"], rest_count + 1)


if __name__ == "__main__":
    unittest.main()
