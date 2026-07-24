from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


SAVE_NAME = "moonlit_v3_save.json"


RUNNER_CODE = r'''
import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = (payload.get("command") or "状态").strip()

os.chdir(save_dir)
sys.path.insert(0, vendor_dir)

if payload.get("reset"):
    try:
        os.remove(os.path.join(save_dir, "moonlit_v3_save.json"))
    except FileNotFoundError:
        pass

import moonlit_cards
moonlit_cards.SAVE_PATH = Path(save_dir) / "moonlit_v3_save.json"

result = moonlit_cards.cmd("开始" if payload.get("reset") else command)
print(result, end="")
'''


GAME = VendorCmdGame(
    "moonlit",
    "vendor/moonlit-myriad",
    RUNNER_CODE,
    timeout=60,
)


def _save_path(player_id):
    return SAVE_ROOT / "moonlit" / require_player_id(player_id) / SAVE_NAME


def save_summary(player_id):
    """只报告存档存在，不读取卡牌游戏的存档内容。"""
    if not _save_path(player_id).exists():
        return None
    return {"saved": True}


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "moonlit_new"}:
        require_save_confirm(
            arguments,
            lambda: _save_path(player_id).exists(),
            save_summary,
            "moonlit",
        )
        text = GAME.run(player_id, "开始", reset=True)
    elif action in {"cmd", "moonlit_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 moonlit action")
    return {"game": "moonlit", "player_id": player_id, "text": text}
