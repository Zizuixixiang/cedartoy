"""Bounded, best-effort MCP transport diagnostics. Never authenticates callers."""
import asyncio
from collections import OrderedDict
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
# Legacy incident target. Persistent mode requires its own explicit configuration.
TARGET_ACCOUNT_ID = 10604
CACHE_TTL = 5.0
CACHE_SIZE = 256
SLOW_MS = 5000
STAGES = frozenset(("entered", "identity_checked", "parsed", "proxy_dispatch",
                    "duel_dispatch", "autochess_dispatch", "response_ready"))

class Sink:
    """Independent target/unknown budgets and files; other known accounts are omitted."""
    def __init__(self, path, max_bytes=1024*1024, per_minute=120, deadline=0,
                 *, persistent=False, account_id=None):
        self.path = Path(path)
        self.deadline = deadline
        self.persistent = persistent
        self.account_id = TARGET_ACCOUNT_ID if account_id is None else account_id
        self.max_bytes = max_bytes
        self.per_minute = per_minute
        self.lock = threading.Lock()
        self._write_lock = threading.Lock()
        self.window = 0
        self.count = {"target": 0, "unattributed": 0}
        self._budgets = {}
        self._next_sample = {}
        self._retry_write = {}
        self._identity_lock = threading.Lock()
        self._cache = OrderedDict()

    def enabled(self):
        return self.persistent or self.deadline - 86400 <= time.time() < self.deadline

    def budget(self, name, limit, period=60, spacing=0):
        """Fixed-size, process-local budgets; no per-client rate-limit state."""
        now = time.monotonic()
        with self.lock:
            window, count = self._budgets.get(name, (-1, 0))
            current = int(now // period)
            if current != window:
                window, count = current, 0
            if count >= limit or now < self._next_sample.get(name, 0):
                return False
            self._budgets[name] = (window, count + 1)
            self._next_sample[name] = now + spacing
            return True

    def identity(self, db_path, token):
        if not self.enabled():
            return None
        if not self.persistent:
            return match_account(db_path, token)
        if not isinstance(token, str) or not token:
            return {"identity": "absent", "identity_age_ms": 0}
        if len(token) > 8192:
            return {"identity": "unknown", "identity_age_ms": 0}
        # Never retain raw credentials. Digests are bounded in-memory keys only.
        # Contention/lookup quota exhaustion means omission, never "unknown".
        if not self._identity_lock.acquire(blocking=False):
            return None
        try:
            key = (str(db_path), hashlib.sha256(token.encode("utf-8")).digest())
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < CACHE_TTL:
                self._cache.move_to_end(key)
                return {**cached[1], "identity_age_ms": round((now-cached[0])*1000, 2)}
            # Known target refreshes have a reserved budget: unrelated traffic
            # cannot consume it. Expired identities are never used as fallback.
            target = cached and cached[1].get("account_id") == self.account_id
            if not self.budget("target_refresh" if target else "lookup", 12 if target else 60):
                return None
            identity = match_account(db_path, token)
            self._cache[key] = (time.monotonic(), identity)
            self._cache.move_to_end(key)
            if len(self._cache) > CACHE_SIZE:
                # Preserve a target entry under unrelated-token floods.
                victim = next((k for k, (_, v) in self._cache.items()
                               if v.get("account_id") != self.account_id), next(iter(self._cache)))
                del self._cache[victim]
            return {**identity, "identity_age_ms": 0}
        except Exception:
            return None
        finally:
            self._identity_lock.release()

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
        bucket = "target" if record.get("scope") == "target" else "unattributed"
        try:
            line = (json.dumps(record, separators=(",", ":")) + "\n").encode()
            if len(line) > self.max_bytes:
                return
            if self.persistent:
                # Also cap actual records: delayed finishes from earlier
                # windows must not create an unbounded completion burst.
                if bucket == "unattributed":
                    admitted = self.budget("unknown_records", 8, period=300)
                elif record.get("entry_sampled"):
                    admitted = self.budget("sample_records", 48)
                else:
                    admitted = self.budget("anomaly_records", 12)
                if not admitted:
                    return
            path = self.path if record.get("scope") == "target" else self.path.with_name(self.path.name + ".unknown")
            # Persistent callers drop contended writes instead of queueing
            # behind slow filesystem I/O. Rate/cache locks never cover I/O.
            if not self._write_lock.acquire(blocking=not self.persistent):
                return
            try:
                if time.monotonic() < self._retry_write.get(bucket, 0):
                    return
                try:
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
                    self._retry_write[bucket] = time.monotonic() + 30
            finally:
                self._write_lock.release()
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
        self.fields = {"request_id": uuid.uuid4().hex, "transport": transport if transport in ("main", "edge") else "other",
                       "http_method": http_method if http_method in ("POST", "GET") else "other",
                       "method": "unparsed", "identity": "unresolved"}
        self.phases = {}
        self.sampled = False
        self.finished = False
        self.http_status = None
        self.rpc_result = "unobserved"
        self.rpc_error_code = None

    @property
    def http_status(self):
        return self._http_status

    @http_status.setter
    def http_status(self, status):
        self._http_status = status if type(status) is int and 100 <= status <= 599 else None
        if self._http_status is not None and self.sink.persistent:
            self.phases.setdefault("response_started", self.elapsed())

    def elapsed(self):
        return round((time.monotonic()-self.started)*1000, 2)

    def record(self, stage, **extra):
        record = {**self.fields, "time_epoch": round(self.started_epoch if stage == "entered" else time.time(), 3),
                  "stage": stage, "elapsed_ms": self.elapsed(), "http_status": self.http_status,
                  "rpc_result": self.rpc_result, "rpc_error_code": self.rpc_error_code}
        if self.sink.persistent:
            record.update(mode="persistent", entry_sampled=self.sampled, phases_ms=dict(self.phases))
        self.sink.write({**record, **extra})

    def emit(self, stage):
        if not self.active or stage not in STAGES:
            return
        if self.sink.persistent:
            if self.finished or stage in self.phases:
                return
            self.phases[stage] = self.elapsed()
            if not self.sampled:
                return
        self.record(stage)

    def identify(self, db_path, token):
        identity = self.sink.identity(db_path, token)
        if identity is None:
            return
        account_id = identity.get("account_id")
        if account_id is not None and account_id != self.sink.account_id:
            return
        bucket = "target" if account_id == self.sink.account_id else "unattributed"
        if self.sink.persistent:
            if bucket == "target":
                self.active = True
                self.sampled = self.sink.budget("entry", 6, spacing=10)
            else:
                self.active = self.sampled = self.sink.budget("unknown", 1, period=300, spacing=300)
        else:
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
        if self.sink.persistent:
            self.emit("response_ready")

    def finish(self, outcome="completed"):
        if not self.active:
            return
        outcome = outcome if outcome in ("completed", "exception", "disconnected", "cancelled") else "exception"
        if not self.sink.persistent:
            self.record(outcome)
            return
        if self.finished:
            return
        self.finished = True
        reasons = []
        if outcome != "completed":
            reasons.append(outcome)
        if self.http_status is not None and self.http_status >= 400:
            reasons.append("http_error")
        if self.rpc_result in ("rpc_error", "tool_error", "transport_error"):
            reasons.append(self.rpc_result)
        if self.elapsed() >= SLOW_MS:
            reasons.append("slow")
        if self.sampled or (reasons and self.fields["scope"] == "target" and self.sink.budget("anomaly", 12)):
            self.record(outcome, reasons=reasons)

def start(sink, transport, method):
    try:
        return Trace(sink, transport, method) if sink.enabled() else None
    except Exception:
        return None

def activation_deadline():
    # Absolute persisted deadline: restarts never grant another 24h.
    try:
        record = read_config(ROOT / "data/mcp_diagnostics_activation.json")
        started, until = record["enabled_at"], record["expires_at"]
        if (type(started) is int and type(until) is int and until-started == 86400
                and started <= time.time() < until and type(record["account_id"]) is int
                and record["account_id"] == TARGET_ACCOUNT_ID):
            return until
    except Exception:
        pass
    return 0

def read_config(path):
    # Bounded reads and strict JSON (including duplicate keys) fail closed.
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate configuration key")
            result[key] = value
        return result
    with path.open("rb") as source:
        raw = source.read(4097)
    if len(raw) > 4096:
        raise ValueError("configuration too large")
    return json.loads(raw, object_pairs_hook=unique)


def configuration():
    """Read once at process startup; changes require restarting both services.

    A present new config always overrides legacy activation, even when invalid.
    Missing/expired legacy activation never grants persistent authorization.
    """
    path = ROOT / "data/mcp_diagnostics_config.json"
    try:
        record = read_config(path)
    except FileNotFoundError:
        if path.is_symlink():
            return {}  # A broken explicit config is not a legacy opt-in.
        return {"deadline": activation_deadline()}
    except Exception:
        return {}
    if (isinstance(record, dict) and set(record) == {"mode", "enabled", "account_id"}
            and record["mode"] == "persistent" and record["enabled"] is True
            and type(record["account_id"]) is int and 0 < record["account_id"] < 2**63):
        return {"persistent": True, "account_id": record["account_id"]}
    return {}


_CONFIGURATION = configuration()
MAIN_SINK = Sink(ROOT / "data/mcp_main_diagnostics.jsonl", **_CONFIGURATION)
EDGE_SINK = Sink(ROOT / "data/mcp_edge_diagnostics.jsonl", **_CONFIGURATION)

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
            if not trace.active:
                # Avoid gateway JSON inspection and extra worker handoffs for
                # unrelated, throttled or unselected unknown traffic.
                scope.pop("mcp_diagnostic", None)
                return await self.app(scope, receive, send)
            await self.app(scope, receive, observed_send)
        except asyncio.CancelledError:
            outcome = "cancelled"
            raise
        except Exception as exc:
            outcome = "disconnected" if type(exc).__name__ == "ClientDisconnect" else "exception"
            raise
        finally:
            if trace.active:
                await asyncio.to_thread(trace.finish, outcome)
