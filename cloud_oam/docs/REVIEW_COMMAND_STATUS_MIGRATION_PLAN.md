# 复核命令状态持久证据迁移计划

状态：0063 前向迁移与应用侧最小证据链已在本地工作树实现，尚未取得本次提交的
PostgreSQL 16 真库发布门禁或生产放行。本文不是生产放行证据，也不授权访问 OAM、
RSC、Workflow、飞书或生产数据库。

## 目标

为非期初 `region`/`headquarters` 复核保存命令执行时的任务版本，使恢复查询可以区分：

- 命令提交前的 `expected_task_version`；
- 该命令成功推进后的 `resulting_task_version`；
- 当前任务版本（仅当前读侧事实，不得冒充历史结果）。

在这三个坐标没有同时被持久化、审计和触发器重证前，不实现“已复核”恢复状态查询。

## 前向迁移边界

### 0063/0064 Alembic 拓扑决策

当前已放行的 `20260906_0064` 直接以 `20260905_0062` 为
`down_revision`，且不能改写其历史内容。复核命令状态迁移采用
**0063 作为 0064 的线性子迁移**：

```text
... → 20260905_0062 → 20260906_0064 → 20260906_0063 (最终唯一 head)
```

该 revision 编号虽然不是时间排序，但能同时满足：

1. 已在 0064 的环境只需执行 0063；从 0062 全新升级时由 Alembic 先执行
   0064、再执行 0063；两条路径最终都只有一个 head。
2. 0063 升级时 readiness 只允许从 0064 的精确 SHA 前进到 0063 的新
   SHA；0063 降级时严格反向恢复 0064，随后才能执行 0064→0062。这样
   不会让已安装的 0064 锁函数或安全清单在版本表之外残留。
3. 0063 必须只删除自己创建的字段、约束、触发器和函数；不得在降级时
   删除 0064 的锁函数、修改 0064 已放行的历史迁移，或接受未知的
   readiness hash。

不采用“0063 从 0062 分叉、0065 合并”的拓扑。该方案会产生两个 head，
并且从现有 0064 升级到合并 head 时需要补跑兄弟分支；若从 merge head
部分降级到任一父节点，另一分支的函数/触发器可能留在数据库而版本表已
不再声明它，难以满足本项目的精确 owner/ACL/hash 回滚门禁。即使通过
0065 条件式推进 readiness，也会把 0063 的 schema capability 与 0064
的锁 capability 拆成两个非线性状态，增加运维和故障恢复风险。

拓扑验收必须覆盖：

- Alembic `get_heads()` 只有 `20260906_0063`；0063 的
  `down_revision == 20260906_0064`；
- 当前 0064 数据库执行 `upgrade head` 只补 0063；0062 数据库执行
  `upgrade head` 按 0064→0063 顺序执行；
- 0063 升级/降级分别验证 readiness SHA、0064 锁函数 owner/ACL/
  SECURITY 属性，且禁止把旧历史行误判成新版本；
- 只允许在没有 0063 新复核行、触发器可安全移除时执行
  `downgrade 0064`，再按已有 0064 门禁执行 `downgrade 0062`。

新增 0063 前向迁移，不修改 0032、0058 或其他历史迁移。`stocktake_reviews` 增加：

- `expected_task_version BIGINT`；
- `resulting_task_version BIGINT`；
- PostgreSQL 检查明确要求“两个字段同时 NULL，或两个字段同时 NOT NULL 且
  `resulting_task_version = expected_task_version + 1`”，并拒绝负值及任一半空对；
  不能依赖 PostgreSQL CHECK 的三值逻辑隐式拒绝 NULL。

为兼容既有历史行，旧行允许保持 NULL；迁移不得猜测或回填历史版本。0063 后新写入的非期初复核由数据库触发器强制两个字段非空且连续，历史 NULL 行在恢复查询中一律 `service_unavailable`/阻塞。

迁移必须锁定 `alembic_version`、`stocktake_reviews`、`stocktake_review_items`、`stocktake_tasks`、
`state_transition_events`、`audit_events` 及既有复核 owner graph；升级前验证旧目录，升级后验证新函数体 SHA256、所有者、ACL、SECURITY 属性、触发器和运行时 readiness。降级仅允许在没有 0063 新行且新触发器可安全移除时执行。

## 应用与证据链改动

当前 `stocktake_review._write_review` 已在任务版本递增前捕获 expected，并在同一事务内写入
resulting；StateTransitionEvent metadata、review audit `after_jsonb`、幂等重放校验和安全
触发器同时包含并核对两个值。`stocktake_query` 现对非期初任务的历史复核重新加载状态迁移与
审计摘要，校验 stage、decision、操作人、差异集、manifest、schema v2 和版本对；缺失或篡改
fail-closed。该查询改动是 payload-level 的历史证据复核，不宣称已交付独立 review
command-status endpoint 的完整 owner-graph + audit-chain 读端；审计链哈希证明仍由写入/重放
服务负责，后续独立读端仍是未完成项。

后续只读 endpoint 才能返回历史版本；它仍需先锁既有 review owner graph、当前 actor/principal 和 audit chain，精确按 `X-Request-ID` 查找，拒绝 body、Idempotency-Key 和重复/错阶段审计，且绝不提交、重放或补写。

## 必测范围

1. 静态：迁移 revision/head、字段/约束、模型映射、写入点、审计载荷、函数指纹与安全清单。
2. PostgreSQL 16 真库：升级/回滚目录检查；新复核版本连续性；历史 NULL 阻塞；触发器、ACL、owner、SECURITY 属性和 hash 变更检测。
3. 服务回归：region 与 headquarters 成功写入、幂等重放、版本错位、审计篡改、item/manifest 缺失、actor 授权变化全部 fail-closed。
4. 客户端：只有收到完整历史事实才清除持久恢复哨兵；`not_observed`、历史 NULL 或 503 均保持阻塞，不换 key 重发。

## 依赖与验收

必须先通过准确 SHA 的 PostgreSQL 16 migration gate，再实现独立 review/recount/disposition/close
的状态查询和 Web/小程序跨重启恢复。本地门禁尚未替代远程 PG16 真库证据；审批、分配、占用、
出库、发货、物流签收、OAM 收货、RSC/个人仓入库、通知送达、对账同步仍分别建模、分别验收。

## 2026-09-06 本地实现边界（提交前）

- Alembic 拓扑为 `0062 → 0064 → 0063`，最终唯一 head 为 `20260906_0063`；未改写历史
  0047/0058/0064 迁移。
- 0063 SQLite/静态回归覆盖双 NULL、双非空连续、双向半空、错位和安全降级阻断；PG16
  门禁脚本已加入双向半空真库断言，并将 0047 启动事实的旧降级阻断与 0063 复核事实隔离。
- 当前 generic `stocktake_query` 只适用于 `TASK_TYPES` 非期初任务；没有新增或宣称独立
  review command-status 路由。
- 本节记录的是工作树实现状态；完整静态套件、提交 SHA、远端状态和 PG16 GitHub run 必须
  重新读取后再补入交接文档，不能引用旧 run 代替。
