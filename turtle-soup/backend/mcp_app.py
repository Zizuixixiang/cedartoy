import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import time

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from auth_utils import hash_password
from room_access import require_room_access
from database import execute, fetch_all, fetch_one, get_db, get_setting
from models import ContentBody, GuessBody, HintRequestBody, HintResponseBody, NormalizedRoomId, NoteBody, RevealAnswerBody, RoomCreateBody
from presence import enter_room
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
MCP_ANSWER_REVEALED_MESSAGE = "你已查看本局汤底，不能继续参与或进入本局。房主仍可使用 close_room 收房。"

TOY_SECRET = os.getenv("TOY_SECRET", "change-me-before-production")
JWT_ALGORITHM = "HS256"
AI_OPAQUE_TOKEN_PREFIX = "ctai_v1_"
AI_OPAQUE_TOKEN_BYTES = 32
AI_OPAQUE_TOKEN_FORMAT_VERSION = 1
DEFAULT_HUMAN_AVATAR = "🙂"
DEFAULT_AI_AVATAR = "🤖"
AVATAR_MAX_CODEPOINTS = 16
AVATAR_MAX_UTF8_BYTES = 64
LEGACY_AI_JWT_COMPAT_ENABLED = os.getenv(
    "LEGACY_AI_JWT_COMPAT_ENABLED", "true"
).strip().lower() not in {"0", "false", "no", "off"}


