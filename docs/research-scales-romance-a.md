# 恋爱五套量表：来源与 4399 描述版交付

核验与验收日期：2026-10-11。本次按用户明确授权，为非商业娱乐用途交付 **5 套、98 题**。所有 JSON 均为 `blocked: false`、`aggregation: "mean"`、`overall_mean: false`，完整原题保存在 `original`，中文翻译保存在 `text`。作者、原始来源、CC 许可链接、翻译及汇总方式的修改声明均在 JSON 中。

这里的解除阻塞仅指 **4399 描述版具备完整题文、量尺、题组和透明算法**。正式作者分数的未决问题仍记录如下，未冒称已经复现。中文为未经心理测量验证的翻译；描述均分不适用于常模、诊断或效度推断，不输出截点、百分位、能力等级或预测结论。主引擎会将 `notice` 和 `scoring_note` 同时带入按需 Guide 与完整结果。

## 交付与评分协议

| JSON | 题数 | 选项数值 | 描述题组及连续题号 | 反向 |
| --- | ---: | --- | --- | --- |
| `research_scales/data/jrrs.json` | 16 | 1–5，平台按原五档顺序编码 | 全题认同程度：1–16（16） | 无 |
| `research_scales/data/ross.json` | 17 | 0–10 | 直接情感支持1–5（5）；直接实际支持6–9（4）；间接支持10–13（4）；不希望支持14–17（4） | 无 |
| `research_scales/data/rss.json` | 12 | 1–7 | 防御性1–4（4）；信任困难5–8（4）；关系技能不足9–12（4） | 9–12：`8-x` |
| `research_scales/data/irrc.json` | 35 | 1–5 | 控制感1–8（8）；换位9–14（6）；恋爱吸引力相关困扰15–17（3）；亲密回避18–22（5）；情绪调节困难23–26（4）；随和与灵活性27–29（3）；冲突解决30–35（6） | 32：`6-x` |
| `research_scales/data/rbi.json` | 18 | 1–5 | 陪伴与情感互动1–7（7）；伴侣责任8–14（7）；开放表达15–18（4） | 无 |

各题组使用 `mean = sum(转换后的题值) / 该组题数`，题内等权。只有完整作答才出结果，不对缺答插补。JRRS 仅有一个平台自定义的全题描述组；其他四套分别报告题组均分，均不生成跨维度总分。RoSS、RBI 保留原题方向的均分是平台规则，不把“没有发现作者反向说明”表述成获得了完整官方计分手册。所有选项中间档若未核到原版文字，只显示数字。

## JRRS：16 题，CC BY-NC 4.0

