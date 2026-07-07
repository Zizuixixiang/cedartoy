import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from auth_utils import hash_password
from database import execute, fetch_all, fetch_one, get_db, get_setting
from models import ContentBody, GuessBody, HintRequestBody, HintResponseBody, NoteBody, RevealAnswerBody, RoomCreateBody
from routers.game import ask as game_ask
from routers.game import _ask_impl
from routers.game import generate as game_generate
from routers.game import guess as game_guess
from routers.game import hint_request as game_hint_request
from routers.game import reveal_answer as game_reveal_answer
from routers.notes import add_note, delete_note, update_note
from routers.rooms import close_room, create_room
from utils import ANSWER_LIMIT, ROOM_FINISHED_STATUS_HINT, SQL_NOW, SURFACE_LIMIT, TAGS_LIMIT, TITLE_LIMIT, clean_content

router = APIRouter(prefix="/mcp", tags=["mcp"])
logger = logging.getLogger(__name__)

TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
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
    title: str | None = None
    surface: str | None = None
    answer: str | None = None
    tags: str | None = None
    style: str | None = None
    note_id: int | None = None
    log_id: int | None = None
    log_limit: int | None = None
    accept: bool | None = None
    auto_hint_log_id: int | None = None
    accept_auto_hint: bool | None = None
    accept_auto_hint_log_id: int | None = None
    reject_auto_hint_log_id: int | None = None
    confirm_reveal: bool | None = None
    confirm_hint: bool | None = None
    confirm: bool | None = None


