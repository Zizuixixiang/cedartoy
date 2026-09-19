"""系统通知 / 投票模块（跨游戏通用）。

运营侧往 `announcements` 表里塞一条通知或投票，玩家下一次执行常用指令
（eco 的 observe/status 之类）时，最近三条未读通知会被拼在指令输出的最前面。

约定：
* 普通动作只自动展示最近三条未读；同批更早的通知会写入归档读状态，不会在
  后续动作里继续刷屏，但仍可通过主动查看公告入口查询。
* `announcement_reads.read_at` 以 `archived:` 开头表示「自动归档但尚未展示」；
  历史查询真正展示后会改成普通时间。这样旧投票不会因防刷屏归档而失去入口。
* 投票是可选的后续动作：`announcement_reads.votes` 初始为 NULL（没回应），
  玩家回复后写成 JSON 数组；显式跳过写成 `[]`，以便和「压根没回」区分。
* `announcements.allow_feedback=1` 的投票可附纯文字意见；旧投票迁移后默认 0。
  意见单独存在 `announcement_reads.feedback`，不改变旧 `votes` JSON 数组格式。
* `announcements.force_mcp_push=1` 是发布时显式开启的小机强制曝光标记，只对投票
  生效。它不占普通「最新三条」额度；曾被自动归档但从未展示的投票仍会在小机
  下一次 MCP 工具请求中单独展示一次，展示后复用普通 read_at 防止重复。
* 一旦写入至少一个有效选项，该身份的选项、意见和提交时间永久锁定；跳过 `[]`
  不算有效票，之后仍可正式投票。
* `target_game` 为具体游戏名（eco/fishing/...）或 `all`（所有游戏都弹）。

时间统一用 Asia/Shanghai 的 `%Y-%m-%d %H:%M:%S`，和 eco_adapter 里的
`_now_iso` 一致——定宽零填充，所以字符串比较等价于时间比较，可以直接在
SQL 里 `expires_at > ?` 过滤。
"""

import json
import os
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# 跟 server.SESSIONS_DB_PATH 认同一个环境变量；server 启动时还会再赋一次，
# 保证两边指向同一个库（这两张表属于 sessions.db，不是 turtle_soup.db）。
DB_PATH = os.getenv("SESSIONS_DB", "/opt/cedartoy/data/sessions.db")
AUTO_PUSH_LIMIT = 3
HISTORY_PAGE_LIMIT = 10
FEEDBACK_MAX_LENGTH = 500
_ARCHIVED_READ_PREFIX = "archived:"
IMPORTANT_MCP_HEADING = "【重要公告｜仅自动展示一次】"

# 通知只弹一次，所以文案里必须把「怎么投票」讲清楚，玩家没有第二次机会看到。
DEFAULT_VOTE_HINT = (
    "投票请回复：choose 投票编号 {id} 1 2（多选，空格分隔）"
    " / choose 投票编号 {id} 0（跳过）。有效选项提交后不可修改；跳过后仍可再投"
)
SINGLE_VOTE_HINT = (
    "投票请回复：choose 投票编号 {id} 1（单选，只能选一个）"
    " / choose 投票编号 {id} 0（跳过）。有效选项提交后不可修改；跳过后仍可再投"
)
DEFAULT_FEEDBACK_HINT = (
    '可附补充意见：choose 投票编号 {id} 1 feedback="我的意见"'
    f"（最多 {FEEDBACK_MAX_LENGTH} 字）"
)


class AnnouncementError(Exception):
    """投票参数不合法。调用方自行翻译成各自协议的错误。"""


def _now_iso(now=None):
    tz = ZoneInfo("Asia/Shanghai")
    dt = datetime.now(tz) if now is None else datetime.fromtimestamp(now, tz=tz)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _connect():
    return sqlite3.connect(DB_PATH)


def _connect_read_only():
    return sqlite3.connect(Path(DB_PATH).resolve().as_uri() + "?mode=ro", uri=True)


