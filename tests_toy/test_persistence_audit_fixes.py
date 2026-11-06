"""Targeted regressions: real vendor runners, one fresh process per command."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from vendor_cmd_adapter import base, memoria, white_room


class PersistenceAuditFixTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="cedar-audit-fixes-", dir="/tmp")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.player = "guest:auditfix"
        for module in (base, memoria, white_room):
            context = patch.object(module, "SAVE_ROOT", self.root)
            context.start()
            self.addCleanup(context.stop)
        context = patch.dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        context.start()
        self.addCleanup(context.stop)

    def path(self, level):
        return self.root / "memoria" / self.player / f"level{level}" / memoria.LEVELS[str(level)]["save"]

    def state(self, level):
        return json.loads(self.path(level).read_text())

    def seed(self, level, **changes):
        memoria.play({"action": "new", "player_id": self.player, "level": level, "confirm": True})
        state = self.state(level)
        state.update(changes)
        self.path(level).write_text(json.dumps(state, ensure_ascii=False))

    def cmd(self, level, command):
        return memoria.play({"action": "cmd", "player_id": self.player,
                             "level": level, "command": command})["text"]

    def test_l2_trap_confirmation_and_cancellation_survive_cold_load(self):
        self.seed(2, turn=20, clues_found={"door_gap_height": True, "painter_entered_daytime": True})
        command = "combine door_gap_height painter_entered_daytime"
        self.assertIn("combine confirm", self.cmd(2, command))
        self.assertIsNotNone(self.state(2)["_pending_trap_combo"])
        self.assertIn("你取消了", self.cmd(2, "combine cancel"))
        self.assertIsNone(self.state(2)["_pending_trap_combo"])
        self.assertIn("没有待确认", self.cmd(2, "combine confirm"))
        self.cmd(2, command)
        self.assertIn("组合完成", self.cmd(2, "combine confirm"))
        self.assertIsNone(self.state(2)["_pending_trap_combo"])
        self.assertEqual(self.state(2)["combination_attempts"], 1)
        self.cmd(2, "status")
        self.assertEqual(self.state(2)["combination_history"][-1]["result"], "trap")

    def test_l3_fourth_wrong_combination_persists_lock_and_cooldown(self):
        self.seed(3, turn=20, combination_attempts=3, clues_found={"soil_layers": True})
        self.assertIn("已锁定 5", self.cmd(3, "combine soil_layers"))
        self.assertEqual(self.state(3)["combination_lockout_until"], 26)
        self.assertEqual(self.state(3)["turn"], 22)
        self.assertIn("组合台已被锁定", self.cmd(3, "combine"))
        for _ in range(3):
            self.cmd(3, "status")
        self.assertNotIn("组合台已被锁定", self.cmd(3, "combine"))

    def test_l3_reasoning_snapshots_and_marker_survive_cold_load(self):
        self.seed(3, turn=20, offset_value=6,
                  timeline_unlocked={f"{track}{i}": True for track in ("A", "B") for i in range(1, 7)},
                  combination_history=["handwriting_check", "burial_time_estimation", "ink_analysis"])
        self.assertNotIn("尚未完全解锁", self.cmd(3, "reasoning"))
        names = [s["name"] for s in self.state(3)["_snapshots"]]
        self.assertIn("调查笔记解锁", names)
        self.assertIn("偏移值首次进入最佳区间", names)
        self.assertTrue(self.state(3)["offset_unlock_saved"])
        self.assertIn("调查笔记解锁", self.cmd(3, "savepoints"))
        self.cmd(3, "reasoning")
        self.assertEqual(names, [s["name"] for s in self.state(3)["_snapshots"]])

    def test_l2_to_l5_final_counters_survive_each_process(self):
        for level in (2, 3, 4, 5):
            with self.subTest(level=level):
                self.seed(level, turn=20, time_elapsed=10)
                for turn in (21, 22):
                    self.cmd(level, "status")
                    state = self.state(level)
                    self.assertEqual(state["turn"], turn)
                    if level in (2, 3):
                        self.assertEqual(state["time_elapsed"], turn - 10)
                    if level != 5:
                        heartbeat = json.loads((self.path(level).parent / f"_heartbeat_l{level}.json").read_text())
                        self.assertEqual(heartbeat["turn"], state["turn"])
                        self.assertEqual(heartbeat["time_elapsed"], state["time_elapsed"])

    def test_l4_notebook_first_open_is_not_repeated(self):
        self.seed(4, turn=20)
        self.assertIn("封面已经软了", self.cmd(4, "notebook"))
        self.assertTrue(self.state(4)["_notebook_opened"])
        self.assertNotIn("封面已经软了", self.cmd(4, "notebook"))

    def test_l4_l5_cold_load_restores_saved_rng(self):
        for level in (4, 5):
            with self.subTest(level=level):
                self.seed(level, rng_state=24680, rng_calls=7)
                self.cmd(level, "status")
                self.assertEqual(self.state(level)["rng_state"], 24680)
                self.assertEqual(self.state(level)["rng_calls"], 7)

    def test_semicolon_pending_and_rest_are_saved_without_extra_turns(self):
        self.seed(2, turn=20, clues_found={"door_gap_height": True, "painter_entered_daytime": True})
        self.cmd(2, "combine door_gap_height painter_entered_daytime; status")
        self.assertEqual(self.state(2)["turn"], 20)  # author's batch semantics
        self.assertIn("组合完成", self.cmd(2, "combine confirm"))
        self.seed(3, turn=20, offset_value=10, rest_count=9)
        self.cmd(3, "rest; rest; status")
        self.assertEqual(self.state(3)["offset_value"], 6)
        self.assertEqual(self.state(3)["turn"], 22)
        self.cmd(3, "status")
        self.assertEqual(self.state(3)["offset_value"], 6)

    def test_memoria_failed_final_save_raises_and_preserves_main_save(self):
        self.seed(2, turn=20)
        before = self.path(2).read_bytes()
        # Inject the filesystem failure in the child, after the real cold load.
        runner = memoria.RUNNER_CODE.replace(
            'text = mod.cmd(command)',
            'text = mod.cmd(command)\n'
            'def fail_replace(*args): raise OSError("injected write failure")\n'
            'os.replace = fail_replace',
        )
        with patch.object(memoria.GAME, "runner_code", runner):
            with self.assertRaisesRegex(base.VendorCmdError, "injected write failure"):
                self.cmd(2, "status")
        self.assertEqual(self.path(2).read_bytes(), before)
        self.assertFalse(list(self.path(2).parent.glob(".memoria-*")))

    def test_white_room_cancel_is_saved_and_confirmation_guard_remains(self):
        def play(action, **extra):
            return white_room.play({"action": action, "player_id": self.player, **extra})["text"]
        play("new")
        play("cmd", command="光")
        play("restart")
        play("status")
        path = self.root / "white_room" / self.player / white_room.SAVE_NAME
        self.assertFalse(json.loads(path.read_text())["pending_restart"])
        with self.assertRaisesRegex(base.VendorCmdError, "confirm=true"):
            play("restart_confirm")
        self.assertIn("当前没有等待确认", play("restart_confirm", confirm=True))
        self.assertEqual(json.loads(path.read_text())["total_inputs"], 1)
        play("restart")
        play("restart_confirm", confirm=True)
        self.assertEqual(json.loads(path.read_text())["total_inputs"], 0)


if __name__ == "__main__":
    unittest.main()