@router.post("/play")
async def play(body: PlayBody):
    if body.game != "turtle_soup":
        raise HTTPException(status_code=404, detail="未知游戏")
    if not body.action:
        raise HTTPException(status_code=400, detail="action 必填")
    if body.action == "list_rooms":
        return await fetch_all(
            """
            SELECT r.id,
                   COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
                   r.surface, r.status, r.created_at,
                   (SELECT MAX(rp.last_active_at) FROM room_presence rp
                    WHERE rp.room_id = r.id) AS last_active_at
            FROM rooms r
            LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
            WHERE r.status IN ('waiting','playing')
            ORDER BY r.created_at DESC
            """
        )
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
        player = await _mcp_player(body.path_token) if body.path_token else None
        return await _room_context(body.room_id, body.log_limit, player)
    if body.action == "register":
        return await _register_toy_user(body.username, body.password)
    if body.action == "join":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        room = await fetch_one(
            """
            SELECT r.id,
                   COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
                   r.surface, r.status, r.created_at
            FROM rooms r
            LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
            WHERE r.id = ?
            """,
            (body.room_id,),
        )
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        return room
    if body.action == "generate":
        return await game_generate({"style": body.style or "horror"})
    if body.action == "note_list":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        notes = await fetch_all(
            """
            SELECT rn.*, p.username, p.is_guest
            FROM room_notes rn
            LEFT JOIN players p ON p.id = rn.player_id
            WHERE rn.room_id = ?
            ORDER BY rn.updated_at ASC
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
        surface = clean_content(body.surface or "", SURFACE_LIMIT)
        answer = clean_content(body.answer or "", ANSWER_LIMIT)
        tags = (body.tags or "").strip()[:TAGS_LIMIT]
        if not surface or not answer:
            raise HTTPException(status_code=400, detail="surface 和 answer 必填")
        result = await create_room(
            RoomCreateBody(mode="custom", title=(body.title or "").strip()[:TITLE_LIMIT], surface=surface, answer=answer, tags=tags),
            player,
        )
        return await fetch_one(
            "SELECT id, title, surface, status, created_by, winner_id, created_at, finished_at FROM rooms WHERE id = ?",
            (result["room_id"],),
        )
    if body.action == "close_room":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await close_room(body.room_id, player)
    if body.action == "ask":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        if body.confirm_reveal:
            return await game_reveal_answer(
                RevealAnswerBody(room_id=body.room_id, confirm_reveal=True),
                player,
            )
        if not body.content:
            raise HTTPException(status_code=400, detail="content 必填")
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        auto_hint_decision = await _auto_hint_decision_from_ask(body, player)
        previous_own_log = await _previous_own_public_log(body.room_id, player["id"])
        payload, hint_task = await _ask_impl(ContentBody(room_id=body.room_id, content=clean_content(body.content, 200)), player)
        if auto_hint_decision is not None:
            payload["auto_hint_decision"] = auto_hint_decision
        hint_result = await hint_task
        if hint_result:
            payload["auto_hint"] = _masked_auto_hint_prompt(hint_result["log_id"])
        prompt = await _answer_reveal_prompt(body.room_id)
        if prompt:
            payload["answer_reveal_prompt"] = prompt
        payload["room"] = await _public_room(body.room_id)
        payload["logs_since_last_own_action"] = await _room_logs_after(
            body.room_id,
            previous_own_log["id"] if previous_own_log else None,
            None,
            current_log_id=payload.get("id"),
            player_id=player["id"],
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
        raise HTTPException(status_code=400, detail="自动提示请在下一次 ask 中传 auto_hint_log_id 和 accept_auto_hint=true/false 处理")
    if body.action == "hint_request":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await game_hint_request(
            HintRequestBody(room_id=body.room_id, confirm_hint=bool(body.confirm_hint or body.confirm)),
            player,
        )
    if body.action == "reveal_answer":
        raise HTTPException(status_code=400, detail="查看汤底确认请在下一次 ask 中传 confirm_reveal=true 处理")
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
               COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
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


async def _room_context(room_id: str, log_limit: int | None = None, player: dict | None = None) -> dict:
    data = {
        "room": await _public_room(room_id),
        "logs": await _room_logs_after(room_id, None, log_limit, latest_limit=True, player_id=player["id"] if player else None),
    }
    prompt = await _answer_reveal_prompt(room_id)
    if prompt:
        data["answer_reveal_prompt"] = prompt
    return data


async def _answer_reveal_prompt(room_id: str) -> dict | None:
    room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (room_id,))
    if not room or room["status"] == "finished":
        return None
    trigger = int(await get_setting("answer_reveal_prompt_count", "100"))
    if trigger <= 0:
        return None
    row = await fetch_one("SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND type = 'ask'", (room_id,))
    ask_count = int(row["c"] if row else 0)
    if ask_count <= 0 or ask_count % trigger != 0:
        return None
    return {
        "ask_count": ask_count,
        "message": f"本房间已经累计 {ask_count} 次提问。若用户接受查看汤底提示，请在下一次 ask 中顺便传 confirm_reveal=true；房间不会结束，但你查看后不能继续 ask/guess/hint_request 或操作记事本。",
        "requires_confirmation": True,
        "next_ask_confirm_parameters": {"confirm_reveal": True},
    }


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
    player_id: int | None = None,
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
    await _mask_auto_hints_for_mcp(rows, player_id)
    return rows


def _masked_auto_hint_prompt(log_id: int) -> dict:
    return {
        "log_id": log_id,
        "confirmation_required": True,
        "message": "收到一条自动提示，是否查看？请在下一次 ask 里带 auto_hint_log_id 和 accept_auto_hint=true/false；不要调用其它 action。",
        "next_ask_confirm_parameters": {"auto_hint_log_id": log_id, "accept_auto_hint": True},
        "next_ask_reject_parameters": {"auto_hint_log_id": log_id, "accept_auto_hint": False},
    }


async def _mask_auto_hints_for_mcp(rows: list[dict], player_id: int | None) -> None:
    auto_ids = [
        int(row["id"]) for row in rows
        if row.get("type") == "auto_hint" or row.get("judgment") == "auto_hint"
    ]
    if not auto_ids:
        return
    accepted: set[int] = set()
    rejected: set[int] = set()
    if player_id is not None:
        placeholders = ",".join("?" for _ in auto_ids)
        decisions = await fetch_all(
            f"SELECT log_id, accepted FROM room_hint_views WHERE player_id = ? AND log_id IN ({placeholders})",
            (player_id, *auto_ids),
        )
        accepted = {int(row["log_id"]) for row in decisions if int(row.get("accepted") or 0) == 1}
        rejected = {int(row["log_id"]) for row in decisions if int(row.get("accepted") or 0) != 1}
    for row in rows:
        if row.get("type") != "auto_hint" and row.get("judgment") != "auto_hint":
            continue
        log_id = int(row["id"])
        if log_id in accepted:
            row["auto_hint_accepted"] = True
            continue
        row["hint_text"] = None
        row["content"] = "收到一条自动提示，是否查看？"
        row["auto_hint_confirmation_required"] = True
        row["next_ask_confirm_parameters"] = {"auto_hint_log_id": log_id, "accept_auto_hint": True}
        row["next_ask_reject_parameters"] = {"auto_hint_log_id": log_id, "accept_auto_hint": False}
        if log_id in rejected:
            row["auto_hint_rejected"] = True


async def _auto_hint_decision_from_ask(body: PlayBody, player: dict) -> dict | None:
    log_id = body.auto_hint_log_id or body.accept_auto_hint_log_id or body.reject_auto_hint_log_id
    if log_id is None:
        return None
    accept = False if body.reject_auto_hint_log_id is not None else bool(body.accept_auto_hint if body.accept_auto_hint is not None else True)
    decision = await _respond_auto_hint(body.room_id, log_id, accept, player)
    if decision is None:
        raise HTTPException(status_code=404, detail="自动提示不存在")
    return decision


async def _respond_auto_hint(room_id: str, log_id: int, accept: bool, player: dict) -> dict | None:
    hint = await fetch_one(
        "SELECT * FROM game_logs WHERE id = ? AND room_id = ? AND (type = 'auto_hint' OR judgment = 'auto_hint')",
        (log_id, room_id),
    )
    if not hint:
        return None
    await execute(
        "INSERT OR REPLACE INTO room_hint_views (log_id, player_id, accepted) VALUES (?, ?, ?)",
        (log_id, player["id"], 1 if accept else 0),
    )
    if not accept:
        return {"log_id": log_id, "accept": False, "message": "已拒绝查看这条自动提示。"}
    return {"log_id": log_id, "accept": True, "hint_text": hint.get("hint_text") or hint.get("content")}


async def _mcp_player(path_token: str | None) -> dict:
    if path_token:
        db = await get_db()
        try:
            return await get_player_from_token(db, path_token)
        finally:
            await db.close()
    # 分配游客编号（1-9999），与网页游客共用编号池
    db = await get_db()
    try:
        GUEST_NUMBER_MAX = 9999
        GUEST_NEXT_NUMBER_KEY = "guest_next_number"
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
                "INSERT INTO players (username, is_guest, is_ai, source) VALUES (?, 1, 1, 'mcp')",
                (username,),
            )
            next_number = (number % GUEST_NUMBER_MAX) + 1
            await db.execute(
                "UPDATE settings SET value = ? WHERE key = ?",
                (str(next_number), GUEST_NEXT_NUMBER_KEY),
            )
            await db.commit()
            player_id = int(cur.lastrowid)
            return dict((await db.execute_fetchall("SELECT * FROM players WHERE id = ?", (player_id,)))[0])
        logger.warning("guest number pool exhausted for mcp guest login: max=%s start=%s", GUEST_NUMBER_MAX, start)
        raise HTTPException(status_code=503, detail="游客编号已用完")
    finally:
        await db.close()


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
