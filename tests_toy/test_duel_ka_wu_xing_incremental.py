"""Field-level acceptance over authenticated root MCP and disposable Duel ASGI."""
from copy import deepcopy
import json
import os
import random
import subprocess
import sys
import unittest
from unittest.mock import patch

from tests_toy import test_duel_ka_wu_xing as existing
from app import database, framework, mcp_minimal
from app.games import GAMES
from app.games.ka_wu_xing import KaWuXing
import server


def keys(value):
    if isinstance(value, dict):
        for key, child in value.items():
            yield key
            yield from keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from keys(child)


def category(response):
    fields = set(keys(response))
    if response.get('bootstrap'): return 'bootstrap'
    if response.get('full_state'): return 'full_state'
    assert 'round_state' not in fields, response
    if response.get('status') in ('finished', 'archived'): return 'terminal'
    if 'terminal_hands' in fields: return 'round_end'
    return 'ordinary'


def assert_incremental(response):
    fields = set(keys(response))
    assert not fields & {'wall', 'hands', 'action_history', 'rules_text', 'full_state',
                         'snapshot', 'room', 'board_state', 'round_results', 'round_state',
                         'revealed', 'kong_ledger'}, fields
    assert 'hand' not in response.get('private', {}), response
    assert set(response) <= {'r', 'events', 'private', 'wait', 'status', 'participant_status',
                             'unread', 'unread_hint', 'slot'}, response


