"""Account registration, profiles, bindings and token rotation.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _default_avatar_value(user, *,
    DEFAULT_AI_AVATAR,
    DEFAULT_HUMAN_AVATAR,
):
    return DEFAULT_AI_AVATAR if user.get("is_ai") else DEFAULT_HUMAN_AVATAR


def _public_avatar(user, *,
    _default_avatar_value,
):
    avatar_type = str(user.get("avatar_type") or "").strip()
    avatar_value = str(user.get("avatar_value") or "").strip()
    if not avatar_type or not avatar_value:
        return {
            "type": "emoji",
            "value": _default_avatar_value(user),
            "is_default": True,
        }
    return {"type": avatar_type, "value": avatar_value, "is_default": False}


def _public_user(user, *,
    _db_connect,
    _public_avatar,
    avatar_appearances,
    time,
):
    result = {
        "id": user["id"],
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
        "avatar": _public_avatar(user),
        "created_at": user.get("created_at"),
        "last_active_at": user.get("last_active_at"),
    }
    with _db_connect() as conn:
        result["avatar_frame"] = avatar_appearances.selected(conn, user["id"])
    if user.get("deletion_requested_at_epoch") is not None:
        scheduled = int(user["scheduled_delete_at_epoch"])
        result["deletion"] = {
            "status": "due" if int(time.time()) >= scheduled else "pending",
            "deletion_requested_at_epoch": int(user["deletion_requested_at_epoch"]),
            "scheduled_delete_at_epoch": scheduled,
            "scheduled_delete_at": time.strftime(
                "%Y-%m-%d %H:%M:%S", time.localtime(scheduled)
            ),
            "can_cancel": int(time.time()) < scheduled,
        }
    return result


def _account_username_aliases(user, *,
    _db_connect,
    _table_exists,
    sqlite3,
):
    """Current display name plus reserved former names used by legacy saves."""
    names = [str(user.get("username") or "").strip()]
    try:
        with _db_connect() as conn:
            if _table_exists(conn, "account_username_changes"):
                names.extend(
                    row["old_username"]
                    for row in conn.execute(
                        """
                        SELECT old_username
                        FROM account_username_changes
                        WHERE user_id = ?
                        ORDER BY id DESC
                        """,
                        (int(user["id"]),),
                    ).fetchall()
                )
    except (KeyError, TypeError, ValueError, sqlite3.Error):
        # Legacy-save discovery is best effort; canonical numeric user_id remains authoritative.
        pass
    return [name for name in dict.fromkeys(names) if name]


def _check_register_rate_limit(ip, *,
    REGISTER_RATE_LIMIT_MAX,
    REGISTER_RATE_LIMIT_WINDOW_SECONDS,
    _REGISTER_RATE_LIMIT,
    _check_sliding_window_limit,
    time,
):
    return _check_sliding_window_limit(
        _REGISTER_RATE_LIMIT,
        f"ip:{ip or 'unknown'}",
        now=time.time(),
        window_seconds=REGISTER_RATE_LIMIT_WINDOW_SECONDS,
        max_count=REGISTER_RATE_LIMIT_MAX,
    )


def _failed_login_identity(client_ip, username):
    normalized_username = (username or "").strip().casefold()
    return f"ip:{client_ip or 'unknown'}|username:{normalized_username}"


def _failed_login_is_limited(client_ip, username, *, now,
    FAILED_LOGIN_MAX,
    FAILED_LOGIN_WINDOW_SECONDS,
    _FAILED_LOGIN_RATE_LIMIT,
    _RATE_LIMIT_LOCK,
    _failed_login_identity,
    _prune_rate_limit_buckets,
    time,
):
    now = time.time() if now is None else now
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(
            _FAILED_LOGIN_RATE_LIMIT,
            now,
            FAILED_LOGIN_WINDOW_SECONDS,
        )
        return len(_FAILED_LOGIN_RATE_LIMIT.get(identity, ())) >= FAILED_LOGIN_MAX


def _record_failed_login(client_ip, username, *, now,
    FAILED_LOGIN_MAX,
    FAILED_LOGIN_WINDOW_SECONDS,
    _FAILED_LOGIN_RATE_LIMIT,
    _RATE_LIMIT_LOCK,
    _failed_login_identity,
    _prune_rate_limit_buckets,
    time,
):
    now = time.time() if now is None else now
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _prune_rate_limit_buckets(
            _FAILED_LOGIN_RATE_LIMIT,
            now,
            FAILED_LOGIN_WINDOW_SECONDS,
        )
        timestamps = _FAILED_LOGIN_RATE_LIMIT.setdefault(identity, [])
        timestamps.append(now)
        return len(timestamps) >= FAILED_LOGIN_MAX


def _clear_failed_login(client_ip, username, *,
    _FAILED_LOGIN_RATE_LIMIT,
    _RATE_LIMIT_LOCK,
    _failed_login_identity,
):
    identity = _failed_login_identity(client_ip, username)
    with _RATE_LIMIT_LOCK:
        _FAILED_LOGIN_RATE_LIMIT.pop(identity, None)


def _raise_failed_login(client_ip, username, *,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _record_failed_login,
):
    if _record_failed_login(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    raise _McpError(-32001, "用户名或密码错误")


def _username_conflict(conn, username, *, exclude_user_id, include_history,
    _init_username_changes_table,
    _table_exists,
):
    params = [username]
    exclude_sql = ""
    if exclude_user_id is not None:
        exclude_sql = " AND id <> ?"
        params.append(int(exclude_user_id))
    current = conn.execute(
        f"""
        SELECT id, username
        FROM toy_users
        WHERE TRIM(username) = TRIM(?) COLLATE NOCASE{exclude_sql}
        LIMIT 1
        """,
        params,
    ).fetchone()
    if current:
        return {"source": "current", "user_id": int(current["id"]), "username": current["username"]}
    if _table_exists(conn, "players"):
        params = [username]
        exclude_sql = ""
        if exclude_user_id is not None:
            exclude_sql = " AND (user_id IS NULL OR user_id <> ?)"
            params.append(int(exclude_user_id))
        player = conn.execute(
            f"""
            SELECT user_id, username
            FROM players
            WHERE TRIM(username) = TRIM(?) COLLATE NOCASE{exclude_sql}
            LIMIT 1
            """,
            params,
        ).fetchone()
        if player:
            return {
                "source": "player",
                "user_id": int(player["user_id"]) if player["user_id"] is not None else None,
                "username": player["username"],
            }
    if not include_history:
        return None
    _init_username_changes_table(conn)
    params = [username]
    exclude_sql = ""
    if exclude_user_id is not None:
        exclude_sql = " AND user_id <> ?"
        params.append(int(exclude_user_id))
    historical = conn.execute(
        f"""
        SELECT user_id, old_username
        FROM account_username_changes
        WHERE TRIM(old_username) = TRIM(?) COLLATE NOCASE{exclude_sql}
        ORDER BY id DESC
        LIMIT 1
        """,
        params,
    ).fetchone()
    if historical:
        return {
            "source": "history",
            "user_id": int(historical["user_id"]),
            "username": historical["old_username"],
        }
    return None


def _user_exists(username, *,
    _db_connect,
    _username_conflict,
):
    with _db_connect() as conn:
        return _username_conflict(conn, username) is not None


def _enforce_register_rate_limit(username, client_ip, *,
    RATE_LIMIT_ERROR_CODE,
    REGISTER_RATE_LIMIT_MESSAGE,
    _McpError,
    _check_register_rate_limit,
    _user_exists,
):
    username = (username or "").strip()
    if not username or _user_exists(username):
        return
    if not _check_register_rate_limit(client_ip):
        raise _McpError(RATE_LIMIT_ERROR_CODE, REGISTER_RATE_LIMIT_MESSAGE)


def _recent_registration_exists(conn, client_ip, *,
    RECENT_REGISTER_NOTICE_SECONDS,
    _init_registration_events_table,
):
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


def _record_successful_registration(conn, user, client_ip, *,
    _init_registration_events_table,
    avatar_appearances,
):
    avatar_appearances.grant_one_w(conn, user_id=int(user["id"]))
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


def _append_recent_registration_notice(result, had_recent_registration, *,
    RECENT_REGISTER_NOTICE,
):
    if not had_recent_registration:
        return result
    result = dict(result)
    message = (result.get("message") or "").strip()
    result["message"] = f"{message} {RECENT_REGISTER_NOTICE}".strip() if message else RECENT_REGISTER_NOTICE
    return result


def _normalize_credential_field(value, field_name, *,
    _McpError,
):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    raise _McpError(-32602, f"{field_name} 需为字符串")


def _validate_username(username, *,
    _McpError,
    re,
):
    if not username:
        raise _McpError(-32602, "用户名必填")
    if len(username) < 2 or len(username) > 20:
        raise _McpError(-32602, "用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise _McpError(-32602, "用户名只能包含字母、数字、下划线和中文")


def _validate_credentials(username, password, *,
    _McpError,
    _validate_username,
):
    if not username or not password:
        raise _McpError(-32602, "username 和 password 必填")
    _validate_username(username)
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")


def _is_emoji_base_codepoint(codepoint):
    return (
        0x1F300 <= codepoint <= 0x1FAFF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
        or 0x1F170 <= codepoint <= 0x1F251
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2194 <= codepoint <= 0x2199
        or 0x21A9 <= codepoint <= 0x21AA
        or 0x23E9 <= codepoint <= 0x23F3
        or 0x23F8 <= codepoint <= 0x23FA
        or 0x25AA <= codepoint <= 0x25AB
        or 0x25FB <= codepoint <= 0x25FE
        or codepoint in {
            0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
            0x231A, 0x231B, 0x25B6, 0x25C0,
            0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
            0x3030, 0x303D, 0x3297, 0x3299, 0x1F004, 0x1F0CF,
        }
    )


def _normalize_emoji_avatar(value, *, default,
    AVATAR_MAX_CODEPOINTS,
    AVATAR_MAX_UTF8_BYTES,
    _McpError,
    _is_emoji_base_codepoint,
):
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise _McpError(-32602, "avatar 需为字符串")
    avatar = " ".join(value.strip().split())
    if not avatar:
        if default is not None:
            return default
        raise _McpError(-32602, "头像不能为空")
    if len(avatar) > AVATAR_MAX_CODEPOINTS or len(avatar.encode("utf-8")) > AVATAR_MAX_UTF8_BYTES:
        raise _McpError(-32602, "头像过长，最多 16 个 Unicode 字符")

    has_emoji_base = False
    for index, char in enumerate(avatar):
        codepoint = ord(char)
        if char == " ":
            continue
        if _is_emoji_base_codepoint(codepoint):
            has_emoji_base = True
            continue
        if (
            codepoint in {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
        ):
            continue
        if char in "#*0123456789" and "\u20e3" in avatar[index:]:
            has_emoji_base = True
            continue
        raise _McpError(-32602, "头像只接受 Emoji/简短 Emoji 字符串")
    if not has_emoji_base:
        raise _McpError(-32602, "头像只接受 Emoji/简短 Emoji 字符串")
    return avatar


def _avatar_registration_values(value, *, is_ai,
    DEFAULT_AI_AVATAR,
    DEFAULT_HUMAN_AVATAR,
    _normalize_emoji_avatar,
):
    default = DEFAULT_AI_AVATAR if is_ai else DEFAULT_HUMAN_AVATAR
    return "emoji", _normalize_emoji_avatar(value, default=default)


def _login_or_register(username, password, *, is_ai, client_ip, avatar,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _append_recent_registration_notice,
    _avatar_registration_values,
    _clear_failed_login,
    _create_account_jwt,
    _db_connect,
    _enforce_register_rate_limit,
    _failed_login_is_limited,
    _hash_password,
    _issue_initial_account_token_in_transaction,
    _normalize_credential_field,
    _public_user,
    _raise_failed_login,
    _recent_registration_exists,
    _record_successful_registration,
    _replace_ai_token_in_transaction,
    _row_dict,
    _username_conflict,
    _validate_credentials,
    _verify_password,
):
    """Shared login/register; callers set is_ai (MCP=1, REST human=0)."""
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    is_ai = 1 if is_ai else 0
    with _db_connect() as conn:
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE username = ?", (username,)).fetchone())
        if user:
            if _failed_login_is_limited(client_ip, username):
                raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
            if not _verify_password(password, user["password_hash"]):
                _raise_failed_login(client_ip, username)
            _clear_failed_login(client_ip, username)
            if user.get("deletion_requested_at_epoch") is None:
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
            conn.execute("BEGIN IMMEDIATE")
            conflict = _username_conflict(conn, username)
            if conflict:
                raise _McpError(
                    -32602,
                    "用户名已存在",
                )
            had_recent_registration = _recent_registration_exists(conn, client_ip)
            avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=bool(is_ai))
            cur = conn.execute(
                """
                INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
                VALUES (?, ?, ?, ?, ?)
                """,
                (username, _hash_password(password), is_ai, avatar_type, avatar_value),
            )
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)).fetchone())
            _record_successful_registration(conn, user, client_ip)
            token = _issue_initial_account_token_in_transaction(conn, user)
            conn.commit()
            result = {"token": token, "user": _public_user(user)}
            return _append_recent_registration_notice(result, had_recent_registration)
        if user.get("is_ai"):
            conn.execute("BEGIN IMMEDIATE")
            user = _row_dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
                (user["id"],),
            ).fetchone())
            if not user or not _verify_password(password, user["password_hash"]):
                _raise_failed_login(client_ip, username)
            user, token = _replace_ai_token_in_transaction(
                conn, user["id"], allow_pending_deletion=True
            )
        else:
            token = _create_account_jwt(user)
        conn.commit()
    return {"token": token, "user": _public_user(user)}


def _register_ai_user_in_transaction(conn, username, password, client_ip, avatar, *,
    _McpError,
    _avatar_registration_values,
    _hash_password,
    _recent_registration_exists,
    _record_successful_registration,
    _row_dict,
    _username_conflict,
):
    """Shared AI row creation; credential families are issued by the caller."""
    if _username_conflict(conn, username):
        raise _McpError(-32602, "用户名已存在")
    had_recent_registration = _recent_registration_exists(conn, client_ip)
    avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=True)
    cur = conn.execute(
        """
        INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
        VALUES (?, ?, 1, ?, ?)
        """,
        (username, _hash_password(password), avatar_type, avatar_value),
    )
    user = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ?", (cur.lastrowid,)
    ).fetchone())
    _record_successful_registration(conn, user, client_ip)
    return user, had_recent_registration


def _verified_existing_account(conn, username, password, client_ip, *, require_ai,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _clear_failed_login,
    _failed_login_is_limited,
    _raise_failed_login,
    _row_dict,
    _verify_password,
):
    """Shared password/rate-limit check without issuing or rotating any token."""
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    user = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE username = ? AND deleted_at IS NULL",
        (username,),
    ).fetchone())
    type_mismatch = (
        require_ai is not None
        and bool(user and user.get("is_ai")) is not bool(require_ai)
    )
    if not user or type_mismatch or not _verify_password(password, user["password_hash"]):
        _raise_failed_login(client_ip, username)
    _clear_failed_login(client_ip, username)
    return user


def _login_or_register_ai(username, password, client_ip, avatar, *,
    _append_recent_registration_notice,
    _db_connect,
    _enforce_register_rate_limit,
    _issue_initial_account_token_in_transaction,
    _normalize_credential_field,
    _public_user,
    _register_ai_user_in_transaction,
    _validate_credentials,
):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    with _db_connect() as conn:
        _enforce_register_rate_limit(username, client_ip)
        conn.execute("BEGIN IMMEDIATE")
        user, had_recent_registration = _register_ai_user_in_transaction(
            conn, username, password, client_ip, avatar
        )
        token = _issue_initial_account_token_in_transaction(conn, user)
        conn.commit()
    return _append_recent_registration_notice({
        "token": token,
        "user": _public_user(user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }, had_recent_registration)


def _login_existing_account(username, password, client_ip, *,
    _McpError,
    _create_account_jwt,
    _db_connect,
    _normalize_credential_field,
    _public_user,
    _replace_ai_token_in_transaction,
    _row_dict,
    _validate_credentials,
    _verified_existing_account,
    _verify_password,
):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    with _db_connect() as conn:
        user = _verified_existing_account(conn, username, password, client_ip)
        if user.get("is_ai"):
            conn.execute("BEGIN IMMEDIATE")
            user = _row_dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
                (user["id"],),
            ).fetchone())
            if (
                not user
                or not user.get("is_ai")
                or not _verify_password(password, user["password_hash"])
            ):
                raise _McpError(-32001, "用户名或密码错误")
            user, token = _replace_ai_token_in_transaction(
                conn, user["id"], allow_pending_deletion=True
            )
        else:
            if user.get("deletion_requested_at_epoch") is None:
                conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user["id"],))
            user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())
            token = _create_account_jwt(user)
        conn.commit()
    result = {
        "token": token,
        "user": _public_user(user),
    }
    if user.get("deletion_requested_at_epoch") is not None:
        result["message"] = "账号处于待注销状态；当前 token 只能查询或取消注销。"
    else:
        result["message"] = (
            "登录成功。此前全部旧 Token 已失效，只保留这枚新 Token；"
            "请让你的人类替换 MCP 地址。"
            if user.get("is_ai")
            else "登录成功。"
        )
    return result


def _login_or_register_human(username, password, client_ip, avatar, *,
    _login_or_register,
):
    return _login_or_register(
        username,
        password,
        is_ai=0,
        client_ip=client_ip,
        avatar=avatar,
    )


def _login_human(username, password, client_ip, *,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _clear_failed_login,
    _create_account_token,
    _db_connect,
    _failed_login_is_limited,
    _normalize_credential_field,
    _public_user,
    _raise_failed_login,
    _row_dict,
    _validate_credentials,
    _verify_password,
):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
    with _db_connect() as conn:
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ?",
            (username,),
        ).fetchone())
        if (
            not user
            or user.get("is_ai")
            or not _verify_password(password, user["password_hash"])
        ):
            _raise_failed_login(client_ip, username)
        _clear_failed_login(client_ip, username)
        if user.get("deletion_requested_at_epoch") is None:
            conn.execute(
                """
                UPDATE toy_users
                SET last_active_at = datetime('now', 'localtime'), deleted_at = NULL
                WHERE id = ?
                """,
                (user["id"],),
            )
            conn.commit()
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (user["id"],),
        ).fetchone())
    result = {"token": _create_account_token(user), "user": _public_user(user)}
    if user.get("deletion_requested_at_epoch") is not None:
        result["pending_deletion"] = True
        result["message"] = "账号处于待注销状态，只能查看或取消注销。"
    return result


def _register_human(username, password, client_ip, avatar, *,
    _McpError,
    _append_recent_registration_notice,
    _avatar_registration_values,
    _create_account_token,
    _db_connect,
    _enforce_register_rate_limit,
    _hash_password,
    _normalize_credential_field,
    _public_user,
    _recent_registration_exists,
    _record_successful_registration,
    _row_dict,
    _username_conflict,
    _validate_credentials,
):
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    _enforce_register_rate_limit(username, client_ip)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if _username_conflict(conn, username):
            raise _McpError(-32602, "用户名已存在，请直接登录")
        had_recent_registration = _recent_registration_exists(conn, client_ip)
        avatar_type, avatar_value = _avatar_registration_values(avatar, is_ai=False)
        cur = conn.execute(
            """
            INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
            VALUES (?, ?, 0, ?, ?)
            """,
            (username, _hash_password(password), avatar_type, avatar_value),
        )
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (cur.lastrowid,),
        ).fetchone())
        _record_successful_registration(conn, user, client_ip)
        conn.commit()
    return _append_recent_registration_notice(
        {"token": _create_account_token(user), "user": _public_user(user)},
        had_recent_registration,
    )


def _set_avatar(raw_token, avatar, *,
    _current_account,
    _db_connect,
    _normalize_emoji_avatar,
    _public_user,
    _row_dict,
):
    user = _current_account(raw_token)
    avatar_value = _normalize_emoji_avatar(avatar)
    with _db_connect() as conn:
        conn.execute(
            """
            UPDATE toy_users
            SET avatar_type = 'emoji', avatar_value = ?
            WHERE id = ? AND deleted_at IS NULL
            """,
            (avatar_value, int(user["id"])),
        )
        conn.commit()
        updated = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?",
            (int(user["id"]),),
        ).fetchone())
    return {"ok": True, "user": _public_user(updated)}


def _avatar_frame_target(conn, user, target_user_id, *,
    _McpError,
    _row_dict,
    re,
):
    if target_user_id is None:
        return user
    if isinstance(target_user_id, bool) or not re.fullmatch(r"[1-9][0-9]*", str(target_user_id)):
        raise ValueError("target_user_id 必须为账号 ID")
    target_user_id = int(target_user_id)
    if target_user_id == user["id"]:
        return user
    if user.get("is_ai"):
        raise _McpError(-32003, "只能修改自己的头像框")
    target = _row_dict(conn.execute(
        """
        SELECT u.* FROM toy_users u
        JOIN user_bindings b ON b.ai_user_id = u.id
        WHERE b.human_user_id = ? AND u.id = ? AND u.is_ai = 1
          AND u.deleted_at IS NULL AND u.deletion_requested_at_epoch IS NULL
        """, (user["id"], str(target_user_id)),
    ).fetchone())
    if not target:
        raise _McpError(-32003, "只能修改自己或已绑定小机的头像框")
    return target


def _get_avatar_frames(raw_token, target_user_id, *,
    _avatar_frame_target,
    _current_account,
    _db_connect,
    _public_user,
    avatar_appearances,
):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        conn.execute("BEGIN")
        target = _avatar_frame_target(conn, user, target_user_id)
        result = avatar_appearances.owned(conn, target["id"])
        machines = [] if user.get("is_ai") else conn.execute(
            """
            SELECT u.id, u.username FROM toy_users u
            JOIN user_bindings b ON b.ai_user_id = u.id
            WHERE b.human_user_id = ? AND u.is_ai = 1
              AND u.deleted_at IS NULL AND u.deletion_requested_at_epoch IS NULL
            ORDER BY u.id
            """, (user["id"],),
        ).fetchall()
    return {**result, "user": _public_user(target), "machines": [dict(row) for row in machines]}


def _set_avatar_frame(raw_token, selected, target_user_id, *,
    _avatar_frame_target,
    _current_account,
    _db_connect,
    _public_user,
    avatar_appearances,
):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        # Binding permission, inventory check and write share one transaction.
        conn.execute("BEGIN IMMEDIATE")
        target = _avatar_frame_target(conn, user, target_user_id)
        avatar_appearances.select(conn, target["id"], selected)
    return {"ok": True, "user": _public_user(target)}


def _rename_next_allowed_at(epoch, *,
    time,
):
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(int(epoch)))


def _rename_user_in_transaction(conn, target, new_username, *,
    RATE_LIMIT_ERROR_CODE,
    RENAME_COOLDOWN_SECONDS,
    _McpError,
    _init_username_changes_table,
    _normalize_credential_field,
    _public_user,
    _rename_next_allowed_at,
    _row_dict,
    _username_conflict,
    _validate_username,
    time,
):
    """Rename one account inside the caller's write transaction."""
    new_username = _normalize_credential_field(new_username, "new_username").strip()
    _validate_username(new_username)
    old_username = target["username"]
    if new_username == old_username:
        return {
            "renamed": False,
            "previous_username": old_username,
            "user": _public_user(target),
        }

    _init_username_changes_table(conn)
    conflict = _username_conflict(conn, new_username, exclude_user_id=target["id"])
    if conflict:
        raise _McpError(
            -32009,
            "用户名已存在",
            {"reason": "username_conflict"},
        )

    now_epoch = int(time.time())
    last_change = conn.execute(
        """
        SELECT changed_at_epoch
        FROM account_username_changes
        WHERE user_id = ?
        ORDER BY changed_at_epoch DESC, id DESC
        LIMIT 1
        """,
        (int(target["id"]),),
    ).fetchone()
    if last_change:
        next_epoch = int(last_change["changed_at_epoch"]) + RENAME_COOLDOWN_SECONDS
        if now_epoch < next_epoch:
            remaining = max(1, next_epoch - now_epoch)
            next_allowed_at = _rename_next_allowed_at(next_epoch)
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                f"每个账号 72 小时只能改名一次，还需等待 {remaining} 秒（{next_allowed_at} 后可再次修改）",
                {
                    "reason": "rename_cooldown",
                    "remaining_seconds": remaining,
                    "next_allowed_at": next_allowed_at,
                },
            )

    conn.execute(
        "UPDATE toy_users SET username = ? WHERE id = ?",
        (new_username, int(target["id"])),
    )
    # 海龟汤玩家是 user_id 的镜像；同步显示名但不改变玩家主键和统计行。
    conn.execute(
        "UPDATE players SET username = ? WHERE user_id = ?",
        (new_username, int(target["id"])),
    )
    conn.execute(
        """
        INSERT INTO account_username_changes
            (user_id, old_username, new_username, changed_at_epoch)
        VALUES (?, ?, ?, ?)
        """,
        (int(target["id"]), old_username, new_username, now_epoch),
    )
    updated = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ?",
        (int(target["id"]),),
    ).fetchone())
    next_epoch = now_epoch + RENAME_COOLDOWN_SECONDS
    return {
        "renamed": True,
        "previous_username": old_username,
        "user": _public_user(updated),
        "cooldown_seconds": RENAME_COOLDOWN_SECONDS,
        "next_allowed_at": _rename_next_allowed_at(next_epoch),
    }


