"""Original fixed-order love-language question bank.

The option order below is intentionally mixed and persisted in the data.  Do not
sort options by dimension: the design explicitly forbids presenting every pair
in the review-document order.
"""

VALID_MODES = ("full", "full_fast")
FAST_BATCH_SIZE_MAX = 30

DIMENSIONS = {
    "A": "肯定言辞",
    "B": "优质时间",
    "C": "服务行动",
    "D": "赠送礼物",
    "E": "身体接触",
}

_RAW = [
    ("A", "他直白地说\"我很想你\"", "B", "他推掉别的事，整晚只陪我"),
    ("A", "被他认真夸\"你今天真的很棒\"", "B", "他放下手头的事，专心听我讲完一整件事"),
    ("A", "突然收到他发来的告白长文", "B", "和他什么都不干，纯粹待在一起消磨一下午"),
    ("A", "他把爱挂在嘴边，从不吝啬表达", "C", "我还没开口，他就把我头疼的事默默办好了"),
    ("A", "他写长长的小作文夸我", "C", "他记得我随口提过的麻烦事，主动帮我解决了"),
    ("A", "吵架后他先低头，好好说软话", "C", "吵架后他一声不吭，去把我念叨很久的事干了"),
    ("A", "一句正好戳中我的\"我爱你\"", "D", "一份\"我看到它就想到你\"的小礼物"),
    ("A", "纪念日他写给我的话", "D", "纪念日他准备的礼物"),
    ("A", "他嘴甜，会哄人", "D", "他遇到什么好东西，总惦记着给我也弄一份"),
    ("A", "他用语言表达喜欢", "E", "他用拥抱表达喜欢"),
    ("A", "低落时他耐心开导我、安慰我", "E", "低落时他什么也不说，就抱着我"),
    ("A", "他夸我可爱", "E", "他忍不住揉我脑袋"),
    ("B", "他陪我做我喜欢的事，哪怕他不感兴趣", "C", "他包揽杂事，让我腾出手做喜欢的事"),
    ("B", "他专程留出\"只属于我们俩\"的时间", "C", "他把我的生活打理得井井有条"),
    ("B", "睡前和他的长聊", "C", "醒来发现他把我今天要用的东西都备好了"),
    ("B", "一场他筹划已久、只属于我俩的活动", "D", "一件筹划已久的贵重礼物"),
    ("B", "他花一下午陪我", "D", "他花一下午给我挑礼物"),
    ("B", "生日当天他全程陪着我", "D", "生日收到他精心准备的惊喜"),
    ("B", "促膝长谈到深夜", "E", "靠在一起各干各的，肩膀挨着肩膀"),
    ("B", "他认真和我讨论我关心的话题", "E", "他路过时顺手捏捏我的脸"),
    ("B", "他把整个周末空出来给我", "E", "他睡觉时一定要挨着我"),
    ("C", "他花时间亲手为我做一样东西", "D", "他直接买下我惦记很久的东西"),
    ("C", "我状态很糟时，他忙前忙后地照料我", "D", "我状态很糟时，他变着法儿买东西哄我"),
    ("C", "他帮我分担，让我省心", "D", "他送我礼物，给我惊喜"),
    ("C", "他默默把我的琐事都处理好", "E", "他见缝插针地亲我一下"),
    ("C", "累了一天回来，发现他把一切都收拾好了", "E", "累了一天回来，他张开手臂让我扑进去"),
    ("C", "他行动上靠谱，事事有着落", "E", "他肢体上黏人，时时想贴着我"),
    ("D", "他送的东西，我从不离身", "E", "他牵我的手，从不先撒开"),
    ("D", "拆开他准备的惊喜", "E", "窝在他怀里"),
    ("D", "他记得我的愿望清单", "E", "他记得我喜欢被摸头"),
]

# A fixed, reviewable shuffle: 15 of 30 questions reverse the design-document order.
_SWAPPED = frozenset({2, 3, 5, 8, 10, 12, 13, 16, 18, 19, 23, 25, 27, 29, 30})


def _build_questions():
    questions = []
    for question_id, (left_dim, left_text, right_dim, right_text) in enumerate(_RAW, 1):
        ordered = [(left_dim, left_text), (right_dim, right_text)]
        if question_id in _SWAPPED:
            ordered.reverse()
        questions.append(
            {
                "id": question_id,
                "text": "以下两种情境，选更让你心里一动的那个。",
                "options": [
                    {"value": index, "text": text, "dimension": dimension}
                    for index, (dimension, text) in enumerate(ordered, 1)
                ],
            }
        )
    return questions


QUESTIONS = _build_questions()


def get_questions(mode):
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    return QUESTIONS


def is_fast_mode(mode):
    return mode == "full_fast"


def fast_batch_size(mode):
    return len(QUESTIONS) if is_fast_mode(mode) else 1


# Design checksums: 30 forced choices, every pair three times, every dimension 12 appearances.
assert len(QUESTIONS) == 30
assert len(_SWAPPED) == 15
_pair_counts = {}
_dimension_counts = {dimension: 0 for dimension in DIMENSIONS}
for _question in QUESTIONS:
    _dimensions = tuple(option["dimension"] for option in _question["options"])
    assert len(set(_dimensions)) == 2
    _pair = tuple(sorted(_dimensions))
    _pair_counts[_pair] = _pair_counts.get(_pair, 0) + 1
    for _dimension in _dimensions:
        _dimension_counts[_dimension] += 1
assert len(_pair_counts) == 10 and set(_pair_counts.values()) == {3}
assert set(_dimension_counts.values()) == {12}
