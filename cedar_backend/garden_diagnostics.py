"""Opt-in, bounded human-web incident telemetry; independent of MCP diagnostics."""
import json
import math
from pathlib import Path
import re
import secrets
import threading
import time

ACTIVATION = Path(__file__).resolve().parents[1] / "data/garden_diagnostics_activation.json"
TARGET_ACCOUNT_ID = 13016
ROUTES = {"/": "page", "/api/catalog": "catalog", "/web/status": "status", "/web/cmd": "cmd"}
_lock = threading.Lock()
_counts = {}


def deadline(user_id):
    try:
        if user_id != TARGET_ACCOUNT_ID:
            return 0
        record = json.loads(ACTIVATION.read_text())
        start, end = record["enabled_at"], record["expires_at"]
        if (type(start) is int and type(end) is int and 0 < end - start <= 172800
                and start <= time.time() < end and record["account_id"] == TARGET_ACCOUNT_ID):
            return end
    except Exception:
        pass
    return 0


def admit(until, kind):
    if not until or time.time() >= until:
        return False
    with _lock:
        minute = int(time.monotonic() // 60)
        # Fixed two buckets, bounded memory, reset only for a new absolute expiry.
        old_until, old_minute, count, total = _counts.get(kind, (0, 0, 0, 0))
        if old_until != until:
            count = total = 0
        elif old_minute != minute:
            count = 0
        if count >= 60 or total >= 3000:
            return False
        _counts[kind] = (until, minute, count + 1, total + 1)
        return True


def request_id(value):
    return value if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) else secrets.token_hex(16)


def number(value):
    return round(value, 2) if type(value) in (int, float) and 0 <= value <= 100_000_000 and math.isfinite(value) else None


def browser_metrics(report):
    """Rebuild a small fixed schema; never serialize arbitrary client dictionaries."""
    result = {}
    for key in ("elapsed_ms", "script_start_ms", "script_eval_ms", "headers_ms", "body_ms", "total_ms"):
        result[key] = number(report.get(key))
    result["visibility"] = report.get("visibility") if report.get("visibility") in ("visible", "hidden") else "unknown"
    result["outcome"] = report.get("outcome") if report.get("outcome") in ("ok", "http_error", "network_error", "parse_error", "pending") else "unknown"
    result["status"] = report.get("status") if type(report.get("status")) is int and 100 <= report["status"] <= 599 else None
    resources = report.get("resources")
    if isinstance(resources, dict):
        result["resources"] = {}
        for name in ("js", "css"):
            raw = resources.get(name)
            if not isinstance(raw, dict):
                continue
            result["resources"][name] = {key: number(raw.get(key)) for key in (
                "start_ms", "duration_ms", "ttfb_ms", "download_ms", "transfer_bytes", "encoded_bytes", "decoded_bytes")}
            result["resources"][name]["cache"] = raw.get("cache") if raw.get("cache") in ("local", "network", "unknown") else "unknown"
    return result


def log_browser(logger, user_id, report):
    until = deadline(user_id)
    if not admit(until, "browser"):
        return
    rid = report.get("request_id")
    if not isinstance(rid, str) or not re.fullmatch(r"[0-9a-f]{32}", rid):
        return
    route = report.get("route") if report.get("route") in ("catalog", "status", "cmd") else "boot"
    page_id = report.get("page_id")
    page_id = page_id if isinstance(page_id, str) and re.fullmatch(r"[0-9a-f]{32}", page_id) else None
    event = report.get("event") if report.get("event") in ("page_start", "ready", "error", "timeout", "request") else "unknown"
    phase = report.get("phase") if report.get("phase") in ("script_load", "script_start", "catalog_request", "catalog_ready", "state_request", "ready", "request") else "unknown"
    try:
        logger.info("garden_browser %s", json.dumps({"request_id": rid, "page_id": page_id,
                    "route": route, "event": event, "phase": phase,
                    "user_id": user_id, **browser_metrics(report)}, separators=(",", ":")))
    except Exception:
        pass


class Trace:
    def __init__(self, incoming_id, route):
        self.request_id = request_id(incoming_id)
        self.route = ROUTES.get(route)
        self.started = self.previous = time.monotonic()
        self.until = 0
        self.active = False
        self.status = None
        self.fields = {}

    def identify(self, user_id):
        self.until = deadline(user_id) if self.route else 0
        self.active = admit(self.until, "server")
        self.mark("auth_ms")

    def mark(self, name):
        now = time.monotonic()
        if self.active:
            self.fields[name] = round((now - self.previous) * 1000, 2)
        self.previous = now

    def finish(self, logger):
        if not self.active or time.time() >= self.until:
            return
        try:
            logger.info("garden_proxy %s", json.dumps({"request_id": self.request_id,
                        "route": self.route, "user_id": TARGET_ACCOUNT_ID, "status": self.status,
                        "total_ms": round((time.monotonic() - self.started) * 1000, 2),
                        **self.fields}, separators=(",", ":")))
        except Exception:
            pass
