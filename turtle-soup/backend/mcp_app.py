from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from auth_utils import hash_password, verify_password
from database import execute, fetch_all, fetch_one
from models import ContentBody, HintResponseBody, RoomCreateBody
from routers.game import ask as game_ask
from routers.game import guess as game_guess
from routers.game import hint_respond as game_hint_respond
from routers.rooms import create_room
from utils import clean_content

router = APIRouter(prefix="/mcp", tags=["mcp"])


class PlayBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    game: str
    action: str | None = None
    username: str | None = None
    password: str | None = None
    room_id: str | None = None
    content: str | None = None
    log_id: int | None = None
    accept: bool | None = None


@router.post("/play")
async def play(body: PlayBody):
    if body.game != "turtle_soup":
        raise HTTPException(status_code=404, detail="未知游戏")
    if not body.action:
        raise HTTPException(status_code=400, detail="action 必填")
    if body.action == "list_rooms":
        return await fetch_all("SELECT id, surface, status, created_at FROM rooms WHERE status IN ('waiting','playing') ORDER BY created_at DESC")
    if body.action == "status":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        room = await fetch_one("SELECT id, surface, status, winner_id, created_at, finished_at FROM rooms WHERE id = ?", (body.room_id,))
        logs = await fetch_all("SELECT id, player_id, type, content, judgment, created_at FROM game_logs WHERE room_id = ? ORDER BY id ASC", (body.room_id,))
        return {"room": room, "logs": logs}
    player = await _mcp_player(body.username, body.password)
    if body.action == "register":
        return {"player_id": player["id"], "username": player.get("username"), "is_ai": bool(player["is_ai"])}
    if body.action == "create_random":
        return await create_room(RoomCreateBody(mode="random"), player)
    if body.action == "join":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        room = await fetch_one("SELECT id, surface, status, created_at FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        return room
    if body.action == "ask":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        return await game_ask(ContentBody(room_id=body.room_id, content=body.content), player)
    if body.action == "guess":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        return await game_guess(ContentBody(room_id=body.room_id, content=body.content), player)
    if body.action == "hint_respond":
        if not body.room_id or body.log_id is None or body.accept is None:
            raise HTTPException(status_code=400, detail="room_id、log_id、accept 必填")
        return await game_hint_respond(
            HintResponseBody(room_id=body.room_id, log_id=body.log_id, accept=body.accept),
            player,
        )
    raise HTTPException(status_code=400, detail="未知 action")


async def _mcp_player(username: str | None, password: str | None) -> dict:
    if username:
        username = clean_content(username, 32)
        row = await fetch_one("SELECT * FROM players WHERE username = ?", (username,))
        if row:
            if not verify_password(password or "", row["password_hash"] or ""):
                raise HTTPException(status_code=401, detail="用户名或密码错误")
            await execute("UPDATE players SET is_ai = 1, source = 'mcp', last_active_at = CURRENT_TIMESTAMP WHERE id = ?", (row["id"],))
            return await fetch_one("SELECT * FROM players WHERE id = ?", (row["id"],))
        if not password or len(password) < 4:
            raise HTTPException(status_code=400, detail="密码至少 4 位")
        pid = await execute(
            "INSERT INTO players (username, password_hash, is_ai, source) VALUES (?, ?, 1, 'mcp')",
            (username, hash_password(password)),
        )
        return await fetch_one("SELECT * FROM players WHERE id = ?", (pid,))
    pid = await execute("INSERT INTO players (is_guest, is_ai, source) VALUES (1, 1, 'mcp')")
    return await fetch_one("SELECT * FROM players WHERE id = ?", (pid,))
