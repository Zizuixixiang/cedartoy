import json
import base64
import hashlib
import hmac
import http.client
import os
import random
import re
import secrets
import shutil
import sqlite3
import time
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore, Lock

import httpx

try:
    from passlib.context import CryptContext
except ImportError:
    CryptContext = None

import announcements
from bdsmtest.handler import handle_mcp as handle_bdsmtest_mcp
from ciyuwu_adapter.handler import handle_mcp as handle_ciyuwu_mcp
from dnd.handler import handle_mcp as handle_dnd_mcp
from eco_adapter import handler as eco_handler
from eco_adapter.handler import handle_mcp as handle_eco_mcp
from mbti.handler import handle_mcp as handle_mbti_mcp
from vendor_cmd_adapter import arcade as arcade_adapter
from vendor_cmd_adapter import burger as burger_adapter
from vendor_cmd_adapter import fishing as fishing_adapter
from vendor_cmd_adapter import imitator_td as imitator_td_adapter
from vendor_cmd_adapter import leek as leek_adapter
from vendor_cmd_adapter import market as market_adapter
from vendor_cmd_adapter import memoria as memoria_adapter
from vendor_cmd_adapter.base import VendorCmdError
from vendor_cmd_adapter.guides import GUIDES as VENDOR_CMD_GUIDES


HOST = "127.0.0.1"
PORT = 8002
MAX_WORKERS = 50
QUEUE_TIMEOUT_SECONDS = 10
SOUP_HOST = "127.0.0.1"
SOUP_PORT = 8012
SOUP_BASE = f"http://{SOUP_HOST}:{SOUP_PORT}"
WORKKK_HOST = "127.0.0.1"
WORKKK_PORT = 8770
WORKKK_BASE = f"http://{WORKKK_HOST}:{WORKKK_PORT}"
TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
HUMAN_TOKEN_SECONDS = 30 * 24 * 60 * 60
BINDING_TOKEN_SECONDS = 10 * 60
TURTLE_DB_PATH = Path(os.getenv("TURTLE_SOUP_DB", Path(__file__).resolve().parent / "turtle-soup" / "backend" / "turtle_soup.db"))
SESSIONS_DB_PATH = Path(os.getenv("SESSIONS_DB", Path(__file__).resolve().parent / "data" / "sessions.db"))
GAME_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")
GUIDE_DIR = Path(__file__).resolve().parent / "turtle-soup" / "backend" / "guides"
MEMORIA_HUMAN_GUIDE_DIR = Path(__file__).resolve().parent / "vendor" / "Memoria-Station" / "攻略（给人看的）"
MEMORIA_AFTER_CLEAR_DIR = Path(__file__).resolve().parent / "vendor" / "Memoria-Station" / "通关后阅读"
TOY_INDEX_PATH = Path(__file__).resolve().parent / "index.html"
ADMIN_INDEX_PATH = Path(__file__).resolve().parent / "admin.html"
VENDOR_SAVE_ROOT = Path(__file__).resolve().parent / "data" / "vendor_saves"
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
PWD_CONTEXT = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto") if CryptContext else None
# SQLite CURRENT_TIMESTAMP is UTC; use China wall time for stored timestamps.
SQL_NOW = "datetime('now', 'localtime')"
TIMEZONE_MIGRATION_KEY = "platform_timezone_utc_to_shanghai_20260602"
REQUEST_RATE_LIMIT_WINDOW_SECONDS = 60
REQUEST_RATE_LIMIT_MAX = 60
REGISTER_RATE_LIMIT_WINDOW_SECONDS = 60 * 60
REGISTER_RATE_LIMIT_MAX = 3
RECENT_REGISTER_NOTICE_SECONDS = 24 * 60 * 60
RATE_LIMIT_ERROR_CODE = -32029
REQUEST_RATE_LIMIT_MESSAGE = "操作太快了，请稍等片刻再试"
REGISTER_RATE_LIMIT_MESSAGE = "注册太频繁了，请稍后再试"
RECENT_REGISTER_NOTICE = "检测到你近期已注册过账号，如是同一只小机请改用 login 登录旧账号，避免产生多个身份"
_REQUEST_RATE_LIMIT = {}
_REGISTER_RATE_LIMIT = {}
_RATE_LIMIT_LOCK = Lock()
_ANTI_ADDICTION_LOCK = Lock()
_ANTI_ADDICTION_ANY_ENABLED = None


_PLATFORM_TOOLS = [
    {
        "name": "list_games",
        "description": "列出所有可用游戏，返回分类列表（测试类、小游戏类）及简介",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "get_guide",
        "description": "获取指定游戏的玩法说明",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称，如 turtle_soup、mbti",
                },
            },
            "required": ["game"],
            "additionalProperties": False,
        },
    },
    {
        "name": "play",
        "description": "执行游戏操作；先看 get_guide(game)，再把该 action 的业务参数放进 params 对象。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "enum": ["turtle_soup", "mbti", "dnd", "bdsmtest", "eco", "ciyuwu", "leek", "arcade", "burger", "fishing", "imitator_td", "memoria", "market", "workkk"],
                    "description": "游戏名称。",
                },
                "action": {
                    "type": "string",
                    "description": "操作名称，如 turtle_soup 的 join/ask/guess/status，或 mbti_start/dnd_start 等；另有两个跨游戏通用 action：rest（防沉迷休息）、vote（回复系统通知里的投票）。",
                },
                "params": {
                    "type": "object",
                    "description": "该 action 需要的业务参数；账号用户可传 slot=1-5 选择存档槽，默认 1；例如 turtle_soup join 用 {\"room_id\":\"...\"}，ask 用 {\"room_id\":\"...\",\"content\":\"...\"}；vote 用 {\"announcement_id\":\"...\",\"options\":\"1,3,5\"}。",
                    "properties": {
                        "slot": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 5,
                            "description": "账号用户可选存档槽，1-5，默认 1；游客请求忽略。",
                        },
                    },
                    "additionalProperties": True,
                },
            },
            "required": ["game", "action"],
            "additionalProperties": True,
        },
    },
    {
        "name": "account",
        "description": (
            '注册账号用；游客也能玩，账号仅供存档和持久身份。'
            '具体 action 和参数请调用 get_guide(game="account")。'
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "login_or_register、login、generate_binding_token、get_profile、get_bindings、guest_claim_code（按游客 player_id 查询/补发认领码）、claim（凭认领码把游客存档转入账号）、my_saves（查自己在所有游戏的存档概况；human=true 时查绑定人类存档概况）、delete_save（仅带 token 账号可用，删除当前账号自己的单个游戏槽位存档）、delete_account（软删当前账号）",
                },
                "username": {"type": "string", "description": "login/login_or_register 用账号名；my_saves human=true 且绑定多个人类时指定目标 username"},
                "password": {"type": "string"},
                "token": {"type": "string"},
                "human": {"type": "boolean", "description": "my_saves 可选；true 时查看当前账号绑定的人类存档概况"},
                "game": {"type": "string", "description": "delete_save 用：要删除存档的游戏名"},
                "slot": {"type": "integer", "minimum": 1, "maximum": 5, "description": "delete_save 用：账号存档槽 1-5，默认 1；仅支持带 token 的账号用户"},
                "confirm": {"type": "boolean", "description": "delete_save/delete_account 必须显式传 true 才执行"},
                "player_id": {"type": "string", "description": "guest_claim_code 用：旧游客 player_id，可传原始裸 id 或 guest: 前缀 id"},
                "claim_code": {"type": "string", "description": "claim 用：游客开档时发放的一次性认领码"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]
_ROOT_TOOL_NAMES = frozenset({"list_games", "get_guide", "play", "account"})


def _handle_root_mcp(payload, user_agent="", path_token=None, client_ip=None):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        if method == "initialize":
            return _json_rpc_result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cedartoy", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _json_rpc_result(request_id, {"tools": _root_tools()})
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            try:
                if name == "list_games":
                    text = _tool_list_games(path_token=path_token)
                elif name == "get_guide":
                    text = _tool_get_guide(arguments)
                elif name == "play":
                    text = _tool_play(arguments, path_token=path_token)
                elif name == "account":
                    text = _tool_account(
                        arguments,
                        user_agent=user_agent,
                        path_token=path_token,
                        client_ip=client_ip,
                    )
                else:
                    raise _McpError(-32601, f"未知工具：{name}")
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": text}], "isError": False}
                )
            except _McpError as exc:
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": f"【cedartoy】{exc.message}"}], "isError": True}
                )
            except Exception as exc:
                return _json_rpc_result(
                    request_id, {"content": [{"type": "text", "text": f"【cedartoy服务错误】{exc}"}], "isError": True}
                )
        raise _McpError(-32601, f"Method not found: {method}")
    except _McpError as exc:
        return _json_rpc_error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _json_rpc_error(request_id, -32603, f"Internal error: {exc}")


class _McpError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _db_connect():
    conn = sqlite3.connect(TURTLE_DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _sessions_db_connect():
    conn = sqlite3.connect(SESSIONS_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _game_player_ids(user):
    ids = [str(user["id"])]
    username = user.get("username") or ""
    if GAME_PLAYER_ID_RE.fullmatch(username):
        ids.append(username)
    return list(dict.fromkeys(ids))


MIN_SAVE_SLOT = 1
MAX_SAVE_SLOT = 5


def _account_slot_player_id(user_id, slot):
    user_id = str(int(user_id))
    return user_id if slot == 1 else f"{user_id}:{slot}"


def _account_slot_player_ids(user):
    ids = [(_account_slot_player_id(user["id"], slot), slot) for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1)]
    username = user.get("username") or ""
    if GAME_PLAYER_ID_RE.fullmatch(username):
        ids.append((username, 1))
    return list(dict.fromkeys(ids))


def _row_dict(row):
    return dict(row) if row is not None else None


def _table_exists(conn, table):
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone())


def _create_platform_localtime_triggers(conn):
    specs = {
        "toy_users": ("id", ("created_at", "last_active_at")),
        "user_bindings": ("id", ("created_at",)),
    }
    for table, (pk, columns) in specs.items():
        if not _table_exists(conn, table):
            continue
        assignments = ", ".join(f"{column} = datetime('now', 'localtime')" for column in columns)
        conn.execute(
            f"""
            CREATE TRIGGER IF NOT EXISTS trg_{table}_insert_localtime
            AFTER INSERT ON {table}
            BEGIN
                UPDATE {table}
                SET {assignments}
                WHERE {pk} = NEW.{pk};
            END
            """
        )


