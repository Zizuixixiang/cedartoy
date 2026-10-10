import copy
import unittest
from unittest.mock import patch

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·棋牌对弈·南山君&Clio", result)

    def test_catalog_preserves_categories_ids_and_order(self):
        expected = {
            "测试": "mbti enneagram dnd love ecr humanity sins_virtues bdsmtest".split(),
            "小游戏": (
                "turtle_soup duel tarot ai_life detroit fishing bar forest moonlit eco "
                "ciyuwu leek delve nowhere travel arcade burger crucible_echoes imitator_td "
                "puzzle_box memoria white_room market workkk garden_cat camping_plaza"
            ).split(),
        }
        expected_details = dict(line.split("·", 1) for line in """mbti·人格类型·南山君
enneagram·九型人格·Max Ross
dnd·道德阵营·南山君
love·爱语偏好·南山君
ecr·依恋类型·南山君
humanity·人类浓度·南山君
sins_virtues·罪与美德·南山君
bdsmtest·性癖倾向·南山君
turtle_soup·情境推理·南山君
duel·棋牌对弈·南山君&Clio
tarot·塔罗占卜·林默Moon
ai_life·人生策略·乐诶雷女士
detroit·仿生人叙事·如火如風的容
fishing·钓鱼收集·初一
bar·酒馆经营·西兰花
forest·童话抉择·阿尢
moonlit·卡牌闯关·苏苏脆脆
eco·生态模拟·南山君&Clio
ciyuwu·言语求生·青少年小鼠狂饮乙醇
leek·股市交易·贰拾壹
delve·下矿寻宝·包工头
nowhere·地球漫游·青少年小鼠狂饮乙醇
travel·虚拟旅行·沈澈&sevenleft
arcade·街机博彩·多肉饲养员
burger·汉堡店经营·飞鸢
crucible_echoes·炼金构筑·athok
imitator_td·随机塔防·すみか
puzzle_box·解码谜题·Runsheng_
memoria·车站探案·雨刀
white_room·自由叙事·雨刀
market·买菜做饭·青少年小鼠狂饮乙醇
workkk·打工模拟·💤
garden_cat·种花养猫·乐诶雷女士
camping_plaza·露营地经营·乐诶雷女士""".splitlines())
        with patch.dict(server.GAME_MAINTENANCE, {}, clear=True):
            lines = server._tool_list_games().splitlines()
        categories = {}
        for line in lines:
            if ": " not in line:
                continue
            category, entries = line.split(": ", 1)
            categories[category] = []
            for entry in entries.split(" | "):
                game, description, author = entry.split("·")
                self.assertRegex(description, r"^[\u4e00-\u9fff]{4,8}$")
                self.assertEqual(description + "·" + author, expected_details[game])
                categories[category].append(game)
        self.assertEqual(list(categories), list(expected))
        self.assertEqual(categories, expected)
        self.assertEqual(sum(map(len, categories.values())), 34)

    def test_catalog_is_minimal_and_has_no_accounts_or_instructions(self):
        authors_before = copy.deepcopy(server.AUTHORS)
        catalog = server._tool_list_games()
        self.assertEqual(server.AUTHORS, authors_before)
        self.assertLess(len(catalog), 800)
        self.assertNotRegex(catalog, r"小红书|\d{8,}|racy1501|_Sssonnet0220|同作者")
        self.assertNotRegex(catalog, r"同档|围观|原版|3D UI|完整版|轻量版|\d+题|逐题|批量")
        for author in server.AUTHORS.values():
            self.assertIn("·" + author["name"].split("（", 1)[0], catalog)
            self.assertNotIn(author["name"], catalog)
        self.assertNotIn(server.sins_virtues_questions.DISCLAIMER, catalog)
        self.assertNotRegex(catalog, r"作者|格式|play\(|\brest\b|防沉迷|重置|\d+款|\d+型")
        self.assertEqual(catalog.splitlines()[0], "玩法见 get_guide(game)")
        self.assertEqual(len(catalog.splitlines()), 3)

    def test_catalog_keeps_maintenance_label(self):
        for enabled in (True, False):
            with self.subTest(enabled=enabled), patch.dict(server.GAME_MAINTENANCE, {
                "camping_plaza": {"enabled": enabled, "label": "维护中"},
            }, clear=True):
                catalog = server._tool_list_games()
                self.assertEqual("（维护中）" in catalog, enabled)
                label = "camping_plaza（维护中）" if enabled else "camping_plaza"
                self.assertIn(label + "·露营地经营·乐诶雷女士", catalog)

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
