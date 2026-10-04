#!/usr/bin/env python3
"""Field costs and exactly paired representative v1/v2 JSON, cl100k_base."""
import gzip
import json
import math
from pathlib import Path
import tiktoken

ROOT=Path(__file__).resolve().parents[1]/'artifacts'/'token-opt-v2'
ENC=tiktoken.get_encoding('cl100k_base')
FIELDS=('room_id','ok','status','current_actor','your_turn','private_state','unread','unread_hint','events','revision','r','private','wait')
GAMES=('monopoly','rummikub','bomb_plane','carcassonne')


def dump(x): return json.dumps(x,ensure_ascii=False,separators=(',',':'))
def tokens(x): return len(ENC.encode(dump(x)))
def stats(xs):
    xs=sorted(xs)
    return {'n':len(xs),'p50':xs[math.ceil(.5*len(xs))-1],'p95':xs[math.ceil(.95*len(xs))-1],'max':xs[-1]} if xs else None

def main():
    rows={}
    for side in ('before','after'):
        with gzip.open(ROOT/side/'responses.jsonl.gz','rt') as f: rows[side]=[json.loads(line) for line in f]
    checked=0
    for old,new in zip(rows['before'],rows['after']):
        if old['kind'] not in ('turn_state','move_reply'): continue
        def identities(row):
            result=[]
            for e in row['response'].get('events',[]):
                if isinstance(e,list):
                    if row['game']=='bomb_plane': identity=(e[0],'attack')
                    elif row['game']=='carcassonne': identity=(e[0],'place')
                    else: identity=tuple(e[:2])
                elif 'move' in e: identity=(e['actor'],e['move']['action'])
                elif 'monopoly' in e: identity=(row['request']['player_id'],row['request']['move']['action'])
                elif 'rummikub_delta' in e:
                    a=e['rummikub_delta'].get('last_action')
                    if not a: continue
                    identity=(a['player_id'],a['action'])
                elif 'carcassonne_delta' in e: identity=(e['carcassonne_delta']['placed']['player_id'],'place')
                elif 'bomb_plane_shot' in e: identity=(e['bomb_plane_shot']['player_id'],'attack')
                else: continue
                result.append(identity)
            return result
        assert identities(old)==identities(new),(old['game'],old['step'],old['kind'],identities(old),identities(new))
        checked+=len(identities(new))
    (ROOT/'event-audit.json').write_text(json.dumps({'matched_ordered_action_deliveries':checked,'trajectory_responses':len(rows['after'])},indent=2)+'\n')
    audit=[]
    for side,data in rows.items():
        for game in GAMES:
            normal=[r['response'] for r in data if r['game']==game and r['kind'] in ('turn_state','move_reply')]
            for field in FIELDS:
                costs=[tokens(r)-tokens({k:v for k,v in r.items() if k!=field}) if field in r else 0 for r in normal]
                present=[c for c,r in zip(costs,normal) if field in r]
                audit.append(dict(side=side,game=game,field=field,all_responses=stats(costs),present_responses=stats(present)))
    def save(name,value): (ROOT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    save('field-audit.json',dict(method='marginal cost = tokens(full minified JSON) - tokens(JSON with this top-level key removed); absent=0; BPE boundaries mean sums are approximate',fields=audit))
    pairs=[]
    for game in GAMES:
        for kind in ('bootstrap','waiting','turn_state','move_reply','full_state'):
            ids=[i for i,r in enumerate(rows['after']) if r['game']==game and r['kind']==kind]
            # The nearest median response (events preferred for a decision read).
            with_events=[i for i in ids if rows['after'][i]['response'].get('events')]
            selected=with_events if kind=='turn_state' and with_events else ids
            i=sorted(selected,key=lambda i:tokens(rows['after'][i]['response']))[len(selected)//2]
            before,after=rows['before'][i],rows['after'][i]
            pairs.append(dict(game=game,kind=kind,players=after['players'],seed=after['seed'],step=after['step'],
                              request=after['request'],before=before['response'],after=after['response'],
                              before_tokens=tokens(before['response']),after_tokens=tokens(after['response'])))
    save('before-after.json',pairs)
    stress=[]
    for side,data in rows.items():
        eligible=[(i,r) for i,r in enumerate(data) if r['game']=='monopoly' and r['players']==6 and r['kind']=='turn_state' and r['response'].get('events')]
        i,r=max(eligible,key=lambda t:len(t[1]['response']['events']))
        e=r['response']['events']; total=tokens(e)
        stress.append(dict(side=side,seed=r['seed'],step=r['step'],events=len(e),response_tokens=tokens(r['response']),
                           events_tokens=total,average_event_tokens=round(sum(tokens(v) for v in e)/len(e),2),
                           mean_including_list=round(total/len(e),2)))
        save(side+'-max-monopoly-batch.json',r)
    save('monopoly-stress.json',stress)
    lines=['| 游戏 | 字段 | before p50/p95/max | after p50/p95/max |', '|---|---|---:|---:|']
    for game in GAMES:
        for field in FIELDS:
            values=[]
            for side in ('before','after'):
                a=next(r for r in audit if (r['game'],r['field'],r['side'])==(game,field,side))['all_responses']
                values.append('/'.join(str(a[k]) for k in ('p50','p95','max')))
            lines.append(f'| {game} | {field} | {values[0]} | {values[1]} |')
    (ROOT/'field-audit.md').write_text('\n'.join(lines)+'\n')
    print(json.dumps(stress,ensure_ascii=False,indent=2))

if __name__=='__main__': main()
