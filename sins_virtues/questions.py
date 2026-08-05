"""Original fixed-order Chinese question bank for the entertainment test."""

VALID_MODES = ("full", "full_fast")
FAST_BATCH_SIZE_MAX = 35

DISCLAIMER = "仅供娱乐；不是心理诊断，也不代表道德评价。"

SINS = {
    "lust": "色欲",
    "gluttony": "暴食",
    "greed": "贪婪",
    "sloth": "懒惰",
    "wrath": "暴怒",
    "envy": "嫉妒",
    "pride": "傲慢",
}

VIRTUES = {
    "chastity": "贞洁",
    "temperance": "节制",
    "generosity": "慷慨",
    "diligence": "勤勉",
    "patience": "耐心",
    "kindness": "仁爱",
    "humility": "谦卑",
}

PAIRS = (
    ("lust_chastity", "lust", "chastity"),
    ("gluttony_temperance", "gluttony", "temperance"),
    ("greed_generosity", "greed", "generosity"),
    ("sloth_diligence", "sloth", "diligence"),
    ("wrath_patience", "wrath", "patience"),
    ("envy_kindness", "envy", "kindness"),
    ("pride_humility", "pride", "humility"),
)

SCALE_OPTIONS = (
    (1, "非常不同意"),
    (2, "不同意"),
    (3, "不确定 / 看情况"),
    (4, "同意"),
    (5, "非常同意"),
)


def _items(pair, sin, virtue, statements):
    """Build one pair's two direct, two reverse, and one coexistence item."""
    sin_direct, virtue_direct, sin_reverse, virtue_reverse, coexistence = statements
    return [
        (pair, sin_direct, {sin: 1}, "direct"),
        (pair, virtue_direct, {virtue: 1}, "direct"),
        (pair, sin_reverse, {sin: -1}, "reverse"),
        (pair, virtue_reverse, {virtue: -1}, "reverse"),
        (pair, coexistence, {sin: 1, virtue: 1}, "coexistence"),
    ]


_GROUPED = [
    *_items(
        "lust_chastity",
        "lust",
        "chastity",
        (
            "遇到很有吸引力的人或角色时，我很容易把注意力停在暧昧想象上。",
            "即使很心动，我也会先确认彼此的边界，再决定靠近多少。",
            "面对心动对象，我通常很少被暧昧想象带着跑。",
            "只要气氛到了，我不太会停下来检查彼此是否都舒服。",
            "我可以享受强烈的吸引，同时把克制与尊重也当成乐趣的一部分。",
        ),
    ),
    *_items(
        "gluttony_temperance",
        "gluttony",
        "temperance",
        (
            "碰到特别好吃、好玩或上头的东西，我常想再来一点，即使已经够了。",
            "享受一件事时，我通常知道什么时候停下，给身体和时间留余地。",
            "再喜欢的东西，我也很少因为停不下来而挤掉别的安排。",
            "一旦开始享受，我往往懒得管之后会不会过量。",
            "我既有很响亮的胃口，也能在真正需要时把手收回来。",
        ),
    ),
    *_items(
        "greed_generosity",
        "greed",
        "generosity",
        (
            "资源有限时，我会本能地多留一点在自己手里，免得以后吃亏。",
            "发现自己有余力时，我愿意把时间、机会或好东西分给别人。",
            "拿到一份好处后，我通常不会继续盘算怎样再多占一点。",
            "即使资源充裕，我也更倾向先把自己的份额锁稳再说。",
            "我会积极争取想要的东西，也愿意在拿到以后分出一部分。",
        ),
    ),
    *_items(
        "sloth_diligence",
        "sloth",
        "diligence",
        (
            "明知有件重要的事该开始，我还是可能先用小事把启动时间往后推。",
            "哪怕任务不有趣，我也能靠一点点推进把它做完。",
            "待办已经明确时，我通常能很快动手，而不是继续等状态。",
            "如果没有人催，一个答应过的任务可以被我拖很久。",
            "我允许自己认真休息，也能在休息够了以后起身把事情收尾。",
        ),
    ),
    *_items(
        "wrath_patience",
        "wrath",
        "patience",
        (
            "遇到不公平或被冒犯时，我的火气会比解释更快抵达现场。",
            "别人犯下可修正的错时，我愿意给对方一次说明和改正的机会。",
            "即使被冒犯，我也很少立刻进入战斗状态。",
            "同一件事解释第二遍还没被听懂，我就容易明显不耐烦。",
            "我能让愤怒提醒我守住边界，也能留一点时间听完对方的话。",
        ),
    ),
    *_items(
        "envy_kindness",
        "envy",
        "kindness",
        (
            "看到与我相近的人得到我想要的东西，我会忍不住比较自己哪里输了。",
            "身边的人迎来好事时，我通常能真心替对方高兴。",
            "别人过得亮眼时，我很少因此觉得自己的生活被比暗了。",
            "如果对方得到的正是我求而不得的东西，我很难给出真诚祝福。",
            "我会听见羡慕在说什么，也仍然愿意善待那个让我羡慕的人。",
        ),
    ),
    *_items(
        "pride_humility",
        "pride",
        "humility",
        (
            "事情做得漂亮时，我希望别人清楚知道其中主要有我的功劳。",
            "发现自己判断错了时，我能够直接承认，并更新原来的看法。",
            "我很少需要靠赢过别人来证明自己重要。",
            "受到质疑时，我通常先证明对方错了，而不是检查自己漏了什么。",
            "我可以为自己的本事感到骄傲，也愿意承认许多成果离不开别人。",
        ),
    ),
]

# Fixed, reviewable shuffle: no pair appears twice in a row.
_ORDER = (
    0, 16, 27, 8, 24, 31, 12,
    18, 4, 25, 11, 33, 7, 20,
    29, 2, 19, 30, 14, 6, 23,
    32, 10, 1, 28, 17, 21, 9,
    34, 13, 3, 26, 15, 22, 5,
)

QUESTIONS = [
    {
        "id": question_id,
        "text": _GROUPED[source_index][1],
        "pair": _GROUPED[source_index][0],
        "loadings": dict(_GROUPED[source_index][2]),
        "direction": _GROUPED[source_index][3],
        "options": [
            {"value": value, "text": label} for value, label in SCALE_OPTIONS
        ],
    }
    for question_id, source_index in enumerate(_ORDER, 1)
]


def get_questions(mode):
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    return QUESTIONS


def is_fast_mode(mode):
    return mode == "full_fast"


def fast_batch_size(mode):
    return len(QUESTIONS) if is_fast_mode(mode) else 1


assert len(QUESTIONS) == 35
assert sorted(_ORDER) == list(range(35))
assert all(QUESTIONS[index]["pair"] != QUESTIONS[index + 1]["pair"] for index in range(34))
assert {question["pair"] for question in QUESTIONS} == {pair for pair, _sin, _virtue in PAIRS}
assert all(sum(question["pair"] == pair for question in QUESTIONS) == 5 for pair, _sin, _virtue in PAIRS)
for _dimension in (*SINS, *VIRTUES):
    _dimension_items = [question for question in QUESTIONS if _dimension in question["loadings"]]
    assert len(_dimension_items) == 3
    assert {question["loadings"][_dimension] for question in _dimension_items} == {-1, 1}