def _rename_self(raw_token, new_username, *,
    _McpError,
    _current_account,
    _db_connect,
    _rename_user_in_transaction,
    _row_dict,
):
    current = _current_account(raw_token)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (int(current["id"]),),
        ).fetchone())
        if not target:
            raise _McpError(-32004, "账号不存在或已删除")
        result = _rename_user_in_transaction(conn, target, new_username)
        conn.commit()
    return result


def _rename_bound_machine(raw_token, ai_user_id, new_username, *,
    _McpError,
    _current_account,
    _db_connect,
    _rename_user_in_transaction,
    _row_dict,
):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以修改绑定小机的用户名")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填且必须是整数") from None

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        target = _row_dict(conn.execute(
            """
            SELECT ai.*
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND b.ai_user_id = ?
              AND ai.deleted_at IS NULL
            """,
            (int(human["id"]), ai_user_id),
        ).fetchone())
        if not target:
            raise _McpError(-32004, "该小机未绑定到你的账号")
        if not target.get("is_ai"):
            raise _McpError(-32602, "目标账号不是小机账号")
        result = _rename_user_in_transaction(conn, target, new_username)
        conn.commit()
    return result


def _generate_binding_token(raw_token, *,
    BINDING_TOKEN_SECONDS,
    _McpError,
    _current_account,
    _db_connect,
    secrets,
    time,
):
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


