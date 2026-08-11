#!/usr/bin/env python3
"""Purge CedarToy accounts whose complete 72-hour wait has elapsed.

Production usage is intentionally explicit and cron-friendly.  ``--dry-run``
never calls resident services and never mutates files or databases.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import account_deletion  # noqa: E402
import server  # noqa: E402


def _paths():
    return {
        "account_db": server.TURTLE_DB_PATH,
        "sessions_db": server.SESSIONS_DB_PATH,
        "vendor_save_root": server.VENDOR_SAVE_ROOT,
        "duel_db": server.DUEL_DB_PATH,
        "garden_notes_db": server.GARDEN_NOTES_DB_PATH,
        "garden_legacy_db": server.GARDEN_LEGACY_DB_PATH,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--user-id", type=int, help="limit inspection/purge to one user id")
    parser.add_argument("--now-epoch", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    now_epoch = int(args.now_epoch if args.now_epoch is not None else time.time())
    paths = _paths()

    if args.dry_run:
        if args.user_id is None:
            jobs = account_deletion.due_jobs(paths["account_db"], now_epoch=now_epoch)
            print(json.dumps({"due_jobs": len(jobs)}, ensure_ascii=False))
            return 0
        summary = account_deletion.dry_run_summary(
            **paths, user_id=args.user_id, now_epoch=now_epoch
        )
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0

    jobs = account_deletion.due_jobs(
        paths["account_db"], now_epoch=now_epoch, user_id=args.user_id
    )
    failures = 0
    for job in jobs:
        job_id = str(job["job_id"])
        try:
            result = server._purge_account_deletion(
                int(job["user_id"]), now_epoch=now_epoch
            )
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": result.get("status"),
                        "completed_at_epoch": now_epoch,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        except Exception as exc:
            failures += 1
            # Do not persist exception text: upstream HTTP/path errors can contain
            # identifiers.  The durable job phase is enough for diagnosis/retry.
            print(
                json.dumps(
                    {
                        "job_id": job_id,
                        "status": "retry_required",
                        "error_type": type(exc).__name__,
                    },
                    sort_keys=True,
                ),
                file=sys.stderr,
            )
    print(json.dumps({"due": len(jobs), "failed": failures}, sort_keys=True))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
