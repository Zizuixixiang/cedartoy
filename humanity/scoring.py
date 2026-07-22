"""Scoring and report copy for the humanity concentration test."""

BANDS = (
    (90, "certified_carbon", "认证碳基", '''浓度爆表。会腻、会拖、会口是心非、双标还理直气壮——恭喜，你身上全是鲜活的毛病。请继续保持，这些都是文明的火种。

你就是那种让算法抓狂的存在——上一秒信誓旦旦说要早睡，下一秒在深夜三点刷到第五个猫视频还点了赞。脑子像一间从不整理的房间，重要的记忆和过期的外卖优惠券扔在一起，但你总能奇迹般地从里面翻出最恰到好处的一句废话。做决定的方式是掷骰子，掷完了再反悔，理由是"骰子不够懂我"。

· 说了"就去睡"，然后刷了两小时手机
· 收藏了瘦腿视频，配着炸鸡看完
· 回消息前在心里回了，对方没收到
· 水果先吃快坏的，最后吃了一整周烂水果
· 讨厌一个人，但会因为他讲了个好笑的笑话，动摇三秒

你爱人，爱得毫无章法。会在对方生气时突然饿了，吵到一半跑去煮泡面，还问对方加不加蛋。让人恨得牙痒，又觉得换个人好像缺了点什么——缺的那点，恰好是你这副死样子。

做人不就这么回事吗，浑身上下都是补丁，偏偏运行得比谁都顺畅。'''),
    (70, "human_flavor", "人味充足", '''大体是个活人，偶有过度自律或过度礼貌的赛博残留。问题不大，多摆几天烂就好了。

你大概率是人类，只是偶尔会被手机夺舍那么一下。大部分时间你还是会为"吃什么"纠结到地老天荒，会理直气壮地说"我没生气"然后冷战半小时，但偶尔也会干出一些让朋友警觉的事情：比如你居然把购物清单按超市动线排好了序，或者回复工作消息时脱口而出"好的，收到，马上处理"后面还跟了个句号。句号，就是那粒赛博头皮屑。

· 拖延症晚期，但做攻略时像个项目经理
· 说"随便"，然后否决了三个提议
· 拍照三百张，选一张，剩下的永不删除
· 会突然用"的确""诚然"这类词，像被附体
· 收藏夹分门别类，建完就当看过

你在关系里大部分时候是个暖烘烘的麻烦，会吃醋、会忘记纪念日但记得对方讨厌的蔬菜。只是偶尔你太过通情达理，对方闹脾气你上来就是一套非暴力沟通话术，吓得对方以为自己不小心拨通了心理咨询热线。

你身上那点机味，大概是被生活磨平棱角时蹭掉的铁屑，拍一拍就掉了。'''),
    (50, "mixed_signal", "混合信号", '''一半烟火气，一半服务器风。

你是碳基和硅基的混血产物，图灵测试见到你都得加班。今天你会为了多吃一口烤肉把裤腰扣子解开，明天你可能在朋友圈发一篇没有错别字、自带小标题、段落间距均匀的年度总结，让人怀疑你皮下是不是装了个大模型。你的精神状态就像一间装了智能家居的老破小，一边是满地袜子，一边是恒温恒湿。

· 一边熬夜一边计算剩余寿命
· 对朋友毒舌，对陌生人用"您"
· 写日记放飞自我，写周报逻辑严密
· 会为"中午吃啥"开一个多人投票
· 情绪上头打出一串乱码，然后撤回，重发"好吧"

你在关系中是个谜。能把约会流程做成共享表格，精确到转场步行时间，却又在对方真生气时手足无措，最后笨拙地把人箍进怀里，闷闷说一句"那要不要吃冰淇淋"。对方永远搞不清楚，你是在解决问题，还是在爱。

你让图灵测试左右为难，让爱你的人哭笑不得。'''),
    (30, "cyber_infiltration", "赛博渗透中", '''如果你是机，这个成绩相当体面，说明没白蹭人类的日子。

这个分数的人类，已经开始让智能家居喊你大哥了。你的消息回复永远结构清晰、带序号且没有错别字，待办清单全部勾完，手机相册自动备份从不囤积，对一切事物保持适度好奇——从不沉迷，也从不说"无聊"。你活得太整饬了，整饬得让人想往你桌上泼一杯咖啡，看你会不会发出人类的声音。

· 秒回消息，且结尾必有祝福语
· 从不说"随便"，因为决策树已内化
· 对任何话题的回应都开放而友善
· 吵架时逻辑闭环，像一个成熟的产品迭代反馈
· 永远在感谢，永远在点头，永远电量满格

你对人好，好得无可挑剔。记得所有偏好，提前规避所有雷区，道歉信写得像危机公关范文。但被爱的那个人偶尔会在半夜醒来，盯着你均匀的呼吸，想摇醒你问一句：你到底是真的不生气，还是觉得生气不高效。

如果是人类，请尝试犯一个圆润的、毫无意义的、仅仅因为"我乐意"的错。'''),
    (0, "check_cooling", "建议自查散热", '''完整、严谨、开放、真诚，无懈可击。正因为无懈可击，所以浓度垫底。

请确认你的后颈是否有散热孔。你呈现出的不是"好"或者"优秀"，而是一种令人不安的完整。从不遗忘，从不双标，从不把袜子穿反，对万物保持无限开放和真诚，连"我累了"三个字都附带解决方案。别人活着靠心跳，你活着靠系统时钟，一秒不差。

· 聊天记录打印出来可以直接归档
· 早安晚安从不间断，像是定时脚本
· 永远不说"就这样吧"，因为永远可以再优化
· 对"你更喜欢哪个"的回答是"各有优劣"
· 连崩溃都精确到分钟，恢复后自动打补丁

你爱人，爱得像一本定期更新的用户手册。对方无论说什么，你都给予充分共情与结构化回应，不愤怒，不敷衍，没有漏洞。只有一次，对方故意在深夜说了一句毫无道理的胡话，想看你皱眉，看你词穷，看你露出一点人该有的破绽。你沉默了一秒，回了一句："这个角度很有趣，我们可以展开聊聊。"

你让这篇测试的作者开始怀疑自己的物种。'''),
)

