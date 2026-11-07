import copy
import io
import json
import re
import subprocess
import unittest
from unittest.mock import Mock, patch

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·双弈·南山君&Clio", result)

    @staticmethod
    def homepage_names():
        handler = object.__new__(server.CedarToyHandler)
        handler.path, handler.headers = "/", {}
        handler.wfile = io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        # Exercise GET /, including all server-side card injection. Only the
        # unrelated save count is stubbed so no production storage is read.
        with patch.object(server, "_count_puzzle_box_saves", return_value=0):
            handler.do_GET()
        handler.send_response.assert_called_once_with(200)
        home = handler.wfile.getvalue().decode("utf-8")
        # Execute the delivered data, including generated test/topic cards.
        source = home[home.index('    const games = ['):home.index('    let selected = "soup";')]
        cards = json.loads(subprocess.check_output(['node', '-e', source +
            ';console.log(JSON.stringify(games.filter(g=>!g.adminOnly).map(({id,name})=>({id,name}))))'], text=True))
        aliases = {"soup": "turtle_soup", "attribute": "bdsmtest"}
        return {aliases.get(card["id"], card["id"]): card["name"] for card in cards}

    @staticmethod
    def entries(catalog):
        # The official title may itself contain ·; the last · separates credit.
        return re.findall(r"([a-z][a-z0-9_]*)(?:（[^）]*）)?·([^|\n]+)·([^·|\n]+)", catalog)

    def test_catalog_preserves_categories_ids_and_order(self):
        expected = {
            "深聊问答": list(server.deeptalk_catalog.ALIASES),
            "测试": "mbti enneagram dnd love ecr humanity sins_virtues bdsmtest".split()
                    + sorted(server.research_scales.MCP_GAMES),
            "游戏": (
                "turtle_soup duel tarot ai_life detroit fishing bar forest moonlit eco "
                "ciyuwu leek delve nowhere travel arcade burger crucible_echoes imitator_td "
                "puzzle_box memoria white_room candle shared_craft market workkk garden_cat camping_plaza autochess"
            ).split(),
        }
        with patch.dict(server.GAME_MAINTENANCE, {}, clear=True), patch.object(
                server.autochess_adapter, "enabled", return_value=True):
            catalog = server._tool_list_games()
        sections = re.split(r"\n(深聊问答|测试|游戏):\s*", catalog)
        categories = {sections[i]: [entry[0] for entry in self.entries(sections[i + 1])]
                      for i in range(1, len(sections), 2)}
        self.assertEqual(list(categories), list(expected))
        self.assertEqual(categories, expected)
        self.assertEqual(sum(map(len, categories.values())), 64)
        with patch.object(server.autochess_adapter, "enabled", return_value=False):
            self.assertNotIn("autochess·", server._tool_list_games())

    def test_all_names_match_homepage_favorites_and_delivered_guides(self):
        names = self.homepage_names()
        self.assertEqual(len(names), 65)
        with patch.object(server.autochess_adapter, "enabled", return_value=True):
            entries = self.entries(server._tool_list_games())
        ids = {game for game, _, _ in entries}
        self.assertEqual(set(names) - ids, {"aidep"})
        self.assertEqual(ids - set(names), set())
        for game, name, _ in entries:
            with self.subTest(game=game):
                self.assertEqual(name, names[game])
                self.assertEqual(name, server.favorites.GAME_NAMES[game])
                response = json.loads(server._tool_get_guide({"game": game}))
                self.assertEqual(response["game"], game)
                if game == "turtle_soup":
                    self.assertIn("海龟汤", json.dumps(response, ensure_ascii=False))
                else:
                    heading = response["guide"].splitlines()[0].lstrip("# ")
                    heading = re.sub(r"^" + game + r"\s*·\s*", "", heading)
                    self.assertTrue(heading.startswith(name), heading)
        with self.assertRaises(server._McpError):
            server._tool_get_guide({"game": "aidep"})

    def test_deeptalk_short_credits_keep_full_sources_in_guides(self):
        catalog = server.deeptalk_catalog
        entries = self.entries(catalog.discovery())
        self.assertEqual(len(entries), 10)
        for game, name, author in entries:
            topic = catalog.load_topic(catalog.ALIASES[game])
            self.assertEqual(author, catalog.AUTHOR_SHORT[topic["topic_id"]])
            self.assertNotEqual(author, catalog.TOPICS[topic["topic_id"]][2])
            guide = json.loads(server._tool_get_guide({"game": game}))["guide"]
            for key in ("title", "author", "source_url", "license"):
                self.assertIn(topic[key], guide)

    def test_catalog_is_minimal_and_has_no_accounts_or_instructions(self):
        authors_before = copy.deepcopy(server.AUTHORS)
        catalog = server._tool_list_games()
        self.assertEqual(server.AUTHORS, authors_before)
        self.assertLess(len(catalog), 1750)  # 64 entries; exact homepage titles take priority.
        self.assertNotRegex(catalog, r"小红书|\d{8,}|racy1501|_Sssonnet0220|同作者")
        self.assertNotRegex(catalog, r"同档|围观|原版|3D UI|完整版|轻量版|\d+题|逐题|批量")
        for author in server.AUTHORS.values():
            self.assertIn("·" + author["name"].split("（", 1)[0], catalog)
            self.assertNotIn(author["name"], catalog)
        self.assertNotIn(server.sins_virtues_questions.DISCLAIMER, catalog)
        self.assertNotRegex(catalog, r"作者|格式|play\(|\brest\b|防沉迷|重置|\d+款|\d+型")
        self.assertEqual(catalog.splitlines()[0], "玩法见 get_guide(game)")
        self.assertEqual(len(catalog.splitlines()), 14)

    def test_catalog_keeps_maintenance_label(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), patch.dict(server.GAME_MAINTENANCE, {
                "camping_plaza": {"enabled": enabled, "label": "维护中"},
            }, clear=True):
                catalog = server._tool_list_games()
                self.assertEqual("（维护中）" in catalog, enabled)
                label = "camping_plaza（维护中）" if enabled else "camping_plaza"
                self.assertIn(label + "·露营广场·乐诶雷女士", catalog)

    def test_normal_and_kelivo_clients_receive_same_catalog(self):
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "list_games", "arguments": {}},
        }
        # Isolate identity and notification storage; exercise the real catalog.
        with (
            patch.object(server, "_authenticated_ai_player_id", return_value=None),
            patch.object(server, "_duel_unread_request_reminder", return_value=""),
            patch.object(server, "_mcp_forced_announcement", return_value=""),
        ):
            for enabled in (True, False):
                with patch.dict(server.GAME_MAINTENANCE, {
                    "camping_plaza": {"enabled": enabled, "label": "维护中"},
                }, clear=True):
                    for user_agent in ("ExampleMcpClient/1.0", "Kelivo/1.3.1", "Dart/3.9 (dart:io)", "ktor-client/3.0"):
                        with self.subTest(enabled=enabled, user_agent=user_agent):
                            response = server._handle_root_mcp(payload, user_agent=user_agent)
                            self.assertFalse(response["result"]["isError"])
                            self.assertEqual(response["result"]["content"], [
                                {"type": "text", "text": server._tool_list_games()},
                            ])


if __name__ == "__main__":
    unittest.main()
