from fastapi import APIRouter, Depends, HTTPException

from auth_utils import current_player
from database import execute, fetch_one
from models import NormalizedRoomId, NoteBody
from sse import broadcast
from utils import ROOM_FINISHED_STATUS_HINT, SQL_NOW, clean_content

router = APIRouter(prefix="/notes", tags=["notes"])
ANSWER_REVEALED_NOTE_HINT = "你已经公布并查看过本局汤底，不能继续操作记事板。"


async def _ensure_player_can_note(room_id: str, player_id: int) -> None:
    row = await fetch_one(
        "SELECT 1 FROM room_answer_reveals WHERE room_id = ? AND player_id = ?",
        (room_id, player_id),
    )
    if row is not None:
        raise HTTPException(status_code=400, detail=ANSWER_REVEALED_NOTE_HINT)


async def _note_payload(note_id: int) -> dict:
    note = await fetch_one(
        """
        SELECT rn.*, p.username, p.is_guest
        FROM room_notes rn
        LEFT JOIN players p ON p.id = rn.player_id
        WHERE rn.id = ?
        """,
        (note_id,),
    )
    if note and not (note.get("username") or "").strip():
        note["username"] = f"游客{note['player_id']}"
    return note


async def _note_log_payload(log_id: int) -> dict:
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


@router.post("/{room_id}")
async def add_note(room_id: NormalizedRoomId, body: NoteBody, player: dict = Depends(current_player)):
    room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (room_id,))
    if not room:
        raise HTTPException(status_code=404, detail="房间不存在")
    if room["status"] == "finished":
        raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
    await _ensure_player_can_note(room_id, player["id"])
    content = clean_content(body.content, 50)
    nid = await execute(
        "INSERT INTO room_notes (room_id, player_id, content) VALUES (?, ?, ?)",
        (room_id, player["id"], content),
    )
    note = await _note_payload(nid)
    await broadcast(room_id, "new_note", note)
    log_id = await execute(
        "INSERT INTO game_logs (room_id, type, content, judgment) VALUES (?, 'system', ?, 'note_notice')",
        (room_id, "【系统提示】记事本有新记录。"),
    )
    await broadcast(room_id, "new_log", await _note_log_payload(log_id))
    return note


@router.put("/{note_id}")
async def update_note(note_id: int, body: NoteBody, player: dict = Depends(current_player)):
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (note_id,))
    if not note:
        raise HTTPException(status_code=404, detail="记事不存在")
    if note["player_id"] != player["id"]:
        raise HTTPException(status_code=403, detail="只能修改自己的记事")
    room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (note["room_id"],))
    if room and room["status"] == "finished":
        raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
    await _ensure_player_can_note(note["room_id"], player["id"])
    content = clean_content(body.content, 50)
    await execute(
        f"UPDATE room_notes SET content = ?, updated_at = {SQL_NOW} WHERE id = ?",
        (content, note_id),
    )
    note = await _note_payload(note_id)
    await broadcast(note["room_id"], "update_note", note)
    return note


@router.delete("/{note_id}")
async def delete_note(note_id: int, player: dict = Depends(current_player)):
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (note_id,))
    if not note:
        raise HTTPException(status_code=404, detail="记事不存在")
    if note["player_id"] != player["id"]:
        raise HTTPException(status_code=403, detail="只能删除自己的记事")
    room = await fetch_one("SELECT status FROM rooms WHERE id = ?", (note["room_id"],))
    if room and room["status"] == "finished":
        raise HTTPException(status_code=400, detail=ROOM_FINISHED_STATUS_HINT)
    await _ensure_player_can_note(note["room_id"], player["id"])
    await execute("DELETE FROM room_notes WHERE id = ?", (note_id,))
    await broadcast(note["room_id"], "delete_note", {"id": note_id})
    return {"ok": True}
