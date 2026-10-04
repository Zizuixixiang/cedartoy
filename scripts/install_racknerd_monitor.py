#!/usr/bin/env python3
"""Install/remove only the marked RackNerd job, preserving the user's crontab."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "data/racknerd-monitor"
MARKER = "# racknerd-monitor-managed"
JOB = f"0 9,21 * * * /usr/bin/python3 {ROOT}/scripts/racknerd_monitor.py >/dev/null 2>&1 {MARKER}"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--remove", action="store_true")
    args = parser.parse_args()
    os.umask(0o077)
    RUNTIME.mkdir(parents=True, exist_ok=True)
    if not args.remove:
        if Path("/etc/localtime").resolve() != Path("/usr/share/zoneinfo/Asia/Shanghai"):
            sys.exit("Refusing installation: host cron timezone must be Asia/Shanghai.")
        subprocess.run(["/usr/bin/python3", "-m", "unittest", "tests_toy.test_racknerd_monitor"], cwd=ROOT, check=True)
    result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    if result.returncode and "no crontab" not in result.stderr.lower():
        sys.exit("Unable to read crontab; nothing changed.")
    before = result.stdout if result.returncode == 0 else ""
    lines = [line for line in before.splitlines() if not line.rstrip().endswith(MARKER)]
    if not args.remove:
        # Avoid accidentally installing both this user job and the cron.d template.
        if Path("/etc/cron.d/racknerd-monitor").exists():
            sys.exit("/etc/cron.d/racknerd-monitor already exists; refusing duplicate schedule.")
        lines.append(JOB)
    after = "\n".join(lines) + "\n"
    if before == after:
        print("RackNerd cron already matches; unchanged.")
        return
    backup = RUNTIME / ("crontab-before-" + str(time.time_ns()))
    backup.write_text(before)
    result = subprocess.run(["crontab", "-"], input=after, capture_output=True, text=True)
    if result.returncode:
        # crontab stderr is local diagnostics; avoid dumping unrelated cron entries.
        sys.exit("Cron installation failed; existing crontab was not replaced. " + result.stderr.strip())
    verify = subprocess.run(["crontab", "-l"], capture_output=True, text=True, check=True).stdout
    if verify != after:
        sys.exit("Cron readback differs; inspect crontab before proceeding.")
    print("RackNerd cron removed." if args.remove else "RackNerd cron installed: 09:00 and 21:00 Asia/Shanghai.")


if __name__ == "__main__":
    main()
