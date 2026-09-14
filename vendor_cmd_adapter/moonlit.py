import fcntl
import hashlib
import json
import logging
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

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


logger = logging.getLogger(__name__)


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

import moonlit_cards
moonlit_cards.SAVE_PATH = Path(save_dir) / "moonlit_v3_save.json"
result = moonlit_cards.cmd("开始" if payload.get("reset") else command)
print(result, end="")
'''


TABLE_RUNNER_CODE = r'''
import json
import os
import sys
from pathlib import Path

payload = json.load(sys.stdin)
save_dir = Path(payload["save_dir"])
vendor_dir = payload["vendor_dir"]
output_path = Path(payload["output_path"])

os.chdir(save_dir)
sys.path.insert(0, vendor_dir)

if not (save_dir / "moonlit_v3_save.json").is_file():
    raise RuntimeError("还没有月幕万象存档，请先开局")

import moonlit_cards
moonlit_cards.SAVE_PATH = save_dir / "moonlit_v3_save.json"
# 旧存档可能没有前端所需的命令日志；仅在临时副本中通过作者公开
# 命令补齐，再让牌桌渲染器读取。真实主档和备份不会暴露给这个进程。
moonlit_cards.cmd("状态")
result = moonlit_cards.cmd("牌桌 " + str(output_path))
if not output_path.is_file():
    raise RuntimeError("月幕牌桌没有生成 HTML")
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
    return {"saved": True}


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


def _render_snapshot_in_sandbox(save_dir):
    """只在临时副本中调用作者牌桌接口，返回 HTML 和作者命令输出。"""
    with tempfile.TemporaryDirectory(prefix="cedartoy-moonlit-view-") as raw_dir:
        sandbox = Path(raw_dir)
        for relative_path in dict.fromkeys(SAVE_FILES.values()):
            source = save_dir / relative_path
            if not source.is_file():
                continue
            target = sandbox / relative_path
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)

        output_path = sandbox / "月幕万象.html"
        payload = {
            "save_dir": str(sandbox),
            "vendor_dir": str(GAME.vendor_dir),
            "output_path": str(output_path),
        }
        environment = os.environ.copy()
        environment["PYTHONIOENCODING"] = "utf-8"
        try:
            process = subprocess.run(
                [sys.executable, "-c", TABLE_RUNNER_CODE],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                cwd=str(sandbox),
                env=environment,
                timeout=GAME.timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            logger.exception("moonlit sandbox renderer timed out for %s", save_dir)
            raise VendorCmdError("月幕牌桌生成超时，请稍后再试") from None
        if process.returncode != 0:
            detail = (process.stderr or process.stdout or "").strip()
            logger.error(
                "moonlit sandbox renderer failed for %s (exit=%s): %s",
                save_dir,
                process.returncode,
                detail or "no subprocess output",
            )
            raise VendorCmdError("月幕牌桌生成失败，请稍后刷新重试") from None
        try:
            body = output_path.read_bytes()
        except FileNotFoundError:
            raise VendorCmdError("月幕牌桌没有生成 HTML") from None
        if not body:
            raise VendorCmdError("月幕牌桌生成了空 HTML")
        return body, process.stdout.rstrip("\n")


def _atomic_replace_view(view_path, body):
    view_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{view_path.name}.",
        suffix=".tmp",
        dir=str(view_path.parent),
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as temporary_file:
            temporary_file.write(body)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, view_path)
    finally:
        try:
            temporary_path.unlink()
        except FileNotFoundError:
            pass


def ensure_table(player_id, *, force=False):
    """按真实存档 mtime 生成或复用快照；作者渲染器只接触临时副本。"""
    player_id = require_player_id(player_id)
    save_dir = _save_path(player_id).parent
    if not save_dir.is_dir():
        return None
    lock_path = save_dir / ".lock"
    try:
        with lock_path.open("a", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            main_save = _save_path(player_id)
            if not main_save.is_file():
                return None
            source_paths = [
                save_dir / relative_path
                for relative_path in dict.fromkeys(SAVE_FILES.values())
                if (save_dir / relative_path).is_file()
            ]
            newest_save_mtime = max(path.stat().st_mtime_ns for path in source_paths)
            view_path = _view_path(player_id)
            try:
                view_stat = view_path.stat()
                refresh = (
                    force
                    or view_stat.st_size == 0
                    or view_stat.st_mtime_ns < newest_save_mtime
                )
            except FileNotFoundError:
                refresh = True

            renderer_text = ""
            if refresh:
                body, renderer_text = _render_snapshot_in_sandbox(save_dir)
                _atomic_replace_view(view_path, body)
            else:
                body = view_path.read_bytes()
    except FileNotFoundError:
        return None

    etag = f'"{hashlib.sha256(body).hexdigest()[:16]}"'
    return {
        "body": body,
        "etag": etag,
        "refreshed": refresh,
        "renderer_text": renderer_text,
    }


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
    snapshot = ensure_table(player_id, force=True)
    if snapshot is None:
        raise VendorCmdError("还没有月幕万象存档，请先开局")
    result = "🖥 月幕万象牌桌快照已更新。人类可从 CedarToy 首页「围观牌桌」进入。"
    state_lines = [
        line
        for line in snapshot["renderer_text"].splitlines()
        if line.startswith("[STATE]")
    ]
    if state_lines:
        result += "\n" + state_lines[-1]
    return result


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
