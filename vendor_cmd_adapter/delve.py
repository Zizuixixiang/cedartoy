import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


SAVE_NAME = "mine_v0221_6_1_save.json"


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
import delve
delve.SAVE_FILE = os.path.join(save_dir, "mine_v0221_6_1_save.json")

if payload.get("reset"):
    try:
        os.remove(delve.SAVE_FILE)
    except FileNotFoundError:
        pass

result = delve.cmd(command)
if isinstance(result, str):
    print(result, end="")
else:
    print(json.dumps(result, ensure_ascii=False), end="")
'''


GAME = VendorCmdGame("delve", "vendor/delve-ai-companion", RUNNER_CODE)


def _save_path(player_id):
    return SAVE_ROOT / "delve" / require_player_id(player_id) / SAVE_NAME


def save_summary(player_id):
    """给平台 my_saves 用：读取下矿进度、金币和藏品等基本信息。"""
    try:
        state = json.loads(_save_path(player_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "turn": state.get("turn"),
        "coins": state.get("coins"),
        "trip": state.get("trip"),
        "max_depth_m": state.get("max_depth_m"),
        "collection_total_value": state.get("collection_total_value"),
        "current_title": state.get("current_title"),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action == "new":
        require_save_confirm(arguments, lambda: _save_path(player_id).exists(), save_summary, "delve")
        text = GAME.run(player_id, "new", reset=True)
    elif action == "cmd":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 delve action")
    return {"game": "delve", "player_id": player_id, "text": text}
