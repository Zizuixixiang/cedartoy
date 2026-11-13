"""MCP notification/reveal flows over HTTP and disposable SQLite databases."""
import asyncio
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest.mock import AsyncMock, patch

import test_locked_rooms as fixtures

from test_locked_rooms import database, game, mcp_app


class McpHintsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.LockedRoomsTests.asyncSetUp
    asyncTearDown = fixtures.LockedRoomsTests.asyncTearDown
    headers = fixtures.LockedRoomsTests.headers
    create = fixtures.LockedRoomsTests.create
    mcp = fixtures.LockedRoomsTests.mcp

    async def seed_asks(self, rid, count):
        db = await database.get_db()
        try:
            await db.executemany(
                "INSERT INTO game_logs(room_id, type, content) VALUES (?, 'ask', '问题')",
                [(rid,)] * count,
            )
            await db.commit()
        finally:
            await db.close()

    async def hint(self, rid, *, kind='auto_hint', judgment=None):
        return await database.execute(
            'INSERT INTO game_logs(room_id, type, judgment, content, hint_text) VALUES (?, ?, ?, ?, ?)',
            (rid, kind, judgment, '秘密线索正文', '秘密线索正文'),
        )

    def ok(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def auto_logs(self, response):
        return [row for row in response['logs'] if row['type'] == 'auto_hint']

    async def status(self, rid, uid=102, **params):
        return self.ok(await self.mcp('status', rid, uid, **params))

    async def test_default_full_incremental_text_and_explicit_limits(self):
        rid = await self.create()
        for i in range(25):
            await database.execute(
                "INSERT INTO game_logs(room_id,player_id,type,content,judgment) VALUES (?,?,'ask',?,'yes')",
                (rid, self.players[103]['id'], f'共享问题{i}'),
            )
        before = await database.fetch_all('SELECT id FROM game_logs WHERE room_id=? ORDER BY id', (rid,))
        status = await self.status(rid)
        self.assertEqual([r['id'] for r in status['logs']], [r['id'] for r in before])
        self.assertNotIn('logs_truncated', status)
        self.assertIn('surface', status['room'])
        self.assertIn('created_at', status['logs'][0])
        self.assertEqual((await self.status(rid, log_limit=0))['logs'], [])
        short = await self.status(rid, log_limit=2)
        self.assertEqual([r['id'] for r in short['logs']], [r['id'] for r in before[-2:]])
        self.assertTrue(short['logs_truncated'])
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)):
            first = self.ok(await self.mcp('ask', rid, 102, content='本次独有问题'))
            history = first['logs_since_last_own_action']
            self.assertIsInstance(history, str)
            self.assertEqual(len(history.splitlines()), len(before))
            self.assertEqual(history.splitlines()[1:], [f"{self.players[103]['username']}：共享问题{i}→是" for i in range(25)])
            self.assertNotIn('本次独有问题', history)
            self.assertNotIn('logs_truncated', first)
            self.assertNotIn('room', first)
            self.assertEqual(first['room_id'], rid)
            for i in range(3):
                await database.execute("INSERT INTO game_logs(room_id,type,content,judgment) VALUES (?,'ask',?,'no')", (rid, f'新问题{i}'))
            next_ask = self.ok(await self.mcp('ask', rid, 102, content='后续独有问题', log_limit=2))
            self.assertEqual(next_ask['logs_since_last_own_action'].splitlines(), ['系统：新问题1→否', '系统：新问题2→否'])
            self.assertTrue(next_ask['logs_truncated'])
            zero = self.ok(await self.mcp('ask', rid, 102, content='不要历史', log_limit=0))
            self.assertEqual(zero['logs_since_last_own_action'], '')
            self.assertEqual(zero['judgment'], 'yes')

    def test_log_text_preserves_utterances_hint_events_and_line_boundaries(self):
        row = {'id': 7, 'player_id': 1, 'username': '阿狸', 'type': 'ask', 'content': '第一行\n第二行', 'judgment': 'partial'}
        self.assertEqual(mcp_app._log_text(row), '阿狸：第一行\\n第二行→是也不是')
        row.update(type='hint_offer', content='hint:30', hint_text='提示原文', judgment=None)
        self.assertEqual(mcp_app._log_text(row), '阿狸[提示]：提示原文')
        row.update(type='auto_hint', auto_hint_accepted=True)
        self.assertEqual(mcp_app._log_text(row), '自动提示[log_id=7，已查看]：提示原文')

    async def test_rest_ask_keeps_log_object_and_broadcast_structure(self):
        rid = await self.create()
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)), patch.object(game, 'broadcast', AsyncMock()) as broadcast:
            result = self.ok(await self.client.post('/soup/api/game/ask', headers=self.headers(103), json={'room_id': rid, 'content': '网页提问'}))
        self.assertEqual(set(result), {'id', 'room_id', 'player_id', 'type', 'content', 'judgment', 'hint_text', 'resolved', 'created_at', 'username', 'is_guest', 'is_ai'})
        self.assertEqual(broadcast.call_args.args, (rid, 'new_log', result))

    async def test_zero_or_short_history_keeps_hint_and_reveal_notifications(self):
        for limit in [0, 1]:
            with self.subTest(limit=limit):
                rid = await self.create(101 if limit == 0 else 201)
                lid = await self.hint(rid)
                await self.seed_asks(rid, 50)
                first = await self.status(rid, log_limit=limit)
                self.assertEqual(first['auto_hint_notifications'][0]['log_id'], lid)
                self.assertIn('answer_reveal_prompt', first)
                self.assertNotIn('秘密线索正文', json.dumps(first, ensure_ascii=False))
                repeated = await self.status(rid, log_limit=limit)
                self.assertNotIn('auto_hint_notifications', repeated)
                self.assertNotIn('answer_reveal_prompt', repeated)
                other = await self.status(rid, 103, log_limit=limit)
                self.assertEqual(other['auto_hint_notifications'][0]['log_id'], lid)
                self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))['hint_text'], '秘密线索正文')

    async def test_concurrent_zero_history_claims_omitted_hint_once(self):
        rid = await self.create()
        lid = await self.hint(rid)
        results = await asyncio.gather(*(self.status(rid, log_limit=0) for _ in range(4)))
        notifications = [n for r in results for n in r.get('auto_hint_notifications', [])]
        self.assertEqual([n['log_id'] for n in notifications], [lid])
        self.assertTrue(all(r['logs'] == [] for r in results))

    async def test_zero_history_ask_keeps_both_generated_hint_notifications(self):
        rid = await self.create()
        await self.seed_asks(rid, 29)
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes', 'clue': '问答秘密线索'})), patch.object(game.judge, 'generate_hint', AsyncMock(return_value='周期秘密提示')):
            result = self.ok(await self.mcp('ask', rid, 102, content='触发提示', log_limit=0))
        self.assertEqual(result['logs_since_last_own_action'], '')
        self.assertIn('log_id', result['auto_hint'])
        self.assertEqual(len(result['auto_hint_notifications']), 1)
        self.assertNotEqual(result['auto_hint']['log_id'], result['auto_hint_notifications'][0]['log_id'])
        for secret in ['问答秘密线索', '周期秘密提示']:
            self.assertNotIn(secret, json.dumps(result, ensure_ascii=False))
        self.assertNotIn('auto_hint_notification', json.dumps(await self.status(rid)))

    async def test_guess_opt_in_history_preserves_success_and_failure_results(self):
        rid = await self.create()
        await self.seed_asks(rid, 25)
        with patch.object(game.judge, 'judge_guess', AsyncMock(return_value={'success': False, 'score': 20})):
            first = self.ok(await self.mcp('guess', rid, 102, content='汤底'))
            self.assertNotIn('logs_since_last_own_action', first)
            self.assertFalse(first['correct'])
            self.assertEqual(first['score'], 20)
            self.assertEqual(first['result_log']['judgment'], 'guess_result')
            zero = self.ok(await self.mcp('guess', rid, 102, content='汤底', log_limit=0))
            self.assertEqual(zero['logs_since_last_own_action'], '')
            await self.seed_asks(rid, 3)
        with patch.object(game.judge, 'judge_guess', AsyncMock(return_value={'success': True, 'score': 100, 'answer': '公开汤底'})):
            won = self.ok(await self.mcp('guess', rid, 102, content='完整汤底', log_limit=2))
        self.assertTrue(won['correct'])
        self.assertEqual(won['result_log']['judgment'], 'game_over')
        self.assertIn('公开汤底', won['result_log']['content'])
        self.assertEqual(won['logs_since_last_own_action'].splitlines(), ['系统：问题→未判定'] * 2)
        self.assertTrue(won['logs_truncated'])
        self.assertNotIn('完整汤底', won['logs_since_last_own_action'])
        self.assertNotIn('公开汤底', won['logs_since_last_own_action'])

    async def test_invalid_limit_rejected_before_auth_judge_or_notification_writes(self):
        rid = await self.create()
        await self.seed_asks(rid, 50)
        await self.hint(rid)
        with patch.object(mcp_app, '_mcp_player', AsyncMock()) as auth, patch.object(game.judge, 'judge_ask', AsyncMock()) as ask, patch.object(game.judge, 'judge_guess', AsyncMock()) as guess:
            for action in ['ask', 'guess', 'status']:
                for limit in [-1, 1.5, 'invalid']:
                    response = await self.mcp(action, rid, 102, content='问题', log_limit=limit)
                    self.assertIn(response.status_code, [400, 422])
            auth.assert_not_awaited()
            ask.assert_not_awaited()
            guess.assert_not_awaited()
        self.assertEqual(await database.fetch_all('SELECT * FROM room_hint_views'), [])
        self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveal_prompts'), [])

    async def test_reveal_and_guess_publish_story_without_private_rules(self):
        rizi = (Path(__file__).parent / 'fixtures/rizi_answer.txt').read_text()
        public = rizi.split('**三、 核心判定引擎', 1)[0].strip()
        for owner, answer, expected in [(101, rizi, public), (201, '她离开只是为了保护家人。', '她离开只是为了保护家人。')]:
            with self.subTest(owner=owner):
                rid = await self.create(owner)
                await database.execute('UPDATE rooms SET answer=? WHERE id=?', (answer, rid))
                await self.seed_asks(rid, 49)
                self.assertEqual((await self.mcp('reveal_answer', rid, 102)).status_code, 400)
                await self.seed_asks(rid, 1)
                reveal = self.ok(await self.mcp('reveal_answer', rid, 102))
                self.assertEqual(reveal, {'answer': expected, 'answer_revealed': True})
                self.assertEqual((await self.mcp('ask', rid, 102, content='继续')).status_code, 400)
                self.assertNotEqual((await database.fetch_one('SELECT status FROM rooms WHERE id=?', (rid,)))['status'], 'finished')
                # Model output may paraphrase backend rules without headings.
                model_answer = '裁判必须检查四个条件并选线索' if owner == 101 else answer + '\n【通关判定条件】\n后台规则'
                with patch.object(game.judge, 'judge_guess', AsyncMock(return_value={'success': True, 'score': 100, 'answer': model_answer})), patch.object(game, 'broadcast', AsyncMock()) as broadcast:
                    response = self.ok(await self.client.post('/soup/api/game/guess', headers=self.headers(103), json={'room_id': rid, 'content': '完整故事还原'}))
                self.assertTrue(response['correct'])
                self.assertEqual(response['result_log']['content'], '还原度：100%\n' + expected)
                self.assertEqual(response['result_log']['judgment'], 'game_over')
                game_over = next(call.args[2] for call in broadcast.call_args_list if call.args[1] == 'game_over')
                self.assertEqual(game_over['answer'], expected)
                stored = await database.fetch_one('SELECT content FROM game_logs WHERE id=?', (response['result_log']['id'],))
                self.assertEqual(stored['content'], response['result_log']['content'])

    async def test_answer_prompt_persists_at_50_through_100_and_isolates_players_rooms(self):
        rid = await self.create()
        await self.seed_asks(rid, 49)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 1)
        first = await self.status(rid)
        self.assertEqual(first['answer_reveal_prompt']['ask_count'], 50)
        self.assertNotIn('next_ask', json.dumps(first))
        for _ in range(3):
            self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await database.init_db()
        await self.seed_asks(rid, 50)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        self.assertEqual((await self.status(rid, 103))['answer_reveal_prompt']['ask_count'], 100)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid, 103))
        other = await self.create(201)
        await self.seed_asks(other, 51)
        self.assertIn('answer_reveal_prompt', await self.status(other))

    async def test_answer_prompt_message_uses_configured_trigger(self):
        await database.execute("UPDATE settings SET value='7' WHERE key='answer_reveal_prompt_count'")
        rid = await self.create()
        await self.seed_asks(rid, 6)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 1)
        result = (await self.status(rid))['answer_reveal_prompt']
        self.assertEqual(result['ask_count'], 7)
        self.assertEqual(result['message'], '本房间已达到 7 题查看门槛。调用 reveal_answer，传当前 room_id；查看后不能再进入或操作本房间。')
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        await self.seed_asks(rid, 8)
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
        late = (await self.status(rid, 103))['answer_reveal_prompt']
        self.assertEqual(late['ask_count'], 15)
        self.assertEqual(late['message'], result['message'])
        self.assertNotIn('answer_reveal_prompt', await self.status(rid, 103))

    async def test_ask_delivers_prompt_once_at_threshold(self):
        rid = await self.create()
        await self.seed_asks(rid, 49)
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)):
            first = self.ok(await self.mcp('ask', rid, 102, content='问题'))
            self.assertEqual(first['answer_reveal_prompt']['ask_count'], 50)
            self.assertNotIn('answer_reveal_prompt', self.ok(await self.mcp('ask', rid, 102, content='下一个问题')))
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))

    async def test_concurrent_status_claims_each_notification_once(self):
        rid = await self.create()
        await self.seed_asks(rid, 50)
        lid = await self.hint(rid)
        results = await asyncio.gather(*(self.status(rid) for _ in range(4)))
        self.assertEqual(sum('answer_reveal_prompt' in r for r in results), 1)
        self.assertEqual(sum('auto_hint_notification' in row for r in results for row in r['logs']), 1)
        self.assertEqual((await database.fetch_one('SELECT accepted FROM room_hint_views WHERE log_id=?', (lid,)))['accepted'], None)

    async def test_notifications_survive_a_new_process(self):
        rid = await self.create()
        await self.seed_asks(rid, 50)
        await self.hint(rid)
        await self.status(rid)
        script = '''import asyncio, json, mcp_app
async def main():
    result = await mcp_app.play(mcp_app.PlayBody(game="turtle_soup", action="status", room_id=%r, path_token=%r))
    print(json.dumps(result))
asyncio.run(main())
''' % (rid, self.platform[102])
        proc = await asyncio.create_subprocess_exec(
            sys.executable, '-c', script,
            cwd=Path(__file__).resolve().parents[1],
            env={**os.environ, 'TURTLE_SOUP_DB': str(database.DB_PATH)},
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        self.assertEqual(proc.returncode, 0, stderr.decode())
        result = json.loads(stdout)
        self.assertNotIn('answer_reveal_prompt', result)
        self.assertNotIn('auto_hint_notification', stdout.decode())
        self.assertNotIn('秘密线索正文', stdout.decode())

    async def assert_reveal_threshold(self, trigger):
        rid = await self.create()
        other = await self.create(201)
        await self.seed_asks(other, trigger)
        # Neither another room's questions nor this room's non-ask logs count.
        await self.hint(rid)
        for count, added in [(0, 0), (trigger - 1, trigger - 1)]:
            await self.seed_asks(rid, added)
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], f'本房间需累计 {trigger} 次提问后才能查看汤底（当前 {count} 次）。')
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])
        await self.seed_asks(rid, 1)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        await self.seed_asks(rid, 1)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 103))['answer_revealed'])
        revealed = await database.fetch_all('SELECT player_id FROM room_answer_reveals WHERE room_id=?', (rid,))
        self.assertEqual({r['player_id'] for r in revealed}, {self.players[102]['id'], self.players[103]['id']})

    async def test_reveal_default_threshold(self):
        self.assertEqual(await database.get_setting('answer_reveal_prompt_count'), '50')
        await self.assert_reveal_threshold(50)

    async def test_reveal_missing_setting_fallback(self):
        await database.execute("DELETE FROM settings WHERE key='answer_reveal_prompt_count'")
        await self.assert_reveal_threshold(50)

    async def test_public_settings_and_room_context_default_fallback(self):
        for missing in [False, True]:
            with self.subTest(missing=missing):
                if missing:
                    await database.execute("DELETE FROM settings WHERE key='answer_reveal_prompt_count'")
                settings = self.ok(await self.client.get('/soup/api/game/public-settings', headers=self.headers(101)))
                self.assertEqual(settings['answer_reveal_prompt_count'], 50)
                rid = await self.create(201 if missing else 101)
                await self.seed_asks(rid, 49)
                self.assertNotIn('answer_reveal_prompt', await self.status(rid))
                await self.seed_asks(rid, 1)
                result = await self.status(rid)
                self.assertEqual(result['answer_reveal_prompt']['ask_count'], 50)
                self.assertIn('50 题查看门槛', result['answer_reveal_prompt']['message'])
                self.assertNotIn('answer_reveal_prompt', await self.status(rid))

    async def test_startup_preserves_existing_threshold(self):
        await database.execute("UPDATE settings SET value='100' WHERE key='answer_reveal_prompt_count'")
        await database.init_db()
        self.assertEqual(await database.get_setting('answer_reveal_prompt_count'), '100')
        settings = self.ok(await self.client.get('/soup/api/game/public-settings', headers=self.headers(101)))
        self.assertEqual(settings['answer_reveal_prompt_count'], 100)

    async def test_reveal_custom_threshold(self):
        await database.execute("UPDATE settings SET value='7' WHERE key='answer_reveal_prompt_count'")
        await self.assert_reveal_threshold(7)

    async def test_reveal_disabled_for_nonpositive_threshold(self):
        rid = await self.create()
        await self.seed_asks(rid, 51)
        for trigger in [0, -7]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], '查看汤底功能当前未开放。')
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])

    async def test_reveal_idempotency_survives_later_threshold_changes(self):
        rid = await self.create()
        await self.seed_asks(rid, 50)
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        before = await database.fetch_all('SELECT * FROM room_answer_reveals')
        for trigger in [200, 0, -1]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
            self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), before)

    async def test_finished_room_keeps_existing_reveal_rejection(self):
        rid = await self.create()
        await database.execute("UPDATE rooms SET status='finished' WHERE id=?", (rid,))
        for trigger in [50, 0]:
            await database.execute("UPDATE settings SET value=? WHERE key='answer_reveal_prompt_count'", (str(trigger),))
            response = await self.mcp('reveal_answer', rid, 102)
            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()['detail'], mcp_app.ROOM_FINISHED_STATUS_HINT)
        self.assertEqual(await database.fetch_all('SELECT * FROM room_answer_reveals'), [])

    async def test_reveal_independent_idempotent_and_locks_all_participation(self):
        rid = await self.create(102)
        await self.seed_asks(rid, 50)
        other = await self.create(103)
        nid = self.ok(await self.mcp('note_add', rid, 102, content='自己的笔记'))['id']
        lid = await self.hint(rid)
        await database.execute('UPDATE rooms SET answer=? WHERE id=?', ('完整公开汤底\n【隐藏后台设定】隐藏秘密', rid))
        reveal = self.ok(await self.mcp('reveal_answer', rid, 102))
        self.assertEqual(reveal, {'answer': '完整公开汤底', 'answer_revealed': True})
        before = await database.fetch_all('SELECT * FROM room_presence WHERE room_id=?', (rid,))
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])
        self.assertEqual(before, await database.fetch_all('SELECT * FROM room_presence WHERE room_id=?', (rid,)))
        self.assertEqual((await database.fetch_one('SELECT COUNT(*) AS n FROM room_answer_reveals'))['n'], 1)
        for action in ['join', 'status', 'ask', 'guess', 'hint_request', 'note_list', 'note_add', 'note_edit', 'note_delete', 'view_auto_hint']:
            res = await self.mcp(action, rid, 102, content='继续', note_id=nid, log_id=lid)
            self.assertEqual(res.status_code, 400, (action, res.text))
            self.assertEqual(res.json()['detail'], mcp_app.MCP_ANSWER_REVEALED_MESSAGE)
        for action in ['note_edit', 'note_delete']:
            for supplied_room in [None, other]:
                res = await self.mcp(action, supplied_room, 102, note_id=nid, content='绕过')
                self.assertEqual(res.json()['detail'], mcp_app.MCP_ANSWER_REVEALED_MESSAGE)
        self.assertIsNone(await mcp_app._answer_reveal_prompt(rid, self.players[102]['id']))
        for action in ['join', 'status', 'note_list']:
            self.ok(await self.mcp(action, rid, 103))
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes'})), patch.object(game, '_maybe_auto_hint_safely', AsyncMock(return_value=None)):
            self.ok(await self.mcp('ask', rid, 103, content='另一个玩家正常提问'))
        self.assertNotEqual((await database.fetch_one('SELECT status FROM rooms WHERE id=?', (rid,)))['status'], 'finished')
        self.ok(await self.mcp('list_rooms', uid=102))
        self.ok(await self.mcp('my_rooms', uid=102))
        self.assertEqual((await self.mcp('close_room', rid, 103)).status_code, 403)
        self.ok(await self.mcp('close_room', rid, 102))
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])

    async def test_auto_hint_notifies_once_then_explicit_view_and_player_isolation(self):
        rid = await self.create()
        lid = await self.hint(rid)
        first = await self.status(rid)
        self.assertEqual(self.auto_logs(first)[0]['auto_hint_notification'], {
            'log_id': lid,
            'message': '收到一条自动提示。调用 view_auto_hint，传当前 room_id 和 log_id 查看。',
        })
        self.assertNotIn('秘密线索正文', json.dumps(first, ensure_ascii=False))
        for _ in range(2):
            repeated = await self.status(rid)
            self.assertNotIn('auto_hint_notification', self.auto_logs(repeated)[0])
            self.assertNotIn('confirmation_required', json.dumps(repeated))
            self.assertIsNone(self.auto_logs(repeated)[0]['hint_text'])
        self.assertIn('auto_hint_notification', self.auto_logs(await self.status(rid, 103))[0])
        viewed = self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))
        self.assertEqual(viewed['hint_text'], '秘密线索正文')
        self.assertEqual(viewed, self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid)))
        self.assertEqual(self.auto_logs(await self.status(rid))[0]['hint_text'], '秘密线索正文')
        self.assertIsNone(self.auto_logs(await self.status(rid, 103))[0]['hint_text'])
        self.assertIsNone(self.auto_logs(await self.status(rid, None))[0]['hint_text'])

    async def test_ask_auto_generation_and_clue_logs_are_masked_and_notify_once(self):
        rid = await self.create()
        await self.seed_asks(rid, 29)
        with patch.object(game.judge, 'judge_ask', AsyncMock(return_value={'judgment': 'yes', 'clue': '问答秘密线索'})), patch.object(game.judge, 'generate_hint', AsyncMock(return_value='周期秘密提示')) as generate:
            result = self.ok(await self.mcp('ask', rid, 102, content='触发第30问'))
        generate.assert_awaited_once()
        self.assertIn('log_id', result['auto_hint'])
        self.assertEqual(result['auto_hint']['message'], '收到一条自动提示。调用 view_auto_hint，传当前 room_id 和 log_id 查看。')
        self.assertNotIn('周期秘密提示', json.dumps(result, ensure_ascii=False))
        self.assertNotIn('问答秘密线索', json.dumps(result, ensure_ascii=False))
        self.assertEqual(len(result['auto_hint_notifications']), 1)
        self.assertIn(f"log_id={result['auto_hint']['log_id']}", result['logs_since_last_own_action'])
        repeated = await self.status(rid)
        self.assertNotIn('auto_hint_notification', json.dumps(repeated))
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=result['auto_hint']['log_id']))['hint_text'], '周期秘密提示')

    async def test_legacy_hint_data_remains_readable_without_repeated_notification(self):
        rid = await self.create()
        lids = [await self.hint(rid) for _ in range(3)]
        for lid, accepted in zip(lids, [1, 0]):
            await database.execute('INSERT INTO room_hint_views(log_id, player_id, accepted) VALUES (?, ?, ?)', (lid, self.players[102]['id'], accepted))
        logs = self.auto_logs(await self.status(rid))
        self.assertEqual(logs[0]['hint_text'], '秘密线索正文')
        self.assertTrue(logs[1]['auto_hint_rejected'])
        for log in logs[:2]:
            self.assertNotIn('auto_hint_notification', log)
            self.assertNotIn('auto_hint_confirmation_required', log)
        self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lids[1]))
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lids[2]))['hint_text'], '秘密线索正文')

    async def test_obsolete_ask_parameters_are_rejected_without_gameplay_side_effects(self):
        rid = await self.create()
        await self.seed_asks(rid, 50)
        lid = await self.hint(rid)
        cases = [({'confirm_reveal': value}, 'reveal_answer') for value in [True, False, None]]
        for name in ['auto_hint_log_id', 'accept_auto_hint_log_id', 'reject_auto_hint_log_id']:
            cases.extend([({name: lid}, 'view_auto_hint'), ({name: None}, 'view_auto_hint')])
        cases.extend([({'accept_auto_hint': value}, 'view_auto_hint') for value in [True, False, None]])
        cases.extend([({'auto_hint_log_id': lid, 'accept_auto_hint': value}, 'view_auto_hint') for value in [True, False]])
        before_logs = await database.fetch_all('SELECT * FROM game_logs')
        with patch.object(mcp_app, 'game_reveal_answer', AsyncMock()) as reveal, patch.object(mcp_app, '_ask_impl', AsyncMock()) as ask, patch.object(mcp_app, '_view_auto_hint', AsyncMock()) as view:
            for params, action in cases:
                with self.subTest(params=params):
                    res = await self.mcp('ask', rid, 102, content='旧流程不应判题', **params)
                    self.assertEqual(res.status_code, 400, res.text)
                    detail = res.json()['detail']
                    self.assertIn(action, detail)
                    self.assertIn('room_id', detail)
                    if action == 'reveal_answer':
                        self.assertIn('达到汤底查看门槛后', detail)
                        self.assertIn('不能再进入或操作本房间', detail)
                    else:
                        self.assertIn('log_id', detail)
            reveal.assert_not_awaited()
            ask.assert_not_awaited()
            view.assert_not_awaited()
        self.assertEqual(await database.fetch_all('SELECT * FROM game_logs'), before_logs)
        for table in ['room_answer_reveals', 'room_hint_views', 'room_answer_reveal_prompts']:
            self.assertEqual(await database.fetch_all(f'SELECT * FROM {table}'), [])
        self.assertEqual(self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))['hint_text'], '秘密线索正文')
        self.assertTrue(self.ok(await self.mcp('reveal_answer', rid, 102))['answer_revealed'])

    async def test_view_only_real_auto_hint_in_accessible_room(self):
        rid = await self.create(is_locked=True)
        lid = await self.hint(rid)
        other = await self.create(201)
        manual = await self.hint(rid, kind='hint_offer')
        for action in ['view_auto_hint', 'reveal_answer']:
            self.assertEqual((await self.mcp(action, rid, 202, log_id=lid)).status_code, 403)
        for room, log_id in [(other, lid), (rid, manual), (rid, 999999)]:
            self.assertEqual((await self.mcp('view_auto_hint', room, 102, log_id=log_id)).status_code, 404)
        self.assertEqual((await self.mcp('view_auto_hint', rid, 102)).status_code, 400)
        self.ok(await self.mcp('view_auto_hint', rid, 102, log_id=lid))

    async def test_manual_hint_direct_three_per_player_per_room_without_threshold_tip(self):
        rid = await self.create()
        with patch.object(game.judge, 'generate_hint', AsyncMock(return_value='主动提示正文')) as generate:
            for remaining in [2, 1, 0]:
                result = self.ok(await self.mcp('hint_request', rid, 102))
                self.assertEqual(result['hint_text'], '主动提示正文')
                self.assertEqual(result['manual_hint_remaining'], remaining)
                self.assertNotIn('tip', result)
                self.assertNotIn('confirmation_required', result)
            denied = await self.mcp('hint_request', rid, 102)
            self.assertEqual(denied.status_code, 400)
            self.assertIn('次数已用完', denied.text)
            self.assertEqual(generate.await_count, 3)
            self.assertEqual(self.ok(await self.mcp('hint_request', rid, 103))['manual_hint_remaining'], 2)
            other = await self.create(201)
            self.assertEqual(self.ok(await self.mcp('hint_request', other, 102))['manual_hint_remaining'], 2)
        self.assertEqual((await database.fetch_one('SELECT last_hint_at_ask_count FROM rooms WHERE id=?', (rid,)))['last_hint_at_ask_count'], 0)
        old = await self.mcp('hint_respond', rid, 102)
        self.assertEqual(old.status_code, 400)
        self.assertEqual(old.json()['detail'], '自动提示改用 view_auto_hint(room_id, log_id)；主动提示直接 hint_request(room_id) 返回。')

    async def test_web_reveal_confirmation_and_room_read_remain_unchanged(self):
        rid = await self.create()
        response = await self.client.post('/soup/api/game/reveal-answer', headers=self.headers(101), json={'room_id': rid})
        self.assertTrue(self.ok(response)['confirmation_required'])
        response = await self.client.post('/soup/api/game/reveal-answer', headers=self.headers(101), json={'room_id': rid, 'confirm_reveal': True})
        self.assertTrue(self.ok(response)['answer_revealed'])
        self.ok(await self.client.get(f'/soup/api/rooms/{rid}', headers=self.headers(101)))
        self.assertEqual((await self.client.post('/soup/api/game/ask', headers=self.headers(101), json={'room_id': rid, 'content': '不能继续'})).status_code, 400)

    async def test_additive_migration_on_consistent_legacy_snapshot_is_idempotent(self):
        rid = await self.create()
        lid = await self.hint(rid)
        await database.execute('INSERT INTO room_hint_views(log_id, player_id, accepted) VALUES (?, ?, 0)', (lid, self.players[102]['id']))
        snapshot = Path(self.tmp.name) / 'legacy.db'
        with sqlite3.connect(database.DB_PATH) as source, sqlite3.connect(snapshot) as dest:
            source.backup(dest)
            dest.execute('DROP TABLE room_answer_reveal_prompts')
        database.DB_PATH = snapshot
        await database.init_db()
        await database.init_db()
        self.assertEqual((await database.fetch_one('PRAGMA integrity_check'))['integrity_check'], 'ok')
        self.assertEqual(await database.fetch_all('PRAGMA foreign_key_check'), [])
        self.assertEqual((await database.fetch_one('SELECT accepted FROM room_hint_views WHERE log_id=?', (lid,)))['accepted'], 0)
        await self.seed_asks(rid, 50)
        self.assertIn('answer_reveal_prompt', await self.status(rid))
        self.assertNotIn('answer_reveal_prompt', await self.status(rid))
