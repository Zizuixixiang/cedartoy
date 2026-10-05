"""Password reset and account recovery coordination.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _generate_reset_link(user_id, *,
    _McpError,
    _create_reset_token,
    _db_connect,
    _reset_url,
    _row_dict,
    account_deletion,
):
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        existing = _row_dict(conn.execute(
            "SELECT id, is_ai, deletion_requested_at_epoch FROM toy_users WHERE id = ?", (user_id,)
        ).fetchone())
        if not existing:
            raise _McpError(-32004, "账号不存在")
        if existing.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "待注销账号不能生成密码重置链接")
        # 小机被多个人类绑定时不发链接：无法判定该由谁重置。
        if existing.get("is_ai"):
            owners = conn.execute(
                "SELECT COUNT(*) FROM user_bindings WHERE ai_user_id = ?", (user_id,)
            ).fetchone()[0]
            if owners > 1:
                raise _McpError(
                    -32602,
                    f"该小机绑定了 {owners} 个人类账号，请先解绑到只剩一个再重置密码",
                )
        token, _, _ = _create_reset_token(conn, user_id, lifetime_seconds=3600)
        conn.commit()
    return {"ok": True, "reset_url": _reset_url(token), "expires_in": "1小时"}


def _reset_token_hash(token, *,
    hashlib,
):
    return "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()


def _reset_url(token):
    return f"https://toy.cedarstar.org/?reset_token={token}"


def _create_reset_token(conn, user_id, *, lifetime_seconds, token,
    _reset_token_hash,
    secrets,
    time,
):
    token = token or secrets.token_urlsafe(32)
    expires_at = int(time.time()) + lifetime_seconds
    cursor = conn.execute(
        """INSERT INTO password_reset_tokens (user_id, token, expires_at)
           VALUES (?, ?, datetime(?, 'unixepoch'))""",
        (user_id, _reset_token_hash(token), expires_at),
    )
    return token, cursor.lastrowid, expires_at


def _recovery_text(body, key, limit, *, optional,
    _McpError,
):
    value = body.get(key, "")
    if not isinstance(value, str) or len(value) > limit or (not optional and not value.strip()):
        raise _McpError(-32602, "请完整填写申请信息，并遵守字段长度限制")
    return value.strip()


def _recovery_identity(body, *,
    _McpError,
    _recovery_text,
    re,
):
    kind = body.get("account_kind", "username")
    account = _recovery_text(body, "account", 20)
    if kind not in ("username", "id") or (kind == "id" and not re.fullmatch(r"[0-9]{1,18}", account)):
        raise _McpError(-32602, "请选择账号名或数字 ID，并填写对应信息")
    return kind, str(int(account)) if kind == "id" else account


def _normalize_recovery_code(code, *,
    _McpError,
    _RECOVERY_CODE_ALPHABET,
    _RECOVERY_MISSING,
    re,
):
    if isinstance(code, str):
        code = code.strip()
        # Legacy credentials and their already-issued reset links are case-sensitive.
        if re.fullmatch(r"[A-Za-z0-9]{6,8}", code):
            return code
        if re.fullmatch(r"[A-Za-z0-9]{12}|[A-Za-z0-9]{4}(?:-[A-Za-z0-9]{4}){2}", code):
            compact = code.replace("-", "").upper()
            if all(char in _RECOVERY_CODE_ALPHABET for char in compact):
                return compact
    raise _McpError(-32602, _RECOVERY_MISSING)


def _recovery_query_hash(code, *,
    _email_hmac,
    _normalize_recovery_code,
):
    # Versioning excludes migrated rows from the legacy scan. The application
    # secret stays outside the DB; no plaintext query credential is persisted.
    return "v2:" + _email_hmac("recovery-query-v2", _normalize_recovery_code(code))


def _submit_recovery_ticket(body, client_ip, *,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _RECOVERY_CODE_ALPHABET,
    _RECOVERY_SUBMITTED,
    _db_connect,
    _recovery_identity,
    _recovery_query_hash,
    _recovery_text,
    _reset_token_hash,
    secrets,
    time,
):
    kind, account = _recovery_identity(body)
    fields = [_recovery_text(body, k, n, optional=(k == "explanation")) for k, n in (
        ("machine", 100), ("registered_about", 100), ("games", 500), ("explanation", 2000))]
    now = int(time.time())
    ip_hash = _reset_token_hash(str(client_ip))
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # Limits depend only on the submitter, never on account existence or others' tickets.
        count = conn.execute(
            "SELECT COUNT(*) FROM account_recovery_tickets WHERE ip_hash=? AND created_at_epoch>?",
            (ip_hash, now - 86400),
        ).fetchone()[0]
        if count >= 5:
            raise _McpError(RATE_LIMIT_ERROR_CODE, "此网络今日申请次数较多，请稍后再试")
        if kind == "username" and not conn.execute(
            """SELECT 1 FROM toy_users WHERE username = ? COLLATE BINARY
               AND is_ai = 0 AND deleted_at IS NULL
               AND deletion_requested_at_epoch IS NULL
               AND scheduled_delete_at_epoch IS NULL""",
            (account,),
        ).fetchone():
            raise _McpError(-32602, "账号名不存在")
        while True:
            code = "".join(secrets.choice(_RECOVERY_CODE_ALPHABET) for _ in range(12))
            query_code = "-".join(code[i:i + 4] for i in range(0, 12, 4))
            query_hash = _recovery_query_hash(code)
            if not conn.execute("SELECT 1 FROM account_recovery_tickets WHERE query_code_hash=?", (query_hash,)).fetchone():
                break
        cursor = conn.execute(
            """INSERT INTO account_recovery_tickets
               (account_kind, account, machine, registered_about, games, explanation,
                query_code_hash, ip_hash, created_at_epoch) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (kind, account, *fields, query_hash, ip_hash, now),
        )
        conn.commit()
    return {"ok": True, "message": _RECOVERY_SUBMITTED, "ticket_id": cursor.lastrowid, "query_code": query_code}


