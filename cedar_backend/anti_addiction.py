"""Platform anti-addiction settings, counters and rest/lock coordination.

Server wrappers supply dependencies at call time, including nested helpers, so
runtime patches keep working. The enabled cache has one owner in server; its
accessors preserve the double check under the lock and the write before commit.
This module never imports server or chooses a database path. Schema initialization
and account authorization remain in their existing server entry points.
"""

from threading import Lock

from .errors import _McpError


ANTI_ADDICTION_DEFAULT_REMIND = 30
ANTI_ADDICTION_DEFAULT_FORCE = 50
ANTI_ADDICTION_DEFAULT_LOCK_MINUTES = 30
ANTI_ADDICTION_DEFAULT_ALLOW_SELF_RESET = True
ANTI_ADDICTION_TEST_GAMES = frozenset({"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"})
ANTI_ADDICTION_BASE_MINI_GAMES = frozenset({"turtle_soup", "eco", "ciyuwu", "duel"})
_ANTI_ADDICTION_LOCK = Lock()


def _anti_addiction_defaults(
    *,
    anti_addiction_default_allow_self_reset,
    anti_addiction_default_force,
    anti_addiction_default_lock_minutes,
    anti_addiction_default_remind,
):
    return {
        "enabled": False,
        "remind_threshold": anti_addiction_default_remind,
        "force_threshold": anti_addiction_default_force,
        "lock_minutes": anti_addiction_default_lock_minutes,
        "allow_self_reset": anti_addiction_default_allow_self_reset,
    }


def _anti_addiction_public_settings(
    row=None, *,
    anti_addiction_default_force,
    anti_addiction_default_lock_minutes,
    anti_addiction_default_remind,
    anti_addiction_defaults,
):
    settings = anti_addiction_defaults()
    if row:
        settings.update({
            "enabled": bool(row["enabled"]),
            "remind_threshold": int(row["remind_threshold"] or anti_addiction_default_remind),
            "force_threshold": int(row["force_threshold"] or anti_addiction_default_force),
            "lock_minutes": int(row["lock_minutes"] or anti_addiction_default_lock_minutes),
            "allow_self_reset": bool(row["allow_self_reset"]),
        })
    return settings


def _anti_addiction_settings_for_ai(
    conn, ai_user_id, *,
    anti_addiction_public_settings,
):
    row = conn.execute(
        """
        SELECT enabled, remind_threshold, force_threshold, lock_minutes, allow_self_reset
        FROM anti_addiction_settings
        WHERE ai_user_id = ?
        """,
        (int(ai_user_id),),
    ).fetchone()
    return anti_addiction_public_settings(row)


def _anti_addiction_any_enabled(
    *,
    lock,
    db_connect,
    get_any_enabled,
    set_any_enabled,
):
    if get_any_enabled() is not None:
        return get_any_enabled()
    with lock:
        if get_any_enabled() is not None:
            return get_any_enabled()
        with db_connect() as conn:
            enabled = conn.execute(
                "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
            ).fetchone() is not None
        set_any_enabled(enabled)
        return enabled


def _anti_addiction_validate_settings(
    body, *,
    anti_addiction_defaults,
):
    settings = anti_addiction_defaults()
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


