# 备份 RLS 并发验证与接续（2026-09-21）

**本文件记录 7.73 策略并发证据；7.74 已将修复落入正式备份入口。**
最新实现及验证见[7.74 交接](BACKUP_PRODUCTION_ENTRY_20260921.md)，以下保留原候选验证范围。
现行 `scripts/backup.sh` 仍有[已复现的 RLS 导出阻断](DATABASE_OBJECTS_RESTORE_20260921.md)。
本批仅在被忽略的 artifacts 内验证实现方式，受检的 1,352 个非 Markdown 源文件集合及
摘要保持不变，未提交、推送、部署，也没有运行现有生产备份命令或读取真实 OSS。

## 新发现的完整性风险

在全新 PG16.15 上升级当前 0128，执行真实应用的发布、期初、复核、过账、独立对账和
关单样本，再增加两个专用合成测试表。只读备份角色未获得 superuser、BYPASSRLS、
所有权、写入权限或任何角色成员关系。

以下两种实现均实际产生了 **`pg_dump` 退出 0、应有 2 行却导出 0 行**的结果：

1. 先完成可读策略检查，随后另一连接提交 restrictive `USING(false)` 策略，再导出。
2. 先开启 REPEATABLE READ 并读取策略目录，另一连接提交限制策略后再取得表锁。
   旧目录快照中的检查仍通过，真实导出却采用新的过滤策略。

因此，单独增加 `--enable-row-security`，或者在旧目录快照上晚加锁，都不足以证明完整性。
PostgreSQL 的[导出参数说明](https://www.postgresql.org/docs/16/app-pgdump.html)也明确区分
角色可见行与完整备份；本次缺行结果来自实际执行，不只是源码推断。

## 已验证的候选生命周期

1. 第一连接使用 READ COMMITTED READ ONLY，执行原备份角色完整权限检查，并枚举
   受支持的 public 普通表/分区表。
2. 按固定顺序为全部目标表取得 `ACCESS SHARE NOWAIT` 锁，保持连接和事务存活。
   不支持的关系类型或 schema 拒绝，已有冲突锁时立即拒绝。
3. 加锁后才在第二连接开启 REPEATABLE READ READ ONLY。核对关系 OID、schema、
   表名和类型集合与已加锁集合完全一致，再复核原角色权限与所有适用的备份 SELECT 策略。
4. 从第二连接导出快照，使用该快照运行真实 `pg_dump --enable-row-security`，并设置
   `--lock-wait-timeout=1500ms`。两个保护事务都保留到导出结束。
5. 任一步失败均拒绝完成结果；成功 SQL 另恢复到一个全新的隔离集群，逐表比较内容。

SELECT 权限允许取得 ACCESS SHARE，锁保持到事务结束，见[官方锁语义](https://www.postgresql.org/docs/16/sql-lock.html)。
PG16 [策略实现](https://raw.githubusercontent.com/postgres/postgres/REL_16_STABLE/src/backend/commands/policy.c)
中的 CREATE/ALTER POLICY 取得 AccessExclusiveLock；本次也观察到了真实等待关系。

| 场景 | 实际结果 |
| --- | --- |
| 策略 DDL 先持锁 | 候选以 SQLSTATE `55P03` 拒绝，没有导出快照 |
| 加锁后、快照前新增表 | 关系集合不一致，明确拒绝 |
| 保护锁取得后，DDL 排在 pg_dump 之前 | 导出约 1.5 秒后退出 1，没有发布备份；释放保护锁后 DDL 才完成 |
| pg_dump 先取得自身全部表锁，再发起 DDL | `pg_locks` 和 `pg_blocking_pids` 同时证明 DDL 等待保护连接与实际 pg_dump |
| 导出持锁期间普通 DML | 另一连接成功提交更新；导出仍包含快照时旧值 |
| 导出结束但保护事务尚未结束 | DDL 仍在等待；保护事务结束后才提交 |
| 限制策略生效后的下一次备份 | 新检查明确拒绝，不接受过滤型备份 |
| 完整恢复 | 208 张 public 表、547 行摘要一致，其中 206 张应用表、2 张合成并发测试表 |
| 恢复后权限 | 先核对全部 208 张表，再移除恢复库内本测试新增的两张表；应用 API/Edge 权限边界通过 |

24 张受检 RLS 表由应用原有的 23 张和 1 张合成测试表组成，不应把 24 写为生产表数量。
恢复库包含原策略，未混入快照后的限制策略或普通 DML。所有操作仅作用于本运行新建、
已核验身份且不监听 TCP 的实例；恢复库系统标识与源库不同。

## 失败记录与证据

三轮都保留准确脚本、日志、结果及数据目录；没有复用或重启已停止的数据目录：

- 第一轮实际缺行反例成立，随后在超时错误文字断言处失败。PG16 的该导出参数在
  `LOCK TABLE` 阶段使用 statement timeout，已改为同时检查具体 LOCK 语句、退出码和时间界限。
- 第二轮全部并发场景通过，恢复时演练脚本错误删除了本次 dump 只会 ALTER 的 `public`
  schema。恢复事务失败，未算通过；改为仅当准确 dump 含 CREATE SCHEMA 时移除空 schema。
- 第三轮全部场景和恢复通过，工具会话 `88148` 已确认退出 0。三个源库、两个恢复库
  均已停止、postgres 退出 0，数据及失败证据保留。

证据目录：`cloud_oam/artifacts/backup-rls-concurrency-20260921/`。
主文件为 `run_backup_rls_concurrency.py`、`backup_rls_snapshot_candidate.py`、`result.json`、
`verification.json`、`drill-third.log`；前两轮脚本保存在 `first-attempt/`、`second-attempt/`。
最终源库为 `source-checks/run-gc54al1d/`，恢复库为 `restore-checks/run-o7bxx3e2/`。

源库 `race-evidence.json` 保存锁等待、超时和拒绝场景；`protected-expected.json` 与恢复库
`restored-facts.json` 保存完整表摘要，两者 SHA256 均为
`7004e517f3af64d82da052ab6137ea86f3470a3e47491b49ef2118e2e40e8b3a`。
成功 SQL 摘要为 `1d28492ba9520822fc52bf438492661dd30f04d772a1ac057c6b2402f08bf0a0`。

## 下一步与尚未覆盖的范围

原本地静态回归后来已确认退出 2，未全量通过。已单独复现两项
历史哈希断言错误：0109 通知目标测试、0127 对账事件测试拿当前 0128 清单比较旧迁移。
准确补丁保存在原静态证据目录 `historical-manifest-fix-candidate.patch`，`git apply --check`
通过，并在原运行结束后应用，固定到各自历史清单，保留当前完整清单的独立验证。

随后把受控生命周期纳入实际备份工具，并补正式测试和 Docker Compose 端到端验证。
当前 Python 候选不能直接当作生产运行入口：现有数据库服务是 `postgres:16-alpine`，
不能假设它具备本机的 Python/psycopg；需落实相应执行依赖或验证 psql 编排方案及子进程
失败传递。必须继续保留原专用凭据、077 文件权限、失败不发布、归档校验和原子改名。

本次仅覆盖列明的策略 DDL、关系集合、普通 DML 与快照竞态；没有验证并发角色管理、
进程崩溃、容器重启、真实 OSS 传输、密钥恢复、生产规模或 RPO/RTO。此前数据库与对象
联合恢复证据保留，但本轮没有重做对象归档。完整 GitHub PG16/客户端门禁、真实公开
目录、三天持续对账、多角色真机、通知供应商和生产灰度仍开放；公开目录继续 pending/0。
