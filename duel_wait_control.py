"""Canonical request generations shared by sync fallback and async tickets."""

import logging
import threading
import time

logger = logging.getLogger(__name__)
_lock = threading.Lock()
_active = {}
_last_generation = 0
LEASE_SECONDS = 3720


def cancelled_response(room_id):
    return {
        "ok": True, "status": "wait_cancelled", "room_id": room_id,
        "message": "挂等已取消或被新请求替代；停止此调用链，不要据此行动或自动续等。需要时显式重新挂等。",
    }


def begin(payload):
    global _last_generation
    player, room = payload.get("player_id"), payload.get("room_id")
    if not player or not room:
        return
    key = (str(player), str(room).strip().upper())
    payload["room_id"] = key[1]
    with _lock:
        now = time.time_ns()
        cutoff = now - LEASE_SECONDS * 1_000_000_000
        for expired in [key for key, value in _active.items() if value < cutoff]:
            del _active[expired]
        _last_generation = max(now, _last_generation + 1)
        payload["wait_generation"] = _last_generation
        waiting = payload.get("action") in {"state", "move"} and str(payload.get("wait")).lower() == "true"
        previous = _active.pop(key, None)
        if previous:
            logger.info("duel_wait %s player=%s room=%s", "superseded" if waiting else "cancelled", *key)
        if waiting:
            _active[key] = _last_generation
            logger.info("duel_wait created player=%s room=%s", *key)


def finish(payload, response=None, *, release=True):
    """Linearize completion against cancellation, including buffered responses."""
    if payload.get("action") not in {"state", "move"} or str(payload.get("wait")).lower() != "true":
        return response
    key = (str(payload.get("player_id")), str(payload.get("room_id")))
    generation = payload.get("wait_generation")
    if generation is None:
        return response
    with _lock:
        if _active.get(key) != generation:
            return cancelled_response(key[1])
        if release:
            del _active[key]
            logger.info("duel_wait %s player=%s room=%s", "finished" if response is not None else "abandoned", *key)
        return response