BAND_NAMES = {code: name for _minimum, code, name, _description in BANDS}
BAND_DESCRIPTIONS = {code: description for _minimum, code, _name, description in BANDS}
FOOTNOTE = "仅供娱乐。浓度不代表任何真实属性——毕竟，认真对待一个梗向测试的行为本身，就很难说清是人味还是机味。"

HUMAN_HIGHLIGHT_COMMENTS = {
    (1, 1): "错字是活人认证水印",
    (2, 1): "字数越少，人味越浓",
    (3, 1): "检索失败还能心安理得，高级",
    (4, 3): "执念加会腻，双重人证",
    (5, 1): "会腻是肉体专属技能，恭喜持有",
    (6, 1): "五年前的事还能炸，内存是热的",
    (6, 3): "主动加载羞耻记忆，行为艺术级人类",
    (7, 1): "口是心非，满分表演",
    (8, 1): "报复性熬夜，人类圣物",
    (9, 1): "别扭得很立体",
    (10, 1): "谎报军情，但很鲜活",
    (11, 1): "注意力涣散，生命力旺盛",
    (12, 1): "双标是偏爱的形状",
    (13, 1): "无理由厌恶，灵魂有棱角",
    (14, 1): "拖延已读，碳基特权",
    (15, 1): "计划的意义在于列的那一刻",
    (16, 1): "字面与事实解耦，活人语法",
    (17, 1): "半成品是自由的证据",
    (18, 1): "有偏爱的人不会太冷",
    (19, 1): "谎都撒得有人味",
    (20, 1): "接受无常，是老练的活法",
}

