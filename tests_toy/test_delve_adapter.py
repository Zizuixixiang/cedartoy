import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, delve
from vendor_cmd_adapter.base import VendorCmdError


class DelveAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.save_root = Path(self.temp_dir.name)
        self.base_save_patch = patch.object(base, "SAVE_ROOT", self.save_root)
        self.adapter_save_patch = patch.object(delve, "SAVE_ROOT", self.save_root)
        self.base_save_patch.start()
        self.adapter_save_patch.start()

    def tearDown(self):
        self.adapter_save_patch.stop()
        self.base_save_patch.stop()
        self.temp_dir.cleanup()

    def play(self, player_id, action, **kwargs):
        return delve.play({"player_id": player_id, "action": action, **kwargs})

    def save_dir(self, player_id):
        return self.save_root / "delve" / player_id

    def current_path(self, player_id):
        return self.save_dir(player_id) / delve.SAVE_NAME

    def legacy_path(self, player_id, name=None):
        return self.save_dir(player_id) / (name or delve.LEGACY_SAVE_NAMES[0])

    def state(self, player_id):
        return json.loads(self.current_path(player_id).read_text(encoding="utf-8"))

    def stage_legacy(self, player_id, *, name=None, **updates):
        self.play(player_id, "new")
        current_path = self.current_path(player_id)
        state = json.loads(current_path.read_text(encoding="utf-8"))
        state.update(updates)
        legacy_path = self.legacy_path(player_id, name)
        current_path.unlink()
        legacy_path.write_text(
            json.dumps(state, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return state, legacy_path

    def test_legacy_6_1_is_discovered_summarized_and_migrated_by_status(self):
        original, legacy_path = self.stage_legacy(
            "legacy61",
            coins=321,
            turn=17,
            trip=4,
            max_depth_m=88,
            collection_total_value=456,
            current_title="旧矿工",
        )

        self.assertFalse(self.current_path("legacy61").exists())
        self.assertEqual(
            delve.save_summary("legacy61"),
            {
                "turn": 17,
                "coins": 321,
                "trip": 4,
                "max_depth_m": 88,
                "collection_total_value": 456,
                "current_title": "旧矿工",
            },
        )
        with self.assertRaisesRegex(VendorCmdError, "第17回合.*金币321"):
            self.play("legacy61", "new")
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play("legacy61", "import", save_data=original)

        self.play("legacy61", "cmd", command="status")
        migrated = self.state("legacy61")
        self.assertEqual(migrated["coins"], 321)
        self.assertEqual(migrated["turn"], 17)
        self.assertEqual(migrated["migrated_from"], legacy_path.name)
        self.assertEqual(migrated["version"], "0.2.21.6.2-sidebranch-handoff")

    def test_non_status_command_also_migrates_legacy_to_6_2(self):
        self.stage_legacy("command", coins=91, turn=8)

        self.play("command", "cmd", command="handshake defaults")

        migrated = self.state("command")
        self.assertEqual((migrated["coins"], migrated["turn"]), (91, 8))
        self.assertTrue(migrated["handshake"]["completed"])
        self.assertEqual(migrated["migrated_from"], delve.LEGACY_SAVE_NAMES[0])

    def test_confirmed_new_removes_current_and_all_legacy_saves(self):
        self.stage_legacy("reset", coins=999, turn=42)
        self.play("reset", "cmd", command="status")
        old_legacy_path = self.legacy_path(
            "reset",
            delve.LEGACY_SAVE_NAMES[-1],
        )
        old_legacy_path.write_text('{"turn": 99}', encoding="utf-8")

        self.play("reset", "new", confirm=True)

        fresh = self.state("reset")
        self.assertEqual((fresh["turn"], fresh["coins"]), (0, 0))
        self.assertTrue(self.current_path("reset").is_file())
        for legacy_name in delve.LEGACY_SAVE_NAMES:
            self.assertFalse(self.legacy_path("reset", legacy_name).exists())

    def test_export_migrates_legacy_and_raw_import_targets_6_2(self):
        old_state, _legacy_path = self.stage_legacy(
            "exporter",
            version="0.2.21.6.1",
            coins=777,
            turn=23,
        )

        exported = json.loads(self.play("exporter", "export")["text"])

        self.assertEqual((exported["coins"], exported["turn"]), (777, 23))
        self.assertEqual(exported["version"], "0.2.21.6.2-sidebranch-handoff")
        self.assertTrue(self.current_path("exporter").is_file())

        self.play("imported", "import", save_data=exported)
        self.assertTrue(self.current_path("imported").is_file())
        self.assertFalse(self.legacy_path("imported").exists())
        imported = self.state("imported")
        self.assertEqual((imported["coins"], imported["turn"]), (777, 23))

        old_state["coins"] = 654
        old_state["version"] = "0.2.21.6.1"
        self.play("oldraw", "import", save_data=old_state)
        self.assertTrue(self.current_path("oldraw").is_file())
        self.assertEqual(self.state("oldraw")["coins"], 654)
        status = json.loads(self.play("oldraw", "cmd", command="status")["text"])
        self.assertEqual(
            status["state"]["version"],
            "0.2.21.6.2-sidebranch-handoff",
        )
        self.play("oldraw", "cmd", command="handshake defaults")
        self.assertEqual(
            self.state("oldraw")["version"],
            "0.2.21.6.2-sidebranch-handoff",
        )


if __name__ == "__main__":
    unittest.main()
