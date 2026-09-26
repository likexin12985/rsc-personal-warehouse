# 正式报损、报废缺口与下一实现切片

审计日期：2026-09-26。工作树：`06f6/oam`，分支 `codex/notification-delivery-worker`；当前 HEAD `9176efc079e979ee69cda67d6a9a0d6e5e29b47d`，审计包含工作树中的未提交代码。

范围：依据正式 V1.0 基线，对后端、迁移、权限及客户端做只读审计；关键单据约束和 SN 拒绝路径已复核。本文为后续实施方案，不是已实现或上线验收证据。模型与接口命名属于待实现设计，落地时仍须完成迁移及业务门禁。以下文件位置均相对于 `cloud_oam/`。

## 结论

**报损与报废尚未正式实现。** 已有成色 `scrapped`、状态 `scrap_pending`、流水类型 `scrap` 只是底层枚举；现有正式库存作业实际上仅支持工单回收件退回。不存在“报损冻结 → 区域核实 → 总部终审 → 五种处置 → 受控冲销”的完整事实链。

本批导入可靠性修复和当前冻结候选验证完成后，下一切片应补完整报损闭环，包含 SN 报废与反向恢复、审批退回来源适配；不能只发布非 SN 报损、仅恢复可用、或一个报废按钮而宣称基线完成。

## 证据与复用边界

| 核查点 | 当前源码证据 | 实现影响 |
|---|---|---|
| 共用作业单头目前只允许退回 | `backend/app/stock_operation_models.py:26` 的 CHECK 是 `operation_type = 'return' AND status = 'submitted'`；34–38 行强制工单、目标/在途库位、接收保管绑定 | 基线 §3.9 的共用单头需按业务类型演进；不能给报损伪造工单和运输位置 |
| 明细是回收退回专用 | 同文件 56–70 行强制 `source_recovery_line_id`、待退回账户、成色 used/damaged | 期初盘点或正常收货的库存也可能报损；必须允许精确普通库存来源，同时保留旧回收来源约束 |
| 当前退回不是审批流 | `backend/app/formal_services/stock_return_commands.py:77`、109–135：提交直接写 reserve 流水并形成 submitted 不可变事实；139–172 为发出前整单取消 | 可借用原子事实/审核/Outbox 模式，不可拿它充当区域及总部审批 |
| 退回来源限制不可绕过 | `backend/app/formal_services/work_order_return_sources.py:50`–88 只接受工单 pending_return 回收义务；`backend/app/formal_services/stock_return_plan.py:57`–84 调用该预检；22–43 校验真实区域仓和保管人 | 报损的“退回”结果需新增来源适配和精确处置 FK，不能伪造 recovery line 或同 SKU 替代 |
| 已有审批并非通用审批引擎 | `backend/app/demand_models.py:1023` 审批实例 FK 指向 material_requests，1465 附近的逐行决定指向需求行 | 新增报损专用两级 review/decision 事实；不伪造需求单，不复用第三方总部审批身份 |
| 库存原子入口可复用 | `backend/app/formal_services/inventory_posting.py:1013`、1040、1286；1351–1398 做当前权限、期初范围、追踪/SN 校验；1518–1548 更新流水投影 | 所有冻结、转换、移出及冲销均经此入口；业务服务不直接 UPDATE 余额/SN |
| SN 报废当前明确失败关闭 | `backend/app/formal_services/inventory_posting.py:7028`–7037；`backend/app/formal_services/serial_ledger.py:142`–143；迁移 `backend/alembic/versions/20261002_0092_serial_consumption_projection.py:65`–66 | 必须同时升级写入、不可变流水重算和 PG 生命周期验证，不能删除拒绝分支便放行 |
| 通用反向交易不够 | `backend/app/formal_services/inventory_posting.py:1109`–1132 对工单/退回要求专用补偿，1246–1250 拒绝 consume/scrap SN 通用冲销 | 报损/报废也要专用、持久父命令；复用 `backend/app/formal_services/work_order_reversal_proof.py:19` 的准确逆流水与31–77的原状态重算模式，不复用其工单绑定条件 |
| 权限可复用但需新动作 | `backend/app/formal_access.py:103`–134 范围及 deny 优先，137 起锁当前主体权限图；迁移0100的388–403只种 read/submit_return/cancel_return | 新动作不能等同拥有 stock_operation read 或某个角色名；每阶段校验本人/区域/全国范围、身份状态、权限版本和自审限制 |
| DB 证明现在会把所有作业当退回 | `backend/alembic/versions/20261010_0100_stock_return_orders.py:277`–332 按单头/行/事件路由到退回检查；380–385 安装不可变和证据触发器；0101及后续封存扩展只认识退回命令 | 新 migration 必须按真实 operation_type 分流、未知类型拒绝；保留 return 分支强度，新增 loss/scrap 全链证明 |
| 文件用途尚未覆盖 | `backend/app/formal_file_schemas.py:12`–19；`backend/app/formal_services/formal_files.py:74`–89、693–749 | 增加报损/核实/终审/冲销证据用途与真 FK，验证 available/hash/用途/上传者/当前阅读范围；沿用私有存储意图流程 |
| 客户端尚无正式入口 | `frontend/src/App.tsx:509`–556 正式路由无报损/报废；mini 有 `pages/formal-stock-returns`、outbounds、shipments、receiving，没有 loss/scrap 提交审核页面 | PC 库存作业与审批入口、小程序“我的/个人仓”发起及区域核实均需补；查询枚举不能当交付 |

