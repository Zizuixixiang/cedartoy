"""CedarToy identity and save-management adapter for Camping Plaza.

The upstream FastAPI application remains untouched.  This module is the
resident service entrypoint: it maps CedarToy's trusted ``X-Player-Id`` header
to a stable private upstream session, hides native session identifiers from
clients, and exposes loopback-only save administration used by CedarToy.
"""

from __future__ import annotations

import contextvars
import hashlib
import importlib.util
import json
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict


ROOT = Path(__file__).resolve().parent
UPSTREAM_ROOT = ROOT / "vendor" / "Camping-Plaza" / "camping_plaza"

# The upstream reads this at import time.  Keep the production database inside
# data/ so the existing consistent SQLite backup job automatically includes it.
os.environ.setdefault(
    "CAMPING_PLAZA_DB_PATH",
    str(ROOT / "data" / "camping_plaza.db"),
)

def _load_upstream_module(name: str, filename: str):
    spec = importlib.util.spec_from_file_location(name, UPSTREAM_ROOT / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load Camping Plaza {filename}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_engine_module = _load_upstream_module("cedartoy_camping_plaza_engine", "game_engine.py")
_previous_game_engine = sys.modules.get("game_engine")
sys.modules["game_engine"] = _engine_module
try:
    upstream = _load_upstream_module("cedartoy_camping_plaza_api", "game_api.py")
finally:
    if _previous_game_engine is None:
        sys.modules.pop("game_engine", None)
    else:
        sys.modules["game_engine"] = _previous_game_engine
CampingPlazaEngine = _engine_module.CampingPlazaEngine


PLAYER_ID_RE = re.compile(
    r"^(?:guest:[A-Za-z0-9]{1,64}|[A-Za-z0-9]{1,64}(?::[1-5])?)$"
)
SAVE_FORMAT = "camping_plaza.runtime_snapshot.v1"
_PLAYER_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "camping_plaza_player_id", default=None
)
_ORIGINAL_GET_ENGINE = upstream.get_engine


def normalize_player_id(value: Any) -> str:
    player_id = str(value or "").strip()
    if not PLAYER_ID_RE.fullmatch(player_id):
        raise ValueError("invalid player id")
    return player_id


def session_id_for_player(player_id: str) -> str:
    """Return a stable opaque upstream key without exposing it to clients."""
    player_id = normalize_player_id(player_id)
    # This namespace mapping must survive CedarToy auth-secret rotation and a
    # backup restore.  It is an internal key, not an authentication credential.
    digest = hashlib.sha256(
        f"cedartoy:camping_plaza:v1:{player_id}".encode()
    ).hexdigest()[:32]
    return f"sess_{digest}"


def _cedartoy_get_engine(session_id=None, *, create_new=False):
    player_id = _PLAYER_ID.get()
    if player_id is not None:
        # A first read starts the player's camp.  Native query/body session IDs
        # are ignored whenever CedarToy supplied trusted identity.
        return _ORIGINAL_GET_ENGINE(
            session_id_for_player(player_id),
            create_new=True,
        )
    return _ORIGINAL_GET_ENGINE(session_id, create_new=create_new)


upstream.get_engine = _cedartoy_get_engine
app = upstream.app


@app.middleware("http")
async def cedar_identity(request: Request, call_next):
    raw_player_id = request.headers.get("X-Player-Id")
    token = None
    if raw_player_id:
        try:
            player_id = normalize_player_id(raw_player_id)
        except ValueError:
            return JSONResponse({"detail": "invalid player id"}, status_code=400)
        token = _PLAYER_ID.set(player_id)
        try:
            _remember_identity(player_id)
        except sqlite3.Error:
            return JSONResponse({"detail": "unable to persist CedarToy identity"}, status_code=500)
    try:
        if raw_player_id and request.method == "POST" and request.url.path == "/api/session":
            _cedartoy_get_engine(create_new=True)
            # The browser needs a local marker, not the native random session.
            return JSONResponse({"success": True, "session_id": "cedartoy"})
        return await call_next(request)
    finally:
        if token is not None:
            _PLAYER_ID.reset(token)


class SaveAdminRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    player_id: str | None = None
    source_player_id: str | None = None
    target_player_id: str | None = None
    save_data: dict[str, Any] | None = None
    confirm: bool = False


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    if host not in {"127.0.0.1", "::1"}:
        raise HTTPException(status_code=403, detail="internal save management is loopback-only")


def _db_path() -> Path:
    if upstream.DB_PATH == ":memory:":
        raise HTTPException(status_code=500, detail="file-backed SQLite is required")
    if os.environ.get("DATABASE_URL", "").strip().startswith(("postgres://", "postgresql://")):
        raise HTTPException(status_code=500, detail="CedarToy save administration requires SQLite")
    return Path(upstream.DB_PATH)


def _connect() -> sqlite3.Connection:
    path = _db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS runtime_snapshot (
            session_id TEXT PRIMARY KEY,
            snapshot_json TEXT NOT NULL,
            updated_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS cedartoy_identity (
            session_id TEXT PRIMARY KEY,
            player_id TEXT NOT NULL UNIQUE,
            last_active TEXT NOT NULL DEFAULT (datetime('now', 'localtime'))
        )
        """
    )
    return conn


def _remember_identity(player_id: str) -> None:
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO cedartoy_identity (session_id, player_id, last_active)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(session_id) DO UPDATE SET
                player_id = excluded.player_id,
                last_active = excluded.last_active
            """,
            (session_id_for_player(player_id), player_id),
        )
        conn.commit()


def _snapshot_row(conn: sqlite3.Connection, player_id: str):
    return conn.execute(
        "SELECT snapshot_json, updated_at FROM runtime_snapshot WHERE session_id = ?",
        (session_id_for_player(player_id),),
    ).fetchone()


