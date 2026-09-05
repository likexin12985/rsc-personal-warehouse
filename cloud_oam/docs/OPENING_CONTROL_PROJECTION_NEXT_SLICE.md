# 期初控制库存投影：下一切片的证据与待确认边界

2026-09-05 只读核查，依据 V1.0 与当前源码；不是生产接入、数据库授权或迁移实施记录。
当前只读准备目录始终不宣称控制库存就绪。正式控制投影仍未完成。

## 先证明覆盖范围，不能只看同步成功

V1.0 第 0.3 节要求 OAM 省级余额作为控制总数，第 1.5/4.3 节同时允许完整快照与增量批次。
所以省级控制集完整性是必须证明的，`SyncRun.mode=full` 却不是唯一合法实现，也不能单独证明
省级覆盖。完整的增量重建投影可能满足要求；一期主动限制为完整省级快照须由用户确认。

现有 `edge_sync/oam_edge_sync.py` 的 scope 只有 `all` 或 `warehouse:<code>`。
`--entity inventory` 虽读取仓库列表用于遍历，却只上传 inventory，不上传 warehouse 实体。
因此记录数/hash 正确只能证明该传输批次；零库存仓库不会从库存行显现，零条记录也不能
直接解释成目标省份零库存。下一切片需要明确的来源、区域与预期仓库/位置覆盖集合，
不得凭全国行政表、名称猜测或仅凭现有库存行补造完整范围。

## 来源和权限不能照搬工单投影

- `backend/app/formal_services/opening_stocktake.py` 的 `_prepare_control_evidence` 和
  `inventory_posting.py` 的 `_validate_opening_control_source` 接受既有 `code=oam` 来源。
- `oam_work_order_projection.py` 的工单发布器及配置 schema 则精确限定 `starcharge_oam`。
- `0044` 接收 scope 包含 inventory，但 projector_read/write 的 RLS 并未开放正式库存控制发布；
  不能把允许 edge 接收等同于允许发布，也不能追加任意配置字段或重命名历史来源绕过限制。
- 未发现独立 `InventoryControlSnapshot` ORM 类；已有 `StocktakeControlSnapshotLine` 绑定盘点
  任务，不能冒充新同步的独立来源账本。

正式发布需要独立的来源绑定、最小权限、RLS 和安全清单前向方案，并保留旧 `oam` 历史证据。
禁止修改历史迁移或为库存发布扩大既有工单投影权限。

## 新建准入与历史回放分开

新启动在锁前/锁后分别调用 `_prepare_control_evidence`；启动重放和盘点计数则以
`historical_at=task.control_snapshot_at` 重证冻结图。终态过账还有独立来源 validator。
数据库 `rsc_opening_start_graph_complete_0052` 的 FALSE/TRUE 调用同时覆盖新建闭包和历史验证，
启动审计、outbox 和初轮闭包也调用该函数；不能只改 task INSERT 一处。

现有 projector UPDATE 列权限不包含 `sync_runs.mode`，0044 同时绑定 staged snapshot 的 mode，
但 mode 不是任务或控制 manifest 中封存的历史字段。当前字段值不能被当作启动时 mode 的
独立不可变证明。任何新增 full-only 或新鲜度策略只作用于明确的新准入，不追溯打坏既有合法
盘点的计数、重放、复核和过账，也不能通过改写旧 mode 冒充兼容。

## 建议的最小实现顺序

1. 先确认一期完整覆盖/分批建账复用规则及准入策略。
2. 已新增仅使用隔离假数据的来源、范围与覆盖一致性校验器，不发布正式 SyncRun，不改库存或
   开启启动。契约、反例测试和验证状态见 `INVENTORY_CONTROL_EVIDENCE_ACCEPTANCE.md`。
   它不认证来源或目录，现有 edge 也还没有提供新的显式分页证据；不能把一致性结果当成就绪。
3. 覆盖证明与来源绑定稳定后，设计前向迁移、受控发布器、控制批次选择及独立真库验收。

校验器至少测试错来源、错公司/组织、跨区域、缺仓、重复仓、缺位置、空库存且覆盖未知、
完整且可证明的零库存覆盖、增量重建的覆盖证明及过旧采集。输入一个“完整”布尔标记不能
替代来源清单和独立覆盖核验。失败不得产生正式投影、库存余额或启动凭据。

待用户确认：一期是否只接受完整省级快照、同一控制批次能否用于不同范围的分阶段建账。
V1.0 的不高于 45 分钟是新鲜度目标，是否作为硬准入、采用采集还是投影时点、准备计划 TTL
仍须明确；不得给历史回放追加实时过期判断。确认数据规则不等于授权连接任何生产系统。
