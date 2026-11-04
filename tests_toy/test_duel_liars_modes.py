"""Keep dice room options through the authenticated thin MCP bridge."""
import unittest
import json

import server


class DuelLiarsModesTests(unittest.TestCase):
    def test_only_dice_new_and_invite_forward_rule_options(self):
        options = {'mode':'party', 'literal_dice':True, 'team_mode':'pairs'}
        for action in ('new','invite'):
            with self.subTest(action=action):
                arguments = {'action':action,'game_type':'liars_dice','player_id':'spoofed',
                             'stake':0,'target_player_count':4,'move':options}
                result = server._prepare_duel_payload(arguments, trusted_player_id='ai-1',
                    trusted_opponent_id='human-1', force_opponent=True)
                self.assertEqual(result['move'],options)
                self.assertEqual(result['player_id'],'ai-1')
                self.assertEqual(options,arguments['move'])
                other = server._prepare_duel_payload({**arguments,'game_type':'uno'})
                self.assertNotIn('move',other)
                old = server._prepare_duel_payload({k:v for k,v in arguments.items() if k!='move'})
                self.assertNotIn('move',old)

    def test_seat_command_uses_authenticated_identity_and_avatar(self):
        command={'action':'move','room_id':'ABCDEFGH','revision':2,
                 'player_id':'spoofed','display_avatar':{'type':'emoji','value':'fake'},
                 'move':{'action':'choose_team_seat','team':'blue','seat':3}}
        avatar={'type':'emoji','value':'🦊'}
        result=server._prepare_duel_payload(command,trusted_player_id='ai-1',
                                           trusted_display_name='小机一号',trusted_avatar=avatar)
        self.assertEqual(result['player_id'],'ai-1')
        self.assertEqual(result['move'],command['move'])
        self.assertEqual(result['display_avatar'],avatar)
        self.assertEqual(result['display_name'],'小机一号')
        self.assertNotIn('display_avatar',server._prepare_duel_payload(command))

    def test_options_and_scores_documented_without_growing_shared_schema(self):
        guide = json.loads(server._tool_get_guide({'game':'duel:liars_dice'}))['guide']
        self.assertIn('duel:liars_dice',server.DUEL_GUIDE)
        for word in ('team_mode','literal_dice','普通1仍百搭','胜方不加分','接力淘汰','raw_multiplier','cup_penalty','surrender_no_reroll'):
            self.assertIn(word,guide)
        self.assertNotIn('cup_penalty',server.DUEL_GUIDE)
        self.assertIn('新桌胜方+1、败方按倍率扣1/2/4分', server.DUEL_GUIDE)
        self.assertNotIn('基础败方本局-1', server.DUEL_GUIDE)
        self.assertIn('仅共享队分：新桌胜方+1', guide)
        self.assertNotIn('败者队伍基础扣一次1分', guide)
        for tools in (server._PLATFORM_TOOLS,server._KELIVO_PLATFORM_TOOLS):
            play = next(t for t in tools if t['name']=='play')
            properties = play['inputSchema']['properties']['params'].get('properties',{})
            self.assertNotIn('team_mode',properties)
            self.assertNotIn('literal_dice',properties)


if __name__ == '__main__':
    unittest.main()
