"""Reports from saved scores, with actual temp SQLite / HTTP / MCP paths."""
import copy
import io
import json
import math
import itertools
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import Mock, patch

from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
import server
import research_scales
from research_scales.engine import ResearchScaleEngine
from research_scales.interpretation import build_interpretation, format_interpretation, SUPPORTED_GAMES
from research_scales.interpretation_content import SINGLES
from tests_toy.test_aidep_sharing import seed_accounts


def scenario_answers(engine, case, sid=None):
    """Generate keyed positions; integer mixtures give legal half-step means."""
    lo, hi = engine.answer_min, engine.answer_max
    keys = list(engine.spec['dimensions'])
    keyed = {}
    for d, key in enumerate(keys):
        items = [q for q in engine.items if q['dimension'] == key]
        fraction = {'low': 0, 'quarter': .25, 'middle': .5, 'three_quarters': .75,
                    'high': 1, 'mixed_a': d % 2, 'mixed_b': 1 - d % 2,
                    'raw_low': 0, 'raw_high': 1}[case]
        target = lo + (hi - lo) * fraction
        lower = math.floor(target)
        extra = round((target - lower) * len(items))
        for i, item in enumerate(items):
            value = lower + (i < extra)
            keyed[item['id']] = lo + hi - value if item['reverse'] else value
    if case.startswith('raw_'):
        return [lo if case == 'raw_low' else hi] * len(engine.items)
    return [keyed[q['id']] for q in engine.ordered_items(sid)]


CASES = ('low', 'quarter', 'middle', 'three_quarters', 'high', 'mixed_a', 'mixed_b', 'raw_low', 'raw_high')


def independent_score(engine, answers, sid):
    groups = {k: [] for k in engine.spec['dimensions']}
    for q, value in zip(engine.ordered_items(sid), answers):
        groups[q['dimension']].append(engine.answer_min + engine.answer_max - value if q['reverse'] else value)
    result = {k: sum(v) / (len(v) if engine.spec['aggregation'] == 'mean' else 1) for k, v in groups.items()}
    if engine.spec.get('overall_mean'):
        result['overall'] = sum(map(sum, groups.values())) / len(answers)
    if engine.game == 'bsr':
        result['difference'] = result['motivational'] - result['pseudo_profound']
    return result


class InterpretationTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.temp = Path(self.stack.enter_context(tempfile.TemporaryDirectory(prefix='cedar-report-')))
        self.db = self.temp / 'sessions.db'
        self.stack.enter_context(patch.object(research_scales, 'DB_PATH', str(self.db)))
        self.stack.enter_context(patch.object(server, 'SESSIONS_DB_PATH', self.db))
        self.stack.enter_context(patch.object(server, 'TURTLE_DB_PATH', self.temp / 'accounts.db'))

    def request(self, path, body=None, token=''):
        handler = object.__new__(server.CedarToyHandler)
        handler.path, handler.client_address = path, ('127.0.0.1', 23456)
        raw = json.dumps(body).encode() if body is not None else b''
        handler.headers = {'Content-Length': str(len(raw))}
        if token:
            handler.headers['Authorization'] = 'Bearer ' + token
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        (handler.do_GET if body is None else handler.do_POST)()
        self.assertEqual(handler.send_response.call_args.args[0], 200)
        return json.loads(handler.wfile.getvalue())

    def test_all_18_nine_patterns_real_save_restore_and_no_rescore(self):
        self.assertEqual(SUPPORTED_GAMES, set(research_scales.SCALES))
        for game in sorted(SUPPORTED_GAMES):
            e = research_scales.SCALES[game]
            summaries = set()
            for case in CASES:
                with self.subTest(game=game, case=case):
                    player = 'guest:' + case.replace('_', '')
                    state = e.execute('start', {'player_id': player, 'mode': 'full_fast'})
                    self.assertNotIn('result_data', state)
                    answers = scenario_answers(e, case, state['session_id'])
                    result = e.execute('answer_batch', {'player_id': player, 'session_id': state['session_id'], 'answers': answers})
                    data = result['result_data']
                    self.assertEqual(data['scores'], independent_score(e, answers, state['session_id']))
                    self.assertEqual(set(data['score_ranges']), set(data['scores']))
                    for metric, score in data['scores'].items():
                        bounds = data['score_ranges'][metric]
                        self.assertLessEqual(bounds['min'], score)
                        self.assertLessEqual(score, bounds['max'])
                        self.assertIn(bounds['kind'], ('mean', 'sum', 'difference'))
                        if bounds['kind'] == 'difference':
                            self.assertIn('范围', result['result'])
                        else:
                            self.assertIn(f'/ {bounds["max"]:g}', result['result'])
                    report = data['interpretation']
                    self.assertEqual(report['game'], game)
                    self.assertEqual([n['key'] for n in report['dimension_notes']],
                                     [] if game in SINGLES else list(e.spec['dimensions']))
                    self.assertIn(format_interpretation(report), result['result'])
                    self.assertTrue(report['recommendations'])
                    summaries.add(report['summary'])
                    for note in report['dimension_notes']:
                        self.assertLessEqual(note['range'][0], note['score'])
                        self.assertLessEqual(note['score'], note['range'][1])
                    with sqlite3.connect(self.db) as conn:
                        before = conn.execute('SELECT * FROM test_results WHERE game=? AND player_id=?', (game, player)).fetchone()
                    fresh = ResearchScaleEngine(e.spec, lambda: str(self.db))
                    with patch.object(fresh, 'score', side_effect=AssertionError('historical rescore')):
                        restored = fresh.execute('get_result', {'player_id': player})
                        self.assertEqual(restored['result_data'], data)
                        status = fresh.execute('status', {'player_id': player})
                        self.assertNotIn('interpretation', status['result_data'])
                        self.assertNotIn('【综合观察】', status['result'])
                        self.assertEqual(fresh.execute('status', {'player_id': player}, web=True)['result_data'], data)
                    with sqlite3.connect(self.db) as conn:
                        after = conn.execute('SELECT * FROM test_results WHERE game=? AND player_id=?', (game, player)).fetchone()
                    self.assertEqual(before, after)
                    self.assertNotIn('interpretation', json.loads(before[3]))
            self.assertGreaterEqual(len(summaries), 5, game)

    def test_every_human_http_completed_report_and_mcp_real_tokens(self):
        tokens = seed_accounts()
        # No network reminders/migrations. Token auth, routing and engine are real.
        for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server, name))
        for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        for name in ('_duel_unread_request_reminder', '_mcp_forced_announcement'):
            self.stack.enter_context(patch.object(server, name, return_value=''))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
        self.stack.enter_context(patch.object(server, '_check_request_rate_limit', return_value=True))
        def mcp(game, action, params, path, token):
            response = self.request(path, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                'name': 'play', 'arguments': {'game': game, 'action': action, 'params': params}}}, token)
            result = response['result']
            self.assertFalse(result.get('isError'), result)
            return json.loads(result['content'][0]['text'])
        for game, e in research_scales.SCALES.items():
            with self.subTest(game=game):
                s = self.request('/api/' + game + '/start', {'mode': 'full_fast'}, tokens[1])
                human = self.request('/api/' + game + '/answer_batch', {
                    'session_id': s['session_id'], 'answers': scenario_answers(e, 'mixed_a', s['session_id'])}, tokens[1])
                self.assertIn('interpretation', human['result_data'])
                self.assertEqual(human['result_data'], self.request('/api/' + game + '/result', token=tokens[1])['result_data'])
                if game == 'aidep':
                    continue
                for path, token, slot in (('/' + tokens[11], '', 1), ('/mcp', tokens[11], 2)):
                    s = mcp(game, 'start', {'mode': 'full_fast', 'slot': slot}, path, token)
                    ai = mcp(game, 'answer_batch', {'slot': slot, 'session_id': s['session_id'],
                        'answers': scenario_answers(e, 'mixed_a', s['session_id'])}, path, token)
                    self.assertEqual(ai['result_data'], human['result_data'])
                    self.assertEqual(mcp(game, 'get_result', {'slot': slot}, path, token)['result_data'], ai['result_data'])
                    self.assertNotIn('interpretation', mcp(game, 'status', {'slot': slot}, path, token)['result_data'])
                    self.assertIn('【综合观察】', ai['result'])

    def test_report_is_pure_and_invalid_scores_do_not_invent_findings(self):
        for game in SUPPORTED_GAMES:
            e = research_scales.SCALES[game]
            scores = e.score(scenario_answers(e, 'middle'))
            original = copy.deepcopy(scores)
            report = build_interpretation(e.spec, scores)
            report['recommendations'].append('caller modification')
            self.assertNotIn('caller modification', build_interpretation(e.spec, scores)['recommendations'])
            self.assertEqual(scores, original)
            key = next(iter(e.spec['dimensions']))
            for invalid in (None, True, '3', float('inf'), float('nan'), -999, 999):
                self.assertIsNone(build_interpretation(e.spec, {**scores, key: invalid}))
            self.assertIsNone(build_interpretation(e.spec, {}))
        e = research_scales.SCALES['rbi']
        self.assertIsNone(build_interpretation({**e.spec, 'game': 'unsupported'}, e.score([3]*18)))

    def test_small_differences_ties_and_opposite_directions(self):
        for game in ('ppts', 'ross', 'rnss', 'rss', 'evs', 'aidep', 'bsr', 'rbi'):
            e = research_scales.SCALES[game]
            scores = e.score(scenario_answers(e, 'middle'))
            keys = list(e.spec['dimensions'])
            scores[keys[0]] += .01
            report = build_interpretation(e.spec, scores)
            self.assertNotIn('排在较前', report['summary'])
            self.assertNotIn('排在较前', report['summary'])
        e = research_scales.SCALES['ppts']
        self.assertIn('可以同时成立', build_interpretation(e.spec, {'procrastination': 5, 'precrastination': 5})['summary'])
        e = research_scales.SCALES['irrc']
        report = build_interpretation(e.spec, dict.fromkeys(e.spec['dimensions'], 5))
        self.assertIn('同时靠近', report['summary'])
        self.assertIn('困难体验', report['summary'])
        self.assertNotIn('overall', [n['key'] for n in report['dimension_notes']])
        e = research_scales.SCALES['rss']
        report = build_interpretation(e.spec, e.score([7]*12))
        self.assertIn('不是技能差', report['dimension_notes'][2]['text'])
        e = research_scales.SCALES['aidep']
        report = build_interpretation(e.spec, {'emotional': 1, 'functional': 1, 'cognitive': 1, 'control': 5})
        self.assertIn('不论委托了多少任务', report['summary'])
        report = build_interpretation(e.spec, {'emotional': 5, 'functional': 1, 'cognitive': 1, 'control': 1})
        self.assertIn('难以停下并不是同一回事', report['summary'])

    def test_single_sum_does_not_claim_which_items_were_endorsed(self):
        e = research_scales.SCALES['mesx']
        a, b = [0, 3]*5, [1, 2]*5
        self.assertEqual(build_interpretation(e.spec, e.score(a)), build_interpretation(e.spec, e.score(b)))
        self.assertIn('无法恢复', build_interpretation(e.spec, e.score(a))['boundary'])

    def test_existing_17_reports_match_reviewed_samples_exactly(self):
        samples = json.loads((Path(__file__).resolve().parents[1] /
            'docs/research-scales-report-samples.json').read_text())['samples']
        count = 0
        for game, rows in samples.items():
            if game == 'rbi':
                continue
            for row in rows:
                self.assertEqual(build_interpretation(research_scales.SCALES[game].spec, row['scores']),
                                 row['interpretation'], game)
                count += 1
        self.assertEqual(count, 85)

    def test_rbi_orders_ties_positions_and_requested_profile(self):
        e = research_scales.SCALES['rbi']
        keys = ('sai', 'fod', 'openness')
        reports = [build_interpretation(e.spec, dict.fromkeys(keys, n)) for n in (1, 3, 5)]
        self.assertEqual(len({r['summary'] for r in reports}), 3)
        for i in range(3):
            self.assertEqual(len({r['dimension_notes'][i]['text'] for r in reports}), 3)
        for report in reports:
            self.assertEqual(set(report), {'version', 'game', 'summary', 'dimension_notes',
                'recommendations', 'source_note', 'source_url', 'boundary'})
            self.assertEqual([n['key'] for n in report['dimension_notes']], list(keys))
            self.assertTrue(all(n['range'] == [1, 5] for n in report['dimension_notes']))
            self.assertIn('三方面', report['summary'])
            self.assertIn('非作者官方分数', report['boundary'])
            self.assertNotIn('排在较前', report['summary'])
        permutations = [build_interpretation(e.spec, dict(zip(keys, values)))
                        for values in itertools.permutations((1, 3, 5))]
        self.assertEqual(len({r['summary'] for r in permutations}), 6)
        for tied_keys in itertools.combinations(keys, 2):
            for tied, other in ((1, 5), (5, 1)):
                scores = {k: tied if k in tied_keys else other for k in keys}
                summary = build_interpretation(e.spec, scores)['summary']
                labels = [e.spec['dimensions'][k].replace('（描述均分）', '') for k in tied_keys]
                self.assertIn('、'.join(labels), summary)
        near = build_interpretation(e.spec, dict(zip(keys, (3, 3.01, 3.02))))
        self.assertNotIn('高于', near['summary'])
        self.assertNotIn('排在较前', near['summary'])
        requested = build_interpretation(e.spec, dict(zip(keys, (2.86, 3.57, 4.5))))
        self.assertIn('能沟通的需求是否转化为双方实际感受到的关心与回应', requested['summary'])
        self.assertNotIn('履行伴侣责任的位置高于陪伴', requested['summary'])  # 0.71 is a small gap.
        self.assertIn('【综合观察】', format_interpretation(requested))

    def test_rbi_preexisting_scores_http_mcp_and_subprocess_never_rescore(self):
        tokens = seed_accounts()
        e = research_scales.SCALES['rbi']
        saved_scores = {'sai': 2.86, 'fod': 3.57, 'openness': 4.5}
        # Pre-extension receipt format has scores and private answers, no prose.
        for player in ('1', '11'):
            start = e.execute('start', {'player_id': player, 'mode': 'full_fast'})
            e.execute('answer_batch', {'player_id': player, 'session_id': start['session_id'], 'answers': [3]*18})
            with sqlite3.connect(self.db) as conn:
                raw = conn.execute("SELECT result_detail FROM test_results WHERE player_id=? AND game='rbi'", (player,)).fetchone()[0]
                detail = json.loads(raw)
                self.assertNotIn('interpretation', detail)
                detail['scores'] = saved_scores  # Deliberately differs from answers: must use saved scores.
                conn.execute("UPDATE test_results SET result_detail=? WHERE player_id=? AND game='rbi'",
                             (json.dumps(detail), player))
        with sqlite3.connect(self.db) as conn:
            before = conn.execute('SELECT * FROM test_results ORDER BY player_id').fetchall()
        for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server, name))
        for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        for name in ('_duel_unread_request_reminder', '_mcp_forced_announcement'):
            self.stack.enter_context(patch.object(server, name, return_value=''))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
        self.stack.enter_context(patch.object(server, '_check_request_rate_limit', return_value=True))
        expected = build_interpretation(e.spec, saved_scores)
        with patch.object(e, 'score', side_effect=AssertionError('historical rescore')):
            for action in ('get_result', 'result', 'start'):
                human = self.request('/api/rbi/' + action, {}, tokens[1])
                self.assertEqual(human['result_data']['scores'], saved_scores)
                self.assertEqual(human['result_data']['interpretation'], expected)
                for path, token in (('/mcp', tokens[11]), ('/' + tokens[11], '')):
                    rpc = self.request(path, {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                        'name': 'play', 'arguments': {'game': 'rbi', 'action': action, 'params': {}}}}, token)
                    self.assertFalse(rpc['result'].get('isError'), rpc)
                    state = json.loads(rpc['result']['content'][0]['text'])
                    self.assertEqual(state['result_data'], human['result_data'])
                    self.assertIn(format_interpretation(expected), state['result'])
        code = '''
import json,sys
from research_scales import SCALES
from research_scales.engine import ResearchScaleEngine
e=ResearchScaleEngine(SCALES['rbi'].spec, lambda: sys.argv[1])
def forbidden(*args): raise AssertionError('historical rescore')
e.score=forbidden
print(json.dumps(e.execute('get_result', {'player_id':'11'})['result_data']))
'''
        state = json.loads(subprocess.check_output([sys.executable, '-c', code, str(self.db)],
                           cwd=Path(__file__).resolve().parents[1], text=True, timeout=30))
        self.assertEqual(state['interpretation'], expected)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT * FROM test_results ORDER BY player_id').fetchall(), before)


if __name__ == '__main__':
    unittest.main()
