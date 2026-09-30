# 报损退回 H5 收货与独立入库接入验收

2026-09-30 源码核对；这是后续实现和验收范围，不是完成证明。基线 §1.8、§1.11、§2.3、§6 保持有效。当前审批查询/H5 的 PG16 正在冻结源码验证，本文件不改变该候选。

## 已有服务与 H5 缺口

接收侧后端 0155 已实现并验证；私有小程序已有接收交互，但公开小程序不发布这些业务页面。私有 H5 尚无退回接收页面，不能用普通需求入库页面替代：普通需求以需求单为主键，退回接收以包裹/收货事实为主键，接口与恢复记录不同。

| 动作 | 正式 API 路径（相对 /api） | 事实与权限 |
| --- | --- | --- |
| 接收目录 | GET /v1/stock-returns/my-receiving | 本人当前保管责任；stock_operation.read；UUID 游标 limit≤20 |
| 包裹详情 | GET /v1/stock-returns/my-receiving/{shipment_id} | 精确包裹，不产生收货或库存事实 |
| 验收历史/剩余 | GET /v1/stock-returns/my-receiving/{shipment_id}/receipts | 已验收、拒收和未确认数量/SN 独立投影 |
| 验收预检 | POST 上述 receipts 路径 /preview | receive_return；校验原包裹、责任人、行与证据，不提交 |
| 验收提交 | POST 上述 receipts 路径 | receive_return；原 request/key、expected_plan_hash；仅验收，不入库 |
| 验收恢复 | GET 上述 receipts 路径 /by-request/{request_id} | 当前 read；404 只说明暂未观察，不允许重发 |
| 验收封存 | POST 上述 by-request 路径 /seal | 当前 receive_return；原请求 hash，精确回读 |
| 独立入库状态 | GET /v1/stock-returns/my-receiving/{receipt_id}/inbound | 注意这里是 receipt_id，不是 shipment_id；read |
| 入库预检/提交 | POST 上述 inbound 路径 /preview 或路径本身 | receive_return；只有已验收量，独立库存交易 |
| 入库恢复/封存 | GET 上述 inbound 路径 /by-request/{request_id}，POST 其 /seal | 原入库请求，与验收恢复分开；状态须准确核验 |

接口依据：`backend/app/routers/formal_stock_return_receiving.py`、`formal_stock_return_receipts.py`、`formal_stock_return_inbounds.py`。路径已从当前源码核对，不能根据页面名称推断。

## 必须保留的合同

1. 工单退回携带 work_order_id；报损退回携带 origin 五字段（origin_kind=loss_report、loss_operation_id、loss_line_id、headquarters_decision_id、disposition_id）。两者互斥，origin 不是可选备注；不能以缺 work_order_id 判定报损。原单来源、处置、包裹、责任人、目标仓全部精确匹配。
2. 报损支持 new/used/damaged；普通工单退回仍限 used/damaged。不能为了兼容 H5 放宽普通工单合同，也不能丢失报损新件成色。
3. 数量使用 numeric(18,3) 字符串和精确计算；accepted、rejected、damaged、shortage 分开。damaged 是 accepted 的子集，不重复相加；shortage 保持未确认，可在后续批次验收，不能自动入账或关闭。
4. SN 管理按原包裹未确认 SN 选择；接受 SN 需要实际 SKU/二维码/SN 校验。破损 SN 是接受 SN 子集；接受、拒收、短少三组不得重复，不能仅靠数量完成 SN 验收。
5. 短少、破损、错料、错 SN、拒收必须有匹配异常说明和有效 evidence_file_id。上传沿用正式附件流程，真实上传完成并回读 available 后才能作为证据；不能用手输任意 UUID 替代正常产品流程。
6. 预检内容与确认展示冻结，提交前重新鉴权/预检；方案变化要求重新确认，不自动采用新方案。保留原 received_at、request ID、key、hash、人员/授权版本、来源与包裹坐标。
7. localStorage/Web Locks 按人员、来源、包裹隔离；验收和入库请求类型独立。在同包裹存在未知请求时阻断相冲突新写，先精确回读或显式封存。存储不可读/损坏、跨标签锁竞争、切页或身份/范围变化都保留恢复记录。
8. 请求发送后不依赖 POST 回执清理：精确原请求 GET 成功并核验前后当前授权才清理。404、网络错误、代理 503、错误对象或不完整证明不能清理，也不能触发自动重放。read 与 receive_return 权限分别验证。
9. 验收历史只表示实物确认。每个 receipt 独立读取 inbound 状态；只有 status=posted 且存在 posting_transaction_id 的完整事实才能显示入库完成。通知、物流签收、OAM 收货均不是入库证明。

客户端参考源码：`miniprogram/utils/loss-return-origin.js`、`stock-return-receiving-contract.js`、`stock-return-receipt-contract.js`、`stock-return-inbound-contract.js`、两个 submit 模块及 `pages/formal-stock-return-receiving/index.js`。移植须接入 H5 原身份和 apiNoReplay，不能复制小程序 token/session 或 wx 接口。

## 实现后的证据要求

- 数量/SN × 工单/报损真实 Python 响应与浏览器合同互验；新件报损、来源混杂拒绝、部分正常收货、短少后再次收货、破损子集、拒收/错料/错 SN。
- 当前角色/区域/保管责任和授权到期；读权限保留但写权限撤销仍可恢复；原申请人状态改变不污染既有事实。
- 写前存储失败零请求，结果未知只回读，刷新重入/多标签/切换用户/撤销权限/原记录篡改不得误清理或重复提交。
- 入库单独确认，已入库状态只读显示，不重复过账；部分验收各批独立入库，入库后原请求恢复仍准确。
- H5 导航和直达路由权限、窄屏、键盘、长列表、真实扫码/照片交互验收；公开首页和公开小程序仍不包含私有入口或数据。
- 合成客户端、真实 API 角色 PG16、准确提交 CI、真实设备 UAT、生产上线分别取证。既有 0155 证据只覆盖其候选，不自动覆盖新 H5。

本轮只完成接入边界核对；以上 H5 交互仍待实现，不计入已完成页面。
