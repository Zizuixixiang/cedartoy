#!/usr/bin/env python3
"""Safely reset one claimed guest claim code back to the unclaimed state.

The command is a dry-run unless ``--confirm`` is supplied. It only clears the
three claim-result columns and never changes the code's identity or creation
time.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import server  # noqa: E402


REQUIRED_COLUMNS = {
    "code",
    "guest_player_id",
    "created_at",
    "claimed_by",
    "claimed_at",
    "claimed_slot",
}
SELECT_COLUMNS = (
    "code",
    "guest_player_id",
    "created_at",
    "claimed_by",
    "claimed_at",
    "claimed_slot",
)


class ClaimResetError(RuntimeError):
    """The requested row is missing, unsafe, or already unclaimed."""


def _row_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def _validate_schema(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'guest_claim_codes'"
    ).fetchone()
    if table is None:
        raise ClaimResetError("guest_claim_codes 表不存在；未做任何修改")
    columns = {row[1] for row in conn.execute("PRAGMA table_info(guest_claim_codes)")}
    missing = sorted(REQUIRED_COLUMNS - columns)
    if missing:
        raise ClaimResetError(
            "guest_claim_codes 缺少必要字段：" + ", ".join(missing) + "；未做任何修改"
        )


def _select_exact(
    conn: sqlite3.Connection,
    code: str,
    guest_player_id: str,
) -> dict[str, Any] | None:
    columns = ", ".join(SELECT_COLUMNS)
    return _row_dict(conn.execute(
        f"""
        SELECT {columns}
        FROM guest_claim_codes
        WHERE code = ? AND guest_player_id = ?
        """,
        (code, guest_player_id),
    ).fetchone())


def _project_reset(row: dict[str, Any]) -> dict[str, Any]:
    projected = dict(row)
    projected.update(claimed_by=None, claimed_at=None, claimed_slot=None)
    return projected


def reset_guest_claim(
    code: str,
    guest_player_id: str,
    *,
    confirm: bool = False,
) -> dict[str, Any]:
    """Preview or reset one exact claimed row using the platform DB connection."""
    if not isinstance(code, str) or not code:
        raise ClaimResetError("--code 不能为空；未做任何修改")
    if not isinstance(guest_player_id, str) or not guest_player_id:
        raise ClaimResetError("--guest-player-id 不能为空；未做任何修改")

    conn = server._db_connect()
    try:
        if confirm:
            conn.execute("BEGIN IMMEDIATE")
        else:
            # Enforce read-only behavior at SQLite level as well as in control flow.
            conn.execute("PRAGMA query_only = ON")
        _validate_schema(conn)
        before = _select_exact(conn, code, guest_player_id)
        if before is None:
            raise ClaimResetError(
                "code 与 guest_player_id 没有精确匹配的记录；未做任何修改"
            )
        if before["claimed_by"] is None:
            raise ClaimResetError("该认领码当前已是未认领状态；未做任何修改")

        projected = _project_reset(before)
        if not confirm:
            return {
                "mode": "dry-run",
                "database": str(server.TURTLE_DB_PATH),
                "before": before,
                "after": projected,
                "affected_rows": 0,
            }

        cursor = conn.execute(
            """
            UPDATE guest_claim_codes
            SET claimed_by = NULL, claimed_at = NULL, claimed_slot = NULL
            WHERE code = ? AND guest_player_id = ? AND claimed_by IS NOT NULL
            """,
            (code, guest_player_id),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            raise ClaimResetError(
                f"安全更新预期影响 1 行，实际为 {cursor.rowcount} 行；已回滚"
            )
        after = _select_exact(conn, code, guest_player_id)
        if after != projected:
            conn.rollback()
            raise ClaimResetError("更新后状态校验失败；已回滚")
        conn.commit()
        return {
            "mode": "confirmed",
            "database": str(server.TURTLE_DB_PATH),
            "before": before,
            "after": after,
            "affected_rows": cursor.rowcount,
        }
    except Exception:
        if confirm:
            conn.rollback()
        raise
    finally:
        conn.close()


def _print_result(result: dict[str, Any]) -> None:
    print(f"mode: {result['mode']}")
    print(f"database: {result['database']}")
    print("before: " + json.dumps(result["before"], ensure_ascii=False, sort_keys=True))
    label = "projected after" if result["mode"] == "dry-run" else "after"
    print(f"{label}: " + json.dumps(result["after"], ensure_ascii=False, sort_keys=True))
    print(f"affected row count: {result['affected_rows']}")
    if result["mode"] == "dry-run":
        print("dry-run: no changes made; rerun with --confirm to apply exactly this reset")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", required=True, help="exact claim code to reset")
    parser.add_argument(
        "--guest-player-id",
        required=True,
        help="exact canonical guest player id, for example guest:abc",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="apply the reset; without this flag the command is read-only",
    )
    args = parser.parse_args(argv)
    try:
        result = reset_guest_claim(
            args.code,
            args.guest_player_id,
            confirm=args.confirm,
        )
    except ClaimResetError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    _print_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
