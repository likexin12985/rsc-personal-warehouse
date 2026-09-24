# 日终采集数据库角色配置

本页适用于经审核的新系统数据库，不是 OAM/RSC 数据库操作指令。当前工具要求
PostgreSQL 16、0131 迁移和直接连接的 bootstrap superuser；不接受 SET ROLE 借用身份。
托管数据库受限管理员的配置能力、真实网络与上线操作仍需独立验收。

## 两个固定服务账号

| 账号 | 用途 | 权限 |
| --- | --- | --- |
| `rsc_control_capture` | 已发布控制来源、原采集与审核证据读取 | 固定 27 表 SELECT |
| `rsc_reconciliation_capture` | 截止游标、原始流水、账户和位置读取 | 固定 6 表 SELECT |

完整表清单见[唯一角色契约](../backend/app/daily_reconciliation/capture_role_contract.py)。
这两个账号属于内部后台任务，不是新增业务角色，也不供前端或终端用户登录。
不授予写入、DDL、函数执行、序列访问、授权转授、其他数据库访问或角色成员关系。
默认事务只读；即使客户端改为读写事务，也没有业务写入权限。RLS 表使用固定的
仅面向该角色、SELECT、`USING (true)` 策略，避免把过滤后的缺行误认为完整库存。

## 审核与执行

在受控的运维环境中提供以下环境变量，不放入 API/前端配置、命令行参数或仓库：

- `OAM_DAILY_CAPTURE_BOOTSTRAP_URL`：明确目标库的 `postgresql+psycopg` 连接配置。
- `OAM_DB_CONTROL_CAPTURE_PASSWORD`：控制来源读取密码。
- `OAM_DB_LEDGER_CAPTURE_PASSWORD`：原始账本读取密码。

两个密码必须不同、至少 32 字符且不是占位值。工具通过 libpq 生成 SCRAM 校验值，
SQL 不携带传入的明文密码，结果和错误输出不包含连接配置或密码。

从 `cloud_oam` 目录运行，明确填写已审核的数据库名：

```sh
python scripts/configure_daily_capture_roles.py --database '<已审核数据库名>' --mode preview
python scripts/configure_daily_capture_roles.py --database '<已审核数据库名>' --mode apply
python scripts/configure_daily_capture_roles.py --database '<已审核数据库名>' --mode check
```

`preview` 展示固定账号、表和策略，无角色变更。`apply` 只在两个账号均不存在时
原子创建并授权；先设置 NOLOGIN，完成权限与策略后才在同一事务中启用 LOGIN。
最终校验失败则整笔回滚。配置已经正确时再次执行不会改密码或重写权限。
部分账号、错误权限、过滤策略、继承关系等均拒绝，不自动修补或删除历史配置。

退出码 0 表示本次结果已确认；2 表示未提交配置；3 表示提交结果未知。
结果未知后先对同一数据库执行 `check` 并核对当前角色，不能盲目重放 apply。
密码轮换、账号撤销和异常旧配置处置需要独立审核流程，本工具不代办。

## 运行与恢复边界

API、边缘采集/投影校验和日终读取共同核对角色属性、成员关系、默认设置、数据库、
schema、表/列/序列/函数权限和 RLS。两个角色都不存在时视为该能力未配置；
只存在一个、任一账号禁用或权限异常时拒绝通过。业务授权和区域范围仍由正式业务
接口检查，不能把数据库账号配置成功当作日终任务已执行。

schema 迁移继续使用独立 migrator；两种采集连接及内部 owner 连接都不得从请求中
接收或传给网页。进程配置与部署入口、区域查询/解释接口仍按
[当前交接](CONTINUE_DEVELOPMENT.md)推进。

启用后，新的逻辑备份将引用这两个角色及策略。8.04 已增加恢复前准备和恢复后新凭证启用，
见[恢复流程](DAILY_CAPTURE_RESTORE_OPERATIONS.md)及[原生 PG16 恢复证据](DAILY_CAPTURE_RESTORE_20260921.md)。
原生八角色恢复已核验；当前容器联合附件恢复和真实生产灾备仍需补验，旧 B5 容器样本不能直接放行。
