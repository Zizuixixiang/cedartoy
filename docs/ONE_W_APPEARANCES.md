# 1W 纪念装饰自动发放

`avatar_appearances.grant_one_w` 复用 `grant`，只向 inventory 补缺项，不更新
`account_avatar_selection`、旧发放时间或已有试用权益。

- 普通装饰：当前 `deleted_at IS NULL` 且 `id <= 10000` 的账号。
- 专属框：有效账号 10000，以及它所属的人类与绑定小机组。若 10000 是小机，
  它与人类的绑定也必须符合截止条件；该人类的其他小机逐条检查绑定时间。
- 截止条件：绑定 `created_at < '2026-09-26 00:00:00'`，直接按库中北京时间
  比较，包含 9 月 25 日 23:59:59。即使次日补跑，仍能补截止前的有效绑定。
- 不删除已有权益；账号注销后的行不发，已发后解绑不撤销。只依据仍在库中的
  绑定记录补漏，无法还原部署前已经解绑并删除的历史关系。

## 自动触发

主服务完成时间迁移后先补一次全量；注册共用记录入口及绑定共用入口在同一
事务内发放（包含网页、根 MCP、Operit），事务回滚时发放一起回滚。

9 月 25 日内启动主服务，还会启动短时后台线程，每 10 秒补漏一次，覆盖海龟汤
独立注册入口等其他写入者。北京时间 9 月 26 日零点最后补一次，然后自动退出；
次日启动不再创建线程。每次重启重新查库，不依赖内存中的已完成标记。
截止后主服务注册和绑定的事件触发仍生效，但晚于截止的新绑定无资格。

## 主助手上线操作（本次开发未执行）

正常方式：部署这些文件并重启 `cedartoy` 即启用，不需要安装 cron/timer，
也不需要重启其他游戏服务。

若需在重启主服务前立即覆盖今晚，可先启动独立短时补发进程（必须使用与服务
相同的 `TURTLE_SOUP_DB`；默认即下方主库）。进程本身包含明确日期保护与退出逻辑，
不要设置永久每分钟 cron，也不要配置 `Restart=always`。用临时 systemd 服务示例：

```sh
systemd-run --unit=cedartoy-1w-catchup --property=Restart=on-failure --property=RestartSec=10 /usr/bin/python3 /opt/cedartoy/scripts/grant_one_w_appearances.py --db /opt/cedartoy/turtle-soup/backend/turtle_soup.db --watch
```

正常退出后不会重启。临时服务不能替代长期注册/绑定钩子的部署。主服务与短时
进程同时运行也安全，SQLite 事务及 inventory 主键保证幂等。失败会记录日志；
截止时仍失败则以非零状态退出，供服务管理器重试最后一次补漏。

任何日期都可以手动补一次：

```sh
python3 scripts/grant_one_w_appearances.py --db /opt/cedartoy/turtle-soup/backend/turtle_soup.db
```

脚本不导入 `server`，不启动服务，不执行试用发放；数据库路径不存在时直接报错，
不会创建空库。输出本次新增的 `decoration` / `frame` 数量。

## 验证

```sh
python3 -m unittest tests_toy.test_one_w_appearances tests_toy.test_account_avatar -v
```

上线前可用 SQLite backup API 复制主库，对副本连续补两次，检查第二次新增为 0、
`PRAGMA integrity_check` 为 `ok`，并逐行比较 `account_avatar_selection` 完全一致。
