import json

from .base import SAVE_ROOT, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


COMMANDS = frozenset({
    "trip_plan",
    "trip_start",
    "trip_here",
    "trip_go",
    "trip_collect",
    "trip_postcard",
    "trip_diary",
    "trip_return",
    "care_checkin",
    "wallet_status",
    "trip_shelf",
})


RUNNER_CODE = r'''
import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
command = payload["command"]
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
reset = bool(payload.get("reset"))
extra = payload.get("extra") or {}

# travel_mcp derives all save paths at import time, so this must be set first.
os.environ["TRAVEL_HOME"] = save_dir
sys.path.insert(0, vendor_dir)
import travel_mcp

# VendorCmdGame already holds save_dir/.lock while this process runs. The
# upstream module uses that same filename, which would deadlock across the
# parent/child processes, so keep its own lock in a separate hidden directory.
travel_lock_dir = Path(save_dir) / ".travel"
travel_lock_dir.mkdir(exist_ok=True)
travel_mcp.LOCK_P = str(travel_lock_dir / "lock")

if reset:
    for path in Path(save_dir).glob("*.json"):
        path.unlink()

tool = getattr(travel_mcp, command)
kwargs = {
    key: value
    for key, value in extra.items()
    if key not in {"command", "action", "player_id"}
}
result = tool(**kwargs)
print(result, end="")
'''


GAME = VendorCmdGame("travel", "vendor/travel-mcp", RUNNER_CODE)


def _save_dir(player_id):
    return SAVE_ROOT / "travel" / require_player_id(player_id)


def _has_save(player_id):
    return any(_save_dir(player_id).glob("*.json"))


def save_summary(player_id):
    """给平台 my_saves 用：读取当前旅程的基本信息。"""
    try:
        state = json.loads((_save_dir(player_id) / "state.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "dest": state.get("dest"),
        "day": state.get("day"),
        "phase": state.get("phase"),
        "party": state.get("party"),
        "style": state.get("style"),
        "done": state.get("done"),
        "visited": len(state.get("visited") or []),
        "spent_usd": state.get("spent_usd"),
    }


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action == "new":
        require_save_confirm(arguments, lambda: _has_save(player_id), save_summary, "travel")
        plan = json.loads(GAME.run(player_id, "trip_plan", reset=True))
        text = json.dumps(
            {
                "welcome": "欢迎来到旅行 MCP。先挑一个想去的目的地，再用 trip_start 出发。",
                "plan": plan,
            },
            ensure_ascii=False,
            indent=1,
        )
    elif action == "cmd":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        command = command.strip()
        if command not in COMMANDS:
            raise VendorCmdError("未知 travel command")
        extra = {
            key: value
            for key, value in arguments.items()
            if key not in {"command", "action", "player_id"}
        }
        text = GAME.run(player_id, command, extra=extra)
    else:
        raise VendorCmdError("未知 travel action")
    return {"game": "travel", "player_id": player_id, "text": text}
