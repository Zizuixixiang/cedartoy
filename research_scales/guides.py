"""Compact MCP-only guides; scoring and human-facing notices stay in the engine."""

from .engine import DISCLAIMER
from cedar_backend.display_titles import RESEARCH_TITLES


# Guide summaries of the reviewed registry, not scoring implementations.
NOTES = {
    'bsfl': '原研究为秘鲁18–40岁大学生。4399描述性均分，非原量表标准分；5题0–4取均值，无反向（含第4题），越高表示更常报告孤独恐惧。',
    'bsr': 'Study 4结构的4399娱乐改编：10条原附录生成句＋10条公共领域古典格言，非逐句复刻，不沿用原版信效度。两组各取1–5均分，差值＝格言−伪深刻（−4至4）；非答对率，不代表智力或识骗能力。',
    'evs': '原研究为日本大学生，采用表1英文题。四维及全题均分，越高表示更易在相应情境受伤。',
    'ipip_adhd': 'IPIP表征版12题，非ASRS，不能筛查或确定ADHD。无反向，求和12–60。',
    'ipip_empathy': 'Barchard EI的Empathic Concern，非AB5C/JPI同名量表，不代表全面共情能力。6–10题按6−原分反向后求和10–50。',
    'ipip_need_for_cognition': 'IPIP表征版，非原始NFC问卷，不代表智力。7–10题按6−原分反向后求和10–50。',
    'ipip_self_monitoring': 'IPIP表征版，非Snyder原版。8–10题按6−原分反向后求和10–50。',
    'ipip_sociability': 'IPIP CPI-Sy公共领域表征版，非CPI原卷。6–10题按6−原分反向后求和10–50。',
    'irrc': '原研究为年轻成人大学生。4399七维描述性均分，非作者官方分数；32题按6−原分反向。控制感/换位/灵活性/冲突解决高分表示更认同能力，吸引力困扰/亲密回避/情绪调节困难高分表示更多困难；不合成总分，不作性别DIF或潜变量校正。',
    'jrrs': '原研究为土耳其成人。4399全16题描述性均分（1–5、无反向），非官方分数；原三维映射冲突，不报三分量表。高分表示更认同嫉妒体验，不等于关系损害。',
    'mesx': '原研究为人类成人。内圈3/外圈2/边缘1/圈外0，求和0–30；描述关怀范围，不评判人格好坏。',
    'ppts': '原研究为18–38岁成人。4399描述性均分，非原量表标准分；拖延10题、提前完成8题分别取1–5均分，无反向、不合并，提前完成不倒分为拖延。',
    'rbi': '原验证为交往超过一年的以色列异性伴侣。4399三维描述性均分（1–5、无反向），非官方分数，不合成总分；高分仅指过去一周该类互动更符合，不是追人主动性或关系预测。',
    'rnss': '一般人际关系需求满足，不限恋爱；按论文表2，第18题按6−原分反向，五维及全题均分，越高表示越满足。',
    'romlon': '原研究为巴基斯坦成人；采用表1保留的4题，无反向、求和4–28，越高表示浪漫孤独感越多。',
    'ross': '原研究为成人且恋爱至少6个月。4399四维描述性均分（0–10、无反向），非官方分数，不合成总分；高分表示更认同该类应对，不代表关系优劣。',
    'rss': '原研究为有关系自我破坏经历的英语作答者。4399三维描述性均分（1–7），非官方因子回归加权分；9–12题按8−原分反向，高分表示更多相应困难，不合成总分。',
}

from cedar_backend.question_volume import metadata, guide_volume
QUESTION_VOLUME = {g:(v['variants'][0]['chars'],v['variants'][0]['tokens']) for g,v in metadata()['games'].items() if g in NOTES}


def guide(engine):
    game = engine.game
    options = '；'.join(f"{o['value']}={o['text']}" for o in engine.spec['options'])
    return f'''# {game} · {RESEARCH_TITLES[game]}
原名：{engine.title}
{len(engine.items)}题；中文为未验证翻译。{DISCLAIMER}无临床阈值或常模。
{NOTES[game]}
来源：{engine.attribution}
{engine.spec['instruction']}
选项（整数）：{options}
{guide_volume(game)}

调用 play(game="{game}", action=下列动作, params=对应对象)。登录可省player_id；游客生成唯一英数字ID（加guest:前缀并保存复用，不用保留的guest:web前缀）。登录可在params加slot=1–5，默认1。
- start {{"mode":"full"}}：逐题；改为full_fast一次答全题，同卷同分。start恢复已有记录。
- answer {{"session_id":"返回值","expected_index":0,"answer":{engine.answer_min}}}：full逐题；session_id沿用返回值，expected_index用最新progress（从0开始）。
- answer_batch {{"session_id":"返回值","answers":[{engine.answer_min},{engine.answer_max}]}}：full_fast；数组仅示意，替换为按返回题序的全部{len(engine.items)}个选项值，不自行反向。
- status {{}}：恢复进度；get_result {{}}：读取结果（完成时也自动返回result/result_data）。 游客进度24小时、结果48小时；账号结果长期保留。
- start {{"mode":"full_fast","session_id":"当前值","restart":true,"confirm":true}}：确认覆盖重测，先status取当前session_id。'''
