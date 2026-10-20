#!/usr/bin/env python3
"""Private shared-craft phase-one backup/backfill. Never deletes any source/object."""
import argparse
from contextlib import closing, contextmanager
import fcntl
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from shared_craft_adapter.object_storage import (
    BUCKET, PREFIX, ROOM, MAX_IMAGE, S3, digest, ensure_object, file_identity, object_key,
    verified_get,
)


def source_root(path):
    path = Path(path).absolute()
    if not path.is_dir() or path.is_symlink():
        raise ValueError("source must be an existing non-symlink craft data directory")
    rooms = path / "rooms"
    if rooms.is_symlink():
        raise ValueError("symlink rooms directory")
    return path


def room_folders(root, selected=None):
    folders = root / "rooms"
    if not folders.exists():
        return
    for folder in sorted(folders.iterdir()):
        if not re.fullmatch(ROOM, folder.name) or folder.is_symlink() or not folder.is_dir():
            raise ValueError("unexpected entry under rooms")
        if selected is None or folder.name in selected:
            yield folder


@contextmanager
def room_read_lock(folder):
    # Existing file only: dry-run never creates lock files in the source tree.
    lock = folder / ".lock"
    if lock.is_symlink():
        raise ValueError("symlink room lock")
    if lock.exists():
        with lock.open("rb") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield
    else:
        # Backups / legacy inactive rooms can have no lock. Live rooms always do.
        yield


def connect_readonly(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError("missing or symlink shares.db")
    return sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=15)


def snapshot(source, destination):
    """Copy room files under room locks; SQLite backup, never copy DB/WAL bytes.

    Pause the satellite for a globally consistent files+shares snapshot.
    """
    source = source_root(source)
    destination = Path(destination).absolute()
    if destination.resolve().is_relative_to(source.resolve()):
        raise ValueError("backup must be outside source")
    destination.mkdir(parents=True, exist_ok=False)
    os.chmod(destination, 0o700)
    (destination / "rooms").mkdir()
    for folder in room_folders(source):
        with room_read_lock(folder):
            # Reject symlinks throughout the tree before shutil follows any.
            for base, dirs, files in os.walk(folder, followlinks=False):
                if any((Path(base) / name).is_symlink() for name in dirs + files):
                    raise ValueError("symlink in room snapshot")
            shutil.copytree(folder, destination / "rooms" / folder.name)
    with closing(connect_readonly(source / "shares.db")) as src, \
            closing(sqlite3.connect(destination / "shares.db")) as dst:
        src.backup(dst)
        if dst.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise ValueError("shares snapshot integrity_check failed")
    # COMPLETE only after all files and the consistent DB have been copied.
    metadata = {"version": 1, "source": str(source), "created": int(time.time()),
                "files": {}}
    for path in sorted(destination.rglob("*")):
        if path.is_file() and path.name != ".lock":
            metadata["files"][str(path.relative_to(destination))] = digest(path.read_bytes())
    (destination / "SNAPSHOT.json").write_text(json.dumps(metadata, indent=2) + "\n")
    return destination


def sources(root, selected=None):
    """Read every historical PNG and every frozen BLOB, including private rows."""
    for folder in room_folders(root, selected):
        for directory in ("gallery", "shared-bead-finished"):
            images = folder / directory
            if images.is_symlink():
                raise ValueError("symlink image directory")
            if not images.exists():
                continue
            for path in sorted(images.iterdir()):
                if path.is_symlink():
                    raise ValueError("symlink image")
                if path.suffix != ".png":
                    continue  # upstream temp files are not committed images
                with room_read_lock(folder):
                    identity = file_identity(root, path)
                    if path.stat().st_size > MAX_IMAGE:
                        raise ValueError("local image exceeds limit")
                    raw = path.read_bytes()
                yield str(path.relative_to(root)), identity, raw
    db = root / "shares.db"
    with closing(connect_readonly(db)) as conn:
        # An explicit read transaction keeps the whole BLOB scan consistent.
        conn.execute("BEGIN")
        for sid, room, size in conn.execute(
                "SELECT id,room,length(image) FROM submissions ORDER BY room,id"):
            if not re.fullmatch(ROOM, room):
                raise ValueError("invalid submission room")
            if selected is not None and room not in selected:
                continue
            object_key(room, "shares", sid, "0" * 64)
            if size is None or size > MAX_IMAGE:
                raise ValueError("submission image exceeds limit")
            raw = conn.execute("SELECT image FROM submissions WHERE id=?", (sid,)).fetchone()[0]
            yield f"shares.db:submissions:{sid}", (room, "shares", sid), raw


