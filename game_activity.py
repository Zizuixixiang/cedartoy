"""Minimal, best-effort gameplay events. Never persist request/response bodies."""

import contextlib
import contextvars
import hashlib
import json
import logging
import re
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)
RETENTION_SECONDS = 60 * 86400
_maintenance_lock = threading.Lock()
_next_cleanup = {}
_evidence = contextvars.ContextVar("activity_save_evidence", default=None)


def init_db(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS game_activity_events (
        occurred_at REAL NOT NULL,
        game TEXT NOT NULL,
        identity_id TEXT NOT NULL,
        identity_type TEXT NOT NULL CHECK(identity_type IN ('human', 'ai')),
        action TEXT NOT NULL
    )""")
    conn.execute("""CREATE INDEX IF NOT EXISTS idx_game_activity_time
                    ON game_activity_events(occurred_at, game)""")


@contextlib.contextmanager
def capture_changes():
    """Opaque text engines report save changes inside their existing game lock."""
    evidence = []
    token = _evidence.set(evidence)
    try:
        yield evidence
    finally:
        _evidence.reset(token)


def observe_change(before, after):
    evidence = _evidence.get()
    if evidence is not None:
        evidence.append(before != after)


def observed_change():
    evidence = _evidence.get()
    return any(evidence) if evidence else None


def save_fingerprint(directory):
    if _evidence.get() is None:
        return None
    # Only in-memory digests, inside the existing save lock. No mtime evidence:
    # several engines rewrite identical saves even when rejecting a command.
    try:
        digest = hashlib.sha256()
        for path in sorted(Path(directory).rglob("*.json")):
            if not path.is_file() or any("backup" in p or "corrupt" in p for p in path.parts):
                continue
            digest.update(str(path.relative_to(directory)).encode())
            with path.open("rb") as stream:
                for chunk in iter(lambda: stream.read(65536), b""):
                    digest.update(chunk)
        return digest.digest()
    except OSError:
        return None


READ_ACTIONS = frozenset({
    "initialize", "tools/list", "ping", "guide", "get_guide", "list_games",
    "status", "state", "progress", "list", "help", "rules", "catalog",
    "summary", "inventory", "history", "history_detail", "result", "compare",
    "rooms", "actions", "achievements", "current_decision", "lines", "levels",
    "table", "version", "read_current_scene", "list_saves", "get_save",
    "query_growth_projects", "query_debt", "export", "import", "save",
    "info", "rest", "vote", "announcements", "web_state", "note_list",
})
READ_COMMANDS = frozenset({
    "help", "status", "state", "inventory", "inv", "summary", "rules", "table",
    "帮助", "状态", "图鉴", "背包",
})
MARKET_READ_COMMANDS = frozenset({
    "菜场", "看看", "逛逛", "市场",
    "看看锅", "看锅", "锅", "观察", "尝", "尝一口", "尝尝", "试味",
    "食材图鉴", "收藏", "发现", "篮子", "买了什么", "冰箱", "冰箱里有什么",
    "成就", "奖杯", "菜谱", "菜谱册", "做过什么", "熟客", "摊主",
    "技能", "本领", "能力", "我的技能", "口味", "她的口味", "偏好",
    "声望", "名声", "名声值", "线索", "碎片", "发现的秘密", "结局",
    "走向", "我的结局", "记忆", "谁记得我", "他们记得什么",
    "买", "极简", "紧凑", "沉浸", "完整",
})
GAME_READ_COMMANDS = {
    "fishing": {"shop", "encyclopedia", "look", "s", "i", "e", "enc", "l", "h"},
    "garden_cat": {"shop", "catalog", "collectibles", "letters", "history", "notes"},
    "eco": {"gaze", "look", "folio", "chronicle", "encyclopedia", "trends"},
    "ciyuwu": {"words", "echoes", "quests", "achievements", "origins"},
}


def business_action(game, action, params=None):
    """Classify semantics, returning only a fixed action name (never arguments)."""
    params = params if isinstance(params, dict) else {}
    if "method" in params:
        return None  # Raw JSON-RPC passthrough is not a gameplay action.
    action = str(action or "").strip()
    local = action.removeprefix(game + "_")
    if local in READ_ACTIONS or local.startswith(("get_", "query_", "read_", "list_")):
        return None
    if game == "eco" and local == "observe":
        # eco observe/wait advance time; gaze/look only inspect.
        if params.get("action", "observe") in {"gaze", "look"}:
            return None
    if game == "eco" and local == "act" and params.get("announcement"):
        return None
    if game == "garden_cat" and local == "notes":
        return "notes_write" if str(params.get("content") or "").strip() else None
    if game == "bar" and local == "call" and params.get("function") in {
        "summary", "recipe_profile", "quote_decision", "stars", "intox_stage",
        "export_archive", "viewer_link",
    }:
        return None
    if game == "duel" and action == "chips":
        sub = params.get("op", "status")
        if sub in {"check_in", "bankruptcy"}:
            return "chips_" + sub
        if sub in {"exchange", "loans"}:
            operation = params.get(("loan" if sub == "loans" else sub) + "_action", "list")
            if operation in {"create", "confirm", "reject", "withdraw", "accept", "repay"}:
                return "chips_" + sub + "_" + operation
        return None
    if local in {"cmd", "play"}:
        command = params.get("command", "")
        if not isinstance(command, str) or not command.strip():
            return None
        commands = [part.strip().lower() for part in re.split(r"[;\n]+", command) if part.strip()]
        def readonly(command):
            head = command.split()[0]
            if head in GAME_READ_COMMANDS.get(game, ()):
                return True
            if game == "market" and command in MARKET_READ_COMMANDS:
                return True
            if game == "fishing" and command in {"goto", "go", "choose", "ch"}:
                return True
            return command in READ_COMMANDS or head in READ_COMMANDS
        if all(readonly(command) for command in commands):
            return None
    # Action is metadata, not an arbitrary string supplied via passthrough MCP.
    return action if re.fullmatch(r"[a-z][a-z0-9_]{0,63}", action) else None


def response_succeeded(response):
    if not isinstance(response, dict):
        return False
    if (response.get("error") or response.get("isError") or
            response.get("ok") is False or response.get("success") is False or
            response.get("duplicate") is True):
        return False
    text = response.get("text")
    if isinstance(text, str) and text.lstrip().startswith(("❌", "未知指令", "这条指令没读懂")):
        return False
    nested = response.get("result")
    if isinstance(nested, dict) and not response_succeeded(nested):
        return False
    # Some satellite MCP tools put structured failure JSON inside text blocks.
    for block in response.get("content", []):
        if isinstance(block, dict) and block.get("type") == "text":
            try:
                value = json.loads(block.get("text", ""))
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and not response_succeeded(value):
                return False
    return True


def record(db_path, game, action, user, response, *, params=None, changed=None):
    """Failures in telemetry must never turn a completed game action into failure."""
    try:
        if not user or not response_succeeded(response) or changed is False:
            return
        action = business_action(game, action, params)
        if not action or int(user["id"]) <= 0:
            return
        now = time.time()
        with contextlib.closing(sqlite3.connect(db_path, timeout=0.2)) as conn, conn:
            init_db(conn)
            conn.execute("INSERT INTO game_activity_events VALUES (?, ?, ?, ?, ?)", (
                now, game, str(int(user["id"])), "ai" if user.get("is_ai") else "human", action,
            ))
            key = str(db_path)
            with _maintenance_lock:
                if time.monotonic() >= _next_cleanup.get(key, 0):
                    conn.execute("DELETE FROM game_activity_events WHERE occurred_at < ?", (now - RETENTION_SECONDS,))
                    _next_cleanup[key] = time.monotonic() + 86400
    except Exception:
        # Do not include request, response, identity, or database exception text.
        logger.warning("Game activity event could not be recorded")
