# RackNerd 目标套餐监控

只监控 **2025 Black Friday：4 GB RAM / 3 vCPU / 65 GB SSD / $29.98 年付**，对应产品 925。独立标准库脚本，不导入业务模块、不操作业务数据库、不重启服务。

## 来源与可信度

1. 官方详情：[4 GB KVM VPS (Black Friday 2025)](https://my.racknerd.com/index.php?rp=/store/blackfriday2025/4-gb-kvm-vps-black-friday-2025)。每轮优先请求；只有精确产品名、规格、可用服务器配置表单和 `billingcycle=annually` 的 $29.98 选项都符合，才确认可购买。官方明确缺货优先于第三方；403、验证页、异常重定向、解析失败都记为未知。
2. 辅助页面：[RackNerd Club Black Friday 2025](https://racknerd.club/en/black-friday-2025)。只解析 `4GB Package` 卡片，核对 RAM、CPU、SSD、年价和 `/product/925` 链接。读取允许访问的活动页，不请求其 robots 禁止的 `/product/` 跳转。识别按钮 `Sold Out` / `Out of Stock`、`Available` / `In Stock` / `Order Now` / `Buy Now`；空白、未知文案、禁用的有货按钮或重复卡片均不参与判断。

**Club 未提供库存更新时间，也没有可验证的刷新承诺。HTTP 200/Date 只证明本次取到了页面，不证明其库存实时。** 本监控只能发现这个第三方页面的状态变化，无法保证与官方补货同步。

2026-10-18 VPS 实测：官方目标页仍返回 Cloudflare 403；Club 返回 200，目标卡片显示 Sold Out。调查 [Tracker 的公开搜索页](https://racknerdtracker.com/?q=4+GB+KVM+VPS+%28Black+Friday+2025%29&stock=0) 时，首页全局检查时间虽为当天，目标产品自身 `Checked` 时间却为 **2026-04-22**，因此不接入决策，不能把全局刷新时间当成目标库存更新时间。过期 Tracker 记录不能成为第二份确认。

## 判定与通知

- 每天北京时间 **09:00、21:00**。每轮请求上述两个页面；所有正向信号及发生变化的缺货信号（包括首次缺货基线）均等待 **20 秒**再次请求同一页，规格、价格和状态都通过才采信。不是两家独立来源，也不是库存真实性保证。
- 不复用响应缓存，发送正常 `Cache-Control: no-cache, max-age=0`；HTTP `Age` 超过 5 分钟拒绝采信。若 Club 添加了 `Last Updated`，仅接受可解析且距当前不超过一天的时间；时间未知时只允许“页面变动线索”。不解验证码、不伪造验证 Cookie、不轮换代理。
- 首次第三方有货**不通知**。必须先观察到缺货，在最近 **48 小时**内的缺货基线上转为明确有货，并通过本轮两次读取，才允许疑似补货通知。解析错误/页面消失既不算缺货，也不会重新开启已发送事件。
- 官方无法复核时，Telegram 使用：**“疑似补货，官方站无法自动复核，请手动打开链接”**。附官方详情、Club 来源和官方失败原因，并声明第三方没有库存更新时间。不会把辅助信号写成官方已确认。
- 官方两次确认可配置套餐时可发正式确认；官方明确缺货会取消待发疑似通知。疑似通知后若官方恢复并证实，可发一次正式确认升级。
- 每个来源每次缺货→有货周期只提醒一次。再次确认缺货后才重新开启；无货本身不通知，也不再通知其他套餐、降价或泛优惠。
- 发送失败持久保留待发事件；只有下轮重新确认相应证据才可重试，缺货/官方否定取消过期待发事件。疑似待发事件超过 48 小时取消。
- 只有**两种信号都不可用**连续三轮才发一次合并故障告警；任一恢复后重新计数。官方 403 但 Club 可用时显示 `auxiliary_only`，不反复发 403 告警。

`--dry-run` 的 `mode`：`official_confirmed` 表示官方可判断（含确认缺货），`auxiliary_only` 表示只有第三方页面线索，`unavailable` 表示本轮无法判断。前两种退出 0，全部不可用退出 1；退出 0 不等于官方库存已确认。输出含每个来源状态、错误、检查次数、抓取时间和拟发事件。

## 状态与执行

- 配置：`deploy/racknerd-monitor.json`，固定目标字段、核对间隔、基线有效期、故障阈值及既有 Telegram 配置路径。
- 状态：`data/racknerd-monitor/state.json`（0600、原子替换、fsync）。v2 按官方/Club 分开保存最后明确状态、最后读取、通知标记和待发事件。
- 首次读到旧 v1 状态时，将完整旧数据存入 `legacy_v1`，保留审计信息；旧泛套餐待发通知不会发送。损坏状态拒绝覆盖，不通过删状态解决抓取失败。
- 锁：同目录 `monitor.lock`；暂停标记：`PAUSED`；日志：`monitor.log`，256 KiB、两份轮转。
- 定时仍用 root crontab 的 `# racknerd-monitor-managed` 行；勿同时安装 `deploy/cron.d/racknerd-monitor` 模板。`scripts/install_racknerd_monitor.py` 保留其他任务并回读验证。
- Telegram 继续只读 `/opt/cedarclio/.env` 中既有通知字段，不复制或输出凭据。成功响应后才清理待发事件。Telegram 不提供幂等键，若已发送但响应丢失，后续重试仍可能重复。

```bash
python3 -m unittest tests_toy.test_racknerd_monitor
# 真实外网抓取；不写状态，不发通知
python3 scripts/racknerd_monitor.py --dry-run
# 仅首次建立目标基线，可从旧 v1 迁移；有目标基线时拒绝重置；不发通知
python3 scripts/racknerd_monitor.py --initialize
# 定时执行入口：会持久化，并发送符合上述条件的正式通知
python3 scripts/racknerd_monitor.py
```

修改脚本/配置后下次 cron 直接使用，无需重启业务服务。维护验收不发送测试 Telegram。真实外网 dry-run、针对性测试和 cron 检查需分别完成；合成的有货变体用于验证通知状态机，不能据此声称已观测到真实补货。
