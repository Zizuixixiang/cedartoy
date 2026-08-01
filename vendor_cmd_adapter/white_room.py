import json

from .base import (
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    import_json_saves,
    require_player_id,
    require_save_confirm,
)


SAVE_NAME = "_ewr_save.json"
BACKUP_NAME = "_ewr_save.backup.json"
SAVE_FILES = {
    SAVE_NAME: SAVE_NAME,
    BACKUP_NAME: BACKUP_NAME,
}


RUNNER_CODE = r'''
import json
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = Path(payload["vendor_dir"])
command = (payload.get("command") or "status").strip()
extra = payload.get("extra") or {}

sys.path.insert(0, str(vendor_dir))
import engine

# Upstream currently derives these paths from engine.py's directory. CedarToy
# runs one short-lived process per command, so bind all generated files to the
# locked player save directory before loading any state.
engine.SAVE_PATH = str(save_dir / "_ewr_save.json")
engine.REPORT_PATH = str(save_dir / "_ewr_report.txt")
engine._STATE = None
engine._init_state()

if payload.get("reset"):
    result = engine.new_game(extra.get("mode") or "standard")
else:
    result = engine.cmd(command)

print(result, end="")
'''


GAME = VendorCmdGame(
    "white_room",
    "vendor/echoing-white-room",
    RUNNER_CODE,
)


def _save_dir(player_id):
    return SAVE_ROOT / "white_room" / require_player_id(player_id)


def _save_path(player_id):
    return _save_dir(player_id) / SAVE_NAME


def _has_save(player_id):
    root = _save_dir(player_id)
    return any((root / filename).exists() for filename in SAVE_FILES)


def save_summary(player_id):
    try:
        state = json.loads(_save_path(player_id).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    return {
        "turn": state.get("total_inputs"),
        "module": state.get("module"),
        "mode": state.get("mode"),
        "route": state.get("route"),
        "ending": state.get("ending"),
    }


def _normalize_mode(value):
    mode = str(value or "standard").strip().lower()
    aliases = {
        "标准": "standard",
        "标准模式": "standard",
        "long": "echo",
        "长篇": "echo",
        "长篇模式": "echo",
        "回响": "echo",
        "回响模式": "echo",
    }
    mode = aliases.get(mode, mode)
    if mode not in {"standard", "echo"}:
        raise VendorCmdError("mode 支持 standard/echo（标准模式/长篇模式）")
    return mode


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "white_room_new"}:
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "白房间",
        )
        mode = _normalize_mode(arguments.get("mode"))
        text = GAME.run(
            player_id,
            "status",
            reset=True,
            extra={"mode": mode},
        )
    elif action in {"cmd", "white_room_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        if command.strip().lower() in {"restart confirm", "确认重置", "__debug_reset"}:
            require_save_confirm(
                arguments,
                lambda: _has_save(player_id),
                save_summary,
                "白房间",
            )
        text = GAME.run(player_id, command)
    elif action == "export":
        text = export_json_saves(
            "white_room",
            player_id,
            SAVE_FILES,
            packaged=True,
        )
    elif action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "白房间",
        )
        text = import_json_saves(
            "white_room",
            player_id,
            arguments.get("save_data"),
            SAVE_FILES,
            packaged=True,
        )
    else:
        raise VendorCmdError("未知 white_room action")
    return {"game": "white_room", "player_id": player_id, "text": text}
