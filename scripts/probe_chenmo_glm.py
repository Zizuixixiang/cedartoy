"""Opt-in paid synthetic probe; read-only config, no rooms, saves or cost writes."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import time
from unittest.mock import AsyncMock, patch

ROOT = Path(__file__).resolve().parents[1]
MODEL = '[按量1]glm-5.3-flash'


async def probe(db_path, selected):
    sys.path.insert(0, str(ROOT / 'turtle-soup/backend'))
    import judge

    # Read only public system prompts and these three config rows. Never read a
    # room, user, session, story or save. Credentials stay in process memory.
    with sqlite3.connect(db_path.resolve().as_uri() + '?mode=ro', uri=True) as db:
        db.row_factory = sqlite3.Row
        configs = [dict(row) for row in db.execute(
            'SELECT * FROM judge_api_configs WHERE id IN (28,29,30) ORDER BY id')]
        settings = {row['key']: row['value'] for row in db.execute(
            "SELECT key,value FROM settings WHERE key IN ('judge_prompt','judge_prompt_guess','judge_prompt_hint')")}
    assert [(c['id'], c['purpose']) for c in configs] == [(28, 'both'), (29, 'npc'), (30, 'tarot')]
    assert all(judge._endpoint(c['api_url']) == 'https://api.chenmoai.com/v1/chat/completions' and c['api_key'] for c in configs)
    assert all(c['enabled'] == 1 and isinstance(c['priority'], int) for c in configs)
    for cfg in configs:
        cfg['model'] = MODEL  # memory only

    # Real tarot prompt builder, but a completely synthetic draw/question.
    tarot = json.loads(subprocess.run(
        ['node', str(ROOT / 'scripts/tarot_prompt_helper.mjs')],
        input=json.dumps({'action': 'messages', 'payload': {
            'question': '虚构练习：如何安排今天的阅读学习？', 'spread_id': 'single',
            'draws': [{'position': 0, 'card_id': 'M00', 'reversed': False}],
        }}), text=True, capture_output=True, check=True, timeout=10).stdout)['messages']

    async def setting(sql, *args):
        match = re.search("key = '([^']+)'", sql)
        return {'value': settings.get(match[1])} if match else None

    attempts = []
    def record(*args):
        event = judge.api_costs.make_event(*args)
        attempts.append({**{k: event[k] for k in ('config_id', 'status', 'http_status')},
                         **dict(zip(('input_tokens', 'output_tokens', 'cache_tokens'), event['counts']))})

    original_post = judge._post_chat_completion
    async def post(cfg, payload, timeout, **kwargs):
        # At most one actual node per task, no fallback to real backups in this
        # probe; the offline tests cover fallback. Output and wall time bounded.
        payload = {**payload, 'max_tokens': min(payload.get('max_tokens') or 2048, 2048)}
        return await original_post(cfg, payload, min(timeout, 30), **kwargs)

    surface = '小明带了伞出门，一整天都没有打开。'
    answer = '今天是晴天，没有下雨。小明带伞只是为了防备天气变化。'
    npc = [{'role': 'system', 'content': '从 legal_actions 选择一个 action_id。只返回 JSON，示例：{"action_id":"a_wait"}。'},
           {'role': 'user', 'content': '虚构桌游练习 legal_actions=[{"action_id":"a_wait"}]'}]
    speech = [{'role': 'system', 'content': '为虚构桌游角色发言，只返回 JSON，示例：{"message":"轮到你了"}。'},
              {'role': 'user', 'content': '你已结束回合，提醒下一位玩家。'}]
    cases = [
        ('ask_yes', lambda: judge.judge_ask(surface, answer, '今天是晴天吗？', []), lambda r: r.get('judgment') == 'yes'),
        ('ask_no', lambda: judge.judge_ask(surface, answer, '今天下雨了吗？', []), lambda r: r.get('judgment') == 'no'),
        ('guess', lambda: judge.judge_guess(surface, answer, answer), lambda r: not r.get('error') and r.get('success') is True),
        ('hint', lambda: judge.generate_hint(surface, answer, []), lambda r: bool(r)),
        ('hint_history', lambda: judge.generate_hint(surface, answer, [{'type': 'ask', 'content': '伞坏了吗？', 'judgment': 'no'}]), lambda r: bool(r)),
        ('npc_decision', lambda: judge.npc_decision_chat(npc), lambda r: json.loads(r) == {'action_id': 'a_wait'}),
        ('npc_speech', lambda: judge.npc_speech_chat(speech), lambda r: bool(json.loads(r).get('message'))),
        ('tarot', lambda: judge.tarot_reading_chat(tarot, max_tokens=2048, timeout=30), lambda r: len(r.get('content', '')) >= 40),
    ]
    if selected:
        cases = [case for case in cases if case[0] in selected]
    passed = 0
    with patch.object(judge, 'fetch_all', AsyncMock(return_value=configs)), \
         patch.object(judge, 'fetch_one', side_effect=setting), \
         patch.object(judge.api_costs, 'record', side_effect=record), \
         patch.object(judge, '_post_chat_completion', side_effect=post):
        for name, call, check in cases:
            attempts.clear()
            started = time.monotonic()
            error = None
            result = None
            try:
                result = await asyncio.wait_for(call(), timeout=35)
                valid = bool(check(result)) and len(attempts) == 1 and attempts[0]['status'] == 'success'
            except Exception as exc:
                valid, error = False, type(exc).__name__
            passed += valid
            print(json.dumps({'case': name, 'pass': valid, 'error_type': error,
                              'judgment': result.get('judgment') if name.startswith('ask_') and isinstance(result, dict) else None,
                              'seconds': round(time.monotonic()-started, 2),
                              'attempts': attempts}, ensure_ascii=False), flush=True)
    print(json.dumps({'passed': passed, 'total': len(cases), 'model': MODEL}, ensure_ascii=False))
    return passed == len(cases)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Send eight paid synthetic requests (up to 2048 output tokens each)')
    parser.add_argument('--db', type=Path, default=Path(os.getenv('TURTLE_SOUP_DB', ROOT / 'turtle-soup/backend/turtle_soup.db')))
    parser.add_argument('--case', action='append', choices=['ask_yes', 'ask_no', 'guess', 'hint', 'hint_history', 'npc_decision', 'npc_speech', 'tarot'], help='Run only selected fixtures, once each')
    args = parser.parse_args()
    if not args.live:
        parser.error('Explicit --live required; uses existing provider credentials and incurs API cost')
    logging.disable(logging.CRITICAL)
    try:
        ok = asyncio.run(probe(args.db, args.case))
    except Exception as exc:
        # No raw transport errors, provider responses, prompts or credentials.
        print(json.dumps({'pass': False, 'error_type': type(exc).__name__}))
        ok = False
    raise SystemExit(0 if ok else 1)
