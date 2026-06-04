from fastapi import APIRouter, Depends, HTTPException

from auth_utils import admin_player, current_player
from database import execute, fetch_all, fetch_one
from models import PuzzleBody, RoomCreateBody
from utils import ANSWER_LIMIT, SURFACE_LIMIT, TAGS_LIMIT, TITLE_LIMIT, clean_content, strip_puzzle_text

router = APIRouter(prefix="/puzzles", tags=["puzzles"])


def _normalize_puzzle_body(body: PuzzleBody) -> tuple[str, str, str, str]:
    title = strip_puzzle_text(body.title, label="汤名", limit=TITLE_LIMIT)
    surface = strip_puzzle_text(body.surface, required=True, label="汤面", limit=SURFACE_LIMIT)
    answer = strip_puzzle_text(body.answer, required=True, label="汤底", limit=ANSWER_LIMIT)
    tags = strip_puzzle_text(body.tags, label="标签", limit=TAGS_LIMIT)
    return title, surface, answer, tags


@router.get("/random")
async def random_puzzle(player: dict = Depends(current_player)):
    del player
    row = await fetch_one(
        "SELECT id, title, surface, tags FROM puzzles WHERE enabled = 1 ORDER BY RANDOM() LIMIT 1"
    )
    if not row:
        raise HTTPException(status_code=404, detail="题库暂无可用题目")
    return row


@router.get("/public")
async def public_puzzles(player: dict = Depends(current_player)):
    del player
    return await fetch_all(
        """
        SELECT id, title, surface, tags
        FROM puzzles
        WHERE enabled = 1
        ORDER BY id ASC
        """
    )


@router.post("/submit")
async def submit_puzzle(body: RoomCreateBody, player: dict = Depends(current_player)):
    surface = clean_content(body.surface or "", SURFACE_LIMIT)
    answer = clean_content(body.answer or "", ANSWER_LIMIT)
    sid = await execute(
        "INSERT INTO puzzle_submissions (surface, answer, tags, submitted_by) VALUES (?, ?, ?, ?)",
        (surface, answer, body.tags[:100], player["id"]),
    )
    return {"id": sid, "status": "pending"}


@router.get("/")
async def list_puzzles(admin: dict = Depends(admin_player)):
    del admin
    return await fetch_all(
        "SELECT id, title, surface, tags, enabled, created_at FROM puzzles ORDER BY id DESC"
    )


@router.get("/{puzzle_id}")
async def get_puzzle(puzzle_id: int, admin: dict = Depends(admin_player)):
    del admin
    row = await fetch_one(
        "SELECT id, title, surface, answer, tags, enabled, created_at FROM puzzles WHERE id = ?",
        (puzzle_id,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="题目不存在")
    return row


@router.post("/")
async def add_puzzle(body: PuzzleBody, admin: dict = Depends(admin_player)):
    title, surface, answer, tags = _normalize_puzzle_body(body)
    pid = await execute(
        "INSERT INTO puzzles (title, surface, answer, tags, created_by) VALUES (?, ?, ?, ?, ?)",
        (title, surface, answer, tags, admin["id"]),
    )
    return {"id": pid}


@router.put("/{puzzle_id}")
async def update_puzzle(puzzle_id: int, body: PuzzleBody, admin: dict = Depends(admin_player)):
    del admin
    existing = await fetch_one("SELECT id FROM puzzles WHERE id = ?", (puzzle_id,))
    if not existing:
        raise HTTPException(status_code=404, detail="题目不存在")
    title, surface, answer, tags = _normalize_puzzle_body(body)
    await execute(
        "UPDATE puzzles SET title = ?, surface = ?, answer = ?, tags = ? WHERE id = ?",
        (title, surface, answer, tags, puzzle_id),
    )
    return {"ok": True}


@router.patch("/{puzzle_id}/toggle")
async def toggle_puzzle(puzzle_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE puzzles SET enabled = CASE enabled WHEN 1 THEN 0 ELSE 1 END WHERE id = ?", (puzzle_id,))
    return {"ok": True}


@router.delete("/{puzzle_id}")
async def delete_puzzle(puzzle_id: int, admin: dict = Depends(admin_player)):
    del admin
    await execute("UPDATE rooms SET puzzle_id = NULL WHERE puzzle_id = ?", (puzzle_id,))
    await execute("DELETE FROM puzzles WHERE id = ?", (puzzle_id,))
    return {"ok": True}
