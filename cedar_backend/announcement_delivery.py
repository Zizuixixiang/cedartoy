"""Announcement delivery and platform identity/response mapping.

Storage and voting rules remain in announcements. Paths and callbacks are supplied
at call time by server wrappers so existing patches and direct callers still work.
Only initialization, web voting and forced MCP delivery synchronize DB_PATH, as
before; ordinary MCP votes/history/delivery honor announcements.DB_PATH directly.
"""

import json

from .errors import _McpError


# Protocol handshakes are not player actions.
_ANNOUNCEMENT_META_ACTIONS = frozenset({"initialize", "tools/list", "tools/call"})


def _init_announcement_tables(
    *,
    announcements,
    sessions_db_path,
    sessions_db_connect,
):
    """系统通知/投票两张表建在 data/sessions.db。

    注意别塞进 _migrate_platform_timestamps：那个函数用的 _db_connect() 连的是
    turtle_soup.db，建过去就成了两张没人读的死表。
    DDL 只在 announcements.init_db 里写一份，这里不重复。
    """
    # 让 announcements 跟着 SESSIONS_DB 环境变量走，别在两处各写死一个路径。
    announcements.DB_PATH = str(sessions_db_path)
    with sessions_db_connect() as conn:
        announcements.init_db(conn)


def _human_announcement_identity(user):
    """Keep homepage reads separate from the machine-side numeric player id."""
    return f"human:{int(user['id'])}"


def _current_human_account(
    raw_token, *,
    current_account,
):
    user = current_account(raw_token)
    if user.get("is_ai"):
        raise _McpError(-32001, "此网页接口仅供已登录的人类账号使用")
    return user


def _account_announcement_identity(
    user, account_player_id, *,
    human_announcement_identity,
):
    if user and not user.get("is_ai"):
        return human_announcement_identity(user)
    return account_player_id


def _announcement_options_for_web(raw):
    if not raw:
        return []
    try:
        options = json.loads(raw)
    except (TypeError, ValueError):
        return []
    if not isinstance(options, list):
        return []
    return [str(option) for option in options]


def _announcement_vote_for_web(votes_raw, feedback):
    if votes_raw is None:
        return None
    try:
        parsed = json.loads(votes_raw)
    except (TypeError, ValueError):
        parsed = []
    options = []
    if isinstance(parsed, list):
        for item in parsed:
            if isinstance(item, bool):
                continue
            try:
                option = int(item)
            except (TypeError, ValueError):
                continue
            if option >= 1 and option not in options:
                options.append(option)
    return {
        "options": options,
        "skipped": not options,
        "feedback": feedback if isinstance(feedback, str) else None,
        "locked": bool(options),
    }


def _web_announcements(
    raw_token, *,
    announcements,
    current_human_account,
    human_announcement_identity,
    sessions_db_connect,
    announcement_options_for_web,
    announcement_vote_for_web,
):
    user = current_human_account(raw_token) if raw_token else None
    identity = human_announcement_identity(user) if user else None
    now = announcements._now_iso()
    with sessions_db_connect() as conn:
        announcements.init_db(conn)
        rows = conn.execute(
            """
            SELECT
                a.id,
                a.type,
                a.title,
                a.content,
                a.options,
                a.multiple,
                a.allow_feedback,
                a.target_game,
                a.created_at,
                a.expires_at,
                r.votes,
                r.feedback,
                CASE WHEN r.announcement_id IS NULL OR r.read_at LIKE 'archived:%'
                     THEN 1 ELSE 0 END AS unread
            FROM announcements AS a
            LEFT JOIN announcement_reads AS r
              ON r.player_id = ? AND r.announcement_id = a.id
            WHERE (a.expires_at IS NULL OR a.expires_at > ?)
              AND (a.target_identity IS NULL OR a.target_identity = ?)
            ORDER BY
              CASE WHEN a.pinned_until IS NOT NULL AND a.pinned_until > ? THEN 1 ELSE 0 END DESC,
              a.created_at DESC, a.id DESC
            """,
            (identity or "", now, identity, now),
        ).fetchall()

    items = []
    for row in rows:
        unread = bool(identity and row["unread"])
        items.append(
            {
                "id": row["id"],
                "type": row["type"],
                "title": row["title"],
                "content": row["content"],
                "options": announcement_options_for_web(row["options"]),
                "multiple": bool(row["multiple"]),
                "allow_feedback": bool(row["allow_feedback"]),
                "target_game": row["target_game"],
                "created_at": row["created_at"],
                "expires_at": row["expires_at"],
                "unread": unread,
                "my_vote": announcement_vote_for_web(
                    row["votes"], row["feedback"]
                ) if identity else None,
            }
        )
    return {
        "authenticated": bool(user),
        "announcements": items,
        "unread_count": sum(1 for item in items if item["unread"]),
        "feedback_max_length": announcements.FEEDBACK_MAX_LENGTH,
    }


