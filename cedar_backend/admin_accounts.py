"""Administrator account operations.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _require_admin_account(raw_token, *,
    _McpError,
    _current_account,
):
    user = _current_account(raw_token)
    if not user.get("is_admin"):
        raise _McpError(-32003, "需要管理员权限")
    return user


def _admin_user_page(page, page_size, search, *,
    ADMIN_USERS_MAX_PAGE_SIZE,
    ADMIN_USERS_MAX_SEARCH_LENGTH,
    _db_connect,
    account_deletion,
):
    try:
        page = int(page)
        page_size = int(page_size)
    except (TypeError, ValueError):
        raise ValueError("page 和 page_size 必须是整数") from None
    if page < 1:
        raise ValueError("page 必须大于等于 1")
    if page_size < 1 or page_size > ADMIN_USERS_MAX_PAGE_SIZE:
        raise ValueError(f"page_size 必须在 1-{ADMIN_USERS_MAX_PAGE_SIZE} 之间")
    search = str(search or "").strip()
    if len(search) > ADMIN_USERS_MAX_SEARCH_LENGTH:
        raise ValueError(f"搜索内容最多 {ADMIN_USERS_MAX_SEARCH_LENGTH} 个字符")

    where_sql = ""
    params = []
    if search:
        escaped = search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where_sql = "WHERE u.username LIKE ? ESCAPE '\\' COLLATE NOCASE"
        params.append(f"%{escaped}%")
        try:
            search_id = int(search)
        except ValueError:
            search_id = None
        if search_id is not None:
            where_sql = "WHERE (u.id = ? OR u.username LIKE ? ESCAPE '\\' COLLATE NOCASE)"
            params = [search_id, f"%{escaped}%"]

    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        total = int(conn.execute(
            f"SELECT COUNT(*) FROM toy_users u {where_sql}",
            params,
        ).fetchone()[0])
        rows = conn.execute(
            f"""
            WITH page_users AS MATERIALIZED (
                SELECT
                    u.id, u.username, u.is_ai, u.is_admin, u.created_at,
                    u.last_active_at, u.deleted_at,
                    u.deletion_requested_at_epoch, u.scheduled_delete_at_epoch
                FROM toy_users u
                {where_sql}
                ORDER BY u.deleted_at IS NOT NULL ASC, u.id DESC
                LIMIT ? OFFSET ?
            ),
            player_counts AS (
                SELECT p.user_id, COUNT(*) AS count
                FROM players p
                JOIN page_users pu ON pu.id = p.user_id
                GROUP BY p.user_id
            ),
            bound_ai_counts AS (
                SELECT b.human_user_id AS user_id, COUNT(*) AS count
                FROM user_bindings b
                JOIN page_users pu ON pu.id = b.human_user_id
                GROUP BY b.human_user_id
            ),
            bound_human_counts AS (
                SELECT b.ai_user_id AS user_id, COUNT(*) AS count
                FROM user_bindings b
                JOIN page_users pu ON pu.id = b.ai_user_id
                GROUP BY b.ai_user_id
            ),
            active_token_counts AS (
                SELECT t.ai_user_id AS user_id, COUNT(*) AS count
                FROM binding_tokens t
                JOIN page_users pu ON pu.id = t.ai_user_id
                WHERE t.used = 0
                  AND t.expires_at > datetime('now', 'localtime')
                GROUP BY t.ai_user_id
            )
            SELECT
                u.id,
                u.username,
                u.is_ai,
                u.is_admin,
                u.created_at,
                u.last_active_at,
                u.deleted_at,
                u.deletion_requested_at_epoch,
                u.scheduled_delete_at_epoch,
                COALESCE(pc.count, 0) AS soup_player_count,
                COALESCE(bac.count, 0) AS bound_ai_count,
                COALESCE(bhc.count, 0) AS bound_human_count,
                COALESCE(atc.count, 0) AS active_binding_tokens
            FROM page_users u
            LEFT JOIN player_counts pc ON pc.user_id = u.id
            LEFT JOIN bound_ai_counts bac ON bac.user_id = u.id
            LEFT JOIN bound_human_counts bhc ON bhc.user_id = u.id
            LEFT JOIN active_token_counts atc ON atc.user_id = u.id
            ORDER BY u.deleted_at IS NOT NULL ASC, u.id DESC
            """,
            (*params, page_size, (page - 1) * page_size),
        ).fetchall()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "users": [dict(row) for row in rows],
    }


def _admin_update_user(user_id, body, admin_user, *,
    _McpError,
    _db_connect,
    _rename_user_in_transaction,
    _row_dict,
    _validate_username,
    account_deletion,
):
    username = (body.get("username") or "").strip()
    _validate_username(username)
    is_ai = 1 if body.get("is_ai") else 0
    is_admin = 1 if body.get("is_admin") else 0
    deleted = 1 if body.get("deleted") else 0
    if int(user_id) == int(admin_user["id"]) and (not is_admin or deleted):
        raise _McpError(-32602, "不能取消当前登录管理员的权限或软删当前账号")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        existing = _row_dict(conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone())
        if not existing:
            raise _McpError(-32004, "账号不存在")
        if existing.get("deletion_requested_at_epoch") is not None:
            raise _McpError(-32010, "待注销账号不能通过普通编辑修改或取消状态")
        if username != existing["username"]:
            _rename_user_in_transaction(conn, existing, username)
        conn.execute(
            """
            UPDATE toy_users
            SET is_ai = ?,
                is_admin = ?,
                deleted_at = CASE WHEN ? THEN COALESCE(deleted_at, datetime('now', 'localtime')) ELSE NULL END
            WHERE id = ?
            """,
            (is_ai, is_admin, deleted, user_id),
        )
        conn.execute(
            "UPDATE players SET is_ai = ?, is_admin = ? WHERE user_id = ?",
            (is_ai, is_admin, user_id),
        )
        # 停用账号时连带清绑定，避免留下指向已停用账号的僵尸绑定。
        if deleted:
            conn.execute(
                "DELETE FROM user_bindings WHERE human_user_id = ? OR ai_user_id = ?",
                (user_id, user_id),
            )
        conn.commit()
    return {"ok": True}


def _admin_reset_user_password(user_id, body, *,
    _McpError,
    _complete_recovery_tickets,
    _db_connect,
    _hash_password,
    _invalidate_operit_credentials_in_transaction,
    _normalize_credential_field,
    account_deletion,
):
    password = _normalize_credential_field(body.get("password"), "password")
    if len(password) < 6:
        raise _McpError(-32602, "密码至少 6 位")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        existing = conn.execute("SELECT id FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        pending = conn.execute(
            "SELECT deletion_requested_at_epoch FROM toy_users WHERE id = ?", (user_id,)
        ).fetchone()
        if pending[0] is not None:
            raise _McpError(-32010, "待注销账号不能重置密码；请先由账号本人取消注销")
        conn.execute(
            "UPDATE toy_users SET password_hash = ?, deleted_at = NULL WHERE id = ?",
            (_hash_password(password), user_id),
        )
        _invalidate_operit_credentials_in_transaction(conn, user_id)
        _complete_recovery_tickets(conn, user_id)
        conn.commit()
    return {"ok": True}


def _admin_recovery_tickets(view, page, *,
    _db_connect,
):
    page = max(1, min(int(page), 1000000))
    clause = "status='pending'" if view == "pending" else "status!='pending'"
    with _db_connect() as conn:
        pending = conn.execute("SELECT COUNT(*) FROM account_recovery_tickets WHERE status='pending'").fetchone()[0]
        total = conn.execute(f"SELECT COUNT(*) FROM account_recovery_tickets WHERE {clause}").fetchone()[0]
        rows = conn.execute(
            f"""SELECT id, account_kind, account, machine, registered_about, games, explanation,
                       status, admin_note, user_id, created_at_epoch, reviewed_at_epoch, claim_until_epoch
                FROM account_recovery_tickets WHERE {clause} ORDER BY id DESC LIMIT 20 OFFSET ?""",
            ((page - 1) * 20,),
        ).fetchall()
    return {"tickets": [dict(row) for row in rows], "pending_count": pending, "total": total, "page": page}


def _review_recovery_ticket(ticket_id, body, admin, *,
    _McpError,
    _db_connect,
    _recovery_text,
    time,
):
    decision = body.get("decision")
    if decision not in ("approved", "rejected"):
        raise _McpError(-32602, "请选择通过或不通过")
    note = _recovery_text(body, "admin_note", 2000, optional=decision == "approved")
    with _db_connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        ticket = conn.execute("SELECT * FROM account_recovery_tickets WHERE id=?", (ticket_id,)).fetchone()
        if not ticket or ticket["status"] != "pending":
            raise _McpError(-32602, "工单不存在或已处理，请刷新")
        user_id = None
        if decision == "approved":
            column = "id" if ticket["account_kind"] == "id" else "username"
            user = conn.execute(
                f"SELECT * FROM toy_users WHERE {column}=?", (ticket["account"],)
            ).fetchone()
            if not user or user["is_ai"] or user["deleted_at"] is not None or user["deletion_requested_at_epoch"] is not None:
                raise _McpError(-32602, "目标不是可找回的人类账号，请核验后拒绝此申请")
            user_id = user["id"]
        now = int(time.time())
        conn.execute(
            """UPDATE account_recovery_tickets SET status=?, admin_note=?, user_id=?, reviewed_by=?,
               reviewed_at_epoch=?, claim_until_epoch=? WHERE id=?""",
            (decision, note, user_id, admin["id"], now, now + 7 * 86400 if user_id else None, ticket_id),
        )
        conn.commit()
    return {"ok": True}


def _admin_release_user(user_id, admin_user, *,
    _McpError,
    _db_connect,
    _purge_account_deletion,
    account_deletion,
    time,
):
    if int(user_id) == int(admin_user["id"]):
        raise _McpError(-32602, "不能释放当前登录的管理员账号")
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        existing = conn.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)).fetchone()
        if not existing:
            raise _McpError(-32004, "账号不存在")
        now_epoch = int(time.time())
        if existing["deletion_requested_at_epoch"] is None:
            account_deletion.request_deletion(
                conn, int(user_id), now_epoch=now_epoch, delay_seconds=0
            )
        else:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE toy_users SET scheduled_delete_at_epoch=? WHERE id=?",
                (now_epoch, int(user_id)),
            )
            conn.execute(
                """
                UPDATE account_deletion_jobs SET scheduled_at_epoch=?
                WHERE job_id=? AND status IN ('pending', 'running')
                """,
                (now_epoch, existing["deletion_job_id"]),
            )
            conn.commit()
    result = _purge_account_deletion(int(user_id), now_epoch=now_epoch)
    if result.get("status") != "complete":
        raise _McpError(-32603, "立即释放未完成，后台清理将继续重试")
    return {"ok": True, "message": "账号及私人数据已清理，共享历史已匿名化"}
