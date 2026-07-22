TYPE_NAMES = {
    "secure": "安全型",
    "fearful": "恐惧型",
    "preoccupied": "迷恋型（专注型）",
    "dismissive": "冷漠型",
}

TYPE_DESCRIPTIONS = {
    "secure": '''你在关系里的默认设定是"信"。对方晚回消息，你的第一反应是"在忙"而不是"完了"；需要帮助时你开得了口，对方需要空间时你也给得起。这不代表你没有不安，而是不安不会自动升级成灾难预警。

在关系中的样子：你是那种吵完架还愿意坐下来好好聊的人。冲突对你不是末日信号，是"有东西需要调一下"的提示音。你不会在亲密中感到窒息，也不会在距离中感到恐慌——你能在两者之间找到一个让彼此都舒服的位置，并且相信对方也愿意配合。你的伴侣大概率会觉得跟你在一起很安心，不用猜、不用演、不用小心翼翼地维护什么。

这种模式不是天生的运气，而是某些对的经历在你身上沉淀下来的结果。如果你身边有不安全型的人，你的稳定对他们来说就是锚——但请记得，锚不是不动，是在风浪里选择不走。''',
    "preoccupied": '''你渴望靠近，也擅长靠近，但靠近之后警报器就常亮：他是不是没那么在乎我了？

消息间隔、语气冷热、一句"随便"都能被你反复解码。你并不是在小题大做——你的信号接收器灵敏度设定得太高了，别人还没发射，你已经开始解读静默。你要的其实不多，只是"再确认一次"。问题是"一次"永远不够，因为确认带来的安心保质期很短，过了就得再来一份。

在关系中的样子：你爱得浓烈且全情投入，但有时候这份浓烈会变成对方的压力。你可能会不自觉地追问"你是不是不开心了""我做错什么了吗"——不是因为你不信任对方，而是你不够信任"被爱"这件事的持久性。伴侣如果回应得稳定且明确，你会是最好的恋人之一；但如果对方也不确定，你们可能会陷入一场互相追逐确认的消耗战。

给你的一句话：你不需要对方每分钟都在证明爱你。试试看，在下一次焦虑升起的时候，不急着找对方确认，而是先问自己——"有什么证据表明他不爱了吗？"如果没有，那焦虑只是旧习惯在响铃，不是事实在敲门。''',
    "dismissive": '''你不太担心被抛弃，因为你从一开始就没把重心全放在别人身上。独立是你的舒适区，"依赖"这个词让你皮肤发紧。

你习惯压抑情感需求，把"我不需要"当成自我保护的盔甲穿。别人说你冷，你委屈：我明明在啊。在是在，但门只开了一条缝——对方看得见你，够不着你。当感情升温到某个临界点，你会下意识地退后一步，不是因为不爱，是因为太近让你紧张。

在关系中的样子：你是那种"什么都好但就是够不着"的伴侣。你很少主动表达需要对方，也很少让对方看见你脆弱的样子。你的另一半可能会觉得——在你心里，独处比在一起更自在。这不完全是误读，但也不是事实的全部：你其实也想要温暖，只是不知道怎么在不丢掉自我的前提下接受它。

给你的一句话：打开那扇门不等于交出钥匙。让一个人走进来，不代表你就失去了走出去的自由。试着在下一次想退的时候，多留一秒——不是为了谁，是为了看看多留一秒之后会发生什么。''',
    "fearful": '''最拧巴的组合——你渴望亲密，又害怕亲密；怕被抛弃，也怕被靠太近。于是常常表现为"忽冷忽热"：凑上去，又弹开。

这不是善变，是两套警报系统在同时响。一套说"快靠近，不然他会走"，另一套说"太近了，你会受伤"。你被夹在中间，两边都听了，两边都没听全。结果就是——你最需要亲密的时候，反而是你最用力推开的时候。你的伴侣如果不理解这个结构，会觉得你在耍人；理解的人会知道，你不是不想留，是想留又不敢。

在关系中的样子：你可能需要比别人更长的时间才能真正信任一个人。稳定的关系对你来说不是起点，是证明——"这个人被我推过还没走，也许真的可以"。你可能会不自觉地"测试"对方：故意冷淡、制造距离，看对方会不会追过来。这不是故意伤害，是你在用笨办法验证安全感。但这种测试有代价——对方会累，而且每一次推开都在消耗信任存款。

给你的一句话：你比谁都需要一段稳定到无聊的关系来证明——靠近，不一定受伤。好消息是，模式不是判决书，它可以在安全的关系里慢慢重写。慢慢来，不着急，锚在这儿。''',
}

