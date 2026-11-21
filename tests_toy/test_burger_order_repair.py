import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from vendor_cmd_adapter import base, burger


def legacy_order(customer="宁枝", **fields):
    return {
        "customer": customer, "focus": "accuracy", "patience": 11,
        "base_price": 23, "tip_mult": 1.0, "bun": "原味胚",
        "protein": "鸡肉饼", "doneness": "正好", "toppings": ["生菜", "番茄"],
        "sauce": "辣酱", "sauce_amount": "少量",
        "required_layers": ["原味胚", "生菜", "番茄", "鸡肉饼", "原味胚"],
        "dislikes": ["牛肉饼", "培根"], "vegetarian": False,
        "vegetarian_rule": None, "batch_count": 1, "event_key": "暴雨外卖",
        **fields,
    }


class BurgerOrderRepairTests(unittest.TestCase):
    player = "guest:burgerrepair"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        for module in (base, burger):
            override = patch.object(module, "SAVE_ROOT", self.root)
            override.start()
            self.addCleanup(override.stop)

        self.path = self.root / "burger" / self.player / "save.json"
        self.backup = self.path.with_name("save.json.pre-vegetarian.bak")
        burger.play({"action": "new", "player_id": self.player})
        self.original = self.read()
        normal = legacy_order("周姨", dislikes=["培根", "辣酱"], sauce=None, sauce_amount=None,
                              base_price=24)
        self.original.update(
            day=7, order_pointer=4, scores=[100, 92, 95, 84],
            coins=342, revenue=88, tips=20, daily_gross=108,
            renovations={"霓虹招牌": True}, journal=["第7天第4单：宁枝，84分。"],
            active_event={"key": "暴雨外卖", "desc": "测试", "patience": -1},
            current_orders=[
                normal, normal, normal, legacy_order(), legacy_order(note="原订单附注"),
                normal, legacy_order("伊芙", batch_count=2),
                legacy_order("测试顾客", archetype="素食客", dislikes=["煎蛋", "辣酱"]),
            ],
        )
        self.write(self.original)

    def read(self):
        return json.loads(self.path.read_bytes())

    def write(self, data):
        # Deliberately unlike the runner's JSON formatting; the backup must be exact.
        self.path.write_text(json.dumps(data) + "\n", encoding="utf-8")

    def command(self, command):
        return burger.play({"player_id": self.player, "command": command})["text"]

    def assert_vegetarian(self, order):
        self.assertIs(order["vegetarian"], True)
        self.assertTrue(order["vegetarian_rule"])
        self.assertEqual(order["bun"], "全麦胚")
        self.assertIn(order["protein"], (None, "煎蛋"))
        self.assertIn(order["doneness"], ("流心", "全熟") if order["protein"] else (None,))
        self.assertIn(order["sauce_amount"], ("少量", "正常") if order["sauce"] else (None,))
        vegetables = [x for x in order["toppings"] if x != "芝士"]
        layers = [order["bun"], *vegetables[:2]]
        if order["protein"]:
            layers.append(order["protein"])
        layers += [x for x in order["toppings"] if x == "芝士"]
        self.assertEqual(order["required_layers"], layers + vegetables[2:] + [order["bun"]])
        self.assertTrue({"鸡肉饼", "牛肉饼", "培根"} <= set(order["dislikes"]))
        self.assertTrue(set(order["dislikes"]).isdisjoint(order["required_layers"] + [order["sauce"]]))
        price = 12 + 2 * len(order["toppings"]) + 5 * bool(order["protein"]) + 2 * bool(order["sauce"])
        if order["batch_count"] == 2:
            price = int(price * 1.75)
        self.assertEqual(order["base_price"], price + 2 + 1)  # 霓虹招牌、暴雨外卖

    def test_day_seven_repairs_only_unstarted_violations_before_display(self):
        before = self.path.read_bytes()
        other = self.root / "burger" / "100:2" / "save.json"
        other.parent.mkdir()
        other.write_bytes(before)
        shown = self.command("orders")
        repaired = self.read()
        orders = repaired["current_orders"]
        for index in (4, 6, 7):
            self.assert_vegetarian(orders[index])
        self.assertEqual(orders[4]["note"], "原订单附注")
        self.assertEqual(orders[6]["batch_count"], 2)
        self.assertTrue({"煎蛋", "辣酱"} <= set(orders[7]["dislikes"]))
        self.assertIn("第5单｜宁枝", shown)
        self.assertIn(" → ".join(orders[4]["required_layers"]), shown)
        self.assertIn(orders[4]["vegetarian_rule"], shown)
        expected = copy.deepcopy(self.original)
        for index in (4, 6, 7):
            expected["current_orders"][index] = orders[index]
        self.assertEqual(repaired, expected)
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertEqual(other.read_bytes(), before)
        self.assertEqual(list(other.parent.iterdir()), [other])

        self.command("status")
        self.assertEqual(self.read(), expected)
        self.assertEqual(self.backup.read_bytes(), before)
        self.assertEqual(set(self.path.parent.iterdir()), {self.backup, self.path})

    def test_current_progress_alone_protects_order_but_not_future_orders(self):
        for field, progress in (
            ("current_order_accepted", True), ("turn", 1), ("grill", {"2": {"protein": "鸡肉饼"}}),
            ("cooked", [{"protein": "鸡肉饼"}]), ("layers", ["原味胚"]), ("sauces", {"辣酱": "少量"}),
        ):
            with self.subTest(field=field):
                state = dict(self.original, **{field: progress})
                self.write(state)
                self.command("orders")
                after = self.read()
                self.assertEqual(after[field], progress)
                self.assertEqual(after["current_orders"][:6], state["current_orders"][:6])
                self.assert_vegetarian(after["current_orders"][6])

    def test_clean_pending_orders_and_settled_violation_make_no_backup(self):
        valid = legacy_order(
            "伊芙", vegetarian=True, vegetarian_rule="蔬菜顺序必须完全正确",
            dislikes=["牛肉饼", "鸡肉饼", "培根"], protein=None, doneness=None,
            bun="全麦胚", required_layers=["全麦胚", "生菜", "番茄", "全麦胚"], base_price=21,
        )
        state = dict(self.original, current_orders=self.original["current_orders"][:4] + [valid])
        self.write(state)
        self.command("orders")
        self.assertEqual(self.read(), state)
        self.assertFalse(self.backup.exists())

        # An otherwise legal recipe only needs its missing meat dislike restored.
        valid["dislikes"].remove("鸡肉饼")
        self.write(state)
        self.command("orders")
        valid["dislikes"].append("鸡肉饼")
        self.assertEqual(self.read(), state)

    def test_meat_in_layers_and_disliked_event_topping(self):
        state = self.original
        state["active_event"]["key"] = "隐藏菜单传闻"
        order = legacy_order(
            "测试顾客", archetype="素食客", vegetarian=True, vegetarian_rule="蔬菜顺序必须完全正确",
            protein=None, doneness=None, dislikes=["牛肉饼", "鸡肉饼", "培根", "酸黄瓜"],
            event_key="隐藏菜单传闻",
        )
        state["current_orders"] = state["current_orders"][:4] + [order]
        self.write(state)
        self.command("orders")
        repaired = self.read()["current_orders"][4]
        self.assertTrue(set(repaired["dislikes"]).isdisjoint(
            repaired["required_layers"] + repaired["toppings"] + [repaired["protein"], repaired["sauce"]]
        ))
        self.command("status")
        self.assertEqual(self.read()["current_orders"][4], repaired)

    def test_existing_backup_and_command_independent_repair(self):
        self.backup.write_bytes(b"first original")
        self.command("status")
        expected = self.read()
        self.write(self.original)
        self.command("orders;accept")
        self.assertTrue(self.read()["current_order_accepted"])
        self.assertEqual(self.read()["current_orders"], expected["current_orders"])
        self.assertEqual(self.backup.read_bytes(), b"first original")

    def test_backup_failure_leaves_save_untouched(self):
        before = self.path.read_bytes()
        self.backup.mkdir()
        with self.assertRaises(base.VendorCmdError):
            self.command("accept")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(self.path.parent.iterdir()), {self.path, self.backup})
