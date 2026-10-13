"""Research-scale lifecycle, privacy, scoring and integration regressions."""
import json
import io
import math
import sqlite3
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch, Mock

import research_scales
from scale_test_engine import JsonRpcError
import server


class ResearchScalesTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        temp = self.stack.enter_context(tempfile.TemporaryDirectory())
        self.db = str(Path(temp) / 'sessions.db')
        self.stack.enter_context(patch.object(research_scales, 'DB_PATH', self.db))
        self.stack.enter_context(patch.object(server, 'SESSIONS_DB_PATH', Path(self.db)))
        self.stack.enter_context(patch.object(server, 'TURTLE_DB_PATH', Path(temp) / 'accounts.db'))

    def test_catalog_sources_and_no_placeholders(self):
        self.assertEqual(len(research_scales.SCALES), 18)
        self.assertEqual(sum(len(e.items) for e in research_scales.SCALES.values()), 265)
        self.assertEqual(len(research_scales.MCP_GAMES), 17)
        listing = server._tool_list_games()
        for game, engine in research_scales.SCALES.items():
            self.assertIn(game, server.HUMAN_TEST_GAMES)
            for key in ('author', 'source', 'license', 'notice', 'instruction'):
                self.assertTrue(engine.spec[key])
            if game == 'aidep':
                self.assertNotIn(game, listing)
                with self.assertRaises(server._McpError):
                    server._tool_get_guide({'game': game})
            else:
                self.assertIn(game, listing)
                self.assertIn('full_fast', server._tool_get_guide({'game': game}))
            self.assertIn('未验证翻译', engine.notice)
        for game in ('romantic_motivations',):
            self.assertNotIn(game, research_scales.SCALES)
            self.assertNotIn(game, listing)
            self.assertEqual(self.request('/'+game)[0], 404)
            self.assertEqual(self.request('/api/'+game+'/start', {'player_id':'guest:webblocked','mode':'full'})[0], 404)
            with self.assertRaises(server._McpError):
                server._tool_get_guide({'game': game})

    def test_every_scale_both_modes_identical_and_private(self):
        for game, engine in research_scales.SCALES.items():
            with self.subTest(game=game):
                values = list(range(engine.answer_min, engine.answer_max + 1))
                answer_by_text = {q['text']: values[i % len(values)] for i, q in enumerate(engine.items)}
                answers = [answer_by_text[q['text']] for q in engine.items]
                player = 'guest:sequential'
                state = engine.execute('start', {'player_id': player, 'mode': 'full'})
                self.assertEqual(len(state['questions']), 1)
                sid = state['session_id']
                for i in range(len(answers)):
                    answer = answer_by_text[state['questions'][0]['text']]
                    state = engine.execute('answer', {'player_id': player, 'session_id': sid,
                        'expected_index': i, 'answer': answer})
                    resumed = engine.execute('status', {'player_id': player})
                    self.assertEqual(resumed['progress'], i + 1)
                    retry = engine.execute('answer', {'player_id': player, 'session_id': sid,
                        'expected_index': i, 'answer': answer})
                    self.assertTrue(retry['duplicate'])
                    self.assertEqual(retry['progress'], i + 1)
                self.assertTrue(state['complete'])
                batch = engine.execute('start', {'player_id': 'guest:batch', 'mode': 'full_fast'})
                self.assertEqual(len(batch['questions']), len(answers))
                args = {'player_id': 'guest:batch', 'session_id': batch['session_id'],
                    'answers': [answer_by_text[q['text']] for q in batch['questions']]}
                result = engine.execute('answer_batch', args)
                self.assertEqual(state['result_data'], result['result_data'])
                self.assertTrue(engine.execute('answer_batch', args)['duplicate'])
                bad = {**args, 'answers': [values[-1] if v != values[-1] else values[0] for v in answers]}
                with self.assertRaises(JsonRpcError):
                    engine.execute('answer_batch', bad)
                self.assertNotIn('answers', result)
                self.assertNotIn('questions', result)
                with self.assertRaises(JsonRpcError):
                    engine.execute('compare', {'player_id': player, 'player_id_b': 'guest:batch'})

    def test_invalid_input_conflicts_restart_and_slots(self):
        e = research_scales.SCALES['ipip_empathy']
        state = e.execute('start', {'player_id': '42:2', 'mode': 'full'})
        args = {'player_id': '42:2', 'session_id': state['session_id'], 'expected_index': 0}
        for value in (True, False, 0, 6, 1.5, None, {}, 'oops'):
            with self.assertRaises(JsonRpcError):
                e.execute('answer', {**args, 'answer': value})
        e.execute('answer', {**args, 'answer': 1})
        self.assertEqual(e.execute('start', {'player_id': '42:2', 'mode': 'full'})['progress'], 1)
        for bad in ({**args, 'answer': 2}, {**args, 'answer': 1, 'expected_index': 3},
                    {**args, 'answer': 1, 'session_id': 'stale'}):
            with self.assertRaises(JsonRpcError):
                e.execute('answer', bad)
        with self.assertRaises(JsonRpcError):
            e.execute('start', {'player_id': '42:2', 'mode': 'full_fast', 'restart': True})
        new = e.execute('start', {'player_id': '42:2', 'mode': 'full_fast', 'restart': True, 'confirm': True, 'session_id': state['session_id']})
        self.assertNotEqual(state['session_id'], new['session_id'])
        for answers in ([1], [1] * 11, [], '1,1', [True]*10, [6]*10):
            with self.assertRaises(JsonRpcError):
                e.execute('answer_batch', {'player_id': '42:2', 'session_id': new['session_id'], 'answers': answers})
        for player in ('42', '42:3', '43', 'guest:42'):
            with self.assertRaises(JsonRpcError):
                e.execute('status', {'player_id': player})
        with self.assertRaises(JsonRpcError):
            e.execute('answer', {**args, 'answer': 1})

    def test_numeric_session_id_answers_retries_and_explicit_restart(self):
        e = research_scales.SCALES['ipip_empathy']
        for created, convert in ((1800000000.1234567, float), (1800000000.0, int)):
            with self.subTest(convert=convert), patch('time.time', return_value=created), \
                    patch('secrets.randbelow', return_value=0):
                base = {'player_id': 'guest:numeric' + convert.__name__}
                state = e.execute('start', {**base, 'mode': 'full'})
                sid = state['session_id']
                numeric_sid = convert(float(sid))
                args = {**base, 'session_id': numeric_sid, 'expected_index': 0, 'answer': 1}
                state = e.execute('answer', args)
                self.assertEqual((state['session_id'], state['progress']), (sid, 1))
                self.assertTrue(e.execute('answer', args)['duplicate'])
                with self.assertRaises(JsonRpcError):
                    e.execute('answer', {**args, 'answer': 2})
                restart = {**base, 'mode': 'full_fast', 'restart': True, 'session_id': numeric_sid}
                with self.assertRaises(JsonRpcError):
                    e.execute('start', restart)
                # Change time deterministically so both old representations are stale.
                with patch('time.time', return_value=created + 1):
                    state = e.execute('start', {**restart, 'confirm': True})
                for old in (sid, numeric_sid):
                    with self.assertRaises(JsonRpcError):
                        e.execute('start', {**restart, 'confirm': True, 'session_id': old})
                    with self.assertRaises(JsonRpcError):
                        e.execute('answer_batch', {**base, 'session_id': old, 'answers': [1]*10})
                sid = state['session_id']
                args = {**base, 'session_id': convert(float(sid)), 'answers': [1]*10}
                result = e.execute('answer_batch', args)
                self.assertTrue(result['complete'])
                self.assertEqual(result['session_id'], sid)
                self.assertTrue(e.execute('answer_batch', args)['duplicate'])
                self.assertTrue(e.execute('answer_batch', {**args, 'session_id': sid})['duplicate'])
                with self.assertRaises(JsonRpcError):
                    e.execute('answer_batch', {**args, 'answers': [2]*10})
                restart['session_id'] = args['session_id']
                with self.assertRaises(JsonRpcError):
                    e.execute('start', restart)
                self.assertEqual(e.execute('get_result', base)['result_data'], result['result_data'])
                with patch('time.time', return_value=created + 2):
                    state = e.execute('start', {**restart, 'confirm': True})
                self.assertEqual(state['progress'], 0)
                self.assertNotEqual(state['session_id'], sid)

    def test_invalid_numeric_session_ids_do_not_write_or_restart(self):
        e = research_scales.SCALES['ipip_empathy']
        # Include bool-equal timestamps and the binary64 integer precision edge.
        for created in (0.0, 1.0, 1800000000.1234567, float(2**53)):
            with self.subTest(created=created), patch('time.time', return_value=created), \
                    patch('secrets.randbelow', return_value=0):
                base = {'player_id': 'guest:invalid' + str(int(created))}
                state = e.execute('start', {**base, 'mode': 'full'})
                sid = state['session_id']
                invalid = (None, True, False, [], {}, float('nan'), float('inf'), -float('inf'),
                           'NaN', 'Infinity', '', 'stale', sid + '1',
                           math.nextafter(created, -math.inf), math.nextafter(created, math.inf),
                           int(created) + 1, str(int(created) + 1))
                for value in invalid:
                    with self.subTest(value=value):
                        with self.assertRaises(JsonRpcError):
                            e.execute('answer', {**base, 'session_id': value, 'expected_index': 0, 'answer': 1})
                        with self.assertRaises(JsonRpcError):
                            e.execute('start', {**base, 'mode': 'full', 'restart': True,
                                               'confirm': True, 'session_id': value})
                        self.assertEqual(e.execute('status', base), state)

    def test_old_results_project_only_readable_title_without_rewriting_storage(self):
        from cedar_backend.display_titles import RESEARCH_TITLES
        for game, engine in research_scales.SCALES.items():
            for player in ('91', '91:3'):
                with self.subTest(game=game, player=player):
                    original_spec = json.dumps(engine.spec, ensure_ascii=False)
                    # Complete a pre-rename result using the old presentation label.
                    with patch.dict(RESEARCH_TITLES, {game: engine.title}):
                        start = engine.execute('start', {'player_id': player, 'mode': 'full_fast'})
                        old = engine.execute('answer_batch', {'player_id': player,
                            'session_id': start['session_id'], 'answers': [engine.answer_min] * len(engine.items)})
                    self.assertEqual(old['result'].splitlines()[0], f'【{engine.title}】')
                    with sqlite3.connect(self.db) as conn:
                        before = conn.execute('SELECT * FROM test_results WHERE game=? AND player_id=?', (game, player)).fetchone()
                    for action in ('start', 'status', 'result', 'get_result'):
                        new = engine.execute(action, {'player_id': player}, web=True)
                        self.assertEqual(new['result'].splitlines()[0], f'【{RESEARCH_TITLES[game]}】')
                        self.assertEqual(new['result'].partition('\n')[2], old['result'].partition('\n')[2])
                        self.assertEqual(new['result_data'], old['result_data'])
                    with sqlite3.connect(self.db) as conn:
                        after = conn.execute('SELECT * FROM test_results WHERE game=? AND player_id=?', (game, player)).fetchone()
                    self.assertEqual(after, before)
                    self.assertEqual(json.dumps(engine.spec, ensure_ascii=False), original_spec)
                    self.assertEqual(engine.title, engine.spec['title'])

    def test_scoring_against_official_keys_and_mesx_direction(self):
        # Expected reverse counts from the official IPIP keys, independently of JSON flags.
        for game, count, negative in [('ipip_adhd',12,0), ('ipip_empathy',10,5),
            ('ipip_sociability',10,5), ('ipip_self_monitoring',10,3), ('ipip_need_for_cognition',10,4)]:
            e = research_scales.SCALES[game]
            self.assertEqual(list(e.score([5]*count).values()), [5*(count-negative)+negative])
            self.assertEqual(list(e.score([1]*count).values()), [count-negative+5*negative])
        e = research_scales.SCALES['mesx']
        self.assertEqual(list(e.score([3]*10).values()), [30])
        self.assertEqual(list(e.score([0]*10).values()), [0])
        self.assertIn('内圈', e.spec['options'][3]['text'])
        self.assertEqual([q['id'] for q in research_scales.SCALES['rnss'].items if q['reverse']], [18])

    def test_bsr_study4_structure_and_score_direction(self):
        e = research_scales.SCALES['bsr']
        self.assertEqual(len(e.items), 20)
        self.assertEqual([q['dimension'] for q in e.items], ['pseudo_profound']*10 + ['motivational']*10)
        self.assertFalse(any(q['reverse'] for q in e.items))
        self.assertEqual([o['value'] for o in e.spec['options']], [1, 2, 3, 4, 5])
        self.assertEqual(len({q['text'] for q in e.items}), 20)
        self.assertTrue(all(q['source'] and q['source_item'] and q['reuse'] for q in e.items))
        self.assertEqual(e.items[0]['original'], 'Hidden meaning transforms unparalleled abstract beauty.')
        self.assertEqual(e.items[9]['original'], 'Today, science tells us that the essence of nature is joy.')
        self.assertTrue(all('公共领域' in q['reuse'] for q in e.items[10:]))
        for answers, expected in [([1]*10+[5]*10, (1,5,4)), ([5]*10+[1]*10, (5,1,-4)),
                                  ([3]*20, (3,3,0)), ([1,2,3,4,5]*2+[2,3]*5, (3,2.5,-0.5))]:
            self.assertEqual(tuple(e.score(answers).values()), expected)
        self.assertIn('4399 娱乐改编版', e.title)
        self.assertIn('Study 4', e.notice)
        self.assertIn('不是答对率', e.scoring_note)
        self.assertNotIn('overall', e.result_dimensions)

    def test_bsr_random_order_restore_and_mapped_scores(self):
        e = research_scales.SCALES['bsr']
        # Known canonical halves, independent of runtime dimension metadata.
        response = {q['text']: (1 if i < 10 else 5) for i, q in enumerate(e.items)}
        orders = []
        for mode in ('full', 'full_fast'):
            base = {'player_id': 'guest:bsr'+mode.replace('_','')}
            state = e.execute('start', {**base, 'mode': mode}, web=True)
            questions, sid = state['questions'], state['session_id']
            order = [q['text'] for q in questions]
            orders.append(order)
            self.assertCountEqual(order, response)
            self.assertEqual([q['number'] for q in questions], list(range(1,21)))
            self.assertTrue(all(set(q) == {'text','options','number'} for q in questions))
            self.assertEqual(e.execute('status', base, web=True)['questions'], questions)
            self.assertEqual(e.execute('start', {**base, 'mode': 'full'}, web=True)['questions'], questions)
            values = [response[text] for text in order]
            if mode == 'full':
                for i, value in enumerate(values):
                    current = e.execute('status', base)
                    self.assertEqual(current['questions'][0]['text'], order[i])
                    state = e.execute('answer', {**base, 'session_id':sid,'expected_index':i,'answer':value})
            else:
                args = {**base,'session_id':sid,'answers':values}
                state = e.execute('answer_batch', args)
                self.assertTrue(e.execute('answer_batch', args)['duplicate'])
            expected = {'pseudo_profound':1,'motivational':5,'difference':4}
            self.assertEqual(state['result_data']['scores'], expected)
            self.assertEqual(e.execute('get_result', base)['result_data'], state['result_data'])
            self.assertNotIn('answers', state)
            self.assertNotIn('questions', state)
            with self.assertRaises(JsonRpcError):
                e.execute('compare', {**base,'player_id_b':'guest:someone'})
            new = e.execute('start', {**base,'mode':mode,'restart':True,'confirm':True,'session_id':sid}, web=True)
            self.assertNotEqual(new['questions'], questions)
            with self.assertRaises(JsonRpcError):
                e.execute('answer_batch', {**base,'session_id':sid,'answers':values})
        self.assertNotEqual(*orders)

    def test_bsr_web_identity_privacy_and_batch_validation(self):
        e = research_scales.SCALES['bsr']
        with patch.object(server, '_current_account', return_value={'id':42,'is_ai':False,'username':'human'}):
            state = server._human_test_action('bsr','start','human-token',
                {'player_id':'43','mode':'full_fast'})
            self.assertEqual(state['player_id'], '42')
            for values in ([1]*19, [1]*21, [0]*20, [6]*20, [True]*20, [1.5]*20):
                with self.assertRaises(JsonRpcError):
                    server._human_test_action('bsr','answer_batch','human-token',
                        {'session_id':state['session_id'],'answers':values})
            self.assertEqual(e.execute('status', {'player_id':'42'})['progress'], 0)
            result = server._human_test_action('bsr','answer_batch','human-token',
                {'session_id':state['session_id'],'answers':[2]*20})
            self.assertEqual(result['result_data']['scores'], {'pseudo_profound':2,'motivational':2,'difference':0})
        with patch.object(server, '_current_account', return_value={'id':43,'is_ai':False,'username':'other'}):
            with self.assertRaises(JsonRpcError):
                server._human_test_action('bsr','result','other-token',{'player_id':'42'})
        for player in ('42:2','43','guest:42','guest:webbsr'):
            with self.assertRaises(JsonRpcError):
                e.execute('status', {'player_id':player})

    def test_seven_merged_scales_independent_scoring_fixtures(self):
        # Fixed expectations from the source audits, not derived from JSON dimension/reverse flags.
        fixtures = {
            'jrrs': (16, 1, 5, [], [47/16]),
            'ross': (17, 0, 10, [], [24/5, 4, 5, 6]),
            'rss': (12, 1, 7, [9, 10, 11, 12], [3, 4.5, 3.75]),
            'irrc': (35, 1, 5, [32], [25/8, 16/6, 11/3, 3, 2.5, 3, 2.5]),
            'rbi': (18, 1, 5, [], [22/7, 19/7, 3.5]),
            'ppts': (18, 1, 5, [], [3, 25/8]),
            'bsfl': (5, 0, 4, [], [2]),
        }
        for game, (count, low, high, reverse, expected) in fixtures.items():
            with self.subTest(game=game):
                e = research_scales.SCALES[game]
                self.assertEqual(len(e.items), count)
                self.assertEqual((e.answer_min, e.answer_max), (low, high))
                self.assertEqual([q['id'] for q in e.items if q['reverse']], reverse)
                self.assertFalse(e.spec.get('overall_mean'))
                answers = [low + (3*i+1) % (high-low+1) for i in range(count)]
                for actual, wanted in zip(e.score(answers).values(), expected, strict=True):
                    self.assertAlmostEqual(actual, wanted)
                guide = server._tool_get_guide({'game': game})
                self.assertIn('4399', guide)
                self.assertIn('描述性', guide)
                self.assertIn('非', guide)
                self.assertIn(e.spec['source'], guide)
                self.assertIn(e.spec['license'], guide)
                self.assertIn('未验证翻译', guide)
                self.assertIn('不具有人类量表效度', guide)
        bsfl = research_scales.SCALES['bsfl']
        self.assertEqual(list(bsfl.score([0]*5).values()), [0])
        self.assertEqual(list(bsfl.score([4]*5).values()), [4])

    def test_homepage_catalog_matches_registered_data(self):
        html = Path('index.html').read_text()
        start = html.index('const researchScales = ') + len('const researchScales = ')
        rows = json.JSONDecoder().raw_decode(html[start:])[0]
        self.assertEqual({row[0] for row in rows}, set(research_scales.SCALES))
        from cedar_backend.display_titles import RESEARCH_TITLES
        for game, title, count, author, human_only, descriptive in rows:
            e = research_scales.SCALES[game]
            self.assertEqual((title, count, author), (RESEARCH_TITLES[game], len(e.items), e.spec['author_short']))
            self.assertEqual(server.HUMAN_TEST_GAMES[game]['display_title'], title)
            self.assertEqual(server.HUMAN_TEST_GAMES[game]['title'], e.spec['title'])
            guide = research_scales.guide(game)
            self.assertTrue(guide.startswith(f'# {game} · {title}\n'))
            self.assertIn('原名：' + e.spec['title'], guide)
            if game in research_scales.MCP_GAMES:
                self.assertIn(f'{game}·{title}·', server._tool_list_games())
            self.assertEqual(human_only, game == 'aidep')
            self.assertEqual(descriptive, game in {'jrrs','ross','rss','irrc','rbi','ppts','bsfl'})

    def test_human_api_auth_modes_and_resume(self):
        previous_sid = None
        for mode in ('full', 'full_fast'):
            with patch.object(server, '_current_account', return_value={'id':42,'is_ai':False,'username':'human'}):
                state = server._human_test_action('aidep', 'start', 'human-token',
                    {'player_id':'guest:webspoof','mode':mode,'restart':True,'confirm':True,'session_id':previous_sid})
                previous_sid = state['session_id']
                self.assertEqual(state['player_id'], '42')
                self.assertEqual(state['mode'], mode)
                if mode == 'full':
                    state = server._human_test_action('aidep','answer','human-token',
                        {'session_id':state['session_id'],'expected_index':0,'answer':1})
                    self.assertEqual(server._human_test_action('aidep','result','human-token',{})['progress'],1)
                else:
                    state = server._human_test_action('aidep','answer_batch','human-token',
                        {'session_id':state['session_id'],'answers':[1]*22})
                    self.assertTrue(state['complete'])
                    self.assertIn('大学生', state['result'])
        for action in ('start','answer','answer_batch','result','status','get_result'):
            with patch.object(server, '_current_account', return_value={'id':99,'is_ai':True}):
                with self.assertRaises(server._McpError):
                    server._human_test_action('aidep',action,'ai-token',{'mode':'full','player_id':'guest:webspoof'})
        with self.assertRaises(server._McpError):
            server._human_test_action('aidep','start','',{'mode':'full','player_id':'42'})

    def test_web_saved_answers_are_owner_only_and_mcp_unchanged(self):
        e = research_scales.SCALES['rbi']
        with patch.object(server, '_current_account', return_value={'id':42,'is_ai':False}):
            state = server._human_test_action('rbi', 'start', 'human-token', {'mode':'full'})
            state = server._human_test_action('rbi', 'answer', 'human-token', {
                'session_id':state['session_id'], 'expected_index':0, 'answer':2})
            self.assertEqual(state['saved_answers'], [2])
            restored = server._human_test_action('rbi', 'result', 'human-token', {'player_id':'43'})
            self.assertEqual(restored['saved_answers'], [2])
            self.assertEqual(len(restored['questions']), 18)
        mcp = e.execute('status', {'player_id':'42'})
        self.assertNotIn('saved_answers', mcp)
        self.assertEqual(len(mcp['questions']), 1)
        with patch.object(server, '_current_account', return_value={'id':43,'is_ai':False}):
            with self.assertRaises(JsonRpcError):
                server._human_test_action('rbi', 'result', 'other-token', {'player_id':'42'})

    def test_root_identity_and_human_only_denial(self):
        # Only peripheral platform services mocked; real root identity/dispatch/scale persistence.
        for name in ('_auto_migrate_legacy_account_saves','_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server, name))
        self.stack.enter_context(patch.object(server, '_anti_addiction_context', return_value=None))
        self.stack.enter_context(patch.object(server, '_anti_addiction_preflight', return_value=None))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
        with patch.object(server, '_current_account', return_value={'id':91,'is_ai':True}):
            state = json.loads(server._tool_play({'game':'ipip_empathy','action':'start',
                'player_id':'42','params':{'player_id':'43','slot':3,'mode':'full'}}, path_token='token'))
        self.assertEqual(state['player_id'],'91:3')
        self.assertEqual(state['progress'],0)
        for token in ('token', None):
            with self.assertRaises(server._McpError):
                server._tool_play({'game':'aidep','action':'start','params':{'mode':'full'}}, path_token=token)
        with self.assertRaises(server._McpError):
            server._tool_play({'game':'ipip_empathy','action':'status','player_id':'guest:webspoof'})

    def request(self, path, body=None, bearer=""):
        handler = object.__new__(server.CedarToyHandler)
        handler.path = path
        handler.client_address = ("127.0.0.1", 23456)
        raw = json.dumps(body).encode() if body is not None else b""
        handler.headers = {"Content-Length": str(len(raw))}
        if bearer:
            handler.headers["Authorization"] = "Bearer " + bearer
        handler.rfile, handler.wfile = io.BytesIO(raw), io.BytesIO()
        handler.send_response, handler.send_header, handler.end_headers = Mock(), Mock(), Mock()
        if body is None:
            handler.do_GET()
        else:
            handler.do_POST()
        return handler.send_response.call_args.args[0], handler.wfile.getvalue()

    def test_http_web_routes_and_actual_ai_rejection(self):
        for game in research_scales.SCALES:
            status, html = self.request('/' + game)
            self.assertEqual(status, 200)
            self.assertIn(research_scales.SCALES[game].title.encode(), html)
            self.assertNotIn(b'__TEST_GAME_CONFIG__', html)
        status, body = self.request('/api/mesx/start', {'player_id':'guest:webhttp', 'mode':'full_fast'})
        self.assertEqual(status, 200)
        state = json.loads(body)
        status, body = self.request('/api/mesx/answer_batch', {'player_id':'guest:webhttp',
            'session_id':state['session_id'], 'answers':[0]*10})
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(body)['complete'])
        self.assertEqual(self.request('/api/mesx/result?player_id=guest:webhttp')[0], 200)
        with patch.object(server, '_current_account', return_value={'id':99,'is_ai':True}):
            for action in ('start','answer','answer_batch','result','status','get_result'):
                self.assertEqual(self.request('/api/aidep/'+action,
                    {'player_id':'guest:webhttp','mode':'full'}, 'ai-token')[0], 403)
        self.assertEqual(self.request('/api/aidep/compare', {'player_id':'guest:webhttp'})[0], 404)

    def test_new_scale_root_http_path_token_and_bearer(self):
        for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server, name))
        for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        for name in ('_duel_unread_request_reminder', '_mcp_forced_announcement'):
            self.stack.enter_context(patch.object(server, name, return_value=''))
        self.stack.enter_context(patch.object(server, '_authenticated_ai_player_id', return_value='91'))
        self.stack.enter_context(patch.object(server, '_request_rate_limit_identity', return_value='fixture'))
        self.stack.enter_context(patch.object(server, '_check_request_rate_limit', return_value=True))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
        def call(game, action, params, path, bearer):
            payload = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'play',
                'arguments':{'game':game,'action':action,'player_id':'42',
                    'params':{**params,'slot':2,'player_id':'43'}}}}
            status, raw = self.request(path, payload, bearer)
            self.assertEqual(status, 200)
            result = json.loads(raw)['result']
            self.assertFalse(result.get('isError'), result)
            state = json.loads(result['content'][0]['text'])
            self.assertEqual(state['player_id'], '91:2')
            return state

        with patch.object(server, '_current_account', return_value={'id':91,'is_ai':True}):
            for game in sorted(research_scales.MCP_GAMES):
                e = research_scales.SCALES[game]
                answer_by_text = {q['text']: (5 if game == 'bsr' and i >= 10 else e.answer_min)
                                  for i, q in enumerate(e.items)}
                for mode, path, bearer in (('full','/fixture-token',''), ('full_fast','/mcp','fixture-bearer')):
                    with self.subTest(game=game, mode=mode):
                        params = {'mode':mode}
                        if mode == 'full_fast':
                            params.update(restart=True, confirm=True, session_id=float(state['session_id']))
                        state = call(game, 'start', params, path, bearer)
                        sid = float(state['session_id'])
                        if mode == 'full':
                            for index in range(len(e.items)):
                                state = call(game, 'answer', {'session_id':sid,
                                    'expected_index':index, 'answer':answer_by_text[state['questions'][0]['text']]}, path, bearer)
                            sequential = state['result_data']
                        else:
                            state = call(game, 'answer_batch', {'session_id':sid,
                                'answers':[answer_by_text[q['text']] for q in state['questions']]}, path, bearer)
                            self.assertEqual(state['result_data'], sequential)
                        self.assertTrue(state['complete'])
                        restored = call(game, 'get_result', {}, path, bearer)
                        self.assertEqual(restored['result_data'], state['result_data'])
                        self.assertEqual(restored['result'].splitlines()[0], f'【{research_scales.RESEARCH_TITLES[game]}】')
                        self.assertEqual(state['result'].splitlines()[0], restored['result'].splitlines()[0])
            for path, bearer in (('/fixture-token',''), ('/mcp','fixture-bearer')):
                for action in ('start','answer','answer_batch','status','result','get_result'):
                    payload = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'play',
                        'arguments':{'game':'aidep','action':action,'params':{'mode':'full'}}}}
                    status, raw = self.request(path, payload, bearer)
                    self.assertEqual(status, 200)
                    self.assertTrue(json.loads(raw)['result']['isError'])

    def test_claimed_web_guest_cannot_recreate_or_read_archive(self):
        with patch.object(server, '_claimed_guest_record', return_value={'claimed_slot': 2}):
            for action in ('start', 'result', 'answer', 'answer_batch'):
                with self.assertRaises(server._McpError):
                    server._human_test_action('romlon', action, '', {'player_id':'guest:webclaimed','mode':'full'})
        self.assertFalse(Path(self.db).exists())

    def test_total_means_weight_items_not_subscale_means(self):
        e = research_scales.SCALES['aidep']
        answers = [5]*5 + [1]*17
        self.assertAlmostEqual(e.score(answers)['overall'], 42/22)
        e = research_scales.SCALES['rnss']
        self.assertAlmostEqual(e.score([1]*20)['overall'], 24/20)

    def test_existing_schema_extra_columns_and_guest_expiry(self):
        e = research_scales.SCALES['romlon']
        with sqlite3.connect(self.db) as conn:
            e._init_db(conn)
            for table in ('test_sessions','test_results'):
                conn.execute(f'ALTER TABLE {table} ADD COLUMN user_id INTEGER')
        for player in ('7','guest:expires'):
            state=e.execute('start', {'player_id':player,'mode':'full_fast'})
            e.execute('answer_batch',{'player_id':player,'session_id':state['session_id'],'answers':[1]*4})
        with sqlite3.connect(self.db) as conn:
            conn.execute('UPDATE test_results SET completed_at=0')
        self.assertTrue(e.execute('result', {'player_id':'7'})['complete'])
        with self.assertRaises(JsonRpcError):
            e.execute('result', {'player_id':'guest:expires'})


if __name__ == '__main__':
    unittest.main()
