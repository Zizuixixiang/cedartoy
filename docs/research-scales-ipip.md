# IPIP、MESx 与 BSR 来源核验

核验日期：2026-10-11。可接入题库 **6 套、62 题**；BSR 保留阻塞，不生成题库。本文只记录来源、版本选择与计分边界，不能用作中文版本效度证明。

## 共同规则

- 五套 IPIP 均使用官方原题与官方正负键；`original` 保留英文，`text` 为本项目未验证中文翻译。作者和被表征量表的作者分开说明，不能称为原版权量表的原版。
- [IPIP 官方首页](https://ipip.ori.org/)明确题目及量表属于公共领域，允许复制、修改、翻译、任意目的使用且不需额外许可。
- [官方计分说明](https://ipip.ori.org/newScoringInstructions.htm)：正向题 1–5，负向题 `6 - answer`，然后求和。不是把 OpenScales 同时列出的 sum 与 mean 当作两个心理维度。
- [官方施测说明](https://ipip.ori.org/new_ipip-50-item-scale.htm)：IPIP 表征量表没有固定题序。本项目按原计分键顺序编号；下表的反向编号以本项目 JSON 为准。刻度为 Very Inaccurate / Moderately Inaccurate / Neither Accurate Nor Inaccurate / Moderately Accurate / Very Accurate。
- 不复制 IPIP 范例的“绝对保密”承诺；仅说明作答方法。所有新增报告均不引入诊断阈值、常模百分位或未经验证的高/低划分。小机回答只作娱乐演绎，不具有对人的量表效度。

## 可接入题库

| ID | 明确版本 | 题数 | 本地反向题 | 计分 / 范围 | 正式原题及正负键 |
| --- | --- | ---: | --- | --- | --- |
| `ipip_adhd` | IPIP ADHD，表征 Span、Earleywine、Strybel（2002）构念 | 12 | 无 | 单维求和 12–60 | [Single Construct Scoring Keys, ADHD](https://ipip.ori.org/newSingleConstructsKey.htm#ADHD) |
| `ipip_empathy` | Barchard（2001）EI 的 **Empathic Concern** | 10 | 6–10 | 单维求和 10–50 | [Emotional Intelligence Key](https://ipip.ori.org/newEmotionalIntelligenceKey.htm) |
| `ipip_sociability` | 公共领域 IPIP CPI-Sy 表征版 | 10 | 6–10 | 单维求和 10–50 | [CPI preliminary key, Sociability](https://ipip.ori.org/newCPIKey.htm) |
| `ipip_self_monitoring` | IPIP Self-Monitoring，表征 Snyder（1974）构念 | 10 | 8–10 | 单维求和 10–50 | [Single Construct Scoring Keys, Self-Monitoring](https://ipip.ori.org/newSingleConstructsKey.htm#Self-monitoring) |
| `ipip_need_for_cognition` | IPIP Need for Cognition，表征 Cacioppo、Petty（1982）构念 | 10 | 7–10 | 单维求和 10–50 | [Single Construct Scoring Keys, Need for Cognition](https://ipip.ori.org/newSingleConstructsKey.htm#Need-for-Cognition) |
| `mesx` | Crimston、Hornsey、Bain、Bastian（2018）MESx | 10 | 无 | 单维总分 0–30 | [PLOS 原论文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0205373) |

### IPIP 版本和聚合站交叉核验

- **ADHD**：[OpenScales IPIP-ADHD](https://openscales.net/scale.php?code=IPIP-ADHD&repo=ipip) 12 题与官方集合一致。聚合站称 Screening Scale；项目不据此宣传为临床筛查或诊断，不将其与 ASRS 混为一谈。
- **共情**：[OpenScales IPIP-EI-Empathy](https://openscales.net/scale.php?code=IPIP-EI-Empathy&repo=ipip) 10 题与官方 **EMPATHIC CONCERN** 一致，其正式链接 `newKey4Barchard2001EI.htm` 当前不能读取，已找到并采用可读取的官方 `newEmotionalIntelligenceKey.htm`。同名 AB5C Empathy 有 9 题，JPI Empathy 有 8 题，TCI Empathy 也不是这套；不能混用题目。这里测共情关怀，不能描述为完整共情能力测试。
- **社交舒适度**：IPIP 下有多种 Sociability。采用最符合“社交舒适度”的 CPI-Sy 10 题，官方键明确五正五反；不是取用受版权保护的 CPI 原卷。[OpenScales CPI-Sy 入口](https://openscales.net/scale.php?code=IPIP-CPI-Sy&repo=ipip)在 browse 目录可发现，本次详情抓取失败，但官方全部原题及计分键可独立核实，不依赖该页。JPI/HEXACO 的同名版本不混入。
- **自我呈现**：[OpenScales IPIP-SelfMonitoring](https://openscales.net/scale.php?code=IPIP-SelfMonitoring&repo=ipip)题目集合、三个反向键与官方一致；不是 Snyder 原版量表原题。
- **思考偏好**：[OpenScales IPIP-NFC](https://openscales.net/scale.php?code=IPIP-NFC&repo=ipip)题目集合、四个反向键与官方一致；分数不是智商。

### MESx 原题、刻度与许可

正式来源：Crimston, C. R., Hornsey, M. J., Bain, P. G., & Bastian, B. (2018). *Moral expansiveness short form: Validity and reliability of the MESx*. PLOS ONE, 13(10), e0205373. [原论文](https://journals.plos.org/plosone/article?id=10.1371/journal.pone.0205373)、[正式 PDF](https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0205373&type=printable)。版权段链接 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，允许注明作者/出处后改编；中文已标注未验证翻译。

论文 PDF 第 2 页给出四圈完整含义与方向；第 3 页 Table 1 和第 4 页 Results 明确 10 个实体。它们依次为 family member、citizen of your country、somebody with different religious beliefs、charity/aid worker、mentally challenged individual、murderer、dolphin、fish、old-growth forest、apple tree。中文第 5 项使用“有智力障碍的人”，保留原英文以便核查。

**计分以原文为准**：inner circle = 3，outer circle = 2，fringes = 1，outside = 0；十项相加，范围 0–30，越高表明赋予这些实体的道德关怀更广/更强。本地 `options` 直接保存这个方向，不再反向。[OpenScales MESx 页面](https://openscales.net/scale.php?code=MESx)本次读到的选项恰好相反（内圈 0、圈外 3），不能直接复制。原文称 aggregate score；这里按十项总分报告，不套用原论文每个实体组在 0–3 标尺上的均值当作诊断阈值。

十类实体是抽样类别，原文明确并非统计上独立的子维度，所以这里只返回一个维度，不建立十个一题“分量表”。施测说明根据论文对四圈的定义忠实翻译，保留最高关怀/责任、外圈责任减弱、边缘无义务、圈外无关怀以及个人代价含义，不把道德分数解释为人的好坏。

原文链接的 S1 Appendix DOCX 与 OSF 在本次工具中不能下载；本次实际依据是可读取的正式 HTML/PDF 中已完整列明的实体、圈定义、分值和聚合规则，**没有宣称读过附件**。原始附件入口供后续核对：[S1](https://journals.plos.org/plosone/article/file?id=10.1371/journal.pone.0205373.s001&type=supplementary)、[OSF](https://osf.io/nc5f6/)。

## BSR：此前Study 2候选的阻塞记录

当前已按用户明确选择接入[Study 4结构的4399娱乐改编版](research-scales-bsr.md)，不含推文。以下仅保留此前未发布候选的来源审计，不代表当前 `bsr` 入口状态。

正式研究：Pennycook、Cheyne、Barr、Koehler、Fugelsang（2015），*On the reception and detection of pseudo-profound bullshit*，Judgment and Decision Making 10(6), 549–563。[期刊原 HTML](https://sjdm.org/~baron/journal/15/15923a/jdm15923a.html)、[现出版平台](https://www.cambridge.org/core/journals/judgment-and-decision-making/article/on-the-reception-and-detection-of-pseudoprofound-bullshit/0D3C87BCC238BCA38BC55E395BDC9999)、[OpenScales 30 题聚合](https://openscales.net/scale.php?code=BSR)。

2026-10-11后续审计已取得并逐条复核原始补充PDF。当前选定Study 2 / Table S2的30项：20条生成句与10条Chopra推文，不是聚合站的10伪深刻＋10励志＋10普通陈述。该版本的均分表示接受度，不能称为“识别正确率”。

**仍阻塞：第三方引语权利未闭环。** 原期刊论文为CC BY 3.0；第三方推文仅有账号级出处，没有逐条URL或再发布许可，论文许可不足以证明第三方内容可同样再发布。正式附件已读到的事实替代此前“附件下载失败”的旧阻塞；哈希、版本比较、来源和解除条件见[后续报告](research-scales-misc-integration.md#bsr正式版本与原刺激差异)。本工作树未复制受限刺激或注册入口。

原文五档为1 Not at all profound / 2 somewhat profound / 3 fairly profound / 4 definitely profound / 5 very profound；与OpenScales第4/5档不同。其他研究版本（Study 3/4）的sensitivity为motivational mean − BSR mean，不能混入当前Study 2或反向相减。不得用自编句、删题或近义改写冒充原卷。
