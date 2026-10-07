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
        self.assertFalse(opened["state"]["awaiting_mode_choice"])
        self.assertFalse(opened["state"]["endless_mode"])
        self.assertEqual(opened["state"]["endless_order"], 0)
        self.assertEqual(opened["state"]["endless_target"], 0)
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

    def test_run_end_choices_are_readable_and_choose_enters_requested_mode(self):
        def prepare_run_end(player_id: str):
            self._play(player_id, "new", seed=90210, difficulty=1)

            def mutate(state):
                state.update({
                    "status": "playing",
                    "order_index": 12,
                    "endless_mode": False,
                    "endless_order": 0,
                    "endless_target": 0,
                    "pending": [{
                        "kind": "run_end",
                        "offers": ["end_run", "enter_endless"],
                        "can_skip": False,
                        "source": "mainline_complete",
                        "minimum_rarity": None,
                        "tag_filter": None,
                    }],
                })
                state["stats"].update({
                    "highest_endless_order": 4,
                    "endless_orders_completed": 3,
                    "highest_endless_single_turn_gold": 987,
                    "highest_single_turn_gold": 1234,
                })

            self._edit_state(player_id, mutate)
            return self._play(player_id, "state")

        prompt = prepare_run_end("guest:crucibleendrun")
        self.assertTrue(prompt["state"]["awaiting_mode_choice"])
        self.assertFalse(prompt["state"]["endless_mode"])
        self.assertEqual(prompt["state"]["endless_order"], 0)
        self.assertEqual(prompt["state"]["endless_target"], 0)
        self.assertEqual(prompt["state"]["highest_endless_order"], 4)
        self.assertEqual(prompt["state"]["endless_orders_completed"], 3)
        self.assertEqual(prompt["state"]["highest_endless_single_turn_gold"], 987)
        self.assertEqual(prompt["state"]["highest_single_turn_gold"], 1234)
        self.assertEqual(prompt["decision"]["kind"], "run_end")
        self.assertEqual(prompt["decision"]["offers"], [
            {
                "index": 1,
                "id": "end_run",
                "name": "结束本局",
                "description": "按正常通关处理，结束本局。",
            },
            {
                "index": 2,
                "id": "enter_endless",
                "name": "进入无限模式",
                "description": "保留当前状态，进入10回合的无限订单1。",
            },
        ])
        self.assertEqual(
            [(action.get("index"), action.get("id")) for action in prompt["actions"] if action.get("action") == "choose"],
            [(1, "end_run"), (2, "enter_endless")],
        )

        ended = self._play("guest:crucibleendrun", "choose", index=1)
        self.assertEqual(ended["state"]["status"], "won")
        self.assertFalse(ended["state"]["endless_mode"])
        self.assertFalse(ended["state"]["awaiting_mode_choice"])

        prepare_run_end("guest:crucibleenterendless")
        endless = self._play("guest:crucibleenterendless", "choose", index=2)
        self.assertEqual(endless["state"]["status"], "playing")
        self.assertTrue(endless["state"]["endless_mode"])
        self.assertFalse(endless["state"]["awaiting_mode_choice"])
        self.assertEqual(endless["state"]["endless_order"], 1)
        self.assertEqual(endless["state"]["endless_target"], 1000)
        self.assertEqual(endless["state"]["spins_left"], 10)

    def test_toggle_persists_across_processes_without_advancing_rng(self):
        player = "guest:crucibletoggle"
        other = "guest:crucibletoggleother"
        for identity in (player, other):
            self._play(identity, "new", seed=42)
            self._edit_state(identity, lambda state: state["items"].append("ban"))
        other_before = self._state_path(other).read_bytes()
        before = json.loads(self._state_path(player).read_text())

        for enabled in (True, False):
            toggled = self._play(player, "toggle", item_id=" ban ")
            self.assertEqual(toggled["action"], "toggle")
            resumed = self._play(player, "state")
            for result in (toggled, resumed):
                spec = next(row for row in result["actions"] if row["action"] == "toggle")
                self.assertEqual(spec["item_id"], "ban")
                self.assertEqual(spec["enabled"], enabled)
            saved = json.loads(self._state_path(player).read_text())
            self.assertEqual(saved["flags"]["ingredient_generation_disabled"], enabled)
            for key in ("rng_state", "spin", "gold", "tokens", "items", "ingredients"):
                self.assertEqual(saved[key], before[key], key)
        self.assertEqual(self._state_path(other).read_bytes(), other_before)

    def test_toggle_rejects_invalid_items_and_action_windows_without_saving(self):
        player = "guest:crucibletoggleinvalid"
        self._play(player, "new", seed=42)
        self._edit_state(player, lambda state: state["items"].extend(["ban", "sandpaper_box"]))
        for item_id in (None, "", " ", 1, True, "missing", "sandpaper_box"):
            with self.subTest(item_id=item_id):
                before = self._state_path(player).read_bytes()
                with self.assertRaises(base.VendorCmdError):
                    self._play(player, "toggle", item_id=item_id)
                self.assertEqual(self._state_path(player).read_bytes(), before)

        self._play(player, "spin")
        for status in ("playing", "won", "lost"):
            with self.subTest(status=status):
                def mutate(state):
                    state["status"] = status
                    if status != "playing":
                        state["pending"] = []
                self._edit_state(player, mutate)
                before = self._state_path(player).read_bytes()
                self.assertFalse(any(row["action"] == "toggle" for row in self._play(player, "state")["actions"]))
                with self.assertRaises(base.VendorCmdError):
                    self._play(player, "toggle", item_id="ban")
                self.assertEqual(self._state_path(player).read_bytes(), before)

    def test_d6e3598_save_loads_unchanged_and_continues_deterministically(self):
        # Captured through the old CedarToy adapter at upstream d6e3598:
        # new(seed=42, difficulty=1), spin; no real player data.
        fixture = (ROOT / "tests_toy/fixtures/crucible_echoes_d6e3598.json").read_bytes()
        legacy = json.loads(fixture)
        self.assertNotIn("round_event_values", legacy["stats"])
        self.assertNotIn("round_removed_values", legacy["stats"])
        players = ("guest:cruciblelegacy", "guest:cruciblelegacycopy")
        for player in players:
            path = self._state_path(player)
            path.parent.mkdir(parents=True)
            path.write_bytes(fixture)
            loaded = self._play(player, "state")
            self.assertNotIn("warning", loaded)
            self.assertEqual(loaded["state"]["spin"], legacy["spin"])
            self.assertEqual(loaded["state"]["gold"], legacy["gold"])
            self.assertEqual(loaded["last_board"], legacy["last_board"])
            self.assertEqual([row["id"] for row in loaded["decision"]["offers"]], legacy["pending"][0]["offers"])
            self.assertEqual(path.read_bytes(), fixture)

        for action, params in (("choose", {"index": 1}), ("spin", {}), ("skip", {})):
            for player in players:
                self.assertNotIn("warning", self._play(player, action, **params))
            self.assertEqual(self._state_path(players[0]).read_bytes(), self._state_path(players[1]).read_bytes())
        continued = json.loads(self._state_path(players[0]).read_text())
        self.assertEqual(continued["spin"], 2)
        self.assertIn("round_event_values", continued["stats"])
        self.assertIn("round_removed_values", continued["stats"])
        self.assertFalse(list(self.save_root.rglob("*.corrupt-*")))

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
        self.assertIn("toggle", guide["guide"])
        self.assertIn("run_end", guide["guide"])
        self.assertIn("无限模式", guide["guide"])

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

        state_path = self.save_root / "crucible_echoes" / "guest:cruciblemcp" / "state.json"
        saved = json.loads(state_path.read_text())
        saved["pending"] = []
        saved["items"].append("ban")
        state_path.write_text(json.dumps(saved), encoding="utf-8")
        toggled = json.loads(server._tool_play_inner({
            "game": "crucible_echoes",
            "action": "toggle",
            "player_id": "guest:cruciblemcp",
            "params": {"item_id": "ban"},
        }))
        self.assertEqual(toggled["action"], "toggle")
        self.assertTrue(next(row for row in toggled["actions"] if row["action"] == "toggle")["enabled"])

    def test_homepage_card_links_upstream_without_custom_game_page(self):
        homepage = (ROOT / "index.html").read_text(encoding="utf-8")
        self.assertIn('id: "crucible_echoes"', homepage)
        card = homepage.split('id: "crucible_echoes"', 1)[1].split('\n      },', 1)[0]
        self.assertIn('iconFile: "crucible-echoes.png"', card)
        self.assertIn('url: "https://github.com/megabaka404/crucible-echoes"', card)
        self.assertIn('ctaLabel: "GitHub →"', card)
        self.assertIn("5583289470", card)
        self.assertNotIn('watchLabel: "进入实验室 →"', card)
        self.assertNotIn('window.location.href = "/crucible-echoes/"', homepage)
        self.assertFalse((ROOT / "crucible_echoes.html").exists())

        httpd = server.ThreadPoolHTTPServer(("127.0.0.1", 0), server.CedarToyHandler, max_workers=2)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1", httpd.server_port, timeout=5)
            connection.request("GET", "/crucible-echoes/")
            response = connection.getresponse()
            response.read()
            connection.close()
            self.assertEqual(response.status, 404)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
