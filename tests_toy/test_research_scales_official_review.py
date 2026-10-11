"""Pin the seven authorized descriptive fallbacks, not official scoring validity.

Only temporary databases; no server import or production data access.
"""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from research_scales.engine import ResearchScaleEngine
from tests_toy.test_platform_stats import leaderboard

ROOT = Path(__file__).resolve().parents[1]
# Explicit expectations for answers low + (3*i+1) % response_count.
FIXTURES = {
    'irrc': (35, 1, 5, [25/8, 16/6, 11/3, 3, 2.5, 3, 2.5]),
    'ppts': (18, 1, 5, [3, 25/8]),
    'jrrs': (16, 1, 5, [47/16]),
    'rss': (12, 1, 7, [3, 4.5, 3.75]),
    'rbi': (18, 1, 5, [22/7, 19/7, 3.5]),
    'ross': (17, 0, 10, [24/5, 4, 5, 6]),
    'bsfl': (5, 0, 4, [2]),
}


class OfficialScoringReviewTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='cedar-seven-scales-')
        self.addCleanup(temp.cleanup)
        self.db = str(Path(temp.name) / 'sessions.db')
        self.engines = {
            game: ResearchScaleEngine(json.loads(
                (ROOT / 'research_scales/data' / (game + '.json')).read_text()),
                lambda: self.db)
            for game in FIXTURES
        }

    def complete(self, game, mode, player):
        engine = self.engines[game]
        count, low, high, _ = FIXTURES[game]
        answers = [low + (3*i+1) % (high-low+1) for i in range(count)]
        state = engine.execute('start', {'player_id': player, 'mode': mode})
        args = {'player_id': player, 'session_id': state['session_id']}
        if mode == 'full_fast':
            state = engine.execute('answer_batch', {**args, 'answers': answers})
        else:
            for index, answer in enumerate(answers):
                state = engine.execute('answer', {
                    **args, 'expected_index': index, 'answer': answer})
        return state

    def test_both_modes_keep_descriptive_scores_and_extremes(self):
        for game, (count, low, high, expected) in FIXTURES.items():
            engine = self.engines[game]
            with self.subTest(game=game):
                self.assertEqual(engine.spec['aggregation'], 'mean')
                for mode in ('full', 'full_fast'):
                    state = self.complete(game, mode, 'guest:official' + mode.replace('_', ''))
                    values = list(state['result_data']['scores'].values())
                    self.assertEqual(len(values), len(expected))
                    for actual, wanted in zip(values, expected):
                        self.assertAlmostEqual(actual, wanted)
                    self.assertIn('4399', state['result_data']['scoring_note'])
                    self.assertIn('非', state['result_data']['scoring_note'])
                for endpoint in (low, high):
                    reverse = {32} if game == 'irrc' else set(range(9, 13)) if game == 'rss' else set()
                    answers = [low+high-endpoint if i in reverse else endpoint
                               for i in range(1, count+1)]
                    self.assertEqual(set(engine.score(answers).values()), {endpoint})

    def test_unversioned_receipts_survive_read_and_another_process_without_rescoring(self):
        wanted = {}
        for game, engine in self.engines.items():
            state = self.complete(game, 'full_fast', 'guest:officialrestore')
            wanted[game] = state['result_data']
        with sqlite3.connect(self.db) as conn:
            before = conn.execute('SELECT * FROM test_results ORDER BY game').fetchall()
            for row in conn.execute('SELECT result_detail FROM test_results'):
                # These are the legacy receipts in use at HEAD 28c5a78.
                self.assertNotIn('scoring_version', json.loads(row[0]))
        for engine in self.engines.values():
            with patch.object(engine, 'score', side_effect=AssertionError('silent rescore')):
                for action in ('start', 'status', 'result', 'get_result'):
                    state = engine.execute(action, {'player_id': 'guest:officialrestore'})
                    self.assertEqual(state['result_data'], wanted[engine.game])
        script = '''
import json, sys
from pathlib import Path
from research_scales.engine import ResearchScaleEngine
expected = json.load(sys.stdin)
for game, result in expected.items():
    spec = json.loads(Path('research_scales/data', game + '.json').read_text())
    engine = ResearchScaleEngine(spec, lambda: sys.argv[1])
    def forbidden(*args):
        raise AssertionError('silent rescore')
    engine.score = forbidden
    assert engine.execute('get_result', {'player_id': 'guest:officialrestore'})['result_data'] == result
'''
        subprocess.run([sys.executable, '-B', '-c', script, self.db], cwd=ROOT,
                       input=json.dumps(wanted), text=True, capture_output=True,
                       check=True, timeout=30)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT * FROM test_results ORDER BY game').fetchall(), before)

    def test_saved_scores_use_unchanged_mean_distribution_ranges(self):
        for game in FIXTURES:
            for index in range(5):
                self.complete(game, 'full_fast', 'guest:officialstats' + str(index))
        with sqlite3.connect(self.db) as conn:
            before = conn.execute('SELECT * FROM test_results ORDER BY game, player_id').fetchall()
        with patch.object(leaderboard, 'SESSIONS_DB_PATH', Path(self.db)):
            stats = leaderboard._scored_research_distributions()
        for game, (_, low, high, expected) in FIXTURES.items():
            with self.subTest(game=game):
                self.assertEqual(stats[game]['sample_size'], 5)
                fields = leaderboard._scored_research_specs()[game]
                self.assertEqual([(lo, hi) for _, _, lo, hi in fields], [(low, high)] * len(expected))
                for dimension, value in zip(stats[game]['dimensions'], expected):
                    counts = [0] * 5
                    counts[min(4, int((value-low)*5/(high-low)))] = 5
                    self.assertEqual([b['count'] for b in dimension['buckets']], counts)
        with sqlite3.connect(self.db) as conn:
            self.assertEqual(conn.execute('SELECT * FROM test_results ORDER BY game, player_id').fetchall(), before)


if __name__ == '__main__':
    unittest.main()
