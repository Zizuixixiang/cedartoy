import json
import re
import sqlite3
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import announcements
from command_text import normalize_command_spaces
from eco import engine

# engine.py 默认自己读写 eco_save.json 存档。handler 层接管存档：
# 直接操作 engine 的 _STATE 全局做序列化/反序列化，并把 engine 的文件写入屏蔽掉，
# 不让 engine 自己读写文件。
engine.save_state = lambda state: None

# engine 的 _STATE 是进程级共享可变全局；server 多线程并发，必须串行化整段
# 「装载 -> cmd -> 取回」过程。
_ENGINE_LOCK = threading.Lock()


DB_PATH = "/opt/cedartoy/data/sessions.db"
GAME = "eco"
MAX_SESSIONS = 500
SESSION_TTL_SECONDS = 30 * 24 * 60 * 60
# 允许平台身份层注入的前缀 id：账号玩家=纯数字账号 id 或 id:slot，游客=guest:xxx。
PLAYER_ID_RE = re.compile(r"^(?:guest:[a-zA-Z0-9]{1,64}|[a-zA-Z0-9]{1,64}(?::[1-5])?)$")

_PLAYER_ID_SCHEMA = {
    "type": "string",
    "description": "1-10位字母数字 ID。",
    "pattern": "^[a-zA-Z0-9]{1,10}$",
}


