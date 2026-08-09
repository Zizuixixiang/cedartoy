import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import server
from vendor_cmd_adapter import base, forest
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
        self.assertEqual(state["daily"]["count"], 1)
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
        self.assertEqual(len(state["souvenirs"]), 1)
        status = self.play("515", "status")["text"]
        self.assertIn(state["souvenirs"][0], status)
        self.assertIn(f"/ {state['current_scene']}", status)

    def test_daily_reminder_counter_keeps_advancing_to_firm_threshold(self):
        self.play("daily", "new")
        outputs = [self.play("daily", "start", line=1)["text"] for _ in range(6)]
        self.assertEqual(self.state("daily")["daily"]["count"], 6)
        self.assertEqual(self.state("daily")["total_lines_started"], 3)
        self.assertIn(GAME_DATA["anti_addiction"]["gentle"]["text"], outputs[3])
        self.assertIn(GAME_DATA["anti_addiction"]["firm"]["text"], outputs[5])

        old_state = self.state("daily")
        old_state["daily"] = {"date": "2000-01-01", "count": 99}
        save_path = self.save_root / "forest" / "daily" / forest.SAVE_NAME
        save_path.write_text(json.dumps(old_state, ensure_ascii=False), encoding="utf-8")
        self.assertIn("今日走线调用：0 次", self.play("daily", "status")["text"])
        self.assertEqual(forest.save_summary("daily")["daily_lines"], 0)

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
        self.assertEqual(self.state("parallel")["daily"]["count"], 8)
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
