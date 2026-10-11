#!/usr/bin/env python3
"""Cross-process scale lifecycle; only a new temporary SQLite database is used."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def worker(mode, step, previous):
    sys.path.insert(0, str(ROOT))
    from research_scales import SCALES
    states = {}
    for game, engine in SCALES.items():
        base = {'player_id': 'guest:regcheck'}
        if step == 'start':
            states[game] = engine.execute('start', {**base, 'mode': mode}, web=True)
        elif step == 'mutate':
            old = previous[game]
            args = {**base, 'session_id': old['session_id']}
            if mode == 'full':
                state = old
                for index in range(3):
                    state = engine.execute('save_answer', {**args, 'expected_revision': state['answer_revision'],
                        'index': index, 'answer': engine.answer_min}, web=True)
                states[game] = state
                assert states[game]['questions'] == old['questions'], game
            else:
                states[game] = engine.execute('answer_batch', {**args, 'answers': [engine.answer_min] * len(engine.items)})
        elif step == 'edit' and mode == 'full':
            old = previous[game]
            states[game] = engine.execute('save_answer', {**base, 'session_id': old['session_id'],
                'expected_revision': old['answer_revision'], 'index': 0, 'answer': engine.answer_max}, web=True)
        else:
            state = engine.execute('status', base, web=True)
            if mode == 'full':
                assert state['questions'] == previous[game]['questions'], game
                assert state['saved_answers'] == [engine.answer_max, engine.answer_min, engine.answer_min], game
            else:
                assert state['result_data'] == previous[game]['result_data'], game
            assert state['session_id'] == previous[game]['session_id']
            assert state['progress'] == (3 if mode == 'full' else len(engine.items)), game
            assert state['complete'] == (mode == 'full_fast'), game
            states[game] = state
    print(json.dumps(states, ensure_ascii=False))


def main():
    for mode in ('full', 'full_fast'):
        with tempfile.TemporaryDirectory(prefix='cedar-research-persistence-') as temp:
            env = {**os.environ, 'SESSIONS_DB': str(Path(temp) / 'sessions.db')}
            states = {}
            for step in ('start', 'mutate', 'edit', 'read'):
                run = subprocess.run([sys.executable, __file__, '--worker', mode, step],
                    input=json.dumps(states), text=True, capture_output=True, env=env, cwd=ROOT, timeout=30)
                if run.returncode:
                    raise RuntimeError(run.stderr)
                states = json.loads(run.stdout)
            print(f'{mode}: {len(states)} scales passed independent start/mutate/edit/read processes')
    print('Temporary guest:regcheck databases removed.')
    return 0


if __name__ == '__main__':
    if sys.argv[1:2] == ['--worker']:
        worker(sys.argv[2], sys.argv[3], json.load(sys.stdin))
    else:
        raise SystemExit(main())