TOOLS = [
    {
        "name": "eco_new",
        "description": (
            "开一局新的「瓶中生态」：为该玩家重置出一个空池塘。你是造物主，从一池清水开始。"
            "开局后用 eco_play 发送指令，输入 help 查看所有可用指令。"
            "（游戏总入口：toy.cedarstar.org/，支持 MCP 和 curl 连接，包含更多游戏）"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": {
                    "type": "string",
                    "description": "1-10位字母数字 ID。",
                    "pattern": "^[a-zA-Z0-9]{1,10}$",
                },
                "seed": {
                    "type": "integer",
                    "description": "可选随机种子（整数）；不传则用默认种子。相同种子生态走向相近。",
                },
            },
            "required": ["player_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "eco_observe",
        "description": "观察池塘。observe=推进一天，wait=连续推进，gaze=凝望不推进，look=查看物种或季节详情",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["observe", "wait", "gaze", "look"],
                },
                "days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 7,
                    "description": "wait 推进天数，默认 1。",
                },
                "target": {
                    "type": "string",
                    "description": "look 的目标：物种名或季节名。",
                },
            },
            "required": ["player_id", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "eco_act",
        "description": "干预池塘。summon=投放物种，remove=取走，feed=投喂，clean=换水，crack=凿冰(冬季)，shelter=铺落叶(冬季)，choose=做选择，name=给定居者取名",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["summon", "remove", "feed", "clean", "crack", "shelter", "choose", "name"],
                },
                "species": {
                    "type": "string",
                    "description": "物种名（summon/remove 用）。",
                },
                "quantity": {
                    "type": "integer",
                    "minimum": 1,
                    "description": "数量（summon/remove/feed 用，默认 10/10/1）。",
                },
                "option": {
                    "type": "integer",
                    "enum": [1, 2, 3],
                    "description": "选项编号（choose 用，通常 1–2，蛇事件可选 3）。",
                },
                "announcement": {
                    "type": "string",
                    "description": "投票编号（choose 用）。回复系统通知里的投票时填；填了就是投票，不会影响池塘。",
                },
                "options": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "description": "投票选项序号（choose + announcement 用）：多选如 [1,3,5]，[0] 表示跳过。",
                },
                "settler": {
                    "type": "string",
                    "description": "定居者标识（name 用）：物种名如「翠鸟」、[D-N] 编号如「[D-5]」、或「[D-5] 翠鸟」均可；同物种有多位定居者时须带 [D-N]（见 status）。",
                },
                "nickname": {
                    "type": "string",
                    "description": "要取的昵称，如「小蓝」（name 用）。",
                },
            },
            "required": ["player_id", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "eco_info",
        "description": "查看信息。status=数据面板，folio=万物志，chronicle=年鉴，encyclopedia=图鉴与成就，trends=趋势图",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["status", "folio", "chronicle", "encyclopedia", "trends"],
                },
                "scope": {
                    "type": "string",
                    "enum": ["recent", "all"],
                    "description": "chronicle 范围，默认 recent。",
                },
            },
            "required": ["player_id", "action"],
            "additionalProperties": False,
        },
    },
    {
        "name": "eco_save",
        "description": "存档管理。export=导出存档，import=导入存档。export mode：full=完整，lite=精简，story=年鉴故事",
        "inputSchema": {
            "type": "object",
            "properties": {
                "player_id": _PLAYER_ID_SCHEMA,
                "action": {
                    "type": "string",
                    "enum": ["export", "import"],
                },
                "mode": {
                    "type": "string",
                    "enum": ["full", "lite", "story"],
                    "description": "export 模式，默认 full。",
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
                    "serverInfo": {"name": "cedartoy-eco", "version": "1.0.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "tools/list":
            return _result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            try:
                name = params.get("name")
                arguments = params.get("arguments") or {}
                if name == "eco_new":
                    text = eco_new(arguments)
                elif name == "eco_observe":
                    text = eco_observe(arguments)
                elif name == "eco_act":
                    text = eco_act(arguments)
                elif name == "eco_info":
                    text = eco_info(arguments)
                elif name == "eco_save":
                    text = eco_save(arguments)
                elif name == "eco_play":
                    # 已从 tools/list 移除，保留实现兼容旧调用。
                    text = eco_play(arguments)
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


def eco_new(arguments):
    player_id = _require_player_id(arguments)
    seed = _coerce_seed(arguments.get("seed"))
    now = time.time()

    save_data, intro = _engine_new(seed)

    with _connect() as conn:
        _init_db(conn)
        conn.commit()
        # Take SQLite's write reservation before inspecting the current row so a
        # concurrent read-modify-write request cannot later overwrite this reset.
        conn.execute("BEGIN IMMEDIATE")
        _cleanup_expired(conn, now)
        existing = conn.execute(
            "SELECT 1 FROM eco_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        active_count = conn.execute(
            "SELECT COUNT(*) FROM eco_sessions"
        ).fetchone()[0]
        if existing is None and active_count >= MAX_SESSIONS:
            raise JsonRpcError(-32000, "当前池塘数量过多，请稍后再试")

        ts = _now_iso(now)
        conn.execute(
            """
            INSERT INTO eco_sessions (player_id, save_data, created_at, last_active)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(player_id) DO UPDATE SET
                save_data = excluded.save_data,
                created_at = excluded.created_at,
                last_active = excluded.last_active
            """,
            (player_id, save_data, ts, ts),
        )

    header = "🌊 新池初成"
    if seed is not None:
        header += f"（seed={seed}）"
    header += "。一池清水，静待你的第一笔。"
    return "\n\n".join(
        [
            header,
            intro,
            "用 eco_play 发送指令推进。输入 help 查看所有可用指令。",
        ]
    )


def eco_play(arguments):
    """万能文本指令。已从 tools/list 移除，保留实现以兼容旧调用。"""
    player_id = _require_player_id(arguments)
    command = arguments.get("command")
    if not isinstance(command, str) or not command.strip():
        raise JsonRpcError(-32602, "command 须为非空字符串。")
    return _run_player_command(player_id, command)


def eco_observe(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    if action == "observe":
        command = "observe"
    elif action == "wait":
        days = _coerce_int(arguments.get("days"), "days", default=1)
        if not 1 <= days <= 7:
            raise JsonRpcError(-32602, "days 须为 1–7 的整数。")
        command = f"wait {days}"
    elif action == "gaze":
        command = "gaze"
    elif action == "look":
        target = arguments.get("target")
        if not isinstance(target, str) or not target.strip():
            raise JsonRpcError(-32602, "look 需要 target（物种名或季节名）。")
        command = f"look {target.strip()}"
    else:
        raise JsonRpcError(-32602, "action 须为 observe、wait、gaze、look 之一。")
    return _with_announcements(player_id, _run_player_command(player_id, command))


def eco_act(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    if action in ("summon", "remove"):
        species = arguments.get("species")
        if not isinstance(species, str) or not species.strip():
            raise JsonRpcError(-32602, f"{action} 需要 species（物种名）。")
        quantity = _coerce_int(arguments.get("quantity"), "quantity", default=10)
        if quantity <= 0:
            raise JsonRpcError(-32602, "quantity 须为正整数。")
        command = f"{action} {species.strip()} {quantity}"
    elif action == "feed":
        quantity = _coerce_int(arguments.get("quantity"), "quantity", default=1)
        if quantity <= 0:
            raise JsonRpcError(-32602, "quantity 须为正整数。")
        command = f"feed {quantity}"
    elif action == "clean":
        command = "clean"
    elif action == "crack":
        command = "crack"
    elif action == "shelter":
        command = "shelter"
    elif action == "choose":
        # 带 announcement 的 choose 是在回复系统投票，纯 handler 层的事，不进引擎，
        # 免得和引擎里 pending_choice 的 `choose 1` 撞车。
        announcement = arguments.get("announcement")
        if isinstance(announcement, str) and announcement.strip():
            return _record_vote(player_id, announcement.strip(), arguments)
        option = _coerce_int(arguments.get("option"), "option", default=None)
        if option not in (1, 2, 3):
            raise JsonRpcError(-32602, "option 须为 1、2 或 3。")
        command = f"choose {option}"
    elif action == "name":
        settler = arguments.get("settler")
        nickname = arguments.get("nickname")
        if not isinstance(settler, str) or not settler.strip():
            raise JsonRpcError(
                -32602,
                "name 需要 settler：物种名（如「翠鸟」）、[D-N] 编号（如「[D-5]」）"
                "或「[D-5] 翠鸟」均可；同物种有多位定居者时须带 [D-N]。",
            )
        if not isinstance(nickname, str) or not nickname.strip():
            raise JsonRpcError(-32602, "name 需要 nickname（昵称）。")
        command = f"name {settler.strip()} {nickname.strip()}"
    else:
        raise JsonRpcError(
            -32602,
            "action 须为 summon、remove、feed、clean、crack、shelter、choose、name 之一。",
        )
    return _run_player_command(player_id, command)


def eco_info(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    if action == "status":
        command = "status"
    elif action == "folio":
        command = "folio"
    elif action == "chronicle":
        scope = arguments.get("scope") or "recent"
        if scope not in ("recent", "all"):
            raise JsonRpcError(-32602, "scope 须为 recent 或 all。")
        command = "chronicle all" if scope == "all" else "chronicle"
    elif action == "encyclopedia":
        command = "encyclopedia"
    elif action == "trends":
        command = "trends"
    else:
        raise JsonRpcError(
            -32602, "action 须为 status、folio、chronicle、encyclopedia、trends 之一。"
        )
    return _with_announcements(player_id, _run_player_command(player_id, command))


def eco_save(arguments):
    player_id = _require_player_id(arguments)
    action = arguments.get("action")
    if action == "export":
        mode = arguments.get("mode") or "full"
        if mode not in ("full", "lite", "story"):
            raise JsonRpcError(-32602, "mode 须为 full、lite 或 story。")
        if mode == "lite":
            command = "export lite"
        elif mode == "story":
            command = "export story"
        else:
            command = "export"
    elif action == "import":
        save_data = arguments.get("save_data")
        if not isinstance(save_data, str) or not save_data.strip():
            raise JsonRpcError(-32602, "import 需要 save_data（base64 存档字符串）。")
        command = f"import_save {save_data.strip()}"
    else:
        raise JsonRpcError(-32602, "action 须为 export 或 import 之一。")
    return _run_player_command(player_id, command)


# eco 的指令走 MCP 结构化参数，玩家没法发裸文本，所以投票指引得写成工具调用的样子。
_ECO_VOTE_HINT = (
    '投票请调用 eco_act(action="choose", announcement="{id}", options=[1,3,5])'
    '（多选）/ options=[0] 跳过。不回也没关系，这条通知不会再弹。'
)


def _record_vote(player_id, announcement_id, arguments):
    """回复系统投票。options 缺省时兼容单选的 option。"""
    options = arguments.get("options")
    if options is None:
        option = arguments.get("option")
        options = [] if option is None else [option]
    if not isinstance(options, list):
        raise JsonRpcError(-32602, "options 须为整数数组，如 [1,3] 或 [0]（跳过）。")
    if not options:
        raise JsonRpcError(
            -32602, "choose 投票需要 options：多选如 [1,3]，跳过填 [0]。"
        )
    try:
        return announcements.record_vote(player_id, announcement_id, options)
    except announcements.AnnouncementError as exc:
        raise JsonRpcError(-32602, str(exc))


def _with_announcements(player_id, text):
    """把未读的系统通知拼在指令输出前面。通知只弹一次，取走即标记已读。"""
    try:
        notice = announcements.check_announcements(
            player_id, GAME, vote_hint=_ECO_VOTE_HINT
        )
    except Exception:
        # 通知系统坏掉不该拖垮游戏本身——玩家该看池塘还是看池塘。
        return text
    return f"{notice}\n\n{text}" if notice else text


def _run_player_command(player_id, command):
    """读取该玩家存档 -> 喂给 engine 执行 command -> 写回新存档，返回结果文字。"""
    # 归一化 Unicode 空白：engine 按 ASCII 空格切指令，全角空格会匹配不上。
    # 也覆盖 look/add/name 这类由玩家参数拼出来的指令。
    command = normalize_command_spaces(command)
    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        conn.commit()
        # Acquire the write transaction before loading.  Waiting until UPDATE
        # would allow two writers to run the engine from the same stale save.
        conn.execute("BEGIN IMMEDIATE")
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT save_data FROM eco_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if row is None:
            raise JsonRpcError(
                -32001,
                "没有进行中的池塘（可能从未 eco_new，或超过 30 天未活动已被清理）。"
                "请先 eco_new 开一局。",
            )

        save_data = row[0]
        try:
            state = json.loads(save_data)
        except (json.JSONDecodeError, ValueError) as exc:
            raise JsonRpcError(-32603, f"存档解析失败：{exc}")

        text, new_save_data = _engine_run(state, command)

        conn.execute(
            "UPDATE eco_sessions SET save_data = ?, last_active = ? WHERE player_id = ?",
            (new_save_data, _now_iso(now), player_id),
        )

    return text


def human_action(player_id, action, payload=None):
    """Atomically load a player's save, apply engine.human_action, and persist on success."""
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(-32602, "player_id 不合法")

    now = time.time()
    with _connect() as conn:
        _init_db(conn)
        conn.commit()
        # MCP commands use the same early write reservation, serializing the
        # complete load -> mutate -> save interval across both entry points.
        conn.execute("BEGIN IMMEDIATE")
        _cleanup_expired(conn, now)
        row = conn.execute(
            "SELECT save_data FROM eco_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
        if row is None:
            raise JsonRpcError(-32001, "没有进行中的池塘。")
        try:
            state = json.loads(row[0])
        except (json.JSONDecodeError, ValueError) as exc:
            raise JsonRpcError(-32603, f"存档解析失败：{exc}")

        with _ENGINE_LOCK:
            engine._migrate(state)
            result = engine.human_action(state, action, payload)
            if result.get("ok"):
                save_data = json.dumps(state, ensure_ascii=False)

        if result.get("ok"):
            conn.execute(
                "UPDATE eco_sessions SET save_data = ?, last_active = ? WHERE player_id = ?",
                (save_data, _now_iso(now), player_id),
            )
    return result


def _engine_new(seed):
    """开新局：返回 (序列化存档, 初始状态面板文字)。"""
    with _ENGINE_LOCK:
        try:
            if seed is None:
                engine.new_game()
            else:
                engine.new_game(seed)
            intro = engine.cmd("status")
            state = engine._STATE
            save_data = json.dumps(state, ensure_ascii=False)
        finally:
            engine._STATE = None
    return save_data, intro


def _engine_run(state, command):
    """装载存档 -> 执行指令 -> 取回新存档；返回 (结果文字, 序列化存档)。"""
    with _ENGINE_LOCK:
        engine._migrate(state)
        engine._STATE = state
        try:
            text = engine.cmd(command)
            # cmd 内遇 new/reset 会把 _STATE 换成全新对象，故执行后重新取回。
            new_state = engine._STATE
            save_data = json.dumps(new_state, ensure_ascii=False)
        finally:
            engine._STATE = None
    return text, save_data


def _load_player_state_readonly(player_id):
    """读取玩家 eco 存档并迁移到当前内存结构；不写回 DB。"""
    if not isinstance(player_id, str) or PLAYER_ID_RE.fullmatch(player_id) is None:
        raise JsonRpcError(-32602, "player_id 不合法")
    with _connect() as conn:
        row = conn.execute(
            "SELECT save_data FROM eco_sessions WHERE player_id = ?",
            (player_id,),
        ).fetchone()
    if row is None:
        raise JsonRpcError(-32001, "没有进行中的池塘。")
    try:
        state = json.loads(row[0])
    except (json.JSONDecodeError, ValueError) as exc:
        raise JsonRpcError(-32603, f"存档解析失败：{exc}")
    with _ENGINE_LOCK:
        engine._migrate(state)
    return state


def api_state(player_id):
    return engine.api_state(_load_player_state_readonly(player_id))


def api_codex(player_id):
    return engine.api_codex(_load_player_state_readonly(player_id))


def api_folio(player_id):
    return engine.api_folio(_load_player_state_readonly(player_id))


def api_annals(player_id):
    return engine.api_annals(_load_player_state_readonly(player_id))


def api_species(player_id, name):
    data = engine.api_species(_load_player_state_readonly(player_id), name)
    if data is None:
        raise JsonRpcError(-32004, "物种未解锁或不存在")
    return data


def summarize_save(save_data):
    """给平台 my_saves 用：从存档 JSON 提取概况（天数/评分/存活物种数），失败返回 None。"""
    try:
        state = json.loads(save_data)
    except (TypeError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(state, dict):
        return None
    summary = {"day": state.get("turn")}
    populations = state.get("populations")
    if isinstance(populations, dict):
        summary["alive_species"] = sum(1 for v in populations.values() if isinstance(v, (int, float)) and v >= 1)
    try:
        with _ENGINE_LOCK:
            engine._migrate(state)
            score, word = engine._pond_score(state)
        summary["pond_score"] = score
        summary["pond_score_word"] = word
    except Exception:
        pass
    return summary


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


def _coerce_int(value, name, default):
    if value is None:
        return default
    if isinstance(value, bool):
        raise JsonRpcError(-32602, f"{name} 须为整数。")
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            raise JsonRpcError(-32602, f"{name} 须为整数。")
    raise JsonRpcError(-32602, f"{name} 须为整数。")


def _now_iso(now):
    return datetime.fromtimestamp(now, tz=ZoneInfo("Asia/Shanghai")).strftime(
        "%Y-%m-%d %H:%M:%S"
    )


def _connect():
    return sqlite3.connect(DB_PATH)


def _init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS eco_sessions (
            player_id TEXT PRIMARY KEY,
            save_data TEXT,
            created_at TEXT,
            last_active TEXT,
            user_id INTEGER
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(eco_sessions)")}
    if "user_id" not in columns:
        conn.execute("ALTER TABLE eco_sessions ADD COLUMN user_id INTEGER")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_eco_sessions_user_id ON eco_sessions(user_id)"
    )


def _cleanup_expired(conn, now):
    cutoff = _now_iso(now - SESSION_TTL_SECONDS)
    conn.execute(
        "DELETE FROM eco_sessions WHERE last_active < ?",
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
    if msg.startswith("【瓶中生态"):
        return msg
    prefix_by_code = {
        -32000: "【瓶中生态繁忙】",
        -32001: "【瓶中生态】",
        -32601: "【瓶中生态】",
        -32602: "【瓶中生态参数错误】",
        -32603: "【瓶中生态服务错误】",
    }
    prefix = prefix_by_code.get(exc.code, "【瓶中生态】")
    return f"{prefix}{msg}"


def _error(request_id, code, message):
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


class JsonRpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message
