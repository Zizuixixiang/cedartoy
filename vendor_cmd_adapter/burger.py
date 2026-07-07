import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id


RUNNER_CODE = r'''
import contextlib
import io
import json
import random
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = payload["vendor_dir"]
command = (payload.get("command") or "status").strip()
extra = payload.get("extra") or {}

sys.path.insert(0, vendor_dir)
import game

game.SAVE_FILE = save_dir / "save.json"

SIGN_STYLES = {"温馨", "复古", "极简", "怪诞"}

def clone_default(value):
    return json.loads(json.dumps(value, ensure_ascii=False))

def migrate_state(state):
    for key, value in game.INITIAL_STATE.items():
        state.setdefault(key, clone_default(value))
    if not isinstance(state.get("renovations"), dict):
        state["renovations"] = {}
    if not isinstance(state.get("journal"), list):
        state["journal"] = []
    if not isinstance(state.get("weekly_history"), list):
        state["weekly_history"] = []
    if not isinstance(state.get("custom_recipes"), dict):
        state["custom_recipes"] = {}
    if state.get("sign_style") not in SIGN_STYLES:
        state["sign_style"] = "温馨"
    return state

def ensure_started(state):
    migrate_state(state)
    if not state.get("shop_name"):
        state["shop_name"] = extra.get("shop_name") or "午间汉堡铺"
        state["chef_name"] = extra.get("chef_name") or "AI主厨"
        style = extra.get("sign_style") or "温馨"
        if style not in SIGN_STYLES:
            style = "温馨"
        state["sign_style"] = style
        state["journal"].append(f"{state['shop_name']}开张，主厨{state['chef_name']}。")
    if not state.get("current_orders"):
        game.generate_day(state)
    game.save(state)

def check_order(state):
    o = game.current_order(state)
    if not o:
        print("今天没有订单。")
        return
    result = game.evaluate(state, o)
    print("\n=== 当前制作预检 ===")
    print("订单：", " → ".join(o["required_layers"]))
    print("组装：", " → ".join(state.get("layers") or []) or "未开始")
    print("酱料：", state.get("sauces") or "无")
    print("已取出：", state.get("cooked") or "无")
    print(f"预估总分：{result['total']}/100")
    for detail in result["details"]:
        print("-", detail)

def plate_current(state):
    if not state.get("layers"):
        print("还没有组装汉堡。")
        return
    print("当前成品已准备出餐。v0.6 直接使用 serve 交付。")

def show_tray(state):
    print("\n=== 当前工作台 ===")
    print("已取出：", state.get("cooked") or "无")
    print("组装：", " → ".join(state.get("layers") or []) or "未开始")
    print("酱料：", state.get("sauces") or "无")

def undo_build(state):
    state["layers"] = []
    print("已撤销当前组装。")

def clear_sauce(state):
    state["sauces"] = {}
    print("已清空酱料。")

def discard_slot(state, raw_slot):
    slot = str(raw_slot)
    if slot in state.get("grill", {}) and state["grill"].get(slot):
        item = state["grill"][slot]
        state["grill"][slot] = None
        print(f"已丢弃{slot}号烤位的{item['protein']}。")
        return
    if slot.isdigit():
        idx = int(slot) - 1
        cooked = state.get("cooked") or []
        if 0 <= idx < len(cooked):
            item = cooked.pop(idx)
            print(f"已丢弃已取出食材：{item.get('protein','未知食材')}。")
            return
    print("没有找到可丢弃的烤位或已取出食材。")

def customer_profile(state, name):
    target = next((c for c in game.CUSTOMERS if c.get("name") == name), None)
    if not target:
        print("未找到该顾客。")
        return
    print(f"\n=== 顾客档案：{target['name']} ===")
    print("耐心：", target.get("patience"))
    print("重点：", target.get("focus"))
    print("喜欢：", "、".join(target.get("likes") or []) or "暂无")
    print("忌口：", "、".join(target.get("dislikes") or []) or "暂无")
    if target.get("archetype"):
        print("类型：", target["archetype"])

def show_history(state):
    print("\n=== 经营记录 ===")
    if state.get("weekly_history"):
        print("周记录：")
        for item in state["weekly_history"][-5:]:
            print("-", item)
    if state.get("journal"):
        print("最近日志：")
        for line in state["journal"][-8:]:
            print("-", line)
    if not state.get("weekly_history") and not state.get("journal"):
        print("暂无记录。")

def create_recipe(state, name):
    if not state.get("layers"):
        print("当前没有组装内容，无法保存自创菜单。")
        return
    state.setdefault("custom_recipes", {})[name] = {
        "layers": list(state.get("layers") or []),
        "sauces": dict(state.get("sauces") or {}),
    }
    print(f"已保存自创菜单：{name}")

def test_recipe(state, name):
    recipe = state.get("custom_recipes", {}).get(name)
    if not recipe:
        print("未找到该自创菜单。")
        return
    old_layers = list(state.get("layers") or [])
    old_sauces = dict(state.get("sauces") or {})
    state["layers"] = list(recipe.get("layers") or [])
    state["sauces"] = dict(recipe.get("sauces") or {})
    check_order(state)
    state["layers"] = old_layers
    state["sauces"] = old_sauces

def show_recipes(state):
    recipes = state.get("custom_recipes") or {}
    if not recipes:
        print("暂无自创菜单。")
        return
    print("\n=== 自创菜单 ===")
    for name, recipe in recipes.items():
        print(f"- {name}：{' → '.join(recipe.get('layers') or [])}｜酱料：{recipe.get('sauces') or '无'}")

def set_strategy(state, raw):
    key = raw.strip()
    aliases = {"均衡":"balanced", "剧情":"story", "利润":"profit", "口碑":"reputation", "速度":"speed"}
    if key in game.STRATEGIES:
        state["auto_strategy"] = key if key in {"balanced", "story", "profit", "reputation", "speed"} else aliases.get(key, "balanced")
        print("自动策略：", state["auto_strategy"])
    else:
        print("可用策略：balanced/profit/story/reputation/speed")

def set_difficulty(state, raw):
    diff = raw.strip()
    if diff in game.DIFFICULTIES:
        state["difficulty"] = diff
        print(f"难度已设置为：{diff}｜{game.DIFFICULTIES[diff]['desc']}")
    else:
        print("可用难度：普通、忙碌、地狱午高峰")

def run_auto(state, parts):
    sub = parts[1].lower() if len(parts) >= 2 else ""
    if sub == "on":
        state["auto_mode"] = True
        print("自动模式已开启。周结算会自动买推荐装修并选择目标。")
    elif sub == "off":
        state["auto_mode"] = False
        print("自动模式已关闭。")
    elif sub == "summary":
        game.auto_summary(state)
    elif sub == "order":
        game.auto_run_orders(state, int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1)
    elif sub == "day":
        game.auto_run_days(state, int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else 1)
    else:
        print("auto 支持：on/off/summary/order N/day N")

def serve_noninteractive(state):
    old_auto = state.get("auto_mode", False)
    weekly_boundary = state.get("day", 1) % 7 == 0 and state.get("order_pointer", 0) >= len(state.get("current_orders", [])) - 1
    if weekly_boundary and not old_auto:
        state["auto_mode"] = True
        print("平台 cmd 模式：周结算使用官方自动装修与目标选择，避免交互输入。")
    game.serve(state)
    if weekly_boundary and not old_auto:
        state["auto_mode"] = old_auto
        game.save(state)

def dispatch(state, raw):
    parts = raw.split()
    if not parts:
        game.status(state)
        return
    cmd = parts[0].lower()
    if cmd == "status":
        game.status(state)
    elif cmd == "orders":
        game.show_order(state)
    elif cmd == "accept":
        state["current_order_accepted"] = True
        print("已接单。")
    elif cmd == "grill" and len(parts) == 2:
        game.grill_item(state, parts[1])
    elif cmd == "flip" and len(parts) == 2:
        game.flip(state, parts[1])
    elif cmd == "wait":
        game.wait_turn(state)
    elif cmd == "take" and len(parts) == 2:
        game.take(state, parts[1])
    elif cmd == "build" and len(parts) > 1:
        game.build(state, parts[1:])
    elif cmd == "sauce" and len(parts) == 3:
        game.add_sauce(state, parts[1], parts[2])
    elif cmd == "check":
        check_order(state)
    elif cmd == "plate":
        plate_current(state)
    elif cmd == "tray":
        show_tray(state)
    elif cmd == "undo" and len(parts) == 2 and parts[1].lower() == "build":
        undo_build(state)
    elif cmd == "clear" and len(parts) == 2 and parts[1].lower() == "sauce":
        clear_sauce(state)
    elif cmd == "discard" and len(parts) == 2:
        discard_slot(state, parts[1])
    elif cmd == "customer" and len(parts) >= 2:
        customer_profile(state, " ".join(parts[1:]))
    elif cmd == "history":
        show_history(state)
    elif cmd == "create" and len(parts) >= 2:
        create_recipe(state, " ".join(parts[1:]))
    elif cmd == "test" and len(parts) >= 2:
        test_recipe(state, " ".join(parts[1:]))
    elif cmd == "recipes":
        show_recipes(state)
    elif cmd == "serve":
        serve_noninteractive(state)
        if state["order_pointer"] < len(state["current_orders"]):
            game.show_order(state)
    elif cmd == "renovate" and len(parts) == 1:
        game.renovation_list(state)
    elif cmd == "renovate" and len(parts) >= 2 and parts[1].lower() == "list":
        game.renovation_list(state)
    elif cmd == "renovate" and len(parts) >= 3 and parts[1].lower() == "buy":
        game.buy_renovation_by_choice(state, " ".join(parts[2:]))
    elif cmd == "goal" and len(parts) >= 2:
        game.set_weekly_goal(state, " ".join(parts[1:]))
    elif cmd == "strategy" and len(parts) >= 2:
        set_strategy(state, " ".join(parts[1:]))
    elif cmd == "difficulty" and len(parts) >= 2:
        set_difficulty(state, " ".join(parts[1:]))
    elif cmd == "auto" and len(parts) >= 2:
        run_auto(state, parts)
    elif cmd == "help":
        print(game.HELP)
    elif cmd == "save":
        game.save(state)
        print("已保存。")
    else:
        print("无法识别。输入 help 查看完整指令。")

if payload.get("reset"):
    try:
        game.SAVE_FILE.unlink()
    except FileNotFoundError:
        pass

random.seed(str(game.SAVE_FILE) + ":" + command)
state = game.fresh_state() if payload.get("reset") else game.load()
ensure_started(state)

buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    if payload.get("reset"):
        print(f"欢迎来到「{state['shop_name']}」。")
        game.show_order(state)
    else:
        for raw in [p.strip() for p in command.replace("\n", ";").split(";") if p.strip()]:
            if ";" in command or "\n" in command:
                print(f"▶ {raw}")
            dispatch(state, raw)
            game.save(state)

print(buf.getvalue(), end="")
'''


GAME = VendorCmdGame("burger", "vendor/noon-burger-shop", RUNNER_CODE)


def save_summary(player_id):
    """给平台 my_saves 用：读存档提取店名/天数/金币/口碑，无档或解析失败返回 None。"""
    path = SAVE_ROOT / "burger" / require_player_id(player_id) / "save.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "shop_name": state.get("shop_name"),
        "day": state.get("day"),
        "week": state.get("week"),
        "coins": state.get("coins"),
        "reputation": state.get("reputation"),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "burger_new"}:
        extra = {
            "shop_name": arguments.get("shop_name"),
            "chef_name": arguments.get("chef_name"),
            "sign_style": arguments.get("sign_style"),
        }
        text = GAME.run(player_id, "status", reset=True, extra=extra)
    elif action in {"cmd", "burger_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 burger action")
    return {"game": "burger", "player_id": player_id, "text": text}
