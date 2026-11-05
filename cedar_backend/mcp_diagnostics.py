"""Bounded, best-effort MCP transport diagnostics. Never authenticates callers."""
import asyncio
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import threading
import time
import uuid

METHODS = frozenset((
    "initialize", "ping", "tools/list", "tools/call",
    "resources/list", "resources/templates/list", "resources/read",
    "resources/subscribe", "resources/unsubscribe", "prompts/list", "prompts/get",
    "completion/complete", "logging/setLevel",
    "notifications/initialized", "notifications/cancelled", "notifications/progress",
    "notifications/roots/list_changed", "notifications/tools/list_changed",
    "notifications/resources/list_changed", "notifications/resources/updated",
    "notifications/prompts/list_changed", "notifications/message",
))
ROOT = Path(__file__).resolve().parents[1]
# Temporary, explicitly scoped incident diagnostics; remove after incident closure.
TARGET_ACCOUNT_ID = 10604

class Sink:
    """Independent target/unknown budgets and files; other known accounts are omitted."""
    def __init__(self, path, max_bytes=1024*1024, per_minute=120, deadline=0):
        self.path = Path(path)
        self.deadline = deadline
        self.max_bytes = max_bytes
        self.per_minute = per_minute
        self.lock = threading.Lock()
        self.window = 0
        self.count = {"target": 0, "unattributed": 0}

    def enabled(self):
        return self.deadline - 86400 <= time.time() < self.deadline

    def admit(self, bucket="target"):
        if not self.enabled():
            return False
        with self.lock:
            window = int(time.monotonic() // 60)
            if window != self.window:
                self.window, self.count = window, {"target": 0, "unattributed": 0}
            limit = self.per_minute if bucket == "target" else 10
            if self.count[bucket] >= limit:
                return False
            self.count[bucket] += 1
            return True

    def write(self, record):
        # Only Trace builds records. No paths, client IDs, exceptions or RPC text.
        if not self.enabled():
            return
        try:
            line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
            path = self.path if record.get("scope") == "target" else self.path.with_name(self.path.name + ".unknown")
            with self.lock:
                if path.exists() and path.stat().st_size + len(line) > self.max_bytes:
                    old = path.with_name(path.name + ".2")
                    old.unlink(missing_ok=True)
                    previous = path.with_name(path.name + ".1")
                    if previous.exists():
                        previous.replace(old)
                    path.replace(previous)
                fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
                with os.fdopen(fd, "ab") as output:
                    output.write(line)
        except Exception:
            pass  # Diagnostics cannot fail a request or expose exception details.

def match_account(db_path, raw_token):
    if not isinstance(raw_token, str) or not raw_token:
        return {"identity": "absent"}
    if len(raw_token) > 8192:
        return {"identity": "unknown"}
    try:
        digest = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
        uri = Path(db_path).resolve().as_uri() + "?mode=ro"
        with closing(sqlite3.connect(uri, uri=True, timeout=0.02)) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only=ON")
            matches = []
            for table, generation, revoked in (
                    ("ai_access_tokens", "generation", "revoked_at_epoch"),
                    ("legacy_ai_token_hashes", "token_version", "NULL")):
                if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                    continue
                row = conn.execute(f"""SELECT t.user_id, t.{generation} AS generation,
                    {('t.' + revoked) if revoked != 'NULL' else revoked} AS revoked,
                    u.id AS existing_id, u.is_ai, u.ai_token_version, u.deleted_at,
                    u.deletion_requested_at_epoch, u.scheduled_delete_at_epoch
                    FROM {table} t LEFT JOIN toy_users u ON u.id=t.user_id
                    WHERE t.token_hash=?""", (digest,)).fetchone()
                if row:
                    matches.append(row)
            if len(matches) != 1:
                return {"identity": "unknown"}
            row = matches[0]
            if row["existing_id"] is None:
                state = "missing_account"
            elif row["deleted_at"] is not None:
                state = "deleted"
            elif row["deletion_requested_at_epoch"] is not None or row["scheduled_delete_at_epoch"] is not None:
                state = "pending_deletion"
            elif row["revoked"] is not None:
                state = "revoked"
            elif row["generation"] != row["ai_token_version"]:
                state = "generation_mismatch"
            elif not row["is_ai"]:
                state = "not_ai"
            else:
                state = "current_hash_match"
            return {"identity": state, "account_id": int(row["user_id"])}
    except Exception:
        return {"identity": "lookup_unavailable"}

class Trace:
    def __init__(self, sink, transport, http_method):
        self.sink = sink
        self.started = time.monotonic()
        self.started_epoch = time.time()
        self.active = False
        self.fields = {"request_id": uuid.uuid4().hex, "transport": transport,
                       "http_method": http_method if http_method in ("POST", "GET") else "other",
                       "method": "unparsed", "identity": "unresolved"}
        self.http_status = None
        self.rpc_result = "unobserved"
        self.rpc_error_code = None

    def emit(self, stage):
        if not self.active:
            return
        self.sink.write({**self.fields, "time_epoch": round(self.started_epoch if stage == "entered" else time.time(), 3),
                         "stage": stage, "elapsed_ms": round((time.monotonic()-self.started)*1000, 2),
                         "http_status": self.http_status, "rpc_result": self.rpc_result,
                         "rpc_error_code": self.rpc_error_code})

    def identify(self, db_path, token):
        identity = match_account(db_path, token)
        account_id = identity.get("account_id")
        if account_id is not None and account_id != TARGET_ACCOUNT_ID:
            return
        bucket = "target" if account_id == TARGET_ACCOUNT_ID else "unattributed"
        self.active = self.sink.admit(bucket)
        if not self.active:
            return
        self.fields.update(identity)
        self.fields["scope"] = bucket
        self.emit("entered")
        self.emit("identity_checked")

    def payload(self, payload):
        method = payload.get("method") if isinstance(payload, dict) else None
        self.fields["method"] = method if isinstance(method, str) and method in METHODS else "other"
        self.emit("parsed")

    def response(self, payload):
        self.rpc_error_code = None
        if not isinstance(payload, dict):
            return
        error = payload.get("error")
        result = payload.get("result")
        if isinstance(error, dict):
            self.rpc_result = "rpc_error"
            code = error.get("code")
            # JSON-RPC codes are integers. Never coerce text, booleans or floats.
            if type(code) is int and -(2**31) <= code < 2**31:
                self.rpc_error_code = code
        elif isinstance(result, dict) and result.get("isError") is True:
            self.rpc_result = "tool_error"
        elif "result" in payload:
            self.rpc_result = "rpc_result"
        elif "error" in payload:
            self.rpc_result = "transport_error"

    def finish(self, outcome="completed"):
        self.emit(outcome if outcome in ("completed", "exception", "disconnected", "cancelled") else "exception")

def start(sink, transport, method):
    try:
        return Trace(sink, transport, method) if sink.enabled() else None
    except Exception:
        return None

def activation_deadline():
    # Absolute persisted deadline: restarts never grant another 24h.
    try:
        record = json.loads((ROOT / "data/mcp_diagnostics_activation.json").read_text())
        started, until = record["enabled_at"], record["expires_at"]
        if (type(started) is int and type(until) is int and until-started == 86400
                and started <= time.time() < until and record["account_id"] == TARGET_ACCOUNT_ID):
            return until
    except Exception:
        pass
    return 0

_DEADLINE = activation_deadline()
MAIN_SINK = Sink(ROOT / "data/mcp_main_diagnostics.jsonl", deadline=_DEADLINE)
EDGE_SINK = Sink(ROOT / "data/mcp_edge_diagnostics.jsonl", deadline=_DEADLINE)

class Middleware:
    """Outermost application entry, before body read, routing and upstream I/O."""
    def __init__(self, app, *, sink, db_path, candidate):
        self.app, self.sink, self.db_path, self.candidate = app, sink, db_path, candidate

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or not self.candidate(scope.get("path", "")):
            return await self.app(scope, receive, send)
        trace = start(self.sink, "edge", "POST")
        if trace is None:
            return await self.app(scope, receive, send)
        scope["mcp_diagnostic"] = trace
        path = scope.get("path", "")
        token = path.strip("/") if path not in ("/", "/mcp", "/mcp/") else ""
        if not token:
            for key, value in scope.get("headers", ()):
                if key.lower() == b"authorization" and value.lower().startswith(b"bearer "):
                    token = value[7:].decode("latin-1").strip()
                    break
        async def observed_send(message):
            if message["type"] == "http.response.start":
                trace.http_status = message["status"]
            await send(message)
        outcome = "completed"
        try:
            await asyncio.to_thread(trace.identify, self.db_path, token)
            del token
            await self.app(scope, receive, observed_send)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "disconnected" if type(exc).__name__ == "ClientDisconnect" else "exception"
            raise
        finally:
            await asyncio.to_thread(trace.finish, outcome)
