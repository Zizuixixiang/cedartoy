"""The 36-item Chinese ECR scale.  Item text must remain byte-for-byte unchanged."""

VALID_MODES = ("full", "full_fast")
FAST_BATCH_SIZE_MAX = 36
REVERSE_ITEMS = frozenset({3, 15, 19, 22, 25, 27, 29, 31, 33, 35})

ITEM_TEXTS = (
    "总的来说，我不喜欢让恋人知道自己内心深处的感觉",
    "我担心我会被抛弃",
    "我觉得跟恋人亲近是一件惬意的事情",
    "我很担心我的恋爱关系",
    "当恋人开始要跟我亲近时，我发现我自己在退缩",
    "我担心恋人不会像我关心他（她）那样地关心我",
    "当恋人希望跟我非常亲近时，我会觉得不自在",
    "我有点担心会失去恋人",
    "我觉得对恋人开诚布公，不是一件很舒服的事情",
    "我常常希望恋人对我的感情和我对恋人的感情一样强烈",
    "我想与恋人亲近，但我又总是会退缩不前",
    "我常常想与恋人形影不离，但有时这样会把恋人吓跑",
    "当恋人跟我过分亲密的时候，我会感到内心紧张",
    "我担心一个人独处",
    "我愿意把我内心的想法和感觉告诉恋人，我觉得这是一件自在的事情",
    "我想跟恋人非常亲密的愿望，有时会把恋人吓跑",
    "我试图避免与恋人变得太亲近",
    "我需要我的恋人一再地保证他/她是爱我的",
    "我觉得我比较容易与恋人亲近",
    "我觉得自己在要求恋人把更多的感觉，以及对恋爱关系的投入程度表现出来",
    "我发现让我依赖恋人，是一件困难的事情",
    "我并不是常常担心被恋人抛弃",
    "我倾向于不跟恋人过分亲密",
    "如果我无法得到恋人的注意和关心，我会心烦意乱或者生气",
    "我跟恋人什么事情都讲",
    "我发现恋人并不愿意像我所想的那样跟我亲近",
    "我经常与恋人讨论我所遇到的问题以及我关心的事情",
    "如果我还没有恋人的话，我会感到有点焦虑和不安",
    "我觉得依赖恋人是很自在的事情",
    "如果恋人不能像我所希望的那样在我身边时，我会感到灰心丧气",
    "我并不在意从恋人那里寻找安慰，听取劝告，得到帮助",
    "如果在我需要的时候，恋人却不在我身边，我会感到沮丧",
    "在需要的时候，我向恋人求助，是很有用的",
    "当恋人不赞同我时，我觉得确实是我不好",
    "我会在很多事情上向恋人求助，包括寻求安慰和得到承诺",
    "当恋人不花时间和我在一起时，我会感到怨恨",
)

SCALE_OPTIONS = (
    (1, "非常不同意"),
    (2, "不同意"),
    (3, "有点不同意"),
    (4, "中立"),
    (5, "有点同意"),
    (6, "同意"),
    (7, "非常同意"),
)

QUESTIONS = [
    {
        "id": item_id,
        "text": text,
        "dimension": "avoidance" if item_id % 2 else "anxiety",
        "reverse": item_id in REVERSE_ITEMS,
        "options": [{"value": value, "text": label} for value, label in SCALE_OPTIONS],
    }
    for item_id, text in enumerate(ITEM_TEXTS, 1)
]


def get_questions(mode):
    if mode not in VALID_MODES:
        raise ValueError(f"unsupported mode: {mode}")
    return QUESTIONS


def is_fast_mode(mode):
    return mode == "full_fast"


def fast_batch_size(mode):
    return len(QUESTIONS) if is_fast_mode(mode) else 1


# Academic-scale checksums from the implementation specification.
assert len(ITEM_TEXTS) == 36
assert tuple(question["id"] for question in QUESTIONS if question["dimension"] == "avoidance") == tuple(range(1, 37, 2))
assert tuple(question["id"] for question in QUESTIONS if question["dimension"] == "anxiety") == tuple(range(2, 37, 2))
assert frozenset(question["id"] for question in QUESTIONS if question["reverse"]) == REVERSE_ITEMS
assert len(REVERSE_ITEMS) == 10
assert tuple(question["id"] for question in QUESTIONS if question["dimension"] == "anxiety" and question["reverse"]) == (22,)
