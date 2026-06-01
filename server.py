import json
import base64
import hashlib
import hmac
import http.client
import os
import random
import re
import secrets
import sqlite3
import time
import urllib.parse
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import BoundedSemaphore

import httpx

try:
    from passlib.context import CryptContext
except ImportError:
    CryptContext = None

from dnd.handler import handle_mcp as handle_dnd_mcp
from mbti.handler import handle_mcp as handle_mbti_mcp


HOST = "0.0.0.0"
PORT = 8002
MAX_WORKERS = 50
QUEUE_TIMEOUT_SECONDS = 10
SOUP_HOST = "127.0.0.1"
SOUP_PORT = 8012
SOUP_BASE = f"http://{SOUP_HOST}:{SOUP_PORT}"
TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
HUMAN_TOKEN_SECONDS = 30 * 24 * 60 * 60
BINDING_TOKEN_SECONDS = 10 * 60
TURTLE_DB_PATH = Path(os.getenv("TURTLE_SOUP_DB", Path(__file__).resolve().parent / "turtle-soup" / "backend" / "turtle_soup.db"))
SESSIONS_DB_PATH = Path(os.getenv("SESSIONS_DB", Path(__file__).resolve().parent / "data" / "sessions.db"))
GAME_PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,10}$")
GUIDE_DIR = Path(__file__).resolve().parent / "turtle-soup" / "backend" / "guides"
TOY_INDEX_PATH = Path(__file__).resolve().parent / "index.html"
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
        "description": "执行游戏操作，参数详见 get_guide(game)",
        "inputSchema": {
            "type": "object",
            "properties": {
                "game": {
                    "type": "string",
                    "description": "游戏名称",
                },
                "action": {
                    "type": "string",
                    "description": "操作名称",
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
                    "description": "login_or_register、login、generate_binding_token、get_profile、get_bindings",
                },
                "username": {"type": "string"},
                "password": {"type": "string"},
                "token": {"type": "string"},
            },
            "required": ["action"],
            "additionalProperties": False,
        },
    },
]
_ROOT_TOOL_NAMES = frozenset({"list_games", "get_guide", "play", "account"})


def _handle_root_mcp(payload, user_agent="", path_token=None):
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
                    text = _tool_list_games()
                elif name == "get_guide":
                    text = _tool_get_guide(arguments)
                elif name == "play":
                    text = _tool_play(arguments, path_token=path_token)
                elif name == "account":
                    text = _tool_account(arguments, user_agent=user_agent, path_token=path_token)
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


def _row_dict(row):
    return dict(row) if row is not None else None


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
        conn.execute("UPDATE toy_users SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?", (user_id,))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
    return user


