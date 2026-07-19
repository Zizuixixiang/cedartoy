import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
MARKET_VENDOR = ROOT / "vendor" / "shangzhuochifan"
sys.path.insert(0, str(MARKET_VENDOR))

import market_engine  # noqa: E402


class MarketCookingRegressionTests(unittest.TestCase):
    def test_plated_egg_cannot_burn_while_cooking_next_dish(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            save_path = Path(temp_dir) / "market_save.json"
            with patch.object(market_engine, "SAVE_FILE", str(save_path)):
                game = market_engine.MarketGame()
                game.new_day(seed=20260719, force=True)
                game.fridge = [
                    {"name": "鸡蛋", "quality": "good", "qty": 1},
                    {"name": "番茄", "quality": "good", "qty": 1},
                ]

                game.go_home()
                game.start_dish("炒鸡蛋")
                game.cook_step("热锅倒油")
                game.cook_step("炒鸡蛋")
                plated = game.cook_step("盛出鸡蛋")

                self.assertIn("盛出鸡蛋", plated)
                self.assertNotIn("鸡蛋", game.kitchen_state["item_state"])
                self.assertNotIn("鸡蛋", game.kitchen_state["pot_contents"])

                game.start_dish("炒番茄")
                outputs = [game.cook_step("大火炒番茄") for _ in range(8)]
                stale_burn_warnings = [
                    line
                    for output in outputs
                    for line in output.splitlines()
                    if "鸡蛋" in line and ("糊" in line or "焦" in line)
                ]
                self.assertEqual([], stale_burn_warnings)


if __name__ == "__main__":
    unittest.main(verbosity=2)
