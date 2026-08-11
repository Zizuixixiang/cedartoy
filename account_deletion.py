"""72-hour account deletion state and retry-safe purge orchestration.

The account database is the durable coordinator.  Cross-database/file work is
split into idempotent phases; the identifying ``toy_users`` row is removed only
after every external phase has completed.
"""

from __future__ import annotations

import json
import secrets
import shutil
import sqlite3
import time
from pathlib import Path


DELETION_COOLDOWN_SECONDS = 72 * 60 * 60
PURGE_LEASE_SECONDS = 15 * 60
PHASE_PENDING = "pending"
PHASE_MANAGED_SAVES = "managed_saves"
PHASE_FILE_SAVES = "file_saves"
PHASE_SESSIONS = "sessions_private"
PHASE_SHARED = "shared_anonymized"
PHASE_MAIN = "main_private"
PHASE_COMPLETE = "complete"


class DeletionError(Exception):
    """Base account-deletion error with a stable machine-readable reason."""

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn: sqlite3.Connection, table: str, column: str, sql: str) -> None:
    if column not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql}")


def init_schema(conn: sqlite3.Connection) -> None:
    """Idempotently add the new model without interpreting legacy deleted_at."""
    if not _table_exists(conn, "toy_users"):
        return
    _add_column(conn, "toy_users", "deletion_requested_at_epoch", "INTEGER")
    _add_column(conn, "toy_users", "scheduled_delete_at_epoch", "INTEGER")
    _add_column(conn, "toy_users", "deletion_job_id", "TEXT")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS account_deletion_jobs (
            job_id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('pending', 'running', 'complete')),
            phase TEXT NOT NULL,
            requested_at_epoch INTEGER NOT NULL,
            scheduled_at_epoch INTEGER NOT NULL,
            started_at_epoch INTEGER,
            completed_at_epoch INTEGER,
            attempts INTEGER NOT NULL DEFAULT 0,
            stats_json TEXT NOT NULL DEFAULT '{}',
            lease_token TEXT,
            lease_expires_at_epoch INTEGER
        )
        """
    )
    conn.execute(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS idx_account_deletion_jobs_active_user
        ON account_deletion_jobs(user_id)
        WHERE status IN ('pending', 'running')
        """
    )
    _add_column(conn, "account_deletion_jobs", "lease_token", "TEXT")
    _add_column(conn, "account_deletion_jobs", "lease_expires_at_epoch", "INTEGER")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_account_deletion_jobs_due
        ON account_deletion_jobs(status, scheduled_at_epoch)
        """
    )
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_toy_users_scheduled_delete
        ON toy_users(scheduled_delete_at_epoch)
        WHERE scheduled_delete_at_epoch IS NOT NULL
        """
    )


def _status_dict(row, *, now_epoch: int) -> dict:
    if row is None or row["deletion_requested_at_epoch"] is None:
        return {"status": "active", "pending": False}
    scheduled = int(row["scheduled_delete_at_epoch"])
    return {
        "status": "due" if now_epoch >= scheduled else "pending",
        "pending": True,
        "deletion_requested_at_epoch": int(row["deletion_requested_at_epoch"]),
        "scheduled_delete_at_epoch": scheduled,
        "deletion_job_id": row["deletion_job_id"],
        "seconds_remaining": max(0, scheduled - now_epoch),
        "can_cancel": now_epoch < scheduled,
    }


def deletion_status(conn: sqlite3.Connection, user_id: int, *, now_epoch=None) -> dict:
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    columns = _columns(conn, "toy_users")
    if "deletion_requested_at_epoch" not in columns:
        exists = conn.execute("SELECT 1 FROM toy_users WHERE id=?", (int(user_id),)).fetchone()
        return {"status": "active", "pending": False} if exists else {"status": "deleted", "pending": False}
    row = conn.execute(
        """
        SELECT deletion_requested_at_epoch, scheduled_delete_at_epoch, deletion_job_id
        FROM toy_users WHERE id = ?
        """,
        (int(user_id),),
    ).fetchone()
    if row is None:
        return {"status": "deleted", "pending": False}
    return _status_dict(row, now_epoch=now_epoch)