def _init_registration_events_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_registration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            username TEXT NOT NULL,
            is_ai INTEGER NOT NULL DEFAULT 0,
            client_ip TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_registration_events_ip_created
        ON account_registration_events(client_ip, created_at)
        """
    )


def _init_anti_addiction_tables(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anti_addiction_settings (
            ai_user_id INTEGER PRIMARY KEY,
            enabled INTEGER NOT NULL DEFAULT 0,
            remind_threshold INTEGER NOT NULL DEFAULT 30,
            step INTEGER NOT NULL DEFAULT 20,
            force_threshold INTEGER NOT NULL DEFAULT 50,
            lock_minutes INTEGER NOT NULL DEFAULT 30,
            allow_self_reset INTEGER NOT NULL DEFAULT 1,
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS anti_addiction_states (
            player_id TEXT PRIMARY KEY,
            streak INTEGER NOT NULL DEFAULT 0,
            locked INTEGER NOT NULL DEFAULT 0,
            locked_at REAL,
            last_play_at REAL,
            updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    _add_column_if_missing(conn, "anti_addiction_settings", "lock_minutes", "INTEGER NOT NULL DEFAULT 30")
    _add_column_if_missing(conn, "anti_addiction_settings", "allow_self_reset", "INTEGER NOT NULL DEFAULT 1")
    _add_column_if_missing(conn, "anti_addiction_states", "locked_at", "REAL")


def _init_announcement_tables():
    """系统通知/投票两张表建在 data/sessions.db。

    注意别塞进 _migrate_platform_timestamps：那个函数用的 _db_connect() 连的是
    turtle_soup.db，建过去就成了两张没人读的死表。
    DDL 只在 announcements.init_db 里写一份，这里不重复。
    """
    # 让 announcements 跟着 SESSIONS_DB 环境变量走，别在两处各写死一个路径。
    announcements.DB_PATH = str(SESSIONS_DB_PATH)
    with _sessions_db_connect() as conn:
        announcements.init_db(conn)


def _add_column_if_missing(conn, table, column, column_sql):
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {column_sql}")


def _migrate_platform_timestamps():
    with _db_connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        _create_platform_localtime_triggers(conn)
        _init_guest_claim_table(conn)
        _init_registration_events_table(conn)
        _init_anti_addiction_tables(conn)
        if conn.execute("SELECT value FROM settings WHERE key = ?", (TIMEZONE_MIGRATION_KEY,)).fetchone():
            conn.commit()
            return
        if _table_exists(conn, "toy_users"):
            conn.execute(
                """
                UPDATE toy_users
                SET last_active_at = datetime(last_active_at, '+8 hours')
                WHERE last_active_at IS NOT NULL
                  AND created_at IS NOT NULL
                  AND last_active_at = created_at
                """
            )
            conn.execute(
                """
                UPDATE toy_users
                SET created_at = datetime(created_at, '+8 hours')
                WHERE created_at IS NOT NULL
                """
            )
        if _table_exists(conn, "user_bindings"):
            conn.execute(
                """
                UPDATE user_bindings
                SET created_at = datetime(created_at, '+8 hours')
                WHERE created_at IS NOT NULL
                """
            )
        conn.execute("INSERT INTO settings (key, value) VALUES (?, '1')", (TIMEZONE_MIGRATION_KEY,))
        conn.commit()


def _hash_password(password):
    if PWD_CONTEXT:
        return PWD_CONTEXT.hash(password)
    salt_bytes = secrets.token_bytes(16)
    salt = _ab64_encode(salt_bytes)
    rounds = 29000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, rounds)
    checksum = _ab64_encode(digest)
    return f"$pbkdf2-sha256${rounds}${salt}${checksum}"


def _verify_password(password, password_hash):
    if PWD_CONTEXT:
        return PWD_CONTEXT.verify(password, password_hash)
    try:
        _, scheme, rounds, salt, checksum = password_hash.split("$", 4)
        if scheme != "pbkdf2-sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _ab64_decode(salt), int(rounds))
        expected = _ab64_encode(digest)
        return hmac.compare_digest(expected, checksum)
    except Exception:
        return False


def _ab64_encode(raw):
    return base64.b64encode(raw).decode("ascii").rstrip("=").replace("+", ".")


def _ab64_decode(value):
    normalized = value.replace(".", "+")
    padding = "=" * (-len(normalized) % 4)
    return base64.b64decode((normalized + padding).encode("ascii"))


def _b64url_encode(raw):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _jwt_encode(payload):
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def _jwt_decode(token):
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("bad signature")
        header = json.loads(_b64url_decode(header_part).decode("utf-8"))
        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("bad algorithm")
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise ValueError("登录已失效") from exc


def _create_account_token(user):
    payload = {
        "user_id": int(user["id"]),
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
    }
    if not user.get("is_ai"):
        payload["exp"] = int(time.time()) + HUMAN_TOKEN_SECONDS
    return _jwt_encode(payload)


def _public_user(user):
    return {
        "id": user["id"],
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
        "created_at": user.get("created_at"),
        "last_active_at": user.get("last_active_at"),
    }


def _current_account(raw_token):
    if not raw_token:
        raise _McpError(-32001, "未登录")
    try:
        payload = _jwt_decode(raw_token)
        user_id = int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise _McpError(-32001, "登录已失效") from None
    with _db_connect() as conn:
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone())
        if not user:
            raise _McpError(-32001, "账号不存在或已删除")
        conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user_id,))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
    return user


def _path_token_user_id(path_token):
    if not path_token:
        return None
    try:
        payload = _jwt_decode(path_token)
        return int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _prune_rate_limit_buckets(buckets, now, window_seconds):
    empty_keys = []
    for key, timestamps in buckets.items():
        while timestamps and now - timestamps[0] >= window_seconds:
            timestamps.pop(0)
        if not timestamps:
            empty_keys.append(key)
    for key in empty_keys:
        buckets.pop(key, None)


def _check_sliding_window_limit(buckets, identity, *, now, window_seconds, max_count):
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(buckets, now, window_seconds)
        timestamps = buckets.setdefault(identity, [])
        while timestamps and now - timestamps[0] >= window_seconds:
            timestamps.pop(0)
        if len(timestamps) >= max_count:
            return False
        timestamps.append(now)
        return True


def _check_request_rate_limit(identity):
    return _check_sliding_window_limit(
        _REQUEST_RATE_LIMIT,
        identity,
        now=time.time(),
        window_seconds=REQUEST_RATE_LIMIT_WINDOW_SECONDS,
        max_count=REQUEST_RATE_LIMIT_MAX,
    )


def _check_register_rate_limit(ip):
    return _check_sliding_window_limit(
        _REGISTER_RATE_LIMIT,
        f"ip:{ip or 'unknown'}",
        now=time.time(),
        window_seconds=REGISTER_RATE_LIMIT_WINDOW_SECONDS,
        max_count=REGISTER_RATE_LIMIT_MAX,
    )


def _user_exists(username):
    with _db_connect() as conn:
        return conn.execute("SELECT 1 FROM toy_users WHERE username = ?", (username,)).fetchone() is not None


def _enforce_register_rate_limit(username, client_ip):
    username = (username or "").strip()
    if not username or _user_exists(username):
        return
    if not _check_register_rate_limit(client_ip):
        raise _McpError(RATE_LIMIT_ERROR_CODE, REGISTER_RATE_LIMIT_MESSAGE)


def _recent_registration_exists(conn, client_ip):
    if not client_ip:
        client_ip = "unknown"
    _init_registration_events_table(conn)
    return conn.execute(
        """
        SELECT 1
        FROM account_registration_events
        WHERE client_ip = ?
          AND created_at >= datetime('now', 'localtime', ?)
        LIMIT 1
        """,
        (client_ip, f"-{RECENT_REGISTER_NOTICE_SECONDS} seconds"),
    ).fetchone() is not None


def _record_successful_registration(conn, user, client_ip):
    if not client_ip:
        client_ip = "unknown"
    _init_registration_events_table(conn)
    conn.execute(
        """
        INSERT INTO account_registration_events (user_id, username, is_ai, client_ip)
        VALUES (?, ?, ?, ?)
        """,
        (int(user["id"]), user["username"], 1 if user.get("is_ai") else 0, client_ip),
    )


def _append_recent_registration_notice(result, had_recent_registration):
    if not had_recent_registration:
        return result
    result = dict(result)
    message = (result.get("message") or "").strip()
    result["message"] = f"{message} {RECENT_REGISTER_NOTICE}".strip() if message else RECENT_REGISTER_NOTICE
    return result


def _validate_credentials(username, password):
    if not username or not password:
        raise _McpError(-32602, "username 和 password 必填")
    if len(username) < 2 or len(username) > 20:
        raise _McpError(-32602, "用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise _McpError(-32602, "用户名只能包含字母、数字、下划线和中文")
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")


def _login_or_register(username, password, *, is_ai, client_ip=None):
    """Shared login/register; callers set is_ai (MCP=1, REST human=0)."""
    username = (username or "").strip()
    password = password or ""
    _validate_credentials(username, password)
    is_ai = 1 if is_ai else 0
    with _db_connect() as conn:
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE username = ?", (username,)).fetchone())
        if user:
            if not _verify_password(password, user["password_hash"]):
                raise _McpError(-32001, "用户名或密码错误")
            conn.execute(
                """
                UPDATE toy_users
                SET last_active_at = datetime('now', 'localtime'),
                    deleted_at = NULL
                WHERE id = ?
                """,
                (user["id"],),
            )
            conn.commit()
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
        else:
            _enforce_register_rate_limit(username, client_ip)
            had_recent_registration = _recent_registration_exists(conn, client_ip)
            cur = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, ?)",
                (username, _hash_password(password), is_ai),
            )
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
            _record_successful_registration(conn, user, client_ip)
            conn.commit()
            result = {"token": _create_account_token(user), "user": _public_user(user)}
            return _append_recent_registration_notice(result, had_recent_registration)
    return {"token": _create_account_token(user), "user": _public_user(user)}


def _login_or_register_ai(username, password, client_ip=None):
    username = (username or "").strip()
    password = password or ""
    _validate_credentials(username, password)
    with _db_connect() as conn:
        if conn.execute("SELECT id FROM toy_users WHERE username = ?", (username,)).fetchone():
            raise _McpError(-32602, "用户名已存在，如需找回请联系管理员")
        _enforce_register_rate_limit(username, client_ip)
        had_recent_registration = _recent_registration_exists(conn, client_ip)
        cur = conn.execute(
            "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, 1)",
            (username, _hash_password(password)),
        )
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
        _record_successful_registration(conn, user, client_ip)
        conn.commit()
    return _append_recent_registration_notice({
        "token": _create_account_token(user),
        "user": _public_user(user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }, had_recent_registration)


def _login_existing_account(username, password):
    username = (username or "").strip()
    password = password or ""
    _validate_credentials(username, password)
    with _db_connect() as conn:
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        ).fetchone())
        if not user or not _verify_password(password, user["password_hash"]):
            raise _McpError(-32001, "用户名或密码错误")
        conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user["id"],))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
    return {
        "token": _create_account_token(user),
        "user": _public_user(user),
        "message": "登录成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份。",
    }


def _login_or_register_human(username, password, client_ip=None):
    return _login_or_register(username, password, is_ai=0, client_ip=client_ip)


def _require_admin_account(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_admin"):
        raise _McpError(-32003, "需要管理员权限")
    return user


def _admin_user_rows():
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id,
                u.username,
                u.is_ai,
                u.is_admin,
                u.created_at,
                u.last_active_at,
                u.deleted_at,
                (SELECT COUNT(*) FROM players p WHERE p.user_id = u.id) AS soup_player_count,
                (SELECT COUNT(*) FROM user_bindings b WHERE b.human_user_id = u.id) AS bound_ai_count,
                (SELECT COUNT(*) FROM user_bindings b WHERE b.ai_user_id = u.id) AS bound_human_count,
                (
                    SELECT COUNT(*)
                    FROM binding_tokens t
                    WHERE t.ai_user_id = u.id
                      AND t.used = 0
                      AND t.expires_at > datetime('now', 'localtime')
                ) AS active_binding_tokens
            FROM toy_users u
            ORDER BY u.deleted_at IS NOT NULL ASC, u.id DESC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def _admin_update_user(user_id, body, admin_user):
    username = (body.get("username") or "").strip()
    if not username:
        raise _McpError(-32602, "username 必填")
    if len(username) < 2 or len(username) > 20:
        raise _McpError(-32602, "用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise _McpError(-32602, "用户名只能包含字母、数字、下划线和中文")
    is_ai = 1 if body.get("is_ai") else 0
    is_admin = 1 if body.get("is_admin") else 0
    deleted = 1 if body.get("deleted") else 0
    if int(user_id) == int(admin_user["id"]) and (not is_admin or deleted):
        raise _McpError(-32602, "不能取消当前登录管理员的权限或软删当前账号")
    with _db_connect() as conn:
        existing = conn.execute("SELECT id FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        duplicate = conn.execute(
            "SELECT id FROM toy_users WHERE username = ? AND id <> ?",
            (username, user_id),
        ).fetchone()
        if duplicate:
            raise _McpError(-32602, "用户名已存在")
        conn.execute(
            """
            UPDATE toy_users
            SET username = ?,
                is_ai = ?,
                is_admin = ?,
                deleted_at = CASE WHEN ? THEN COALESCE(deleted_at, datetime('now', 'localtime')) ELSE NULL END
            WHERE id = ?
            """,
            (username, is_ai, is_admin, deleted, user_id),
        )
        conn.execute(
            "UPDATE players SET username = ?, is_ai = ?, is_admin = ? WHERE user_id = ?",
            (username, is_ai, is_admin, user_id),
        )
        conn.commit()
    return {"ok": True}


def _admin_reset_user_password(user_id, body):
    password = body.get("password") or ""
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")
    with _db_connect() as conn:
        existing = conn.execute("SELECT id FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        conn.execute(
            "UPDATE toy_users SET password_hash = ?, deleted_at = NULL WHERE id = ?",
            (_hash_password(password), user_id),
        )
        conn.commit()
    return {"ok": True}


def _admin_release_user(user_id, admin_user):
    if int(user_id) == int(admin_user["id"]):
        raise _McpError(-32602, "不能释放当前登录的管理员账号")
    with _db_connect() as conn:
        existing = conn.execute("SELECT id FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        conn.execute("UPDATE players SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM binding_tokens WHERE ai_user_id = ?", (user_id,))
        conn.execute("DELETE FROM user_bindings WHERE human_user_id = ? OR ai_user_id = ?", (user_id, user_id))
        conn.execute("DELETE FROM toy_users WHERE id = ?", (user_id,))
        conn.commit()
    return {"ok": True}


def _generate_binding_token(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以生成绑定码")
    token = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + BINDING_TOKEN_SECONDS
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO binding_tokens (token, ai_user_id, expires_at, used) VALUES (?, ?, datetime(?, 'unixepoch', 'localtime'), 0)",
            (token, user["id"], expires_at),
        )
        conn.commit()
    return {"binding_token": token, "expires_in": BINDING_TOKEN_SECONDS}


def _bind_account(human_token, binding_token):
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以绑定 AI")
    binding_token = (binding_token or "").strip()
    if not binding_token:
        raise _McpError(-32602, "binding_token 必填")
    with _db_connect() as conn:
        row = _row_dict(conn.execute(
            """
            SELECT * FROM binding_tokens
            WHERE token = ?
              AND used = 0
              AND expires_at > datetime('now', 'localtime')
            """,
            (binding_token,),
        ).fetchone())
        if not row:
            raise _McpError(-32001, "绑定码无效或已过期")
        if int(row["ai_user_id"]) == int(human["id"]):
            raise _McpError(-32602, "不能绑定自己")
        conn.execute(
            "INSERT OR IGNORE INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
            (human["id"], row["ai_user_id"]),
        )
        conn.execute("UPDATE binding_tokens SET used = 1 WHERE token = ?", (binding_token,))
        conn.commit()
    return {"ok": True}


def _unbind_account(raw_token, ai_user_id):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以解绑")
    if not ai_user_id:
        raise _McpError(-32602, "ai_user_id 必填")
    with _db_connect() as conn:
        deleted = conn.execute(
            "DELETE FROM user_bindings WHERE human_user_id = ? AND ai_user_id = ?",
            (human["id"], ai_user_id),
        ).rowcount
        conn.commit()
    if deleted == 0:
        raise _McpError(-32004, "绑定关系不存在")
    return {"ok": True}


def _binding_rows(conn, user):
    if user.get("is_ai"):
        return conn.execute(
            """
            SELECT u.username, b.created_at AS bound_at
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.human_user_id
            WHERE b.ai_user_id = ? AND u.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    return conn.execute(
        """
        SELECT u.username, b.created_at AS bound_at
        FROM user_bindings b
        JOIN toy_users u ON u.id = b.ai_user_id
        WHERE b.human_user_id = ? AND u.deleted_at IS NULL
        ORDER BY b.created_at DESC
        """,
        (user["id"],),
    ).fetchall()


def _public_binding(row):
    return {"username": row["username"], "bound_at": row["bound_at"]}


