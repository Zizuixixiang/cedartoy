#!/usr/bin/env python3
"""Check by default; use --apply only on an explicitly chosen offline database."""
import argparse
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from deeptalk.catalog import DeeptalkError
from deeptalk.migrate import migrate


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Explicit existing SQLite file; no environment/default DB")
    parser.add_argument("--apply", action="store_true", help="Encrypt legacy fields atomically; stop writers first")
    args = parser.parse_args()
    try:
        print(json.dumps(migrate(args.db, apply=args.apply)))
    except (DeeptalkError, sqlite3.Error, OSError, ValueError):
        print("Deep Talk encryption check/migration failed; no migration changes committed. Check key, schema and data on an offline copy.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