def request_deletion(
    conn: sqlite3.Connection,
    user_id: int,
    *,
    now_epoch=None,
    delay_seconds=DELETION_COOLDOWN_SECONDS,
) -> dict:
    """Create a fresh request.  No prior waiting time can be carried forward."""
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    scheduled = now_epoch + int(delay_seconds)
    job_id = secrets.token_hex(16)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        "SELECT id, deletion_requested_at_epoch FROM toy_users WHERE id = ?",
        (int(user_id),),
    ).fetchone()
    if row is None:
        conn.rollback()
        raise DeletionError("账号不存在", "account_missing")
    if row["deletion_requested_at_epoch"] is not None:
        status = deletion_status(conn, int(user_id), now_epoch=now_epoch)
        conn.rollback()
        return status
    conn.execute(
        """
        INSERT INTO account_deletion_jobs (
            job_id, user_id, status, phase, requested_at_epoch, scheduled_at_epoch
        ) VALUES (?, ?, 'pending', ?, ?, ?)
        """,
        (job_id, int(user_id), PHASE_PENDING, now_epoch, scheduled),
    )
    conn.execute(
        """
        UPDATE toy_users
        SET deletion_requested_at_epoch = ?, scheduled_delete_at_epoch = ?,
            deletion_job_id = ?
        WHERE id = ?
        """,
        (now_epoch, scheduled, job_id, int(user_id)),
    )
    conn.commit()
    return deletion_status(conn, int(user_id), now_epoch=now_epoch)


def cancel_deletion(conn: sqlite3.Connection, user_id: int, *, now_epoch=None) -> dict:
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    conn.execute("BEGIN IMMEDIATE")
    row = conn.execute(
        """
        SELECT deletion_requested_at_epoch, scheduled_delete_at_epoch, deletion_job_id
        FROM toy_users WHERE id = ?
        """,
        (int(user_id),),
    ).fetchone()
    if row is None:
        conn.rollback()
        raise DeletionError("账号已完成删除", "already_deleted")
    if row["deletion_requested_at_epoch"] is None:
        conn.rollback()
        return {"status": "active", "pending": False, "cancelled": False}
    if now_epoch >= int(row["scheduled_delete_at_epoch"]):
        conn.rollback()
        raise DeletionError(
            "72 小时冷静期已结束，账号不能再取消，正在等待最终清理",
            "deletion_due",
        )
    job_id = row["deletion_job_id"]
    job = conn.execute(
        "SELECT status, phase FROM account_deletion_jobs WHERE job_id = ?", (job_id,)
    ).fetchone()
    if job is None or job["status"] != "pending" or job["phase"] != PHASE_PENDING:
        conn.rollback()
        raise DeletionError("账号清理已经开始，不能再取消", "purge_started")
    conn.execute(
        """
        UPDATE toy_users
        SET deletion_requested_at_epoch = NULL, scheduled_delete_at_epoch = NULL,
            deletion_job_id = NULL
        WHERE id = ?
        """,
        (int(user_id),),
    )
    # A cancelled request has no residual clock or reusable job state.
    conn.execute("DELETE FROM account_deletion_jobs WHERE job_id = ?", (job_id,))
    conn.commit()
    return {"status": "active", "pending": False, "cancelled": True}


def _player_ids(user_id: int) -> list[str]:
    base = str(int(user_id))
    return [base, *(f"{base}:{slot}" for slot in range(2, 6))]


def _placeholders(values) -> str:
    return ",".join("?" for _ in values)


def _merge_stats(account_db: Path, job_id: str, phase: str, additions: dict) -> None:
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT stats_json FROM account_deletion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        stats = json.loads(row["stats_json"] or "{}") if row else {}
        previous = stats.get(phase) if isinstance(stats.get(phase), dict) else {}
        stats[phase] = {
            key: max(int(previous.get(key, 0) or 0), int(additions.get(key, 0) or 0))
            for key in set(previous) | set(additions)
        }
        conn.execute(
            "UPDATE account_deletion_jobs SET stats_json = ? WHERE job_id = ?",
            (json.dumps(stats, ensure_ascii=False, sort_keys=True), job_id),
        )


def _start_phase(account_db: Path, job_id: str, phase: str, lease_token: str, now_epoch: int) -> None:
    with sqlite3.connect(account_db) as conn:
        changed = conn.execute(
            """
            UPDATE account_deletion_jobs
            SET status='running', phase=?, started_at_epoch=COALESCE(started_at_epoch, ?),
                lease_expires_at_epoch=?
            WHERE job_id=? AND status!='complete' AND lease_token=?
            """,
            (phase, now_epoch, now_epoch + PURGE_LEASE_SECONDS, job_id, lease_token),
        ).rowcount
        if changed != 1:
            raise RuntimeError("account purge lease lost")