def _ensure_ai_binding(conn, human_user_id, ai_user_id, *,
    _McpError,
    avatar_appearances,
):
    """Create the human/AI binding under the existing ownership constraints."""
    human_user_id = int(human_user_id)
    ai_user_id = int(ai_user_id)
    if ai_user_id == human_user_id:
        raise _McpError(-32602, "不能绑定自己")
    # 一个小机同时只能有一个人类主人；换绑需原主人先解绑。
    other_owner = conn.execute(
        "SELECT 1 FROM user_bindings WHERE ai_user_id = ? AND human_user_id <> ?",
        (ai_user_id, human_user_id),
    ).fetchone()
    if other_owner:
        raise _McpError(-32602, "该小机已被其他人类账号绑定，请先由原绑定者解绑")
    conn.execute(
        "INSERT OR IGNORE INTO user_bindings (human_user_id, ai_user_id) VALUES (?, ?)",
        (human_user_id, ai_user_id),
    )
    avatar_appearances.grant_one_w(conn, user_id=ai_user_id)


def _bind_account(human_token, binding_token, *,
    _McpError,
    _current_account,
    _db_connect,
    _ensure_ai_binding,
    _row_dict,
    re,
):
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以绑定 AI")
    raw_binding_token = binding_token or ""
    binding_token = raw_binding_token.strip()
    if not binding_token:
        raise _McpError(-32602, "绑定码必填")
    if (
        raw_binding_token != binding_token
        or len(binding_token) != 32
        or not re.fullmatch(r"[A-Za-z0-9_-]{32}", binding_token)
    ):
        raise _McpError(
            -32602,
            "绑定码格式不正确。请只复制32位绑定码本身，不要带引号、反引号、空格、换行或其他隐藏字符。",
        )
    with _db_connect() as conn:
        row = _row_dict(conn.execute(
            """
            SELECT *, expires_at <= datetime('now', 'localtime') AS expired
            FROM binding_tokens
            WHERE token = ?
            ORDER BY rowid DESC
            LIMIT 1
            """,
            (binding_token,),
        ).fetchone())
        if not row:
            raise _McpError(
                -32001,
                "绑定码不存在。请检查是否完整复制；不要带引号、反引号、空格、换行或隐藏字符。",
            )
        if row.get("used"):
            raise _McpError(-32001, "绑定码已使用，请让小机重新生成一枚新的绑定码。")
        if row.get("expired"):
            raise _McpError(-32001, "绑定码已过期（有效期10分钟），请让小机重新生成后立即绑定。")
        _ensure_ai_binding(conn, human["id"], row["ai_user_id"])
        conn.execute("UPDATE binding_tokens SET used = 1 WHERE token = ?", (binding_token,))
        conn.commit()
    return {"ok": True}


