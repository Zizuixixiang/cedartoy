"""Password and account token authentication.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _hash_password(password, *,
    PWD_CONTEXT,
    _ab64_encode,
    hashlib,
    secrets,
):
    if PWD_CONTEXT:
        return PWD_CONTEXT.hash(password)
    salt_bytes = secrets.token_bytes(16)
    salt = _ab64_encode(salt_bytes)
    rounds = 29000
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, rounds)
    checksum = _ab64_encode(digest)
    return f"$pbkdf2-sha256${rounds}${salt}${checksum}"


def _verify_password(password, password_hash, *,
    PWD_CONTEXT,
    _ab64_decode,
    _ab64_encode,
    hashlib,
    hmac,
):
    if PWD_CONTEXT:
        return PWD_CONTEXT.verify(password, password_hash)
    try:
        _, scheme, rounds, salt, checksum = password_hash.split("$", 4)
        if scheme != "pbkdf2-sha256":
            return False
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), _ab64_decode(salt), int(rounds))
        expected = _ab64_encode(digest)
        return hmac.compare_digest(expected, checksum)
    except Exception:
        return False


def _ab64_encode(raw, *,
    base64,
):
    return base64.b64encode(raw).decode("ascii").rstrip("=").replace("+", ".")


def _ab64_decode(value, *,
    base64,
):
    normalized = value.replace(".", "+")
    padding = "=" * (-len(normalized) % 4)
    return base64.b64decode((normalized + padding).encode("ascii"))


def _b64url_encode(raw, *,
    base64,
):
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value, *,
    base64,
):
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))


def _jwt_unverified_payload(token, *,
    base64,
    json,
):
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a three-part JWT")
        payload_part = parts[1]
        padding = "=" * (-len(payload_part) % 4)
        payload_raw = base64.b64decode(
            (payload_part + padding).encode("ascii"),
            altchars=b"-_",
            validate=True,
        )
        payload = json.loads(payload_raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JWT payload is not an object")
        return payload
    except Exception as exc:
        raise ValueError("malformed JWT payload") from exc


def _jwt_encode(payload, *,
    JWT_ALGORITHM,
    TOY_SECRET,
    _b64url_encode,
    hashlib,
    hmac,
    json,
):
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def _legacy_ai_token_hash(token, *,
    hashlib,
):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _opaque_ai_token_hash(token, *,
    hashlib,
):
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _issue_ai_token_in_transaction(conn, user, *,
    AI_OPAQUE_TOKEN_BYTES,
    AI_OPAQUE_TOKEN_FORMAT_VERSION,
    AI_OPAQUE_TOKEN_PREFIX,
    _McpError,
    _opaque_ai_token_hash,
    secrets,
    sqlite3,
):
    """Issue one opaque AI token; only its SHA-256 hash survives this call."""
    user_id = int(user["id"])
    if not user.get("is_ai") or user.get("deleted_at") is not None:
        raise _McpError(-32602, "目标账号不是有效的小机账号")
    generation = int(user.get("ai_token_version") or 0)
    for _attempt in range(3):
        token = AI_OPAQUE_TOKEN_PREFIX + secrets.token_urlsafe(AI_OPAQUE_TOKEN_BYTES)
        try:
            conn.execute(
                """
                INSERT INTO ai_access_tokens (
                    token_hash, user_id, generation, format_version
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _opaque_ai_token_hash(token),
                    user_id,
                    generation,
                    AI_OPAQUE_TOKEN_FORMAT_VERSION,
                ),
            )
            return token
        except sqlite3.IntegrityError as exc:
            if "ai_access_tokens.token_hash" not in str(exc):
                raise
            continue
    raise RuntimeError("failed to allocate a unique AI token")


