# 2026-10-11 第十套 Deeptalk 隔离验收

当前交付工作树：`/opt/cedar-workspaces/cedartoy-deeptalk-tenth-20261011`，开始时干净。仅开发与隔离验证；未 commit/push/deploy/restart，未操作生产、用户数据或其他工作树。下方前序验收数字仅为历史证据，当前为 **10套490题**。

## 修改文件（14个）

- 运行时：`deeptalk/data/learning_goals_problem_solving.json`（新增）、`deeptalk/catalog.py`、`index.html`、`cedar_backend/question_volumes.json`。
- 回归：`tests_toy/test_deeptalk.py`、`tests_toy/test_deeptalk_packages.py`、`tests_toy/test_question_volumes.py`、`scripts/check_deeptalk_home_groups.js`、`scripts/check_deeptalk_ui.js`。
- 文档：`docs/DEEPTALK.md`、`docs/DEEPTALK_LEARNING_SOURCES.md`（新增）、`docs/DEEPTALK_SOURCES.md`、`docs/QUESTION_TEXT_VOLUMES.md`、`docs/DEEPTALK_RELEASE_VALIDATION.md`。

## 当前内容与集成

新增 `learning_goals_problem_solving` / `dt_learning_goals`「学习、目标与解决问题」28题，四组9+6+9+4。原始页面成功访问，署名 Alli M. Tucker-Stanojković (North Orange Continuing Education)、CC BY 4.0、题表与反思问题均已核实；[逐条来源与翻译边界](DEEPTALK_LEARNING_SOURCES.md)。CedarToy 非官方中译与编排 v1，不采用 D-3 评分，不生成报告或匹配度。没有题文缺口或许可归属争议；不声称作者审核中文译文。

新增包 SHA-256：`840af1da25876d4a922e3ed512e78bc3adf88323b8ab3321b917cc231572041b`。旧9套均与下方原始SHA-256逐字节一致，已有轮次快照与存储实现未改。

首页仍为两个标签；「测试」下10张「深聊问答 · 不计分」卡片，26款「测试 · 有结果」不变。新卡片进入 `/deeptalk?topic_id=learning_goals_problem_solving`，原页面动态下拉显示10套。无新CSS或新答题页；`deeptalk.html`、`test_game.html`、人类量表、统计服务和 AIDep 分享实现均未改。

## 本次有限验收

- Python 主回归27项：`test_deeptalk`、`test_deeptalk_packages`、`test_question_volumes`、`test_mcp_schema_selection`、`test_mcp_dispatch_order`。正式10套均走 create/questions/status/answer/submit/result/history，路径Token与Bearer，双方提交前草稿保密、无关身份读写/历史隔离、两种提交顺序、重答与旧轮次保护。新增包分组/署名/无评分字段和旧9包哈希有专门断言。
- Python 边界26项：`test_aidep_sharing`、`test_platform_stats`、`test_homepage_stats`，以及 `test_root_mcp_protocol.RootMcpProtocolTests.test_play_schemas_keep_gemini_numeric_enum_compatibility`。以 `PYTHONPATH=turtle-soup/backend:. python3 -m unittest ...` 运行。覆盖 AIDep 分享授权/撤销/身份边界、统计18量表与排除所有 `dt_*`、公开统计不带私密题答、Gemini schema；Deeptalk 全链路另断言没有写出 `test_results` 表，并拒绝 `shared_result`。
- `python3 scripts/persistence_check.py --deeptalk`：10套、四个独立进程的 create/drafts/sealed/complete 通过，临时数据库自动删除。
- `NODE_PATH=/opt/cedartoy/node_modules node scripts/check_deeptalk_home_groups.js`：360/430/1280px配置下10张卡片、原26款有结果测试、双标签/分组/搜索/小游戏、新卡片选中URL、10套预选通过。
- 同样 NODE_PATH 执行 `scripts/check_deeptalk_ui.js`：默认新增28题；另 `DEEPTALK_TOPIC=relations_communication` 重跑旧13题。真实HTTP/MCP处理器+临时SQLite+jsdom，创建/草稿/跳题/编辑/重试/刷新/确认/单方封存/双提交解锁/历史/XSS/跨账号拒绝全部通过。修复旧脚本仍断言单一Deeptalk卡片的过期预期。
- `scripts/check_platform_stats_fold.js`：统计DOM折叠、顺序、数字、条宽及隐私提示不变。
- `python3 scripts/update_question_volumes.py --write` 后 `--check`：35份Guide元数据通过。新题文438字符/503 Token（cl100k_base，逐题计量）；按需Guide正文910字符/425 Token。原34项元数据不变，静态旧Guide无diff。
- 根schema实现无diff；list从1534字符/1213 Token变为1578字符/1245 Token，仅新增一条发现索引（+44字符/+32 Token）。原9份按需Guide正文不变；题目与教师正文不进入常驻schema。最终 `git diff --check` 通过。

