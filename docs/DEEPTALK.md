# Deeptalk 双人开放问答

首页「测试」标签下「深聊问答 · 不计分」提供10张独立卡片，以 `/deeptalk?topic_id=...` 进入同一页面并预选题库；「测试 · 有结果」保持26款。新轮次固定为「边聊边答」（chat），当前绑定小机共用小机侧答卷；网页换题时保存或跳过，每题成功保存后对方即可通过 question/result 查看，显示双方进度。全题处理后可正式提交并锁定自己的整卷。旧 blind「答完再看」轮次仍可续答、从历史或 round_id 直达：一方提交后该侧答卷可见，未提交侧隐藏。无评分、匹配度或心理诊断。

## 内容包集成

本发布树已集成十个正式 JSON，共490题，原九套462题逐字节保留。加载器每次按需读取文件，不依赖编译生成步骤：

| 文件 | 展示名 | 数量与范围 |
| --- | --- | --- |
| `deeptalk/data/relations_communication.json` | 好好说话，也好好相处 | 13：边界、倾听、同意与沟通 |
| `deeptalk/data/couple_50.json` | 恋人50问：聊聊我们 | 50：日常相处、偏好与情感表达 |
| `deeptalk/data/relationship_scenarios.json` | 关系里的两难时刻 | 14：假设关系两难、边界与冲突 |
| `deeptalk/data/romance_values_100.json` | 恋爱观100问：我们想的一样吗？ | 100：吃醋、信任、同居、未来规划等全面恋爱观 |
| `deeptalk/data/work_values_20.json` | 工作里，你最看重什么？ | 20：职业抉择 |
| `deeptalk/data/favorites_100.json` | 你喜欢什么？100问 | 100：日常兴趣 |
| `deeptalk/data/getting_to_know_100.json` | 关于你，我还想知道100件事 | 100：生活背景 |
| `deeptalk/data/life_reflection_35.json` | 深夜谈心：35个人生问题 | 35：价值与人生 |
| `deeptalk/data/real_life_dilemmas_30.json` | 如果有人向你求助：30道难题 | 30：回应虚构烦恼 |
| `deeptalk/data/learning_goals_problem_solving.json` | 学习、目标与未来计划 | 28：目标9、周复盘6、能力举例与反思9、问题分析4 |

来源核验：[关系四套](DEEPTALK_SOURCES.md)、[通用A](DEEPTALK_GENERAL_A_SOURCES.md)、[通用B](DEEPTALK_GENERAL_B_SOURCES.md)、[现实难题](DEEPTALK_REAL_LIFE_SOURCES.md)。新增第十套见[学习、目标与解决问题来源核验](DEEPTALK_LEARNING_SOURCES.md)：28题，CedarToy 中译与编排 v1，原作 CC BY 4.0。

顶层必需 `topic_id,title,description,author,source_url,license,license_url,adaptation_note,questions`；前八项为字符串。`questions` 为 `{id,text,source_ref}` 数组，`id` 为唯一字符串或整数（读取时统一成字符串），`text` 为非空字符串，`source_ref` 保留原结构。可另带复合来源 `sources`。ID、数量必须与上表一致；JSON title 保留原题包名，上表展示名由 `cedar_backend/display_titles.py` 映射，不是原作者命名。作者、许可和出处保留在原包中，通过 `source` 按需查看，不重复注入全局 MCP schema。

缺失或无效的包显示「题库尚未就绪」，不能开局/读取，不会用临时题代替。可用以下命令验证正式十包，不访问数据库：

```sh
python3 -c 'from deeptalk.catalog import topics; t=topics(); print(t); assert all(x["available"] for x in t)'
```

Deeptalk 不接入公开平台统计，不写入有结果测试的 `test_results`，不提供 AIDep 的 `shared_result` 分享操作；D-3 的原始自评分数不进入本题包或结果。

创建时把完整包快照保存入轮次。以后修改、移除题包都不会改写已有轮次的题目、答案和来源。新旧轮次的界面标题按 topic_id 映射展示名，未知主题回退保存的标题；不回写快照或历史标题。

## 身份与持久化

使用服务实际 `SESSIONS_DB`（默认 `data/sessions.db`）中的 `deeptalk_rounds`、`deeptalk_requests`，首次使用幂等建表。账号及绑定仍来自实际 `TURTLE_SOUP_DB`，不复制账号库。测试全用临时账号库和会话库。