def _delete_managed_saves(
    save_root: Path,
    player_ids: list[str],
    *,
    workkk_delete,
    garden_delete,
) -> dict:
    counts = {"workkk": 0, "garden_cat": 0}
    for player_id in player_ids:
        workkk_path = save_root / "workkk" / player_id / "game_state.json"
        if workkk_path.is_file():
            if workkk_delete is None:
                raise RuntimeError("workkk managed delete callback is required")
            workkk_delete(player_id)
            if workkk_path.exists():
                raise RuntimeError("workkk managed delete did not remove save")
            counts["workkk"] += 1
        garden_path = save_root / "garden_cat" / player_id / "state.json"
        if garden_path.is_file():
            if garden_delete is None:
                raise RuntimeError("garden_cat managed delete callback is required")
            garden_delete(player_id)
            if garden_path.exists():
                raise RuntimeError("garden_cat managed delete did not remove save")
            counts["garden_cat"] += 1
    return counts


def _delete_file_saves(save_root: Path, player_ids: list[str]) -> dict:
    removed = 0
    if not save_root.is_dir():
        return {"directories": 0}
    for game_dir in save_root.iterdir():
        if not game_dir.is_dir() or game_dir.name in {"workkk", "garden_cat"}:
            continue
        for player_id in player_ids:
            target = game_dir / player_id
            if target.is_symlink():
                raise RuntimeError(f"refusing symlink save directory: {target}")
            if target.is_dir():
                shutil.rmtree(target)
                removed += 1
    return {"directories": removed}


def _delete_sessions_private(sessions_db: Path, user_id: int, player_ids: list[str]) -> dict:
    counts = {}
    if not sessions_db.is_file():
        return counts
    with sqlite3.connect(sessions_db) as conn:
        conn.row_factory = sqlite3.Row
        for table in (
            "test_sessions", "test_results", "eco_sessions", "ciyuwu_sessions",
            "sessions_deprecated", "results_deprecated",
        ):
            columns = _columns(conn, table)
            if not columns:
                continue
            player_clause = f"player_id IN ({_placeholders(player_ids)})"
            args = list(player_ids)
            if "user_id" in columns:
                clauses = [f"(user_id IS NULL AND {player_clause})", "user_id = ?"]
                args.append(int(user_id))
            else:
                clauses = [player_clause]
            cur = conn.execute(f"DELETE FROM {table} WHERE " + " OR ".join(clauses), args)
            counts[table] = max(0, cur.rowcount)
        if _table_exists(conn, "announcement_reads"):
            identities = [*player_ids, f"human:{int(user_id)}"]
            cur = conn.execute(
                f"DELETE FROM announcement_reads WHERE player_id IN ({_placeholders(identities)})",
                identities,
            )
            counts["announcement_reads"] = max(0, cur.rowcount)
    return counts


def _delete_garden_notes(notes_db: Path, player_ids: list[str]) -> dict:
    if not notes_db.is_file():
        return {"garden_notes": 0}
    with sqlite3.connect(notes_db) as conn:
        if not _table_exists(conn, "garden_notes"):
            return {"garden_notes": 0}
        cur = conn.execute(
            f"DELETE FROM garden_notes WHERE session_id IN ({_placeholders(player_ids)})",
            player_ids,
        )
        return {"garden_notes": max(0, cur.rowcount)}


def _delete_legacy_garden(legacy_db: Path | None, player_ids: list[str]) -> dict:
    if legacy_db is None or not legacy_db.is_file():
        return {}
    counts = {}
    with sqlite3.connect(legacy_db) as conn:
        for table in ("garden_saves", "garden_notes"):
            if not _table_exists(conn, table):
                continue
            cur = conn.execute(
                f"DELETE FROM {table} WHERE session_id IN ({_placeholders(player_ids)})",
                player_ids,
            )
            counts[f"legacy_{table}"] = max(0, cur.rowcount)
    return counts


