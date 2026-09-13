# 正式退回提交与请求恢复接口

适用代码：迁移 `20261011_0101` 及对应后端。产品依据为仓库内 V1.0 正式需求。
接口已接入小程序工单退回页。代码接入不表示已发布生产；实物发出、接收和
保管责任解除仍须后续实现与验证。

## 业务事实

退回提交将本人准确原回收行的库存由 `available` 转为同维度的 `return_pending`。
此时位置和个人保管人不变；`submitted` 是原提交事实，不是发运或接收成功。
未发出整单取消追加独立取消事实和反向库存流水；原提交记录保持不可变。
查询原提交仍返回 `submitted`，因此不能用该响应推导当前是否已取消。
个人应退责任不会因提交或取消而解除，必须由后续独立接收与过账事实证明。

## 路径与权限

以下路径均以 `/api/v1/work-orders/{work_order_id}` 开头。

| 方法 | 后缀 | 本人范围的独立权限 | 作用 |
| --- | --- | --- | --- |
| GET | `/returns/options` | `stock_operation/submit_return` | 读取准确原回收行及个人仓所属区域仓的有效在途位置 |
| GET | `/returns` | `stock_operation/read` | 读取本人原提交及各自独立的取消事实 |
| POST | `/returns/preview` | `stock_operation/submit_return` | 完整退回预检，返回当前方案摘要，不移动库存 |
| POST | `/returns` | `stock_operation/submit_return` | 创建原退回与占用事实 |
| POST | `/returns/{operation_id}/cancellations` | `stock_operation/cancel_return` | 取消准确原单的未发出占用 |
| GET | `/returns/by-request/{request_id}` | `stock_operation/read` | 按本人原请求回查提交或封存事实 |
| GET | `/returns/{operation_id}/cancellations/by-request/{request_id}` | `stock_operation/read` | 按本人原请求回查取消或封存事实 |
| POST | `/returns/by-request/{request_id}/seal` | `stock_operation/submit_return` | 核验原请求；若未执行则永久封存 |
| POST | `/returns/{operation_id}/cancellations/by-request/{request_id}/seal` | `stock_operation/cancel_return` | 核验原取消请求；若未执行则永久封存 |

服务仍核验准确来源、当前身份和权限；拥有提交权限不代替来源读取权限。
封存需要当前对应写权限及原请求读取权限。已关闭工单仍可按本人退回权限处理。
历史回查与封存不依赖工单仍分配给本人，但原单、原请求与人员绑定必须一致。

## 准备与提交

1. 从正式 `/returns/options` 的 `sources` 选择原回收行，逐行保留原账户和数量；同账户的
   `available_quantity` 不能跨来源行相加。SN 行必须核验实物物料、序列号及二维码。
2. 明确选择个人仓所属区域仓和其在途位置；服务核验所有组织及唯一有效接收保管人。
   从同一响应的 `destinations` 选择完整绑定，不允许按名称猜 UUID。没有有效在途
   位置时返回空列表；保管关系矛盾则拒绝整次读取。来源、权限和绑定在读取期间
   发生变化时，旧选择不能继续使用。
3. 预检提交 `operator_person_id`、`target_location_id`、`transit_location_id`、
   `reason`、`lines`。每行有 `source_recovery_line_id`、`stock_account_id`、
   `quantity` 和 `serial_verifications`。准确结构以 `stock_return_schemas.py` 为准。
4. 确认整组结果后，一次提交同一内容，并附 `expected_plan_hash`、新的
   `idempotency_key`、`request_id`。若提供 `X-Request-ID` 或 `Idempotency-Key`
   请求头，必须与正文一致。操作人必须是当前登录人员。
5. 方案变化返回冲突，重新准备并确认；不能复用旧方案覆盖新的库存、责任或权限。

取消正文只接受本人 `operator_person_id`、明确 `reason`、`request_id` 和
`idempotency_key`；路径工单必须与原退回单匹配。

