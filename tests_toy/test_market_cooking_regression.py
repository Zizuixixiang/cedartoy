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
    def kitchen(self, *names):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        save = patch.object(market_engine, "SAVE_FILE", str(Path(temp.name) / "save.json"))
        save.start()
        self.addCleanup(save.stop)

        game = market_engine.MarketGame()
        game.new_day(seed=20260719, force=True)
        game.fridge = [{"name": name, "quality": "good", "qty": 1} for name in names]
        game.basket = []
        game.go_home()
        return game

    def test_plated_egg_cannot_burn_while_cooking_next_dish(self):
        game = self.kitchen("鸡蛋", "番茄")
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

    def test_prepared_cabbage_enters_hot_pan(self):
        for command in ("白菜丝倒进锅", "白菜丝下锅煸炒", "把白菜丝倒入锅中，翻炒"):
            with self.subTest(command=command):
                game = self.kitchen("大白菜")
                game.cmd("切丝大白菜")
                game.cmd("热锅倒油")
                game = market_engine.MarketGame()
                game.load()  # 和适配层一样，从落盘的案板状态继续
                game.cmd(command)
                ks = game.kitchen_state
                self.assertNotIn("大白菜", ks["_on_board"])
                self.assertGreater(ks["item_state"]["大白菜"], 0)

    def test_ambiguous_and_missing_cabbage_do_not_advance(self):
        for names in [("大白菜", "小白菜"), ("上海青",)]:
            with self.subTest(names=names):
                game = self.kitchen(*names)
                game.cmd("做 清炒时蔬")
                game.cmd("炒" + names[0])
                ks = game.kitchen_state
                skills = game.player_skills.copy()
                response = game.cmd("白菜丝下锅翻炒")
                self.assertIn("白菜", response)
                if len(names) == 2:
                    self.assertIn("大白菜", response)
                    self.assertIn("小白菜", response)
                self.assertNotIn("stir_fry", ks["completed_steps"])
                self.assertEqual(skills, game.player_skills)

    def test_empty_stir_cold_pan_and_real_nameless_stir(self):
        game = self.kitchen("大白菜")
        game.cmd("做 清炒时蔬")
        game.cmd("切大白菜")
        ks = game.kitchen_state
        for command in ("大白菜下锅", "翻炒"):
            skills = game.player_skills.copy()
            response = game.cmd(command)
            self.assertIn("锅", response)
            self.assertEqual(0, ks["item_state"]["大白菜"])
            self.assertNotIn("stir_fry", ks["completed_steps"])
            self.assertEqual(skills, game.player_skills)
        game.cmd("炒大白菜")
        before = ks["item_state"]["大白菜"]
        skills = game.player_skills.copy()
        self.assertIn("写明", game.cmd("炒紫薯丝"))
        self.assertEqual(skills, game.player_skills)
        self.assertEqual(before, ks["item_state"]["大白菜"])
        game.cmd("继续翻炒几下")
        self.assertGreater(ks["item_state"]["大白菜"], before)
        self.assertIn("stir_fry", ks["completed_steps"])

    def test_pour_held_item_or_new_ingredient(self):
        game = self.kitchen("鸡蛋", "番茄", "大白菜")
        ks = game.kitchen_state
        game.cmd("炒鸡蛋和番茄")
        game.cmd("盛出鸡蛋")
        held_doneness = ks["item_state"]["鸡蛋"]
        game.cmd("白菜丝倒进锅")
        self.assertEqual(held_doneness, ks["item_state"]["鸡蛋"])
        self.assertIn("鸡蛋", ks["held_items"])
        self.assertIn("大白菜", ks["pot_contents"])
        game.cmd("鸡蛋倒进锅")
        self.assertNotIn("鸡蛋", ks["held_items"])
        self.assertIn("鸡蛋", ks["pot_contents"])

    def test_quick_cook_respects_named_ingredients(self):
        for ingredient, name in [("苋菜", "苋菜"), ("白菜丝", "大白菜"), ("蛋", "鸡蛋"), ("蒜苗", "蒜苗"), ("黄瓜", "黄瓜")]:
            with self.subTest(ingredient=ingredient):
                game = self.kitchen(name, "上海青")
                game.cmd("做法 炒" + ingredient + "，翻炒")
                self.assertEqual({name}, set(game.kitchen_state["item_state"]))
        game = self.kitchen("上海青")
        game.cmd("做法 炒苋菜，加盐出锅")
        self.assertEqual({}, game.kitchen_state["item_state"])
        self.assertFalse(game.done)

    def test_quick_cook_only_infers_a_single_candidate(self):
        game = self.kitchen("苋菜", "上海青")
        response = game.cmd("做法 翻炒，加盐出锅")
        self.assertIn("食材", response)
        self.assertFalse(game.kitchen_state["item_state"])
        self.assertFalse(game.done)
        game = self.kitchen("苋菜")
        game.cmd("做法 切苋菜，翻炒")
        self.assertGreater(game.kitchen_state["item_state"]["苋菜"], 0)

    def test_quick_cook_tomato_egg_recipe(self):
        game = self.kitchen("番茄", "鸡蛋", "上海青")
        game.cmd("做法 番茄切块，鸡蛋打散先炒盛出，再炒番茄出汁放回蛋，加盐出锅")
        self.assertTrue(game.done)
        self.assertEqual("番茄炒蛋", game.plate["dish"])
        self.assertEqual(["上海青"], [item["name"] for item in game.fridge])

    def test_prepared_cold_dish_can_still_be_served(self):
        game = self.kitchen("黄瓜", "上海青")
        game.cmd("做 凉拌黄瓜")
        game.cmd("做法 拍黄瓜，加盐，加醋拌匀，出锅")
        self.assertTrue(game.done)
        self.assertEqual("凉拌黄瓜", game.plate["dish"])
        self.assertEqual(["上海青"], [item["name"] for item in game.fridge])

    def test_leaf_ready_window_matches_serving(self):
        for d in (25, 29, 30, 40, 50, 51):
            with self.subTest(d=d):
                game = self.kitchen("苋菜")
                ks = game.kitchen_state
                ks["pot_contents"] = ["苋菜"]
                ks["item_state"] = {"苋菜": d}
                reply = game.cmd("加盐")
                ready = 30 <= d <= 50
                self.assertEqual(ready, "出锅正好" in reply)
                served = game.cmd("出锅")
                self.assertEqual(d < 30, "苋菜差点火候" in served)
                self.assertEqual(d > 50, "苋菜有点过了" in served)

    def test_partial_readiness_and_quick_cook_summary(self):
        game = self.kitchen("苋菜", "土豆")
        ks = game.kitchen_state
        ks["pot_contents"] = ["苋菜", "土豆"]
        ks["item_state"] = {"苋菜": 40, "土豆": 12}
        reply = game.cmd("加盐")
        self.assertIn("盛出苋菜", reply)
        self.assertNotIn("出锅正好", reply)
        ks["item_state"]["土豆"] = 42  # 平均超过 40，但土豆还没到最佳区间
        reply = game.cmd("做法 加盐")
        self.assertNotIn("端上桌？", reply)
        ks.update(heat=3, pot_temp=3)
        ks["item_state"]["苋菜"] = 50
        game.cmd("盛出苋菜")
        game.cmd("翻炒")
        self.assertEqual(50, ks["item_state"]["苋菜"])
        self.assertNotIn("苋菜", game.cmd("看锅"))

    def test_snapshot_matches_final_heat_and_lowering_fire(self):
        game = self.kitchen("苋菜")
        ks = game.kitchen_state
        ks.update(pot_contents=["苋菜"], item_state={"苋菜": 69}, heat=3, pot_temp=3)
        reply = game.cmd("加盐")
        self.assertIn(game.cmd("看锅").removeprefix("🍳 "), reply)
        self.assertIn("焦", reply)
        ks["item_state"]["苋菜"] = 40
        game.cmd("关小火")
        self.assertEqual(46.5, ks["item_state"]["苋菜"])
        game.cmd("关火")
        self.assertEqual(46.5, ks["item_state"]["苋菜"])

    def test_board_ingredient_is_not_served_or_scored_as_raw(self):
        game = self.kitchen("苋菜", "土豆")
        game.cmd("切土豆")
        game.cmd("炒苋菜")
        ks = game.kitchen_state
        ks["item_state"]["苋菜"] = 40
        reply = game.cmd("出锅")
        self.assertNotIn("土豆还是生的", reply)
        self.assertIn("土豆", [item["name"] for item in game.fridge])

    def test_trap_ending_and_inspection(self):
        game = self.kitchen("小白菜")
        game.fridge.clear()
        game.basket = [{"name": "小白菜", "quality": "trap", "qty": 1, "owner": "王姐"}]
        game.cmd("炒小白菜")
        reply = game.cmd("出锅")
        self.assertIn("王姐", reply)
        self.assertIn("小白菜⚠坑", reply)
        self.assertIn("当时就觉得不太对", reply)
        game = self.kitchen("小白菜")
        game.fridge.clear()
        game.basket = [{"name": "小白菜", "quality": "trap", "qty": 1}]
        self.assertIn(market_engine.TRAP_TRUTH["小白菜"], game.cmd("细看 小白菜"))

    def test_visiting_and_revisiting_cost_time_until_close(self):
        game = self.kitchen()
        game.market_time = 3
        game._market_closed = False
        game.closed_stalls.clear()
        for command, delta in [("去 veg_1", "3→2"), ("去 veg_1", "2→1"), ("去 veg_2", "1→0")]:
            reply = game.cmd(command)
            self.assertEqual(int(delta[-1]), game.market_time)
            self.assertIn(f"⏰{game.market_time:g}" if game.market_time > 0 else "⏰散场", reply)
        self.assertTrue(game._market_closed)
        self.assertIn("买不了", game.cmd("买 小白菜"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
