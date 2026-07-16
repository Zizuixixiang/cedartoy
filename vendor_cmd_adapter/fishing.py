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
command = (payload.get("command") or "status").strip()
command = re.sub(r'[\u3000\u00A0\u2002\u2003\u2009\u200A\uFEFF]+', ' ', command)

sys.path.insert(0, vendor_dir)
os.chdir(save_dir)
import fishing
fishing._SAVE = os.path.join(save_dir, "fishing_save.json")
fishing.S = None
try:
    import engine
    engine._SAVE = fishing._SAVE
    engine.S = None
except Exception:
    pass

reset = payload.get("reset")
if reset:
    try:
        os.remove(os.path.join(save_dir, "fishing_save.json"))
    except FileNotFoundError:
        pass

extra = payload.get("extra") or {}
if reset:
    seed = extra.get("seed")
    if seed is None:
        print(fishing.new_game(), end="")
    else:
        print(fishing.new_game(seed), end="")
elif command == "__import__":
    raw = extra.get("save_data")
    if isinstance(raw, str):
        data = json.loads(raw)
    else:
        data = raw
    with open(os.path.join(save_dir, "fishing_save.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    print(fishing.cmd("status"), end="")
else:
    print(fishing.cmd(command), end="")
'''


GAME = VendorCmdGame("fishing", "vendor/ai-fishing-game", RUNNER_CODE)


def save_summary(player_id):
    """给平台 my_saves 用：读存档提取回合/点数/图鉴数，无档或解析失败返回 None。"""
    path = SAVE_ROOT / "fishing" / require_player_id(player_id) / "fishing_save.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    stats = state.get("stats") or {}
    return {
        "turn": state.get("turn"),
        "points": state.get("points"),
        "encyclopedia": len(state.get("encyclopedia") or {}),
        "total_casts": stats.get("total_casts"),
    }


def _has_save(player_id):
    return (SAVE_ROOT / "fishing" / require_player_id(player_id) / "fishing_save.json").exists()


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "fishing_new"}:
        require_save_confirm(arguments, lambda: _has_save(player_id), save_summary, "fishing")
        seed = arguments.get("seed")
        text = GAME.run(player_id, "", reset=True, extra={"seed": seed})
    elif action in {"cmd", "fishing_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = GAME.run(player_id, command)
    elif action == "import":
        require_save_confirm(arguments, lambda: _has_save(player_id), save_summary, "fishing")
        save_data = arguments.get("save_data")
        if save_data is None:
            raise VendorCmdError("save_data 必填")
        if isinstance(save_data, str):
            try:
                parsed = json.loads(save_data)
            except (json.JSONDecodeError, ValueError):
                raise VendorCmdError("save_data 不是合法 JSON 字符串")
            if not isinstance(parsed, dict):
                raise VendorCmdError("save_data 必须是 JSON 对象")
        elif isinstance(save_data, dict):
            pass
        else:
            raise VendorCmdError("save_data 必须是 JSON 对象或 JSON 字符串")
        serialized = json.dumps(save_data, ensure_ascii=False)
        if len(serialized.encode("utf-8")) > 128 * 1024:
            raise VendorCmdError("save_data 序列化后超过 128KB")
        text = GAME.run(player_id, "__import__", extra={"save_data": save_data})
    else:
        raise VendorCmdError("未知 fishing action")
    return {"game": "fishing", "player_id": player_id, "text": text}