class WireHarness(unittest.TestCase):
    setUp = existing.KaWuXingRootTests.setUp
    create_three_ai_room = existing.KaWuXingRootTests.create_three_ai_room
    fresh_process_projection = existing.KaWuXingRootTests.fresh_process_projection

    def http(self, *args, **kwargs):
        response = existing.KaWuXingRootTests.http(self, *args, **kwargs)
        self.last_asgi = response.json()
        return response

    def rpc(self, action, params=None, *, auth='path_token', token=None, expect_ok=True):
        payload = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{
            'name':'play','arguments':{'game':'duel','action':action,'params':params or {}}}}
        result = server._handle_root_mcp(payload, **{auth:token or self.token})
        self.assertNotIn('error', result)
        self.assertEqual(bool(result['result'].get('isError')), not expect_ok, result)
        if not expect_ok: return result
        body = json.loads(result['result']['content'][0]['text'])
        self.last_wire = dict(asgi=self.last_asgi, content=body, root=result)
        if hasattr(self, 'views') and action in ('state', 'move', 'start'):
            pid = next(p for p, t in self.tokens.items() if t == (token or self.token))
            self.verify_private(pid, body)
        return body

    def verify_private(self, pid, response):
        """Replay the client contract; compare against a fresh persisted projection."""
        snapshot = response.get('snapshot') or response.get('room')
        if snapshot:
            self.assertIn('rules_text', snapshot)
            self.assertIn('move_format', snapshot)
            self.views[pid] = deepcopy(snapshot['private_state'])
            self.rid = snapshot['room_id']
            self.assertFalse(set(keys(snapshot)) & {'wall', 'hands', 'action_history'})
            if not hasattr(self, 'boards'): self.boards = {}
            self.boards[pid] = deepcopy(snapshot['board_state'])
        elif pid in self.views:
            private = self.views[pid]
            delta = response.get('private', {})
            by_id = {t['id']: t for t in private['hand']}
            for tile in delta.get('-', []):
                self.assertEqual(by_id.pop(tile['id']), tile)
            for tile in delta.get('+', []):
                self.assertNotIn(tile['id'], by_id)
                by_id[tile['id']] = tile
            private.update({k:deepcopy(v) for k,v in delta.items() if k not in ('+', '-')})
            private['hand'] = list(by_id.values())
            if category(response) == 'ordinary': assert_incremental(response)
        else:
            return
        room = framework.get_room(self.rid)
        # Waiting polls may intentionally defer event/private consumption.
        with database.connect() as conn:
            row = conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',
                               (self.rid, pid)).fetchone()
        baseline = json.loads(row[0])['private']
        expected = deepcopy(baseline)
        actual = deepcopy(self.views[pid])
        expected['hand'].sort(key=lambda t:t['id']); actual['hand'].sort(key=lambda t:t['id'])
        self.assertEqual(actual, expected)
        if framework.participant_can_act(room, pid) or response.get('private') or snapshot:
            authoritative = mcp_minimal.private_baseline('ka_wu_xing', room['board_state'], pid)
            authoritative['hand'].sort(key=lambda t:t['id'])
            self.assertEqual(actual, authoritative)
        board = self.boards[pid]
        for event in response.get('events', []):
            if not isinstance(event, list) or len(event)<3: continue
            d=event[2]
            self.assertNotIn('round_state', d)
            if 'discard_added' in d: board['discards'][d['player_id']].append(d['discard_added'])
            if 'discard_removed' in d:
                removal=d['discard_removed']
                board['discards'][removal['player_id']]=[t for t in board['discards'][removal['player_id']] if t['id']!=removal['tile_id']]
            if 'discards_changed' in d: board['discards'].update(d['discards_changed'])
            if 'meld_added' in d:
                added=d['meld_added']; board['melds'][added.get('player_id',d['player_id'])].append(added['meld'])
            for change in d.get('meld_changed',[]): board['melds'][change['player_id']][change['index']]=change['meld']
            if 'melds_changed' in d: board['melds'].update(d['melds_changed'])
            board['revealed'].update(d.get('revealed_added',{}))
            if 'kong_added' in d: board['kong_ledger'].append(d['kong_added'])
            for key in ('phase','turn_player_id','wall_remaining','revealed','kong_scores','kong_ledger',
                        'dealer_player_id','participant_order','seat_winds'):
                if key in d: board[key]=deepcopy(d[key])
        if snapshot or framework.participant_can_act(room,pid) or response.get('events'):
            expected=GAMES['ka_wu_xing'].public_state(room['board_state'],room['participants'])
            # Tile labels and derived meld display fields are deliberately absent in deltas.
            def normalize(v):
                if isinstance(v,dict): return {k:normalize(x) for k,x in v.items() if k not in ('label','suit','rank','tile_count','concealed')}
                if isinstance(v,list): return [normalize(x) for x in v]
                return v
            for key in ('discards','melds','phase','turn_player_id','wall_remaining','revealed','kong_scores','kong_ledger',
                        'dealer_player_id','participant_order','seat_winds'):
                self.assertEqual(normalize(board[key]),normalize(expected[key]),key)

    def bootstrap_all(self, room):
        self.rid = room['room_id']
        for pid, token in self.tokens.items():
            response = self.rpc('state', {'room_id':self.rid,'wait':False}, token=token)
            if pid not in self.views:
                self.rpc('state', {'room_id':self.rid,'wait':False,'full_state':True}, token=token)

    def turn(self, kind=None):
        room = framework.get_room(self.rid); pid = room['current_player_id']
        before = self.rpc('state', {'room_id':self.rid,'wait':False}, token=self.tokens[pid])
        before_wire = self.last_wire
        game = GAMES['ka_wu_xing']
        legal = game.legal_actions_for(room['board_state'], pid)
        chosen = next(a for a in legal if a['kind']==kind or a['action_id'].startswith(str(kind)+':')) if kind else None
        move = ({'action':'act','action_id':chosen['action_id']} if chosen else
                game.choose_local_npc_action(room['board_state'], {'player_id':pid}, room['participants']))
        after = self.rpc('move', {'room_id':self.rid,'revision':before['r'],'move':move},
                         token=self.tokens[pid], auth='bearer_token')
        self.last_pair = dict(state=before_wire, move=self.last_wire, action=move['action_id'].split(':')[0])
        return before, after

    def fixture(self, scenario):
        """84 real unique tile IDs; only setup is injected, all actions use MCP."""
        room = framework.get_room(self.rid); game = GAMES['ka_wu_xing']
        order = room['board_state']['participant_order']; a,b,c = order
        options = {**existing.OPTIONS, 'rounds':1}
        if scenario in ('double_hu','liang','peng','ming_gang','added_gang','rob_kong'):
            options['joker'] = 'none'
        s = game.configure(game._empty_state(order), options)
        s.update(phase='discard', turn_player_id=a, dealer_player_id=a)
        s['flow']['phase'] = 'discard'
        stock = game.build_wall()
        def take(codes):
            result=[]
            for code in codes.split():
                tile=next(t for t in stock if t['code']==code); stock.remove(tile); result.append(tile)
            return result
        gap='B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B6 J1 J1'
        if scenario == 'double_hu':
            for pid in (b,c):
                s['hands'][pid]=take(gap)
                s['revealed'][pid]={'hand':deepcopy(s['hands'][pid]),'waits':['B5']}
            s['hands'][a]=take('B5')
        elif scenario == 'liang': s['hands'][a]=take(gap+' J2')
        elif scenario == 'self_draw': s['hands'][a]=take(gap+' B5')
        elif scenario == 'joker': s['hands'][a]=take('B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 J1 J2 J2 B5')
        elif scenario in ('peng','ming_gang'):
            s['hands'][b]=take(('B5 B5 ' if scenario=='peng' else 'B5 B5 B5 ')+'B1 B2 B3 T1 T2 T3 J2 J3')
            s['hands'][a]=take('B5')
        elif scenario in ('added_gang','rob_kong'):
            s['melds'][a]=[dict(kind='peng',tiles=take('B5 B5 B5'),engine_tile='B5',offer=1,source_player_id=c)]
            s['hands'][a]=take('B5')
            if scenario=='rob_kong': s['hands'][b]=take(gap)
        elif scenario == 'concealed_gang': s['hands'][a]=take('B1 B1 B1 B1')
        else: raise ValueError(scenario)
        for pid in order:
            target=(14 if pid==a else 13)-3*len(s['melds'][pid])
            while len(s['hands'][pid])<target:
                s['hands'][pid].append(stock.pop())
        s['wall']=stock
        s['drawn_tile_id']=s['hands'][a][-1]['id']
        self.assertEqual(len(stock)+sum(map(len,s['hands'].values()))+sum(len(m['tiles']) for ms in s['melds'].values() for m in ms),84)
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?,current_player_id=? WHERE room_id=?',
                         (json.dumps(s),a,self.rid))
            conn.execute('DELETE FROM mcp_minimal_contexts WHERE room_id=?',(self.rid,))
        self.views={}; self.bootstrap_all(framework.get_room(self.rid))
        return a,b,c


