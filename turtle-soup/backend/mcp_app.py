import base64
import hashlib
import hmac
import json
import os
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from auth_utils import hash_password
from database import execute, fetch_all, fetch_one, get_db
from models import ContentBody, GuessBody, HintRequestBody, HintResponseBody, NoteBody, RoomCreateBody
from routers.game import ask as game_ask
from routers.game import _ask_impl
from routers.game import generate as game_generate
from routers.game import guess as game_guess
from routers.game import hint_request as game_hint_request
from routers.game import hint_respond as game_hint_respond
from routers.notes import add_note, delete_note, update_note
from routers.rooms import close_room, create_room
from utils import SQL_NOW, clean_content

router = APIRouter(prefix="/mcp", tags=["mcp"])

TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
ROOM_FINISHED_STATUS_HINT = (
    "房间已结束，无法继续操作。请调用 "
    'play(game="turtle_soup", action="status", room_id=...) '
    "查看最终汤底和完整日志（未参与本局时请谨慎查看，可能包含剧透）。"
)


class PlayBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    game: str
    action: str | None = None
    path_token: str | None = None
    username: str | None = None
    password: str | None = None
    room_id: str | None = None
    content: str | None = None
    puzzle_id: int | None = None
    surface: str | None = None
    answer: str | None = None
    tags: str | None = None
    note_id: int | None = None
    log_id: int | None = None
    log_limit: int | None = None
    accept: bool | None = None


