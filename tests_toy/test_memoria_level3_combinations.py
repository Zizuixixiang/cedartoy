import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, memoria


class MemoriaLevel3CombinationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="memoria-feedback24-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.save_root = self.root / "saves"
        self.player_id = "guest:memoriafeedback24"
        cfg = memoria.LEVELS["3"]
        # Import-time heartbeat recovery must never read the vendor's live files.
        vendor = self.root / "vendor"
        level_dir = vendor / cfg["dir"]
        level_dir.mkdir(parents=True)
        shutil.copyfile(memoria.GAME.vendor_dir / cfg["dir"] / "detective_l3.py",
                        level_dir / "detective_l3.py")
        for target, attribute, value in (
            (base, "SAVE_ROOT", self.save_root),
            (memoria, "SAVE_ROOT", self.save_root),
            (memoria.GAME, "vendor_dir", vendor),
        ):
            active = patch.object(target, attribute, value)
            active.start()
            self.addCleanup(active.stop)
        self.path = self.save_root / "memoria" / self.player_id / "level3" / cfg["save"]
        memoria.play({"action": "new", "player_id": self.player_id,
                      "level": 3, "difficulty": "normal"})
        self.seed(
            turn=20, offset_value=3,
            clues_found={cid: True for cid in (
                "investigation_notebook", "notebook_blacked_out", "desk_notepad",
                "soil_layers", "marker_tags", "brick_residue", "duty_roster_hidden",
                "platform_sign", "duty_record_waterlogged", "photo_envelope",
                "missing_passenger_file", "ticket_stubs",
            )},
        )

    def state(self):
        return json.loads(self.path.read_text(encoding="utf-8"))

    def seed(self, **changes):
        state = self.state()
        state.update(changes)
        self.path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")

    def cmd(self, command):
        # Real adapter + a fresh runner on every call, with only temporary saves.
        return memoria.play({"action": "cmd", "player_id": self.player_id,
                             "level": 3, "command": command})["text"]

    def generate_a(self):
        for ingredients in ("notebook_blacked_out", "soil_layers marker_tags",
                            "brick_residue", "notebook_blacked_out duty_roster_hidden"):
            self.assertIn("组合成功", self.cmd("combine " + ingredients))

    def generate_b(self):
        for ingredients in ("platform_sign duty_record_waterlogged", "photo_envelope",
                            "missing_passenger_file ticket_stubs"):
            self.assertIn("组合成功", self.cmd("combine " + ingredients))

    def assert_state_unchanged_except_clock(self, before):
        after = self.state()
        self.assertEqual(set(after), set(before))
        for key in before:
            if key not in {"turn", "time_elapsed"}:
                self.assertEqual(after[key], before[key], key)

    def test_same_input_generates_handwriting_then_ink_before_repeat(self):
        for output_id in ("handwriting_report", "ink_report"):
            self.assertIn("组合成功", self.cmd("combine notebook_blacked_out"))
            self.assertEqual(self.state()["generated_cards"][-1]["id"], output_id)
        state = self.state()
        self.assertTrue(state["notebook_analyzed"])
        self.assertTrue(state["ink_analyzed"])
        self.assertEqual(state["combination_history"], ["handwriting_check", "ink_analysis"])
        self.assertEqual(state["combination_attempts"], 0)
        self.assertIn("已完成", self.cmd("combine notebook_blacked_out"))
        self.assert_state_unchanged_except_clock(state)

    def test_optional_ingredients_match_only_their_original_recipe(self):
        command = "combine notebook_blacked_out desk_notepad"
        self.assertIn("组合成功", self.cmd(command))
        state = self.state()
        self.assertIn("已完成", self.cmd(command))
        self.assert_state_unchanged_except_clock(state)
        self.assertIn("组合成功", self.cmd("combine notebook_blacked_out"))
        self.assertEqual(self.state()["generated_cards"][-1]["id"], "ink_report")

    def test_a_outputs_hint_and_arrange_preserve_game_rules(self):
        self.generate_a()
        state = self.state()
        self.assertEqual([g["id"] for g in state["generated_cards"]],
                         ["handwriting_report", "burial_time", "cause_of_death", "last_contact"])
        for cid in ("victim_identity", "last_four_days", "killer_window"):
            self.assertIn(cid, state["clues_found"])
        for cid in ("burial_time", "cause_of_death", "last_contact"):
            self.assertNotIn(cid, state["clues_found"])
        hint = self.cmd("hint")
        for name in ("遗骸埋入时间推定", "死因鉴定", "死者生前最后联系对象"):
            self.assertNotIn(name, hint)
        self.assertIn("失踪乘客身份", hint)  # Real missing B cards still appear.
        self.assert_state_unchanged_except_clock(state)
        self.seed(timeline_unlocked={f"{track}{i}": True
                                    for track in ("A", "B") for i in range(1, 7)})
        self.assertIn("核心卡片进度：2/6", self.cmd(
            "arrange a victim_identity last_contact burial_time last_four_days cause_of_death killer_window"))
        self.assertIn("核心卡片进度：6/6", self.cmd(
            "arrange a victim_identity burial_time cause_of_death last_contact last_four_days killer_window"))

    def test_legacy_generated_cards_keep_penalties_and_save_shape(self):
        self.generate_a()
        self.seed(combination_attempts=2, offset_value=5, reasoning_errors=1)
        before = self.state()
        # This is the unchanged legacy representation: generated outputs are
        # absent from clues_found; no migration or per-save patch marker exists.
        self.assertIn("已完成", self.cmd("combine notebook_blacked_out duty_roster_hidden"))
        self.assert_state_unchanged_except_clock(before)
        self.cmd("hint")
        self.cmd("status")
        self.assert_state_unchanged_except_clock(before)

    def test_b_outputs_and_completed_guidance(self):
        self.generate_a()
        self.cmd("combine notebook_blacked_out")  # Finish ink analysis as well.
        self.generate_b()
        before = self.state()
        hint = self.cmd("hint")
        self.assertNotIn("❓", hint)
        self.assert_state_unchanged_except_clock(before)
        for command in ("combine platform_sign duty_record_waterlogged",
                        "combine photo_envelope", "combine missing_passenger_file ticket_stubs"):
            self.assertIn("已完成", self.cmd(command))
        self.assert_state_unchanged_except_clock(before)
        # Every recipe is done: a true failure must not recommend any of them.
        text = self.cmd("combine soil_layers brick_residue")
        self.assertIn("❌", text)
        self.assertNotIn("💡 试试", text)
        self.assertIn("用 clues", text)
        self.assertEqual(self.state()["combination_attempts"], before["combination_attempts"] + 1)
        self.assertEqual(self.state()["offset_value"], before["offset_value"] + 1)

    def test_failure_suggests_unfinished_recipe_and_keeps_lockout(self):
        self.generate_a()
        self.cmd("combine notebook_blacked_out")
        self.seed(combination_attempts=3, offset_value=3)
        text = self.cmd("combine soil_layers brick_residue")
        self.assertIn("组合台已锁定", text)
        self.assertNotIn("找出最后联系对象", text)
        self.assertIn("combine duty_record_waterlogged platform_sign", text)
        state = self.state()
        self.assertEqual(state["combination_attempts"], 4)
        self.assertEqual(state["offset_value"], 4)
        self.assertGreater(state["combination_lockout_until"], state["turn"])
        self.assertIn("组合台已被锁定", self.cmd("combine notebook_blacked_out duty_roster_hidden"))
        self.assert_state_unchanged_except_clock(state)

    def test_unavailable_or_extra_input_does_not_bypass_validation(self):
        self.cmd("combine notebook_blacked_out")
        state = self.state()
        self.assertIn("还没有解锁", self.cmd("combine notebook_blacked_out unknown_clue"))
        self.assert_state_unchanged_except_clock(state)
        text = self.cmd("combine notebook_blacked_out soil_layers")
        self.assertIn("❌", text)
        self.assertEqual(self.state()["combination_attempts"], 1)
        self.assertEqual(self.state()["offset_value"], state["offset_value"] + 1)

    def test_chained_commands_and_no_hint_difficulty(self):
        text = self.cmd("combine notebook_blacked_out; combine notebook_blacked_out; combine notebook_blacked_out")
        self.assertEqual(text.count("组合成功"), 2)
        self.assertEqual(text.count("该组合已完成"), 1)
        state = self.state()
        self.assertEqual(state["combination_attempts"], 0)
        self.assertEqual(state["offset_value"], 3)
        self.cmd("status")
        self.assert_state_unchanged_except_clock(state)
        self.seed(difficulty="hell")
        self.assertIn("噩梦难度不提供提示", self.cmd("hint"))


if __name__ == "__main__":
    unittest.main()
