# 四套量表局部集成候选

核验日期：2026-10-11。仅本独立工作树的数据、独立评分建议和自检；没有修改共享 engine、分发、前端或主任务工作树，没有部署。中文为**未验证翻译**，不设临床阈值、常模或高低分级；小机作答不具有人类量表效度。

| 文件 | 数据状态 | 接入条件 |
| --- | --- | --- |
| `research_scales/data/ppts.json` | 18题；`blocked:false` | 可由现有通用引擎加载，两个维度分别取1–5均分 |
| `research_scales/data/bsfl.json` | 5题；`blocked:false` | 可由现有通用引擎加载，0–4均分 |
| `research_scales/data/romantic_motivations.json` | 原附件完整75题；`blocked:true` | 正文73题与附录75题冲突未解决，另需专用评分路由 |
| `research_scales/data/bsr.json` | 正式Study 2的30题版本元数据；0道刺激录入；`blocked:true` | 第三方推文逐条来源及再发布权利未核定 |

## PPTS / BSFL：平台描述性计分

两套均明确标注 **4399 描述性均分，非原量表标准分**。这是本次用户允许的平台汇总方式，不再以缺少原作者个人计分手册为阻塞理由，也不声称复现原论文的综合分数。标注保存在 `notice`、`scoring_note` 和结果维度名称中，现有引擎会把它们带入说明及结果。全部题目同向，`overall_mean:false`，PPTS 不合并总分或倒分提前完成题。

- PPTS：[原论文](https://link.springer.com/article/10.1186/s40359-025-03072-6)、[表1](https://link.springer.com/article/10.1186/s40359-025-03072-6/tables/1)、[正式全文XML](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC12247415/fullTextXML)。保留 Proc1–10、Prec1/2/3/5/6/7/8/9，删除原始 Prec4/10。18道英文逐条对照官方XML通过，仅统一末尾句号和空白。量尺仅两端有原文标签，中间保留数字。
- BSFL：[原论文表2及Instruments](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2020.583396/full)。5题英文保留在 `original`，西班牙语保留在 `original_es`；选项按0 Never / 1 Rarely / 2 Sometimes / 3 Almost always / 4 Always。第4题寻找陪伴不是反向题。
- 两篇原论文均明确链接 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，并非依赖聚合站授权标签。保留原作者、论文URL、许可URL及翻译/评分改编说明。

## Romantic Motivations：拿到原件后的新阻塞

[正文](https://www.frontiersin.org/journals/psychology/articles/10.3389/fpsyg.2023.1273607/full)写73题；[PMC附件入口](https://pmc.ncbi.nlm.nih.gov/articles/instance/10713733/bin/Data_Sheet_1.docx)在本环境返回浏览器检查页。成功来源是 [Europe PMC官方附件包](https://www.ebi.ac.uk/europepmc/webservices/rest/PMC10713733/supplementaryFiles)中的 `Data_Sheet_1.docx`（70,370字节；SHA-256 `117d95b4209954d5ee32ba5546dad1c546eca30f73d918e9a353cbd13edaaea8`）。原文为CC BY 4.0，附件未发现单独限制声明。

Table A1依次有：心理成长5、独立于父母4、摆脱孤独6、性满足6、社会地位提升5、控制对方5、经济利益8、获得尊重3、情感支持9、感到被爱6、组建家庭3、生育与养育5、避免社会压力4、关爱对方6，合计 **75**。没有加入Table A2的择偶偏好题。JSON原文直接从DOCX第一张表提取；题号为本地按附件行序编号，附件本身无编号。没有自行删两题凑73。

需原作者/正式勘误澄清真实施测题集。`expected_item_count:73`表示正文声称，`source_item_count:75`和 `items` 表示实际附件；不要只修改计数字段或解除 `blocked` 来上线。

独立评分建议代码：`research_scales/romantic_motivations_scoring.py`。当前候选调用会拒绝；它不接入共享引擎，也不能自行解决题数冲突。题集核定后，公式为：

1. 对全部作答求题目加权均值 `m`；每个基础动机的题目均分减 `m` 得到个人中心化分数。
2. 四高阶簇对其包含的基础动机分数**等权**平均，不按每维题数加权。分组采用正文Results的最终3/3/6/2结构，详见JSON `higher_order_clusters`。
3. 两轴分别为爱与关怀−地位与资源、性与探索−家庭与子女；不输出单一总分。

集成人员需在独立评分路由中同时使用该模块 `score(spec, answers)` 和 `result_dimensions(spec)`，保留 `scoring_note`。通用引擎当前只支持 sum/mean，不能把 `aggregation` 改成 mean 代替中心化/高阶评分。先核定来源、再接好专用路由、最后解除blocked。

## BSR：此前Study 2候选与原刺激差异

当前主测试工作树已另行接入[Study 4结构的4399娱乐改编版](research-scales-bsr.md)。以下是未发布的Study 2候选审计，推文仍未使用。

本候选固定为 [Pennycook等（2015）Study 2](https://sjdm.org/~baron/journal/15/15923a/jdm15923a.html) / [原始补充Table S2](https://journal.sjdm.org/15/15923a/supp.pdf)：20条生成句＋10条Chopra推文，30项均分表示接受度；不是识别正确率，也没有励志题差值。原始PDF已成功读取全部5页（221,035字节；SHA-256 `be4fd61a78b478c59700b197d0bfb76cbac319ef3d658e0d41a2dd054ea8c959`）。

旧刊正文许可是 [CC BY 3.0](https://creativecommons.org/licenses/by/3.0/)。附录对推文仅给 `twitter.com/deepakchopra` 账号级来源，没有逐条URL或再发布许可。不能以论文许可代替第三方权利证据。因此JSON仅存版本/量尺/来源/阻塞元数据，`items:[]`，没有复制推文作为可发布题库。

原先聚合站的10伪深刻＋10励志＋10普通陈述不是本次正式Study 2版本；其若干伪深刻项措辞、7/10普通陈述及3/10励志引语与正式附件不一致。S5励志引语也没有逐条来源或再许可声明，不能改用它们解除权利阻塞。没有拿自编句、近义改写或删题替代原题。若以后改选其他研究版本，须重新核对题集和评分；Study 3/4的敏感性方向是励志均分减伪深刻均分。

## 自检与合并

```
python3 -B scripts/check_research_scale_candidates.py
python3 -B scripts/check_research_scale_candidates.py --engine /path/to/research_scales/engine.py --romantic-docx /path/to/Data_Sheet_1.docx
```

检查四份JSON协议、真实题数/源编号/维度/量尺/方向、阻塞注册门槛；独立评分器用内存算法夹具验证中心化、不同题数维度的等权簇及拒绝无效输入。可选真实引擎检查PPTS/BSFL的full/full_fast、结果恢复与免责声明，数据库全部在自动删除的临时目录中。

只合并所需局部文件。主任务 `__init__.py` 会自动发现所有未blocked的JSON，复制PPTS/BSFL后即可能进入其注册表；不要误解为此工作树已完成全局分发或网页入口接入。其余两份必须继续保持blocked。`suppress_total`只是补充元数据，实际不生成总分依赖 `overall_mean:false` 和当前引擎按维度返回的行为。无需迁移存档或重启进程来进行本地验证。