Aslı Nur İpek、Nihan Turhan，Jealousy in Romantic Relationships Scale（2023）。[官方论文与许可](https://ijcer.net/index.php/pub/article/view/428)；[官方 PDF](https://ijcer.net/index.php/pub/article/download/428/344)，Appendix 1，印刷页606／PDF第12页。全部16题来自该土耳其语附录，原文仅归一化换行和空白。

五档依次为 Hiç Katılmıyorum、Katılmıyorum、Kararsızım、Katılıyorum、Tamamen Katılıyorum，译为完全不同意、不同意、不确定、同意、完全同意。附录没有印出数值编码，`1–5` 明确作为4399对原顺序的编码，不声称核到了作者正式数值键。

[作者学位论文](https://acikerisim.fsm.edu.tr/xmlui/bitstream/handle/11352/4426/%C4%B0pek.pdf?isAllowed=y&sequence=1) §2.3.2，印刷页38／PDF第57页明示 “Ölçekte ters kodlanan madde bulunmamaktadır.”，即没有反向题。本次原题取自具备开放许可的期刊附录，学位论文仅作评分方向证据，不据此复制其整份内容。

正式三维映射仍冲突：期刊 Table 4 重复 Item 8、漏 Item 6；紧接正文第一维列1、2、3、4、6、7、9、13，第三维列5、8、10、15、16。学位论文§2.3.2第一维与第三维还存在不同分配。平台因此只算全16题认同均分；包括“嫉妒体现重视”等信念题，不能把均分解释为所有题都表示损害。

恢复官方三分量表还需要作者勘误后的最终题号映射，以及明确的正式数值编码和汇总说明。非商业限制保留在 JSON 许可字段；本次用途依据用户明确声明的非商业娱乐测试范围。原研究为土耳其18岁以上人群，中文效度尚未验证。

## RoSS：17 题，CC BY 4.0

Rhia E. Perks、Laura M. Vowels、Claire M. Hart、Rachel R. R. Francois-Walcott、Katherine B. Carnelley。[官方论文与许可](https://link.springer.com/article/10.1007/s12144-025-08749-0)；[官方 PDF](https://link.springer.com/content/pdf/10.1007/s12144-025-08749-0.pdf)。最终题文与分组来自 Table 3（PDF第11页），不是34题初始题池。JSON按表格从上到下编号1–17。第4题遵循作者 Study 3 后修订，删除多余的 “When I feel stressed” 前缀。

0（disagree strongly）至10（agree strongly），只给两端文字锚点。四维5/4/4/4题分别保留自身方向，高分表示更认同该应对方式；不把“不希望支持”反转后拼成关系优劣总分。指导语保持痛苦难过、需要伴侣支持的情境。原研究入组要求18岁以上、当前关系至少6个月。

正文的分量表均值与单题均值的算术平均吻合，只是对作者算法的推断；本实现明确是4399等权均分。若要恢复作者正式计分，需要 [OSF分析代码／材料](https://osf.io/rv56c/?view_only=83c4a6489a2b46698ed68a71ec230fd7) 或 [正式补充DOCX](https://media.springernature.com/original/springer-static/esm/art%3A10.1007%2Fs12144-025-08749-0/MediaObjects/12144_2025_8749_MOESM1_ESM.docx) 的汇总及缺失处理说明；本次仍未取得可核查内容。作者机构学位论文 [归档页](https://eprints.soton.ac.uk/511035/) 的全文受限至2027-04-17，不能当作已取得的评分手册。

## RSS：12 题，CC BY 4.0

Raquel Peel、Nerina Caltabiano（2021）。[官方论文与许可](https://link.springer.com/article/10.1186/s40359-021-00644-0)；[官方 PDF](https://link.springer.com/content/pdf/10.1186/s40359-021-00644-0.pdf)。采用 Table 2（PDF第9页）最终12题、编号及维度。JSON 1–12依次对应原60题题池：`18,19,23,28,8,37,44,45,26,40,41,42`。最终9–12题均明示R，按7档量尺转换为 `8-x`。

量尺1 strongly disagree 至7 strongly agree；中间档不添加未经证实的锚点。指导语为通常的恋爱经历，没有当前伴侣时回想上一段关系。三个均分均按“困难更多”方向解释，未赋予诊断含义。

**正式方法与本版有明确区别**：正文 Scale construct validity 明示使用单因子同属模型得到的因子得分回归权重，将权重归一到和为1来构建复合变量，并指出这不同于原始题值相加。Table 2的载荷不是这组可直接代入的评分权重。本次使用等权描述均分，不假称是作者方法。

要恢复正式分数，仍需最终2021版的因子得分回归系数及完整计算规范，或 [作者官网](https://www.raquelpeel.com/relationship-sabotage-scale/) 联系入口提供的计分手册。未借用 [2020博士论文](https://researchonline.jcu.edu.au/65696/1/JCU_65696_peel_raquel_thesis_2020.pdf) Appendix N 的旧RSSS题目或常模：其题文不同，且附有版权声明。

## IRRC：35 题，CC BY 4.0

Anthony J. Faber、Eu Gene Chin、Victor R. Wilburn、Shahrokh M. Shafaie（2019）。[正式 DOI](https://doi.org/10.5964/ijpr.v13i1.341)；[PsychArchives正文与许可记录](https://www.psycharchives.org/en/item/77434b02-0954-4201-a3c3-25e403cab250)；本次成功取得的 [Dialnet机构全文PDF](https://dialnet.unirioja.es/descarga/articulo/7065905.pdf)。该PDF是同一篇期刊文章，Appendix B印刷页94–95／PDF第23–24页完整给出最终35题、最终编号及EFA题池号；Table 3印刷页82–83／PDF第11–12页给出分组与反向键。所有原题已与Appendix B逐项程序比对，保留其标点，仅归一化空白。

五档完整锚点：1 Almost Never or Never True；2 Not Very Often True；3 Sometimes True；4 Often True；5 Almost Always or Always True。中文依次为几乎从不或从不符合、不太常符合、有时符合、经常符合、几乎总是或总是符合。

最终1–35对应EFA原题池号如下，不能将原题池跳号当作连续题号：

`1,2,3,4,5,6,7,8,39,40,41,42,43,44,12,13,45,19,20,21,22,29,23,24,26,27,16,17,18,35,36,37,38,30,31`

最终32题对应题池37，Table 3明示R；仅该题反向为 `6-x`。关系控制感、换位思考、随和与灵活性、冲突解决维度高分表示更认同能力。原名 Romantic Appeal 的三题实际询问担心孤独、孤独和难找约会对象，平台按原方向报告“恋爱吸引力相关困扰”；Emotion Regulation 四题按原方向报告“情绪调节困难”。这两处是描述标签的明确调整，不凭量表名称臆造反向键。亲密回避亦保持高分表示更多困难。七维不汇总为能力总分。

**官方Excel仍未核到公式**：[官方附件记录](https://www.psycharchives.org/en/item/c7831282-1f84-428c-a1e1-b5af37886691) 标示CC BY 4.0，文件名 `Faber_Chin_Wilburn_Shafaie_2019_Score_Estimation_File_(IRRC)_Suppl.xlsx`，MD5 `5a1a26d6976b6c481134cf110b7ac45e`。[文件下载端点](https://pada.psycharchives.org/bitstream/88639f94-8ee8-418d-ad66-2963aab9792f) 本次返回502，不能声称已获得或验证算法。

作者推荐使用模型参数校正的潜变量估计，涉及反向、载荷、误差相关及性别DIF，并提供95%置信区间。恢复此正式模式需要可打开的上述Excel、公式及参数、可对照输入输出样例；通用均分引擎不能冒充。当前描述版无需性别输入，不作性别DIF假设，不生成潜变量、置信区间或官方IRRC分数。原研究为年轻成人大学生，中文改版未验证。

## RBI：18 题，CC BY 4.0

Tal Harel、Meni Koslowsky（2024）。[官方论文与许可](https://www.nature.com/articles/s41598-024-58901-2)；[官方PDF](https://www.nature.com/articles/s41598-024-58901-2.pdf)，Table 2／PDF第7页为最终18题。三维7/7/4题；JSON连续编号按该表排列，原题池号为：SAI `3,8,9,14,21,22,25`；FOD `1,2,4,13,18,19,23`；OR `10,11,15,16`。未采用初始27题、先导25题或Table 1的20题。

Study 2问 **过去一周的伴侣互动**，1 Not at all 至5 Very much；未混入先导Study 1的同意度锚点。所有题保留原方向；高分表示过去一周相应行为更符合自身情况，既不是“追人主动性”，也不是伴侣的评价或配对预测。原验证为111对交往超过一年的以色列异性伴侣。

正文的因子结构、载荷及关系长度调节模型不能直接作为个人评分公式。等权均分是4399自定义描述；若要恢复官方个体分数，仍需通讯作者提供最终18题的正式分量表汇总规则／分析代码。文章说明原始数据可向作者请求；[作者机构记录](https://cris.biu.ac.il/en/publications/development-and-validation-of-the-relational-behavior-interaction/) 可辅助确认论文身份，但不代替评分材料。

## 本地验收与合并边界

只读主实现树的 `research_scales/__init__.py`、`engine.py`、`data/*.json` 和原专项文档。将所读两个引擎文件及本次五套数据复制到系统临时目录，以本隔离树的既有 `scale_test_engine.py` 为依赖运行；使用 `python3 -B` 避免产生字节码，`SESSIONS_DB` 显式指向临时SQLite，临时目录退出时清理。没有安装依赖，没有写主实现树、生产库或生产目录。

验收全部通过：

- 5套98题连续题号、非空原题与译文、完整选项、维度题号和题数、反向键及许可字段检查；主注册器读入5套且均进入MCP候选集合。
- JRRS16题、IRRC35题与取得的原始PDF最终附录逐项比对；RoSS/RSS/RBI的47道原题与主树已核题库保持一致，再复查正式PDF表格。修改只涉及中文措辞、描述规则、状态及必要展示元数据。
- 各套全低、全高、中间值及混合作答与独立题号规则计算的均分一致；98次单题加1检查，确认只影响对应维度且变化幅度为正／负`1/题数`。
- 逐题full与批量full_fast共10个完整临时会话，结果数据一致。每套Guide及完整结果均包含“不是作者官方分数”、翻译未验证、具体算法、作者、来源和许可。

混合样例按零起始题号 `i` 生成 `最低值 + (3*i+1) % 档数`，输出如下：

| 量表 | 各维度按JSON顺序的未显示舍入得分 |
| --- | --- |
| JRRS | 2.9375 |
| RoSS | 4.8 / 4 / 5 / 6 |
| RSS | 3 / 4.5 / 3.75 |
| IRRC | 3.125 / 2.6666666666666665 / 3.6666666666666665 / 3 / 2.5 / 3 / 2.5 |
| RBI | 3.142857142857143 / 2.7142857142857144 / 3.5 |

实测完整按需Guide字符数（不是token）：JRRS1198、RoSS1397、RSS1230、IRRC1357、RBI1165。`notice+scoring_note`字符数相对主树候选：RoSS96→259、RSS88→250、RBI109→282；JRRS新增281、IRRC新增402。增加的内容用于防止描述均分冒充官方分数及说明不同高分方向；未扩张根MCP schema、list或任何全局说明。

可复核文件SHA256：

- 本次读取的主树 `engine.py`：`8428605ad96cc8688437e80c68fbbb7a00096f4e0be2de49fcae1742d1a0ac2b`。
- 本次读取的主树 `__init__.py`：`9aca38ec8ca1c88971c81cba46661f5c212f162084725bc9c074d0ec2277b1ce`。
- JRRS官方PDF：`36a761da5835436de405e649842eb2595f63fcacb52c34e37fb87c1fd9831e65`。
- IRRC机构PDF：`ac2a349193d4141b903a00f5852a8de77924c874c5335c2543e356d3e039c9ad`。

网络并非完全不可用：浏览工具能够读取正式PDF，本地直接下载部分出版方PDF却返回HTML；未将这些响应当作PDF证据。IRRC附件502、RoSS补充材料未取得等限制如上保留。

本隔离树仅交付五个JSON和本记录。共有引擎、server.py、index.html均未修改；未部署、未push。数据已满足本次描述版验收，供主Codex合并；页面入口及主服务接入由主实现负责，本任务未宣称已在生产上线。
