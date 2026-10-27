"""Scoped Yijian dashboard grants: temporary accounts and loopback services only."""
import json
import os
import unittest
from unittest.mock import patch

from tests_toy.test_autochess_web import Fixture, NODE_OK, ROOT


@unittest.skipUnless(NODE_OK and (ROOT / 'vendor/autochess/node_modules/tsx').exists(),
                     'needs the local Node >=22 runtime and locked dependencies')
class YijianAdminStatsTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Fixture()
        self.fixture.__enter__()
        self.addCleanup(self.fixture.__exit__, None, None, None)
        allowed = patch.dict(os.environ, {'AUTOCHESS_AUTHOR_USER_IDS': '101,202'})
        allowed.start()
        self.addCleanup(allowed.stop)

    def mcp(self, who=None, *, name='play', arguments=None):
        f = self.fixture
        headers = {'Authorization': 'Bearer ' + f.tokens[who]} if who else {}
        payload = {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call',
                   'params': {'name': name, 'arguments': arguments or {}}}
        response = f.http.post(f.origin + '/mcp', json=payload, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()['result']

    def web_access(self, who):
        f = self.fixture
        result = f.http.post(f.origin + '/autochess/api/stats',
                             headers={'Authorization': 'Bearer ' + f.tokens[who]},
                             json={'op': 'access'})
        self.assertEqual(result.status_code, 200)
        return result.json()

    def test_human_dashboard_and_machine_mcp_both_read_same_aggregate(self):
        f = self.fixture
        self.assertEqual(self.web_access('101'), {'allowed': True})
        self.assertEqual(self.web_access('303'), {'allowed': False})
        self.assertEqual(f.call('stats', '202').status_code, 403)
        # The AI has its own root MCP, not a human browser login or another site account.
        made = f.call('create', '101', requestId='author-stats-create', pack='ai', mode='duel').json()
        self.assertIn('roomId', made)
        arguments = {'game': 'autochess', 'action': 'stats', 'params': {'range': '1h'}}
        machine = self.mcp('202', arguments=arguments)
        human = self.mcp('101', arguments=arguments)
        self.assertFalse(machine['isError'], machine)
        self.assertFalse(human['isError'], human)
        machine_stats = json.loads(machine['content'][0]['text'])
        human_stats = json.loads(human['content'][0]['text'])
        self.assertEqual(machine_stats['created'], 1)
        self.assertEqual(human_stats['created'], machine_stats['created'])
        self.assertEqual(set(machine_stats), {'range', 'generated_at', 'active_humans', 'active_machines',
                                              'created', 'started', 'finished'})
        self.assertNotIn('roomId', json.dumps(machine_stats))
        self.assertNotIn('playerId', json.dumps(machine_stats))

    def test_guests_site_admins_and_forged_player_id_are_denied(self):
        for identity, player_id in [(None, '101'), ('303', '101'), ('404', '202')]:
            args = {'game': 'autochess', 'action': 'stats', 'player_id': player_id,
                    'params': {'range': '1h', 'player_id': '101', 'is_admin': True}}
            result = self.mcp(identity, arguments=args)
            self.assertTrue(result['isError'], result)
            self.assertIn('授权', result['content'][0]['text'])
        # A grant must not follow the site-wide is_admin bit or any old identity.
        with patch.dict(os.environ, {'AUTOCHESS_AUTHOR_USER_IDS': ''}):
            self.assertTrue(self.mcp('202', arguments={'game': 'autochess', 'action': 'stats'})['isError'])
            self.assertEqual(self.web_access('101'), {'allowed': False})
        bad = self.mcp('202', arguments={'game': 'autochess', 'action': 'stats',
                                        'params': {'range': '100y'}})
        self.assertTrue(bad['isError'])
        self.assertIn('时段', bad['content'][0]['text'])

    def test_guide_only_tells_authorized_account_about_stats(self):
        for who, allowed in [('101', True), ('202', True), ('303', False), (None, False)]:
            result = self.mcp(who, name='get_guide', arguments={'game': 'autochess'})
            self.assertFalse(result['isError'], result)
            guide = json.loads(result['content'][0]['text'])['guide']
            self.assertIn('江川雪', guide)
            self.assertEqual('【本作管理统计·只读】' in guide, allowed)
            self.assertEqual('action="stats"' in guide, allowed)


if __name__ == '__main__':
    unittest.main()
