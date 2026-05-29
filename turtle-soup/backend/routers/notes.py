from fastapi import APIRouter, Depends, HTTPException

from auth_utils import current_player
from database import execute, fetch_one
from models import NoteBody
from sse import broadcast
from utils import clean_content

router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("/{room_id}")
async def add_note(room_id: str, body: NoteBody, player: dict = Depends(current_player)):
    content = clean_content(body.content, 50)
    nid = await execute(
        "INSERT INTO room_notes (room_id, player_id, content) VALUES (?, ?, ?)",
        (room_id, player["id"], content),
    )
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (nid,))
    await broadcast(room_id, "new_note", note)
    return note


@router.put("/{note_id}")
async def update_note(note_id: int, body: NoteBody, player: dict = Depends(current_player)):
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (note_id,))
    if not note:
        raise HTTPException(status_code=404, detail="记事不存在")
    if note["player_id"] != player["id"]:
        raise HTTPException(status_code=403, detail="只能修改自己的记事")
    content = clean_content(body.content, 50)
    await execute(
        "UPDATE room_notes SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (content, note_id),
    )
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (note_id,))
    await broadcast(note["room_id"], "update_note", note)
    return note


@router.delete("/{note_id}")
async def delete_note(note_id: int, player: dict = Depends(current_player)):
    note = await fetch_one("SELECT * FROM room_notes WHERE id = ?", (note_id,))
    if not note:
        raise HTTPException(status_code=404, detail="记事不存在")
    if note["player_id"] != player["id"]:
        raise HTTPException(status_code=403, detail="只能删除自己的记事")
    await execute("DELETE FROM room_notes WHERE id = ?", (note_id,))
    await broadcast(note["room_id"], "delete_note", {"id": note_id})
    return {"ok": True}
