"""Dead-radio compatibility, with real engine checks in an isolated process."""
import copy
import json
import os
from pathlib import Path
import subprocess
import unittest

from nowhere_adapter import history, radio
from nowhere_adapter.radio_state import clear_dead_radio
from tests_toy.test_nowhere import TemporaryStores, ROOT, archive, storage

DEAD = {'name': 'Nile FM', 'country': 'EG', 'dead': True,
        'stream_url': 'https://playerservices.streamtheworld.com/api/livestream-redirect/NILEFM.mp3'}
LIVE = {'name': 'Live', 'country': 'EG', 'dead': False, 'stream_url': 'https://live.example.test/radio'}


class DeadRadioTests(TemporaryStores):
    def test_cache_cleanup_is_narrow_and_does_not_mutate_source(self):
        state = archive()['files']['journey.json']
        state.update(radio_station=DEAD, radio_pos=[30, 31],
                     last_env={'radio': {k: v for k, v in DEAD.items() if k != 'dead'},
                               'weather': {'temperature': 22}}, quotes=['keep'])
        original = copy.deepcopy(state)
        result = clear_dead_radio(state)
        self.assertEqual(state, original)
        expected = copy.deepcopy(state)
        expected.update(radio_station=None, radio_pos=None)
        expected['last_env']['radio'] = None
        self.assertEqual(result, expected)
        for station in (LIVE, {k: v for k, v in LIVE.items() if k != 'dead'}):
            state.update(radio_station=station, last_env={'radio': station})
            self.assertEqual(clear_dead_radio(state), state)
        state.update(radio_station=LIVE, last_env={'radio': DEAD, 'weather': 'keep'})
        result = clear_dead_radio(state)
        self.assertEqual(result['radio_station'], LIVE)
        self.assertIsNone(result['last_env']['radio'])

    def test_history_and_proxy_suppress_dead_urls_but_preserve_live_and_save(self):
        saved = archive('开罗')
        state = saved['files']['journey.json']
        state.update(radio_station=DEAD, radio_pos=[30, 31], last_env={'radio': DEAD})
        items = [{'place': '开罗', 'text': 'keep history'},
                 {'place': '开罗', 'text': 'keep listen', 'stream_url': DEAD['stream_url'], 'station': {'name': 'Nile FM'}},
                 {'place': '开罗', 'text': 'live listen', 'stream_url': LIVE['stream_url']}]
        saved['files']['footprints.json'] = {'items': items}
        original = copy.deepcopy(saved)
        self.put('guest:deadradio', saved)
        result = history.enrich_history({'footprints': items}, saved)['footprints']
        self.assertNotIn('stream_url', result[0])
        self.assertNotIn('stream_url', result[1])
        self.assertEqual(result[1]['text'], items[1]['text'])
        self.assertEqual(result[2], items[2])
        self.assertEqual(saved, original)
        with self.assertRaises(radio.RadioError):
            radio.authorize('guest:deadradio', DEAD['stream_url'] + '#browser')
        self.assertEqual(radio.authorize('guest:deadradio', LIVE['stream_url']), LIVE['stream_url'])
        self.assertEqual(storage.read('guest:deadradio'), original)
        # Even after cache cleanup, historical listen links must stay blocked.
        saved['files']['journey.json'] = clear_dead_radio(state)
        self.assertNotIn('stream_url', history.enrich_history({'footprints': items}, saved)['footprints'][1])

    @unittest.skipUnless(os.environ.get('NOWHERE_REAL_TEST') == '1', 'requires real engine')
    def test_real_worker_fallback_restore_web_and_snapshot(self):
        self.run_worker_check()

    @unittest.skipUnless(os.environ.get('NOWHERE_DEAD_RADIO_COPY'), 'requires explicit temporary save copy')
    def test_explicit_production_copy(self):
        source = Path(os.environ['NOWHERE_DEAD_RADIO_COPY']).resolve()
        self.assertTrue(source.is_relative_to(Path('/tmp')), 'only a temporary copy is accepted')
        before = source.read_bytes()
        self.run_worker_check(str(source))
        saved = json.loads(before)
        items = saved['files'].get('footprints.json', {}).get('items', [])
        response = history.enrich_history({'footprints': items}, saved)
        self.assertTrue(all(item.get('stream_url') != DEAD['stream_url']
                            for item in response['footprints']))
        self.put('guest:deadradio', saved)
        with self.assertRaises(radio.RadioError):
            radio.authorize('guest:deadradio', DEAD['stream_url'])
        self.assertEqual(source.read_bytes(), before)

    def run_worker_check(self, source=''):
        home = self.root / 'worker'
        home.mkdir()
        env = {**os.environ, 'NOWHERE_HOME': str(home), 'NOWHERE_SAVE_ROOT': str(storage.ROOT),
               'NOWHERE_SHARED_DB': str(self.root / 'shared.db'), 'PYTHONDONTWRITEBYTECODE': '1',
               'PYTHONPATH': str(ROOT) + os.pathsep + str(ROOT / 'vendor/nowhere')}
        result = subprocess.run([str(ROOT / '.venv-nowhere/bin/python'), '-c', _WORKER_CHECK, source],
                                cwd=home, env=env, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


_WORKER_CHECK = r'''
import asyncio, copy, json, sys
from pathlib import Path
from unittest.mock import AsyncMock, patch
from nowhere_adapter.worker import Engine
from nowhere_adapter import storage
from nowhere_adapter.radio_state import clear_dead_radio

async def check():
    engine = Engine('guest:deadradio')
    await engine.initialize()
    radio = engine.server.radio
    assert all(s.get('dead') is not True for s in radio._load_fallback())
    assert radio._pick_nearest_from_fallback(30.04, 31.24, 'EG') is None
    dead = {'name':'Dead', 'dead':True, 'country':'EG', 'lat':30, 'lon':31,
            'stream_url':'https://dead.example.test/radio'}
    live = {**dead, 'name':'Live', 'dead':False, 'stream_url':'https://live.example.test/radio'}
    for station in (live, {k:v for k,v in live.items() if k != 'dead'}):
        with patch.object(radio, '_fallback_cache', [dead, station]):
            assert radio._pick_nearest_from_fallback(30, 31, 'EG') == station
    with patch.object(radio, '_fallback_cache', [dead, {**live, 'country':'FR'}]), patch.object(radio, '_MIRRORS', []):
        assert await radio.nearest(30, 31, 'EG') is None  # Never substitute a foreign station.

    if sys.argv[1]:
        saved = json.loads(Path(sys.argv[1]).read_text())
        assert saved['files']['journey.json']['radio_station']['dead'] is True
    else:
        state = engine.state.WorldState().to_dict()
        state.update(pos=[30,31], place_name='Cairo', journey_slug='cairo',
                     radio_station=dead, radio_pos=[30,31],
                     last_env={'radio':dead, 'weather':{'temperature':22}},
                     quotes=['keep'], total_distance_km=37, cotraveler_alone=True)
        saved = {'format':'cedartoy.nowhere.v1', 'upstream':storage.VERSION, 'generation':'radio-test',
                 'files':{'journey.json':state, 'journeys/old.json':copy.deepcopy(state),
                          'marks.json':[], 'notebook.json':{'flora':['keep']},
                          'postcards.json':{'items':[]}},
                 'images':{}, 'runtime':{'metadata':{'cotraveler':'0'}}}
    original = copy.deepcopy(saved)
    engine.restore(saved, saved['generation'])
    assert saved == original  # restore never rewrites the input archive.
    state = engine.server._state
    assert state.radio_station is None and state.radio_pos is None
    assert not (state.last_env or {}).get('radio')
    # The same from_dict hook covers inactive journey load/continue.
    old = engine.state.WorldState.from_dict(original['files']['journey.json'])
    assert old.radio_station is None and old.radio_pos is None
    view = await engine.web_action({'method':'GET', 'path':'/state'})
    assert view['status'] == 200 and view['body']['radio'] is None
    expected = clear_dead_radio(original['files']['journey.json'])
    normalized = engine.state.WorldState.from_dict(expected).to_dict()
    assert state.to_dict() == normalized
    # Check every persisted gameplay field, allowing upstream's set ordering.
    for key, value in expected.items():
        actual = state.to_dict()[key]
        if isinstance(getattr(state, key, None), set):
            assert set(actual) == set(value), key
        else:
            assert actual == value, key
    # Production IPC JSON-roundtrips tuples (including RNG state) before write.
    snapshot = json.loads(json.dumps(engine.snapshot()))
    with storage.locked('guest:deadradio'):
        storage.write('guest:deadradio', snapshot)
        written = storage.read('guest:deadradio')
    assert written['files']['journey.json'] == normalized
    for name, value in original['files'].items():
        if name not in {'journey.json', 'journeys/index.json', 'journeys/' + state.journey_slug + '.json'}:
            assert written['files'][name] == value, name
    assert written['images'] == original['images']
    assert written['runtime']['metadata'] == original['runtime']['metadata']
    with patch.object(radio, 'nearest', new=AsyncMock(return_value=None)) as nearest:
        assert await engine.server._get_radio(*state.pos) is None
        nearest.assert_awaited_once()  # No sticky reuse of the restored dead station.
    for station in (live, {k:v for k,v in live.items() if k != 'dead'}):
        good = copy.deepcopy(original)
        good['files']['journey.json'].update(radio_station=station, radio_pos=list(state.pos), last_env={'radio':station})
        engine.restore(good, good['generation'])
        view = await engine.web_action({'method':'GET', 'path':'/state'})
        assert view['body']['radio']['stream_url'] == station['stream_url']
        with patch.object(radio, 'nearest', new=AsyncMock()) as nearest:
            assert await engine.server._get_radio(*state.pos) == station
            nearest.assert_not_awaited()
    print('PASS fallback, restore, web, sticky, snapshot/write; other saved fields preserved')

asyncio.run(check())
'''