## 基线逐项缺口

1. §1.11 “工程师提交报损后先冻结对应库存”：没有绑定报损单的冻结流水与逐行未处置数量；普通冻结状态不证明报损。
2. §1.1、§1.11 “区域核实、总部审批、申请人不得自批”：没有两级报损 review、在途审批对象及自审/撤权验证。
3. §1.11 五种结果“恢复可用、转旧件、转坏件、退回、报废”：没有持久决定及与每行数量/SN 对应的处置过账。
4. §1.11 “报废批准后移出可管理资产”：没有总部批准与 scrap 外边界流水的不可拆分证明；SN scrap 当前主动拒绝。
5. §1.11、§3.11.9 “误报/失而复得用反向单”：没有报损/报废专用冲销事实、审批、原 SN 生命周期恢复及后续事实依赖检查。
6. §1.2/1.3/2.1、§3.10、§6.1–3：缺 PC/mini 流程、状态时间线、证据、任务提示和独立通知；不能靠改余额或把通知完成当处置完成。

## 最小完整业务模型（建议方案，尚未实施）

### A. 共用单据，类型特化

保留 `stock_operation_orders/lines/serials` 为基线共用事实源。作业类型新增 `loss_report` 与独立的 `scrap` 作业；报损原单保持 submitted 不可变，当前阶段由 review/处置/反向事实推导。

迁移只对非退回类型放开工单、运输库位与回收来源的必填条件；使用显式 `(type='return' AND 原完整条件) OR (type IN 新类型 AND 新完整条件)` CHECK，加 deferred cross-table proof。旧 return 不能因 NULL 的 SQL 三值逻辑绕过必填。基线没有要求草稿必须上云，首个切片可保留本地草稿，提交后即冻结并产生正式单号。

新增的最小持久事实：

- `stock_operation_reviews`：operation FK、阶段 regional/headquarters、明确核实结论/决定、reviewer、权限和范围快照、原因、前一阶段 FK、请求/幂等 hash。单据+阶段+审批轮次唯一，自审在服务及 DB 两处拒绝。
- `stock_operation_dispositions` 及逐行行表：终审事实 FK，原报损行 FK，处置类型、准确数量/SN、源冻结账户及目标维度、posting transaction FK，必要时绑定后续退回/报废单。每个已终审原行必须完整分配且不重复；首版可以每原行一个处置，不必额外引入部分核销语义。
- 报废专用单 `stock_operation_orders(type='scrap')` + 原报损处置 FK：承载最终批准及独立 scrap posting，不以报损创建或区域核实代替报废批准。直接报废申请也走冻结和同样两级验证，不允许只有管理员按钮。
- `stock_operation_reversals` 及逐笔 original/inverse links：专用反向命令、原因/证据、区域及总部复核事实、准确原交易、严格逆序的子交易和恢复前 SN 状态。原单、原审批和原流水全部保留。
- 专用命令 seal/request recovery（可扩展现有公共协议，但新类型必须有约束）：提交、review、终审、冲销分别独立请求键；404/超时属于未观察到，不自动重放。
- 附件使用有真实 operation/review/reversal FK 的绑定表，并形成不可变证据清单与 hash。不得只信 command_jsonb 中任意 file_id。

