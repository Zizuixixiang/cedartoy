# 开放研究量表来源核验：Romantic Motivations / RomLon / RNSS / BSFL / PPTS / EVS / AIDep

核验日期：2026-10-11。本文件只记录这七套量表的可复核证据；接口是否接入、测试是否通过以接入文档和测试为准。题库 JSON 保留原文、忠实中文翻译、维度与反向标记。中文均为未验证翻译，不冒充作者验证的中文版。各量表不加自创高低阈值，不作诊断；小机仅作娱乐演绎。

## 可开放题库：6 套，85 题；Romantic Motivations 仍阻塞

| game | 正式版题数 | 刻度 | 维度与计分 | 授权 |
| --- | ---: | --- | --- | --- |
| `romlon` | 4 | 1–7，extremely false → extremely true | 单维；4题和，4–28；无反向题 | CC BY 4.0 |
| `rnss` | 20 | 1–5，completely disagree → completely agree | 5维各4题；第18题反向（6−x），各维均分；论文也定义20题总均分 | CC BY 4.0 |
| `ppts` | 18 | 1–5，Never → Always | 拖延10题、提前完成8题；4399描述性维度均分，非原作者正式评分 | CC BY 4.0 |
| `bsfl` | 5 | 0–4，Never → Always | 同向；4399描述性均分，非原作者正式评分 | CC BY 4.0 |
| `evs` | 16 | 1–4，I completely disagree → I completely agree | 4维各4题，均分；无反向题 | CC BY 4.0 |
| `aidep` | 22 | 1–5，strongly disagree → strongly agree | 情绪5、功能6、认知5、失控6；均分，无反向题；仅人类 | CC BY 4.0 |

量尺中间选项：除BSFL有完整五档标签外，其余原文只明确命名两端；实现中间档位保留数字，不把聚合网站补写的措辞冒充原量尺。

### RomLon

作者：Waqar Husain, Muhammad Ahmad Husain, Farrukh Ijaz, Javeria Farrukh, Fatima Batool, Nimra Tahir, Khaled Trabelsi, Achraf Ammar, Haitham Jahrami。

