"""Read-only report projection from saved scores; never rescore answers.

Position bands (quarters / fifths) and a 20%-of-range comparison deadband
select cautious prose, NOT norms, cutoffs, effect sizes or significance tests.
Only listed games are supported; unsupported games receive no interpretation.
"""
import math

from .interpretation_content import DIMENSIONS, SINGLES, INTRO, RECOMMENDATIONS, BOUNDARY, COMBINATIONS

SUPPORTED_GAMES = frozenset(DIMENSIONS) | frozenset(SINGLES)
CAPABILITIES = ('locus', 'perspective', 'temperament', 'conflict')
DIFFICULTIES = ('romantic_distress', 'intimacy_avoidance', 'emotion_difficulty')
COMPARISON_GAP = .20


def _band(position):
    return 0 if position < .25 else 2 if position > .75 else 1


def _label(label):
    return label.replace('（描述性均分）', '').replace('（描述均分）', '')


def _ordering(keys, positions, labels):
    """Compare only response positions of same-range dimensions, not constructs."""
    ordered = sorted(keys, key=lambda key: -positions[key])
    first, last = ordered[0], ordered[-1]
    if positions[first] - positions[last] < COMPARISON_GAP:
        return ''
    # Retain ties / small gaps in the top and bottom groups.
    top = [labels[k] for k in ordered if positions[first] - positions[k] < COMPARISON_GAP / 2]
    bottom = [labels[k] for k in ordered if positions[k] - positions[last] < COMPARISON_GAP / 2]
    return f'相对更突出：{"、".join(top)}；较少：{"、".join(bottom)}。'


def _pair(p, a, b, both_high, both_low, a_first, b_first, close):
    if p[a] > .75 and p[b] > .75:
        return both_high
    if p[a] < .25 and p[b] < .25:
        return both_low
    if p[a] - p[b] >= COMPARISON_GAP:
        return a_first
    if p[b] - p[a] >= COMPARISON_GAP:
        return b_first
    return close


def _combination(game, p):
    """Choose an offline-authored DeepSeek paragraph. Input: normalized saved means."""
    text = COMBINATIONS[game]
    if game == 'rbi':
        spread = max(p.values()) - min(p.values())
        if spread < COMPARISON_GAP:
            if max(p.values()) < .25:
                return text['all_low']
            if min(p.values()) > .75:
                return text['all_high']
            return text['all_close']
        top = {k for k, v in p.items() if max(p.values()) - v < COMPARISON_GAP / 2}
        bottom = {k for k, v in p.items() if v - min(p.values()) < COMPARISON_GAP / 2}
        keys = ('sai', 'fod', 'openness')
        if len(top) == 2:
            return text['top_' + '_'.join(k for k in keys if k in top)]
        if len(bottom) == 2:
            return text['bottom_' + '_'.join(k for k in keys if k in bottom)]
        pairs = (('openness', 'sai'), ('fod', 'sai'), ('openness', 'fod'))
        distance, a, b = max((abs(p[a]-p[b]), a, b) for a, b in pairs)
        return text[a + '_gt_' + b if p[a] > p[b] else b + '_gt_' + a]
    if game == 'ppts':
        return _pair(p, 'procrastination', 'precrastination',
            *[text[k] for k in ('both_high','both_low','first_high','second_high','close')])
    if game == 'ross':
        if all(p[key] < .25 for key in ('emotional','instrumental','indirect','no_support')):
            return text['all_low']
        indirect = _pair(p, 'indirect', 'no_support',
            *[text[k] for k in ('indirect_both_high','indirect_both_low','indirect_high','no_support_high','indirect_close')])
        direct = _pair(p, 'emotional', 'instrumental',
            *[text[k] for k in ('direct_both_high','direct_both_low','emotional_high','instrumental_high','direct_close')])
        return indirect + ' ' + direct
    if game == 'aidep':
        observation = _pair(p, 'functional', 'cognitive',
            *[text[k] for k in ('both_high','both_low','first_high','second_high','close')])
        if p['control'] > .75:
            observation += ' ' + text['control_high']
        elif p['emotional'] > .75 and p['control'] < .25:
            observation += ' ' + text['emotional_high_control_low']
        return observation
    if game == 'evs':
        return _pair(p, 'relationships', 'criticism',
            *[text[k] for k in ('both_high','both_low','first_high','second_high','close')])
    if game == 'rnss':
        return _pair(p, 'authenticity', 'initiative',
            *[text[k] for k in ('both_high','both_low','first_high','second_high','close')])
    if game == 'rss':
        return _pair(p, 'trust', 'skills',
            *[text[k] for k in ('both_high','both_low','first_high','second_high','close')])
    if game == 'irrc':
        strong = any(p[k] > .75 for k in CAPABILITIES)
        difficult = any(p[k] > .75 for k in DIFFICULTIES)
        if all(value < .25 for value in p.values()):
            return text['all_low']
        if strong and difficult:
            return text['ability_and_difficulty_high']
        if strong:
            return text['ability_high']
        if difficult:
            return text['difficulty_high']
        return text['mixed']
    if game == 'bsr':
        difference = (p['motivational'] - p['pseudo_profound']) * 4
        if round(abs(difference), 8) < .8:
            return _pair(p, 'motivational', 'pseudo_profound',
                text['both_high'], text['both_low'], '', '', text['close'])
        return text['motivational_high' if difference > 0 else 'pseudo_high']
    raise KeyError(game)