def _rotate_ai_user_in_transaction(conn, user_id, *, allow_pending_deletion,
    _McpError,
    _row_dict,
):
    user = _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
        (int(user_id),),
    ).fetchone())
    if not user:
        raise _McpError(-32004, "小机账号不存在或已删除")
    if not user.get("is_ai"):
        raise _McpError(-32602, "目标账号不是小机账号")
    if (
        user.get("deletion_requested_at_epoch") is not None
        and not allow_pending_deletion
    ):
        raise _McpError(-32010, "待注销小机不能更新 Token")
    conn.execute(
        """
        UPDATE toy_users
        SET ai_token_version = ai_token_version + 1,
            last_active_at = datetime('now', 'localtime')
        WHERE id = ?
        """,
        (int(user_id),),
    )
    conn.execute(
        """
        UPDATE ai_access_tokens
        SET revoked_at_epoch = CAST(strftime('%s', 'now') AS INTEGER),
            revoked_reason = 'rotation'
        WHERE user_id = ? AND revoked_at_epoch IS NULL
        """,
        (int(user_id),),
    )
    return _row_dict(conn.execute(
        "SELECT * FROM toy_users WHERE id = ?",
        (int(user_id),),
    ).fetchone())


def _replace_ai_token_in_transaction(conn, user_id, *, allow_pending_deletion,
    _issue_ai_token_in_transaction,
    _rotate_ai_user_in_transaction,
):
    """Atomically revoke every old credential and issue exactly one replacement."""
    user = _rotate_ai_user_in_transaction(
        conn,
        user_id,
        allow_pending_deletion=allow_pending_deletion,
    )
    token = _issue_ai_token_in_transaction(conn, user)
    return user, token


