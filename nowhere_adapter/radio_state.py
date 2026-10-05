"""Process-local radio health; reads never perform network I/O or write saves."""
from collections import OrderedDict
import threading
import time

# Confirmed non-audio fallback, also covers old saves without a `dead` flag.
DENIED_STREAMS = frozenset({'https://www.jrtv.gov.jo/radio/stream'})
HEALTH = OrderedDict()
HEALTH_LOCK = threading.Lock()
MAX_HEALTH = 256


def health_key(url):
    # Lazy import avoids a cycle with the proxy's URL/SSRF implementation.
    from .radio import canonical_url
    try:
        return canonical_url(url)
    except ValueError:
        return None


def stream_health(url):
    key = health_key(url)
    if key is None or key in DENIED_STREAMS:
        return False
    with HEALTH_LOCK:
        entry = HEALTH.get(key)
        if entry and entry[1] > time.monotonic():
            return entry[0]
        HEALTH.pop(key, None)
    return None


def remember_health(url, healthy):
    key = health_key(url)
    if key is None:
        return
    with HEALTH_LOCK:
        HEALTH[key] = (healthy, time.monotonic() + (3600 if healthy else 300))
        HEALTH.move_to_end(key)
        while len(HEALTH) > MAX_HEALTH:
            HEALTH.popitem(last=False)


def is_dead(station):
    return isinstance(station, dict) and (station.get("dead") is True
        or (station.get("stream_url") is not None and stream_health(station['stream_url']) is False))


def clear_dead_radio(data):
    """Copy only affected cache fields, preserving the caller's save/other state.

    last_env may hold a reduced copy without the dead flag. In that case the
    dead/failed sticky station's URL still identifies the stale env radio.
    """
    station = data.get("radio_station")
    env = data.get("last_env")
    env_radio = env.get("radio") if isinstance(env, dict) else None
    dead_urls = {r.get("stream_url") for r in (station, env_radio)
                 if is_dead(r) and isinstance(r.get("stream_url"), str) and r["stream_url"]}

    def dead(r):
        return is_dead(r) or (isinstance(r, dict) and isinstance(r.get("stream_url"), str)
                              and r["stream_url"] in dead_urls)

    result = dict(data)
    if dead(station):
        result.update(radio_station=None, radio_pos=None)
    if dead(env_radio):
        result["last_env"] = {**env, "radio": None}
    return result