FISHER_COEFFICIENTS = {
    "secure": (3.2893296, 5.4725318, -11.5307833),
    "fearful": (7.2371075, 8.1776448, -32.3553266),
    "preoccupied": (3.9246754, 9.7102446, -28.4573220),
    "dismissive": (7.3654621, 4.9392039, -22.2281088),
}

FOOTNOTE = "依恋类型不是判决书，是当前的默认模式。模式是可以在安全的关系里慢慢重写的。"
SOURCE = "Experiences in Close Relationships（ECR），Brennan, Clark & Shaver (1998)；中文版修订：李同归、加藤和生 (2006)，《心理学报》38(03), 399-406。"


def axis_interpretation(avoidance, anxiety):
    if avoidance < 2.5 and anxiety < 2.5:
        return "两条警报线都很安静——安全感差不多是你的出厂配置。"
    if avoidance > 4.5 and anxiety > 4.5:
        return "两套警报常年同时响——辛苦了，你比谁都值得一段稳定的关系。"
    if anxiety - avoidance >= 0.5 and anxiety > 4.5:
        return "焦虑明显高过回避——比起嫌人近，你怕的是人走。"
    if anxiety - avoidance >= 0.5:
        return "焦虑略高于回避——比起嫌人近，你更怕人走。"
    if avoidance - anxiety >= 0.5 and avoidance > 4.5:
        return "回避明显高过焦虑——比起怕人走，你怕的是被贴得太近。"
    if avoidance - anxiety >= 0.5:
        return "回避略高于焦虑——比起怕人走，你更怕被贴得太近。"
    if avoidance >= 3.0 and anxiety >= 3.0:
        return "两轴都不低——想靠近又想退开，两股力气同时在拉，拧巴本身就是你的状态。"
    return "两轴不相上下——比起类型标签，更值得留意的是你此刻的状态。"


def fisher_discriminants(avoidance, anxiety):
    return {
        attachment_type: avoidance * a_coefficient + anxiety * b_coefficient + constant
        for attachment_type, (a_coefficient, b_coefficient, constant) in FISHER_COEFFICIENTS.items()
    }


def score_answers(questions, answers):
    if len(questions) != 36 or len(answers) != 36:
        raise ValueError("ecr requires exactly 36 answers")
    totals = {"avoidance": 0.0, "anxiety": 0.0}
    counts = {"avoidance": 0, "anxiety": 0}
    for question, answer in zip(questions, answers):
        if not isinstance(answer, int) or isinstance(answer, bool) or not 1 <= answer <= 7:
            raise ValueError(f"invalid answer {answer!r} for question {question['id']}")
        scored = 8 - answer if question["reverse"] else answer
        totals[question["dimension"]] += scored
        counts[question["dimension"]] += 1
    assert counts == {"avoidance": 18, "anxiety": 18}
    avoidance = totals["avoidance"] / 18.0
    anxiety = totals["anxiety"] / 18.0
    discriminants = fisher_discriminants(avoidance, anxiety)
    attachment_type = max(discriminants, key=discriminants.get)
    return {
        "result_value": attachment_type,
        "avoidance": avoidance,
        "anxiety": anxiety,
        "discriminants": discriminants,
        "axis_interpretation": axis_interpretation(avoidance, anxiety),
    }


def format_result(mode, result):
    attachment_type = result["result_value"]
    return "\n".join(
        [
            f"【依恋类型测试完成 · {mode}模式】",
            "",
            f"你的依恋类型：{TYPE_NAMES[attachment_type]}",
            f"回避均分 A：{result['avoidance']:.2f}（对亲近和依赖感到不适的程度）",
            f"焦虑均分 B：{result['anxiety']:.2f}（害怕被拒绝和被抛弃的程度）",
            "",
            f"轴解读：{result.get('axis_interpretation') or axis_interpretation(result['avoidance'], result['anxiety'])}",
            "",
            "━━━ 类型描述 ━━━",
            TYPE_DESCRIPTIONS[attachment_type],
            "",
            FOOTNOTE,
            "",
            f"来源：{SOURCE}",
            "（账号结果永久保留；游客结果存档 48 小时，可用 ecr_get_result 凭 player_id 查询。）",
        ]
    )


def format_stored_result(mode, result_value, detail, completed_at_label):
    result = {"result_value": result_value, **detail}
    text = format_result(mode, result).replace("依恋类型测试完成", "依恋类型历史结果", 1)
    return text.replace(
        "（账号结果永久保留；游客结果存档 48 小时，可用 ecr_get_result 凭 player_id 查询。）",
        f"完成时间：{completed_at_label}",
    )


