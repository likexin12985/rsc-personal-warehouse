# 报损退回发件侧 HTTP 与原请求恢复缺口审计

> 2026-09-30 21:45 接续核验：0157 草稿业务封存的数量/SN 原生 PG16 v3 均通过，库正常停止、1805 当时源码零漂移。随后 52 文件补丁已逐文件核对并接入正式工作树：0157 迁移/ORM/运行目录/当前 head 消费者、发件写 API 与回查封存、H5 `/loss-returns/sending`。前端全量 1962 项通过，277 个源码与测试副本逐项相同；正式类型检查、双入口构建和公开入口边界通过。完整公开发布仍因知识目录 pending 被阻断。新正式后端聚焦及正式 head PG16 HTTP 尚在运行，未提交、未部署；下方旧阶段叙述均为历史，不代表当前候选/运行状态。准确会话、日志及下一步见 [当前交接](CONTINUE_DEVELOPMENT.md)。

最新增量：发件页面/表单 14 项测试、strict TS 和合成构建通过；浏览器数量/SN 出库、390px 丢回执刷新保留与只读回查已验证（仅合成 transport）。业务封存 v2 后续因 0154 的既有封存拒绝先触发而失败，v3 已加未封存正常对照及指定约束证明，会话 26956/80565 在运行。正式路由未接入、未提交部署；下方旧阶段记录不能覆盖此状态。

最新增量（2026-09-30，发件恢复与 CI 诊断）：发件读取原生 PG16 数量/SN 均 passed/stopped/0；H5 读取、命令、持久化恢复、适配器候选合计 75 项及 strict TS 通过。0157 业务门禁 v1 被真实 0110 namespace 拒绝，但测试仅期待 0101 报错，故整体失败；v2 已补指定触发器独立核验及执行/封存并发，会话 71113 和 33424 正在复验。GitHub attempt 2 三个静态作业均 runner shutdown，整轮 failure；本地接入进度/资源诊断的 5 项聚焦测试通过，尚未提交。全部仍不构成上线验收，详见 CONTINUE_DEVELOPMENT.md。

## 本轮接续：正式读取已整合，新增门禁运行中

以下覆盖旧章节中“候选未整合”的过程状态，尚不代表发件功能完成。

- 11 个读取模块及 3 个测试已正式整合。隔离读取及普通退料回归 51 passed；正式 HTTP/目录首轮 23 passed、2 failed，原因是保管责任结束测试使用 now−1 秒，可能早于派生时间。仅修正测试夹具时间边界后，正式目录全部 12 passed；生产证据守卫未修改。
- 新增 `backend/tests/pg16_loss_return_sender_read_gate.py`，接入既有 shipment gate，在真实派生、出库、发运三个阶段运行正式 HTTP。仅注入合成认证身份与 `star_oam_api` 数据库会话；require_permission 和业务授权使用真实数据库授权，所有查询使用 API SELECT-only 语句监测（保留期初核验一致性锁），前后比较库存、审计、通知、收货、入库及封存记录。
- PG16 v2 会话 68460 / PID 16674，日志 `artifacts/loss-sender-read-next/native-read-pg16-v2.log`，数量首库 `run-olw99neq`；1803 个非 Markdown 源文件冻结且中间回读无漂移。计划覆盖数量/SN，尚未终态。
- 独立候选 `loss_return_sender_recovery.py` 与测试位于 ignored 证据目录。完整原命令、请求 ID、幂等键、内容、计划摘要和派生源同时匹配；只读权限可以回查，缺失和权限变化不允许重发。后续恢复/封存候选 20 项服务测试及 4 项 HTTP 测试已通过；封存范围明确为 actor/request，不冒充已保存 key/plan。正式迁移、业务 PG16 和正式导入路径验证完成后才能开放入口。
- 0157 草稿 SQL 解析及 SQLite 升降级/守卫保留/有历史拒绝降级测试已 3 passed（26.74 秒），原生 PG16 空库 0156→0157→0156→0157、函数/触发器/ACL 核对也已通过（`native-seal-migration/run-enw3dcs6` passed/stopped/serverExitCode=0）；仍未注册正式迁移链，数量/SN 业务封存门禁正在运行。
- 远端准确 5e PG16 首轮终态 21 success / 4 failure：三个 static_safety 被 runner 关闭，最终汇总因依赖失败。inventory 已成功。已接受仅失败作业重跑并回读 attempt 2 in_progress，证据 `loss-return-h5-next/new-sha-pg16-attempt2-v1.json`。这轮远端不覆盖尚未提交的新增读取与恢复代码。


