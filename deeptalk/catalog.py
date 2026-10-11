"""Load independently maintained question packages; never synthesize questions."""
import json
from pathlib import Path
from cedar_backend.question_volume import guide_volume

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
        f"{game}·{TOPICS[tid][0]}·{TOPICS[tid][2]}" for game, tid in ALIASES.items()
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
        result.append(dict(topic_id=topic_id, title=title, total=total,
                           description=description, available=available))
    return result


def guide(game):
    topic = load_topic(ALIASES[game])
    title, total, description = TOPICS[topic["topic_id"]]
    urls = list(dict.fromkeys([topic["source_url"]] + [s["source_url"] for s in topic.get("sources", [])]))
    license_ref = "" if topic['license_url'] in urls else f"（{topic['license_url']}）"
    return f'''# {game} · {title}（{total}题）
{description}。开放问答，无标准答案，不计分，可跳题。
{guide_volume(game)}
作者：{topic['author']}
许可：{topic['license']}{license_ref}
原网址：{'；'.join(urls)}
play(game="{game}",action=...,params={{...}})；以下为action:参数。
边聊 questions:number从1起，可选limit；原聊天软件讨论。source查来源。
答完再看：人类/deeptalk开局；history取round_id；status:round_id取revision；question:round_id,number查题。
answer:round_id,question_id,revision,request_id，加text或skip=true。request_id须8–100位[A-Za-z0-9_-]，重试复用。
全题答/跳后submit:round_id,revision,confirm=true；双方提交才解锁result:round_id，可选number/limit。仅参与双方可见；result只对照双方回答，无评分或测评报告。
'''


GUIDE = """# deeptalk · 兼容入口
旧 play(game="deeptalk",action=...,params={...}) 仍可用：topics查topic_id；questions/source须传topic_id，读题number从1起、limit默认1最多100。
新调用优先使用下列独立game ID，get_guide(game)按需获取该套来源和完整操作。开放问答无标准答案、不计分；result仅对照双方回答，无测评报告；人类在 /deeptalk 创建轮次；双方正式提交才解锁。边聊边答在现有聊天软件中进行。
""" + "\n".join(f"{game} → {tid}" for game, tid in ALIASES.items())