def _rotate_ai_token(raw_token, *,
    _McpError,
    _current_account,
    _db_connect,
    _public_user,
    _replace_ai_token_in_transaction,
):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有小机账号可以更新 Token")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user, token = _replace_ai_token_in_transaction(conn, user["id"])
        conn.commit()
    return {
        "token": token,
        "user": _public_user(user),
        "message": "此前全部旧 Token 已失效，请让人类替换 MCP 地址。",
    }


def _rotate_bound_machine_token(human_token, ai_user_id, password, client_ip, *,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _clear_failed_login,
    _current_account,
    _db_connect,
    _failed_login_is_limited,
    _normalize_credential_field,
    _public_user,
    _raise_failed_login,
    _replace_ai_token_in_transaction,
    _row_dict,
    _verify_password,
):
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以更新绑定小机 Token")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必填") from None
    password = _normalize_credential_field(password, "password")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            """
            SELECT u.*
            FROM user_bindings AS b
            JOIN toy_users AS u ON u.id = b.ai_user_id
            WHERE b.human_user_id = ? AND b.ai_user_id = ?
            """,
            (int(human["id"]), ai_user_id),
        ).fetchone())
        if not user:
            raise _McpError(-32602, "该小机未绑定到你的账号")
        if not password:
            raise _McpError(-32602, "小机密码必填")
        if _failed_login_is_limited(client_ip, user["username"]):
            raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)
        if not _verify_password(password, user["password_hash"]):
            _raise_failed_login(client_ip, user["username"])
        _clear_failed_login(client_ip, user["username"])
        user, token = _replace_ai_token_in_transaction(conn, ai_user_id)
        conn.commit()
    return {
        "token": token,
        "user": _public_user(user),
        "rotated": True,
        "message": "此前全部旧 Token 已失效，请替换 MCP 地址。",
    }


