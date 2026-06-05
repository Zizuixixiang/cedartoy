from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from auth_utils import create_token, current_player, hash_password, verify_password
from database import execute, fetch_one, get_db
from models import AuthBody
from utils import SQL_NOW, clean_content, public_player

router = APIRouter(prefix="/auth", tags=["auth"])
GUEST_NUMBER_MAX = 999
GUEST_NEXT_NUMBER_KEY = "guest_next_number"


def _source(source: str) -> str:
    return "mcp" if source == "mcp" else "web"


class GuestRequest(BaseModel):
    user_id: Optional[int] = None


async def _create_guest_player() -> dict:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            "INSERT OR IGNORE INTO settings (key, value) VALUES (?, '1')",
            (GUEST_NEXT_NUMBER_KEY,),
        )
        row = await db.execute_fetchall(
            "SELECT value FROM settings WHERE key = ?",
            (GUEST_NEXT_NUMBER_KEY,),
        )
        try:
            start = int(row[0]["value"]) if row else 1
        except Exception:
            start = 1
        start = ((start - 1) % GUEST_NUMBER_MAX) + 1
        for offset in range(GUEST_NUMBER_MAX):
            number = ((start - 1 + offset) % GUEST_NUMBER_MAX) + 1
            username = f"游客{number}"
            existing = await db.execute_fetchall(
                "SELECT id FROM players WHERE username = ?",
                (username,),
            )
            if existing:
                continue
            cur = await db.execute(
                "INSERT INTO players (username, is_guest, source) VALUES (?, 1, 'web')",
                (username,),
            )
            next_number = (number % GUEST_NUMBER_MAX) + 1
            await db.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (str(next_number), GUEST_NEXT_NUMBER_KEY),
            )
            await db.commit()
            player_id = int(cur.lastrowid)
            player = await db.execute_fetchall("SELECT * FROM players WHERE id = ?", (player_id,))
            return dict(player[0])
        raise HTTPException(status_code=503, detail="游客编号已用完，请稍后再试")
    finally:
        await db.close()


@router.post("/guest")
async def guest_login(body: GuestRequest | None = None):
    user_id = body.user_id if body else None
    if user_id:
        toy_user = await fetch_one("SELECT username, is_ai, is_admin FROM toy_users WHERE id = ? AND deleted_at IS NULL", (user_id,))
        if not toy_user:
            raise HTTPException(status_code=401, detail="统一账号不存在或已删除")
        named_player = await fetch_one("SELECT * FROM players WHERE username = ?", (toy_user["username"],))
        player = await fetch_one("SELECT * FROM players WHERE user_id = ?", (user_id,))
        if named_player and (not named_player.get("user_id") or int(named_player["user_id"]) == int(user_id)):
            if player and player["id"] != named_player["id"]:
                await execute("UPDATE players SET user_id = NULL WHERE id = ?", (player["id"],))
            player = named_player
        if not player:
            player_id = await execute(
                "INSERT INTO players (username, user_id, is_guest, is_ai, is_admin, source) VALUES (?, ?, 0, ?, ?, 'web')",
                (toy_user["username"], user_id, 1 if toy_user["is_ai"] else 0, 1 if toy_user["is_admin"] else 0),
            )
            player = await fetch_one("SELECT * FROM players WHERE id = ?", (player_id,))
        else:
            await execute(
                f"UPDATE players SET user_id = ?, is_guest = 0, is_ai = ?, is_admin = ?, source = 'web', last_active_at = {SQL_NOW} WHERE id = ?",
                (user_id, 1 if toy_user["is_ai"] else 0, 1 if toy_user["is_admin"] else 0, player["id"]),
            )
            player = await fetch_one("SELECT * FROM players WHERE id = ?", (player["id"],))
        return {"token": create_token(player), "player": public_player(player)}
    player = await _create_guest_player()
    return {"token": create_token(player), "player": public_player(player)}


@router.post("/register")
async def register(body: AuthBody):
    username = clean_content(body.username or "", 32)
    password = body.password or ""
    if len(password) < 4:
        raise HTTPException(status_code=400, detail="密码至少 4 位")
    if await fetch_one("SELECT id FROM players WHERE username = ?", (username,)):
        return await login(body)
    src = _source(body.source)
    player_id = await execute(
        "INSERT INTO players (username, password_hash, is_guest, is_ai, source) VALUES (?, ?, 0, ?, ?)",
        (username, hash_password(password), 1 if src == "mcp" else 0, src),
    )
    player = await fetch_one("SELECT * FROM players WHERE id = ?", (player_id,))
    return {"token": create_token(player), "player": public_player(player)}


@router.post("/login")
async def login(body: AuthBody):
    username = clean_content(body.username or "", 32)
    player = await fetch_one("SELECT * FROM players WHERE username = ?", (username,))
    if not player or not player.get("password_hash") or not verify_password(body.password or "", player["password_hash"]):
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    src = _source(body.source)
    await execute(
        f"UPDATE players SET source = ?, is_ai = CASE WHEN ? = 'mcp' THEN 1 ELSE is_ai END, last_active_at = {SQL_NOW} WHERE id = ?",
        (src, src, player["id"]),
    )
    player = await fetch_one("SELECT * FROM players WHERE id = ?", (player["id"],))
    return {"token": create_token(player), "player": public_player(player)}


@router.get("/me")
async def me(player: dict = Depends(current_player)):
    return {"player": public_player(player)}
