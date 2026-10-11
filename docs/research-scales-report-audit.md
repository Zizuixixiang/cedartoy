# 人类测试与小机报告审计 / 18款研究量表解读

**2026-10-12结果页简化**：18款均显示各自的「得分 / 满分」；原始总分显示可得区间，BSR双极差值独立标注−4～4，均不改算分。报告正文删除重复量表介绍、排名解释和免责声明，保留动态观察、各维度内容和行动建议。原局限仍在来源/许可及结构化数据，MCP可读文本同步简化；以下旧阶段的“待整合”和响应大小属于历史记录，最新大小见重新生成的样例。

2026-10-11；仅修改 `cedartoy-report-audit-20261011` 隔离树。无生产数据库、服务操作、commit、push、部署或重启。`test_game.html`、`index.html`、全部量表题库均未修改；RBI本次仅补服务端解读。

交付：18款按历史 `scores` 即时生成中文解读，包含综合观察、维度说明与行动建议；人类完成结果和恢复结果都有结构化字段，17款普通MCP完成/get_result有可读全文，AIDep仅经既有显式授权分享。**本文件的旧量表审计描述针对改版前状态**；发布整合已将18款统一字段接入网页结果页，360px、430px和1280px真实浏览器检查通过，旧8款回归保持不变。

## 26个人类入口审计

实际核对首页链接、全部本地结果渲染分支。18款研究量表逐一经临时库完成、恢复并检查 `result_data` / `result`；MCP经真实根HTTP处理器读取（AIDep走授权例外）。旧7款也分别完成人类流程及小机作答/get_result，原输出保存在[样例JSON](research-scales-report-samples.json)的 `legacy`。现有DOM检查覆盖18款研究量表和旧7款；不是仅凭文件名判断覆盖。

表中「数值页」指当前 `renderResearchResult` 只显示分数、简短说明与论文入口；有新后端字段也不会自动显示，因为成功的结构化渲染会隐藏 `result` fallback。「新报告」指本轮服务端解读；所有研究量表原数字/原说明均保留。

| 入口 | 当前网页内容与不足原因 | MCP原有 → 本轮 | 处理范围 |
| --- | --- | --- | --- |
| mbti | 四轴、类型描述、优势、注意事项；已有可读内容 | 类型及解释保留 | 足够本轮目标，不重写 |
| enneagram | 主型、三中心、动机/恐惧、优势/弱项、状态/箭头、成长建议；完整型另有侧翼等 | 深度解释与成长建议保留 | 内容丰富，不重写；不把娱乐类型学升级为临床结论 |
| dnd | 两轴、详细阵营描述、原始桶分，已有丰富阵营语境 | 阵营解释保留 | 娱乐阵营用途已有解释，不强加心理建议 |
| love | 五维、主/次爱语描述与提醒；并列及偏低维度已有处理 | 爱语解释及已有比较建议保留 | 足够，不重写 |
| ecr | 两维、轴解读、组合类型描述与边界 | 二轴/组合解释保留 | 足够，不重写 |
| humanity | 浓度描述、人味高光、赛博证据；属娱乐吐槽 | 原趣味报告保留 | 符合原玩法，不套研究量表建议 |
| sins_virtues | 雷达、七对独立分、最高项与计分边界；**未发现逐维深度解释/行动建议** | 14维数字、最高项与说明保留 | 有专用渲染不等于已有丰富报告；记录后续项，超出本轮18款交付，不改 |
| bdsmtest | 首页去 `https://bdsmtest.org/?lang=zh_CN`，不使用本地结果模板 | 本地MCP另有原有引擎，与外站报告不能混为一谈 | 外站内容不可改，未宣称审过外站完整报告 |
| aidep | 数值页；学习AI四维缺情境与组合说明 | 默认禁止；授权简短数字摘要 → 授权新报告 | 可改生成与白名单，不开放题目/作答权限 |
| bsfl | 数值页；未区分害怕孤独与现实社交数量 | 数值 → 新报告 | 单维，允许补解释 |
| bsr | 数值页；两组均分及差值不能自动说明阅读倾向 | 数值 → 新报告 | 改编材料内比较，不判智力/识骗 |
| evs | 数值页；四种受伤场景缺区别，回避不能当频率 | 数值 → 新报告 | 四维与情境建议 |
| ipip_adhd | 数值页；易被误读为临床筛查 | 数值 → 新报告 | 单维相关特征、外部支持建议 |
| ipip_empathy | 数值页；缺关怀与完整共情能力的区别 | 数值 → 新报告 | 单维Empathic Concern |
| ipip_need_for_cognition | 数值页；缺思考偏好与智力的区别 | 数值 → 新报告 | 单维，探索与落实两面性 |
| ipip_self_monitoring | 数值页；易把社交调节理解成虚伪 | 数值 → 新报告 | 单维，表达/立场区分 |
| ipip_sociability | 数值页；缺人数、亲密质量与偏好的区分 | 数值 → 新报告 | 单维，不判社交焦虑 |
| irrc | 数值页；七维方向相反，不能总排序为能力高低 | 数值 → 新报告 | 能力/困难分开比较与组合 |
| jrrs | 数值页；体验/行为/信念混合，官方三维映射冲突 | 数值 → 新报告 | 只解释16题整体认同 |
| mesx | 数值页；容易被理解成道德好坏评分 | 数值 → 新报告 | 单维关怀范围，不恢复实体圈层 |
| ppts | 数值页；拖延/提前完成易被误当一条反向轴 | 数值 → 新报告 | 双高、双少、相反组合分别解释 |
| rbi | 本树模板保留；另一任务的前端已完成（用户说明，本轮未合并） | 原数值 → 统一三维新报告 | 本次补服务端/MCP；前端文件不改 |
| rnss | 数值页；总均分掩盖五种不同需要 | 数值 → 新报告 | 五维比较，不限恋爱 |
| romlon | 数值页；缺浪漫连结与一般孤独的区别 | 数值 → 新报告 | 单维，不推断单身/价值 |
| ross | 数值页；不同求助方式容易被道德化/类型化 | 数值 → 新报告 | 四维；间接与独处可并存 |
| rss | 数值页；技能反向后易读反，也不应预言关系结局 | 数值 → 新报告 | 三类困难与沟通行动 |

