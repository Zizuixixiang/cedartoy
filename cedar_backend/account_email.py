"""Email verification and binding.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _normalize_email(value, *,
    _McpError,
    re,
):
    if not isinstance(value, str):
        raise _McpError(-32602, "邮箱必填")
    email = value.strip().casefold()
    if len(email) > 254 or not re.fullmatch(
        r"[a-z0-9.!#$%&'*+/=?^_{}|~-]{1,64}@[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?",
        email,
    ):
        raise _McpError(-32602, "邮箱格式不正确")
    local, domain = email.rsplit("@", 1)
    if "." not in domain or any(
        not label or label.startswith("-") or label.endswith("-") or len(label) > 63
        for label in domain.split(".")
    ):
        raise _McpError(-32602, "邮箱格式不正确")
    return email


def _mask_email(email):
    local, domain = email.rsplit("@", 1)
    domain_parts = domain.split(".")
    domain_name = domain_parts[0]
    suffix = "." + ".".join(domain_parts[1:]) if len(domain_parts) > 1 else ""
    return f"{local[:1]}***@{domain_name[:1]}***{suffix}"


def _email_hmac(*parts,
    TOY_SECRET,
    hashlib,
    hmac,
):
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8")
    return hmac.new(TOY_SECRET.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _email_code_hash(code, salt, purpose, user_id, email, *,
    _email_hmac,
):
    return _email_hmac("email-code-v1", salt, purpose, int(user_id), email, code)


def _email_ip_hash(client_ip, *,
    _email_hmac,
):
    return _email_hmac("email-ip-v1", client_ip or "unknown")


def _email_value_hash(email, *,
    _email_hmac,
):
    return _email_hmac("email-value-v1", email)


def _smtp_config(*,
    EMAIL_PROVIDER_ERROR_CODE,
    _McpError,
    os,
):
    host = os.getenv("CEDARTOY_SMTP_HOST", "").strip()
    sender = os.getenv("CEDARTOY_SMTP_FROM", "").strip()
    if not host or not sender:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务尚未配置，请联系管理员（缺少 CEDARTOY_SMTP_HOST / CEDARTOY_SMTP_FROM）",
            {"reason": "email_provider_not_configured"},
        )
    security = os.getenv("CEDARTOY_SMTP_SECURITY", "starttls").strip().lower()
    if security not in {"starttls", "ssl", "none"}:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（CEDARTOY_SMTP_SECURITY 无效）",
            {"reason": "email_provider_misconfigured"},
        )
    default_port = 465 if security == "ssl" else 587
    try:
        port = int(os.getenv("CEDARTOY_SMTP_PORT", str(default_port)))
    except ValueError:
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（CEDARTOY_SMTP_PORT 无效）",
            {"reason": "email_provider_misconfigured"},
        ) from None
    username = os.getenv("CEDARTOY_SMTP_USERNAME", "")
    password = os.getenv("CEDARTOY_SMTP_PASSWORD", "")
    if bool(username) != bool(password):
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件服务配置错误，请联系管理员（SMTP 用户名和密码必须同时配置）",
            {"reason": "email_provider_misconfigured"},
        )
    return {
        "host": host,
        "port": port,
        "sender": sender,
        "username": username,
        "password": password,
        "security": security,
    }


def _send_verification_email(email, code, purpose, smtp_config, *,
    EMAIL_PROVIDER_ERROR_CODE,
    EmailMessage,
    _McpError,
    logger,
    smtplib,
    ssl,
):
    purpose_labels = {
        "bind": "绑定邮箱",
        "change": "更换邮箱",
        "reset": "重置密码",
    }
    label = purpose_labels[purpose]
    message = EmailMessage()
    message["Subject"] = f"CedarToy 验证码：{label}"
    message["From"] = smtp_config["sender"]
    message["To"] = email
    message.set_content(
        f"你的 CedarToy 验证码是：{code}\n\n"
        f"验证码用于{label}，10 分钟内有效。\n"
        "如果不是你本人操作，请忽略这封邮件。"
    )
    try:
        if smtp_config["security"] == "ssl":
            client = smtplib.SMTP_SSL(
                smtp_config["host"],
                smtp_config["port"],
                timeout=10,
                context=ssl.create_default_context(),
            )
        else:
            client = smtplib.SMTP(
                smtp_config["host"],
                smtp_config["port"],
                timeout=10,
            )
        with client:
            if smtp_config["security"] == "starttls":
                client.starttls(context=ssl.create_default_context())
            if smtp_config["username"]:
                client.login(smtp_config["username"], smtp_config["password"])
            client.send_message(message)
    except (OSError, smtplib.SMTPException) as exc:
        logger.warning("email delivery failed: %s", type(exc).__name__)
        raise _McpError(
            EMAIL_PROVIDER_ERROR_CODE,
            "邮件暂时无法发送，请稍后重试或联系管理员",
            {"reason": "email_delivery_unavailable"},
        ) from exc


def _email_send_rate_limit(conn, user_id, email, ip_hash, now, *,
    EMAIL_SEND_COOLDOWN_SECONDS,
    EMAIL_SEND_MAX_PER_ACCOUNT,
    EMAIL_SEND_MAX_PER_EMAIL,
    EMAIL_SEND_MAX_PER_IP,
    EMAIL_SEND_WINDOW_SECONDS,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
):
    dimensions = (
        ("user_id = ?", int(user_id), EMAIL_SEND_MAX_PER_ACCOUNT),
        ("email_normalized = ? COLLATE NOCASE", email, EMAIL_SEND_MAX_PER_EMAIL),
        ("request_ip_hash = ?", ip_hash, EMAIL_SEND_MAX_PER_IP),
    )
    latest = None
    for where_sql, value, _limit in dimensions:
        row = conn.execute(
            f"SELECT MAX(created_at_epoch) FROM email_verification_codes WHERE {where_sql}",
            (value,),
        ).fetchone()
        if row and row[0] is not None:
            latest = max(latest or 0, int(row[0]))
    if latest is not None and now - latest < EMAIL_SEND_COOLDOWN_SECONDS:
        raise _McpError(
            RATE_LIMIT_ERROR_CODE,
            "验证码发送太频繁，请稍后再试",
            {"retry_after": EMAIL_SEND_COOLDOWN_SECONDS - (now - latest)},
        )
    window_start = now - EMAIL_SEND_WINDOW_SECONDS
    for where_sql, value, limit in dimensions:
        count = int(conn.execute(
            f"""
            SELECT COUNT(*)
            FROM email_verification_codes
            WHERE {where_sql} AND created_at_epoch > ?
            """,
            (value, window_start),
        ).fetchone()[0])
        if count >= limit:
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                "验证码发送次数过多，请稍后再试",
            )


def _issue_email_code(user_id, email, purpose, client_ip, *,
    EMAIL_CODE_TTL_SECONDS,
    _db_connect,
    _email_code_hash,
    _email_ip_hash,
    _email_send_rate_limit,
    _mask_email,
    _send_verification_email,
    _smtp_config,
    secrets,
    time,
):
    smtp_config = _smtp_config()
    now = int(time.time())
    ip_hash = _email_ip_hash(client_ip)
    code = f"{secrets.randbelow(1_000_000):06d}"
    salt = secrets.token_hex(16)
    code_hash = _email_code_hash(code, salt, purpose, user_id, email)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        _email_send_rate_limit(conn, user_id, email, ip_hash, now)
        cursor = conn.execute(
            """
            INSERT INTO email_verification_codes (
                user_id, email_normalized, purpose, code_salt, code_hash,
                request_ip_hash, created_at_epoch, expires_at_epoch
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                int(user_id),
                email,
                purpose,
                salt,
                code_hash,
                ip_hash,
                now,
                now + EMAIL_CODE_TTL_SECONDS,
            ),
        )
        code_id = int(cursor.lastrowid)
        conn.commit()
    try:
        _send_verification_email(email, code, purpose, smtp_config)
    except Exception:
        with _db_connect() as conn:
            conn.execute(
                "UPDATE email_verification_codes SET used_at_epoch = ? WHERE id = ?",
                (int(time.time()), code_id),
            )
            conn.commit()
        raise
    delivered_at = int(time.time())
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE email_verification_codes
            SET used_at_epoch = ?
            WHERE user_id = ? AND purpose = ? AND id != ?
              AND delivered_at_epoch IS NOT NULL AND used_at_epoch IS NULL
            """,
            (delivered_at, int(user_id), purpose, code_id),
        )
        conn.execute(
            "UPDATE email_verification_codes SET delivered_at_epoch = ? WHERE id = ?",
            (delivered_at, code_id),
        )
        conn.commit()
    return {
        "ok": True,
        "state": "code_sent",
        "purpose": purpose,
        "masked_email": _mask_email(email),
        "expires_in": EMAIL_CODE_TTL_SECONDS,
        "message": "验证码已发送，10 分钟内有效",
    }


def _check_email_verify_rate_limit(conn, user_id, ip_hash, now, *,
    EMAIL_VERIFY_MAX_PER_ACCOUNT,
    EMAIL_VERIFY_MAX_PER_IP,
    EMAIL_VERIFY_WINDOW_SECONDS,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
):
    window_start = now - EMAIL_VERIFY_WINDOW_SECONDS
    account_failures = int(conn.execute(
        """
        SELECT COUNT(*) FROM email_verification_attempts
        WHERE user_id = ? AND succeeded = 0 AND attempted_at_epoch > ?
        """,
        (int(user_id), window_start),
    ).fetchone()[0])
    ip_failures = int(conn.execute(
        """
        SELECT COUNT(*) FROM email_verification_attempts
        WHERE request_ip_hash = ? AND succeeded = 0 AND attempted_at_epoch > ?
        """,
        (ip_hash, window_start),
    ).fetchone()[0])
    if account_failures >= EMAIL_VERIFY_MAX_PER_ACCOUNT or ip_failures >= EMAIL_VERIFY_MAX_PER_IP:
        raise _McpError(
            RATE_LIMIT_ERROR_CODE,
            "验证码错误次数过多，请稍后再试",
        )


def _verify_email_code_in_transaction(conn, user_id, email, purpose, code, client_ip, *,
    EMAIL_CODE_MAX_ATTEMPTS,
    RATE_LIMIT_ERROR_CODE,
    _McpError,
    _check_email_verify_rate_limit,
    _email_code_hash,
    _email_ip_hash,
    _email_value_hash,
    _row_dict,
    hmac,
    time,
):
    now = int(time.time())
    ip_hash = _email_ip_hash(client_ip)
    _check_email_verify_rate_limit(conn, user_id, ip_hash, now)
    row = _row_dict(conn.execute(
        """
        SELECT *
        FROM email_verification_codes
        WHERE user_id = ? AND email_normalized = ? COLLATE NOCASE AND purpose = ?
          AND delivered_at_epoch IS NOT NULL AND used_at_epoch IS NULL
          AND expires_at_epoch >= ?
        ORDER BY delivered_at_epoch DESC, id DESC
        LIMIT 1
        """,
        (int(user_id), email, purpose, now),
    ).fetchone())
    if not row:
        raise _McpError(-32602, "验证码无效、已过期或已使用")
    supplied_code = code.strip() if isinstance(code, str) else ""
    expected_hash = _email_code_hash(
        supplied_code,
        row["code_salt"],
        purpose,
        user_id,
        email,
    )
    succeeded = hmac.compare_digest(expected_hash, row["code_hash"])
    conn.execute(
        """
        INSERT INTO email_verification_attempts (
            code_id, user_id, email_hash, request_ip_hash, succeeded, attempted_at_epoch
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            int(row["id"]),
            int(user_id),
            _email_value_hash(email),
            ip_hash,
            1 if succeeded else 0,
            now,
        ),
    )
    if not succeeded:
        failed_attempts = int(row["failed_attempts"]) + 1
        conn.execute(
            """
            UPDATE email_verification_codes
            SET failed_attempts = ?,
                used_at_epoch = CASE WHEN ? >= ? THEN ? ELSE used_at_epoch END
            WHERE id = ?
            """,
            (
                failed_attempts,
                failed_attempts,
                EMAIL_CODE_MAX_ATTEMPTS,
                now,
                int(row["id"]),
            ),
        )
        conn.commit()
        if failed_attempts >= EMAIL_CODE_MAX_ATTEMPTS:
            raise _McpError(
                RATE_LIMIT_ERROR_CODE,
                "验证码错误次数过多，请重新获取",
            )
        raise _McpError(-32602, "验证码错误")
    updated = conn.execute(
        """
        UPDATE email_verification_codes
        SET used_at_epoch = ?
        WHERE id = ? AND used_at_epoch IS NULL
        """,
        (now, int(row["id"])),
    ).rowcount
    if updated != 1:
        raise _McpError(-32602, "验证码已使用")
    return row


