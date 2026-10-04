#!/usr/bin/env python3
"""Paired third-round full-state audit (cl100k_base, minified JSON).

First sample before/after with sample_duel_incremental_tokens.py using unchanged
seeds/policies. Run this script from the repository root. No live DB is opened.
"""
import argparse
from copy import deepcopy
import gzip
import json
import math
from pathlib import Path

import tiktoken


def dump(value):
    return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def stats(values):
    values=sorted(values)
    return dict(n=len(values),p50=values[math.ceil(len(values)*.5)-1],
                p95=values[math.ceil(len(values)*.95)-1],max=values[-1])


def expected_dynamic(old, new, game):
    """Compare all delivered dynamic fields to the pre-change safe snapshot.

    Only monopoly rows need normalization: strip static and derived attributes.
    Every other delivered field must match verbatim, including complete maps,
    joker roles, private hands and grouped shots.
    """
    expected=deepcopy(old['board_state'])
    if game=='monopoly':
        expected['tiles']=[{k:t[k] for k in ('id','owner','level','mortgaged')}
                           for t in expected['tiles'] if t['price']]
        expected['players']=[{k:p[k] for k in ('player_id','cash','position','bankrupt','jailed','jail_turns')}
                             for p in expected['players']]
    assert new['board_state']=={k:expected[k] for k in new['board_state']}
    assert new['private_state']=={k:old['private_state'][k] for k in new['private_state']}
    # Mandatory state is checked independently of the output key selection.
    required={
        'monopoly':{'players','tiles','phase','dice','auction','trade','debt','bank_supply',
                    'current_player_id','turn_player_id','turn_number','action_seq','extra_roll','doubles','trades_this_turn'},
        'rummikub':{'flow','melds','meld_kinds','joker_roles','pool_count','hand_counts','opened',
                    'active_player_ids','blocked_player_ids','turn_player_id','result'},
        'bomb_plane':{'phase','ready','active_player_id','winner_player_id','shots'},
        'carcassonne':{'flow','board','scores','supply','active_player_ids','turn_player_id','result',
                       'current_tile','deck_count','discarded'},
    }[game]
    if game=='bomb_plane' and expected['phase']=='finished':
        required.add('revealed_planes')
    assert set(new['board_state'])==required
    assert set(new['private_state'])=={
        'monopoly':{'jail_cards'},'rummikub':{'hand'},'bomb_plane':{'planes'},'carcassonne':set()}[game]
    assert set(new)=={'board_state','private_state'}


def audit(root):
    enc=tiktoken.get_encoding('cl100k_base')
    sides=[]
    for name in ('before','after'):
        with gzip.open(root/name/'responses.jsonl.gz','rt') as f:
            sides.append([json.loads(line) for line in f])
    before,after=sides
    assert len(before)==len(after)
    end={}
    for row in before:
        key=tuple(row[k] for k in ('game','players','seed'))
        end[key]=max(end.get(key,0),row['step'])
    full={}; rounds={}; examples={}; normal_equal=0
    for old,new in zip(before,after):
        for k in ('game','players','seed','step','kind'): assert old[k]==new[k],k
        assert {k:v for k,v in old['request'].items() if k!='room_id'}=={
                k:v for k,v in new['request'].items() if k!='room_id'}
        game=old['game']; kind=old['kind']; step=old['step']
        tokens=[len(enc.encode(dump(row['response']))) for row in (old,new)]
        key=tuple(old[k] for k in ('game','players','seed'))
        if kind=='full_state':
            assert old['response']['r']==new['response']['r']
            assert 'events' not in new['response'] and 'protocol_guide' not in new['response']
            expected_dynamic(old['response']['snapshot'],new['response']['snapshot'],game)
            phase='early' if step<(end[key]+1)/3 else 'late' if step>=(end[key]+1)*2/3 else 'middle'
            for bucket in ('all',phase): full.setdefault((game,bucket),[]).append(tokens)
            if phase!='early': examples[game]=dict(game=game,players=old['players'],seed=old['seed'],
                step=step,phase=phase,before_tokens=tokens[0],after_tokens=tokens[1],
                before=old['response'],after=new['response'])
        elif kind!='bootstrap':
            # Generated IDs and achievement wall-clock timestamps are not
            # gameplay. Count tokens on raw responses; normalize only equality.
            def normalize(row):
                reply=deepcopy(row['response'])
                for unlock in reply.get('unlocks',[]):
                    if 'unlocked_at' in unlock: unlock['unlocked_at']='TIME'
                return dump(reply).replace(row['request']['room_id'],'ROOM')
            assert normalize(old)==normalize(new),(game,kind,step)
            normal_equal+=1
            if kind in ('turn_state','move_reply'):
                pair=rounds.setdefault((*key,step),[0,0])
                for i in (0,1): pair[i]+=tokens[i]
    def compare(key,values):
        a,b=(stats([v[i] for v in values]) for i in (0,1))
        return dict(game=key[0],scope=key[1],before=a,after=b,
                    reduction_percent=round(100*(1-b['p50']/a['p50']),1))
    rows=[compare(key,values) for key,values in full.items()]
    normal=[compare((game,'normal_round'),[v for k,v in rounds.items() if k[0]==game])
            for game in ('monopoly','rummikub','bomb_plane','carcassonne')]
    for row in normal:
        for metric in ('p50','p95','max'):
            assert row['after'][metric] <= row['before'][metric], (row['game'],metric,row)
    result=dict(encoding='cl100k_base',serialization='minified JSON response',
                equality_normalization=['generated room ID','unlocks[].unlocked_at'],
                paired_requests=len(before),unchanged_ordinary_responses=normal_equal,
                dynamic_snapshots_checked=sum(r['after']['n'] for r in rows if r['scope']=='all'),
                full_state=rows,normal_round=normal)
    (root/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    (root/'examples.json').write_text(json.dumps(examples,ensure_ascii=False,indent=2)+'\n')
    lines=['| Game / scope | n | Before p50 / p95 / max | After p50 / p95 / max | p50 reduction |',
           '|---|---:|---:|---:|---:|']
    for row in rows+normal:
        fmt=lambda side:' / '.join(str(row[side][k]) for k in ('p50','p95','max'))
        lines.append(f"| {row['game']} / {row['scope']} | {row['after']['n']} | {fmt('before')} | {fmt('after')} | {row['reduction_percent']}% |")
    (root/'comparison.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='full_state'},indent=2))
    print('\n'.join(lines))


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--root',type=Path,default=Path('artifacts/third-round/full-state'))
    audit(parser.parse_args().root)
