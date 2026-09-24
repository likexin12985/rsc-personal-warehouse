# 数据库与附件联合恢复，以及新发现的备份阻断（2026-09-21）

**本文件保留原脚本 RLS 阻断及联合恢复的发现记录；7.74 已将该修复落入正式文件。**
当前结果及尚未覆盖的真实 Docker/OSS 范围见[7.74 交接](BACKUP_PRODUCTION_ENTRY_20260921.md)。
它的只读备份角色 SQL 预检通过，但 `pg_dump` 默认命令因 `audit_logs` 的行级安全
策略失败。本次已验证不提权的候选导出路径及数据库/附件联合恢复；受检生产源码仍冻结。

后续[策略并发验证](BACKUP_RLS_CONCURRENCY_20260921.md)已补两种缺行反例，以及加锁后
另取新快照的保护路径、DDL 等待/超时、普通 DML 与完整恢复。以下保留首次联合恢复的
原始范围；生产工具接入、并发角色管理和 Docker Compose 整体执行仍待完成。

另一个范围缺口是：正式附件使用 OSS，现有脚本只打包 `/data/uploads`。不能用本地
上传目录的归档证明正式 OSS 附件已备份。这里说明的是代码覆盖范围，未读取生产 Bucket
或判断生产当前是否已经启用正式附件功能。

## 已复现的生产代码问题

在全新、无 TCP 监听的实际 PostgreSQL 16.15 中升级当前 0128 迁移，以
`star_oam_backup` 原样执行当前 `backup.sh` 的 SQL 预检，结果为 `ok`。
随后使用相同角色和现行导出参数，`pg_dump` 退出 1：

```text
query would be affected by row-level security policy for table "audit_logs"
```

原角色保持非 superuser、无 BYPASSRLS、无写权限及角色继承。故障不是权限预检不执行，
而是预检未验证导出命令与现有 RLS 的配合。失败没有生成完成的联合备份包。

