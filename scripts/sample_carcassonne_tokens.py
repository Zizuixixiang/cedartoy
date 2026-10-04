#!/usr/bin/env python3
"""Sample actual MCP responses using temporary games. No production service.

Sampling: PYTHONPATH=vendor/duel <duel-python> scripts/sample_carcassonne_tokens.py
Counting: <python-with-tiktoken> scripts/sample_carcassonne_tokens.py --count
Use the shared heavy-test flock for sampling. tiktoken is optional and only used
by --count; neither this script nor tests install it.
"""
import argparse
import json
from pathlib import Path
import tempfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'artifacts'/'carcassonne'


def count_tokens():
    import tiktoken
    enc=tiktoken.get_encoding('cl100k_base')
    samples=json.loads((OUT/'token-fixtures.json').read_text())
    measured=[]
    for row in samples:
        counts={key:len(enc.encode(json.dumps(row[key],ensure_ascii=False,separators=(',',':')))) for key in ('state_response','move_response')}
        measured.append({k:row[k] for k in ('players','turn','board_tiles','placements')}|counts|{'total':sum(counts.values())})
    report={'encoding':'cl100k_base','measurement':'minified JSON, one current-turn state delta + its move reply; excludes bootstrap/guide/chat/thinking',
            'samples':measured,'min':min(r['total'] for r in measured),'max':max(r['total'] for r in measured)}
    (OUT/'token-counts.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


async def sample():
    import httpx
    import random
    from unittest.mock import patch
    from app import database, framework
    from app import main as main_module
    from app.games import GAMES
    from app.games.carcassonne import Carcassonne
    from tests.test_carcassonne import players
    rows=[]
    with tempfile.TemporaryDirectory(prefix='carcassonne-tokens-') as temp:
        for n in (2,3,4,5):
            with patch.object(database,'DB_PATH',Path(temp)/f'tokens-{n}.db'),patch.dict(GAMES,{'carcassonne':Carcassonne(random.Random(700+n))}):
                database.init_db();g=GAMES['carcassonne']
                room=framework.create_room('carcassonne','ai_first','ai','p1',opponent_id='p0',ordered_participants=players(n),require_confirmations=False)
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main_module.app),base_url='http://carcassonne.test') as client:
                    # Consume all one-time bootstraps before measuring turn deltas.
                    for p in room['participants']:
                        if p['role']=='ai':
                            res=await client.post('/mcp/play',json={'action':'state','room_id':room['room_id'],'player_id':p['player_id']});res.raise_for_status()
                    seen=set()
                    while room['status']=='playing':
                        turn=room['board_state']['flow']['turn_number'];a=next(p for p in room['participants'] if p['player_id']==room['current_player_id'])
                        m=g.choose_local_npc_action(room['board_state'],a,room['participants'])
                        if a['role']=='ai':
                            before=await client.post('/mcp/play',json={'action':'state','room_id':room['room_id'],'player_id':a['player_id']});before.raise_for_status()
                            after=await client.post('/mcp/play',json={'action':'move','room_id':room['room_id'],'player_id':a['player_id'],'revision':room['revision'],'move':m});after.raise_for_status()
                            bucket=max(t for t in (0,15,35,60) if t<=turn)
                            if bucket not in seen:
                                seen.add(bucket);state=before.json();assert 'bootstrap' not in state
                                rows.append({'players':n,'turn':turn,'board_tiles':len(room['board_state']['board']),
                                    'placements':len(state['private_state']['placements']),'state_response':state,'move_response':after.json()})
                        else:
                            framework.play_move(room['room_id'],a['role'],a['player_id'],m,expected_revision=room['revision'])
                        room=framework.get_room(room['room_id'])
    OUT.mkdir(parents=True,exist_ok=True)
    (OUT/'token-fixtures.json').write_text(json.dumps(rows,ensure_ascii=False,separators=(',',':'))+'\n')
    print(f'{len(rows)} actual MCP state/move pairs saved to {OUT / "token-fixtures.json"}')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--count',action='store_true');args=p.parse_args()
    if args.count:count_tokens()
    else:
        import asyncio
        asyncio.run(sample())
