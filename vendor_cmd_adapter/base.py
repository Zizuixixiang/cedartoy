import fcntl
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from command_text import normalize_command_spaces


ROOT_DIR = Path(__file__).resolve().parent.parent
SAVE_ROOT = ROOT_DIR / "data" / "vendor_saves"
# 允许平台身份层注入的前缀 id：账号玩家=纯数字账号 id 或 id:slot，游客=guest:xxx（作目录名，Linux 下冒号合法）。
PLAYER_ID_RE = re.compile(r"^(?:guest:[a-zA-Z0-9]{1,64}|[a-zA-Z0-9]{1,64}(?::[1-5])?)$")


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
            # 归一化 Unicode 空白：vendor 引擎普遍按 ASCII 空格切指令，
            # 全角空格会让 startswith("买 ") 这类匹配直接落空。
            "command": normalize_command_spaces(str(command or "")),
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
        raise VendorCmdError("player_id 只能包含 1-64 位字母数字，账号存档槽可使用 id:2 到 id:5")
    return value


def require_save_confirm(arguments, has_save_fn, summary_fn=None, game_name=""):
    """重开覆盖存档拦截：有存档且未 confirm=true 时拒绝执行。

    参数:
        arguments: play() 的原始参数字典，从 arguments["confirm"] 读取确认标志。
        has_save_fn: () -> bool，检测该玩家是否有存档文件。
        summary_fn: (player_id) -> dict|None，可选，读存档摘要用于提示文案。
        game_name: 提示文案中的游戏名。
    """
    if not has_save_fn():
        return
    if str(arguments.get("confirm", "")).lower() == "true":
        return
    # 尝试读取摘要信息，丰富提示文案
    detail = ""
    if summary_fn:
        try:
            player_id = arguments.get("player_id")
            info = summary_fn(player_id)
        except Exception:
            info = None
        if isinstance(info, dict) and info:
            parts = []
            if "turn" in info:
                parts.append(f"第{info['turn']}回合")
            if "day" in info:
                parts.append(f"第{info['day']}天")
            if "week" in info:
                parts.append(f"第{info['week']}周")
            if "level" in info:
                parts.append(f"关卡{info['level']}")
            if "chips" in info:
                parts.append(f"筹码{info['chips']}")
            if "points" in info:
                parts.append(f"点数{info['points']}")
            if "total_casts" in info:
                parts.append(f"抛竿{info['total_casts']}次")
            if "coins" in info:
                parts.append(f"金币{info['coins']}")
            if "cash" in info:
                parts.append(f"现金{info['cash']}")
            if "budget" in info:
                parts.append(f"预算{info['budget']}")
            if "spent" in info:
                parts.append(f"已花{info['spent']}")
            if "reputation" in info:
                parts.append(f"口碑{info['reputation']}")
            if "encyclopedia" in info:
                parts.append(f"图鉴{info['encyclopedia']}种")
            if "levels" in info and isinstance(info["levels"], dict):
                parts.append(f"已通关{len(info['levels'])}关")
            if parts:
                detail = "（" + "，".join(parts) + "）"
    if not detail:
        detail = ""
    raise VendorCmdError(
        f"检测到已有存档{detail}，此操作将永久覆盖且无法恢复。"
        f"确认重开请在参数中加 confirm=true"
    )