`GET /returns` 返回 `person_id`、`work_order_id`、`authorization_version`、
`queried_at` 和 `items`。每项的 `original` 是不可变原提交，`cancellation` 为
独立取消事实或 null。历史不依赖工单当前受派人，仍逐项核验本人、完整流水和审计。
本次最多读取 100 笔；超过上限明确拒绝，不能静默截断成完整历史。两个新增 GET
及预检只读数据库，不产生库存、通知或责任转移。

## 小程序入口

从正式“工单物料”的本人工单进入 `pages/formal-stock-returns/index`；已关闭工单
仍可办理本人应退物料。本人待恢复记录也提供该入口，工单转派后仍能按原请求核验。
页面先读取当前身份和权限，再读取来源、区域仓在途选择和独立历史。数量件按原行
填写数量，SN 件逐件扫描物料号、SN、二维码；所有明细、目的位置和原因须整组确认。
服务端预检会在提交前重新执行。取消也重新读取原单并另行确认。

扫码正文仅留在本页内存；离开、刷新、身份权限变化或写入结果未知时清除实物草稿。
恢复记录复用工单级持久化存储和互斥，兼容已有物料、配对、登记及冲销记录。
写入只发一次，即使 POST 返回成功也要准确 GET 核验后才清除恢复记录。未送达
请求由用户选择封存，单纯 404 不能清除记录或触发重发。

## 结果丢失后的恢复

客户端应在发出请求前持久化本人、工单、操作类型、原请求 ID、请求摘要、方案摘要
（提交时）及原退回单 ID（取消时）。这些坐标用于回查，不能代替新写入的授权。
不要持久化扫码正文或用于自动重发的幂等键。

- 成功响应分别是 `StockReturnOut` 或 `StockReturnCancellationOut`，均有独立的
  `posting_transaction_id`。客户端需核对原请求坐标和摘要后确认结果。
- 网络中断、503 或无法验证响应时，只回查原请求；不要自动换键提交。
- GET 返回 404 `stock_return_not_observed` 只表示暂未观察到，不能清除恢复记录或
  认定没有执行。503 `stock_return_evidence_invalid` 表示证据不完整，也不能当作空结果。
- 用户明确决定停止原请求后，可调用对应 `/seal`，正文为 `operator_person_id`
  与原 `request_hash`；禁止携带 `Idempotency-Key`。
- 若请求已经执行，封存入口返回原成功事实；若未执行，则返回
  `{schema_version: "1.0", lookup_status: "sealed", seal: ...}`。封存与原提交互斥，
  以后即使换幂等键，原请求 ID 也不能执行。GET 可反复只读核验该封存结果。
- 封存只记录请求处置及审计，不移动库存，也不产生接收或通知送达事实。

成功、业务拒绝和框架级错误均禁止缓存。验证错误不回显扫码输入；历史响应不返回二维码。
所有客户端状态必须分别展示退回占用、取消、实物发出、对方接收及保管责任解除。

## 验证入口

`test_formal_stock_return_routes.py` 覆盖预检、实际提交后响应丢失、原 GET 恢复、
封存、迟到请求、请求头绑定、工单绑定和错误响应隐私。
`pg16_stock_return_recovery_gate.py` 验证真实角色的只读回查与数据库双向互斥。
`pg16_stock_return_transport_gate.py` 验证真实 HTTP、两连接竞争及提交后新会话回读。
`test_stock_return_options_history.py` 覆盖来源选择、保管关系、独立权限和历史只读。
小程序 `stock-return.test.js`、`stock-return-page.test.js` 覆盖正式 SDK 和页面流程；
`pg16_stock_return_mini_gate.py` 用实际小程序 API 客户端连接测试 HTTP 与 PG16 API
角色，覆盖数量/SN × 提交/取消 × 正常返回/执行后丢响应/未送达三种路径。
新增封存历史后禁止降级删除；旧版本的降级验证必须在永久新封存之前完成。
