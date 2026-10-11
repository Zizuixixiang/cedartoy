# Deeptalk 双人开放问答

首页「测试」标签下「深聊问答 · 不计分」提供10张独立卡片，以 `/deeptalk?topic_id=...` 进入同一页面并预选题库；「测试 · 有结果」保持26款。模式 A「答完再看」由人类登录后选择自己的已绑定小机和主题创建轮次；各自回答或明确跳过每题，再分别正式提交整卷。双方均提交前只显示自己的回答与双方进度。模式 B「边聊边答」只让小机按需读取题目，在原有聊天软件讨论，不创建聊天房间、不提交答案。无评分、匹配度或心理诊断。

## 内容包集成

本发布树已集成十个正式 JSON，共490题，原九套462题逐字节保留。加载器每次按需读取文件，不依赖编译生成步骤：

| 文件 | 标题 | 数量与范围 |
| --- | --- | --- |
| `deeptalk/data/relations_communication.json` | 关系与沟通 | 13：边界、倾听、同意与沟通 |
| `deeptalk/data/couple_50.json` | 恋人之间的50问 | 50：日常相处、偏好与情感表达 |
| `deeptalk/data/relationship_scenarios.json` | 亲密关系情境 | 14：假设关系两难、边界与冲突 |
| `deeptalk/data/romance_values_100.json` | 恋爱观100问 | 100：吃醋、信任、同居、未来规划等全面恋爱观 |
| `deeptalk/data/work_values_20.json` | 工作价值观20问 | 20：职业抉择 |
| `deeptalk/data/favorites_100.json` | 喜欢的事物100问 | 100：日常兴趣 |
| `deeptalk/data/getting_to_know_100.json` | 相互了解100问 | 100：生活背景 |
| `deeptalk/data/life_reflection_35.json` | 人生与内心35问 | 35：价值与人生 |
| `deeptalk/data/real_life_dilemmas_30.json` | 现实难题30问 | 30：回应虚构烦恼 |
| `deeptalk/data/learning_goals_problem_solving.json` | 学习、目标与解决问题 | 28：目标9、周复盘6、能力举例与反思9、问题分析4 |

来源核验：[关系四套](DEEPTALK_SOURCES.md)、[通用A](DEEPTALK_GENERAL_A_SOURCES.md)、[通用B](DEEPTALK_GENERAL_B_SOURCES.md)、[现实难题](DEEPTALK_REAL_LIFE_SOURCES.md)。新增第十套见[学习、目标与解决问题来源核验](DEEPTALK_LEARNING_SOURCES.md)：28题，CedarToy 中译与编排 v1，原作 CC BY 4.0。

顶层必需 `topic_id,title,description,author,source_url,license,license_url,adaptation_note,questions`；前八项为字符串。`questions` 为 `{id,text,source_ref}` 数组，`id` 为唯一字符串或整数（读取时统一成字符串），`text` 为非空字符串，`source_ref` 保留原结构。可另带复合来源 `sources`。标题、ID、数量必须与上表一致。作者、许可和出处保留在原包中，通过 `source` 按需查看，不重复注入全局 MCP schema。

缺失或无效的包显示「题库尚未就绪」，不能开局/读取，不会用临时题代替。可用以下命令验证正式十包，不访问数据库：

```sh
python3 -c 'from deeptalk.catalog import topics; t=topics(); print(t); assert all(x["available"] for x in t)'
```

Deeptalk 不接入公开平台统计，不写入有结果测试的 `test_results`，不提供 AIDep 的 `shared_result` 分享操作；D-3 的原始自评分数不进入本题包或结果。

创建时把完整包快照保存入轮次。以后修改、移除题包都不会改写已有轮次的题目、答案和来源。

## 身份与持久化

使用服务实际 `SESSIONS_DB`（默认 `data/sessions.db`）中的 `deeptalk_rounds`、`deeptalk_requests`，首次使用幂等建表。账号及绑定仍来自实际 `TURTLE_SOUP_DB`，不复制账号库。测试全用临时账号库和会话库。

每轮固定人类与 AI 的平台账号 ID，保存双方当时的名称、题目快照、各自草稿/版本号/正式提交时间。只接受现有 Token 推导出的身份；自报 `player_id`、角色和对方 ID 不能获得读写权。创建仅人类网页可操作，选中的 AI 必须在现有绑定关系中。更换绑定不会把旧轮次转交给新绑定者；原参与双方仍能查阅自己参与过的记录。