def _mark_web_announcements_read(
    raw_token, announcement_ids, *,
    announcements,
    current_human_account,
    human_announcement_identity,
    sessions_db_connect,
):
    user = current_human_account(raw_token)
    if not isinstance(announcement_ids, list):
        raise _McpError(-32602, "announcement_ids 必须是数组")
    normalized_ids = list(
        dict.fromkeys(
            item.strip()
            for item in announcement_ids
            if isinstance(item, str) and item.strip()
        )
    )
    if len(normalized_ids) > 500:
        raise _McpError(-32602, "一次最多标记 500 条通知")
    if not normalized_ids:
        return {"marked": 0}

    identity = human_announcement_identity(user)
    now = announcements._now_iso()
    placeholders = ",".join("?" for _ in normalized_ids)
    with sessions_db_connect() as conn:
        announcements.init_db(conn)
        before = conn.total_changes
        conn.execute(
            f"""
            INSERT OR IGNORE INTO announcement_reads
                (player_id, announcement_id, votes, read_at)
            SELECT ?, id, NULL, ?
            FROM announcements
            WHERE id IN ({placeholders})
              AND (expires_at IS NULL OR expires_at > ?)
              AND (target_identity IS NULL OR target_identity = ?)
            """,
            (identity, now, *normalized_ids, now, identity),
        )
        conn.execute(
            f"""
            UPDATE announcement_reads
            SET read_at = ?
            WHERE player_id = ?
              AND announcement_id IN ({placeholders})
              AND read_at LIKE 'archived:%'
              AND EXISTS (
                  SELECT 1 FROM announcements AS a
                  WHERE a.id = announcement_reads.announcement_id
                    AND (a.target_identity IS NULL OR a.target_identity = ?)
              )
            """,
            (now, identity, *normalized_ids, identity),
        )
        marked = conn.total_changes - before
    return {"marked": marked}


def _submit_web_announcement_vote(
    raw_token, announcement_id, options, feedback=None, *,
    announcements,
    sessions_db_path,
    current_human_account,
    human_announcement_identity,
):
    """Submit under the authenticated human identity; ignore all client identities."""
    user = current_human_account(raw_token)
    if options is None:
        raise _McpError(-32602, "options 必填；选择投票项，或用 [0] 明确跳过")
    identity = human_announcement_identity(user)
    announcements.DB_PATH = str(sessions_db_path)
    try:
        vote = announcements.submit_vote(
            identity,
            announcement_id,
            options,
            feedback=feedback,
            mark_seen=True,
        )
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    return {"ok": True, "vote": vote}


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


def _announcement_vote_hint(game):
    """生成该游戏的投票指引。通知只弹一次，示例参数必须是能直接照抄的。"""

    def hint(ann_id, multiple, option_count=2):
        example = "[1,2]" if multiple and option_count >= 2 else "[1]"
        kind = "多选，使用整数数组" if multiple else "单选，数组内只填一个"
        return (
            f'投票请调用 play(game="{game}", action="vote", '
            f'params={{"announcement_id": "{ann_id}", "options": {example}}})'
            f'（{kind}）；options=[0] 表示跳过。'
            "有效选项提交后不可修改；跳过后仍可再投。"
        )

    return hint


def _announcement_feedback_hint(game):
    def hint(ann_id, _multiple, _option_count):
        return (
            "本投票可附补充意见，例如："
            f'play(game="{game}", action="vote", params={{"announcement_id": '
            f'"{ann_id}", "options": [1], "feedback": "我的意见"}})'
        )

    return hint


