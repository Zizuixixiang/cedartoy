"""Account, guest and save-slot identity resolution.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _game_player_ids(user, *,
    GAME_PLAYER_ID_RE,
    _account_username_aliases,
):
    ids = [str(user["id"])]
    for username in _account_username_aliases(user):
        if GAME_PLAYER_ID_RE.fullmatch(username):
            ids.append(username)
    return list(dict.fromkeys(ids))


def _account_slot_player_id(user_id, slot):
    user_id = str(int(user_id))
    return user_id if slot == 1 else f"{user_id}:{slot}"


def _account_slot_player_ids(user, *,
    GAME_PLAYER_ID_RE,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    _account_slot_player_id,
    _account_username_aliases,
):
    ids = [(_account_slot_player_id(user["id"], slot), slot) for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1)]
    for username in _account_username_aliases(user):
        if GAME_PLAYER_ID_RE.fullmatch(username):
            ids.append((username, 1))
    return list(dict.fromkeys(ids))


def _normalize_save_slot(slot, *,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
):
    if isinstance(slot, bool) or not isinstance(slot, (int, str)):
        return MIN_SAVE_SLOT
    try:
        normalized = int(slot)
    except ValueError:
        return MIN_SAVE_SLOT
    if not MIN_SAVE_SLOT <= normalized <= MAX_SAVE_SLOT:
        return MIN_SAVE_SLOT
    return normalized


def _bound_ai_slot_target_for_user(user, requested_player, *,
    MIN_SAVE_SLOT,
    _account_slot_player_id,
    _db_connect,
    re,
):
    """Resolve a browser-supplied player to one canonical bound AI save slot."""
    if not user or user.get("is_ai"):
        return None
    match = re.fullmatch(r"([1-9][0-9]*)(?::([1-5]))?", str(requested_player or ""))
    if not match:
        return None
    ai_user_id = int(match.group(1))
    slot = int(match.group(2) or MIN_SAVE_SLOT)
    with _db_connect() as conn:
        row = conn.execute(
            """
            SELECT ai.id, ai.username
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND ai.is_ai = 1
              AND ai.deleted_at IS NULL
            LIMIT 1
            """,
            (int(user["id"]), ai_user_id),
        ).fetchone()
    if not row:
        return None
    return {
        "player": _account_slot_player_id(row["id"], slot),
        "ai_user_id": int(row["id"]),
        "machine_name": str(row["username"]),
        "slot": slot,
    }


def _forest_bound_target_for_user(user, requested_player, *,
    _bound_ai_slot_target_for_user,
):
    return _bound_ai_slot_target_for_user(user, requested_player)


def _moonlit_bound_target_for_user(user, requested_player, *,
    _bound_ai_slot_target_for_user,
):
    return _bound_ai_slot_target_for_user(user, requested_player)


def _ai_life_bound_target_for_user(user, requested_player, *,
    _bound_ai_slot_target_for_user,
):
    return _bound_ai_slot_target_for_user(user, requested_player)


def _guest_player_id(raw, *,
    GUEST_PREFIX,
    PLAIN_PLAYER_ID_RE,
):
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


def _save_slot_from_arguments(arguments, *,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    _McpError,
):
    params = arguments.get("params")
    raw = params.get("slot", MIN_SAVE_SLOT) if isinstance(params, dict) else MIN_SAVE_SLOT
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    if raw < MIN_SAVE_SLOT or raw > MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    return raw


def _save_slot_from_account_arguments(arguments, *,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    _McpError,
):
    raw = arguments.get("slot", MIN_SAVE_SLOT)
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    slot = raw
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


def _guestify_mcp_payload(payload, *,
    _guest_player_id,
):
    """Direct test MCP endpoints have no token; isolate reported IDs as guests."""
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


def _bound_human_user_for_saves(raw_token, username, *,
    _McpError,
    _current_account,
    _db_connect,
    _row_dict,
):
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
