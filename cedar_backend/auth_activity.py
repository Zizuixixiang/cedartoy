"""Best-effort activity bookkeeping after fresh, successful authentication."""
import datetime
import logging
import os
import sqlite3
import threading
import time

_LOG = logging.getLogger("cedartoy.auth_activity")
ACTIVITY_INTERVAL_SECONDS = 60
ACTIVITY_BUSY_TIMEOUT_MS = 100
_LOG_LOCK = threading.Lock()
_LOG_WINDOW = 0.0
_LOG_COUNT = 0

def _recent(value):
    if not isinstance(value, str):
        return False
    try:
        previous = datetime.datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return False
    age = (datetime.datetime.now() - previous).total_seconds()
    return 0 <= age < ACTIVITY_INTERVAL_SECONDS

def _is_busy(exc):
    code = getattr(exc, "sqlite_errorcode", None)
    if isinstance(code, int):
        return code & 255 in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)
    # Python 3.10 lacks sqlite_errorcode. Never swallow other SQL errors.
    return str(exc) in ("database is locked", "database table is locked",
                        "database schema is locked")

def _log_deferred(stage, started, exc):
    global _LOG_WINDOW, _LOG_COUNT
    now = time.monotonic()
    with _LOG_LOCK:
        if now - _LOG_WINDOW >= 60:
            _LOG_WINDOW, _LOG_COUNT = now, 0
        if _LOG_COUNT >= 10:
            return
        _LOG_COUNT += 1
    # No exception text, account ID, SQL, bindings or request content.
    _LOG.warning("auth_activity_deferred pid=%s native_tid=%s stage=%s "
                 "sqlite_code=%s elapsed_ms=%s wait_limit_ms=%s",
                 os.getpid(), threading.get_native_id(), stage,
                 getattr(exc, "sqlite_errorcode", "busy_or_locked"),
                 round((now - started) * 1000), ACTIVITY_BUSY_TIMEOUT_MS)

def update_activity(conn, user_id, last_active_at):
    """True after committed update; False if recent or safely deferred.

    Caller must complete all identity/token/deletion checks before invoking.
    No identity or authorization state is cached.
    """
    if _recent(last_active_at):
        return False
    if conn.in_transaction:
        raise RuntimeError("activity update requires a clean transaction")
    previous_timeout = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
    conn.execute("PRAGMA busy_timeout=%d" % ACTIVITY_BUSY_TIMEOUT_MS)
    started = time.monotonic()
    stage = "update"
    try:
        conn.execute("UPDATE toy_users SET last_active_at = datetime('now', 'localtime') WHERE id = ?", (user_id,))
        stage = "commit"
        conn.commit()
        return True
    except sqlite3.OperationalError as exc:
        if not _is_busy(exc):
            raise
        # Busy COMMIT leaves its transaction open; rollback before returning the
        # already validated identity. A rollback failure must still propagate.
        conn.rollback()
        try:
            _log_deferred(stage, started, exc)
        except Exception:
            pass  # Diagnostics must not turn a safely deferred write into failure.
        return False
    finally:
        conn.execute("PRAGMA busy_timeout=%d" % previous_timeout)