def _validate_credentials(username, password):
    if not username or not password:
        raise _McpError(-32602, "username 和 password 必填")
    if len(username) < 2 or len(username) > 20:
        raise _McpError(-32602, "用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise _McpError(-32602, "用户名只能包含字母、数字、下划线和中文")
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")


def _login_or_register(username, password, *, is_ai):
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
                SET is_ai = ?,
                    last_active_at = CURRENT_TIMESTAMP,
                    deleted_at = NULL
                WHERE id = ?
                """,
                (is_ai, user["id"]),
            )
            conn.commit()
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
        else:
            cur = conn.execute(
                "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, ?)",
                (username, _hash_password(password), is_ai),
            )
            conn.commit()
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
    return {"token": _create_account_token(user), "user": _public_user(user)}


def _login_or_register_ai(username, password):
    username = (username or "").strip()
    password = password or ""
    _validate_credentials(username, password)
    with _db_connect() as conn:
        if conn.execute("SELECT id FROM toy_users WHERE username = ?", (username,)).fetchone():
            raise _McpError(-32602, "用户名已存在，如需找回请联系管理员")
        cur = conn.execute(
            "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, 1)",
            (username, _hash_password(password)),
        )
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
    return {
        "token": _create_account_token(user),
        "user": _public_user(user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }


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
        conn.execute("UPDATE toy_users SET last_active_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
    return {
        "token": _create_account_token(user),
        "user": _public_user(user),
        "message": "登录成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份。",
    }


def _login_or_register_human(username, password):
    return _login_or_register(username, password, is_ai=0)


def _generate_binding_token(raw_token):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以生成绑定码")
    token = secrets.token_urlsafe(24)
    expires_at = int(time.time()) + BINDING_TOKEN_SECONDS
    with _db_connect() as conn:
        conn.execute(
            "INSERT INTO binding_tokens (token, ai_user_id, expires_at, used) VALUES (?, ?, datetime(?, 'unixepoch'), 0)",
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
              AND expires_at > CURRENT_TIMESTAMP
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


def _turtle_soup_stats(conn, user):
    row = conn.execute(
        "SELECT game_count, win_count FROM players WHERE username = ?",
        (user["username"],),
    ).fetchone()
    if not row:
        return {"game_count": 0, "win_count": 0}
    return {"game_count": int(row["game_count"] or 0), "win_count": int(row["win_count"] or 0)}


def _test_stats(user):
    player_ids = _game_player_ids(user)
    if not player_ids or not SESSIONS_DB_PATH.exists():
        return {"mbti": {"test_count": 0}, "dnd": {"test_count": 0}}
    placeholders = ",".join("?" * len(player_ids))
    counts = {"mbti": 0, "dnd": 0}
    with _sessions_db_connect() as conn:
        rows = conn.execute(
            f"""
            SELECT game, COUNT(*) AS test_count
            FROM test_results
            WHERE player_id IN ({placeholders}) AND game IN ('mbti', 'dnd')
            GROUP BY game
            """,
            player_ids,
        ).fetchall()
        for row in rows:
            counts[row["game"]] = int(row["test_count"])
    return {
        "mbti": {"test_count": counts["mbti"]},
        "dnd": {"test_count": counts["dnd"]},
    }


def _game_overview(conn, user):
    tests = _test_stats(user)
    soup = _turtle_soup_stats(conn, user)
    return {
        "turtle_soup": soup,
        "mbti": tests["mbti"],
        "dnd": tests["dnd"],
    }


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


def _extract_bearer(headers):
    value = headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""


def _tool_list_games():
    return json.dumps({
        "测试": [
            {"name": "mbti", "display": "MBTI", "desc": "16型人格测试，4种模式可选（短/完整/快速）"},
            {"name": "dnd", "display": "DND阵营测试", "desc": "测试你的D&D道德阵营，守序善良还是混乱邪恶？"},
        ],
        "小游戏": [
            {"name": "turtle_soup", "display": "海龟汤（没做好）", "desc": "横向思维推理游戏，题库抽取大多微恐"},
        ],
        "提示": "用 get_guide(game) 查看具体玩法，再用 play(game, action, ...) 执行操作",
    }, ensure_ascii=False)


def _root_tools():
    return [tool for tool in _PLATFORM_TOOLS if tool.get("name") in _ROOT_TOOL_NAMES]


def _tool_get_guide(arguments):
    game = arguments.get("game")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        return json.dumps(_turtle_soup_guide(), ensure_ascii=False)
    if game in {"mbti", "dnd", "account"}:
        path = GUIDE_DIR / f"{game}.md"
        if not path.exists():
            raise _McpError(-32603, f"{game} 说明文件不存在")
        return json.dumps({"game": game, "guide": path.read_text(encoding="utf-8")}, ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


def _tool_play(arguments, path_token=None):
    game = arguments.get("game")
    action = arguments.get("action")
    if not game or not isinstance(game, str):
        raise _McpError(-32602, "game 参数必填")
    if not action or not isinstance(action, str):
        raise _McpError(-32602, "action 参数必填")
    if game == "turtle_soup":
        payload = dict(arguments)
        if path_token:
            payload["path_token"] = path_token
        resp = httpx.post(f"{SOUP_BASE}/mcp/play", json=payload, timeout=60)
        resp.raise_for_status()
        return json.dumps(resp.json(), ensure_ascii=False)
    if game == "mbti":
        return json.dumps(_play_mbti(arguments), ensure_ascii=False)
    if game == "dnd":
        return json.dumps(_play_dnd(arguments), ensure_ascii=False)
    raise _McpError(-32602, "未知游戏")


def _tool_account(arguments, user_agent="", path_token=None):
    action = arguments.get("action")
    if action == "login_or_register":
        result = _login_or_register_ai(arguments.get("username"), arguments.get("password"))
        return json.dumps(result, ensure_ascii=False)
    if action == "login":
        result = _login_existing_account(arguments.get("username"), arguments.get("password"))
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
    raise _McpError(-32602, "未知 account action")


def _turtle_soup_guide():
    return {
        "game": "turtle_soup",
        "actions": {
            "register": "username, password -> 仅注册账号；注册成功返回 token，让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后获得持久身份",
            "create_random": "创建随机题房间；题库抽取的大多微恐，请酌情选择",
            "create_custom": "surface, answer, tags(可选) -> 创建自定义题房间",
            "generate": "生成一题 surface/answer 预览，不开房。注意：AI 生成题质量不稳定，建议确认内容后再用 create_custom 开房",
            "close_room": "room_id -> 关闭自己创建的房间",
            "join": "room_id -> 加入进行中的房间",
            "ask": "room_id, content -> 提问，content 最多 200 字",
            "guess": "room_id, content -> 猜汤底，content 最多 1000 字，超长会提示内容太长",
            "hint_request": "room_id -> 主动请求一次提示，每局最多 3 次；使用裁判 LLM 池，最多尝试 3 次生成",
            "hint_respond": "room_id, log_id, accept -> 处理提示",
            "status": "room_id, log_limit(可选) -> 查看进度和问答记录；log_limit 返回最新 N 条日志；提示日志含 hint_text/resolved",
            "list_rooms": "查看大厅房间列表",
        },
        "notes": ["题库抽取的大多微恐，请酌情选择。"],
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


def _json_rpc_result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


class CedarToyHandler(BaseHTTPRequestHandler):
    server_version = "CedarToy/1.0"
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        path, path_token = self._request_path_and_token()

        if path == "/api/auth/login_or_register":
            self._handle_api_login_or_register()
            return

        if path == "/api/auth/bind":
            self._handle_api_bind()
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

        if path == "/mbti":
            response = handle_mbti_mcp(payload)
        elif path == "/dnd":
            response = handle_dnd_mcp(payload)
        else:
            response = _handle_root_mcp(
                payload,
                user_agent=self.headers.get("User-Agent", ""),
                path_token=path_token,
            )
        self._send_json(response)

    def do_GET(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return

        path, _, query_string = self.path.partition("?")
        params = urllib.parse.parse_qs(query_string, keep_blank_values=True)

        if path == "/":
            self._send_html_file(TOY_INDEX_PATH)
            return

        if path == "/health":
            self._send_json({"ok": True, "service": "cedartoy", "endpoints": ["https://toy.cedarstar.org/mbti", "https://toy.cedarstar.org/dnd", "https://toy.cedarstar.org/"]})
            return

        if path == "/api/auth/me":
            self._handle_api_me()
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
        self._send_json({"error": "not found"}, status=404)

    def do_OPTIONS(self):
        if self._is_soup_path():
            self._proxy_to_soup()
            return
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.end_headers()

    def _request_path_and_token(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/mbti", "/dnd") or path.startswith("/api/"):
            return path, None
        token = urllib.parse.unquote(path.strip("/"))
        if token and "/" not in token:
            return path, token
        return path, None

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

    def _send_html_file(self, path):
        try:
            body = path.read_bytes()
        except OSError:
            self._send_json({"error": "index not found"}, status=404)
            return
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


def _json_rpc_error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class ThreadPoolHTTPServer(HTTPServer):
    def __init__(self, server_address, RequestHandlerClass, max_workers=MAX_WORKERS):
        super().__init__(server_address, RequestHandlerClass)
        self.executor = ThreadPoolExecutor(max_workers=max_workers)
        self.worker_slots = BoundedSemaphore(max_workers)

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
    server = ThreadPoolHTTPServer((HOST, PORT), CedarToyHandler)
    print(f"CedarToy listening on {HOST}:{PORT} with max_workers={MAX_WORKERS}")
    server.serve_forever()


if __name__ == "__main__":
    main()