### B. 数量与事实规则

1. 提交来源必须是当前本人、已期初建立的个人仓、准确 owner/location/custodian/SKU/condition/lot/SN。从 available 原账户转到同维度 frozen；只冻结申请量，不把整仓冻结。已占用/出库/在途或他人保管对象进入明确异常处理，不绕过既有工单/履约原单。
2. 所有行整体预检、在事务内重新校验；任何一行不足或 SN 已移动则整单无变动。SKU 为整件时禁止小数，SN 数量必须等于数量。
3. 审批本身不减少资产。区域核实保存建议，总部终审保存实际结果；两步 actor 均不能是申请人。代理若启用必须验证现存授权的时间/范围及 evidence；不因有 admin 角色跳过正式权限。
4. 恢复可用/转旧/转坏：frozen → 对应 condition 的 available；保留 owner/custodian/location/lot/SN，资产总量不变。
5. 退回：frozen → return_pending 并绑定由该处置派生的真实退回单；随后复用出库、发运、验收、入账事实链。决定退回不解除个人责任；精确责任结清沿用实际运输/接收/入账规则，未完成不得显示报损全部处置结束。
6. 报废：总部批准事实已存在且与全量行/SN匹配后，frozen/scrap_pending → 受控外边界 `scrap`；余额移出可管理资产，SN 生命周期 scrapped，当前位置为空但原 owner/原账户/最后流水仍可重算。`condition=scrapped` 余额不能用来假装已经移出资产。
7. 不假设 frozen 账户只属于一个报损单。账户维度相同的单据共用账户，必须按原行及历史流水核算各自冻结余额，阻止另一单/通用 move 消耗本单冻结额度。
8. 误报/失而复得：反向单恢复精确原账户、SN 与生命周期。已发生后续退回/发运/接收/再次消耗时，先证明并补偿后续业务，不允许直接通用 reversal。涉及多个子交易，按依赖逆序在一个原子命令内写入；重复反向、反向的反向及换源单规避均拒绝。
9. 每次库存事实与 audit、state transition、notification event/outbox 在同一 DB 事务内形成；通知投递状态独立，不影响过账。两级批准、处置执行、退回履约、反向各有事实和时间点。

## 实施拆解与退出条件

### 切片 1：两级报损事实与冻结（为完整闭环打基础，不作为全功能上线）

- 后端：新增 loss schemas、options/preview、submit、read/list、regional review、headquarters preview 服务；复用 current principal graph、posting、受控文件、审计 Outbox。
- API：`/v1/stock-operations/loss-reports` 的来源/预检/提交/详情/列表；`/{id}/regional-reviews`；各命令原请求 lookup/seal。全部 mutating endpoints 要精确 request_id、idempotency key、expected plan/evidence hash。
- 权限：建议新增 `submit_loss`（本人）、`review_loss_regional`（原 owner 区域）、`finalize_loss`（总部全国）、`reverse_loss`（审批后受控执行），读取仍按精确对象范围；星星外部审批身份零授权。
- 迁移：共用单据条件化 CHECK；追加 immutable review/附件事实；权限矩阵；PG deferred proof 覆盖主体、来源、冻结流水与审计通知；现有0100/0101/0110分流和 seal request 冲突必须适配。
- 退出证据：SQL/HTTP 同时拒绝本人自审、跨区域、过期/撤权/deny、重复 SN、余额不足、未期初建立和伪造证据；同键并发只一单、一笔冻结，无半批变动。

### 切片 2：终审五种结果、SN 报废、退回适配和反向闭环

- 服务：终审命令将持久批准、逐行决定与所有过账组成原子事务；恢复/旧/坏/退回/报废全部实现，不用前端过滤隐藏缺分支。
- 退回适配：增加受控 `source_loss_disposition_id` 并在 DB 强制与原回收来源 XOR；退回行数量、condition/SN 来自批准处置。查读、出库、发运、收货、入账以及其 proof/recovery 均必须接受该真实新来源；旧工单回收路径回归不变。
- SN：新增 loss/scrap lifecycle proof helper；在 inventory_posting、serial_ledger 和 PG `rsc_check_serial_lifecycle_0092` 当前继承函数三个层面共同放行“有批准和准确父命令”的 scrap 与逆向恢复。启动安全函数 body hash/trigger manifest 随新迁移更新。
- 反向：专用 preview/execute/read/recovery/seal；复制可用“逆流水坐标+原状态重算”的机制，不调用通用逆账伪造业务来源。确认下游依赖已补偿。
- 退出证据：五分支和误报/失而复得都贯穿 API→数据库→余额/SN重建→通知事实；批准先于报废、终审失败整批 rollback；新退回按独立状态走至真实入账，不能止于 submitted。