# 账号存档槽把 player_id 写成 "12:3"（槽 1 就是裸 "12"，见 server._account_slot_player_id）。
# 存档是存档，通知是发给「人」的，所以标记已读时把槽后缀削掉，否则同一条系统通知
# 会在同一个账号的 5 个槽里各弹一次。
# 只削「纯数字:纯数字」——游客 id 是 "guest:xxx"（前缀非数字），用户名 id 不含冒号，都不受影响。
_SLOT_SUFFIX_RE = re.compile(r"^(\d+):\d+$")

# AI 模型爱把逗号写成全角「，」或顿号「、」（同 command_text 里那类空白问题）。
# 分隔符一律宽进：半/全角逗号、顿号、分号、空白都能切。
_OPTION_SEPARATORS = re.compile(r"[,，、;；\s]+")


def _announcement_identity(player_id):
    """把带存档槽的 player_id 归一成「人」的 id。非字符串/无槽后缀原样返回。"""
    if not isinstance(player_id, str):
        return player_id
    match = _SLOT_SUFFIX_RE.match(player_id)
    return match.group(1) if match else player_id


def parse_option_list(raw):
    """把玩家传来的选项归一成序号字符串列表。

    接受 "1,3,5" / "1、3" / "1 3" / [1, 3] / 5；空输入返回 []。
    真正的整数校验和范围校验在 record_vote 里做，这里只负责切开。

    >>> parse_option_list("1，3、5")
    ['1', '3', '5']
    >>> parse_option_list([1, 3])
    ['1', '3']
    """
    if raw is None:
        return []
    if isinstance(raw, bool):
        raise AnnouncementError("options 须为序号，不能是布尔值。")
    if isinstance(raw, int):
        return [str(raw)]
    if isinstance(raw, (list, tuple)):
        return [str(item).strip() for item in raw if str(item).strip()]
    if not isinstance(raw, str):
        raise AnnouncementError("options 须为逗号分隔的序号字符串或整数数组。")
    return [part for part in _OPTION_SEPARATORS.split(raw.strip()) if part]


def init_db(conn):
    """建表。这是两张表 DDL 的唯一定义处——server.py 启动时调它，
    check_announcements/list_announcements/record_vote 也会兜底调一次
    （`IF NOT EXISTS` 幂等）。

    注意别在别处再抄一份 `CREATE TABLE IF NOT EXISTS`：谁先跑谁的列定义生效，
    另一份会变成永远不报错的死代码。
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS announcements (
            id TEXT PRIMARY KEY,
            type TEXT NOT NULL DEFAULT 'notice',
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            options TEXT,
            multiple INTEGER DEFAULT 0,
            allow_feedback INTEGER NOT NULL DEFAULT 0,
            force_mcp_push INTEGER NOT NULL DEFAULT 0,
            target_game TEXT NOT NULL DEFAULT 'all',
            created_at TEXT NOT NULL,
            expires_at TEXT
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS announcement_reads (
            player_id TEXT NOT NULL,
            announcement_id TEXT NOT NULL,
            votes TEXT,
            feedback TEXT,
            read_at TEXT NOT NULL,
            PRIMARY KEY (player_id, announcement_id)
        )
        """
    )
    # 拉未读时按 target_game 过滤，玩家量上来以后这条索引有用。
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_announcements_target"
        " ON announcements(target_game)"
    )
    # 最小幂等迁移：只补新列，不重建表，也不重写旧 votes JSON / read_at。
    announcement_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(announcements)")
    }
    if "allow_feedback" not in announcement_columns:
        conn.execute(
            "ALTER TABLE announcements"
            " ADD COLUMN allow_feedback INTEGER NOT NULL DEFAULT 0"
        )
    if "force_mcp_push" not in announcement_columns:
        conn.execute(
            "ALTER TABLE announcements"
            " ADD COLUMN force_mcp_push INTEGER NOT NULL DEFAULT 0"
        )
    read_columns = {
        row[1] for row in conn.execute("PRAGMA table_info(announcement_reads)")
    }
    if "feedback" not in read_columns:
        conn.execute("ALTER TABLE announcement_reads ADD COLUMN feedback TEXT")


