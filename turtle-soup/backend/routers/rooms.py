from fastapi import APIRouter, Depends, HTTPException

from auth_utils import current_player
from database import execute, fetch_all, fetch_one, get_setting
from judge import scan_text
from models import RoomCreateBody
from utils import ANSWER_LIMIT, SURFACE_LIMIT, TITLE_LIMIT, SQL_NOW, clean_content, public_player, room_id

router = APIRouter(prefix="/rooms", tags=["rooms"])


def _public_room(row: dict) -> dict:
    out = {k: row[k] for k in row.keys() if k != "answer"}
    return out


@router.get("/")
async def list_rooms(player: dict = Depends(current_player)):
    del player
    finished_retention_hours = int(await get_setting("finished_room_retention_hours", "1"))
    rows = await fetch_all(
        """
        SELECT r.id, r.surface, r.status, r.created_by, r.winner_id, r.created_at, r.finished_at,
               COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
               COALESCE(pz.tags, '') AS tags,
               p.username AS creator_name,
               (SELECT COUNT(*) FROM game_logs gl WHERE gl.room_id = r.id AND gl.type = 'ask') AS ask_count,
               (SELECT COUNT(*) FROM room_presence rp
                WHERE rp.room_id = r.id
                  AND rp.last_active_at > datetime('now', 'localtime', '-1 hour')) AS active_players,
               (SELECT MAX(gl2.created_at) FROM game_logs gl2
                WHERE gl2.room_id = r.id) AS last_active_at
        FROM rooms r
        LEFT JOIN players p ON p.id = r.created_by
        LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
        WHERE r.status IN ('waiting', 'playing')
           OR (
             r.status = 'finished'
             AND r.finished_at IS NOT NULL
             AND r.finished_at >= datetime('now', 'localtime', ?)
           )
        ORDER BY CASE r.status WHEN 'finished' THEN 2 ELSE 0 END, COALESCE((SELECT MAX(gl.created_at) FROM game_logs gl WHERE gl.room_id = r.id), r.created_at) DESC
        LIMIT 50
        """,
        (f"-{finished_retention_hours} hours",),
    )
    return rows


@router.post("/create")
async def create_room(body: RoomCreateBody, player: dict = Depends(current_player)):
    unlimited_creator = bool(player.get("is_admin")) or (player.get("username") or "").lower() == "nanshan"
    if not unlimited_creator:
        active = await fetch_one(
            "SELECT id FROM rooms WHERE created_by = ? AND status IN ('waiting','playing')",
            (player["id"],),
        )
        if active:
            raise HTTPException(status_code=400, detail="请先关闭你当前的房间")
        max_rooms = int(await get_setting("max_rooms", "5"))
        current = await fetch_one("SELECT COUNT(*) AS c FROM rooms WHERE status IN ('waiting','playing')")
        if int(current["c"]) >= max_rooms:
            raise HTTPException(status_code=400, detail="当前房间已满")

    puzzle_id = None
    if body.mode == "random":
        if body.puzzle_id:
            puzzle = await fetch_one("SELECT * FROM puzzles WHERE id = ? AND enabled = 1", (body.puzzle_id,))
        else:
            puzzle = await fetch_one("SELECT * FROM puzzles WHERE enabled = 1 ORDER BY RANDOM() LIMIT 1")
        if not puzzle:
            raise HTTPException(status_code=404, detail="题目不存在")
        puzzle_id = puzzle["id"]
        title = puzzle["title"]
        surface, answer = puzzle["surface"], puzzle["answer"]
    else:
        title = clean_content(body.title or "", TITLE_LIMIT)
        surface = clean_content(body.surface or "", SURFACE_LIMIT)
        answer = clean_content(body.answer or "", ANSWER_LIMIT)
        if body.mode == "custom":
            reason = await scan_text(f"{surface}\n{answer}")
            if reason:
                raise HTTPException(status_code=400, detail=reason)
            await execute(
                "INSERT INTO puzzle_submissions (surface, answer, tags, submitted_by) VALUES (?, ?, ?, ?)",
                (surface, answer, body.tags[:100], player["id"]),
            )

    rid = room_id()
    while await fetch_one("SELECT id FROM rooms WHERE id = ?", (rid,)):
        rid = room_id()
    await execute(
        "INSERT INTO rooms (id, puzzle_id, title, surface, answer, status, created_by) VALUES (?, ?, ?, ?, ?, 'playing', ?)",
        (rid, puzzle_id, title, surface, answer, player["id"]),
    )
    await execute(
        "INSERT INTO game_logs (room_id, player_id, type, content) VALUES (?, ?, 'system', ?)",
        (rid, player["id"], "游戏开始"),
    )
    return {"room_id": rid}


