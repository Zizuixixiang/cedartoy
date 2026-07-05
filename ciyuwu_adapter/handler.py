"""词与物 MCP 适配层。

上游引擎在 vendor/ci-yu-wu/，本层只用其 engine 风格接口：
    engine.new_game(seed) -> (state, text)
    engine.cmd(state, inst) -> (new_state, text)

上游代码一律不改，所有适配在这里：
- engine.py 会从 ciyuwu_save.json 读跨局 meta，dark_engine.DarkWorld 会读写全局
  dark_save.json（_load / _save_meta）。这两处都是单机单人设计，多玩家共用会串档，
  在此全部屏蔽，改由本层按 player_id 把两层进度存进 sqlite：
    save_data  当局 state（engine._snapshot 的完整 dict，含 PRNG 状态）
    meta_data  跨局 meta（遗刻、来路解锁、杀过的 Boss、残壁字、成就等）
- ciyuwu_new 只重置当局，跨局 meta 保留（对齐上游"新局也保留跨局进度"的语义）。
"""

import base64
import importlib.util
import json
import os
import re
import sqlite3
import sys
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

_VENDOR_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "vendor", "ci-yu-wu")

# 上游 engine.py 不是包，且模块名"engine"太通用，用 importlib 以独立名字装载，
# 避免污染 sys.modules["engine"]。dark_engine 等依赖靠 vendor 目录进 sys.path 解析。
if _VENDOR_DIR not in sys.path:
    sys.path.insert(0, _VENDOR_DIR)

_spec = importlib.util.spec_from_file_location("ciyuwu_engine", os.path.join(_VENDOR_DIR, "engine.py"))
engine = importlib.util.module_from_spec(_spec)
sys.modules["ciyuwu_engine"] = engine
_spec.loader.exec_module(engine)

# 屏蔽上游文件存档（见模块 docstring）。
engine._SAVE_FILE = os.path.join(_VENDOR_DIR, "__adapter_never_exists__.json")
engine._ensure_init()
import dark_engine  # noqa: E402  vendor 模块，_ensure_init 后可导入

dark_engine.DarkWorld._load = lambda self: None
dark_engine.DarkWorld._save_meta = lambda self: None

# 跨局 meta 字段，与上游 dark_engine._save_meta / engine.new_game 的清单一致。
META_KEYS = [
    "echoes", "runs", "echo_map", "killed_bosses",
    "unlocked_origins", "wall_writings", "total_wait",
    "unlocked_achievements", "heart_slots",
    "cross_word_stats", "game_diary",
    "cross_deform_count", "cross_swallow_count",
]

# engine 的 _det_rng 是进程级共享可变全局；server 多线程并发，
# 必须串行化整段「恢复 state -> cmd -> 快照」过程。
_ENGINE_LOCK = threading.Lock()

DB_PATH = "/opt/cedartoy/data/sessions.db"
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
# 允许平台身份层注入的前缀 id：账号玩家=纯数字账号 id 或 id:slot，游客=guest:xxx。
PLAYER_ID_RE = re.compile(r"^(?:guest:[a-zA-Z0-9]{1,64}|[a-zA-Z0-9]{1,64}(?::[1-5])?)$")
EXPORT_VERSION = 1

_PLAYER_ID_SCHEMA = {
    "type": "string",
    "description": "1-10位字母数字 ID。",
    "pattern": "^[a-zA-Z0-9]{1,10}$",
}


