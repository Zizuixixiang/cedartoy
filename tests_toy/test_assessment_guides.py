"""Exercise the 25 delivered guides against real routes in disposable databases.

Optional tiktoken is used only for offline volume verification, never by the app.
BDSM's live upstream is replaced by a three-question protocol fixture.
"""
import importlib
import json
import re
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs

install_missing_optional_game_stubs()
import server
import research_scales
from research_scales.guides import QUESTION_VOLUME

OLD = 'mbti enneagram dnd love ecr humanity sins_virtues bdsmtest'.split()


def examples(guide):
    return [(action, json.loads(params)) for action, params in
            re.findall(r'^- (\w+) (\{.*?\})(?=：)', guide, re.M)]


def question_text(game, mode):
    parts = []
    for question in importlib.import_module(game + '.questions').get_questions(mode):
        parts.append(question['text'])
        parts.extend([question['option_a'], question['option_b']] if game == 'mbti'
                     else [option['text'] for option in question['options']])
    return '\n'.join(parts)


class AssessmentGuideTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        folder = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        for name in ('SESSIONS_DB_PATH', 'TURTLE_DB_PATH'):
            self.stack.enter_context(patch.object(server, name, folder / (name + '.db')))
        self.stack.enter_context(patch.object(research_scales, 'DB_PATH', str(folder / 'research.db')))
        for game in OLD:
            handler = importlib.import_module(game + '.handler')
            self.stack.enter_context(patch.object(handler, 'DB_PATH', str(folder / (game + '.db'))))
            if hasattr(handler, 'ACCOUNT_DB_PATH'):
                self.stack.enter_context(patch.object(handler, 'ACCOUNT_DB_PATH', str(folder / 'accounts.db')))
        for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
            self.stack.enter_context(patch.object(server, name))
        for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
            self.stack.enter_context(patch.object(server, name, return_value=None))
        self.stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda value, **kw: value))

    def guide(self, game):
        return json.loads(server._tool_get_guide({'game': game}))['guide']

    def call(self, game, action, params, player='guest:guidecheck'):
        response = json.loads(server._tool_play({'game': game, 'action': action,
            'params': {**params, 'player_id': player}}))
        self.assertNotIn('error', response, response)
        result = response.get('result')
        if isinstance(result, dict):
            self.assertFalse(result.get('isError'), response)
        return response

    def test_all_guides_are_self_contained_and_preserve_modes(self):
        self.assertEqual(len(OLD) + len(research_scales.MCP_GAMES), 25)
        for game in OLD + sorted(research_scales.MCP_GAMES):
            with self.subTest(game=game):
                guide = self.guide(game)
                self.assertIn(f'play(game="{game}"', guide)
                self.assertNotIn('get_guide(', guide)
                self.assertIn('非实际调用总消耗' if game != 'bdsmtest' else '未测', guide)
                self.assertGreaterEqual(len(examples(guide)), 4)
                if game in OLD:
                    for mode in importlib.import_module(game + '.questions').VALID_MODES:
                        self.assertRegex(guide, r'(?<![A-Za-z_])' + mode + r'(?![A-Za-z_])')
                else:
                    engine = research_scales.SCALES[game]
                    for source in (engine.spec['author'], engine.spec['source'], engine.spec['license']):
                        self.assertIn(source, guide)
                    self.assertEqual(guide.count('不作诊断'), 1)
                    self.assertEqual(guide.count('不具有人类量表效度'), 1)
                    self.assertIn('未验证翻译', guide)

    def test_old_guide_examples_all_legal_modes_and_results(self):
        for game in OLD[:-1]:
            guide = self.guide(game)
            params = dict(examples(guide))
            questions = importlib.import_module(game + '.questions')
            for mode in questions.VALID_MODES:
                with self.subTest(game=game, mode=mode):
                    self.call(game, game + '_start', {**params[game + '_start'], 'mode': mode})
                    total = len(questions.get_questions(mode))
                    if questions.is_fast_mode(mode):
                        key, sample = next(iter(params[game + '_answer_batch'].items()))
                        size = questions.fast_batch_size(mode)
                        for offset in range(0, total, size):
                            self.call(game, game + '_answer_batch', {key: [sample[0]] * min(size, total - offset)})
                    else:
                        for _ in range(total):
                            self.call(game, game + '_answer', params[game + '_answer'])
                    result = self.call(game, game + '_get_result', params[game + '_get_result'])
                    self.assertIn('存档身份', json.dumps(result, ensure_ascii=False))
            if game == 'enneagram':
                self.assertIn('detail', guide)
                self.call(game, 'enneagram_get_result', {'detail': 'full'})
            if game in ('love', 'ecr'):
                compare = params[game + '_compare']
                for player in compare.values():
                    self.call(game, game + '_start', {'mode': 'full_fast'}, player)
                    self.call(game, game + '_answer_batch', {'answers': [1] * total}, player)
                self.call(game, game + '_compare', compare)

    def test_research_guide_examples_complete_resume_and_restart(self):
        for game in sorted(research_scales.MCP_GAMES):
            with self.subTest(game=game):
                guide_examples = examples(self.guide(game))
                params = dict(guide_examples)
                state = self.call(game, 'start', guide_examples[0][1])
                engine = research_scales.SCALES[game]
                for index in range(len(engine.items)):
                    state = self.call(game, 'answer', {**params['answer'],
                        'session_id': state['session_id'], 'expected_index': state['progress']})
                self.assertTrue(state['complete'])
                self.assertEqual(self.call(game, 'get_result', {})['result_data'], state['result_data'])
                state = self.call(game, 'status', params['status'])
                state = self.call(game, 'start', {**params['start'], 'session_id': state['session_id']})
                self.assertEqual(state['mode'], 'full_fast')
                batch = params['answer_batch']
                state = self.call(game, 'answer_batch', {**batch, 'session_id': state['session_id'],
                    'answers': [batch['answers'][0]] * len(state['questions'])})
                self.assertTrue(state['complete'])

    def test_bdsm_object_answers_with_stubbed_upstream(self):
        from bdsmtest import handler
        fixtures = [{'id': i, 'wording': f'Fixture {i}'} for i in (3, 98, 2)]
        self.stack.enter_context(patch.object(handler.api, 'init_session', return_value={'rauth': {'rid': 'fixture'}, 'pdata': {}}))
        self.stack.enter_context(patch.object(handler.api, 'fetch_questions', return_value=fixtures))
        score = self.stack.enter_context(patch.object(handler.api, 'submit_and_score',
            return_value={'scores': [], 'rid': 'fixture'}))
        params = dict(examples(self.guide('bdsmtest')))
        for mode in ('normal', 'fast'):
            self.call('bdsmtest', 'bdsmtest_start', {'mode': mode})
            if mode == 'normal':
                for _ in fixtures:
                    self.call('bdsmtest', 'bdsmtest_answer', params['bdsmtest_answer'])
            else:
                self.call('bdsmtest', 'bdsmtest_answer_batch', params['bdsmtest_answer_batch'])
                self.assertEqual(score.call_args.args[2], params['bdsmtest_answer_batch']['answers'])
            self.call('bdsmtest', 'bdsmtest_get_result', params['bdsmtest_get_result'])

    def test_aidep_remains_blocked_for_all_mcp_actions(self):
        with self.assertRaises(server._McpError):
            server._tool_get_guide({'game': 'aidep'})
        self.assertNotIn('aidep', QUESTION_VOLUME)
        for action in ('start', 'answer', 'answer_batch', 'status', 'get_result', 'result'):
            with self.subTest(action=action), self.assertRaises(server._McpError):
                self.call('aidep', action, {'mode': 'full'})

    def test_question_volume_is_measured_with_cl100k_base(self):
        try:
            import tiktoken
        except ImportError:
            self.skipTest('offline verification requires optional tiktoken')
        encoding = tiktoken.get_encoding('cl100k_base')
        def totals(strings):
            return sum(len(s) for s in strings), sum(len(encoding.encode(s)) for s in strings)
        for game in sorted(research_scales.MCP_GAMES):
            engine = research_scales.SCALES[game]
            texts = [q['text'] for q in engine.items]
            texts += [o['text'] for o in engine.spec['options']]
            chars, tokens = totals(texts)
            self.assertEqual(QUESTION_VOLUME[game], (chars, tokens), game)
            self.assertIn(f'{chars}字符 / 约{tokens} Token', self.guide(game))
        for game in OLD[:-1]:
            modes = ['short', 'full'] if game == 'mbti' else ['quick', 'full'] if game == 'enneagram' else ['full']
            for mode in modes:
                questions = importlib.import_module(game + '.questions').get_questions(mode)
                texts = [q['text'] for q in questions]
                seen = set()
                for q in questions:
                    options = (q['option_a'], q['option_b']) if game == 'mbti' else tuple(o['text'] for o in q['options'])
                    if options not in seen:
                        texts.extend(options)
                        seen.add(options)
                chars, tokens = totals(texts)
                self.assertIn(f'{chars}字符 / 约{tokens} Token', self.guide(game))
