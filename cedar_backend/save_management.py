"""Save discovery, migration, guest claims and deletion.

Dependencies are explicit arguments supplied by server wrappers at call time. Nested calls therefore retain the existing server patch points.
Database paths, mutable state and configuration remain owned by the caller.
Payload-forwarding helpers take dependencies positionally so arbitrary payload
keys cannot collide with injected dependencies. The claim lock stays on the
server compatibility wrapper and covers the entire migration and rollback.
"""


def _init_guest_claim_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS guest_claim_codes (
            code TEXT PRIMARY KEY,
            guest_player_id TEXT NOT NULL UNIQUE,
            created_at TEXT,
            claimed_by INTEGER,
            claimed_at TEXT,
            claimed_slot INTEGER
        )
        """
    )
    columns = {row[1] for row in conn.execute("PRAGMA table_info(guest_claim_codes)")}
    if "claimed_slot" not in columns:
        conn.execute("ALTER TABLE guest_claim_codes ADD COLUMN claimed_slot INTEGER")


def _ensure_guest_claim_code(guest_player_id, *,
    _db_connect,
    _init_guest_claim_table,
    secrets,
    sqlite3,
):
    """游客首次开档时生成一次性认领码；已有码（或已认领过）返回 None，不重复提示。"""
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = conn.execute(
            "SELECT code FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone()
        if row:
            return None
        code = secrets.token_urlsafe(9)
        try:
            conn.execute(
                "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
                (code, guest_player_id),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            return None
    return code


def _normalize_guest_player_id(value, *,
    GUEST_PREFIX,
    PLAIN_PLAYER_ID_RE,
    _McpError,
):
    if not isinstance(value, str) or not value.strip():
        raise _McpError(-32602, "player_id 必填")
    raw = value.strip()
    if raw.startswith(GUEST_PREFIX):
        suffix = raw[len(GUEST_PREFIX):]
        if PLAIN_PLAYER_ID_RE.fullmatch(suffix):
            return raw
        raise _McpError(-32602, "guest player_id 格式不合法")
    if PLAIN_PLAYER_ID_RE.fullmatch(raw):
        return GUEST_PREFIX + raw
    raise _McpError(-32602, "player_id 只能包含 1-64 位字母数字；也可传 guest: 前缀")


def _claimed_guest_record(guest_player_id, *,
    _db_connect,
    _init_guest_claim_table,
    _row_dict,
):
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        return _row_dict(conn.execute(
            """
            SELECT guest_player_id, claimed_by, claimed_at, claimed_slot
            FROM guest_claim_codes
            WHERE guest_player_id = ? AND claimed_by IS NOT NULL
            """,
            (guest_player_id,),
        ).fetchone())


def _reject_claimed_guest(guest_player_id, *,
    MIN_SAVE_SLOT,
    _McpError,
    _claimed_guest_record,
):
    row = _claimed_guest_record(guest_player_id)
    if not row:
        return
    slot = row.get("claimed_slot") or MIN_SAVE_SLOT
    raise _McpError(
        -32003,
        f"该游客身份已认领，请改用带 token 的 MCP 地址并选择对应 slot（槽 {slot}）。",
    )


def _guest_claim_code_for_player_id(player_id, *,
    MIN_SAVE_SLOT,
    _McpError,
    _collect_player_saves,
    _db_connect,
    _init_guest_claim_table,
    _normalize_guest_player_id,
    _row_dict,
    secrets,
):
    guest_player_id = _normalize_guest_player_id(player_id)
    found, _conflicts = _collect_player_saves(guest_player_id, "__claim_probe__")
    if not found:
        raise _McpError(-32004, f"没有找到 {guest_player_id} 名下的游客存档")

    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE guest_player_id = ?",
            (guest_player_id,),
        ).fetchone())
        if row:
            claimed_slot = row.get("claimed_slot") or MIN_SAVE_SLOT
            return {
                "guest_player_id": guest_player_id,
                "claim_code": row["code"],
                "claimed_by": row.get("claimed_by"),
                "claimed_at": row.get("claimed_at"),
                "claimed_slot": claimed_slot if row.get("claimed_by") is not None else None,
                "saves": found,
                "message": (
                    f"该游客身份已认领到槽 {claimed_slot}，请改用带 token 的 MCP 地址并选择对应 slot。"
                    if row.get("claimed_by") is not None
                    else "该游客存档已有认领码；可先用 my_saves 选择空槽，再用 account(action=\"claim\", claim_code=\"...\", slot=2) 转入账号。"
                ),
            }
        code = secrets.token_urlsafe(9)
        conn.execute(
            "INSERT INTO guest_claim_codes (code, guest_player_id, created_at) VALUES (?, ?, datetime('now', 'localtime'))",
            (code, guest_player_id),
        )
        conn.commit()
    return {
        "guest_player_id": guest_player_id,
        "claim_code": code,
        "claimed_by": None,
        "claimed_at": None,
        "saves": found,
        "message": "已为旧游客存档生成认领码；登录后可先用 my_saves 选择空槽，再调用 account(action=\"claim\", claim_code=\"...\", slot=2)；slot 默认 1。",
    }


def _sessions_table_columns(conn, table, *,
    _table_exists,
):
    if not _table_exists(conn, table):
        return set()
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _stamp_save_owner(game, player_id, user_id, *,
    SESSIONS_DB_PATH,
    _sessions_db_connect,
    _sessions_table_columns,
    sqlite3,
):
    """token 玩家写档后回填 user_id 列（表或列不存在时静默跳过）。"""
    if not SESSIONS_DB_PATH.exists():
        return
    if game == "eco":
        targets = [("eco_sessions", False)]
    elif game == "ciyuwu":
        targets = [("ciyuwu_sessions", False)]
    elif game in {"mbti", "enneagram", "dnd", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"}:
        targets = [("test_sessions", True), ("test_results", True)]
    else:
        return
    try:
        with _sessions_db_connect() as conn:
            for table, has_game_column in targets:
                if "user_id" not in _sessions_table_columns(conn, table):
                    continue
                if has_game_column:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND game = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, game, user_id),
                    )
                else:
                    conn.execute(
                        f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                        (user_id, player_id, user_id),
                    )
            conn.commit()
    except sqlite3.OperationalError:
        pass


def _directory_vendor_save_exists(game, save_dir, *,
    ai_life_adapter,
    detroit_adapter,
    nowhere_storage,
):
    if not save_dir.is_dir():
        return False
    if game == "nowhere":
        return (save_dir / nowhere_storage.SAVE_NAME).is_file()
    if game == "ai_life":
        return (save_dir / ai_life_adapter.SAVE_NAME).is_file()
    if game == "detroit":
        return detroit_adapter.has_save(save_dir.name)
    return True


def _collect_player_saves(old_player_id, target_player_id, *,
    DIRECTORY_VENDOR_GAMES,
    SESSIONS_DB_PATH,
    VENDOR_SAVE_ROOT,
    _camping_plaza_save_summary,
    _directory_vendor_save_exists,
    _sessions_db_connect,
    _table_exists,
):
    """列出 old_player_id 名下所有存档，以及迁到 target_player_id 会撞上的冲突。

    返回 (found, conflicts)：found 形如 {"eco": {...}, "vendor:arcade": {...}}。
    """
    found = {}
    conflicts = []
    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            for table, game_label in (("eco_sessions", "eco"), ("ciyuwu_sessions", "ciyuwu")):
                if not _table_exists(conn, table):
                    continue
                row = conn.execute(
                    f"SELECT last_active FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchone()
                if not row:
                    continue
                found[game_label] = {"table": table, "last_active": row["last_active"]}
                if conn.execute(
                    f"SELECT 1 FROM {table} WHERE player_id = ?", (target_player_id,)
                ).fetchone():
                    conflicts.append(f"{game_label}（账号名下已有存档）")
            for table in ("test_sessions", "test_results"):
                if not _table_exists(conn, table):
                    continue
                for row in conn.execute(
                    f"SELECT game FROM {table} WHERE player_id = ?", (old_player_id,)
                ).fetchall():
                    game = row["game"]
                    found[f"{table}:{game}"] = {"table": table, "game": game}
                    if conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                        (target_player_id, game),
                    ).fetchone():
                        conflicts.append(f"{game}/{table}（账号名下已有记录）")
    for game in DIRECTORY_VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / old_player_id
        if not _directory_vendor_save_exists(game, old_dir):
            continue
        found[f"vendor:{game}"] = {"dir": str(old_dir)}
        if (VENDOR_SAVE_ROOT / game / target_player_id).exists():
            conflicts.append(f"{game}（账号名下已有存档目录）")
    garden_old_dir = VENDOR_SAVE_ROOT / "garden_cat" / old_player_id
    if (garden_old_dir / "state.json").is_file():
        found["garden_cat"] = {"dir": str(garden_old_dir)}
        if (VENDOR_SAVE_ROOT / "garden_cat" / target_player_id).exists():
            conflicts.append("garden_cat（账号目标槽已有存档目录）")
    workkk_old_dir = VENDOR_SAVE_ROOT / "workkk" / old_player_id
    if (workkk_old_dir / "game_state.json").is_file():
        found["workkk"] = {"dir": str(workkk_old_dir)}
        workkk_target_dir = VENDOR_SAVE_ROOT / "workkk" / target_player_id
        if workkk_target_dir.exists():
            conflicts.append("workkk（账号名下已有存档目录）")
    camping_summary = _camping_plaza_save_summary(old_player_id)
    if camping_summary is not None:
        found["camping_plaza"] = {"summary": camping_summary}
        if _camping_plaza_save_summary(target_player_id) is not None:
            conflicts.append("camping_plaza（账号目标槽已有存档）")
    return found, conflicts


def _workkk_save_admin(WORKKK_BASE, _McpError, httpx, /, action, **payload):
    """Ask the resident workkk process to mutate a save while holding its cache lock."""
    try:
        response = httpx.post(
            f"{WORKKK_BASE}/internal/saves/{action}",
            json=payload,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"workkk 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if response.status_code in {400, 409}:
        raise _McpError(-32602, detail or "workkk 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 workkk 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"workkk 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "workkk 存档管理服务返回格式错误")
    return body


def _migrate_workkk_save(old_player_id, target_player_id, *,
    _McpError,
    _workkk_save_admin,
):
    result = _workkk_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "workkk 存档管理服务未确认迁移成功")
    return True


def _delete_workkk_save(player_id, *,
    _workkk_save_admin,
):
    result = _workkk_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {"target": f"vendor_saves/workkk/{player_id}", "rows": 1}


def _garden_cat_save_admin(GARDEN_CAT_BASE, _McpError, httpx, /, action, **payload):
    """Ask Garden-Cat to mutate state/cache/notes under its own store lock."""
    try:
        response = httpx.post(
            f"{GARDEN_CAT_BASE}/internal/saves/{action}",
            json=payload,
            timeout=15,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"Garden-Cat 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = None
    if isinstance(body, dict):
        detail = body.get("detail") or body.get("message")
    if response.status_code in {400, 409}:
        raise _McpError(-32602, detail or "Garden-Cat 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 Garden-Cat 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"Garden-Cat 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "Garden-Cat 存档管理服务返回格式错误")
    return body


def _migrate_garden_cat_save(old_player_id, target_player_id, *,
    _McpError,
    _garden_cat_save_admin,
):
    result = _garden_cat_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "Garden-Cat 存档管理服务未确认迁移成功")
    return True


def _delete_garden_cat_save(player_id, *,
    _garden_cat_save_admin,
):
    result = _garden_cat_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {
        "target": f"vendor_saves/garden_cat/{player_id}",
        "rows": 1,
        "notes_deleted": int(result.get("notes_deleted") or 0),
    }


def _camping_plaza_save_admin(CAMPING_PLAZA_BASE, _McpError, httpx, /, action, *, timeout, **payload):
    """Ask the resident Camping Plaza adapter to manage its SQLite snapshot."""
    try:
        response = httpx.post(
            f"{CAMPING_PLAZA_BASE}/internal/saves/{action}",
            json=payload,
            timeout=timeout,
        )
    except httpx.HTTPError as exc:
        raise _McpError(-32603, f"Camping Plaza 存档管理服务连接失败：{exc}") from exc
    try:
        body = response.json()
    except ValueError:
        body = {}
    detail = body.get("detail") if isinstance(body, dict) else None
    if isinstance(detail, dict):
        detail = detail.get("message") or detail.get("error_code")
    if response.status_code in {400, 409, 422}:
        raise _McpError(-32602, detail or "Camping Plaza 存档参数无效或目标存档已存在")
    if response.status_code == 404:
        raise _McpError(-32004, detail or "没有找到 Camping Plaza 存档")
    if response.status_code >= 400:
        raise _McpError(
            -32603,
            detail or f"Camping Plaza 存档管理失败 HTTP {response.status_code}：{response.text[:200]}",
        )
    if not isinstance(body, dict):
        raise _McpError(-32603, "Camping Plaza 存档管理服务返回格式错误")
    return body


def _migrate_camping_plaza_save(old_player_id, target_player_id, *,
    _McpError,
    _camping_plaza_save_admin,
):
    result = _camping_plaza_save_admin(
        "migrate",
        source_player_id=old_player_id,
        target_player_id=target_player_id,
    )
    if result.get("migrated") is not True:
        raise _McpError(-32603, "Camping Plaza 存档管理服务未确认迁移成功")
    return True


def _delete_camping_plaza_save(player_id, *,
    CAMPING_PLAZA_DB_PATH,
    _camping_plaza_save_admin,
):
    if not CAMPING_PLAZA_DB_PATH.is_file():
        return None
    result = _camping_plaza_save_admin("delete", player_id=player_id)
    if not result.get("deleted"):
        return None
    return {"target": f"camping_plaza/{player_id}", "rows": 1}


def _camping_plaza_save_summary(player_id, *, timeout,
    CAMPING_PLAZA_DB_PATH,
    _McpError,
    _camping_plaza_save_admin,
):
    if not CAMPING_PLAZA_DB_PATH.is_file():
        return None
    try:
        result = _camping_plaza_save_admin("summary", player_id=player_id, timeout=timeout)
    except _McpError as exc:
        if exc.code == -32004:
            return None
        raise
    summary = result.get("summary")
    return summary if isinstance(summary, dict) else None


def _rollback_managed_claim_saves(managed_migrations, old_player_id, target_player_id, *,
    logger,
):
    for game, migrate in reversed(managed_migrations):
        try:
            migrate(target_player_id, old_player_id)
        except Exception as rollback_exc:
            message = getattr(rollback_exc, "message", str(rollback_exc))
            logger.error(
                "%s claim rollback failed %s -> %s: %s",
                game,
                target_player_id,
                old_player_id,
                message,
            )


def _migrate_player_saves(old_player_id, user_id, slot, *,
    DIRECTORY_VENDOR_GAMES,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    SESSIONS_DB_PATH,
    VENDOR_SAVE_ROOT,
    _McpError,
    _account_slot_player_id,
    _collect_player_saves,
    _directory_vendor_save_exists,
    _migrate_camping_plaza_save,
    _migrate_garden_cat_save,
    _migrate_workkk_save,
    _rollback_managed_claim_saves,
    _sessions_db_connect,
    _sessions_table_columns,
    _table_exists,
    logger,
):
    """把游客全部存档改绑到账号选择的 canonical slot，并回填 user_id 列。

    冲突时整体报错、不迁移、绝不覆盖或删除任何存档。返回迁移摘要。
    """
    if isinstance(slot, bool) or not isinstance(slot, int) or not MIN_SAVE_SLOT <= slot <= MAX_SAVE_SLOT:
        raise _McpError(-32602, "slot 必须是 1-5 的整数")
    target_player_id = _account_slot_player_id(user_id, slot)
    if old_player_id == target_player_id:
        raise _McpError(-32602, "旧 id 与账号 id 相同，无需迁移")
    found, conflicts = _collect_player_saves(old_player_id, target_player_id)
    if not found:
        raise _McpError(-32004, f"没有找到 player_id={old_player_id} 的任何存档")
    if conflicts:
        raise _McpError(
            -32602,
            "以下游戏在账号名下已有存档，迁移会冲突，已全部取消（不覆盖不删档）：" + "、".join(conflicts),
        )
    migrated = []
    managed_migrations = []
    moved_directories = []
    sessions_conn = None
    try:
        # Resident services own live caches. They must all succeed before touching
        # ordinary saves; never fall back to direct rename for either service.
        if "camping_plaza" in found:
            _migrate_camping_plaza_save(old_player_id, target_player_id)
            managed_migrations.append(("camping_plaza", _migrate_camping_plaza_save))
            migrated.append("camping_plaza")
        if "garden_cat" in found:
            _migrate_garden_cat_save(old_player_id, target_player_id)
            managed_migrations.append(("garden_cat", _migrate_garden_cat_save))
            migrated.append("vendor_saves/garden_cat")
        if "workkk" in found:
            _migrate_workkk_save(old_player_id, target_player_id)
            managed_migrations.append(("workkk", _migrate_workkk_save))
            migrated.append("vendor_saves/workkk")

        if SESSIONS_DB_PATH.exists():
            sessions_conn = _sessions_db_connect()
            for table in ("eco_sessions", "ciyuwu_sessions", "test_sessions", "test_results"):
                if not _table_exists(sessions_conn, table):
                    continue
                if "user_id" in _sessions_table_columns(sessions_conn, table):
                    cur = sessions_conn.execute(
                        f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                        (target_player_id, int(user_id), old_player_id),
                    )
                else:
                    cur = sessions_conn.execute(
                        f"UPDATE {table} SET player_id = ? WHERE player_id = ?",
                        (target_player_id, old_player_id),
                    )
                if cur.rowcount:
                    migrated.append(f"{table}×{cur.rowcount}")
        for game in DIRECTORY_VENDOR_GAMES:
            old_dir = VENDOR_SAVE_ROOT / game / old_player_id
            if _directory_vendor_save_exists(game, old_dir):
                target_dir = VENDOR_SAVE_ROOT / game / target_player_id
                old_dir.rename(target_dir)
                moved_directories.append((old_dir, target_dir))
                migrated.append(f"vendor_saves/{game}")
        if sessions_conn is not None:
            sessions_conn.commit()
    except Exception:
        if sessions_conn is not None:
            sessions_conn.rollback()
        for old_dir, target_dir in reversed(moved_directories):
            try:
                target_dir.rename(old_dir)
            except OSError as rollback_exc:
                logger.error(
                    "directory claim rollback failed %s -> %s: %s",
                    target_dir,
                    old_dir,
                    rollback_exc,
                )
        _rollback_managed_claim_saves(managed_migrations, old_player_id, target_player_id)
        raise
    finally:
        if sessions_conn is not None:
            sessions_conn.close()
    return {
        "old_player_id": old_player_id,
        "new_player_id": target_player_id,
        "target_player_id": target_player_id,
        "slot": slot,
        "migrated": migrated,
    }


def _auto_migrate_legacy_username_saves(user, username, *,
    DIRECTORY_VENDOR_GAMES,
    GAME_PLAYER_ID_RE,
    SESSIONS_DB_PATH,
    VENDOR_SAVE_ROOT,
    _McpError,
    _camping_plaza_save_summary,
    _directory_vendor_save_exists,
    _migrate_camping_plaza_save,
    _migrate_garden_cat_save,
    _migrate_workkk_save,
    _sessions_db_connect,
    _sessions_table_columns,
    _table_exists,
    logger,
    nowhere_storage,
    sqlite3,
):
    """Best-effort bridge for pre-account saves keyed by one username.

    Older games used the account username as ``player_id``. Token-based play now
    uses the numeric account id, so move only non-conflicting username saves to
    the numeric id before dispatching the game command.
    """
    username = (username or "").strip()
    if not username or not GAME_PLAYER_ID_RE.fullmatch(username):
        return []
    target_player_id = str(int(user["id"]))
    if username == target_player_id:
        return []

    migrated = []
    if SESSIONS_DB_PATH.exists():
        try:
            with _sessions_db_connect() as conn:
                for table in ("eco_sessions", "ciyuwu_sessions"):
                    if not _table_exists(conn, table):
                        continue
                    old_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchone()
                    target_row = conn.execute(
                        f"SELECT 1 FROM {table} WHERE player_id = ?",
                        (target_player_id,),
                    ).fetchone()
                    if old_row and not target_row:
                        conn.execute(
                            f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ?",
                            (target_player_id, int(user["id"]), username),
                        )
                        migrated.append(table)
                    elif target_row and "user_id" in _sessions_table_columns(conn, table):
                        conn.execute(
                            f"UPDATE {table} SET user_id = ? WHERE player_id = ? AND (user_id IS NULL OR user_id <> ?)",
                            (int(user["id"]), target_player_id, int(user["id"])),
                        )

                for table in ("test_sessions", "test_results"):
                    if not _table_exists(conn, table):
                        continue
                    rows = conn.execute(
                        f"SELECT DISTINCT game FROM {table} WHERE player_id = ?",
                        (username,),
                    ).fetchall()
                    for row in rows:
                        game = row["game"]
                        target_row = conn.execute(
                            f"SELECT 1 FROM {table} WHERE player_id = ? AND game = ?",
                            (target_player_id, game),
                        ).fetchone()
                        if target_row:
                            continue
                        if "user_id" in _sessions_table_columns(conn, table):
                            conn.execute(
                                f"UPDATE {table} SET player_id = ?, user_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, int(user["id"]), username, game),
                            )
                        else:
                            conn.execute(
                                f"UPDATE {table} SET player_id = ? WHERE player_id = ? AND game = ?",
                                (target_player_id, username, game),
                            )
                        migrated.append(f"{table}:{game}")
                conn.commit()
        except sqlite3.OperationalError:
            pass

    garden_old_dir = VENDOR_SAVE_ROOT / "garden_cat" / username
    garden_target_dir = VENDOR_SAVE_ROOT / "garden_cat" / target_player_id
    if (garden_old_dir / "state.json").is_file() and not garden_target_dir.exists():
        try:
            if _migrate_garden_cat_save(username, target_player_id):
                migrated.append("vendor_saves/garden_cat")
        except _McpError as exc:
            logger.warning(
                "garden_cat legacy save migration deferred %s -> %s: %s",
                username,
                target_player_id,
                exc.message,
            )

    for game in DIRECTORY_VENDOR_GAMES:
        old_dir = VENDOR_SAVE_ROOT / game / username
        target_dir = VENDOR_SAVE_ROOT / game / target_player_id
        if _directory_vendor_save_exists(game, old_dir) and not target_dir.exists():
            target_dir.parent.mkdir(parents=True, exist_ok=True)
            if game == "nowhere":
                nowhere_storage.migrate(username, target_player_id)
            else:
                old_dir.rename(target_dir)
            migrated.append(f"vendor_saves/{game}")
    workkk_old_dir = VENDOR_SAVE_ROOT / "workkk" / username
    workkk_target_dir = VENDOR_SAVE_ROOT / "workkk" / target_player_id
    if (workkk_old_dir / "game_state.json").is_file() and not workkk_target_dir.exists():
        try:
            if _migrate_workkk_save(username, target_player_id):
                migrated.append("vendor_saves/workkk")
        except _McpError as exc:
            # Legacy migration is best-effort. Never bypass the resident process,
            # because an out-of-band rename can be undone by its stale cache.
            logger.warning(
                "workkk legacy save migration deferred %s -> %s: %s",
                username,
                target_player_id,
                exc.message,
            )
    try:
        camping_old = _camping_plaza_save_summary(username)
        camping_target = _camping_plaza_save_summary(target_player_id)
        if camping_old is not None and camping_target is None:
            if _migrate_camping_plaza_save(username, target_player_id):
                migrated.append("camping_plaza")
    except _McpError as exc:
        logger.warning(
            "camping_plaza legacy save migration deferred %s -> %s: %s",
            username,
            target_player_id,
            exc.message,
        )
    return migrated


def _auto_migrate_legacy_account_saves(user, *,
    _account_username_aliases,
    _auto_migrate_legacy_username_saves,
):
    """Migrate saves under both the current and all reserved former names."""
    migrated = []
    for username in _account_username_aliases(user):
        migrated.extend(_auto_migrate_legacy_username_saves(user, username))
    return list(dict.fromkeys(migrated))


def _claim_guest_saves(raw_token, claim_code, slot, *,
    _McpError,
    _current_account,
    _db_connect,
    _init_guest_claim_table,
    _migrate_player_saves,
    _public_user,
    _row_dict,
    _save_slot_from_account_arguments,
):
    user = _current_account(raw_token)
    slot = _save_slot_from_account_arguments({"slot": slot})
    claim_code = (claim_code or "").strip()
    if not claim_code:
        raise _McpError(-32602, "claim_code 必填")
    with _db_connect() as conn:
        _init_guest_claim_table(conn)
        row = _row_dict(conn.execute(
            "SELECT * FROM guest_claim_codes WHERE code = ?", (claim_code,)
        ).fetchone())
    if not row or row.get("claimed_by") is not None:
        raise _McpError(-32001, "认领码无效或已被使用")
    result = _migrate_player_saves(
        row["guest_player_id"],
        int(user["id"]),
        slot=slot,
    )
    with _db_connect() as conn:
        conn.execute(
            """
            UPDATE guest_claim_codes
            SET claimed_by = ?, claimed_at = datetime('now', 'localtime'), claimed_slot = ?
            WHERE code = ? AND claimed_by IS NULL
            """,
            (int(user["id"]), slot, claim_code),
        )
        conn.commit()
    return {
        "ok": True,
        "user": _public_user(user),
        **result,
        "message": f"游客存档已认领并转入账号槽 {slot}；旧游客身份已停用，请带 token 并选择该 slot 续档。",
    }


def _delete_owned_session_rows(game, player_id, *,
    SESSIONS_DB_PATH,
    _sessions_db_connect,
    _table_exists,
):
    if not SESSIONS_DB_PATH.exists():
        return []
    deleted = []
    with _sessions_db_connect() as conn:
        if game == "eco":
            targets = [("eco_sessions", "eco", False)]
        elif game == "ciyuwu":
            targets = [("ciyuwu_sessions", "ciyuwu", False)]
        elif game in {"dnd", "mbti", "enneagram", "love", "ecr", "humanity", "sins_virtues", "bdsmtest"}:
            targets = [("test_sessions", game, True), ("test_results", game, True)]
        else:
            return deleted
        for table, label, has_game_column in targets:
            if not _table_exists(conn, table):
                continue
            if has_game_column:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ? AND game = ?",
                    (player_id, label),
                )
            else:
                cur = conn.execute(
                    f"DELETE FROM {table} WHERE player_id = ?",
                    (player_id,),
                )
            if cur.rowcount:
                deleted.append({"target": table, "rows": cur.rowcount})
        conn.commit()
    return deleted


def _delete_vendor_save_dir(game, player_id, *,
    DIRECTORY_VENDOR_GAMES,
    VENDOR_SAVE_ROOT,
    nowhere_storage,
    shutil,
):
    if game == "nowhere":
        if nowhere_storage.delete(player_id):
            return {"target": f"vendor_saves/nowhere/{player_id}", "rows": 1}
        return None
    if game not in DIRECTORY_VENDOR_GAMES:
        return None
    save_dir = VENDOR_SAVE_ROOT / game / player_id
    if not save_dir.is_dir():
        return None
    shutil.rmtree(save_dir)
    return {"target": f"vendor_saves/{game}/{player_id}", "rows": 1}


def _workkk_save_summary(player_id, *,
    VENDOR_SAVE_ROOT,
    json,
    logger,
):
    try:
        save_path = VENDOR_SAVE_ROOT / "workkk" / player_id / "game_state.json"
        with save_path.open("r", encoding="utf-8") as save_file:
            state = json.load(save_file)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("workkk save summary skipped unreadable save %s: %s", player_id, exc)
        return None
    if not isinstance(state, dict):
        logger.warning("workkk save summary skipped non-object save %s", player_id)
        return None
    return {
        "day": state.get("day_count", 0),
        "balance": state.get("salary_balance", 0),
    }


def _garden_cat_save_summary(player_id, *,
    VENDOR_SAVE_ROOT,
    _epoch_to_local_str,
    json,
    logger,
):
    """Read an existing Garden-Cat state without importing or calling its engine."""
    save_path = VENDOR_SAVE_ROOT / "garden_cat" / player_id / "state.json"
    try:
        with save_path.open("r", encoding="utf-8") as save_file:
            state = json.load(save_file)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        logger.warning("garden_cat save summary skipped unreadable save %s: %s", save_path, exc)
        return None
    if not isinstance(state, dict):
        logger.warning("garden_cat save summary skipped non-object save %s", save_path)
        return None
    encyclopedia = state.get("encyclopedia")
    return {
        "money": state.get("money", 0),
        "encyclopedia_count": len(encyclopedia) if isinstance(encyclopedia, list) else 0,
        "has_cat": state.get("cat") is not None,
        "last_active": _epoch_to_local_str(state.get("last_active_at")),
    }


def _delete_save(arguments, raw_token, *,
    DIRECTORY_VENDOR_GAMES,
    VENDOR_GAMES,
    _McpError,
    _account_slot_player_id,
    _auto_migrate_legacy_account_saves,
    _current_account,
    _delete_camping_plaza_save,
    _delete_garden_cat_save,
    _delete_owned_session_rows,
    _delete_vendor_save_dir,
    _delete_workkk_save,
    _public_user,
    _save_slot_from_account_arguments,
    detroit_adapter,
    moonlit_adapter,
):
    if arguments.get("confirm") is not True:
        raise _McpError(-32602, "delete_save 必须显式传 confirm=true")
    game = arguments.get("game")
    if not isinstance(game, str) or not game:
        raise _McpError(-32602, "game 参数必填")
    if game == "turtle_soup":
        raise _McpError(-32602, "海龟汤对局数据不支持 delete_save")
    if game not in {"eco", "ciyuwu", "dnd", "mbti", "enneagram", "love", "ecr", "humanity", "sins_virtues", "bdsmtest", "workkk", "camping_plaza", *VENDOR_GAMES}:
        raise _McpError(-32602, "未知或不支持删除存档的游戏")

    if not raw_token:
        raise _McpError(
            -32001,
            "游客存档无鉴权凭证，不支持删除；想重开可直接换一个新的游客 player_id，或注册账号后用认领码把档转入账号管理",
        )

    slot = _save_slot_from_account_arguments(arguments)
    user = _current_account(raw_token)
    _auto_migrate_legacy_account_saves(user)
    player_id = _account_slot_player_id(user["id"], slot)

    deleted = []
    if game == "garden_cat":
        garden_deleted = _delete_garden_cat_save(player_id)
        if garden_deleted:
            deleted.append(garden_deleted)
    elif game == "camping_plaza":
        camping_deleted = _delete_camping_plaza_save(player_id)
        if camping_deleted:
            deleted.append(camping_deleted)
    elif game == "workkk":
        workkk_deleted = _delete_workkk_save(player_id)
        if workkk_deleted:
            deleted.append(workkk_deleted)
    elif game == "detroit":
        try:
            if detroit_adapter.delete_save(player_id):
                deleted.append({"target": f"remote:detroit/{player_id}", "rows": 1})
        except detroit_adapter.DetroitError as exc:
            raise _McpError(-32010 if exc.uncertain else -32602, exc.message) from None
    elif game == "moonlit":
        if moonlit_adapter.delete_save(player_id):
            deleted.append(
                {"target": f"vendor_saves/moonlit/{player_id}", "rows": 1}
            )
    elif game in DIRECTORY_VENDOR_GAMES:
        vendor_deleted = _delete_vendor_save_dir(game, player_id)
        if vendor_deleted:
            deleted.append(vendor_deleted)
    else:
        deleted.extend(_delete_owned_session_rows(game, player_id))

    return {
        "ok": True,
        "game": game,
        "slot": slot,
        "player_id": player_id,
        "user": _public_user(user),
        "deleted": deleted,
        "message": "已删除存档。" if deleted else "没有找到该身份和槽位下的存档。",
    }


def _account_saves_for_user(user, *, migrate_legacy,
    SESSIONS_DB_PATH,
    VendorCmdError,
    _account_slot_player_ids,
    _auto_migrate_legacy_account_saves,
    _camping_plaza_save_summary,
    _db_connect,
    _epoch_to_local_str,
    _garden_cat_save_summary,
    _public_user,
    _sessions_db_connect,
    _sessions_table_columns,
    _table_exists,
    _turtle_soup_stats,
    _workkk_save_summary,
    ai_life_adapter,
    arcade_adapter,
    bar_adapter,
    burger_adapter,
    crucible_echoes_adapter,
    delve_adapter,
    detroit_adapter,
    fishing_adapter,
    forest_adapter,
    imitator_td_adapter,
    leek_adapter,
    market_adapter,
    memoria_adapter,
    moonlit_adapter,
    nowhere_adapter,
    travel_adapter,
    white_room_adapter,
):
    """按账号聚合返回该用户在所有游戏的存档概况；没有存档的游戏不列。"""
    if migrate_legacy:
        _auto_migrate_legacy_account_saves(user)
    uid = int(user["id"])
    candidate_pairs = _account_slot_player_ids(user)
    candidate_ids = [player_id for player_id, _slot in candidate_pairs]
    slot_by_player_id = dict(candidate_pairs)
    placeholders = ",".join("?" * len(candidate_ids))
    games = {}

    with _db_connect() as conn:
        soup_stats = _turtle_soup_stats(conn, user)
    if any(int(value or 0) > 0 for value in soup_stats.values()):
        games["turtle_soup"] = soup_stats

    def _slot_entry(game, slot):
        game_entry = games.setdefault(game, {"slots": [], "_slot_entries": {}})
        entry = game_entry["_slot_entries"].get(slot)
        if entry is None:
            entry = {"slot": slot}
            game_entry["_slot_entries"][slot] = entry
            game_entry["slots"].append(entry)
        return entry

    if SESSIONS_DB_PATH.exists():
        with _sessions_db_connect() as conn:
            def _owned_rows(table, select_columns):
                has_uid = "user_id" in _sessions_table_columns(conn, table)
                where = f"player_id IN ({placeholders})" + (" OR user_id = ?" if has_uid else "")
                args = list(candidate_ids) + ([uid] if has_uid else [])
                return conn.execute(f"SELECT player_id, {select_columns} FROM {table} WHERE {where}", args).fetchall()

            if _table_exists(conn, "test_results"):
                for row in _owned_rows("test_results", "game, result_value, completed_at"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    if (row["completed_at"] or 0) > (entry.get("_completed_at") or 0):
                        entry["_completed_at"] = row["completed_at"]
                        entry["latest_result"] = row["result_value"]
                        entry["completed_at"] = _epoch_to_local_str(row["completed_at"])
            if _table_exists(conn, "test_sessions"):
                for row in _owned_rows("test_sessions", "game, mode, current_question"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry(row["game"], slot)
                    entry["in_progress"] = {"mode": row["mode"], "current_question": row["current_question"]}
            if _table_exists(conn, "eco_sessions"):
                for row in _owned_rows("eco_sessions", "save_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("eco", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from eco_adapter import handler as eco_handler
                    summary = eco_handler.summarize_save(row["save_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
            if _table_exists(conn, "ciyuwu_sessions"):
                for row in _owned_rows("ciyuwu_sessions", "save_data, meta_data, last_active"):
                    slot = slot_by_player_id.get(row["player_id"])
                    if slot is None:
                        continue
                    entry = _slot_entry("ciyuwu", slot)
                    if entry.get("last_active") and (row["last_active"] or "") <= (entry.get("last_active") or ""):
                        continue
                    from ciyuwu_adapter import handler as ciyuwu_handler
                    summary = ciyuwu_handler.summarize_save(row["save_data"], row["meta_data"]) or {}
                    summary["last_active"] = row["last_active"]
                    entry.clear()
                    entry.update({"slot": slot, **summary})
    vendor_summaries = {
        "ai_life": ai_life_adapter.save_summary,
        "bar": bar_adapter.save_summary,
        "leek": leek_adapter.save_summary,
        "delve": delve_adapter.save_summary,
        "travel": travel_adapter.save_summary,
        "nowhere": nowhere_adapter.save_summary,
        "arcade": arcade_adapter.save_summary,
        "burger": burger_adapter.save_summary,
        "crucible_echoes": crucible_echoes_adapter.save_summary,
        "fishing": fishing_adapter.save_summary,
        "forest": forest_adapter.save_summary,
        "moonlit": moonlit_adapter.save_summary,
        "imitator_td": imitator_td_adapter.save_summary,
        "memoria": memoria_adapter.save_summary,
        "white_room": white_room_adapter.save_summary,
        "market": market_adapter.save_summary,
        "workkk": _workkk_save_summary,
        "garden_cat": _garden_cat_save_summary,
        "camping_plaza": _camping_plaza_save_summary,
        "detroit": detroit_adapter.save_summary,
    }
    for game, summarize in vendor_summaries.items():
        for candidate, slot in candidate_pairs:
            try:
                summary = summarize(candidate)
            except VendorCmdError:
                summary = None
            if summary is not None:
                entry = _slot_entry(game, slot)
                if len(entry) == 1:
                    entry.update(summary)
    for game_entry in games.values():
        if isinstance(game_entry, dict):
            game_entry.pop("_slot_entries", None)
            for entry in game_entry.get("slots", []):
                if isinstance(entry, dict):
                    entry.pop("_completed_at", None)
            if "slots" in game_entry:
                game_entry["slots"].sort(key=lambda item: item.get("slot", 0))
    return {"user": _public_user(user), "saves": games}


def _account_my_saves(raw_token, *, human, username,
    _account_saves_for_user,
    _bound_human_user_for_saves,
    _current_account,
):
    if human is True:
        target = _bound_human_user_for_saves(raw_token, username)
        result = _account_saves_for_user(target, migrate_legacy=False)
        return {
            "username": target["username"],
            "user": result["user"],
            "saves": result["saves"],
        }
    user = _current_account(raw_token)
    return _account_saves_for_user(user)


def _nowhere_web_saves(raw_token, *,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    _account_slot_player_id,
    _current_human_account,
    _db_connect,
    nowhere_adapter,
):
    """Read only Nowhere save envelopes for this human's bound machines."""
    user = _current_human_account(raw_token)
    with _db_connect() as conn:
        rows = conn.execute(
            """SELECT ai.id, ai.username FROM user_bindings b
               JOIN toy_users ai ON ai.id = b.ai_user_id
               WHERE b.human_user_id = ? AND ai.is_ai = 1 AND ai.deleted_at IS NULL
               ORDER BY ai.username, ai.id""", (int(user["id"]),)).fetchall()
    machines = []
    for row in rows:
        slots = []
        for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1):
            summary = nowhere_adapter.save_summary(_account_slot_player_id(row["id"], slot))
            if summary is not None:
                slots.append({**summary, "slot": slot})
        machines.append({"user": {"id": int(row["id"]), "username": row["username"]}, "slots": slots})
    return {"machines": machines}