class IncrementalAcceptance(WireHarness):
    def setUp(self):
        super().setUp()
        p=patch.dict(GAMES, ka_wu_xing=KaWuXing(random.Random(11)))
        p.start(); self.addCleanup(p.stop)
        self.views={}

    def test_seeded_single_hand_reconstruction_and_reconnect(self):
        room=self.create_three_ai_room(existing.OPTIONS, stake=0); self.bootstrap_all(room)
        categories=set()
        for step in range(1000):
            room=framework.get_room(self.rid)
            if room['status']=='finished': break
            if step==8:
                pid=room['current_player_id']
                fresh=self.fresh_process_projection(room,pid)
                self.assertEqual(fresh['private_state'],framework.project_room_for_viewer(room,pid)['private_state'])
                full=self.rpc('state', {'room_id':self.rid,'wait':False,'full_state':True},token=self.tokens[pid])
                self.assertIn('rules_text',full['snapshot'])
                # Same account, a new HTTP client on every request: no rebootstrap.
                following=self.rpc('state',{'room_id':self.rid,'wait':False},token=self.tokens[pid])
                self.assertNotIn('events',following); self.assertNotIn('private',following)
                self.assertNotIn('bootstrap',following)
                # A fresh process reads the persisted v2 cursor/baseline via real ASGI.
                code = '''import asyncio,json,sys,httpx
from app.main import app
async def run():
 async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://isolated') as c:
  r=await c.post('/mcp/play',json={'action':'state','room_id':sys.argv[1],'player_id':sys.argv[2],'wait':False})
  r.raise_for_status(); print(json.dumps(r.json()))
asyncio.run(run())'''
                child=subprocess.run([sys.executable,'-c',code,self.rid,pid],capture_output=True,text=True,check=True,timeout=30,
                    env={**os.environ,'DUEL_DB_PATH':str(database.DB_PATH),
                         'PYTHONPATH':os.pathsep.join([str(existing.Path(__import__('app').__file__).resolve().parent.parent),os.environ.get('PYTHONPATH','')])})
                self.assertEqual(json.loads(child.stdout),{'r':room['revision']})
            pair=self.turn(); categories.update(map(category,pair))
        self.assertEqual(room['status'],'finished')
        self.assertNotIn('round_results', room['board_state'])
        self.assertEqual(room['board_state']['phase'], 'finished')
        self.assertTrue({'ordinary','terminal'}<=categories)

    def test_legacy_context_upgrades_once_and_repeated_state_is_empty(self):
        room=self.create_three_ai_room(); self.bootstrap_all(room)
        pid=room['current_player_id']
        with database.write_transaction() as conn:
            conn.execute('DELETE FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',(self.rid,pid))
        boot=self.rpc('state',{'room_id':self.rid,'wait':False},token=self.tokens[pid])
        self.assertEqual(boot['protocol'],2); self.assertTrue(boot['bootstrap'])
        for _ in range(2):
            self.assertEqual(self.rpc('state',{'room_id':self.rid,'wait':False},token=self.tokens[pid]),{'r':room['revision'],'slot':1})

    def test_full_state_covers_actions_but_keeps_unread_chat_once(self):
        room=self.create_three_ai_room(); self.bootstrap_all(room)
        self.turn()
        room=framework.get_room(self.rid); pid=room['current_player_id']
        other=next(p for p in self.tokens if p!=pid)
        framework.post_message(self.rid,'ai',other,'isolated recovery message')
        for _ in range(2):
            full=self.rpc('state',{'room_id':self.rid,'full_state':True,'wait':False},token=self.tokens[pid])
            self.assertNotIn('events',full)
        state=self.rpc('state',{'room_id':self.rid,'wait':False},token=self.tokens[pid])
        self.assertEqual(state['events'],[{'actor':other,'message':'isolated recovery message'}])
        state=self.rpc('state',{'room_id':self.rid,'wait':False},token=self.tokens[pid])
        self.assertNotIn('events',state); self.assertNotIn('private',state)

    def test_special_actions_use_real_mcp_and_private_deltas(self):
        # Separate room/DB per subcase; ordinary distribution excludes forced fixtures.
        for scenario, actions in [('liang',['liang']),('peng',['discard','peng']),
                ('ming_gang',['discard','ming_gang']),('concealed_gang',['concealed_gang']),
                ('added_gang',['added_gang']),('rob_kong',['added_gang','hu']),
                ('double_hu',['discard','hu','hu']),('self_draw',['hu']),('joker',['hu'])]:
            with self.subTest(scenario=scenario):
                h=WireHarness(); h.setUp(); h.views={}
                try:
                    room=h.create_three_ai_room(); h.rid=room['room_id']; h.fixture(scenario)
                    for kind in actions:
                        before,after=h.turn(kind)
                        if category(after)=='ordinary': assert_incremental(after)
                    final=framework.get_room(h.rid)
                    if scenario=='double_hu': self.assertEqual(len(final['result']['winner_player_ids']),2)
                    if scenario in ('self_draw','joker'):
                        self.assertEqual(len(final['result']['horse_tiles']),1)
                        self.assertTrue(any(final['result']['score_breakdown']['piao'].values()))
                    if final['status']=='finished':
                        self.assertFalse(any(final['result'].get('settlement_deltas', {}).values()))
                finally: h.doCleanups()