2026-09-30。依据正式V1.0 §1.8、§1.11、§2.3、§3.11、§6及当前源码。该文件记录已核实缺口和下一批实现边界，不是实现或上线完成证明。收货 H5 已在 5e847d2 提交；本人报损 H5 的数量/SN PG16 已通过并正常停库，源码冻结解除。发件读取候选仅在 ignored artifacts 中隔离开发，未接入正式后端。

## 当前已存在的能力

- `formal_services/stock_loss_return_commands.py::execute_loss_return` 从原总部批准的 return_to_region 决定派生退回，保存原报损、行、审批、处置、派生退回的关系，实际出库/发运尚未发生。
- `stock_return_outbound_plan.py::original` 对报损原单调用 `stock_return_origins.verify_return_origin`，内部 `execute_outbound` 支持 work_order_id=None，不需要伪造工单。
- `stock_return_shipment_commands.py::execute_shipment` 支持报损来源，发运本身不重复改变库存，依赖精确原出库量和SN。
- `loss_return_outbound_schemas.py` 和 `loss_return_shipment_schemas.py` 已有独立报损响应类型，明确origin，排除普通work_order_id/source_recovery_line_id；普通工单响应不能被放宽。
- `routers/formal_stock_returns.py` 已有普通工单提交/取消/出库/发运/原请求回查/封存HTTP，路径带UUID work_order_id，响应模型也是普通工单类型。不要重复实现这些接口。
- 收货和独立入库已由0155及当前H5接入覆盖，与本批发件缺口分开验收。

## 当前缺口的直接证据

| 层 | 当前源码事实 | 影响 |
| --- | --- | --- |
| HTTP | formal_stock_returns.router前缀为 `/v1/work-orders`；formal_stock_losses.py当前只暴露报损来源/提交/审批及对应恢复 | 报损派生退回/出库/发运不能通过现有正式工单路径安全调用 |
| 原请求查询 | stock_return_recovery._original_order调用普通facts.order_result；lookup返回普通StockReturnOut/StockReturnSealedOut | 不能直接传None当作完整报损恢复实现 |
| 永久封存 | seal_return_request无条件锁并读取OamWorkOrder；StockReturnSealOut要求work_order_id为UUID | 报损发件没有正确的封存来源证明 |
| 数据约束 | StockOperationCommandSeal.ck_stock_operation_seals_source仅允许source_loss_disposition_id非空且operation_type=receive_return | 只加路由会在真实PG16拒绝报损出库/发运封存 |
| SQL证明 | 0155的SEAL_BODY和SOURCE_CHECK同样只允许报损receive_return；SQLite也有专门阻断trigger | 需新增版本迁移及双数据库拒绝边界，不能修改旧迁移或放宽整个约束 |
| 中间件 | main.py 的退回专用分类匹配普通 work-orders/my-receiving；另有覆盖整个 `/api/v1/stock-operations/loss-reports` 的私有响应前缀 | 发件候选选择该报损前缀下的 `/returns`，仍须实际验证框架 401/404/405/422 和敏感输入不回显，不能只查正常响应 |
| 出库可选项 | stock_return_outbound_queries._options_basis 固定构造 StockReturnOutboundOptionLineOut，要求 source_recovery_line_id；末次复核仍调用普通 returns.order_result | 报损 source_loss_line_id 不能直接套用普通可出库/SN 选择响应 |
| 发运可选项 | stock_return_shipment_queries._options_basis 同样固定普通来源字段；响应头要求 work_order_id | 已有内部 preview/execute 并不证明工程师能从真实可装包明细进入页面 |
| 出库/发运历史 | 两个 history 都调用普通 _original_order；其要求原单 actor_user_id 等于当前工程师，而报损派生单的原操作者是总部 | 合法原申请工程师也会被错误视为无此退回，必须区分总部历史派生身份与当前实物执行人 |

