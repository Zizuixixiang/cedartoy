"""Read-only station enrichment for the upstream travel-trail response."""
import copy
from datetime import datetime
import re

from .radio import canonical_url, dead_streams, stream_is_dead
from .radio_state import clear_dead_radio, is_dead


def _time(value):
    try:
        stamp = datetime.fromisoformat(value.replace('Z', '+00:00'))
        # Footprint at/index timestamps are real UTC; don't guess local time.
        return stamp.timestamp() if stamp.tzinfo is not None else None
    except (AttributeError, TypeError, ValueError, OverflowError):
        return None


def _journeys(files):
    index = files.get('journeys/index.json', {})
    current = files.get('journey.json', {})
    entries = index.get('journeys', [])
    active = index.get('active')
    for entry in entries:
        state = current if entry['slug'] == active else files.get('journeys/' + entry['slug'] + '.json', {})
        end = [_time(entry.get(key)) for key in ('departed_at', 'last_active')]
        end = [value for value in end if value is not None]
        yield (entry.get('place_name') or state.get('place_name'),
               _time(entry.get('landed_at') or state.get('landed_at')),
               max(end) if end else None, state)
    # Legacy saves may predate the index. Only the actual current state exists.
    if not entries:
        yield (current.get('place_name'), _time(current.get('landed_at')), None, current)


def _station(state):
    state = clear_dead_radio(state)
    env = state.get('last_env') or {}
    for station in (state.get('radio_station'), env.get('radio') if isinstance(env, dict) else None):
        if not isinstance(station, dict):
            continue
        url = station.get('stream_url')
        if not isinstance(url, str) or not re.match(r'^https?://', url, re.I):
            continue
        try:
            canonical_url(url)
        except ValueError:
            continue
        public = {key: station[key] for key in ('name', 'genre', 'country')
                  if isinstance(station.get(key), str) and station[key]}
        if public.get('name'):
            return url, public
    return None


def enrich_history(body, saved):
    """Use only this locked slot's snapshot, never mutate worker/save objects.

    A unique timestamp interval wins; overlapping intervals are ambiguous.
    Missing old timestamps permit an exact, unique place-name fallback only.
    Complete intervals that exclude an action never grant a fallback match.
    """
    result = copy.deepcopy(body)
    journeys = list(_journeys(saved['files']))
    dead_urls = dead_streams(saved)
    for item in result.get('footprints', []):
        if isinstance(item, dict) and (is_dead(item.get('station'))
                                      or stream_is_dead(item.get('stream_url'), dead_urls)):
            # Response only: retain the user's historical text/station metadata.
            if item.get('stream_url'):
                item['stream_unavailable'] = True
            item.pop('stream_url', None)
            continue
        # Preserve listen fields (including its station) exactly. Malformed
        # nonempty URLs also stay untouched: safeHttpUrl will hide them.
        if not isinstance(item, dict) or item.get('stream_url'):
            continue
        at = _time(item.get('at'))
        matches = [j for j in journeys if at is not None and j[1] is not None
                   and j[2] is not None and j[1] <= at <= j[2]]
        if len(matches) > 1:
            continue
        if matches:
            journey = matches[0]
            if item.get('place') and item['place'] != journey[0]:
                continue  # Conflicting location evidence is not a safe match.
        else:
            places = [j for j in journeys if item.get('place') and item['place'] == j[0]]
            if len(places) != 1:
                continue
            journey = places[0]
            if at is not None:
                if journey[1] is not None and at < journey[1]:
                    continue
                if journey[2] is not None and at > journey[2]:
                    continue
                if journey[1] is not None and journey[2] is not None:
                    continue
        station = _station(journey[3])
        if station and not stream_is_dead(station[0], dead_urls):
            item['stream_url'], item['station'] = station
    return result
