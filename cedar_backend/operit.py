"""Operit sessions, web tickets and Duel coordination.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _operit_token_hash(raw_token, *,
    hashlib,
):
    return hashlib.sha256(str(raw_token).encode("utf-8")).hexdigest()


def _operit_client_id(client_id, *,
    _McpError,
):
    if not isinstance(client_id, str):
        raise _McpError(-32602, "client_id 必须是字符串")
    if client_id != client_id.strip() or not 1 <= len(client_id) <= 200:
        raise _McpError(-32602, "client_id 长度须为 1-200，且不能有首尾空白")
    if any(ord(char) < 32 or ord(char) == 127 for char in client_id):
        raise _McpError(-32602, "client_id 不能包含控制字符")
    if len(client_id.encode("utf-8")) > 512:
        raise _McpError(-32602, "client_id 过长")
    return client_id


def _operit_client_id_hash(client_id, *,
    _operit_client_id,
    hashlib,
):
    normalized = _operit_client_id(client_id)
    return hashlib.sha256(
        b"cedartoy-operit-client-v1\0" + normalized.encode("utf-8")
    ).hexdigest()


def _reject_pending_operit_user(user, *,
    _McpError,
):
    if user.get("deletion_requested_at_epoch") is not None:
        raise _McpError(
            -32010,
            "账号处于待注销状态，不能创建或使用 Operit 会话",
            {"reason": "pending_deletion"},
        )


def _issue_operit_session_in_transaction(conn, user, client_id, *, now_epoch,
    OPERIT_SESSION_FORMAT_VERSION,
    OPERIT_SESSION_SECONDS,
    OPERIT_SESSION_TOKEN_BYTES,
    OPERIT_SESSION_TOKEN_PREFIX,
    _init_operit_schema,
    _operit_client_id_hash,
    _operit_token_hash,
    _reject_pending_operit_user,
    secrets,
    time,
):
    """Issue only an Operit credential; never reads or mutates ai_access_tokens."""
    _init_operit_schema(conn)
    _reject_pending_operit_user(user)
    raw_token = OPERIT_SESSION_TOKEN_PREFIX + secrets.token_urlsafe(
        OPERIT_SESSION_TOKEN_BYTES
    )
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    client_hash = _operit_client_id_hash(client_id)
    # One caller card maps to one active machine. Re-login replaces only that
    # caller's Operit session, even when the card switches machines. It cannot
    # touch MCP tokens or sessions belonging to another caller.
    conn.execute(
        """
        UPDATE operit_ai_sessions SET revoked_at_epoch = ?
        WHERE client_id_hash = ? AND revoked_at_epoch IS NULL
        """,
        (now_epoch, client_hash),
    )
    conn.execute(
        """
        INSERT INTO operit_ai_sessions (
            token_hash, user_id, client_id_hash, format_version,
            created_at_epoch, expires_at_epoch, last_used_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            _operit_token_hash(raw_token), int(user["id"]),
            client_hash, OPERIT_SESSION_FORMAT_VERSION,
            now_epoch, now_epoch + OPERIT_SESSION_SECONDS, now_epoch,
        ),
    )
    return raw_token, now_epoch + OPERIT_SESSION_SECONDS


