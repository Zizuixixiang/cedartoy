"""Known-dead radio cache compatibility; no upstream modules are imported."""


def is_dead(station):
    return isinstance(station, dict) and station.get("dead") is True


def clear_dead_radio(data):
    """Copy only affected cache fields, preserving the caller's save/other state.

    last_env may hold a reduced copy without the dead flag. In that case the
    explicitly dead sticky station's URL still identifies the stale env radio.
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
