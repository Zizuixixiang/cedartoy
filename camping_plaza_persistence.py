"""SQLite request transactions for the isolated Camping Plaza engine module.

The author's storage methods open/commit/close their own connections. Borrowing
one request-owned connection lets us reuse the exact snapshot codec without
changing the vendor or duplicating its restoration/migration logic.
"""

from contextvars import ContextVar
from functools import wraps
from pathlib import Path
import sqlite3

from fastapi import HTTPException
from fastapi.routing import APIRoute


class _BorrowedConnection:
    def __init__(self, connection):
        self.connection = connection

    def __getattr__(self, name):
        return getattr(self.connection, name)

    def commit(self):
        # Only the outer request may publish success and commit.
        pass

    def close(self):
        pass


class RequestPersistence:
    def __init__(self, connect, db_path):
        self.connect = connect
        self.db_path = db_path
        self.current = ContextVar("camping_request_connection", default=None)

    def engine_connect(self, database, *args, **kwargs):
        active = self.current.get()
        if active is None:
            return sqlite3.connect(database, *args, **kwargs)
        connection, path = active
        if Path(database).resolve() != path:
            raise sqlite3.OperationalError("unexpected database inside game request")
        return _BorrowedConnection(connection)

    def transactional(self, function):
        @wraps(function)
        def wrapped(*args, **kwargs):
            if self.current.get() is not None:
                return function(*args, **kwargs)
            connection = None
            token = None
            try:
                path = self.db_path().resolve()
                connection = self.connect()
                connection.execute("BEGIN IMMEDIATE")
                token = self.current.set((connection, path))
                result = function(*args, **kwargs)
                connection.commit()
                return result
            except sqlite3.Error as exc:
                raise HTTPException(503, "露营存档读写失败，本次操作未确认保存") from exc
            finally:
                if token is not None:
                    self.current.reset(token)
                if connection is not None:
                    try:
                        if connection.in_transaction:
                            connection.rollback()
                    finally:
                        connection.close()
        return wrapped


def install(engine_module, api, connect, db_path):
    persistence = RequestPersistence(connect, db_path)

    class EngineSQLite:
        # This replaces only the privately loaded engine's module reference;
        # Python's sqlite3 module and other games are never monkey-patched.
        connect = staticmethod(persistence.engine_connect)

        def __getattr__(self, name):
            return getattr(sqlite3, name)

    engine_module.sqlite3 = EngineSQLite()
    original_save = engine_module.CampingPlazaEngine.save_state

    @wraps(original_save)
    def checked_save(engine):
        if not original_save(engine):
            raise HTTPException(503, "露营存档写入失败，本次操作未确认保存")
        return True

    engine_module.CampingPlazaEngine.save_state = checked_save
    # GET routes can create a first save or persist arrival plans in get_engine.
    # They need the same load-to-commit boundary; health/static routes do not.
    for route in api.app.routes:
        if (not isinstance(route, APIRoute)
                or not route.path.startswith(("/api/", "/mcp/"))
                or route.path == "/api/health"):
            continue
        original = route.endpoint
        wrapped = persistence.transactional(original)
        route.endpoint = wrapped
        route.dependant.call = wrapped
        # Direct/nested API calls use the same transaction as HTTP entrypoints.
        setattr(api, original.__name__, wrapped)
    return persistence