def _anonymize_shared_garden_notes(
    account_db: Path, notes_db: Path, user_id: int
) -> dict:
    """Anonymize human notes left in gardens owned by still-existing bound AIs."""
    if not notes_db.is_file():
        return {"shared_garden_notes": 0}
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        user = conn.execute(
            "SELECT username, is_ai FROM toy_users WHERE id=?", (int(user_id),)
        ).fetchone()
        if user is None or int(user["is_ai"] or 0):
            return {"shared_garden_notes": 0}
        names = [str(user["username"])]
        if _table_exists(conn, "account_username_changes"):
            names.extend(
                str(row[0])
                for row in conn.execute(
                    "SELECT old_username FROM account_username_changes WHERE user_id=?",
                    (int(user_id),),
                ).fetchall()
            )
        ai_ids = [
            int(row[0])
            for row in conn.execute(
                "SELECT ai_user_id FROM user_bindings WHERE human_user_id=?",
                (int(user_id),),
            ).fetchall()
        ] if _table_exists(conn, "user_bindings") else []
    if not ai_ids or not names:
        return {"shared_garden_notes": 0}
    sessions = [pid for ai_id in ai_ids for pid in _player_ids(ai_id)]
    with sqlite3.connect(notes_db) as conn:
        columns = _columns(conn, "garden_notes")
        if not {"session_id", "author_type", "author_name"}.issubset(columns):
            return {"shared_garden_notes": 0}
        cur = conn.execute(
            f"""
            UPDATE garden_notes SET author_name='已注销用户'
            WHERE author_type='human'
              AND session_id IN ({_placeholders(sessions)})
              AND author_name IN ({_placeholders(names)})
            """,
            [*sessions, *names],
        )
        return {"shared_garden_notes": max(0, cur.rowcount)}


def _anonymize_duel(duel_db: Path, player_ids: list[str]) -> dict:
    if not duel_db.is_file():
        return {"duel_participants": 0}
    with sqlite3.connect(duel_db) as conn:
        if not _table_exists(conn, "room_participants"):
            return {"duel_participants": 0}
        cur = conn.execute(
            f"""
            UPDATE room_participants SET display_name='已注销用户'
            WHERE player_id IN ({_placeholders(player_ids)})
              AND COALESCE(display_name, '')!='已注销用户'
            """,
            player_ids,
        )
        # player_id remains an opaque room key so message/cursor foreign keys and
        # the other participant's history stay intact.  toy_users is removed later.
        return {"duel_participants": max(0, cur.rowcount)}


def _delete_if_exists(conn, table: str, where: str, args) -> int:
    if not _table_exists(conn, table):
        return 0
    cur = conn.execute(f"DELETE FROM {table} WHERE {where}", args)
    return max(0, cur.rowcount)