@contextmanager
def manifest_stream(path, root):
    """Exclusive append-only journal; repair only an interrupted final line."""
    path = Path(path).absolute()
    if path.resolve().is_relative_to(root.resolve()) or path.is_symlink():
        raise ValueError("manifest must be outside source and not a symlink")
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "r+b") as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        header = {"type": "header", "version": 1, "source_root": str(root),
                  "bucket": BUCKET, "prefix": PREFIX}
        stream.seek(0)
        first = True
        while True:
            start = stream.tell()
            line = stream.readline()
            if not line:
                break
            if not line.endswith(b"\n"):
                stream.seek(start)
                stream.truncate()
                break
            entry = json.loads(line)
            if first and entry != header:
                raise ValueError("manifest belongs to another source/version/target")
            first = False
        if first:
            append_record(stream, header)
        yield stream


def append_record(stream, record):
    stream.write((json.dumps(record, sort_keys=True) + "\n").encode())
    stream.flush()
    os.fsync(stream.fileno())


def migrate(root, manifest, *, apply=False, verify_only=False, backend=None, rooms=None):
    root = source_root(root)
    if rooms is not None and any(not re.fullmatch(ROOM, room) for room in rooms):
        raise ValueError("invalid selected room")
    if apply and verify_only:
        raise ValueError("apply and verify-only are mutually exclusive")
    if (apply or verify_only) and backend is None:
        raise ValueError("explicit backend required")
    counts = {"planned": 0, "uploaded": 0, "existing": 0, "verified": 0, "failed": 0}
    with manifest_stream(manifest, root) as journal:
        for source, identity, raw in sources(root, rooms):
            sha = digest(raw)
            key = object_key(*identity, sha)
            entry = {"source": source, "room": identity[0], "kind": identity[1],
                     "key": key, "sha256": sha, "bytes": len(raw), "time": int(time.time())}
            # Intent is durable before the first remote call.
            append_record(journal, {**entry, "status": "planned"})
            if not apply and not verify_only:
                counts["planned"] += 1
                continue
            try:
                if verify_only:
                    if verified_get(backend, key, sha) != raw:
                        raise ValueError("remote image missing")
                    status = "verified"
                else:
                    status = ensure_object(backend, key, raw)
                counts[status] += 1
                append_record(journal, {**entry, "status": status})
            except Exception as exc:
                counts["failed"] += 1
                # Exception types only; SDK/transport errors can contain secrets.
                append_record(journal, {**entry, "status": "failed", "error": type(exc).__name__})
        append_record(journal, {"type": "summary", "time": int(time.time()), **counts})
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    backup = commands.add_parser("snapshot", help="create a new local SQLite-consistent backup")
    backup.add_argument("--data-dir", required=True)
    backup.add_argument("--output", required=True)
    backfill = commands.add_parser("backfill", help="default: dry-run, no credentials/network")
    backfill.add_argument("--data-dir", required=True)
    backfill.add_argument("--manifest", required=True)
    backfill.add_argument("--room", action="append", help="repeat to select rooms; default all")
    mode = backfill.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="upload missing images and read back")
    mode.add_argument("--verify-only", action="store_true", help="remote GET/SHA check, no PUT")
    args = parser.parse_args(argv)
    backend = None
    try:
        if args.command == "snapshot":
            print(snapshot(args.data_dir, args.output))
            return 0
        # Validate source/manifest before loading credentials.
        root = source_root(args.data_dir)
        if Path(args.manifest).resolve().is_relative_to(root.resolve()):
            raise ValueError("manifest must be outside source")
        backend = S3.from_env() if args.apply or args.verify_only else None
        counts = migrate(root, args.manifest, apply=args.apply, verify_only=args.verify_only,
                         backend=backend, rooms=set(args.room) if args.room else None)
        print(json.dumps(counts, sort_keys=True))
        return int(counts["failed"] > 0)
    except Exception as exc:
        print(f"shared-craft migration stopped ({type(exc).__name__}); sources retained", file=sys.stderr)
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == "__main__":
    raise SystemExit(main())
