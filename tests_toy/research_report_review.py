"""Reproducible editorial snapshots; no DB, service, or private answer access.

Regenerate after reading changed prose:
    python3 -m tests_toy.research_report_review
The separate audit_research_reports.py checks real temporary HTTP/MCP paths.
"""
import itertools
import json
from pathlib import Path

import research_scales
from research_scales.interpretation import build_interpretation, CAPABILITIES
from research_scales.interpretation_content import DIMENSIONS, SINGLES, INTRO, RECOMMENDATIONS

ROOT = Path(__file__).resolve().parents[1]
SNAPSHOT = ROOT / 'docs/research-scales-chinese-review.json'


def review_cases():
    """Stored-score fixtures: all text bands, comparison directions and ties."""
    for game, engine in sorted(research_scales.SCALES.items()):
        keys = list(engine.spec['dimensions'])
        bounds = engine.score_ranges()

        def scores_at(positions):
            scores = {k: bounds[k]['min'] + p * (bounds[k]['max'] - bounds[k]['min'])
                      for k, p in zip(keys, positions)}
            if engine.spec.get('overall_mean'):
                # Complete the synthetic fixture, including its existing metric.
                scores['overall'] = sum(scores[q['dimension']] for q in engine.items) / len(engine.items)
            if game == 'bsr':
                scores['difference'] = scores['motivational'] - scores['pseudo_profound']
            return scores

        for case, position in (('low', 0), ('quarter', .25), ('middle', .5),
                               ('three_quarters', .75), ('high', 1)):
            yield game, case, scores_at([position] * len(keys))
        if game in SINGLES:
            continue
        for parity in (0, 1):
            yield game, f'mixed_{parity}', scores_at([(i + parity) % 2 for i in range(len(keys))])
        yield game, 'near', scores_at([.5 + i * .005 for i in range(len(keys))])
        # Individually vary each dimension so less prominent ones get reviewed too.
        for index, key in enumerate(keys):
            for position in (0, 1):
                values = [.5] * len(keys)
                values[index] = position
                yield game, f'{key}_{position}', scores_at(values)
        if game == 'rbi':
            for positions in itertools.product((0, .5, 1), repeat=3):
                yield game, 'grid_' + '_'.join(map(str, positions)), scores_at(positions)
            for case, values in (
                ('requested', (2.86, 3.57, 4.5)),
                ('near_top', (1, 4.8, 5)),
                ('near_bottom', (1, 1.2, 5)),
                ('below_gap', (3, 3.4, 3.79)),
                ('above_gap', (3, 3.405, 3.81)),
            ):
                yield game, case, dict(zip(keys, values))
        if game == 'irrc':
            for abilities_high in (False, True):
                values = [int((k in CAPABILITIES) == abilities_high) for k in keys]
                yield game, f'abilities_high_{abilities_high}', scores_at(values)


def report_case(game, case, scores):
    engine = research_scales.SCALES[game]
    report = build_interpretation(engine.spec, scores)
    # Same pure formatter used for completed / restored human and MCP results.
    result = engine._state('guest:editorial', 'full_fast', 'editorial', [],
                           complete=True, scores=scores)['result']
    return {'game': game, 'case': case, 'scores': scores,
            'interpretation': report, 'result': result}


def snapshot():
    rows = [report_case(*case) for case in review_cases()]
    return {'content': {'dimensions': DIMENSIONS, 'singles': SINGLES,
                        'intro': INTRO, 'recommendations': RECOMMENDATIONS},
            'cases': rows,
            'requested_cases': [report_case('ipip_empathy', 'requested_26', {'ipip_empathy': 26})]}


def write_snapshot():
    data = snapshot()
    SNAPSHOT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
    selections = [('ipip_empathy', 'requested_26'), ('rbi', 'requested'), ('ppts', 'high'), ('irrc', 'abilities_high_True'),
                  ('rss', 'skills_0'), ('bsr', 'mixed_0'), ('aidep', 'control_1'),
                  ('evs', 'mixed_0'), ('rnss', 'mixed_1'), ('ross', 'mixed_0')]
    selections += [(g, 'middle') for g in sorted(SINGLES) if g != 'ipip_empathy']
    lines = ['# 18款报告中文二轮审稿样例（2026-10-12）', '',
             '下列全文由现有结果生成器直接输出，分数是合成的保存分数；没有手工改写生成结果。',
             'RBI使用准确的2.86/3.57/4.50输入；真实问卷作答得到20/7、25/7、4.5的MCP样例另见research-scales-report-samples.md。',
             f'同名JSON保存全部{len(data["cases"])}个组合及文案常量；再生成：`python3 -m tests_toy.research_report_review`。',
             '另附共情关怀26/50特例：合法范围10～50，位置(26−10)/(50−10)=0.4，选择五段中的第三段；题目刻度中心是30/50。此处不是常模分档。',
             '五档/三档只是选择文案的内部位置，不是官方等级。本文是提交审稿的版本，尚未获得用户文字验收。', '']
    for game, case in selections:
        row = next(r for r in data['cases'] + data['requested_cases'] if r['game'] == game and r['case'] == case)
        lines += [f'## {game} / {case}', '', row['result'], '']
    lines += ['## 共情关怀五段对照', '', '这里只摘综合观察；每段均使用首份26/50报告中的两条建议。', '']
    for row in data['cases']:
        if row['game'] == 'ipip_empathy':
            lines += [f'### {row["scores"]["ipip_empathy"]:g}/50 / {row["case"]}', '',
                      row['interpretation']['summary'], '']
    lines += ['## RBI排序与并列复读', '']
    for row in data['cases']:
        if row['game'] == 'rbi' and (row['case'].startswith('grid_') or row['case'].startswith('near') or row['case'].endswith('_gap')):
            lines += [f'### {row["scores"]}', '', row['interpretation']['summary'], '']
    (SNAPSHOT.with_suffix('.md')).write_text('\n'.join(lines))
    print(f'Generated {len(data["cases"])} score profiles and {len(selections)} full review reports.')


if __name__ == '__main__':
    write_snapshot()