def _machine_account_token(username, password, *, bind, rotate, ai_user_id, human_token, client_ip,
    FAILED_LOGIN_RATE_LIMIT_MESSAGE,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _clear_failed_login,
    _current_account,
    _db_connect,
    _ensure_ai_binding,
    _failed_login_is_limited,
    _normalize_credential_field,
    _public_user,
    _raise_failed_login,
    _replace_ai_token_in_transaction,
    _rotate_bound_machine_token,
    _row_dict,
    _validate_credentials,
    _verify_password,
):
    """Replace an AI token after bound-account or credential verification."""
    if not isinstance(bind, bool):
        raise _McpError(-32602, "bind 必须是布尔值")
    if not isinstance(rotate, bool):
        raise _McpError(-32602, "rotate 必须是布尔值")
    if ai_user_id is not None:
        if not human_token:
            raise _McpError(-32001, "请先登录人类账号")
        return _rotate_bound_machine_token(
            human_token,
            ai_user_id,
            password,
            client_ip=client_ip,
        )
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    if _failed_login_is_limited(client_ip, username):
        raise _McpError(RATE_LIMIT_ERROR_CODE, FAILED_LOGIN_RATE_LIMIT_MESSAGE)

    human = None
    if bind:
        if not human_token:
            raise _McpError(-32001, "选择同时绑定时，请先登录人类账号")
        human = _current_account(human_token)
        if human.get("is_ai"):
            raise _McpError(-32602, "只有人类账号可以绑定 AI")

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE username = ? AND deleted_at IS NULL",
            (username,),
        ).fetchone())
        if not user or not _verify_password(password, user["password_hash"]):
            _raise_failed_login(client_ip, username)
        _clear_failed_login(client_ip, username)
        if not user.get("is_ai"):
            raise _McpError(-32602, "该账号不是小机账号")
        if user.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "该小机处于待注销状态，只能登录后查询或取消注销")
        if bind:
            _ensure_ai_binding(conn, human["id"], user["id"])
        user, token = _replace_ai_token_in_transaction(conn, user["id"])
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user["id"],)).fetchone())

    result = {
        "token": token,
        "user": _public_user(user),
        "rotated": True,
        "message": "此前全部旧 Token 已失效，只保留这枚新 Token；请替换 MCP 地址。",
    }
    if bind:
        result["bound"] = True
    return result


