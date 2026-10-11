# 开放心理量表接入与验收

2026-10-11评分收尾：IRRC/PPTS/JRRS/RSS/RBI/RoSS/BSFL按已授权方案保留现有描述性均分，本轮无算法切换。IRRC官方Excel已取得，但实际29题与当前35题不匹配；证据及历史兼容验收见[七款评分收尾](research-scales-official-review.md)。

本次工作树：`/opt/cedar-workspaces/cedartoy-tests-20261011`。2026-10-11。

**已接入18套、265道题；其中17套/243题供MCP，AIDep-22的22题仅人类。** 最后新增 `bsr`「伪深刻话语测试 · 4399 娱乐改编版」，按Study 4的10＋10结构，来源、古典格言复用及本轮验收见[BSR专题](research-scales-bsr.md)。75题Romantic Motivations不在本次范围，仍未开放。此前新增的7套采用已明确授权的“4399描述性维度均分，非原作者正式评分”，不冒充官方合成分或验证中文版。全部已开放量表支持 `full` 与 `full_fast`、人类网页、恢复与已有结果查询（沿用每身份每量表一份当前结果，重测会覆盖）。未推送、未部署、未重启生产，也未编辑主生产工作树。

## 逐套来源和状态

| 量表 | 题数 | 来源/许可 | 本次状态 |
| --- | ---: | --- | --- |
| JRRS 恋爱吃醋 | 16 | [原论文](https://www.ijcer.net/index.php/pub/article/view/428)，CC BY-NC 4.0 | 已接入 `jrrs`；16题单一描述性均分，1–5，无反向；原三维映射冲突，未冒充官方三维分数 |
| RoSS 求安慰 | 17 | [原论文](https://link.springer.com/article/10.1007/s12144-025-08749-0)，CC BY 4.0 | 已接入 `ross`；0–10，四维描述性均分，无反向，不合成总分 |
| RSS 恋爱自我破坏 | 12 | [原论文](https://link.springer.com/article/10.1186/s40359-021-00644-0)，CC BY 4.0 | 已接入 `rss`；1–7，9–12题反向（8−x），三维描述性均分；未复现作者因子回归加权 |
| IRRC 关系能力 | 35 | [PsychArchives](https://www.psycharchives.org/en/item/77434b02-0954-4201-a3c3-25e403cab250)，CC BY 4.0 | 已接入 `irrc`；1–5，第32题反向（6−x），七维描述性均分；无性别DIF/潜变量/置信区间/总分 |
| RBI 伴侣关系互动 | 18 | [原论文](https://www.nature.com/articles/s41598-024-58901-2)，CC BY 4.0 | 已接入 `rbi`；1–5，三维描述性均分，无反向；过去一周伴侣关系互动，不是主动追人 |
| Romantic Motivations | 73/75冲突 | [原论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1273607/full)，CC BY 4.0 | 阻塞：正式附件已取得，实际75题与正文73题冲突；需作者或勘误核定题集，不能自行删两题 |
| RomLon 浪漫孤独感 | 4 | [原论文](https://link.springer.com/article/10.1186/s40359-025-03208-8)，CC BY 4.0 | 已接入 `romlon`，1–7，同向和4–28 |
| RNSS 关系需求满足 | 20 | [原论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.00901/full)，CC BY 4.0 | 已接入 `rnss`，1–5，18反向，5维均分及全题均分；不限恋爱关系 |
| BSFL 孤独恐惧 | 5 | [原论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.583396/full)，CC BY 4.0 | 已接入 `bsfl`；0–4，五题同向描述性均分；第4题不反向，零分有效 |
| IPIP-ADHD | 12 | [官方原题/正负键](https://ipip.ori.org/newSingleConstructsKey.htm#ADHD)，公共领域 | 已接入 `ipip_adhd`，1–5，同向和12–60；非临床筛查 |
| PPTS 拖延/提前完成 | 18 | [原论文](https://link.springer.com/article/10.1186/s40359-025-03072-6)，CC BY 4.0 | 已接入 `ppts`；1–5，拖延10题与提前完成8题分别描述性均分，无反向、不合成总分 |
| EVS 情绪脆弱性 | 16 | [原论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.941250/full)，CC BY 4.0 | 已接入 `evs`，1–4，4维及全题均分；原验证为日本大学生 |
| IPIP Empathy | 10 | [Barchard EI 官方键](https://ipip.ori.org/newEmotionalIntelligenceKey.htm)，公共领域 | 已接入 `ipip_empathy`；明确为 Empathic Concern，反向6–10 |
| IPIP Sociability | 10 | [CPI-Sy 官方表征键](https://ipip.ori.org/newCPIKey.htm)，公共领域 | 已接入 `ipip_sociability`，反向6–10；不是受版权限制的 CPI 原卷 |
| IPIP Self-Monitoring | 10 | [官方键](https://ipip.ori.org/newSingleConstructsKey.htm#Self-monitoring)，公共领域 | 已接入 `ipip_self_monitoring`，反向8–10 |
| IPIP Need for Cognition | 10 | [官方键](https://ipip.ori.org/newSingleConstructsKey.htm#Need-for-Cognition)，公共领域 | 已接入 `ipip_need_for_cognition`，反向7–10 |
| BSR | 20 | [原论文Study 4](https://doi.org/10.1017/S1930297500006999)，生成句CC BY 3.0＋公共领域古典格言 | 已接入 `bsr`，4399娱乐改编版；10伪深刻句＋10格言，1–5、两组均分及格言−伪深刻差值；无反向，不沿用原版信效度 |
| MESx 道德关怀圈 | 10 | [原论文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0205373)，CC BY 4.0 | 已接入 `mesx`；内圈3/外圈2/边缘1/圈外0，总分0–30 |
| AIDep-22 | 22 | [原论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1725393/full)，CC BY 4.0 | 已接入 `aidep`，仅人类，4维及全题均分；中国18–24岁大学生学习AI使用 |

五套 IPIP 使用 [官方1–5计分方法](https://ipip.ori.org/newScoringInstructions.htm)，负键 `6−answer` 后求和；除12题ADHD外四套范围均10–50。[官方公共领域授权](https://ipip.ori.org/)允许复制和翻译。所有本地译文标明“未验证翻译”，不冒充验证中文原卷，不添加临床分界、常模或效度宣传。

完整作者、题表编号、刻度、反向依据、许可和追查过的附件：

- [前五套恋爱量表核验](research-scales-romance-a.md)
- [其余恋爱量表、PPTS、EVS、AIDep核验](research-scales-other.md)
- [PPTS/BSFL补充核验及两款最新阻塞证据](research-scales-misc-integration.md)（原来源工作树报告；其中候选评分器和阻塞JSON未复制到本实现树）
- [IPIP、MESx、BSR核验](research-scales-ipip.md)

此前17款合并时仅从以下隔离来源目录读取并逐字节复制7份有效JSON，没有修改源目录，也未复制两个阻塞候选。来源报告同时复制后与本地旧核验文档对齐；具体题目原文、作者、表号、选项、反向及许可仍保留在JSON。

- `…/cedartoy-scale-audit-romance-20261011/research_scales/data/`：`jrrs/ross/rss/irrc/rbi`，98题。
- `…/cedartoy-scale-audit-misc-20261011/research_scales/data/`：`ppts/bsfl`，23题。
- 完整路径共同前缀为 `/opt/cedar-workspaces/`。

合并源文件SHA-256（本地副本一致）：

| ID | 题数 | SHA-256 |
| --- | ---: | --- |
| `jrrs` | 16 | `ec07a24ba415002578e76c64bf39d1e0bba76fc2ad8c656a27f132cea3f58e16` |
| `ross` | 17 | `725b44acc0152ba126e17b2cafc637b6f8d1a91984ebdf3ab41dbf42713f11ed` |
| `rss` | 12 | `a95b629613551e3b3d285c883fc2e4a4bf0814598087d95fff4eca5ce37e232e` |
| `irrc` | 35 | `461c108999919225193e331bcb136530434188a58ce0278426f5b9bccaabca33` |
| `rbi` | 18 | `afc647b69a3530ce99434fbac2cffc9949264e999550bf4f4f74d808c844773f` |
| `ppts` | 18 | `c5493be49d266512dfabec7f2ca940bc0f45ec4470fb516ab9c0a4d07e33d624` |
| `bsfl` | 5 | `be47358c7463001a1fc4d4f71e94ae6d4c2b12b10bb7e5a37bd75d244a2eca09` |

## 接入约定

- 首页测试分类共18张沿用既有样式的卡片；网页为 `/<game>`，共用 `test_game.html`。AI依赖卡片明确人类专属、大学生学习AI场景。
- 网页 `POST /api/<game>/{start,answer,answer_batch,result,status,get_result}`；GET仅查询 `result/status`。有 token 必须人类账号，身份覆盖自报字段；AI token 返回403。无token使用既有 `guest:web...` 命名空间；已认领游客拒绝继续访问。网页沿用人类自己的槽1，不绑定或读取小机档。
- 根MCP只增加17条发现索引和按需Guide；AIDep不进入目录，`get_guide`及全部原作答/查看动作仍拒绝，仅有下述显式授权 `shared_result` 例外。路径 token/Bearer 走现有身份解析；MCP不能拿 `guest:web...` 访问网页游客记录。新量表不增加 compare。
- `start` 指定 `mode=full/full_fast`。有记录默认恢复。重测需要当前 `session_id`、`restart=true`、`confirm=true`，过期重试不能再次清空新会话。
- `full` 每题提交 `answer`、返回的 `session_id`、从0开始的 `expected_index=progress`；相同题号同答案幂等，冲突或跳题拒绝。网页按服务端进度恢复，已提交题不直接修改。
- `full_fast` 返回全部题，按顺序一次提交 `answers` 和 `session_id`；必须题数完全一致、整数且在量尺范围内（布尔/浮点拒绝）。同会话同答案重试幂等，不同答案不能覆盖已完成结果。网页按原有逐页选题体验保存本机草稿，完成后统一提交，可提交前返回修改；草稿绑定会话与身份。
- 两种模式共用同一JSON题库和评分函数。结果呈现既有核实算法或明确标注的平台描述性统计、适用限制、原作者/来源/许可。RNSS/EVS/AIDep的全量均分按全体题目计算，不将不等长子量表均值再次简单平均。
- 复用 `ScaleTestEngine` 的校验、SQLite连接和既有 `test_sessions/test_results` 表；新量表的严格会话流程隔离在 `research_scales/engine.py`，未改旧8套题库和评分。读改写同一 `BEGIN IMMEDIATE` 事务；完成回执用于重复识别，接口不返回原始答卷。兼容已有表中额外 `user_id` 列。
- 新量表默认DB为工作树 `data/sessions.db`，与 server 一致；`SESSIONS_DB` 可覆盖。账号防认领回流检查使用实际 `TURTLE_SOUP_DB`。游客进度24小时/结果48小时按原框架清理。现有认领/账号删除基于原表工作，补充新game的存档删除及归属标记。

此前17款的上下文字符测量（不是token数；新增BSR后为1224字符、增26，schema不变）：根工具 schema 未修改，紧凑JSON仍1292字符；`list_games` 由732增至1198字符（+466），用于16套可玩内容发现。详细题目/选项不在索引中；批量MCP响应共用一份 options，逐题提交后的中间响应省去重复说明，正式来源在按需Guide和结果中。

## AIDep 本次结果分享（2026-10-11）

默认完全私密。仅已登录人类在完整结果页选择一台当前绑定小机并点击「保存选择」后生效；刷新结果页可恢复、切换对象或选「关闭」撤销。多台绑定不会自动广播；游客、未完成测试、AI及其他17款页面不显示分享区域。授权成功后可复制包含本人 `human_id` 的查看指令交给所选小机。

- 网页 `GET /api/aidep/sharing` 获取本人设置；`POST` 提交 `enabled` 布尔值。开启另需 `ai_id`、当前 `session_id` 与GET返回的 `result_version`。只接受真实人类 Bearer Token；忽略自报 `player_id/human_id`，服务端复核所选绑定。GET不会变更授权。设置、网页结果及AIDep根MCP响应带 `Cache-Control: no-store`。
- 根MCP唯一例外：`play(game='aidep', action='shared_result', params={"human_id": 123})`，由小机自己的路径Token或Bearer鉴权。`human_id` 必须显式整数；没有列举人类/结果的入口。未授权、其他小机、未绑定、结果缺失一律返回权限错误；`params` 不能覆盖受信身份或顶层动作。原 `start/answer/answer_batch/status/get_result/result/compare` 仍全部拒绝。
- `cedar_backend/aidep_sharing.py` 使用AIDep引擎的实际 `SESSIONS_DB`，按需幂等创建 `aidep_result_shares`，每个人类最多一个有效对象。保存 `human_id/ai_id/binding_id/session_id/result_version/enabled`，不复制答卷。版本校验包含结果回执和完成时间的摘要；重测、覆盖、旧页面重试都不能把授权移到新结果。读取复核当前绑定及双方存活、非待注销状态；解绑重绑因绑定ID变化不会恢复旧授权。授权和结果检查在同一会话库事务中，账号绑定查询持独立只读事务。
- AI响应白名单只有总体与四维数字分数、维度名、简短解释和现有适用限定，加 `ok/game/human_id` 路由字段；不会序列化 `result_detail`、原始22个 `answers`、题目或其他账号信息。撤销阻止后续读取，无法收回小机已收到的摘要。
- 未修改根schema、普通小机目录或测试Guide，常驻上下文增加0字符。此功能仅在独立发布工作树，未部署、push、重启或修改生产。

本次针对性验证：47项Python回归通过（其中10项AIDep授权测试，覆盖真实Token、HTTP/根MCP、跨进程恢复、越权/撤销/重测/解绑/删除/缺失坏档；另含原量表/人类网页/路由/schema/Gemini回归）。18款两模式的独立进程持久化检查通过。AIDep DOM＋真实HTTP/MCP处理器通过提交、历史恢复、明确选择、切换、撤销、过期页面、失败恢复、XSS、复制、游客/AI/无绑定与其他17页隔离检查。测试数据库均在临时目录，使用现有bootstrap仅替代缺失的无关eco/词与物导入，不模拟鉴权或本次业务。

```sh
python3 -m unittest tests_toy.test_aidep_sharing tests_toy.test_research_scales tests_toy.test_human_test_web tests_toy.test_mcp_schema_selection tests_toy.test_mcp_dispatch_order tests_toy.test_root_mcp_protocol.RootMcpProtocolTests.test_play_schemas_keep_gemini_numeric_enum_compatibility tests_toy.test_root_mcp_protocol.RootMcpProtocolTests.test_ordinary_play_params_only_declare_slot_and_command_and_allow_game_parameters
python3 scripts/persistence_check.py --research-scales
NODE_PATH=/opt/cedartoy/node_modules node scripts/check_aidep_sharing_ui.js
```

真实Chromium已尝试启动，但当前沙箱在 `sandbox_host_linux.cc:41` 返回 `Operation not permitted`。因此本次360/430px与桌面视觉、真实键盘/触控布局均未验收；DOM通过不替代渲染检查，也不沿用旧任务截图。现有UI技能引用的搜索脚本/辅助清单未随安装提供，采用本页组件与本地技能正文作为样式依据。新增区域沿用比较区域的2px边框/16px内距、既有像素按钮和颜色，正文13px、说明12px、选择和按钮至少44px；长名称/指令可换行。

## 验证与待复核项

新增BSR的当前验收记录见[专题](research-scales-bsr.md#验证)；本环境浏览器执行受限，不能沿用此前截图宣称BSR视觉通过。以下保留此前17款的验收记录。

针对性单测 **52项通过**：研究量表13项、既有人类网页15项、MCP schema选择2项、Gemini数值enum兼容1项、Love/ECR既有行为21项。没有跑全量测试。覆盖全部17套两种模式同答案一致、长度/范围/布尔拒绝、重复/冲突/过期会话、账号/槽/游客隔离、七套新增量表独立计分夹具、BSFL零分、RSS/IRRC反向、MESx方向、当前结果恢复、表额外列及游客TTL。16套MCP均通过真实根HTTP处理器完成逐题/批量及结果查询，分别覆盖路径Token和Bearer；AIDep六类动作在根MCP与人类API均拒绝AI。

```sh
python3 -m unittest tests_toy.test_research_scales tests_toy.test_human_test_web tests_toy.test_mcp_schema_selection tests_toy.test_root_mcp_protocol.RootMcpProtocolTests.test_play_schemas_keep_gemini_numeric_enum_compatibility
python3 scripts/persistence_check.py --research-scales
NODE_PATH=/opt/cedartoy/node_modules node scripts/check_research_scale_web.js
NODE_PATH=/tmp/monopoly-pending-tools/node_modules CHROMIUM_PATH=/tmp/monopoly-pending-tools/browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell node scripts/check_research_scale_browser.js
```

- 存档检查：临时SQLite上17×2条独立进程start/mutate/read链路全部通过，仅使用临时 `guest:regcheck`，完成后删除临时目录；不调用全游戏存档清理。
- Node DOM/HTTP smoke通过：17个页面、两种模式、逐题刷新、批量草稿恢复/修改/一次提交、完成结果刷新恢复、MESx/BSFL零分；AI Token进入AIDep显示拒绝且不降级为游客继续做题。
- 本轮真实Chromium检查通过，替代上一轮“环境阻止浏览器执行”的未验收结论。360/430/1280px：17张首页卡片可发现，首页RBI入口可导航；RoSS十一档、IRRC长说明、AIDep限制说明及模式/题目、BSFL零分结果与刷新恢复。无横向溢出；答案按钮至少44px，手机沿用54px；无脚本异常。截图在 `/tmp/cedar-scale-screenshots/`，人工对照检查字体层级、边框/阴影、移动侧边距与长链接换行，沿用既有样式；未安装依赖。浏览器仅允许本地本任务页面/API请求，外部字体/无关接口被拦截，所以截图使用系统回退字体，不代表生产数据或线上网络验收。
- 原有Love/ECR共22项中，21项行为验证通过；旧目录文案断言 `test_human_web_and_root_registry_include_all_three_games` 仍期待 `love·爱之语测试`，而HEAD已为 `love·爱语偏好`。本轮显式排除这一已核实的基线断言，未修改无关文案或测评。前一轮更宽根协议检查中的过时“覆盖”文案和缺失钓鱼vendor问题也未纳入本轮通过项；本轮仅运行相关schema回归。
- 独立树需要已有 `vendor/ci-yu-wu` 和 `eco` Python源码以导入server；此前仅只读复制源码到独立树，没有复制运行存档或改vendor指针。本轮只读复用已有jsdom/Playwright/Chromium，无安装操作。
- Python编译与 `git diff --check` 通过；7份源JSON哈希再次比对一致。所有接口/存档/浏览器测试使用隔离临时库或目录，无生产重启、部署、推送。

Romantic Motivations仍未开放且不在本次范围。此前BSR Study 2候选的第三方推文权利问题未解决，但本次已明确改选Study 4结构并替换励志组为公共领域古典短句，不再采用Study 2候选。JRRS官方三维映射、RSS正式加权、IRRC潜变量等未被声称实现；当前授权的描述性版本明确披露这些限制，不作为阻塞伪装成官方分数。JRRS保留CC BY-NC 4.0非商业限制；此前其余新增量表CC BY 4.0，五套IPIP为公共领域；BSR采用CC BY 3.0生成句和公共领域格言。翻译题目仍为未验证翻译。