def _account_email_status(raw_token, *,
    _db_connect,
    _mask_email,
    _require_human_account,
):
    user = _require_human_account(raw_token)
    with _db_connect() as conn:
        row = conn.execute(
            "SELECT email_normalized, verified FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
    return {
        "bound": bool(row and row["verified"]),
        "verified": bool(row and row["verified"]),
        "masked_email": _mask_email(row["email_normalized"]) if row else None,
    }


def _send_account_email_code(raw_token, email, client_ip, *,
    _McpError,
    _db_connect,
    _issue_email_code,
    _normalize_email,
    _require_human_account,
):
    user = _require_human_account(raw_token)
    email = _normalize_email(email)
    with _db_connect() as conn:
        current = conn.execute(
            "SELECT email_normalized FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
        conflict = conn.execute(
            "SELECT user_id FROM account_emails WHERE email_normalized = ? COLLATE NOCASE AND user_id != ?",
            (email, int(user["id"])),
        ).fetchone()
    if conflict:
        raise _McpError(-32602, "该邮箱已绑定其他账号", {"reason": "email_in_use"})
    if current and current["email_normalized"].casefold() == email:
        raise _McpError(-32602, "该邮箱已经绑定当前账号")
    purpose = "change" if current else "bind"
    return _issue_email_code(user["id"], email, purpose, client_ip)


def _confirm_account_email(raw_token, email, code, client_ip, *,
    _McpError,
    _db_connect,
    _mask_email,
    _normalize_email,
    _require_human_account,
    _verify_email_code_in_transaction,
    sqlite3,
):
    user = _require_human_account(raw_token)
    email = _normalize_email(email)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute(
            "SELECT email_normalized FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).fetchone()
        purpose = "change" if current else "bind"
        _verify_email_code_in_transaction(
            conn,
            user["id"],
            email,
            purpose,
            code,
            client_ip,
        )
        try:
            conn.execute(
                """
                INSERT INTO account_emails (
                    user_id, email_normalized, verified, verified_at_epoch,
                    created_at_epoch, updated_at_epoch
                ) VALUES (
                    ?, ?, 1,
                    CAST(strftime('%s', 'now') AS INTEGER),
                    CAST(strftime('%s', 'now') AS INTEGER),
                    CAST(strftime('%s', 'now') AS INTEGER)
                )
                ON CONFLICT(user_id) DO UPDATE SET
                    email_normalized = excluded.email_normalized,
                    verified = 1,
                    verified_at_epoch = excluded.verified_at_epoch,
                    updated_at_epoch = excluded.updated_at_epoch
                """,
                (int(user["id"]), email),
            )
        except sqlite3.IntegrityError as exc:
            raise _McpError(
                -32602,
                "该邮箱已绑定其他账号",
                {"reason": "email_in_use"},
            ) from exc
        conn.commit()
    return {
        "ok": True,
        "bound": True,
        "verified": True,
        "masked_email": _mask_email(email),
        "message": "邮箱已绑定" if purpose == "bind" else "邮箱已更换",
    }


def _unbind_account_email(raw_token, password, *,
    _McpError,
    _db_connect,
    _normalize_credential_field,
    _require_human_account,
    _verify_password,
):
    user = _require_human_account(raw_token)
    password = _normalize_credential_field(password, "password")
    if not _verify_password(password, user["password_hash"]):
        raise _McpError(-32602, "当前密码错误")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        deleted = conn.execute(
            "DELETE FROM account_emails WHERE user_id = ?",
            (int(user["id"]),),
        ).rowcount
        conn.execute(
            """
            UPDATE email_verification_codes
            SET used_at_epoch = COALESCE(
                used_at_epoch,
                CAST(strftime('%s', 'now') AS INTEGER)
            )
            WHERE user_id = ? AND used_at_epoch IS NULL
            """,
            (int(user["id"]),),
        )
        conn.commit()
    if not deleted:
        raise _McpError(-32602, "当前账号未绑定邮箱")
    return {"ok": True, "bound": False, "message": "邮箱已解绑"}


def _init_account_email_schema(conn):
    """Create the optional, human-only email recovery schema idempotently."""
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_emails (
            user_id INTEGER PRIMARY KEY REFERENCES toy_users(id) ON DELETE CASCADE,
            email_normalized TEXT NOT NULL COLLATE NOCASE,
            verified INTEGER NOT NULL DEFAULT 1 CHECK (verified = 1),
            verified_at_epoch INTEGER NOT NULL,
            created_at_epoch INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER)),
            updated_at_epoch INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_account_emails_unique_normalized
        ON account_emails(email_normalized COLLATE NOCASE)
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_human_only_insert
        BEFORE INSERT ON account_emails
        WHEN COALESCE((SELECT is_ai FROM toy_users WHERE id = NEW.user_id), 1) != 0
        BEGIN
            SELECT RAISE(ABORT, 'email is only available to human accounts');
        END
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_human_only_update
        BEFORE UPDATE OF user_id ON account_emails
        WHEN COALESCE((SELECT is_ai FROM toy_users WHERE id = NEW.user_id), 1) != 0
        BEGIN
            SELECT RAISE(ABORT, 'email is only available to human accounts');
        END
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_codes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            email_normalized TEXT NOT NULL COLLATE NOCASE,
            purpose TEXT NOT NULL CHECK (purpose IN ('bind', 'change', 'reset')),
            code_salt TEXT NOT NULL,
            code_hash TEXT NOT NULL CHECK (length(code_hash) = 64),
            request_ip_hash TEXT NOT NULL CHECK (length(request_ip_hash) = 64),
            created_at_epoch INTEGER NOT NULL,
            expires_at_epoch INTEGER NOT NULL,
            delivered_at_epoch INTEGER,
            used_at_epoch INTEGER,
            failed_attempts INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute(
        """
        CREATE TRIGGER IF NOT EXISTS account_emails_remove_if_user_becomes_ai
        AFTER UPDATE OF is_ai ON toy_users
        WHEN NEW.is_ai != 0
        BEGIN
            DELETE FROM account_emails WHERE user_id = NEW.id;
            UPDATE email_verification_codes
            SET used_at_epoch = COALESCE(
                used_at_epoch,
                CAST(strftime('%s', 'now') AS INTEGER)
            )
            WHERE user_id = NEW.id;
        END
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_account_purpose_created
        ON email_verification_codes(user_id, purpose, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_email_created
        ON email_verification_codes(email_normalized, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_codes_ip_created
        ON email_verification_codes(request_ip_hash, created_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS email_verification_attempts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            code_id INTEGER REFERENCES email_verification_codes(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES toy_users(id) ON DELETE CASCADE,
            email_hash TEXT NOT NULL CHECK (length(email_hash) = 64),
            request_ip_hash TEXT NOT NULL CHECK (length(request_ip_hash) = 64),
            succeeded INTEGER NOT NULL DEFAULT 0,
            attempted_at_epoch INTEGER NOT NULL
        )
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_attempts_account_time
        ON email_verification_attempts(user_id, attempted_at_epoch DESC)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_email_attempts_ip_time
        ON email_verification_attempts(request_ip_hash, attempted_at_epoch DESC)
        """
    )
