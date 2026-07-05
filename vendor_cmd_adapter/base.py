import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parent.parent
SAVE_ROOT = ROOT_DIR / "data" / "vendor_saves"
PLAYER_ID_RE = re.compile(r"^[a-zA-Z0-9]{1,64}$")


class VendorCmdError(Exception):
    pass


class VendorCmdGame:
    def __init__(self, name, vendor_dir, runner_code, timeout=30):
        self.name = name
        self.vendor_dir = ROOT_DIR / vendor_dir
        self.runner_code = runner_code
        self.timeout = timeout

    def run(self, player_id, command, *, reset=False, extra=None):
        player_id = require_player_id(player_id)
        save_dir = SAVE_ROOT / self.name / player_id
        save_dir.mkdir(parents=True, exist_ok=True)
        lock_path = save_dir / ".lock"

        payload = {
            "command": str(command or ""),
            "reset": bool(reset),
            "save_dir": str(save_dir),
            "vendor_dir": str(self.vendor_dir),
            "extra": extra or {},
        }
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"

        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            proc = subprocess.run(
                [sys.executable, "-c", self.runner_code],
                input=json.dumps(payload, ensure_ascii=False),
                text=True,
                capture_output=True,
                cwd=str(save_dir),
                env=env,
                timeout=self.timeout,
                check=False,
            )

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()
            raise VendorCmdError(detail or f"{self.name} exited with code {proc.returncode}")
        return proc.stdout.rstrip("\n")


def require_player_id(value):
    if not isinstance(value, str):
        raise VendorCmdError("player_id 必须是字符串")
    value = value.strip()
    if not PLAYER_ID_RE.fullmatch(value):
        raise VendorCmdError("player_id 只能包含 1-64 位字母数字")
    return value