## 视觉验收限制

已核对现有相邻深聊卡片与有结果测试共用样式：桌面标题19px/1.2、辅助12px/1.5、4px边框/阴影、14×16px内边距；手机标题16px、辅助10px、14px内边距、最小卡片高度112px，沿用原抽屉。答题页沿用16px/1.65系统字体、13px辅助文字、44px按钮，无CSS修改。

尝试现有 Playwright/Chromium headless shell，启动报 `sandbox_host_linux.cc:41 ... shutdown: Operation not permitted`。因此上述宽度为DOM/交互配置检查，**真实360/430/1280px渲染、长标题换行/裁切和视觉层级尚待发布环境补验**，不能视为视觉通过。UI技能的设计搜索脚本未随本地技能安装，采用现有页面样式与组件作依据。

交付给主助手审核发布；本次没有运行全站测试、启动业务服务或访问生产数据库。仅可合并本工作树diff（包括新增JSON/来源文档）；不覆盖正在另树修改的人类量表网页。

---

# 前序集成验收（历史记录）

以下前序记录仅适用于 `/opt/cedar-workspaces/cedartoy-deeptalk-release-20261011`。未部署、未 push、未操作生产服务或配置，未修改 `/opt/cedartoy` 工作文件。

## 后续更新：三分类与题文体积

当前list严格为深聊问答、测试、游戏三类。9套深聊、17套开放量表及可可靠计量的旧测试Guide已添加离线cl100k_base题文体积；当前数据、完整展示、前后开销和复核命令见 [题文计量记录](QUESTION_TEXT_VOLUMES.md)。下面的旧分类/体积与108项、20项测试为此前阶段证据；本次另有27项直接相关检查通过，未部署/push/restart。本次只保留本发布树修改，此前 `/tmp` 导出的补丁不包含这次变更，不应作为当前完整交付。

## 同日局部修正：九主题独立发现

此节更新此前集成结果；下文108项是上一阶段记录，本次只跑直接相关检查。九个 `dt_*` 独立发现ID及单套Guide已接入，旧deeptalk条目从list移除，旧Guide/play兼容保留。映射和调用见 [接口说明](DEEPTALK.md)。人类网页、历史、存储、题包JSON及18套量表未改。

`python3 -m unittest tests_toy.test_deeptalk tests_toy.test_deeptalk_packages tests_toy.test_mcp_schema_selection tests_toy.test_mcp_dispatch_order`：20项通过，覆盖9别名发现/单套Guide/路径Token与Bearer/批量读题/source/主题冲突拒绝/权限/双提交，兼容旧入口；另通过语法和diff检查。本次没有全量测试，也没有部署、push或生产操作，未新增commit。

根play标准/legacy schema仍为541/11221字符。list由1244到1539字符，新增295字符用于用户要求的九套独立发现；旧兼容Guide由1142降到537字符。`questions` 每题仅返回number/id/text，source保留完整包元信息，原JSON逐题source_ref不变。按实际JSON响应字符串计，恋爱观100问18861→7369字符（减少11492），喜欢的事物100问14354→6562（减少7792），相互了解100问10023→7231（减少2792）。这些是字符数，不是token数。

