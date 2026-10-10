"""Compatibility of server's MCP entry points after module extraction."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import server
from cedar_backend.errors import _McpError


class McpModuleCompatibilityTests(unittest.TestCase):
    def test_builders_use_current_server_definitions_without_mutating_them(self):
        definitions = copy.deepcopy(server._PLATFORM_TOOLS)
        definitions[0]["description"] = "patched catalog"
        original = copy.deepcopy(definitions)
        with patch.object(server, "_PLATFORM_TOOLS", definitions):
            for build in (
                server._build_root_platform_tools,
                server._build_kelivo_platform_tools,
            ):
                tools = build()
                self.assertEqual(tools[0]["description"], "patched catalog")
                tools[0]["inputSchema"]["properties"]["test"] = {"type": "string"}
                self.assertEqual(definitions, original)

    def test_tool_selection_uses_current_server_tables_classifier_and_explicit_mode(self):
        root = [{"name": "root"}, {"name": "hidden"}]
        kelivo = [{"name": "kelivo"}]
        with (
            patch.object(server, "_ROOT_PLATFORM_TOOLS", root),
            patch.object(server, "_KELIVO_PLATFORM_TOOLS", kelivo),
            patch.object(server, "_ROOT_TOOL_NAMES", {"root", "kelivo"}),
            patch.object(server, "_is_kelivo_user_agent") as classify,
        ):
            for compatible, default in ((False, root[0]), (True, kelivo[0])):
                classify.return_value = compatible
                for mode, expected in (("", default), ("legacy", kelivo[0]),
                                       ("standard", root[0]), ("unknown", default)):
                    with self.subTest(compatible=compatible, mode=mode):
                        classify.reset_mock()
                        selected = server._root_tools("test-client", schema_mode=mode)
                        self.assertEqual(selected, [expected])
                        self.assertIs(selected[0], expected)
                        if mode in ("legacy", "standard"):
                            classify.assert_not_called()
                        else:
                            classify.assert_called_once_with("test-client")

    def test_guide_constants_and_note_patches_remain_effective(self):
        with (
            patch.object(server, "SAVE_SLOT_GUIDE_NOTE", "<slot>"),
            patch.object(server, "_game_maintenance", return_value=None),
        ):
            self.assertEqual(server._guide_with_slot_note("body"), "body<slot>")
            for game, constant, suffix in (
                ("ai_life", "AI_LIFE_GUIDE", "<slot>"),
                ("workkk", "WORKKK_GUIDE", "<slot>"),
                ("garden_cat", "GARDEN_CAT_GUIDE", "<slot>"),
                ("camping_plaza", "CAMPING_PLAZA_GUIDE", "<slot>"),
                ("crucible_echoes", "CRUCIBLE_ECHOES_GUIDE", "<slot>"),
                ("detroit", "DETROIT_GUIDE", "<slot>"),
                ("duel", "DUEL_GUIDE", ""),
                ("tarot", "TAROT_GUIDE", ""),
            ):
                with self.subTest(game=game), patch.object(server, constant, "body"):
                    delivered = json.loads(server._tool_get_guide({"game": game}))
                    self.assertEqual(delivered, {"game": game, "guide": "body" + suffix})

    def test_guide_file_and_maintenance_dependencies_remain_patchable(self):
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(server, "GUIDE_DIR", Path(directory)),
            patch.object(server, "_guide_with_slot_note", side_effect=lambda text: text + "<note>"),
            patch.object(server, "CAMPING_PLAZA_GUIDE", "camping"),
            patch.object(server, "_game_maintenance", return_value={"label": "暂停", "message": "测试"}),
        ):
            path = Path(directory) / "mbti.md"
            for text in ("first", "updated"):
                path.write_text(text, encoding="utf-8")
                delivered = json.loads(server._tool_get_guide({"game": "mbti"}))
                self.assertEqual(delivered["guide"], text + "<note>")
            path.unlink()
            with self.assertRaises(_McpError) as caught:
                server._tool_get_guide({"game": "mbti"})
            self.assertEqual((caught.exception.code, caught.exception.message), (-32603, "mbti 说明文件不存在"))
            delivered = json.loads(server._tool_get_guide({"game": "camping_plaza"}))
            self.assertEqual(delivered["guide"], "【暂停】测试\n\ncamping<note>")

    def test_adapter_and_soup_guide_dependencies_remain_patchable(self):
        nowhere_guide = Mock(return_value="nowhere body")
        with (
            patch.object(server, "puzzle_box", SimpleNamespace(GUIDE="puzzle body")),
            patch.object(server, "nowhere_adapter", SimpleNamespace(guide=nowhere_guide)),
            patch.object(server, "AUTHORS", {"nowhere": {"name": "author"}}),
            patch.object(server, "VENDOR_CMD_GUIDES", {"test_vendor": "vendor body"}),
            patch.object(server, "_turtle_soup_guide", return_value={"game": "turtle_soup", "patched": True}),
            patch.object(server, "_guide_with_slot_note", side_effect=lambda text: text + "<note>"),
        ):
            for game, expected in (("puzzle_box", "puzzle body"), ("nowhere", "nowhere body<note>"), ("test_vendor", "vendor body<note>")):
                delivered = json.loads(server._tool_get_guide({"game": game}))
                self.assertEqual(delivered["guide"], expected)
                if game == "nowhere":
                    self.assertNotIn("attribution", delivered)
            nowhere_guide.assert_called_once_with()
            soup = json.loads(server._tool_get_guide({"game": "turtle_soup"}))
            self.assertTrue(soup["patched"])
            self.assertNotIn("platform_announcements", soup)

    def test_shared_error_type_and_existing_validation(self):
        self.assertIs(server._McpError, _McpError)
        for arguments, message in (
            ({}, "game 参数必填"),
            ({"game": []}, "game 参数必填"),
            ({"game": "missing"}, "未知游戏"),
        ):
            with self.subTest(arguments=arguments), self.assertRaises(server._McpError) as caught:
                server._tool_get_guide(arguments)
            self.assertEqual(caught.exception.code, -32602)
            self.assertEqual(caught.exception.message, message)
            self.assertEqual(caught.exception.details, {})