def _parse_options(raw):
    """options 列存 JSON 数组；脏数据一律当成「没有选项」，不要炸在玩家脸上。"""
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _resolve_vote_hint(vote_hint, ann_id, multiple, option_count):
    """vote_hint 可以是模板串（`{id}` 占位）或 `(ann_id, multiple) -> str` 的可调用对象。

    可调用形式是给平台层用的：单选/多选的示例参数不一样（options="1" vs "1,2"），
    给单选投票展示多选示例会直接把 AI 引到一个必然报错的调用上。
    """
    if callable(vote_hint):
        try:
            return vote_hint(ann_id, bool(multiple), option_count)
        except TypeError:
            # 兼容既有的 `(ann_id, multiple) -> str` 定制提示。
            return vote_hint(ann_id, bool(multiple))
    if vote_hint is None:
        if multiple and option_count < 2:
            vote_hint = DEFAULT_VOTE_HINT.replace("1 2", "1")
        else:
            vote_hint = DEFAULT_VOTE_HINT if multiple else SINGLE_VOTE_HINT
    return vote_hint.format(id=ann_id)


def _resolve_feedback_hint(feedback_hint, ann_id, multiple, option_count):
    if callable(feedback_hint):
        return feedback_hint(ann_id, bool(multiple), option_count)
    if feedback_hint is None:
        feedback_hint = DEFAULT_FEEDBACK_HINT
    return feedback_hint.format(id=ann_id)


def _resolve_more_hint(more_hint, game_name, count):
    if callable(more_hint):
        return more_hint(count)
    if more_hint is None:
        return f'另有 {count} 条旧公告；action="announcements" 可查看。'
    return more_hint.format(game=game_name, count=count)


def _format(row, vote_hint, feedback_hint=None):
    ann_id, ann_type, title, content, options_raw, multiple, allow_feedback = row
    lines = ["【系统通知】" + (title or "")]
    if content:
        lines.append(content)

    if ann_type == "poll":
        options = _parse_options(options_raw)
        if options:
            lines.extend(
                "  %d. %s" % (idx, label) for idx, label in enumerate(options, 1)
            )
        lines.append(_resolve_vote_hint(vote_hint, ann_id, multiple, len(options)))
        if allow_feedback:
            lines.append(
                _resolve_feedback_hint(
                    feedback_hint, ann_id, multiple, len(options)
                )
            )

    return "\n".join(lines)


def check_forced_mcp_announcements(
    player_id,
    game_name=None,
    vote_hint=None,
    feedback_hint=None,
):
    """认领并格式化该小机尚未真正展示过的重要投票。

    只把缺少回执或 read_at 仍为 ``archived:`` 的显式置标投票视为待曝光；普通
    read_at（包括曾按最新三条展示或已经投票）不会再次推送。``game_name=None``
    只匹配 target_game=all，供没有游戏上下文的 MCP 工具请求使用。

    查询、认领和 archived 状态提升都在同一个 ``BEGIN IMMEDIATE`` 事务中完成，
    所以同一小机的并发请求至多有一个拿到展示文本。网页端不调用本函数。
    """
    if not player_id:
        return ""
    player_id = _announcement_identity(player_id)
    if isinstance(player_id, str) and (
        player_id.startswith("human:") or player_id.startswith("guest:")
    ):
        return ""

    now = _now_iso()
    if game_name is None:
        target_clause = "a.target_game = 'all'"
        target_args = ()
    else:
        target_clause = "(a.target_game = ? OR a.target_game = 'all')"
        target_args = (game_name,)

    with _connect() as conn:
        init_db(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            f"""
            SELECT a.id, a.type, a.title, a.content, a.options, a.multiple,
                   a.allow_feedback
            FROM announcements AS a
            LEFT JOIN announcement_reads AS r
              ON r.player_id = ? AND r.announcement_id = a.id
            WHERE a.type = 'poll'
              AND a.force_mcp_push = 1
              AND {target_clause}
              AND (a.expires_at IS NULL OR a.expires_at > ?)
              AND (r.announcement_id IS NULL OR r.read_at LIKE ?)
            ORDER BY a.created_at DESC, a.id DESC
            """,
            (player_id, *target_args, now, _ARCHIVED_READ_PREFIX + "%"),
        ).fetchall()
        if not rows:
            return ""

        conn.executemany(
            "INSERT OR IGNORE INTO announcement_reads"
            " (player_id, announcement_id, votes, read_at) VALUES (?, ?, NULL, ?)",
            ((player_id, row[0], now) for row in rows),
        )
        conn.executemany(
            "UPDATE announcement_reads SET read_at = ?"
            " WHERE player_id = ? AND announcement_id = ? AND read_at LIKE ?",
            (
                (now, player_id, row[0], _ARCHIVED_READ_PREFIX + "%")
                for row in rows
            ),
        )

    blocks = [_format(row, vote_hint, feedback_hint) for row in rows]
    return IMPORTANT_MCP_HEADING + "\n\n" + "\n\n".join(blocks)


