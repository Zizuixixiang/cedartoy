#!/usr/bin/env python3
"""Reproduce completed report samples and actual MCP content measurements.

All accounts, tokens, and results are temporary. No sockets, service operations,
production data or dependency downloads. Optional --output writes review files.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import sys
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tests_toy.test_research_interpretation import InterpretationTests, scenario_answers
from tests_toy.test_aidep_sharing import seed_accounts
from research_scales.interpretation import SUPPORTED_GAMES, format_interpretation
from research_scales.interpretation_content import SINGLES
import research_scales
import server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--token-encoding', help='Optional already-cached tiktoken encoding, e.g. o200k_base')
    parser.add_argument('--only-rbi', action='store_true', help='Extend existing output with RBI; preserve prior samples and measurements')
    args = parser.parse_args()
    previous = None
    if args.only_rbi:
        if not args.output:
            parser.error('--only-rbi requires --output with an existing sample JSON')
        previous = json.loads((args.output / 'research-scales-report-samples.json').read_text())
        if previous['token_encoding'] != args.token_encoding:
            parser.error('use the same token encoding as the existing samples')
    encoding = None
    if args.token_encoding:
        import tiktoken
        encoding = tiktoken.get_encoding(args.token_encoding)
    fixture = InterpretationTests()
    fixture.setUp()
    try:
        with ExitStack() as stack:
            tokens = seed_accounts()
            for game in ('mbti', 'enneagram', 'dnd', 'love', 'ecr', 'humanity', 'sins_virtues'):
                config = server.HUMAN_TEST_GAMES[game]
                stack.enter_context(patch.object(config['handler'], 'DB_PATH', str(fixture.db)))
            for name in ('_auto_migrate_legacy_account_saves', '_reject_claimed_guest'):
                stack.enter_context(patch.object(server, name))
            for name in ('_anti_addiction_context', '_anti_addiction_preflight'):
                stack.enter_context(patch.object(server, name, return_value=None))
            for name in ('_duel_unread_request_reminder', '_mcp_forced_announcement'):
                stack.enter_context(patch.object(server, name, return_value=''))
            stack.enter_context(patch.object(server, '_finalize_play_response', side_effect=lambda response, **kw: response))
            stack.enter_context(patch.object(server, '_check_request_rate_limit', return_value=True))

            def call(game, action, params):
                raw = fixture.request('/mcp', {'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call', 'params': {
                    'name': 'play', 'arguments': {'game': game, 'action': action, 'params': params}}}, tokens[11])
                result = raw['result']
                assert not result.get('isError'), result
                text = result['content'][0]['text']
                return text, json.loads(text) if text.startswith('{') else text

            def measure(text):
                return {'characters': len(text), 'utf8_bytes': len(text.encode()),
                        'tokens': len(encoding.encode(text)) if encoding else None}

            samples = previous['samples'] if previous else {}
            metrics = previous['mcp_sizes'] if previous else {}
            for game in (['rbi'] if args.only_rbi else sorted(SUPPORTED_GAMES)):
                engine = research_scales.SCALES[game]
                cases = ('low', 'quarter', 'middle', 'three_quarters', 'high') if game in SINGLES else ('low', 'middle', 'high', 'mixed_a', 'mixed_b')
                if game == 'rbi':
                    cases += ('communication_to_response',)
                samples[game], metrics[game] = [], []
                sid = None
                for case in cases:
                    params = {'mode': 'full_fast'}
                    if sid:
                        params.update(restart=True, confirm=True, session_id=sid)
                    if game == 'aidep':
                        state = server._human_test_action(game, 'start', tokens[1], params)
                    else:
                        _, state = call(game, 'start', params)
                    sid = state['session_id']
                    # Legal item responses produce 20/7, 25/7, 4.5 (display 2.86/3.57/4.5).
                    answers = ([3]*6 + [2] + [4]*4 + [3]*3 + [5, 4, 5, 4]
                               if case == 'communication_to_response' else scenario_answers(engine, case, sid))
                    params = {'session_id': sid, 'answers': answers}
                    if game == 'aidep':
                        completed = server._human_test_action(game, 'answer_batch', tokens[1], params)
                        settings = server._human_test_action(game, 'sharing', tokens[1], {})
                        server._human_test_action(game, 'sharing', tokens[1], {'enabled': True, 'ai_id': 11,
                            'session_id': sid, 'result_version': settings['result_version']})
                        text, shared = call(game, 'shared_result', {'human_id': 1})
                        report = shared['interpretation']
                        assert report == completed['result_data']['interpretation']
                        baseline = {k: v for k, v in shared.items() if k not in ('interpretation', 'result')}
                        status_size = None
                    else:
                        text, completed = call(game, 'answer_batch', params)
                        _, restored = call(game, 'get_result', {})
                        assert completed['result_data'] == restored['result_data']
                        report = completed['result_data']['interpretation']
                        baseline = json.loads(text)
                        del baseline['result_data']['interpretation']
                        baseline['result'] = baseline['result'].partition('\n\n【综合观察】')[0]
                        status_text, status = call(game, 'status', {})
                        assert 'interpretation' not in status['result_data']
                        status_size = measure(status_text)
                    samples[game].append({'case': case, 'scores': completed['result_data']['scores'], 'interpretation': report})
                    metrics[game].append({'case': case, 'before': measure(json.dumps(baseline, ensure_ascii=False)),
                                          'after': measure(text), 'status': status_size})

            _, rbi = call('rbi', 'get_result', {})
            assert rbi['result_data']['interpretation']['game'] == 'rbi'

            legacy = previous['legacy'] if previous else {}
            for game in (() if args.only_rbi else ('mbti', 'enneagram', 'dnd', 'love', 'ecr', 'humanity', 'sins_virtues')):
                state = server._human_test_action(game, 'start', tokens[1], {'edition': 'quick'})
                answer = 3 if game in ('mbti', 'sins_virtues') else 4 if game == 'ecr' else 1
                total = state['total']
                state = server._human_test_action(game, 'answer_batch', tokens[1], {'answers': [answer]*state['total']})
                handler = server.HUMAN_TEST_GAMES[game]['handler']
                # Read the real stored local report; web user and AI are not merged.
                stored = getattr(handler, game + '_get_result')({'player_id': '1'})
                call(game, game + '_start', {'mode': 'short' if game == 'mbti' else 'quick' if game == 'enneagram' else 'full'})
                for _ in range(total):
                    _, mcp_result = call(game, game + '_answer', {'a_score' if game == 'mbti' else 'answer': answer})
                    assert not mcp_result['result'].get('isError'), mcp_result
                _, mcp_result = call(game, game + '_get_result', {})
                assert not mcp_result['result'].get('isError'), mcp_result
                mcp_text = mcp_result['result']['content'][0]['text']
                legacy[game] = {'result_data_keys': list(state['result_data']), 'web_result': state['result'],
                                'stored_report': stored, 'mcp_report': mcp_text}
            output = {'token_encoding': args.token_encoding, 'samples': samples, 'mcp_sizes': metrics,
                      'rbi': rbi['result_data'], 'legacy': legacy}
            if args.output:
                args.output.mkdir(parents=True, exist_ok=True)
                (args.output / 'research-scales-report-samples.json').write_text(json.dumps(output, ensure_ascii=False, indent=2) + '\n')
                lines = ['# 研究量表报告实测样例', '', '由 `python3 scripts/audit_research_reports.py --output docs --token-encoding o200k_base` 生成。',
                         '原17款各5套（85套）保留，RBI补6套，共18款91套。全部经临时库完成及真实根MCP处理器读取；AIDep为人类完成后显式授权分享。',
                         'RBI增量命令在上述命令末尾加 `--only-rbi`，保留原17款样例、体积记录及旧7款输出。',
                         '样例仅为合成测试输入。多维包含两组方向相反的混合；单维用五个合法分数位置，不编造可由总分恢复的题目分布。',
                         '完整结构、分数、旧7款原报告、RBI结果和逐响应体积见同名JSON。', '',
                         '## MCP响应体积', '',
                         '| game | 原响应字符范围 | 新响应字符范围 | 新响应o200k_base token范围 | status字符范围 |',
                         '| --- | ---: | ---: | ---: | ---: |']
                def span(rows, field, metric):
                    values = [row[field][metric] for row in rows if row[field] and row[field][metric] is not None]
                    return f'{min(values)}–{max(values)}' if values else '不适用'
                for game, rows in metrics.items():
                    lines.append(f'| {game} | {span(rows,"before","characters")} | {span(rows,"after","characters")} | {span(rows,"after","tokens")} | {span(rows,"status","characters")} |')
                lines += ['', '统计的是MCP `content[0].text`（含其JSON字段与转义），不含外层JSON-RPC包裹或公告。原响应为同一回执去掉本次新增字段/文本；字符、UTF-8字节和token在JSON中分开保存。o200k_base是测量编码，不等于每个小机所用模型的精确token数。',
                          '完整读取同时保留结构化解读与可读文本，存在有意的重复以兼容两类消费者；status不重复长报告。', '']
                for game, rows in samples.items():
                    lines += [f'## {game}', '']
                    for row in rows:
                        lines += [f'### {row["case"]}', '', '`' + json.dumps(row['scores'], ensure_ascii=False) + '`', '', format_interpretation(row['interpretation']), '']
                (args.output / 'research-scales-report-samples.md').write_text('\n'.join(lines))
            print(f'PASS: {sum(map(len, samples.values()))} total MCP samples; RBI included; 7 legacy reports; temporary databases cleaned on exit.')
            for game, rows in metrics.items():
                print(game, max(row['after']['characters'] for row in rows), 'characters;', max((row['after']['tokens'] or 0) for row in rows), 'tokens')
    finally:
        fixture.doCleanups()


if __name__ == '__main__':
    main()
