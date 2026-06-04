from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_utils import admin_player, verify_password
from database import execute, fetch_all, fetch_one
from judge import list_models, test_config
from models import RoomCreateBody
from utils import ANSWER_LIMIT, SURFACE_LIMIT, SQL_NOW, clean_content, room_id

router = APIRouter(prefix="/admin", tags=["admin"])


def _source(value: str) -> str:
    return value if value in {"web", "mcp"} else "web"


def _optional_clean(value: str, limit: int) -> str | None:
    text = (value or "").strip()
    return clean_content(text, limit) if text else None


async def _sync_toy_user_admin(player: dict, enabled: bool) -> None:
    if player.get("user_id"):
        await execute("UPDATE toy_users SET is_admin = ? WHERE id = ?", (1 if enabled else 0, player["user_id"]))
    elif player.get("username"):
        await execute("UPDATE toy_users SET is_admin = ? WHERE username = ?", (1 if enabled else 0, player["username"]))


class AdminPasswordBody(BaseModel):
    password: str


class ApiConfigBody(BaseModel):
    name: str
    api_url: str
    api_key: str = ""
    model: str
    enabled: int = 1
    priority: int = 0


class ApiModelsBody(BaseModel):
    config_id: Optional[int] = None
    name: str = ""
    api_url: str = ""
    api_key: str = ""
    model: str = ""


class ApiTestBody(BaseModel):
    config_id: Optional[int] = None
    name: str = ""
    api_url: str = ""
    api_key: str = ""
    model: str = ""


class SettingBody(BaseModel):
    value: str = Field(max_length=50000)


class BanBody(BaseModel):
    ip: str
    reason: str = ""


class SubmissionBody(BaseModel):
    surface: str = ""
    answer: str = ""
    tags: str = ""
    status: str = "pending"


class PlayerBody(BaseModel):
    username: str = ""
    is_guest: int = 0
    is_ai: int = 0
    is_admin: int = 0
    source: str = "web"


class RoomAdminBody(BaseModel):
    surface: str = ""
    answer: str = ""
    status: str = "waiting"
    winner_id: Optional[int] = None


class ReportAdminBody(BaseModel):
    reporter_id: Optional[int] = None
    target_player_id: Optional[int] = None
    room_id: Optional[str] = None
    log_id: Optional[int] = None
    reason: str = ""
    status: str = "pending"


class ReportBody(BaseModel):
    reason: str = ""
    status: str = "pending"


class FlagBody(BaseModel):
    type: str = "manual"
    ref_id: int = 0
    reason: str = ""
    status: str = "pending"


@router.post("/verify")
async def verify_admin_password(body: AdminPasswordBody, admin: dict = Depends(admin_player)):
    if not verify_password(body.password, admin["password_hash"] or ""):
        raise HTTPException(status_code=401, detail="密码错误")
    return {"ok": True}


@router.get("/overview")
async def overview(admin: dict = Depends(admin_player)):
    del admin
    tables = ["players", "rooms", "puzzles", "puzzle_submissions", "reports", "flagged_content"]
    out = {}
    for table in tables:
        out[table] = (await fetch_one(f"SELECT COUNT(*) AS c FROM {table}"))["c"]
    return out


