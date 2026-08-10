import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from vendor_cmd_adapter import base, forest, forest_runtime
from vendor_cmd_adapter.base import VendorCmdError


ROOT = Path(__file__).resolve().parents[1]
VENDOR_DIR = ROOT / "vendor" / "mo-yao-play-games"
GAME_DATA = json.loads((VENDOR_DIR / "forest_game_data.json").read_text(encoding="utf-8"))


class ForestRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="forest-v3-")
        self.root = Path(self.temp_dir.name)

    def tearDown(self):
        self.temp_dir.cleanup()

    def invoke(self, player, action, **extra):
        return forest_runtime.run_payload(
            {
                "vendor_dir": str(VENDOR_DIR),
                "save_dir": str(self.root / player),
                "extra": {"action": action, **extra},
            }
        )

    def state(self, player):
        return json.loads((self.root / player / forest.SAVE_NAME).read_text(encoding="utf-8"))

    def web(self, player, action="web_state", **extra):
        return json.loads(self.invoke(player, action, **extra))

    def reach_line1_final(self, player):
        self.invoke(player, "start", line="1")
        self.invoke(player, "ai_choose", option="E", line="1", scene_id="opening")
        for option in ("A", "A", "A", "A"):
            self.invoke(player, "choose", option=option)
        self.assertEqual(self.state(player)["human_scene"], "shared_final_sweet")
        self.assertEqual(self.state(player)["ai_scene"], "shared_final_sweet")

    def test_new_and_start_create_v3_dual_axis_state_and_private_opening(self):
        self.invoke("fresh", "new")
        output = self.invoke("fresh", "start", line="11")
        state = self.state("fresh")
        self.assertEqual(state["version"], 2)
        self.assertEqual(state["current_scene"], "opening")
        self.assertEqual(state["human_scene"], "opening")
        self.assertEqual(state["ai_scene"], "opening")
        self.assertEqual(state["ai_mode"], "shared")
        self.assertEqual(state["pending_shared"], {})
        self.assertIn("AI 的岔路", output)
        self.assertIn("D.", output)
        self.assertIn("E.", output)
        self.assertNotIn("{player}", output)
        self.assertNotIn("{ai_name}", output)
        self.assertNotIn("{ai}", output)

    def test_human_choose_is_abc_only_and_ai_choose_is_de_only(self):
        self.invoke("strict", "start", line="1")
        with self.assertRaisesRegex(ValueError, "只接受 A/B/C"):
            self.invoke("strict", "choose", option="D")
        with self.assertRaisesRegex(ValueError, "只接受 D/E"):
            self.invoke("strict", "ai_choose", option="A")
        self.invoke("strict", "choose", option="A")
        self.assertEqual(self.state("strict")["human_scene"], "1a")
        self.invoke("strict", "ai_choose", option="D", line="1", scene_id="opening")
        self.assertEqual(self.state("strict")["ai_scene"], "ai_taste_wall")
        with self.assertRaisesRegex(ValueError, "line 与存档不一致"):
            self.invoke("strict", "ai_choose", option="D", line="2")

    def test_human_path_and_ai_d_solo_progress_independently(self):
        self.invoke("parallel", "start", line="8")
        self.invoke("parallel", "choose", option="B")
        self.invoke("parallel", "ai_choose", option="D")
        state = self.state("parallel")
        self.assertEqual(state["human_scene"], "8b")
        self.assertEqual(state["current_scene"], "8b")
        self.assertEqual(state["ai_scene"], "ai_seafloor")
        self.assertEqual(state["ai_mode"], "ai_solo")

        self.invoke("parallel", "choose", option="C")
        self.invoke("parallel", "ai_choose", option="D")
        state = self.state("parallel")
        self.assertEqual(state["human_scene"], "8b_deep")
        self.assertEqual(state["ai_scene"], "ai_scale_memory")
        self.assertEqual(state["ai_loop_count"], 1)

    def test_null_e_follows_human_for_both_action_orders(self):
        self.invoke("human-first", "start", line="1")
        self.invoke("human-first", "choose", option="B")
        self.invoke("human-first", "choose", option="C")
        self.invoke("human-first", "ai_choose", option="E", scene_id="opening")
        human_first = self.state("human-first")
        self.assertEqual(human_first["human_scene"], "1b_deep")
        self.assertEqual(human_first["ai_scene"], "1b_deep")
        self.assertEqual(human_first["ai_mode"], "following")
        self.assertEqual(human_first["pending_shared"], {})

        self.invoke("ai-first", "start", line="1")
        self.invoke("ai-first", "ai_choose", option="E", scene_id="opening")
        waiting = self.state("ai-first")
        self.assertEqual(waiting["ai_scene"], "opening")
        self.assertEqual(waiting["ai_mode"], "following")
        self.invoke("ai-first", "choose", option="B")
        self.invoke("ai-first", "choose", option="C")
        ai_first = self.state("ai-first")
        self.assertEqual(ai_first["human_scene"], "1b_deep")
        self.assertEqual(ai_first["ai_scene"], "1b_deep")
        self.assertEqual(ai_first["pending_shared"], {})

    def test_final_combo_is_order_independent_and_completion_counted_once(self):
        for player in ("human-first", "ai-first"):
            self.reach_line1_final(player)

        waiting = self.invoke("human-first", "choose", option="B")
        self.assertIn("等待同行者", waiting)
        self.invoke("human-first", "ai_choose", option="D")

        waiting = self.invoke("ai-first", "ai_choose", option="D")
        self.assertIn("等待人类", waiting)
        self.invoke("ai-first", "choose", option="B")

        first = self.state("human-first")
        second = self.state("ai-first")
        for state in (first, second):
            self.assertEqual(state["human_scene"], "ending_stop_stirring")
            self.assertEqual(state["ai_scene"], "ending_stop_stirring")
            self.assertEqual(state["current_scene"], "ending_stop_stirring")
            self.assertEqual(state["daily"]["count"], 1)
            self.assertEqual(state["completed_endings"], ["1:ending_stop_stirring"])
            self.assertEqual(len(state["souvenirs"]), 1)
            self.assertEqual(state["pending_shared"], {})
        web = self.web("human-first")
        self.assertTrue(web["current"]["is_ending"])
        self.assertEqual(web["current"]["scene_id"], "ending_stop_stirring")
        with self.assertRaisesRegex(ValueError, "已经是结局"):
            self.invoke("human-first", "ai_choose", option="D")
        self.assertEqual(self.state("human-first")["daily"]["count"], 1)

    def test_ai_solo_max_loop_uses_actual_return_chain_and_memory_is_private(self):
        self.invoke("loop", "start", line="1")
        self.invoke("loop", "choose", option="A")
        self.invoke("loop", "ai_choose", option="D")
        first_memory = self.invoke("loop", "ai_choose", option="D")
        self.assertIn("随机记忆", first_memory)
        self.assertEqual(self.state("loop")["ai_loop_count"], 1)
        second_memory = self.invoke("loop", "ai_choose", option="D")
        self.assertIn("随机记忆", second_memory)
        self.assertEqual(self.state("loop")["ai_loop_count"], 2)
        returned = self.invoke("loop", "ai_choose", option="D")
        state = self.state("loop")
        self.assertIn("ai_return_sweet", returned)
        self.assertIn("merge_candy", returned)
        self.assertIn("shared_final_sweet", returned)
        self.assertEqual(state["ai_scene"], "shared_final_sweet")
        self.assertEqual(state["ai_mode"], "shared")
        self.assertEqual(state["ai_loop_count"], 0)
        web_json = self.invoke("loop", "web_state")
        saved_json = json.dumps(state, ensure_ascii=False)
        for memory in GAME_DATA["lines"]["1"]["memory_pool"]:
            self.assertNotIn(memory["text"], web_json)
            self.assertNotIn(memory["text"], saved_json)

    def test_web_snapshot_is_recursive_privacy_allowlist_and_keeps_public_state(self):
        self.invoke("private", "start", line="8")
        game = forest_runtime._read_game(VENDOR_DIR)
        game["lines"]["8"]["opening"]["public_state"] = "海水已经没过脚踝"
        state = forest_runtime._validate_state(self.state("private"), game)
        snapshot = forest_runtime._web_snapshot(game, state, True)
        encoded = json.dumps(snapshot, ensure_ascii=False)

        forbidden_keys = {
            "ai_text", "private_text", "ai_hidden", "hidden_info", "ai_layer",
            "random_pool", "memory_pool", "ai_scene", "ai_mode", "ai_prompt",
        }

        def walk(value):
            if isinstance(value, dict):
                self.assertTrue(forbidden_keys.isdisjoint(value))
                for child in value.values():
                    walk(child)
            elif isinstance(value, list):
                for child in value:
                    walk(child)

        walk(snapshot)
        self.assertEqual(snapshot["current"]["public_state"], "海水已经没过脚踝")
        self.assertEqual({item["key"] for item in snapshot["current"]["options"]}, {"A", "B", "C"})
        opening = GAME_DATA["lines"]["8"]["opening"]
        self.assertNotIn(opening["ai_layer"]["hidden_info"], encoded)
        for option in opening["ai_layer"]["options"].values():
            self.assertNotIn(option["text"], encoded)

    def test_status_contains_dual_resume_position_and_private_context(self):
        self.invoke("resume", "start", line="8")
        self.invoke("resume", "choose", option="C")
        self.invoke("resume", "ai_choose", option="D")
        status = self.invoke("resume", "status")
        private_scene = GAME_DATA["lines"]["8"]["scenes"]["ai_seafloor"]
        self.assertIn("人类位置：8c", status)
        self.assertIn("AI 位置：ai_seafloor（ai_solo）", status)
        self.assertIn(private_scene["ai_hidden"], status)
        self.assertIn("D.", status)
        self.assertIn("E.", status)

    def test_old_flat_valid_and_removed_completed_saves_migrate_without_loss(self):
        base_state = {
            "version": 1,
            "revision": 17,
            "current_line": "1",
            "current_scene": "1a",
            "souvenirs": ["旧纪念品"],
            "completed_endings": ["9:旧结局"],
            "observations": {"1:1a": "旧观察"},
            "latest_observation_key": "1:1a",
            "participants": {"player": "旧旅人", "ai": "旧同行者"},
            "daily": {"date": "2026-08-10", "count": 2, "semantic": forest_runtime.DAILY_SEMANTIC},
            "total_lines_started": 3,
            "total_choices": 4,
            "updated_at": "2026-08-10T12:00:00+08:00",
            "historical_extra": {"keep": True},
        }
        path = self.root / "valid" / forest.SAVE_NAME
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(base_state, ensure_ascii=False), encoding="utf-8")
        before = path.read_bytes()
        status = self.invoke("valid", "status")
        self.assertIn("人类位置：1a", status)
        self.assertIn("AI 位置：1a（following）", status)
        self.assertEqual(path.read_bytes(), before)
        self.invoke("valid", "observe", content="新观察")
        migrated = self.state("valid")
        self.assertEqual(migrated["version"], 2)
        self.assertEqual(migrated["human_scene"], "1a")
        self.assertEqual(migrated["ai_scene"], "1a")
        self.assertEqual(migrated["historical_extra"], {"keep": True})
        self.assertEqual(migrated["souvenirs"], ["旧纪念品"])
        self.assertEqual(migrated["revision"], 18)

        removed = dict(base_state)
        removed.update({"current_scene": "1d", "completed_endings": ["1:1d"]})
        removed_path = self.root / "removed" / forest.SAVE_NAME
        removed_path.parent.mkdir(parents=True)
        removed_path.write_text(json.dumps(removed, ensure_ascii=False), encoding="utf-8")
        web = self.web("removed")
        self.assertIsNone(web["current"])
        self.assertEqual(web["completed_endings"], ["1:1d"])
        self.assertEqual(web["souvenirs"], ["旧纪念品"])
        self.assertFalse(list(removed_path.parent.glob(f"{forest.SAVE_NAME}.corrupt-*")))
        self.invoke("removed", "start", line="2")
        persisted = self.state("removed")
        self.assertEqual(persisted["completed_endings"], ["1:1d"])
        self.assertEqual(persisted["souvenirs"], ["旧纪念品"])
        self.assertEqual(persisted["observations"], {"1:1a": "旧观察"})
        self.assertEqual(persisted["historical_extra"], {"keep": True})


class ForestAdapterIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="forest-adapter-")
        self.save_root = Path(self.temp_dir.name)
        self.base_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.forest_patch = patch.object(forest, "SAVE_ROOT", self.save_root)
        self.base_patch.start()
        self.forest_patch.start()

    def tearDown(self):
        self.forest_patch.stop()
        self.base_patch.stop()
        self.temp_dir.cleanup()

    def state(self, player):
        return json.loads(
            (self.save_root / "forest" / player / forest.SAVE_NAME).read_text(encoding="utf-8")
        )

    def test_human_mcp_revision_sync_slots_export_and_import(self):
        initial = forest.web_state("42", player_name="小满", ai_name="阿橘")
        started = forest.web_action(
            "42", "start", expected_revision=initial["revision"],
            player_name="小满", ai_name="阿橘", line=1,
        )
        ai_result = forest.play(
            {"player_id": "42", "action": "ai_choose", "option": "E", "scene_id": "opening"}
        )["text"]
        self.assertIn("等待人类", ai_result)
        refreshed = forest.web_state("42", player_name="小满", ai_name="阿橘")
        self.assertGreater(refreshed["revision"], started["revision"])
        human = forest.web_action(
            "42", "choose", expected_revision=refreshed["revision"],
            expected_scene="opening", player_name="小满", ai_name="阿橘", option="A",
        )
        self.assertEqual(human["current"]["scene_id"], "1a")
        self.assertIn("人类位置：1a", forest.play({"player_id": "42", "action": "status"})["text"])

        forest.play({"player_id": "42:2", "action": "new"})
        forest.play({"player_id": "42:2", "action": "start", "line": 8})
        archive = json.loads(forest.play({"player_id": "42:2", "action": "export"})["text"])
        forest.play({"player_id": "99", "action": "import", "save_data": archive})
        imported = self.state("99")
        self.assertEqual(imported["current_line"], "8")
        self.assertEqual(imported["human_scene"], "opening")
        self.assertEqual(imported["ai_scene"], "opening")
        self.assertEqual(self.state("42")["current_line"], "1")

    def test_confirm_action_list_guide_and_legacy_save_summary(self):
        forest.play({"player_id": "confirm", "action": "new"})
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            forest.play({"player_id": "confirm", "action": "new"})
        with self.assertRaisesRegex(VendorCmdError, "ai_choose"):
            forest.play({"player_id": "confirm", "action": "unknown"})

        legacy_path = self.save_root / "forest" / "legacysummary" / forest.SAVE_NAME
        legacy_path.parent.mkdir(parents=True)
        legacy_path.write_text(
            json.dumps({"current_line": "1", "current_scene": "1d", "souvenirs": ["旧物"]}),
            encoding="utf-8",
        )
        summary = forest.save_summary("legacysummary")
        self.assertEqual(summary["current_scene"], "1d")
        self.assertEqual(summary["souvenirs"], 1)

        guide = json.loads(server._tool_get_guide({"game": "forest"}))["guide"]
        self.assertIn('action="ai_choose"', guide)
        self.assertIn("shared / human_path / ai_solo / merge", guide)
        self.assertIn("阿尢（1155896103）", guide)


if __name__ == "__main__":
    unittest.main(verbosity=2)
