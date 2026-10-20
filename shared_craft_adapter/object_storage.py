"""Phase-one private PNG mirror. Local files/BLOBs remain authoritative.

No list/delete/public URL API; all keys are confined to one fixed bucket/prefix.
Only the satellite and the explicit backfill command load credentials.
"""
import configparser
from datetime import datetime, timezone
import hashlib
import hmac
import http.client
import logging
import os
from pathlib import Path
import queue
import re
import subprocess
import threading
import time
from urllib.parse import quote, urlsplit

BUCKET = "cedarclio-backup"
PREFIX = "shared-craft/v1"
MAX_IMAGE = 32 * 1024 * 1024
LOG = logging.getLogger(__name__)
ROOM = r"[1-9][0-9]{0,19}"
FILE = r"[a-f0-9]{1,64}(?:-display|-piece)?\.png"
KEY = re.compile(
    rf"{PREFIX}/rooms/({ROOM})/(gallery|finished|shares)/"
    r"([a-f0-9]{1,64}(?:-display|-piece)?)/([a-f0-9]{64})\.png"
)


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def object_key(room, kind, name, sha):
    key = f"{PREFIX}/rooms/{room}/{kind}/{name}/{sha}.png"
    validate_key(key)
    return key


def validate_key(key):
    if not isinstance(key, str) or not KEY.fullmatch(key):
        raise ValueError("invalid shared-craft object key")
    return key


def file_identity(root, path):
    """Reject symlinks, traversal and anything outside the two image folders."""
    root, path = Path(root).absolute(), Path(path).absolute()
    relative = path.relative_to(root)
    if (len(relative.parts) != 4 or relative.parts[0] != "rooms"
            or not re.fullmatch(ROOM, relative.parts[1])
            or relative.parts[2] not in {"gallery", "shared-bead-finished"}
            or not re.fullmatch(FILE, relative.parts[3])):
        raise ValueError("invalid shared-craft image path")
    current = root
    if current.is_symlink():
        raise ValueError("symlink craft root")
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("symlink craft image path")
    if path.resolve() != root.resolve() / relative:
        raise ValueError("image path escapes craft root")
    return (relative.parts[1],
            "gallery" if relative.parts[2] == "gallery" else "finished",
            path.stem)


def signed_headers(method, host, uri, raw, access, secret, region="auto",
                   stamp=None, token=""):
    """AWS SigV4, single-chunk payload; no query, redirects or unsigned body."""
    stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    headers = {"host": host, "x-amz-content-sha256": digest(raw), "x-amz-date": stamp}
    if token:
        headers["x-amz-security-token"] = token
    names = ";".join(sorted(headers))
    canonical_headers = "".join(f"{name}:{headers[name]}\n" for name in sorted(headers))
    canonical = "\n".join((method, uri, "", canonical_headers, names, digest(raw)))
    scope = f"{stamp[:8]}/{region}/s3/aws4_request"
    message = "\n".join(("AWS4-HMAC-SHA256", stamp, scope, digest(canonical.encode())))
    key = ("AWS4" + secret).encode()
    for part in (stamp[:8], region, "s3", "aws4_request"):
        key = hmac.new(key, part.encode(), hashlib.sha256).digest()
    signature = hmac.new(key, message.encode(), hashlib.sha256).hexdigest()
    headers["authorization"] = (
        f"AWS4-HMAC-SHA256 Credential={access}/{scope},"
        f"SignedHeaders={names},Signature={signature}"
    )
    return headers


class S3:
    """One reusable HTTPS connection, serialized across reader/background writer."""
    def __init__(self, endpoint, access, secret, region="auto", token="", timeout=2):
        url = urlsplit(endpoint)
        if (url.scheme != "https" or not url.hostname or url.username or url.password
                or url.path not in ("", "/") or url.query or url.fragment):
            raise ValueError("R2 endpoint must be an HTTPS origin")
        if not access or not secret:
            raise ValueError("missing R2 credentials")
        self.host, self.port = url.hostname, url.port
        self.authority = url.netloc
        self.access, self.secret, self.region, self.token = access, secret, region, token
        self.timeout = timeout
        self.connection = None
        self.lock = threading.Lock()

    def _request(self, method, key, raw=b""):
        validate_key(key)
        if method not in {"GET", "PUT"} or len(raw) > MAX_IMAGE:
            raise ValueError("unsupported object operation/size")
        uri = quote(f"/{BUCKET}/{key}", safe="/")
        headers = signed_headers(method, self.authority, uri, raw,
                                 self.access, self.secret, self.region, token=self.token)
        if method == "PUT":
            headers["content-type"] = "image/png"
        # Bound lock waiting too: a slow background upload must not indefinitely
        # stall an authenticated foreground reader.
        if not self.lock.acquire(timeout=self.timeout):
            raise TimeoutError("R2 connection busy")
        try:
            if self.connection is None:
                self.connection = http.client.HTTPSConnection(self.host, self.port, timeout=self.timeout)
            self.connection.request(method, uri, body=raw, headers=headers)
            response = self.connection.getresponse()
            body = response.read(MAX_IMAGE + 1)
            if len(body) > MAX_IMAGE:
                raise ValueError("R2 response exceeds image limit")
            if response.status == 404 and method == "GET":
                return None
            if response.status != 200:
                # Never log remote XML, credentials, or authorization headers.
                raise OSError(f"R2 HTTP {response.status}")
            return body
        except Exception:
            if self.connection:
                self.connection.close()
            self.connection = None
            raise
        finally:
            self.lock.release()

    def get(self, key):
        return self._request("GET", key)

    def put(self, key, raw):
        validate_key(key)
        if key.rsplit("/", 1)[1] != digest(raw) + ".png":
            raise ValueError("upload key/hash mismatch")
        self._request("PUT", key, raw)

    def close(self):
        with self.lock:
            if self.connection:
                self.connection.close()
            self.connection = None

    @classmethod
    def from_env(cls, env=None):
        env = os.environ if env is None else env
        # Existing verified remote; exactly one local subprocess per startup.
        # Capture output and sanitize failures: never print config or stderr.
        try:
            args = [env.get("CRAFT_R2_RCLONE", "/snap/bin/rclone")]
            if env.get("CRAFT_R2_RCLONE_CONFIG"):
                args += ["--config", env["CRAFT_R2_RCLONE_CONFIG"]]
            args += ["config", "show", "cloudflare_r2"]
            result = subprocess.run(args, capture_output=True, text=True, timeout=10, check=True)
            config = configparser.ConfigParser(interpolation=None)
            config.read_string(result.stdout)
            section = config["cloudflare_r2"]
            if section.get("type") != "s3" or section.get("provider") != "Cloudflare":
                raise ValueError("unexpected remote type")
            return cls(section["endpoint"], section["access_key_id"],
                       section["secret_access_key"], section.get("region") or "auto",
                       section.get("session_token", ""))
        except Exception:
            raise ValueError("cannot load cloudflare_r2 configuration (details redacted)") from None


