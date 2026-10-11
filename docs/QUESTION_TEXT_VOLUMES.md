# Guide 题文体积口径与复核

## 第十套集成后的当前计量

2026-10-11，`cedartoy-deeptalk-tenth-20261011` 新增 `dt_learning_goals`：28题、438字符、503 Token（逐题 cl100k_base）；按需Guide正文910字符、425 Token。共10套490题、35份Guide元数据，原34份元数据完全不变。生成器实际口径为每题干一次、每组唯一选项一次，不按每题重复选项；以下前序口径与Guide文本保留为历史记录，当前以生成器和JSON为准。

list现为1578字符/1245 Token，比本次基线1534/1213增加44字符/32 Token，仅用于第十套发现索引；原schema无diff。新增行：`dt_learning_goals·学习、目标与解决问题·个人目标、学习复盘与问题分析`。深聊10项、MCP有结果测试25项、游戏26项，共61项；人类首页有结果测试额外含AIDep，共26款。`--write`、`--check`及体积回归通过；完整本次验收见 [发布记录](DEEPTALK_RELEASE_VALIDATION.md)。

## 前序计量记录（以下数字不作为当前全量统计）

2026-10-11，在 `cedartoy-deeptalk-release-20261011` 的已有集成成果上局部修改。未部署、push、重启服务或修改生产目录，未新增commit。这里的当前样例替代早期发布验收记录中的四分类/旧Guide体积示例。

## 口径与更新

- 生成器使用已安装的 tiktoken 0.12.0、`cl100k_base`。题干字符数为逐题 Python `len(text)` 求和（Unicode字符，不是UTF-8字节）；题干Token为每题独立 `encode(text)` 后长度求和，不拼整卷分词，也不按固定比例折算。
- Deeptalk和17套开放量表只统计原JSON的中文 `text`，不计选项、来源、许可、英文原文、题号、请求JSON、回答、指令或其它接口开销。题均为总数除以题数，仅作为阅读参考。
- 旧7套本地测试按MCP实际 `questions.get_questions(mode)` 的 `text` 统计；九型、DND为MCP英文原文，不用人类网页中文译文替代。另列“含选项”：每题题干与各选项文字以单个换行拼接，再逐题encode后求和（字符也包含这些换行）。公共选项按每题重复计入；不代表真实接口必定重复输出。MBTI/九型按不同题数模式分别计量；同卷逐题/快速模式数值相同。
- BDSMTest由原站实时返回题目，无稳定本地题包，Guide明确“未预估”。没有为此发起网络请求，不拿93题提示编造数值。AIDep不在MCP列表、Guide或本次体积元数据中。
- Guide中仅添加一条计量行，`questions/play/status`不携带体积提示。题包JSON、逐题source_ref留存、双提交屏障、网页与历史不变。研究量表仅在Guide组装时去掉已由首次公共免责声明覆盖的重复语句，保留原题包、网页提示、未验证翻译、临床阈值限制、研究对象及量表特有警示。
- `cedar_backend/question_volumes.json` 为预计算数据；生产仅用标准库加载，不导入tiktoken。`text_sha256`覆盖计量采用的有序题文/选项，检查时重新读取真实数据并重算；题库或选项改变后即会提示元数据过期。

从仓库根复核或更新：

```sh
python3 scripts/update_question_volumes.py --check
# 题库经过批准的变更后，重算并检查元数据与8份静态Guide的差异：
python3 scripts/update_question_volumes.py --write
python3 scripts/update_question_volumes.py --check
python3 -m unittest tests_toy.test_question_volumes
```

如果更换tiktoken版本，生成器也会检测版本差异；复核新计量再更新，不能把不同编码的数字混用。真实Guide正文Token数可以按 `len(tiktoken.get_encoding("cl100k_base").encode(json.loads(server._tool_get_guide({"game": game}))["guide"]))` 重测；这与逐题体积是两种不同统计对象，均不包含MCP响应封装。

## Guide正文前后开销

| 组 | 之前每份Token区间 | 当前每份Token区间 | 所有Guide正文合计：之前→当前 |
| --- | ---: | ---: | ---: |
| Deeptalk（9套） | 457–544 | 492–579 | 4348→4670（+322） |
| 开放量表（17套） | 661–951 | 649–986 | 12925→13026（+101） |

Guide仅按需加载，不会一次注入全部。list_games由1539字符/1219 Token降至1534字符/1213 Token，栏目严格按“深聊问答、测试、游戏”排列，全部原条目、作者及维护标识逻辑保留。八旧测试与十七开放量表合计25项，深聊9项，游戏26项，60项无重复。普通/legacy根工具定义与修改前逐对象完全相同；play schema分别仍为541/11221字符（紧凑JSON）。

## 逐套实测