def _person_data(player_id, result_value, detail):
    return {
        "player_id": player_id,
        "type": result_value,
        "type_name": TYPE_NAMES[result_value],
        "avoidance": float(detail["avoidance"]),
        "anxiety": float(detail["anxiety"]),
    }


_PAIR_MESSAGES = {
    ("secure", "secure"): '''教科书组合。你们都能靠近，也都给得起空间。冲突来了不会有人逃跑或追着不放，而是坐下来把话说开——听起来简单，但多数组合做不到这一步。

你们不需要时刻确认对方是否还在，因为"在"这件事不需要证据，它是空气一样的默认值。

唯一的风险是太稳定以至于忘了维护：别让"没什么问题"变成"没什么可聊"。好日子也需要偶尔翻新。''',
    ("fearful", "secure"): '''安全的一方是这段关系的锚。恐惧型是所有类型中最拧巴的——想靠近又怕受伤，被推开会痛但被靠近也会慌。

安全型能做的最重要的事不是"追着哄"，而是"稳定地在"：你推我我不走，你拉我我靠近，但我的节奏是恒定的，不会因为你的推拉而忽快忽慢。这种可预期的稳定，是恐惧型最稀缺的养分。

给恐惧的一方：你不需要一次性交出全部信任，可以一点一点给，每次给一点，看看对方接住没有——安全型会接住的。给安全的一方：别因为对方偶尔的推开而真的退后，那不是拒绝，是恐惧在替他按下了紧急制动。''',
    ("preoccupied", "secure"): '''安全的一方是这段关系里天然的稳压器。迷恋型需要反复确认"你还在不在"，而安全型恰好擅长给出明确、稳定、不打折扣的回应——不是敷衍的"我在"，是让人安心的"我在，而且我不会因为你多问了一句就烦"。

长期相处中，迷恋的一方会慢慢发现：不是每一次沉默都意味着被抛弃，有些人的沉默只是在想晚饭吃什么。

安全的一方需要注意的是：不要把对方的焦虑当作"又来了"，每一次确认请求背后都是真实的不安，回应它不花你什么，但对对方意味着全部。''',
    ("dismissive", "secure"): '''安全的一方像一扇永远不上锁的门——不追、不逼、但也不消失。冷漠型最怕的是被需要的压力，而安全型恰好不会制造这种压力：你需要空间我给你，你想回来门开着。

这种"不逼近也不撤退"的姿态，是冷漠型最容易接受的安全感形式。

长期相处的关键：安全的一方别把对方的独立解读为"不需要我"——他需要，只是不知道怎么开口；冷漠的一方试着在想退的时候多留一秒，说一句"我需要一点时间但我会回来"，这句话能省掉对方三天的猜。''',
    ("preoccupied", "preoccupied"): '''两个警报器都很灵敏的组合。

你们都渴望亲密、都害怕被忽略，所以关系的温度可以很高——但同样，任何一方的短暂沉默都可能被另一方解读为"他不在乎了"，然后触发连锁反应：一个人开始追问，另一个人因为被追问而焦虑，焦虑让他也开始追问，于是两个人在互相确认的漩涡里越转越快。

解药：约好一个"安全词"——当焦虑升起的时候，不是追问"你是不是不爱我了"，而是直接说"我现在需要你确认一下"。把焦虑翻译成需求，比把需求包装成质问，有效一百倍。''',
    ("dismissive", "preoccupied"): '''经典的"追逃循环"：一个在追，一个在退，追得越紧退得越远，退得越远追得越急。

迷恋型觉得"你不回应我就是不爱我"，冷漠型觉得"你追得太紧我喘不过气"——两个人都在用自己的方式保护自己，但保护的方式恰好是伤害对方的方式。这是所有组合中最容易产生消耗的一对，但也不是死局。

关键在于：追的一方练习"直接说我需要你"而不是绕圈子试探，退的一方练习"告诉对方你什么时候回来"而不是无声消失。把追逃翻译成对话，循环就能刹车。''',
    ("fearful", "preoccupied"): '''一方不断确认，一方想靠近又会弹开——节奏很容易失控。迷恋型的"你到底爱不爱我"撞上恐惧型的"我想说爱但我害怕说完你就会伤害我"，结果是两个人都在关系里用力过猛又收不住。

恐惧型的推开会激活迷恋型的焦虑，迷恋型的追问会激活恐惧型的退缩，于是进入一场越来越快的推拉。

实操建议：不要试图一次把安全感谈明白。建立小的、可兑现的承诺——"我今晚八点给你打电话""我周末陪你"——然后每次都做到。小承诺积累出来的信任，比任何长谈都扎实。''',
    ("dismissive", "dismissive"): '''你们都擅长独立，也都习惯把情绪需求藏在门背后。关系可能看起来很平静，甚至像室友多过恋人——不是因为不爱，是因为两个人都在等对方先开口，然后都等到了沉默。

这种组合的和平可能是真的舒适，也可能是两个人各自关着门以为对方不需要自己。风险是：等到问题大到不得不谈的时候，才发现你们已经很久没练习过"说出来"这件事了。

建议定期主动打开门——不用等到有问题。"最近怎么样""有没有什么想跟我说的"，这种看似没用的废话，就是在给沉默通风。''',
    ("dismissive", "fearful"): '''一个习惯退开，一个既想追又怕受伤。

冷漠型的沉默在恐惧型眼里会被无限放大成拒绝，恐惧型的推拉在冷漠型眼里会变成"太复杂了我搞不定"。两个人都不太会主动表达需要，但不表达不代表没有——只是都在用各自的方式硬撑。

这个组合需要的不是热烈，而是"说具体"：空间要多久、什么时候重新连线、需要对方做什么而不是猜。把模糊的不安翻译成具体的请求，关系才不会一直悬在半空。''',
    ("fearful", "fearful"): '''两套靠近与退缩的开关都在反复切换。你们理解彼此的拧巴——这是这个组合最珍贵的地方，因为只有同样害怕的人才真正懂"想留又不敢"是什么感觉。

但理解不等于不会互相触发：你的退缩可能激活他的恐慌，他的试探可能激活你的防御，然后两个人在镜子迷宫里互相惊吓。

这对组合的生存法则只有一个字：慢。慢一点靠近，慢一点信任，用可兑现的小承诺代替宏大的告白。不急，你们比谁都清楚，信任不是一次建成的，但每多一天没被伤害，地基就厚一层。''',
}

