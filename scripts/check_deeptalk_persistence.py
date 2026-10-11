#!/usr/bin/env python3
"""Real Deeptalk packages, independent processes and disposable databases."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def worker(step, payload):
    from deeptalk import catalog
    from deeptalk.store import Store
    store = Store(os.environ['SESSIONS_DB'])
    human = dict(id=101, username='测试人类', is_ai=False)
    ai = dict(id=201, username='测试小机', is_ai=True)
    if step == 'create':
        payload = {tid: store.create(human,ai,tid,f'create_{tid}')['round_id'] for tid in catalog.TOPICS}
    for tid, rid in payload.items():
        if step == 'drafts':
            for user in (human, ai):
                questions = catalog.load_topic(tid)['questions']
                total = len(questions)
                for i,q in enumerate(questions,1):
                    store.answer(user,dict(round_id=rid,question_id=q['id'],text=f'private_{user["id"]}_{i}',revision=i-1,request_id=f'answer_{user["id"]}_{i}'))
            store.submit(human,dict(round_id=rid,revision=total,confirm=True))
        elif step == 'sealed':
            read = store.read(ai,rid,action='question')
            assert read['questions'][0]['own']['text']=='private_201_1'
            assert 'private_101' not in json.dumps(read) and not read['unlocked']
            store.submit(ai,dict(round_id=rid,revision=read['total'],confirm=True))
        elif step == 'complete':
            read = store.read(human,rid,action='view')
            assert read['unlocked'] and len(read['questions'])==catalog.TOPICS[tid][1]
            assert read['questions'][0]['partner']['text']=='private_201_1'
            assert store.create(human,ai,tid,f'reanswer_{tid}')['round_id'] != rid
            assert store.read(human,rid)['unlocked']
    print(json.dumps(payload))


def main():
    with tempfile.TemporaryDirectory(prefix='deeptalk-persistence-') as temp:
        env={**os.environ,'SESSIONS_DB':str(Path(temp)/'sessions.db')}
        result={}
        for step in ('create','drafts','sealed','complete'):
            run=subprocess.run([sys.executable,__file__,'--worker',step],input=json.dumps(result),capture_output=True,text=True,env=env,cwd=ROOT,timeout=30)
            if run.returncode: raise RuntimeError(run.stderr)
            result=json.loads(run.stdout)
        print(f'Deeptalk: {len(result)} real topics passed create/drafts/sealed/complete across independent processes; repeat rounds preserved originals.')
    print('Disposable databases removed; question packages unchanged.')
    return 0


if __name__=='__main__':
    if sys.argv[1:2]==['--worker']: worker(sys.argv[2],json.load(sys.stdin))
    else: raise SystemExit(main())
