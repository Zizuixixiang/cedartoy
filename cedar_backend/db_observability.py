"""Bounded, parameter-free diagnostics for the shared account SQLite database.

Only successful writes are registered as open write transactions. Registry entries
are candidates, not proof that they blocked another connection. Kernel locks can
identify other processes, but cannot identify their application-level operations.
"""
import itertools
import json
import logging
import os
import re
from pathlib import Path
import sqlite3
import sys
import threading
import time

_LOG = logging.getLogger(__name__)
_GUARD = threading.Lock()
_ACTIVE = {}
_IDS = itertools.count(1)
_LAST_LOG = {}
SLOW_SECONDS = 1.0


def is_lock_error(exc):
    code = getattr(exc, 'sqlite_errorcode', None)
    return (code is not None and (code & 255) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED)) or (
        code is None and str(exc).lower() in ('database is locked', 'database table is locked', 'database schema is locked'))


def _site():
    frame = sys._getframe(2)
    for _ in range(16):
        if frame is None:
            break
        name = frame.f_code.co_filename
        if name != __file__:
            return '%s:%s:%s' % (Path(name).name, frame.f_code.co_name, frame.f_lineno)
        frame = frame.f_back
    return 'unknown'


def _kernel_locks(path):
    rows = []
    try:
        st = os.stat(path)
        target = (os.major(st.st_dev), os.minor(st.st_dev), st.st_ino)
        for line in Path('/proc/locks').read_text().splitlines():
            bits = line.split()
            if '->' in bits:
                continue
            if len(bits) < 8:
                continue
            parts = bits[5].split(':')
            if len(parts) != 3 or (int(parts[0],16),int(parts[1],16),int(parts[2])) != target:
                continue
            rows.append({'pid':int(bits[4]), 'mode':bits[3], 'start':bits[6], 'end':bits[7]})
            if len(rows) >= 12:
                break
    except (OSError, ValueError):
        pass
    return rows


def _emit(kind, payload):
    try:
        now = time.monotonic()
        key = (kind, payload.get('site'))
        with _GUARD:
            if now - _LAST_LOG.get(key, -1e9) < 10:
                return
            if len(_LAST_LOG) >= 256:
                _LAST_LOG.clear()
            _LAST_LOG[key] = now
        _LOG.warning('account_db %s', json.dumps({'event':kind, 'time_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()), 'pid':os.getpid(), **payload}, sort_keys=True))
    except Exception:
        pass  # Diagnostic failure must never change database semantics.


class ObservedCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        return self.connection._observe(lambda: super(ObservedCursor,self).execute(sql,parameters), sql)

    def executemany(self, sql, seq_of_parameters):
        return self.connection._observe(lambda: super(ObservedCursor,self).executemany(sql,seq_of_parameters), sql)

    def executescript(self, sql_script):
        return self.connection._observe(lambda: super(ObservedCursor,self).executescript(sql_script), 'SCRIPT')


class ObservedConnection(sqlite3.Connection):
    def __init__(self, database, *args, diagnostic_origin=None, **kwargs):
        self._diag_origin = diagnostic_origin
        super().__init__(database, *args, **kwargs)
        self._diag_path = str(database)
        self._diag_id = next(_IDS)
        self._diag_started = None

    def _sync(self, sql, site):
        # SQL is classified in memory only; no text, table names or values logged.
        operation = sql.lstrip().split(None,1)[0].upper() if isinstance(sql,str) and sql.strip() else ''
        write_started = operation in ('INSERT','UPDATE','DELETE','REPLACE','CREATE','DROP','ALTER','SCRIPT') or (operation == 'BEGIN' and re.match(r'BEGIN\s+(?:IMMEDIATE|EXCLUSIVE)\b', sql.strip(), re.I))
        if self.in_transaction and self._diag_started is None and write_started:
            self._diag_started = time.monotonic()
            with _GUARD:
                _ACTIVE[self._diag_id] = {'connection':self._diag_id,'thread':threading.get_native_id(),'site':site,'started':self._diag_started,'path':self._diag_path}
        elif not self.in_transaction:
            self._finished(site)

    def _finished(self, site):
        started = self._diag_started
        self._diag_started = None
        with _GUARD:
            prior = _ACTIVE.pop(self._diag_id, None)
        if started is not None and time.monotonic()-started >= SLOW_SECONDS:
            _emit('slow_write_transaction', {'connection':self._diag_id,'site':prior['site'] if prior else site,'end_site':site,'duration_ms':round((time.monotonic()-started)*1000)})

    def _observe(self, operation, sql):
        site = self._diag_origin or _site()
        started = time.monotonic()
        try:
            result = operation()
        except sqlite3.Error as exc:
            if is_lock_error(exc):
                try:
                    with _GUARD:
                        candidates = [{k:v for k,v in row.items() if k not in ('path','started')} | {'age_ms':round((time.monotonic()-row['started'])*1000)} for row in list(_ACTIVE.values()) if row['path']==self._diag_path][:12]
                    _emit('lock_wait_failed', {'connection':self._diag_id,'thread':threading.get_native_id(),'site':site,'wait_ms':round((time.monotonic()-started)*1000),'sqlite_code':getattr(exc,'sqlite_errorcode',None),'in_transaction':self.in_transaction,'open_write_candidates':candidates,'kernel_locks':_kernel_locks(self._diag_path)})
                except Exception:
                    pass
            raise
        try:
            self._sync(sql,site)
            if time.monotonic()-started >= SLOW_SECONDS:
                _emit('slow_database_call', {'connection':self._diag_id,'thread':threading.get_native_id(),'site':site,'duration_ms':round((time.monotonic()-started)*1000),'in_transaction':self.in_transaction})
        except Exception:
            pass
        return result

    def cursor(self, factory=ObservedCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def executescript(self, sql_script):
        return self.cursor().executescript(sql_script)

    def commit(self):
        return self._observe(super().commit, 'COMMIT')

    def rollback(self):
        return self._observe(super().rollback, 'ROLLBACK')

    def __exit__(self, *args):
        return self._observe(lambda: super(ObservedConnection,self).__exit__(*args), 'CONTEXT_EXIT')

    def close(self):
        try:
            return super().close()
        finally:
            self._finished(_site())

    def __del__(self):
        try:
            with _GUARD:
                _ACTIVE.pop(self._diag_id, None)
        except Exception:
            pass
