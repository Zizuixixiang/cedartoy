import json
from datetime import datetime
from zoneinfo import ZoneInfo

from .base import (
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    parse_import_save_data,
    require_player_id,
    require_save_confirm,
)


SAVE_NAME = "forest_save.json"
SAVE_FILES = {SAVE_NAME: SAVE_NAME}
DAILY_SEMANTIC = "completed_endings_v1"


class ForestConflictError(VendorCmdError):
    pass


RUNNER_CODE = r'''
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
root_dir = Path(payload["vendor_dir"]).resolve().parents[1]
sys.path.insert(0, str(root_dir))

from vendor_cmd_adapter.forest_runtime import run_payload

try:
    print(run_payload(payload), end="")
except ValueError as exc:
    print(str(exc), file=sys.stderr)
    raise SystemExit(2)
'''


GAME = VendorCmdGame(
    "forest",
    "vendor/mo-yao-play-games",
    RUNNER_CODE,
)


def _save_path(player_id):
    return SAVE_ROOT / "forest" / require_player_id(player_id) / SAVE_NAME


def _has_save(player_id):
    return _save_path(player_id).exists()


def save_summary(player_id):
    path = _save_path(player_id)
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    souvenirs = state.get("souvenirs")
    daily = state.get("daily")
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    daily_count = (
        daily.get("count", 0)
        if isinstance(daily, dict)
        and daily.get("date") == today
        and daily.get("semantic") == DAILY_SEMANTIC
        else 0
    )
    return {
        "current_line": state.get("current_line"),
        "current_scene": state.get("current_scene"),
        "souvenirs": len(souvenirs) if isinstance(souvenirs, list) else 0,
        "daily_lines": daily_count,
        "total_lines": state.get("total_lines_started", 0),
        "updated_at": state.get("updated_at"),
    }


def play(arguments):
    action = str(arguments.get("action") or "status").strip().lower()
    player_id = arguments.get("player_id")

    if action in {"new", "reset"}:
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "forest",
        )
        text = GAME.run(
            player_id,
            "",
            reset=True,
            extra={"action": action},
        )
    elif action == "lines":
        text = GAME.run(player_id, "", extra={"action": action})
    elif action == "start":
        line = arguments.get("line")
        if isinstance(line, bool) or not isinstance(line, (str, int)):
            raise VendorCmdError("line 参数必填，使用 1-11 的线号")
        text = GAME.run(
            player_id,
            "",
            extra={"action": action, "line": str(line)},
        )
    elif action == "choose":
        option = arguments.get("option")
        if not isinstance(option, str) or not option.strip():
            raise VendorCmdError("option 参数必填")
        text = GAME.run(
            player_id,
            "",
            extra={
                "action": action,
                "option": option.strip(),
                "observation": arguments.get("observation"),
            },
        )
    elif action == "observe":
        content = arguments.get("content")
        if not isinstance(content, str) or not content.strip():
            raise VendorCmdError("content 参数必填")
        text = GAME.run(
            player_id,
            "",
            extra={"action": action, "content": content},
        )
    elif action == "status":
        text = GAME.run(player_id, "", extra={"action": action})
    elif action == "export":
        text = export_json_saves("forest", player_id, SAVE_FILES)
    elif action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "forest",
        )
        state = parse_import_save_data(arguments.get("save_data"))
        text = GAME.run(
            player_id,
            "",
            extra={"action": action, "state": state},
        )
    else:
        raise VendorCmdError(
            "未知 forest action；可用 lines / new / reset / start / choose / observe / status / export / import"
        )
    return {"game": "forest", "player_id": player_id, "text": text}


def web_state(player_id, *, player_name=None, ai_name=None):
    text = GAME.run(
        player_id,
        "",
        extra={
            "action": "web_state",
            "player_name": player_name,
            "ai_name": ai_name,
        },
    )
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise VendorCmdError("forest 网页状态返回格式无效") from None


def web_action(player_id, action, *, expected_revision, player_name, ai_name, **params):
    extra = {
        "action": "web_action",
        "web_action": action,
        "expected_revision": expected_revision,
        "player_name": player_name,
        "ai_name": ai_name,
        **params,
    }
    try:
        text = GAME.run(player_id, "", extra=extra)
    except VendorCmdError as exc:
        message = str(exc)
        marker = "FOREST_CONFLICT:"
        if marker in message:
            raise ForestConflictError(message.split(marker, 1)[1]) from None
        raise
    try:
        return json.loads(text)
    except (TypeError, json.JSONDecodeError):
        raise VendorCmdError("forest 网页动作返回格式无效") from None