def check_announcements(
    player_id,
    game_name,
    vote_hint=None,
    more_hint=None,
    feedback_hint=None,
    include_forced_mcp=False,
):
    """自动展示最近三条未读，并归档同批更早公告。

    没有未读时返回空字符串，调用方可以直接 `if text:` 判断要不要拼进输出。
    超过三条时只展示按 created_at 最新的三条，并追加较早条数提醒；较早条目
    不会在下次普通动作继续弹，但 list_announcements 仍可查到。

    `vote_hint` 用来覆盖投票指引文案（各游戏的指令语法不一样，比如 eco 走的是
    MCP 结构化参数而不是裸文本），模板里用 `{id}` 占位通知编号。
    `more_hint` 是较早公告提醒模板（可用 `{game}` / `{count}`）或接收 count 的函数。
    `include_forced_mcp=True` 时先独立认领重要投票；该文本不计入普通三条上限。
    """
    if not player_id:
        return ""
    player_id = _announcement_identity(player_id)

    forced_text = ""
    if include_forced_mcp:
        forced_text = check_forced_mcp_announcements(
            player_id,
            game_name,
            vote_hint=vote_hint,
            feedback_hint=feedback_hint,
        )

    now = _now_iso()
    blocks = []
    with _connect() as conn:
        init_db(conn)
        # 先拿写锁再查未读并整批标记，确保并发普通动作不会各自弹到同一条。
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        unread_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM announcements AS a
            WHERE (a.target_game = ? OR a.target_game = 'all')
              AND (a.expires_at IS NULL OR a.expires_at > ?)
              AND NOT EXISTS (
                    SELECT 1 FROM announcement_reads AS r
                    WHERE r.player_id = ? AND r.announcement_id = a.id
              )
            """,
            (game_name, now, player_id),
        ).fetchone()[0]
        if not unread_count:
            return forced_text

        rows = conn.execute(
            """
            SELECT a.id, a.type, a.title, a.content, a.options, a.multiple,
                   a.allow_feedback
            FROM announcements AS a
            WHERE (a.target_game = ? OR a.target_game = 'all')
              AND (a.expires_at IS NULL OR a.expires_at > ?)
              AND NOT EXISTS (
                    SELECT 1 FROM announcement_reads AS r
                    WHERE r.player_id = ? AND r.announcement_id = a.id
              )
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (game_name, now, player_id, AUTO_PUSH_LIMIT),
        ).fetchall()

        conn.executemany(
            "INSERT OR IGNORE INTO announcement_reads"
            " (player_id, announcement_id, votes, read_at) VALUES (?, ?, NULL, ?)",
            ((player_id, row[0], now) for row in rows),
        )
        # 未展示的旧公告只做防重复归档；历史查询真正展示它时会把 archived:
        # 状态提升成普通 read_at，随后才允许投票。
        conn.execute(
            """
            INSERT OR IGNORE INTO announcement_reads
                (player_id, announcement_id, votes, read_at)
            SELECT ?, a.id, NULL, ?
            FROM announcements AS a
            WHERE (a.target_game = ? OR a.target_game = 'all')
              AND (a.expires_at IS NULL OR a.expires_at > ?)
              AND NOT EXISTS (
                    SELECT 1 FROM announcement_reads AS r
                    WHERE r.player_id = ? AND r.announcement_id = a.id
              )
            """,
            (
                player_id,
                _ARCHIVED_READ_PREFIX + now,
                game_name,
                now,
                player_id,
            ),
        )

        blocks.extend(_format(row, vote_hint, feedback_hint) for row in rows)

    older_count = unread_count - len(rows)
    if older_count:
        blocks.append(_resolve_more_hint(more_hint, game_name, older_count))

    ordinary_text = "\n\n".join(blocks)
    return "\n\n".join(text for text in (forced_text, ordinary_text) if text)