def _complete_recovery_tickets(conn, user_id, *,
    _table_exists,
    time,
):
    # Some legacy test/maintenance databases predate the ticket table.
    if not _table_exists(conn, "account_recovery_tickets"):
        return
    conn.execute(
        """UPDATE password_reset_tokens SET used=1 WHERE id IN
           (SELECT reset_token_id FROM account_recovery_tickets WHERE user_id=?)""", (user_id,))
    conn.execute(
        """UPDATE account_recovery_tickets SET status='completed', completed_at_epoch=?, reset_nonce=NULL
           WHERE user_id=? AND status='approved'""", (int(time.time()), user_id))


def _query_recovery_ticket(body, *,
    _McpError,
    _RECOVERY_MISSING,
    _complete_recovery_tickets,
    _create_reset_token,
    _db_connect,
    _email_hmac,
    _normalize_recovery_code,
    _recovery_query_hash,
    _reset_url,
    _row_dict,
    hmac,
    re,
    secrets,
    time,
):
    code = _normalize_recovery_code(body.get("query_code"))
    query_hash = _recovery_query_hash(code)
    now = int(time.time())
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ticket = _row_dict(conn.execute(
            "SELECT * FROM account_recovery_tickets WHERE query_code_hash=?",
            (query_hash,),
        ).fetchone())
        if not ticket and re.fullmatch(r"[A-Za-z0-9]{6,8}", code):
            matches = []
            for candidate in conn.execute(
                "SELECT * FROM account_recovery_tickets WHERE query_code_hash NOT LIKE 'v2:%'"
            ):
                legacy_hash = _email_hmac(
                    "recovery-query-v1", candidate["account_kind"], candidate["account"], code)
                if hmac.compare_digest(candidate["query_code_hash"], legacy_hash):
                    matches.append(dict(candidate))
            # Never choose an arbitrary account if old credentials collide.
            if len(matches) == 1:
                ticket = matches[0]
                conn.execute("UPDATE account_recovery_tickets SET query_code_hash=? WHERE id=?",
                             (query_hash, ticket["id"]))
        if not ticket:
            raise _McpError(-32602, _RECOVERY_MISSING)
        result = {"ok": True, "status": ticket["status"], "admin_note": ticket["admin_note"]}
        if ticket["status"] != "approved":
            return result
        user = conn.execute(
            "SELECT is_ai, deleted_at, deletion_requested_at_epoch FROM toy_users WHERE id=?",
            (ticket["user_id"],),
        ).fetchone()
        if not user or user[0] or user[1] is not None or user[2] is not None:
            return {"ok": True, "status": "unavailable", "message": "申请暂不可领取，请联系管理员。"}
        reset = conn.execute(
            "SELECT used, CAST(strftime('%s', expires_at) AS INTEGER) FROM password_reset_tokens WHERE id=?",
            (ticket["reset_token_id"],),
        ).fetchone()
        if reset and reset[0]:
            _complete_recovery_tickets(conn, ticket["user_id"])
            conn.commit()
            return {**result, "status": "completed"}
        # The window limits issuance; a link already issued remains valid for its full 24h.
        if reset and reset[1] > now:
            expires_at = reset[1]
        else:
            if ticket["claim_until_epoch"] <= now:
                return {**result, "status": "expired"}
            if ticket["reset_token_id"]:
                conn.execute("UPDATE password_reset_tokens SET used=1 WHERE id=?", (ticket["reset_token_id"],))
            ticket["reset_nonce"] = secrets.token_urlsafe(32)
            token = _email_hmac("recovery-reset-v1", code, ticket["reset_nonce"])
            _, token_id, expires_at = _create_reset_token(conn, ticket["user_id"], lifetime_seconds=86400, token=token)
            conn.execute(
                "UPDATE account_recovery_tickets SET reset_token_id=?, reset_nonce=? WHERE id=?",
                (token_id, ticket["reset_nonce"], ticket["id"]),
            )
        # Reconstruct only after query-code verification; neither credential is stored in plaintext.
        token = _email_hmac("recovery-reset-v1", code, ticket["reset_nonce"])
        conn.commit()
        return {**result, "reset_url": _reset_url(token), "expires_at_epoch": expires_at,
                "claim_until_epoch": ticket["claim_until_epoch"]}


