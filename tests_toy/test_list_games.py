import unittest

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·29款棋牌（双弈）·南山君&Clio", result)


if __name__ == "__main__":
    unittest.main()
