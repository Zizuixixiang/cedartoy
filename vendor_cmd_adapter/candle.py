"""Candle v2.1: unchanged upstream commands, isolated and durable platform I/O."""

import copy
import hashlib
import importlib.util
import json
import os
import sqlite3
import tempfile
import time
import uuid
from pathlib import Path

import candle_feedback
from . import base
from .base import ROOT_DIR, VendorCmdError, VendorCmdGame, require_player_id, require_save_confirm


SOURCE_COMMIT = "c5d8fa8740a45d15f9a35cee67f7187942b1f5ae"
SOURCE_HASHES = {
    "candle_player.py": "6ed0f4a889d48efdbfc5ab527fe139994f3af8e77baa3483279ac6dae976b015",
    "candle_acts.json": "659c5762727d443b69598ab3b44da6370397700c0f0497d2a3e4d63f756d2604",
}
SAVE_NAME = "candle_save.json"
# Public statistics count actual saves, not lock-only player directories.
SAVE_FILES = {SAVE_NAME: SAVE_NAME}
PRIVATE_NOTICE = "【写给作者】选择与留言仅供本作作者查看，不公开给其他玩家。"
RUNNER_CODE = '''
import json, sys
payload = json.load(sys.stdin)
sys.path.insert(0, payload["extra"]["platform_root"])
from vendor_cmd_adapter.candle import run_locked
from vendor_cmd_adapter.base import VendorCmdError
try:
    print(run_locked(payload), end="")
except VendorCmdError as exc:
    print(str(exc), file=sys.stderr)
    sys.exit(1)
'''
GAME = VendorCmdGame("candle", "vendor/candle", RUNNER_CODE, environment={
    "LANG": "C.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
})


def feedback_db():
    return Path(os.getenv("SESSIONS_DB", ROOT_DIR / "data/sessions.db")).resolve()


def _path(player_id):
    return base.SAVE_ROOT / "candle" / require_player_id(player_id) / SAVE_NAME


