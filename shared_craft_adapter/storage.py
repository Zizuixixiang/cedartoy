"""Room-wide reentrant flock and fail-closed, atomic JSON persistence."""
import fcntl
import hashlib
import json
import os
from pathlib import Path
import threading
import tempfile


class RoomLock:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.thread_lock = threading.RLock()
        self.local = threading.local()

    def __enter__(self):
        self.thread_lock.acquire()
        try:
            depth = getattr(self.local, "depth", 0)
            if depth == 0:
                self.local.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
                fcntl.flock(self.local.fd, fcntl.LOCK_EX)
            self.local.depth = depth + 1
            return self
        except BaseException:
            self.thread_lock.release()
            raise

    def __exit__(self, *args):
        self.local.depth -= 1
        if not self.local.depth:
            os.close(self.local.fd)
        self.thread_lock.release()


def atomic_bytes(path, raw):
    path = Path(path)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_json(path, kind):
    raw = path.read_bytes()  # IO failures propagate, never become an empty save.
    try:
        data = json.loads(raw)
        assert isinstance(data, dict)
        field = {"canvas": "strokes", "beads": "boards", "gallery": "items",
                 "participants": "participants"}[kind]
        assert isinstance(data[field], list)
        assert all(isinstance(item, dict) for item in data[field])
        if kind == "canvas":
            assert isinstance(data["revision"], int)
            assert 0 < data["width"] <= 4096 and 0 < data["height"] <= 4096
            assert isinstance(data["background"], str)
            for stroke in data[field]:
                assert isinstance(stroke["points"], list) and isinstance(stroke["actor"], str)
        elif kind == "beads":
            for board in data[field]:
                assert board["mode"] in {"together", "separate", "gift"}
                assert isinstance(board["cells"], dict) and isinstance(board["inventories"], dict)
                assert isinstance(board["id"], str) and isinstance(board["owner"], str)
        elif kind == "gallery":
            for item in data[field]:
                assert isinstance(item["id"], str) and isinstance(item["authors"], list)
        else:
            for item in data[field]:
                assert isinstance(item["id"], str) and item["id"].isdigit()
                assert isinstance(item["display_name"], str)
        return data
    except (ValueError, AssertionError, KeyError, TypeError):
        backup = path.with_name(path.name + ".corrupt-" + hashlib.sha256(raw).hexdigest()[:16])
        if not backup.exists():
            atomic_bytes(backup, raw)
        raise ValueError("存档损坏，原文件及备份已保留；请联系管理员恢复") from None


def protect_store(store, kind, lock):
    store.lock = lock
    original_load = store._load

    def load():
        read_json(store.path, kind)
        # Preserve upstream bead migration/normalisation after strict validation.
        return original_load()

    def save(data):
        # Upstream already replaces PNGs atomically. Flush them before publishing
        # metadata that makes those snapshots visible after a power loss.
        images = []
        if kind == "gallery":
            images = [store.image_dir / (item["id"] + suffix)
                      for item in data["items"] for suffix in (".png", "-display.png")]
        elif kind == "beads":
            images = [store.path.with_name("shared-bead-finished") / (board["id"] + suffix)
                      for board in data["boards"] if board.get("craft", {}).get("decorated")
                      for suffix in (".png", "-piece.png")]
        for image in images:
            if image.exists():
                with image.open("rb") as stream:
                    os.fsync(stream.fileno())
        for directory in {image.parent for image in images if image.exists()}:
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        atomic_bytes(store.path, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    store._load = load
    store._save = save
