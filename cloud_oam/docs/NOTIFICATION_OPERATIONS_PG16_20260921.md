# 7.76 通知扩展权限阻断与原生 PG16 并发候选（2026-09-21）

**本页保留 7.76 原始失败和隔离候选证据；后续 0129 修复已落入源码。**
当前结论见[7.77 修复交接](NOTIFICATION_EXPANSION_STATUS_20260921.md)，准确会话只在[续开发入口](CONTINUE_DEVELOPMENT.md)维护。
本批没有发送外部消息、运行供应商 adapter、改变业务系统或生产数据库。

## 当前源码的实际阻断 N0

全新自有 PG16.15 实例升级到当前 0128，真实 API/Edge 安全检查及非零应用样本通过。
以正式总部管理员和准确人员渠道记录通知，调用原 `expand_notification_event()` 时失败：

```text
SQLSTATE 42501: permission denied for table notification_events
SELECT ... FROM notification_events WHERE id = ... FOR UPDATE
```

0107 只给 `notification_events`、`notification_recipients` SELECT/INSERT；0108 给
`notification_deliveries` 指定状态列 UPDATE。当前运行安全清单同样没有事件/收件人 UPDATE。
原扩展器既对两张表执行 FOR UPDATE，又修改事件 status，权限与实际执行不一致。
[PG16 官方权限说明](https://www.postgresql.org/docs/16/ddl-priv.html)确认锁行查询还要求至少一列
UPDATE 权限；本项目的阻断结论来自实际 API-role 调用，未仅依赖文字分析。

不能通过改用 migrator、表级 UPDATE 或 owner 权限来让 worker 运行；普通接口与后台
worker 仍必须使用原最小权限身份，事件正文和原目标坐标必须保留。

## 已执行的隔离候选

候选只保存在 artifacts，未修改正式 Python、迁移、权限清单或开关：

1. 在全新演练库中仅授予 `notification_events.status` 列 UPDATE；确认表级 UPDATE 为 false，
   status 列为 true，收件人任意列 UPDATE 仍为 false。
2. 候选扩展函数保留事件 FOR UPDATE，收件人改为普通只读查询；接收人坐标对于 API 是追加事实，
   不通过授予 UPDATE 来满足一次只读查询。事件锁、唯一 delivery/recipient 约束和原目标事实保留。
3. 所有重试/worker/审计业务函数使用原生产函数，真实 `star_oam_api`、真实 FormalPrincipal、
   独立数据库连接及事务。已配置的测试身份策略使用合成 HMAC/app 坐标，走原正式身份解析器。
4. 状态复查将候选一列加入**演练进程内**的预期 ACL，再运行其余原检查，Edge 检查保持原样。
   这不是原生产权限清单通过；候选 API 权限确实比当前多一列，不能称为完全未变。

正式落地仍需前向迁移、readiness/运行清单同步，以及事件状态不可倒退、取消后不可复活和
事件正文不可改写的数据库证明；当前候选不能替代这些要求。

## 实际 PG16 结果

| 场景 | 证据 |
| --- | --- |
| 同一操作者、同键 | 实际审计头锁等待；后到请求回放，只有一个重排事实和一条审计 |
| 两个操作者、同键 | 实际锁等待；后到请求 409 幂等冲突，不重复重排 |
| 两个操作者、不同键 | 实际锁等待；后到请求 409 当前不可重试，只有一次重排 |
| worker 与重试 | 未提交重排不会被领取；worker 只领取第 2 次尝试，竞争重试拒绝；原命令回放不把 sending 改回 queued |
| 未知供应商结果 | 无响应证据时明确拒绝重试，不改投递、尝试或审计 |
| 真实审计 INSERT 失败 | 测试专用 PG trigger 注入 23514；投递修改、尝试和审计链头全部回滚，之后移除注入 trigger/function |

等待证明来自 `pg_blocking_pids` 和 `pg_locks`，不是靠 sleep 假定并发。
四条成功重排审计通过正式哈希链验证，未保存原始幂等键。
比较全部 206 张 public 表，排除预期可变的 6 张通知表和 2 张审计表后，**198 张业务表
完整摘要保持**，包含非零库存流水、余额、盘点与独立对账事实。通知行为没有推进库存。

供应商输入均为显式合成拒绝/未知证据，**外部供应商调用 0 次**。HTTP 503 本身是否证明
未受理仍未验证，现有仅依据 429/5xx 的重试分类还需供应商语义补齐。回调验签、真实发送、
送达/已读和超时回读不属于本次通过范围，N1—N5 不能全部关闭。

## 原始失败、资源和文件

目录：`cloud_oam/artifacts/notification-operations-native-20260921/`。

- 首次导入没有先初始化测试设置，生产配置校验拒绝；退出 1、未创建数据库。
  `attempt-1-import/` 原样保存脚本和输出。修正为先加载测试设置，隔离全局 SQLite 为内存库，
  专项实际连接全部由新建 PG16 工厂提供；迁移子进程使用独立生产迁移配置。
- 第二次会话 `27049` 退出 1，原生产扩展器触发上述 42501。
  `attempt-2-native-acl/`、`drill-second.log` 和 `failure.json` 保留。
- 第三次候选会话 `5755` 退出 0，`drill-third-candidate.log`、`candidate-result.json`、
  `candidate-inputs.json`、`candidate_notification_expansion.py` 为准确候选证据。
- 失败源库 `native-checks/run-i0qxvvyz/`，候选源库 `native-checks/run-c65wjo02/`；
  两个全新实例已停止，均无 TCP 监听，没有重启原数据库目录。
- 候选源库含 `candidate-acl.json`、`business-before.json`、`business-after.json`、`cases.json`
  和正式样本/迁移记录；最终汇总在 `verification.json`。

## 后续交付

先按上述最小权限候选准备前向迁移及事件状态/不可变正文约束，并纳入实际 PG16 门禁。
补 event/recipient 扩展竞争与权限撤销等需要的验证，再复跑通知运维并发专项和当前完整门禁。
受检源码冻结结束或有准确、记录完整的受控停止后才落地，不能在运行中修改源码。

本次未提交、推送或部署；B3 持续日终对账、B5 正式 OSS、真实目录/身份/通知/设备以及
生产灾备/压测/灰度仍未完成。交接文档只收拢现行状态，旧 3,694 行已完整归档。