def _operit_confirmed_human(human_token, confirm, *,
    _McpError,
    _current_account,
):
    if confirm is not True:
        raise _McpError(-32602, "必须由已登录人类显式确认")
    human = _current_account(human_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以确认绑定或网页登录")
    return human


def _create_operit_ai_session(action, username, password, client_id, *, client_ip, avatar, bind_to_human, confirm_binding, human_token,
    _McpError,
    _append_recent_registration_notice,
    _db_connect,
    _enforce_register_rate_limit,
    _ensure_ai_binding,
    _init_operit_schema,
    _issue_operit_session_in_transaction,
    _normalize_credential_field,
    _operit_client_id,
    _operit_confirmed_human,
    _public_user,
    _register_ai_user_in_transaction,
    _reject_pending_operit_user,
    _row_dict,
    _validate_credentials,
    _verified_existing_account,
    _verify_password,
):
    """Register/login an AI without creating, rotating, or revoking MCP tokens."""
    if action not in {"register", "login"}:
        raise _McpError(-32602, "action 只支持 register 或 login")
    username = _normalize_credential_field(username, "username").strip()
    password = _normalize_credential_field(password, "password")
    _validate_credentials(username, password)
    client_id = _operit_client_id(client_id)
    human = None
    if bind_to_human is True:
        human = _operit_confirmed_human(human_token, confirm_binding)
    elif bind_to_human is not False and bind_to_human is not None:
        raise _McpError(-32602, "bind_to_human 必须是布尔值")

    with _db_connect() as conn:
        _init_operit_schema(conn)
        if action == "register":
            _enforce_register_rate_limit(username, client_ip)
            conn.execute("BEGIN IMMEDIATE")
            user, had_recent_registration = _register_ai_user_in_transaction(
                conn, username, password, client_ip, avatar
            )
        else:
            user = _verified_existing_account(
                conn, username, password, client_ip, require_ai=True
            )
            _reject_pending_operit_user(user)
            conn.execute("BEGIN IMMEDIATE")
            # Lock and recheck the same account before issuing the sidecar session.
            user = _row_dict(conn.execute(
                "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
                (int(user["id"]),),
            ).fetchone())
            if (
                not user
                or not user.get("is_ai")
                or not _verify_password(password, user["password_hash"])
            ):
                raise _McpError(-32001, "用户名或密码错误")
            _reject_pending_operit_user(user)
            had_recent_registration = False

        if human is not None:
            _ensure_ai_binding(conn, human["id"], user["id"])
        raw_token, expires_at = _issue_operit_session_in_transaction(
            conn, user, client_id
        )
        conn.execute(
            "UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?",
            (int(user["id"]),),
        )
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ?", (int(user["id"]),)
        ).fetchone())
        conn.commit()

    result = {
        "session_token": raw_token,
        "expires_at_epoch": expires_at,
        "user": _public_user(user),
        "bound": human is not None,
        "credential_family": "operit_v1",
        "message": "Operit 小机会话已创建；现有 MCP Token 未改变。",
    }
    return _append_recent_registration_notice(result, had_recent_registration)


def _current_operit_ai(raw_token, client_id, *, touch, now_epoch,
    OPERIT_SESSION_FORMAT_VERSION,
    OPERIT_SESSION_TOKEN_PREFIX,
    _McpError,
    _db_connect,
    _init_operit_schema,
    _operit_client_id_hash,
    _operit_token_hash,
    _reject_pending_operit_user,
    _row_dict,
    time,
):
    raw_token = str(raw_token or "")
    if not raw_token.startswith(OPERIT_SESSION_TOKEN_PREFIX):
        raise _McpError(-32001, "Operit 会话不存在或已失效")
    client_hash = _operit_client_id_hash(client_id)
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    with _db_connect() as conn:
        _init_operit_schema(conn)
        row = _row_dict(conn.execute(
            """
            SELECT s.user_id, s.format_version, s.expires_at_epoch, u.*
            FROM operit_ai_sessions AS s
            JOIN toy_users AS u ON u.id = s.user_id
            WHERE s.token_hash = ?
              AND s.client_id_hash = ?
              AND s.revoked_at_epoch IS NULL
              AND s.expires_at_epoch > ?
              AND u.deleted_at IS NULL
            """,
            (_operit_token_hash(raw_token), client_hash, now_epoch),
        ).fetchone())
        if (
            not row
            or int(row["format_version"]) != OPERIT_SESSION_FORMAT_VERSION
            or not row.get("is_ai")
        ):
            raise _McpError(-32001, "Operit 会话不存在、已失效或不属于当前 callerCardId")
        _reject_pending_operit_user(row)
        if touch:
            conn.execute(
                """
                UPDATE operit_ai_sessions SET last_used_at_epoch = ?
                WHERE token_hash = ?
                """,
                (now_epoch, _operit_token_hash(raw_token)),
            )
            conn.execute(
                "UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?",
                (int(row["user_id"]),),
            )
            conn.commit()
    return row