答案及回答去重摘要采用服务端 AES-256-GCM 字段加密；上线必须配置独立 `DEEPTALK_SAVE_KEY_FILE` 并显式迁移旧明文。密钥、迁移顺序、WAL/备份残留与故障恢复见 [存档加密手册](DEEPTALK_ENCRYPTION.md)。

新轮次属于创建它的人类账号，`sharing_mode=bound_shared`：当前绑定小机共用一份草稿/revision，任一可代表小机侧提交，解绑即失权。旧 `single_ai` 轮次仍限原参与双方，不因新绑定转交。服务端从 Token 推导身份；自报 `player_id`、角色或对方 ID 不能获得读写权。创建仅人类网页可操作，须已有绑定；不再选择某个小机，旧 `ai_id` 参数拒绝并提示刷新。

所有读改写使用同一 SQLite 事务，写入用 `BEGIN IMMEDIATE`。`Store.serialize` 按轮次模式控制详细读取：chat 逐题已保存/跳过内容可读；blind 只在对方整卷提交后读取该侧。`peer_visible` 表示对方可读，`unlocked` 仅双方均提交时为 true。正式提交前须逐题回答/跳过，提交后不可修改。开局用人类账号与 `request_id` 唯一约束；回答用轮次、参与方、`request_id` 去重，并验证内容指纹和本方 `revision`。提交重试幂等。不同轮次永不覆盖；无游客 TTL、自动过期或存档槽。

首页 `/api/auth/history` 只把请求者实际参与的轮次摘要放入 `self.deeptalk_history`，不借小机绑定关系读取其他人参与的历史。摘要只有轮次 ID、问卷名、双方名称、状态、时间和同页链接，不带回答、题目或进度明细；分页每页20轮。点击 `/deeptalk?round_id=...` 在同一答题页恢复/查看，详情再次校验身份。网页优先恢复本标签页 sessionStorage 中未保存的本地草稿，否则恢复已成功保存的回答；本地恢复须先通过服务端轮次授权，轮询不公开本地草稿。未保存输入有离页提醒，失败保留输入供重试。响应设 `Cache-Control: no-store`。

## 题库续答与单轮删除

`?topic_id=...` 先调用网页专用 `resume`，只在当前人类本人、同题库、人类尚未提交的轮次中选择，覆盖全部历史而非仅第一页；小机已提交不妨碍续答。有真实文字回答优先于仅跳过或空轮次，同类取最近活跃；本标签页的未保存草稿可提供轮次提示，但服务端仍校验所属人类、题库和未提交状态。恢复定位本地草稿题或第一道未处理题，不隐式创建轮次。旧 `?round_id=...` 仍直接打开指定轮次，已提交者只读。

全部历史（包括最近一轮和未完成轮次）放在默认折叠的「历史轮次」里。答题页复用原有底部链接：未提交显示「题库 / 历史」，已提交显示「重新答题」；链接带 `?topic_id=...&new=1` 绕过自动恢复并进入新建页，只有点击「开始新一轮」才创建。自动恢复暂时失败时保留历史和新建入口。

历史每轮的「删除」先读取网页专用 `delete_preview`，在对话框明确说明永久删除整轮及双方答案，再以 `delete_version,confirm=true` 请求 `delete`。仅该轮所属人类账号有权执行，AI/MCP 不提供删除。写事务内校验双方版本、提交状态及更新时间，变化返回409，须重新查看并确认；轮次与 `deeptalk_requests` 同事务删除。删除后的迟到回答或提交返回404，不会复活旧轮次。正在使用的网页收到404后明确提示并保留当前输入供复制，停止继续保存。无自动删除、过期清理或生产数据迁移。

续答/删除仅增加网页接口，根 MCP schema 不变；十个子 Guide 与兼容 Guide 按需说明 chat 新轮次和 blind 旧轮次规则，不添加 MCP create 或删除操作。

## 接口

网页 API 使用 `Authorization: Bearer <现有人类Token>`，不读环境 Cookie，不把 Token 放入链接。

- `GET /deeptalk[?round_id=...]`：同一页面。
- `GET /api/deeptalk?action=context`：十主题就绪状态、当前人类名称、自己的绑定小机。
- `GET /api/deeptalk?action=view&round_id=...`：完整本方问卷；对方回答按 chat/blind 可见性返回。
- `GET /api/deeptalk?action=history&offset=0&limit=20`：本人轮次摘要。
- `GET /api/deeptalk?action=source&topic_id=...`：题库元信息；也可传 `round_id` 看本人轮次快照来源。
- `GET /api/deeptalk?action=resume&topic_id=...`：自动续答候选，可带 `draft_round_id`；无候选返回 null。
- `GET /api/deeptalk?action=delete_preview&round_id=...`：本人轮次删除摘要与 `delete_version`，不含答案。
- `POST /api/deeptalk/action`：JSON `action=create|answer|submit|delete` 及下表字段。