PostgreSQL 16 [官方 pg_dump 文档](https://www.postgresql.org/docs/16/app-pgdump.html)
说明默认导出会设置 `row_security=off`；无绕过权限时遇到 RLS 会报错。
`--enable-row-security` 只导出角色可见的行，因此**不能只加这个参数就认为备份完整**。

## 本地候选方案及结果

候选代码目前只在被忽略的 artifacts 中，不是已发布运维工具：

1. 保留原只读角色预检，再核对全部适用的备份 SELECT 策略：必须显式、仅授予备份角色、
   permissive 且 `USING (true)`；没有策略、过滤表达式、限制策略或未覆盖外部表时拒绝。
2. 当前 **23 张 RLS 表 / 23 条备份策略**通过；将只读备份角色的完整表摘要与迁移角色
   摘要比对一致，其中 17 张 RLS 表非空，包含 4 条 `audit_logs`。
3. 在同一新源库中再次复现现行命令失败，再使用候选参数导出成功。没有给备份角色
   BYPASSRLS、所有权、继承或写权限，没有关闭任何表的 RLS。
4. 在临时事务中增加一条会过滤所有行的限制策略，候选预检明确拒绝；事务回滚后原策略
   保持。该验证是顺序拒绝场景，**尚未验证预检与导出之间的策略并发变更**。
5. 文件清单与 `pg_dump --snapshot` 使用同一个导出的数据库快照；按准确 storage_key、
   文件 ID、SHA256、长度及对象元数据核对并归档内容。缺失或损坏对象时不发布完成备份包。
6. 从联合包恢复到另一套全新数据库和另一份对象目录，核对数据、对象、库存重建及权限。

| 验证项 | 结果 |
| --- | --- |
| 实际数据库 | PostgreSQL 16.15，当前 0128 迁移；新源库与恢复库的系统标识不同 |
| 数据恢复 | 206 张 public 表、572 行完整内容摘要一致，包括空表；无 public 序列 |
| 对象恢复 | 3 个 available 文件的内容、SHA256、长度和元数据一致 |
| 未完成上传 | 1 个 pending 文件没有被当作完成对象，也不能下载 |
| 文件范围 | 来源配置审核证据、个人申领附件、盘点证据；有总部管理员和独立技师上传者 |
| 文件读取权限 | 3 个正确上传者的下载意图通过；其他上传者被拒；检查事务回滚后原表摘要保持 |
| 库存/SN | 3 条余额及 1 条非空 SN 位置从不可变流水重建一致 |
| 审计链 | authorization 10、inventory 52、material_request 2，合计 64 条事件逐项验证至链起点 |
| 负面场景 | 缺失对象、损坏对象及过滤型 RLS 策略均被拒绝 |
| 现行上传目录范围 | 合成 `/data/uploads` 归档未包含任何正式对象，3 个 available storage_key 均缺失 |
| 恢复后数据库权限 | API 生产边界及 Edge 数据库边界检查通过 |
| 临时资源 | 本批 4 个源库、1 个恢复库全部停止、退出 0；失败样本及日志保留 |

本机小样本从解包到新库恢复、完整内容和权限核对耗时 **3.436 秒**。这不是生产 RTO；
没有证明生产 RPO、远端对象下载性能或故障切换时间。

OSS 传输在本次用本地文件支持的测试替身替代，URL 仅为 `.invalid` 测试值，未发出真实
OSS/HTTPS 请求。文件内容取自仓库中的 PNG 测试样本；正式文件上传意图、完成、审核、
权限、数据库约束与审计服务都实际执行。没有公开业务附件或放开正式文件功能开关。

## 失败记录

前三轮均明确退出 1，其源库已停止，没有将它们写作通过：

1. 审核命令的旧夹具仍使用占位文件摘要，真实文件校验拒绝。只在本地夹具中把 5 个审核
   命令构造器改为读取真实文件 SHA256，未修改或绕过生产校验。
2. 误选管理员作为个人申领附件上传者，`file_purpose_forbidden` 拒绝。改用独立技师夹具，
   没有扩大管理员权限。
3. 正确上传后，现行 `pg_dump` 命令被 RLS 拒绝，发现本文件开头的真实备份代码缺口。

第四轮使用候选完整读取预检和 RLS 参数，完整联合恢复通过；同轮仍准确复现旧命令失败。

## 证据与接续

目录：`cloud_oam/artifacts/database-objects-restore-20260921/`。

- 本地验证代码：`run_database_objects_drill.py`、`object_fixture.py`、`backup_rls_fixture_guard.py`。
- 终端结果：工具会话 `74775` 退出 0，`drill-fourth.log`、`result.json`。
- 最终源库：`source-checks/run-8ixtypvn/`；恢复库：`restore-checks/run-uptd0hqy/`。
- 源库证据：`backup-role-result.json`、`original-backup-failure.json`、`pg-dump-original.log`、
  `backup-rls-completeness.json`、`backup-rls-negative.json`、`file-catalog.json`、
  `object-negative-checks.json`、`legacy-uploads-gap.json`、联合备份包及目标表摘要。
- 恢复库证据：`logical-restore.log`、`restored-facts.json`、`restored-semantics.json`、
  `restored-file-access.json`、实例关闭记录。
- 失败证据：`first-attempt/`、`second-attempt/`、`third-attempt/` 和各自原源库目录。

最终结果 SHA256：`6e88c59e312bfa647b5a0de4639542c6f091d1a68d8d35465ce5414d02eb763b`。
源库与恢复库表摘要均为：`77ca6c476bea2a64fc6d38558c1a29125bd2929260e865ec8993b7a7d1bb9a9c`。

原定策略并发验证已按上述后续记录完成。下一步在原静态回归取得终端结果后，把完整
预检、持锁与新快照生命周期纳入生产备份脚本和正式测试；不能只复制一个导出参数。
正式 OSS 对象备份/保留、快照与对象一致性、密钥恢复、生产 RPO/RTO 及真实文件读取仍待落地。
现行脚本的 Docker Compose 完整执行未在本次测试，不能将原生命令结果扩大到该范围。

原静态回归后来已因确认失败而主动结束，退出 2，源码稳定但未全量通过；7.74 的修复
在监督进程结束后实施。全部未提交改动保留，待批提交例外与 Edge 恢复答复不变；
公开目录仍 pending/0。本批未提交、推送或部署。
