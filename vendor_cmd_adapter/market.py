import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


RUNNER_CODE = r'''
import json
import os
import re
import sys

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = (payload.get("command") or "状态").strip()
command = re.sub(r'[\u3000\u00A0\u2002\u2003\u2009\u200A\uFEFF]+', ' ', command)
extra = payload.get("extra") or {}

sys.path.insert(0, vendor_dir)
import market_engine

market_engine.SAVE_FILE = os.path.join(save_dir, "market_save.json")
market_engine._game = None

if payload.get("reset"):
    try:
        os.remove(market_engine.SAVE_FILE)
    except FileNotFoundError:
        pass
    seed = extra.get("seed")
    if seed is None:
        print(market_engine.new_game(), end="")
    else:
        print(market_engine.new_game(int(seed)), end="")
else:
    print(market_engine.cmd(command), end="")
'''


GAME = VendorCmdGame("market", "vendor/shangzhuochifan", RUNNER_CODE)


def save_summary(player_id):
    path = SAVE_ROOT / "market" / require_player_id(player_id) / "market_save.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "day": state.get("day"),
        "season": state.get("season"),
        "budget": state.get("budget"),
        "spent": state.get("spent"),
        "done": state.get("done"),
        "fridge": len(state.get("fridge") or []),
        "basket": len(state.get("basket") or []),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "market_new"}:
        require_save_confirm(arguments, lambda: (SAVE_ROOT / "market" / require_player_id(player_id) / "market_save.json").exists(), save_summary, "market")
        extra = {}
        if arguments.get("seed") is not None:
            extra["seed"] = arguments.get("seed")
        text = GAME.run(player_id, "状态", reset=True, extra=extra)
    elif action in {"cmd", "market_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 market action")
    return {"game": "market", "player_id": player_id, "text": text}
