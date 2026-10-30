"""Repair the mislabeled custom room without changing the puzzle or its answer.

Run with the verified service database and a new SQLite snapshot path:
    python3 scripts/fix_turtle_room_daily_missing.py --db /actual/soup.db --backup /new/snapshot.db

The original pending submission is retained as the source record. This is a
one-room data correction, not a parser for arbitrary player-authored text.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


ROOM_ID = "vapgf4rK"
TITLE = "每天少一个"
SURFACE = """男人每天回家后，都会数一遍家里的人。

第一天，五个。
第二天，四个。
第三天，三个。
第四天，两个。

第五天，他没有再数。

他笑着锁上门，终于安心睡了一觉。

为什么？"""
OLD_SURFACE = f"标题】{TITLE}\n\n【汤面】\n\n{SURFACE}"


def apply_correction(db: sqlite3.Connection) -> bool:
    """Accept only the observed source or repaired state; update atomically."""
    with db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT puzzle_id, title, surface FROM rooms WHERE id = ?", (ROOM_ID,)
        ).fetchone()
        if row is None:
            raise ValueError("Expected custom room is missing; no changes applied")
        if tuple(row) == (None, TITLE, SURFACE):
            return False
        if tuple(row) != (None, "", OLD_SURFACE):
            raise ValueError("Unexpected room content; no changes applied")
        db.execute(
            "UPDATE rooms SET title = ?, surface = ? WHERE id = ?",
            (TITLE, SURFACE, ROOM_ID),
        )
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    # Never create a database accidentally or overwrite an existing backup.
    with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=rw", uri=True)) as db:
        with args.backup.open("xb"):
            pass
        with closing(sqlite3.connect(args.backup)) as snapshot:
            db.backup(snapshot)
        changed = apply_correction(db)
    print(f"Room {ROOM_ID}: {'updated' if changed else 'already repaired'}; backup: {args.backup}")


if __name__ == "__main__":
    main()
