import unittest

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·双弈，25款棋牌骰对弈，支持多人/NPC桌与娱乐筹码·南山君&Clio", result)


if __name__ == "__main__":
    unittest.main()