@router.post("/play")
async def play(body: PlayBody):
    if body.game != "turtle_soup":
        raise HTTPException(status_code=404, detail="未知游戏")
    if not body.action:
        raise HTTPException(status_code=400, detail="action 必填")
    if body.action == "list_rooms":
        return await fetch_all("SELECT id, surface, status, created_at FROM rooms WHERE status IN ('waiting','playing') ORDER BY created_at DESC")
    if body.action == "list_puzzles":
        return await fetch_all(
            """
            SELECT id,
                   COALESCE(NULLIF(TRIM(title), ''), SUBSTR(surface, 1, 10)) AS title,
                   tags
            FROM puzzles
            WHERE enabled = 1
            ORDER BY id ASC
            """
        )
    if body.action == "get_puzzle":
        if body.puzzle_id is None:
            raise HTTPException(status_code=400, detail="puzzle_id 必填")
        puzzle = await fetch_one(
            """
            SELECT id,
                   COALESCE(NULLIF(TRIM(title), ''), SUBSTR(surface, 1, 10)) AS title,
                   surface,
                   tags
            FROM puzzles
            WHERE id = ? AND enabled = 1
            """,
            (body.puzzle_id,),
        )
        if not puzzle:
            raise HTTPException(status_code=404, detail="题目不存在")
        return puzzle
    if body.action == "status":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await _room_context(body.room_id, body.log_limit)
    if body.action == "register":
        return await _register_toy_user(body.username, body.password)
    if body.action == "join":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        room = await fetch_one("SELECT id, surface, status, created_at FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        return room
    if body.action == "generate":
        return await game_generate()
    if body.action == "note_list":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        notes = await fetch_all(
            """
            SELECT rn.*, p.username, p.is_guest
            FROM room_notes rn
            LEFT JOIN players p ON p.id = rn.player_id
            WHERE rn.room_id = ?
            ORDER BY rn.updated_at DESC
            """,
            (body.room_id,),
        )
        for note in notes:
            if not (note.get("username") or "").strip():
                note["username"] = f"游客{note['player_id']}"
        return notes
    player = await _mcp_player(body.path_token)
    if body.action == "create_random":
        result = await create_room(RoomCreateBody(mode="random", puzzle_id=body.puzzle_id), player)
        return await _public_room(result["room_id"])
    if body.action == "create_custom":
        surface = clean_content(body.surface or "", 500)
        answer = clean_content(body.answer or "", 1000)
        tags = (body.tags or "").strip()[:100]
        if not surface or not answer:
            raise HTTPException(status_code=400, detail="surface 和 answer 必填")
        result = await create_room(
            RoomCreateBody(mode="custom", surface=surface, answer=answer, tags=tags),
            player,
        )
        return await fetch_one(
            "SELECT id, surface, status, created_by, winner_id, created_at, finished_at FROM rooms WHERE id = ?",
            (result["room_id"],),
        )
    if body.action == "close_room":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await close_room(body.room_id, player)
    if body.action == "ask":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        previous_own_log = await _previous_own_public_log(body.room_id, player["id"])
        payload, hint_task = await _ask_impl(ContentBody(room_id=body.room_id, content=clean_content(body.content, 200)), player)
        hint_result = await hint_task
        if hint_result:
            payload["auto_hint"] = hint_result
        payload["room"] = await _public_room(body.room_id)
        payload["logs_since_last_own_action"] = await _room_logs_after(
            body.room_id,
            previous_own_log["id"] if previous_own_log else None,
            None,
            current_log_id=payload.get("id"),
        )
        return payload
    if body.action == "guess":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        return await game_guess(GuessBody(room_id=body.room_id, content=clean_content(body.content, 1000)), player)
    if body.action == "hint_respond":
        if not body.room_id or body.log_id is None or body.accept is None:
            raise HTTPException(status_code=400, detail="room_id、log_id、accept 必填")
        return await game_hint_respond(
            HintResponseBody(room_id=body.room_id, log_id=body.log_id, accept=body.accept),
            player,
        )
    if body.action == "hint_request":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await game_hint_request(HintRequestBody(room_id=body.room_id), player)
    if body.action == "note_add":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        return await add_note(body.room_id, NoteBody(content=clean_content(body.content, 50)), player)
    if body.action == "note_edit":
        if body.note_id is None or not body.content:
            raise HTTPException(status_code=400, detail="note_id 和 content 必填")
        return await update_note(body.note_id, NoteBody(content=clean_content(body.content, 50)), player)
    if body.action == "note_delete":
        if body.note_id is None:
            raise HTTPException(status_code=400, detail="note_id 必填")
        return await delete_note(body.note_id, player)
    raise HTTPException(status_code=400, detail="未知 action")


async def _public_room(room_id: str) -> dict:
    room = await fetch_one(
        """
        SELECT r.id, r.surface, r.status, r.winner_id, r.created_at, r.finished_at,
               COALESCE(NULLIF(TRIM(pz.title), ''), '') AS title,
               COALESCE(pz.tags, '') AS tags
        FROM rooms r
        LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
        WHERE r.id = ?
        """,
        (room_id,),
    )
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    return room


async def _room_context(room_id: str, log_limit: int | None = None) -> dict:
    return {"room": await _public_room(room_id), "logs": await _room_logs_after(room_id, None, log_limit, latest_limit=True)}


async def _previous_own_public_log(room_id: str, player_id: int) -> dict | None:
    return await fetch_one(
        """
        SELECT id
        FROM game_logs
        WHERE room_id = ?
          AND player_id = ?
          AND type IN ('ask', 'guess', 'hint_accept', 'hint_reject')
        ORDER BY id DESC
        LIMIT 1
        """,
        (room_id, player_id),
    )


async def _room_logs_after(
    room_id: str,
    after_log_id: int | None,
    log_limit: int | None = None,
    current_log_id: int | None = None,
    latest_limit: bool = False,
) -> list[dict]:
    if log_limit is not None and log_limit < 0:
        raise HTTPException(status_code=400, detail="log_limit 不能为负数")
    params: list = [room_id]
    after_clause = ""
    if after_log_id is not None:
        after_clause = "AND gl.id > ?"
        params.append(after_log_id)
    if log_limit is not None and latest_limit:
        params.append(log_limit)
        rows = await fetch_all(
            f"""
            SELECT *
            FROM (
                SELECT gl.id, gl.player_id, gl.type, gl.content, gl.judgment,
                       gl.hint_text, gl.resolved, gl.created_at,
                       p.username, p.is_guest, p.is_ai
                FROM game_logs gl
                LEFT JOIN players p ON p.id = gl.player_id
                WHERE gl.room_id = ?
                  {after_clause}
                ORDER BY gl.id DESC
                LIMIT ?
            ) recent_logs
            ORDER BY id ASC
            """,
            tuple(params),
        )
    else:
        limit_clause = ""
        if log_limit is not None:
            limit_clause = "LIMIT ?"
            params.append(log_limit)
        rows = await fetch_all(
            f"""
            SELECT gl.id, gl.player_id, gl.type, gl.content, gl.judgment,
                   gl.hint_text, gl.resolved, gl.created_at,
                   p.username, p.is_guest, p.is_ai
            FROM game_logs gl
            LEFT JOIN players p ON p.id = gl.player_id
            WHERE gl.room_id = ?
              {after_clause}
            ORDER BY gl.id ASC
            {limit_clause}
            """,
            tuple(params),
        )
    if current_log_id is not None:
        for row in rows:
            row["is_current_ask_result"] = int(row["id"]) == int(current_log_id)
    return rows


async def _mcp_player(path_token: str | None) -> dict:
    if path_token:
        db = await get_db()
        try:
            return await get_player_from_token(db, path_token)
        finally:
            await db.close()
    pid = await execute("INSERT INTO players (is_guest, is_ai, source) VALUES (1, 1, 'mcp')")
    return await fetch_one("SELECT * FROM players WHERE id = ?", (pid,))


async def get_player_from_token(db, path_token: str | None):
    """
    path_token -> toy_users.id -> players.user_id.
    If no player exists, create a passwordless AI player bound to that toy_user.
    """
    if not path_token:
        raise HTTPException(status_code=401, detail="path_token 必填")
    user_id = _account_user_id(path_token)
    async with db.execute(
        "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
        (user_id,),
    ) as cur:
        toy_user = await cur.fetchone()
    if not toy_user:
        raise HTTPException(status_code=401, detail="账号不存在或已删除")

    await db.execute(f"UPDATE toy_users SET last_active_at = {SQL_NOW} WHERE id = ?", (user_id,))
    async with db.execute("SELECT * FROM players WHERE user_id = ?", (user_id,)) as cur:
        player = await cur.fetchone()
    async with db.execute(
        "SELECT * FROM players WHERE username = ? AND (user_id IS NULL OR user_id = ?)",
        (toy_user["username"], user_id),
    ) as cur:
        named_player = await cur.fetchone()
    if named_player:
        if player and player["id"] != named_player["id"]:
            await db.execute("UPDATE players SET user_id = NULL WHERE id = ?", (player["id"],))
        player = named_player
    if player:
        await db.execute(
            f"""
            UPDATE players
            SET user_id = ?, username = ?, is_guest = 0, is_ai = 1, source = 'mcp',
                last_active_at = {SQL_NOW}
            WHERE id = ?
            """,
            (user_id, toy_user["username"], player["id"]),
        )
        await db.commit()
        async with db.execute("SELECT * FROM players WHERE id = ?", (player["id"],)) as cur:
            return dict(await cur.fetchone())

    cur = await db.execute(
        """
        INSERT INTO players (username, user_id, is_guest, is_ai, is_admin, source)
        VALUES (?, ?, 0, 1, ?, 'mcp')
        """,
        (toy_user["username"], user_id, 1 if toy_user["is_admin"] else 0),
    )
    await db.commit()
    async with db.execute("SELECT * FROM players WHERE id = ?", (cur.lastrowid,)) as cur:
        return dict(await cur.fetchone())


async def _register_toy_user(username: str | None, password: str | None) -> dict:
    username = clean_content(username or "", 32).strip()
    password = password or ""
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(status_code=400, detail="用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise HTTPException(status_code=400, detail="用户名只能包含字母、数字、下划线和中文")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    if await fetch_one("SELECT id FROM toy_users WHERE username = ?", (username,)):
        raise HTTPException(status_code=400, detail="用户名已存在，如需找回请联系管理员")
    user_id = await execute(
        "INSERT INTO toy_users (username, password_hash, is_ai) VALUES (?, ?, 1)",
        (username, hash_password(password)),
    )
    toy_user = await fetch_one("SELECT * FROM toy_users WHERE id = ?", (user_id,))
    return {
        "token": _create_account_token(toy_user),
        "user": _public_toy_user(toy_user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }


def _public_toy_user(user: dict) -> dict:
    return {
        "id": user["id"],
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
        "created_at": user.get("created_at"),
        "last_active_at": user.get("last_active_at"),
    }


def _create_account_token(user: dict) -> str:
    payload = {
        "user_id": int(user["id"]),
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
    }
    return _jwt_encode(payload)


def _account_user_id(path_token: str) -> int:
    try:
        payload = _jwt_decode(path_token)
        return int(payload["user_id"])
    except (KeyError, TypeError, ValueError):
        raise HTTPException(status_code=401, detail="登录已失效") from None


def _jwt_encode(payload: dict) -> str:
    header = {"alg": JWT_ALGORITHM, "typ": "JWT"}
    header_part = _b64url_encode(json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    payload_part = _b64url_encode(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    signing_input = f"{header_part}.{payload_part}".encode("ascii")
    signature = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
    return f"{header_part}.{payload_part}.{_b64url_encode(signature)}"


def _jwt_decode(token: str) -> dict:
    try:
        header_part, payload_part, signature_part = token.split(".", 2)
        signing_input = f"{header_part}.{payload_part}".encode("ascii")
        expected = hmac.new(TOY_SECRET.encode("utf-8"), signing_input, hashlib.sha256).digest()
        actual = _b64url_decode(signature_part)
        if not hmac.compare_digest(expected, actual):
            raise ValueError("bad signature")
        header = json.loads(_b64url_decode(header_part).decode("utf-8"))
        if header.get("alg") != JWT_ALGORITHM:
            raise ValueError("bad algorithm")
        payload = json.loads(_b64url_decode(payload_part).decode("utf-8"))
        exp = payload.get("exp")
        if exp is not None and int(exp) < int(time.time()):
            raise ValueError("expired")
        return payload
    except Exception as exc:
        raise ValueError("bad token") from exc


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("ascii"))
