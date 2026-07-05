import fcntl
import json
import re

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id


MAX_BUY_AMOUNT = 500
ARCADE_SAVE_DEFAULT = {
    "chips": 0,
    "total_bought": 0,
    "total_cashed": 0,
    "winnings": 0,
    "visits": 0,
    "current_game": None,
    "owned": [],
    "equipped": [],
    "decor": [],
}


RUNNER_CODE = r'''
import json
import os
import sys

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = payload.get("command") or "help"

sys.path.insert(0, vendor_dir)
import arcade
import blackjack
import roulette
import slots

arcade._SAVE = os.path.join(save_dir, "arcade_save.json")
slots._SAVE = os.path.join(save_dir, "slots_save.json")
blackjack._SAVE = os.path.join(save_dir, "blackjack_save.json")
roulette._SAVE = os.path.join(save_dir, "roulette_save.json")

if payload.get("reset"):
    for name in ("arcade_save.json", "slots_save.json", "blackjack_save.json", "roulette_save.json"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

print(arcade.cmd(command), end="")
'''


GAME = VendorCmdGame("arcade", "vendor/claude-arcade", RUNNER_CODE)


def _save_dir(player_id, *, create=True):
    player_id = require_player_id(player_id)
    path = SAVE_ROOT / "arcade" / player_id
    if create:
        path.mkdir(parents=True, exist_ok=True)
    return path


def _load_state(save_path):
    try:
        with save_path.open("r", encoding="utf-8") as fh:
            state = json.load(fh)
    except FileNotFoundError:
        state = {}
    except json.JSONDecodeError:
        state = {}
    merged = dict(ARCADE_SAVE_DEFAULT)
    if isinstance(state, dict):
        merged.update(state)
    return merged


def _status_from_state(state):
    return {
        "chips": int(state.get("chips") or 0),
        "winnings": int(state.get("winnings") or 0),
        "total_bought": int(state.get("total_bought") or 0),
        "total_cashed": int(state.get("total_cashed") or 0),
    }


def status(player_id):
    save_dir = _save_dir(player_id, create=False)
    if not save_dir.exists():
        return _status_from_state({})
    lock_path = save_dir / ".lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        return _status_from_state(_load_state(save_dir / "arcade_save.json"))


def save_summary(player_id):
    """给平台 my_saves 用：有档返回筹码概况，无档返回 None。"""
    save_dir = _save_dir(player_id, create=False)
    if not (save_dir / "arcade_save.json").exists():
        return None
    return status(player_id)


def grant_chips(player_id, amount):
    try:
        amount = int(amount)
    except (TypeError, ValueError):
        raise VendorCmdError("筹码金额必须是整数")
    if amount < 1 or amount > MAX_BUY_AMOUNT:
        raise VendorCmdError(f"街机厅单次发放筹码必须在 1-{MAX_BUY_AMOUNT} 之间")

    save_dir = _save_dir(player_id)
    lock_path = save_dir / ".lock"
    save_path = save_dir / "arcade_save.json"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        state = _load_state(save_path)
        state["chips"] = int(state.get("chips") or 0) + amount
        state["total_bought"] = int(state.get("total_bought") or 0) + amount
        with save_path.open("w", encoding="utf-8") as fh:
            json.dump(state, fh, ensure_ascii=False)
        return _status_from_state(state)


def _guard_command(command):
    text = str(command or "").strip()
    match = re.match(r"^buy\s+(\d+)\b", text, re.IGNORECASE)
    if match:
        raise VendorCmdError("街机厅筹码只能由人类在网页端发放；让你的人类打开街机厅卡片，输入金额后发放。")
    return text


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "arcade_new"}:
        command = _guard_command(arguments.get("command") or "enter")
        text = GAME.run(player_id, command, reset=True)
    elif action in {"cmd", "arcade_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        command = _guard_command(command)
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 arcade action")
    return {"game": "arcade", "player_id": player_id, "text": text}