现有 `test_stock_loss_return_outbound.py`、`test_stock_loss_return_shipment.py` 和对应pg16 gate验证内部服务、来源、数量和原子性，不能当作新增HTTP与原请求封存的证据。

### 2026-09-30 发件读取增量复核

当前源码中 `stock_return_origins.verify_return_origin` 已区分 `requester_id` 与 `submitted_by_user_id`，并验证原报损、总部决定、处置、派生退回及流水；后续发件读取应复用这份来源证明。不得为了通过普通 `_original_order` 而把派生单历史 actor 改成工程师，也不能放宽到任意具有 read 权限的人。

最低完整发件读取范围应包括：本人派生退回目录与原单详情、可出库数量及 SN、逐笔出库历史、可装包明细、发运历史及原请求查询。只新增两个 POST 仍无法交付可用的发件页面。可选项需要当前相应写权限，历史和请求恢复只要求当前读权限；当前保管责任的发出限制不能用于抹去旧事实。

本批本人报损 H5 的 PG16 源码冻结期间，仅在 ignored artifacts 中建立隔离诊断 `test_sender_query_gap.py`，复用已有数量/SN 合成夹具，并通过 SQLite query_only 及前后快照证明查询不写库存。结果日志为 `artifacts/loss-submission-h5-next/sender-query-gap-v1.log`；数量/SN × 4 个查询共 8 个缺口复现用例已退出 0（195.37 秒），全部启用 query_only 且库存/审计快照不变。两个 options 均在 source_recovery_line_id 校验失败，两个 history 均返回 stock_return_not_found。摘要及日志 hash 在 sender-query-gap-result-v1.json。这是确认缺口存在的诊断通过，不是发件业务验收通过。

## 实现顺序与状态边界

1. 本人报损 H5 候选先完成其既有门禁并保存完整终态；避免在源码冻结时将发件改动混入。
2. 确定报损发件HTTP的独立对象坐标：精确派生退回operation_id及核验后的origin；不使用虚构work_order_id，不允许客户端任意补来源。目录/明细按本人当前权限与来源验证，不因UUID可猜就返回对象。
3. 新增独立、严格的报损原请求查询及封存响应；GET回查允许当前read，写/封存分别校验outbound_return或ship_return。恢复不得要求仍拥有已经撤销的写权限。
4. 在当前0156之后新增迁移，允许且仅允许目标发件类型的报损封存。数据库证明须绑定原处置、退回、申请人/操作者、原request/hash、审计事件，保持同命名空间跨类型唯一。旧普通工单、收货和独立入库封存约束保持有效。
5. 提交与封存必须双向互斥：先封存后到达的写失败；先提交的事实由精确回查返回；并发竞争不能同时留下封存和已执行事实。依赖数据库证明和锁，不能只做Python写前SELECT。
6. HTTP只有COMMIT成功后返回事实；COMMIT/传输503归未知，保留原请求，客户端apiNoReplay。请求头/体坐标不一致拒绝；预检不写业务事实。封存只携带原hash，不生成新业务幂等键。
7. 出库改变实物在途/流水；发运记录承运交接及通知意图；物流签收、收货、入库、通知送达仍独立。取消/冲销不能用封存替代，已发生实物事实不得删除。
8. 派生退回本身的原请求恢复也须单独审计：其写同时产生Disposition和派生Order，不能套用只允许单一业务记录的普通lookup。先覆盖发件恢复，不宣称整个报损闭环完成；随后补派生、处置、反向冲销及发起H5。

## 必须获得的验证证据

- HTTP：本人、跨人、跨区域、来源混杂、普通工单误入、申请人与总部操作者区分；隐私响应头、异常响应、COMMIT后返回、头体冲突。
- 原请求：命中/未观察/明确封存；结果未知不重放；hash不符及跨操作类型冲突；读权限仍在时恢复；审计或来源被破坏时拒绝。
- 数量/SN：部分出库、多包裹、重复SN、累计超出预算、原成色保留；通知失败不推翻已提交库存事实。
- PG16：API角色真实COMMIT、直接SQL绕过拒绝、双方并发互斥、主数据最小权限、空库往返目录/ACL一致，有发件封存历史时拒绝破坏性降级。
- 普通工单、0155收货/入库、0156审批封存兼容；唯一migration head和前驱链；最终源码清单零漂移、临时库正常停止。
- 最终客户端、准确提交CI、真实设备/服务UAT分别取证。未取得原请求恢复、迁移/权限和PG16证据前，不发布写入口。

