"""Offline health regressions; no production stores or real internet required."""
import asyncio
from collections import OrderedDict
import copy
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from nowhere_adapter import history, radio, radio_health, radio_state
from tests_toy.test_nowhere import TemporaryStores, archive, storage, web

JORDAN = {'name': 'Radio Jordan', 'country': 'JO',
          'homepage': 'https://jrtv.gov.jo',
          'stream_url': 'https://www.jrtv.gov.jo/radio/stream'}
LIVE = {'name': 'Live', 'country': 'JO', 'stream_url': 'https://live.example.test/audio'}


class HealthTests(TemporaryStores):
    def setUp(self):
        super().setUp()
        self.stack.enter_context(patch.object(radio_state, 'HEALTH', OrderedDict()))

    def test_jordan_restore_history_and_authorization_are_read_only(self):
        saved = archive('Amman')
        state = saved['files']['journey.json']
        state.update(radio_station=JORDAN, radio_pos=[31, 35], last_env={'radio': JORDAN, 'weather': 'keep'})
        items = [{'place': 'Amman', 'text': 'walk'}, {'text': 'listen', 'stream_url': JORDAN['stream_url']}]
        saved['files']['footprints.json'] = {'items': items}
        before = copy.deepcopy(saved)
        self.put('guest:radiohealth', saved)
        with patch.object(radio, 'open_stream', side_effect=AssertionError('read must not probe')):
            cleaned = radio_state.clear_dead_radio(state)
            self.assertIsNone(cleaned['radio_station'])
            self.assertIsNone(cleaned['radio_pos'])
            self.assertEqual(cleaned['last_env'], {'radio': None, 'weather': 'keep'})
            output = history.enrich_history({'footprints': items}, saved)
            self.assertTrue(all('stream_url' not in item for item in output['footprints']))
            with self.assertRaises(radio.RadioError):
                radio.authorize('guest:radiohealth', JORDAN['stream_url'] + '#fragment')
        self.assertEqual(saved, before)
        self.assertEqual(storage.read('guest:radiohealth'), before)

    def test_only_200_audio_passes_and_failures_hide_cached_links(self):
        saved = archive()
        saved['files']['footprints.json'] = {'items': [{'stream_url': LIVE['stream_url']}]}
        self.put('guest:radiohealth', saved)
        for status, mime, good in [(200, 'audio/mpeg', True), (200, 'audio/aac; charset=binary', True),
                                   (200, 'text/html', False), (404, 'text/html', False),
                                   (404, 'audio/mpeg', False), (200, 'application/octet-stream', False)]:
            with self.subTest(status=status, mime=mime):
                response = Mock(status=status)
                response.getheader.side_effect = lambda key, default=None: {'Content-Type': mime}.get(key, default)
                connection = Mock()
                connection.getresponse.return_value = response
                with patch.object(radio, 'PinnedConnection', return_value=connection):
                    if good:
                        conn, stream, _ = radio.open_stream(LIVE['stream_url'])
                        stream.close(); conn.close()
                        self.assertEqual(radio.authorize('guest:radiohealth', LIVE['stream_url']), LIVE['stream_url'])
                        self.assertEqual(history.enrich_history({'footprints': saved['files']['footprints.json']['items']}, saved)['footprints'][0]['stream_url'], LIVE['stream_url'])
                    else:
                        with self.assertRaises(radio.RadioError):
                            radio.open_stream(LIVE['stream_url'])
                        connection.close.assert_called_once()
                        response.close.assert_called_once()
                        with self.assertRaises(radio.RadioError):
                            radio.authorize('guest:radiohealth', LIVE['stream_url'])
                        self.assertNotIn('stream_url', history.enrich_history({'footprints': saved['files']['footprints.json']['items']}, saved)['footprints'][0])

    def test_cache_expiry_and_size_are_bounded(self):
        with patch.object(radio_state.time, 'monotonic', return_value=0):
            radio_state.remember_health(LIVE['stream_url'], False)
        with patch.object(radio_state.time, 'monotonic', return_value=301):
            self.assertIsNone(radio_state.stream_health(LIVE['stream_url']))
        for i in range(300):
            radio_state.remember_health(f'https://live.example.test/{i}', True)
        self.assertEqual(len(radio_state.HEALTH), radio_state.MAX_HEALTH)

    def test_jordan_html_is_rejected_by_existing_safe_stream_opener(self):
        for status in (200, 404):
            response = Mock(status=status)
            response.getheader.side_effect = lambda key, default=None: {'Content-Type': 'text/html'}.get(key, default)
            connection = Mock()
            connection.getresponse.return_value = response
            with patch.object(radio, 'PinnedConnection', return_value=connection), self.assertRaisesRegex(radio.RadioError, '未返回音频流'):
                radio.open_stream(JORDAN['stream_url'])
            response.close.assert_called_once()
            connection.close.assert_called_once()

    def test_redirects_keep_using_pinned_validator_and_close_connections(self):
        redirect = Mock(status=302)
        redirect.getheader.return_value = 'http://127.0.0.1/private'
        connection = Mock()
        connection.getresponse.return_value = redirect
        # First hop has already passed the pinning layer; the second is denied
        # by that same layer before a successful audio response is available.
        blocked = Mock()
        blocked.request.side_effect = radio.RadioError('电台地址不可用', 403)
        with patch.object(radio, 'PinnedConnection', side_effect=[connection, blocked]) as dial:
            with self.assertRaises(radio.RadioError):
                radio.open_stream(LIVE['stream_url'])
        self.assertEqual(dial.call_args_list[1].args[0].hostname, '127.0.0.1')
        redirect.close.assert_called_once()
        connection.close.assert_called_once()
        blocked.close.assert_called_once()

    def test_worker_probes_once_filters_failed_fallback_and_clears_sticky(self):
        async def check(good):
            radio_state.HEALTH.clear()
            stations = [JORDAN, LIVE, {**LIVE, 'country': 'FR', 'dead': True}]
            upstream = SimpleNamespace(_load_fallback=lambda: stations)
            async def nearest(*args, **kwargs):
                return next((s for s in upstream._load_fallback() if s['country'] == 'JO'), None)
            upstream.nearest = nearest
            data = {'radio_station': LIVE, 'radio_pos': [31, 35], 'last_env': {'radio': LIVE, 'weather': 'keep'}}
            state = SimpleNamespace(**data)
            state.to_dict = lambda: {key: getattr(state, key) for key in data}
            async def get(*args):
                return state.radio_station or await upstream.nearest(*args)
            server = SimpleNamespace(radio=upstream, _state=state, _get_radio=get)
            radio_health.install(server)
            with patch.object(radio_health, 'probe', new=AsyncMock(return_value=good)) as probe:
                for _ in range(2):
                    self.assertEqual(await server._get_radio(31, 35), LIVE if good else None)
                probe.assert_awaited_once_with(LIVE['stream_url'])
                if not good:
                    self.assertIsNone(state.radio_station)
                    self.assertIsNone(state.last_env['radio'])
                    self.assertEqual(upstream._load_fallback(), [])
                self.assertEqual(state.last_env['weather'], 'keep')
        asyncio.run(check(True))
        asyncio.run(check(False))

    def test_probe_timeout_kills_and_reaps_child(self):
        async def check():
            child = Mock(returncode=None)
            child.wait = AsyncMock(return_value=0)
            async def timeout(wait, seconds):
                wait.close()
                self.assertEqual(seconds, radio_health.PROBE_TIMEOUT)
                raise asyncio.TimeoutError
            with patch.object(asyncio, 'create_subprocess_exec', new=AsyncMock(return_value=child)), patch.object(asyncio, 'wait_for', side_effect=timeout):
                self.assertFalse(await radio_health.probe(LIVE['stream_url']))
            child.kill.assert_called_once()
            child.wait.assert_awaited_once()
        asyncio.run(check())

    def test_rendered_href_uses_proxy_even_without_click_interception(self):
        page = web.render_page().decode()
        self.assertIn('href="${escapeHtml(window.nowhereRadioUrl(streamUrl))}"', page)
        self.assertNotIn('href="${escapeHtml(streamUrl)}"', page)