此前 `/tmp/cedartoy-deeptalk-release-20261011.patch` 已重新导出，包含这次修正；它是相对becb656的完整集成交付，不应直接应用到含其他任务修改的生产树。

## 集成与交付状态

起始及最终 Git HEAD 均为 `becb656`。初始发布树干净。原计划顺序 cherry-pick：

- `179b67784719ed28282c2c5a915a4f8c3ef173d9 feat(tests): integrate 17 reviewed research scales with human and MCP modes`
- `156698e404009cf6408da3a4a12b5faece6cc823 feat(tests): add Study 4 inspired pseudo-profound speech test`
- `785914c27206fbc1c5b9a524c441a54bdaa8114e feat: add private two-party Deeptalk questionnaires`
- `535b0ade0c82740ec8c9c83ba3ad900431d9ece3 Fold platform statistics distributions by default`

实际 cherry-pick 在写 sequencer 前被只读文件系统阻止：本工作树 `.git` 指向 `/opt/cedartoy/.git/worktrees/cedartoy-deeptalk-release-20261011`，该目录不可写。因此按上述顺序对各提交做 `git diff <commit>^ <commit> --binary`，逐个 `git apply --check` 后应用，四份补丁均无冲突。未修改 Git 元数据、未 reset、未创建提交。**当前交付是本发布树的已跟踪修改和未跟踪新增文件，不能只取 HEAD 当成成果。**

保留 `becb656` 的 Kelivo 默认标准 schema、显式 legacy 退路及此前蜡烛封面图片；对应生产已提交文件与 HEAD 无差异。未复制其他题包树代码，仅复制9个 JSON和4份来源文档。新增的集成修正：

- catalog/按需 Guide 从4主题扩为9主题；原题包逐字节不变。
- 修复量表接入后的 `get_guide(game=[])` TypeError，恢复原有 `-32602` 参数错误。
- 正式题包 HTTP/MCP 双提交、权限、轮次隔离测试；跨进程和网页 DOM 夹具使用正式题包。
- 旧根协议测试按当前已提交的默认 standard 行为更新，保留 Gemini 数值 enum 检查；旧网页抽取测试按新量表共享数据库入口隔离测试存储。
- 统计折叠原静态断言无需删除；另验证真实 DOM 开合、排序、数值和条形宽度。

## 正式 Deeptalk 包

以下 SHA-256 与指定来源工作树逐字节一致；9套共462题。来源记录见 [Deeptalk 接入说明](DEEPTALK.md)。

| ID | 题数 | 主题 | SHA-256 |
| --- | ---: | --- | --- |
| `relations_communication` | 13 | 关系与沟通：边界、倾听、同意与沟通 | `f5c2335732144528fe1346a3d1601eb269b90a31b13086179dc6eae6782a357e` |
| `couple_50` | 50 | 恋人之间的50问：恋人日常相处、偏好与情感表达 | `21836375dd10f08dc565d11594a9f758669ac2ffa1a88895108ad9f340396c62` |
| `relationship_scenarios` | 14 | 亲密关系情境：假设关系两难的处置、边界与冲突 | `44fcfe03ee0be1ce3fe13a622f426b3d7beafb667724ab4aff58fc9a32aa8739` |
| `romance_values_100` | 100 | 恋爱观100问：全面恋爱观：吃醋、信任、同居与未来规划 | `94ed8b2fbdcfe55dc7a19d84077c310d9b8865d719aba562b3deb67cb3bf4def` |
| `work_values_20` | 20 | 工作价值观20问：职业抉择与工作价值取舍 | `6bf3d0bf8f560aff5f49b509296aaa39157ddf3f95452a030ef4b96b775e192b` |
| `favorites_100` | 100 | 喜欢的事物100问：日常兴趣与喜好 | `1e47c94a40f06a6d039e1416729e8a3a3b4940c5c223f004495a9c32d3b98443` |
| `getting_to_know_100` | 100 | 相互了解100问：生活背景与相互了解 | `dfea6cd24ea154dab2ebf5c1e1554f94aba4c139a176cbd71ccda67a92db6320` |
| `life_reflection_35` | 35 | 人生与内心35问：价值观、人生经历与内心反思 | `41ba568b04b8cb36bde874842797234dbf509dacf0c2839aef614c5ff10232bd` |
| `real_life_dilemmas_30` | 30 | 现实难题30问：如何回应虚构烦恼与现实两难 | `018cb8ffe3c15d78466a20807e1cfbb25276df87fa8d3c82397c435a7dcdd061` |