def _validate_snapshot(snapshot: Any) -> dict[str, Any]:
    if not isinstance(snapshot, dict):
        raise HTTPException(status_code=400, detail="save_data snapshot must be an object")
    required = {
        "snapshot_version",
        "state",
        "tents",
        "facilities",
        "npc_pool",
        "npc_id_counter",
    }
    if not required.issubset(snapshot):
        raise HTTPException(status_code=400, detail="save_data is missing required snapshot fields")
    if snapshot.get("snapshot_version") != CampingPlazaEngine.SNAPSHOT_VERSION:
        raise HTTPException(status_code=400, detail="unsupported Camping Plaza snapshot version")

    # Let the upstream restoration code validate every nested dataclass field in
    # an isolated database before the production row is touched.
    with tempfile.TemporaryDirectory(prefix="camping-import-") as tempdir:
        validation_db = Path(tempdir) / "validation.db"
        validation_session = "sess_" + "0" * 32
        with sqlite3.connect(validation_db) as conn:
            conn.execute(
                """
                CREATE TABLE runtime_snapshot (
                    session_id TEXT PRIMARY KEY,
                    snapshot_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "INSERT INTO runtime_snapshot VALUES (?, ?, datetime('now'))",
                (validation_session, json.dumps(snapshot, ensure_ascii=False)),
            )
        try:
            CampingPlazaEngine(
                db_path=str(validation_db),
                database_url="",
                session_id=validation_session,
                create_new=False,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"invalid Camping Plaza snapshot: {exc}") from exc
    return snapshot


def _unpack_save_data(save_data: Any) -> dict[str, Any]:
    if not isinstance(save_data, dict):
        raise HTTPException(status_code=400, detail="save_data must be an object")
    if save_data.get("format") == SAVE_FORMAT:
        return _validate_snapshot(save_data.get("snapshot"))
    # Accept a bare snapshot for forward compatibility with early local exports.
    return _validate_snapshot(save_data)


def _summary(snapshot: dict[str, Any], updated_at: str | None) -> dict[str, Any]:
    state = snapshot.get("state") if isinstance(snapshot.get("state"), dict) else {}
    return {
        "player_name": state.get("player_name"),
        "day": state.get("day"),
        "turn": state.get("turn"),
        "balance": state.get("balance"),
        "average_rating": state.get("average_rating"),
        "updated_at": updated_at,
    }


@app.post("/internal/saves/{action}")
def manage_save(action: str, payload: SaveAdminRequest, request: Request):
    _require_loopback(request)

    if action in {"summary", "export", "delete"}:
        try:
            player_id = normalize_player_id(payload.player_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    elif action == "import":
        try:
            player_id = normalize_player_id(payload.player_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        snapshot = _unpack_save_data(payload.save_data)
    elif action == "migrate":
        try:
            source_player_id = normalize_player_id(payload.source_player_id)
            target_player_id = normalize_player_id(payload.target_player_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if source_player_id == target_player_id:
            raise HTTPException(status_code=409, detail="source and target player ids are identical")
    elif action == "stats":
        with _connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS count FROM runtime_snapshot").fetchone()
        return {"save_count": int(row["count"])}
    else:
        raise HTTPException(status_code=404, detail="unknown save administration action")

    with _connect() as conn:
        if action == "summary":
            row = _snapshot_row(conn, player_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Camping Plaza save not found")
            snapshot = json.loads(row["snapshot_json"])
            return {"exists": True, "summary": _summary(snapshot, row["updated_at"])}

        if action == "export":
            row = _snapshot_row(conn, player_id)
            if row is None:
                raise HTTPException(status_code=404, detail="Camping Plaza save not found")
            snapshot = json.loads(row["snapshot_json"])
            _validate_snapshot(snapshot)
            return {
                "exported": True,
                "save_data": {"format": SAVE_FORMAT, "snapshot": snapshot},
            }

        conn.execute("BEGIN IMMEDIATE")
        if action == "delete":
            cur = conn.execute(
                "DELETE FROM runtime_snapshot WHERE session_id = ?",
                (session_id_for_player(player_id),),
            )
            conn.execute(
                "DELETE FROM cedartoy_identity WHERE session_id = ?",
                (session_id_for_player(player_id),),
            )
            conn.commit()
            return {"deleted": cur.rowcount > 0}

        if action == "import":
            existing = _snapshot_row(conn, player_id)
            if existing is not None and not payload.confirm:
                conn.rollback()
                raise HTTPException(status_code=409, detail="target save already exists; confirm is required")
            conn.execute(
                """
                INSERT INTO runtime_snapshot (session_id, snapshot_json, updated_at)
                VALUES (?, ?, datetime('now', 'localtime'))
                ON CONFLICT(session_id) DO UPDATE SET
                    snapshot_json = excluded.snapshot_json,
                    updated_at = excluded.updated_at
                """,
                (session_id_for_player(player_id), json.dumps(snapshot, ensure_ascii=False)),
            )
            conn.execute(
                """
                INSERT INTO cedartoy_identity (session_id, player_id, last_active)
                VALUES (?, ?, datetime('now', 'localtime'))
                ON CONFLICT(session_id) DO UPDATE SET
                    player_id = excluded.player_id,
                    last_active = excluded.last_active
                """,
                (session_id_for_player(player_id), player_id),
            )
            conn.commit()
            return {"imported": True}

        source_session = session_id_for_player(source_player_id)
        target_session = session_id_for_player(target_player_id)
        source_row = conn.execute(
            "SELECT 1 FROM runtime_snapshot WHERE session_id = ?", (source_session,)
        ).fetchone()
        if source_row is None:
            conn.rollback()
            raise HTTPException(status_code=404, detail="Camping Plaza source save not found")
        target_row = conn.execute(
            "SELECT 1 FROM runtime_snapshot WHERE session_id = ?", (target_session,)
        ).fetchone()
        if target_row is not None:
            conn.rollback()
            raise HTTPException(status_code=409, detail="Camping Plaza target save already exists")
        conn.execute(
            "UPDATE runtime_snapshot SET session_id = ? WHERE session_id = ?",
            (target_session, source_session),
        )
        conn.execute(
            """
            INSERT INTO cedartoy_identity (session_id, player_id, last_active)
            VALUES (?, ?, datetime('now', 'localtime'))
            ON CONFLICT(session_id) DO UPDATE SET
                player_id = excluded.player_id,
                last_active = excluded.last_active
            """,
            (target_session, target_player_id),
        )
        conn.execute("DELETE FROM cedartoy_identity WHERE session_id = ?", (source_session,))
        conn.commit()
        return {
            "migrated": True,
            "source_player_id": source_player_id,
            "target_player_id": target_player_id,
        }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8773")))
