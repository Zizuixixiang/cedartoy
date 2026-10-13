"""Human-only additions against all real questions and disposable legacy rounds."""
import hashlib
import json
import unittest
from unittest.mock import patch

from deeptalk import catalog, quick_answers
from tests_toy import test_deeptalk as fixtures, test_deeptalk_packages as packages
from tests_toy.test_deeptalk import HUMAN, AI, OTHER_HUMAN

# Official DeepSeek output, 2026-10-14: retain all 17 free-only decisions.
FREE_TEXT_ONLY = {
    'couple_50': [
        'couple_50_008',
        'couple_50_010',
        'couple_50_025',
    ],
    'romance_values_100': [
        'romance_values_100_010',
        'romance_values_100_011',
        'romance_values_100_020',
        'romance_values_100_032',
        'romance_values_100_045',
        'romance_values_100_080',
    ],
    'favorites_100': [
        'favorites_100_099',
    ],
    'getting_to_know_100': [
        'getting_to_know_100_001',
        'getting_to_know_100_003',
        'getting_to_know_100_050',
        'getting_to_know_100_087',
        'getting_to_know_100_088',
        'getting_to_know_100_100',
    ],
    'life_reflection_35': [
        'life_reflection_35_011',
    ],
}

# Canonical JSON digests preserve verified wording, modes and ordering.
# Official DeepSeek import followed by narrow editorial fixes to 14 reviewed
# issue groups (approved model phrasing plus objective category/alias removals).
IMPORTED_DIGESTS = {
    'relations_communication': '24e1970b847834bd8db151c135df1e7c83f4b19900d2e0b759a1eea257ec2a2d',
    'couple_50': '825f3275022f516326cc682d8de86e5bd73d13190f28ff3a54be17fae7bb2b79',
    'relationship_scenarios': 'b113de689f90a654d2239d30c5b7a9212507a8fbfa013c1e8453a9da0b5e4fd6',
    'romance_values_100': '0037e633198ccef8f41435a94c6f86ed52b95a5af145c29e7c2e13ee6a5dbf96',
    'work_values_20': '5bf51834901ce040709565d06c5379db91b3da8f1ebab10ad7cd88436310a41b',
    'favorites_100': '702c33a2922ce3928c1c7531ad7036979b6e0a903d7134cf73ebe5a5ce8218b9',
    'getting_to_know_100': '5fdd0eed7c0e58c34bedb90149dfe7eb7e81dd8ed1e158ef34dca87232cb0155',
    'life_reflection_35': 'bad244a5e11c788c4e955c88461bccdb964031d2659359cc6e298473767ad066',
    'real_life_dilemmas_30': '74a1faf8505355d33b5525a7d6a1ab0b03a87b2ea22d4e0340059a0597c98d91',
    'learning_goals_problem_solving': 'fb5653596e4eb775a1335e24d092bc653d81dfaaf453100e00a781d2544a0fc9',
}


