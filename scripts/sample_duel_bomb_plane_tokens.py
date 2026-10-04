"""Sample real temporary-room HTTP replies; no model calls, billing or tokenizer claim."""
import asyncio
import json
import math
import os
from pathlib import Path
import random
import tempfile
from unittest.mock import patch
import httpx
from app import database, framework, main
from app.games.bomb_plane import BombPlane, SQUARES


async def sample():
    rows=[]
    with tempfile.TemporaryDirectory(prefix='bomb-plane-token-') as tmp, patch.object(database,'DB_PATH',Path(tmp)/'sample.db'):
        database.init_db()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app),base_url='http://test') as client:
            async def call(**body):
                reply=await client.post('/mcp/play',json=body)
                reply.raise_for_status();return reply.json()
            # Deterministic setup, sequential-grid attacks also exercise longer
            # histories than the heuristic NPC. All moves go through real HTTP.
            ps=[{'player_id':p,'role':'ai','participant_kind':'bound_machine','display_name':p} for p in ('sample-a','sample-b')]
            room=framework.create_room('bomb_plane','ai_first','ai',ps[0]['player_id'],ordered_participants=ps)
            game=BombPlane(random.Random(11))
            from app.games import GAMES
            with patch.dict(GAMES,{'bomb_plane':game}):
                for turn in range(202):
                    pid=room['current_player_id'];state=room['board_state']
                    # Claim bootstrap separately: guide/bootstrap intentionally excluded.
                    await call(action='state',player_id=pid,room_id=room['room_id'])
                    view=await call(action='state',player_id=pid,room_id=room['room_id'],full_state=True)
                    move=({'action':'auto_setup'} if state['phase']=='setup' else
                          {'action':'attack','cell':next(c for c in SQUARES if c not in {s['cell'] for s in state['shots'][pid]})})
                    reply=await call(action='move',player_id=pid,room_id=room['room_id'],revision=room['revision'],move=move)
                    sizes=[len(json.dumps(x,ensure_ascii=False,separators=(',',':')).encode()) for x in (view,reply)]
                    total=sum(sizes)
                    rows.append({'turn':turn,'phase':state['phase'],'shot_count':sum(map(len,state['shots'].values())),
                                 'state_bytes':sizes[0],'move_reply_bytes':sizes[1],
                                 'rough_tokens_low':math.ceil(total/4),'rough_tokens_high':math.ceil(total/3)})
                    room=framework.get_room(room['room_id'])
                    if room['status']=='finished':break
    print(json.dumps({'method':'minified UTF-8 bytes / 4 .. / 3; coarse heuristic, NOT tokenizer or billing',
                      'scope':'real full_state resync + move reply for one decision; excludes bootstrap/guide/chat/thinking',
                      'range':[min(r['rough_tokens_low'] for r in rows),max(r['rough_tokens_high'] for r in rows)],
                      'samples':rows},ensure_ascii=False,indent=2))

if __name__=='__main__':asyncio.run(sample())
