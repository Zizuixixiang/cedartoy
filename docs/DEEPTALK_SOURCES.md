# Deeptalk 深聊问答：题库来源与使用条件

核验日期：2026-10-11。本次按南杉批准的范围整理四套真实现成题单，仅交付 `deeptalk/data/*.json` 与本说明。

## 实际交付

| topic_id / 文件 | 对外名称 | 题数 | 侧重点 | 来源与使用条件 |
| --- | --- | ---: | --- | --- |
| [relations_communication](../deeptalk/data/relations_communication.json) | 关系与沟通 | 13 | 界限、倾听、征求同意与沟通 | BCcampus H1/H3 官方简体中文；Context and Connection 第6.8节指定 PDF 中译；各来源 CC BY 4.0 |
| [couple_50](../deeptalk/data/couple_50.json) | 恋人之间的50问 | 50 | 相处偏好、日常互相了解和感受 | [そらとひかり / note](https://note.com/magic_stork7986/n/nd855484b35ab) 配布用模板；作者允许分享问题共同使用，非 CC 许可 |
| [relationship_scenarios](../deeptalk/data/relationship_scenarios.json) | 亲密关系情境 | 14 | 具体关系情境、相处与冲突 | [BCcampus 官方简体中文 H4](https://opentextbc.ca/isvinternationalzh/wp-content/uploads/sites/454/2023/10/ZH_H4.pdf)；遵守 [SFU InterroBang](https://www.sfu.ca/sexual-violence/education-prevention/interrobang/print---play-.html) 的 CC BY-NC 4.0 上游条件 |
| [romance_values_100](../deeptalk/data/romance_values_100.json) | 恋爱观100问 | 100 | 恋爱边界、依赖、同居、未来规划与价值观 | [デジタル教科書](https://fucchan-blog.com/100questions-romance/4109/)「コピペOK」复制使用声明，非 CC 许可 |

共 **4 包、177 项完整对话提示，缺口 0**。沿用产品“题”的计数；原站第100项是邀请向读者说句话的结束提示，也原样保留。一题可以有多个相连追问；两套日文题单按原编号一对一翻译，不补题、删题或拆问凑数。题包标题和简介由 CedarToy 编排，日文题中译由 CedarToy 完成，均非官方译文。

原有 `relationships_boundaries` 5题、`communication_understanding` 4题、`consent_respect` 4题已依次合并至 `relations_communication`。13个问题对象的 `id`、`text`、`source_ref` 全部原样保留；原作者、许可和改编说明保留在 `sources`，用 `question_ids` 精确映射。旧三个 JSON 已被合并文件取代，数据目录仅保留四个最终题包，不再作为三个独立题包注册。若后续整合方已有旧 `(topic_id, id)` 引用，应将前三个旧 topic_id 映射至 `relations_communication`，题 ID 不变；本次未修改服务注册逻辑。

所有题均接受自由文本回答，无选项约束、分数、匹配阈值或心理诊断。原题中的二选一、是非问法照译，回答者仍可解释、谈不适用或略过。两套日文模板只取问题，不取作者或读者的个人回答；不引入创作人物档案问卷。

## 关系与沟通：原有来源与翻译核验

### 官方简体中文：界限与同意

作品：《沟通、健康关系与同意：面向BC省高等院校的资源》；作者为 **Intersectional Sexualized Violence – International Student Resource Development Team**；版权 **© 2024 Government of British Columbia**；出版者 **BCcampus**。

授权与版本证据：

- [中文版主页](https://opentextbc.ca/isvinternationalzh/)的 License、Metadata 和署名要求明确 CC BY 4.0，另行标注的内容除外。
- [官方翻译说明](https://opentextbc.ca/isvinternationalzh/front-matter/翻译/)确认这是简体中文版本。没有另行可核实的中文译者个人署名，因而不虚构译者。
- [全套讲义](https://opentextbc.ca/isvinternationalzh/chapter/全套讲义/)提供 H1、H2、H3、H4 的下载入口。
- [ZH_H1.pdf](https://opentextbc.ca/isvinternationalzh/wp-content/uploads/sites/454/2023/10/ZH_H1.pdf#page=1)和 [ZH_H3.pdf](https://opentextbc.ca/isvinternationalzh/wp-content/uploads/sites/454/2023/10/ZH_H3.pdf#page=1)均只有1页，文件自身写明知识共享署名4.0国际许可及2024年不列颠哥伦比亚省政府版权；所采用的问题区没有另列第三方作者或限制性许可。
- [Using the Modules](https://opentextbc.ca/isvinternational/chapter/using-the-modules/)的 Adapting the Modules 明确允许修改问题、反思及讲义，Opportunities for Extended Learning 明确允许线上或线下小组讨论。这里只用来核实使用方式，具体题文授权仍核到各讲义。
- 许可全文：[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)；[法律条文](https://creativecommons.org/licenses/by/4.0/legalcode)。

逐题定位及改动：

| 题 ID | 原文位置（均为 PDF 第1页） | 处理 |
| --- | --- | --- |
| `h1_01` | H1“更多了解自己的界限”，问题项目符号1：安全和支持 | 逐字保留 |
| `h1_02` | H1，问题项目符号2：时间安排及决定不被尊重 | 合并排版换行，保留两问 |
| `h1_03` | H1，问题项目符号3：未经询问发布照片 | 逐字保留 |
| `h1_04` | H1，问题项目符号4：不舒服的话题及表达拒绝 | 合并排版换行，保留两问 |
| `h1_05` | H1，问题项目符号5：个人空间 | 逐字保留，保留两问 |
| `h3_01` | H3“更多了解‘同意’对你意味着什么”，问题项目符号1：理解的变化 | 合并排版换行，保留两问 |
| `h3_02` | H3，问题项目符号2：希望被征求同意的情况 | 逐字保留 |
| `h3_03` | H3，问题项目符号3：性行为之外的同意及例子 | 逐字保留，保留两问 |
| `h3_04` | H3，问题项目符号4、5、6：是否征求伴侣同意；如果是；如果没有 | 按原顺序合成一题，两个条件分支都保留，没有删问或改词 |

H1 保留原文的“我”，由回答者自行代入；H3 保留原文的“你”。不把“我”自动解释成系统或另一位参与者。H3 的年龄、伴侣和经历可能不适用于部分参与者，允许直接略过，不要求小机虚构成长或现实伴侣经历。

没有把讲义的说明段、日记操作指令、软硬界限解释纳入题目。H1 原文允许不公开回答；转成双人对话时必须保留自主选择，不能用“双方可见”强迫披露。H2《界限和健康关系》只有设定及接受界限的建议，本次不把它改写成无出处的新问题。

可使用的署名形式：

> 题文选自《沟通、健康关系与同意：面向BC省高等院校的资源》，Intersectional Sexualized Violence – International Student Resource Development Team 编写，© 2024 Government of British Columbia，BCcampus 出版，CC BY 4.0。采用官方简体中文讲义；排版及题目合并情况见题包 adaptation_note。

展示署名时同时保留对应题包 `source_url` 和 `license_url` 的可访问链接；不得暗示原作者为 CedarToy 背书。

### 英文原题中译：倾听与沟通

作品：**Interpersonal Communication: Context and Connection**，ASCCC Open Educational Resources Initiative（OERI）。采用的是第 **6.8: Summary and Review** 节 **Discussion Questions 第2—5题**；该节署名为 **Victoria Leonard & Elizabeth Coleman**。中文翻译及题包编排由 CedarToy 本次完成，并以 CC BY 4.0 提供；不是官方中文翻译。

采用版本与授权链：

1. 原有题库记录的 [Open Textbook Library 条目1573](https://open.umn.edu/opentextbooks/textbooks/1573)提供书名、作者、出版者和 CC BY 目录信息。它是发现入口，不能单独代替章节授权。
2. [出版者 ASCCC OERI 的资源目录](https://asccc-oeri.org/asccc-oeri-resources/)把本书列为 CC BY（另有说明除外）；[出版者学科目录](https://asccc-oeri.org/open-educational-resources-and-communication-studies/)的 Interpersonal Communication 小节也有相同说明。
3. 本次题文及许可版本限定为 [Coalinga College 保存的完整教材 PDF](https://coalingacollege.edu/oer/documents/interpersonalcommunicationcontextandconnectionascccoeri.pdf)。**印刷页码6.8.1**包含完整讨论题，**印刷页码6.8.2**是词汇表续页及该节的 CC BY 4.0 授权、Victoria Leonard & Elizabeth Coleman 署名。这里的页码是页面上印的节内页码，不是 PDF 阅读器的总页序号。
4. 该 PDF 的第6.8节题目及节末声明没有列出这四题的第三方版权例外；只提取问题，不复制配图、词汇表或其他章节。

**与当前网页的差异及核验限制：** 当前 [LibreTexts 第6.8节网页](https://socialsci.libretexts.org/Bookshelves/Communication/Interpersonal_Communication/Interpersonal_Communication:_Context_and_Connection-OERI/06:_Listening/6.08:_Summary_and_Review)标为 **CC BY-SA 4.0**。没有将该网页重标为 CC BY，也没有以目录信息覆盖该网页许可。本次采用的是院校保存、节末明确标注 CC BY 4.0 的 PDF 文本。其题目通过检索工具返回的 PDF 正文抽取逐题读取，并与网页上的完整英文题文交叉核对；该 PDF 约89.8 MB，超过浏览工具的全文打开限制，未完成整个文件的下载、哈希或逐页截图核验。检索另返回[大小写不同的 PDF 地址](https://coalingacollege.edu/oer/documents/InterpersonalCommunicationContextandConnectionASCCCOERI.pdf)的同节题文；没有声称两个地址的二进制文件相同。可复查时在采用的 PDF 内搜索 `6.8: Summary and Review`、`6.8.2` 和作者名。对于后续增加的章节或换用版本，应重新核验，不能沿用本节结论。

完整英文原题（上述 PDF，印刷页码6.8.1；保留在此便于中译复核）：

| 题 ID | 原编号 | 原题 |
| --- | ---: | --- |
| `ccc_6_8_02` | 2 | What is your listening style? Can you think of an instance when this listening style can be beneficial? Can you provide an example of when this listening style is harmful? How can knowing this information help improve your listening? |
| `ccc_6_8_03` | 3 | What barriers to listening impact you the most? What steps can you take to lessen these barriers within different contexts of communication? |
| `ccc_6_8_04` | 4 | Which of the ineffective listening practices do you find yourself prone to using? What specific changes do you need to make to minimize or eliminate these practices? |
| `ccc_6_8_05` | 5 | Which of the active listening strategies can you use to improve your relationship with others? |

翻译说明：`listening style` 译为“倾听时的方式”，`ineffective listening practices` 译为“妨碍有效倾听的做法”，`active listening strategies` 译为“积极倾听的方法”。保留四题全部追问，不添加题外情境或特定答案，不给倾听风格分型。教材原第1题依赖课程背景，初始13题库未收录；本次原样合并其中已选的第2—5题，不新增或删除教材题。

可使用的署名形式：

> 译自 Interpersonal Communication: Context and Connection，第6.8节，Victoria Leonard & Elizabeth Coleman，ASCCC OERI；采用 Coalinga College 保存的该节 CC BY 4.0 PDF 版本。中文翻译与编排：CedarToy，CC BY 4.0。

## 恋人之间的50问：作者配布模板

原文为 **そらとひかり** 的《恋人に聞いてみたい50の質問》，[note 原文](https://note.com/magic_stork7986/n/nd855484b35ab)，发布日期 2025-08-20。采用文章中“💡恋人に聞いてみたい50の質問（配布用テンプレ）”下面的“🗒 質問リスト”第1—50题；不采用下方“そらの50問回答集”的回答，也不把重复展示的题目再次计数。

授权依据在原文模板及“使い方の例”：作者说明这是个人制作并公开配布的问答模板，可自己回答、采访对方、两个人一起玩，明确列出“質問だけをシェアしてみんなで使う”（只分享问题供大家使用）。这属于作者明确提供的模板分享使用许可，没有 CC 标识；`license_url` 因而指向原文声明所在页，不能改标 CC BY 或公共领域。南杉已批准本次中文整理；该批准是平台的使用决定，不冒称取得作者另行签署的翻译或无限再授权协议。

逐题 `source_ref` 均指向原文 URL、模板小节和原编号。中译保留全部50题与原顺序。第7题涉及色情书刊，第11题是现实伴侣装扮偏好；保留原题含义，均允许略过。原文第28—30题包含时光机、超能力、哆啦A梦道具的假设，按模板照译，不扩写为角色设定题。`アーティスト` 按本文音乐语境译为“音乐人”，`ネタ` 按寿司语境译为“配料”。

核验方式与限制：浏览工具直接打开 note 原页多次返回 Cache miss；随后以原文标题和作者检索，取得**该原站页面**返回的完整配布模板、1—50连续编号及分享声明。实际逐题读取的是该原页的检索正文，不是其他人的答卷或摘要。已完成50题全文及顺序核验；未声称下载了原站 HTML 或完成页面截图。

建议按需署名：“原题：そらとひかり《恋人に聞いてみたい50の質問》，作者配布模板；中文翻译与编排：CedarToy。”同时提供原文及授权声明链接。

## 亲密关系情境：官方简体中文与上游非商业条件

采用 [ZH_H4.pdf《其他问题》](https://opentextbc.ca/isvinternationalzh/wp-content/uploads/sites/454/2023/10/ZH_H4.pdf#page=1) 唯一一页中的全部14个问题项目符号，**按官方简体中文逐字保留，仅合并排版换行**。PDF 开头说明题目与场景提取自 InterroBang，允许自我反思或与朋友分享；它还包含有关界限、性取向、性别、性行为和资源的开放反思，不能为了题包名把这些题改写成新情境。

原题署名为 **Sexual Violence Support & Prevention Office, Simon Fraser University**。官方 [InterroBang Print & Play / Attribution](https://www.sfu.ca/sexual-violence/education-prevention/interrobang/print---play-.html) 明确准许复制、再分发、改编，条件是署名且仅非商业使用；链接到 [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/)。平台按本任务确认的非商业用途收录，南杉已批准。

中文讲义由 **Intersectional Sexualized Violence – International Student Resource Development Team** 提供，**© 2024 Government of British Columbia，BCcampus**。其页脚标注 [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)，但本包不把这一标注解释为豁免 InterroBang 原题的 NC 条件；对整个题包标 `CC BY-NC 4.0`，保留原题与中文讲义双方署名、出处和改动说明。旧说明中基于“统一 CC BY”的14题排除结论已由本次批准的非商业使用范围取代。

| ID | H4 第1页原小节 / 小节内项目符号 | 全页顺序 |
| --- | --- | ---: |
| `h4_01` | 理解和表达我们的界限 / 1 | 1 |
| `h4_02`—`h4_10` | 界限和健康关系 / 1—9 | 2—10 |
| `h4_11`—`h4_14` | 谈谈有关同意的话题 / 1—4 | 11—14 |

这些编号是本次按小节项目符号建立的稳定 ID，原 PDF 没有印刷题号。每题 `source_ref` 保留 PDF URL、页码、小节和小节内位置；上游网址在本节及 JSON `adaptation_note` 中。题文已与 PDF 正文抽取逐项核对；浏览工具的截图调用未成功，未声称完成截图核验。

建议按需署名：“原题：InterroBang，Sexual Violence Support & Prevention Office, Simon Fraser University，CC BY-NC 4.0。采用 BCcampus《沟通、健康关系与同意》官方简体中文 H4，Intersectional Sexualized Violence – International Student Resource Development Team，© 2024 Government of British Columbia；CedarToy 仅合并排版换行及编排题包。”同时提供 H4、SFU 和两个许可链接。

## 恋爱观100问：原站复制使用声明

原文是 [デジタル教科書《恋愛限定！100の質問【コピペOK】》](https://fucchan-blog.com/100questions-romance/4109/)，页面显示日期 2023-04-29。原页未列个人作者署名，因此 `author` 采用站名并注明这一事实，不从域名猜测作者昵称。采用同名小节中的完整编号1—100；文章下方推荐的“恋愛50の質問”等其他链接不属于本题单。

授权依据：标题明确写“コピペOK”，正文写“コピペして使える恋愛に関する100の質問を用意しました。”（准备了可复制粘贴使用的100个恋爱问题）。按此声明和南杉的明确批准收录本次中文题库；这是原站复制使用许可，**不是 CC BY、公共领域或无限制再许可**。`license_url` 指向该原站声明页；未从“可复制使用”推导作者对所有用途、所有素材或任意未来商业再发行的许可。翻译与编排为 CedarToy 完成，保留原站来源，不暗示原作者提供或认可中文译文。

完整题单既有恋爱边界、依赖、同居和未来规划，也有日常喜好、经历与结尾留言；按真实内容保留全部100项。原文没有独立的“吃醋”题，不凭题包概述补入新问题。第100项原文“見てくれた方に一言！”是结束提示，不伪造为新的价值观问题。

翻译核对重点：`浮気` 译为“出轨”；`束縛` 为“束缚”；`依存` 为“依赖”；`好きバレ` 为喜欢某人的心意被察觉；`モテ期` 为“最有桃花运的时期”；`オタク` 保留为“御宅族”；`蛙化現象` 保留日语俗称并附“喜欢的人回应好感后，自己反而产生排斥感”的简短释义，不赋予诊断意义。原问使用“异性”的地方保留原意，未擅改人称、关系对象或另加追问。`LINE` 是原题明确提及的应用名，未替换为本地应用。

建议按需署名：“原题：デジタル教科書《恋愛限定！100の質問【コピペOK】》，依原站复制使用声明；中文翻译与编排：CedarToy。”同时提供原文链接。

## 数据与按需披露约定

- 最终四份 UTF-8 JSON 的顶层字段为 `topic_id, title, description, author, source_url, license, license_url, adaptation_note, questions`；仅关系与沟通另有跨来源 `sources`。每题字段严格为 `id, text, source_ref`；文件名与 `topic_id` 相同，ID 在四包间唯一。
- `couple_50_001`—`couple_50_050` 和 `romance_values_100_001`—`romance_values_100_100` 对应原站编号；`h4_01`—`h4_14` 按 H4 顺序；旧13题沿用原 ID。不要因排序、中文修订或题包合并重建题 ID。
- 题包详情或来源入口按需读取作者、出处、授权链接和改编说明；跨来源包按当前题 ID 查 `sources`。普通发现索引只需名称与短简介，选包后只加载当前所需题文；不要把本说明、150条日文对照或大量版权说明常驻在工具 schema、所有 Guide 或每轮上下文中。
- 对外使用表内四个中文名称。所有题目开放回答，可解释、略过或说明不适用；不强迫披露，不以略过或回答内容评分、贴标签或作心理诊断。题文使用许可不自动覆盖用户的回答。涉及个人经历时不要求人类或小机虚构现实生活史。
- 如后续接入双人共享回答，需在提交前说明谁可见；本次只交付数据，不实现或修改任何交互、服务注册、前端、Guide 或工具 schema。

## 核验与交付范围

已核对四套实际数量 **13 + 50 + 14 + 100 = 177**、原题位置及许可声明；两套日文逐题翻译并保存下方原文对照。JSON 校验覆盖重复键拒绝、必需字段、ID唯一、来源与题文非空、文件名一致、连续编号与原顺序、原13题完整保留、H4逐字对照；另执行 `git diff --check`。原题间可能主题接近，不以跨包“去重”为由删掉真实题目。

获取限制：note 用原站检索正文完成核验；Coalinga 大 PDF 仍有前文所述全文下载/截图限制；H4 有正文、无成功截图。这些限制均未导致题文或编号缺口。未触碰生产主树、其他工作树、服务/前端代码、数据库或 vendor，未部署、push 或重启。交付为本隔离工作树的本地未提交文件。

## 日文原题逐项对照

以下仅供维护者按 ID 复核译文，中文题文在对应 JSON `text` 中，不作为每轮玩法提示加载。表内各行原文取自各节链接的原站题单，保留原编号；对应 JSON 每题 `source_ref` 再次保存该网址、小节与编号。

### 恋人之间的50问：日文原题

[原文](https://note.com/magic_stork7986/n/nd855484b35ab)。

| 原编号 | 稳定 ID | 日文原题 |
| ---: | --- | --- |
| 1 | `couple_50_001` | 最近ハマってることは？ |
| 2 | `couple_50_002` | 好きな映画ジャンルは？ |
| 3 | `couple_50_003` | 1週間休みがあったら何したい？ |
| 4 | `couple_50_004` | 得意料理は？ |
| 5 | `couple_50_005` | 右利き？左利き？ |
| 6 | `couple_50_006` | 犬派？猫派？ |
| 7 | `couple_50_007` | えっちな本とか見る？ |
| 8 | `couple_50_008` | 子どもの頃の夢は？ |
| 9 | `couple_50_009` | 好きなアーティストは？ |
| 10 | `couple_50_010` | カラオケでよく歌う曲は？ |
| 11 | `couple_50_011` | 恋人にさせたいコスプレは？ |
| 12 | `couple_50_012` | 好きなアイスのフレーバーは？ |
| 13 | `couple_50_013` | 好きなカフェメニューは？ |
| 14 | `couple_50_014` | 好きなバーガーショップは？ |
| 15 | `couple_50_015` | 好きなお酒は？ |
| 16 | `couple_50_016` | タバコ吸う？ |
| 17 | `couple_50_017` | 香水はつける？ |
| 18 | `couple_50_018` | 恋人との旅行で行きたい場所は？ |
| 19 | `couple_50_019` | テーマパークで行きたいのは？ |
| 20 | `couple_50_020` | 好きな服のブランドは？ |
| 21 | `couple_50_021` | 洋食・和食・中華、どれが好き？ |
| 22 | `couple_50_022` | 好きなお寿司のネタは？ |
| 23 | `couple_50_023` | 今までで一番笑った出来事は？ |
| 24 | `couple_50_024` | 最近泣いたのはいつ？ |
| 25 | `couple_50_025` | 自分を一言で表すなら？ |
| 26 | `couple_50_026` | 理想のデートは？ |
| 27 | `couple_50_027` | 憧れの職業は？ |
| 28 | `couple_50_028` | もしもタイムマシンがあったら？ |
| 29 | `couple_50_029` | もしも超能力が使えたら？ |
| 30 | `couple_50_030` | もしもドラえもんの道具を一つもらえるなら？ |
| 31 | `couple_50_031` | 休みの日の過ごし方は？ |
| 32 | `couple_50_032` | 朝型？夜型？ |
| 33 | `couple_50_033` | インドア派？アウトドア派？ |
| 34 | `couple_50_034` | スマホでよく使うアプリは？ |
| 35 | `couple_50_035` | 好きなゲームは？ |
| 36 | `couple_50_036` | 好きな本は？ |
| 37 | `couple_50_037` | 好きな漫画は？ |
| 38 | `couple_50_038` | 学生時代得意だった科目は？ |
| 39 | `couple_50_039` | 苦手だった科目は？ |
| 40 | `couple_50_040` | 勉強するときのスタイルは？ |
| 41 | `couple_50_041` | 仕事するときに大事にしてることは？ |
| 42 | `couple_50_042` | これから挑戦したいことは？ |
| 43 | `couple_50_043` | 人生で一番大切にしてることは？ |
| 44 | `couple_50_044` | 尊敬する人は？ |
| 45 | `couple_50_045` | 夢は何？ |
| 46 | `couple_50_046` | 幸せを感じる瞬間は？ |
| 47 | `couple_50_047` | 緊張するのはどんなとき？ |
| 48 | `couple_50_048` | 朝ごはんと夜ごはん、どっちを重視してる？ |
| 49 | `couple_50_049` | 最近欲しいものは？ |
| 50 | `couple_50_050` | 今一番言いたいことは？ |

### 恋爱观100问：日文原题

[原文](https://fucchan-blog.com/100questions-romance/4109/)。

| 原编号 | 稳定 ID | 日文原题 |
| ---: | --- | --- |
| 1 | `romance_values_100_001` | 好きなタイプは？ |
| 2 | `romance_values_100_002` | 恋人には何て呼ばれたい？ |
| 3 | `romance_values_100_003` | 付き合うなら、年上？タメ？年下？ |
| 4 | `romance_values_100_004` | 実際今まで付き合ったのは、年上？タメ？年下？ |
| 5 | `romance_values_100_005` | どんな外見が好き？ |
| 6 | `romance_values_100_006` | どんな性格が好き？ |
| 7 | `romance_values_100_007` | 外見、性格、お金、重視するのは？ |
| 8 | `romance_values_100_008` | 理想の身長差は何センチ？ |
| 9 | `romance_values_100_009` | 初恋は何歳？ |
| 10 | `romance_values_100_010` | 初恋の相手は？ |
| 11 | `romance_values_100_011` | 初恋の相手との思い出、エピソード |
| 12 | `romance_values_100_012` | 今まで付き合った人数は？ |
| 13 | `romance_values_100_013` | 最長何年付き合った？ |
| 14 | `romance_values_100_014` | 好きな異性の仕草は？ |
| 15 | `romance_values_100_015` | 好きな異性の髪型は？ |
| 16 | `romance_values_100_016` | 好きな異性の服装は？ |
| 17 | `romance_values_100_017` | 一目惚れしたことある？ |
| 18 | `romance_values_100_018` | キュンとする瞬間は？ |
| 19 | `romance_values_100_019` | アウトドア派？インドア派？ |
| 20 | `romance_values_100_020` | 理想の初デートは？ |
| 21 | `romance_values_100_021` | 結婚願望はある？ |
| 22 | `romance_values_100_022` | 子供は何人ほしい？ |
| 23 | `romance_values_100_023` | ペット飼いたい？ |
| 24 | `romance_values_100_024` | 将来はどこに住みたい？ |
| 25 | `romance_values_100_025` | 家事はする？ |
| 26 | `romance_values_100_026` | 好きな家事は？ |
| 27 | `romance_values_100_027` | 苦手な家事は？ |
| 28 | `romance_values_100_028` | 恋人に作ってほしい料理は？ |
| 29 | `romance_values_100_029` | 作ってあげたい料理は？ |
| 30 | `romance_values_100_030` | 恋人と一緒にやりたいことは？ |
| 31 | `romance_values_100_031` | 幸せだと感じる瞬間は？ |
| 32 | `romance_values_100_032` | 今まで恋人にもらって一番うれしかったプレゼントは？ |
| 33 | `romance_values_100_033` | 方言がある異性はどう思う？ |
| 34 | `romance_values_100_034` | ドライブしたい？ |
| 35 | `romance_values_100_035` | 何フェチ？ |
| 36 | `romance_values_100_036` | 恋人の香水はあり？なし？ |
| 37 | `romance_values_100_037` | 自分のチャームポイントは？ |
| 38 | `romance_values_100_038` | どれくらい付き合ったら結婚できる？ |
| 39 | `romance_values_100_039` | 今恋してる？ |
| 40 | `romance_values_100_040` | これだけは譲れない結婚条件 |
| 41 | `romance_values_100_041` | LINEで告白はあり？ |
| 42 | `romance_values_100_042` | 甘えたい？甘えてほしい？ |
| 43 | `romance_values_100_043` | 記念日はお祝いしたい？ |
| 44 | `romance_values_100_044` | 浮気はどこから？ |
| 45 | `romance_values_100_045` | 子供にどんな名前をつけたい？ |
| 46 | `romance_values_100_046` | 恋人にされてうれしかったことは？ |
| 47 | `romance_values_100_047` | 恋人にされて悲しかったことは？ |
| 48 | `romance_values_100_048` | 朝はごはん派？パン派？ |
| 49 | `romance_values_100_049` | 遠距離恋愛できる？ |
| 50 | `romance_values_100_050` | 恋人との理想の休日の過ごし方 |
| 51 | `romance_values_100_051` | 恋人と旅行で行きたい場所は？ |
| 52 | `romance_values_100_052` | マッチングアプリでの出会いはあり？なし？ |
| 53 | `romance_values_100_053` | 一緒にしたい趣味は？ |
| 54 | `romance_values_100_054` | 恋人と接するときに心掛けていること |
| 55 | `romance_values_100_055` | 恋人と喧嘩したことある？ |
| 56 | `romance_values_100_056` | 喧嘩したらどう仲直りする？ |
| 57 | `romance_values_100_057` | 恋人に「好きだよ」ってどのくらいの頻度で伝える？ |
| 58 | `romance_values_100_058` | 結婚前に同棲したい？ |
| 59 | `romance_values_100_059` | 好きな人にどんなアピールをする？ |
| 60 | `romance_values_100_060` | 恋人に嘘ついたことある？ |
| 61 | `romance_values_100_061` | 外見がタイプじゃなくても付き合える？ |
| 62 | `romance_values_100_062` | 浮気は許せる？ |
| 63 | `romance_values_100_063` | 昔の恋人を思い出すことはある？ |
| 64 | `romance_values_100_064` | モテ期はいつ？ |
| 65 | `romance_values_100_065` | 恋人にも言えない秘密ある？ |
| 66 | `romance_values_100_066` | 自分から告白する？ |
| 67 | `romance_values_100_067` | 告白するならどんな言葉で伝える？ |
| 68 | `romance_values_100_068` | 恋人と別れたいと思うのはどんなとき？ |
| 69 | `romance_values_100_069` | 理想の家族像 |
| 70 | `romance_values_100_070` | 駆け引きする？ |
| 71 | `romance_values_100_071` | 昔の恋人に連絡したことある？ |
| 72 | `romance_values_100_072` | 理想は高い？ |
| 73 | `romance_values_100_073` | お金の管理はどうしたい？ |
| 74 | `romance_values_100_074` | 幼少期はモテた？ |
| 75 | `romance_values_100_075` | 好きになった人に好きな人がいたらどうする？ |
| 76 | `romance_values_100_076` | 長く付き合う秘訣は？ |
| 77 | `romance_values_100_077` | 蛙化現象になったことある？ |
| 78 | `romance_values_100_078` | プロポーズされたい？したい？ |
| 79 | `romance_values_100_079` | 理想のプロポーズのシチュエーションは？ |
| 80 | `romance_values_100_080` | 学生時代の恋愛エピソードは？ |
| 81 | `romance_values_100_081` | オタクは好き？ |
| 82 | `romance_values_100_082` | 結婚は何歳までにしたい？ |
| 83 | `romance_values_100_083` | ひとりの時間は必要？ |
| 84 | `romance_values_100_084` | お風呂は一緒に入れる？ |
| 85 | `romance_values_100_085` | 復縁の経験はある？ |
| 86 | `romance_values_100_086` | 結婚を意識する瞬間は？ |
| 87 | `romance_values_100_087` | 寝室はいっしょ？別がいい？ |
| 88 | `romance_values_100_088` | 束縛する？ |
| 89 | `romance_values_100_089` | 束縛されるのは？ |
| 90 | `romance_values_100_090` | デート中にされて萎える行動は？ |
| 91 | `romance_values_100_091` | 恋人がそっけないときどうする？ |
| 92 | `romance_values_100_092` | 恋人に依存する？ |
| 93 | `romance_values_100_093` | 好きバレしたことある？ |
| 94 | `romance_values_100_094` | ペアルックしたい？ |
| 95 | `romance_values_100_095` | 恋人にされたくないことは？ |
| 96 | `romance_values_100_096` | デート先はどうやって決める？ |
| 97 | `romance_values_100_097` | 恋人にイラッとしたときの対処法は？ |
| 98 | `romance_values_100_098` | 割り勘ってどう思う？ |
| 99 | `romance_values_100_099` | 10年後、都会と田舎どっちに住む？ |
| 100 | `romance_values_100_100` | 見てくれた方に一言！ |
