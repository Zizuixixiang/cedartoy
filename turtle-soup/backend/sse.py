import asyncio
import json
from collections import defaultdict
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from auth_utils import current_player
from room_access import require_room_access
from models import NormalizedRoomId
from presence import enter_room, leave_room


router = APIRouter()
_connections: dict[str, set[asyncio.Queue]] = defaultdict(set)


async def broadcast(room_id: str, event: str, data: dict[str, Any]) -> None:
    payload = {"event": event, "data": data}
    for queue in list(_connections.get(room_id, set())):
        await queue.put(payload)


@router.get("/sse/{room_id}")
async def room_events(room_id: NormalizedRoomId, player: dict = Depends(current_player)):
    await require_room_access(room_id, player)
    player_id = player["id"]
    await enter_room(room_id, player_id)
    queue: asyncio.Queue = asyncio.Queue()
    _connections[room_id].add(queue)

    async def stream():
        try:
            yield ": connected\n\n"
            while True:
                item = None
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=25)
                except asyncio.TimeoutError:
                    pass
                try:
                    await require_room_access(room_id, player)
                except HTTPException:
                    return  # Binding/account changed; stop before sending any more room data.
                if item is not None:
                    yield f"event: {item['event']}\ndata: {json.dumps(item['data'], ensure_ascii=False)}\n\n"
                else:
                    yield ": ping\n\n"
        finally:
            _connections[room_id].discard(queue)
            await leave_room(room_id, player_id)

    return StreamingResponse(stream(), media_type="text/event-stream")