def _reset_machine_password(raw_token, ai_user_id, new_password, *,
    _McpError,
    _current_account,
    _db_connect,
    _hash_password,
    _invalidate_operit_credentials_in_transaction,
    _normalize_credential_field,
    _row_dict,
):
    human = _current_account(raw_token)
    if human.get("is_ai"):
        raise _McpError(-32602, "只有人类账号可以为小机重置密码")
    if ai_user_id is None:
        raise _McpError(-32602, "ai_user_id 必填")
    try:
        ai_user_id = int(ai_user_id)
    except (TypeError, ValueError):
        raise _McpError(-32602, "ai_user_id 必须是整数") from None
    new_password = _normalize_credential_field(new_password, "new_password")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")

    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        binding = conn.execute(
            """
            SELECT 1
            FROM user_bindings
            WHERE human_user_id = ? AND ai_user_id = ?
            LIMIT 1
            """,
            (human["id"], ai_user_id),
        ).fetchone()
        if not binding:
            raise _McpError(-32602, "该小机未绑定到你的账号")

        machine = _row_dict(conn.execute(
            """
            SELECT id, is_ai
            FROM toy_users
            WHERE id = ? AND deleted_at IS NULL
            """,
            (ai_user_id,),
        ).fetchone())
        if not machine:
            raise _McpError(-32004, "小机账号不存在或已删除")
        if not machine.get("is_ai"):
            raise _McpError(-32602, "目标账号不是小机账号")

        owners = conn.execute(
            "SELECT COUNT(*) FROM user_bindings WHERE ai_user_id = ?",
            (ai_user_id,),
        ).fetchone()[0]
        if owners > 1:
            raise _McpError(
                -32602,
                f"该小机绑定了 {owners} 个人类账号，请先解绑到只剩一个再重置密码",
            )

        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), ai_user_id),
        )
        _invalidate_operit_credentials_in_transaction(conn, ai_user_id)
        conn.commit()

    return {"ok": True, "message": "已为小机重置密码"}


