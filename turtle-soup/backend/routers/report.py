from fastapi import APIRouter, Depends

from auth_utils import current_player
from database import execute
from models import ReportBody

router = APIRouter(prefix="/report", tags=["report"])


@router.post("")
async def report(body: ReportBody, player: dict = Depends(current_player)):
    rid = await execute(
        "INSERT INTO reports (reporter_id, target_player_id, room_id, log_id, reason) VALUES (?, ?, ?, ?, ?)",
        (player["id"], body.target_player_id, body.room_id, body.log_id, body.reason[:300]),
    )
    return {"id": rid, "status": "pending"}
