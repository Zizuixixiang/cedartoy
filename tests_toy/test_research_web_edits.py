"""Web-only answer revisions, isolated SQLite and real identity/HTTP routing."""
import io
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import research_scales
import server
from scale_test_engine import JsonRpcError


class ResearchWebEditsTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        folder = Path(stack.enter_context(tempfile.TemporaryDirectory(prefix='cedar-web-edits-')))
        stack.enter_context(patch.object(research_scales, 'DB_PATH', str(folder / 'sessions.db')))
        stack.enter_context(patch.object(server, 'SESSIONS_DB_PATH', folder / 'sessions.db'))
        stack.enter_context(patch.object(server, 'TURTLE_DB_PATH', folder / 'accounts.db'))

    def start(self, game='rbi', player='guest:webedits'):
        return research_scales.SCALES[game].execute('start', {'player_id': player, 'mode': 'full'}, web=True)

    def payload(self, state, index, value, **extra):
        return {'player_id': state['player_id'], 'session_id': state['session_id'],
                'expected_revision': state['answer_revision'], 'index': index, 'answer': value, **extra}

    def save(self, state, index, value, **extra):
        return research_scales.SCALES[state['game']].execute('save_answer', self.payload(state, index, value, **extra), web=True)

    def test_all_18_edit_without_truncation_defer_report_and_retry_finish(self):
        for game, engine in research_scales.SCALES.items():
            with self.subTest(game=game):
                state = self.start(game)
                values = [engine.answer_min] * len(engine.items)
                for index in range(3):
                    state = self.save(state, index, values[index])
                state = self.save(state, 0, engine.answer_max)
                values[0] = engine.answer_max
                self.assertEqual(state['saved_answers'], values[:3])
                resumed = engine.execute('status', {'player_id': state['player_id']}, web=True)
                self.assertEqual(resumed['saved_answers'], values[:3])
                for index in range(3, len(values)):
                    state = self.save(state, index, values[index])
                self.assertFalse(state['complete'], 'selecting last answer does not make a report')
                state = self.save(state, 1, engine.answer_max)
                values[1] = engine.answer_max
                final_request = self.payload(state, len(values)-1, values[-1], finish=True)
                result = engine.execute('save_answer', final_request, web=True)
                self.assertTrue(result['complete'])
                self.assertEqual(result['result_data']['scores'], engine.score(values, state['session_id']))
                again = engine.execute('save_answer', final_request, web=True)
                self.assertTrue(again['duplicate'])
                self.assertEqual(again['result_data'], result['result_data'])
                with self.assertRaises(JsonRpcError):
                    self.save(state, 0, engine.answer_min)
                self.assertEqual(engine.execute('status', {'player_id': state['player_id']}, web=True)['result_data'], result['result_data'])

    def test_conflicts_lost_response_retries_and_concurrent_writes(self):
        state = self.start()
        request = self.payload(state, 0, 1)
        engine = research_scales.SCALES['rbi']
        state = engine.execute('save_answer', request, web=True)
        self.assertTrue(engine.execute('save_answer', request, web=True)['duplicate'])
        old = state
        state = self.save(state, 0, 2)
        with self.assertRaises(JsonRpcError):
            self.save(old, 0, 3)
        # The later value must also survive a late retry of the first request.
        with self.assertRaises(JsonRpcError):
            engine.execute('save_answer', request, web=True)
        def write(value):
            try:
                return self.save(state, 0, value)['saved_answers'][0]
            except JsonRpcError:
                return 'conflict'
        with ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(write, [3, 4]))
        self.assertEqual(outcomes.count('conflict'), 1)
        current = engine.execute('status', {'player_id': state['player_id']}, web=True)
        self.assertEqual(current['saved_answers'][0], next(v for v in outcomes if v != 'conflict'))
        for index in range(1, len(engine.items)):
            current = self.save(current, index, 1)
        stale = current
        current = self.save(current, 0, 5)
        with self.assertRaises(JsonRpcError):
            self.save(stale, len(engine.items)-1, 1, finish=True)
        self.assertFalse(current['complete'])

    def test_protocol_validation_and_mcp_unchanged(self):
        state = self.start()
        engine = research_scales.SCALES['rbi']
        for extra in ({'index': True}, {'index': 2}, {'answer': True}, {'answer': 6},
                      {'expected_revision': ''}, {'finish': 'true'}, {'finish': True}, {'session_id': 'old'}):
            args = {**self.payload(state, 0, 1), **extra}
            with self.assertRaises(JsonRpcError):
                engine.execute('save_answer', args, web=True)
        with self.assertRaises(JsonRpcError):
            engine.execute('save_answer', self.payload(state, 0, 1))
        state = engine.execute('answer', {'player_id': state['player_id'], 'session_id': state['session_id'], 'expected_index': 0, 'answer': 1})
        self.assertNotIn('answer_revision', state)
        self.assertNotIn('saved_answers', state)
        with self.assertRaises(JsonRpcError):
            engine.execute('answer', {'player_id': state['player_id'], 'session_id': state['session_id'], 'expected_index': 0, 'answer': 2})
        self.assertTrue(engine.execute('answer', {'player_id': state['player_id'], 'session_id': state['session_id'], 'expected_index': 0, 'answer': 1})['duplicate'])
        other = self.start(player='guest:webother')
        with self.assertRaises(JsonRpcError):
            engine.execute('save_answer', {**self.payload(state | {'answer_revision': '0'*64}, 0, 1), 'player_id': other['player_id']}, web=True)

    def http(self, game, action, body):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = f'/api/{game}/{action}'
        handler.client_address = ('127.0.0.1', 23456)
        raw = json.dumps(body).encode()
        handler.headers = {'Content-Length': str(len(raw)), 'Authorization': 'Bearer fixture'}
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        handler.do_POST()
        return handler.send_response.call_args.args[0], json.loads(handler.wfile.getvalue())

    def test_http_identity_spoof_ai_and_cross_session_rejected(self):
        for game in ('rbi', 'aidep'):
            with patch.object(server, '_current_account', return_value={'id': 42, 'is_ai': False}):
                status, own = self.http(game, 'start', {'mode': 'full', 'player_id': '43'})
                self.assertEqual(status, 200)
                self.assertEqual(own['player_id'], '42')
                status, saved = self.http(game, 'save_answer', {**self.payload(own, 0, 1), 'player_id': '43'})
                self.assertEqual(status, 200)
                self.assertEqual(saved['player_id'], '42')
            with patch.object(server, '_current_account', return_value={'id': 43, 'is_ai': False}):
                _, other = self.http(game, 'start', {'mode': 'full'})
                status, _ = self.http(game, 'save_answer', self.payload(saved, 0, 2))
                self.assertNotEqual(status, 200)
                status, unchanged = self.http(game, 'status', {})
                self.assertEqual(unchanged['saved_answers'], [])
            with patch.object(server, '_current_account', return_value={'id': 42, 'is_ai': True}):
                self.assertEqual(self.http(game, 'save_answer', self.payload(saved, 0, 2))[0], 403)
            with patch.object(server, '_current_account', return_value={'id': 42, 'is_ai': False}):
                _, new = self.http(game, 'start', {'mode': 'full', 'restart': True, 'confirm': True, 'session_id': saved['session_id']})
                self.assertNotEqual(self.http(game, 'save_answer', self.payload(saved, 0, 2))[0], 200)
                self.assertNotEqual(new['session_id'], saved['session_id'])