@router.get("/submissions")
async def submissions(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM puzzle_submissions ORDER BY id DESC")


@router.post("/submissions/{submission_id}/add")
async def add_submission(submission_id: int, body: RoomCreateBody, admin: dict = Depends(admin_player)):
    sub = await fetch_one("SELECT * FROM puzzle_submissions WHERE id = ?", (submission_id,))
    if not sub:
        raise HTTPException(status_code=404, detail="投稿不存在")
    surface = clean_content(body.surface or sub["surface"], SURFACE_LIMIT)
    answer = clean_content(body.answer or sub["answer"], ANSWER_LIMIT)
    await execute(
        "INSERT INTO puzzles (title, surface, answer, tags, created_by) VALUES (?, ?, ?, ?, ?)",
        ("", surface, answer, (body.tags or sub["tags"])[:100], admin["id"]),
    )
    await execute("UPDATE puzzle_submissions SET status = 'added' WHERE id = ?", (submission_id,))
    return {"ok": True}


@router.post("/submissions/{submission_id}/ignore")
async def ignore_submission(submission_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE puzzle_submissions SET status = 'ignored' WHERE id = ?", (submission_id,))
    return {"ok": True}


@router.post("/submissions")
async def create_submission(body: SubmissionBody, admin: dict = Depends(admin_player)):
    del admin
    sid = await execute(
        "INSERT INTO puzzle_submissions (surface, answer, tags, status) VALUES (?, ?, ?, ?)",
        (
            clean_content(body.surface, SURFACE_LIMIT),
            clean_content(body.answer, ANSWER_LIMIT),
            body.tags[:100],
            body.status[:32],
        ),
    )
    return {"id": sid}


@router.put("/submissions/{submission_id}")
async def update_submission(submission_id: int, body: SubmissionBody, admin: dict = Depends(admin_player)):
    del admin
    existing = await fetch_one("SELECT id FROM puzzle_submissions WHERE id = ?", (submission_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="投稿不存在")
    await execute(
        "UPDATE puzzle_submissions SET surface = ?, answer = ?, tags = ?, status = ? WHERE id = ?",
        (
            clean_content(body.surface, SURFACE_LIMIT),
            clean_content(body.answer, ANSWER_LIMIT),
            body.tags[:100],
            body.status[:32],
            submission_id,
        ),
    )
    return {"ok": True}


@router.delete("/submissions/{submission_id}")
async def delete_submission(submission_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM puzzle_submissions WHERE id = ?", (submission_id,))
    return {"ok": True}


@router.get("/players")
async def players(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT id, username, user_id, is_guest, is_ai, is_admin, source, ask_count, win_count, game_count, created_at, last_active_at FROM players ORDER BY id DESC")


@router.post("/players")
async def create_player(body: PlayerBody, admin: dict = Depends(admin_player)):
    del admin
    username = _optional_clean(body.username, 32)
    pid = await execute(
        "INSERT INTO players (username, is_guest, is_ai, is_admin, source) VALUES (?, ?, ?, ?, ?)",
        (username, 1 if body.is_guest else 0, 1 if body.is_ai else 0, 1 if body.is_admin else 0, _source(body.source)),
    )
    return {"id": pid}


@router.put("/players/{player_id}")
async def update_player(player_id: int, body: PlayerBody, admin: dict = Depends(admin_player)):
    del admin
    existing = await fetch_one("SELECT id, username, user_id FROM players WHERE id = ?", (player_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="玩家不存在")
    username = _optional_clean(body.username, 32)
    enabled = bool(body.is_admin)
    await execute(
        "UPDATE players SET username = ?, is_guest = ?, is_ai = ?, is_admin = ?, source = ? WHERE id = ?",
        (username, 1 if body.is_guest else 0, 1 if body.is_ai else 0, 1 if enabled else 0, _source(body.source), player_id),
    )
    await _sync_toy_user_admin(existing | {"username": username or existing.get("username")}, enabled)
    return {"ok": True}


@router.patch("/players/{player_id}/admin")
async def set_admin(player_id: int, enabled: int, admin: dict = Depends(admin_player)):
    del admin
    player = await fetch_one("SELECT id, username, user_id FROM players WHERE id = ?", (player_id,))
    if not player:
        raise HTTPException(status_code=404, detail="玩家不存在")
    is_enabled = bool(enabled)
    await execute("UPDATE players SET is_admin = ? WHERE id = ?", (1 if is_enabled else 0, player_id))
    await _sync_toy_user_admin(player, is_enabled)
    return {"ok": True}


@router.post("/players/{player_id}/reset")
async def reset_stats(player_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute(
        "UPDATE players SET ask_count=0, ask_count_y=0, ask_count_n=0, ask_count_u=0, ask_count_p=0, win_count=0, game_count=0 WHERE id = ?",
        (player_id,),
    )
    return {"ok": True}


@router.delete("/players/{player_id}")
async def delete_player(player_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE puzzles SET created_by = NULL WHERE created_by = ?", (player_id,))
    await execute("UPDATE puzzle_submissions SET submitted_by = NULL WHERE submitted_by = ?", (player_id,))
    await execute("UPDATE rooms SET created_by = NULL WHERE created_by = ?", (player_id,))
    await execute("UPDATE rooms SET winner_id = NULL WHERE winner_id = ?", (player_id,))
    await execute("UPDATE game_logs SET player_id = NULL WHERE player_id = ?", (player_id,))
    await execute("UPDATE room_notes SET player_id = NULL WHERE player_id = ?", (player_id,))
    await execute("UPDATE reports SET reporter_id = NULL WHERE reporter_id = ?", (player_id,))
    await execute("UPDATE reports SET target_player_id = NULL WHERE target_player_id = ?", (player_id,))
    await execute("UPDATE ban_ips SET banned_by = NULL WHERE banned_by = ?", (player_id,))
    await execute("DELETE FROM players WHERE id = ?", (player_id,))
    return {"ok": True}


@router.get("/rooms")
async def admin_rooms(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT id, surface, answer, status, created_by, winner_id, created_at, finished_at FROM rooms ORDER BY created_at DESC LIMIT 100")


@router.post("/rooms")
async def create_admin_room(body: RoomAdminBody, admin: dict = Depends(admin_player)):
    rid = room_id()
    while await fetch_one("SELECT id FROM rooms WHERE id = ?", (rid,)):
        rid = room_id()
    status = body.status[:32] or "waiting"
    await execute(
        f"INSERT INTO rooms (id, surface, answer, status, created_by, winner_id, finished_at) VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ? = 'finished' THEN {SQL_NOW} ELSE NULL END)",
        (
            rid,
            clean_content(body.surface, SURFACE_LIMIT),
            clean_content(body.answer, ANSWER_LIMIT),
            status,
            admin["id"],
            body.winner_id,
            status,
        ),
    )
    return {"id": rid}


@router.post("/rooms/{room_id}/finish")
async def finish_room(room_id: str, admin: dict = Depends(admin_player)):
    del admin
    await execute(f"UPDATE rooms SET status = 'finished', finished_at = {SQL_NOW} WHERE id = ?", (room_id,))
    return {"ok": True}


@router.put("/rooms/{room_id}")
async def update_room(room_id: str, body: RoomAdminBody, admin: dict = Depends(admin_player)):
    del admin
    existing = await fetch_one("SELECT id FROM rooms WHERE id = ?", (room_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="房间不存在")
    finished_at_sql = f", finished_at = CASE WHEN ? = 'finished' THEN COALESCE(finished_at, {SQL_NOW}) ELSE NULL END"
    await execute(
        f"UPDATE rooms SET surface = ?, answer = ?, status = ?, winner_id = ?{finished_at_sql} WHERE id = ?",
        (
            clean_content(body.surface, SURFACE_LIMIT),
            clean_content(body.answer, ANSWER_LIMIT),
            body.status[:32],
            body.winner_id,
            body.status[:32],
            room_id,
        ),
    )
    return {"ok": True}


@router.delete("/rooms/{room_id}")
async def delete_room(room_id: str, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE reports SET room_id = NULL WHERE room_id = ?", (room_id,))
    await execute(
        "UPDATE reports SET log_id = NULL WHERE log_id IN (SELECT id FROM game_logs WHERE room_id = ?)",
        (room_id,),
    )
    await execute("DELETE FROM room_notes WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM game_logs WHERE room_id = ?", (room_id,))
    await execute("DELETE FROM rooms WHERE id = ?", (room_id,))
    return {"ok": True}


@router.get("/reports")
async def reports(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM reports ORDER BY id DESC")


@router.post("/reports")
async def create_report(body: ReportAdminBody, admin: dict = Depends(admin_player)):
    del admin
    rid = await execute(
        "INSERT INTO reports (reporter_id, target_player_id, room_id, log_id, reason, status) VALUES (?, ?, ?, ?, ?, ?)",
        (
            body.reporter_id,
            body.target_player_id,
            (body.room_id or None),
            body.log_id,
            body.reason[:200],
            body.status[:32],
        ),
    )
    return {"id": rid}


@router.put("/reports/{report_id}")
async def update_report(report_id: int, body: ReportAdminBody, admin: dict = Depends(admin_player)):
    del admin
    await execute(
        "UPDATE reports SET reporter_id = ?, target_player_id = ?, room_id = ?, log_id = ?, reason = ?, status = ? WHERE id = ?",
        (
            body.reporter_id,
            body.target_player_id,
            (body.room_id or None),
            body.log_id,
            body.reason[:200],
            body.status[:32],
            report_id,
        ),
    )
    return {"ok": True}


@router.post("/reports/{report_id}/resolve")
async def resolve_report(report_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE reports SET status = 'resolved' WHERE id = ?", (report_id,))
    return {"ok": True}


@router.delete("/reports/{report_id}")
async def delete_report(report_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM reports WHERE id = ?", (report_id,))
    return {"ok": True}


@router.get("/flags")
async def flags(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM flagged_content ORDER BY id DESC")


@router.post("/flags")
async def create_flag(body: FlagBody, admin: dict = Depends(admin_player)):
    del admin
    fid = await execute(
        "INSERT INTO flagged_content (type, ref_id, reason, status) VALUES (?, ?, ?, ?)",
        (body.type[:32], body.ref_id, body.reason[:200], body.status[:32]),
    )
    return {"id": fid}


@router.put("/flags/{flag_id}")
async def update_flag(flag_id: int, body: FlagBody, admin: dict = Depends(admin_player)):
    del admin
    await execute(
        "UPDATE flagged_content SET type = ?, ref_id = ?, reason = ?, status = ? WHERE id = ?",
        (body.type[:32], body.ref_id, body.reason[:200], body.status[:32], flag_id),
    )
    return {"ok": True}


@router.post("/flags/{flag_id}/resolve")
async def resolve_flag(flag_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE flagged_content SET status = 'resolved' WHERE id = ?", (flag_id,))
    return {"ok": True}


@router.delete("/flags/{flag_id}")
async def delete_flag(flag_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM flagged_content WHERE id = ?", (flag_id,))
    return {"ok": True}


@router.get("/bans")
async def bans(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM ban_ips ORDER BY id DESC")


@router.post("/bans")
async def add_ban(body: BanBody, admin: dict = Depends(admin_player)):
    await execute(
        "INSERT OR REPLACE INTO ban_ips (ip, reason, banned_by) VALUES (?, ?, ?)",
        (body.ip.strip(), body.reason[:200], admin["id"]),
    )
    return {"ok": True}


@router.put("/bans/{ban_id}")
async def update_ban(ban_id: int, body: BanBody, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE ban_ips SET ip = ?, reason = ? WHERE id = ?", (body.ip.strip(), body.reason[:200], ban_id))
    return {"ok": True}


@router.delete("/bans/{ban_id}")
async def remove_ban(ban_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM ban_ips WHERE id = ?", (ban_id,))
    return {"ok": True}


@router.get("/api-configs")
async def api_configs(admin: dict = Depends(admin_player)):
    del admin
    rows = await fetch_all("SELECT * FROM judge_api_configs ORDER BY priority ASC, id ASC")
    for row in rows:
        key = row.get("api_key") or ""
        row["api_key"] = f"{key[:4]}...{key[-4:]}" if len(key) > 8 else "****"
    return rows


@router.post("/api-configs")
async def add_api_config(body: ApiConfigBody, admin: dict = Depends(admin_player)):
    del admin
    cid = await execute(
        "INSERT INTO judge_api_configs (name, api_url, api_key, model, enabled, priority) VALUES (?, ?, ?, ?, ?, ?)",
        (body.name, body.api_url, body.api_key, body.model, body.enabled, body.priority),
    )
    return {"id": cid}


@router.post("/api-configs/models")
async def fetch_api_models(body: ApiModelsBody, admin: dict = Depends(admin_player)):
    del admin
    existing = None
    if body.config_id:
        existing = await fetch_one("SELECT * FROM judge_api_configs WHERE id = ?", (body.config_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="配置不存在")
    cfg = {
        "api_url": body.api_url.strip() or (existing["api_url"] if existing else ""),
        "api_key": body.api_key.strip() or (existing["api_key"] if existing else ""),
    }
    return await list_models(cfg)


@router.post("/api-configs/test")
async def test_api_config_draft(body: ApiTestBody, admin: dict = Depends(admin_player)):
    del admin
    existing = None
    if body.config_id:
        existing = await fetch_one("SELECT * FROM judge_api_configs WHERE id = ?", (body.config_id,))
        if not existing:
            raise HTTPException(status_code=404, detail="配置不存在")
    cfg = {
        "name": body.name.strip() or (existing["name"] if existing else ""),
        "api_url": body.api_url.strip() or (existing["api_url"] if existing else ""),
        "api_key": body.api_key.strip() or (existing["api_key"] if existing else ""),
        "model": body.model.strip() or (existing["model"] if existing else ""),
    }
    return await test_config(cfg)


@router.put("/api-configs/{config_id}")
async def update_api_config(config_id: int, body: ApiConfigBody, admin: dict = Depends(admin_player)):
    del admin
    existing = await fetch_one("SELECT * FROM judge_api_configs WHERE id = ?", (config_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="配置不存在")
    key = body.api_key or existing["api_key"]
    await execute(
        "UPDATE judge_api_configs SET name=?, api_url=?, api_key=?, model=?, enabled=?, priority=? WHERE id=?",
        (body.name, body.api_url, key, body.model, body.enabled, body.priority, config_id),
    )
    return {"ok": True}


@router.delete("/api-configs/{config_id}")
async def delete_api_config(config_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM judge_api_configs WHERE id = ?", (config_id,))
    return {"ok": True}


@router.post("/api-configs/{config_id}/test")
async def test_api_config(config_id: int, admin: dict = Depends(admin_player)):
    del admin
    cfg = await fetch_one("SELECT * FROM judge_api_configs WHERE id = ?", (config_id,))
    if not cfg:
        raise HTTPException(status_code=404, detail="配置不存在")
    return await test_config(cfg)


@router.get("/settings")
async def settings(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT key, value FROM settings ORDER BY key ASC")


@router.put("/settings/{key}")
async def update_setting(key: str, body: SettingBody, admin: dict = Depends(admin_player)):
    del admin
    await execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, body.value))
    return {"ok": True}


@router.delete("/settings/{key}")
async def delete_setting(key: str, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM settings WHERE key = ?", (key,))
    return {"ok": True}