def _announcement_more_hint(game):
    return lambda count: f"另外有 {count} 条未读公告。"


def _tool_play_vote(
    game, player_id, params, *,
    announcements,
    guest_prefix,
):
    """平台级投票动作：params={announcement_id, options, feedback?}。"""
    if not player_id:
        raise _McpError(-32602, "vote 需要 player_id（或带 token 的账号身份）")
    if isinstance(player_id, str) and player_id.startswith(guest_prefix):
        return {"ok": False, "text": "游客身份不参与投票，注册认领存档后可参与"}
    announcement_id = params.get("announcement_id")
    if not isinstance(announcement_id, str) or not announcement_id.strip():
        raise _McpError(-32602, "vote 需要 announcement_id（通知里给的投票编号）")
    try:
        options = announcements.parse_option_list(params.get("options"))
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    if not options:
        raise _McpError(-32602, "vote 需要 options：单选如 [1]，多选如 [1,2]，跳过填 [0]")
    try:
        message = announcements.record_vote(
            player_id,
            announcement_id.strip(),
            options,
            feedback=params.get("feedback"),
        )
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))
    return {"ok": True, "text": message}


def _tool_play_announcement_history(
    game, player_id, params, *,
    announcements,
    guest_prefix,
    announcement_vote_hint,
    announcement_feedback_hint,
):
    """平台级公告历史：首次取最新十条，后续用 before 游标向前翻。"""
    if not player_id:
        raise _McpError(-32602, "announcements 需要 player_id（或带 token 的账号身份）")
    if isinstance(player_id, str) and player_id.startswith(guest_prefix):
        return {"ok": False, "text": "游客身份不记录公告，注册后可查看"}

    before = params.get("before")

    try:
        result = announcements.list_announcements(
            player_id,
            game,
            before=before,
            vote_hint=announcement_vote_hint(game),
            feedback_hint=announcement_feedback_hint(game),
        )
    except announcements.AnnouncementError as exc:
        raise _McpError(-32602, str(exc))

    blocks = result.pop("blocks")
    if blocks:
        text = "公告（最新在前）\n\n" + "\n\n".join(blocks)
    else:
        text = "没有更早的有效公告。" if before is not None else "暂无有效公告。"
    if result["has_more"]:
        next_params = json.dumps(
            {"before": result["next_before"]},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        text += f"\n\n还有更早公告：params={next_params}"
    return {"ok": True, "text": text}


def _play_announcements(
    player_id, game, action, *,
    announcements,
    guest_prefix,
    meta_actions,
    announcement_vote_hint,
    announcement_more_hint,
    announcement_feedback_hint,
):
    """取该玩家在这个游戏下的未读通知；顺带标记已读。"""
    # 游客身份不持久，公告与投票对其无意义，也会徒增 token。
    if isinstance(player_id, str) and player_id.startswith(guest_prefix):
        return ""
    if not player_id or action in meta_actions:
        return ""
    try:
        return announcements.check_announcements(
            player_id,
            game,
            vote_hint=announcement_vote_hint(game),
            more_hint=announcement_more_hint(game),
            feedback_hint=announcement_feedback_hint(game),
            include_forced_mcp=True,
        )
    except Exception:
        # 通知系统坏掉不该拖垮游戏本身——玩家该玩游戏还是玩游戏。
        return ""


def _mcp_forced_announcement(
    player_id, game=None, *,
    announcements,
    sessions_db_path,
    announcement_vote_hint,
    announcement_feedback_hint,
):
    """Best-effort one-time important poll exposure for authenticated machines."""
    if player_id in {None, ""}:
        return ""
    try:
        announcements.DB_PATH = str(sessions_db_path)
        hint_game = game if isinstance(game, str) and game else "eco"
        return announcements.check_forced_mcp_announcements(
            player_id,
            game if isinstance(game, str) and game else None,
            vote_hint=announcement_vote_hint(hint_game),
            feedback_hint=announcement_feedback_hint(hint_game),
        )
    except Exception:
        # 和普通公告一致：公告故障不能覆盖原本的 MCP 工具结果。
        return ""
