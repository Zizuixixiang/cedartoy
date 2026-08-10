import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server
from vendor_cmd_adapter import base, forest, forest_runtime
from vendor_cmd_adapter.base import VendorCmdError


ROOT = Path(__file__).resolve().parent.parent
GAME_DATA = json.loads(
    (ROOT / "vendor" / "mo-yao-play-games" / "forest_game_data.json").read_text(
        encoding="utf-8"
    )
)


class ForestAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.save_root = Path(self.temp_dir.name)
        self.base_save_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.adapter_save_patch = patch.object(forest, "SAVE_ROOT", self.save_root)
        self.base_save_patch.start()
        self.adapter_save_patch.start()

    def tearDown(self):
        self.adapter_save_patch.stop()
        self.base_save_patch.stop()
        self.temp_dir.cleanup()

    def play(self, player_id, action, **kwargs):
        return forest.play({"player_id": player_id, "action": action, **kwargs})

    def state(self, player_id):
        return json.loads(
            (self.save_root / "forest" / player_id / forest.SAVE_NAME).read_text(
                encoding="utf-8"
            )
        )

    def finish_line(self, player_id, line_id=1):
        self.play(player_id, "start", line=line_id)
        line = GAME_DATA["lines"][str(line_id)]
        for _ in range(20):
            state = self.state(player_id)
            scene_id = state["current_scene"]
            scene = (
                line["opening"]
                if scene_id == "opening"
                else line["scenes"][scene_id]
            )
            if scene.get("type") == "ending":
                return
            self.play(player_id, "choose", option=next(iter(scene["options"])))
        self.fail(f"line {line_id} did not reach an ending")

    def test_new_is_immediately_saved_and_requires_confirmation_to_overwrite(self):
        text = self.play("fresh", "new")["text"]
        self.assertIn("新的森林存档已建立", text)
        self.assertEqual(self.state("fresh")["total_lines_started"], 0)
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play("fresh", "new")
        self.play("fresh", "reset", confirm=True)
        self.assertEqual(self.state("fresh")["current_line"], None)
        self.assertFalse(
            list((self.save_root / "forest" / "fresh").glob(f"{forest.SAVE_NAME}.corrupt-*"))
        )

    def test_position_daily_count_souvenir_and_slot_isolation_persist(self):
        self.play("515", "new")
        self.play("515:2", "new")
        opening = self.play("515", "start", line=1)["text"]
        self.assertIn("当前场景ID", opening)
        state = self.state("515")
        self.assertEqual((state["current_line"], state["current_scene"]), ("1", "opening"))
        self.assertEqual(state["daily"]["count"], 0)
        self.assertEqual(state["daily"]["semantic"], forest_runtime.DAILY_SEMANTIC)
        self.assertEqual(self.state("515:2")["daily"]["count"], 0)

        line = GAME_DATA["lines"]["1"]
        for _ in range(20):
            state = self.state("515")
            scene_id = state["current_scene"]
            scene = line["opening"] if scene_id == "opening" else line["scenes"][scene_id]
            if scene.get("type") == "ending":
                break
            option = next(iter(scene["options"]))
            self.play("515", "choose", option=option)
        else:
            self.fail("line 1 did not reach an ending")

        state = self.state("515")
        self.assertEqual(state["daily"]["count"], 1)
        self.assertEqual(len(state["souvenirs"]), 1)
        with self.assertRaisesRegex(VendorCmdError, "已经是结局"):
            self.play("515", "choose", option="A")
        self.assertEqual(self.state("515")["daily"]["count"], 1)
        status = self.play("515", "status")["text"]
        self.assertIn(state["souvenirs"][0], status)
        self.assertIn(f"/ {state['current_scene']}", status)

    def test_daily_count_increments_only_at_endings_and_gentle_blocks_starts(self):
        self.play("daily", "new")
        for _ in range(3):
            self.finish_line("daily")
        save_path = self.save_root / "forest" / "daily" / forest.SAVE_NAME
        saved_bytes = save_path.read_bytes()
        saved_stat = save_path.stat()
        saved_file_identity = (
            saved_stat.st_ino,
            saved_stat.st_size,
            saved_stat.st_mtime_ns,
            saved_stat.st_ctime_ns,
        )

        outputs = [self.play("daily", "start", line=1)["text"] for _ in range(3)]
        self.assertEqual(self.state("daily")["daily"]["count"], 3)
        self.assertEqual(self.state("daily")["total_lines_started"], 3)
        self.assertEqual(save_path.read_bytes(), saved_bytes)
        current_stat = save_path.stat()
        self.assertEqual(
            (
                current_stat.st_ino,
                current_stat.st_size,
                current_stat.st_mtime_ns,
                current_stat.st_ctime_ns,
            ),
            saved_file_identity,
        )
        for output in outputs:
            self.assertIn(GAME_DATA["anti_addiction"]["gentle"]["text"], output)
            self.assertNotIn(GAME_DATA["anti_addiction"]["firm"]["text"], output)

    def test_legacy_start_count_is_zero_until_next_mutation_persists_migration(self):
        self.play("legacy", "new")
        self.play("legacy", "start", line=1)
        save_path = self.save_root / "forest" / "legacy" / forest.SAVE_NAME
        legacy = self.state("legacy")
        legacy["daily"].pop("semantic")
        legacy["daily"]["count"] = 99
        legacy["souvenirs"] = ["旧纪念品"]
        legacy["completed_endings"] = ["1:1d"]
        legacy["observations"] = {"1:1d": "旧观察还在。"}
        legacy["latest_observation_key"] = "1:1d"
        legacy["participants"] = {"player": "旧旅人", "ai": "旧同行者"}
        legacy["revision"] = 17
        save_path.write_text(json.dumps(legacy, ensure_ascii=False), encoding="utf-8")
        legacy_bytes = save_path.read_bytes()

        status = self.play("legacy", "status")["text"]
        self.assertIn("今日完成角色线：0 次", status)
        self.assertEqual(forest.save_summary("legacy")["daily_lines"], 0)
        self.assertEqual(save_path.read_bytes(), legacy_bytes)

        self.play("legacy", "start", line=2)
        migrated = self.state("legacy")
        self.assertEqual(migrated["daily"]["count"], 0)
        self.assertEqual(migrated["daily"]["semantic"], forest_runtime.DAILY_SEMANTIC)
        self.assertEqual(migrated["souvenirs"], legacy["souvenirs"])
        self.assertEqual(migrated["completed_endings"], legacy["completed_endings"])
        self.assertEqual(migrated["observations"], legacy["observations"])
        self.assertEqual(migrated["participants"], legacy["participants"])
        self.assertEqual(migrated["latest_observation_key"], legacy["latest_observation_key"])
        self.assertEqual(migrated["revision"], legacy["revision"] + 1)
        self.assertEqual(migrated["total_choices"], legacy["total_choices"])

    def test_web_actions_count_only_the_ending_transition(self):
        snapshot = forest.web_state("webdaily", player_name="旅人", ai_name="同行者")
        snapshot = forest.web_action(
            "webdaily",
            "start",
            expected_revision=snapshot["revision"],
            player_name="旅人",
            ai_name="同行者",
            line=1,
        )
        self.assertEqual(snapshot["daily"]["count"], 0)
        while not snapshot["current"]["is_ending"]:
            snapshot = forest.web_action(
                "webdaily",
                "choose",
                expected_revision=snapshot["revision"],
                expected_scene=snapshot["current"]["scene_id"],
                player_name="旅人",
                ai_name="同行者",
                option=snapshot["current"]["options"][0]["key"],
            )
        self.assertEqual(snapshot["daily"]["count"], 1)
        self.assertEqual(self.state("webdaily")["daily"]["count"], 1)

    def test_free_play_matches_upstream_and_keeps_current_scene(self):
        self.play("free", "new")
        self.play("free", "start", line=3)
        before = self.state("free")
        text = self.play("free", "choose", option="D")["text"]
        expected = GAME_DATA["free_play"]["text"].replace("__return_scene__", "opening")
        self.assertEqual(text, expected)
        self.assertEqual(self.state("free"), before)

    def test_concurrent_commands_are_serialized_by_player_lock(self):
        self.play("parallel", "new")
        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(
                pool.map(lambda _index: self.play("parallel", "start", line=1), range(8))
            )
        self.assertEqual(len(outputs), 8)
        self.assertEqual(self.state("parallel")["daily"]["count"], 0)
        self.assertEqual(self.state("parallel")["total_lines_started"], 8)
        self.assertFalse(
            list(
                (self.save_root / "forest" / "parallel").glob(
                    f".{forest.SAVE_NAME}.tmp-*"
                )
            )
        )

    def test_corrupt_save_is_backed_up_warned_and_rebuilt(self):
        self.play("broken", "new")
        save_path = self.save_root / "forest" / "broken" / forest.SAVE_NAME
        save_path.write_text("{broken", encoding="utf-8")
        text = self.play("broken", "status")["text"]
        self.assertTrue(text.startswith("⚠️ 存档告警"))
        backups = list(save_path.parent.glob(f"{forest.SAVE_NAME}.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")
        json.loads(save_path.read_text(encoding="utf-8"))
        self.assertFalse(list(save_path.parent.glob(f".{forest.SAVE_NAME}.tmp-*")))

        save_path.write_text("{broken-again", encoding="utf-8")
        with self.assertRaisesRegex(VendorCmdError, "⚠️ 存档告警"):
            self.play("broken", "choose", option="A")

    def test_export_import_and_server_registries(self):
        self.play("exporter", "new")
        self.play("exporter", "start", line=2)
        archive = json.loads(self.play("exporter", "export")["text"])
        self.play("importer", "import", save_data=archive)
        self.assertEqual(self.state("importer")["current_line"], "2")

        self.assertIn("forest", server.IDENTITY_GAMES)
        self.assertIn("forest", server.PERSISTENT_SAVE_GAMES)
        self.assertIn("forest", server.VENDOR_GAMES)
        self.assertIn("forest·格林童话境遇", server._tool_list_games())
        play_schema = next(tool for tool in server._PLATFORM_TOOLS if tool["name"] == "play")
        self.assertIn("forest", play_schema["inputSchema"]["properties"]["game"]["enum"])
        guide = json.loads(server._tool_get_guide({"game": "forest"}))["guide"]
        self.assertIn("阿尢（1155896103）", guide)
        self.assertNotIn("memory/", guide)

        expected = {"game": "forest", "player_id": "guest:r", "text": "ok"}
        with patch.object(server.forest_adapter, "play", return_value=expected) as dispatch:
            self.assertEqual(
                server._play_vendor_cmd(
                    "forest",
                    {
                        "game": "forest",
                        "action": "status",
                        "params": {"player_id": "guest:r"},
                    },
                ),
                expected,
            )
            self.assertEqual(dispatch.call_args.args[0]["player_id"], "guest:r")


if __name__ == "__main__":
    unittest.main()
