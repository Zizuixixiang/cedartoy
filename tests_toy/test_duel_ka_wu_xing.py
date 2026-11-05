"""Real authenticated root MCP -> isolated Duel ASGI, three independent AIs.

Run with PYTHONPATH pointing to the paired Duel worktree during development.
Only temporary account/session/room databases are written.
"""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import server
from tests_toy import test_duel_new_games_integration as integration
from app import framework, main
from app.npc_personas import NpcPersona
from app.games import GAMES


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
        self.assertIn('不支持一炮双响', guide)
        self.assertNotIn('kong_ledger', server.DUEL_GUIDE)
        for tools in (server._PLATFORM_TOOLS, server._KELIVO_PLATFORM_TOOLS):
            serialized = json.dumps(tools)
            self.assertNotIn('declare_waits', serialized)
            self.assertNotIn('kong_ledger', serialized)
