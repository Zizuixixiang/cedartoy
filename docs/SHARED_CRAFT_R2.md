# 共同手作间：私有 R2 图片镜像与主存

代码默认 `off`；显式 `primary` 为二期主存，原 `mirror` / `prefer-r2` 仍是一阶段本地权威镜像。仅涉及卫星服务 `cedartoy-shared-craft`；主服务、nginx、
网页/MCP 的 `image/png` 路由、身份规则、Guide 与 UI 不变。没有新增依赖。
本次实现与测试不代表已经生产回填或切换。

## 数据布局与边界

- 固定桶 `cedarclio-backup`，固定前缀 `shared-craft/v1/`。适配器只提供 GET/PUT，
  不提供列桶、删除、公开 URL、修改 ACL 或跨前缀操作。
- 对象键为
  `shared-craft/v1/rooms/<房间数字ID>/<gallery|finished|shares|references>/<图片ID及后缀>/<SHA256>.png`。
  收藏完整图和 `-display` 图分别保存；装裱完整图与 `-piece` 透明图分别保存。
  投稿使用 submission ID 和原 BLOB，绝不从当前画布重新生成历史投稿。
- 房间身份来自既有服务端鉴权。作者和 pending 投稿的权限、gift 可见性、
  投稿状态检查都先于 R2 读取。知道对象键不能通过网页直接取图。
- **一期 mirror / prefer-r2 的未迁移数据**：本地 PNG 与 `shares.db` BLOB 全部保留为权威副本，数据库 schema 不变。
  第一阶段仍先读本地内容计算 SHA，再取 R2 并校验；不支持删除本地后仅靠 R2 运行，
  也暂不减少磁盘占用。旧 `shared_craft_r2.py backfill` 不删本地或远端对象。原有用户主动操作的业务语义不变。
- 远端同键内容不匹配时拒收、保留损坏对象供排查，不自动覆盖。每次新上传后必须 GET
  回读并比对完整字节/SHA256；不把 ETag 当 SHA256。

## 开关与实现

只给卫星进程设置以下变量，保留其已有 secret、库路径及其他环境：

| 变量 | 默认 | 含义 |
| --- | --- | --- |
| `CRAFT_R2_MODE` | `off` | `off`：纯本地且不读取 rclone 配置；`mirror`：本地读、后台双写；`prefer-r2`：双写且优先读取校验后的 R2 图片，失败回退本地；`primary`：验证 R2 后只提交本地短引用（见二期章节） |
| `CRAFT_R2_ROOMS` | 空 | 逗号分隔的房间数字 ID，如 `123,456`；空代表全部房间。不是 AI ID、投稿 ID 或存档槽 ID |
| `CRAFT_R2_RCLONE` | `/snap/bin/rclone` | 只用于启动时读取一次 `cloudflare_r2` remote |
| `CRAFT_R2_RCLONE_CONFIG` | 不设置 | 使用 rclone 自己的配置路径；必要时显式指定已经核实的配置文件 |

启用时调用一次 `rclone config show cloudflare_r2`，只捕获这个 remote 的配置，
不输出凭据。标准库 HTTPS 连接复用 + SigV4 完成图片 GET/PUT，
不会为每张图片启动 rclone。未修改 requirements 或现有 rclone 配置。
连接/读写 socket 超时与连接锁等待分别为 2 秒；没有前台重试。
一期镜像故障触发 30 秒冷却，期间直接使用本地。主存引用没有本地原图可回退。DNS 与多次网络读取的总耗时不等同于
2 秒请求总截止时间；开启 `prefer-r2` 前应在 VPS 实测读取延迟。

一期镜像在本地原子提交后才入后台队列；队列最多 8 张、每张上限 32 MiB。
队列满、网络失败、强杀进程都不会撤销本地保存；缺失镜像由重复回填修复。
正常关闭会排空队列。历史文件不在启动时自动上传，避免启动时批量网络请求。
`mirror` 不增加前台网络读延迟；`prefer-r2` 仍在现有同步读取路径中执行 GET，
小范围验证性能后再扩大范围。后台上传也会等待同一条连接，读者锁等待有上限。

