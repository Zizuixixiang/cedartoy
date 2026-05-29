from fastapi import APIRouter, Depends, HTTPException

from auth_utils import create_token, current_player, hash_password, verify_password
from database import execute, fetch_one
from models import AuthBody
from utils import clean_content, public_player

router = APIRouter(prefix="/auth", tags=["auth"])


def _source(source: str) -> str:
    return "mcp" if source == "mcp" else "web"


@router.post("/guest")
async def guest_login():
    player_id = await execute("INSERT INTO players (is_guest, source) VALUES (1, 'web')")
    player = await fetch_one("SELECT * FROM players WHERE id = ?", (player_id,))
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
        "UPDATE players SET source = ?, is_ai = CASE WHEN ? = 'mcp' THEN 1 ELSE is_ai END, last_active_at = CURRENT_TIMESTAMP WHERE id = ?",
        (src, src, player["id"]),
    )
    player = await fetch_one("SELECT * FROM players WHERE id = ?", (player["id"],))
    return {"token": create_token(player), "player": public_player(player)}


@router.get("/me")
async def me(player: dict = Depends(current_player)):
    return {"player": public_player(player)}