def _reset_password_token_info(reset_token, *,
    _McpError,
    _db_connect,
    _reset_token_hash,
):
    """Read the account named by a usable reset link without consuming it."""
    reset_token = reset_token if isinstance(reset_token, str) else ""
    with _db_connect() as conn:
        reset = conn.execute(
            """
            SELECT r.used, r.expires_at <= datetime('now') AS expired,
                   u.username, u.deletion_requested_at_epoch
            FROM password_reset_tokens AS r
            LEFT JOIN toy_users AS u ON u.id = r.user_id
            WHERE r.token = ? OR (r.token = ? AND r.token NOT LIKE 'sha256:%')
            """,
            (_reset_token_hash(reset_token), reset_token),
        ).fetchone()
    if not reset:
        raise _McpError(-32602, "无效的重置链接")
    if int(reset["used"]) == 1:
        raise _McpError(-32602, "该链接已使用")
    if bool(reset["expired"]):
        raise _McpError(-32602, "链接已过期")
    if reset["username"] is None or reset["deletion_requested_at_epoch"] is not None:
        raise _McpError(-32602, "账号不存在或处于待注销状态，不能重置密码")
    return {"username": reset["username"]}


def _reset_password_by_token(reset_token, new_password, *,
    _McpError,
    _complete_recovery_tickets,
    _db_connect,
    _hash_password,
    _invalidate_operit_credentials_in_transaction,
    _normalize_credential_field,
    _reset_token_hash,
    _row_dict,
):
    reset_token = reset_token if isinstance(reset_token, str) else ""
    new_password = _normalize_credential_field(new_password, "new_password")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        reset = _row_dict(conn.execute(
            """
            SELECT *, expires_at <= datetime('now') AS expired
            FROM password_reset_tokens
            WHERE token = ? OR (token = ? AND token NOT LIKE 'sha256:%')
            """,
            (_reset_token_hash(reset_token), reset_token),
        ).fetchone())
        if not reset:
            raise _McpError(-32602, "无效的重置链接")
        if int(reset["used"]) == 1:
            raise _McpError(-32602, "该链接已使用")
        if bool(reset["expired"]):
            raise _McpError(-32602, "链接已过期")
        user = conn.execute(
            "SELECT deletion_requested_at_epoch FROM toy_users WHERE id = ?",
            (int(reset["user_id"]),),
        ).fetchone()
        if not user or user[0] is not None:
            raise _McpError(-32602, "账号处于待注销状态，不能重置密码")
        if len(new_password) < 6:
            raise _McpError(-32602, "新密码至少 6 位")
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(reset["user_id"])),
        )
        _invalidate_operit_credentials_in_transaction(
            conn, int(reset["user_id"])
        )
        conn.execute(
            "UPDATE password_reset_tokens SET used = 1 WHERE id = ?",
            (int(reset["id"]),),
        )
        _complete_recovery_tickets(conn, int(reset["user_id"]))
        conn.commit()
    return {"ok": True, "message": "密码已重置，请用新密码登录"}


def _account_security_http_status(exc, *,
    EMAIL_PROVIDER_ERROR_CODE,
    RATE_LIMIT_ERROR_CODE,
):
    if exc.code == -32001:
        return 401
    if exc.code == -32003:
        return 403
    if exc.code == RATE_LIMIT_ERROR_CODE:
        return 429
    if exc.code == EMAIL_PROVIDER_ERROR_CODE:
        return 503
    if exc.details.get("reason") == "email_in_use":
        return 409
    return 400


def _start_password_recovery(username, client_ip, *,
    _MACHINE_PASSWORD_RECOVERY_MESSAGE,
    _NO_EMAIL_RECOVERY_MESSAGE,
    _db_connect,
    _issue_email_code,
    _row_dict,
    account_deletion,
):
    username = username.strip() if isinstance(username, str) else ""
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        user = _row_dict(conn.execute(
            """
            SELECT id, username, is_ai, deletion_requested_at_epoch
            FROM toy_users
            WHERE username = ? AND deleted_at IS NULL
            """,
            (username,),
        ).fetchone())
        email_row = None
        if user and not user.get("is_ai") and user.get("deletion_requested_at_epoch") is None:
            email_row = conn.execute(
                """
                SELECT email_normalized
                FROM account_emails
                WHERE user_id = ? AND verified = 1
                """,
                (int(user["id"]),),
            ).fetchone()
    if user and user.get("is_ai"):
        return {"state": "machine", "message": _MACHINE_PASSWORD_RECOVERY_MESSAGE}
    if user and user.get("deletion_requested_at_epoch") is not None:
        return {"state": "unavailable", "message": "账号待注销；请用用户名和密码登录后取消注销"}
    if not user or not email_row:
        return {"state": "unavailable", "message": _NO_EMAIL_RECOVERY_MESSAGE}
    return _issue_email_code(
        user["id"],
        email_row["email_normalized"],
        "reset",
        client_ip,
    )