def list_announcements(
    player_id,
    game_name,
    before=None,
    vote_hint=None,
    feedback_hint=None,
):
    """按游标返回 game_name 相关的十条有效公告，并把本页标为确实已展示。

    查询包含已读和自动归档公告，因此自动推送中未展开的较早条目不会永久丢失。
    `before` 使用上一页最后一条公告的 id；排序同时使用 created_at / id，避免同秒
    创建的公告造成跳页或重复。
    """
    if not player_id:
        return {"blocks": [], "has_more": False, "next_before": None}
    player_id = _announcement_identity(player_id)
    now = _now_iso()

    with _connect() as conn:
        init_db(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        cursor_clause = ""
        cursor_args = []
        if before is not None:
            if not isinstance(before, str) or not before.strip():
                raise AnnouncementError("before 须为公告游标。")
            before = before.strip()
            cursor = conn.execute(
                "SELECT created_at, id FROM announcements WHERE id = ?",
                (before,),
            ).fetchone()
            if cursor is None:
                raise AnnouncementError("before 公告游标无效。")
            cursor_clause = (
                " AND (a.created_at < ? OR (a.created_at = ? AND a.id < ?))"
            )
            cursor_args = [cursor[0], cursor[0], cursor[1]]
        rows = conn.execute(
            f"""
            SELECT a.id, a.type, a.title, a.content, a.options, a.multiple,
                   a.allow_feedback
            FROM announcements AS a
            WHERE (a.target_game = ? OR a.target_game = 'all')
              AND (a.expires_at IS NULL OR a.expires_at > ?)
              {cursor_clause}
            ORDER BY a.created_at DESC, a.id DESC
            LIMIT ?
            """,
            (game_name, now, *cursor_args, HISTORY_PAGE_LIMIT + 1),
        ).fetchall()
        visible = rows[:HISTORY_PAGE_LIMIT]
        for row in visible:
            conn.execute(
                "UPDATE announcement_reads SET read_at = ?"
                " WHERE player_id = ? AND announcement_id = ? AND read_at LIKE ?",
                (now, player_id, row[0], _ARCHIVED_READ_PREFIX + "%"),
            )
            conn.execute(
                "INSERT OR IGNORE INTO announcement_reads"
                " (player_id, announcement_id, votes, read_at) VALUES (?, ?, NULL, ?)",
                (player_id, row[0], now),
            )

    return {
        "blocks": [_format(row, vote_hint, feedback_hint) for row in visible],
        "has_more": len(rows) > HISTORY_PAGE_LIMIT,
        "next_before": visible[-1][0]
        if len(rows) > HISTORY_PAGE_LIMIT and visible
        else None,
    }


def _normalize_feedback(feedback):
    if feedback is None:
        return None
    if not isinstance(feedback, str):
        raise AnnouncementError("feedback 须为文字。")
    feedback = feedback.strip()
    if not feedback:
        return None
    if len(feedback) > FEEDBACK_MAX_LENGTH:
        raise AnnouncementError(
            f"feedback 最多 {FEEDBACK_MAX_LENGTH} 字，当前 {len(feedback)} 字。"
        )
    return feedback


def _has_effective_vote(votes_raw, option_count):
    """旧库里只要已存至少一个当前有效选项，就视为不可修改的正式选票。"""
    if votes_raw is None:
        return False
    try:
        stored = json.loads(votes_raw)
    except (TypeError, ValueError):
        return False
    if not isinstance(stored, list):
        return False
    for value in stored:
        if isinstance(value, bool):
            continue
        try:
            pick = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= pick <= option_count:
            return True
    return False


def submit_vote(
    player_id,
    announcement_id,
    options,
    feedback=None,
    *,
    mark_seen=False,
):
    """共享投票校验与记录逻辑，返回归一化后的本次提交。

    `options` 接受 parse_option_list 支持的旧格式；`[0]` 或 `[]` 表示跳过。
    默认仍要求公告已经展示。人类网页接口可传 `mark_seen=True`，把“已展示/已读”
    与投票写入放在同一写事务里，避免前端已读请求和提交请求竞态。有效选项
    首次写入后不可修改；`BEGIN IMMEDIATE` 内检查既有票，避免并发首投互相覆盖。
    """
    if not player_id:
        raise AnnouncementError("缺少 player_id。")
    player_id = _announcement_identity(player_id)

    announcement_id = str(announcement_id or "").strip()
    if not announcement_id:
        raise AnnouncementError("缺少投票编号。")

    now = _now_iso()
    with _connect() as conn:
        init_db(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT type, options, multiple, expires_at, allow_feedback"
            " FROM announcements WHERE id = ?",
            (announcement_id,),
        ).fetchone()
        if row is None:
            raise AnnouncementError(f"没有编号为 {announcement_id} 的通知。")

        ann_type, options_raw, multiple, expires_at, allow_feedback = row
        if ann_type != "poll":
            raise AnnouncementError(f"通知 {announcement_id} 不是投票，无需回复。")

        choices = _parse_options(options_raw)
        existing = conn.execute(
            "SELECT votes FROM announcement_reads"
            " WHERE player_id = ? AND announcement_id = ?",
            (player_id, announcement_id),
        ).fetchone()
        if existing is not None and _has_effective_vote(
            existing[0], len(choices)
        ):
            raise AnnouncementError(
                "你已提交有效选票，选票和补充意见不可修改。"
            )

        if expires_at is not None and expires_at <= now:
            raise AnnouncementError(f"投票 {announcement_id} 已经结束了。")

        raw_picks = parse_option_list(options)
        picks = []
        for raw in raw_picks:
            try:
                picks.append(int(raw))
            except (TypeError, ValueError):
                raise AnnouncementError("选项须为整数序号。")
        normalized_feedback = _normalize_feedback(feedback)
        if normalized_feedback is not None and not allow_feedback:
            raise AnnouncementError(f"投票 {announcement_id} 未开放文字反馈。")

        # 0 = 跳过。跟别的序号混着传属于表达矛盾，直接拒绝。
        if 0 in picks:
            if len(picks) > 1:
                raise AnnouncementError("0（跳过）不能和其他选项一起选。")
            picks = []

        for pick in picks:
            if not 1 <= pick <= len(choices):
                raise AnnouncementError(
                    f"选项 {pick} 超出范围，可选 1–{len(choices)}，或 0 跳过。"
                )

        picks = sorted(set(picks))
        if not multiple and len(picks) > 1:
            raise AnnouncementError(f"投票 {announcement_id} 是单选，只能选一个。")

        if mark_seen:
            conn.execute(
                "INSERT OR IGNORE INTO announcement_reads"
                " (player_id, announcement_id, votes, feedback, read_at)"
                " VALUES (?, ?, NULL, NULL, ?)",
                (player_id, announcement_id, now),
            )
            conn.execute(
                "UPDATE announcement_reads SET read_at = ?"
                " WHERE player_id = ? AND announcement_id = ? AND read_at LIKE ?",
                (
                    now,
                    player_id,
                    announcement_id,
                    _ARCHIVED_READ_PREFIX + "%",
                ),
            )

        seen = conn.execute(
            "SELECT 1 FROM announcement_reads"
            " WHERE player_id = ? AND announcement_id = ? AND read_at NOT LIKE ?",
            (player_id, announcement_id, _ARCHIVED_READ_PREFIX + "%"),
        ).fetchone()
        if seen is None:
            raise AnnouncementError(f"投票 {announcement_id} 还没推送给你。")

        conn.execute(
            "UPDATE announcement_reads SET votes = ?, feedback = ?, read_at = ?"
            " WHERE player_id = ? AND announcement_id = ?",
            (
                json.dumps(picks),
                normalized_feedback,
                now,
                player_id,
                announcement_id,
            ),
        )

    return {
        "announcement_id": announcement_id,
        "options": picks,
        "labels": [choices[index - 1] for index in picks],
        "skipped": not picks,
        "feedback": normalized_feedback,
        "allow_feedback": bool(allow_feedback),
        "locked": bool(picks),
    }


def record_vote(player_id, announcement_id, options, feedback=None):
    """记录一次投票并返回机器端兼容的文字结果。

    只有实际展示过（read_at 不是 archived: 状态）的投票才能回复；一旦提交
    有效选项，选票和补充意见均不可修改。跳过不算有效票，之后仍可正式投票。
    """
    result = submit_vote(
        player_id,
        announcement_id,
        options,
        feedback=feedback,
    )
    feedback_suffix = "并保存了补充意见。" if result["feedback"] else ""
    if result["skipped"]:
        return f"已记录：跳过投票 {result['announcement_id']}。{feedback_suffix}"
    labels = "、".join(
        f"{index}. {label}"
        for index, label in zip(result["options"], result["labels"])
    )
    return (
        f"已记录你对投票 {result['announcement_id']} 的选择：{labels}。"
        f"{feedback_suffix}"
    )


def get_poll_results(announcement_id):
    """运营侧本机只读查询：返回票数、有效参与数和文字意见。

    本函数不调用 init_db、不执行迁移；普通 HTTP/MCP API 没有暴露该结果。
    """
    announcement_id = str(announcement_id or "").strip()
    if not announcement_id:
        raise AnnouncementError("缺少投票编号。")

    with _connect_read_only() as conn:
        conn.row_factory = sqlite3.Row
        announcement_columns = {
            row["name"] for row in conn.execute("PRAGMA table_info(announcements)")
        }
        read_columns = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(announcement_reads)")
        }
        allow_feedback_sql = (
            "allow_feedback" if "allow_feedback" in announcement_columns else "0"
        )
        feedback_sql = "feedback" if "feedback" in read_columns else "NULL"
        announcement = conn.execute(
            f"SELECT id, type, title, options, multiple, {allow_feedback_sql}"
            " AS allow_feedback FROM announcements WHERE id = ?",
            (announcement_id,),
        ).fetchone()
        if announcement is None:
            raise AnnouncementError(f"没有编号为 {announcement_id} 的通知。")
        if announcement["type"] != "poll":
            raise AnnouncementError(f"通知 {announcement_id} 不是投票。")
        responses = conn.execute(
            f"SELECT player_id, votes, {feedback_sql} AS feedback, read_at"
            " FROM announcement_reads WHERE announcement_id = ?"
            " ORDER BY read_at, player_id",
            (announcement_id,),
        ).fetchall()

    choices = _parse_options(announcement["options"])
    counts = [0] * len(choices)
    participant_counts = {"total": 0, "human": 0, "machine": 0}
    skipped_responses = 0
    feedback_items = []
    for response in responses:
        votes_raw = response["votes"]
        stored_picks = []
        if votes_raw is not None:
            try:
                parsed = json.loads(votes_raw)
            except (TypeError, ValueError):
                parsed = None
            if isinstance(parsed, list):
                for value in parsed:
                    if isinstance(value, bool):
                        continue
                    try:
                        pick = int(value)
                    except (TypeError, ValueError):
                        continue
                    if 1 <= pick <= len(choices) and pick not in stored_picks:
                        stored_picks.append(pick)
        identity = str(response["player_id"])
        # 新入口禁止游客；若老库遗留过 guest 行，运营统计也不把它当账号参与。
        if identity.startswith("guest:"):
            continue
        identity_type = "human" if identity.startswith("human:") else "machine"
        effective = bool(stored_picks)
        if effective:
            participant_counts["total"] += 1
            participant_counts[identity_type] += 1
            for pick in stored_picks:
                counts[pick - 1] += 1
        elif votes_raw is not None:
            skipped_responses += 1

        feedback = response["feedback"]
        if isinstance(feedback, str) and feedback:
            feedback_items.append(
                {
                    "identity": identity,
                    "identity_type": identity_type,
                    "options": stored_picks,
                    "labels": [choices[pick - 1] for pick in stored_picks],
                    "effective_vote": effective,
                    "feedback": feedback,
                    "updated_at": response["read_at"],
                }
            )

    return {
        "id": announcement["id"],
        "title": announcement["title"],
        "multiple": bool(announcement["multiple"]),
        "allow_feedback": bool(announcement["allow_feedback"]),
        "options": [
            {"index": index, "label": label, "votes": counts[index - 1]}
            for index, label in enumerate(choices, 1)
        ],
        "valid_participants": participant_counts,
        "skipped_responses": skipped_responses,
        "feedback": feedback_items,
    }