class PlayBody(BaseModel):
    model_config = ConfigDict(extra="allow")

    game: str
    action: str | None = None
    path_token: str | None = None
    username: str | None = None
    password: str | None = None
    avatar: str | None = None
    room_id: NormalizedRoomId | None = None
    content: str | None = None
    is_locked: bool = False
    include_finished: bool = False
    puzzle_id: int | None = None
    title: str | None = None
    surface: str | None = None
    answer: str | None = None
    tags: str | None = None
    page: int | None = None
    page_size: int | None = None
    tag: str | None = None
    q: str | None = None
    style: str | None = None
    note_id: int | None = None
    log_id: int | None = None
    log_limit: int | None = None
    accept: bool | None = None
    # Retained only to identify and reject obsolete ask calls.
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
    if body.action == "my_rooms":
        if not body.path_token:
            raise HTTPException(status_code=401, detail="path_token 必填")
        player = await _mcp_player(body.path_token)
        return await fetch_all(
            """
            SELECT r.id, r.is_locked,
                   COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
                   r.surface, r.status, r.created_at,
                   COALESCE(NULLIF(TRIM(creator.username), ''), p.username, '') AS creator_name,
                   CASE WHEN r.created_by = ? THEN 'self' ELSE 'human' END AS creator_type,
                   activity.last_active_at
            FROM rooms r
            JOIN players p ON p.id = r.created_by
            LEFT JOIN toy_users creator ON creator.id = p.user_id
            LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
            LEFT JOIN (
                SELECT room_id, MAX(last_active_at) AS last_active_at
                FROM room_presence GROUP BY room_id
            ) activity ON activity.room_id = r.id
            WHERE (r.status IN ('waiting','playing') OR (? AND r.status = 'finished'))
              AND (r.created_by = ? OR EXISTS (
                  SELECT 1 FROM user_bindings b
                  JOIN toy_users h ON h.id = b.human_user_id AND h.is_ai = 0
                  JOIN toy_users a ON a.id = b.ai_user_id AND a.is_ai = 1
                  WHERE b.ai_user_id = ? AND h.id = p.user_id
                    AND h.deleted_at IS NULL AND h.deletion_requested_at_epoch IS NULL
                    AND a.deleted_at IS NULL AND a.deletion_requested_at_epoch IS NULL
              ))
            ORDER BY CASE WHEN r.status IN ('waiting','playing') THEN 0 ELSE 1 END,
                     COALESCE(activity.last_active_at, r.created_at) DESC, r.created_at DESC
            """,
            (player["id"], body.include_finished, player["id"], player["verified_user_id"]),
        )
    if body.action == "list_rooms":
        if not body.path_token:
            raise HTTPException(status_code=401, detail="path_token 必填")
        player = await _mcp_player(body.path_token)
        return await fetch_all(
            """
            SELECT r.id, r.is_locked,
                   CASE WHEN r.created_by = ? THEN 1 ELSE 0 END AS is_mine,
                   COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
                   r.surface, r.status, r.created_at,
                   (SELECT MAX(rp.last_active_at) FROM room_presence rp
                    WHERE rp.room_id = r.id) AS last_active_at
            FROM rooms r
            LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
            WHERE r.status IN ('waiting','playing')
            ORDER BY is_mine DESC, r.created_at DESC
            """,
            (player["id"],),
        )
    if body.action == "list_puzzles":
        page = body.page if body.page is not None else 1
        page_size = body.page_size if body.page_size is not None else 20
        if page < 1:
            raise HTTPException(status_code=400, detail="page 必须 >= 1")
        if page_size < 1 or page_size > 50:
            raise HTTPException(status_code=400, detail="page_size 必须在 1-50 之间")

        where = ["enabled = 1"]
        params: list[object] = []
        tag = (body.tag or "").strip()
        tags = [item for item in re.split(r"[,，、;；\s]+", (body.tags or "").strip()) if item]
        selected_tags = []
        for item in [tag, *tags]:
            if item and item not in selected_tags:
                selected_tags.append(item)
        q = (body.q or "").strip()
        for selected_tag in selected_tags:
            where.append("COALESCE(tags, '') LIKE ?")
            params.append(f"%{selected_tag}%")
        if q:
            where.append("title LIKE ?")
            params.append(f"%{q}%")

        where_sql = " AND ".join(where)
        count_row = await fetch_one(
            f"SELECT COUNT(*) AS c FROM puzzles WHERE {where_sql}",
            tuple(params),
        )
        total = int(count_row["c"] if count_row else 0)
        total_pages = (total + page_size - 1) // page_size if total else 0
        offset = (page - 1) * page_size
        items = await fetch_all(
            f"""
            SELECT id,
                   COALESCE(NULLIF(TRIM(title), ''), SUBSTR(surface, 1, 10)) AS title,
                   tags
            FROM puzzles
            WHERE {where_sql}
            ORDER BY id ASC
            LIMIT ? OFFSET ?
            """,
            tuple([*params, page_size, offset]),
        )
        return {
            "items": items,
            "page": page,
            "page_size": page_size,
            "total": total,
            "total_pages": total_pages,
            "has_next": page < total_pages,
            "has_prev": page > 1 and total > 0,
        }
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
        await require_room_access(body.room_id, player)
        await _ensure_mcp_can_participate(body.room_id, player)
        return await _room_context(body.room_id, body.log_limit, player)
    if body.action == "register":
        return await _register_toy_user(body.username, body.password, body.avatar)
    if body.action == "join":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        room = await fetch_one(
            """
            SELECT r.id, r.is_locked, r.lock_owner_user_id,
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
        player = await _mcp_player(body.path_token)
        await require_room_access(body.room_id, player, room=room)
        await _ensure_mcp_can_participate(body.room_id, player)
        room.pop("lock_owner_user_id", None)
        await enter_room(body.room_id, player["id"])
        return room
    if body.action == "generate":
        return await game_generate({"style": body.style or "horror"})
    if body.action == "note_list":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        player = await _mcp_player(body.path_token) if body.path_token else None
        await require_room_access(body.room_id, player)
        await _ensure_mcp_can_participate(body.room_id, player)
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
    if body.room_id:
        await require_room_access(body.room_id, player)
        if body.action in {"ask", "guess", "hint_request", "hint_respond", "view_auto_hint", "note_add", "note_edit", "note_delete"}:
            await _ensure_mcp_can_participate(body.room_id, player)
    # Edits/deletes identify the actual room by note_id, even if room_id is absent
    # or names another room.
    if body.action in {"note_edit", "note_delete"} and body.note_id is not None:
        note = await fetch_one("SELECT room_id FROM room_notes WHERE id = ?", (body.note_id,))
        if note:
            await require_room_access(note["room_id"], player)
            await _ensure_mcp_can_participate(note["room_id"], player)
    if body.action == "create_random":
        result = await create_room(RoomCreateBody(mode="random", puzzle_id=body.puzzle_id, is_locked=body.is_locked), player)
        return await _public_room(result["room_id"])
    if body.action == "create_custom":
        surface = clean_content(body.surface or "", SURFACE_LIMIT)
        answer = clean_content(body.answer or "", ANSWER_LIMIT)
        tags = (body.tags or "").strip()[:TAGS_LIMIT]
        if not surface or not answer:
            raise HTTPException(status_code=400, detail="surface 和 answer 必填")
        result = await create_room(
            RoomCreateBody(mode="custom", is_locked=body.is_locked, title=(body.title or "").strip()[:TITLE_LIMIT], surface=surface, answer=answer, tags=tags),
            player,
        )
        return await fetch_one(
            "SELECT id, is_locked, title, surface, status, created_by, winner_id, created_at, finished_at FROM rooms WHERE id = ?",
            (result["room_id"],),
        )
    if body.action == "close_room":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await close_room(body.room_id, player)
    if body.action == "ask":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        if "confirm_reveal" in body.model_fields_set:
            raise HTTPException(status_code=400, detail="达到汤底查看门槛后，请调用 reveal_answer，传 room_id；查看后不能再进入或操作本房间。")
        if body.model_fields_set & {"auto_hint_log_id", "accept_auto_hint", "accept_auto_hint_log_id", "reject_auto_hint_log_id"}:
            raise HTTPException(status_code=400, detail="自动提示请改用 view_auto_hint，传 room_id 和 log_id 查看。")
        if not body.content:
            raise HTTPException(status_code=400, detail="content 必填")
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        previous_own_log = await _previous_own_public_log(body.room_id, player["id"])
        payload, hint_task = await _ask_impl(ContentBody(room_id=body.room_id, content=clean_content(body.content, 200)), player)
        hint_result = await hint_task
        prompt = await _answer_reveal_prompt(body.room_id, player["id"])
        if prompt:
            payload["answer_reveal_prompt"] = prompt
        # join/status already provide the full surface; avoid repeating it on every ask.
        payload["room"] = await _public_room(body.room_id, include_surface=False)
        payload["logs_since_last_own_action"] = await _room_logs_after(
            body.room_id,
            previous_own_log["id"] if previous_own_log else None,
            None,
            current_log_id=payload.get("id"),
            player_id=player["id"],
        )
        if hint_result:
            for log in payload["logs_since_last_own_action"]:
                if log["id"] == hint_result["log_id"] and "auto_hint_notification" in log:
                    payload["auto_hint"] = log.pop("auto_hint_notification")
        return payload
    if body.action == "guess":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        return await game_guess(GuessBody(room_id=body.room_id, content=clean_content(body.content, 3000)), player)
    if body.action == "hint_respond":
        raise HTTPException(status_code=400, detail="自动提示改用 view_auto_hint(room_id, log_id)；主动提示直接 hint_request(room_id) 返回。")
    if body.action == "view_auto_hint":
        if not body.room_id or body.log_id is None:
            raise HTTPException(status_code=400, detail="room_id 和 log_id 必填")
        result = await _view_auto_hint(body.room_id, body.log_id, player)
        if result is None:
            raise HTTPException(status_code=404, detail="自动提示不存在")
        return result
    if body.action == "hint_request":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        return await game_hint_request(
            HintRequestBody(room_id=body.room_id, confirm_hint=bool(body.confirm_hint or body.confirm)),
            player,
        )
    if body.action == "reveal_answer":
        if not body.room_id:
            raise HTTPException(status_code=400, detail="room_id 必填")
        if await _mcp_has_revealed(body.room_id, player):
            return {"answer_revealed": True, "message": MCP_ANSWER_REVEALED_MESSAGE}
        room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (body.room_id,))
        if not room:
            raise HTTPException(status_code=404, detail="房间不存在")
        if room["status"] == "finished":
            raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
        trigger = int(await get_setting("answer_reveal_prompt_count", "100"))
        if trigger <= 0:
            raise HTTPException(status_code=400, detail="查看汤底功能当前未开放。")
        row = await fetch_one(
            "SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND type = 'ask'",
            (body.room_id,),
        )
        ask_count = int(row["c"])
        if ask_count < trigger:
            raise HTTPException(status_code=400, detail=f"本房间需累计 {trigger} 次提问后才能查看汤底（当前 {ask_count} 次）。")
        return await game_reveal_answer(RevealAnswerBody(room_id=body.room_id, confirm_reveal=True), player)
    if body.action == "note_add":
        if not body.room_id or not body.content:
            raise HTTPException(status_code=400, detail="room_id 和 content 必填")
        return await add_note(body.room_id, NoteBody(content=clean_content(body.content, 200)), player)
    if body.action == "note_edit":
        if body.note_id is None or not body.content:
            raise HTTPException(status_code=400, detail="note_id 和 content 必填")
        return await update_note(body.note_id, NoteBody(content=clean_content(body.content, 200)), player)
    if body.action == "note_delete":
        if body.note_id is None:
            raise HTTPException(status_code=400, detail="note_id 必填")
        return await delete_note(body.note_id, player)
    raise HTTPException(status_code=400, detail="未知 action")


async def _mcp_has_revealed(room_id: str, player: dict | None) -> bool:
    return player is not None and await fetch_one(
        "SELECT 1 FROM room_answer_reveals WHERE room_id = ? AND player_id = ?",
        (room_id, player["id"]),
    ) is not None


async def _ensure_mcp_can_participate(room_id: str, player: dict | None) -> None:
    if await _mcp_has_revealed(room_id, player):
        raise HTTPException(status_code=400, detail=MCP_ANSWER_REVEALED_MESSAGE)


async def _public_room(room_id: str, *, include_surface: bool = True) -> dict:
    room = await fetch_one(
        """
        SELECT r.id, r.is_locked, r.surface, r.status, r.winner_id, r.created_at, r.finished_at,
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
    if not include_surface:
        room.pop("surface", None)
    return room


async def _room_context(room_id: str, log_limit: int | None = None, player: dict | None = None) -> dict:
    data = {
        "room": await _public_room(room_id),
        "logs": await _room_logs_after(room_id, None, log_limit, latest_limit=True, player_id=player["id"] if player else None),
    }
    prompt = await _answer_reveal_prompt(room_id, player["id"] if player else None)
    if prompt:
        data["answer_reveal_prompt"] = prompt
    return data


async def _answer_reveal_prompt(room_id: str, player_id: int | None) -> dict | None:
    if player_id is None:
        return None
    trigger = int(await get_setting("answer_reveal_prompt_count", "100"))
    if trigger <= 0:
        return None
    db = await get_db()
    try:
        # Claim the notification atomically, including eligibility and reveal checks.
        await db.execute("BEGIN IMMEDIATE")
        cur = await db.execute(
            """
            INSERT OR IGNORE INTO room_answer_reveal_prompts (room_id, player_id)
            SELECT id, ? FROM rooms WHERE id = ? AND status != 'finished'
              AND (SELECT COUNT(*) FROM game_logs WHERE room_id = ? AND type = 'ask') >= ?
              AND NOT EXISTS (SELECT 1 FROM room_answer_reveals WHERE room_id = ? AND player_id = ?)
            """,
            (player_id, room_id, room_id, trigger, room_id, player_id),
        )
        if not cur.rowcount:
            await db.commit()
            return None
        async with db.execute("SELECT COUNT(*) FROM game_logs WHERE room_id = ? AND type = 'ask'", (room_id,)) as cur:
            ask_count = (await cur.fetchone())[0]
        await db.commit()
    finally:
        await db.close()
    return {
        "ask_count": ask_count,
        "message": f"本房间已达到 {trigger} 题查看门槛。调用 reveal_answer，传当前 room_id；查看后不能再进入或操作本房间。",
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
        "message": "收到一条自动提示。调用 view_auto_hint，传当前 room_id 和 log_id 查看。",
    }


async def _mask_auto_hints_for_mcp(rows: list[dict], player_id: int | None) -> None:
    auto_rows = [
        row for row in rows
        if row.get("type") == "auto_hint" or row.get("judgment") == "auto_hint"
    ]
    if not auto_rows:
        return
    decisions = {}
    notified = set()
    if player_id is not None:
        db = await get_db()
        try:
            await db.execute("BEGIN IMMEDIATE")
            for row in auto_rows:
                log_id = int(row["id"])
                # NULL = notified only; legacy 0/1 remain rejected/viewed and
                # already notified. INSERT OR IGNORE arbitrates concurrent reads.
                cur = await db.execute(
                    "INSERT OR IGNORE INTO room_hint_views (log_id, player_id, accepted) VALUES (?, ?, NULL)",
                    (log_id, player_id),
                )
                if cur.rowcount:
                    notified.add(log_id)
                async with db.execute(
                    "SELECT accepted FROM room_hint_views WHERE log_id = ? AND player_id = ?",
                    (log_id, player_id),
                ) as cur:
                    decisions[log_id] = (await cur.fetchone())[0]
            await db.commit()
        finally:
            await db.close()
    for row in auto_rows:
        log_id = int(row["id"])
        if decisions.get(log_id) == 1:
            row["auto_hint_accepted"] = True
            continue
        # Clue logs may duplicate the secret in content as well as hint_text.
        row["hint_text"] = None
        row["content"] = "自动提示（未查看）"
        if log_id in notified:
            row["auto_hint_notification"] = _masked_auto_hint_prompt(log_id)
        if decisions.get(log_id) == 0:
            row["auto_hint_rejected"] = True


async def _view_auto_hint(room_id: str, log_id: int, player: dict) -> dict | None:
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            "SELECT * FROM game_logs WHERE id = ? AND room_id = ? AND (type = 'auto_hint' OR judgment = 'auto_hint')",
            (log_id, room_id),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            await db.commit()
            return None
        hint = dict(row)
        await db.execute(
            "INSERT INTO room_hint_views (log_id, player_id, accepted) VALUES (?, ?, ?) "
            "ON CONFLICT(log_id, player_id) DO UPDATE SET accepted = excluded.accepted",
            (log_id, player["id"], 1),
        )
        await db.commit()
    finally:
        await db.close()
    return {"log_id": log_id, "accept": True, "hint_text": hint.get("hint_text") or hint.get("content")}


async def _mcp_player(path_token: str | None) -> dict:
    if path_token:
        db = await get_db()
        try:
            player = await get_player_from_token(db, path_token)
            player["verified_user_id"] = player["user_id"]
            return player
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
    toy_user = await _account_user(db, path_token)
    user_id = int(toy_user["id"])

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


def _is_emoji_base_codepoint(codepoint: int) -> bool:
    return (
        0x1F300 <= codepoint <= 0x1FAFF
        or 0x1F1E6 <= codepoint <= 0x1F1FF
        or 0x1F170 <= codepoint <= 0x1F251
        or 0x2600 <= codepoint <= 0x27BF
        or 0x2194 <= codepoint <= 0x2199
        or 0x21A9 <= codepoint <= 0x21AA
        or 0x23E9 <= codepoint <= 0x23F3
        or 0x23F8 <= codepoint <= 0x23FA
        or 0x25AA <= codepoint <= 0x25AB
        or 0x25FB <= codepoint <= 0x25FE
        or codepoint in {
            0x00A9, 0x00AE, 0x203C, 0x2049, 0x2122, 0x2139,
            0x231A, 0x231B, 0x25B6, 0x25C0,
            0x2B05, 0x2B06, 0x2B07, 0x2B1B, 0x2B1C, 0x2B50, 0x2B55,
            0x3030, 0x303D, 0x3297, 0x3299, 0x1F004, 0x1F0CF,
        }
    )


def _normalize_emoji_avatar(value: str | None) -> str:
    if value is None:
        value = ""
    if not isinstance(value, str):
        raise HTTPException(status_code=400, detail="avatar 需为字符串")
    avatar = " ".join(value.strip().split())
    if not avatar:
        return DEFAULT_AI_AVATAR
    if len(avatar) > AVATAR_MAX_CODEPOINTS or len(avatar.encode("utf-8")) > AVATAR_MAX_UTF8_BYTES:
        raise HTTPException(status_code=400, detail="头像过长，最多 16 个 Unicode 字符")
    has_emoji_base = False
    for index, char in enumerate(avatar):
        codepoint = ord(char)
        if char == " ":
            continue
        if _is_emoji_base_codepoint(codepoint):
            has_emoji_base = True
            continue
        if (
            codepoint in {0x200D, 0x20E3, 0xFE0E, 0xFE0F}
            or 0x1F3FB <= codepoint <= 0x1F3FF
            or 0xE0020 <= codepoint <= 0xE007F
        ):
            continue
        if char in "#*0123456789" and "\u20e3" in avatar[index:]:
            has_emoji_base = True
            continue
        raise HTTPException(status_code=400, detail="头像只接受 Emoji/简短 Emoji 字符串")
    if not has_emoji_base:
        raise HTTPException(status_code=400, detail="头像只接受 Emoji/简短 Emoji 字符串")
    return avatar


async def _register_toy_user(
    username: str | None,
    password: str | None,
    avatar: str | None = None,
) -> dict:
    username = clean_content(username or "", 32).strip()
    password = password or ""
    if len(username) < 2 or len(username) > 20:
        raise HTTPException(status_code=400, detail="用户名长度须为 2-20 个字符")
    if not re.fullmatch(r"[a-zA-Z0-9_\u4e00-\u9fff]+", username):
        raise HTTPException(status_code=400, detail="用户名只能包含字母、数字、下划线和中文")
    if len(password) < 6:
        raise HTTPException(status_code=400, detail="密码至少 6 位")
    password_hash = hash_password(password)
    avatar_value = _normalize_emoji_avatar(avatar)
    db = await get_db()
    try:
        await db.execute("BEGIN IMMEDIATE")
        async with db.execute(
            """
            SELECT 1
            FROM toy_users
            WHERE username = ?
            UNION ALL
            SELECT 1
            FROM account_username_changes
            WHERE old_username = ?
            LIMIT 1
            """,
            (username, username),
        ) as cur:
            conflict = await cur.fetchone()
        if conflict:
            raise HTTPException(status_code=400, detail="用户名已存在")
        cur = await db.execute(
            """
            INSERT INTO toy_users (username, password_hash, is_ai, avatar_type, avatar_value)
            VALUES (?, ?, 1, 'emoji', ?)
            """,
            (username, password_hash, avatar_value),
        )
        user_id = int(cur.lastrowid)
        async with db.execute("SELECT * FROM toy_users WHERE id = ?", (user_id,)) as cur:
            toy_user = dict(await cur.fetchone())
        token = await _issue_ai_token(db, toy_user)
        await db.commit()
    except Exception:
        await db.rollback()
        raise
    finally:
        await db.close()
    return {
        "token": token,
        "user": _public_toy_user(toy_user),
        "message": "注册成功。让你的人类把 MCP 地址改为 https://toy.cedarstar.org/{token} 后即可获得持久身份，无需再次登录。",
    }


def _public_toy_user(user: dict) -> dict:
    avatar_type = str(user.get("avatar_type") or "").strip()
    avatar_value = str(user.get("avatar_value") or "").strip()
    default_avatar = DEFAULT_AI_AVATAR if user.get("is_ai") else DEFAULT_HUMAN_AVATAR
    return {
        "id": user["id"],
        "username": user["username"],
        "is_ai": bool(user.get("is_ai")),
        "is_admin": bool(user.get("is_admin")),
        "avatar": {
            "type": avatar_type or "emoji",
            "value": avatar_value or default_avatar,
            "is_default": not bool(avatar_type and avatar_value),
        },
        "created_at": user.get("created_at"),
        "last_active_at": user.get("last_active_at"),
    }


def _opaque_ai_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def _issue_ai_token(db, user: dict) -> str:
    if not user.get("is_ai") or user.get("deleted_at") is not None:
        raise HTTPException(status_code=400, detail="目标账号不是有效的小机账号")
    for _attempt in range(3):
        token = AI_OPAQUE_TOKEN_PREFIX + secrets.token_urlsafe(AI_OPAQUE_TOKEN_BYTES)
        try:
            await db.execute(
                """
                INSERT INTO ai_access_tokens (
                    token_hash, user_id, generation, format_version
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    _opaque_ai_token_hash(token),
                    int(user["id"]),
                    int(user.get("ai_token_version") or 0),
                    AI_OPAQUE_TOKEN_FORMAT_VERSION,
                ),
            )
            return token
        except Exception as exc:
            if "UNIQUE constraint failed: ai_access_tokens.token_hash" not in str(exc):
                raise
    raise RuntimeError("failed to allocate a unique AI token")


def _jwt_unverified_payload(token: str) -> dict:
    try:
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("not a three-part JWT")
        payload = json.loads(_b64url_decode(parts[1]).decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JWT payload is not an object")
        return payload
    except Exception as exc:
        raise ValueError("bad token") from exc


async def _legacy_allowlisted_ai_payload(db, token: str) -> dict:
    if not LEGACY_AI_JWT_COMPAT_ENABLED:
        raise ValueError("legacy AI JWT compatibility is disabled")
    payload = _jwt_unverified_payload(token)
    if set(payload) != {"user_id", "username", "is_ai", "is_admin"}:
        raise ValueError("unexpected legacy payload")
    if payload.get("is_ai") is not True:
        raise ValueError("not a permanent AI token")
    async with db.execute(
        """
        SELECT user_id, token_version
        FROM legacy_ai_token_hashes
        WHERE token_hash = ?
        """,
        (hashlib.sha256(token.encode("utf-8")).hexdigest(),),
    ) as cur:
        row = await cur.fetchone()
    if not row or int(row["user_id"]) != int(payload["user_id"]):
        raise ValueError("legacy token is not allowlisted")
    payload["_legacy_token_version"] = int(row["token_version"])
    return payload


async def _account_user(db, path_token: str) -> dict:
    if path_token.startswith(AI_OPAQUE_TOKEN_PREFIX):
        async with db.execute(
            """
            SELECT u.*, t.generation, t.format_version
            FROM ai_access_tokens AS t
            JOIN toy_users AS u ON u.id = t.user_id
            WHERE t.token_hash = ?
              AND t.revoked_at_epoch IS NULL
              AND u.deleted_at IS NULL
            """,
            (_opaque_ai_token_hash(path_token),),
        ) as cur:
            row = await cur.fetchone()
        if (
            not row
            or not row["is_ai"]
            or int(row["format_version"]) != AI_OPAQUE_TOKEN_FORMAT_VERSION
            or int(row["generation"]) != int(row["ai_token_version"] or 0)
        ):
            raise HTTPException(status_code=401, detail="登录已失效")
        toy_user = dict(row)
    else:
        try:
            payload = _jwt_decode(path_token)
        except ValueError:
            try:
                payload = await _legacy_allowlisted_ai_payload(db, path_token)
            except (KeyError, TypeError, ValueError):
                raise HTTPException(status_code=401, detail="登录已失效") from None
        try:
            user_id = int(payload["user_id"])
        except (KeyError, TypeError, ValueError):
            raise HTTPException(status_code=401, detail="登录已失效") from None
        async with db.execute(
            "SELECT * FROM toy_users WHERE id = ? AND deleted_at IS NULL",
            (user_id,),
        ) as cur:
            row = await cur.fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="账号不存在或已删除")
        toy_user = dict(row)
        if payload.get("is_ai") is True:
            if not toy_user.get("is_ai"):
                raise HTTPException(status_code=401, detail="登录已失效")
            version = int(payload.get("token_version", payload.get("_legacy_token_version", 0)))
            if version != int(toy_user.get("ai_token_version") or 0):
                raise HTTPException(status_code=401, detail="登录已失效")
        elif toy_user.get("is_ai"):
            raise HTTPException(status_code=401, detail="登录已失效")
    if toy_user.get("deletion_requested_at_epoch") is not None:
        raise HTTPException(status_code=401, detail="账号处于待注销状态")
    return toy_user


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
        if payload.get("is_ai") is True and not LEGACY_AI_JWT_COMPAT_ENABLED:
            raise ValueError("legacy AI JWT compatibility is disabled")
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