def _filtered_account_web_saves(raw_token, game, *,
    MAX_SAVE_SLOT,
    MIN_SAVE_SLOT,
    ThreadPoolExecutor,
    VendorCmdError,
    _McpError,
    _account_slot_player_id,
    _camping_plaza_save_summary,
    _current_human_account,
    _db_connect,
    _workkk_save_summary,
    ai_life_adapter,
    detroit_adapter,
    logger,
    moonlit_adapter,
):
    """Read one allowlisted game's existing slots; never migrate or start a game."""
    summaries = {
        "workkk": _workkk_save_summary,
        "moonlit": moonlit_adapter.save_summary,
        "ai_life": ai_life_adapter.save_summary,
        "detroit": detroit_adapter.save_summary,
        "camping_plaza": lambda player: _camping_plaza_save_summary(player, timeout=2),
    }
    if game not in summaries:
        raise _McpError(-32602, "不支持的存档筛选游戏")
    user = _current_human_account(raw_token)
    with _db_connect() as conn:
        rows = conn.execute(
            """SELECT ai.id, ai.username FROM user_bindings b
               JOIN toy_users ai ON ai.id = b.ai_user_id
               WHERE b.human_user_id = ? AND ai.is_ai = 1 AND ai.deleted_at IS NULL
               ORDER BY b.created_at DESC""", (int(user["id"]),)).fetchall()
    machines = [
        {"username": row["username"],
         "user": {"id": int(row["id"]), "username": row["username"]}, "saves": {}}
        for row in rows
    ]
    targets = [(index, slot) for index in range(len(rows))
               for slot in range(MIN_SAVE_SLOT, MAX_SAVE_SLOT + 1)]

    def read_slot(target):
        index, slot = target
        player = _account_slot_player_id(rows[index]["id"], slot)
        try:
            return index, slot, summaries[game](player), None
        except (VendorCmdError, _McpError, OSError) as exc:
            logger.warning("%s picker summary failed for %s: %s", game, player, exc)
            return index, slot, None, "存档摘要暂不可用"

    # Only Camping uses HTTP. Bound fan-out and a short per-call timeout prevent
    # one failed slot from serially blocking every other bound machine's slots.
    if game == "camping_plaza" and targets:
        with ThreadPoolExecutor(max_workers=min(8, len(targets))) as pool:
            results = list(pool.map(read_slot, targets))
    else:
        results = map(read_slot, targets)
    errors = []
    for index, slot, summary, error in results:
        if error:
            errors.append({"ai_user_id": machines[index]["user"]["id"], "slot": slot, "error": error})
        elif summary is not None:
            entry = machines[index]["saves"].setdefault(game, {"slots": []})
            entry["slots"].append({**summary, "slot": slot})
    if errors and not any(machine["saves"] for machine in machines):
        # An unavailable service must not masquerade as an empty/new save.
        raise _McpError(-32603, "存档摘要读取失败，请稍后再试")
    result = {"machines": machines}
    if errors:
        result["errors"] = errors
    return result


def _account_web_saves(raw_token, *,
    _account_saves_for_user,
    _current_account,
    _db_connect,
    _public_user,
    _row_dict,
):
    user = _current_account(raw_token)
    own = _account_saves_for_user(user, migrate_legacy=False)
    with _db_connect() as conn:
        rows = conn.execute(
            """
            SELECT ai.*
            FROM user_bindings b
            JOIN toy_users ai ON ai.id = b.ai_user_id
            WHERE b.human_user_id = ?
              AND ai.deleted_at IS NULL
            ORDER BY b.created_at DESC
            """,
            (int(user["id"]),),
        ).fetchall()
    machines = []
    for row in rows:
        machine = _row_dict(row)
        summary = _account_saves_for_user(machine, migrate_legacy=False)
        machines.append({
            "username": machine["username"],
            "user": summary["user"],
            "saves": summary["saves"],
        })
    return {
        "user": _public_user(user),
        "self": {
            "username": user["username"],
            "user": own["user"],
            "saves": own["saves"],
        },
        "machines": machines,
    }
