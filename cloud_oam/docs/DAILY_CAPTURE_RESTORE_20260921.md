# 新增日终账号后的数据库恢复（8.04）

2026-09-21，本地未提交候选。前序[角色配置与权限共存](DAILY_CAPTURE_ROLES_20260921.md)
已完成；本批补齐新增账号的恢复前准备、阶段检查和恢复后新凭证启用，
详细操作见[恢复说明](DAILY_CAPTURE_RESTORE_OPERATIONS.md)。

## 正式改动

- 现有配置 CLI 增加 prepare-restore、check-restore、activate-restore。
- 准备仅接受明确空目标库，建立两个 NOLOGIN/无密码账号。角色注释绑定备份摘要、恢复 UUID、
  集群 system identifier、数据库 OID/名称和 0130 版本；不接受部分账号或错配请求。
- SQL 加载完成后保持账号禁用。启用重新检查全部采集权限和原请求，在同一事务设置新 SCRAM
  密码、LOGIN 和完成标记。重复准备和启用不重置密码，提交结果未知须先精确检查。
- API 与普通读取的 LOGIN 要求保持。密码检查复用一个入口，并修正非字符串值可能导致
  未分类 TypeError 的问题。现有六个基础账号初始化不在本 CLI 中重写。
- 0130 结构及业务守卫没有改动，没有新增表或业务写入路径。

## 最终证据

| 项目 | 已核验结果 |
| --- | --- |
| 聚焦测试 | 会话 74442 退出 0，**420 passed** |
| 真实 PG16 | 会话 96070 退出 0，**87 个场景通过**，其中新增恢复 16 项 |
| 当前数据库导出 | 正式 deployment/backup/backup_database.sh；持锁与快照校验均执行，包含两个新账号的 ACL/RLS |
| 独立目标恢复 | **208 表 / 934 行**全部内容摘要一致；public 序列为 0，不宣称验证了非空序列恢复 |
| 库存与审计 | 四份截止对应四份审计，六张库存表不变；启用前后全库摘要不变 |
| 登录凭证 | 八个数据库角色中七个登录账号使用与源库不同的新密码；旧密码全部拒绝，star_oam_edge 保持 NOLOGIN |
| 运行权限 | 恢复后实际新凭证的 API、edge_inbox、star_oam_projector 及两个采集账号通过完整现行检查 |
| 失败处理 | 非空目标、危险 PUBLIC 权限、错请求/摘要、未加载、弱密码、写权限/继承/过滤漂移均拒绝；并发准备只创建一次 |
| 事务回滚 | SQL 末尾 division by zero 使整份恢复回滚；启用事务主动回滚后两账号仍禁用且无密码 |
| 资源 | 4 个全新 PostgreSQL 16.15 实例均停止、服务器退出 0，无 postmaster PID 或 socket 残留 |

冻结清单含 **1,417 个非 Markdown 输入**，较上批多两个新文件并将原有根 .gitignore 纳入摘要。
最终清单 SHA256：`bd67741a282dea376962a1303a3c504c472ca2be995be3adbab8000f768cb9a8`。
最终验证期间所有输入保持不变，当前冻结已结束。

证据目录为 `cloud_oam/artifacts/daily-capture-restore-20260921/`。
`verification.json` SHA256：`79e82c6868263d5fe79c47e1b5690504454b6e0970de3d4cbf4726337390105a`。
`verify_integration.py` 核对终态、文件摘要、全库内容、失败注入和实例停止记录。
本批 DB 快照归档 SHA256 为 `4e45833d6caa88c48ec19c24b647744d50a618cc44a4d418524760fa34d9ef00`，
恢复 SQL SHA256 为 `5bc5d8f99eebfc27a4dead24d5365d7fe6b07ee6208bc6cf1d480c2d8f1b770a`。
这些都是本地合成样本，不能上传为真实业务数据或冒充线上备份。

## 保留的失败

首轮聚焦会话 21264 退出 2：pytest 参数名 request 与保留 fixture 冲突，改名后通过。
首轮原生 37888 退出 1：测试一律删除 public，而当前 pg_dump 仅调整 schema，造成正常导入失败。
改为检查 SQL 是否含 CREATE SCHEMA，再选择目标初始化方式；业务迁移与导出没有放宽。
第一次的“恢复失败回滚”在 schema 错误处提前退出，不能当成完整注入证据；已显式标注无效。
最终重跑确认实际执行到 SQL 末尾的除零故障，整份 schema/数据回滚，才计入通过。
原始日志、样本、终端状态和两个失败实例保存在 attempt-01-schema-fixture 与 native-checks。

## 尚未覆盖的上线门槛

- 本批使用正式数据库导出与角色 CLI，在原生 PG16 中恢复；没有重建当前 API 镜像，
  没有运行当前八角色的 Docker/Compose 联合附件恢复。8.01 容器证据属于旧候选。
- 不加载旧全局角色/密码；基础六角色通过原有自有实例 helper 准备。受审计的生产初始化、
  托管数据库受限管理员、TLS/pg_hba、实际部署密钥、OSS 对象与异地/PITR/RPO/RTO 仍待验收。
- 提交后精确检查和不改密重放已验证；没有注入真实网络断开、远程提交回执丢失或生产故障。
- CLI 的 business_data_verified 始终为 false；完整业务数据、附件和生产放量必须单独验收。
- 继续日终区域查询、差异解释/审核及进程部署；当前完整静态、GitHub PG16/Client、真实三天
  对账、渠道实发和设备 UAT 仍缺。未提交、推送或部署。
