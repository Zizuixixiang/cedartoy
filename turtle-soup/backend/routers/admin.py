from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth_utils import admin_player, verify_password
from database import execute, fetch_all, fetch_one
from models import RoomCreateBody
from utils import clean_content

router = APIRouter(prefix="/admin", tags=["admin"])


class AdminPasswordBody(BaseModel):
    password: str


class ApiConfigBody(BaseModel):
    name: str
    api_url: str
    api_key: str = ""
    model: str
    enabled: int = 1
    priority: int = 0


class SettingBody(BaseModel):
    value: str = Field(max_length=100)


class BanBody(BaseModel):
    ip: str
    reason: str = ""


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
    return await fetch_all("SELECT * FROM puzzle_submissions WHERE status = 'pending' ORDER BY id DESC")


@router.post("/submissions/{submission_id}/add")
async def add_submission(submission_id: int, body: RoomCreateBody, admin: dict = Depends(admin_player)):
    sub = await fetch_one("SELECT * FROM puzzle_submissions WHERE id = ?", (submission_id,))
    if not sub:
        raise HTTPException(status_code=404, detail="投稿不存在")
    surface = clean_content(body.surface or sub["surface"], 500)
    answer = clean_content(body.answer or sub["answer"], 1000)
    await execute(
        "INSERT INTO puzzles (surface, answer, tags, created_by) VALUES (?, ?, ?, ?)",
        (surface, answer, (body.tags or sub["tags"])[:100], admin["id"]),
    )
    await execute("UPDATE puzzle_submissions SET status = 'added' WHERE id = ?", (submission_id,))
    return {"ok": True}


@router.post("/submissions/{submission_id}/ignore")
async def ignore_submission(submission_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE puzzle_submissions SET status = 'ignored' WHERE id = ?", (submission_id,))
    return {"ok": True}


@router.get("/players")
async def players(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT id, username, is_guest, is_ai, is_admin, source, ask_count, win_count, game_count, created_at, last_active_at FROM players ORDER BY id DESC")


@router.patch("/players/{player_id}/admin")
async def set_admin(player_id: int, enabled: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE players SET is_admin = ? WHERE id = ?", (1 if enabled else 0, player_id))
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
    await execute("DELETE FROM players WHERE id = ?", (player_id,))
    return {"ok": True}


@router.get("/rooms")
async def admin_rooms(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT id, surface, answer, status, created_by, winner_id, created_at, finished_at FROM rooms ORDER BY created_at DESC LIMIT 100")


@router.post("/rooms/{room_id}/finish")
async def finish_room(room_id: str, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE rooms SET status = 'finished', finished_at = CURRENT_TIMESTAMP WHERE id = ?", (room_id,))
    return {"ok": True}


@router.get("/reports")
async def reports(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM reports ORDER BY id DESC")


@router.post("/reports/{report_id}/resolve")
async def resolve_report(report_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE reports SET status = 'resolved' WHERE id = ?", (report_id,))
    return {"ok": True}


@router.get("/flags")
async def flags(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT * FROM flagged_content WHERE status = 'pending' ORDER BY id DESC")


@router.post("/flags/{flag_id}/resolve")
async def resolve_flag(flag_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE flagged_content SET status = 'resolved' WHERE id = ?", (flag_id,))
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


@router.get("/settings")
async def settings(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT key, value FROM settings ORDER BY key ASC")


@router.put("/settings/{key}")
async def update_setting(key: str, body: SettingBody, admin: dict = Depends(admin_player)):
    del admin
    await execute("INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)", (key, body.value))
    return {"ok": True}
