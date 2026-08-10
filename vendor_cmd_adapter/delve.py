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


SAVE_NAME = "mine_v0221_6_2_save.json"
LEGACY_SAVE_NAMES = (
    "mine_v0221_6_1_save.json",
    "mine_v0221_6_save.json",
    "mine_v0221_5_save.json",
    "mine_v0221_4_save.json",
    "mine_v0221_3_save.json",
    "mine_v0221_2_save.json",
    "mine_v0221_1_save.json",
    "mine_v0221_save.json",
    "mine_v0220_save.json",
    "mine_v0218_save.json",
    "mine_v0217_save.json",
    "mine_v0216_save.json",
    "mine_v0215_save.json",
    "mine_v0214_save.json",
    "mine_v0213_save.json",
    "mine_v0212_save.json",
)
SAVE_FILES = {SAVE_NAME: SAVE_NAME}


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

delve.SAVE_FILE = os.path.join(save_dir, "mine_v0221_6_2_save.json")
legacy_names = list(payload.get("extra", {}).get("legacy_save_names") or [])
legacy_names.extend(os.path.basename(path) for path in delve.LEGACY_SAVE_FILES)
legacy_names = list(dict.fromkeys(
    name for name in legacy_names
    if name and name != os.path.basename(delve.SAVE_FILE)
))
delve.LEGACY_SAVE_FILES = [os.path.join(save_dir, name) for name in legacy_names]

if payload.get("reset"):
    for path in [delve.SAVE_FILE, *delve.LEGACY_SAVE_FILES]:
        try:
            os.remove(path)
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


def _legacy_save_paths(player_id):
    save_dir = SAVE_ROOT / "delve" / require_player_id(player_id)
    return tuple(save_dir / name for name in LEGACY_SAVE_NAMES)


def _candidate_save_paths(player_id):
    return (_save_path(player_id), *_legacy_save_paths(player_id))


def _has_save(player_id):
    return any(path.is_file() for path in _candidate_save_paths(player_id))


def _run(player_id, command, *, reset=False):
    return GAME.run(
        player_id,
        command,
        reset=reset,
        extra={"legacy_save_names": list(LEGACY_SAVE_NAMES)},
    )


def save_summary(player_id):
    """给平台 my_saves 用：读取下矿进度、金币和藏品等基本信息。"""
    state = None
    for path in _candidate_save_paths(player_id):
        try:
            candidate = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if isinstance(candidate, dict):
            state = candidate
            break
    if state is None:
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
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "delve",
        )
        text = _run(player_id, "new", reset=True)
    elif action == "cmd":
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        text = _run(player_id, command)
    elif action == "export":
        if not _save_path(player_id).is_file() and _has_save(player_id):
            _run(player_id, "status")
        text = export_json_saves("delve", player_id, SAVE_FILES)
    elif action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "delve",
        )
        text = import_json_saves(
            "delve",
            player_id,
            arguments.get("save_data"),
            SAVE_FILES,
        )
    else:
        raise VendorCmdError("未知 delve action")
    return {"game": "delve", "player_id": player_id, "text": text}
