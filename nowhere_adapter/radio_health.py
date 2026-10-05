"""Worker-only bounded fallback validation using the existing safe proxy dialer."""
import asyncio
from pathlib import Path
import sys

from .radio_state import clear_dead_radio, health_key, is_dead, remember_health, stream_health

PROBE_TIMEOUT = 4


async def probe(url):
    # A disposable process bounds DNS, TLS, headers and redirects together.
    # Cancelling a thread would leave a stuck DNS/socket operation running.
    child = await asyncio.create_subprocess_exec(
        sys.executable, '-B', '-m', 'nowhere_adapter.radio_health', url,
        cwd=str(Path(__file__).resolve().parents[1]),
        stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
    try:
        return await asyncio.wait_for(child.wait(), PROBE_TIMEOUT) == 0
    except asyncio.TimeoutError:
        return False
    finally:
        if child.returncode is None:
            try:
                child.kill()
            except ProcessLookupError:
                pass
        await child.wait()


def install(server):
    original_load = server.radio._load_fallback
    fallback_urls = {health_key(s.get('stream_url')) for s in original_load()}
    server.radio._load_fallback = lambda: [s for s in original_load() if not is_dead(s)]
    original_nearest = server.radio.nearest
    original_get = server._get_radio

    async def usable(station):
        if station is None or is_dead(station):
            return False
        url = station.get('stream_url')
        if health_key(url) not in fallback_urls:
            return True  # Online selection remains upstream; playback still validates.
        healthy = stream_health(url)
        if healthy is None:
            try:
                healthy = await probe(url)
            except OSError:
                healthy = False
            remember_health(url, healthy)
        return healthy

    async def nearest(*args, **kwargs):
        station = await original_nearest(*args, **kwargs)
        return station if await usable(station) else None

    async def get_radio(*args, **kwargs):
        # Only game actions call this; ordinary /state and /history never probe.
        state = server._state
        if state.radio_station is not None and not await usable(state.radio_station):
            cleaned = clear_dead_radio(state.to_dict())
            state.radio_station, state.radio_pos = None, None
            state.last_env = cleaned.get('last_env')
        return await original_get(*args, **kwargs)

    server.radio.nearest = nearest
    server._get_radio = get_radio


if __name__ == '__main__':
    from .radio import open_stream
    try:
        connection, response, _ = open_stream(sys.argv[1])
        response.close()
        connection.close()
    except Exception:
        sys.exit(1)
