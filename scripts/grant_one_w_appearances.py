#!/usr/bin/env python3
"""Immediate 1W backfill, optionally watching until Beijing 2026-09-26 midnight."""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import avatar_appearances


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--db", type=Path,
        default=Path(os.environ.get("TURTLE_SOUP_DB", ROOT / "turtle-soup/backend/turtle_soup.db")),
    )
    parser.add_argument("--watch", action="store_true", help="poll every 10s until the fixed deadline, then exit")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    if args.watch:
        avatar_appearances.watch_one_w(args.db)
    else:
        print(json.dumps(avatar_appearances.reconcile_one_w(args.db)))


if __name__ == "__main__":
    main()
