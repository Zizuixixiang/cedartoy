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
command = (payload.get("command") or "look").strip()
command = re.sub(r'[\u3000\u00A0\u2002\u2003\u2009\u200A\uFEFF]+', ' ', command)

sys.path.insert(0, vendor_dir)
os.environ["RANDOM_IMITATOR_TD_SAVE"] = os.path.join(save_dir, "random_imitator_td_save.json")
os.environ["RANDOM_IMITATOR_TD_RECORDS"] = os.path.join(save_dir, "random_imitator_td_records.json")

from random_imitator_td import cmd

if payload.get("reset"):
    for name in ("random_imitator_td_save.json", "random_imitator_td_records.json"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

print(cmd(command), end="")
'''


GAME = VendorCmdGame("imitator_td", "vendor/random-imitator-td", RUNNER_CODE)


def save_summary(player_id):
    """给平台 my_saves 用：读存档提取关卡/回合/种子/结果，无档或解析失败返回 None。"""
    path = SAVE_ROOT / "imitator_td" / require_player_id(player_id) / "random_imitator_td_save.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    engine = state.get("engine") if isinstance(state.get("engine"), dict) else {}
    game_state = engine.get("state") if isinstance(engine.get("state"), dict) else {}
    return {
        "level": state.get("level"),
        "turn": state.get("turn"),
        "seed": state.get("seed"),
        "result": game_state.get("result") or "running",
        "tick": game_state.get("tick"),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "imitator_td_new"}:
        def _imitator_has_save():
            d = SAVE_ROOT / "imitator_td" / require_player_id(player_id)
            return any((d / f).exists() for f in ("random_imitator_td_save.json", "random_imitator_td_records.json"))
        require_save_confirm(arguments, _imitator_has_save, save_summary, "imitator_td")
        level = arguments.get("level")
        mode = str(arguments.get("mode") or "").strip()
        chaos = str(arguments.get("chaos") or "").strip()
        seed = arguments.get("seed")
        cards = arguments.get("cards")
        parts = ["new_game"]
        if mode:
            parts.append(f"mode={mode}")
        elif level is not None:
            parts.append(f"level={level}")
        if chaos:
            parts.append(f"chaos={chaos}")
        if seed is not None:
            parts.append(f"seed={seed}")
        if isinstance(cards, str) and cards.strip():
            parts.append(f"cards={cards.strip()}")
        text = GAME.run(player_id, " ".join(parts), reset=True)
    elif action in {"cmd", "imitator_td_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    else:
        raise VendorCmdError("未知 imitator_td action")
    return {"game": "imitator_td", "player_id": player_id, "text": text}
