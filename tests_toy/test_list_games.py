import unittest

import server


class ListGamesTests(unittest.TestCase):
    def test_duel_is_listed(self):
        result = server._tool_list_games()

        self.assertIn("duel·双弈，人类与绑定小机的6种棋类对弈厅", result)


if __name__ == "__main__":
    unittest.main()