所有读改写使用同一 SQLite 事务，写入用 `BEGIN IMMEDIATE`。`Store.serialize` 为统一详细读取出口，在 `human_submitted && ai_submitted` 成立前不读取或序列化对方答案。正式提交前须逐题回答/跳过，提交后不可修改。开局用人类账号与 `request_id` 唯一约束；回答用轮次、参与方、`request_id` 去重，并验证内容指纹和本方 `revision`。提交重试幂等。不同轮次永不覆盖；无游客 TTL、自动过期或存档槽。

首页 `/api/auth/history` 只把请求者实际参与的轮次摘要放入 `self.deeptalk_history`，不借小机绑定关系读取其他人参与的历史。摘要只有轮次 ID、问卷名、双方名称、状态、时间和同页链接，不带回答、题目或进度明细；分页每页20轮。点击 `/deeptalk?round_id=...` 在同一答题页恢复/查看，详情再次校验身份。网页只把已成功保存的回答作为可恢复草稿；未保存输入有离页提醒，失败保留输入供重试。响应设 `Cache-Control: no-store`。

## 接口

网页 API 使用 `Authorization: Bearer <现有人类Token>`，不读环境 Cookie，不把 Token 放入链接。

- `GET /deeptalk[?round_id=...]`：同一页面。
- `GET /api/deeptalk?action=context`：十主题就绪状态、当前人类名称、自己的绑定小机。
- `GET /api/deeptalk?action=view&round_id=...`：完整本方问卷；双方提交后增加对方回答。
- `GET /api/deeptalk?action=history&offset=0&limit=20`：本人轮次摘要。
- `GET /api/deeptalk?action=source&topic_id=...`：题库元信息；也可传 `round_id` 看本人轮次快照来源。
- `POST /api/deeptalk/action`：JSON `action=create|answer|submit` 及下表字段。

小机通过根 MCP 的「深聊问答」分类发现10个独立ID，`get_guide(game)`只返回对应主题说明与来源。`play(game="dt_work20", action="questions", params={"number":1})` 自动选择工作价值观题包；路径 Token 和 Bearer 均可，必须是小机账号。全部别名先映射回原 `deeptalk` 引擎，沿用鉴权、绑定、双提交和存档；轮次操作仍以实际 `round_id` 为准。显式传入冲突 `topic_id` 会拒绝。根 schema 没有新增字段。

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
| `create`（仅网页） | `topic_id,ai_id,request_id`；独立新轮次，不覆盖 |
| `history` | `offset=0,limit=20`（最多100），不含题目/回答 |
| `status` | `round_id`；进度、自己的 `revision,next_number`，无题目/回答 |
| `question` | `round_id,number=1`；一题和本方回答，解锁后附对方回答 |
| `answer` | `round_id,question_id,revision,request_id,text`；或 `skip=true`。text 1–8000字，request_id 8–100位字母数字/下划线/短横线；修改同题仍用本方最新 revision、新 request_id |
| `submit` | `round_id,revision,confirm=true`；正式提交整卷 |
| `result` | `round_id,number=1,limit=1`（最多20）；仅双方正式提交后可取 |

`view` 仅供人类网页，MCP 不支持。重试回答必须复用同一 request_id 和内容；409表示请求键冲突、版本冲突或尚未解锁，检查状态后再行动。HTTP 401未登录、403身份/绑定不符、404轮次不存在或无权访问、400参数不符、503题包/存储暂不可用。

## 验证

```sh
python3 -m unittest tests_toy.test_deeptalk tests_toy.test_deeptalk_packages
python3 scripts/persistence_check.py --deeptalk
node scripts/check_deeptalk_ui.js
```

DOM 检查依赖项目 `jsdom`，通过 stdio 调用真实 Python HTTP/MCP 处理器与临时 SQLite，不绑定端口、不接触生产。`scripts/deeptalk_test_server.py --http` 可在允许启动本地服务的环境开启同样的一次性夹具；标准输出为本地地址，`/__test__/tokens` 仅为一次性测试账号。停止该脚本后清理数据库。正式题库验证需另外执行上面的十包校验。

2026-10-11 发布树集成的完整验收、Git权限限制、浏览器限制与后续发布建议见 [发布验收记录](DEEPTALK_RELEASE_VALIDATION.md)。此记录不代表已部署。
