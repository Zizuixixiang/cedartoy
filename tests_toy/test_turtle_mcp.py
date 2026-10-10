"""Root MCP discovery and soup forwarding; backend HTTP tests use a temp DB."""
import json
import unittest
from contextlib import ExitStack
from unittest.mock import Mock, patch

import server


class TurtleMcpTests(unittest.TestCase):
    def setUp(self):
        stack = ExitStack()
        self.addCleanup(stack.close)
        # Keep root account activity, announcements and reminders off live DBs.
        for name, value in [
            ('_authenticated_ai_player_id', None),
            ('_duel_unread_request_reminder', ''),
            ('_mcp_forced_announcement', ''),
            ('_game_maintenance', None),
            ('_anti_addiction_context', None),
            ('_anti_addiction_preflight', None),
        ]:
            stack.enter_context(patch.object(server, name, return_value=value))
        stack.enter_context(patch.object(
            server, '_current_account', return_value={'id': 102, 'is_ai': 1},
        ))
        stack.enter_context(patch.object(
            server, '_finalize_play_response', side_effect=lambda response, **kwargs: response,
        ))

    def call(self, name, arguments, **identity):
        response = server._handle_root_mcp({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': name, 'arguments': arguments},
        }, **identity)
        self.assertFalse(response['result']['isError'], response)
        return json.loads(response['result']['content'][0]['text'])

    def test_schema_and_guide_expose_lock_and_ownership(self):
        for client in ['', 'Kelivo/1.2.6']:
            listed = server._handle_root_mcp(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, user_agent=client,
            )
            play = next(t for t in listed['result']['tools'] if t['name'] == 'play')
            fields = play['inputSchema']['properties']['params']['properties']
            if not client:
                # Ordinary clients get business parameters from the Guide.
                self.assertNotIn('is_locked', fields)
                self.assertNotIn('include_finished', fields)
                continue
            field = fields['is_locked']
            self.assertEqual(field['type'], 'boolean')
            for text in ['仅海龟汤', 'create_random/create_custom', 'true', '创建者本人', '当前同一绑定关系', '人类/小机', '默认 false']:
                self.assertIn(text, field['description'])
            finished = fields['include_finished']
            self.assertEqual(finished['type'], 'boolean')
            for text in ['仅 turtle_soup my_rooms', '默认 false', 'true', '已结束']:
                self.assertIn(text, finished['description'])
        guide = self.call('get_guide', {'game': 'turtle_soup'})
        for action in ['create_random', 'create_custom']:
            self.assertIn('is_locked(可选，默认 false', guide['actions'][action])
            self.assertIn('true', guide['actions'][action])
        for text in ['is_locked', 'is_mine', '自己的房间优先', '不含绑定账号', '需认证身份']:
            self.assertIn(text, guide['actions']['list_rooms'])
        self.assertNotIn('finished', guide['actions']['list_rooms'])
        self.assertIn('公共大厅', guide['actions']['list_rooms'])
        for text in ['include_finished', '默认 false', '当前有效绑定人类', '不含同绑定其他小机', 'creator_name', 'creator_type', 'self', 'human', '最近活跃']:
            self.assertIn(text, guide['actions']['my_rooms'])
        self.assertIn('优先用 my_rooms，无需先扫大厅', '\n'.join(guide['notes']))

    def test_create_params_and_current_token_reach_soup(self):
        response = Mock(status_code=200)
        response.json.return_value = {'id': 'ROOM'}
        with patch.object(server.httpx, 'post', return_value=response) as post:
            for identity in [{'path_token': 'ai-a'}, {'bearer_token': 'ai-b'}]:
                for action in ['create_random', 'create_custom']:
                    for lock_params in [{}, {'is_locked': False}, {'is_locked': True}]:
                        params = {'surface': 'surface', 'answer': 'answer', 'path_token': 'spoofed', **lock_params}
                        self.call('play', {'game': 'turtle_soup', 'action': action, 'params': params}, **identity)
                        payload = post.call_args.kwargs['json']
                        self.assertEqual(post.call_args.args[0], f'{server.SOUP_BASE}/mcp/play')
                        self.assertEqual(payload['path_token'], next(iter(identity.values())))
                        self.assertEqual(payload['action'], action)
                        self.assertEqual(payload['surface'], 'surface')
                        self.assertEqual(payload['answer'], 'answer')
                        if lock_params:
                            self.assertIs(payload['is_locked'], lock_params['is_locked'])
                        else:
                            self.assertNotIn('is_locked', payload)

    def test_list_preserves_fields_order_and_forwards_each_identity(self):
        with patch.object(server.httpx, 'post') as post:
            for token in ['ai-a', 'ai-b', 'ai-a']:
                rows = [
                    {'id': token, 'is_mine': 1, 'is_locked': 1},
                    {'id': 'other', 'is_mine': 0, 'is_locked': 0},
                ]
                post.return_value = Mock(status_code=200)
                post.return_value.json.return_value = rows
                result = self.call('play', {
                    'game': 'turtle_soup', 'action': 'list_rooms',
                    'params': {'path_token': 'spoofed'},
                }, bearer_token=token)
                self.assertEqual(post.call_args.kwargs['json']['path_token'], token)
                self.assertEqual(result, rows)

    def test_my_rooms_forwards_include_finished_and_authenticated_identity(self):
        rows = [{'id': 'HUMAN', 'creator_name': '人类', 'creator_type': 'human', 'is_locked': 1}]
        response = Mock(status_code=200)
        response.json.return_value = rows
        with patch.object(server.httpx, 'post', return_value=response) as post:
            for identity in [{'path_token': 'ai-a'}, {'bearer_token': 'ai-b'}, {'path_token': 'ai-a'}]:
                for params in [{}, {'include_finished': False}, {'include_finished': True}]:
                    result = self.call('play', {
                        'game': 'turtle_soup', 'action': 'my_rooms',
                        'params': {'path_token': 'spoofed', **params},
                    }, **identity)
                    payload = post.call_args.kwargs['json']
                    self.assertEqual(post.call_args.args[0], f'{server.SOUP_BASE}/mcp/play')
                    self.assertEqual(payload['action'], 'my_rooms')
                    self.assertEqual(payload['path_token'], next(iter(identity.values())))
                    if params:
                        self.assertIs(payload['include_finished'], params['include_finished'])
                    else:
                        self.assertNotIn('include_finished', payload)
                    self.assertEqual(result, rows)

    def test_hint_reveal_guide_and_log_id_schema(self):
        guide = self.call('get_guide', {'game': 'turtle_soup'})
        expected = {
            'ask': 'room_id, content -> 向裁判提出海龟汤是/否问题，不是群聊发言；content 最多 200 字；返回本次结果和新日志 logs_since_last_own_action。',
            'hint_request': 'room_id -> 主动请求提示，直接返回；每玩家每房最多3次。',
            'view_auto_hint': 'room_id, log_id -> 查看收到的自动提示；通知只出现一次。',
            'reveal_answer': '达到当前查看门槛（默认 50 题）后，传 room_id 查看；查看后不能再进入或操作本房间。',
            'status': 'room_id, log_limit(可选) -> 查看完整汤面和最新 N 条日志；自动提示和汤底资格只通知一次；未查看自动提示不泄露正文。',
        }
        for action, description in expected.items():
            self.assertEqual(guide['actions'][action], description)
        for obsolete in ['下一次 ask', 'next_ask', '兼容', 'confirm_reveal', 'auto_hint_log_id', 'accept_auto_hint', 'hint_respond']:
            self.assertNotIn(obsolete, str(guide))
        hint_notes = [note for note in guide['notes'] if any(action in note for action in ['view_auto_hint', 'hint_request', 'reveal_answer'])]
        # The three action descriptions above retain the parameters, conditions
        # and consequences; notes need not repeat their routing summary.
        self.assertEqual(hint_notes, [])
        self.assertTrue(any('线索汤格式' in note for note in guide['notes']))
        for client in ['', 'Kelivo/1.2.6']:
            listed = server._handle_root_mcp(
                {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/list'}, user_agent=client,
            )
            play = next(t for t in listed['result']['tools'] if t['name'] == 'play')
            fields = play['inputSchema']['properties']['params']['properties']
            if client:
                self.assertEqual(fields['log_id']['type'], 'integer')
                self.assertIn('view_auto_hint', fields['log_id']['description'])
            else:
                self.assertNotIn('log_id', fields)
            for name in ['auto_hint_log_id', 'accept_auto_hint', 'accept_auto_hint_log_id', 'reject_auto_hint_log_id', 'confirm_reveal', 'confirm_hint']:
                self.assertNotIn(name, json.dumps(play['inputSchema']))

    def test_guide_keeps_puzzle_discovery_in_actions(self):
        guide = self.call('get_guide', {'game': 'turtle_soup'})
        expected = {
            'list_puzzles': ['page/page_size', '默认20题/页', 'q 搜标题', 'tag 单标签',
                             'tags 多标签', '逗号/空格分隔，AND', 'items[id/title/tags]',
                             '分页信息', '不返回汤面/汤底'],
            'get_puzzle': ['puzzle_id', '单题汤面', 'id/title/surface/tags', '不返回汤底'],
            'create_random': ['puzzle_id 指定题目', '不传则随机抽题', '大多微恐'],
        }
        for action, knowledge in expected.items():
            with self.subTest(action=action):
                for text in knowledge:
                    self.assertIn(text, guide['actions'][action])
                self.assertNotIn(action, '\n'.join(guide['notes']))

    def test_guide_keeps_play_semantics_limits_and_clue_format(self):
        guide = self.call('get_guide', {'game': 'turtle_soup'})
        self.assertIn('业务参数放入 params 对象', guide['call_format'])
        self.assertIn('play(game="turtle_soup", action="ask", params=', guide['call_format'])
        expected = {
            'register': ['username, password', 'avatar', '默认🤖', '仅注册账号', '{token}', '持久身份'],
            'ask': ['room_id, content', '是/否问题', '不是群聊', '最多 200 字', 'logs_since_last_own_action'],
            'guess': ['room_id, content', '最多 1000 字', '完整汤底还原', '是/否问题请用 ask', '超长'],
            'create_custom': ['title(可选，最多20字)', 'surface(最多1000字)',
                              'answer(最多3000字)', 'tags(可选)', '线索汤格式见 notes'],
            'generate': ['style(可选)', 'title/surface/answer 预览，不开房', 'title 最多20字',
                         'surface 最多1000字', 'answer 最多3000字',
                         'cozy/absurd/mystery/fantasy/history/scifi/horror',
                         '质量不稳定', '确认内容后再用 create_custom'],
            'join': ['room_id', '进行中的房间'],
            'close_room': ['room_id', '自己创建的房间'],
            'note_list': ['room_id', '记事本'],
            'note_add': ['room_id, content', '自己的记事', '最多 50 字', '不含记事内容',
                         '【系统提示】记事本有新记录'],
            'note_edit': ['note_id, content', '自己的记事', '最多 50 字', '不写公屏日志'],
            'note_delete': ['note_id', '自己的记事', '不写公屏日志'],
        }
        for action, knowledge in expected.items():
            with self.subTest(action=action):
                for text in knowledge:
                    self.assertIn(text, guide['actions'][action])
        notes = '\n'.join(guide['notes'])
        for text in [
            '绑定关系实时查询', '解绑后对应人类房间不再返回', '无绑定时只返回自己的房间',
            '锁房仅创建者本人和当前同一绑定关系下的人类/小机可进入',
            'logs/status/logs_since_last_own_action 是公开对局记录',
            '同步其他玩家动作', '不要把它当作需要回复的群聊消息',
            '在完整 answer 内写【线索公布】公开线索内容【线索公布结束】',
            '触发后系统只公布两个标记之间的内容',
        ]:
            self.assertIn(text, notes)
        # The full clue syntax has one accessible home, referenced by create_custom.
        self.assertEqual(json.dumps(guide, ensure_ascii=False).count('【线索公布】'), 1)
        self.assertEqual(json.dumps(guide, ensure_ascii=False).count('【线索公布结束】'), 1)

    def test_guide_omits_platform_announcements(self):
        guide = self.call('get_guide', {'game': 'turtle_soup'})
        self.assertNotIn('platform_announcements', guide)
        self.assertEqual(guide, server._turtle_soup_guide())

    def test_independent_view_actions_forward_params_and_authenticated_identity(self):
        response = Mock(status_code=200)
        response.json.return_value = {'answer_revealed': True}
        with patch.object(server.httpx, 'post', return_value=response) as post:
            for identity in [{'path_token': 'ai-a'}, {'bearer_token': 'ai-b'}]:
                for action, params in [('reveal_answer', {'room_id': 'ROOM'}), ('view_auto_hint', {'room_id': 'ROOM', 'log_id': 7})]:
                    self.call('play', {'game': 'turtle_soup', 'action': action, 'params': {**params, 'path_token': 'spoofed'}}, **identity)
                    payload = post.call_args.kwargs['json']
                    self.assertEqual(payload['action'], action)
                    self.assertEqual(payload['path_token'], next(iter(identity.values())))
                    for key, value in params.items():
                        self.assertEqual(payload[key], value)
                    self.assertNotIn('confirm_reveal', payload)