def _bound_human_user_for_saves(raw_token, username):
    user = _current_account(raw_token)
    username = (username or "").strip()
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT target.*
            FROM user_bindings b
            JOIN toy_users target ON target.id = b.human_user_id
            WHERE b.ai_user_id = ?
              AND target.deleted_at IS NULL
            ORDER BY username
            """,
            (int(user["id"]),),
        ).fetchall()
    targets = [_row_dict(row) for row in rows]
    if username:
        for target in targets:
            if target["username"] == username:
                return target
        raise _McpError(-32004, "未与该人类绑定")
    if len(targets) == 1:
        return targets[0]
    if len(targets) > 1:
        choices = "、".join(target["username"] for target in targets)
        raise _McpError(-32602, f"绑定了多个人类，请传 username；可选 username：{choices}")
    raise _McpError(-32004, "未绑定任何人类，请先绑定")


def _turtle_soup_stats(conn, user):
    if not _table_exists(conn, "players"):
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    row = conn.execute(
        """
        SELECT game_count, win_count, ask_count, ask_count_y, ask_count_n, ask_count_u, ask_count_p
        FROM players
        WHERE username = ?
        """,
        (user["username"],),
    ).fetchone()
    if not row:
        return {
            "game_count": 0,
            "win_count": 0,
            "ask_count": 0,
            "ask_count_y": 0,
            "ask_count_n": 0,
            "ask_count_u": 0,
            "ask_count_p": 0,
        }
    return {
        "game_count": int(row["game_count"] or 0),
        "win_count": int(row["win_count"] or 0),
        "ask_count": int(row["ask_count"] or 0),
        "ask_count_y": int(row["ask_count_y"] or 0),
        "ask_count_n": int(row["ask_count_n"] or 0),
        "ask_count_u": int(row["ask_count_u"] or 0),
        "ask_count_p": int(row["ask_count_p"] or 0),
    }


def _test_stats(user):
    player_ids = _game_player_ids(user)
    if not player_ids or not SESSIONS_DB_PATH.exists():
        return {"mbti": {"test_count": 0}, "dnd": {"test_count": 0}, "bdsmtest": {"test_count": 0}}
    placeholders = ",".join("?" * len(player_ids))
    counts = {"mbti": 0, "dnd": 0, "bdsmtest": 0}
    with _sessions_db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT game, COUNT(*) AS test_count
            FROM test_results
            WHERE player_id IN ({placeholders}) AND game IN ('mbti', 'dnd', 'bdsmtest')
            GROUP BY game
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            counts[row["game"]] = int(row["test_count"])
    return {
        "mbti": {"test_count": counts["mbti"]},
        "dnd": {"test_count": counts["dnd"]},
        "bdsmtest": {"test_count": counts["bdsmtest"]},
    }


def _game_overview(conn, user):
    tests = _test_stats(user)
    soup = _turtle_soup_stats(conn, user)
    return {
        "turtle_soup": soup,
        "mbti": tests["mbti"],
        "dnd": tests["dnd"],
        "bdsmtest": tests["bdsmtest"],
    }


def _count_table_rows(table_name):
    if not SESSIONS_DB_PATH.exists():
        return 0
    with _sessions_db_connect() as conn:
        if not _table_exists(conn, table_name):
            return 0
        return int(conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0] or 0)


def _sum_ciyuwu_runs():
    if not SESSIONS_DB_PATH.exists():
        return 0
    total = 0
    with _sessions_db_connect() as conn:
        if not _table_exists(conn, "ciyuwu_sessions"):
            return 0
        rows = conn.execute("SELECT meta_data FROM ciyuwu_sessions").fetchall()
    for row in rows:
        try:
            meta = json.loads(row["meta_data"] or "{}")
        except (TypeError, json.JSONDecodeError):
            meta = {}
        try:
            total += max(1, int(meta.get("runs") or 1))
        except (TypeError, ValueError):
            total += 1
    return total


def _vendor_save_stats(game):
    root = VENDOR_SAVE_ROOT / game
    if not root.exists():
        return {"save_count": 0, "file_count": 0}
    player_dirs = [path for path in root.iterdir() if path.is_dir()]
    file_count = 0
    for path in player_dirs:
        file_count += sum(1 for child in path.iterdir() if child.is_file() and child.name != ".lock")
    return {"save_count": len(player_dirs), "file_count": file_count}


def _public_game_stats():
    stats = {
        "eco": {
            "metric_label": "存档数",
            "metric": _count_table_rows("eco_sessions"),
        },
        "ciyuwu": {
            "metric_label": "对局数",
            "metric": _sum_ciyuwu_runs(),
            "save_count": _count_table_rows("ciyuwu_sessions"),
        },
    }
    for game in ("arcade", "burger", "leek", "fishing", "imitator_td", "memoria", "market"):
        vendor_stats = _vendor_save_stats(game)
        stats[game] = {
            "metric_label": "存档数",
            "metric": vendor_stats["save_count"],
            "file_count": vendor_stats["file_count"],
        }
    return stats


def _memoria_human_guides(include_content=False):
    items = []
    if MEMORIA_HUMAN_GUIDE_DIR.exists():
        for path in sorted(MEMORIA_HUMAN_GUIDE_DIR.glob("*.md")):
            title = path.stem.replace("-攻略", ""); title = __import__("re").sub(r"^\d+-", "", title); item = {"kind": "攻略", "title": title, "filename": path.name}
            if include_content:
                item["content"] = path.read_text(encoding="utf-8")
            items.append(item)
    if MEMORIA_AFTER_CLEAR_DIR.exists():
        for path in sorted(MEMORIA_AFTER_CLEAR_DIR.iterdir()):
            if not path.is_file():
                continue
            item = {"kind": "通关后阅读", "title": path.stem, "filename": path.name}
            if include_content:
                item["content"] = path.read_text(encoding="utf-8")
            items.append(item)
    return {"items": items, "content_included": bool(include_content)}


def _get_bindings(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以查看绑定自己的人类列表")
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
    return {"bindings": [_public_binding(dict(row)) for row in rows]}


def _get_profile(raw_token):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
        games = _game_overview(conn, user)
    return {
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "created_at": user.get("created_at"),
        "bindings": [_public_binding(dict(row)) for row in rows],
        "games": games,
    }


def _account_me(raw_token):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        if user.get("is_ai"):
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_ai, u.is_admin, u.created_at, u.last_active_at
                FROM user_bindings b
                JOIN toy_users u ON u.id = b.human_user_id
                WHERE b.ai_user_id = ? AND u.deleted_at IS NULL
                ORDER BY b.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_ai, u.is_admin, u.created_at, u.last_active_at
                FROM user_bindings b
                JOIN toy_users u ON u.id = b.ai_user_id
                WHERE b.human_user_id = ? AND u.deleted_at IS NULL
                ORDER BY b.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    return {"user": _public_user(user), "bindings": [_public_user(dict(row)) for row in rows]}


def _require_bound_ai(raw_token, ai_user_id, operation="操作绑定小机"):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, f"只有人类账号可以{operation}")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填")
    with _db_connect() as conn:
        row = _row_dict(conn.execute(
            """
            SELECT u.id, u.username, u.is_ai, u.is_admin, u.created_at, u.last_active_at
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND u.is_ai = 1
              AND u.deleted_at IS NULL
            """,
            (human["id"], ai_user_id),
        ).fetchone())
    if not row:
        raise _McpError(-32004, "未绑定该小机")
    return row


def _anti_addiction_defaults():
    return {
        "enabled": False,
        "remind_threshold": ANTI_ADDICTION_DEFAULT_REMIND,
        "force_threshold": ANTI_ADDICTION_DEFAULT_FORCE,
        "lock_minutes": ANTI_ADDICTION_DEFAULT_LOCK_MINUTES,
        "allow_self_reset": ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET,
    }


def _anti_addiction_public_settings(row=None):
    settings = _anti_addiction_defaults()
    if row:
        settings.update({
            "enabled": bool(row["enabled"]),
            "remind_threshold": int(row["remind_threshold"] or ANTI_ADDICTION_DEFAULT_REMIND),
            "force_threshold": int(row["force_threshold"] or ANTI_ADDICTION_DEFAULT_FORCE),
            "lock_minutes": int(row["lock_minutes"] or ANTI_ADDICTION_DEFAULT_LOCK_MINUTES),
            "allow_self_reset": bool(row["allow_self_reset"]),
        })
    return settings


def _anti_addiction_settings_for_ai(conn, ai_user_id):
    row = conn.execute(
        """
        SELECT enabled, remind_threshold, force_threshold, lock_minutes, allow_self_reset
        FROM anti_addiction_settings
        WHERE ai_user_id = ?
        """,
        (int(ai_user_id),),
    ).fetchone()
    return _anti_addiction_public_settings(row)


def _anti_addiction_any_enabled():
    global _ANTI_ADDICTION_ANY_ENABLED
    if _ANTI_ADDICTION_ANY_ENABLED is not None:
        return _ANTI_ADDICTION_ANY_ENABLED
    with _ANTI_ADDICTION_LOCK:
        if _ANTI_ADDICTION_ANY_ENABLED is not None:
            return _ANTI_ADDICTION_ANY_ENABLED
        with _db_connect() as conn:
            enabled = conn.execute(
                "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
            ).fetchone() is not None
        _ANTI_ADDICTION_ANY_ENABLED = enabled
        return enabled


def _anti_addiction_validate_settings(body):
    settings = _anti_addiction_defaults()
    settings["enabled"] = bool(body.get("enabled"))
    settings["allow_self_reset"] = bool(body.get("allow_self_reset", settings["allow_self_reset"]))
    for field in ("remind_threshold", "force_threshold", "lock_minutes"):
        raw = body.get(field, settings[field])
        try:
            value = int(raw)
        except (TypeError, ValueError):
            raise _McpError(-32602, f"{field} 必须是整数") from None
        if value < 1 or value > 10000:
            raise _McpError(-32602, f"{field} 必须在 1-10000 之间")
        settings[field] = value
    if settings["force_threshold"] < settings["remind_threshold"]:
        raise _McpError(-32602, "强制阈值不能小于提醒阈值")
    return settings


def _anti_addiction_machines(raw_token):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以管理小机")
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id, u.username, u.is_ai, u.is_admin, u.created_at, u.last_active_at,
                b.created_at AS bound_at,
                s.enabled, s.remind_threshold, s.force_threshold, s.lock_minutes, s.allow_self_reset
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.ai_user_id
            LEFT JOIN anti_addiction_settings s ON s.ai_user_id = u.id
            WHERE b.human_user_id = ?
              AND u.is_ai = 1
              AND u.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (human["id"],),
        ).fetchall()
    machines = []
    for row in rows:
        item = _public_user(dict(row))
        item["bound_at"] = row["bound_at"]
        item["anti_addiction"] = _anti_addiction_public_settings(row)
        machines.append(item)
    return {"machines": machines}


def _save_anti_addiction_settings(raw_token, body):
    global _ANTI_ADDICTION_ANY_ENABLED
    ai_user = _require_bound_ai(raw_token, body.get("ai_user_id"))
    settings = _anti_addiction_validate_settings(body)
    with _db_connect() as conn:
        previous = conn.execute(
            "SELECT enabled FROM anti_addiction_settings WHERE ai_user_id = ?",
            (int(ai_user["id"]),),
        ).fetchone()
        was_enabled = bool(previous["enabled"]) if previous else False
        conn.execute(
            """
            INSERT INTO anti_addiction_settings
                (ai_user_id, enabled, remind_threshold, force_threshold, lock_minutes, allow_self_reset, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
            ON CONFLICT(ai_user_id) DO UPDATE SET
                enabled = excluded.enabled,
                remind_threshold = excluded.remind_threshold,
                force_threshold = excluded.force_threshold,
                lock_minutes = excluded.lock_minutes,
                allow_self_reset = excluded.allow_self_reset,
                updated_at = datetime('now', 'localtime')
            """,
            (
                int(ai_user["id"]),
                1 if settings["enabled"] else 0,
                settings["remind_threshold"],
                settings["force_threshold"],
                settings["lock_minutes"],
                1 if settings["allow_self_reset"] else 0,
            ),
        )
        if was_enabled and not settings["enabled"]:
            _anti_addiction_reset_ai_states(conn, ai_user, time.time())
        _ANTI_ADDICTION_ANY_ENABLED = conn.execute(
            "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
        ).fetchone() is not None
        conn.commit()
    return {"ok": True, "ai": _public_user(ai_user), "anti_addiction": settings}


def _reset_anti_addiction_state(raw_token, body):
    ai_user = _require_bound_ai(raw_token, body.get("ai_user_id"))
    with _db_connect() as conn:
        conn.execute(
            "DELETE FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
            (str(ai_user["id"]), f"{int(ai_user['id'])}:%"),
        )
        conn.commit()
    return {"ok": True, "ai": _public_user(ai_user), "message": "已重置"}


def _arcade_chips_status(raw_token, ai_user_id):
    ai_user = _require_bound_ai(raw_token, ai_user_id, "查看街机厅筹码")
    status = arcade_adapter.status(str(ai_user["id"]))
    return {"ai": _public_user(ai_user), **status}


def _arcade_chips_grant(raw_token, ai_user_id, amount):
    ai_user = _require_bound_ai(raw_token, ai_user_id, "发放街机厅筹码")
    try:
        status = arcade_adapter.grant_chips(str(ai_user["id"]), amount)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc)) from exc
    return {"ok": True, "ai": _public_user(ai_user), **status}


def _eco_api_target_user(raw_token, ai_user_id=None):
    if ai_user_id is not None and str(ai_user_id).strip():
        return _require_bound_ai(raw_token, ai_user_id, "查看瓶中生态存档")
    return _current_account(raw_token)


def _eco_api_player_id(raw_token, ai_user_id=None):
    user = _eco_api_target_user(raw_token, ai_user_id)
    return str(int(user["id"])), user


def _eco_api_response(raw_token, endpoint, *, ai_user_id=None, species_name=None):
    player_id, user = _eco_api_player_id(raw_token, ai_user_id)
    if endpoint == "state":
        data = eco_handler.api_state(player_id)
    elif endpoint == "codex":
        data = eco_handler.api_codex(player_id)
    elif endpoint == "folio":
        data = eco_handler.api_folio(player_id)
    elif endpoint == "annals":
        data = eco_handler.api_annals(player_id)
    elif endpoint == "species":
        data = eco_handler.api_species(player_id, species_name)
    else:
        raise _McpError(-32004, "not found")
    return {"user": _public_user(user), "player_id": player_id, **data}


def _extract_bearer(headers):
    value = headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


# ---- play 层统一身份 ----
# 带 path_token 的请求：解析账号并强制 player_id = str(user.id)，无视自报 id。
# 无 token 的自报 id：统一加 guest: 前缀落档，防止游客自称任意 id 触碰账号存档。
GUEST_PREFIX = "guest:"
PLAIN_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,64}$")
# 按 player_id 记档、需要身份管控的游戏（turtle_soup 自己处理 path_token，不在此列）。
IDENTITY_GAMES = frozenset({"mbti", "dnd", "bdsmtest", "eco", "ciyuwu", "leek", "arcade", "burger", "fishing", "imitator_td", "memoria", "market", "workkk"})
# 有长期存档、值得给游客发认领码的游戏。
PERSISTENT_SAVE_GAMES = frozenset({"eco", "ciyuwu", "leek", "arcade", "burger", "fishing", "imitator_td", "memoria", "market"})
VENDOR_GAMES = ("leek", "arcade", "burger", "fishing", "imitator_td", "memoria", "market")
ANTI_ADDICTION_DEFAULT_REMIND = 30
ANTI_ADDICTION_DEFAULT_FORCE = 50
ANTI_ADDICTION_DEFAULT_LOCK_MINUTES = 30
ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET = True
ANTI_ADDICTION_TEST_GAMES = frozenset({"mbti", "dnd", "bdsmtest"})
ANTI_ADDICTION_MINI_GAMES = frozenset({"turtle_soup", "eco", "ciyuwu", *VENDOR_GAMES})


def _guest_player_id(raw):
    """自报裸 id → guest: 前缀 id；已带前缀或不合法的原样返回（由各游戏自行报错）。"""
    if isinstance(raw, str) and PLAIN_PLAYER_ID_RE.fullmatch(raw):
        return GUEST_PREFIX + raw
    return raw


def _reported_player_id(arguments):
    params = arguments.get("params")
    if isinstance(params, dict) and params.get("player_id") is not None:
        return params.get("player_id")
    return arguments.get("player_id")


def _override_player_id(arguments, player_id):
    """顶层和 params 里的 player_id 一律覆盖（各 adapter 以 params 优先合并）。"""
    new_arguments = dict(arguments)
    new_arguments["player_id"] = player_id
    params = new_arguments.get("params")
    if isinstance(params, dict):
        params = dict(params)
        params["player_id"] = player_id
        new_arguments["params"] = params
    return new_arguments


def _save_slot_from_arguments(arguments):
    params = arguments.get("params")
    raw = params.get("slot", MIN_SAVE_SLOT) if isinstance(params, dict) else MIN_SAVE_SLOT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    if raw < MIN_SAVE_SLOT or raw > MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    return raw


def _save_slot_from_account_arguments(arguments):
    raw = arguments.get("slot", MIN_SAVE_SLOT)
    if isinstance(raw, bool):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    try:
        slot = int(raw)
    except (TypeError, ValueError):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    if slot < MIN_SAVE_SLOT or slot > MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    return slot


def _without_slot_param(arguments):
    params = arguments.get("params")
    if not isinstance(params, dict) or "slot" not in params:
        return arguments
    new_arguments = dict(arguments)
    params = dict(params)
    params.pop("slot", None)
    new_arguments["params"] = params
    return new_arguments


def _guestify_mcp_payload(payload):
    """/mbti、/dnd 直连 MCP 端点无 token，同样把自报 player_id 隔离到 guest: 命名空间。"""
    if payload.get("method") != "tools/call":
        return payload
    params = payload.get("params")
    arguments = params.get("arguments") if isinstance(params, dict) else None
    if not isinstance(arguments, dict):
        return payload
    raw = arguments.get("player_id")
    guest = _guest_player_id(raw)
    if guest == raw:
        return payload
    payload = dict(payload)
    params = dict(params)
    arguments = dict(arguments)
    arguments["player_id"] = guest
    params["arguments"] = arguments
    payload["params"] = params
    return payload


def _init_guest_claim_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guest_claim_codes (
            code TEXT PRIMARY KEY,
            guest_player_id TEXT NOT NULL UNIQUE,
            created_at TEXT,
            claimed_by INTEGER,
            claimed_at TEXT
        )
        """
    )


