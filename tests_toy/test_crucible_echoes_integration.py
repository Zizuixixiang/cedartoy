from __future__ import annotations

import http.client
import json
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from vendor_cmd_adapter import base
from vendor_cmd_adapter import crucible_echoes as adapter


ROOT = Path(__file__).resolve().parents[1]


class CrucibleEchoesAdapterTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="crucible-echoes-")
        self.save_root = Path(self.tempdir.name) / "vendor_saves"
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(adapter, "SAVE_ROOT", self.save_root),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tempdir.cleanup()

    def _play(self, player_id: str, action: str, **params):
        return adapter.play({"player_id": player_id, "action": action, **params})

    def _state_path(self, player_id: str) -> Path:
        return self.save_root / "crucible_echoes" / player_id / "state.json"

    def _edit_state(self, player_id: str, mutate) -> dict:
        path = self._state_path(player_id)
        state = json.loads(path.read_text(encoding="utf-8"))
        mutate(state)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return state

    def test_full_action_chain_persists_and_stays_compact(self):
        player = "guest:cruciblechain"
        opened = self._play(player, "new", seed=42, difficulty=1)
        self.assertEqual(opened["state"]["spin"], 0)
        self.assertNotIn("rng_state", opened["state"])
        self.assertNotIn("endless_mode", opened["state"])
        self.assertLess(len(json.dumps(opened, ensure_ascii=False).encode("utf-8")), 5_000)

        spun = self._play(player, "spin")
        self.assertEqual(spun["state"]["spin"], 1)
        self.assertEqual(spun["decision"]["kind"], "ingredient")
        self.assertTrue(any(action.get("action") == "choose" for action in spun["actions"]))
        self.assertNotIn("ingredients", spun)
        self.assertLess(len(json.dumps(spun, ensure_ascii=False).encode("utf-8")), 8_000)

        self._edit_state(player, lambda state: state["tokens"].update({"roll": 1}))
        rerolled = self._play(player, "reroll")
        self.assertEqual(rerolled["state"]["tokens"]["roll"], 0)
        self.assertIsNotNone(rerolled["decision"])

        chosen = self._play(player, "choose", index=1)
        self.assertIsNone(chosen["decision"])
        pool_after_choose = chosen["state"]["pool_size"]

        self._edit_state(player, lambda state: state["tokens"].update({"remove": 1}))
        before_remove = self._play(player, "state")
        self.assertIn("ingredients", before_remove)
        removable = next(action for action in before_remove["actions"] if action.get("action") == "remove")
        removed = self._play(player, "remove", index=removable["index"])
        self.assertEqual(removed["state"]["pool_size"], pool_after_choose - 1)
        self.assertEqual(removed["state"]["tokens"]["remove"], 0)

        self._edit_state(player, lambda state: state["items"].append("sandpaper_box"))
        with_item = self._play(player, "state")
        self.assertTrue(any(action.get("action") == "use" for action in with_item["actions"]))
        used = self._play(player, "use", item_id="sandpaper_box")
        self.assertFalse(any(item.get("id") == "sandpaper_box" for item in used["owned_items"]))
        self.assertEqual(used["state"]["pool_size"], removed["state"]["pool_size"] + 2)

        resumed = self._play(player, "state")
        self.assertEqual(resumed["state"]["spin"], 1)
        self.assertEqual(resumed["state"]["pool_size"], used["state"]["pool_size"])
        persisted = json.loads(self._state_path(player).read_text(encoding="utf-8"))
        self.assertIn("rng_state", persisted)

    def test_players_are_isolated_and_export_import_is_per_player(self):
        player_a = "guest:cruciblea"
        player_b = "guest:crucibleb"
        self._play(player_a, "new", seed=11, difficulty=1)
        self._play(player_b, "new", seed=22, difficulty=3)
        self._play(player_a, "spin")

        state_a = self._play(player_a, "state")
        state_b = self._play(player_b, "state")
        self.assertEqual(state_a["state"]["spin"], 1)
        self.assertEqual(state_b["state"]["spin"], 0)
        self.assertEqual(state_b["state"]["seed"], 22)
        self.assertNotEqual(self._state_path(player_a).read_bytes(), self._state_path(player_b).read_bytes())

        exported = self._play(player_a, "export")
        save_data = json.loads(exported["text"])
        imported = self._play(player_b, "import", save_data=save_data, confirm=True)
        self.assertEqual(imported["state"]["seed"], 11)
        self.assertEqual(imported["state"]["spin"], 1)

    def test_corrupt_save_is_backed_up_and_warning_is_returned(self):
        player = "guest:cruciblecorrupt"
        self._play(player, "new", seed=9, difficulty=2)
        self._state_path(player).write_text("{broken", encoding="utf-8")

        recovered = self._play(player, "state")
        self.assertIn("原存档损坏", recovered["warning"])
        self.assertEqual(recovered["action"], "recovered")
        self.assertEqual(recovered["state"]["seed"], 1)
        backups = list(self._state_path(player).parent.glob("state.json.corrupt-*"))
        self.assertEqual(len(backups), 1)
        self.assertEqual(backups[0].read_text(encoding="utf-8"), "{broken")


class CrucibleEchoesPlatformTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(prefix="crucible-platform-")
        self.save_root = Path(self.tempdir.name) / "vendor_saves"
        self.patches = [
            patch.object(base, "SAVE_ROOT", self.save_root),
            patch.object(adapter, "SAVE_ROOT", self.save_root),
            patch.object(server, "VENDOR_SAVE_ROOT", self.save_root),
            patch.object(server, "_ensure_guest_claim_code", return_value=None),
            patch.object(server, "_play_announcements", return_value=""),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.tempdir.cleanup()

    def test_mcp_catalog_guide_schema_and_play_chain(self):
        self.assertIn("crucible_echoes·确定性文字炼金构筑", server._tool_list_games())
        guide = json.loads(server._tool_get_guide({"game": "crucible_echoes"}))
        self.assertIn("athok", guide["guide"])
        self.assertIn("MIT License", guide["guide"])
        self.assertIn("remove", guide["guide"])

        play_tool = next(tool for tool in server._root_tools(user_agent="Kelivo/1") if tool["name"] == "play")
        properties = play_tool["inputSchema"]["properties"]["params"]["properties"]
        self.assertIn("index", properties)
        self.assertIn("difficulty", properties)
        self.assertIn("crucible_echoes", play_tool["inputSchema"]["properties"]["action"]["description"])

        opened = json.loads(server._tool_play_inner({
            "game": "crucible_echoes",
            "action": "new",
            "player_id": "guest:cruciblemcp",
            "params": {"seed": 42, "difficulty": 1},
        }))
        self.assertEqual(opened["state"]["spin"], 0)
        spun = json.loads(server._tool_play_inner({
            "game": "crucible_echoes",
            "action": "spin",
            "player_id": "guest:cruciblemcp",
        }))
        self.assertEqual(spun["state"]["spin"], 1)
        self.assertTrue(spun["decision"]["offers"])

    def test_frontend_card_and_http_page_are_available(self):
        homepage = (ROOT / "index.html").read_text(encoding="utf-8")
        page = (ROOT / "crucible_echoes.html").read_text(encoding="utf-8")
        self.assertIn('id: "crucible_echoes"', homepage)
        self.assertIn('url: "/crucible-echoes/"', homepage)
        self.assertIn("5583289470", homepage)
        self.assertIn('game: "crucible_echoes"', page)
        self.assertIn("MIT License", page)

        httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=2)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request("GET", "/crucible-echoes/")
            response = connection.getresponse()
            body = response.read().decode("utf-8")
            connection.close()
            self.assertEqual(response.status, 200)
            self.assertIn("坩埚余响", body)
            self.assertIn("/mcp", body)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
