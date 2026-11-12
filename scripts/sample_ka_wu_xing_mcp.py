"""Reproduce isolated root MCP -> ASGI measurements without runtime dependencies.

Run: python3 scripts/sample_ka_wu_xing_mcp.py [--seeds 7 11 37]
System Python supplies tiktoken; Duel's existing venv runs the actual handlers.
No response/token/credential dump is written to disk. Counts use unmodified,
ensure_ascii=False, separators=(',', ':') JSON; IDs/timestamps are NOT normalized.
state+move is the sum of two separately encoded responses (not a JSON array).
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def compact(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def stats(values):
    values = sorted(values)
    return dict(n=len(values), min=values[0], p50=values[math.ceil(len(values)*.5)-1],
                p95=values[math.ceil(len(values)*.95)-1], max=values[-1])


def collect(seeds, legacy=False):
    import random
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT))
    # Root server's requests dependency lives in the existing system environment.
    # Append (never prepend) so ASGI still uses Duel's pinned venv packages.
    sys.path.append('/usr/local/lib/python3.10/dist-packages')
    from tests_toy.test_duel_ka_wu_xing import OPTIONS
    from tests_toy.test_duel_ka_wu_xing_incremental import WireHarness, category, assert_incremental
    from app.games import GAMES
    from app.games.ka_wu_xing import KaWuXing
    from app import framework, mcp_minimal

    records = []
    class Harness(WireHarness):
        def rpc(self, *args, **kwargs):
            body = super().rpc(*args, **kwargs)
            if body.get('bootstrap'):
                records.append(dict(profile=profile, seed=seed, category='bootstrap', **self.last_wire))
            return body

    for profile, options in [('default', None), ('options', OPTIONS)]:
        for seed in seeds:
            h = Harness(); h.setUp()
            if not legacy: h.views = {}
            try:
                game = KaWuXing(random.Random(seed))
                if legacy: game.mcp_event_key = None
                with patch.dict(GAMES, ka_wu_xing=game), \
                     patch.object(mcp_minimal, 'GAMES', mcp_minimal.GAMES-{'ka_wu_xing'} if legacy else mcp_minimal.GAMES), \
                     patch('app.invites.secrets.SystemRandom', return_value=random.Random(seed)):
                    room = h.create_three_ai_room(options, stake=0)
                    rid = room['room_id']
                    for token in h.tokens.values():
                        h.rpc('state', {'room_id':rid,'wait':False}, token=token)
                    for step in range(1200):
                        room = framework.get_room(rid)
                        if room['status'] == 'finished':
                            break
                        pid = room['current_player_id']
                        state = h.rpc('state', {'room_id':rid,'wait':False}, token=h.tokens[pid])
                        state_wire = h.last_wire
                        move = GAMES['ka_wu_xing'].choose_local_npc_action(room['board_state'], {'player_id':pid}, room['participants'])
                        after = h.rpc('move', {'room_id':rid,'revision':state.get('r',state.get('revision')),'move':move},
                                      token=h.tokens[pid], auth='bearer_token')
                        categories = {category(state), category(after)}
                        group = next(c for c in ('terminal','round_end','ordinary') if c in categories)
                        if group == 'ordinary' and not legacy:
                            assert_incremental(state); assert_incremental(after)
                        records.append(dict(profile=profile, seed=seed, category=group,
                                            action=move['action_id'].split(':')[0], step=step,
                                            trajectory=hashlib.sha256(compact([move,framework.get_room(rid)['board_state']]).encode()).hexdigest(),
                                            state=state_wire, move=h.last_wire))
                    else:
                        raise AssertionError('seeded match failed to terminate')
                    assert 'round_results' not in room['board_state']
                    assert room['board_state']['phase'] == 'finished'
                    h.rpc('state', {'room_id':rid,'wait':False}, token=h.tokens[pid])
                    records.append(dict(profile=profile,seed=seed,category='terminal_repeat',**h.last_wire))
                    for token in h.tokens.values():
                        h.rpc('state', {'room_id':rid,'wait':False,'full_state':True}, token=token)
                        records.append(dict(profile=profile, seed=seed, category='full_state_terminal', **h.last_wire))
            finally:
                h.doCleanups()
    if legacy: return records
    profile, seed = 'recovery', 11
    h=Harness(); h.setUp(); h.views={}
    try:
        with patch.dict(GAMES,ka_wu_xing=KaWuXing(random.Random(seed))), \
             patch('app.invites.secrets.SystemRandom', return_value=random.Random(seed)):
            room=h.create_three_ai_room(OPTIONS); h.bootstrap_all(room)
            h.turn()
            assert framework.get_room(h.rid)['status'] == 'playing'
            room=framework.get_room(h.rid); token=h.tokens[room['current_player_id']]
            h.rpc('state',{'room_id':h.rid,'wait':False,'full_state':True},token=token)
            records.append(dict(profile=profile,seed=seed,category='full_state_midgame',**h.last_wire))
            h.rpc('state',{'room_id':h.rid,'wait':False},token=token)
            records.append(dict(profile=profile,seed=seed,category='reconnect',**h.last_wire))
    finally: h.doCleanups()
    profile, seed = 'forced', 11
    for scenario, actions in [('liang',['liang']),('peng',['discard','peng']),
            ('ming_gang',['discard','ming_gang']),('concealed_gang',['concealed_gang']),
            ('added_gang',['added_gang']),('rob_kong',['added_gang','hu']),
            ('double_hu',['discard','hu','hu']),('self_draw',['hu']),('joker',['hu'])]:
        h=Harness(); h.setUp(); h.views={}
        try:
            with patch.dict(GAMES,ka_wu_xing=KaWuXing(random.Random(seed))), \
                 patch('app.invites.secrets.SystemRandom', return_value=random.Random(seed)):
                room=h.create_three_ai_room(); h.rid=room['room_id']; h.fixture(scenario)
                for action in actions:
                    h.turn(action)
                    records.append(dict(profile=profile,seed=seed,category=scenario,**h.last_pair))
        finally:
            h.doCleanups()
    return records


def report(records):
    import tiktoken
    enc = tiktoken.get_encoding('cl100k_base')
    result = {'encoding':'cl100k_base', 'tiktoken':tiktoken.__version__,
              'method':'compact JSON, no normalization; nearest rank; response only; state+move sums encodings',
              'actions':dict(Counter(r['action'] for r in records if 'action' in r)), 'groups':{}}
    result['trajectory_sha256'] = {
        f'{profile}/{seed}':hashlib.sha256(''.join(r['trajectory'] for r in records
            if r['profile']==profile and r['seed']==seed and 'trajectory' in r).encode()).hexdigest()
        for profile,seed in sorted({(r['profile'],r['seed']) for r in records if 'trajectory' in r})}
    for profile in sorted({r['profile'] for r in records}):
        for category in sorted({r['category'] for r in records}):
            rows = [r for r in records if r['profile']==profile and r['category']==category]
            if not rows: continue
            group = result['groups'][profile+'/'+category] = {}
            for layer in ('asgi', 'content', 'root'):
                for unit, size in [('bytes', lambda s:len(s.encode('utf-8'))), ('tokens', lambda s:len(enc.encode(s)))]:
                    for part in (('state','move','state+move') if 'state' in rows[0] else ('response',)):
                        values = [sum(size(compact(r[p][layer])) for p in ('state','move')) if part=='state+move'
                                  else size(compact(r[layer] if part=='response' else r[part][layer])) for r in rows]
                        group[f'{layer}/{unit}/{part}'] = stats(values)
    return result


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--seeds', type=int, nargs='+', default=[7,11,37])
    p.add_argument('--collect', action='store_true', help=argparse.SUPPRESS)
    p.add_argument('--legacy-baseline', action='store_true',
                   help='reproduce pre-fix routing/private projection, for paired comparison only')
    args = p.parse_args()
    if args.collect:
        print(compact(collect(args.seeds, args.legacy_baseline)))
    else:
        run = subprocess.run([str(ROOT/'vendor/duel/.venv/bin/python'), __file__, '--collect',
                              *(['--legacy-baseline'] if args.legacy_baseline else []),
                              '--seeds', *map(str,args.seeds)], cwd=ROOT, capture_output=True, text=True)
        if run.returncode:
            sys.exit(run.stderr)
        print(json.dumps(report(json.loads(run.stdout)), ensure_ascii=False, indent=2))
