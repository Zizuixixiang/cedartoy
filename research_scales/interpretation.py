"""Read-only report projection from saved scores; never rescore answers.

Position bands (quarters / fifths) and a 20%-of-range comparison deadband
select cautious prose, NOT norms, cutoffs, effect sizes or significance tests.
Only listed games are supported; unsupported games receive no interpretation.
"""
import math

from .interpretation_content import DIMENSIONS, SINGLES, INTRO, RECOMMENDATIONS, BOUNDARY

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
    if game == 'rbi':
        spread = max(p.values()) - min(p.values())
        if spread < COMPARISON_GAP:
            if max(p.values()) < .25:
                return '三方面的互动在这一周都较少出现。先核对是否有足够相处机会，再商量一个双方愿意尝试的小改变。'
            if min(p.values()) > .75:
                return '三方面的互动这一周都比较丰富。可以互相说说哪些做法真正有帮助，继续保留双方都喜欢的部分。'
            return '三方面相对接近，不必硬找短板。可以回想一件相处中的小事，看看表达的需要和实际回应是否一致。'
        # Choose one strongest meaningful contrast; do not re-explain all three
        # pair comparisons as if every small gap were a separate finding.
        comparisons = (
            ('openness', 'sai',
             '开放表达比日常情感互动更突出：能沟通的需求是否转化为双方实际感受到的关心与回应？',
             '情感互动比开放表达更突出：相处中虽然有温暖，有没有一些需要还没真正谈清楚？'),
            ('fod', 'sai',
             '日常责任落实比情感互动更突出：除了把事情做好，双方是否也有被专注陪伴的时刻？',
             '情感互动比责任落实更突出：有没有一件共同需要完成的事，适合把关心转化为实际分担？'),
            ('openness', 'fod',
             '开放表达比责任落实更突出：可以从已谈过的需要中，选一件双方认可的小事真正落实。',
             '责任落实比开放表达更突出：实际帮忙有没有符合对方真正想要的支持？不妨一起确认。'),
        )
        compared = sorted(
            ((abs(p[a]-p[b]), a_first if p[a] >= p[b] else b_first)
             for a,b,a_first,b_first in comparisons), reverse=True)
        max_keys = [k for k,v in p.items() if max(p.values())-v < COMPARISON_GAP / 2]
        min_keys = [k for k,v in p.items() if v-min(p.values()) < COMPARISON_GAP / 2]
        if len(max_keys)>1 or len(min_keys)>1:
            names = {key: {'sai':'陪伴与情感互动','fod':'履行伴侣责任','openness':'关系中的开放表达'}[key] for key in p}
            return f'{"、".join(names[k] for k in max_keys)}相对更突出，{"、".join(names[k] for k in min_keys)}较少。' + compared[0][1]
        return compared[0][1]
    if game == 'ppts':
        return _pair(p, 'procrastination', 'precrastination',
            '两项都靠近频率较多的一端，可以同时成立，不必强行选一个标签。可对照难开始的任务与急着清掉的任务，检查是否需要分别调整开始和复核环节。',
            '两项都靠近频率较少的一端：较少拖延也较少急着提前完成，不等于没有动力；可以观察现有节奏是否足以满足任务要求。',
            '本次拖延的回答位置高于提前完成，可优先检查开工阻力；把一个任务变成十分钟能开始的一步，再看是否需要调整负担。',
            '本次提前完成的回答位置高于拖延，可优先检查过早提交或返工的场景；为重要任务保留一次条件核对。',
            '两项没有拉开足够距离，不需要编成相互冲突的性格。可以按任务种类分别观察何时想拖、何时想赶紧结束。')
    if game == 'ross':
        return _pair(p, 'indirect', 'no_support',
            '间接求助与不希望支持都较被认同，可能涉及不同时间或不同类型的帮助，不能据此判定你自相矛盾。可明确区分希望被留意与暂时不想被介入。',
            '间接求助和不希望支持都较少被认同；是否愿意直接求助仍要分别看安慰与实际帮助两项，不自动等于沟通无碍。',
            '间接求助比不希望支持更被认同，可以观察是否有想被回应却还没说清的需要，用一个具体请求试着补足线索。',
            '不希望支持比间接求助更被认同，可以先把独处需要说清，并约定重新联系的时机，不必强迫自己当下接受帮助。',
            '间接求助与不希望支持的位置接近，不能只靠这两项区分想被陪伴还是想独处；一次具体的确认比猜测类型更有用。') + ' ' + _pair(p, 'emotional', 'instrumental',
            '安慰与实际帮助都较被认同，开口时可以说明希望先获得哪一种。',
            '两种直接求助都较少被认同，这既不能证明不需要帮助，也不能确定难以开口。',
            '直接求安慰的回答位置更靠前，可告诉对方先听你说，再讨论方案。',
            '直接求实际帮助的回答位置更靠前，可把希望协助的步骤说具体。',
            '两种直接求助位置相近，不必选定永久偏好，可按当次需要说明。')
    if game == 'aidep':
        observation = _pair(p, 'functional', 'cognitive',
            '委托任务与交出思考判断都较被认同，可以优先保留一个独立推理环节，观察工具帮助之后自己是否仍能解释答案。',
            '功能与认知依靠都较少被认同，不必为了提高或降低分数改变用法；可以继续按任务收益决定是否用工具。',
            '功能依靠比认知依靠更被认同，说明外包步骤与交出判断不必同步；可以检查自己是否仍掌握验收依据，而非直接把工具使用当作思考退化。',
            '认知依靠比功能依靠更被认同，值得关注少数关键判断是否很快交给AI，而不只统计使用了多少功能。',
            '功能与认知依靠的位置接近，不能据此判定使用是否合适；还需分别看是否难以停下，以及没有工具时的感受。')
        if p['control'] > .75:
            observation += ' 同时，难以按计划停止使用的体验较被认同；不论委托了多少任务，都可单独检查是否挤占了原定安排，并先试一个明确的结束条件。'
        elif p['emotional'] > .75 and p['control'] < .25:
            observation += ' 学习时获得安定与支持较被认同，而失控体验较少；享受工具带来的回应与难以停下并不是同一回事，可以保留其他获得支持的途径。'
        return observation
    if game == 'evs':
        return _pair(p, 'relationships', 'criticism',
            '顾及关系而难以拒绝、面对批评时受伤都较被认同，可以分别练习表达界限与处理反馈，不把所有不舒服都归为同一种问题。',
            '这两类受伤体验都较少被认同，也要分别看人际不和与回避之后的感受，不将局部低认同当作全面无困扰。',
            '担心关系恶化相关体验比批评相关体验更被认同，反思重点可以先放在拒绝与表达不同意见的场景。',
            '批评相关体验比担心关系恶化相关体验更被认同，反思重点可以先放在如何接收具体反馈，而非预设自己不敢拒绝。',
            '这两类体验相近，不能确定哪个更重要；选最近影响最大的一次事件，检查当时究竟需要澄清意见还是表达界限。')
    if game == 'rnss':
        return _pair(p, 'authenticity', 'initiative',
            '能做自己与感到别人主动靠近都较被认同，可以继续留意具体什么互动支持了这种满足，而不把良好体验当成对方必须随时回应。',
            '能做自己与感到别人主动靠近都较少被认同，可先选一段相对安全的关系，提出一个小偏好和一种期待的联系方式。',
            '真实自我的满足位置高于他人主动：相处自在与希望被主动惦记可以并存，可尝试直接说明希望对方何时主动联系。',
            '他人主动的满足位置高于真实自我：被邀请或关心未必等于能自在表达，可以关注联系中是否留有不同意的空间。',
            '真实自我与他人主动的位置接近，不能仅凭整体均分判断需求是否都被覆盖；还要分别看支持、影响和共同经历。')
    if game == 'rss':
        return _pair(p, 'trust', 'skills',
            '信任困难与技能不足都较被认同，可以先核实具体事实，再练习表达请求；不能假设只要沟通技巧变好就能解决全部信任问题。',
            '信任困难与技能不足都较少被认同，但防御反应仍有自己的含义，不能用两项结果抵消一次实际分歧。',
            '信任困难位置高于技能不足，能沟通与仍然不放心可以同时存在。先核实担心的事实依据，再讨论怎样的约定能被双方接受。',
            '技能不足位置高于信任困难，相信对方与不知道怎么把话说清可以同时存在。可优先练习一个沟通步骤，而非刻意寻找背叛线索。',
            '信任与技能方面的困难位置接近，不代表彼此导致；从最近一次分歧中选一个能具体改变的环节。')
    if game == 'irrc':
        strong = [k for k in CAPABILITIES if p[k] > .75]
        difficult = [k for k in DIFFICULTIES if p[k] > .75]
        if all(value < .25 for value in p.values()):
            return '能力体验与困难体验都较少被认同，不能简单称为关系能力差或状态良好。可以先回想自己是否有足够相关经历，再从一次具体互动中辨认已经做到的事与想练习的事。'
        if strong and difficult:
            return '部分能力体验与困难体验同时靠近认同较多的一端；觉得自己能处理关系与在某些时刻仍受困可以并存。可选一个已有把握的沟通步骤，尝试用于最困扰自己的场景，不让能力分抵消真实困难。'
        if strong:
            return '部分能力体验较被认同，而困难体验没有同样靠近量尺上端；可以用一次真实分歧检验哪些做法确实对双方有帮助，避免把自评信心当作关系保证。'
        if difficult:
            return '部分困难体验较被认同，而能力体验没有同样靠近量尺上端；可以先选一个可练习的小环节，同时辨认哪些问题需要对方配合，避免将责任都放在自己身上。'
        return '能力与困难分开理解后，本次没有上述较突出的组合；较少报告困难也不自动意味着能力很强。可以依据一个实际情境选择练习，而非追求把所有维度都提高。'
    if game == 'bsr':
        # Difference is in the original 1–5 mean units, not a norm or accuracy.
        difference = (p['motivational'] - p['pseudo_profound']) * 4
        if abs(difference) < .8:
            return _pair(p, 'motivational', 'pseudo_profound',
                '两组都较能引发深刻感；相近的评价可能有不同阅读理由，不说明你无法分辨内容。可以检查每组各一句能否说出具体意义。',
                '两组都较少引发深刻感；这可能涉及材料共鸣，不能把少受打动等同于更理性。可以比较自己对两组各一句的评价依据。',
                '', '', '两组均分相近，本次不把小差值讲成明显的辨别差异；相同均分也可能掩盖对不同句子的不同评价。')
        if difference > 0:
            return '格言的平均深刻感高于生成语句；这是这套材料内的评价倾向，不是通用识别能力。可比较两组各一句，说明哪一组更容易找到清楚含义与适用条件。'
        return '生成语句的平均深刻感高于格言；可以探索语言新鲜感、联想与具体意义各占多大分量，不据此判断轻信。试着用同样的改写与举例步骤检查两组。'
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
        recommendations = [action]
    else:
        boundary = BOUNDARY[game]
        for key in spec['dimensions']:
            notes.append({'key': key, 'label': labels[key], 'score': scores[key],
                          'range': ranges[key], 'text': DIMENSIONS[game][key][_band(positions[key])]})
        observation = _combination(game, positions)
        if game == 'irrc':
            ability = _ordering(CAPABILITIES, positions, labels)
            difficulty = _ordering(DIFFICULTIES, positions, labels)
            differences = ' '.join(f'{name}：{detail}' for name, detail in
                                   (('关系能力', ability), ('困扰体验', difficulty)) if detail)
            if differences:
                observation = differences + ' ' + observation
        summary = observation
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
