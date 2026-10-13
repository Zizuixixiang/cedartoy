"""Load independently maintained question packages; never synthesize questions."""
import json
from pathlib import Path
from cedar_backend.question_volume import guide_volume
from cedar_backend.display_titles import DEEPTALK_TITLES

DATA_DIR = Path(__file__).resolve().parent / "data"
TOPICS = {
    "relations_communication": ("关系与沟通", 13, "边界、倾听、同意与沟通"),
    "couple_50": ("恋人之间的50问", 50, "恋人日常相处、偏好与情感表达"),
    "relationship_scenarios": ("亲密关系情境", 14, "假设关系两难的处置、边界与冲突"),
    "romance_values_100": ("恋爱观100问", 100, "全面恋爱观：吃醋、信任、同居与未来规划"),
    "work_values_20": ("工作价值观20问", 20, "职业抉择与工作价值取舍"),
    "favorites_100": ("喜欢的事物100问", 100, "日常兴趣与喜好"),
    "getting_to_know_100": ("相互了解100问", 100, "生活背景与相互了解"),
    "life_reflection_35": ("人生与内心35问", 35, "价值观、人生经历与内心反思"),
    "real_life_dilemmas_30": ("现实难题30问", 30, "如何回应虚构烦恼与现实两难"),
    "learning_goals_problem_solving": ("学习、目标与解决问题", 28, "个人目标、学习复盘与问题分析"),
}

# Stable discovery IDs; storage and web topic IDs remain unchanged.
ALIASES = {
    "dt_relations": "relations_communication",
    "dt_couple50": "couple_50",
    "dt_scenarios": "relationship_scenarios",
    "dt_romance100": "romance_values_100",
    "dt_work20": "work_values_20",
    "dt_favorites100": "favorites_100",
    "dt_know100": "getting_to_know_100",
    "dt_life35": "life_reflection_35",
    "dt_dilemmas30": "real_life_dilemmas_30",
    "dt_learning_goals": "learning_goals_problem_solving",
}


def discovery():
    return "深聊问答:\n" + "\n".join(
        f"{game}·{DEEPTALK_TITLES[tid]}·{TOPICS[tid][2]}" for game, tid in ALIASES.items()
    )


class DeeptalkError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message, self.status = message, status


def require(condition):
    if not condition:
        raise ValueError("Invalid question package")


def load_topic(topic_id):
    if not isinstance(topic_id, str) or topic_id not in TOPICS:
        raise DeeptalkError("未知主题")
    try:
        raw = json.loads((DATA_DIR / f"{topic_id}.json").read_text(encoding="utf-8"))
        title, count, _ = TOPICS[topic_id]
        require(isinstance(raw, dict) and raw["topic_id"] == topic_id and raw["title"] == title)
        for key in ("description", "author", "source_url", "license", "license_url", "adaptation_note"):
            require(key in raw and isinstance(raw[key], str))
        questions = raw["questions"]
        require(isinstance(questions, list) and len(questions) == count)
        ids = set()
        for q in questions:
            require(isinstance(q, dict) and type(q["id"]) in (str, int))
            qid = str(q["id"])
            require(qid and len(qid) <= 100 and qid not in ids)
            require(isinstance(q["text"], str) and q["text"].strip() and "source_ref" in q)
            ids.add(qid)
            q["id"] = qid
        return raw
    except (OSError, ValueError, KeyError, TypeError):
        raise DeeptalkError("这套题库尚未就绪，请稍后再试", 503) from None


def topics():
    result = []
    for topic_id, (title, total, description) in TOPICS.items():
        try:
            load_topic(topic_id)
            available = True
        except DeeptalkError:
            available = False
        result.append(dict(topic_id=topic_id, title=DEEPTALK_TITLES[topic_id], total=total,
                           description=description, available=available))
    return result


# Presentation only: keep the complete licensing/provenance records in packages
# and round snapshots. Do not render internal editorial notes in the human UI.
PUBLIC_NOTES = {
    "relations_communication": "采用官方简体中文讲义；其中教材题由 CedarToy 翻译，非官方译文。合并排版换行及部分连续追问。",
    "relationship_scenarios": "采用官方简体中文讲义，仅整理排版。原题来自 SFU InterroBang，保留非商业许可限制。",
    "learning_goals_problem_solving": "CedarToy 中文翻译与编排，非官方译文。工作表部分栏目改为开放问句，未采用评分栏。",
    "life_reflection_35": "CedarToy 中文自译，非官方译文；仅采用传统问句，不涵盖参考网页的现代释义与正文。",
    "favorites_100": "CedarToy 中文自译，非官方译文；个别品牌名称补充释义。",
    "getting_to_know_100": "CedarToy 中文自译，非官方译文；保留原题重复条目并附必要译注。",
    "romance_values_100": "CedarToy 中文自译，非官方译文；个别日语俗称补充释义。",
}
SOURCE_NAMES = {
    "Tool D-1. Personal Goal-Setting Worksheet": "个人目标（D-1）",
    "Tool D-2. Weekly Reflection Sheet": "每周复盘（D-2）",
    "Tool D-3. Soft-Skills Self-Assessment": "能力举例（D-3）",
    "Tool D-4. Visual Decision Organizer (Optional)": "问题分析（D-4）",
    "ZH_H1.pdf": "沟通、健康关系与同意 · 第1单元",
    "ZH_H3.pdf": "沟通、健康关系与同意 · 第3单元",
    "interpersonalcommunicationcontextandconnectionascccoeri.pdf": "Interpersonal Communication: Context and Connection · 第6.8节（PDF）",
}