这比「所有测试都是只返回数字」更精确：18款研究量表此前有数字、计分和边界，但缺个性化解释；旧7款多数已有有用内容，sins_virtues是明确例外。未删改任何旧报告。

## 依据与证据不足处理

先读 [接入总览](RESEARCH_SCALES.md)及全部既有 `research-scales-*.md`，逐一核对JSON的题组、原题、刻度和正反键。本轮一次批量复核论文/官方页面；不扩展相关性或因果结论，不照搬论文研究人群的常模。

| 量表 | 解读的构念依据 | 保留的限制 / 本轮网络复核 |
| --- | --- | --- |
| aidep | [论文四维定义及最终题组](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1725393/full) | 成功；中国18–24岁大学生学习使用，不推断情感恋爱依赖、不使用探索性高低分组 |
| bsfl | [论文单维及五题](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.583396/full) | 成功；平台0–4均分，不冒充作者正式算法 |
| bsr | [原研究Study 4](https://doi.org/10.1017/S1930297500006999)、[本地逐项核验](research-scales-bsr.md) | DOI本轮读取失败；沿用已核正式题组与量尺，替换格言的改编不沿用信效度 |
| evs | [论文四类情境](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.941250/full) | 成功；日本大学生，回避维度关注受伤体验 |
| ipip_adhd | [官方Single Construct键](https://ipip.ori.org/newSingleConstructsKey.htm#ADHD) | 成功；非ASRS，非筛查，不得输出患病概率 |
| ipip_empathy | [官方EI键](https://ipip.ori.org/newEmotionalIntelligenceKey.htm)、[本地核验](research-scales-ipip.md) | 本轮页面读取失败；沿用已核Empathic Concern十题，不外推完整能力 |
| ipip_need_for_cognition | [官方Single Construct键](https://ipip.ori.org/newSingleConstructsKey.htm#Need-for-Cognition) | 成功；思考偏好，不是智力 |
| ipip_self_monitoring | [官方Single Construct键](https://ipip.ori.org/newSingleConstructsKey.htm#Self-monitoring) | 成功；IPIP表征版，不是Snyder原卷 |
| ipip_sociability | [官方CPI-Sy表征键](https://ipip.ori.org/newCPIKey.htm) | 成功；不是CPI原卷，不是人缘排名 |
| irrc | [原论文](https://doi.org/10.5964/ijpr.v13i1.341)、[七维与反向核验](research-scales-romance-a.md)、[官方版本冲突](research-scales-official-review.md) | DOI本轮读取失败；沿用既有Appendix B证据；官方29题Excel与35题不匹配，仍不合成总能力 |
| jrrs | [期刊](https://ijcer.net/index.php/pub/article/view/428)、[映射冲突核验](research-scales-romance-a.md) | 期刊成功；三维缺可靠映射，拒绝虚构维度；保留非商业许可 |
| mesx | [论文圈层定义](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0205373) | 成功；实体不是十个独立子维度，总分不能恢复各对象位置 |
| ppts | [论文两因子及保留题组](https://link.springer.com/article/10.1186/s40359-025-03072-6) | 成功；平台分别取均分，不反转提前完成、不设置健康分级 |
| rnss | [论文五需要与汇总](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.00901/full) | 成功；一般关系，不只恋爱；不能判定缺口原因 |
| romlon | [论文四题单因子](https://link.springer.com/article/10.1186/s40359-025-03208-8) | 成功；巴基斯坦成年人验证；不将浪漫孤独泛化为全部孤独 |
| ross | [论文四方式](https://link.springer.com/article/10.1007/s12144-025-08749-0) | 成功；平台0–10描述均分，不推依恋类型或人格缺陷 |
| rbi | [原论文](https://www.nature.com/articles/s41598-024-58901-2)、[官方PDF](https://www.nature.com/articles/s41598-024-58901-2.pdf) Study 2及Table 2 | 本轮成功读取PDF；过去一周，7/7/4题，三维1–5平台描述均分；不以性别/关系时长相关模型推断个人关系质量 |
| rss | [论文三因子](https://link.springer.com/article/10.1186/s40359-021-00644-0) | 成功；保留反向后的技能不足方向，未取得完整官方回归权重，不伪装成作者分数 |

原论文/官方定义只支持「在测什么、分数方向是什么」；**本报告所有生活场景、组合观察和行动建议都是平台原创反思建议，不声称是这些论文验证过的干预或因果推论。** 未取得正式中文验证题文的状况保持原说明。未改题库、许可或评分。

实现按合法范围做位置描述：多维文本在量尺跨度的1/4、3/4处切换侧重点，单维五段取1/5跨度；均为编辑分支，**不是官方阈值、群体高低或诊断等级**，不输出等级标签。比较仅限同量尺题组的回答位置，差距不足跨度20%时不强调先后；端部相差不足10%的项一起提及。20%也不是显著性检验。IRRC仅在能力组/困难组内排序，不把两组分值相抵；BSR差值只在本材料内解释。

## 接入与隐私契约

新增 `research_scales/interpretation.py`（纯生成/格式化）及 `interpretation_content.py`（各量表原创短文）。输入只有spec与保存的scores，不读答卷、不重算分数、不写DB。未知量表、缺失/越界/非有限/布尔分数不生成猜测报告；原有结果仍可读取。

完成报告的 `result` 改为得分与解读的精简版本，`result_data` 保留 `kind/scores/dimensions/scoring_note/source/notice`，新增 `score_ranges` 和 `interpretation`。现有 `overall`、BSR `difference` 原样保留，不另造评分维度。

| 路径 | 新字段位置 | 行为 |
| --- | --- | --- |
| 人类完成、`result/get_result`、完成后的`start/status`恢复 | `result_data.interpretation` | 仅已完成；网页未来读取这里 |
| 普通MCP完成、`result/get_result`、完成后的`start`恢复 | `result_data.interpretation`，并在 `result` 追加文本 | 17款普通MCP支持（含RBI） |
| 普通MCP `status` | 不附新字段/长文本 | 保留原分数与原说明，主动取报告用get_result |
| AIDep `shared_result` | 顶层 `interpretation` 与顶层 `result` | 授权后返回同一解读；旧scores/dimensions/explanation/notice保留 |
| 未完成、Guide、list、工具schema | 不附 | 无常驻长报告注入，schema不改 |

AIDep仍仅人类作答，只有当前结果已显式授权给当前绑定小机时才能分享。生成器只接收白名单过滤后的数字；不拷贝已存任意文本、答案、题干或result_detail。原分享版本摘要、绑定ID、撤销、解绑重绑、重测失效、AI普通get_result拒绝及no-store都保持。已有授权允许分享这份结果的解释，但不会授予新一轮结果或其他绑定小机。

### 网页前端接入的统一字段

`interpretation`对象（不存在时继续沿用当前渲染，RBI也使用同一契约）：

| 字段 | JSON类型 / 内容 |
| --- | --- |
| `version` | integer，当前为1，表示报告结构/文案版本，不是评分版本 |
| `game` | string，原game ID |
| `summary` | string，综合观察（纯文本） |
| `dimension_notes` | array，按原spec维度顺序；不重复overall或difference为新维度 |
| `dimension_notes[].key` | string，原维度key，不得按中文label路由 |
| `dimension_notes[].label` | string，显示名（可去除重复的描述均分后缀） |
| `dimension_notes[].score` | number，保存的对应score原值 |
| `dimension_notes[].range` | `[number, number]`，合法最小/最大；总分按题数计，如ADHD `[12,60]`、MESx `[0,30]` |
| `dimension_notes[].text` | string，维度说明和具体尝试方式；单维不重复summary |
| `recommendations` | string[]，1–2条综合行动思路 |
| `source_note` | string，构念依据与平台反思建议的区分 |
| `source_url` | string，原spec论文/官方键URL |
| `boundary` | string，该量表的一句特有边界 |

完整的真实JSON见[91套样例](research-scales-report-samples.json)，例如 `samples.ppts[2].interpretation`；每个game有相同结构。建议在已有分数之后复用 `appendResultTextSection`：先综合观察，再按维度顺序渲染label/text，最后 `appendResultListSection` 显示「行动思路 · 平台建议」，来源与研究边界放在既有折叠区域和结构化元数据，不再占据阅读正文。全程textContent/现有安全文本helper，不用innerHTML。不要把整份 `result` 再同时贴在结构化报告下方，也不要用前端再生成解读或给分数加红绿等级。保持原结果图表、分数、统计和来源入口。

整合后真实Chromium检查360px、430px与1280px，长段落、七维长报告、RBI混合组合、旧MBTI/ECR参考和水平溢出均通过；安全文本节点渲染，不以innerHTML拼接报告。分数原样展示，报告在其下方呈现。

## 覆盖矩阵与样例

[样例Markdown](research-scales-report-samples.md)保留原17款**各5套完整报告**，补RBI 6套，共91套；对应JSON含原scores、全部结构、旧7款人类/MCP全文、RBI带解读结构及逐响应体积。由脚本真实完成临时库并经根MCP读取，非手写理想示例。

新增回归的「9种输入」为计分键对齐的低端、1/4、中间、3/4、高端、两组维度方向相反的混合、全选原始最低、全选原始最高。半整数位置用同一维度内合法整数分配实现；先按正反键构造，再按BSR会话题序提交。单维混合仅是端点对照，不冒充多维模式；额外检查MESx不同作答同总分生成相同解释。全套162次完成/新引擎恢复，独立计算期望scores，断言旧结果行逐字节不变且读取时禁止调用score。

| game | 计分键对齐五位置 | 原始两端/反向 | 相反多维组合 | 人类HTTP | 普通MCP路径token+Bearer | 历史scores不重算 | 5套全文 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| aidep | ✓ | ✓无反向 | ✓ | ✓ | 仅显式授权shared_result；普通动作拒绝 | ✓ | ✓ |
| bsfl | ✓含0 | ✓无反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| bsr | ✓ | ✓无反向、随机序 | ✓正/负/零差 | ✓ | ✓ | ✓ | ✓ |
| evs | ✓ | ✓无反向 | ✓ | ✓ | ✓ | ✓ | ✓ |
| ipip_adhd | ✓ | ✓无反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ipip_empathy | ✓ | ✓6–10反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ipip_need_for_cognition | ✓ | ✓7–10反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ipip_self_monitoring | ✓ | ✓8–10反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ipip_sociability | ✓ | ✓6–10反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| irrc | ✓ | ✓32反向 | ✓能力/困难分组 | ✓ | ✓ | ✓ | ✓ |
| jrrs | ✓ | ✓无反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| mesx | ✓含0 | ✓圈层方向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ppts | ✓ | ✓无反向 | ✓双高/双少与相反组合 | ✓ | ✓ | ✓ | ✓ |
| rnss | ✓ | ✓18反向 | ✓ | ✓ | ✓ | ✓ | ✓ |
| romlon | ✓ | ✓无反向 | 单维不造组合 | ✓ | ✓ | ✓ | ✓ |
| ross | ✓含0 | ✓无反向 | ✓ | ✓ | ✓ | ✓ | ✓ |
| rbi | ✓1/3/5等五位置 | ✓无反向 | ✓六种排序、六种两项并列、小差距与指定组合 | ✓ | ✓ | ✓另含真实历史分数与跨进程 | ✓6套 |
| rss | ✓ | ✓9–12反向 | ✓技能不足方向 | ✓ | ✓ | ✓ | ✓ |

额外验证：小差距不夸大排序、同分不强排；每款至少5个不只替换数字的summary；所有dimension_notes保留原key及合法范围；输入/返回对象互不污染；无完成状态不泄露报告；RBI保留原结构字段并新增统一interpretation；匿名分布范围和旧结果原字节保持；AIDep泄露检查覆盖原题逐条不出现在共享输出。

## RBI第18款最终补齐（本轮验收）

原论文Study 2及Table 2确认时间范围为过去一周、最终18题按SAI/FOD/开放表达分为7/7/4题。本轮只新增RBI内容和组合分支，复用原生成契约与格式化器；`engine.py`、AIDep分享代码与授权边界不变。

- 三维分别给出专注相处与核实感受、明确并协商实际安排、澄清需要与复述理解的具体做法。各维在1/3/5位置有不同文字，不只是替换数字。
- 三维接近时分别描述量尺较少符合一端、中间、较符合一端的情况，不输出高/中/低关系等级。沿用原20%跨度比较规则（1–5时为0.8）；小差距不强讲落差，六种充分分离的顺序各有不同组合解读，两项并列不强分次序。
- `sai=2.86, fod=3.57, openness=4.5` 明确提出「能沟通的需求是否转化为双方实际感受到的关心与回应」。0.71的SAI/FOD差距不单独强化；这是反思问题，不声称已存在脱节或推断关系质量。
- RBI已有结果从保存的scores即时生成解释，保持原scores/原报告/私有result_detail。真实临时库中另注入与答案不同的历史scores，禁止调用score，验证人类HTTP及小机路径Token/Bearer的start恢复、result/get_result一致，并在另一个Python进程恢复；数据库结果行逐字节不变。
- **38项针对性测试通过**：`test_research_interpretation`、`test_aidep_sharing`、`test_research_scales`、`test_research_scales_official_review`。新增3项检查原17款85份解读完全一致、RBI语义组合、RBI旧结果真实双端/跨进程读取；既有全量量表测试扩到18款×9种输入，覆盖MCP新完成与读取、web result_data、AIDep隐私回归。
- 18款×2模式的独立进程存档检查再次通过；临时guest:regcheck库已清除。Python编译及`git diff --check`通过。
- 与扩展前快照相比，原17款所有文案常量、93个原始作答位置的报告、85份原样例及体积记录、旧7款输出完全相同。引擎、AIDep分享、两个前端文件和18份问卷逐字节一致。本轮没有更改评分、许可、工具schema、Guide或list。
- 样例只增量更新RBI：1/1/1、3/3/3、5/5/5、两组相反混合，以及合法作答得到的20/7、25/7、4.5（显示2.86/3.57/4.5），共6套。真实MCP完成响应为3709–3933字符、2333–2487个o200k_base token；普通status仍不附长报告。

复现本轮：

```sh
python3 -m unittest tests_toy.test_research_interpretation tests_toy.test_aidep_sharing tests_toy.test_research_scales tests_toy.test_research_scales_official_review
python3 scripts/audit_research_reports.py --output docs --token-encoding o200k_base --only-rbi
```

最初的服务端独立任务未碰前端，发布阶段另行合并了RBI网页和统一结果渲染。以下95项和沙箱浏览器限制是开发阶段记录；发布前已另外通过真实Chromium与DOM测试，不把沙箱限制误报为上线后的验收缺口。

## 此前17款阶段验收与上下文体积

此前17款阶段的针对性Python回归共95项：**92通过，3项既有目录文案断言失败**。失败均在未修改的测试/目录代码：Love/ECR旧测试仍期待 `love·爱之语测试`；Sins两项仍要求将免责声明长驻 `list_games`。当前目录按现行规则为短索引；本轮没有为通过过期断言扩张目录或改旧文案。此前新报告与授权相关39项全部通过（包含上述95项中）。未跑全站。

```sh
python3 -m unittest tests_toy.test_aidep_sharing tests_toy.test_research_interpretation tests_toy.test_research_scales tests_toy.test_research_scales_official_review tests_toy.test_research_web_edits
python3 scripts/persistence_check.py --research-scales
NODE_PATH=/opt/cedartoy/node_modules node scripts/check_research_scale_web.js
python3 scripts/audit_research_reports.py --output docs --token-encoding o200k_base
```

较宽回归还运行 `test_human_test_web`、`test_love_ecr`、`test_sins_virtues`、`test_enneagram_smoke`、`test_mcp_schema_selection`、`test_mcp_dispatch_order`，及根协议的Gemini数值enum兼容、普通play最小schema两项。三条过期断言未删改。新增测试只对新status契约调整原评分审计的一条结果比较；原分数/存档字节断言仍保留。

- 18款×2模式的独立进程start/mutate/edit/read通过，临时guest:regcheck库自动清除。
- DOM/真实HTTP处理器检查通过：18页/首页标题和统计、修改后恢复、并发页/重试、原7款提交与结构化结果、XSS及身份边界。360/430/1280是DOM设置宽度，**不代表浏览器布局验收**。
- 尝试已有Playwright/Chromium真实检查，临时HTTP服务建立socket时报 `PermissionError: [Errno 1] Operation not permitted`，未获得截图。没有提升权限、安装依赖或启动生产服务。
- 根MCP新完成/读取文本体积为约2.2–5.3千字符；用已缓存`tiktoken/o200k_base`实测约1.2–3.4千token，逐款前后字符、UTF-8字节、token见样例。AIDep授权报告约2.5–2.8千字符/1.6–1.8千token。IRRC七维最长3388 token。这里不把字符数冒充token，也不宣称这是所有小机模型的token数。
- 完整读取同时保留可读result和结构化解读，存在兼容性重复；长报告只在主动完成/读取/完成恢复时给出，小机status不重复注入。Guide/list/schema新增字符为0。没有更改原公告和提醒行为；体积测量关闭无关公告/提醒，因此不含这些内容。

已整合：18款统一结果渲染及RBI内容已通过真实移动/桌面检查。旧sins_virtues是否另补深度内容不在本轮范围；官方评分/版本缺口仍按原描述性计分处理。

## 改动文件清单

| 文件 | 用途 |
| --- | --- |
| `research_scales/interpretation.py` | 从scores生成结构化解读、组合比较及MCP文本 |
| `research_scales/interpretation_content.py` | 18款原创中文说明、行动建议和边界 |
| `research_scales/engine.py` | 完成/读取时附报告，普通MCP status省略新长文 |
| `cedar_backend/aidep_sharing.py` | 在原授权白名单之后生成同一报告 |
| `tests_toy/test_research_interpretation.py` | 18×9输入、真实HTTP/MCP、历史不重算与语义边界 |
| `tests_toy/test_aidep_sharing.py` | 新报告字段与人类结果一致、原隐私断言保留 |
| `tests_toy/test_research_scales_official_review.py` | 适配status省略解读，仍检查旧分数/存档不变 |
| `scripts/audit_research_reports.py` | 临时库生成91套样例（支持仅更新RBI）、旧7款MCP读取与体积复测 |
| `docs/RESEARCH_SCALES.md` | 新增审计入口，更新AIDep已授权输出说明 |
| `docs/research-scales-report-audit.md` | 本审计、矩阵、契约、结果与未解决项 |
| `docs/research-scales-report-samples.md` | 91套完整可读样例与MCP体积表 |
| `docs/research-scales-report-samples.json` | 结构化样例、原7款/RBI实测结果、逐响应测量 |
