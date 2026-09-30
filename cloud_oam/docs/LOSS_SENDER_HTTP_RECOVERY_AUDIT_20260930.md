# 报损退回发件侧 HTTP 与原请求恢复缺口审计

2026-09-30。依据正式V1.0 §1.8、§1.11、§2.3、§3.11、§6及当前源码。该文件记录已核实缺口和下一批实现边界，不是实现或上线完成证明。当前收货H5候选正在独立PG16门禁中，业务源码冻结；本审计不修改服务和数据库。

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
| 中间件 | main.py根据formal_stock_returns.is_return_path决定退回API隐私错误处理；当前匹配普通work-orders或my-receiving | 新路径必须加入同一私有响应/错误边界 |

现有 `test_stock_loss_return_outbound.py`、`test_stock_loss_return_shipment.py` 和对应pg16 gate验证内部服务、来源、数量和原子性，不能当作新增HTTP与原请求封存的证据。

## 实现顺序与状态边界

1. 收货H5候选先完成其既有门禁并保存完整终态；避免在源码冻结时将发件改动混入。
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