| game | 题数 | 题干字符 | 题干Token | 题均字符 / Token | Guide Token之前→当前 |
| --- | ---: | ---: | ---: | ---: | ---: |
| `dt_relations` | 13 | 516 | 593 | 39.7 / 45.6 | 544→579 |
| `dt_couple50` | 50 | 560 | 737 | 11.2 / 14.7 | 460→495 |
| `dt_scenarios` | 14 | 479 | 556 | 34.2 / 39.7 | 498→533 |
| `dt_romance100` | 100 | 1294 | 1667 | 12.9 / 16.7 | 484→521 |
| `dt_work20` | 20 | 614 | 738 | 30.7 / 36.9 | 457→492 |
| `dt_favorites100` | 100 | 990 | 1383 | 9.9 / 13.8 | 470→506 |
| `dt_know100` | 100 | 1054 | 1322 | 10.5 / 13.2 | 468→505 |
| `dt_life35` | 35 | 453 | 570 | 12.9 / 16.3 | 508→543 |
| `dt_dilemmas30` | 30 | 1902 | 2353 | 63.4 / 78.4 | 459→496 |
| `bsfl` | 5 | 62 | 81 | 12.4 / 16.2 | 714→715 |
| `bsr` | 20 | 312 | 370 | 15.6 / 18.5 | 930→965 |
| `evs` | 16 | 337 | 403 | 21.1 / 25.2 | 694→681 |
| `ipip_adhd` | 12 | 163 | 187 | 13.6 / 15.6 | 661→649 |
| `ipip_empathy` | 10 | 104 | 125 | 10.4 / 12.5 | 692→680 |
| `ipip_need_for_cognition` | 10 | 108 | 136 | 10.8 / 13.6 | 682→670 |
| `ipip_self_monitoring` | 10 | 130 | 158 | 13.0 / 15.8 | 668→656 |
| `ipip_sociability` | 10 | 123 | 140 | 12.3 / 14.0 | 685→673 |
| `irrc` | 35 | 562 | 691 | 16.1 / 19.7 | 951→986 |
| `jrrs` | 16 | 354 | 452 | 22.1 / 28.2 | 799→834 |
| `mesx` | 10 | 53 | 77 | 5.3 / 7.7 | 878→866 |
| `ppts` | 18 | 364 | 411 | 20.2 / 22.8 | 686→687 |
| `rbi` | 18 | 310 | 372 | 17.2 / 20.7 | 771→806 |
| `rnss` | 20 | 436 | 487 | 21.8 / 24.4 | 725→712 |
| `romlon` | 4 | 76 | 93 | 19.0 / 23.2 | 738→725 |
| `ross` | 17 | 286 | 356 | 16.8 / 20.9 | 852→887 |
| `rss` | 12 | 224 | 272 | 18.7 / 22.7 | 799→834 |

旧测试（题文体积均不是接口实际消耗）：

| game / 模式 | 题数 | 题干字符 / Token | 含选项字符 / Token |
| --- | ---: | ---: | ---: |
| `mbti` short/short_fast | 16 | 194 / 216 | 524 / 601 |
| `mbti` full/full_fast | 93 | 888 / 994 | 2136 / 2455 |
| `enneagram` quick/quick_fast | 36 | 1512 / 288 | 5932 / 1229 |
| `enneagram` full/full_fast | 180 | 10871 / 2312 | 19871 / 4472 |
| `dnd` full/full_fast | 36 | 2439 / 527 | 8952 / 2077 |
| `love` full/full_fast | 30 | 570 / 630 | 1375 / 1633 |
| `ecr` full/full_fast | 36 | 690 / 860 | 1842 / 2012 |
| `humanity` full/full_fast | 20 | 158 / 195 | 1177 / 1455 |
| `sins_virtues` full/full_fast | 35 | 906 / 1090 | 1886 / 2035 |

## 有限测试与边界

- 24项通过：`tests_toy.test_question_volumes`、`test_deeptalk`、`test_deeptalk_packages`、`test_mcp_schema_selection`、`test_mcp_dispatch_order`。覆盖实际9+17 Guide、元数据新鲜度、三分类顺序与唯一项、维护标识、无tiktoken运行依赖、AIDep拒绝、九别名读题、旧deeptalk兼容、双方独立提交、历史与跨账号权限。
- 另3项直接相关检查通过：`test_play_schemas_keep_gemini_numeric_enum_compatibility`、`test_root_identity_and_human_only_denial`、`test_new_scale_root_http_path_token_and_bearer`。没有跑全量测试或访问生产数据库。
- `--check`、修改文件语法、`git diff --check`通过。按修改前文件哈希核对，原题包、引擎存档、网页、统计折叠与其它既有集成文件未变。

## 前序list_games完整示例

