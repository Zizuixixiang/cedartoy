"""Retain upstream encounter rules, replace its shared JSON transactions.

Only upstream public position/footprints, display names and encounter metadata
enter this database. No journals, messages, postcards or personal archives.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import sqlite3

from . import storage


def db_path():
    return Path(os.environ.get("NOWHERE_SHARED_DB", storage.ROOT.parent.parent / "nowhere_shared.db"))


@contextmanager
def transaction():
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    try:
        db.execute("CREATE TABLE IF NOT EXISTS shared (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        db.execute("BEGIN IMMEDIATE")
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def get(db, key):
    row = db.execute("SELECT value FROM shared WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else {}


def put(db, key, value):
    db.execute("INSERT INTO shared VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
               (key, json.dumps(value, ensure_ascii=False, allow_nan=False)))


def forget(player):
    if not db_path().exists():
        return
    with transaction() as db:
        for key in ("travelers.json", "travelers_archive.json"):
            value = get(db, key)
            value.pop(player, None)
            put(db, key, value)
        for key in ("meetings", "counts"):
            value = get(db, key)
            value = {k: v for k, v in value.items() if player not in k.split("|")}
            put(db, key, value)


def install(module, player, metadata):
    """Installed once in a process permanently assigned to this player."""
    active_db = None
    module.is_enabled = lambda: metadata().get("cotraveler", "1") in {"1", "quiet"}
    module.is_quiet = lambda: metadata().get("cotraveler", "1") == "quiet"
    module._load_json = lambda path: copy.deepcopy(get(active_db, path.name))
    module._save_json = lambda path, value: put(active_db, path.name, value)
    module.check_at_messages = lambda *args: None  # No connected upstream sender.

    def wrap(name):
        original = getattr(module, name)

        def call(*args, **kwargs):
            nonlocal active_db
            with transaction() as db:
                active_db = db
                try:
                    # The upstream opening name and walking env now both key
                    # the registry with the immutable platform identity.
                    args = (player, *args[1:])
                    if name == "check_footprints":
                        counts = get(db, "counts")
                        mine = {k.split("|", 1)[1]: v for k, v in counts.items() if k.startswith(player + "|")}
                        result = original(*args[:-1], mine)
                        counts.update({player + "|" + k: v for k, v in mine.items()})
                        put(db, "counts", counts)
                    elif name == "check_meeting":
                        meetings = get(db, "meetings")
                        result = original(*args[:-1], meetings)
                        put(db, "meetings", meetings)
                    else:
                        result = original(*args, **kwargs)
                    if name == "register":
                        registry = get(db, "travelers.json")
                        if player in registry:
                            registry[player]["display_name"] = metadata().get("traveler_name", "旅者")
                            put(db, "travelers.json", registry)
                    return result
                finally:
                    active_db = None
        setattr(module, name, call)

    for name in ("register", "refresh_pos", "record_footprint", "check_footprints", "check_meeting"):
        wrap(name)
    for name in ("_fp_named", "_fp_named_archived"):
        original = getattr(module, name)
        def named(identity, bearing, rng, render=original):
            entry = get(active_db, "travelers.json").get(identity) or get(active_db, "travelers_archive.json").get(identity, {})
            return render(entry.get("display_name", "旅者"), bearing, rng)
        setattr(module, name, named)