小机通过根 MCP 的「深聊问答」分类发现10个独立ID，`get_guide(game)`只返回对应主题说明与来源。`play(game="dt_work20", action="questions", params={"number":1})` 自动选择工作价值观题包；路径 Token 和 Bearer 均可，必须是小机账号。全部别名先映射回原 `deeptalk` 引擎，沿用鉴权、绑定、按模式可见性和存档；轮次操作仍以实际 `round_id` 为准。显式传入冲突 `topic_id` 会拒绝。根 schema 没有新增字段。

| 发现ID | 原topic_id |
| --- | --- |
| `dt_relations` | `relations_communication` |
| `dt_couple50` | `couple_50` |
| `dt_scenarios` | `relationship_scenarios` |
| `dt_romance100` | `romance_values_100` |
| `dt_work20` | `work_values_20` |
| `dt_favorites100` | `favorites_100` |
| `dt_know100` | `getting_to_know_100` |
| `dt_life35` | `life_reflection_35` |
| `dt_dilemmas30` | `real_life_dilemmas_30` |
| `dt_learning_goals` | `learning_goals_problem_solving` |

旧 `get_guide("deeptalk")` 保留简短兼容说明与索引，旧 `play(game="deeptalk",...)` 仍可用；旧入口的 `questions/source` 需显式 `topic_id`。作者、原网址和许可只在单套 Guide / 按需 source 展示，不在发现列表重复。

| 操作 | 字段/行为 |
| --- | --- |
| `topics` | 返回十套名称、区别、题量与就绪状态，不带题目 |
| `questions` | 别名自动选主题，旧入口传 `topic_id`；`number=1` 起始，`limit=1` 默认，最多100。每题仅 `number,id,text`，逐题 `source_ref` 留在原JSON追溯 |
| `source` | `topic_id` 或有权访问的 `round_id`，按需查来源 |
| `create`（仅网页） | `topic_id,request_id`，`mode` 省略或为 `chat`；显式 `blind` 返回409提示刷新，其余畸形值返回400；独立新轮次，不覆盖 |
| `history` | `offset=0,limit=20`（最多100），不含题目/回答 |
| `status` | `round_id`；进度、自己的 `revision,next_number`，无题目/回答 |
| `question` | `round_id,number=1`；一题和本方回答，按模式附获准查看的对方回答 |
| `answer` | `round_id,question_id,revision,request_id,text`；或 `skip=true`。text 1–8000字，request_id 8–100位字母数字/下划线/短横线；修改同题仍用本方最新 revision、新 request_id |
| `submit` | `round_id,revision,confirm=true`；正式提交整卷 |
| `result` | `round_id,number=1,limit=1`（最多20）；按模式返回获准查看的回答 |

`delete` 仅网页：`round_id,delete_version,confirm=true`，核验预览版本后同事务删除整轮和幂等记录。`resume/delete_preview/view` 仅供人类网页，MCP 不支持。重试回答必须复用同一 request_id 和内容；409表示请求键冲突、版本冲突或删除确认已过期，检查状态后再行动。HTTP 401未登录、403身份/绑定不符、404轮次不存在或无权访问、400参数不符、503题包/存储暂不可用。

## 验证

```sh
python3 -m unittest tests_toy.test_deeptalk tests_toy.test_deeptalk_packages tests_toy.test_deeptalk_quick_answers
python3 scripts/persistence_check.py --deeptalk
node scripts/check_deeptalk_ui.js
python3 -m unittest tests_toy.test_deeptalk_resume_delete
node scripts/check_deeptalk_resume_delete.js
node scripts/check_deeptalk_home_groups.js
node scripts/check_deeptalk_uuid.js
node scripts/check_deeptalk_layout.js
```

DOM 检查依赖项目 `jsdom`，通过 stdio 调用真实 Python HTTP/MCP 处理器与临时 SQLite，不绑定端口、不接触生产。`scripts/deeptalk_test_server.py --http` 可在允许启动本地服务的环境开启同样的一次性夹具；标准输出为本地地址，`/__test__/tokens` 仅为一次性测试账号。停止该脚本后清理数据库。正式题库验证需另外执行上面的十包校验。

2026-10-11 发布树集成的完整验收、Git权限限制、浏览器限制与后续发布建议见 [发布验收记录](DEEPTALK_RELEASE_VALIDATION.md)。此记录不代表已部署。
