# 海龟汤 / NPC / 塔罗模型回退与沉默切换

本文是供主助手复核后执行的上线步骤；代码修改不等于服务已加载，也不等于配置已切换。

## 回退语义

`judge.py` 在记录成功前检查明确的上游拒绝，以及普通提问、猜汤底、提示各自原有的格式校验。判题、评分和线索规则不变。无效响应在本次请求内跳过该物理节点（端点 + 凭据 + 模型），按 priority 从小到大继续尝试；同层保留轮转，不把拒绝文本追加进下一渠道的提示词。

每个业务请求最多尝试 8 个不同节点，各次调用串行。保留凭据锁、NPC / 塔罗信号量和排队上限；普通节点有该业务 timeout 的调用总时限，官方 DeepSeek 保留原 30 秒节点总时限。等待既有凭据锁的时间不算上游调用时限；这不是整个 HTTP 请求的统一 deadline。

内容拒绝 / 业务格式错误仅作请求内排除，不增减节点连续失败、冷却阶梯或上次成功时间；半开探测标记会释放。网络、HTTP、截断和协议损坏等故障仍按现有熔断规则处理。全部失败保留原系统提示或用途对应的 503；猜汤底保留最后无效正文供既有私密审计使用。

日志记录 pool、config_id、拒绝原因和尝试上限，不输出玩家正文、Key 或拒绝回复原文。计费账本把已识别的无效 HTTP 200 响应记为 `failed`，仍保留真实 HTTP 状态、usage 和按既有价格计算的消耗；历史账本不回写。

拒绝检测采用响应开头的明确服务拒绝句式和协议拒绝标记，不能仅凭正文出现“抱歉”判失败。NPC / 塔罗未新增通用业务格式限制；未知措辞仍可能漏检，应依据匿名化特征补回归用例，不应改为全文关键词封禁。

## 已核实的生产映射（2026-10-25）

宿主 supervisor 的 `turtle-soup` 工作目录为 `/opt/cedartoy/turtle-soup/backend`，命令为 `/opt/cedarstar/venv/bin/uvicorn main:app --host 127.0.0.1 --port 8012`。实际进程未覆盖 `TURTLE_SOUP_DB` / `API_COST_DB`，因此配置库为 `/opt/cedartoy/turtle-soup/backend/turtle_soup.db`，计费库为 `/opt/cedartoy/data/api_costs.db`。执行切换前重新核对进程环境，仅读取路径白名单，不打印整个 environment。

三个配置的接口均为 `https://api.chenmoai.com/v1`，有现成非空凭据，且三者凭据相同；不需要新建、轮换或复制 Key。

| ID | 用途 purpose | 切换前 model | 切换前 priority / enabled | 目标 model | 目标 priority / enabled |
|---|---|---|---|---|---|
| 28 | both（裁判与提示） | `[anti量]gemini-3.8-flash` | 2 / 1 | `[正向量3]deepseek-v4.1-flash` | 1 / 1 |
| 29 | npc（决策与发言） | 同上 | 2 / 1 | 同上 | 1 / 1 |
| 30 | tarot | 同上 | 2 / 1 | 同上 | 1 / 1 |

模型前缀和字符必须完整保留。SSE 适配按沉默域名和精确模型名匹配。只改模型而保留 priority=2，不会成为首选。三种用途现统一用正整数 1/2/3：DeepSeek 为 1；海龟汤的巧克力/小鸡农场为 2/3；NPC、塔罗的小鸡农场/巧克力为 2/3，保留按业务成本形成的备用顺序。

切换后计划路由：

- 裁判 / 提示：28(1) → 33(2) → 11(3) → 8(4) → 9(6) → 13(7)。
- NPC 决策 / 发言：29(1) → 17(2) → 31(3)。
- 塔罗：30(1) → 24(2) → 32(3)。

三用途共用沉默凭据锁与同模型健康状态；不通过复制配置规避串行限制。NPC 会跳过忙节点，必要时落到 Gemini 备线。这是保留的并发规则，不代表 DeepSeek 配置失效。

## 不触碰房间的探活

2026-10-25 使用 #28 现有凭据、目标模型完成了一次实际 SSE 探活：HTTP 200、1.9 秒、严格返回“测试成功”，上游 usage 为输入 17、输出 2、共 19 tokens。未经过房间 / 存档 / 生产账本写入链路。该探活只证明当时的连通性、模型名、凭据和 SSE 解析，不证明长上下文、完整塔罗输出或负载下的表现。新模型价格未核验，usage 不能换算为已确认金额。

随后另做了五次短合成任务探活（复用同一凭据与模型），全部 HTTP 200：普通判定 2.40 秒 / 49 tokens、猜汤底 JSON 3.28 秒 / 259 tokens、提示 2.06 秒 / 65 tokens、NPC 决策 2.23 秒 / 36 tokens、塔罗短解读 2.42 秒 / 80 tokens。前三项使用原有业务 validator，NPC 检查合法 action JSON，塔罗只检查非拒绝且有足够长度的短文。这些是虚构短提示，不是生产完整系统提示、私密汤底或完整牌阵质量评测。六次探活总计上游报告 508 tokens，未写生产账本。