前序阶段当时未添加「学习、目标与解决问题」；当前已补齐，见本文顶部验收。Guide 无 OC 名称；默认一次读1题、status无题目/答案、history仅摘要；完整来源按需读取。网页完整轮次仍为 `/deeptalk?round_id=...`。边聊边答没有房间、评分或聊天存档。

## 研究量表

18套共265题；AIDep仅人类，MCP共17套243题。75题恋爱动机未纳入。研究量表沿用既有每身份/量表当前结果机制；Deeptalk则每次新轮次永久独立保存，两者不混淆。

| ID | 题数 |
| --- | ---: |
| `aidep` | 22 |
| `bsfl` | 5 |
| `bsr` | 20 |
| `evs` | 16 |
| `ipip_adhd` | 12 |
| `ipip_empathy` | 10 |
| `ipip_need_for_cognition` | 10 |
| `ipip_self_monitoring` | 10 |
| `ipip_sociability` | 10 |
| `irrc` | 35 |
| `jrrs` | 16 |
| `mesx` | 10 |
| `ppts` | 18 |
| `rbi` | 18 |
| `rnss` | 20 |
| `romlon` | 4 |
| `ross` | 17 |
| `rss` | 12 |

## 验证结果

- 针对性 Python 组合回归：**108项通过**。包括 Deeptalk 原安全测试、真实9套起始/恢复/提交/分页查询、双方先后提交顺序、跨账号读写拒绝、重复答不覆盖、研究量表、网页、统计、核心MCP/schema/Gemini、开户/登录安全。没有跑全量测试。
- `python3 scripts/persistence_check.py --deeptalk`：9套正式题包，4个独立进程完成开局/草稿/单方封存/双提交解锁，重答不覆盖。
- `python3 scripts/persistence_check.py --research-scales`：18套两种模式各走开局/修改/恢复的3进程链路。均为临时SQLite，本次产物自动清理。
- `NODE_PATH=/opt/cedartoy/node_modules node scripts/check_deeptalk_ui.js`：正式题包的网页创建、读题、回答、跳题、编辑、响应丢失重试、刷新恢复、确认提交、保密/解锁、XSS、首页摘要历史、跨账号拒绝通过。调用真实HTTP/MCP处理器和临时SQLite，无监听端口。
- 同样 NODE_PATH 执行 `scripts/check_research_scale_web.js`：18页/265题、两模式、BSR乱序评分/恢复、草稿与历史、零分、AIDep AI拒绝通过。
- 同样 NODE_PATH 执行 `scripts/check_platform_stats_fold.js`：每个原生details默认收起/独立开合、仅标题切换、标题44px、排序/数值/条宽、空态、汇总/隐私说明位于折叠外通过。
- 所有本次Python文件语法、三个HTML内联JS语法、`git diff --check`通过。题包字节校验通过；无 vendor 指针改动。

组合回归复现（项目依赖已安装；孤立工作树用既有bootstrap只补缺失的无关eco/词语屋导入，不模拟本次业务）：

