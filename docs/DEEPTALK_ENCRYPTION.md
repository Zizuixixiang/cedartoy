# Deep Talk 服务端存档加密

本变更只涉及 `deeptalk_rounds.human_answers/ai_answers` 和 `deeptalk_requests.digest`。其他列、其他游戏、Web UI、HTTP/MCP 参数和身份鉴权不变。集成基线为 `753dd13`；此文不表示已部署或迁移生产数据。

## 格式和兼容边界

- 使用现有依赖 `cryptography` 的 `AESGCM`，256 位独立服务器密钥。已在本地 `cryptography 3.4.8` 验证，无新增依赖。[官方 AEAD 文档](https://cryptography.io/en/3.4.8/hazmat/primitives/aead.html)说明 nonce 和认证标签要求。
- TEXT 字段格式为 `dtenc:v1:` + 标准 Base64(`12字节随机nonce || 密文 || 16字节GCM标签`)。每次字段写入产生新 nonce，空答案也加密。版本、表、轮次、人类/小机 ID、字段作为 AAD；答案另绑定本方 revision，摘要另绑定角色和 request_id。共享小机侧使用轮次原有 `ai_id=0`，不绑定当前调用者或 Token。
- 原 SHA256 输入的 JSON 排序、空格、UTF-8、qid、answer、revision 均未改。保存的是**加密后的原 SHA256 字符串**，迁移也只是包裹旧摘要，不从当前答案重算；即使答案后来编辑或提交，老请求仍按原规则重试。避免公开低熵答案的裸哈希供离线猜测。
- 只在原有权限校验后解密。盲答维持当前代码的“某方正式提交后，对方可见该方答案”；chat 维持实时可见。共享绑定、单机旧轮次、双方 revision、提交、历史和活动记录规则不变。历史/题库/source 无答案，不需要密钥。
- 正常服务**不接受明文回退**。缺失/格式错误/权限过宽的密钥、错误密钥、未知密文版本、截断、认证失败、损坏 JSON 都返回固定存储不可用错误（网页 503，MCP 沿用现有错误映射）。不输出答案、密钥、底层异常或路径；事务回滚并保留原数据，不自动建新答案覆盖坏档。
- 加密保护 DB/备份中的答案与摘要；名称、参与 ID、题目快照、时间、进度元数据、request_id 仍是明文。客户端本来可见的回答仍经原接口返回。它不保护已被控制的应用进程，也不能阻止攻击者回放整份旧数据库；权限元数据未做全库认证。

## 必需配置与密钥恢复

唯一配置为 `DEEPTALK_SAVE_KEY_FILE`，值是**绝对文件路径**。文件内容必须恰好为 **32 个原始随机字节**，不是 hex/Base64，不加换行；普通文件、非符号链接、group/other 权限全部关闭（如 0400/0600），由服务进程可读。应放在 Git、数据库、应用数据目录和自动 DB 备份范围之外的受限目录，由部署系统安全挂载或注入。环境变量只放路径，不放密钥内容。不通过命令行参数、SQL 或日志传递密钥。

以下仅示范在**隔离演练目录**生成新测试密钥，绝不能覆盖现有密钥，也不要在恢复时生成替代密钥：

```python
import os
from pathlib import Path
path = Path('/secure/offline-rehearsal/deeptalk.key')  # 事先创建并限制父目录访问
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
with os.fdopen(fd, 'wb') as out:
    out.write(os.urandom(32))
    out.flush()
    os.fsync(out.fileno())
```

密钥须独立托管和备份，记录对应的数据备份范围/密钥时期，限制访问并定期做恢复演练；不要把密钥一并塞入现有 DB 归档/R2。丢失密钥且没有独立备份时，密文无法恢复。Token 轮换不涉及本密钥。v1 不提供在线密钥轮换/多密钥环：不要直接替换密钥文件。文件首次用于一个 Store 时读入内存，同一 Store 生命周期复用；正式请求各自创建 Store。修改配置后应在计划发布中重建服务进程，避免混用密钥。纯明文或全新空库不能自行判断“选错但长度合法的密钥”，上线前必须核验配置和独立备份；已有密文可用下面的只读检查验证密钥。

## 迁移与发布次序（人工集成后执行）

本次只在临时库演练。脚本没有默认数据库、不读 `SESSIONS_DB`，必须显式传已有文件；不会创建数据库、改 schema、删除数据、VACUUM、checkpoint、修改备份或访问 R2。只读模式也需要 SQLite 读取一致性视图所需的文件权限。

1. 人工合并，重点检查 `deeptalk/store.py` 与另一窗口的存取逻辑冲突。保留其最新业务规则，只把字段读写放进加解密边界。确保任何新增答案副本/回执没有绕过加密。同步测试夹具的临时密钥配置。
2. 核实拟发布服务的实际 `SESSIONS_DB` 和解释器依赖（不凭文件名猜库）。准备安全密钥配置及独立密钥恢复副本。
3. 先按 [备份手册](BACKUP.md) 的 SQLite 一致性快照方式制作受限离线副本，使用拟上线密钥演练检查、迁移、再次检查、重试、恢复。不要只复制一个仍在写的 `.db` 文件而漏掉 WAL。副本含其他游戏数据，应遵守同样保密规则。
4. 安排维护窗口，停止所有会写 Deep Talk 的旧进程，防止迁移后继续写入明文；确认备份和回退点。下面命令的路径始终由操作人显式指定，示例是离线副本：

```sh
export DEEPTALK_SAVE_KEY_FILE=/secure/offline-rehearsal/deeptalk.key
python3 scripts/migrate_deeptalk_encryption.py --db /secure/offline-rehearsal/sessions-copy.db
python3 scripts/migrate_deeptalk_encryption.py --db /secure/offline-rehearsal/sessions-copy.db --apply
python3 scripts/migrate_deeptalk_encryption.py --db /secure/offline-rehearsal/sessions-copy.db
```

5. 首次只读检查报告待迁移 `answer_fields` 和 `digests`；`--apply` 在单一 `BEGIN IMMEDIATE` 事务内处理所有 Deep Talk 记录，并解密校验已有密文。任一坏字段/未知版本/孤立回执/错误密钥均整批回滚。混合明文/密文可处理，重复执行不改已有密文，也不改 revision、时间、提交状态和 request_id。最终检查应为 `answer_fields=0, digests=0`，`encrypted_fields` 为验证通过的总字段数；脚本只输出计数。
6. 正式目标的操作需人工按已演练方案执行。确认所有旧写入者已停止、迁移和密钥检查成功后，再启用带密钥配置的新代码。未迁移的旧记录会明确报不可用；不能让明文兼容长期留在运行时。启动后验证双方读取、历史、继续答题、旧请求重试及错误监控，不记录真实答案。

## WAL、旧页和备份残留

字段更新成功不等于抹除过去的明文。SQLite 空闲页、rollback journal、WAL 的旧帧、长读事务、文件系统快照、已删除块及历史备份可能保留旧数据。测试专门证明：有读者固定 WAL 快照时，迁移后的逻辑字段已加密，旧回答仍留在 WAL。

本迁移不自动改整个 sessions.db 的维护行为。需要另行批准的离线清理方案：停止**所有共享该库的读写者**，保存受限的一致性恢复点；确认 checkpoint 无 busy 后进行 WAL truncate；按 SQLite 官方建议选择离线 VACUUM/重建干净一致性文件，再 checkpoint/truncate，检查完整性与其他游戏数据。`secure_delete` 对未来释放页可能有帮助，但不会追溯清除 WAL、备份或文件系统历史。不要手工删除仍有活跃连接的 `-wal/-shm`，不要为了消除回答而删除/替换整库。

本地验收只对一次性测试库执行了 checkpoint/VACUUM 并扫描 DB/WAL，确认测试回答与裸摘要不再出现；这不能证明生产介质、快照、SSD 旧块已安全擦除。生产方案应结合存储介质和备份保留政策评估。

现有本地归档和 R2 历史对象仍可能含明文。应单独盘点访问权限、保留期限与加密/退役方案；不能把本次代码上线当作历史备份已加密，不能擅自删除/改写归档或 R2。未来 SQLite 一致性备份保存的是密文，但若源库未做物理残留处理，仍应评估旧页被带入快照的可能性。现行备份脚本完全不变。

## 故障恢复

- **配置失败/错误密钥**：停止 Deep Talk 写入，恢复正确配置和原密钥，用离线一致性副本只读校验，再恢复服务；不要生成新密钥试图“修复”。
- **迁移失败**：事务会回滚。保留原库与错误现场（不要导出答案到日志），在受限副本排查；修正后重新运行幂等迁移。即使逻辑回滚，日志文件仍可能有历史页，按残留方案处理。
- **密文损坏**：保留损坏库的一致性副本，使用可信备份及其对应密钥验证恢复。没有有效认证的数据不能猜测性还原、跳过或覆盖。
- **恢复旧明文备份**：先在隔离副本迁移并验证，才可接入新服务；恢复加密备份先核对对应密钥。禁止直接让旧版明文代码读取密文。优先修复新版本；若必须回退旧版，只能另行规划受控数据恢复和维护窗口。整库回退会影响其他游戏和备份后的新增记录，不在本脚本范围内。

## 可复现验收

```sh
python3 -m unittest tests_toy.test_deeptalk tests_toy.test_deeptalk_packages tests_toy.test_deeptalk_quick_answers tests_toy.test_deeptalk_encryption
python3 scripts/persistence_check.py --deeptalk
python3 scripts/benchmark_deeptalk_encryption.py
git diff --check
```

测试与 benchmark 自建随机测试密钥、临时账号/会话库，覆盖环境中的密钥路径，退出清理；不需要真实配置。专项检查覆盖 DB/WAL 不含新回答明文、摘要保密、nonce/AAD、旧 schema/旧摘要/混合记录、重复/并发迁移、原子回滚、Token 轮换和跨账号隔离、HTTP/MCP 安全错误。现有回归继续覆盖双方隐藏策略、chat、绑定、revision 和并发重试。

2026-10-14 本地容器模拟（单次测量，非线上容量承诺）：包含 UTF-8/Base64/AAD 的字段加密/解密平均延迟如下，每档 200 次：128B 为 59.64/76.65 µs；8,000B 为 123.23/323.75 µs；800,000B 为 7.12/13.80 ms。8KB 每请求新 Store、含密钥文件读取与 SQLite 事务，保存/读题各 100 次平均 4.81/0.96 ms，p95 8.12/1.22 ms。密文长度分别为 217、10,713、1,066,713 字节；无明文性能基线，不据此声称性能增幅。