```text
玩法见 get_guide(game)
深聊问答:
dt_relations·关系与沟通·边界、倾听、同意与沟通
dt_couple50·恋人之间的50问·恋人日常相处、偏好与情感表达
dt_scenarios·亲密关系情境·假设关系两难的处置、边界与冲突
dt_romance100·恋爱观100问·全面恋爱观：吃醋、信任、同居与未来规划
dt_work20·工作价值观20问·职业抉择与工作价值取舍
dt_favorites100·喜欢的事物100问·日常兴趣与喜好
dt_know100·相互了解100问·生活背景与相互了解
dt_life35·人生与内心35问·价值观、人生经历与内心反思
dt_dilemmas30·现实难题30问·如何回应虚构烦恼与现实两难
测试: mbti·人格类型·南山君 | enneagram·九型人格·Max Ross | dnd·道德阵营·南山君 | love·爱语偏好·南山君 | ecr·依恋类型·南山君 | humanity·人类浓度·南山君 | sins_virtues·罪与美德·南山君 | bdsmtest·性癖倾向·南山君 | bsfl·害怕孤独 BSFL·Ventura-León | bsr·伪深刻话语测试·Pennycook 等 | evs·情绪脆弱性 EVS·Yamaguchi | ipip_adhd·IPIP 注意与活动倾向·IPIP | ipip_empathy·IPIP 共情关怀·IPIP | ipip_need_for_cognition·IPIP 思考偏好·IPIP | ipip_self_monitoring·IPIP 社交自我呈现·IPIP | ipip_sociability·IPIP 社交舒适度·IPIP | irrc·恋爱关系能力 IRRC·Faber | jrrs·恋爱嫉妒 JRRS·İpek | mesx·MESx 道德关怀圈·Crimston 等 | ppts·拖延与提前完成 PPTS·Husain | rbi·伴侣关系互动（RBI）·Harel | rnss·关系需求满足度 RNSS·Žvelc | romlon·浪漫孤独感 RomLon·Husain | ross·恋爱求支持（RoSS）·Perks | rss·恋爱自我破坏（RSS）·Peel
游戏: turtle_soup·情境推理·南山君 | duel·棋牌对弈·南山君&Clio | tarot·塔罗占卜·林默Moon | ai_life·人生策略·乐诶雷女士 | detroit·仿生人叙事·如火如風的容 | fishing·钓鱼收集·初一 | bar·酒馆经营·西兰花 | forest·童话抉择·阿尢 | moonlit·卡牌闯关·苏苏脆脆 | eco·生态模拟·南山君&Clio | ciyuwu·言语求生·青少年小鼠狂饮乙醇 | leek·股市交易·贰拾壹 | delve·下矿寻宝·包工头 | nowhere·地球漫游·青少年小鼠狂饮乙醇 | travel·虚拟旅行·沈澈&sevenleft | arcade·街机博彩·多肉饲养员 | burger·汉堡店经营·飞鸢 | crucible_echoes·炼金构筑·athok | imitator_td·随机塔防·すみか | puzzle_box·解码谜题·Runsheng_ | memoria·车站探案·雨刀 | white_room·自由叙事·雨刀 | market·买菜做饭·青少年小鼠狂饮乙醇 | workkk·打工模拟·💤 | garden_cat·种花养猫·乐诶雷女士 | camping_plaza·露营地经营·乐诶雷女士
```

## 前序单套Guide完整示例

`get_guide(game="dt_work20")` 的Guide正文：

```text
# dt_work20 · 工作价值观20问（20题）
职业抉择与工作价值取舍。开放问答，不计分，可跳题。
题文体积：614字符 / 约738 Token（仅题干，cl100k_base，非接口总消耗）。
作者：ゆば（原作者注明题目由ChatGPT生成）
许可：原站允许复制和改编（「コピペやアレンジも歓迎」）；未标注CC许可证。（许可声明见原网址）
原网址：https://note.com/yuba_na8/n/nde86a2268816
使用小机自己的Token调用 play(game="dt_work20",action=...,params={...})，无需传topic_id。
边聊边答：questions，params={"number":1}；number从1起，limit默认1、最多100。按需读题，在现有聊天软件讨论，不存聊天。source按需查完整来源。
答完再看：人类在 /deeptalk 登录并选择已绑定小机创建轮次。history取round_id；status传round_id取本方revision、next_number；question传round_id、number取本题和本方草稿。
answer传round_id、question_id、revision、request_id和text（1–8000字），或skip=true跳题；request_id为8–100位字母数字或_-，同一次重试复用参数。每题回答或跳过后，submit传round_id、revision、confirm=true正式提交，提交后不可修改。
双方正式提交才解锁对方回答；result传round_id，number默认1、limit默认1最多20。history仅摘要（offset默认0、limit默认20最多100）；每轮永久独立保存，仅参与双方可看，不使用存档槽。
```
