#!/usr/bin/env python3
"""Single-process, locked, loopback-only AutoChess launcher. Never run during review."""
import fcntl
import os
from pathlib import Path
import re
import shutil
import subprocess


def main():
    root = Path(__file__).resolve().parents[1]
    data = Path(os.environ.get('CHESS_ROOM_DATA', ''))
    if not data.is_absolute() or not re.fullmatch('[a-f0-9]{64}', os.environ.get('CHESS_PLATFORM_SECRET', '')):
        raise SystemExit('Set absolute CHESS_ROOM_DATA and a random 64-character hex CHESS_PLATFORM_SECRET')
    if os.environ.get('CHESS_CEDAR_MODE') != 'platform' and os.environ.get('CHESS_SLIM', '1') != '1':
        raise SystemExit('Use CHESS_CEDAR_MODE=platform or legacy CHESS_SLIM=1')
    node = shutil.which('node')
    if not node:
        raise SystemExit('Node >=22.13 required')
    version = subprocess.check_output([node, '--version'], text=True).strip().lstrip('v')
    if tuple(map(int, version.split('.')[:2])) < (22, 13):
        raise SystemExit('Node >=22.13 required')
    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(data / '.cedartoy.lock', os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit('AutoChess data directory already in use') from None
    os.set_inheritable(fd, True)
    os.environ['CHESS_SLIM'] = '1'
    os.chdir(root / 'vendor/autochess')
    os.execv(node, [node, '--import', 'tsx', str(root / 'vendor/autochess/scripts/cedartoy-server.ts')])


if __name__ == '__main__':
    main()
