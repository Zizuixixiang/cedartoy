import json
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class HomepageGameOrderingTests(unittest.TestCase):
    def setUp(self):
        self.home = (ROOT / "index.html").read_text(encoding="utf-8")

    @unittest.skipUnless(shutil.which("node"), "node is required for homepage JavaScript test")
    def test_mini_games_put_fixed_and_human_frontend_games_first(self):
        start = self.home.index("    const games = [")
        end = self.home.index('    let selected = "soup";')
        catalog_script = textwrap.dedent(self.home[start:end])
        probe = catalog_script + """
          const miniGameIds = homepageGames
            .filter((game) => game.category === "mini" && !game.adminOnly)
            .map((game) => game.id);
          process.stdout.write(JSON.stringify(miniGameIds));
        """

        result = subprocess.run(
            ["node", "-"],
            cwd=ROOT,
            input=probe,
            text=True,
            capture_output=True,
            check=True,
        )

        self.assertEqual(
            json.loads(result.stdout),
            [
                "soup",
                "fishing",
                "eco",
                "forest",
                "garden_cat",
                "camping_plaza",
                "duel",
                "workkk",
                "white_room",
                "market",
                "ciyuwu",
                "arcade",
                "leek",
                "bar",
                "travel",
                "burger",
                "delve",
                "moonlit",
                "imitator_td",
                "memoria",
            ],
        )

    def test_ordering_uses_explicit_frontend_ids_and_sorted_catalog(self):
        self.assertIn("const HUMAN_FRONTEND_GAME_IDS = new Set([", self.home)
        self.assertIn('return left.name.localeCompare(right.name, "zh-CN");', self.home)
        self.assertIn("const visibleGames = homepageGames.filter(canShowGame);", self.home)
        self.assertIn('$("gameList").innerHTML = homepageGames.map((game) => {', self.home)


if __name__ == "__main__":
    unittest.main()