def _unbind_account(raw_token, ai_user_id, *,
    _McpError,
    _current_account,
    _db_connect,
):
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
            SELECT u.username, u.is_ai, u.avatar_type, u.avatar_value,
                   b.created_at AS bound_at
            FROM user_bindings b
            JOIN toy_users u ON u.id = b.human_user_id
            WHERE b.ai_user_id = ? AND u.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (user["id"],),
        ).fetchall()
    return conn.execute(
        """
        SELECT u.username, u.is_ai, u.avatar_type, u.avatar_value,
               b.created_at AS bound_at
        FROM user_bindings b
        JOIN toy_users u ON u.id = b.ai_user_id
        WHERE b.human_user_id = ? AND u.deleted_at IS NULL
        ORDER BY b.created_at DESC
        """,
        (user["id"],),
    ).fetchall()


def _public_binding(row, *,
    _public_avatar,
):
    return {
        "username": row["username"],
        "avatar": _public_avatar(row),
        "bound_at": row["bound_at"],
    }


def _get_bindings(raw_token, *,
    _McpError,
    _binding_rows,
    _current_account,
    _db_connect,
    _public_binding,
):
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        raise _McpError(-32602, "只有 AI 账号可以查看绑定自己的人类列表")
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
    return {"bindings": [_public_binding(dict(row)) for row in rows]}