离线回归：内容回退 / SSE / 计费 55 项，以及既有韧性 / 官方 DeepSeek 额度 / NPC 与塔罗桥接 76 项通过。测试用 MockTransport、临时库或被替换的计费 recorder，不连接真实模型与生产存档。

需要重新探活时，在仓库根执行以下代码。每次只发一个付费用量请求，最多 128 输出 tokens、45 秒总时限。`cfg=None` 是此诊断特意绕开生产账本的参数；不要改成 `_post_chat_completion` / `test_config`，后两者会计费入账或修改运行健康。

```bash
/opt/cedarstar/venv/bin/python -B - <<'PY'
import asyncio, json, logging, sqlite3, sys, time
from pathlib import Path
sys.path.insert(0, 'turtle-soup/backend')
import httpx, judge
logging.disable(logging.CRITICAL)
with sqlite3.connect(Path('/opt/cedartoy/turtle-soup/backend/turtle_soup.db').as_uri()+'?mode=ro', uri=True) as db:
    db.row_factory = sqlite3.Row
    cfg = dict(db.execute('SELECT id,api_url,api_key FROM judge_api_configs WHERE id=28').fetchone())
model = '[正向量3]deepseek-v4.1-flash'
assert judge._requires_chenmo_sse(cfg['api_url'], model)
async def run():
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=45) as client:
            response = await asyncio.wait_for(judge._send_chat_completion(
                client, cfg['api_url'], cfg['api_key'], {
                    'model': model, 'temperature': 0.1, 'max_tokens': 128,
                    'messages': [{'role': 'user', 'content': '请只回复：测试成功'}],
                }, cfg=None, pool='test',
                validate=lambda r: judge._completion_text(r, lambda s: s.strip() == '测试成功'),
            ), timeout=45)
        print(json.dumps({'success': True, 'http_status': response.status_code,
                          'seconds': round(time.monotonic()-started, 2)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({'success': False, 'error_type': type(exc).__name__}, ensure_ascii=False))
        raise SystemExit(1)
asyncio.run(run())
PY
```

## 主助手切换顺序

1. 复核本补丁与定向测试，保留工作树其他脏改。核验当前进程环境、上述三条配置及已有主备列表；若与表中不同，停止套用旧值，重新确定变更和回滚基线。
2. 对实际配置库做 SQLite 一致性快照，按 [备份手册](BACKUP.md) 的机制保存到受保护位置（含凭据，限制访问，不入 Git）。不要直接复制正在运行的 `.db`，也不要运行整个备份上传流程来代替一次局部快照。记录三条配置的非敏感原值用于逐条回滚。
3. 发布已复核的代码，按 [运维手册](OPERATIONS.md#服务与日志) 用显式 supervisor 配置重启 `turtle-soup`。本补丁也修改主进程导入的 `api_costs.py`，应同步重启 `cedartoy`。这是待执行步骤，本开发任务没有执行部署。
4. 再做上述合成探活，失败就保持原生产配置。先切 #28 并核验，再切 #29、#30。通过既有管理员配置更新接口 `PUT /soup/api/admin/api-configs/{id}` 操作，沿用管理员会话，不新建账号或轮换用户 Token。
5. 每次更新提交现有 `name`、原 `api_url`、原 `purpose`、`enabled=1`、目标 `model` 和 `priority=1`；同时按上表将同用途的巧克力、小鸡农场配置编号依次调整为 2/3。`api_key` 留空字符串使服务端保留原凭据。接口会重置该节点运行状态。不要用列表接口返回的掩码字符串覆盖 Key，不改其他配置，不批量禁用 Gemini 备用。配置按请求读取，三条配置更新自身无需再次重启。
6. 回读核对九个相关 ID 的精确 model / priority / enabled / purpose；禁止打印 Key 字段。用模拟配置与 MockTransport 离线核验四个 pool 的第一候选是 28 / 29 / 29 / 30。真实业务正常到来后，从计费账本按 `config_id,model,purpose,status,http_status` 聚合观察目标模型是否实际承担 soup / npc / tarot；不在公共房间提交测试、不展示用户内容。
7. 观察 `/var/log/turtle-soup.err.log` 的拒绝原因、fallback 和总时限，以及账本 `failed`（包括 HTTP 200 无效答案）。原有“仅 HTTP 200 即健康”的判断已不足够。使用合成内容单独检查普通判定、猜汤底 JSON、提示、NPC 和塔罗的任务质量；没有经过业务校验的“测试成功”不能宣称全部玩法验收完成。

## 回滚

配置回滚优先，保留已经修复的回退代码。要恢复本次切换前的路由，需把 #28 / #29 / #30 的 `model` 恢复为 `[anti量]gemini-3.8-flash`、`priority` 恢复为 2；同时把 #33 / #17 / #24 的 `priority` 恢复为 0，#11 / #31 / #32 的 `priority` 恢复为 1。保持凭据、purpose、enabled 不变，通过相同管理接口提交。若只完成部分切换，仅回滚对应已改行；配置热读取，无需重启。

不要为这次配置回滚整库恢复：账号库还有并发产生的用户与游戏数据。若代码自身出现回归，再由主助手用部署前保存的文件版本恢复本补丁相关生产代码并重启对应服务；不要 `git reset --hard` 清掉无关工作树改动。回滚回退代码会重新暴露 HTTP 200 拒绝反复命中主节点的已知缺陷。