def _operit_session_status(raw_token, client_id, *,
    _current_operit_ai,
    _db_connect,
    _operit_token_hash,
    _public_user,
):
    user = _current_operit_ai(raw_token, client_id)
    with _db_connect() as conn:
        expires_at = int(conn.execute(
            "SELECT expires_at_epoch FROM operit_ai_sessions WHERE token_hash = ?",
            (_operit_token_hash(raw_token),),
        ).fetchone()[0])
    return {
        "authenticated": True,
        "credential_family": "operit_v1",
        "expires_at_epoch": expires_at,
        "user": _public_user(user),
    }


def _revoke_operit_session(raw_token, client_id, *,
    _current_operit_ai,
    _db_connect,
    _operit_token_hash,
    time,
):
    _current_operit_ai(raw_token, client_id, touch=False)
    now_epoch = int(time.time())
    with _db_connect() as conn:
        conn.execute(
            """
            UPDATE operit_ai_sessions SET revoked_at_epoch = ?
            WHERE token_hash = ? AND revoked_at_epoch IS NULL
            """,
            (now_epoch, _operit_token_hash(raw_token)),
        )
        conn.commit()
    return {"ok": True, "authenticated": False}


def _bind_operit_ai(human_token, operit_token, client_id, *, confirm,
    _current_operit_ai,
    _db_connect,
    _ensure_ai_binding,
    _operit_confirmed_human,
    _public_user,
):
    human = _operit_confirmed_human(human_token, confirm)
    ai = _current_operit_ai(operit_token, client_id)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _ensure_ai_binding(conn, human["id"], ai["id"])
        conn.commit()
    return {
        "ok": True,
        "bound": True,
        "human": _public_user(human),
        "machine": _public_user(ai),
    }


def _issue_operit_web_ticket(human_token, *, confirm, now_epoch,
    OPERIT_WEB_TICKET_BYTES,
    OPERIT_WEB_TICKET_PREFIX,
    OPERIT_WEB_TICKET_SECONDS,
    _db_connect,
    _init_operit_schema,
    _operit_confirmed_human,
    _operit_token_hash,
    secrets,
    time,
    urllib,
):
    human = _operit_confirmed_human(human_token, confirm)
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    raw_ticket = OPERIT_WEB_TICKET_PREFIX + secrets.token_urlsafe(
        OPERIT_WEB_TICKET_BYTES
    )
    expires_at = now_epoch + OPERIT_WEB_TICKET_SECONDS
    with _db_connect() as conn:
        _init_operit_schema(conn)
        conn.execute(
            "DELETE FROM operit_web_tickets WHERE expires_at_epoch <= ?",
            (now_epoch,),
        )
        conn.execute(
            """
            INSERT INTO operit_web_tickets (
                ticket_hash, human_user_id, created_at_epoch, expires_at_epoch
            ) VALUES (?, ?, ?, ?)
            """,
            (_operit_token_hash(raw_ticket), int(human["id"]), now_epoch, expires_at),
        )
        conn.commit()
    return {
        "ticket": raw_ticket,
        "ticket_path": "/duel/?web_ticket=" + urllib.parse.quote(raw_ticket, safe=""),
        "expires_in": OPERIT_WEB_TICKET_SECONDS,
        "expires_at_epoch": expires_at,
    }


