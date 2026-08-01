import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, white_room
from vendor_cmd_adapter.base import VendorCmdError


class WhiteRoomAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.save_root = Path(self.temp_dir.name)
        self.base_save_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.adapter_save_patch = patch.object(white_room, "SAVE_ROOT", self.save_root)
        self.base_save_patch.start()
        self.adapter_save_patch.start()

    def tearDown(self):
        self.adapter_save_patch.stop()
        self.base_save_patch.stop()
        self.temp_dir.cleanup()

    def test_commands_confirmation_export_and_slot_isolation(self):
        slot1 = "player1"
        slot2 = "player1:2"

        opening = white_room.play({"action": "new", "player_id": slot1})["text"]
        self.assertIn("你在这里", opening)
        white_room.play({"action": "cmd", "player_id": slot1, "command": "光"})
        status = white_room.play({"action": "status", "player_id": slot1})["text"]
        self.assertIn("累计输入: 1 次", status)
        self.assertIn(
            "存档已保存",
            white_room.play({"action": "save", "player_id": slot1})["text"],
        )
        white_room.play({"action": "save_backup", "player_id": slot1})

        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            white_room.play({"action": "new", "player_id": slot1})

        restart = white_room.play({"action": "restart", "player_id": slot1})["text"]
        self.assertIn("restart confirm", restart)
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            white_room.play({"action": "restart_confirm", "player_id": slot1})

        white_room.play(
            {
                "action": "restart_confirm",
                "player_id": slot1,
                "confirm": True,
            }
        )
        white_room.play({"action": "cmd", "player_id": slot1, "command": "光"})

        opening2 = white_room.play(
            {"action": "new", "player_id": slot2, "mode": "echo"}
        )["text"]
        self.assertIn("长篇回响模式", opening2)
        white_room.play({"action": "cmd", "player_id": slot2, "command": "雨"})

        path1 = self.save_root / "white_room" / slot1 / white_room.SAVE_NAME
        path2 = self.save_root / "white_room" / slot2 / white_room.SAVE_NAME
        state1 = json.loads(path1.read_text(encoding="utf-8"))
        state2 = json.loads(path2.read_text(encoding="utf-8"))
        self.assertEqual((state1["mode"], state1["total_inputs"]), ("standard", 1))
        self.assertEqual((state2["mode"], state2["total_inputs"]), ("echo", 1))

        archive = json.loads(
            white_room.play({"action": "export", "player_id": slot1})["text"]
        )
        self.assertIn(white_room.SAVE_NAME, archive)
        self.assertIn(white_room.BACKUP_NAME, archive)
        white_room.play(
            {
                "action": "import",
                "player_id": "player1:3",
                "save_data": archive,
            }
        )
        imported_status = white_room.play(
            {"action": "status", "player_id": "player1:3"}
        )["text"]
        self.assertIn("累计输入: 1 次", imported_status)

    def test_all_meta_actions_are_explicit_and_typos_still_fail(self):
        player_id = "meta1"
        white_room.play({"action": "new", "player_id": player_id})

        expected_text = {
            "status": "【当前状态】",
            "help": "可用命令",
            "hint": "试着描述你能感知到的东西",
            "recap": "【回声回顾】",
            "privacy": "【隐私说明】",
            "endings": "【结局收藏】",
            "report": "匿名试玩报告已生成",
            "report_reset": "试玩统计已清空",
            "save": "存档已保存",
            "save_backup": "存档备份已创建",
            "quit": "打字机安静了。下次再见。",
        }
        for action, marker in expected_text.items():
            with self.subTest(action=action):
                result = white_room.play(
                    {"action": action, "player_id": player_id}
                )["text"]
                self.assertIn(marker, result)

        status = white_room.play({"action": "status", "player_id": player_id})["text"]
        self.assertIn("累计输入: 0 次", status)

        with self.assertRaisesRegex(VendorCmdError, "未知 white_room action"):
            white_room.play({"action": "statuz", "player_id": player_id})


if __name__ == "__main__":
    unittest.main()
