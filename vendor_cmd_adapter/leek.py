import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


RUNNER_CODE = r'''
import json
import os
import sys

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = payload.get("command") or "status"

sys.path.insert(0, vendor_dir)
os.chdir(save_dir)
import leek

if payload.get("reset"):
    for name in ("leek_save.json", "leek_save.json.tmp", "leek_save.json.bak"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

print(leek.cmd(command), end="")
'''


GAME = VendorCmdGame("leek", "vendor/leek", RUNNER_CODE)


def save_summary(player_id):
    """给平台 my_saves 用：读存档提取天数/现金/职业，无档或解析失败返回 None。"""
    path = SAVE_ROOT / "leek" / require_player_id(player_id) / "leek_save.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "day": state.get("day"),
        "cash": state.get("cash"),
        "career": state.get("career"),
        "holdings": len(state.get("holdings") or {}),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "leek_new"}:
        require_save_confirm(arguments, lambda: (SAVE_ROOT / "leek" / require_player_id(player_id) / "leek_save.json").exists(), save_summary, "leek")
        seed = arguments.get("seed")
        career = str(arguments.get("career") or "").strip().lower()
        parts = ["new_game"]
        if career == "fund":
            parts.append("fund")
        if seed is not None:
            parts.append(str(seed))
        text = GAME.run(player_id, " ".join(parts), reset=True)
    elif action in {"cmd", "leek_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 leek action")
    return {"game": "leek", "player_id": player_id, "text": text}
