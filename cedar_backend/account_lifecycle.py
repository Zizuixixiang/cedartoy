"""Account deletion scheduling, cancellation and purge.

Dependencies are explicit keyword-only arguments supplied by server wrappers at
call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
"""


def _account_deletion_status(raw_token, *,
    _current_account,
    _db_connect,
    _public_user,
    account_deletion,
    time,
):
    user = _current_account(raw_token, allow_pending_deletion=True)
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        result = account_deletion.deletion_status(conn, int(user["id"]))
    if result.get("scheduled_delete_at_epoch") is not None:
        result["scheduled_delete_at"] = time.strftime(
            "%Y-%m-%d %H:%M:%S",
            time.localtime(int(result["scheduled_delete_at_epoch"])),
        )
    result["user"] = _public_user(user)
    return result


def _delete_account(raw_token, confirm, current_password, *,
    _McpError,
    _current_account,
    _db_connect,
    _normalize_credential_field,
    _public_user,
    _verify_password,
    account_deletion,
    time,
):
    """Request deletion; this never performs or carries forward old waiting time."""
    if confirm is not True:
        raise _McpError(-32602, "delete_account 必须显式传 confirm=true")
    user = _current_account(raw_token)
    if not user.get("is_ai"):
        current_password = _normalize_credential_field(
            current_password, "current_password"
        )
        if not current_password or not _verify_password(
            current_password, user["password_hash"]
        ):
            raise _McpError(
                -32001,
                "当前密码错误，未申请注销",
                {"reason": "password_mismatch"},
            )
    with _db_connect() as conn:
        account_deletion.init_schema(conn)
        conn.commit()
        result = account_deletion.request_deletion(
            conn, int(user["id"])
        )
    result["ok"] = True
    result["scheduled_delete_at"] = time.strftime(
        "%Y-%m-%d %H:%M:%S",
        time.localtime(int(result["scheduled_delete_at_epoch"])),
    )
    result["user"] = _public_user({**user, **{
        "deletion_requested_at_epoch": result["deletion_requested_at_epoch"],
        "scheduled_delete_at_epoch": result["scheduled_delete_at_epoch"],
    }})
    result["message"] = (
        "注销申请已提交。72 小时内可取消；到期后账号和个人存档永久删除，"
        "多人公共历史将匿名化保留。"
    )
    return result


def _purge_managed_save_safely(delete_func, player_id, *,
    _McpError,
):
    try:
        return delete_func(player_id)
    except _McpError as exc:
        if exc.code == -32004:
            return None
        raise


def _purge_account_deletion(user_id, *, now_epoch,
    DUEL_DB_PATH,
    GARDEN_LEGACY_DB_PATH,
    GARDEN_NOTES_DB_PATH,
    SESSIONS_DB_PATH,
    TURTLE_DB_PATH,
    VENDOR_SAVE_ROOT,
    _delete_camping_plaza_save,
    _delete_garden_cat_save,
    _delete_workkk_save,
    _purge_managed_save_safely,
    account_deletion,
    detroit_adapter,
    get_tarot_store,
):
    return account_deletion.purge_account(
        account_db=TURTLE_DB_PATH,
        sessions_db=SESSIONS_DB_PATH,
        vendor_save_root=VENDOR_SAVE_ROOT,
        duel_db=DUEL_DB_PATH,
        garden_notes_db=GARDEN_NOTES_DB_PATH,
        garden_legacy_db=GARDEN_LEGACY_DB_PATH,
        user_id=int(user_id),
        now_epoch=now_epoch,
        workkk_delete=lambda player_id: _purge_managed_save_safely(
            _delete_workkk_save, player_id
        ),
        garden_delete=lambda player_id: _purge_managed_save_safely(
            _delete_garden_cat_save, player_id
        ),
        camping_delete=lambda player_id: _purge_managed_save_safely(
            _delete_camping_plaza_save, player_id
        ),
        detroit_delete=lambda player_id: _purge_managed_save_safely(
            detroit_adapter.delete_save, player_id
        ),
        tarot_delete=lambda tarot_user_id: get_tarot_store().delete_user_data(
            tarot_user_id
        ),
    )


def _cancel_account_deletion(raw_token, *,
    _McpError,
    _current_account,
    _db_connect,
    _purge_account_deletion,
    account_deletion,
    logger,
):
    user = _current_account(raw_token, allow_pending_deletion=True)
    try:
        with _db_connect() as conn:
            account_deletion.init_schema(conn)
            conn.commit()
            result = account_deletion.cancel_deletion(conn, int(user["id"]))
    except account_deletion.DeletionError as exc:
        if exc.reason == "deletion_due":
            try:
                purge_result = _purge_account_deletion(int(user["id"]))
            except Exception:
                logger.exception("due account purge failed for user_id=%s", user["id"])
                raise _McpError(
                    -32010,
                    "72 小时已结束，不能取消；最终清理正在等待后台重试。",
                    {"reason": "deletion_due"},
                ) from None
            if purge_result.get("status") == "complete":
                raise _McpError(
                    -32010,
                    "72 小时已结束，账号已完成永久删除。",
                    {"reason": "already_deleted"},
                ) from None
        raise _McpError(-32010, str(exc), {"reason": exc.reason}) from None
    result["ok"] = True
    result["message"] = "注销已取消；本次等待时间已彻底作废。"
    return result
