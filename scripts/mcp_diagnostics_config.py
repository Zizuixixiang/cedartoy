#!/usr/bin/env python3
"""Local operator helper. Never called by an MCP tool or service request."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cedar_backend.mcp_diagnostics import ROOT, read_config, validate_configuration


def update(record, action, account_ids, *, now, days=7):
    if type(days) is not int or not 1 <= days <= 30:
        raise ValueError("days must be an integer between 1 and 30")
    if len(set(account_ids)) != len(account_ids):
        raise ValueError("duplicate account IDs")
    if any(type(account) is not int or not 0 < account < 2**63 for account in account_ids):
        raise ValueError("account IDs must be positive integers below 2^63")
    if action == "set":
        result = {"mode": "allowlist", "enabled": True, "accounts": []}
    else:
        validate_configuration(record)
        if record["mode"] != "allowlist":
            raise ValueError("use set with ALL desired IDs to migrate a single-account configuration")
        result = {**record, "accounts": [dict(entry) for entry in record["accounts"]]}
    accounts = {entry["account_id"]: entry["expires_at"] for entry in result["accounts"]}
    for account in account_ids:
        if action == "add" and account in accounts:
            raise ValueError("account already exists; use renew explicitly")
        if action in ("renew", "remove") and account not in accounts:
            raise ValueError("account is not in the allowlist")
        if action == "remove":
            del accounts[account]
        else:
            accounts[account] = now + days * 86400
    result["accounts"] = [{"account_id": account, "expires_at": until}
                          for account, until in sorted(accounts.items())]
    validate_configuration(result)
    return result


def atomic_write(path, record):
    validate_configuration(record)
    fd, temporary = tempfile.mkstemp(prefix=".mcp-diagnostics-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as output:
            json.dump(record, output, ensure_ascii=True, separators=(",", ":"))
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=ROOT / "data/mcp_diagnostics_config.json")
    parser.add_argument("--days", type=int, default=7, help="new/renewed grant, 1–30 days from now (default 7)")
    parser.add_argument("action", choices=("show", "set", "add", "remove", "renew"))
    parser.add_argument("account_ids", nargs="*", type=int)
    args = parser.parse_args(argv)
    try:
        if args.action == "show":
            if args.account_ids:
                raise ValueError("show takes no account IDs")
            record = read_config(args.config)
            validate_configuration(record)
        else:
            if not args.account_ids:
                raise ValueError("at least one account ID is required")
            # Serialize this helper's read-modify-replace; services never take
            # this lock. Manual editors must also avoid concurrent changes.
            with os.fdopen(os.open(str(args.config) + ".lock", os.O_CREAT | os.O_RDWR, 0o600), "w") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX)
                record = None if args.action == "set" else read_config(args.config)
                record = update(record, args.action, args.account_ids, now=int(time.time()), days=args.days)
                atomic_write(args.config, record)
        print(json.dumps(record, indent=2))
    except (OSError, ValueError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