def _purge_main_private(
    account_db: Path, job_id: str, user_id: int, player_ids: list[str],
    now_epoch: int, lease_token: str,
) -> dict:
    counts = {}
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        player_rows = []
        if _table_exists(conn, "players"):
            player_rows = conn.execute(
                "SELECT id FROM players WHERE user_id = ?", (int(user_id),)
            ).fetchall()
            for row in player_rows:
                player_id = int(row["id"])
                anonymous = f"deleted-{job_id[:12]}-{player_id}"
                conn.execute(
                    """
                    UPDATE players
                    SET username=?, password_hash=NULL, user_id=NULL, is_guest=1,
                        is_ai=0, is_admin=0, source='deleted'
                    WHERE id=?
                    """,
                    (anonymous, player_id),
                )
            counts["players_anonymized"] = len(player_rows)
            if player_rows and _table_exists(conn, "flagged_content"):
                ids = [int(row["id"]) for row in player_rows]
                counts["username_flags"] = _delete_if_exists(
                    conn,
                    "flagged_content",
                    f"type='username' AND ref_id IN ({_placeholders(ids)})",
                    ids,
                )

        table_deletes = (
            ("email_verification_attempts", "user_id = ?", (user_id,)),
            ("email_verification_codes", "user_id = ?", (user_id,)),
            ("account_emails", "user_id = ?", (user_id,)),
            ("password_reset_tokens", "user_id = ?", (user_id,)),
            ("ai_access_tokens", "user_id = ?", (user_id,)),
            ("legacy_ai_token_hashes", "user_id = ?", (user_id,)),
            ("binding_tokens", "ai_user_id = ?", (user_id,)),
            ("user_bindings", "human_user_id = ? OR ai_user_id = ?", (user_id, user_id)),
            ("anti_addiction_settings", "ai_user_id = ?", (user_id,)),
            ("anti_addiction_states", f"player_id IN ({_placeholders(player_ids)})", player_ids),
            ("account_registration_events", "user_id = ?", (user_id,)),
            ("account_username_changes", "user_id = ?", (user_id,)),
            ("guest_claim_codes", "claimed_by = ?", (user_id,)),
        )
        for table, where, args in table_deletes:
            counts[table] = _delete_if_exists(conn, table, where, args)

        counts["toy_users"] = _delete_if_exists(conn, "toy_users", "id = ?", (user_id,))
        final_stats = conn.execute(
            "SELECT stats_json FROM account_deletion_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        stats = json.loads(final_stats["stats_json"] or "{}") if final_stats else {}
        stats[PHASE_MAIN] = counts
        conn.execute(
            """
            UPDATE account_deletion_jobs
            SET status='complete', phase=?, completed_at_epoch=?, stats_json=?,
                user_id=0, lease_token=NULL, lease_expires_at_epoch=NULL
            WHERE job_id=? AND lease_token=?
            """,
            (
                PHASE_COMPLETE, now_epoch,
                json.dumps(stats, ensure_ascii=False, sort_keys=True),
                job_id, lease_token,
            ),
        )
        conn.commit()
    return counts


def purge_account(
    *,
    account_db,
    sessions_db,
    vendor_save_root,
    duel_db,
    garden_notes_db,
    user_id=None,
    job_id=None,
    now_epoch=None,
    workkk_delete=None,
    garden_delete=None,
    garden_legacy_db=None,
) -> dict:
    """Purge one due account. Every committed phase is safe to repeat."""
    account_db = Path(account_db)
    sessions_db = Path(sessions_db)
    vendor_save_root = Path(vendor_save_root)
    duel_db = Path(duel_db)
    garden_notes_db = Path(garden_notes_db)
    garden_legacy_db = Path(garden_legacy_db) if garden_legacy_db is not None else None
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        init_schema(conn)
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        if job_id is not None:
            job = conn.execute(
                "SELECT * FROM account_deletion_jobs WHERE job_id = ?", (str(job_id),)
            ).fetchone()
        else:
            job = conn.execute(
                """
                SELECT * FROM account_deletion_jobs
                WHERE user_id=? AND status IN ('pending', 'running')
                ORDER BY requested_at_epoch DESC LIMIT 1
                """,
                (int(user_id),),
            ).fetchone()
        if job is None:
            conn.rollback()
            return {"status": "not_found", "purged": False}
        job = dict(job)
        if job["status"] == "complete":
            conn.rollback()
            return {"status": "complete", "purged": False, "job_id": job["job_id"]}
        if now_epoch < int(job["scheduled_at_epoch"]):
            conn.rollback()
            return {
                "status": "pending",
                "purged": False,
                "job_id": job["job_id"],
                "scheduled_at_epoch": int(job["scheduled_at_epoch"]),
            }
        if (
            job["status"] == "running"
            and job.get("lease_expires_at_epoch") is not None
            and int(job["lease_expires_at_epoch"]) > now_epoch
        ):
            conn.rollback()
            return {"status": "busy", "purged": False, "job_id": job["job_id"]}
        lease_token = secrets.token_hex(16)
        conn.execute(
            """
            UPDATE account_deletion_jobs
            SET status='running', lease_token=?, lease_expires_at_epoch=?,
                attempts=attempts+1
            WHERE job_id=?
            """,
            (lease_token, now_epoch + PURGE_LEASE_SECONDS, job["job_id"]),
        )
        conn.commit()

    job_id = str(job["job_id"])
    user_id = int(job["user_id"])
    player_ids = _player_ids(user_id)

    _start_phase(account_db, job_id, PHASE_MANAGED_SAVES, lease_token, now_epoch)
    stats = _delete_managed_saves(
        vendor_save_root,
        player_ids,
        workkk_delete=workkk_delete,
        garden_delete=garden_delete,
    )
    _merge_stats(account_db, job_id, PHASE_MANAGED_SAVES, stats)

    _start_phase(account_db, job_id, PHASE_FILE_SAVES, lease_token, now_epoch)
    stats = _delete_file_saves(vendor_save_root, player_ids)
    _merge_stats(account_db, job_id, PHASE_FILE_SAVES, stats)

    _start_phase(account_db, job_id, PHASE_SESSIONS, lease_token, now_epoch)
    stats = _delete_sessions_private(sessions_db, user_id, player_ids)
    stats.update(_delete_garden_notes(garden_notes_db, player_ids))
    stats.update(_delete_legacy_garden(garden_legacy_db, player_ids))
    _merge_stats(account_db, job_id, PHASE_SESSIONS, stats)

    _start_phase(account_db, job_id, PHASE_SHARED, lease_token, now_epoch)
    stats = _anonymize_duel(duel_db, player_ids)
    stats.update(_anonymize_shared_garden_notes(account_db, garden_notes_db, user_id))
    _merge_stats(account_db, job_id, PHASE_SHARED, stats)

    _start_phase(account_db, job_id, PHASE_MAIN, lease_token, now_epoch)
    final = _purge_main_private(
        account_db, job_id, user_id, player_ids, now_epoch, lease_token
    )
    return {"status": "complete", "purged": True, "job_id": job_id, "final": final}


def due_jobs(account_db, *, now_epoch=None, user_id=None) -> list[dict]:
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, "account_deletion_jobs"):
            return []
        sql = """
            SELECT * FROM account_deletion_jobs
            WHERE status IN ('pending', 'running') AND scheduled_at_epoch <= ?
              AND (
                status='pending' OR lease_expires_at_epoch IS NULL
                OR lease_expires_at_epoch <= ?
              )
        """
        args = [now_epoch, now_epoch]
        if user_id is not None:
            sql += " AND user_id = ?"
            args.append(int(user_id))
        sql += " ORDER BY scheduled_at_epoch, job_id"
        return [dict(row) for row in conn.execute(sql, args).fetchall()]