def build_interpretation(spec, scores):
    """Return a fresh JSON-safe report or None for unsupported/invalid scores.

    Historical scores are the sole input. Do not consult private answers, parse
    stored prose, add subscales, or mutate either scores or the scoring spec.
    """
    game = spec['game']
    if game not in SUPPORTED_GAMES or not isinstance(scores, dict):
        return None
    minimum = min(o['value'] for o in spec['options'])
    maximum = max(o['value'] for o in spec['options'])
    positions, ranges = {}, {}
    for key in spec['dimensions']:
        count = sum(q['dimension'] == key for q in spec['items']) if spec['aggregation'] == 'sum' else 1
        low, high = minimum * count, maximum * count
        value = scores.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
            return None
        positions[key] = (value - low) / (high - low)
        ranges[key] = [low, high]
    labels = {k: _label(v) for k, v in spec['dimensions'].items()}
    notes = []
    if game in SINGLES:
        key, variants, action, boundary = SINGLES[game]
        reflection = variants[min(4, int(positions[key] * 5))]
        summary = reflection
        # No second copy of the reflection in the same report.
        # For one-dimensional scales the summary is the explanation. Do not
        # add a generic second paragraph that merely says the score is single.
        recommendations = list(action)
    else:
        boundary = BOUNDARY[game]
        for key in spec['dimensions']:
            notes.append({'key': key, 'label': labels[key], 'score': scores[key],
                          'range': ranges[key], 'text': DIMENSIONS[game][key][_band(positions[key])]})
        summary = _combination(game, positions)
        recommendations = list(RECOMMENDATIONS[game])
    return {
        'version': 1, 'game': game, 'summary': summary,
        'dimension_notes': notes, 'recommendations': recommendations,
        'source_note': '维度含义参考论文或官方量表；综合观察与行动建议由平台编写，不是研究验证过的个人结论。分数没有统一及格线，接近的分数不必强行分高低。',
        'source_url': spec['source'], 'boundary': boundary,
    }


def format_interpretation(report):
    """Plain text for MCP and fallback consumers; contains no private item data."""
    parts = ['【综合观察】', report['summary']]
    if report['dimension_notes']:
        parts.append('【维度解读】')
    for note in report['dimension_notes']:
        parts.append(f"{note['label']}：{note['text']}")
    if report['recommendations']:
        parts.append('【可以试试】')
        parts.extend(f'{i}. {item}' for i, item in enumerate(report['recommendations'], 1))
    # Source/version limitations remain in structured metadata and the existing
    # notice; do not repeat them at the foot of every human/AI report.
    return '\n\n'.join(parts)
