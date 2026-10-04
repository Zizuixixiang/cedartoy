"""History response enrichment: real save envelopes, no production writes."""
import copy
import json
import os
from pathlib import Path
from unittest.mock import Mock, patch
from contextlib import contextmanager

from tests_toy.test_nowhere import TemporaryStores, archive, handler, storage
from tests_toy.test_nowhere_radio import upstream_footprints
from nowhere_adapter import history, radio

FIP = 'https://icecast.radiofrance.fr/fip-midfi.mp3'


def journey_archive():
    saved = archive('巴黎')
    state = saved['files']['journey.json']
    state.update(journey_slug='paris', landed_at='2026-10-02T11:00:00Z',
                 radio_station={'name':'FIP', 'genre':'eclectic', 'country':'FR',
                                'stream_url':FIP, 'private':'not public'})
    saved['files'].update({
        'journeys/index.json': {'active':'paris', 'journeys':[
            {'slug':'paris', 'place_name':'巴黎', 'landed_at':state['landed_at'],
             'departed_at':'2026-10-02T14:00:00Z', 'last_active':'2026-10-02T14:00:00Z'}]},
        'journeys/paris.json':copy.deepcopy(state),
        'footprints.json': {'items':[
            {'action':action, 'place':'巴黎', 'at':f'2026-10-02T11:01:0{i}Z',
             'lat':48.85, 'lon':2.35, 'text':f'巴黎行动 {i}'}
            for i,action in enumerate(('land','walk','mark','postcard','land'))]},
    })
    return saved


