#!/usr/bin/env python3
"""Deterministic real /mcp/play + temporary SQLite benchmark, no live services.

PYTHONPATH=vendor/duel <duel-python> scripts/sample_duel_incremental_tokens.py --out artifacts/token-opt/before
python3 scripts/sample_duel_incremental_tokens.py --count artifacts/token-opt/before
Repeat the same seed/policies after editing. Raw replies are gzip JSONL.
"""
import argparse
import asyncio
import gzip
import json
import math
from pathlib import Path
import random
import tempfile
from unittest.mock import patch


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


async def sample(out, seeds):
    import httpx
    from app import database, framework, main
    from app.games import GAMES
    from app.games.monopoly import Monopoly
    from app.games.rummikub import Rummikub
    from app.games.bomb_plane import BombPlane, SQUARES
    from app.games.carcassonne import Carcassonne
    out.mkdir(parents=True, exist_ok=True)
    cases = [('monopoly', Monopoly, (2, 4, 6), 760),
             ('rummikub', Rummikub, (2, 4), 240),
             ('bomb_plane', BombPlane, (2,), 202),
             ('carcassonne', Carcassonne, (2, 4, 5), 72)]
    with tempfile.TemporaryDirectory(prefix='duel-token-opt-') as tmp, gzip.open(out/'responses.jsonl.gz', 'wt') as output:
        for game_type, cls, counts, limit in cases:
            for n in counts:
                for seed in seeds:
                    game = cls(random.Random(seed))
                    with patch.object(database, 'DB_PATH', Path(tmp)/f'{game_type}-{n}-{seed}.db'), patch.dict(GAMES, {game_type: game}):
                        database.init_db()
                        seats = [dict(player_id=f'p{i}', role='ai', participant_kind='bound_machine', display_name=f'Player {i}') for i in range(n)]
                        room = framework.create_room(game_type, 'ai_first', 'ai', 'p0', ordered_participants=seats, require_confirmations=False)
                        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://token.test') as client:
                            async def call(kind, pid, step, **body):
                                request = dict(player_id=pid, room_id=room['room_id'], **body)
                                response = await client.post('/mcp/play', json=request)
                                response.raise_for_status()
                                data = response.json()
                                if data.get('ok') is False:
                                    raise AssertionError(data)
                                output.write(dump(dict(game=game_type, players=n, seed=seed, step=step, kind=kind,
                                                      request=request, response=data))+'\n')
                                return data
                            for p in seats:
                                await call('bootstrap', p['player_id'], 0, action='state')
                            for step in range(limit):
                                if room['status'] != 'playing':
                                    break
                                pid = room['current_player_id']
                                actor = next(p for p in room['participants'] if p['player_id'] == pid)
                                state = room['board_state']
                                if game_type == 'bomb_plane':
                                    move = ({'action':'auto_setup'} if state['phase']=='setup' else
                                            {'action':'attack', 'cell':next(c for c in SQUARES if c not in {s['cell'] for s in state['shots'][pid]})})
                                else:
                                    move = game.choose_local_npc_action(state, actor, room['participants'])
                                await call('turn_state', pid, step, action='state')
                                if step % 20 == 0 or step >= limit-5:
                                    await call('full_state', pid, step, action='state', full_state=True)
                                await call('move_reply', pid, step, action='move', revision=room['revision'], move=move)
                                room = framework.get_room(room['room_id'])
                                if room['status']=='playing' and room['current_player_id'] != pid:
                                    await call('waiting', pid, step, action='state')
                            output.flush()
                            print(f'{game_type} {n}p seed={seed}: {step+1} actions, {room["status"]}', flush=True)
    (out/'method.json').write_text(dump(dict(seeds=seeds, transport='httpx ASGITransport /mcp/play; temporary SQLite',
        normal_round='turn_state + move_reply; waiting separate; bootstrap/full_state excluded',
        policies='existing local NPC policy; bomb_plane sequential grid; no game state edits'))+'\n')


def count(path):
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    rows, groups, pairs = [], {}, {}
    with gzip.open(path/'responses.jsonl.gz', 'rt') as source:
        for line in source:
            row = json.loads(line)
            meta = {k:row[k] for k in ('game','players','seed','step','kind')}
            meta['response_tokens'] = len(enc.encode(dump(row['response'])))
            meta['request_tokens'] = len(enc.encode(dump(row['request'])))
            rows.append(meta)
            key=(row['game'],row['players'],row['kind'])
            groups.setdefault(key,[]).append(meta['response_tokens'])
            if row['kind'] in ('turn_state','move_reply'):
                key=(row['game'],row['players'],row['seed'],row['step'])
                pairs[key] = pairs.get(key,0)+meta['response_tokens']
    for (game,n,seed,step), tokens in pairs.items():
        groups.setdefault((game,n,'normal_round'),[]).append(tokens)
        last=max(s for g,p,r,s in pairs if (g,p,r)==(game,n,seed))
        phase = 'early' if step < (last+1)/3 else 'late' if step >= (last+1)*2/3 else 'middle'
        groups.setdefault((game,n,'normal_'+phase),[]).append(tokens)
        threshold={'monopoly':690,'rummikub':40,'bomb_plane':90,'carcassonne':60}[game]
        if step>=threshold:groups.setdefault((game,n,'normal_step_ge_'+str(threshold)),[]).append(tokens)
    def stats(values):
        v=sorted(values)
        return dict(n=len(v),p50=v[math.ceil(len(v)*.5)-1],p95=v[math.ceil(len(v)*.95)-1],max=v[-1],min=v[0])
    report=dict(encoding='cl100k_base',serialization='minified JSON; response only (request separately recorded)',
                summaries=[dict(game=g,players=n,kind=k,**stats(v)) for (g,n,k),v in groups.items()],samples=rows)
    (path/'counts.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report['summaries'],indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--out',type=Path)
    parser.add_argument('--count',type=Path)
    parser.add_argument('--seeds',default='11,37')
    args=parser.parse_args()
    if args.count: count(args.count)
    else: asyncio.run(sample(args.out,[int(s) for s in args.seeds.split(',')]))
