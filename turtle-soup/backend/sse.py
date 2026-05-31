import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from auth_utils import current_player
from presence import enter_room, leave_room


router = APIRouter()
_connections: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def broadcast(room_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data}
    for queue in list(_connections.get(room_id, set())):
        await queue.put(payload)


@router.get("/sse/{room_id}")
async def room_events(room_id: str, player: dict = Depends(current_player)):
    player_id = player["id"]
    await enter_room(room_id, player_id)
    queue: asyncio.Queue = asyncio.Queue()
    _connections[room_id].add(queue)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            _connections[room_id].discard(queue)
            await leave_room(room_id, player_id)

    return StreamingResponse(stream(), media_type="text/event-stream")