def _issue_initial_account_token_in_transaction(conn, user, *,
    _create_account_jwt,
    _issue_ai_token_in_transaction,
):
    """Issue the first token for a newly-created account."""
    if user.get("is_ai"):
        active_count = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_access_tokens
            WHERE user_id = ? AND revoked_at_epoch IS NULL
            """,
            (int(user["id"]),),
        ).fetchone()[0])
        if active_count:
            raise RuntimeError("initial AI token already exists")
        return _issue_ai_token_in_transaction(conn, user)
    return _create_account_jwt(user)


def _legacy_allowlisted_ai_payload(token, *,
    LEGACY_AI_JWT_COMPAT_ENABLED,
    _db_connect,
    _jwt_unverified_payload,
    _legacy_ai_token_hash,
    _table_exists,
    sqlite3,
):
    """Accept only an exact pre-verified legacy AI token hash, never a candidate."""
    try:
        if not LEGACY_AI_JWT_COMPAT_ENABLED:
            raise ValueError("legacy AI JWT compatibility is disabled")
        payload = _jwt_unverified_payload(token)
        if set(payload) != {"user_id", "username", "is_ai", "is_admin"}:
            raise ValueError("unexpected legacy payload")
        if payload.get("is_ai") is not True:
            raise ValueError("not a permanent AI token")
        user_id = int(payload["user_id"])
        token_hash = _legacy_ai_token_hash(token)
        with _db_connect() as conn:
            if not _table_exists(conn, "legacy_ai_token_hashes"):
                raise ValueError("legacy allowlist unavailable")
            row = conn.execute(
                """
                SELECT user_id, token_version
                FROM legacy_ai_token_hashes
                WHERE token_hash = ?
                """,
                (token_hash,),
            ).fetchone()
        if not row or int(row["user_id"]) != user_id:
            raise ValueError("legacy token is not allowlisted")
        payload["_legacy_token_version"] = int(row["token_version"])
        return payload
    except (KeyError, TypeError, ValueError, sqlite3.Error) as exc:
        raise ValueError("legacy token is not allowlisted") from exc


def _jwt_decode(token, *,
    JWT_ALGORITHM,
    LEGACY_AI_JWT_COMPAT_ENABLED,
    TOY_SECRET,
    _b64url_decode,
    _legacy_allowlisted_ai_payload,
    hashlib,
    hmac,
    json,
    time,
):
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected, actual):
            return _legacy_allowlisted_ai_payload(token)
        header = json.loads(_b64url_decode(header_part).decode("utf-8"))
        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("bad algorithm")
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        if payload.get("is_ai") is True and not LEGACY_AI_JWT_COMPAT_ENABLED:
            raise ValueError("legacy AI JWT compatibility is disabled")
        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise ValueError("登录已失效。请检查：1) MCP 地址是否为 toy.cedarstar.org/你的token（不要带花括号）；2) 人类是否完整复制了 token（不要漏字符）；3) 如果 token 确实丢失，可用 account 工具的 login 重新获取。") from exc


def _create_account_jwt(user, *,
    HUMAN_TOKEN_SECONDS,
    _jwt_encode,
    time,
):
    payload = {
        "user_id": int(user["id"]),
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
    }
    if user.get("is_ai"):
        payload["token_version"] = int(user.get("ai_token_version") or 0)
    else:
        payload["exp"] = int(time.time()) + HUMAN_TOKEN_SECONDS
    return _jwt_encode(payload)


def _create_account_token(user, *,
    _McpError,
    _create_account_jwt,
    _db_connect,
    _issue_ai_token_in_transaction,
    _replace_ai_token_in_transaction,
    _row_dict,
):
    """Create a human JWT or bootstrap/replace an internal AI credential."""
    if not user.get("is_ai"):
        return _create_account_jwt(user)
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        current_user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (int(user["id"]),),
        ).fetchone())
        if not current_user:
            raise _McpError(-32004, "小机账号不存在或已删除")
        active_count = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM ai_access_tokens
            WHERE user_id = ? AND revoked_at_epoch IS NULL
            """,
            (current_user["id"],),
        ).fetchone()[0])
        if active_count:
            current_user, token = _replace_ai_token_in_transaction(
                conn, current_user["id"]
            )
        else:
            token = _issue_ai_token_in_transaction(conn, current_user)
        conn.commit()
    return token


