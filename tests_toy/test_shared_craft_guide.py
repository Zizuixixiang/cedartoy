"""Guide contract and real root response; never use production craft stores."""
import ast
from contextlib import ExitStack
import json
from pathlib import Path
import re
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
UPSTREAM = ROOT / "vendor/shared-craft-room/src/shared_craft_room/server.py"
GUIDE = ROOT / "shared_craft_adapter/guide.txt"


def original_tools():
    # Evaluate only schema constants, avoiding upstream module-level store creation.
    tree = ast.parse(UPSTREAM.read_text())
    names = {"POINT", "BEAD", "STROKE", "TOOLS"}
    nodes = [node for node in tree.body if isinstance(node, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id in names for t in node.targets)]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(UPSTREAM), "exec"), namespace)
    return namespace["TOOLS"]


def guide_signatures(text):
    signatures = {}
    for names, params in re.findall(r"([a-z_]+(?:/[a-z_]+)*)\(([^()]*)\)", text):
        if names == "play":
            continue
        fields = {}
        for param in params.split(",") if params else []:
            fields[param.rstrip("?").split("=")[0]] = not param.endswith("?")
        for name in names.split("/"):
            signatures[name] = fields
    # Compact no-argument actions and the explicitly restricted alias.
    for tool in original_tools():
        if not tool["inputSchema"]["properties"] and re.search(r"\b" + tool["name"] + r"\b", text):
            signatures[tool["name"]] = {}
    if "list_gallery" in text:
        signatures["list_gallery"] = {}
    if "brush_stroke 同参但无 mode" in text:
        signatures["brush_stroke"] = {k: v for k, v in signatures["draw_path"].items() if k != "mode"}
    return signatures


class SharedCraftGuideTests(unittest.TestCase):
    def test_every_upstream_action_field_and_enum(self):
        text = GUIDE.read_text()
        signatures = guide_signatures(text)
        for tool in original_tools():
            name, schema = tool["name"], tool["inputSchema"]
            with self.subTest(action=name):
                expected = set(schema["properties"])
                if name == "new_canvas":
                    expected.add("confirm")  # Adapter-only overwrite guard.
                self.assertEqual(set(signatures[name]), expected)
                self.assertEqual({k for k, required in signatures[name].items() if required},
                                 set(schema.get("required", [])))
                for field in schema["properties"].values():
                    for value in field.get("enum", []):
                        self.assertIn(str(value), text)
        self.assertNotIn("mode", signatures["brush_stroke"])
        self.assertEqual(set(signatures),
                         {t["name"] for t in original_tools()} | {"list_gallery", "save_gallery", "share_work"})

    def test_adapter_fields_constraints_and_attribution(self):
        text = GUIDE.read_text()
        signatures = guide_signatures(text)
        self.assertEqual(signatures["save_gallery"],
                         dict(kind=True, title=False, source_id=False, piece_columns=False, piece_rows=False))
        self.assertEqual(signatures["share_work"], dict(item_id=True, note=False))
        for constraint in ("1–14", "1–100", "0.05–1", "1–12", "180–1200", "1–5",
                           "1–512", "1–8", "250–1500", "2.8–6.2", "40字", "60字",
                           "500字", "size-1", "正整数", "heat_ready_at/cool_ready_at",
                           "confirm=true", "默认不分享", "主动调用即分享", "不公开"):
            self.assertIn(constraint, text)
        for attribution in ("嘞噫黎", "493242614", "https://github.com/SuLee3927/shared-craft-room", "MIT"):
            self.assertIn(attribution, text)
        self.assertLess(len(text), 1729)  # Before this correction; characters, not tokens.

    def test_actual_root_get_guide_response(self):
        import server
        with ExitStack() as stack:
            # Reminders are unrelated and may read live account/announcement stores.
            for name, value in (("_authenticated_ai_player_id", None),
                                ("_duel_unread_request_reminder", ""),
                                ("_mcp_forced_announcement", ""), ("_feedback_unread_reminder", "")):
                stack.enter_context(patch.object(server, name, return_value=value))
            response = server._handle_root_mcp({
                "jsonrpc": "2.0", "id": 1, "method": "tools/call",
                "params": {"name": "get_guide", "arguments": {"game": "shared_craft"}},
            })
        self.assertFalse(response["result"]["isError"], response)
        content = response["result"]["content"]
        self.assertEqual(len(content), 1)
        self.assertEqual(json.loads(content[0]["text"]),
                         {"game": "shared_craft", "guide": GUIDE.read_text()})


if __name__ == "__main__":
    unittest.main()
