"""Bounded, opt-in diagnostics after real human authentication; never log input."""
import json
import os
from pathlib import Path
import threading
import time
import uuid

ROOT = Path(__file__).resolve().parents[1] / 'data'
ACTIVATION = ROOT / 'autochess_web_diagnostics_activation.json'
LOG = ROOT / 'autochess_web_diagnostics.jsonl'
TARGET = 12635
ROUTES = frozenset('/autochess/api/' + x for x in ('bootstrap', 'room', 'stats', 'economy'))
_LOCK = threading.Lock()
_BUDGET = {}
MAX_BYTES = 1024 * 1024


def _active(now):
    try:
        data = json.loads(ACTIVATION.read_text())
        start, end = data['enabled_at'], data['expires_at']
        return (data['account_id'] == TARGET and type(start) is int and type(end) is int
                and 0 < end - start <= 86400 and start <= now < end)
    except (OSError, ValueError, KeyError, TypeError):
        return False


class Trace:
    def __init__(self, route):
        self.request_id = uuid.uuid4().hex
        self.route = route
        self.started = time.monotonic()

    def emit(self, stage, status=None, error=None):
        try:
            now = time.time()
            if not _active(now) or stage not in ('authenticated', 'response', 'exception'):
                return
            row = {'request_id': self.request_id, 'time_epoch': round(now, 3),
                   'account_id': TARGET, 'route': self.route, 'stage': stage,
                   'elapsed_ms': round((time.monotonic() - self.started) * 1000, 2),
                   'http_status': status if type(status) is int and 100 <= status <= 599 else None,
                   'error_type': error if error in ('McpError', 'ValueError', 'TypeError', 'OtherError') else None}
            line = (json.dumps(row, separators=(',', ':')) + '\n').encode()
            with _LOCK:
                if LOG.exists() and LOG.stat().st_size + len(line) > MAX_BYTES:
                    os.replace(LOG, str(LOG) + '.1')
                fd = os.open(LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
                try:
                    os.fchmod(fd, 0o600)
                    os.write(fd, line)
                finally:
                    os.close(fd)
        except (OSError, ValueError, TypeError):
            pass  # Diagnostics must never break a request.


def begin(authenticated_owner, route):
    try:
        now = time.time()
        if str(authenticated_owner) != str(TARGET) or route not in ROUTES or not _active(now):
            return None
        minute = int(now // 60)
        with _LOCK:
            for old in list(_BUDGET):
                if old != minute:
                    del _BUDGET[old]
            if _BUDGET.get(minute, 0) >= 120:
                return None
            _BUDGET[minute] = _BUDGET.get(minute, 0) + 1
        trace = Trace(route)
        trace.emit('authenticated')
        return trace
    except (OSError, ValueError, TypeError):
        return None