assert len(_PAIR_MESSAGES) == 10
assert {tuple(sorted(pair)) for pair in _PAIR_MESSAGES} == {
    tuple(sorted((left, right)))
    for index, left in enumerate(TYPE_NAMES)
    for right in tuple(TYPE_NAMES)[index:]
}


def _pair_key(type_a, type_b):
    for key in _PAIR_MESSAGES:
        if sorted(key) == sorted((type_a, type_b)):
            return key
    raise KeyError((type_a, type_b))


def build_compare_data(player_id_a, result_a, detail_a, player_id_b, result_b, detail_b):
    a = _person_data(player_id_a, result_a, detail_a)
    b = _person_data(player_id_b, result_b, detail_b)
    return {
        "kind": "ecr_compare",
        "player_a": a,
        "player_b": b,
        "combination": f"{a['type_name']} × {b['type_name']}",
        "message": _PAIR_MESSAGES[_pair_key(a["type"], b["type"])],
        "source": SOURCE,
    }


def format_compare(data):
    a = data["player_a"]
    b = data["player_b"]
    name_a = a.get("display_name") or a["player_id"]
    name_b = b.get("display_name") or b["player_id"]
    return "\n".join(
        [
            "【依恋类型双人对测】",
            "",
            f"A · {name_a}：回避 {a['avoidance']:.2f} / 焦虑 {a['anxiety']:.2f} / {a['type_name']}",
            f"B · {name_b}：回避 {b['avoidance']:.2f} / 焦虑 {b['anxiety']:.2f} / {b['type_name']}",
            "",
            f"组合：{data['combination']}",
            data["message"],
            "",
            f"来源：{data['source']}",
        ]
    )


# Formula checksum: coefficients stay verbatim, and neutral responses produce the known four M values.
assert FISHER_COEFFICIENTS == {
    "secure": (3.2893296, 5.4725318, -11.5307833),
    "fearful": (7.2371075, 8.1776448, -32.3553266),
    "preoccupied": (3.9246754, 9.7102446, -28.4573220),
    "dismissive": (7.3654621, 4.9392039, -22.2281088),
}
_neutral_discriminants = fisher_discriminants(4.0, 4.0)
assert abs(_neutral_discriminants["secure"] - 23.5166623) < 1e-12
assert abs(_neutral_discriminants["fearful"] - 29.3036826) < 1e-12
assert abs(_neutral_discriminants["preoccupied"] - 26.0823580) < 1e-12
assert abs(_neutral_discriminants["dismissive"] - 26.9905552) < 1e-12
