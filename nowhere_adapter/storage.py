"""One atomic envelope contains ALL upstream JSON files and private images.

Workers materialize it in disposable, process-private homes. Stable external
locks survive delete/claim; crashes never publish a partial multi-file save.
"""
import base64
from contextlib import contextmanager, ExitStack
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from vendor_cmd_adapter.base import VendorCmdError, require_player_id, parse_import_save_data

ROOT = Path(os.environ.get("NOWHERE_SAVE_ROOT", Path(__file__).resolve().parents[1] / "data/vendor_saves/nowhere"))
VERSION = "f0803c1053e5f97a6f44bab26e15043d71825ec7"
SAVE_NAME = "save.json"
FILE_RE = re.compile(r"(?:[a-z_]+\.json|journeys/[\w\-]+\.json)\Z")
IMAGE_RE = re.compile(r"postcards/card_[0-9]+\.png\Z")


@contextmanager
def locked(*players):
    # Kept outside the game directory: stats and guest cleanup must not count it.
    lock_root = ROOT.parent / ".nowhere-locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    with ExitStack() as stack:
        for player in sorted(set(players)):
            player = require_player_id(player)
            fd = stack.enter_context((lock_root / player).open("a"))
            fcntl.flock(fd, fcntl.LOCK_EX)
        yield


def validate(raw):
    data = parse_import_save_data(raw)
    if data.get("format") != "cedartoy.nowhere.v1" or data.get("upstream") != VERSION:
        raise VendorCmdError("乌有乡存档格式或上游版本不兼容")
    files = data.get("files")
    if not isinstance(files, dict) or "journey.json" not in files:
        raise VendorCmdError("缺少完整旅程存档")
    for name, value in files.items():
        expected = list if name == "marks.json" else dict
        if not FILE_RE.fullmatch(name) or not isinstance(value, expected):
            raise VendorCmdError("存档包含非法文件或结构")
        if name in {"travelers.json", "travelers_archive.json", "cotraveler_messages.json"}:
            raise VendorCmdError("同游共享数据不能导入私人存档")
        if name == "journey.json" or (name.startswith("journeys/") and name != "journeys/index.json"):
            if value.get("save_version") != 1 or not isinstance(value.get("path", []), list):
                raise VendorCmdError("旅程结构不合法")
            slug = value.get("journey_slug")
            if slug is not None and (not isinstance(slug, str) or not re.fullmatch(r"[\w\-]+", slug)):
                raise VendorCmdError("旅程 slug 不合法")
        cards = value.get("items", []) if name == "postcards.json" else value.get("postcards", []) if isinstance(value, dict) else []
        if not isinstance(cards, list):
            raise VendorCmdError("明信片列表不合法")
        for card in cards:
            if not isinstance(card, dict) or type(card.get("id")) is not int or card["id"] < 0:
                raise VendorCmdError("明信片编号不合法")
            if not isinstance(card.get("stamp", {}), dict):
                raise VendorCmdError("明信片邮戳不合法")
    index = files.get("journeys/index.json", {})
    if not isinstance(index.get("journeys", []), list):
        raise VendorCmdError("旅程索引不合法")
    for entry in index.get("journeys", []):
        if not isinstance(entry, dict) or not re.fullmatch(r"[\w\-]+", str(entry.get("slug", ""))):
            raise VendorCmdError("旅程索引不合法")
        if f"journeys/{entry['slug']}.json" not in files:
            raise VendorCmdError("旅程索引引用了缺失的文件")
    images = data.get("images", {})
    if not isinstance(images, dict) or not isinstance(data.get("runtime"), dict):
        raise VendorCmdError("缺少运行状态或图像结构错误")
    if not isinstance(data.get("generation"), str):
        raise VendorCmdError("缺少存档代次")
    runtime = data["runtime"]
    if not isinstance(runtime.get("metadata", {}), dict):
        raise VendorCmdError("运行状态 metadata 不合法")
    meta = runtime.get("metadata", {})
    if meta.get("cotraveler", "1") not in {"0", "1", "quiet"} or not isinstance(meta.get("traveler_name", ""), str):
        raise VendorCmdError("同游设置不合法")
    for name, value in images.items():
        if not IMAGE_RE.fullmatch(name) or not isinstance(value, str):
            raise VendorCmdError("非法明信片图片")
        try:
            decoded = base64.b64decode(value, validate=True)
            if not decoded.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError()
        except ValueError:
            raise VendorCmdError("明信片图片不是 PNG") from None
    return data


def read(player):
    path = ROOT / require_player_id(player) / SAVE_NAME
    if not path.exists():
        return None
    try:
        return validate(path.read_text(encoding="utf-8"))
    except (ValueError, TypeError, KeyError, AttributeError, VendorCmdError) as exc:
        # Preserve original AND a unique evidence copy; every future access fails
        # closed until an explicitly confirmed replacement or repair.
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
        backup = path.with_name("save.corrupt-" + digest + ".json")
        if not backup.exists():
            shutil.copyfile(path, backup)
        raise VendorCmdError("乌有乡存档损坏，原档及证据副本已保留；拒绝自动重建") from exc


def write(player, data):
    data = validate(data)
    directory = ROOT / require_player_id(player)
    directory.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".save-", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(data, stream, ensure_ascii=False, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, directory / SAVE_NAME)
        fd = os.open(directory, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def delete(player):
    with locked(player):
        from .pool import POOL
        POOL.discard(player)
        directory = ROOT / require_player_id(player)
        existed = directory.exists()
        if existed:
            shutil.rmtree(directory)
        from .shared import forget
        forget(player)
        return existed


def migrate(old, new):
    with locked(old, new):
        source, target = ROOT / old, ROOT / new
        if target.exists():
            raise VendorCmdError("目标乌有乡槽位已有数据")
        # A worker never owns authoritative data. A later call reloads the
        # renamed envelope. Delete shared guest traces instead of exposing ID.
        source.rename(target)
        from .pool import POOL
        POOL.discard(old)
        from .shared import forget
        forget(old)


def lock_platform_claim(function):
    """Hold Nowhere locks through the platform's all-game claim + rollback."""
    @functools.wraps(function)
    def wrapped(old_player_id, user_id, slot=1):
        if not (ROOT / require_player_id(old_player_id)).exists():
            return function(old_player_id, user_id, slot=slot)
        target = str(user_id) if slot == 1 else f"{user_id}:{slot}"
        with locked(old_player_id, target):
            result = function(old_player_id, user_id, slot=slot)
            from .pool import POOL
            POOL.discard(old_player_id)
            from .shared import forget
            forget(old_player_id)
            return result
    return wrapped