TOOLS = [
    {
        "name": "ciyuwu_new",
        "description": (
            "开一局新的「词与物」（暗黑文字Roguelike）。只重置当局；跨局进度"
            "（遗刻、来路解锁、残壁字、成就等）保留。开局后用 ciyuwu_cmd 发指令，"
            "先发「新角」建角色。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "seed": {
                    "type": "integer",
                    "description": "可选随机种子（整数）。同种子+同指令序列=同结果。",
                },
            },
            "required": ["player_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ciyuwu_cmd",
        "description": (
            "执行「词与物」游戏指令（中文原生指令）。支持批量：「前进5」连走5步只回汇总；"
            "支持分号串联：「前进;说 我在;前进」一次跑三条。每次输出末尾有一行紧凑状态栏JSON，"
            "看它就够，不用单独查状态。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "command": {
                    "type": "string",
                    "description": "游戏指令，如「新角」「确认」「出镇 灰林」「前进5」「说 我在」「攻;攻;说 不要」。输入「帮助」看全部指令。",
                },
            },
            "required": ["player_id", "command"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ciyuwu_info",
        "description": (
            "查看「词与物」信息。status=属性/词库/物品，help=全部指令，words=词库详情，"
            "echoes=跨局进度，quests=任务，achievements=成就，origins=来路列表"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["status", "help", "words", "echoes", "quests", "achievements", "origins"],
                },
            },
            "required": ["player_id", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ciyuwu_save",
        "description": (
            "存档管理。export=导出存档（含当局+跨局两层进度的 base64 字符串），"
            "import=导入存档。服务端本身按 player_id 自动持久化，此工具用于备份/迁移。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["export", "import"],
                },
                "save_data": {
                    "type": "string",
                    "description": "import 用的 base64 存档字符串。",
                },
            },
            "required": ["player_id", "action"],
            "additionalProperties": False,
        },
    },
]

_INFO_COMMANDS = {
    "status": "状态",
    "help": "帮助",
    "words": "词库",
    "echoes": "遗刻",
    "quests": "任务",
    "achievements": "成就",
    "origins": "来路",
}