class HistoryTests(TemporaryStores):
    def enriched(self, saved, items=None):
        return history.enrich_history({'footprints': items if items is not None else upstream_footprints(saved)}, saved)['footprints']

    def test_five_actions_read_only_and_locked_handler_response(self):
        saved = journey_archive()
        self.put('101', saved)
        before = (storage.ROOT/'101/save.json').read_bytes()
        body = {'landings':[], 'path':[], 'footprints':upstream_footprints(saved)}
        original = copy.deepcopy(body)
        @contextmanager
        def worker_for(player):
            self.assertEqual(player, '101')
            yield Mock(call=Mock(return_value={'result':{'status':200,'body':body}}))
        with patch.object(handler.POOL, 'acquire', side_effect=worker_for), patch.object(storage, 'write', side_effect=AssertionError('read-only')):
            result = handler.execute('101', {'action':'_web', 'method':'GET', 'path':'/history'})
        self.assertEqual(body, original)
        self.assertEqual((storage.ROOT/'101/save.json').read_bytes(), before)
        self.assertEqual(len(result['body']['footprints']), 5)
        for item in result['body']['footprints']:
            self.assertEqual(item['stream_url'], FIP)
            self.assertEqual(item['station'], {'name':'FIP','genre':'eclectic','country':'FR'})
            self.assertEqual(radio.authorize('101', item['stream_url']), FIP)

    def test_cross_journey_active_state_and_listen_priority(self):
        saved = journey_archive(); files = saved['files']
        old = files['journeys/paris.json']
        current = copy.deepcopy(old)
        current.update(place_name='东京', journey_slug='tokyo', landed_at='2026-10-03T00:00:00Z')
        current.pop('radio_station')
        current['last_env']={'radio':{'name':'Tokyo FM','stream_url':'https://tokyo.test/live'}}
        files['journey.json']=current
        files['journeys/tokyo.json']=copy.deepcopy(old)  # stale: active must use journey.json
        files['journeys/index.json']['active']='tokyo'
        files['journeys/index.json']['journeys'].append({'slug':'tokyo','place_name':'东京',
            'landed_at':current['landed_at'],'last_active':'2026-10-03T02:00:00Z'})
        listen = {'place':'东京','at':'2026-10-03T01:00:00Z','stream_url':'https://listen.test/live','station':{'name':'Listen original'}}
        items = [*files['footprints.json']['items'],
                 {'place':'东京','at':'2026-10-03T01:00:00+00:00'}, listen]
        result = self.enriched(saved,items)
        self.assertEqual([f['stream_url'] for f in result], [FIP]*5+['https://tokyo.test/live',listen['stream_url']])
        self.assertEqual(result[-1],listen)
        current.pop('last_env')
        self.assertNotIn('stream_url',self.enriched(saved,items)[-2])

    def test_missing_times_unique_place_and_ambiguity(self):
        saved=journey_archive(); entries=saved['files']['journeys/index.json']['journeys']
        self.assertEqual(self.enriched(saved,[{'place':'巴黎'}])[0]['stream_url'], FIP)
        self.assertNotIn('stream_url', self.enriched(saved,[{'place':'巴黎','at':'2026-10-01T00:00:00Z'}])[0])
        self.assertNotIn('stream_url', self.enriched(saved,[{'place':'巴黎','at':'2026-10-04T00:00:00Z'}])[0])
        saved['files']['journeys/again.json']=copy.deepcopy(saved['files']['journey.json'])
        entries.append({**entries[0], 'slug':'again'})
        for item in ({'place':'巴黎'}, {'place':'巴黎','at':'2026-10-02T12:00:00Z'}):
            self.assertNotIn('stream_url',self.enriched(saved,[item])[0])
        entries.pop()
        entries[0].pop('landed_at');saved['files']['journey.json'].pop('landed_at')
        self.assertEqual(self.enriched(saved,[{'place':'巴黎','at':'2026-10-02T12:00:00Z'}])[0]['stream_url'],FIP)
        self.assertNotIn('stream_url',self.enriched(saved,[{'place':'别处','at':'2026-10-02T12:00:00Z'}])[0])

    def test_unsafe_station_and_other_slots_never_supply_radio(self):
        saved=journey_archive(); self.put('202',saved); self.put('101:2',saved)
        empty=archive('巴黎')
        self.assertNotIn('stream_url',self.enriched(empty,[{'place':'巴黎'}])[0])
        for url in ('', '//evil.test/live','javascript:alert(1)','data:audio/wav,x','file:///x','ftp://evil.test/live','https://user:pw@host/x'):
            saved['files']['journey.json']['radio_station']['stream_url']=url
            self.assertTrue(all('stream_url' not in f for f in self.enriched(saved)))

    def test_handler_only_enriches_from_requested_slot_snapshot(self):
        expected = {'101': None, '101:2': FIP, '202': 'https://other.test/live'}
        for player, url in expected.items():
            saved = journey_archive()
            if url is None:
                saved['files']['journey.json'].pop('radio_station')
            else:
                saved['files']['journey.json']['radio_station']['stream_url'] = url
            self.put(player, saved)
        @contextmanager
        def worker_for(player):
            def read(payload):
                return {'result': {'status':200, 'body':{
                    'footprints':upstream_footprints(payload['archive'])}}}
            yield Mock(call=Mock(side_effect=read))
        with patch.object(handler.POOL, 'acquire', side_effect=worker_for):
            for player, url in expected.items():
                with patch.object(storage, 'read', wraps=storage.read) as reads:
                    body=handler.execute(player, {'action':'_web','method':'GET','path':'/history'})['body']
                    reads.assert_called_once_with(player)
                self.assertEqual([item.get('stream_url') for item in body['footprints']], [url]*5)

    def test_explicit_paris_copy_when_provided(self):
        # Optional extra fixture, never open a production path from tests.
        if not os.environ.get('NOWHERE_PARIS_COPY'):
            self.skipTest('requires explicitly provided read-only Paris copy')
        path=Path(os.environ['NOWHERE_PARIS_COPY']); before=path.read_bytes()
        saved=json.loads(before)
        result=self.enriched(saved)
        actions=[f for f in result if not f.get('legacy')]
        self.assertEqual(len(actions),5)
        self.assertTrue(all(f['stream_url']==FIP and f['station']['name']=='FIP' for f in actions))
        self.assertEqual(path.read_bytes(),before)
