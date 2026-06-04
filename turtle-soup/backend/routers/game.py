import asyncio
import logging

from fastapi import APIRouter, Depends, HTTPException

import judge
from auth_utils import current_player
from database import execute, fetch_all, fetch_one, get_db, get_setting
from models import ContentBody, GuessBody, HintRequestBody, HintResponseBody
from presence import touch_room
from sse import broadcast
from utils import ROOM_FINISHED_STATUS_HINT, SQL_NOW, clean_content

router = APIRouter(prefix="/game", tags=["game"])
logger = logging.getLogger(__name__)
_hint_locks: dict[str, asyncio.Lock] = {}


def _hint_lock(room_id: str) -> asyncio.Lock:
    lock = _hint_locks.get(room_id)
    if lock is None:
        lock = asyncio.Lock()
        _hint_locks[room_id] = lock
    return lock


async def _room(room_id: str) -> dict:
    room = await fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    return room


def _ensure_active(room: dict) -> None:
    if room["status"] == "finished":
        raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)


async def _log_payload(log_id: int) -> dict:
    return await fetch_one(
        """
        SELECT gl.id, gl.room_id, gl.player_id, gl.type, gl.content, gl.judgment,
               gl.hint_text, gl.resolved, gl.created_at,
               p.username, p.is_guest, p.is_ai
        FROM game_logs gl
        LEFT JOIN players p ON p.id = gl.player_id
        WHERE gl.id = ?
        """,
        (log_id,),
    )


async def _ask_count(room_id: str) -> int:
    row = await fetch_one("SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND type = 'ask'", (room_id,))
    return int(row["c"])


async def _pending_hint(room_id: str, player_id: int | None = None) -> dict | None:
    if player_id is not None:
        return await fetch_one(
            "SELECT id FROM game_logs WHERE room_id = ? AND player_id = ? AND type = 'hint_offer' AND resolved = 0",
            (room_id, player_id),
        )
    return await fetch_one(
        "SELECT id FROM game_logs WHERE room_id = ? AND type = 'hint_offer' AND resolved = 0",
        (room_id,),
    )


async def _manual_hint_count(room_id: str, player_id: int) -> int:
    row = await fetch_one(
        "SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND player_id = ? AND type = 'hint_offer'",
        (room_id, player_id),
    )
    return int(row["c"] if row else 0)


async def _system_notice(room_id: str, player_id: int | None = None) -> dict:
    log_id = await execute(
        "INSERT INTO game_logs (room_id, type, content) VALUES (?, 'system', ?)",
        (room_id, judge.SYSTEM_BUSY_NOTICE),
    )
    if player_id is not None:
        await touch_room(room_id, player_id)
    payload = await _log_payload(log_id)
    await broadcast(room_id, "new_log", payload)
    return payload | {"system_error": True}


async def _offer_hint(room: dict, ask_count: int, *, manual: bool = False, player: dict | None = None) -> dict | None:
    player_id = int(player["id"]) if player else None
    async with _hint_lock(room["id"]):
        manual_count = 0
        if manual:
            if player_id is None:
                raise HTTPException(status_code=400, detail="缺少玩家信息")
            if await _pending_hint(room["id"], player_id):
                raise HTTPException(status_code=400, detail="请先处理当前提示")
            manual_count = await _manual_hint_count(room["id"], player_id)
            if manual_count >= 3:
                raise HTTPException(status_code=400, detail="手动提示次数已用完")
        else:
            latest_room = await _room(room["id"])
            trigger = int(await get_setting("hint_trigger_count", "30"))
            latest_ask_count = await _ask_count(room["id"])
            last_hint_at = int(latest_room.get("last_hint_at_ask_count") or 0)
            if trigger <= 0 or latest_ask_count < last_hint_at + trigger:
                return None
            if await _pending_hint(room["id"]):
                return None
            room = latest_room
            ask_count = latest_ask_count

        logs = await fetch_all("SELECT * FROM game_logs WHERE room_id = ? ORDER BY id ASC", (room["id"],))
        hint = await judge.generate_hint(room["surface"], room["answer"], logs)
        if hint is None:
            return None
        if manual:
            hint_id = await execute(
                "INSERT INTO game_logs (room_id, player_id, type, content, hint_text) VALUES (?, ?, 'hint_offer', ?, ?)",
                (room["id"], player_id, f"hint:{ask_count}", hint),
            )
            await execute(
                "UPDATE rooms SET last_hint_at_ask_count = ? WHERE id = ?",
                (ask_count, room["id"]),
            )
            payload = {
                "log_id": hint_id,
                "hint_text": hint,
                "player_id": player_id,
                "username": player.get("username") if player else None,
                "is_guest": player.get("is_guest") if player else None,
                "is_ai": player.get("is_ai") if player else None,
                "manual_hint_remaining": 3 - manual_count - 1,
            }
            await broadcast(room["id"], "hint_offer", payload)
            return payload
        hint_id = await execute(
            "INSERT INTO game_logs (room_id, type, content, hint_text) VALUES (?, 'auto_hint', ?, ?)",
            (room["id"], hint, hint),
        )
        await execute(
            "UPDATE rooms SET last_hint_at_ask_count = ? WHERE id = ?",
            (ask_count, room["id"]),
        )
        payload = await _log_payload(hint_id)
        await broadcast(room["id"], "new_log", payload)
        return {"log_id": hint_id, "hint_text": hint}


