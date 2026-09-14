import fcntl
import hashlib
import shutil

from command_text import normalize_command_spaces

from .base import (
    SAVE_ROOT,
    VendorCmdError,
    VendorCmdGame,
    export_json_saves,
    import_json_saves,
    require_player_id,
    require_save_confirm,
)


SAVE_NAME = "moonlit_v3_save.json"
SAVE_FILES = {
    SAVE_NAME: SAVE_NAME,
    f"{SAVE_NAME}.bak": f"{SAVE_NAME}.bak",
}
VIEW_RELATIVE_PATH = ".view/月幕万象.html"


RUNNER_CODE = r'''
import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = payload["save_dir"]
vendor_dir = payload["vendor_dir"]
command = (payload.get("command") or "状态").strip()
extra = payload.get("extra") or {}

os.chdir(save_dir)
sys.path.insert(0, vendor_dir)

view_path = Path(extra["view_path"]) if extra.get("view_path") else None
if extra.get("invalidate_view") and view_path is not None:
    try:
        view_path.unlink()
    except FileNotFoundError:
        pass

if payload.get("reset"):
    for name in ("moonlit_v3_save.json", "moonlit_v3_save.json.bak"):
        try:
            os.remove(os.path.join(save_dir, name))
        except FileNotFoundError:
            pass

if extra.get("render_view") and not (Path(save_dir) / "moonlit_v3_save.json").is_file():
    raise RuntimeError("还没有月幕万象存档，请先开局")

import moonlit_cards
moonlit_cards.SAVE_PATH = Path(save_dir) / "moonlit_v3_save.json"

if extra.get("render_view"):
    if view_path is None:
        raise RuntimeError("月幕牌桌输出路径未配置")
    view_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_view = view_path.with_name(
        "." + view_path.name + "." + str(os.getpid()) + ".tmp.html"
    )
    try:
        result = moonlit_cards.cmd("牌桌 " + str(temporary_view))
        if not temporary_view.is_file():
            raise RuntimeError("月幕牌桌没有生成 HTML")
        os.replace(temporary_view, view_path)
    finally:
        try:
            temporary_view.unlink()
        except FileNotFoundError:
            pass
    state_lines = [
        line for line in str(result).splitlines() if line.startswith("[STATE]")
    ]
    result = "🖥 月幕万象牌桌快照已更新。人类可从 CedarToy 首页「围观牌桌」进入。"
    if state_lines:
        result += "\n" + state_lines[-1]
else:
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


def _view_path(player_id):
    return _save_path(player_id).parent / VIEW_RELATIVE_PATH


def _has_save(player_id):
    save_dir = _save_path(player_id).parent
    return any((save_dir / filename).exists() for filename in SAVE_FILES)


def save_summary(player_id):
    """只报告存档存在，不读取卡牌游戏的存档内容。"""
    if not _save_path(player_id).exists():
        return None
    return {"saved": True, "table_ready": _view_path(player_id).is_file()}


def read_table(player_id):
    """在玩家锁内读取最近一次生成的牌桌；不读取或解析游戏存档。"""
    player_id = require_player_id(player_id)
    save_dir = _save_path(player_id).parent
    if not save_dir.is_dir():
        return None
    lock_path = save_dir / ".lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
            if not _save_path(player_id).is_file():
                return None
            try:
                body = _view_path(player_id).read_bytes()
            except FileNotFoundError:
                return None
    except FileNotFoundError:
        return None
    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    return {"body": body, "etag": etag}


def delete_save(player_id):
    """在同一玩家锁内删除整槽存档及派生牌桌，避免并发生成把快照复活。"""
    player_id = require_player_id(player_id)
    save_dir = _save_path(player_id).parent
    if not save_dir.is_dir():
        return False
    lock_path = save_dir / ".lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                shutil.rmtree(save_dir)
            except FileNotFoundError:
                return False
    except FileNotFoundError:
        return False
    return True


def _render_table(player_id):
    player_id = require_player_id(player_id)
    if not _has_save(player_id):
        raise VendorCmdError("还没有月幕万象存档，请先开局")
    return GAME.run(
        player_id,
        "",
        extra={
            "render_view": True,
            "view_path": str(_view_path(player_id)),
        },
    )


def play(arguments):
    action = (arguments.get("action") or "cmd").strip()
    player_id = arguments.get("player_id")
    if action in {"new", "moonlit_new"}:
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "moonlit",
        )
        text = GAME.run(
            player_id,
            "开始",
            reset=True,
            extra={
                "invalidate_view": True,
                "view_path": str(_view_path(player_id)),
            },
        )
    elif action in {"table", "moonlit_table"}:
        unexpected = set(arguments) - {"action", "player_id"}
        if unexpected:
            raise VendorCmdError("table 不接受输出路径或其他额外参数")
        text = _render_table(player_id)
    elif action in {"cmd", "moonlit_cmd"}:
        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            raise VendorCmdError("command 参数必填")
        normalized = normalize_command_spaces(command)
        if normalized == "牌桌":
            unexpected = set(arguments) - {"action", "player_id", "command"}
            if unexpected:
                raise VendorCmdError("牌桌命令不接受输出路径或其他额外参数")
            text = _render_table(player_id)
        elif normalized.startswith("牌桌"):
            raise VendorCmdError(
                "CedarToy 不允许为牌桌指定输出路径或额外参数；请使用 action=\"table\""
            )
        else:
            text = GAME.run(player_id, command)
    elif action == "export":
        text = export_json_saves(
            "moonlit",
            player_id,
            SAVE_FILES,
            packaged=True,
        )
    elif action == "import":
        require_save_confirm(
            arguments,
            lambda: _has_save(player_id),
            save_summary,
            "moonlit",
        )
        text = import_json_saves(
            "moonlit",
            player_id,
            arguments.get("save_data"),
            SAVE_FILES,
            packaged=True,
            invalidate_files=(VIEW_RELATIVE_PATH,),
        )
    else:
        raise VendorCmdError("未知 moonlit action")
    return {"game": "moonlit", "player_id": player_id, "text": text}
