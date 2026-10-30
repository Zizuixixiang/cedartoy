#!/usr/bin/env python3
"""Verified R2 primary compaction/restore. Default dry-run; no remote deletion.

CLI is confined to the production craft root. Library tests explicitly supply an
isolated allowed_root. Stop the satellite and snapshot before applying changes.
"""
import argparse
import base64
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.shared_craft_r2 import (
    source_root, room_folders, room_read_lock, connect_readonly,
    manifest_stream, append_record,
)
from shared_craft_adapter.object_storage import (
    S3, MAX_IMAGE, ROOM, digest, reference, reference_sha, file_identity,
    object_key, ensure_object, verified_get,
)
from shared_craft_adapter.primary import decode_data_url, ref_bytes
from shared_craft_adapter.storage import atomic_bytes, validate_json

PRODUCTION_ROOT = Path('/opt/cedartoy/data/shared_craft')


def confined_root(root, allowed_root):
    root = source_root(root)
    if root != Path(allowed_root).absolute() or root.resolve() != root:
        raise ValueError('primary migration root is not the explicitly allowed directory')
    # Reject symlinks in ancestors too, not merely final path components.
    if any(parent.is_symlink() for parent in root.parents):
        raise ValueError('symlink source ancestor')
    for suffix in ('', '-wal', '-shm', '-journal'):
        if (root / ('shares.db' + suffix)).is_symlink():
            raise ValueError('symlink SQLite file')
    return root


def check_backup(backup, root):
    backup = Path(backup).absolute()
    if backup.resolve().is_relative_to(root) or backup.resolve() != backup:
        raise ValueError('backup must be external')
    record = json.loads((backup / 'SNAPSHOT.json').read_bytes())
    if record['source'] != str(root) or not record.get('files'):
        raise ValueError('backup belongs to another source or is incomplete')
    for name, sha in record['files'].items():
        path = backup / name
        if not path.resolve().is_relative_to(backup) or path.is_symlink():
            raise ValueError('invalid backup path')
        if digest(path.read_bytes()) != sha:
            raise ValueError('backup hash mismatch')
    with closing(connect_readonly(backup / 'shares.db')) as conn:
        if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
            raise ValueError('backup integrity failure')


def check_preimage(backup, source, identity, original, json_ref):
    """Never discard a full local image absent from the validated snapshot."""
    backup = Path(backup)
    if identity[1] == 'shares':
        with closing(connect_readonly(backup / 'shares.db')) as conn:
            row = conn.execute('SELECT image FROM submissions WHERE id=? AND room=?',
                               (identity[2], identity[0])).fetchone()
            saved = row[0] if row else None
    elif json_ref:
        data = json.loads((backup / source.split(':', 1)[0]).read_bytes())
        saved = next((b.get('reference_image') for b in data['boards'] if b['id'] == identity[2]), None)
    else:
        saved = (backup / source).read_bytes()
    if saved != original:
        raise ValueError('source image is not in the consistent snapshot')


def database_bytes(root):
    return sum(path.stat().st_size for suffix in ('', '-wal', '-shm', '-journal')
               if (path := root / ('shares.db' + suffix)).is_file())