async def _maybe_auto_hint(room: dict, ask_count: int) -> dict | None:
    trigger = int(await get_setting("hint_trigger_count", "30"))
    last_hint_at = int(room.get("last_hint_at_ask_count") or 0)
    if trigger <= 0 or ask_count < last_hint_at + trigger:
        return None
    if await _pending_hint(room["id"]):
        return None
    return await _offer_hint(room, ask_count)


async def _maybe_auto_hint_safely(room: dict, ask_count: int) -> dict | None:
    try:
        return await _maybe_auto_hint(room, ask_count)
    except Exception:
        logger.exception("auto hint failed: room_id=%s ask_count=%s", room.get("id"), ask_count)
        return None


async def _ask_impl(body: ContentBody, player: dict) -> tuple[dict, asyncio.Task]:
    """Core ask logic. Returns (payload, hint_task)."""
    question = clean_content(body.content, 200)
    room = await _room(body.room_id)
    _ensure_active(room)
    if player.get("is_ai"):
        n = int(await get_setting("ai_cooldown_questions", "5"))
        seconds = int(await get_setting("ai_cooldown_seconds", "3"))
        recent = await fetch_all(
            """
            SELECT created_at FROM game_logs
            WHERE room_id = ? AND player_id = ? AND type = 'ask'
            ORDER BY id DESC LIMIT ?
            """,
            (body.room_id, player["id"], n),
        )
        if len(recent) >= n:
            too_fast = await fetch_one(
                """
                SELECT COUNT(*) AS c FROM (
                  SELECT created_at FROM game_logs
                  WHERE room_id = ? AND player_id = ? AND type = 'ask'
                  ORDER BY id DESC LIMIT ?
                ) WHERE datetime(created_at) >= datetime('now', 'localtime', ?)
                """,
                (body.room_id, player["id"], n, f"-{seconds} seconds"),
            )
            if int(too_fast["c"]) >= n:
                raise HTTPException(status_code=429, detail="AI 提问太快了，请先思考已有线索，稍等几秒后再问。")
    try:
        result = await judge.judge_ask(room["surface"], room["answer"], question)
    except HTTPException as exc:
        if exc.status_code == 503:
            resp = await _system_notice(body.room_id, player["id"])
            return resp, asyncio.create_task(asyncio.sleep(0))
        raise
    judgment = result["judgment"]
    log_content = result.get("content_override") or question
    log_id = await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content, judgment) VALUES (?, ?, 'ask', ?, ?)",
        (body.room_id, player["id"], log_content, judgment),
    )
    column = {"yes": "ask_count_y", "no": "ask_count_n", "unrelated": "ask_count_u", "partial": "ask_count_p"}[judgment]
    await execute(
        f"UPDATE players SET ask_count = ask_count + 1, {column} = {column} + 1 WHERE id = ?",
        (player["id"],),
    )
    payload = await _log_payload(log_id)
    await touch_room(body.room_id, player["id"])
    await broadcast(body.room_id, "new_log", payload)
    clue = result.get("clue")
    if clue is not None:
        clue_id = await execute(
            "INSERT INTO game_logs (room_id, type, content, hint_text, judgment) VALUES (?, 'auto_hint', ?, ?, 'auto_hint')",
            (body.room_id, clue, clue),
        )
        clue_payload = await _log_payload(clue_id)
        await broadcast(body.room_id, "new_log", clue_payload)

    ask_count = await _ask_count(body.room_id)
    hint_task = asyncio.create_task(_maybe_auto_hint_safely(room, ask_count))
    return payload, hint_task


@router.post("/ask")
async def ask(body: ContentBody, player: dict = Depends(current_player)):
    payload, _ = await _ask_impl(body, player)
    return payload