- [正式论文](https://link.springer.com/article/10.1186/s40359-025-03208-8)，Discussion 明确4题、单因子、7点量尺。研究对象是巴基斯坦成年人。
- [表1：原题与去留](https://bmcpsychology.biomedcentral.com/articles/10.1186/s40359-025-03208-8/tables/1)：保留前4题，删除第5、6题；不纳入删题。
- [表2：分数范围](https://bmcpsychology.biomedcentral.com/articles/10.1186/s40359-025-03208-8/tables/2)明确潜在总分4–28，且报告总分均值，因此使用4道1–7正向题求和。不是把题目均分误称论文总分。
- Rights and permissions 明确 CC BY 4.0，包括无另署权利说明的材料。题表无单独限制。

### RNSS

作者：Gregor Žvelc, Karolina Jovanoska, Maša Žvelc。

- [正式论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.00901/full)：Study 2 表2含最终20题英文原文与原题号；正文明确子量表题目相加除以题数、总量表除以20。
- 维度：Authenticity = 2/11/12/16；Support and Protection = 3/4/13/17；Having an Impact = 6/15/19/20；Initiative from Other = 7/9/10/18；Shared Experience = 1/5/8/14。
- 原表18为否定题。正式[西班牙语验证论文表1](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.992205/full)以星号标明第18题反向；[波斯尼亚语验证论文 Discussion](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1528926/full)明确其是唯一反向题。不能因第2/12题含否定词就反向计分。
- 这是一般人际关系需求满足，不专指恋爱；保留这一适用范围。
- 原论文 Copyright 为 CC BY，链接 [4.0 许可](https://creativecommons.org/licenses/by/4.0/)。有正式中文改编研究，但本次未取得并核对其整套授权中文原题，因此明确使用未验证翻译。

### PPTS

作者：Waqar Husain, Khaled Trabelsi, Achraf Ammar, Zahra Saif, Haitham Jahrami。

- [正式论文](https://link.springer.com/article/10.1186/s40359-025-03072-6)，Measures 明确最终18题，10道拖延、8道提前完成，1 Never 至5 Always；样本18–38岁。
- [表1脚注](https://bmcpsychology.biomedcentral.com/articles/10.1186/s40359-025-03072-6/tables/1)给出初始20题完整原文；删去 Prec4 与 Prec10；Results 最终保留 Proc1–10、Prec1/2/3/5/6/7/8/9。JSON保留 `source_id`，防止与重编号混淆。
- 全部保留题按各自维度正向。不能把提前完成题反向后并入“拖延总分”。两个因子相关不代表同一轴。
- 计分复核：原文没有单列个人评分手册，不能仅凭报告尺度推定正式算法。本轮用户明确授权平台描述性统计，已合并正式18题的 `blocked:false` 数据：两维分别取均分（1–5），标注“4399描述性维度均分，非原作者正式评分”，不合成总分、不设置阈值、不使用因子载荷作权重。此处替代先前评分规则缺失的阻塞结论；完整原文/XML复核见[后续来源报告](research-scales-misc-integration.md)。
- Supplementary Material 1 为 DOCX；本环境网页读取器不能解析该下载，故题目取自可直接读的正式表1，而不是聚合站。授权在 Rights and permissions 为 CC BY 4.0。

### EVS

作者：Shinji Yamaguchi, Yujiro Kawata, Yuka Murofushi, Tsuneyoshi Ota。

- [正式论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2022.941250/full)表1完整刊出16题，按批评否定、关系恶化、人际不和、拖延回避四组各4题。全部同向。
- Measures 规定1–4认同量尺。该处英语“adding the average of each item”措辞不够清楚；必须结合表2复核：总分与4个子量表均值约2–3，明确为1–4均分尺度，不能输出16–64的总和冒充文中分数。
- 原验证对象为日本大学生，原题为日语；本次译自论文正式英文表述，不称中文验证版。
- Copyright 的 CC BY 链接实际指向 [4.0](https://creativecommons.org/licenses/by/4.0/)。

### AIDep-22

作者：Houyu Wu, Haiyang Ni, Wenfu Luo, Tenglong Wu。DOI含2025，但正式发表于2026-01-19。

- [正式论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2025.1725393/full)表1是26题初始题池，不能直接取前22题。
- 表2删除初始题4/8/19/20；修改11/15/17/23/25。JSON采用表2 **Final wording**，其余取表1，额外保存初始 `source_id`。
- 表4最终维度：情绪1/2/3/5/6；功能7/9/10/11/12/13；认知14/15/16/17/18；失控21/22/23/24/25/26。无反向题；“获得支持/感到放松”等措辞仍测量依赖方向，不因措辞积极就倒分。
- Methods 明确1–5和均分。论文给过探索性的低/中/高均分组，不是临床诊断界值，本次不输出该分组。
- 原研究在中国一所高校18–24岁本科生中进行，内容是**学习任务中的AI依赖**。未取得作者验证中文问卷；本次为正式英文题目中文回译，明确未验证，不推广到全部年龄、情感陪伴或所有AI使用。
- `human_only: true` 是数据标记，仍必须由接入层实际鉴权，不能仅靠入口隐藏。
- Copyright CC BY 链接为 [4.0](https://creativecommons.org/licenses/by/4.0/)。

## 后续核验更新

### Romantic Motivations（正文73题、附件75题冲突）

作者 Eugene Tartakovsky；[正式论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1273607/full)及[PMC存档](https://pmc.ncbi.nlm.nih.gov/articles/PMC10713733/)均为 CC BY。正文核实73题、14个基础动机、4个高阶簇、1–6（not important at all → very important），对象是以色列18–30岁正在寻找伴侣的人。

**仍阻塞，原因已更新：** 已经从 Europe PMC 官方附件包取得并读取 `Data_Sheet_1.docx`；Table A1 有75题，与正文73题冲突，不能擅自删去两题。哈希、14维逐组题数和来源链接见[后续核验报告](research-scales-misc-integration.md)。需原作者或正式勘误澄清施测题集，故本实现工作树没有复制该候选或注册入口。

题集明确后还需实现基础维度个人中心化、四高阶簇等权汇总与两对立轴，不能直接用当前通用均分引擎。没有输出单一“恋爱动机高低总分”。

### BSFL（正式5题）

作者 José Ventura-León, Andy Rick Sánchez-Villena, Tomás Caycho-Rodríguez, Miguel Barboza-Palomino, Andrés Rubio。[正式论文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.583396/full)为CC BY；Instruments明确5题、单维，0 Never / 1 Rarely / 2 Sometimes / 3 Almost always / 4 Always。表2完整刊出5题西班牙语及英文。所有题语义同向，原研究是秘鲁18–40岁有高等教育经历的人群。

**已按本轮授权接入：** 原文未明确个人合成分算法，采用注明“4399描述性维度均分，非原作者正式评分”的五题均分（0–4）。全部同向，第4题不反向；保留英文、西班牙语、未验证中文翻译及原研究人群限制。许可与逐题核对见[后续核验报告](research-scales-misc-integration.md)。不宣称平台均分复现作者潜变量分析，不提供常模或分界。