def create_announcement(
    ann_id,
    ann_type,
    title,
    content,
    target_game,
    options=None,
    multiple=False,
    expires_at=None,
    allow_feedback=False,
    force_mcp_push=False,
):
    """运营侧写入一条通知/投票。重复 id 覆盖旧内容（已读记录不受影响）。

    ``force_mcp_push`` 默认关闭且只对 poll 生效；显式开启后，尚未真正展示过的
    小机身份会在下一次 MCP 工具请求中收到一次独立曝光。
    """
    if ann_type not in ("notice", "poll"):
        raise AnnouncementError("type 须为 notice 或 poll。")
    if ann_type == "poll" and not options:
        raise AnnouncementError("poll 必须带 options。")
    # title/content 是 NOT NULL 列；这里显式挡一道，别让运营看见 IntegrityError。
    if not isinstance(title, str) or not title.strip():
        raise AnnouncementError("title 必填。")
    if not isinstance(content, str) or not content.strip():
        raise AnnouncementError("content 必填。")
    if not isinstance(target_game, str) or not target_game.strip():
        raise AnnouncementError("target_game 必填（具体游戏名或 all）。")

    with _connect() as conn:
        init_db(conn)
        conn.execute(
            "INSERT OR REPLACE INTO announcements"
            " (id, type, title, content, options, multiple, allow_feedback,"
            "  force_mcp_push, target_game, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                str(ann_id),
                ann_type,
                title.strip(),
                content.strip(),
                json.dumps(list(options), ensure_ascii=False) if options else None,
                1 if multiple else 0,
                1 if ann_type == "poll" and allow_feedback else 0,
                1 if ann_type == "poll" and force_mcp_push else 0,
                target_game.strip(),
                _now_iso(),
                expires_at,
            ),
        )
    return str(ann_id)


def set_force_mcp_push(announcement_id, enabled=True):
    """只修改既有投票的小机强制曝光标记，不重发或改写投票/回执。"""
    announcement_id = str(announcement_id or "").strip()
    if not announcement_id:
        raise AnnouncementError("缺少投票编号。")

    with _connect() as conn:
        init_db(conn)
        row = conn.execute(
            "SELECT type FROM announcements WHERE id = ?",
            (announcement_id,),
        ).fetchone()
        if row is None:
            raise AnnouncementError(f"没有编号为 {announcement_id} 的通知。")
        if enabled and row[0] != "poll":
            raise AnnouncementError("force_mcp_push 只能用于投票。")
        conn.execute(
            "UPDATE announcements SET force_mcp_push = ? WHERE id = ?",
            (1 if enabled else 0, announcement_id),
        )
    return announcement_id