```sh
python3 - <<'PYTEST'
import sys, unittest
from pathlib import Path
sys.path.insert(0, str(Path('turtle-soup/backend').resolve()))
from tests_toy.tarot_server_test_bootstrap import install_missing_optional_game_stubs
install_missing_optional_game_stubs()
names = ['test_deeptalk', 'test_deeptalk_packages', 'test_research_scales',
         'test_human_test_web', 'test_homepage_stats', 'test_platform_stats',
         'test_stats_extraction', 'test_human_tests_extraction',
         'test_mcp_schema_selection', 'test_mcp_module_compat',
         'test_mcp_dispatch_order', 'test_root_mcp_protocol', 'test_account_security_round1']
excluded = {'test_removed_export_import_support_and_usage_remain_in_guides',
            'test_fishing_tool_play_uses_params_command_and_rejects_missing_command'}
def flatten(suite):
    for test in suite:
        if isinstance(test, unittest.TestSuite): yield from flatten(test)
        else: yield test
suite = unittest.defaultTestLoader.loadTestsFromNames(['tests_toy.' + n for n in names])
suite = unittest.TestSuite(t for t in flatten(suite) if t._testMethodName not in excluded)
result = unittest.TextTestRunner().run(suite)
sys.exit(not result.wasSuccessful())
PYTEST
```

### 既有失败与验证边界

首次组合回归及在 `/tmp/cedar-release-becb656` 的只读Git归档基线复核，均发现旧根协议测试仍假定Kelivo自动legacy，以及旧Guide必须逐字包含“覆盖”。默认schema相关断言已更新为当前标准行为。最终组合明确排除两项：

1. `test_removed_export_import_support_and_usage_remain_in_guides`：基线同样有13个“覆盖”字面断言失败；业务Guide已保留 `confirm=true`，本次未改其他游戏Guide。
2. `test_fishing_tool_play_uses_params_command_and_rejects_missing_command`：基线同样缺少独立fishing vendor，不能运行其引擎；未下载/更新vendor。不是所有其他游戏运行时验收。

`test_platform_stats` 需要将 `turtle-soup/backend` 放入导入路径以读取现有utils，按上述复现运行后通过。调试中一次全局禁用日志导致4个assertLogs失败，已恢复日志并完整重跑通过。

**浏览器限制**：尝试使用现有Playwright/Chromium启动真实浏览器，因 `sandbox_host_linux.cc:41 ... Operation not permitted` 退出。因此360px、430px、桌面真实布局、原生键盘开合和视觉层级仍需可启动浏览器的环境补验；DOM/CSS检查不替代视觉通过。本次没有启动业务HTTP服务。

### 上下文成本

同一方法 `json.dumps(inputSchema, ensure_ascii=False, separators=(',', ':'))`：根play标准schema为541字符、legacy为11221字符，前后完全相同。list_games由732到1244字符，新增512字符用于17款小机量表和Deeptalk的发现索引。Deeptalk按需Guide为1142字符；462题不进入常驻schema或默认状态。以上为字符数，不是token数。

## 生产风险与后续发布/回滚建议（未执行）

只读检查确认生产主树HEAD仍为becb656，且有多个并行任务未提交：server.py、index.html、README、路由/MCP/存档模块、游戏派发/Guide/统计、vendor adapter及candle新增文件等；与本次多处重叠，还有vendor指针脏状态和运行文件。**不能直接覆盖、reset、checkout这些生产文件，不能将本发布树整体复制进生产。**

1. 由主助手与各任务负责人在另一个可写Git元数据的干净树整合所有待发布成果，提交本交付全部文件（含未跟踪新增），复核真实diff，保留最新已提交功能与其他任务改动。当前本树没有新的commit可供直接cherry-pick。
2. 正式发布前补齐手机/桌面浏览器检查，并在具备相关vendor的隔离环境检查需要的既有游戏；确认实际服务 `SESSIONS_DB` / `TURTLE_SOUP_DB`，按[备份手册](BACKUP.md)做SQLite一致性快照并验证。服务操作只在另行授权的发布步骤执行。
3. 保存实际部署前的完整代码版本（不能只把becb656当成当前生产运行文件）与一致性快照。代码回滚到该版本，保留新增Deeptalk表、研究量表记录和永久轮次；旧代码可忽略新增数据。若确需数据库恢复，先保护发布后新增记录，再协调恢复，不能直接用旧库覆盖新答卷。

本次没有部署、push、restart/kill生产或独立游戏服务，也没有修改服务配置。
