#!/usr/bin/env python3
"""Delete inactive guest saves and invalidate their claim codes.

Defaults to deleting guest saves whose last activity, or vendor save directory
mtime, is older than 180 days. Use --dry-run to list matches without deleting.
"""
from __future__ import annotations

import argparse
import os
import shutil
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
SESSIONS_DB = Path(os.getenv("SESSIONS_DB", ROOT / "data" / "sessions.db"))
PLATFORM_DB = Path(os.getenv("TURTLE_SOUP_DB", ROOT / "turtle-soup" / "backend" / "turtle_soup.db"))
VENDOR_SAVE_ROOT = Path(os.getenv("VENDOR_SAVE_ROOT", ROOT / "data" / "vendor_saves"))
GUEST_PREFIX = "guest:"


@dataclass(frozen=True)
class SaveTableSpec:
    table: str
    timestamp_column: str
    timestamp_kind: str


SAVE_TABLE_SPECS = (
    SaveTableSpec("eco_sessions", "last_active", "text"),
    SaveTableSpec("ciyuwu_sessions", "last_active", "text"),
    SaveTableSpec("test_sessions", "last_active", "epoch"),
    SaveTableSpec("test_results", "completed_at", "epoch"),
)


def connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return bool(conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone())


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}


def iso_cutoff(days: int) -> str:
    return (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")


def epoch_cutoff(days: int) -> float:
    return time.time() - days * 24 * 60 * 60


def collect_table_rows(days: int) -> list[dict]:
    rows: list[dict] = []
    if not SESSIONS_DB.exists():
        return rows
    cutoff_text = iso_cutoff(days)
    cutoff_epoch = epoch_cutoff(days)
    with connect(SESSIONS_DB) as conn:
        for spec in SAVE_TABLE_SPECS:
            if not table_exists(conn, spec.table):
                continue
            columns = table_columns(conn, spec.table)
            if "player_id" not in columns or spec.timestamp_column not in columns:
                continue
            cutoff = cutoff_epoch if spec.timestamp_kind == "epoch" else cutoff_text
            for row in conn.execute(
                f"""
                SELECT rowid, player_id, {spec.timestamp_column} AS last_seen
                FROM {spec.table}
                WHERE player_id LIKE ? AND {spec.timestamp_column} IS NOT NULL
                  AND {spec.timestamp_column} < ?
                """,
                (f"{GUEST_PREFIX}%", cutoff),
            ):
                rows.append({
                    "table": spec.table,
                    "rowid": row["rowid"],
                    "player_id": row["player_id"],
                    "last_seen": row["last_seen"],
                })
    return rows


def collect_vendor_dirs(days: int) -> list[dict]:
    rows: list[dict] = []
    if not VENDOR_SAVE_ROOT.is_dir():
        return rows
    cutoff = epoch_cutoff(days)
    for game_dir in sorted(VENDOR_SAVE_ROOT.iterdir()):
        if not game_dir.is_dir():
            continue
        for player_dir in sorted(game_dir.glob(f"{GUEST_PREFIX}*")):
            if not player_dir.is_dir():
                continue
            mtime = player_dir.stat().st_mtime
            if mtime < cutoff:
                rows.append({
                    "game": game_dir.name,
                    "player_id": player_dir.name,
                    "path": player_dir,
                    "last_seen": datetime.fromtimestamp(mtime).strftime("%Y-%m-%d %H:%M:%S"),
                })
    return rows


def delete_table_rows(rows: list[dict]) -> int:
    if not rows or not SESSIONS_DB.exists():
        return 0
    deleted = 0
    with connect(SESSIONS_DB) as conn:
        for row in rows:
            cur = conn.execute(
                f"DELETE FROM {row['table']} WHERE rowid = ?",
                (row["rowid"],),
            )
            deleted += cur.rowcount
        conn.commit()
    return deleted


def delete_vendor_dirs(rows: list[dict]) -> int:
    deleted = 0
    for row in rows:
        path = row["path"]
        if path.is_dir():
            shutil.rmtree(path)
            deleted += 1
    return deleted


def delete_claim_codes(player_ids: set[str]) -> int:
    if not player_ids or not PLATFORM_DB.exists():
        return 0
    with connect(PLATFORM_DB) as conn:
        if not table_exists(conn, "guest_claim_codes"):
            return 0
        placeholders = ",".join("?" for _ in player_ids)
        cur = conn.execute(
            f"DELETE FROM guest_claim_codes WHERE guest_player_id IN ({placeholders})",
            tuple(sorted(player_ids)),
        )
        conn.commit()
        return cur.rowcount


def print_plan(table_rows: list[dict], vendor_dirs: list[dict], claim_ids: set[str]) -> None:
    print(f"database rows: {len(table_rows)}")
    for row in table_rows:
        print(f"  {row['table']} rowid={row['rowid']} player_id={row['player_id']} last_seen={row['last_seen']}")
    print(f"vendor directories: {len(vendor_dirs)}")
    for row in vendor_dirs:
        print(f"  vendor_saves/{row['game']}/{row['player_id']} last_seen={row['last_seen']}")
    print(f"claim codes to invalidate: {len(claim_ids)}")
    for player_id in sorted(claim_ids):
        print(f"  {player_id}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--days", type=int, default=180, help="inactive days threshold (default: 180)")
    parser.add_argument("--dry-run", action="store_true", help="list matches without deleting")
    args = parser.parse_args()

    if args.days <= 0:
        parser.error("--days must be positive")

    table_rows = collect_table_rows(args.days)
    vendor_dirs = collect_vendor_dirs(args.days)
    claim_ids = {row["player_id"] for row in table_rows}
    claim_ids.update(row["player_id"] for row in vendor_dirs)

    print(f"guest save cleanup threshold: {args.days} days")
    print_plan(table_rows, vendor_dirs, claim_ids)

    if args.dry_run:
        print("dry-run: no changes made")
        return 0

    deleted_rows = delete_table_rows(table_rows)
    deleted_dirs = delete_vendor_dirs(vendor_dirs)
    deleted_codes = delete_claim_codes(claim_ids)
    print(f"deleted database rows: {deleted_rows}")
    print(f"deleted vendor directories: {deleted_dirs}")
    print(f"invalidated claim codes: {deleted_codes}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