@router.post("/guess")
async def guess(body: GuessBody, player: dict = Depends(current_player)):
    guess_text = clean_content(body.content, 1000)
    room = await _room(body.room_id)
    _ensure_active(room)
    try:
        result = await judge.judge_guess(room["surface"], room["answer"], guess_text)
    except HTTPException as exc:
        if exc.status_code == 503:
            return await _system_notice(body.room_id, player["id"])
        raise
    correct = bool(result["success"])
    score = int(result["score"])
    log_id = await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content, judgment) VALUES (?, ?, 'guess', ?, ?)",
        (body.room_id, player["id"], guess_text, "yes" if correct else "no"),
    )
    payload = await _log_payload(log_id)
    result_content = result.get("error") or f"还原度：{score}%"
    await touch_room(body.room_id, player["id"])
    if correct:
        db = await get_db()
        try:
            await db.execute(
                f"UPDATE rooms SET status = 'finished', winner_id = ?, finished_at = {SQL_NOW} WHERE id = ?",
                (player["id"], body.room_id),
            )
            await db.execute("UPDATE players SET win_count = win_count + 1 WHERE id = ?", (player["id"],))
            ids = await db.execute_fetchall(
                "SELECT DISTINCT player_id FROM game_logs WHERE room_id = ? AND type IN ('ask','guess') AND player_id IS NOT NULL",
                (body.room_id,),
            )
            for row in ids:
                await db.execute("UPDATE players SET game_count = game_count + 1 WHERE id = ?", (row["player_id"],))
            await db.commit()
        finally:
            await db.close()
        reveal_answer = result.get("answer") or room["answer"]
        reveal_content = f"还原度：{score}%\n{reveal_answer}"
        reveal_id = await execute(
            "INSERT INTO game_logs (room_id, type, content, judgment) VALUES (?, 'system', ?, 'game_over')",
            (body.room_id, reveal_content),
        )
        reveal_payload = await _log_payload(reveal_id)
        await broadcast(body.room_id, "new_log", payload)
        await broadcast(body.room_id, "new_log", reveal_payload)
        await broadcast(body.room_id, "game_over", {"answer": reveal_answer, "winner": {"id": player["id"], "username": player.get("username") or f"游客{player['id']}"}})
        result_payload = reveal_payload
    else:
        result_id = await execute(
            "INSERT INTO game_logs (room_id, type, content, judgment) VALUES (?, 'system', ?, 'guess_result')",
            (body.room_id, result_content),
        )
        result_payload = await _log_payload(result_id)
        await broadcast(body.room_id, "new_log", payload)
        await broadcast(body.room_id, "new_log", result_payload)
    return payload | {"correct": correct, "score": score, "result_log": result_payload}


@router.post("/hint/request")
async def hint_request(body: HintRequestBody, player: dict = Depends(current_player)):
    room = await _room(body.room_id)
    _ensure_active(room)
    ask_count = await _ask_count(body.room_id)
    payload = await _offer_hint(room, ask_count, manual=True, player=player)
    if payload is None:
        raise HTTPException(status_code=503, detail="暂时无法生成提示，请稍后再试")
    return payload


@router.post("/hint/respond")
async def hint_respond(body: HintResponseBody, player: dict = Depends(current_player)):
    hint = await fetch_one(
        "SELECT * FROM game_logs WHERE id = ? AND room_id = ? AND type = 'hint_offer'",
        (body.log_id, body.room_id),
    )
    if not hint:
        raise HTTPException(status_code=404, detail="提示不存在")
    if hint["player_id"] is not None and int(hint["player_id"]) != int(player["id"]):
        raise HTTPException(status_code=403, detail="只能处理自己的提示")
    if hint["resolved"]:
        raise HTTPException(status_code=409, detail="提示已处理")
    await execute("UPDATE game_logs SET resolved = 1 WHERE id = ?", (body.log_id,))
    await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content) VALUES (?, ?, ?, ?)",
        (body.room_id, player["id"], "hint_accept" if body.accept else "hint_reject", str(body.log_id)),
    )
    data = {"log_id": body.log_id, "accept": body.accept}
    if body.accept:
        data["hint_text"] = hint["hint_text"]
    await broadcast(body.room_id, "hint_resolved", data)
    return data


@router.post("/generate")
async def generate(player: dict = Depends(current_player)):
    del player
    return await judge.generate_puzzle()


@router.get("/public-settings")
async def public_settings(player: dict = Depends(current_player)):
    del player
    return {
        "generate_cooldown_seconds": int(await get_setting("generate_cooldown_seconds", "5")),
    }