def _atomic_save(path, data):
    fd, name = tempfile.mkstemp(prefix=".candle-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            json.dump(data, out, ensure_ascii=False, allow_nan=False)
            out.flush()
            os.fsync(out.fileno())
        os.replace(name, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def _load(path, *, preserve_corrupt=True):
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        state = data["state"]
        if not (data["format"] == 1 and isinstance(data["run_id"], str)
                and isinstance(data["pending"], list) and isinstance(state, dict)
                and type(state["act"]) is int and 1 <= state["act"] <= 13):
            raise ValueError("invalid save")
        chosen = state.get("chosen")
        if not (chosen is None or chosen == "abstain" or (type(chosen) is int and 1 <= chosen <= 13)):
            raise ValueError("invalid choice")
        if not ((state.get("comment") is None or isinstance(state["comment"], str))
                and isinstance(state.get("recalls"), list)):
            raise ValueError("invalid feedback")
        return data
    except (ValueError, UnicodeError, KeyError, TypeError):
        # Preserve the original in place AND a forensic copy; never silently reset.
        if preserve_corrupt:
            backup = path.with_name(path.name + ".corrupt-" + uuid.uuid4().hex)
            backup.write_bytes(path.read_bytes())
        raise VendorCmdError("《烛》存档损坏，原档及 .corrupt 副本已保留；请联系管理员恢复") from None


def _flush(path, data, db_path):
    if data and data["pending"]:
        # The outbox is committed with progress before DB delivery. Event IDs make
        # replay safe after a crash between SQLite commit and clearing the outbox.
        candle_feedback.append_events(db_path, data["pending"])
        data["pending"] = []
        _atomic_save(path, data)


def run_locked(payload):
    """Executed only under VendorCmdGame's per-player flock, in a clean process."""
    save_dir, vendor_dir = Path(payload["save_dir"]), Path(payload["vendor_dir"])
    path = save_dir / SAVE_NAME
    command, extra = payload["command"], payload["extra"]
    head = command.split(maxsplit=1)[0].lower()
    try:
        data = _load(path)
        _flush(path, data, extra["db_path"])
        if head in {"start", "reset", "清场"}:
            require_save_confirm(extra, lambda: data is not None, game_name="烛")
        if head in {"reset", "清场"}:
            # Preserve upstream reset semantics; start/new always persists act 1.
            if path.exists():
                path.unlink()
            return "上一场已清。蜡烛还热着，随时 start。"

        for name, expected in SOURCE_HASHES.items():
            if hashlib.sha256((vendor_dir / name).read_bytes()).hexdigest() != expected:
                raise VendorCmdError("《烛》源码版本变动，需重新审阅后启用")
        spec = importlib.util.spec_from_file_location("candle_player", vendor_dir / "candle_player.py")
        engine = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(engine)
        engine.BASE = str(save_dir)
        engine.SAVE_FILE = str(path)
        engine.COMMENTS_FILE = str(save_dir / "comments.jsonl")
        engine._file_lock = engine.FileLock(str(save_dir / ".upstream.lock"))

        previous = copy.deepcopy(data["state"]) if data else None
        current = copy.deepcopy(previous)
        changed = False
        commented = False

        def save(state):
            nonlocal current, changed
            current = copy.deepcopy(state)
            current["updated"] = int(time.time())
            changed = True

        def comment(state, text):
            nonlocal commented
            commented = True

        # Only storage hooks change. All scenes, validations and rendering remain
        # upstream code. No source file or shared author save is ever written.
        engine._load = lambda: copy.deepcopy(current)
        engine._save = save
        engine._log_comment = comment
        result = engine.cmd(command)
        if changed:
            if data is None or head == "start":
                data = {"format": 1, "run_id": uuid.uuid4().hex, "pending": []}
            data["state"] = current
            selected = head == "choose" and previous.get("chosen") is None and current.get("chosen") is not None
            if selected or commented:
                data["pending"].append({
                    "event_id": uuid.uuid4().hex, "run_id": data["run_id"],
                    "player_id": save_dir.name, "kind": "comment" if commented else "choose",
                    "chosen": 0 if current.get("chosen") == "abstain" else current.get("chosen"),
                    "comment": current.get("comment") if commented else None,
                    "created_at": int(time.time()), "source_commit": SOURCE_COMMIT,
                })
            _atomic_save(path, data)
            _flush(path, data, extra["db_path"])
        if head in {"start", "help", "帮助", "choose", "comment"} or (current and current["act"] == 13):
            result += "\n\n" + PRIVATE_NOTICE
        return result
    except (OSError, sqlite3.Error):
        raise VendorCmdError("《烛》存档或反馈写入失败；已保存的待入库反馈会在下次操作时重试，请勿删除存档") from None


def play(arguments):
    player_id = require_player_id(arguments.get("player_id"))
    action = arguments.get("action") or "status"
    if action == "cmd":
        command = arguments.get("command")
    elif action in {"new", "start", "next", "status", "help", "reset"}:
        command = "start" if action == "new" else action
    elif action in {"review", "recall", "choose"}:
        value = arguments.get("act", "")
        command = ("recall" if action == "review" else action) + " " + str(value)
    elif action == "comment":
        value = arguments.get("text")
        if not isinstance(value, str):
            raise VendorCmdError("text 必须是写给作者的文字")
        command = "comment " + value
    else:
        raise VendorCmdError("未知 candle action")
    if not isinstance(command, str) or not command.strip() or len(command) > 4096:
        raise VendorCmdError("command 必须是非空文字，最多 4096 字")
    head, *tail = command.strip().split(maxsplit=1)
    if head.lower() == "review":
        command = "recall" + (" " + tail[0] if tail else "")
    text = GAME.run(player_id, command, extra={
        "confirm": arguments.get("confirm"), "db_path": str(feedback_db()),
        "platform_root": str(ROOT_DIR),
    })
    return {"game": "candle", "player_id": player_id, "text": text}


def save_summary(player_id):
    try:
        data = _load(_path(player_id), preserve_corrupt=False)
    except FileNotFoundError:
        return None  # A concurrent confirmed reset removed this slot.
    if data is None:
        return None
    # Bound humans/save inventories get progress only, never private feedback.
    return {"act": data["state"]["act"], "finished": data["state"]["act"] == 13}


def delete_save(player_id):
    exists = _path(player_id).exists()
    play({"player_id": player_id, "action": "reset", "confirm": True})
    return exists