def migrate(root, manifest, *, apply=False, verify_only=False, restore=False,
            backend=None, rooms=None, allowed_root=PRODUCTION_ROOT, backup=None,
            compact_db=False):
    root = confined_root(root, allowed_root)
    if apply and verify_only or restore and verify_only:
        raise ValueError('incompatible modes')
    if compact_db and (not apply or rooms is not None):
        raise ValueError('database compaction requires whole-database apply')
    if rooms is not None and any(not re.fullmatch(ROOM, room) for room in rooms):
        raise ValueError('invalid selected room')
    if apply:
        if not backup:
            raise ValueError('apply requires a verified consistent snapshot')
        check_backup(backup, root)
        if Path(manifest).resolve().is_relative_to(Path(backup).resolve()):
            raise ValueError("manifest must not overwrite snapshot files")
    if (apply or verify_only) and backend is None:
        raise ValueError('backend required')
    counts = dict(planned=0, converted=0, unchanged=0, verified=0, failed=0,
                  source_bytes=0, estimated_after_bytes=0, committed_after_bytes=0,
                  restore_unknown_objects=0, db_bytes_before=database_bytes(root))

    with manifest_stream(manifest, root) as journal:
        def convert_one(source, identity, stored, commit, *, json_ref=False):
            """Caller holds room lock or BEGIN IMMEDIATE until commit finishes."""
            original, mime = stored, None
            if json_ref:
                before = len(json.dumps(stored, ensure_ascii=False).encode())
                if isinstance(stored, dict):
                    marker = ref_bytes(stored)
                    sha, raw, mime = reference_sha(marker), None, stored['mime']
                else:
                    mime, raw = decode_data_url(stored)
                    sha = digest(raw)
            else:
                if not isinstance(stored, bytes) or len(stored) > MAX_IMAGE:
                    raise ValueError('invalid source image/size')
                before = len(stored)
                sha = reference_sha(stored)
                raw = None if sha else stored
                sha = sha or digest(raw)
            key = object_key(*identity, sha)
            marker = ({'craft_r2': 1, 'sha256': sha, 'mime': mime}
                      if json_ref else reference(sha))
            expected_size = (len(json.dumps(marker).encode()) if json_ref else len(marker))
            if restore and raw is None:
                counts['restore_unknown_objects'] += 1
            counts['source_bytes'] += before
            counts['estimated_after_bytes'] += before if restore and raw is not None else (0 if restore else expected_size)
            entry = dict(source=source, room=identity[0], kind=identity[1], key=key,
                         sha256=sha, bytes=before, time=int(time.time()),
                         operation='restore' if restore else 'primary')
            append_record(journal, {**entry, 'status': 'planned'})
            if not apply and not verify_only:
                counts['planned'] += 1
                counts['committed_after_bytes'] += before
                return
            try:
                if apply and not restore and raw is not None:
                    check_preimage(backup, source, identity, original, json_ref)
                if restore and raw is not None:
                    remote = raw
                elif raw is None or verify_only:
                    remote = verified_get(backend, key, sha)
                    if remote is None or (raw is not None and remote != raw):
                        raise ValueError('remote object missing/mismatched')
                else:
                    ensure_object(backend, key, raw)
                    remote = raw
                if verify_only:
                    status, after = 'verified', before
                else:
                    replacement = marker
                    if restore:
                        replacement = (f'data:{mime};base64,' + base64.b64encode(remote).decode()
                                       if json_ref else remote)
                    after = (len(json.dumps(replacement, ensure_ascii=False).encode())
                             if json_ref else len(replacement))
                    if original != replacement:
                        commit(replacement)
                        status = 'converted'
                    else:
                        status = 'unchanged'
                counts[status] += 1
                counts['committed_after_bytes'] += after
                append_record(journal, {**entry, 'status': status, 'after_bytes': after})
            except Exception as exc:
                counts['failed'] += 1
                counts['committed_after_bytes'] += before
                append_record(journal, {**entry, 'status': 'failed', 'error': type(exc).__name__})

        def convert(source, identity, stored, commit, **kwargs):
            try:
                convert_one(source, identity, stored, commit, **kwargs)
            except (ValueError, KeyError, TypeError, AssertionError) as exc:
                counts['failed'] += 1
                append_record(journal, dict(source=source, room=identity[0], kind=identity[1],
                                            status='failed', error=type(exc).__name__))

        for folder in room_folders(root, rooms):
            with room_read_lock(folder):
                if (folder / '.r2-commit.json').exists():
                    # Dry-run/verify never mutate a pending runtime transaction.
                    raise ValueError('pending primary transaction; recover satellite before migration')
                for directory in ('gallery', 'shared-bead-finished'):
                    image_dir = folder / directory
                    if image_dir.is_symlink():
                        raise ValueError('symlink image directory')
                    if not image_dir.exists():
                        continue
                    for path in sorted(image_dir.iterdir()):
                        if path.is_symlink():
                            raise ValueError('symlink image')
                        if path.suffix != '.png':
                            continue
                        identity = file_identity(root, path)
                        if path.stat().st_size > MAX_IMAGE:
                            raise ValueError('source image too large')
                        convert(str(path.relative_to(root)), identity, path.read_bytes(),
                                lambda value, path=path: atomic_bytes(path, value))
                path = folder / 'beads.json'
                if path.is_symlink():
                    raise ValueError('symlink bead metadata')
                if path.exists():
                    # read_json can retain corrupt evidence, so dry-run uses a
                    # plain parse and structural validation instead.
                    data = validate_json(json.loads(path.read_bytes()), "beads")
                    for board in data['boards']:
                        value = board.get('reference_image')
                        if not value:
                            continue
                        def commit(value, board=board):
                            previous = board['reference_image']
                            board['reference_image'] = value
                            try:
                                atomic_bytes(path, json.dumps(data, ensure_ascii=False).encode())
                            except BaseException:
                                board['reference_image'] = previous
                                raise
                        convert(str(path.relative_to(root)) + ':' + board['id'],
                                (folder.name, 'references', board['id']), value, commit, json_ref=True)

        db = root / 'shares.db'
        # Validate before opening any writable SQLite handle.
        with closing(connect_readonly(db)) as conn:
            rows = conn.execute('SELECT id,room FROM submissions ORDER BY room,id').fetchall()
        for sid, room in rows:
            object_key(room, 'shares', sid, '0' * 64)
            if rooms is not None and room not in rooms:
                continue
            with closing(sqlite3.connect(db, timeout=15) if apply else connect_readonly(db)) as conn:
                if apply:
                    conn.execute('PRAGMA secure_delete=ON')
                conn.execute('BEGIN IMMEDIATE' if apply else 'BEGIN')
                try:
                    row = conn.execute('SELECT image FROM submissions WHERE id=? AND room=?', (sid, room)).fetchone()
                    if row:
                        def commit(value):
                            conn.execute('UPDATE submissions SET image=? WHERE id=? AND room=?', (value, sid, room))
                            conn.commit()
                        convert('shares.db:submissions:' + sid, (room, 'shares', sid), row[0], commit)
                finally:
                    conn.rollback()
        if compact_db and counts['failed'] == 0:
            with closing(sqlite3.connect(db, timeout=15)) as conn:
                if conn.execute('PRAGMA integrity_check').fetchall() != [('ok',)]:
                    raise ValueError('database integrity failure')
                conn.execute('VACUUM')
                conn.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        counts['db_bytes_after'] = database_bytes(root)
        append_record(journal, {'type': 'summary', **counts})
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data-dir', default=str(PRODUCTION_ROOT))
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--backup', help='external snapshot directory created by shared_craft_r2.py snapshot')
    parser.add_argument('--room', action='append')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--apply', action='store_true')
    mode.add_argument('--verify-only', action='store_true')
    parser.add_argument('--restore', action='store_true', help='rehydrate current references; default is dry-run')
    parser.add_argument('--compact-db', action='store_true', help='VACUUM after successful whole-database apply')
    args = parser.parse_args(argv)
    backend = None
    try:
        root = confined_root(args.data_dir, PRODUCTION_ROOT)
        if args.apply:
            if not args.backup:
                raise ValueError('apply requires backup')
            check_backup(args.backup, root)
        if Path(args.manifest).resolve().is_relative_to(root):
            raise ValueError('manifest must be external')
        backend = S3.from_env() if args.apply or args.verify_only else None
        counts = migrate(root, args.manifest, apply=args.apply, verify_only=args.verify_only,
                         restore=args.restore, backend=backend, rooms=set(args.room) if args.room else None,
                         backup=args.backup, compact_db=args.compact_db, allowed_root=PRODUCTION_ROOT)
        print(json.dumps(counts, sort_keys=True))
        return int(counts['failed'] > 0)
    except Exception as exc:
        print(f'primary migration stopped ({type(exc).__name__}); inspect manifest and retained snapshot', file=sys.stderr)
        return 1
    finally:
        if backend is not None:
            backend.close()


if __name__ == '__main__':
    raise SystemExit(main())
