from fastapi import APIRouter, Depends

from auth_utils import current_player
from database import fetch_all

router = APIRouter(prefix="/leaderboard", tags=["leaderboard"])


@router.get("/{metric}")
async def leaderboard(metric: str, player: dict = Depends(current_player)):
    del player
    columns = {
        "games": "game_count",
        "wins": "win_count",
        "asks": "ask_count",
        "yes": "ask_count_y",
        "no": "ask_count_n",
    }
    col = columns.get(metric, "game_count")
    return await fetch_all(
        f"""
        SELECT id, COALESCE(username, '游客' || id) AS username, is_ai, {col} AS score
        FROM players
        WHERE is_guest = 0 OR {col} > 0
        ORDER BY {col} DESC, id ASC
        LIMIT 20
        """
    )
