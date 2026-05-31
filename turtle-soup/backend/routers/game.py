from fastapi import APIRouter, Depends, HTTPException

import judge
from auth_utils import current_player
from database import execute, fetch_all, fetch_one, get_db, get_setting
from models import ContentBody, HintResponseBody
from presence import touch_room
from sse import broadcast
from utils import clean_content

router = APIRouter(prefix="/game", tags=["game"])


async def _room(room_id: str) -> dict:
    room = await fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    if room["status"] == "finished":
        raise HTTPException(status_code=400, detail="游戏已结束")
    return room


async def _log_payload(log_id: int) -> dict:
    return await fetch_one(
        """
        SELECT gl.id, gl.room_id, gl.player_id, gl.type, gl.content, gl.judgment, gl.created_at,
               p.username, p.is_guest, p.is_ai
        FROM game_logs gl
        LEFT JOIN players p ON p.id = gl.player_id
        WHERE gl.id = ?
        """,
        (log_id,),
    )


@router.post("/ask")
async def ask(body: ContentBody, player: dict = Depends(current_player)):
    question = clean_content(body.content, 200)
    room = await _room(body.room_id)
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
                ) WHERE datetime(created_at) >= datetime('now', ?)
                """,
                (body.room_id, player["id"], n, f"-{seconds} seconds"),
            )
            if int(too_fast["c"]) >= n:
                raise HTTPException(status_code=429, detail="AI 提问太快，请稍后再试")
    judgment = await judge.judge_ask(room["answer"], question)
    log_id = await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content, judgment) VALUES (?, ?, 'ask', ?, ?)",
        (body.room_id, player["id"], question, judgment),
    )
    column = {"yes": "ask_count_y", "no": "ask_count_n", "unrelated": "ask_count_u", "partial": "ask_count_p"}[judgment]
    await execute(
        f"UPDATE players SET ask_count = ask_count + 1, {column} = {column} + 1 WHERE id = ?",
        (player["id"],),
    )
    payload = await _log_payload(log_id)
    await touch_room(body.room_id, player["id"])
    await broadcast(body.room_id, "new_log", payload)

    trigger = int(await get_setting("hint_trigger_count", "30"))
    count_row = await fetch_one("SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND type = 'ask'", (body.room_id,))
    ask_count = int(count_row["c"])
    if trigger > 0 and ask_count % trigger == 0:
        offered = await fetch_one(
            "SELECT id FROM game_logs WHERE room_id = ? AND type = 'hint_offer' AND content = ?",
            (body.room_id, f"hint:{ask_count}"),
        )
        if not offered:
            logs = await fetch_all("SELECT * FROM game_logs WHERE room_id = ? ORDER BY id ASC", (body.room_id,))
            hint = await judge.generate_hint(room["answer"], logs)
            hint_id = await execute(
                "INSERT INTO game_logs (room_id, type, content, hint_text) VALUES (?, 'hint_offer', ?, ?)",
                (body.room_id, f"hint:{ask_count}", hint),
            )
            await broadcast(body.room_id, "hint_offer", {"log_id": hint_id, "hint_text": hint})
    return payload


@router.post("/guess")
async def guess(body: ContentBody, player: dict = Depends(current_player)):
    guess_text = clean_content(body.content, 200)
    room = await _room(body.room_id)
    correct = await judge.judge_guess(room["answer"], guess_text)
    log_id = await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content, judgment) VALUES (?, ?, 'guess', ?, ?)",
        (body.room_id, player["id"], guess_text, "yes" if correct else "no"),
    )
    payload = await _log_payload(log_id)
    await touch_room(body.room_id, player["id"])
    if correct:
        db = await get_db()
        try:
            await db.execute(
                "UPDATE rooms SET status = 'finished', winner_id = ?, finished_at = CURRENT_TIMESTAMP WHERE id = ?",
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
        await broadcast(body.room_id, "new_log", payload)
        await broadcast(body.room_id, "game_over", {"answer": room["answer"], "winner": {"id": player["id"], "username": player.get("username") or f"游客{player['id']}"}})
    else:
        await broadcast(body.room_id, "new_log", payload)
    return payload | {"correct": correct}


@router.post("/hint/respond")
async def hint_respond(body: HintResponseBody, player: dict = Depends(current_player)):
    hint = await fetch_one(
        "SELECT * FROM game_logs WHERE id = ? AND room_id = ? AND type = 'hint_offer'",
        (body.log_id, body.room_id),
    )
    if not hint:
        raise HTTPException(status_code=404, detail="提示不存在")
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