def public_source(topic):
    def entry(source, name):
        return dict(name=SOURCE_NAMES.get(source.get("name"), source.get("name")) or name,
                    **{k: source.get(k, topic.get(k, "")) for k in ("author", "source_url", "license", "license_url")})

    sources = topic.get("sources") or [topic]
    entries = [entry(source, SOURCE_NAMES.get(source.get("source_url", "").rsplit("/", 1)[-1], topic["title"]))
               for source in sources]
    if topic["topic_id"] == "relationship_scenarios":
        entries.append(dict(name="InterroBang · 原题与许可", author="Sexual Violence Support & Prevention Office, Simon Fraser University",
                            source_url="https://www.sfu.ca/sexual-violence/education-prevention/interrobang/print---play-.html",
                            license=topic["license"], license_url=topic["license_url"]))
    grouped = {}
    for item in entries:
        key = tuple(item[k] for k in ("author", "source_url", "license", "license_url"))
        if key in grouped:
            grouped[key]["name"] += "、" + item["name"]
        else:
            grouped[key] = item
    return dict(title=topic["title"], entries=list(grouped.values()),
                note=PUBLIC_NOTES.get(topic["topic_id"], "CedarToy 中文翻译与编排，非官方译文。"))


VISIBILITY_GUIDE = "新轮次仅边聊边答（chat）：对方每题answer保存或skip=true跳过后，question/result即可读取；双方可商量答完再看。旧blind（答完再看）仍一方整卷submit后该侧可读，未提交侧隐藏。"


def guide(game):
    topic = load_topic(ALIASES[game])
    title, total, description = TOPICS[topic["topic_id"]]
    urls = list(dict.fromkeys([topic["source_url"]] + [s["source_url"] for s in topic.get("sources", [])]))
    license_ref = "" if topic['license_url'] in urls else f"（{topic['license_url']}）"
    return f'''# {game} · {DEEPTALK_TITLES[topic["topic_id"]]}（{total}题）
题包原名：{title}
{description}。开放问答，无标准答案，不计分，可跳题。
{guide_volume(game)}
作者：{topic['author']}
许可：{topic['license']}{license_ref}
原网址：{'；'.join(urls)}
play(game="{game}",action=...,params={{...}})；以下为action:参数。
questions:number从1起，可选limit；source查来源。
人类首页/deeptalk开局；history取round_id（可选topic_id、offset/limit）；status:round_id取revision；question:round_id,number查题。
answer:round_id,question_id,revision,request_id，加text或skip=true。request_id须8–100位[A-Za-z0-9_-]，重试复用。
全题答/跳后submit:round_id,revision,confirm=true。{VISIBILITY_GUIDE}result:round_id，可选number/limit；peer_visible表示对方可读，unlocked仅双方提交为true。当前绑定小机共用草稿/revision，任一可提交，解绑失权；旧single_ai轮次限原双方。冲突先重读合并；无测评报告。
'''


GUIDE = """# deeptalk · 兼容入口
旧 play(game="deeptalk",action=...,params={...}) 仍可用：topics查topic_id；questions/source须传topic_id，读题number从1起、limit默认1最多100。
优先使用下列game ID，get_guide(game)按需取来源与完整操作。无标准答案、不计分、无测评报告。人类从首页开局；绑定小机共用答卷/revision，任一可提交，解绑失权；旧single_ai轮次限原双方。
history取round_id；status:round_id取revision；question/result:round_id，可选number/limit；answer:round_id,question_id,revision,request_id，加text或skip=true；全题答/跳后submit:round_id,revision,confirm=true。request_id须8–100位[A-Za-z0-9_-]，重试复用。peer_visible表示对方可读，unlocked仅双方提交为true。
""" + VISIBILITY_GUIDE + "\n" + "\n".join(f"{game} → {tid}" for game, tid in ALIASES.items())
