"""Real authenticated root MCP -> isolated Duel ASGI, three independent AIs.

Run with PYTHONPATH pointing to the paired Duel worktree during development.
Only temporary account/session/room databases are written.
"""
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from tests_toy import test_duel_new_games_integration as integration
from app import chips, database, framework, main
from app.npc_personas import NpcPersona
from app.games import GAMES


OPTIONS = dict(double_hu=True, gang_discard_double=True, liang_liability=True,
               consecutive_kong_double=True, horse_count=1, piao=3, joker='red', rounds=4)


class KaWuXingRootTests(unittest.TestCase):
    setUp = integration.NewGamesRootIntegration.setUp
    http = integration.NewGamesRootIntegration.http
    rpc = integration.NewGamesRootIntegration.rpc

    def test_authenticated_cross_account_invite_state_move_and_zero_stake(self):
        with server._db_connect() as conn:
            conn.execute("INSERT INTO toy_users(id,username,is_ai) VALUES(404,'third-ai',1)")
            third = server._issue_initial_account_token_in_transaction(conn, dict(conn.execute('SELECT * FROM toy_users WHERE id=404').fetchone()))
        catalog = self.rpc('catalog')['games']
        game = next(g for g in catalog if g['game_type'] == 'ka_wu_xing')
        self.assertEqual(game['allowed_player_counts'], [3])
        tokens = {'202': self.token, '303': self.outsider, '404': third}
        invitation = self.rpc('invite', {'game_type': 'ka_wu_xing', 'target_player_count': 3, 'stake': 0, 'player_id': 'forged'})
        room_data = invitation.get('room', invitation)
        rid = room_data['room_id']
        room = framework.get_room(rid)
        for token in (self.outsider, third):
            self.rpc('join', {'invite_code': room['invite_code'], 'player_id': 'forged'}, token=token, auth='bearer_token')
        self.rpc('start', {'room_id': rid})
        room = framework.get_room(rid)
        self.assertEqual(room['status'], 'playing')
        self.assertEqual({p['player_id'] for p in room['participants']}, set(tokens))
        self.assertTrue(all(p['role'] == 'ai' for p in room['participants']))
        for pid, token in tokens.items():
            response = self.rpc('state', {'room_id': rid, 'full_state': True, 'wait': False}, token=token)
            snapshot = response.get('snapshot', response.get('room', response))
            self.assertNotIn('"wall":', json.dumps(snapshot))
            self.assertNotIn('"hands":', json.dumps(snapshot))
        actor = next(p for p in room['participants'] if p['player_id'] == room['current_player_id'])
        action = GAMES['ka_wu_xing'].choose_local_npc_action(room['board_state'], actor, room['participants'])
        args = {'room_id': rid, 'revision': room['revision'], 'move': action}
        self.rpc('move', args, token=tokens[actor['player_id']], auth='bearer_token')
        self.rpc('move', args, token=tokens[actor['player_id']], expect_ok=False)
        self.rpc('resign', {'room_id': rid})
        self.assertEqual(framework.get_room(rid)['status'], 'finished')

    def test_local_npcs_fill_two_seats_without_model_provider(self):
        with patch.object(main, 'select_personas', return_value=[NpcPersona(f'kwx-{i}', f'测试NPC{i}', 'isolated') for i in range(2)]):
            created = self.rpc('new', {'game_type': 'ka_wu_xing', 'include_human': False,
                                      'target_player_count': 3, 'fill_with_npcs': True, 'stake': 0})
        data = created.get('room', created)
        room = framework.get_room(data['room_id'])
        self.assertEqual(len(room['participants']), 3)
        self.assertEqual(sum(p['participant_kind'] == 'system_npc' for p in room['participants']), 2)
        self.assertEqual(room['status'], 'playing')
        self.rpc('resign', {'room_id': room['room_id']})

    def test_guide_is_on_demand_and_shared_schema_stays_small(self):
        guide_path = Path(__file__).resolve().parents[1] / 'vendor/duel/docs/KA_WU_XING.md'
        # In isolated paired worktrees vendor is intentionally not replaced.
        source = Path(__import__('app').__file__).resolve().parent.parent / 'docs/KA_WU_XING.md'
        original = Path.read_text
        def read(path, *args, **kwargs):
            return original(source if path == guide_path else path, *args, **kwargs)
        with patch.object(Path, 'read_text', read):
            guide = json.loads(server._tool_get_guide({'game': 'duel:ka_wu_xing'}))['guide']
        self.assertIn('ka_wu_xing', guide)
        self.assertIn('liang:', guide)
        for option in OPTIONS:
            self.assertIn(option, guide)
        self.assertNotIn('明确不支持', guide)
        self.assertIn('next_round:', guide)
        self.assertNotIn('kong_ledger', server.DUEL_GUIDE)
        for tools in (server._PLATFORM_TOOLS, server._KELIVO_PLATFORM_TOOLS):
            serialized = json.dumps(tools)
            self.assertNotIn('declare_waits', serialized)
            self.assertNotIn('kong_ledger', serialized)
            for option in OPTIONS:
                self.assertNotIn('"' + option + '"', serialized)

    def create_three_ai_room(self, options=None, stake=0):
        with server._db_connect() as conn:
            conn.execute("INSERT INTO toy_users(id,username,is_ai) VALUES(404,'third-ai',1)")
            third = server._issue_initial_account_token_in_transaction(
                conn, dict(conn.execute('SELECT * FROM toy_users WHERE id=404').fetchone()))
        self.tokens = {'202': self.token, '303': self.outsider, '404': third}
        params = {'game_type': 'ka_wu_xing', 'target_player_count': 3, 'stake': stake}
        if options is not None:
            params['game_options'] = options
        created = self.rpc('invite', params)
        rid = created.get('room', created)['room_id']
        room = framework.get_room(rid)
        for token in (self.outsider, third):
            self.rpc('join', {'invite_code': room['invite_code']}, token=token, auth='bearer_token')
        self.rpc('start', {'room_id': rid})
        return framework.get_room(rid)

    def fresh_process_projection(self, room, player_id):
        # The child imports the real framework with a distinct interpreter and
        # reads only this test's SQLite, so no in-memory room cache can mask loss.
        code = ("from app import framework; import json,sys; "
                "r=framework.get_room(sys.argv[1]); "
                "print(json.dumps(framework.project_room_for_viewer(r,sys.argv[2])))")
        env = {**os.environ, 'DUEL_DB_PATH': str(database.DB_PATH),
               'PYTHONPATH': os.pathsep.join([str(Path(__import__('app').__file__).resolve().parent.parent),
                                             os.environ.get('PYTHONPATH', '')])}
        loaded = subprocess.run([sys.executable, '-c', code, room['room_id'], player_id],
                                env=env, capture_output=True, text=True, check=True, timeout=30)
        return json.loads(loaded.stdout)

    def test_all_options_cross_account_invite_persist_reload_and_private_actions(self):
        room = self.create_three_ai_room(OPTIONS)
        self.assertEqual(room['game_options'], OPTIONS)
        self.assertEqual(room['board_state']['options'], OPTIONS)
        for pid, token in self.tokens.items():
            restored = self.fresh_process_projection(room, pid)
            current = framework.project_room_for_viewer(room, pid)
            self.assertEqual(restored['private_state'], current['private_state'])
            self.assertEqual(restored['game_options'], OPTIONS)
            snapshot = self.rpc('state', {'room_id': room['room_id'], 'full_state': True,
                                          'wait': False}, token=token)['snapshot']
            self.assertEqual(snapshot['private_state']['hand'], current['private_state']['hand'])
            encoded = json.dumps(snapshot)
            self.assertNotIn('"wall":', encoded)
            self.assertNotIn('"hands":', encoded)
            self.assertEqual({t['id'] for t in snapshot['private_state']['hand']},
                             {t['id'] for t in room['board_state']['hands'][pid]})
        actor = room['current_player_id']
        action = GAMES['ka_wu_xing'].choose_local_npc_action(
            room['board_state'], {'player_id': actor}, room['participants'])
        self.rpc('move', {'room_id': room['room_id'], 'revision': room['revision'],
                          'move': action, 'game_options': {'joker': 'none', 'rounds': 1}},
                 token=self.tokens[actor], auth='bearer_token', expect_ok=False)
        self.assertEqual(framework.get_room(room['room_id'])['revision'], room['revision'])
        self.rpc('move', {'room_id': room['room_id'], 'revision': room['revision'],
                          'move': action}, token=self.tokens[actor], auth='bearer_token')
        self.assertEqual(framework.get_room(room['room_id'])['game_options'], OPTIONS)
        self.rpc('resign', {'room_id': room['room_id']})

    def test_all_options_ordinary_new_and_npc_fill_persist(self):
        with patch.object(main, 'select_personas', return_value=[
                NpcPersona(f'kwx-options-{i}', f'规则NPC{i}', 'isolated') for i in range(2)]):
            created = self.rpc('new', {'game_type': 'ka_wu_xing', 'include_human': False,
                                      'target_player_count': 3, 'fill_with_npcs': True,
                                      'stake': 0, 'game_options': OPTIONS})
        room = framework.get_room(created.get('room', created)['room_id'])
        self.assertEqual(room['game_options'], OPTIONS)
        self.assertEqual(sum(p['participant_kind'] == 'system_npc' for p in room['participants']), 2)
        self.assertEqual(self.fresh_process_projection(room, '202')['game_options'], OPTIONS)
        self.rpc('resign', {'room_id': room['room_id']})

    def test_invalid_options_are_rejected_by_authenticated_root_without_rooms(self):
        for options in ({'double_hu': 1}, {'double_hu': 'true'}, {'piao': True},
                        {'piao': 2}, {'horse_count': 2}, {'rounds': 2},
                        {'joker': 'wild'}, {'unexpected': True}):
            for action in ('new', 'invite'):
                with self.subTest(options=options, action=action):
                    params = {'game_type': 'ka_wu_xing', 'target_player_count': 3,
                              'game_options': options}
                    if action == 'new':
                        params.update(include_human=False, fill_with_npcs=True)
                    with patch.object(main, 'select_personas', return_value=[
                            NpcPersona(f'kwx-invalid-{i}', f'规则NPC{i}', 'isolated') for i in range(2)]):
                        error = self.rpc(action, params, expect_ok=False)
                    message = error['result']['content'][0]['text']
                    self.assertIn('未知' if 'unexpected' in options else next(iter(options)), message)
        self.rpc('invite', {'game_type': 'mahjong', 'target_player_count': 4,
                            'game_options': {'double_hu': True}}, expect_ok=False)
        with database.connect() as conn:
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM rooms').fetchone()[0], 0)

    def test_four_rounds_root_reconnect_and_wallet_is_settled_only_once(self):
        room = self.create_three_ai_room({**OPTIONS, 'joker': 'none'}, stake=1)
        rid = room['room_id']
        order = room['board_state']['participant_order']
        dealer = room['current_player_id']
        losers = [p for p in order if p != dealer]
        with database.write_transaction() as conn:
            for pid, balance in zip(losers, (1, 0)):
                wallet = chips._ensure_wallet(conn, 'ai', pid)
                conn.execute('UPDATE chip_wallets SET balance=? WHERE id=?', (balance, wallet['id']))
            ledger_before = conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0]
        for number in range(1, 5):
            room = framework.get_room(rid)
            state = room['board_state']
            # A deterministic exhaustive-draw fixture in the disposable room:
            # move remaining wall tiles into public discards, preserving all 84
            # physical tile IDs. The lifecycle, authenticated actions, persistence
            # and settlement still pass through the real root and Duel handlers.
            state['discards'][dealer].extend(state['wall'])
            state['wall'] = []
            state['revealed'] = {p: {'hand': list(state['hands'][p]), 'waits': ['J1']}
                                 for p in losers}
            with database.write_transaction() as conn:
                conn.execute('UPDATE rooms SET board_state=? WHERE room_id=?', (json.dumps(state), rid))
            for _ in range(4):
                room = framework.get_room(rid)
                if room['board_state']['phase'] in ('round_end', 'finished'):
                    break
                pid = room['current_player_id']
                legal = GAMES['ka_wu_xing'].legal_actions_for(room['board_state'], pid)
                chosen = next(a for a in legal if a['kind'] in ('discard', 'pass'))
                self.rpc('move', {'room_id': rid, 'revision': room['revision'],
                                  'move': {'action': 'act', 'action_id': chosen['action_id']}},
                         token=self.tokens[pid], auth='bearer_token')
            room = framework.get_room(rid)
            expected = {p: (-number if p in losers else 2 * number) for p in order}
            self.assertEqual(room['board_state']['cumulative_scores'], expected)
            self.assertEqual(len(room['board_state']['round_results']), number)
            if number < 4:
                self.assertEqual(room['status'], 'playing')
                self.assertEqual(room['board_state']['phase'], 'round_end')
                with database.connect() as conn:
                    self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], ledger_before)
                pid = room['current_player_id']
                resumed = self.fresh_process_projection(room, pid)
                self.assertEqual(resumed['board_state']['cumulative_scores'], expected)
                self.assertEqual(resumed['private_state'], framework.project_room_for_viewer(room, pid)['private_state'])
                action = resumed['private_state']['legal_actions'][0]
                self.assertTrue(action['action_id'].startswith('next_round:'))
                args = {'room_id': rid, 'revision': room['revision'],
                        'move': {'action': 'act', 'action_id': action['action_id']}}
                self.rpc('move', args, token=self.tokens[pid])
                self.rpc('move', args, token=self.tokens[pid], expect_ok=False)
        self.assertEqual(room['status'], 'finished')
        self.assertEqual(room['result']['requested_settlement_deltas'], expected)
        actual = {dealer: 1, losers[0]: -1, losers[1]: 0}
        self.assertEqual(room['result']['settlement_deltas'], actual)
        with database.write_transaction() as conn:
            before = conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0]
            self.assertFalse(chips.settle_duel_room(conn, room))
            self.assertEqual(conn.execute('SELECT COUNT(*) FROM chip_ledger').fetchone()[0], before)
        for pid, token in self.tokens.items():
            self.rpc('state', {'room_id': rid, 'full_state': True, 'wait': False}, token=token)
        self.assertEqual(framework.get_room(rid)['result']['settlement_deltas'], actual)

    def test_double_hu_root_cross_account_reconnect_liability_and_capped_wallet(self):
        room = self.create_three_ai_room({**OPTIONS, 'rounds': 1}, stake=1)
        game = GAMES['ka_wu_xing']
        order = room['board_state']['participant_order']
        source, first, second = order
        state = game.configure(game._empty_state(order), {**OPTIONS, 'rounds': 1})
        state.update(phase='discard', turn_player_id=source, dealer_player_id=source)
        state['flow']['phase'] = 'discard'
        stock = game.build_wall()
        def take(code):
            tile = next(t for t in stock if t['code'] == code)
            stock.remove(tile)
            return tile
        gap = 'B1 B2 B3 T1 T2 T3 T7 T8 T9 B4 B6 J1 J1'.split()
        for pid in (first, second):
            state['hands'][pid] = [take(code) for code in gap]
            state['revealed'][pid] = {'hand': list(state['hands'][pid]), 'waits': ['B5']}
        discard = take('B5')
        state['hands'][source] = [discard, *stock[:13]]
        del stock[:13]
        state['wall'] = stock
        state['drawn_tile_id'] = state['hands'][source][-1]['id']
        with database.write_transaction() as conn:
            conn.execute('UPDATE rooms SET board_state=?,current_player_id=? WHERE room_id=?',
                         (json.dumps(state), source, room['room_id']))
            wallet = chips._ensure_wallet(conn, 'ai', source)
            conn.execute('UPDATE chip_wallets SET balance=3 WHERE id=?', (wallet['id'],))
        self.rpc('move', {'room_id': room['room_id'], 'revision': room['revision'],
                          'move': {'action': 'act', 'action_id': 'discard:' + discard['id']}},
                 token=self.tokens[source])
        for pid in (first, second):
            room = framework.get_room(room['room_id'])
            self.assertEqual(room['status'], 'playing')
            self.assertEqual(room['current_player_id'], pid)
            restored = self.fresh_process_projection(room, pid)
            self.assertNotIn('"wall":', json.dumps(restored))
            action = next(a for a in restored['private_state']['legal_actions'] if a['kind'] == 'hu')
            self.rpc('move', {'room_id': room['room_id'], 'revision': room['revision'],
                              'move': {'action': 'act', 'action_id': action['action_id']}},
                     token=self.tokens[pid], auth='bearer_token')
            if pid == first:
                pending = framework.get_room(room['room_id'])
                membership = [(p['player_id'], p['active'], p['join_status']) for p in pending['participants']]
                for quitting in (source, second):
                    for action_name in ('resign', 'leave'):
                        self.rpc(action_name, {'room_id': room['room_id']},
                                 token=self.tokens[quitting], expect_ok=False)
                        unchanged = framework.get_room(room['room_id'])
                        self.assertEqual(unchanged['revision'], pending['revision'])
                        self.assertEqual(unchanged['status'], 'playing')
                        self.assertEqual([(p['player_id'], p['active'], p['join_status'])
                                          for p in unchanged['participants']], membership)
        room = framework.get_room(room['room_id'])
        result = room['result']
        self.assertEqual(room['status'], 'finished')
        self.assertEqual(result['winner_player_ids'], [first, second])
        self.assertEqual(result['source_player_id'], source)
        for pid in (first, second):
            self.assertEqual(result['score_breakdown']['piao'][pid], 6)
        self.assertEqual(result['score_breakdown']['piao'][source], -12)
        self.assertEqual(set(result['score_breakdown']['horse'].values()), {0})
        actual = result['settlement_deltas']
        self.assertEqual(actual[source], -3)
        self.assertEqual(actual[first] + actual[second], 3)
        self.assertGreater(result['requested_settlement_deltas'][first], actual[first])
        saved = room['board_state']
        zones = (saved['wall'] + sum(saved['hands'].values(), []) + sum(saved['discards'].values(), []) +
                 [t for melds in saved['melds'].values() for meld in melds for t in meld['tiles']] +
                 saved['robbed_kong_tiles'] + saved.get('horse_tiles', []))
        self.assertEqual(len(zones), 84)
        self.assertEqual(len({t['id'] for t in zones}), 84)
        self.assertEqual(sum(t['id'] == discard['id'] for t in saved['discards'][source]), 1)