def dry_run_summary(
    *, account_db, sessions_db, vendor_save_root, duel_db, garden_notes_db,
    user_id, now_epoch=None, garden_legacy_db=None
) -> dict:
    """Return counts only; never invoke managed services or mutate a database/file."""
    now_epoch = int(time.time() if now_epoch is None else now_epoch)
    user_id = int(user_id)
    player_ids = _player_ids(user_id)
    summary = {"user_id": user_id, "player_ids": player_ids}
    with sqlite3.connect(account_db) as conn:
        conn.row_factory = sqlite3.Row
        status = deletion_status(conn, user_id, now_epoch=now_epoch)
        summary["deletion"] = status
        counts = {}
        for table, column in (
            ("players", "user_id"),
            ("password_reset_tokens", "user_id"),
            ("ai_access_tokens", "user_id"),
            ("legacy_ai_token_hashes", "user_id"),
            ("account_registration_events", "user_id"),
            ("account_username_changes", "user_id"),
            ("account_emails", "user_id"),
            ("email_verification_codes", "user_id"),
            ("email_verification_attempts", "user_id"),
        ):
            if _table_exists(conn, table):
                counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {column}=?", (user_id,)).fetchone()[0]
                )
        summary["account_db"] = counts
    session_counts = {}
    if Path(sessions_db).is_file():
        with sqlite3.connect(sessions_db) as conn:
            for table in (
                "test_sessions", "test_results", "eco_sessions", "ciyuwu_sessions",
                "sessions_deprecated", "results_deprecated",
            ):
                columns = _columns(conn, table)
                if not columns:
                    continue
                player_clause = f"player_id IN ({_placeholders(player_ids)})"
                args = list(player_ids)
                if "user_id" in columns:
                    clauses = [f"(user_id IS NULL AND {player_clause})", "user_id=?"]
                    args.append(user_id)
                else:
                    clauses = [player_clause]
                session_counts[table] = int(
                    conn.execute(f"SELECT COUNT(*) FROM {table} WHERE " + " OR ".join(clauses), args).fetchone()[0]
                )
    summary["sessions_db"] = session_counts
    file_counts = {}
    root = Path(vendor_save_root)
    if root.is_dir():
        for game_dir in root.iterdir():
            if game_dir.is_dir():
                count = sum(1 for pid in player_ids if (game_dir / pid).is_dir())
                if count:
                    file_counts[game_dir.name] = count
    summary["vendor_save_directories"] = file_counts
    return summary
