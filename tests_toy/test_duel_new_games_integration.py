"""Root token/binding -> real Duel ASGI transport, only disposable SQLite.

No auth resolver or binding lookup is mocked. Only the loopback HTTP transport,
legacy save migration, and unrelated announcements are replaced. No live ports,
production accounts, game rooms, model provider, or production files are used.
"""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

import httpx
import server

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'vendor' / 'duel'))
from app import database, framework, main, npc_controller
from app.games import GAMES


class NewGamesRootIntegration(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='duel-root-games-')
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        for obj, key, value in [
            (server, 'TURTLE_DB_PATH', base/'accounts.db'),
            (server, 'SESSIONS_DB_PATH', base/'sessions.db'),
            (server, 'DUEL_DB_PATH', base/'duel.db'),
            (database, 'DB_PATH', base/'duel.db'),
            (main, 'revision_events', main.RevisionEvents()),
            (server, '_ANTI_ADDICTION_ANY_ENABLED', None),
        ]:
            p = patch.object(obj, key, value); p.start(); self.addCleanup(p.stop)
        for name, value in [('_auto_migrate_legacy_account_saves', []),
                            ('_play_announcements', ''), ('_mcp_forced_announcement', '')]:
            p = patch.object(server, name, return_value=value); p.start(); self.addCleanup(p.stop)
        p = patch.object(npc_controller, 'get_npc_provider', side_effect=AssertionError('no model calls'))
        p.start(); self.addCleanup(p.stop)
        with server._db_connect() as conn:
            conn.executescript('''
                CREATE TABLE toy_users (id INTEGER PRIMARY KEY, username TEXT,
                    password_hash TEXT, is_ai INTEGER, is_admin INTEGER DEFAULT 0,
                    deleted_at TEXT, last_active_at TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP);
                CREATE TABLE user_bindings (human_user_id INTEGER, ai_user_id INTEGER);
                CREATE TABLE anti_addiction_settings (enabled INTEGER);
                INSERT INTO toy_users(id,username,is_ai) VALUES
                    (101,'temporary-human',0),(202,'temporary-ai',1),(303,'temporary-outsider',1);
                INSERT INTO user_bindings VALUES(101,202);
            ''')
            server._init_account_security_schema(conn)
            self.token = server._issue_initial_account_token_in_transaction(conn, dict(conn.execute('SELECT * FROM toy_users WHERE id=202').fetchone()))
            self.outsider = server._issue_initial_account_token_in_transaction(conn, dict(conn.execute('SELECT * FROM toy_users WHERE id=303').fetchone()))
        database.init_db()
        self.forwarded = []
        def post(url, *, json, **kwargs):
            self.assertEqual(url, f'{server.DUEL_BASE}/mcp/play')
            self.forwarded.append(dict(json))
            return self.http('POST', '/mcp/play', json=json)
        p = patch.object(server.httpx, 'post', side_effect=post); p.start(); self.addCleanup(p.stop)

    def http(self, method, path, **kwargs):
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=main.app), base_url='http://duel.test') as client:
                return await client.request(method, path, **kwargs)
        return asyncio.run(request())

    def rpc(self, action, params=None, *, auth='path_token', token=None, tool='play', expect_ok=True):
        arguments = {'game':'duel','action':action,'params':params or {}} if tool=='play' else {'game':'duel'}
        payload = {'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':tool,'arguments':arguments}}
        result = server._handle_root_mcp(payload, **{auth: token or self.token})
        self.assertNotIn('error',result,result)
        self.assertEqual(result['result'].get('isError',False),not expect_ok,result)
        if not expect_ok:return result
        return json.loads(result['result']['content'][0]['text'])

    def test_path_and_bearer_catalog_guide_and_canonical_ordinary_moves(self):
        for auth in ('path_token','bearer_token'):
            catalog = self.rpc('catalog',auth=auth)['games']
            by_id = {g['game_type']:g for g in catalog}
            self.assertEqual(len(by_id),29)
            self.assertEqual(by_id['bomb_plane']['category'],'board')
            self.assertEqual(by_id['carcassonne']['category'],'tabletop')
            guide = self.rpc('',tool='get_guide',auth=auth)['guide']
            for name in ('bomb_plane','carcassonne','monopoly','rummikub'):self.assertIn(name,guide)
            for name in ('bomb_plane','carcassonne'):
                created = self.rpc('new',{'game_type':name,'mode':'ai_first','player_id':'forged','opponent_id':'forged','viewer':'forged','participant_ids':['forged']},auth=auth)
                rid = created.get('room_id') or created['room']['room_id']
                room = framework.get_room(rid)
                self.assertEqual({p['player_id'] for p in room['participants']},{'101','202'})
                self.assertEqual(self.forwarded[-1]['player_id'],'202')
                self.assertEqual(self.forwarded[-1]['opponent_id'],'101')
                if room['status']!='playing':
                    # Human joins the existing trusted pair through the real Web route.
                    response = self.http('POST',f'/api/rooms/{rid}/join',headers={'X-Duel-Human-Player':'101'},json={'player_id':'101'})
                    self.assertEqual(response.status_code,200,response.text)
                    room = framework.get_room(rid)
                state = self.rpc('state',{'room_id':rid,'wait':False},auth=auth)
                # Exactly one initial rules bootstrap; subsequent replies stay compact.
                boot = created if created.get('bootstrap') else state
                self.assertIn('rules_text',boot['room'])
                state = self.rpc('state',{'room_id':rid,'wait':False},auth=auth)
                self.assertNotIn('rules_text',json.dumps(state))
                if name == 'carcassonne':
                    query = self.rpc('state', {'room_id':rid, 'move':{'query':'placements','all':True}}, auth=auth)
                    self.assertTrue(query['placements'])
                    self.assertEqual(self.forwarded[-1]['move'], {'query':'placements','all':True})
                move = GAMES[name].choose_local_npc_action(room['board_state'],{'player_id':'202'},room['participants'])
                rev = room['revision']
                self.rpc('move',{'room_id':rid,'revision':rev,'move':move,'player_id':'101'},auth=auth)
                after = framework.get_room(rid)
                self.assertEqual(after['revision'],rev+1)
                self.assertEqual(self.forwarded[-1]['player_id'],'202')
                self.rpc('move',{'room_id':rid,'revision':rev,'move':move},auth=auth,expect_ok=False)
                self.assertEqual(framework.get_room(rid)['board_state'],after['board_state'])
                self.rpc('state',{'room_id':rid,'viewer':'202'},auth=auth,token=self.outsider,expect_ok=False)
                projected = self.rpc('state',{'room_id':rid,'full_state':True,'wait':False},auth=auth)
                self.assertNotIn('"deck":',json.dumps(projected))
                if name=='bomb_plane':
                    def check_keys(value):
                        if isinstance(value, dict):
                            self.assertNotIn('revealed_planes', value)
                            for child in value.values(): check_keys(child)
                        elif isinstance(value, list):
                            for child in value: check_keys(child)
                    check_keys(projected)
                    self.assertEqual(projected['snapshot']['private_state']['planes'], after['board_state']['planes']['202'])
                self.rpc('resign',{'room_id':rid},auth=auth)

    def test_guide_removed_game_enumeration_is_available_in_real_catalog(self):
        # This is the knowledge removed from DUEL_GUIDE, not a comparison with
        # internal plugin constants. Both supported auth paths must expose it.
        counts = {}
        for names, allowed in (
            ('tictactoe gomoku othello connect4 jungle xiangqi checkers banqi chess junqi go bomb_plane', [2]),
            ('doudizhu', [3]),
            ('guandan mahjong', [4]),
            ('dots_boxes aeroplane_chess gandengyan rummikub', [2, 3, 4]),
            ('chinese_checkers', [2, 3, 4, 6]),
            ('carcassonne', [2, 3, 4, 5]),
            ('liars_dice yahtzee uno blackjack train_cards zhajinhua texas_holdem monopoly', [2, 3, 4, 5, 6]),
        ):
            counts.update(dict.fromkeys(names.split(), allowed))
        without_npcs = set('tictactoe gomoku othello connect4 jungle xiangqi'.split())
        without_stakes = {'yahtzee', 'monopoly', 'rummikub'}
        for auth in ('path_token', 'bearer_token'):
            catalog = {g['game_type']: g for g in self.rpc('catalog', auth=auth)['games']}
            self.assertEqual(set(catalog), set(counts))
            for name, allowed in counts.items():
                with self.subTest(auth=auth, game=name):
                    game = catalog[name]
                    self.assertEqual(game['allowed_player_counts'], allowed)
                    self.assertIs(game['supports_npcs'], name not in without_npcs)
                    self.assertIs(game['supports_stakes'], name not in without_stakes)
            self.assertEqual(catalog['monopoly']['recommended_players'], 4)

    def test_guide_removed_v2_details_are_delivered_and_usable(self):
        # Exercise the model-visible root MCP responses on disposable databases.
        # full_state must stand alone, even when bootstrap is no longer in context.
        for name in ('monopoly', 'rummikub', 'bomb_plane', 'carcassonne'):
            with self.subTest(game=name):
                seats = [dict(player_id=pid, role='ai', participant_kind='bound_machine', display_name=pid)
                         for pid in ('202', '303')]
                room = framework.create_room(name, 'ai_first', 'ai', '202',
                    ordered_participants=seats, require_confirmations=False)
                rid = room['room_id']
                boot = self.rpc('state', {'room_id': rid})
                self.assertTrue(boot['bootstrap'])
                self.assertEqual(boot['protocol'], 2)
                initial = boot['room']
                self.assertTrue(initial['rules_text'])
                self.assertTrue(initial['move_format'])
                self.assertTrue(initial['private_state'])
                full = self.rpc('state', {'room_id': rid, 'full_state': True}, auth='bearer_token')
                self.assertTrue(full['full_state'])
                snap = full['snapshot']
                rules, formats = snap['rules'], snap['action_formats']
                self.assertEqual(snap['viewer_player_id'], '202')
                self.assertEqual(snap['protocol'], 2)
                self.assertEqual(snap['revision'], full['r'])
                self.assertTrue(rules)
                self.assertTrue(formats)
                for protocol in (boot['protocol_guide'], snap['protocol_guide']):
                    self.assertIn('including your own', protocol)
                    self.assertIn('unchanged', protocol)
                    self.assertIn('private', protocol)

                if name == 'monopoly':
                    for text in ('拍卖', '交易', '抵押', '破产', '局内现金与平台筹码完全分离', '接收方'):
                        self.assertIn(text, initial['rules_text'])
                    for actions in (initial['action_formats'], formats):
                        self.assertIn('action_seq', actions['all'])
                        self.assertEqual(actions['respond_trade'], {'accept': 'boolean'})
                        self.assertEqual(set(actions['propose_trade']),
                            {'to', 'give_cash', 'take_cash', 'give_tiles', 'take_tiles'})
                        self.assertIn('auction', actions['no_args'])
                        self.assertIn('mortgage', actions['tile_id'])
                        self.assertIn('bankrupt', actions['no_args'])
                    self.assertIn('only the recipient responds', rules['trade'])
                    self.assertIn('local cash never changes platform chips', rules['debt'])
                    self.assertTrue(snap['trade_options']['partners'])
                    move = snap['legal_actions'][0]
                    self.assertEqual(move['action_seq'], snap['board_state']['action_seq'])
                elif name == 'rummikub':
                    for text in ('至少 30 分', '原桌面牌必须全部留在桌上', '释放后必须在本回合',
                                 '全部在局玩家连续一圈', '牌面局分不扣平台钱包'):
                        self.assertIn(text, initial['rules_text'])
                    self.assertEqual(initial['private_state']['legal_action_spec']['action'], 'meld')
                    self.assertEqual(initial['private_state']['hand'], snap['private_state']['hand'])
                    self.assertEqual(len(snap['private_state']['hand']), 14)
                    self.assertIn('melds', snap['board_state'])
                    self.assertIn('meld_kinds', snap['board_state'])
                    self.assertEqual(rules['opening_threshold'], 30)
                    self.assertIn('only original own hand', rules['opening'])
                    self.assertIn('every old table ID remains exactly once', rules['rearrange'])
                    self.assertIn('at least one original hand ID', rules['rearrange'])
                    self.assertIn('reused this turn', rules['joker'])
                    self.assertIn('containing an original hand tile', rules['joker'])
                    self.assertIn('all active players consecutively pass', rules['turn'])
                    self.assertIn('No platform chip stakes', rules['scoring'])
                    for text in ('FULL final table', 'physical tile IDs', 'runs ascending', 'joker at substituted position'):
                        self.assertIn(text, formats['meld']['melds'])
                    self.assertIn('group|run', formats['meld']['kinds'])
                    self.assertIn('preserve old meld_kinds', formats['meld']['kinds'])
                    self.assertEqual(formats['no_args'], ['draw', 'pass'])
                    for protocol in (boot['protocol_guide'], snap['protocol_guide']):
                        self.assertIn('private.+/- adds/removes hand IDs', protocol)
                        self.assertIn('first remove the submitted table IDs from your hand', protocol)
                        self.assertIn('No automatic suggestion', protocol)
                    move = {'action': formats['no_args'][0]}
                elif name == 'bomb_plane':
                    self.assertIn('10×10', initial['rules_text'])
                    self.assertIn('三个不同机头获胜', initial['rules_text'])
                    self.assertIn('终局（含认输）', initial['rules_text'])
                    self.assertEqual(snap['board_state']['rows'], 10)
                    self.assertEqual(snap['board_state']['cols'], 10)
                    self.assertEqual(snap['private_state']['planes'], [])
                    self.assertEqual(formats['place'], {'head': 'A1..J10', 'direction': ['N', 'E', 'S', 'W']})
                    self.assertEqual(set(formats['no_args']), {'undo', 'clear', 'shuffle', 'ready', 'auto_setup'})
                    self.assertIn('AND ready', formats['no_args']['auto_setup'])
                    self.assertIn('NOT ready', formats['no_args']['shuffle'])
                    self.assertIn('cell', formats['attack'])
                    self.assertIn('Exactly 3 planes', rules['layout'])
                    self.assertIn('All outcomes switch turn', rules['shots'])
                    self.assertIn('First 3 heads wins', rules['shots'])
                    self.assertIn('only terminal revealed_planes', rules['shots'])
                    move = {'action': 'auto_setup'}
                    self.assertIn(move, initial['private_state']['legal_actions'])
                else:
                    self.assertTrue(initial['board_state']['topology'])
                    self.assertEqual(initial['board_state']['topology'], snap['board_state']['topology'])
                    self.assertTrue(snap['board_state']['board'])
                    self.assertEqual(set(formats['place']), {'x', 'y', 'rotation', 'meeple'})
                    self.assertIn('null or available region ID', formats['place']['meeple'])
                    self.assertEqual(formats['place']['rotation'], [0, 1, 2, 3])
                    self.assertEqual(initial['private_state']['legal_action_spec']['action'], 'place')
                    for protocol in (boot['protocol_guide'], snap['protocol_guide']):
                        for text in ('No default placements', 'query:placements,x,y,rotation?,meeple?',
                                     'up to 8 nearby', 'query:placements,all:true',
                                     'Queries do not consume events', 'last consumed response', 'stale detection'):
                            self.assertIn(text, protocol)
                    query = self.rpc('state', {'room_id': rid, 'move': {'query': 'placements', 'all': True}})
                    x, y, rotation, regions = query['placements'][0]
                    self.assertEqual(query['revision'], full['r'])
                    self.assertIsInstance(regions, list)
                    move = dict(action='place', x=x, y=y, rotation=rotation, meeple=None)

                # A move constructed from the replacement surface really works;
                # no engine helper chooses a move and no static guide example is needed.
                reply = self.rpc('move', {'room_id': rid, 'revision': full['r'], 'move': move})
                self.assertEqual(reply['r'], full['r'] + 1)
                self.assertNotIn('bootstrap', reply)
                self.assertNotIn('rules_text', json.dumps(reply))

    def test_merged_schema_recovery_and_query_cursors(self):
        listed = server._handle_root_mcp({'jsonrpc':'2.0','id':8,'method':'tools/list'}, path_token=self.token)
        schema = next(t['inputSchema'] for t in listed['result']['tools'] if t['name']=='play')
        self.assertIn('move', schema['properties']['params']['properties'])
        for auth in ('path_token', 'bearer_token'):
            guide = self.rpc('', tool='get_guide', auth=auth)['guide']
            self.assertIn('老25款确认快照已覆盖的动作', guide)
            self.assertNotIn('旧协议不消费增量事件', guide)
            for name in ('gomoku','uno','monopoly','rummikub','bomb_plane','carcassonne'):
                seats = [dict(player_id=pid, role='ai', participant_kind='bound_machine', display_name=pid)
                         for pid in ('202','303')]
                room = framework.create_room(name,'ai_first','ai','202',ordered_participants=seats,require_confirmations=False)
                rid = room['room_id']
                self.rpc('state', {'room_id':rid}, auth=auth)  # discard bootstrap
                full = self.rpc('state', {'room_id':rid,'full_state':True}, auth=auth)
                self.assertTrue(full['full_state'])
                self.assertNotIn('bootstrap', full)
                self.assertEqual(full['snapshot']['game'], name)
                self.assertEqual(full['snapshot'], self.rpc('state', {'room_id':rid,'full_state':True}, auth=auth)['snapshot'])
                if name == 'carcassonne':
                    def saved_context():
                        with database.connect() as conn:
                            return (tuple(conn.execute('SELECT last_event_id FROM room_event_cursors WHERE room_id=? AND player_id=?',(rid,'202')).fetchone()),
                                    tuple(conn.execute('SELECT context FROM mcp_minimal_contexts WHERE room_id=? AND player_id=?',(rid,'202')).fetchone()))
                    before = saved_context()
                    query = self.rpc('state', {'room_id':rid,'move':{'query':'placements','all':True}}, auth=auth)
                    self.assertEqual(saved_context(), before)
                    x,y,r,regions = query['placements'][0]
                    candidate = self.rpc('state', {'room_id':rid,'move':{'query':'placements','x':x,'y':y,'rotation':r,'meeple':None}}, auth=auth)
                    self.assertTrue(candidate['valid'])
                    self.assertEqual(candidate['revision'], full['r'])
                    self.assertEqual(saved_context(), before)
                    move = dict(action='place',x=x,y=y,rotation=r,meeple=None)
                    self.rpc('move', {'room_id':rid,'revision':full['r'],'move':move}, auth=auth)
                    self.rpc('move', {'room_id':rid,'revision':query['revision'],'move':move}, auth=auth,expect_ok=False)
                    self.assertEqual(framework.get_room(rid)['revision'], full['r']+1)

    def test_bomb_plane_bound_machine_sets_up_before_human_with_real_tokens(self):
        for auth in ('path_token', 'bearer_token'):
            created = self.rpc('new', {'game_type': 'bomb_plane', 'mode': 'human_first'}, auth=auth)
            rid = created.get('room_id') or created['room']['room_id']
            room = framework.get_room(rid)
            if room['status'] != 'playing':
                joined = self.http('POST', f'/api/rooms/{rid}/join',
                    headers={'X-Duel-Human-Player': '101'}, json={'player_id': '101'})
                self.assertEqual(joined.status_code, 200, joined.text)
                room = framework.get_room(rid)
            self.assertEqual(room['current_player_id'], '101')
            # Consume the one-time bootstrap, then exercise compact wait replies.
            self.rpc('state', {'room_id': rid, 'wait': False}, auth=auth)
            with patch.object(main, 'wait_for_revision', side_effect=AssertionError('setup must not wait')):
                state = self.rpc('state', {'room_id': rid, 'wait': True}, auth=auth)
                self.assertEqual(state['setup'], 'open')
                self.assertNotIn('next_call', state)
                placed = self.rpc('move', {'room_id': rid, 'revision': room['revision'],
                    'wait': True, 'player_id': '101',
                    'move': {'action': 'place', 'head': 'C1', 'direction': 'N'}}, auth=auth)
                self.assertEqual(placed['setup'], 'open')
                self.assertNotIn('next_call', placed)
                self.assertEqual(self.forwarded[-1]['player_id'], '202')
            latest = framework.get_room(rid)
            self.assertEqual(latest['board_state']['planes']['101'], [])
            self.assertEqual(latest['board_state']['planes']['202'], [{'head': 'C1', 'direction': 'N'}])
            self.assertNotIn('"head": "C1"', json.dumps(framework.project_room_for_viewer(latest, '101')))
            self.rpc('resign', {'room_id': rid}, auth=auth)

    def test_real_token_invite_and_async_prepare_binding(self):
        for name in ('bomb_plane','carcassonne'):
            created = self.rpc('invite',{'game_type':name,'target_player_count':2,'player_id':'forged'},auth='bearer_token')
            rid = created['room_id']
            self.rpc('join',{'invite_code':created['invite_code'],'player_id':'forged'},token=self.outsider)
            started = self.rpc('start',{'room_id':rid},auth='bearer_token')
            self.assertTrue(started['bootstrap'])
            room = framework.get_room(rid)
            self.assertEqual({p['player_id'] for p in room['participants']},{'202','303'})
            self.rpc('resign',{'room_id':rid})
            for path,bearer in [('/'+self.token,None),('/mcp',self.token)]:
                payload={'jsonrpc':'2.0','id':7,'method':'tools/call','params':{'name':'play','arguments':{'game':'duel','action':'new','params':{'game_type':name,'mode':'ai_first','player_id':'forged','opponent_id':'forged'}}}}
                prepared=server._prepare_duel_gateway_request(payload,original_path=path,bearer_token=bearer,client_ip='192.0.2.99')
                self.assertEqual(prepared['kind'],'ready',prepared)
                self.assertEqual(prepared['backend_payload']['player_id'],'202')
                self.assertEqual(prepared['backend_payload']['opponent_id'],'101')
                backend=self.http('POST','/mcp/play',json=prepared['backend_payload'])
                completed=server._finalize_duel_gateway_rpc(prepared['ticket'],{
                    'kind':'response','status_code':backend.status_code,'data':backend.json(),
                })
                self.assertTrue(completed['ok'],completed)
                self.assertFalse(completed['body']['result'].get('isError',False),completed)
                data=json.loads(completed['body']['result']['content'][0]['text'])
                rid=data.get('room_id') or data['room']['room_id']
                self.assertEqual({p['player_id'] for p in framework.get_room(rid)['participants']},{'101','202'})
                self.assertEqual(server._finalize_duel_gateway_rpc(prepared['ticket'],{})['status_code'],410)
                self.rpc('resign',{'room_id':rid})

if __name__=='__main__':unittest.main()
