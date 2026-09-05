# 复核命令状态持久证据迁移计划

状态：设计冻结，尚未实现。本文不是生产放行证据，也不授权访问 OAM、RSC、Workflow、飞书或生产数据库。

## 目标

为非期初 `region`/`headquarters` 复核保存命令执行时的任务版本，使恢复查询可以区分：

- 命令提交前的 `expected_task_version`；
- 该命令成功推进后的 `resulting_task_version`；
- 当前任务版本（仅当前读侧事实，不得冒充历史结果）。

在这三个坐标没有同时被持久化、审计和触发器重证前，不实现“已复核”恢复状态查询。

## 前向迁移边界

新增 0063 前向迁移，不修改 0032、0058 或其他历史迁移。`stocktake_reviews` 增加：

- `expected_task_version BIGINT`；
- `resulting_task_version BIGINT`；
- PostgreSQL 检查 `resulting_task_version = expected_task_version + 1`，并拒绝负值。

为兼容既有历史行，旧行允许保持 NULL；迁移不得猜测或回填历史版本。0063 后新写入的非期初复核由数据库触发器强制两个字段非空且连续，历史 NULL 行在恢复查询中一律 `service_unavailable`/阻塞。

迁移必须锁定 `alembic_version`、`stocktake_reviews`、`stocktake_review_items`、`stocktake_tasks`、
`state_transition_events`、`audit_events` 及既有复核 owner graph；升级前验证旧目录，升级后验证新函数体 SHA256、所有者、ACL、SECURITY 属性、触发器和运行时 readiness。降级仅允许在没有 0063 新行且新触发器可安全移除时执行。

## 应用与证据链改动

`stocktake_review._write_review` 必须在任务版本递增前捕获 expected，在同一事务内写入 resulting；StateTransitionEvent metadata、review audit `after_jsonb`、幂等重放校验和安全触发器必须同时包含并核对两个值。任何一个载荷缺失、与任务版本不一致、审计摘要不一致或复核 item/manifest 不完整，都必须 fail-closed。

后续只读 endpoint 才能返回历史版本；它仍需先锁既有 review owner graph、当前 actor/principal 和 audit chain，精确按 `X-Request-ID` 查找，拒绝 body、Idempotency-Key 和重复/错阶段审计，且绝不提交、重放或补写。

## 必测范围

1. 静态：迁移 revision/head、字段/约束、模型映射、写入点、审计载荷、函数指纹与安全清单。
2. PostgreSQL 16 真库：升级/回滚目录检查；新复核版本连续性；历史 NULL 阻塞；触发器、ACL、owner、SECURITY 属性和 hash 变更检测。
3. 服务回归：region 与 headquarters 成功写入、幂等重放、版本错位、审计篡改、item/manifest 缺失、actor 授权变化全部 fail-closed。
4. 客户端：只有收到完整历史事实才清除持久恢复哨兵；`not_observed`、历史 NULL 或 503 均保持阻塞，不换 key 重发。

## 依赖与验收

必须先通过准确 SHA 的 PostgreSQL 16 migration gate，再实现 review/recount/disposition/close 的状态查询和 Web/小程序跨重启恢复。该迁移完成前，当前已交付的过账状态查询仍是唯一新增的非期初命令恢复切片；审批、分配、占用、出库、发货、物流签收、OAM 收货、RSC/个人仓入库、通知送达、对账同步仍分别建模、分别验收。
