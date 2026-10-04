#!/usr/bin/env python3
"""Audit paired trajectories and generate response-token comparison tables."""
import gzip
import json
import math
from pathlib import Path

import os
ROOT=Path(os.environ.get('DUEL_TOKEN_REPORT_ROOT', Path(__file__).resolve().parents[1]/'artifacts'/'token-opt'))


def stats(values):
    values=sorted(values)
    return dict(n=len(values),p50=values[math.ceil(len(values)*.5)-1],
                p95=values[math.ceil(len(values)*.95)-1],max=values[-1])


def canonical(snapshot):
    board=snapshot['board_state']
    game=snapshot['game']
    if game=='monopoly':return {k:board[k] for k in ('tiles','players','phase','action_seq','dice','auction','trade','debt')}
    if game=='rummikub':return {k:board[k] for k in ('melds','opened','pool_count','hand_counts')}
    if game=='carcassonne':return {k:board[k] for k in ('board','scores','supply','deck_count','discarded')}
    shots={pid:({r:[s['cell'] for s in values if s['result']==r] for r in ('miss','hit','head')}
                if isinstance(values,list) else values) for pid,values in board['shots'].items()}
    return dict(shots=shots,planes=snapshot['private_state']['planes'],ready=board['ready'],phase=board['phase'])


def main():
    reports=[json.loads((ROOT/which/'counts.json').read_text()) for which in ('before','after')]
    count=0; snapshots=0
    with gzip.open(ROOT/'before'/'responses.jsonl.gz','rt') as a,gzip.open(ROOT/'after'/'responses.jsonl.gz','rt') as b:
        from itertools import zip_longest
        for left,right in zip_longest(a,b):
            assert left and right,'Sample count changed'
            old,new=json.loads(left),json.loads(right)
            for key in ('game','players','seed','step','kind'):assert old[key]==new[key],(key,old,new)
            for row in (old,new):row['request'].pop('room_id')
            assert old['request']==new['request'],(old['request'],new['request'])
            if old['kind']=='full_state':
                assert canonical(old['response']['snapshot'])==canonical(new['response']['snapshot'])
                snapshots+=1
            count+=1
    indexed=[{(r['game'],r['players'],r['kind']):r for r in report['summaries']} for report in reports]
    comparisons=[]
    for key,old in indexed[0].items():
        new=indexed[1][key]
        comparisons.append(dict(game=key[0],players=key[1],kind=key[2],before=old,after=new,
                                p50_reduction_percent=round(100*(1-new['p50']/old['p50']),1)))
    aggregate=[]
    for game in ('monopoly','rummikub','bomb_plane','carcassonne'):
        sides=[]
        for report in reports:
            pairs={}
            for row in report['samples']:
                if row['game']==game and row['kind'] in ('turn_state','move_reply'):
                    key=(row['players'],row['seed'],row['step'])
                    pairs[key]=pairs.get(key,0)+row['response_tokens']
            sides.append(stats(pairs.values()))
        aggregate.append(dict(game=game,before=sides[0],after=sides[1],p50_reduction_percent=round(100*(1-sides[1]['p50']/sides[0]['p50']),1)))
    result=dict(encoding='cl100k_base',normal_round='turn_state + move_reply responses; bootstrap/full_state/waiting separate',
                paired_requests_equal=count,canonical_resyncs_equal=snapshots,aggregate=aggregate,comparisons=comparisons)
    (ROOT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    lines=['| 游戏 | 正常轮次样本 | before p50 / p95 / max | after p50 / p95 / max | p50 降幅 |',
           '|---|---:|---:|---:|---:|']
    for row in aggregate:
        fmt=lambda side:' / '.join(str(row[side][k]) for k in ('p50','p95','max'))
        lines.append(f'| {row["game"]} | {row["after"]["n"]} | {fmt("before")} | {fmt("after")} | {row["p50_reduction_percent"]}% |')
    lines+=['','| 游戏 / 人数 | 类型 | before p50 / p95 / max | after p50 / p95 / max |','|---|---|---:|---:|']
    for row in comparisons:
        fmt=lambda side:' / '.join(str(row[side][k]) for k in ('p50','p95','max'))
        lines.append(f'| {row["game"]} / {row["players"]} | {row["kind"]} | {fmt("before")} | {fmt("after")} |')
    (ROOT/'comparison.md').write_text('\n'.join(lines)+'\n')
    print('\n'.join(lines[:6]))
    print(f'Paired requests: {count}; canonical resyncs: {snapshots}')


if __name__=='__main__':main()
