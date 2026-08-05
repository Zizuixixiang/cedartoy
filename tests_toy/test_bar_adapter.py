import ast
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from vendor_cmd_adapter import bar, base
from vendor_cmd_adapter.base import VendorCmdError


class BarAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory(prefix="bar-adapter-")
        self.save_root = Path(self.temp_dir.name) / "vendor_saves"
        self.save_root.mkdir()
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(bar, "SAVE_ROOT", self.save_root),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
        ]
        for active in self.patches:
            active.start()

    def tearDown(self):
        for active in reversed(self.patches):
            active.stop()
        self.temp_dir.cleanup()

    def play(self, player_id, action, **kwargs):
        return bar.play({"player_id": player_id, "action": action, **kwargs})

    def test_unselected_blocks_runtime_and_select_only_writes_metadata(self):
        player_id = "guest:choose"
        result = self.play(player_id, "version")
        self.assertEqual(result["version"], "unselected")
        self.assertIn("未加载或运行任何游戏模块", result["text"])
        self.assertIn("西兰花（小红书号 1033358978）", result["text"])
        self.assertFalse((self.save_root / "bar" / player_id).exists())

        for action in ("rules", "summary", "cmd", "call"):
            kwargs = {}
            if action == "cmd":
                kwargs["command"] = "status"
            if action == "call":
                kwargs.update(function="summary", arguments={})
            with self.subTest(action=action), self.assertRaisesRegex(
                VendorCmdError, "尚未选择版本"
            ):
                self.play(player_id, action, **kwargs)

        selected = self.play(player_id, "select", version="完整版")
        self.assertEqual(selected["version"], "full")
        save_dir = self.save_root / "bar" / player_id
        self.assertEqual(
            json.loads((save_dir / "selection.json").read_text(encoding="utf-8")),
            {"schema_version": 1, "version": "full"},
        )
        self.assertFalse((save_dir / "bar_save.json").exists())
        self.assertFalse((save_dir / "bar_lite_save.json").exists())

    def test_full_new_cmd_status_and_confirm(self):
        player_id = "full1"
        opening = self.play(player_id, "new", version="normal", seed=123)
        self.assertEqual(opening["version"], "full")
        self.assertIn("种子123", opening["text"])
        help_text = self.play(player_id, "cmd", command="help")["text"]
        self.assertIn("《空杯俱乐部》内部指令", help_text)
        status = self.play(player_id, "status")["text"]
        self.assertIn("资金460点", status)
        self.assertNotIn("📊", status)
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play(player_id, "new", version="full", seed=456)
        replaced = self.play(
            player_id, "new", version="full", seed=456, confirm=True
        )
        self.assertIn("种子456", replaced["text"])

    def test_lite_rules_and_public_calls_with_parameters(self):
        player_id = "lite1"
        created = self.play(
            player_id,
            "new",
            version="light",
            seed=7,
            cash=700,
            owner_tolerance=60,
            owner_absorption=0.9,
        )
        self.assertEqual(created["version"], "lite")
        self.assertEqual(json.loads(created["text"])["cash"], 700)

        rules = self.play(player_id, "rules")["text"]
        self.assertIn("# 空杯俱乐部：生成式轻量版规则书", rules)
        self.assertIn("# 生成式轻量版：人物卡示例", rules)
        self.assertIn("【运行入口】", rules)

        drawn = json.loads(
            self.play(
                player_id,
                "call",
                function="draw_creation_direction",
                arguments={"purpose": "drink", "forced_direction": "history"},
            )["text"]
        )
        self.assertEqual(drawn["direction_id"], "history")
        product = json.loads(
            self.play(
                player_id,
                "call",
                function="define_product",
                arguments={
                    "product_id": "gin",
                    "name": "店藏金酒",
                    "kind": "gin",
                    "bottle_ml": 700,
                    "abv": 40,
                    "bottle_cost": 150,
                },
            )["text"]
        )
        self.assertEqual(product["id"], "gin")
        purchased = json.loads(
            self.play(
                player_id,
                "call",
                function="purchase",
                arguments={"product_id": "gin", "bottles": 2},
            )["text"]
        )
        self.assertEqual(purchased["stock_ml"], 1400)
        summary = json.loads(
            self.play(
                player_id, "call", function="summary", arguments={}
            )["text"]
        )
        self.assertEqual((summary["cash"], summary["products"]), (400, 1))
        self.assertEqual(json.loads(self.play(player_id, "summary")["text"]), summary)
        with self.assertRaisesRegex(VendorCmdError, "没有 cmd 接口"):
            self.play(player_id, "cmd", command="status")

    def test_lite_allowlist_matches_all_upstream_public_top_level_functions(self):
        source_path = Path("vendor/ai-bar-game/bar_game_lite.py")
        tree = ast.parse(source_path.read_text(encoding="utf-8"))
        public_defs = {
            node.name
            for node in tree.body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and not node.name.startswith("_")
        }
        self.assertEqual(set(bar.LITE_PUBLIC_FUNCTIONS), public_defs)

    def test_versions_coexist_switch_without_cross_talk_and_reset_one_only(self):
        player_id = "both1"
        self.play(player_id, "new", version="full", seed=101)
        full_path = self.save_root / "bar" / player_id / "bar_save.json"
        full_before = full_path.read_bytes()
        self.play(player_id, "new", version="lite", seed=202, cash=888)
        lite_path = self.save_root / "bar" / player_id / "bar_lite_save.json"
        lite_before = lite_path.read_bytes()

        switched = self.play(player_id, "select", version="full")
        self.assertEqual(switched["version"], "full")
        self.assertIn("资金460点", self.play(player_id, "summary")["text"])
        self.assertEqual(lite_path.read_bytes(), lite_before)

        self.play(player_id, "select", version="lite")
        self.assertEqual(json.loads(self.play(player_id, "summary")["text"])["cash"], 888)
        self.assertEqual(full_path.read_bytes(), full_before)

        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play(player_id, "new", version="lite", cash=999)
        self.play(player_id, "new", version="lite", cash=999, confirm=True)
        self.assertEqual(full_path.read_bytes(), full_before)
        self.assertNotEqual(lite_path.read_bytes(), lite_before)

    def test_export_import_two_versions_lossless_and_selection_inference(self):
        source = "archive1"
        self.play(source, "new", version="full", seed=11)
        self.play(source, "new", version="lite", seed=22, cash=654)
        archive = json.loads(self.play(source, "export")["text"])
        self.assertEqual(
            set(archive),
            {"selection.json", "bar_save.json", "bar_lite_save.json"},
        )

        target = "archive2"
        imported = self.play(target, "import", save_data=archive)
        self.assertEqual(imported["version"], "lite")
        self.assertEqual(json.loads(self.play(target, "export")["text"]), archive)
        with self.assertRaisesRegex(VendorCmdError, "confirm=true"):
            self.play(target, "import", save_data=archive)
        self.play(target, "import", save_data=archive, confirm=True)

        one_version = {"bar_save.json": archive["bar_save.json"]}
        inferred = self.play("archive3", "import", save_data=one_version)
        self.assertEqual(inferred["version"], "full")

        no_selection = dict(archive)
        no_selection.pop("selection.json")
        ambiguous = self.play("archive4", "import", save_data=no_selection)
        self.assertEqual(ambiguous["version"], "unselected")
        self.assertIn("请先 action=\"select\"", ambiguous["text"])

    def test_import_and_lite_call_reject_unsafe_shapes(self):
        player_id = "reject1"
        self.play(player_id, "select", version="lite")
        for function in ("_load", "__import__", "missing"):
            with self.subTest(function=function), self.assertRaisesRegex(
                VendorCmdError, "未开放"
            ):
                self.play(
                    player_id,
                    "call",
                    function=function,
                    arguments={},
                )
        with self.assertRaisesRegex(VendorCmdError, "arguments 必须是 JSON 对象"):
            self.play(
                player_id,
                "call",
                function="summary",
                arguments=[],
            )
        with self.assertRaises(VendorCmdError):
            self.play(
                player_id,
                "call",
                function="define_product",
                arguments={"unexpected": 1},
            )

        with self.assertRaisesRegex(VendorCmdError, "未知存档文件"):
            self.play("badimport1", "import", save_data={"../../x": {}})
        with self.assertRaisesRegex(VendorCmdError, "必须是 JSON 对象"):
            self.play(
                "badimport2",
                "import",
                save_data={"bar_save.json": []},
            )
        with self.assertRaisesRegex(VendorCmdError, "canonical"):
            self.play(
                "badimport3",
                "import",
                save_data={
                    "selection.json": {"schema_version": 1, "version": "完整版"}
                },
            )
        with self.assertRaisesRegex(VendorCmdError, "合法 JSON"):
            self.play("badimport4", "import", save_data="not json")

    def test_slots_and_save_summary_are_isolated(self):
        slot1 = "515"
        slot2 = "515:2"
        self.play(slot1, "new", version="full", seed=1)
        self.play(slot2, "new", version="lite", seed=2, cash=321)
        first = bar.save_summary(slot1)
        second = bar.save_summary(slot2)
        self.assertEqual((first["version"], first["cash"]), ("full", 460))
        self.assertEqual((second["version"], second["cash"]), ("lite", 321))
        self.assertFalse(first["lite_save"])
        self.assertFalse(second["full_save"])

    def test_server_registration_list_guide_route_and_my_saves(self):
        self.assertIn("bar", server.IDENTITY_GAMES)
        self.assertIn("bar", server.PERSISTENT_SAVE_GAMES)
        self.assertIn("bar", server.VENDOR_GAMES)
        self.assertIn("bar", server.DIRECTORY_VENDOR_GAMES)
        self.assertIn("bar·空杯俱乐部", server._tool_list_games())

        before = {name for name in ("bar_game", "bar_game_lite") if name in sys.modules}
        guide = json.loads(server._tool_get_guide({"game": "bar"}))["guide"]
        after = {name for name in ("bar_game", "bar_game_lite") if name in sys.modules}
        self.assertEqual(before, after)
        for marker in (
            "必须先选版本",
            "version",
            "select",
            "conversation_turn",
            "restore_archive",
            "西兰花（小红书号 1033358978）",
            "[存档槽]",
        ):
            self.assertIn(marker, guide)

        expected = {"game": "bar", "player_id": "guest:r", "version": "unselected", "text": "ok"}
        with patch.object(server.bar_adapter, "play", return_value=expected) as dispatch:
            self.assertEqual(
                server._play_vendor_cmd(
                    "bar",
                    {
                        "game": "bar",
                        "action": "version",
                        "params": {"player_id": "guest:r"},
                    },
                ),
                expected,
            )
            self.assertEqual(dispatch.call_args.args[0]["player_id"], "guest:r")

        routed = {"game": "bar", "player_id": "909:3", "version": "unselected", "text": "ok"}
        common = (
            patch.object(server, "_current_account", return_value={"id": 909, "username": "barowner", "is_ai": True}),
            patch.object(server, "_auto_migrate_legacy_account_saves"),
            patch.object(server, "_anti_addiction_context", return_value=None),
            patch.object(server, "_anti_addiction_record_success", return_value=""),
            patch.object(server, "_play_announcements", return_value=""),
            patch.object(server, "_stamp_save_owner"),
            patch.object(server, "_play_vendor_cmd", return_value=routed),
        )
        started = [active.start() for active in common]
        try:
            server._tool_play_inner(
                {
                    "game": "bar",
                    "action": "version",
                    "params": {"player_id": "victim", "slot": 3},
                },
                path_token="trusted-token",
            )
            routed_arguments = started[-1].call_args.args[1]
            self.assertEqual(routed_arguments["params"]["player_id"], "909:3")
            self.assertNotIn("slot", routed_arguments["params"])
        finally:
            for active in reversed(common):
                active.stop()

        self.play("909:2", "new", version="lite", seed=9, cash=909)
        empty_db = Path(self.temp_dir.name) / "empty.db"

        def connect_empty():
            conn = sqlite3.connect(empty_db)
            conn.row_factory = sqlite3.Row
            return conn

        with (
            patch.object(server, "SESSIONS_DB_PATH", Path(self.temp_dir.name) / "missing.db"),
            patch.object(server, "_db_connect", side_effect=connect_empty),
            patch.object(server, "_account_username_aliases", return_value=[]),
        ):
            saves = server._account_saves_for_user(
                {"id": 909, "username": "barowner", "is_ai": True},
                migrate_legacy=False,
            )["saves"]
        self.assertEqual(saves["bar"]["slots"][0]["slot"], 2)
        self.assertEqual(saves["bar"]["slots"][0]["cash"], 909)

    def test_directory_claim_migration_and_delete_cover_bar(self):
        source = "guest:claimbar"
        self.play(source, "new", version="full", seed=77)
        with patch.object(
            server, "SESSIONS_DB_PATH", Path(self.temp_dir.name) / "missing.db"
        ):
            migrated = server._migrate_player_saves(source, 919, slot=3)
        self.assertIn("vendor_saves/bar", migrated["migrated"])
        target_dir = self.save_root / "bar" / "919:3"
        self.assertTrue((target_dir / "bar_save.json").is_file())
        self.assertFalse((self.save_root / "bar" / source).exists())
        deleted = server._delete_vendor_save_dir("bar", "919:3")
        self.assertEqual(deleted["target"], "vendor_saves/bar/919:3")
        self.assertFalse(target_dir.exists())


if __name__ == "__main__":
    unittest.main()