def _anti_addiction_machines(
    raw_token, *,
    anti_addiction_public_settings,
    current_account,
    db_connect,
    public_user,
):
    human = current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以管理小机")
    with db_connect() as conn:
        rows = conn.execute(
            """
            SELECT
                u.id, u.username, u.is_ai, u.is_admin,
                u.avatar_type, u.avatar_value, u.created_at, u.last_active_at,
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
        item = public_user(dict(row))
        item["bound_at"] = row["bound_at"]
        item["anti_addiction"] = anti_addiction_public_settings(row)
        machines.append(item)
    return {"machines": machines}


def _save_anti_addiction_settings(
    raw_token, body, *,
    anti_addiction_reset_ai_states,
    anti_addiction_validate_settings,
    db_connect,
    public_user,
    require_bound_ai,
    clock,
    set_any_enabled,
):
    ai_user = require_bound_ai(raw_token, body.get("ai_user_id"))
    settings = anti_addiction_validate_settings(body)
    with db_connect() as conn:
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
            anti_addiction_reset_ai_states(conn, ai_user, clock())
        set_any_enabled(conn.execute(
            "SELECT 1 FROM anti_addiction_settings WHERE enabled = 1 LIMIT 1"
        ).fetchone() is not None)
        conn.commit()
    return {"ok": True, "ai": public_user(ai_user), "anti_addiction": settings}


def _reset_anti_addiction_state(
    raw_token, body, *,
    db_connect,
    public_user,
    require_bound_ai,
):
    ai_user = require_bound_ai(raw_token, body.get("ai_user_id"))
    with db_connect() as conn:
        conn.execute(
            "DELETE FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
            (str(ai_user["id"]), f"{int(ai_user['id'])}:%"),
        )
        conn.commit()
    return {"ok": True, "ai": public_user(ai_user), "message": "已重置"}


def _anti_addiction_context(
    game, account_user, account_player_id, *,
    anti_addiction_mini_games,
    anti_addiction_test_games,
    anti_addiction_any_enabled,
    anti_addiction_settings_for_ai,
    db_connect,
):
    if game in anti_addiction_test_games or game not in anti_addiction_mini_games:
        return None
    if not anti_addiction_any_enabled():
        return None
    if not account_user or not account_user.get("is_ai") or not account_player_id:
        return None
    with db_connect() as conn:
        settings = anti_addiction_settings_for_ai(conn, int(account_user["id"]))
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


def _anti_addiction_reset_ai_states(
    conn, ai_user, now, *,
    anti_addiction_reset_state,
):
    base_player_id = str(int(ai_user["id"]))
    rows = conn.execute(
        "SELECT player_id FROM anti_addiction_states WHERE player_id = ? OR player_id LIKE ?",
        (base_player_id, f"{base_player_id}:%"),
    ).fetchall()
    for row in rows:
        anti_addiction_reset_state(conn, row["player_id"], now)


def _anti_addiction_lock_seconds(settings):
    return max(1, int(settings["lock_minutes"])) * 60


def _anti_addiction_state_for_update(
    conn, player_id, settings, now, *,
    anti_addiction_lock_seconds,
    anti_addiction_reset_state,
):
    row = conn.execute(
        "SELECT streak, locked, locked_at, last_play_at FROM anti_addiction_states WHERE player_id = ?",
        (player_id,),
    ).fetchone()
    if not row:
        return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": None}
    locked = bool(row["locked"])
    locked_at = row["locked_at"]
    if locked:
        lock_seconds = anti_addiction_lock_seconds(settings)
        if locked_at is not None and now - float(locked_at) >= lock_seconds:
            anti_addiction_reset_state(conn, player_id, now)
            return {"streak": 0, "locked": False, "locked_at": None, "last_play_at": now}
        return {
            "streak": int(row["streak"] or 0),
            "locked": True,
            "locked_at": locked_at,
            "last_play_at": row["last_play_at"],
        }
    last_play_at = row["last_play_at"]
    idle_reset_seconds = anti_addiction_lock_seconds(settings)
    if last_play_at is not None and now - float(last_play_at) >= idle_reset_seconds:
        anti_addiction_reset_state(conn, player_id, now)
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


def _anti_addiction_rest(
    context, account_player_id, *,
    lock,
    anti_addiction_reset_state,
    anti_addiction_rest_disabled_text,
    anti_addiction_state_for_update,
    db_connect,
    clock,
):
    if not context:
        return {"player_id": str(account_player_id) if account_player_id else None, "text": "已重置，可以开新局了。"}
    settings = context["settings"]
    player_id = context["player_id"]
    now = clock()
    with lock:
        with db_connect() as conn:
            state = anti_addiction_state_for_update(conn, player_id, settings, now)
            if state["locked"] and not settings["allow_self_reset"]:
                conn.commit()
                return {"game": context.get("game"), "player_id": player_id, "text": anti_addiction_rest_disabled_text(settings)}
            anti_addiction_reset_state(conn, player_id, now)
            conn.commit()
    return {"game": context.get("game"), "player_id": player_id, "text": "已重置，可以开新局了。"}


def _anti_addiction_preflight(
    game, context, *,
    lock,
    anti_addiction_lock_text,
    anti_addiction_state_for_update,
    db_connect,
    clock,
):
    if not context:
        return None
    now = clock()
    player_id = context["player_id"]
    settings = context["settings"]
    with lock:
        with db_connect() as conn:
            state = anti_addiction_state_for_update(conn, player_id, settings, now)
            conn.commit()
    if not state["locked"]:
        return None
    return {"game": game, "player_id": player_id, "text": anti_addiction_lock_text(settings)}


def _anti_addiction_notice(
    streak, settings, *,
    anti_addiction_lock_text,
):
    remind = settings["remind_threshold"]
    force = settings["force_threshold"]
    if streak >= force:
        return anti_addiction_lock_text(settings)
    if streak == remind:
        return f"玩了 {streak} 轮了，喘口气；到 {force} 轮会请你休息一下。"
    return ""


def _anti_addiction_record_success(
    context, *,
    lock,
    anti_addiction_notice,
    anti_addiction_state_for_update,
    db_connect,
    clock,
):
    if not context:
        return ""
    settings = context["settings"]
    player_id = context["player_id"]
    now = clock()
    with lock:
        with db_connect() as conn:
            state = anti_addiction_state_for_update(conn, player_id, settings, now)
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
    return anti_addiction_notice(streak, settings)
