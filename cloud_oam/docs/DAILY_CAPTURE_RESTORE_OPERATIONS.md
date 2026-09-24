# 日终采集账号的备份恢复流程

此流程只适用于新系统的独立恢复目标，不读取或写入 OAM、RSC、飞书或其他业务系统。
完整灾备操作须按正式基线完成授权、演练和书面验收。本工具只处理两个新增采集账号，
不把账号启用或本地样本恢复称为生产恢复验收。

## 目标库准备

先核对当前受审核备份的 SHA256、归档内容校验及 SQL 版本；保持来源库、备份、恢复库隔离。
不得使用 `pg_dumpall` 搬运旧角色或旧密码，不得删除现有业务库后原地恢复。
已有六个基础数据库角色、目标库所有者及集群级 PUBLIC 权限，继续按基础部署边界准备。
其中 `star_oam_edge` 保持 NOLOGIN；五个基础登录账号使用独立于源库的新凭证。
本工具不会创建、提权、改密或修补这六个基础角色。当前本地验证通过自有实例 helper
准备基础环境；托管 RDS 管理员权限及实际生产初始化还需独立验收。

新增账号的准备阶段要求：

- 直接 bootstrap superuser、PostgreSQL 16、明确且匹配的目标数据库。
- 目标数据库无业务表、视图、序列、函数或自定义类型，没有额外业务 schema/扩展。
- 两个固定采集账号都不存在，或均为同一个恢复请求准备的禁用账号。
- 没有危险 PUBLIC、其他数据库或 schema 权限；发现异常则拒绝，不自动修补。

提供 `OAM_DAILY_CAPTURE_BOOTSTRAP_URL`，使用同一恢复请求 UUID 和同一已审核备份摘要：

```sh
python scripts/configure_daily_capture_roles.py --database '<目标数据库名>' \
  --mode prepare-restore --restore-id '<恢复请求UUID>' --backup-sha256 '<备份SHA256>'
python scripts/configure_daily_capture_roles.py --database '<目标数据库名>' \
  --mode check-restore --restore-id '<恢复请求UUID>' --backup-sha256 '<备份SHA256>'
```

准备会在一个事务内建立 `rsc_control_capture` 和 `rsc_reconciliation_capture`，
均为 NOLOGIN、无密码、无成员关系，只配置当前库连接、public 使用权及只读默认值。
角色注释绑定恢复请求、备份摘要、集群 system identifier、数据库 OID/名称及 0131 版本。
如果恢复的是 0130 备份，须先使用迁移账号升级到当前 0131，再检查或启用采集账号；
不能改写旧备份里的版本号来通过检查。本地合成旧备份升级路径见[8.05 查询验证](DAILY_RECONCILIATION_QUERIES_20260921.md)。
恢复账号绑定错误、只剩一个账号、注释变化或出现意外权限均拒绝。
同一空库的并发/重复准备保持幂等，不覆盖已有账号。

## 加载与核验

在独立目标库加载已核验的 SQL，开启 `ON_ERROR_STOP` 和单一事务。
PG16 导出可能只调整现有 public schema，也可能包含 CREATE SCHEMA；必须按实际 SQL
选择初始化方式。不能一律先删除 public。禁止为了恢复而去掉 ACL、RLS、所有者或业务守卫。
SQL 导入失败后精确检查目标库及恢复请求状态，不盲目重放，不把部分导入视为完成。

恢复后先逐表比较完整内容和数量，核对非空库存流水、余额重建、SN、截止记录、审计链、
附件目录和实际对象。备份 SHA256 只是本工具的请求绑定，工具没有据此自动证明导入内容正确。
对象内容、外部密钥、日志归档、PITR 和 RPO/RTO 继续由对应灾备检查负责。

`check-restore` 的阶段：

| 阶段 | 含义 |
| --- | --- |
| `absent` | 空目标库尚未准备两个采集账号 |
| `prepared` | 空目标库账号准备完成，禁用且无密码 |
| `restored_disabled` | 0131 结构和两种采集权限符合契约，账号继续禁用且无密码 |
| `active` | 对应恢复请求的采集账号已启用，权限和 SCRAM 配置通过检查 |

这些阶段均不表示业务数据验收。结果中的 `business_data_verified` 始终为 false。
API 普通运行检查仍拒绝禁用的采集账号，不能在恢复期间提前启动业务服务。

## 新凭证启用

数据及附件已完成独立核验后，提供两种新的、互不相同的至少 32 字符密码：
`OAM_DB_CONTROL_CAPTURE_PASSWORD`、`OAM_DB_LEDGER_CAPTURE_PASSWORD`。沿用同一个恢复请求和摘要：

```sh
python scripts/configure_daily_capture_roles.py --database '<目标数据库名>' \
  --mode activate-restore --restore-id '<恢复请求UUID>' --backup-sha256 '<备份SHA256>'
python scripts/configure_daily_capture_roles.py --database '<目标数据库名>' \
  --mode check-restore --restore-id '<恢复请求UUID>' --backup-sha256 '<备份SHA256>'
```

启用重新检查原请求、0131、角色和精确表/列/函数/序列/数据库/schema/RLS 权限，并在
同一事务写入 SCRAM 校验值、启用 LOGIN 和完成标记。检查失败不会启用账号或部分改密。
本工具不从源库取得密码，运维必须提供新凭证；正确配置后的重复启用不会再次轮换密码。

退出码 0 是本次结果确认，2 是本次未提交，3 是提交结果未知。收到 3 后，必须使用
原数据库、恢复 UUID、备份摘要执行 `check-restore`，核对阶段和数据库证据再决定下一步。
不得凭超时或没有回执认定失败。账号启用后，还须以实际新凭证逐项核验 API、边缘采集、
投影器、备份与日终读取的完整边界，再启动对应服务；不能略过服务启动检查。

旧密码回收、传输 TLS/pg_hba、受限管理员、实际容器工件、真实云端恢复及放量仍须验收。
当前实现和最新测试结果以[续开发入口](CONTINUE_DEVELOPMENT.md)为准。
