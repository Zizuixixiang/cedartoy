from fastapi import APIRouter, Depends, HTTPException

from auth_utils import admin_player, current_player
from database import execute, fetch_all, fetch_one
from models import RoomCreateBody
from utils import clean_content

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


@router.get("/random")
async def random_puzzle(player: dict = Depends(current_player)):
    del player
    row = await fetch_one(
        "SELECT id, surface, tags FROM puzzles WHERE enabled = 1 ORDER BY RANDOM() LIMIT 1"
    )
    if not row:
        raise HTTPException(status_code=404, detail="题库暂无可用题目")
    return row


@router.post("/submit")
async def submit_puzzle(body: RoomCreateBody, player: dict = Depends(current_player)):
    surface = clean_content(body.surface or "", 500)
    answer = clean_content(body.answer or "", 1000)
    sid = await execute(
        "INSERT INTO puzzle_submissions (surface, answer, tags, submitted_by) VALUES (?, ?, ?, ?)",
        (surface, answer, body.tags[:100], player["id"]),
    )
    return {"id": sid, "status": "pending"}


@router.get("/")
async def list_puzzles(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all("SELECT id, surface, tags, enabled, created_at FROM puzzles ORDER BY id DESC")


@router.post("/")
async def add_puzzle(body: RoomCreateBody, admin: dict = Depends(admin_player)):
    surface = clean_content(body.surface or "", 500)
    answer = clean_content(body.answer or "", 1000)
    pid = await execute(
        "INSERT INTO puzzles (surface, answer, tags, created_by) VALUES (?, ?, ?, ?)",
        (surface, answer, body.tags[:100], admin["id"]),
    )
    return {"id": pid}


@router.patch("/{puzzle_id}/toggle")
async def toggle_puzzle(puzzle_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE puzzles SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (puzzle_id,))
    return {"ok": True}


@router.delete("/{puzzle_id}")
async def delete_puzzle(puzzle_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("DELETE FROM puzzles WHERE id = ?", (puzzle_id,))
    return {"ok": True}