def verified_get(backend, key, sha):
    validate_key(key)
    if key.rsplit("/", 1)[1] != sha + ".png":
        raise ValueError("key/hash mismatch")
    raw = backend.get(key)
    if raw is not None and digest(raw) != sha:
        raise ValueError("R2 SHA256 mismatch")
    return raw


def ensure_object(backend, key, raw):
    """Idempotent, read-before-write and mandatory full readback after PUT."""
    sha = digest(raw)
    existing = verified_get(backend, key, sha)
    if existing is not None:
        return "existing"
    backend.put(key, raw)
    if verified_get(backend, key, sha) != raw:
        raise ValueError("R2 upload readback failed")
    return "uploaded"


class Images:
    MODES = {"off", "mirror", "prefer-r2"}

    def __init__(self, root, mode="off", backend=None, rooms=None):
        if mode not in self.MODES:
            raise ValueError("CRAFT_R2_MODE must be off, mirror or prefer-r2")
        self.root, self.mode, self.backend = Path(root), mode, backend
        self.rooms = None if rooms is None else frozenset(rooms)
        if self.rooms is not None and any(not re.fullmatch(ROOM, r) for r in self.rooms):
            raise ValueError("invalid CRAFT_R2_ROOMS")
        self.retry_at = 0
        self.queue = queue.Queue(maxsize=8)
        self.worker = None
        self.worker_lock = threading.Lock()
        self.closed = False

    @classmethod
    def from_env(cls, root, env=None):
        env = os.environ if env is None else env
        mode = env.get("CRAFT_R2_MODE", "off")
        rooms = env.get("CRAFT_R2_ROOMS", "").strip()
        result = cls(root, mode, rooms=rooms.split(",") if rooms else None)
        if mode != "off":
            try:
                result.backend = S3.from_env(env)
            except Exception:
                LOG.warning("Shared craft R2 configuration unavailable; using local images")
        return result

    def enabled(self, room):
        return (self.mode != "off" and self.backend is not None
                and (self.rooms is None or room in self.rooms))

    def _failure(self):
        self.retry_at = time.monotonic() + 30
        LOG.warning("Shared craft R2 unavailable or SHA check failed; local image retained")

    def read(self, room, kind, name, local):
        if self.mode != "prefer-r2" or not self.enabled(room) or time.monotonic() < self.retry_at:
            return local
        key = object_key(room, kind, name, digest(local))
        try:
            remote = verified_get(self.backend, key, digest(local))
            return local if remote is None else remote
        except Exception:
            self._failure()
            return local

    def read_file(self, path):
        identity = file_identity(self.root, path)
        return self.read(*identity, Path(path).read_bytes())

    def mirror_file(self, path):
        identity = file_identity(self.root, path)
        if self.enabled(identity[0]):
            self.mirror(*identity, Path(path).read_bytes())

    def mirror(self, room, kind, name, raw):
        if not self.enabled(room) or time.monotonic() < self.retry_at:
            return
        key = object_key(room, kind, name, digest(raw))
        if len(raw) > MAX_IMAGE:
            self._failure()
            return
        with self.worker_lock:
            if self.closed:
                return
            if self.worker is None:
                self.worker = threading.Thread(target=self._run, daemon=True, name="craft-r2")
                self.worker.start()
            try:
                self.queue.put_nowait((key, raw))
            except queue.Full:
                LOG.warning("Shared craft R2 queue full; local image retained; backfill required")

    def _run(self):
        while True:
            item = self.queue.get()
            try:
                if item is None:
                    return
                if time.monotonic() >= self.retry_at:
                    ensure_object(self.backend, *item)
            except Exception:
                self._failure()
            finally:
                self.queue.task_done()

    def close(self):
        with self.worker_lock:
            if self.closed:
                return
            self.closed = True
        if self.worker:
            self.queue.put(None)
            self.worker.join()
        if self.backend:
            self.backend.close()