def _consume_operit_web_ticket(raw_ticket, *, now_epoch,
    OPERIT_WEB_TICKET_PREFIX,
    _McpError,
    _db_connect,
    _init_operit_schema,
    _operit_token_hash,
    _reject_pending_operit_user,
    _row_dict,
    time,
):
    raw_ticket = str(raw_ticket or "")
    if not raw_ticket.startswith(OPERIT_WEB_TICKET_PREFIX):
        raise _McpError(-32001, "网页登录票据不存在或已失效")
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    ticket_hash = _operit_token_hash(raw_ticket)
    with _db_connect() as conn:
        _init_operit_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = _row_dict(conn.execute(
            """
            SELECT t.expires_at_epoch, u.*
            FROM operit_web_tickets AS t
            JOIN toy_users AS u ON u.id = t.human_user_id
            WHERE t.ticket_hash = ?
              AND t.expires_at_epoch > ?
              AND u.deleted_at IS NULL
              AND u.is_ai = 0
            """,
            (ticket_hash, now_epoch),
        ).fetchone())
        # Consumption is atomic and irreversible, including a race with a second GET.
        deleted = conn.execute(
            "DELETE FROM operit_web_tickets WHERE ticket_hash = ?",
            (ticket_hash,),
        ).rowcount
        conn.commit()
    if not row or deleted != 1:
        raise _McpError(-32001, "网页登录票据不存在或已失效")
    _reject_pending_operit_user(row)
    return row


def _init_operit_schema(conn):
    """Create credentials that are deliberately separate from MCP AI tokens."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operit_ai_sessions (
            token_hash TEXT PRIMARY KEY CHECK (length(token_hash) = 64),
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            client_id_hash TEXT NOT NULL CHECK (length(client_id_hash) = 64),
            format_version INTEGER NOT NULL DEFAULT 1 CHECK (format_version = 1),
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            last_used_at_epoch INTEGER NOT NULL,
            revoked_at_epoch INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operit_ai_sessions_user_active
        ON operit_ai_sessions(user_id, revoked_at_epoch, expires_at_epoch)
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_operit_ai_sessions_client_active
        ON operit_ai_sessions(client_id_hash)
        WHERE revoked_at_epoch IS NULL
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS operit_web_tickets (
            ticket_hash TEXT PRIMARY KEY CHECK (length(ticket_hash) = 64),
            human_user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_operit_web_tickets_expiry
        ON operit_web_tickets(expires_at_epoch)
        """
    )


def _invalidate_operit_credentials_in_transaction(conn, user_id, *, now_epoch,
    _table_exists,
    time,
):
    """Invalidate only the new credential family after a password/security reset."""
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    if _table_exists(conn, "operit_ai_sessions"):
        conn.execute(
            """
            UPDATE operit_ai_sessions
            SET revoked_at_epoch = COALESCE(revoked_at_epoch, ?)
            WHERE user_id = ?
            """,
            (now_epoch, int(user_id)),
        )
    if _table_exists(conn, "operit_web_tickets"):
        conn.execute(
            "DELETE FROM operit_web_tickets WHERE human_user_id = ?",
            (int(user_id),),
        )


def _operit_duel_call(raw_token, client_id, action, params, *,
    DUEL_REQUEST_RATE_LIMIT_MAX,
    RATE_LIMIT_ERROR_CODE,
    REQUEST_RATE_LIMIT_MESSAGE,
    _McpError,
    _OPERIT_DUEL_ACTIONS,
    _check_request_rate_limit,
    _current_operit_ai,
    _tool_play,
    json,
):
    """Run one Duel action under the AI resolved from an Operit session."""
    user = _current_operit_ai(raw_token, client_id)
    if not isinstance(action, str) or action not in _OPERIT_DUEL_ACTIONS:
        raise _McpError(-32602, "不支持的 duel action")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise _McpError(-32602, "params 必须是对象")
    rate_identity = f"operit:{int(user['id'])}:duel"
    if not _check_request_rate_limit(
        rate_identity, max_count=DUEL_REQUEST_RATE_LIMIT_MAX
    ):
        raise _McpError(RATE_LIMIT_ERROR_CODE, REQUEST_RATE_LIMIT_MESSAGE)
    # Only game/action/params cross this boundary. _tool_play then overwrites every
    # reported player_id with the canonical account id before Duel normalization.
    raw_result = _tool_play(
        {"game": "duel", "action": action, "params": dict(params)},
        authenticated_account=user,
    )
    try:
        result = json.loads(raw_result)
    except (TypeError, json.JSONDecodeError) as exc:
        raise _McpError(-32603, "duel 返回格式异常") from exc
    if not isinstance(result, dict):
        raise _McpError(-32603, "duel 返回格式异常")
    return result