def handle_mcp(payload):
    request_id = payload.get("id")
    method = payload.get("method")
    params = payload.get("params") or {}

    try:
        if method == "initialize":
            return _result(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cedartoy-ciyuwu", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            try:
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "ciyuwu_new":
                    text = ciyuwu_new(arguments)
                elif name == "ciyuwu_cmd":
                    text = ciyuwu_cmd(arguments)
                elif name == "ciyuwu_info":
                    text = ciyuwu_info(arguments)
                elif name == "ciyuwu_save":
                    text = ciyuwu_save(arguments)
                else:
                    raise JsonRpcError(-32601, f"未知工具：{name}")
                return _result(
                    request_id, {"content": [{"type": "text", "text": text}]}
                )
            except JsonRpcError as exc:
                return _tool_error_result(request_id, exc)
            except Exception as exc:
                return _tool_error_result(
                    request_id,
                    JsonRpcError(-32603, f"服务内部错误：{exc}"),
                )
        raise JsonRpcError(-32601, f"Method not found: {method}")
    except JsonRpcError as exc:
        return _error(request_id, exc.code, exc.message)
    except Exception as exc:
        return _error(request_id, -32603, f"Internal error: {exc}")


def ciyuwu_new(arguments):
    player_id = _require_player_id(arguments)
    seed = _coerce_seed(arguments.get("seed"))
    now = time.time()

    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT meta_data FROM ciyuwu_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        active_count = conn.execute(
            "SELECT COUNT(*) FROM ciyuwu_sessions"
        ).fetchone()[0]
        if row is None and active_count >= MAX_SESSIONS:
            raise JsonRpcError(-32000, "当前会话数量过多，请稍后再试")

        meta = _parse_meta(row[0]) if row else {}
        state, intro = _engine_new(seed, meta)
        new_meta = _extract_meta(state)

        ts = _now_iso(now)
        conn.execute(
            """
            INSERT INTO ciyuwu_sessions (player_id, save_data, meta_data, created_at, last_active)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                save_data = excluded.save_data,
                meta_data = excluded.meta_data,
                created_at = excluded.created_at,
                last_active = excluded.last_active
            """,
            (player_id, _dumps(state), _dumps(new_meta), ts, ts),
        )

    header = "▓ 新局已开"
    if seed is not None:
        header += f"（seed={seed}）"
    if meta:
        header += f"。跨局进度保留（第{meta.get('runs', '?')}局起算的遗刻与解锁还在）。"
    else:
        header += "。"
    return "\n\n".join(
        [
            header,
            intro,
            "用 ciyuwu_cmd 发「新角」开始建角色。支持批量（前进5）和串联（前进;说 我在）。",
        ]
    )


def ciyuwu_cmd(arguments):
    player_id = _require_player_id(arguments)
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise JsonRpcError(-32602, "command 须为非空字符串。")
    return _run_player_command(player_id, command.strip())


def ciyuwu_info(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    command = _INFO_COMMANDS.get(action)
    if command is None:
        raise JsonRpcError(
            -32602,
            "action 须为 status、help、words、echoes、quests、achievements、origins 之一。",
        )
    return _run_player_command(player_id, command)


def ciyuwu_save(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    now = time.time()

    if action == "export":
        with _connect() as conn:
            _init_db(conn)
            row = conn.execute(
                "SELECT save_data, meta_data FROM ciyuwu_sessions WHERE player_id = ?",
                (player_id,),
            ).fetchone()
        if row is None:
            raise JsonRpcError(-32001, "没有进行中的游戏，无档可导。请先 ciyuwu_new 开一局。")
        blob = {
            "v": EXPORT_VERSION,
            "game": "ciyuwu",
            "run": json.loads(row[0]),
            "meta": _parse_meta(row[1]),
        }
        encoded = base64.b64encode(_dumps(blob).encode("utf-8")).decode("ascii")
        return (
            "存档已导出（含当局进度与跨局 meta 两层）。"
            "用 ciyuwu_save import 可恢复到任意 player_id。\n\n" + encoded
        )

    if action == "import":
        save_data = arguments.get("save_data")
        if not isinstance(save_data, str) or not save_data.strip():
            raise JsonRpcError(-32602, "import 需要 save_data（base64 存档字符串）。")
        try:
            blob = json.loads(base64.b64decode(save_data.strip(), validate=True).decode("utf-8"))
        except Exception:
            raise JsonRpcError(-32602, "save_data 解析失败：不是有效的 base64 JSON 存档。")
        if not isinstance(blob, dict) or blob.get("game") != "ciyuwu" or not isinstance(blob.get("run"), dict):
            raise JsonRpcError(-32602, "save_data 不是词与物的存档。")
        state = blob["run"]
        meta = blob.get("meta") if isinstance(blob.get("meta"), dict) else {}
        ts = _now_iso(now)
        with _connect() as conn:
            _init_db(conn)
            _cleanup_expired(conn, now)
            conn.execute(
                """
                INSERT INTO ciyuwu_sessions (player_id, save_data, meta_data, created_at, last_active)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(player_id) DO UPDATE SET
                    save_data = excluded.save_data,
                    meta_data = excluded.meta_data,
                    last_active = excluded.last_active
                """,
                (player_id, _dumps(state), _dumps(meta), ts, ts),
            )
        return "存档已导入。用 ciyuwu_info status 或 ciyuwu_cmd 继续。"

    raise JsonRpcError(-32602, "action 须为 export 或 import 之一。")


def _run_player_command(player_id, command):
    """读该玩家两层存档 -> 喂给 engine 执行 -> 写回，返回结果文字。"""
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT save_data, meta_data FROM ciyuwu_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if row is None:
            raise JsonRpcError(
                -32001,
                "没有进行中的游戏（可能从未 ciyuwu_new，或超过 30 天未活动已被清理）。"
                "请先 ciyuwu_new 开一局。",
            )

        try:
            state = json.loads(row[0])
        except (json.JSONDecodeError, ValueError) as exc:
            raise JsonRpcError(-32603, f"存档解析失败：{exc}")
        # meta_data 是跨局层的权威副本，覆盖进当局 state 再执行。
        state.update(_extract_meta(_parse_meta(row[1])))

        new_state, new_meta, text = _engine_run(state, command)

        conn.execute(
            "UPDATE ciyuwu_sessions SET save_data = ?, meta_data = ?, last_active = ? WHERE player_id = ?",
            (_dumps(new_state), _dumps(new_meta), _now_iso(now), player_id),
        )

    return text


def _engine_new(seed, meta):
    """开新局：返回 (state, 开场文字)。meta 为该玩家已有跨局进度，注入新局 state。"""
    with _ENGINE_LOCK:
        state, text = engine.new_game(seed)
    # engine.new_game 原本从文件恢复跨局 meta（已屏蔽），这里从 DB 注入同一批字段。
    for key in META_KEYS:
        if key in meta:
            state[key] = meta[key]
    return state, text


def _engine_run(state, command):
    """执行指令：返回 (新 state, 新跨局 meta, 输出文字)。"""
    with _ENGINE_LOCK:
        new_state, text = engine.cmd(state, command)
    return new_state, _extract_meta(new_state), text


def _extract_meta(state):
    return {key: state[key] for key in META_KEYS if key in state}


def _parse_meta(raw):
    if not raw:
        return {}
    try:
        meta = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return meta if isinstance(meta, dict) else {}


def summarize_save(save_data, meta_data):
    """给平台 my_saves 用：从两层存档提取跨局进度概况，失败返回 None。"""
    meta = _parse_meta(meta_data)
    try:
        state = json.loads(save_data) if save_data else {}
    except (TypeError, json.JSONDecodeError, ValueError):
        state = {}
    if not isinstance(state, dict):
        state = {}
    if not meta and not state:
        return None
    summary = {"runs": meta.get("runs")}
    echoes = meta.get("echoes")
    if isinstance(echoes, (int, float)):
        summary["echoes"] = echoes
    for key, label in (("unlocked_achievements", "achievements"), ("killed_bosses", "bosses_killed")):
        value = meta.get(key)
        if isinstance(value, (list, dict)):
            summary[label] = len(value)
    return summary


def _dumps(obj):
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def _require_player_id(arguments):
    player_id = arguments.get("player_id")
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(
            -32602,
            "player_id 须为 1–10 位英文字母或数字（正则 ^[a-zA-Z0-9]{1,10}$）。",
        )
    return player_id


def _coerce_seed(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise JsonRpcError(-32602, "seed 须为整数。")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise JsonRpcError(-32602, "seed 须为整数。")
    raise JsonRpcError(-32602, "seed 须为整数。")


def _now_iso(now):
    return datetime.fromtimestamp(now, tz=ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _connect():
    return sqlite3.connect(DB_PATH)


def _init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS ciyuwu_sessions (
            player_id TEXT PRIMARY KEY,
            save_data TEXT,
            meta_data TEXT,
            created_at TEXT,
            last_active TEXT,
            user_id INTEGER
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_ciyuwu_sessions_user_id ON ciyuwu_sessions(user_id)"
    )


def _cleanup_expired(conn, now):
    cutoff = _now_iso(now - SESSION_TTL_SECONDS)
    conn.execute(
        "DELETE FROM ciyuwu_sessions WHERE last_active < ?",
        (cutoff,),
    )


def _result(request_id, result):
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _tool_error_result(request_id, exc):
    return _result(
        request_id,
        {
            "content": [{"type": "text", "text": _user_facing_tool_error(exc)}],
            "isError": True,
        },
    )


def _user_facing_tool_error(exc):
    msg = (exc.message or "").strip()
    prefix_by_code = {
        -32000: "【词与物繁忙】",
        -32001: "【词与物】",
        -32601: "【词与物】",
        -32602: "【词与物参数错误】",
        -32603: "【词与物服务错误】",
    }
    prefix = prefix_by_code.get(exc.code, "【词与物】")
    return f"{prefix}{msg}"


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