下一次开发以 [当前交接](CONTINUE_DEVELOPMENT.md) 确认冻结解除及准确HEAD，不直接执行历史门禁或覆盖未提交代码。

## 本轮读取候选与后续迁移补充

`artifacts/loss-sender-read-next/` 内已隔离实现本人目录、原单详情、出库/发运 options 与 history，以及报损前缀下的 GET 路由。目录使用本人 requester（保留总部 submitted_by 身份）、UUID 翻页及库存/审计快照摘要；原单详情读取已证明的原报损 SKU、数量和 SN，不把旧单数量解释成当前可用量。所有历史读取独立检查当前 read；新出库/装包选项仍要求相应当前写权限和保管责任。

- `queries-v2.log`：8 passed，修正两次独立查询不能直接比较 queried_at 的测试问题，业务字段仍全部相等。
- `http-v2.log`：9 passed，真实应用中间件下数量/SN、两张实际派生退回单分页、旧快照拒绝、只读/写权限分离及 401/404/405/422。
- `detail-privacy-v1.log`：14 passed，原单详情在出库后及写权限撤销后可读，读权限撤销、来源损坏、读取中审计变化被拒绝；新增发件和普通工单接口的错误响应均不回显输入。
- HTTP v1 发现了实际缺口：报损前缀仅添加 no-store，默认字段错误仍回显输入。候选将新 `/loss-reports/returns` 精确纳入 `formal_stock_returns.is_return_path`，复用已有固定错误正文处理。正式源码仍未整合此修复。
- 目录初版夹具只有出库/发运权限，没有独立 read；已补测试授权，服务端拒绝保持不变。`read-regression-v1.log` 因此在 6 failed / 8 passed 时主动中断（exit 2），不计通过；修正后全套读取及普通退料回归仍需回收终态。

后续封存迁移还须处理身份校验，而不仅是放宽 SOURCE_CHECK。0101 的原单条件含 `parent.actor_user_id=seal.actor_user_id`，经 0103/0104 扩展到出库/发运；0155 只添加报损收货分支。新增报损发件分支必须绑定 requester 与当前工程师，同时用已核验的 loss disposition/root/child 关系保留总部历史派生身份；普通工单分支仍保持原约束。应复用 0153/0154 的实物执行 COMMIT 权限证明，并验证未知请求封存与晚到命令双向竞争。

以上均为本地隔离候选证据，不是正式发件入口或上线验收。候选原文件、目标路径及摘要记录在该目录 `continuation.json`；Git 会忽略整个 artifacts，后续必须完成正式整合与对应 PG16 证据。

现行封存函数已用 `cache_migration_compilation` 只读展开成功（会话 80041 / exit 0；53 份编译、15835 次字节码复用；没有执行迁移或连接数据库）。`current-seal-0155.sql` 的实际 SHA256 为 `484989dddfd93a1b473f5418b03d7f5e55626ef535c953d96f6cb54a2307e226`，确认原单 actor 条件仍在。基于该精确文本生成 `sender-seal-body-draft.sql`：限定报损 source 的出库/发运分支，原子封存时证明 0152 原派生链并分别调用 0153/0154 当前实物操作权限；普通工单、收货和既有审计正文保持原分支。此文件仅为 SQL 草案；0156 后的新迁移、目录/ACL 校验、SQLite 阻断、空库往返、保留历史阻止降级及双向并发证明均未实现，不得直接执行草案或宣布封存可用。

PG16 读取 v1 已终态失败：测试设置 READ ONLY 拒绝既有期初证据核验的 SELECT FOR UPDATE，返回 stock_return_unavailable/503；原库已正常 stopped/serverExitCode=0。v2 只修改门禁事务设置与 SQL 监测，不修改生产一致性校验，重新使用全新隔离库。