def _reset_human_password_by_email(username, code, new_password, client_ip, *,
    _MACHINE_PASSWORD_RECOVERY_MESSAGE,
    _McpError,
    _NO_EMAIL_RECOVERY_MESSAGE,
    _complete_recovery_tickets,
    _db_connect,
    _hash_password,
    _invalidate_operit_credentials_in_transaction,
    _normalize_credential_field,
    _row_dict,
    _verify_email_code_in_transaction,
    account_deletion,
):
    username = username.strip() if isinstance(username, str) else ""
    new_password = _normalize_credential_field(new_password, "new_password")
    if len(new_password) < 6:
        raise _McpError(-32602, "新密码至少 6 位")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        user = _row_dict(conn.execute(
            """
            SELECT id, is_ai, deletion_requested_at_epoch
            FROM toy_users
            WHERE username = ? AND deleted_at IS NULL
            """,
            (username,),
        ).fetchone())
        if user and user.get("is_ai"):
            raise _McpError(-32602, _MACHINE_PASSWORD_RECOVERY_MESSAGE)
        if not user:
            raise _McpError(-32602, "验证码无效、已过期或已使用")
        if user.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32602, "账号待注销；请登录后取消注销")
        email_row = conn.execute(
            """
            SELECT email_normalized
            FROM account_emails
            WHERE user_id = ? AND verified = 1
            """,
            (int(user["id"]),),
        ).fetchone()
        if not email_row:
            raise _McpError(-32602, _NO_EMAIL_RECOVERY_MESSAGE)
        _verify_email_code_in_transaction(
            conn,
            user["id"],
            email_row["email_normalized"],
            "reset",
            code,
            client_ip,
        )
        conn.execute(
            "UPDATE toy_users SET password_hash = ? WHERE id = ?",
            (_hash_password(new_password), int(user["id"])),
        )
        _invalidate_operit_credentials_in_transaction(conn, int(user["id"]))
        _complete_recovery_tickets(conn, int(user["id"]))
        conn.commit()
    return {"ok": True, "message": "密码已重置，请用新密码登录"}


def _init_password_reset_tokens_table(conn, *,
    _init_account_recovery_table,
):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS password_reset_tokens (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            token TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            expires_at TEXT NOT NULL,
            used INTEGER NOT NULL DEFAULT 0
        )
        """
    )

    _init_account_recovery_table(conn)


def _init_account_recovery_table(conn):
    # Epoch timestamps here; password_reset_tokens retains its UTC text dates.
    conn.execute("""
        CREATE TABLE IF NOT EXISTS account_recovery_tickets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_kind TEXT NOT NULL,
            account TEXT NOT NULL,
            machine TEXT NOT NULL,
            registered_about TEXT NOT NULL,
            games TEXT NOT NULL,
            explanation TEXT NOT NULL,
            query_code_hash TEXT NOT NULL UNIQUE,
            ip_hash TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            admin_note TEXT NOT NULL DEFAULT '',
            user_id INTEGER REFERENCES toy_users(id) ON DELETE CASCADE,
            reviewed_by INTEGER,
            created_at_epoch INTEGER NOT NULL,
            reviewed_at_epoch INTEGER,
            claim_until_epoch INTEGER,
            completed_at_epoch INTEGER,
            reset_token_id INTEGER,
            reset_nonce TEXT
        )
    """)
    # Preserve unfinished-version rows if its schema was already initialized.
    columns = {row[1] for row in conn.execute("PRAGMA table_info(account_recovery_tickets)")}
    if "access_hash" in columns and "query_code_hash" not in columns:
        conn.execute("ALTER TABLE account_recovery_tickets RENAME COLUMN access_hash TO query_code_hash")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_status ON account_recovery_tickets(status, id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_recovery_ip ON account_recovery_tickets(ip_hash, created_at_epoch)")