class QuickAnswerTests(unittest.TestCase):
    connect = fixtures.DeeptalkTests.connect
    http = fixtures.DeeptalkTests.http
    mcp = fixtures.DeeptalkTests.mcp
    setUp = packages.RealPackageTests.setUp

    def test_all_490_have_valid_options_or_explicit_free_text_exception(self):
        self.assertEqual(set(p.stem for p in quick_answers.DATA_DIR.glob('*.json')), set(catalog.TOPICS))
        total = free_count = option_count = 0
        for tid, (_, count, _) in catalog.TOPICS.items():
            with self.subTest(topic=tid):
                topic = catalog.load_topic(tid)
                data = json.loads((quick_answers.DATA_DIR / f'{tid}.json').read_text())
                self.assertEqual(set(data), {q['id'] for q in topic['questions']})
                self.assertEqual(len(data), count)
                self.assertEqual(quick_answers.load_options(tid), data)
                canonical = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
                self.assertEqual(hashlib.sha256(canonical.encode()).hexdigest(), IMPORTED_DIGESTS[tid])
                for q in topic['questions']:
                    item = data[q['id']]
                    self.assertEqual(set(item), {'mode', 'free_text_only', 'options', 'question_sha256'})
                    self.assertIn(item['mode'], {'single', 'multiple'})
                    self.assertIs(type(item['free_text_only']), bool)
                    self.assertEqual(item['free_text_only'], q['id'] in FREE_TEXT_ONLY.get(tid, []))
                    self.assertEqual(item['question_sha256'], hashlib.sha256(q['text'].encode()).hexdigest())
                    options = item['options']
                    if item['free_text_only']:
                        self.assertEqual(options, [])
                        free_count += 1
                    else:
                        self.assertTrue(2 <= len(options) <= 5)
                        self.assertEqual(len(set(options)), len(options))
                        self.assertTrue(all(isinstance(o, str) and o == o.strip() and 1 <= len(o) <= 60 and '\n' not in o for o in options))
                        self.assertNotIn(q['text'], options)
                        option_count += len(options)
                total += len(data)
        self.assertEqual((total, free_count, option_count), (490, 17, 2132))

    def test_human_view_only_old_rounds_no_migration_or_mcp_growth(self):
        for tid in catalog.TOPICS:
            with self.subTest(topic=tid):
                # single_ai is the legacy round shape, created without any extra data.
                rid = self.store.create(HUMAN, AI, tid, 'quick_old_' + tid)['round_id']
                q = catalog.load_topic(tid)['questions'][0]
                self.store.answer(HUMAN, dict(round_id=rid, question_id=q['id'], revision=0,
                                             request_id='old_answer_001', text='原有历史回答'))
                with self.store.connection() as conn:
                    before = tuple(conn.execute('SELECT * FROM deeptalk_rounds WHERE id=?', (rid,)).fetchone())
                # A legacy round needs neither a current catalog read nor a snapshot edit.
                with patch.object(catalog, 'load_topic', side_effect=AssertionError('snapshot only')):
                    status, view, _ = self.http(HUMAN, 'view', round_id=rid)
                self.assertEqual(status, 200)
                for item in view['questions']:
                    self.assertEqual('quick_answers' in item, item['id'] not in FREE_TEXT_ONLY.get(tid, {}))
                self.assertEqual(view['questions'][0]['own']['text'], '原有历史回答')
                for action in ('question', 'status'):
                    expected = self.store.read(AI, rid, action=action)
                    for path in (True, False):
                        with patch.object(quick_answers, 'attach_to_view', side_effect=AssertionError('human only')):
                            response = self.mcp(AI, action, path=path, round_id=rid)['result']
                        actual = json.loads(response['content'][0]['text'])
                        self.assertEqual(actual, expected)
                        self.assertEqual(len(json.dumps(actual, ensure_ascii=False)), len(json.dumps(expected, ensure_ascii=False)))
                self.assertNotIn('quick_answers', json.dumps(self.http(HUMAN, 'question', round_id=rid)[1]))
                self.assertEqual(self.http(OTHER_HUMAN, 'view', round_id=rid)[0], 404)
                with self.store.connection() as conn:
                    after = tuple(conn.execute('SELECT * FROM deeptalk_rounds WHERE id=?', (rid,)).fetchone())
                self.assertEqual(after, before)
                self.assertNotIn('quick_answers', json.dumps(self.store.source(HUMAN, rid)))

    def test_free_text_exceptions_accept_player_supplied_answers(self):
        for tid, ids in FREE_TEXT_ONLY.items():
            rid = self.store.create(HUMAN, AI, tid, 'free_text_' + tid)['round_id']
            for revision, qid in enumerate(ids):
                with self.subTest(topic=tid, question=qid):
                    text = 'Player-supplied fixture ' + qid
                    status, _, _ = self.http(HUMAN, 'answer', post=True, round_id=rid,
                                            question_id=qid, revision=revision,
                                            request_id=f'free_text_{revision}', text=text)
                    self.assertEqual(status, 200)
                    status, view, _ = self.http(HUMAN, 'view', round_id=rid)
                    self.assertEqual(status, 200)
                    question = next(q for q in view['questions'] if q['id'] == qid)
                    self.assertNotIn('quick_answers', question)
                    self.assertEqual(question['own']['text'], text)

    def test_different_historical_text_or_id_never_gets_wrong_suggestions(self):
        for tid in catalog.TOPICS:
            q = catalog.load_topic(tid)['questions'][0]
            for question in ({**q, 'text': q['text'] + '（旧版问法）'}, {**q, 'id': 'retired_question'}):
                view = {'topic_id': tid, 'questions': [question]}
                self.assertNotIn('quick_answers', quick_answers.attach_to_view(view)['questions'][0])

    def test_two_options_are_usable_but_invalid_bundles_fall_back(self):
        tid = 'work_values_20'
        q = catalog.load_topic(tid)['questions'][0]
        item = dict(mode='single', question_sha256=hashlib.sha256(q['text'].encode()).hexdigest())
        options = [f'Fixture option {n}' for n in range(6)]
        for count in (2, 5, 1, 6):
            with self.subTest(count=count), patch.object(quick_answers, 'DATA_DIR', self.root):
                data = {q['id']: dict(item, options=options[:count])}
                (self.root / f'{tid}.json').write_text(json.dumps(data, ensure_ascii=False))
                quick_answers.load_options.cache_clear()
                try:
                    view = dict(topic_id=tid, questions=[dict(q)])
                    if count in (2, 5):
                        result = quick_answers.attach_to_view(view)
                        self.assertEqual(result['questions'][0]['quick_answers']['options'], options[:count])
                    else:
                        with self.assertLogs('deeptalk.quick_answers', level='WARNING'):
                            result = quick_answers.attach_to_view(view)
                        self.assertNotIn('quick_answers', result['questions'][0])
                finally:
                    quick_answers.load_options.cache_clear()

    def test_all_work_comparisons_offer_one_choice_from_three_directions(self):
        data = quick_answers.load_options('work_values_20')
        self.assertEqual(len(data), 20)
        for qid, item in data.items():
            with self.subTest(question=qid):
                self.assertEqual(item['mode'], 'single')
                self.assertEqual(len(item['options']), 3, 'A, B and a middle ground')

    def test_free_only_schema_and_legacy_default(self):
        tid = 'work_values_20'
        questions = catalog.load_topic(tid)['questions'][:2]
        original = quick_answers.load_options(tid)
        regular = dict(original[questions[0]['id']])
        free = dict(original[questions[1]['id']], free_text_only=True, options=[])
        legacy = {k: v for k, v in regular.items() if k != 'free_text_only'}
        valid = [regular, legacy]
        invalid = [dict(regular, free_text_only=value) for value in (None, 0, 1, 'false', 'true', [])]
        invalid += [dict(regular, free_text_only=True), dict(regular, options=[]),
                    {k: v for k, v in free.items() if k != 'free_text_only'},
                    dict(regular, question_sha256='invalid'), dict(regular, mode=[]),
                    dict(regular, options=[{}, {}]), None]
        for is_valid, item in [(True, i) for i in valid] + [(False, i) for i in invalid]:
            with self.subTest(item=item), patch.object(quick_answers, 'DATA_DIR', self.root):
                data = {questions[0]['id']: item, questions[1]['id']: free}
                (self.root / f'{tid}.json').write_text(json.dumps(data))
                quick_answers.load_options.cache_clear()
                try:
                    view = dict(topic_id=tid, questions=[dict(q) for q in questions])
                    if is_valid:
                        result = quick_answers.attach_to_view(view)
                        self.assertEqual(result['questions'][0]['quick_answers']['options'], regular['options'])
                        self.assertNotIn('free_text_only', result['questions'][0]['quick_answers'])
                        self.assertNotIn('quick_answers', result['questions'][1])
                        self.assertEqual(quick_answers.load_options(tid)[questions[1]['id']], free)
                    else:
                        with self.assertLogs('deeptalk.quick_answers', level='WARNING'):
                            result = quick_answers.attach_to_view(view)
                        self.assertTrue(all('quick_answers' not in q for q in result['questions']))
                finally:
                    quick_answers.load_options.cache_clear()

    def test_mcp_tools_never_advertise_web_choices(self):
        import server
        for legacy in (False, True):
            response = server._handle_root_mcp(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'},
                bearer_token=self.tokens[AI['id']], schema_mode='legacy' if legacy else 'standard')
            serialized = json.dumps(response)
            for field in ('quick_answers', 'free_text_only', 'question_sha256'):
                self.assertNotIn(field, serialized)

    def test_broken_suggestions_do_not_block_free_text(self):
        quick_answers.load_options.cache_clear()
        try:
            with patch.object(quick_answers, 'DATA_DIR', self.root / 'missing'), self.assertLogs('deeptalk.quick_answers', level='WARNING'):
                self.assertEqual(quick_answers.load_options('favorites_100'), {})
        finally:
            quick_answers.load_options.cache_clear()


if __name__ == '__main__':
    unittest.main()
