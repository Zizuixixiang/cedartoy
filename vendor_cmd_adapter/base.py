import fcntl
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from command_text import normalize_command_spaces


ROOT_DIR = Path(__file__).resolve().parent.parent
SAVE_ROOT = ROOT_DIR / "data" / "vendor_saves"
# 允许平台身份层注入的前缀 id：账号玩家=纯数字账号 id 或 id:slot，游客=guest:xxx（作目录名，Linux 下冒号合法）。
PLAYER_ID_RE = re.compile(r"^(?:guest:[a-zA-Z0-9]{1,64}|[a-zA-Z0-9]{1,64}(?::[1-5])?)$")
MAX_IMPORT_SAVE_BYTES = 32 * 1024 * 1024


class VendorCmdError(Exception):
    pass


def _reject_json_constant(value):
    raise ValueError(f"不允许非标准 JSON 常量：{value}")


def _validate_json_types(value):
    if value is None or isinstance(value, (bool, str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("JSON 数字必须是有限值")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_types(item)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError("JSON 对象的 key 必须是字符串")
            _validate_json_types(item)
        return
    raise ValueError(f"不支持的 JSON 类型：{type(value).__name__}")


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


def parse_import_save_data(raw):
    """把导入参数严格解析为 JSON 对象，并限制异常大的请求。"""
    if raw is None:
        raise VendorCmdError("save_data 必填")
    if isinstance(raw, str):
        try:
            data = json.loads(raw, parse_constant=_reject_json_constant)
        except (json.JSONDecodeError, ValueError):
            raise VendorCmdError("save_data 不是合法 JSON 字符串") from None
    elif isinstance(raw, dict):
        data = raw
    else:
        raise VendorCmdError("save_data 必须是 JSON 对象或 JSON 字符串")
    if not isinstance(data, dict):
        raise VendorCmdError("save_data 必须是 JSON 对象")
    try:
        _validate_json_types(data)
        serialized = json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        size = len(serialized.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, RecursionError):
        raise VendorCmdError("save_data 包含无法序列化为 JSON 的内容") from None
    if size > MAX_IMPORT_SAVE_BYTES:
        raise VendorCmdError("save_data 序列化后超过 32MB")
    return data


def _expected_json_label(expected):
    if expected is dict:
        return "JSON 对象"
    if expected is list:
        return "JSON 数组"
    if isinstance(expected, tuple):
        labels = []
        if dict in expected:
            labels.append("JSON 对象")
        if list in expected:
            labels.append("JSON 数组")
        if labels:
            return "或".join(labels)
    return "指定的 JSON 结构"


def _validate_save_value(filename, value, expected_types):
    expected = expected_types.get(filename, dict)
    if not isinstance(value, expected):
        raise VendorCmdError(
            f"{filename} 的内容必须是{_expected_json_label(expected)}"
        )


def export_json_saves(
    game_name,
    player_id,
    files,
    *,
    packaged=False,
    expected_types=None,
):
    """在游戏锁内读取存档；多文件存档按导出文件名打包。"""
    player_id = require_player_id(player_id)
    save_dir = SAVE_ROOT / game_name / player_id
    candidates = {
        filename: save_dir / relative_path
        for filename, relative_path in files.items()
    }
    if not any(path.is_file() for path in candidates.values()):
        raise VendorCmdError(f"{game_name} 没有可导出的存档")

    expected_types = expected_types or {}
    archive = {}
    lock_path = save_dir / ".lock"
    with lock_path.open("w", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        for filename, path in candidates.items():
            if not path.is_file():
                continue
            try:
                value = json.loads(
                    path.read_text(encoding="utf-8"),
                    parse_constant=_reject_json_constant,
                )
            except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
                raise VendorCmdError(f"{filename} 不是可导出的合法 JSON 存档") from None
            _validate_save_value(filename, value, expected_types)
            archive[filename] = value

    if not archive:
        raise VendorCmdError(f"{game_name} 没有可导出的存档")
    value = archive if packaged else next(iter(archive.values()))
    return json.dumps(value, ensure_ascii=False, indent=2)


def import_json_saves(
    game_name,
    player_id,
    raw,
    files,
    *,
    packaged=False,
    expected_types=None,
):
    """校验并在游戏锁内原子替换单文件或多文件 JSON 存档。"""
    player_id = require_player_id(player_id)
    data = parse_import_save_data(raw)
    expected_types = expected_types or {}

    if packaged:
        unknown = sorted(set(data) - set(files))
        if unknown:
            raise VendorCmdError(f"save_data 包含未知存档文件：{', '.join(unknown)}")
        if not data:
            raise VendorCmdError("save_data 存档包不能为空")
        archive = data
    else:
        if len(files) != 1:
            raise VendorCmdError("单文件存档配置错误")
        archive = {next(iter(files)): data}

    for filename, value in archive.items():
        _validate_save_value(filename, value, expected_types)

    save_dir = SAVE_ROOT / game_name / player_id
    save_dir.mkdir(parents=True, exist_ok=True)
    targets = {
        filename: save_dir / relative_path
        for filename, relative_path in files.items()
    }
    lock_path = save_dir / ".lock"
    staged = {}
    try:
        with lock_path.open("w", encoding="utf-8") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            for filename, value in archive.items():
                target = targets[filename]
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temp_name = tempfile.mkstemp(
                    prefix=f".{target.name}.import-",
                    dir=str(target.parent),
                )
                staged[filename] = Path(temp_name)
                with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
                    json.dump(
                        value,
                        temp_file,
                        ensure_ascii=False,
                        indent=2,
                        allow_nan=False,
                    )

            for filename, temp_path in staged.items():
                os.replace(temp_path, targets[filename])
            staged.clear()

            if packaged:
                for filename, target in targets.items():
                    if filename not in archive:
                        try:
                            target.unlink()
                        except FileNotFoundError:
                            pass
    finally:
        for temp_path in staged.values():
            try:
                temp_path.unlink()
            except FileNotFoundError:
                pass
    return "存档导入成功"


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