协议依据：[AWS SigV4 单块签名](https://docs.aws.amazon.com/AmazonS3/latest/API/sig-v4-header-based-auth.html)、
[R2 S3 API](https://developers.cloudflare.com/r2/get-started/s3/)、
[rclone config show](https://rclone.org/commands/rclone_config_show/)。
VPS 上既有 remote 已验证可写/读/删；agent 沙盒的 socket 限制不代表凭据失效。
本实现的真实 HTTPS/SigV4 接通情况仍由下面的 VPS 小范围 apply/verify 验收。

## 一期镜像操作步骤（不适用于已压缩主存引用）

所有命令在 `/opt/cedartoy` 运行。以下不调用通用备份脚本推送到其他前缀，
也不读取或修改账户 DB。先核对实际卫星进程名；示例使用仓库中的
`cedartoy-shared-craft`。`set -e` 示例应放在独立 shell 会话中执行。

### 1. 核对路径，保存一致性副本

只从运行中卫星进程读取 `CRAFT_DATA_DIR`，不要打印整个 environ：

```bash
cd /opt/cedartoy
set -e
umask 077
craft_pid=$(supervisorctl -c /etc/supervisor/supervisord.conf pid cedartoy-shared-craft)
export CRAFT_SOURCE=$(.venv-shared-craft/bin/python - "$craft_pid" <<'PY'
import os, sys
from pathlib import Path
env = dict(part.split(b"=", 1) for part in Path(
    "/proc/" + sys.argv[1] + "/environ").read_bytes().split(b"\0") if b"=" in part)
path = os.fsdecode(env[b"CRAFT_DATA_DIR"])
assert Path(path).is_absolute() and Path(path).is_dir()
print(path)
PY
)
export CRAFT_BACKUP="/home/backups/shared-craft-r2-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$CRAFT_BACKUP"
(
  set -e
  supervisorctl -c /etc/supervisor/supervisord.conf stop cedartoy-shared-craft
  trap 'supervisorctl -c /etc/supervisor/supervisord.conf start cedartoy-shared-craft' EXIT
  .venv-shared-craft/bin/python scripts/shared_craft_r2.py snapshot \
    --data-dir "$CRAFT_SOURCE" --output "$CRAFT_BACKUP/snapshot"
)
test -f "$CRAFT_BACKUP/snapshot/SNAPSHOT.json"
```

snapshot 只读取来源，目标必须是不存在的新目录、且位于来源之外。
房间复制遵循已有锁，`shares.db` 使用 SQLite backup API 并跑 `integrity_check`；
不会直接复制 DB/WAL/SHM。完成标记 `SNAPSHOT.json` 记录复制文件的 SHA256。
短暂停止卫星是为了得到房间文件和投稿库共同的时间点，不需停主站。
只有 snapshot 退出码 0 且完成标记存在才继续；失败的部分副本保留供排查，
重试用另一个新目录。此备份仅包含手作间，不替代平台完整备份。

### 2. 默认 dry-run，再按房间 apply 和回读

先选一个经核实的房间数字 ID，以下 `123` 必须替换。回填工具的 `--room`
与运行期开关分别控制；工具没有 `--room` 时处理全部房间，不依赖 `CRAFT_R2_ROOMS`。

```bash
CRAFT_CANARY_ROOM=123
.venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
  --data-dir "$CRAFT_BACKUP/snapshot" \
  --manifest "$CRAFT_BACKUP/backfill.jsonl" --room "$CRAFT_CANARY_ROOM"

.venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
  --data-dir "$CRAFT_BACKUP/snapshot" \
  --manifest "$CRAFT_BACKUP/backfill.jsonl" --room "$CRAFT_CANARY_ROOM" --apply

.venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
  --data-dir "$CRAFT_BACKUP/snapshot" \
  --manifest "$CRAFT_BACKUP/backfill.jsonl" --room "$CRAFT_CANARY_ROOM" --verify-only
```

dry-run 不加载凭据、不联网、不改来源，只在显式指定的外部路径生成 manifest。
`--apply` 才允许上传；`--verify-only` 只 GET，不 PUT。两者互斥。
回填遍历两个图片目录的全部历史 PNG（包括仍残留的旧图）及投稿表所有状态的原 BLOB，
不会发布 pending/declined 投稿。拒绝路径穿越、符号链接、异常房间/文件名。

每件记录 `source/room/kind/key/sha256/bytes/status/time`。远端动作前先 fsync
`planned`，成功回读后才记录 `uploaded/existing/verified`。失败记录类型，不记录
敏感远端响应；只要有失败便返回非零。**不以退出码 0 的 dry-run 代替上传成功**。

原命令重复执行即可续跑：每次重新读取本地并 GET 比对远端，不盲信 manifest
的成功记录；已有正确对象不再 PUT。上传后、完成记录落盘前中断也能自动识别。
manifest 独占锁避免并发追加；中断留下的未完成末行可修复，完整但非法的记录
会使任务停止；同一 manifest 不得换来源根目录或目标版本。
逐件回读会消耗 GET/流量，这是第一阶段完整性核验的取舍。

小范围确认通过后，去掉三个命令中的 `--room` 即可逐步回填全部。
若发现 `SHA256` 失败，保留本地、远端对象及 manifest，先调查；工具不会删掉或覆盖证据。

### 3. 双写、追平增量、渐进读取

1. 在现有卫星环境配置中加入 `CRAFT_R2_MODE=mirror`、
   `CRAFT_R2_ROOMS=123`（替换实际房间），保留全部原配置。
   supervisor 的 `environment=` 中若包含逗号房间列表，应使用引号包住变量值。
   应用修改后的配置并仅重启卫星：

   ```bash
   supervisorctl -c /etc/supervisor/supervisord.conf reread
   supervisorctl -c /etc/supervisor/supervisord.conf update cedartoy-shared-craft
   supervisorctl -c /etc/supervisor/supervisord.conf restart cedartoy-shared-craft
   ```

   如果实际由外部 env 文件供给、且 supervisor 配置本身未变，只需 restart。
   不要仅在 shell 中 export 后 restart，就假设 supervisor 子进程会继承。

2. 用已有网页/MCP 在选定房间保存一份新作品、装裱完整图/透明图、主动投稿，
   核对原图片路由与权限。查看 `/var/log/cedartoy-shared-craft.log` 中
   `Shared craft R2` 的故障/队列满提示。使用下面独立 manifest 追平在线数据，
   命令只读来源文件和投稿 DB；不会改生产存档：

   ```bash
   .venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
     --data-dir "$CRAFT_SOURCE" --manifest "$CRAFT_BACKUP/live.jsonl" \
     --room "$CRAFT_CANARY_ROOM"
   .venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
     --data-dir "$CRAFT_SOURCE" --manifest "$CRAFT_BACKUP/live.jsonl" \
     --room "$CRAFT_CANARY_ROOM" --apply
   .venv-shared-craft/bin/python scripts/shared_craft_r2.py backfill \
     --data-dir "$CRAFT_SOURCE" --manifest "$CRAFT_BACKUP/live.jsonl" \
     --room "$CRAFT_CANARY_ROOM" --verify-only
   ```

   在线删除/新建可能让一次扫描不完整或因文件消失而停止，重跑即可；
   需要严格某时点验收时仍使用短暂停服的一致性副本。
3. 将同一小范围的 `CRAFT_R2_MODE` 改为 `prefer-r2` 并重启卫星。
   验证授权用户的 PNG 与本地 SHA 一致、未授权仍拒绝、作者撤权立即生效、
   投稿不因收藏改变而变化、实际读取延迟可接受。检查 manifest 中所有目标对象均成功。
4. 扩大 `CRAFT_R2_ROOMS`，每批先 mirror + 回填/verify，再 prefer-r2。
   最后移除房间限制才是全量启用。为强杀/临时故障/队列满遗漏周期性重跑回填；
   本阶段代码不新增 cron，也不自动修改运维环境。

### 4. 回滚

**仅限尚未运行二期压缩、没有主存引用的数据**：将卫星 `CRAFT_R2_MODE=off` 并按上述方式应用环境、重启卫星，即恢复纯本地读写。
也可先退回 `mirror`，只关闭 R2 读取。无需逆向 SQL、下载覆盖或恢复旧 JSON：
本地保存一直在继续，恢复旧备份反而可能覆盖切换后的新作品。
R2 对象与 manifest 保留；没有删除远端的回滚步骤，也不得对桶根或其他备份前缀执行清理。
备份用于额外故障恢复，不是正常关闭 R2 的前置步骤。

## 二期 primary 主存

仅编码和临时数据验收，不代表生产已迁移或上线。浏览器 localStorage、vendor、花园均不在范围内。
没有新增依赖；参考图校验复用上游已有 Pillow。

- 开关为 `CRAFT_R2_MODE=primary`。`CRAFT_R2_ROOMS` 仍可限制新写入的房间，
  空代表全部；未选房间保留旧本地写入。选择主存不依赖后端是否健康，缺配置时启动失败，
  运行时上传、回读或容量失败返回明确错误，不降级成本地永久保存。
- 每张新图片同步 GET 查重；缺失时 PUT，再完整 GET 校验 SHA256 和字节。
  同键损坏拒绝覆盖。主存不使用一期后台队列，最多 8 个同步上传任务，满时立即失败。
  单张仍最多 32 MiB，装裱上传仍受上游每张 6,000,000 字节及请求 17 MB 限制。
- gallery 完整图 / `-display`、finished 完整图 / `-piece` 和投稿 BLOB 使用
  `CRAFT-R2:1:<64位SHA256>`，每份 75 字节。对象房间、类别和 ID 从业务上下文推导，
  引用不接受任意对象键、路径或 URL。PNG 路径仍存在供上游检查；网页 FileResponse、
  拼豆读取和投稿取图均经适配器解引用，作者授权和 gift 可见性仍先于远端读取。
- `primary.py` 只对各实例的上游 `CraftGallery.add` / `BeadBoards.save_decoration`
  两组图片写入做计数校验的 AST 替换；保留上游权限检查与业务逻辑。图片不暂存成 VPS PNG。
  所有图片验证完成后，`.r2-commit.json` 记录短引用及业务 JSON，再原子发布各文件。
  断电发生在发布中途时，持房间锁的下一次加载或重启会完成发布；上传中途失败不改旧文件。
  已传上 R2 但尚未发布的对象保留，不提供自动远端清理。
- 实际参考图字段是 `rooms/<room>/beads.json` 的 `boards[].reference_image`，
  **不是独立的 boards.json**。新上传和工作计划生成的参考图均保存为
  `{craft_r2:1, sha256:..., mime:...}`；只在已获权限的 `get` 结果中恢复 data URL，
  网页及 MCP image content 保持原协议。支持 PNG/JPEG/WebP/GIF，严格 Base64 与格式核验，
  data URL 长度不超过 1,500,000，解码图片不超过 16,777,216 像素。
  支持类型的 data URL 标头若误标另一种支持格式，上传和历史迁移均以 Pillow 检测的
  实际格式写入 MIME；读取或迁移恢复时规范化标头，原始图片字节和 SHA256 保持不变。
  对象使用新增 `references` 类别；沿用 `.png` 键后缀，真实 MIME 以短引用为准，未改码或重编码。
- 新投稿在 R2 验证后才提交 SQLite，`image` 列无需 schema 迁移；历史 `pending/shared/declined`
  的身份、状态、标题、附言、时间和快照语义不变。运行期不主动压缩无关旧图 / 旧 Base64，
  历史原件由显式迁移处理。尚未迁移的旧 PNG/BLOB/Base64 仍可正常读取。
- 已迁移引用可由本版代码的 `primary`、`mirror`、`prefer-r2` 读取，即使房间已移出写入名单；
  `off` 不加载凭据，遇到引用明确失败，绝不会把引用文字发成图片。**不能把 off 当作二期回滚。**

### 压缩、全量核验与续跑

工具为 `scripts/shared_craft_r2_primary.py`。CLI 严格只接受
`/opt/cedartoy/data/shared_craft`；仅遍历数字房间的 `gallery/*.png`、
`shared-bead-finished/*.png`、`beads.json` 参考图及 `shares.db.submissions.image`。
拒绝路径穿越、符号链接、非法名称和未恢复的运行期提交日志。
测试使用 Python 函数的显式 `allowed_root` 指定临时目录；CLI 没有放宽范围的开关。

主助手先核实实际 `CRAFT_DATA_DIR`，停止卫星写入，并在整个 snapshot → apply → verify
期间保持停止。若路径不同应先复核实现，不能绕过目录限制。以下命令仅供验收执行：

```bash
cd /opt/cedartoy
# CRAFT_PRIMARY_BACKUP 指向来源外、空间充足的专用备份目录；snapshot 子目录必须不存在。
CRAFT_PRIMARY_BACKUP=/home/backups/shared-craft-primary-YYYYMMDD-HHMMSS
mkdir -p "$CRAFT_PRIMARY_BACKUP"
.venv-shared-craft/bin/python scripts/shared_craft_r2.py snapshot \
  --data-dir /opt/cedartoy/data/shared_craft --output "$CRAFT_PRIMARY_BACKUP/snapshot"

# 默认 dry-run：不加载 R2 凭据、不联网、不改源文件，只写外部 manifest。
.venv-shared-craft/bin/python scripts/shared_craft_r2_primary.py \
  --manifest "$CRAFT_PRIMARY_BACKUP/primary.jsonl"

# 显式 apply：上传、完整回读、校验，然后逐对象原子替换。
.venv-shared-craft/bin/python scripts/shared_craft_r2_primary.py \
  --manifest "$CRAFT_PRIMARY_BACKUP/primary.jsonl" \
  --backup "$CRAFT_PRIMARY_BACKUP/snapshot" --apply --compact-db

# 全量 GET 核验，既检查已迁移引用，也检查尚存本地原图的远端镜像；不 PUT。
.venv-shared-craft/bin/python scripts/shared_craft_r2_primary.py \
  --manifest "$CRAFT_PRIMARY_BACKUP/primary.jsonl" --verify-only
```

`--room 123` 可重复指定做小范围 apply/verify；`--compact-db` 仅允许全部房间 apply。
apply 必须提供 SQLite 一致性快照，先校验 SNAPSHOT 文件清单及数据库完整性；
每份将压缩的本地原件还必须与快照中的对应图片/BLOB/参考图完全相同，防止旧快照漏掉新作品。
快照不匹配的原件保留并报告失败，重新停写、建立新快照后再续跑。

每个对象操作前 fsync `planned`，成功提交后记 `converted/unchanged/verified`；失败仅记异常类型，
不记录图片、附言、密钥或远端错误响应。有失败退出非零。重复运行重新核验真实源和远端，
不依赖 manifest 宣称成功；中断尾行可修复。文件 / JSON 在房间锁内检查并原子替换；
BLOB 在 `BEGIN IMMEDIATE` 事务内读取、验证、替换，只改 `image` 一列。
`secure_delete` 清空旧 BLOB 页，`--compact-db` 在无失败后执行完整性检查、VACUUM、WAL checkpoint；
需预留 SQLite 重建及一致性快照空间，不会删除备份或 R2 对象。

报告 `source_bytes`、`estimated_after_bytes`、`committed_after_bytes` 是被选图片载荷/引用的逻辑字节，
不把它们当整个目录磁盘节省；`db_bytes_before/after` 是 SQLite 及 sidecar 的实际文件字节。
备份、业务元数据、文件系统块开销不含在图片估算中。dry-run 的估算不能替代上传或核验成功。
旧一期 backfill 识别短引用后只 GET 校验，绝不把指针当图片上传；参考图全量核验使用二期工具。

### 二期回滚

保留二期代码及 R2 读取能力时可暂退 `prefer-r2`，但这会让之后的新图恢复本地权威保存，
不是长期主存方案。若要回滚旧代码或切 `off`，必须先停写、对**当前**数据再做一致性快照，
通过以下操作恢复所有当前引用。不能直接拿切换前的旧快照覆盖切换后的新作品。

```bash
# 先用 snapshot 命令生成新的 $CRAFT_RESTORE_BACKUP/snapshot，来源仍为固定数据目录。
.venv-shared-craft/bin/python scripts/shared_craft_r2_primary.py \
  --manifest "$CRAFT_RESTORE_BACKUP/restore.jsonl" --restore
.venv-shared-craft/bin/python scripts/shared_craft_r2_primary.py \
  --manifest "$CRAFT_RESTORE_BACKUP/restore.jsonl" --restore --apply \
  --backup "$CRAFT_RESTORE_BACKUP/snapshot"
```

`--restore` 逐引用 GET + SHA256 校验后原子恢复 PNG、原 BLOB 或 data URL，不 PUT/删除远端。
未迁移原件保持原样；可重复运行。dry-run 不下载，所以引用原图大小未知时以
`restore_unknown_objects` 单列，不能把其零字节估算当实际恢复空间。
任何失败都保留对应引用，必须排除故障并全部恢复后才能启用旧代码/off。
R2 故障时没有新作品的本地原图兜底；迁移前快照只覆盖当时数据，应单独保留并按时间点恢复策略使用。
备份删除、R2 清理、生产开关及服务操作均由主助手另外验收执行。

上游更新仍须先审查 `CraftGallery.add` / `BeadBoards.save_decoration` 的图片写入方式、
参考图入口及完整图/透明图导出，再更新 `upstream.json` 并复跑定向回归；不能只重算哈希绕过检查。

## 离线验收

使用隔离解释器，全部 fixture 在临时目录，不运行真实 R2、生产服务或生产数据迁移：

```bash
.venv-shared-craft/bin/python -m unittest \
  tests_toy.test_shared_craft_r2 \
  tests_toy.test_shared_craft_r2_primary \
  tests_toy.test_shared_craft \
  tests_toy.test_shared_craft_author_mcp \
  tests_toy.test_shared_craft_sharing \
  tests_toy.test_shared_craft_framer \
  tests_toy.test_shared_craft_guide
SHARED_CRAFT_TEST_PYTHON=/opt/cedartoy/.venv-shared-craft/bin/python \
  python3 scripts/persistence_check.py --shared-craft
git diff --check
```

fake S3 在 HTTPS 连接边界验证签名、路径、GET/PUT、连接复用；无需监听 socket。
另覆盖授权/拒绝、gift 私密图、不可变投稿、完整图/透明图、旧本地兼容、
多房间隔离、dry-run、重复迁移、坏 SHA、读回失败、上传中断、manifest 续写、
WAL 数据库一致性副本、后台队列上限与关闭、R2 故障回退和 off 回滚。
