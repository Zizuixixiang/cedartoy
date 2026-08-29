import unittest

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·双弈，绑定人机的8种棋牌对弈，含多人/NPC桌", result)


if __name__ == "__main__":
    unittest.main()