def _current_account(raw_token, *, allow_pending_deletion,
    AI_OPAQUE_TOKEN_FORMAT_VERSION,
    AI_OPAQUE_TOKEN_PREFIX,
    _McpError,
    _db_connect,
    _jwt_decode,
    _jwt_unverified_payload,
    _opaque_ai_token_hash,
    _row_dict,
    time,
):
    if not raw_token:
        raise _McpError(-32001, "未登录：当前是游客模式。已注册请把 MCP 地址改成 toy.cedarstar.org/你的token 再重连；未注册请先 login_or_register。")
    raw_token = str(raw_token)
    is_opaque = raw_token.startswith(AI_OPAQUE_TOKEN_PREFIX)
    payload = None
    if not is_opaque:
        try:
            _jwt_unverified_payload(raw_token)
        except ValueError:
            raise _McpError(-32001, "token 格式不完整，可能在复制时断行或漏了字符。请重新完整复制一整行 token 后重连。") from None
        try:
            payload = _jwt_decode(raw_token)
            user_id = int(payload["user_id"])
        except (KeyError, TypeError, ValueError):
            raise _McpError(-32001, "登录已失效。请检查：1) MCP 地址是否为 toy.cedarstar.org/你的token（不要带花括号）；2) 人类是否完整复制了 token（不要漏字符）；3) 如果 token 确实丢失，可用 account 工具的 login 重新获取。") from None
    with _db_connect() as conn:
        if is_opaque:
            token_row = _row_dict(conn.execute(
                """
                SELECT user_id, generation, format_version
                FROM ai_access_tokens
                WHERE token_hash = ? AND revoked_at_epoch IS NULL
                """,
                (_opaque_ai_token_hash(raw_token),),
            ).fetchone())
            if not token_row or int(token_row["format_version"]) != AI_OPAQUE_TOKEN_FORMAT_VERSION:
                raise _McpError(-32001, "登录已失效：Token 不存在或已撤销")
            user_id = int(token_row["user_id"])
        user = _row_dict(conn.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ).fetchone())
        if not user:
            raise _McpError(-32001, "账号不存在或已删除")
        if is_opaque:
            if not user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：该账号已不是小机账号")
            if int(token_row["generation"]) != int(user.get("ai_token_version") or 0):
                raise _McpError(-32001, "登录已失效：该小机 Token 已更新")
            auth_token_format = "opaque_v1"
        elif payload.get("is_ai") is True:
            if not user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：该账号已不是小机账号")
            presented_version = payload.get(
                "token_version",
                payload.get("_legacy_token_version", 0),
            )
            try:
                presented_version = int(presented_version)
            except (TypeError, ValueError):
                raise _McpError(-32001, "登录已失效") from None
            if presented_version != int(user.get("ai_token_version") or 0):
                raise _McpError(-32001, "登录已失效：该小机 Token 已更新")
            auth_token_format = "legacy_jwt"
        else:
            if user.get("is_ai"):
                raise _McpError(-32001, "登录已失效：Token 账号类型不匹配")
            auth_token_format = "jwt"
        if user.get("deletion_requested_at_epoch") is not None:
            scheduled = int(user["scheduled_delete_at_epoch"])
            if not allow_pending_deletion:
                when = time.strftime("%Y-%m-%d %H:%M", time.localtime(scheduled))
                raise _McpError(
                    -32010,
                    f"账号处于待注销状态，将于 {when} 永久删除；当前只可查询或取消注销。",
                    {"reason": "pending_deletion"},
                )
            user["_auth_token_format"] = auth_token_format
            return user
        conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user_id,))
        conn.commit()
        user = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        user["_auth_token_format"] = auth_token_format
    return user


def _path_token_user_id(path_token, *,
    AI_OPAQUE_TOKEN_FORMAT_VERSION,
    AI_OPAQUE_TOKEN_PREFIX,
    _db_connect,
    _jwt_decode,
    _opaque_ai_token_hash,
    sqlite3,
):
    if not path_token:
        return None
    if str(path_token).startswith(AI_OPAQUE_TOKEN_PREFIX):
        try:
            with _db_connect() as conn:
                row = conn.execute(
                    """
                    SELECT t.user_id
                    FROM ai_access_tokens AS t
                    JOIN toy_users AS u ON u.id = t.user_id
                    WHERE t.token_hash = ?
                      AND t.revoked_at_epoch IS NULL
                      AND t.format_version = ?
                      AND t.generation = u.ai_token_version
                      AND u.is_ai = 1
                      AND u.deleted_at IS NULL
                    """,
                    (
                        _opaque_ai_token_hash(str(path_token)),
                        AI_OPAQUE_TOKEN_FORMAT_VERSION,
                    ),
                ).fetchone()
            return int(row["user_id"]) if row else None
        except (KeyError, TypeError, ValueError, sqlite3.Error):
            return None
    try:
        payload = _jwt_decode(path_token)
        return int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        return None


def _extract_bearer(headers):
    value = headers.get("Authorization", "")
    if value.lower().startswith("bearer "):
        return value[7:].strip()
    return ""