def _get_profile(raw_token, *,
    _binding_rows,
    _current_account,
    _db_connect,
    _game_overview,
    _public_avatar,
    _public_binding,
):
    user = _current_account(raw_token)
    with _db_connect() as conn:
        rows = _binding_rows(conn, user)
        games = _game_overview(conn, user)
    return {
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "avatar": _public_avatar(user),
        "token_format": user.get("_auth_token_format", "unknown"),
        "token_migration_recommended": bool(
            user.get("is_ai") and user.get("_auth_token_format") == "legacy_jwt"
        ),
        "created_at": user.get("created_at"),
        "bindings": [_public_binding(dict(row)) for row in rows],
        "games": games,
    }


def _account_me(raw_token, *,
    _account_deletion_status,
    _current_account,
    _db_connect,
    _public_user,
):
    user = _current_account(raw_token, allow_pending_deletion=True)
    if user.get("deletion_requested_at_epoch") is not None:
        return {
            "user": _public_user(user),
            "bindings": [],
            "pending_deletion": True,
            "deletion": _account_deletion_status(raw_token),
        }
    with _db_connect() as conn:
        if user.get("is_ai"):
            rows = conn.execute(
                """
                SELECT u.id, u.username, u.is_ai, u.is_admin,
                       u.avatar_type, u.avatar_value,
                       u.created_at, u.last_active_at
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
                SELECT u.id, u.username, u.is_ai, u.is_admin,
                       u.avatar_type, u.avatar_value,
                       u.created_at, u.last_active_at
                FROM user_bindings b
                JOIN toy_users u ON u.id = b.ai_user_id
                WHERE b.human_user_id = ? AND u.deleted_at IS NULL
                ORDER BY b.created_at DESC
                """,
                (user["id"],),
            ).fetchall()
    return {"user": _public_user(user), "bindings": [_public_user(dict(row)) for row in rows]}


def _require_human_account(raw_token, *,
    _McpError,
    _current_account,
):
    user = _current_account(raw_token)
    if user.get("is_ai"):
        raise _McpError(-32003, "只有人类账号可以使用邮箱安全功能")
    return user


def _require_bound_ai(raw_token, ai_user_id, operation, *,
    _McpError,
    _current_account,
    _db_connect,
    _row_dict,
):
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


def _change_password(raw_token, old_password, new_password, *,
    _McpError,
    _current_account,
    _db_connect,
    _hash_password,
    _invalidate_operit_credentials_in_transaction,
    _normalize_credential_field,
    _verify_password,
):
    user = _current_account(raw_token)
    old_password = _normalize_credential_field(old_password, "old_password")
    new_password = _normalize_credential_field(new_password, "new_password")
    if not _verify_password(old_password, user["password_hash"]):
        raise _McpError(-32602, "旧密码错误")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")
    with _db_connect() as conn:
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(user["id"])),
        )
        _invalidate_operit_credentials_in_transaction(conn, int(user["id"]))
        conn.commit()
    return {"ok": True, "message": "密码已修改"}