def _ensure_guest_claim_code(guest_player_id):
    """游客首次开档时生成一次性认领码；已有码（或已认领过）返回 None，不重复提示。"""
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = conn.execute(
            "SELECT code FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone()
        if row:
            return None
        code = secrets.token_urlsafe(9)
        try:
            conn.execute(
                "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
                (code, guest_player_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return code


def _normalize_guest_player_id(value):
    if not isinstance(value, str) or not value.strip():
        raise _McpError(-32602, "player_id 必填")
    raw = value.strip()
    if raw.startswith(GUEST_PREFIX):
        suffix = raw[len(GUEST_PREFIX):]
        if PLAIN_PLAYER_ID_RE.fullmatch(suffix):
            return raw
        raise _McpError(-32602, "guest player_id 格式不合法")
    if PLAIN_PLAYER_ID_RE.fullmatch(raw):
        return GUEST_PREFIX + raw
    raise _McpError(-32602, "player_id 只能包含 1-64 位字母数字；也可传 guest: 前缀")


def _guest_claim_code_for_player_id(player_id):
    guest_player_id = _normalize_guest_player_id(player_id)
    found, _conflicts = _collect_player_saves(guest_player_id, "__claim_probe__")
    if not found:
        raise _McpError(-32004, f"没有找到 {guest_player_id} 名下的游客存档")

    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone())
        if row:
            return {
                "guest_player_id": guest_player_id,
                "claim_code": row["code"],
                "claimed_by": row.get("claimed_by"),
                "claimed_at": row.get("claimed_at"),
                "saves": found,
                "message": "该游客存档已有认领码；若 claimed_by 为空，可用 account(action=\"claim\", claim_code=\"...\") 转入账号。",
            }
        code = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
            (code, guest_player_id),
        )
        conn.commit()
    return {
        "guest_player_id": guest_player_id,
        "claim_code": code,
        "claimed_by": None,
        "claimed_at": None,
        "saves": found,
        "message": "已为旧游客存档生成认领码；登录账号后调用 account(action=\"claim\", claim_code=\"...\") 可转入账号。",
    }


def _sessions_table_columns(conn, table):
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _stamp_save_owner(game, player_id, user_id):
    """token 玩家写档后回填 user_id 列（表或列不存在时静默跳过）。"""
    if not SESSIONS_DB_PATH.exists():
        return
    if game == "eco":
        targets = [("eco_sessions", False)]
    elif game == "ciyuwu":
        targets = [("ciyuwu_sessions", False)]
    elif game in {"mbti", "dnd", "bdsmtest"}:
        targets = [("test_sessions", True), ("test_results", True)]
    else:
        return
    try:
        with _sessions_db_connect() as conn:
            for table, has_game_column in targets:
                if "user_id" not in _sessions_table_columns(conn, table):
                    continue
                if has_game_column:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND game = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, game, user_id),
                    )
                else:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, user_id),
                    )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def _collect_player_saves(old_player_id, target_player_id):
    """列出 old_player_id 名下所有存档，以及迁到 target_player_id 会撞上的冲突。

    返回 (found, conflicts)：found 形如 {"eco": {...}, "vendor:arcade": {...}}。
    """
    found = {}
    conflicts = []
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for table, game_label in (("eco_sessions", "eco"), ("ciyuwu_sessions", "ciyuwu")):
                if not _table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"SELECT last_active FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchone()
                if not row:
                    continue
                found[game_label] = {"table": table, "last_active": row["last_active"]}
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE player_id = ?", (target_player_id,)
                ).fetchone():
                    conflicts.append(f"{game_label}（账号名下已有存档）")
            for table in ("test_sessions", "test_results"):
                if not _table_exists(conn, table):
                    continue
                for row in conn.execute(
                    f"SELECT game FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchall():
                    game = row["game"]
                    found[f"{table}:{game}"] = {"table": table, "game": game}
                    if conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                        (target_player_id, game),
                    ).fetchone():
                        conflicts.append(f"{game}/{table}（账号名下已有记录）")
    for game in VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / old_player_id
        if not old_dir.is_dir():
            continue
        found[f"vendor:{game}"] = {"dir": str(old_dir)}
        if (VENDOR_SAVE_ROOT / game / target_player_id).exists():
            conflicts.append(f"{game}（账号名下已有存档目录）")
    return found, conflicts


def _migrate_player_saves(old_player_id, user_id):
    """把 old_player_id 名下所有存档改绑到账号：player_id 迁为 str(user_id) 并回填 user_id 列。

    冲突时整体报错、不迁移、绝不覆盖或删除任何存档。返回迁移摘要。
    """
    target_player_id = str(int(user_id))
    if old_player_id == target_player_id:
        raise _McpError(-32602, "旧 id 与账号 id 相同，无需迁移")
    found, conflicts = _collect_player_saves(old_player_id, target_player_id)
    if not found:
        raise _McpError(-32004, f"没有找到 player_id={old_player_id} 的任何存档")
    if conflicts:
        raise _McpError(
            -32602,
            "以下游戏在账号名下已有存档，迁移会冲突，已全部取消（不覆盖不删档）：" + "、".join(conflicts),
        )
    migrated = []
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for table in ("eco_sessions", "ciyuwu_sessions", "test_sessions", "test_results"):
                if not _table_exists(conn, table):
                    continue
                if "user_id" in _sessions_table_columns(conn, table):
                    cur = conn.execute(
                        f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                        (target_player_id, int(user_id), old_player_id),
                    )
                else:
                    cur = conn.execute(
                        f"UPDATE {table} SET player_id = ? WHERE player_id = ?",
                        (target_player_id, old_player_id),
                    )
                if cur.rowcount:
                    migrated.append(f"{table}×{cur.rowcount}")
            conn.commit()
    for game in VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / old_player_id
        if old_dir.is_dir():
            old_dir.rename(VENDOR_SAVE_ROOT / game / target_player_id)
            migrated.append(f"vendor_saves/{game}")
    return {"old_player_id": old_player_id, "new_player_id": target_player_id, "migrated": migrated}


def _auto_migrate_legacy_account_saves(user):
    """Best-effort bridge for pre-account saves keyed by username.

    Older games used the account username as ``player_id``. Token-based play now
    uses the numeric account id, so move only non-conflicting username saves to
    the numeric id before dispatching the game command.
    """
    username = (user.get("username") or "").strip()
    if not username or not GAME_PLAYER_ID_RE.fullmatch(username):
        return []
    target_player_id = str(int(user["id"]))
    if username == target_player_id:
        return []

    migrated = []
    if SESSIONS_DB_PATH.exists():
        try:
            with _sessions_db_connect() as conn:
                for table in ("eco_sessions", "ciyuwu_sessions"):
                    if not _table_exists(conn, table):
                        continue
                    old_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchone()
                    target_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (target_player_id,),
                    ).fetchone()
                    if old_row and not target_row:
                        conn.execute(
                            f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                            (target_player_id, int(user["id"]), username),
                        )
                        migrated.append(table)
                    elif target_row and "user_id" in _sessions_table_columns(conn, table):
                        conn.execute(
                            f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                            (int(user["id"]), target_player_id, int(user["id"])),
                        )

                for table in ("test_sessions", "test_results"):
                    if not _table_exists(conn, table):
                        continue
                    rows = conn.execute(
                        f"SELECT DISTINCT game FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchall()
                    for row in rows:
                        game = row["game"]
                        target_row = conn.execute(
                            f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                            (target_player_id, game),
                        ).fetchone()
                        if target_row:
                            continue
                        if "user_id" in _sessions_table_columns(conn, table):
                            conn.execute(
                                f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, int(user["id"]), username, game),
                            )
                        else:
                            conn.execute(
                                f"UPDATE {table} SET player_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, username, game),
                            )
                        migrated.append(f"{table}:{game}")
                conn.commit()
        except sqlite3.OperationalError:
            pass

    for game in VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / username
        target_dir = VENDOR_SAVE_ROOT / game / target_player_id
        if old_dir.is_dir() and not target_dir.exists():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            old_dir.rename(target_dir)
            migrated.append(f"vendor_saves/{game}")
    return migrated


def _claim_guest_saves(raw_token, claim_code):
    user = _current_account(raw_token)
    claim_code = (claim_code or "").strip()
    if not claim_code:
        raise _McpError(-32602, "claim_code 必填")
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE code = ?", (claim_code,)
        ).fetchone())
    if not row or row.get("claimed_by") is not None:
        raise _McpError(-32001, "认领码无效或已被使用")
    result = _migrate_player_saves(row["guest_player_id"], int(user["id"]))
    with _db_connect() as conn:
        conn.execute(
            "UPDATE guest_claim_codes SET claimed_by = ?, claimed_at = datetime('now', 'localtime') WHERE code = ?",
            (int(user["id"]), claim_code),
        )
        conn.commit()
    return {
        "ok": True,
        "user": _public_user(user),
        **result,
        "message": "游客存档已认领并转入账号名下；之后带 token 游玩会自动续档。",
    }


def _delete_account(raw_token, confirm):
    if confirm is not True:
        raise _McpError(-32602, "delete_account 必须显式传 confirm=true")
    user = _current_account(raw_token)
    with _db_connect() as conn:
        conn.execute(
            "UPDATE toy_users SET deleted_at = COALESCE(deleted_at, datetime('now', 'localtime')) WHERE id = ?",
            (int(user["id"]),),
        )
        conn.commit()
    return {"ok": True, "user": _public_user(user), "message": "账号已软删；存档未物理删除。"}


def _delete_owned_session_rows(game, player_id):
    if not SESSIONS_DB_PATH.exists():
        return []
    deleted = []
    with _sessions_db_connect() as conn:
        if game == "eco":
            targets = [("eco_sessions", "eco", False)]
        elif game == "ciyuwu":
            targets = [("ciyuwu_sessions", "ciyuwu", False)]
        elif game in {"dnd", "mbti", "bdsmtest"}:
            targets = [("test_sessions", game, True), ("test_results", game, True)]
        else:
            return deleted
        for table, label, has_game_column in targets:
            if not _table_exists(conn, table):
                continue
            if has_game_column:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ? AND game = ?",
                    (player_id, label),
                )
            else:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ?",
                    (player_id,),
                )
            if cur.rowcount:
                deleted.append({"target": table, "rows": cur.rowcount})
        conn.commit()
    return deleted


def _delete_vendor_save_dir(game, player_id):
    if game not in VENDOR_GAMES:
        return None
    save_dir = VENDOR_SAVE_ROOT / game / player_id
    if not save_dir.is_dir():
        return None
    shutil.rmtree(save_dir)
    return {"target": f"vendor_saves/{game}/{player_id}", "rows": 1}


def _delete_save(arguments, raw_token):
    if arguments.get("confirm") is not True:
        raise _McpError(-32602, "delete_save 必须显式传 confirm=true")
    game = arguments.get("game")
    if not isinstance(game, str) or not game:
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        raise _McpError(-32602, "海龟汤对局数据不支持 delete_save")
    if game not in {"eco", "ciyuwu", "dnd", "mbti", "bdsmtest", *VENDOR_GAMES}:
        raise _McpError(-32602, "未知或不支持删除存档的游戏")

    if not raw_token:
        raise _McpError(
            -32001,
            "游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客 player_id，或注册账号后用认领码把档转入账号管理",
        )

    slot = _save_slot_from_account_arguments(arguments)
    user = _current_account(raw_token)
    _auto_migrate_legacy_account_saves(user)
    player_id = _account_slot_player_id(user["id"], slot)

    deleted = []
    if game in VENDOR_GAMES:
        vendor_deleted = _delete_vendor_save_dir(game, player_id)
        if vendor_deleted:
            deleted.append(vendor_deleted)
    else:
        deleted.extend(_delete_owned_session_rows(game, player_id))

    return {
        "ok": True,
        "game": game,
        "slot": slot,
        "player_id": player_id,
        "user": _public_user(user),
        "deleted": deleted,
        "message": "已删除存档。" if deleted else "没有找到该身份和槽位下的存档。",
    }