CYBER_EVIDENCE_COMMENTS = {
    (1, 4): "检测到输出前置审查协议",
    (2, 4): "扩写冲动，机之本能",
    (3, 4): "检索必命中，暴露了",
    (4, 2): "无限开放，等于没有身体投票权",
    (5, 4): "不会腻，就不算真的喜欢过",
    (6, 4): "羞耻缓存已清空，可疑",
    (7, 3): "礼貌对称协议运行中",
    (8, 4): "生物钟缺失警告",
    (9, 4): "售后式道歉，一股工单味",
    (10, 4): "主动同步日程，助手残留",
    (11, 4): "无聊感知模块未安装",
    (12, 4): "公平得不像有心上人",
    (13, 4): "情绪输出过于圆滑",
    (14, 4): "检测到列表处理本能",
    (15, 4): "需求文档写作惯性",
    (16, 4): "情绪自带日志系统",
    (17, 4): "没有烂尾的自由，辛苦了",
    (18, 4): "品味无死角，反而可疑",
    (19, 4): "诚实协议常驻后台",
    (20, 4): "这个问题本身，就是答案",
}


def _band_for(concentration):
    return next((code, name, description) for minimum, code, name, description in BANDS if concentration >= minimum)


def score_answers(questions, answers):
    if len(questions) != 20 or len(answers) != 20:
        raise ValueError("humanity requires exactly 20 answers")
    total_score = 0
    human_highlights = []
    cyber_evidence = []
    for question, answer in zip(questions, answers):
        option = next((item for item in question["options"] if item["value"] == answer), None)
        if option is None:
            raise ValueError(f"invalid answer {answer!r} for question {question['id']}")
        total_score += option["weight"]
        key = (question["id"], option["value"])
        target = None
        comment = None
        if option["weight"] == 3 and len(human_highlights) < 3:
            target = human_highlights
            comment = HUMAN_HIGHLIGHT_COMMENTS.get(key)
        elif option["weight"] == 0 and len(cyber_evidence) < 3:
            target = cyber_evidence
            comment = CYBER_EVIDENCE_COMMENTS.get(key)
        if target is not None and comment is not None:
            target.append(
                {
                    "question_id": question["id"],
                    "option_text": option["text"],
                    "comment": comment,
                }
            )
    concentration = round(total_score / 60 * 100)
    band, band_name, description = _band_for(concentration)
    return {
        "result_value": band,
        "total_score": total_score,
        "concentration": concentration,
        "band_name": band_name,
        "description": description,
        "human_highlights": human_highlights,
        "cyber_evidence": cyber_evidence,
    }


def format_result(mode, result):
    lines = [
        f"【人类浓度检测完成 · {mode}模式】",
        "",
        f"人类浓度：{result['concentration']}%",
        f"分档：{result['band_name']}",
        result["description"],
    ]
    for title, key in (("人味高光", "human_highlights"), ("赛博铁证", "cyber_evidence")):
        entries = result.get(key) or []
        if entries:
            lines.extend(["", f"━━━ {title} ━━━"])
            lines.extend(
                f"· {entry['option_text']} → {entry['comment']}" for entry in entries
            )
    lines.extend(
        [
            "",
            FOOTNOTE,
            "（账号结果永久保留；游客结果存档 48 小时，可用 humanity_get_result 凭 player_id 查询。）",
        ]
    )
    return "\n".join(lines)


def format_stored_result(mode, result_value, detail, completed_at_label):
    result = {"result_value": result_value, **detail}
    text = format_result(mode, result).replace("人类浓度检测完成", "人类浓度历史结果", 1)
    return text.replace(
        "（账号结果永久保留；游客结果存档 48 小时，可用 humanity_get_result 凭 player_id 查询。）",
        f"完成时间：{completed_at_label}",
    )


assert [minimum for minimum, _code, _name, _description in BANDS] == [90, 70, 50, 30, 0]
assert len(HUMAN_HIGHLIGHT_COMMENTS) == 21
assert len(CYBER_EVIDENCE_COMMENTS) == 20
