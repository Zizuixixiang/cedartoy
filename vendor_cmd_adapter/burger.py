from .base import VendorCmdError, VendorCmdGame


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

def adapter_renovation_shop(state):
    print("周结算进入装修环节；平台 cmd 模式暂不打开交互式装修店。")
    affordable = [
        f"{name}({info['cost']})"
        for name, info in game.RENOVATIONS.items()
        if not state.get("renovations", {}).get(name)
    ]
    if affordable:
        print("可在后续版本接入购买；当前可选项目：", "、".join(affordable))

def adapter_choose_weekly_goal(state):
    state["weekly_goal"] = state.get("weekly_goal") or "均衡经营"
    print("下一周目标：", state["weekly_goal"])

game.renovation_shop = adapter_renovation_shop
game.choose_weekly_goal = adapter_choose_weekly_goal

def ensure_started(state):
    if not state.get("shop_name"):
        state["shop_name"] = extra.get("shop_name") or "午间汉堡铺"
        state["chef_name"] = extra.get("chef_name") or "AI主厨"
        style = extra.get("sign_style") or "温馨"
        if style not in game.SIGN_STYLE_EFFECTS:
            style = "温馨"
        state["sign_style"] = style
        state["journal"].append(f"{state['shop_name']}开张，主厨{state['chef_name']}。")
    if not state.get("current_orders"):
        game.generate_day(state)
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
        game.check_order(state)
    elif cmd == "plate":
        game.plate_current(state)
    elif cmd == "tray":
        game.show_tray(state)
    elif cmd == "undo" and len(parts) == 2 and parts[1].lower() == "build":
        game.undo_build(state)
    elif cmd == "clear" and len(parts) == 2 and parts[1].lower() == "sauce":
        game.clear_sauce(state)
    elif cmd == "discard" and len(parts) == 2:
        game.discard_slot(state, parts[1])
    elif cmd == "customer" and len(parts) >= 2:
        game.customer_profile(state, " ".join(parts[1:]))
    elif cmd == "history":
        game.show_history(state)
    elif cmd == "create" and len(parts) >= 2:
        game.create_recipe(state, " ".join(parts[1:]))
    elif cmd == "test" and len(parts) >= 2:
        game.test_recipe(state, " ".join(parts[1:]))
    elif cmd == "recipes":
        game.show_recipes(state)
    elif cmd == "serve":
        game.serve(state)
        if state["order_pointer"] < len(state["current_orders"]):
            game.show_order(state)
    elif cmd == "renovate":
        game.renovation_shop(state)
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
