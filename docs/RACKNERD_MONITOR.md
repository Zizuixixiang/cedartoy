# RackNerd 优惠与库存监控

独立标准库脚本，不导入 CedarToy/CedarStar/CedarClio 业务模块、不重启服务、不操作业务数据库。仅允许 HTTPS RackNerd 官方域名，不监控 GreenCloud。

## 文件与配置

- 脚本：`scripts/racknerd_monitor.py`
- 非秘密配置：`deploy/racknerd-monitor.json`
- 状态：`data/racknerd-monitor/state.json`（权限 0600、原子替换、fsync）
- 锁：`data/racknerd-monitor/monitor.lock`
- 日志：`data/racknerd-monitor/monitor.log`（256 KiB，最多两份轮转）
- 暂停标记：`data/racknerd-monitor/PAUSED`
- 定时模板：`deploy/cron.d/racknerd-monitor`；安装脚本使用 root 用户 crontab，二者只安装一个。

默认精确匹配 4096 MB / 4 GB RAM、美元年付 ≤ 3000 美分。配置 `ram_mb`、`max_annual_cents` 可调整；`sources` 可添加官方活动页或购物分类页。支持 GB/MB、`$/year`、`$/yr`、`USD Annually`，不把月付或两年付当年付；优先采用实际账单价格而非描述里的旧价格。

已有来源包括 specials、BlackFriday、NewYear 及 Black Friday 2025、Black Friday 2024、New Year Specials、Special Promos 官方购物目录。旧活动入口可能重定向，保留购物目录才能继续追踪旧产品。新套餐发现限于配置页面，不扫描全站。后续新活动可添加来源，无需修改代码。

明确 `0 Available` / Out of Stock 优先于 Order Now 按钮；符合门槛、可能有货的产品会额外 GET 一次订购页，要求出现对应产品、年付金额及服务器配置表单后才确认为可购买。不提交表单，不登录，不下单。未知页面/反爬/异常跳转不当作有货；目标产品库存未知计入来源失败。按产品链接去重，同次重复 URL 使用缓存。

## 通知与持久化

只读 `/opt/cedarclio/.env` 中既有 `TELEGRAM_BOT_TOKEN`、`TELEGRAM_MAIN_USER_CHAT_ID` 和可选 HTTP(S) `TELEGRAM_PROXY`。不复制凭据，不记录响应正文、异常完整消息或 Telegram API URL。既有通知 helper 会吞掉发送错误，因此这里复用配置，直接检查 Telegram `ok` 和 `message_id`，确认成功才移除待发事件。

新符合条件套餐（无货也注明状态）、无货→有货、符合门槛的价格下降会通知。普通首次运行会报告当前符合条件套餐；可用 `--initialize` 首次静默建立基线。之后相同状态不重复；正常缺货本身不通知。未知/消失的产品保留最后已确认状态，不凭页面缺失认定缺货。

发送失败保留待发事件，下次计划检查确认状态仍有效才重试；过期补货通知取消。连续三次来源失败后只发一条合并告警，全部恢复后重新允许下一轮故障告警。没有几分钟重试、后台常驻循环或自动网络重试；一次扫描请求间隔至少 2 秒、单请求超时 25 秒。

Telegram 不提供消息幂等键：若发送已成功但网络返回丢失，或恰在成功后写状态前崩溃，下一轮可能重发；正常成功响应后的相同状态由磁盘状态去重。损坏状态拒绝覆盖，日志留错误类别，需保留文件并人工处理。

## 安装与验收

在具备外网及 crontab 写权限的 VPS shell 执行：

```bash
cd /opt/cedartoy
python3 -m unittest tests_toy.test_racknerd_monitor
python3 scripts/racknerd_monitor.py --dry-run
python3 scripts/racknerd_monitor.py --test-telegram
# 可选：仅第一次，且所有来源检查成功时静默建立初始状态。
python3 scripts/racknerd_monitor.py --initialize
python3 scripts/install_racknerd_monitor.py
crontab -l
```

安装器验证服务器时区为 Asia/Shanghai，保留全部既有任务、保存私有 crontab 备份，仅增删带 `# racknerd-monitor-managed` 标记的行，并回读验证。重复安装幂等。不要同时把 cron.d 模板复制到 `/etc/cron.d`。

安装后的任务：

```cron
0 9,21 * * * /usr/bin/python3 /opt/cedartoy/scripts/racknerd_monitor.py >/dev/null 2>&1 # racknerd-monitor-managed
```

必须分别确认外网 dry-run、实际 Telegram 测试和 cron 安装结果，单元测试成功不等于这三项已完成。合成 HTML 测试覆盖已知页面字段及常见标签布局；仍需用 VPS 实际响应验收 HTML 解析。

## 日常操作

```bash
# 手动真实检查、保存状态，并发送符合条件的通知
python3 /opt/cedartoy/scripts/racknerd_monitor.py
# 只抓取预览，不改监控状态、不发通知
python3 /opt/cedartoy/scripts/racknerd_monitor.py --dry-run
# 暂停；保留状态和定时配置
touch /opt/cedartoy/data/racknerd-monitor/PAUSED
# 恢复
rm /opt/cedartoy/data/racknerd-monitor/PAUSED
# 彻底移除本任务的 cron 行（状态保留）
python3 /opt/cedartoy/scripts/install_racknerd_monitor.py --remove
# 最近日志
tail -n 20 /opt/cedartoy/data/racknerd-monitor/monitor.log
```

不要删除 `state.json` 来解决普通抓取失败，否则会丢失去重历史。更改脚本或来源配置无需重启任何现有服务。