### 切片 3：PC、私有 H5 及可操作验收

- PC 正式库存作业列表/详情/发起、区域核实、总部终审、反向及精确库存链接；每行展示源账户/成色/批次/SN、冻结数量和最终处置，不混合资产所有人与保管人。
- 私有 H5 `/xx` 支持工程师报损、扫码选 SN、上传证据和区域核实；申请人查看完整阶段及退回履约。现行公开首页与小程序公开知识查询范围保持不变；本文不授权扩大公开小程序的私有业务发布范围。
- 客户端保留原 request/body/身份版本；网络中断展示“结果待确认”，后台回查，无观察结果不能发新键；切账号、权限版本变化清除内存候选/分页缓存，但保留与原身份绑定的可恢复请求记录。
- 增加待审批/待处置异常入口；避免仅能从库存明细隐式进入而遗漏任务发现。
- 退出证据：Web/H5 使用实际 PG 生成协议 fixture，覆盖完整五分支、未知提交结果和不同账号隔离；手机和 PC 的真实角色 UAT 记录另行取得，静态测试不代替。小程序另做公开查询入口回归。

## PostgreSQL 16 必须覆盖的门禁

按现有 `backend/tests/pg16_stock_return_gate.py:61/145` 的 API role/fixture role 分离、并发 barrier、新 session 回读模式新增专用 `pg16_stock_loss_scrap_gate.py`，接入 `backend/tests/test_postgresql16_release_gate.py` 的新库及已有库存两轮。建议检查组：

1. 新库升级至 head、ORM一致、空库可回退；带现有 return/新loss数据升级保留精确行和hash；有新业务数据的降级拒绝，不能删账。
2. API运行角色直接SQL伪造报损头、附件、review、终审、scrap、反向、audit/outbox任一不全，均在COMMIT拒绝；API无UPDATE/DELETE/TRUNCATE不可变事实权限。
3. 当前权限、真实组织/身份、deny、到期/撤权、区域范围、申请人自审在普通服务和原始SQL都拒绝；提交后撤权不得让旧客户端继续终审。
4. 余额与SN并发：相同/不同幂等键争抢最后一件；与工单占用/出库、盘点冻结、另一报损并发；不死锁、不负数、不重复冻结/报废。
5. frozen共享账户隔离：两个单据同SKU/批次不同SN/数量，仅本单精确份额可恢复或报废；其他单/通用过账不可劫持。
6. 五种处置：前四种资产守恒；只有批准报废减少资产；批次/SN/成色/保管维度守恒；所有通知Outbox一次且不改变实物结论。
7. SN重算：报废前active→scrapped、当前位置null、来源可追溯；原单精确冲销恢复原生命周期/账户；不能凭修改lifecycle字段修复，伪造引用/原SN已后续移动拒绝。
8. 真实退回新来源全流程：loss→return_pending→outbound→shipment→receipt→inbound；批准、物理离开、发运、签收、验收、入账分别回读。旧回收退回原有门禁全回归。
9. 提交/终审/反向COMMIT已成功但HTTP响应丢失：新session查到原结果，原键重复只回放，无第二流水；真正未提交可封存，封存与执行竞争只有一个赢；provider通知失败不rollback库存。
10. 反向关联、证据缺失、未知原结果、部分子交易、重复反向、下游未补偿、授权变更导致的失败都零新增余额事实。函数body、触发器、RLS/ACL指纹与database_security基线匹配。

建议实施顺序：当前冻结验证终结并处理已知回归 → 切片1提交/冻结及两级事实 → 切片2全处置+SN+新退回来源+反向 → 切片3客户端与真实角色UAT。功能开关在五分支与冲销门禁齐全前保持关闭；每切片可独立评审提交，但不得将切片1当完整上线目标。
