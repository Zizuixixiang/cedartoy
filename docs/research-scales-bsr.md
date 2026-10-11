# 伪深刻话语测试：Study 4 结构的 4399 娱乐改编版

2026-10-11，入口 `/bsr`，MCP `game="bsr"`。本版仅做20题：10条伪深刻生成句＋10条公共领域古典励志格言。它替代此前未发布的 Study 2 候选；不接入第三方推文，不增添其他测试。

## 原始依据与复用

Pennycook, G., Cheyne, J. A., Barr, N., Koehler, D. J., & Fugelsang, J. A. (2015). *On the reception and detection of pseudo-profound bullshit*. Judgment and Decision Making, 10(6), 549–563. [正式出版页](https://doi.org/10.1017/S1930297500006999)、[期刊全文镜像](https://dlab.sauder.ubc.ca/sjdm/journal/15/15923a/jdm15923a.html)、[作者全文](https://gordonpennycook.com/wp-content/uploads/2021/09/pennycook-et-al.-2015-jdm.pdf)。正文声明 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)。保留全部作者、出处、许可和改编说明；仅生成句及本项目译文采用此许可，不据此授权论文中的第三方作品。

正文15.2明确 Study 4 复用10题 BSR；15.3明确两类句子为每位参与者混合随机呈现。BSR 原句核对[原始补充材料](https://journal.sjdm.org/15/15923a/supp.pdf)第1页 Table S1：前5条来自 wisdomofchopra.com 生成器，后5条来自 Seb Pearce 生成器，不是 Chopra 本人的推文。本轮网络附件入口无法读取，实际读取此前来源审计保存的同一份5页原始PDF，SHA-256：`be4fd61a78b478c59700b197d0bfb76cbac319ef3d658e0d41a2dd054ea8c959`，与既有审计报告一致。原文、逐条表行、生成器、本地未验证翻译保存在 `research_scales/data/bsr.json`。

励志组不复制论文的现代引语，替换为下列古典原文摘句，仅转换简体、整理标点；不使用现代译文、注释或校勘。原作属于公共领域。JSON保留逐条出处与复用说明，题目作答时不显示分组或作者，以免给出分类提示。

| 本地固定ID（非随机展示序号） | 格言 | 古典出处及核对文本 |
| --- | --- | --- |
| 11 | 学不可以已。 | [荀子·劝学](https://zh.wikisource.org/wiki/荀子/勸學篇) |
| 12 | 青，取之于蓝而青于蓝。 | 同上 |
| 13 | 不积跬步，无以至千里。 | 同上 |
| 14 | 锲而不舍，金石可镂。 | 同上 |
| 15 | 过则勿惮改。 | [论语·子罕](https://zh.wikisource.org/wiki/論語/子罕第九)，孔子及其弟子 |
| 16 | 三军可夺帅也，匹夫不可夺志也。 | 同上 |
| 17 | 岁寒，然后知松柏之后凋也。 | 同上 |
| 18 | 知者不惑，仁者不忧，勇者不惧。 | 同上 |
| 19 | 学而不厌，诲人不倦。 | [论语·述而](https://zh.wikisource.org/wiki/論語/述而第七)，孔子及其弟子 |
| 20 | 三人行，必有我师焉。 | 同上 |

页面明确标「4399 娱乐改编版」：这不是原论文逐句复刻，也没有中文验证，不能沿用原研究信效度、常模或群体相关性解释个人。古典措辞、熟悉度、文化背景与生成句的现代译文可能影响评价；差值仅描述本次两组材料的主观深刻程度。非诊断、仅供娱乐；小机作答不具有人类量表效度。

## 量尺、得分、题序

原文6.2的五档依次为 1 Not at all profound / 2 somewhat profound / 3 fairly profound / 4 definitely profound / 5 very profound。本版对应「完全不深刻／有一点深刻／颇为深刻／确实深刻／非常深刻」，不是同意程度或真假选择。

- 两类各10题、无反向题，分别求均分，范围均为1–5。
- 差值＝励志格言均分−伪深刻语句评价均分，方向与原文 Table 4 的 Var2−Var1 一致，范围−4至4。正数表示本次格言均分较高，负数表示伪深刻句均分较高，0表示相同；不是答对率，也不代表智力、识骗能力或人格好坏。不生成20题总分、诊断阈值、百分位或等级。
- 仅本题库启用 `randomize` 与 `mean_difference`。按已持久化 `session_id`、game、固定题ID的 SHA-256 排序，`order-v1` 固定算法跨进程稳定，不依赖Python随机种子或身份。继续既有会话、刷新、游客认领不重排；确认重测产生新会话和新顺序。现有17份JSON逐字节保持不变。
- 展示和计分使用同一顺序。答案按展示序号存储，评分再按该会话顺序映射到固定题目维度。不得在仍有活动存档时更改本卷固定ID或 `order-v1` 算法。
- `full` 逐题、`full_fast` 按返回顺序一次提交20个分值；复用现有会话、幂等回执、覆盖确认、SQLite事务和隐私限制，不迁库。网页批量草稿继续绑定身份和会话；接口不返回历史原始答案，不支持 compare 或读取其他身份结果。

根MCP公开schema未改（紧凑JSON 1292字符）；`list_games` 从1198到1224字符，仅增加26字符发现索引；BSR按需Guide为1279字符。这些是字符数，不是token估算。完整题表与逐条来源留在JSON/此文档，不注入常驻schema。

## 验证

命令均以 `timeout 3600s` 显式设置本次执行上限，测试使用自动删除的临时SQLite库，不接触生产数据库。

```sh
timeout 3600s python3 -m unittest tests_toy.test_research_scales tests_toy.test_mcp_schema_selection tests_toy.test_root_mcp_protocol.RootMcpProtocolTests.test_play_schemas_keep_gemini_numeric_enum_compatibility
timeout 3600s python3 scripts/persistence_check.py --research-scales
NODE_PATH=/opt/cedartoy/node_modules timeout 3600s node scripts/check_research_scale_web.js
NODE_PATH=/tmp/monopoly-pending-tools/node_modules CHROMIUM_PATH=/tmp/monopoly-pending-tools/browsers/chromium_headless_shell-1243/chrome-headless-shell-linux64/chrome-headless-shell SCALE_SCREENSHOTS=/tmp/cedar-bsr-screenshots timeout 3600s node scripts/check_research_scale_browser.js
```

**通过：19项针对性单测、18套×2模式跨进程存档检查、18页/265题DOM与真实HTTP处理器检查、JS语法检查及 `git diff --check`。** MCP补充采用非均匀BSR答案重跑路径Token/Bearer全流程通过；网页两模式均得到1.00/5.00/+4.00并完成刷新恢复。原17份题库与基线HEAD逐字节一致，10条英文生成句与正式Table S1逐字一致（仅规范空白）。

单测覆盖20题结构、原量尺、无反向、正/负/零及非整数均分差值；随机映射、两模式一致、重复/冲突/过期提交、非法批量拒绝、结果恢复、网页/MCP身份覆盖、槽位和游客隔离。原17套仍参加原有评分、反向和生命周期回归。跨进程检查覆盖18套×2模式，并核对恢复题序/结果不变。

浏览器脚本已增加 BSR 首页入口、两模式、非均匀答案计分、逐题与批量刷新、结果恢复、键盘、触控及360/430/1280px截图断言。但本次环境禁止创建监听socket；离线Chromium启动也因IPC权限失败，所以**本轮尚未取得渲染截图，不能宣称视觉验收通过**。模板及CSS没有修改，沿用已有系统字体、18–25px题干、13px研究说明、3px边框、10px答案间距、手机54px答案按钮与14px侧边距；仍需在允许浏览器运行的环境执行上述验收。
