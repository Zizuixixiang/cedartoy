"""Apply only the title/rule-label corrections, after a SQLite backup.

Run at deployment time with the service's verified database path:
    python3 scripts/fix_turtle_puzzles_52_112.py --db /actual/turtle_soup.db --backup /new/snapshot.db
The backup path must not already exist. No service restart is performed.
"""
import argparse
from contextlib import closing
from pathlib import Path
import sqlite3


OLD_RULE_LABEL = "2、4、6、8 为红字规则"
NEW_RULE_LABEL = "2、4、6、8、10 为红字规则"


def apply_corrections(db: sqlite3.Connection) -> list[int]:
    """Validate and update both puzzles atomically; a second run is a no-op."""
    changed = []
    with db:
        db.execute("BEGIN IMMEDIATE")
        rows = {
            row[0]: row[1:]
            for row in db.execute("SELECT id, title, surface FROM puzzles WHERE id IN (52, 112)")
        }
        if set(rows) != {52, 112}:
            raise ValueError("Expected puzzle IDs 52 and 112; no changes applied")
        title, _ = rows[52]
        if title not in ("蚊子", "吃饱之后"):
            raise ValueError("Puzzle 52 has an unexpected title; no changes applied")
        if title == "蚊子":
            db.execute("UPDATE puzzles SET title = ? WHERE id = 52", ("吃饱之后",))
            changed.append(52)

        title, surface = rows[112]
        if title != "白雪公主规则怪谈":
            raise ValueError("Puzzle 112 has an unexpected title; no changes applied")
        if surface.count(OLD_RULE_LABEL) == 1 and NEW_RULE_LABEL not in surface:
            db.execute(
                "UPDATE puzzles SET surface = ? WHERE id = 112",
                (surface.replace(OLD_RULE_LABEL, NEW_RULE_LABEL, 1),),
            )
            changed.append(112)
        elif surface.count(NEW_RULE_LABEL) != 1 or OLD_RULE_LABEL in surface:
            raise ValueError("Puzzle 112 has an unexpected rule label; no changes applied")
    return changed


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--backup", required=True, type=Path)
    args = parser.parse_args()
    # mode=rw prevents silently creating a database at a mistaken path.
    with closing(sqlite3.connect(args.db.resolve().as_uri() + "?mode=rw", uri=True)) as db:
        with args.backup.open("xb"):
            pass
        with closing(sqlite3.connect(args.backup)) as snapshot:
            db.backup(snapshot)
        changed = apply_corrections(db)
    print(f"Updated puzzle IDs: {changed}; backup: {args.backup}")


if __name__ == "__main__":
    main()