@router.get("/{room_id}")
async def get_room(room_id: str, player: dict = Depends(current_player)):
    room = await fetch_one(
        """
        SELECT r.id, r.puzzle_id,
               COALESCE(NULLIF(TRIM(r.title), ''), NULLIF(TRIM(pz.title), ''), '') AS title,
               r.surface, r.answer, r.status, r.created_by, r.winner_id,
               r.manual_hint_count, r.last_hint_at_ask_count, r.created_at, r.finished_at,
               COALESCE(pz.tags, '') AS tags
        FROM rooms r
        LEFT JOIN puzzles pz ON pz.id = r.puzzle_id
        WHERE r.id = ?
        """,
        (room_id,),
    )
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    logs = await fetch_all(
        """
        SELECT gl.id, gl.room_id, gl.player_id, gl.type, gl.content, gl.judgment,
               gl.hint_text, gl.resolved, gl.created_at,
               p.username, p.is_guest, p.is_ai
        FROM game_logs gl
        LEFT JOIN players p ON p.id = gl.player_id
        WHERE gl.room_id = ?
        ORDER BY gl.id ASC
        """,
        (room_id,),
    )
    notes = await fetch_all(
        """
        SELECT rn.*, p.username, p.is_guest FROM room_notes rn
        LEFT JOIN players p ON p.id = rn.player_id
        WHERE rn.room_id = ? ORDER BY rn.updated_at DESC
        """,
        (room_id,),
    )
    for note in notes:
        if not (note.get("username") or "").strip():
            note["username"] = f"游客{note['player_id']}"
    manual_hint_row = await fetch_one(
        "SELECT COUNT(*) AS c FROM game_logs WHERE room_id = ? AND player_id = ? AND type = 'hint_offer'",
        (room_id, player["id"]),
    )
    data = _public_room(room)
    data["manual_hint_count"] = int(manual_hint_row["c"] if manual_hint_row else 0)
    data["logs"] = logs
    data["notes"] = notes
    return data


@router.post("/{room_id}/close")
async def close_room(room_id: str, player: dict = Depends(current_player)):
    room = await fetch_one("SELECT * FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    if room["created_by"] != player["id"] and not player.get("is_admin"):
        raise HTTPException(status_code=403, detail="只能关闭自己的房间")
    await execute(
        f"UPDATE rooms SET status = 'finished', finished_at = {SQL_NOW} WHERE id = ?",
        (room_id,),
    )
    return {"ok": True}


@router.get("/profile/me")
async def profile(player: dict = Depends(current_player)):
    rooms = await fetch_all(
        """
        SELECT id, surface, status, winner_id, created_at, finished_at
        FROM rooms
        WHERE created_by = ? OR id IN (SELECT DISTINCT room_id FROM game_logs WHERE player_id = ?)
        ORDER BY created_at DESC LIMIT 30
        """,
        (player["id"], player["id"]),
    )
    return {"player": public_player(player) | {k: player[k] for k in ["ask_count", "ask_count_y", "ask_count_n", "ask_count_u", "ask_count_p", "win_count", "game_count"]}, "rooms": rooms}