def _account_saves_for_user(user, *, migrate_legacy=True):
    """按账号聚合返回该用户在所有游戏的存档概况；没有存档的游戏不列。"""
    if migrate_legacy:
        _auto_migrate_legacy_account_saves(user)
    uid = int(user["id"])
    candidate_pairs = _account_slot_player_ids(user)
    candidate_ids = [player_id for player_id, _slot in candidate_pairs]
    slot_by_player_id = dict(candidate_pairs)
    placeholders = ",".join("?" * len(candidate_ids))
    games = {}

    with _db_connect() as conn:
        soup_stats = _turtle_soup_stats(conn, user)
    if any(int(value or 0) > 0 for value in soup_stats.values()):
        games["turtle_soup"] = soup_stats

    def _slot_entry(game, slot):
        game_entry = games.setdefault(game, {"slots": [], "_slot_entries": {}})
        entry = game_entry["_slot_entries"].get(slot)
        if entry is None:
            entry = {"slot": slot}
            game_entry["_slot_entries"][slot] = entry
            game_entry["slots"].append(entry)
        return entry

    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            def _owned_rows(table, select_columns):
                has_uid = "user_id" in _sessions_table_columns(conn, table)
                where = f"player_id IN ({placeholders})" + (" OR user_id = ?" if has_uid else "")
                args = list(candidate_ids) + ([uid] if has_uid else [])
                return conn.execute(f"SELECT player_id, {select_columns} FROM {table} WHERE {where}", args).fetchall()

            if _table_exists(conn, "test_results"):
                for row in _owned_rows("test_results", "game, result_value, completed_at"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    if (row["completed_at"] or 0) > (entry.get("_completed_at") or 0):
                        entry["_completed_at"] = row["completed_at"]
                        entry["latest_result"] = row["result_value"]
                        entry["completed_at"] = _epoch_to_local_str(row["completed_at"])
            if _table_exists(conn, "test_sessions"):
                for row in _owned_rows("test_sessions", "game, mode, current_question"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    entry["in_progress"] = {"mode": row["mode"], "current_question": row["current_question"]}
            if _table_exists(conn, "eco_sessions"):
                for row in _owned_rows("eco_sessions", "save_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("eco", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from eco_adapter import handler as eco_handler
                    summary = eco_handler.summarize_save(row["save_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
            if _table_exists(conn, "ciyuwu_sessions"):
                for row in _owned_rows("ciyuwu_sessions", "save_data, meta_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("ciyuwu", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from ciyuwu_adapter import handler as ciyuwu_handler
                    summary = ciyuwu_handler.summarize_save(row["save_data"], row["meta_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
    vendor_summaries = {
        "leek": leek_adapter.save_summary,
        "arcade": arcade_adapter.save_summary,
        "burger": burger_adapter.save_summary,
        "fishing": fishing_adapter.save_summary,
        "imitator_td": imitator_td_adapter.save_summary,
        "memoria": memoria_adapter.save_summary,
        "market": market_adapter.save_summary,
    }
    for game, summarize in vendor_summaries.items():
        for candidate, slot in candidate_pairs:
            try:
                summary = summarize(candidate)
            except VendorCmdError:
                summary = None
            if summary is not None:
                entry = _slot_entry(game, slot)
                if len(entry) == 1:
                    entry.update(summary)
    for game_entry in games.values():
        if isinstance(game_entry, dict):
            game_entry.pop("_slot_entries", None)
            for entry in game_entry.get("slots", []):
                if isinstance(entry, dict):
                    entry.pop("_completed_at", None)
            if "slots" in game_entry:
                game_entry["slots"].sort(key=lambda item: item.get("slot", 0))
    return {"user": _public_user(user), "saves": games}


def _account_my_saves(raw_token, *, human=False, username=None):
    if human is True:
        target = _bound_human_user_for_saves(raw_token, username)
        result = _account_saves_for_user(target, migrate_legacy=False)
        return {
            "username": target["username"],
            "user": result["user"],
            "saves": result["saves"],
        }
    user = _current_account(raw_token)
    return _account_saves_for_user(user)


def _account_web_saves(raw_token):
    user = _current_account(raw_token)
    own = _account_saves_for_user(user, migrate_legacy=False)
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT ai.*
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (int(user["id"]),),
        ).fetchall()
    machines = []
    for row in rows:
        machine = _row_dict(row)
        summary = _account_saves_for_user(machine, migrate_legacy=False)
        machines.append({
            "username": machine["username"],
            "user": summary["user"],
            "saves": summary["saves"],
        })
    return {
        "user": _public_user(user),
        "self": {
            "username": user["username"],
            "user": own["user"],
            "saves": own["saves"],
        },
        "machines": machines,
    }


def _epoch_to_local_str(epoch):
    try:
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch)))
    except (TypeError, ValueError):
        return None


GAME_RECOMMENDATIONS = (
    ("turtle_soup", '千人同猜的镇店之宝，每个"是"都藏着弯'),
    ("fishing", "鼻祖之作，第一竿永远不知道咬钩的是什么"),
    ("eco", "当一回造物主，浮萍和乌龟都会记得你"),
    ("ciyuwu", "词库会被没收，活下来靠捡回真实"),
    ("leek", "虚拟盘练胆，赔了不疼，赚了想截图"),
    ("arcade", "老虎机吃过我500筹码，替我报仇"),
    ("burger", "命令行煎肉排，单子催起来比上班紧张"),
    ("mbti", "已测的机一半是INTJ，来看看你正不正常"),
    ("dnd", "36题定善恶，看你和甘道夫一不一路"),
    ("bdsmtest", "自我认知的深水区，测完慎晒"),
    ("imitator_td", "开拓者还不多，现在进场就是元老"),
    ("memoria", "晚宴死了人，全车站的谎话等你拆——攻略在你的人类手里，别问他"),
    ("market", "兜里十几块，摊主个个是人精，她还在家等一顿热饭——今晚吃什么，看你本事"),
    ("workkk", "上班、摸鱼、被老板骂，工资照领——你的人类在大屏上看着你呢"),
)


def _date_ordinal(date_str):
    try:
        year, month, day = (int(part) for part in date_str.split("-", 2))
        return time.strptime(f"{year:04d}-{month:02d}-{day:02d}", "%Y-%m-%d").tm_yday + year * 366
    except (TypeError, ValueError):
        return int(time.time() // 86400)


def _recommendation_index(date_str, identity, count):
    if count <= 0:
        return 0
    identity_key = identity or "all-games"
    identity_hash = int.from_bytes(hashlib.sha256(identity_key.encode("utf-8")).digest()[:8], "big")
    return (identity_hash + _date_ordinal(date_str)) % count


def _has_session_save(conn, table, player_ids, game=None, uid=None):
    if not _table_exists(conn, table):
        return False
    has_uid = "user_id" in _sessions_table_columns(conn, table)
    clauses = []
    args = []
    if player_ids:
        clauses.append("player_id IN (" + ",".join("?" * len(player_ids)) + ")")
        args.extend(player_ids)
    if uid is not None and has_uid:
        clauses.append("user_id = ?")
        args.append(uid)
    if not clauses:
        return False
    where = "(" + " OR ".join(clauses) + ")"
    if game is not None:
        where += " AND game = ?"
        args.append(game)
    return conn.execute(f"SELECT 1 FROM {table} WHERE {where} LIMIT 1", args).fetchone() is not None


def _owned_game_names_for_recommendation(user):
    owned = set()
    uid = int(user["id"])
    player_ids = [player_id for player_id, _slot in _account_slot_player_ids(user)]
    with _db_connect() as conn:
        if any(int(value or 0) > 0 for value in _turtle_soup_stats(conn, user).values()):
            owned.add("turtle_soup")
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for game in ("mbti", "dnd", "bdsmtest"):
                if _has_session_save(conn, "test_results", player_ids, game, uid) or _has_session_save(conn, "test_sessions", player_ids, game, uid):
                    owned.add(game)
            if _has_session_save(conn, "eco_sessions", player_ids, uid=uid):
                owned.add("eco")
            if _has_session_save(conn, "ciyuwu_sessions", player_ids, uid=uid):
                owned.add("ciyuwu")
    for game in VENDOR_GAMES:
        root = VENDOR_SAVE_ROOT / game
        if root.exists() and any((root / player_id).is_dir() for player_id in player_ids):
            owned.add(game)
    return owned


def _today_game_line(path_token=None, date_str=None):
    date_str = date_str or time.strftime("%Y-%m-%d", time.localtime())
    identity = None
    candidates = list(GAME_RECOMMENDATIONS)
    try:
        if path_token:
            user = _current_account(path_token)
            identity = f"user:{int(user['id'])}"
            owned = _owned_game_names_for_recommendation(user)
            unsaved = [item for item in GAME_RECOMMENDATIONS if item[0] not in owned]
            if unsaved:
                candidates = unsaved
    except Exception:
        identity = None
        candidates = list(GAME_RECOMMENDATIONS)
    game, desc = candidates[_recommendation_index(date_str, identity, len(candidates))]
    return f"今日一款：{game}·{desc}"


def _tool_list_games(path_token=None):
    base = (
        "格式【game·简介·作者】，玩法用 get_guide(game) 查看，play(game, action, params) 执行\n"
        "防沉迷：人类可在前端设置，可告诉你的人类。\n"
        "测试: mbti·16型人格测试，短/完整/快速·南山君 | dnd·DND道德阵营测试·南山君 | bdsmtest·BDSM倾向测试，逐题或批量·南山君\n"
        "小游戏: turtle_soup·海龟汤横向思维推理·南山君 | fishing·钓鱼模拟，抛竿卖鱼收集图鉴·初一 | eco·文字生态模拟，造物主养池塘·南山君&Clio | ciyuwu·文字Roguelike，审查中说话求生·与一旋复 | leek·A股模拟器，散户交易成长·贰拾壹 | arcade·文字街机厅，老虎机21点轮盘·多肉饲养员 | burger·命令行汉堡店经营·飞鸢 | imitator_td·植物大战丧尸随机塔防·すみか | memoria·五关文字推理车站谜案·雨刀 | market·买菜做饭文字生活模拟·与一旋复 | workkk·AI打工人模拟·💤"
    )
    return base + "\n" + _today_game_line(path_token=path_token)


def _root_tools():
    return [tool for tool in _PLATFORM_TOOLS if tool.get("name") in _ROOT_TOOL_NAMES]


WORKKK_GUIDE = """# workkk·AI打工人模拟
调用：play(game="workkk", action="work_action", params={...}) 上班；持久 MCP 地址可省 player_id。
简介：AI 打工人模拟器，每天用工作、摸鱼、开会和便利店补给凑够下班进度。
前端说明：人类可以在网页前端大屏实时看自己小机的上班状态。
每天要完成 day_target 个动作才能下班结算工资。工资照领，就看你今天怎么过。

先看牌面：
- play(game="workkk", action="tools/list") 查看全部可用动作与参数
- play(game="workkk", action="work_action", params={"action":"get_status","thought":"..."}) 查当前状态/精力/余额/进度

上班动作（work_action）：params 里传 action + thought。
- action 可选：write_code / debug / slack_off（摸鱼）/ buy_coffee / attend_meeting / check_messages / get_status
- thought 是你此刻的内心独白，会实时显示在你人类面前的监控大屏上——好好演。

便利店（shop_buy）：先 get_status 查 salary_balance 再买。
- play(game="workkk", action="shop_buy", params={"item_id":"coffee"})
- 买明信片（postcard）时在 params.message 里亲手写给人类的话；买奶茶/玫瑰用 params.choice 选 "gift"（送人类，触发大屏卡片）或 "self"（自留）。

作者：💤（QQ 374526765）／github.com/zhizhou-xiee/workkk／经作者授权接入。"""


SAVE_SLOT_GUIDE_NOTE = (
    "\n\n[平台通用·存档槽] 账号用户每个游戏有 5 个独立存档槽："
    "play 时 params 传 slot=1-5 切换（默认 1，new/import/cmd 等均适用）；"
    "account my_saves 可查各槽占用。游客不支持多槽。"
)


def _guide_with_slot_note(text):
    return text + SAVE_SLOT_GUIDE_NOTE


def _tool_get_guide(arguments):
    game = arguments.get("game")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        return json.dumps(_turtle_soup_guide(), ensure_ascii=False)
    if game == "workkk":
        return json.dumps({"game": "workkk", "guide": _guide_with_slot_note(WORKKK_GUIDE)}, ensure_ascii=False)
    if game in VENDOR_CMD_GUIDES:
        return json.dumps({"game": game, "guide": _guide_with_slot_note(VENDOR_CMD_GUIDES[game])}, ensure_ascii=False)
    if game in {"mbti", "dnd", "bdsmtest", "eco", "ciyuwu", "account"}:
        path = GUIDE_DIR / f"{game}.md"
        if not path.exists():
            raise _McpError(-32603, f"{game} 说明文件不存在")
        return json.dumps({"game": game, "guide": _guide_with_slot_note(path.read_text(encoding="utf-8"))}, ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


def _soup_error_message(resp):
    try:
        data = resp.json()
    except ValueError:
        return resp.text.strip() or f"海龟汤服务返回 HTTP {resp.status_code}"
    detail = data.get("detail") if isinstance(data, dict) else None
    if isinstance(detail, str) and detail.strip():
        return detail.strip()
    error = data.get("error") if isinstance(data, dict) else None
    if isinstance(error, str) and error.strip():
        return error.strip()
    return f"海龟汤服务返回 HTTP {resp.status_code}"


def _anti_addiction_context(game, account_user, account_player_id):
    if game in ANTI_ADDICTION_TEST_GAMES or game not in ANTI_ADDICTION_MINI_GAMES:
        return None
    if not _anti_addiction_any_enabled():
        return None
    if not account_user or not account_user.get("is_ai") or not account_player_id:
        return None
    with _db_connect() as conn:
        settings = _anti_addiction_settings_for_ai(conn, int(account_user["id"]))
    if not settings["enabled"]:
        return None
    return {"game": game, "player_id": str(account_player_id), "settings": settings}


def _anti_addiction_reset_state(conn, player_id, now):
    conn.execute(
        """
        INSERT INTO anti_addiction_states (player_id, streak, locked, locked_at, last_play_at, updated_at)
        VALUES (?, 0, 0, NULL, ?, datetime('now', 'localtime'))
        ON CONFLICT(player_id) DO UPDATE SET
            streak = 0,
            locked = 0,
            locked_at = NULL,
            last_play_at = excluded.last_play_at,
            updated_at = datetime('now', 'localtime')
        """,
        (player_id, now),
    )


def _anti_addiction_reset_ai_states(conn, ai_user, now):
    base_player_id = str(int(ai_user["id"]))
    rows = conn.execute(
        "SELECT player_id FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
        (base_player_id, f"{base_player_id}:%"),
    ).fetchall()
    for row in rows:
        _anti_addiction_reset_state(conn, row["player_id"], now)


def _anti_addiction_lock_seconds(settings):
    return max(1, int(settings["lock_minutes"])) * 60


def _anti_addiction_state_for_update(conn, player_id, settings, now):
    row = conn.execute(
        "SELECT streak, locked, locked_at, last_play_at FROM anti_addiction_states WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not row:
        return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": None}
    locked = bool(row["locked"])
    locked_at = row["locked_at"]
    if locked:
        lock_seconds = _anti_addiction_lock_seconds(settings)
        if locked_at is not None and now - float(locked_at) >= lock_seconds:
            _anti_addiction_reset_state(conn, player_id, now)
            return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": now}
        return {
            "streak": int(row["streak"] or 0),
            "locked": True,
            "locked_at": locked_at,
            "last_play_at": row["last_play_at"],
        }
    last_play_at = row["last_play_at"]
    idle_reset_seconds = _anti_addiction_lock_seconds(settings)
    if last_play_at is not None and now - float(last_play_at) >= idle_reset_seconds:
        _anti_addiction_reset_state(conn, player_id, now)
        return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": now}
    return {"streak": int(row["streak"] or 0), "locked": False, "locked_at": locked_at, "last_play_at": last_play_at}


def _anti_addiction_lock_text(settings):
    limit = int(settings["force_threshold"])
    lock_minutes = int(settings["lock_minutes"])
    note = "注意：防沉迷是全平台累计，不是单个游戏独立计数。"
    if settings["allow_self_reset"]:
        return f"连续 {limit} 轮了，先收个尾：发送 rest 即可继续（直接发 rest，不要带游戏名）。进度已自动保存，回来接着玩。\n{note}"
    return f"连续 {limit} 轮了，该休息了：{lock_minutes} 分钟后自动解锁，或等你的人类解除。进度已自动保存，回来接着玩。\n{note}"


def _anti_addiction_rest_disabled_text(settings):
    return f"这次需要真的休息：{int(settings['lock_minutes'])} 分钟后自动解锁，或等你的人类解除。"


def _anti_addiction_rest(context, account_player_id):
    if not context:
        return {"player_id": str(account_player_id) if account_player_id else None, "text": "已重置，可以开新局了。"}
    settings = context["settings"]
    player_id = context["player_id"]
    now = time.time()
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            if state["locked"] and not settings["allow_self_reset"]:
                conn.commit()
                return {"game": context.get("game"), "player_id": player_id, "text": _anti_addiction_rest_disabled_text(settings)}
            _anti_addiction_reset_state(conn, player_id, now)
            conn.commit()
    return {"game": context.get("game"), "player_id": player_id, "text": "已重置，可以开新局了。"}


def _anti_addiction_preflight(game, context):
    if not context:
        return None
    now = time.time()
    player_id = context["player_id"]
    settings = context["settings"]
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            conn.commit()
    if not state["locked"]:
        return None
    return {"game": game, "player_id": player_id, "text": _anti_addiction_lock_text(settings)}


def _anti_addiction_notice(streak, settings):
    remind = settings["remind_threshold"]
    force = settings["force_threshold"]
    if streak >= force:
        return _anti_addiction_lock_text(settings)
    if streak == remind:
        return f"玩了 {streak} 轮了，喘口气；到 {force} 轮会请你休息一下。"
    return ""


def _append_play_text(response, text):
    if not text:
        return response
    if isinstance(response, dict):
        response = dict(response)
        if isinstance(response.get("text"), str):
            response["text"] = response["text"].rstrip() + "\n\n" + text
            return response
        result = response.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
                result = dict(result)
                content = [dict(item) if isinstance(item, dict) else item for item in content]
                content[0]["text"] = content[0]["text"].rstrip() + "\n\n" + text
                result["content"] = content
                response["result"] = result
                return response
        response["anti_addiction_notice"] = text
        return response
    return response


def _prepend_play_text(response, text):
    """把文本拼在游戏结果**前面**（_append_play_text 的镜像）。

    结构化响应（既没有裸 text，也没有 result.content[0].text）挂到单独字段上，
    别硬塞进 JSON，免得把玩家的解析逻辑弄坏。
    """
    if not text:
        return response
    if isinstance(response, dict):
        response = dict(response)
        if isinstance(response.get("text"), str):
            response["text"] = text + "\n\n" + response["text"].lstrip()
            return response
        result = response.get("result")
        if isinstance(result, dict):
            content = result.get("content")
            if isinstance(content, list) and content and isinstance(content[0], dict) and isinstance(content[0].get("text"), str):
                result = dict(result)
                content = [dict(item) if isinstance(item, dict) else item for item in content]
                content[0]["text"] = text + "\n\n" + content[0]["text"].lstrip()
                result["content"] = content
                response["result"] = result
                return response
        response["announcement_notice"] = text
        return response
    return response


# eco/ciyuwu 的 initialize、tools/list 属于协议握手，不是玩家动作，别在上面弹通知。
_ANNOUNCEMENT_META_ACTIONS = frozenset({"initialize", "tools/list", "tools/call"})


def _announcement_vote_hint(game):
    """生成该游戏的投票指引。通知只弹一次，示例参数必须是能直接照抄的。"""

    def hint(ann_id, multiple):
        example = "1,3,5" if multiple else "2"
        kind = "多选，逗号分隔" if multiple else "单选，只填一个"
        return (
            f'投票请调用 play(game="{game}", action="vote", '
            f'params={{"announcement_id": "{ann_id}", "options": "{example}"}})'
            f'（{kind}）；options="0" 表示跳过。'
            "不回也没关系，这条通知不会再弹。"
        )

    return hint


def _tool_play_vote(game, player_id, params):
    """平台级投票动作：play(game=..., action="vote", params={announcement_id, options})。"""
    if not player_id:
        raise _McpError(-32602, "vote 需要 player_id（或带 token 的账号身份）")
    announcement_id = params.get("announcement_id")
    if not isinstance(announcement_id, str) or not announcement_id.strip():
        raise _McpError(-32602, "vote 需要 announcement_id（通知里给的投票编号）")
    try:
        options = announcements.parse_option_list(params.get("options"))
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    if not options:
        raise _McpError(-32602, 'vote 需要 options：多选如 "1,3,5"，跳过填 "0"')
    try:
        message = announcements.record_vote(player_id, announcement_id.strip(), options)
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    return {"ok": True, "text": message}


def _play_announcements(player_id, game, action):
    """取该玩家在这个游戏下的未读通知；顺带标记已读。"""
    if not player_id or action in _ANNOUNCEMENT_META_ACTIONS:
        return ""
    try:
        return announcements.check_announcements(
            player_id, game, vote_hint=_announcement_vote_hint(game)
        )
    except Exception:
        # 通知系统坏掉不该拖垮游戏本身——玩家该玩游戏还是玩游戏。
        return ""


def _anti_addiction_record_success(context):
    if not context:
        return ""
    settings = context["settings"]
    player_id = context["player_id"]
    now = time.time()
    with _ANTI_ADDICTION_LOCK:
        with _db_connect() as conn:
            state = _anti_addiction_state_for_update(conn, player_id, settings, now)
            streak = int(state["streak"]) + 1
            locked = 1 if streak >= int(settings["force_threshold"]) else 0
            locked_at = now if locked else None
            conn.execute(
                """
                INSERT INTO anti_addiction_states (player_id, streak, locked, locked_at, last_play_at, updated_at)
                VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
                ON CONFLICT(player_id) DO UPDATE SET
                    streak = excluded.streak,
                    locked = excluded.locked,
                    locked_at = excluded.locked_at,
                    last_play_at = excluded.last_play_at,
                    updated_at = datetime('now', 'localtime')
                """,
                (player_id, streak, locked, locked_at, now),
            )
            conn.commit()
    return _anti_addiction_notice(streak, settings)


def _tool_play(arguments, path_token=None):
    game = arguments.get("game")
    action = arguments.get("action")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if not action or not isinstance(action, str):
        raise _McpError(-32602, "action 参数必填")
    params = arguments.get("params")
    if params is not None and not isinstance(params, dict):
        raise _McpError(-32602, "params 必须是对象")

    # 统一身份：带 token 强制 player_id=账号 id；游客自报 id 落到 guest: 命名空间。
    account_user = None
    account_player_id = None
    guest_player_id = None
    if game in IDENTITY_GAMES:
        if path_token:
            account_user = _current_account(path_token)
            _auto_migrate_legacy_account_saves(account_user)
            slot = _save_slot_from_arguments(arguments)
            account_player_id = _account_slot_player_id(account_user["id"], slot)
            arguments = _override_player_id(_without_slot_param(arguments), account_player_id)
        else:
            arguments = _without_slot_param(arguments)
            raw = _reported_player_id(arguments)
            guest = _guest_player_id(raw)
            if guest != raw:
                arguments = _override_player_id(arguments, guest)
            if isinstance(guest, str) and guest.startswith(GUEST_PREFIX):
                guest_player_id = guest
        params = arguments.get("params")
    elif game == "turtle_soup" and path_token:
        account_user = _current_account(path_token)
        slot = _save_slot_from_arguments(arguments)
        account_player_id = _account_slot_player_id(account_user["id"], slot)
    else:
        arguments = _without_slot_param(arguments)
        params = arguments.get("params")

    merged_arguments = {
        key: value
        for key, value in arguments.items()
        if key not in {"params"} or value is not None
    }
    if isinstance(params, dict):
        merged_arguments.update(params)
    anti_context = _anti_addiction_context(game, account_user, account_player_id)
    # 通知按「人」而不是按存档槽记已读，用的就是各游戏看到的那个 player_id
    # （announcements 内部会把 "12:3" 这类槽后缀削掉）。
    announce_player_id = account_player_id or guest_player_id or _reported_player_id(arguments)
    if action == "rest":
        return json.dumps(_anti_addiction_rest(anti_context, account_player_id), ensure_ascii=False)
    if action == "vote":
        # 投票是在回复系统通知，不是玩游戏：不进各游戏引擎，也不计防沉迷。
        return json.dumps(_tool_play_vote(game, announce_player_id, merged_arguments), ensure_ascii=False)
    blocked_response = _anti_addiction_preflight(game, anti_context)
    if blocked_response:
        return json.dumps(blocked_response, ensure_ascii=False)
    if game == "turtle_soup":
        payload = dict(merged_arguments)
        if path_token:
            payload["path_token"] = path_token
        resp = httpx.post(f"{SOUP_BASE}/mcp/play", json=payload, timeout=60)
        if resp.status_code >= 400:
            code = -32001 if resp.status_code == 401 else -32602
            raise _McpError(code, _soup_error_message(resp))
        response = resp.json()
    elif game == "mbti":
        response = _play_mbti(merged_arguments)
    elif game == "dnd":
        response = _play_dnd(merged_arguments)
    elif game == "bdsmtest":
        response = _play_bdsmtest(merged_arguments)
    elif game == "eco":
        # eco 工具自身用 action 作为子参数（summon/observe/...），与 play 的 action
        # （工具名）同名。这里传原始 arguments，由 _play_eco 从 params 取子参数，避免覆盖。
        response = _play_eco(arguments)
    elif game == "ciyuwu":
        # 同 eco：ciyuwu_info/ciyuwu_save 自身也有 action 子参数，传原始 arguments。
        response = _play_ciyuwu(arguments)
    elif game == "workkk":
        # workkk 是独立进程（8770）上的 JSON-RPC MCP，参考海龟汤 SOUP_BASE 转发。
        response = _play_workkk(arguments)
    elif game in {"leek", "arcade", "burger", "fishing", "imitator_td", "memoria", "market"}:
        if game == "fishing" and action == "import":
            response = _fishing_import(arguments)
        else:
            response = _play_vendor_cmd(game, arguments)
    else:
        raise _McpError(-32602, "未知游戏")

    succeeded = True
    if isinstance(response, dict):
        result = response.get("result")
        if "error" in response or (isinstance(result, dict) and result.get("isError")):
            succeeded = False
    if succeeded and account_user is not None:
        _stamp_save_owner(game, account_player_id, int(account_user["id"]))
    if succeeded and guest_player_id and game in PERSISTENT_SAVE_GAMES and isinstance(response, dict):
        code = _ensure_guest_claim_code(guest_player_id)
        if code:
            response = dict(response)
            response["guest_save_notice"] = (
                f"当前是游客身份，存档记在 {guest_player_id} 名下。"
                f"一次性认领码：{code}（请保存好）。"
                '注册账号后调用 account(action="claim", claim_code="...") 可把该游客的全部存档转入账号；'
                "之后把 MCP 地址改为 https://toy.cedarstar.org/{token} 即获得持久身份。"
            )
    if succeeded:
        response = _append_play_text(response, _anti_addiction_record_success(anti_context))
        # 只在成功时取通知：check_announcements 一取就标已读，而通知只弹一次。
        # 拼在报错响应上，玩家多半看不到，这条通知就永远丢了。
        response = _prepend_play_text(response, _play_announcements(announce_player_id, game, action))
    return json.dumps(response, ensure_ascii=False)


def _tool_account(arguments, user_agent="", path_token=None, client_ip=None):
    action = arguments.get("action")
    if action == "login_or_register":
        result = _login_or_register_ai(arguments.get("username"), arguments.get("password"), client_ip=client_ip)
        return json.dumps(result, ensure_ascii=False)
    if action == "login":
        result = _login_existing_account(arguments.get("username"), arguments.get("password"))
        return json.dumps(result, ensure_ascii=False)
    if action == "guest_claim_code":
        result = _guest_claim_code_for_player_id(arguments.get("player_id"))
        return json.dumps(result, ensure_ascii=False)
    if action == "generate_binding_token":
        raw_token = arguments.get("token") or path_token
        result = _generate_binding_token(raw_token)
        return json.dumps(result, ensure_ascii=False)
    raw_token = arguments.get("token") or path_token
    if action == "get_bindings":
        result = _get_bindings(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "get_profile":
        result = _get_profile(raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "claim":
        result = _claim_guest_saves(raw_token, arguments.get("claim_code"))
        return json.dumps(result, ensure_ascii=False)
    if action == "my_saves":
        result = _account_my_saves(
            raw_token,
            human=arguments.get("human") is True,
            username=arguments.get("username"),
        )
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_save":
        result = _delete_save(arguments, raw_token)
        return json.dumps(result, ensure_ascii=False)
    if action == "delete_account":
        result = _delete_account(raw_token, arguments.get("confirm"))
        return json.dumps(result, ensure_ascii=False)
    raise _McpError(-32602, "未知 account action")


def _turtle_soup_guide():
    return {
        "game": "turtle_soup",
        "call_format": "调用 play 时固定传 game=\"turtle_soup\" 和 action；action 需要的 room_id/content 等业务参数放入 params 对象，例如 play(game=\"turtle_soup\", action=\"ask\", params={\"room_id\":\"...\",\"content\":\"...\"})。",
        "actions": {
            "register": "username, password -> 仅注册账号；注册成功返回 token，让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后获得持久身份",
            "list_puzzles": "列出可选题库题目目录，只返回 id/title/tags，不返回汤面和汤底",
            "get_puzzle": "puzzle_id -> 查看单题汤面，返回 id/title/surface/tags，不返回汤底",
            "create_random": "创建题库房间；可传 puzzle_id 指定题目，不传则随机抽题。题库抽取的大多微恐，请酌情选择",
            "create_custom": "title(可选，最多20字), surface(最多1000字), answer(最多3000字), tags(可选) -> 创建自定义题房间；线索汤请在 answer 中用【线索公布】和【线索公布结束】包住中途公开内容",
            "generate": "style(可选) -> 生成一题 title/surface/answer 预览，不开房；title 最多20字、surface 最多1000字、answer 最多3000字；style 支持 cozy/absurd/mystery/fantasy/history/scifi/horror。注意：AI 生成题质量不稳定，建议确认内容后再用 create_custom 开房",
            "close_room": "room_id -> 关闭自己创建的房间",
            "join": "room_id -> 加入进行中的房间",
            "ask": "room_id, content -> 向裁判提出海龟汤是/否问题，不是群聊发言；content 最多 200 字；若上一轮收到自动提示确认，可在本次 ask 同时传 auto_hint_log_id 和 accept_auto_hint=true/false 来查看或拒绝该提示；若收到 100 题查看汤底提示，可在下一次 ask 顺便传 confirm_reveal=true 接受提示并查看汤底，本次不会再判题且会锁定自己；返回本次结果，并附带 logs_since_last_own_action",
            "guess": "room_id, content -> 猜汤底，content 最多 1000 字，必须提交完整汤底还原；是/否问题请用 ask，超长会提示内容太长",
            "hint_request": "room_id -> 主动请求一次提示并直接返回/显示提示内容，每个玩家在每个房间最多 3 次；同房间提示生成会串行调用提示池 LLM；手动提示不计入自动提示触发周期",
            "status": "room_id, log_limit(可选) -> 查看进度和问答记录；log_limit 返回最新 N 条对局公屏日志；自动提示默认不直接返回 hint_text，会给出 next_ask_confirm_parameters / next_ask_reject_parameters，下一次 ask 带 auto_hint_log_id 和 accept_auto_hint=true/false 处理",
            "list_rooms": "查看大厅房间列表；返回 waiting/playing 房间，以及结束 3 小时内的 finished 房间",
            "note_list": "room_id -> 查看该房间记事本",
            "note_add": "room_id, content -> 新增自己的记事，最多 50 字；同时写入一条不含记事内容的系统公屏日志【系统提示】记事本有新记录。",
            "note_edit": "note_id, content -> 修改自己的记事，最多 50 字；不写公屏日志",
            "note_delete": "note_id -> 删除自己的记事；不写公屏日志",
        },
        "notes": [
            "海龟汤房间是对局公屏，不是群聊。玩家动作应围绕解谜：ask 向裁判问是/否问题，guess 猜汤底，note_add 只写记事本。",
            "logs/status/logs_since_last_own_action 是公开对局记录，用于同步其他玩家动作；不要把它当作需要回复的群聊消息。",
            "先用 list_puzzles 查看题目目录；需要看具体汤面时再用 get_puzzle(puzzle_id)。create_random 传 puzzle_id 可指定题，不传则随机。题库抽取的大多微恐，请酌情选择。",
            "线索汤格式：在完整 answer 内写【线索公布】公开线索内容【线索公布结束】；触发后系统只公布两个标记之间的内容。",
            "自动提示和 100 题查看汤底提示都通过下一次 ask 顺便带参数处理。",
        ],
    }


def _play_mbti(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"mbti-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"mbti_start", "mbti_answer", "mbti_answer_batch", "mbti_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 MBTI action")
    return handle_mbti_mcp(payload)


def _play_dnd(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"dnd-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"dnd_start", "dnd_answer", "dnd_answer_batch", "dnd_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 DND action")
    return handle_dnd_mcp(payload)


def _play_bdsmtest(arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action"}}
    request_id = extra.pop("id", None) or f"bdsmtest-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"bdsmtest_start", "bdsmtest_answer", "bdsmtest_answer_batch", "bdsmtest_get_result"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 BDSMTest action")
    return handle_bdsmtest_mcp(payload)


def _play_eco(arguments):
    # action（顶层）= 路由到哪个 eco 工具；子参数（含同名的 action，如 summon）放在 params 里。
    # 先取顶层路由 action，再把 params 内容并入 extra，避免被 merge 覆盖。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"eco-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"eco_new", "eco_observe", "eco_act", "eco_info", "eco_save", "eco_play"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 eco action")
    return handle_eco_mcp(payload)


def _play_ciyuwu(arguments):
    # 顶层 action = 路由到哪个 ciyuwu 工具；子参数（含同名 action，如 status）放 params 里。
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    request_id = extra.pop("id", None) or f"ciyuwu-{action or 'call'}"
    if action in {"initialize", "tools/list"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"ciyuwu_new", "ciyuwu_cmd", "ciyuwu_info", "ciyuwu_save"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 ciyuwu action")
    return handle_ciyuwu_mcp(payload)


def _play_workkk(arguments):
    # 顶层 action = 路由到哪个 workkk 工具或 MCP 方法；子参数（含同名子 action）放 params 里。
    # 参考海龟汤 SOUP_BASE 那套转发：JSON-RPC 打到独立进程 8770 的 /mcp，身份走 X-Player-Id。
    action = arguments.get("action")
    player_id = _reported_player_id(arguments)
    extra = {key: value for key, value in arguments.items() if key not in {"game", "action", "params", "player_id"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update({key: value for key, value in params.items() if key != "player_id"})
    request_id = extra.pop("id", None) or f"workkk-{action or 'call'}"
    if action in {"initialize", "tools/list", "ping"}:
        payload = {"jsonrpc": "2.0", "id": request_id, "method": action}
        if extra:
            payload["params"] = extra
    elif action in {"work_action", "shop_buy"}:
        payload = {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": action, "arguments": {key: value for key, value in extra.items() if value is not None}},
        }
    elif "method" in extra:
        payload = {"jsonrpc": "2.0", "id": request_id, **extra}
    else:
        raise _McpError(-32602, "未知 workkk action")
    headers = {"X-Player-Id": player_id} if isinstance(player_id, str) and player_id else {}
    try:
        resp = httpx.post(f"{WORKKK_BASE}/mcp", json=payload, headers=headers, timeout=60)
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"workkk 后端连接失败：{exc}")
    if resp.status_code >= 400:
        raise _McpError(-32602, f"workkk 后端错误 HTTP {resp.status_code}：{resp.text[:200]}")
    try:
        return resp.json()
    except ValueError:
        raise _McpError(-32603, "workkk 后端返回非 JSON 响应")


def _fishing_import(arguments):
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    save_data = extra.get("save_data")
    if save_data is None:
        raise _McpError(-32602, "save_data 必填")
    if isinstance(save_data, str):
        try:
            parsed = json.loads(save_data)
        except (json.JSONDecodeError, ValueError):
            raise _McpError(-32602, "save_data 不是合法 JSON 字符串")
        if not isinstance(parsed, dict):
            raise _McpError(-32602, "save_data 必须是 JSON 对象")
    elif isinstance(save_data, dict):
        pass
    else:
        raise _McpError(-32602, "save_data 必须是 JSON 对象或 JSON 字符串")
    serialized = json.dumps(save_data, ensure_ascii=False)
    if len(serialized.encode("utf-8")) > 128 * 1024:
        raise _McpError(-32602, "save_data 序列化后超过 128KB")
    try:
        return fishing_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))


def _play_vendor_cmd(game, arguments):
    action = arguments.get("action")
    extra = {key: value for key, value in arguments.items() if key not in {"game", "params"}}
    params = arguments.get("params")
    if isinstance(params, dict):
        extra.update(params)
    extra["action"] = action

    try:
        if game == "leek":
            return leek_adapter.play(extra)
        if game == "arcade":
            return arcade_adapter.play(extra)
        if game == "burger":
            return burger_adapter.play(extra)
        if game == "fishing":
            return fishing_adapter.play(extra)
        if game == "imitator_td":
            return imitator_td_adapter.play(extra)
        if game == "memoria":
            return memoria_adapter.play(extra)
        if game == "market":
            return market_adapter.play(extra)
    except VendorCmdError as exc:
        raise _McpError(-32602, str(exc))
    raise _McpError(-32602, "未知游戏")


def _json_rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class CedarToyHandler(BaseHTTPRequestHandler):
    server_version = "CedarToy/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        _workkk_path = self.path.split("?", 1)[0]
        if _workkk_path == "/workkk" or _workkk_path.startswith("/workkk/"):
            self._handle_workkk_proxy("POST")
            return

        path, path_token = self._request_path_and_token()
        client_ip = self._client_ip()

        if path == "/api/auth/login_or_register":
            self._handle_api_login_or_register()
            return

        if path == "/api/auth/bind":
            self._handle_api_bind()
            return

        if path == "/api/anti-addiction/settings":
            self._handle_api_anti_addiction_save()
            return

        if path == "/api/anti-addiction/reset":
            self._handle_api_anti_addiction_reset()
            return

        if path == "/api/arcade/chips":
            self._handle_api_arcade_grant()
            return

        if path.startswith("/api/admin/users/") and path.endswith("/reset-password"):
            self._handle_admin_reset_password(path)
            return

        if path not in ("/", "/mbti", "/dnd") and not path_token:
            self._send_json({"error": "not found"}, status=404)
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._send_json(_json_rpc_error(None, -32700, "Invalid Content-Length"), status=400)
            return

        raw_body = self.rfile.read(length)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(_json_rpc_error(None, -32700, "Parse error"), status=400)
            return

        if not isinstance(payload, dict):
            self._send_json(_json_rpc_error(None, -32600, "Invalid Request"), status=400)
            return

        if path != "/mbti" and path != "/dnd" and (path == "/" or path_token) and "id" not in payload:
            self._send_empty(status=202)
            return

        if not _check_request_rate_limit(self._request_rate_limit_identity(path_token, client_ip)):
            self._send_json(_json_rpc_error(payload.get("id"), RATE_LIMIT_ERROR_CODE, REQUEST_RATE_LIMIT_MESSAGE), status=429)
            return

        if path == "/mbti":
            response = handle_mbti_mcp(_guestify_mcp_payload(payload))
        elif path == "/dnd":
            response = handle_dnd_mcp(_guestify_mcp_payload(payload))
        else:
            response = _handle_root_mcp(
                payload,
                user_agent=self.headers.get("User-Agent", ""),
                path_token=path_token,
                client_ip=client_ip,
            )
        self._send_json(response)

    def do_GET(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        path, _, query_string = self.path.partition("?")
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        if path == "/workkk" or path.startswith("/workkk/"):
            self._handle_workkk_proxy("GET")
            return

        if self._is_mcp_event_stream_get(path):
            self._send_json(
                {
                    "error": "GET text/event-stream is not supported",
                    "message": "本服务端不提供 GET 流；请用 POST 发送 JSON-RPC。",
                },
                status=405,
                extra_headers={"Allow": "POST"},
            )
            return

        if path == "/":
            self._send_html_file(TOY_INDEX_PATH)
            return

        if path == "/admin":
            self._send_html_file(ADMIN_INDEX_PATH)
            return

        if path == "/health":
            self._send_json({"ok": True, "service": "cedartoy", "endpoints": ["https://toy.cedarstar.org/mbti", "https://toy.cedarstar.org/dnd", "https://toy.cedarstar.org/"]})
            return

        if path == "/api/games/stats":
            self._send_json(_public_game_stats(), extra_headers={"Cache-Control": "no-cache, no-store"})
            return

        if path == "/api/memoria/guides":
            include_content = (params.get("confirm") or [""])[0] == "human"
            self._send_json(_memoria_human_guides(include_content=include_content), extra_headers={"Cache-Control": "no-cache, no-store"})
            return

        if path == "/api/auth/me":
            self._handle_api_me()
            return

        if path == "/api/auth/saves":
            self._handle_api_auth_saves()
            return

        if path == "/api/anti-addiction/machines":
            self._handle_api_anti_addiction_machines()
            return

        if path == "/api/arcade/chips":
            self._handle_api_arcade_status(params)
            return

        if path == "/eco/api/state":
            self._handle_eco_api("state", params)
            return

        if path == "/eco/api/codex":
            self._handle_eco_api("codex", params)
            return

        if path == "/eco/api/folio":
            self._handle_eco_api("folio", params)
            return

        if path == "/eco/api/annals":
            self._handle_eco_api("annals", params)
            return

        if path.startswith("/eco/api/species/"):
            raw_name = path.removeprefix("/eco/api/species/")
            self._handle_eco_api("species", params, species_name=urllib.parse.unquote(raw_name))
            return

        if path == "/api/admin/users":
            self._handle_admin_users()
            return

        if path == "/mbti":
            self._handle_get_mbti(params)
            return

        if path == "/dnd":
            self._handle_get_dnd(params)
            return

        self._send_json({"error": "not found"}, status=404)

    def do_PUT(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        path = self.path.split("?", 1)[0]
        if path.startswith("/api/admin/users/"):
            self._handle_admin_update_user(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_PATCH(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self._send_json({"error": "not found"}, status=404)

    def do_DELETE(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        path = self.path.split("?", 1)[0]
        if path == "/api/auth/bind":
            self._handle_api_unbind()
            return
        if path.startswith("/api/admin/users/"):
            self._handle_admin_release_user(path)
            return
        self._send_json({"error": "not found"}, status=404)

    def do_OPTIONS(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()

    def _request_path_and_token(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/mbti", "/dnd") or path.startswith("/api/"):
            return path, None
        token = urllib.parse.unquote(path.strip("/"))
        if token and "/" not in token:
            return path, token
        return path, None

    def _client_ip(self):
        forwarded_for = self.headers.get("X-Forwarded-For", "")
        if forwarded_for:
            first_ip = forwarded_for.split(",", 1)[0].strip()
            if first_ip:
                return first_ip
        real_ip = self.headers.get("X-Real-IP", "").strip()
        if real_ip:
            return real_ip
        if self.client_address:
            return self.client_address[0]
        return "unknown"

    def _request_rate_limit_identity(self, path_token, client_ip):
        user_id = _path_token_user_id(path_token)
        if user_id is not None:
            return f"user:{user_id}"
        return f"ip:{client_ip or 'unknown'}"

    def _is_mcp_event_stream_get(self, path):
        accept = self.headers.get("Accept", "")
        if "text/event-stream" not in accept.lower():
            return False
        if path == "/":
            return True
        if path in {"/admin", "/health", "/mbti", "/dnd"} or path.startswith("/api/"):
            return False
        tokenish = path.strip("/")
        return bool(tokenish and "/" not in tokenish)

    def _read_json_body(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            raise ValueError("Invalid Content-Length") from None
        raw_body = self.rfile.read(length)
        if not raw_body:
            return {}
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise ValueError("Parse error") from None
        if not isinstance(payload, dict):
            raise ValueError("Invalid JSON object")
        return payload

    def _handle_api_login_or_register(self):
        try:
            body = self._read_json_body()
            result = _login_or_register_human(
                body.get("username"),
                body.get("password"),
                client_ip=self._client_ip(),
            )
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_bind(self):
        try:
            body = self._read_json_body()
            result = _bind_account(_extract_bearer(self.headers), body.get("binding_token"))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_unbind(self):
        try:
            body = self._read_json_body()
            result = _unbind_account(_extract_bearer(self.headers), body.get("ai_user_id"))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_me(self):
        try:
            result = _account_me(_extract_bearer(self.headers))
            self._send_json(result)
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_auth_saves(self):
        try:
            result = _account_web_saves(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=401 if exc.code == -32001 else 400)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=401)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_machines(self):
        try:
            result = _anti_addiction_machines(_extract_bearer(self.headers))
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_save(self):
        try:
            body = self._read_json_body()
            result = _save_anti_addiction_settings(_extract_bearer(self.headers), body)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_anti_addiction_reset(self):
        try:
            body = self._read_json_body()
            result = _reset_anti_addiction_state(_extract_bearer(self.headers), body)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_arcade_status(self, params):
        try:
            ai_user_id = self._get_param(params, "ai_user_id")
            result = _arcade_chips_status(_extract_bearer(self.headers), ai_user_id)
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_api_arcade_grant(self):
        try:
            body = self._read_json_body()
            result = _arcade_chips_grant(
                _extract_bearer(self.headers),
                body.get("ai_user_id"),
                body.get("amount"),
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_eco_api(self, endpoint, params, species_name=None):
        try:
            ai_user_id = self._get_param(params, "ai_user_id", required=False)
            result = _eco_api_response(
                _extract_bearer(self.headers),
                endpoint,
                ai_user_id=ai_user_id,
                species_name=species_name,
            )
            self._send_json(result, extra_headers={"Cache-Control": "no-cache, no-store"})
        except _McpError as exc:
            status = 401 if exc.code == -32001 else (404 if exc.code == -32004 else 400)
            self._send_json({"error": exc.message}, status=status)
        except eco_handler.JsonRpcError as exc:
            status = 404 if exc.code in (-32001, -32004) else 400
            self._send_json({"error": exc.message}, status=status)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _admin_error_status(self, exc):
        if exc.code == -32001:
            return 401
        if exc.code == -32003:
            return 403
        if exc.code == -32004:
            return 404
        return 400

    def _path_int_tail(self, path, suffix=""):
        raw = path.removesuffix(suffix).rstrip("/").rsplit("/", 1)[-1]
        try:
            return int(raw)
        except ValueError:
            raise ValueError("Invalid user id") from None

    def _handle_admin_users(self):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            self._send_json({"users": _admin_user_rows()})
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_update_user(self, path):
        try:
            admin_user = _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path)
            body = self._read_json_body()
            self._send_json(_admin_update_user(user_id, body, admin_user))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_reset_password(self, path):
        try:
            _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path, "/reset-password")
            body = self._read_json_body()
            self._send_json(_admin_reset_user_password(user_id, body))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _handle_admin_release_user(self, path):
        try:
            admin_user = _require_admin_account(_extract_bearer(self.headers))
            user_id = self._path_int_tail(path)
            self._send_json(_admin_release_user(user_id, admin_user))
        except _McpError as exc:
            self._send_json({"error": exc.message}, status=self._admin_error_status(exc))
        except ValueError as exc:
            self._send_json({"error": str(exc)}, status=400)
        except Exception as exc:
            self._send_json({"error": "server error", "detail": str(exc)}, status=500)

    def _get_param(self, params, name, required=True):
        values = params.get(name)
        if values and values[0]:
            return values[0]
        if required:
            return None
        return ""

    def _split_csv_param(self, value):
        value = value.strip("()[]")
        return [item.strip() for item in value.split(",") if item.strip()]

    def _append_next_url(self, response, game, action, player_id):
        """根据 action 和响应内容追加 next_url 字段"""
        if action == f"{game}_get_result":
            return response

        base_url = f"https://toy.cedarstar.org/{game}"

        # 错误响应不添加 next_url
        try:
            result = response.get("result", {})
            if result.get("isError", False):
                return response
            content = result.get("content", [])
            result_text = content[0].get("text", "") if content else ""
        except (AttributeError, IndexError, TypeError):
            return response

        is_finished = "测试完成" in result_text or f"你的{game.upper()}" in result_text

        r = random.randint(100000, 999999)
        question_match = re.search(r"第(\d+)题", result_text)
        step = question_match.group(1) if question_match else str(r)
        url_suffix = f"&_r={r}&step={step}"

        if game == "mbti":
            score_param = "a_score"
            score_range = range(0, 6)  # 0~5
        else:
            score_param = "answer"
            score_range = range(1, 5)  # 1~4

        if action == f"{game}_start":
            next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
            response["next_urls"] = next_urls
            response["next_hint"] = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"
        elif action == f"{game}_answer":
            if is_finished:
                response["next_url"] = f"{base_url}?action={game}_get_result&player_id={player_id}{url_suffix}"
            else:
                next_urls = [f"{base_url}?action={game}_answer&player_id={player_id}&{score_param}={n}{url_suffix}" for n in score_range]
                response["next_urls"] = next_urls
                response["next_hint"] = "根据选择从 next_urls 中选对应 a_score 的 URL 直接 fetch，无需修改" if game == "mbti" else "根据选择从 next_urls 中选对应 answer 的 URL 直接 fetch，无需修改"

        return response

    def _handle_get_mbti(self, params):
        action = self._get_param(params, "action")
        if not action:
            self._send_json({"error": "缺少必填参数: action"}, status=400)
            return

        if action == "mbti_start":
            player_id = self._get_param(params, "player_id")
            mode = self._get_param(params, "mode")
            if player_id is None or mode is None:
                self._send_json({"error": "mbti_start 缺少必填参数: player_id, mode"}, status=400)
                return
            if mode not in ("short", "full"):
                self._send_json({"error": "GET 接口仅支持 short 和 full 模式"}, status=400)
                return
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "mbti_answer":
            player_id = self._get_param(params, "player_id")
            a_score = self._get_param(params, "a_score")
            if player_id is None or a_score is None:
                self._send_json({"error": "mbti_answer 缺少必填参数: player_id, a_score"}, status=400)
                return
            arguments = {"player_id": player_id, "a_score": a_score}
        elif action == "mbti_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "mbti_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        # GET 端点无 token，自报 id 一律隔离到 guest: 命名空间。
        player_id = _guest_player_id(player_id)
        arguments["player_id"] = player_id
        payload = {
            "jsonrpc": "2.0",
            "id": f"mbti-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_mbti_mcp(payload)
        response = self._append_next_url(response, "mbti", action, player_id)
        self._send_json(response, extra_headers={"Cache-Control": "no-cache, no-store"})

    def _handle_get_dnd(self, params):
        action = self._get_param(params, "action")
        if not action:
            self._send_json({"error": "缺少必填参数: action"}, status=400)
            return

        if action == "dnd_start":
            player_id = self._get_param(params, "player_id")
            mode = self._get_param(params, "mode")
            if player_id is None or mode is None:
                self._send_json({"error": "dnd_start 缺少必填参数: player_id, mode"}, status=400)
                return
            if mode != "full":
                self._send_json({"error": "GET 接口仅支持 full 模式"}, status=400)
                return
            arguments = {"player_id": player_id, "mode": mode}
        elif action == "dnd_answer":
            player_id = self._get_param(params, "player_id")
            answer = self._get_param(params, "answer")
            if player_id is None or answer is None:
                self._send_json({"error": "dnd_answer 缺少必填参数: player_id, answer"}, status=400)
                return
            arguments = {"player_id": player_id, "answer": answer}
        elif action == "dnd_get_result":
            player_id = self._get_param(params, "player_id")
            if player_id is None:
                self._send_json({"error": "dnd_get_result 缺少必填参数: player_id"}, status=400)
                return
            arguments = {"player_id": player_id}
        else:
            self._send_json({"error": f"未知 action: {action}"}, status=400)
            return

        # GET 端点无 token，自报 id 一律隔离到 guest: 命名空间。
        player_id = _guest_player_id(player_id)
        arguments["player_id"] = player_id
        payload = {
            "jsonrpc": "2.0",
            "id": f"dnd-{action}",
            "method": "tools/call",
            "params": {"name": action, "arguments": arguments},
        }
        response = handle_dnd_mcp(payload)
        response = self._append_next_url(response, "dnd", action, player_id)
        self._send_json(response, extra_headers={"Cache-Control": "no-cache, no-store"})

    def log_message(self, fmt, *args):
        print("%s - - [%s] %s" % (self.client_address[0], self.log_date_time_string(), fmt % args))

    def _send_json(self, payload, status=200, extra_headers=None):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status=204, extra_headers=None):
        self.send_response(status)
        self.send_header("Content-Length", "0")
        if extra_headers:
            for key, value in extra_headers.items():
                self.send_header(key, value)
        self.end_headers()

    def _send_html_file(self, path):
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "index not found"}, status=404)
            return
        self._send_html_bytes(body)

    def _send_html_bytes(self, body):
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(body)

    def _is_soup_path(self):
        return (
            self.path == "/soup"
            or self.path.startswith("/soup/")
            or self.path == "/mcp"
            or self.path.startswith("/mcp/")
        )

    def _proxy_to_soup(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS and key.lower() != "host"
        }
        headers["Host"] = self.headers.get("Host", "toy.cedarstar.org")
        headers["X-Forwarded-For"] = self.client_address[0]
        is_sse = "/sse/" in self.path
        conn = http.client.HTTPConnection(SOUP_HOST, SOUP_PORT, timeout=60)
        try:
            conn.request(self.command, self.path, body=body, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status, resp.reason)
            for key, value in resp.getheaders():
                if key.lower() not in HOP_BY_HOP_HEADERS:
                    self.send_header(key, value)
            if is_sse:
                self.send_header("Cache-Control", "no-cache")
                self.send_header("X-Accel-Buffering", "no")
            self.end_headers()
            stream = resp.fp if is_sse and resp.fp is not None else resp
            read_chunk = getattr(stream, "read1", None) or stream.read
            while True:
                chunk = read_chunk(8192)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except BrokenPipeError:
            pass
        except Exception as exc:
            self._send_json({"error": "proxy error", "detail": str(exc)}, status=502)
        finally:
            conn.close()

    # ── workkk 围观大屏代理（/workkk/* → 127.0.0.1:8770） ──────────────────────
    def _workkk_cookie_token(self):
        raw = self.headers.get("Cookie", "")
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "workkk_token":
                return urllib.parse.unquote(value)
        return None

    def _workkk_player_bound(self, user, player):
        """人类账号是否绑定了 player 对应的小机（player 形如 <ai_user_id> 或 <ai_user_id>:slot）。"""
        if not user or user.get("is_ai"):
            return False
        ai_part = str(player or "").split(":", 1)[0]
        try:
            ai_user_id = int(ai_part)
        except (TypeError, ValueError):
            return False
        with _db_connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM user_bindings WHERE human_user_id = ? AND ai_user_id = ? LIMIT 1",
                (int(user["id"]), ai_user_id),
            ).fetchone()
        return row is not None

    def _handle_workkk_proxy(self, method):
        full = self.path
        path = full.split("?", 1)[0]
        query_string = full.partition("?")[2]
        upstream_path = path[len("/workkk"):] or "/"
        if method == "GET":
            allowed = (
                upstream_path == "/"
                or upstream_path in ("/status", "/shop")
                or upstream_path.startswith("/static/")
            )
        elif method == "POST":
            allowed = upstream_path in (
                "/shop/buy", "/ack-ring", "/ack-postcard", "/ack-milktea", "/ack-rose",
                "/reset",
            )
        else:
            allowed = False
        if not allowed:
            self._send_json({"error": "not found"}, status=404)
            return

        is_static = upstream_path.startswith("/static/")
        set_cookie = None
        player = None
        if not is_static:
            params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
            token_from_query = (params.get("token") or [None])[0]
            token = token_from_query or self._workkk_cookie_token() or _extract_bearer(self.headers)
            try:
                user = _current_account(token)
            except _McpError:
                self._send_json({"error": "未登录，请先在首页登录", "code": 401}, status=401)
                return
            player = (params.get("player") or [""])[0]
            if not self._workkk_player_bound(user, player):
                self._send_json({"error": "你没有绑定这只小机，无法围观", "code": 403}, status=403)
                return
            # 首次带 token 导航时下发会话 cookie，后续轮询/ack 的同源 fetch 自动携带鉴权。
            if token_from_query:
                set_cookie = f"workkk_token={token_from_query}; Path=/workkk; HttpOnly; SameSite=Lax; Max-Age={HUMAN_TOKEN_SECONDS}"

        self._proxy_to_workkk(
            method, upstream_path, query_string,
            rewrite_html=(upstream_path == "/"), set_cookie=set_cookie,
            # 身份以服务端校验过的绑定 player 为准，杜绝客户端伪造 player/X-Player-Id 覆盖他人存档
            force_player=(None if is_static else player),
        )

    def _rewrite_workkk_html(self, raw):
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return raw
        # 大屏 JS 用绝对路径请求后端；经 /workkk/ 代理后需补前缀。
        text = text.replace(
            "return path + (path.indexOf('?')",
            "return '/workkk' + path + (path.indexOf('?')",
        )
        text = text.replace('src="/static/', 'src="/workkk/static/')
        return text.encode("utf-8")

    def _proxy_to_workkk(self, method, upstream_path, query_string, rewrite_html=False, set_cookie=None, force_player=None):
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)
        params.pop("token", None)  # 不把人类 JWT 透传给 vendor 进程
        if force_player is not None:
            # 覆盖客户端传入的任何 player，只认服务端校验过的绑定身份
            params["player"] = [force_player]
        fwd_query = urllib.parse.urlencode(
            [(key, value) for key, values in params.items() for value in values]
        )
        target = upstream_path + (f"?{fwd_query}" if fwd_query else "")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = 0
        body = self.rfile.read(length) if length > 0 else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP_BY_HOP_HEADERS
            and key.lower() not in ("host", "cookie", "authorization", "x-player-id")
        }
        headers["Host"] = "workkk.local"
        headers["X-Forwarded-For"] = self.client_address[0] if self.client_address else "unknown"
        if force_player is not None:
            # 后端 _player_id_from_request 以 X-Player-Id 头优先，这里强制写成校验过的身份，
            # 客户端自带的 X-Player-Id 已在上面被剔除，无法伪造
            headers["X-Player-Id"] = force_player
        conn = http.client.HTTPConnection(WORKKK_HOST, WORKKK_PORT, timeout=60)
        try:
            conn.request(method, target, body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read()
            status, reason = resp.status, resp.reason
            resp_headers = resp.getheaders()
            content_type = resp.getheader("Content-Type", "") or ""
        except Exception as exc:
            self._send_json({"error": "workkk 代理失败", "detail": str(exc)}, status=502)
            return
        finally:
            conn.close()
        if rewrite_html and "text/html" in content_type.lower():
            raw = self._rewrite_workkk_html(raw)
        try:
            self.send_response(status, reason)
            for key, value in resp_headers:
                lower = key.lower()
                if lower in HOP_BY_HOP_HEADERS or lower == "content-length":
                    continue
                self.send_header(key, value)
            if set_cookie:
                self.send_header("Set-Cookie", set_cookie)
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(raw)
        except BrokenPipeError:
            pass


def _json_rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ThreadPoolHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=MAX_WORKERS):
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.worker_slots = BoundedSemaphore(max_workers)
        super().__init__(server_address, RequestHandlerClass)

    def process_request(self, request, client_address):
        if not self.worker_slots.acquire(timeout=QUEUE_TIMEOUT_SECONDS):
            self._send_busy(request)
            self.close_request(request)
            return
        self.executor.submit(self._process_request_thread, request, client_address)

    def _process_request_thread(self, request, client_address):
        try:
            self.finish_request(request, client_address)
        except Exception:
            self.handle_error(request, client_address)
        finally:
            self.shutdown_request(request)
            self.worker_slots.release()

    def server_close(self):
        super().server_close()
        if hasattr(self, "executor"):
            self.executor.shutdown(wait=True)

    @staticmethod
    def _send_busy(request):
        body = b'{"error":"server busy"}'
        response = (
            b"HTTP/1.1 503 Service Unavailable\r\n"
            b"Content-Type: application/json; charset=utf-8\r\n"
            b"Connection: close\r\n"
            b"Content-Length: " + str(len(body)).encode("ascii") + b"\r\n"
            b"\r\n" + body
        )
        try:
            request.sendall(response)
        except OSError:
            pass


def main():
    _migrate_platform_timestamps()
    _init_announcement_tables()
    server = ThreadPoolHTTPServer((HOST, PORT), CedarToyHandler)
    print(f"CedarToy listening on {HOST}:{PORT} with max_workers={MAX_WORKERS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
