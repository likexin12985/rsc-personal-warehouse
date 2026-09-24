# 退回独立入账：待修复的上线缺口

**7.39 更新：** 服务/HTTP 修复及更完整测试已接续到
[退回独立入账验证与剩余门禁](STOCK_RETURN_INBOUND_VALIDATION_20260920.md)。预览、整数错误状态、
精确重放、当前权限和已完成请求封存已有修复；独立封存事实、完整图校验、小程序已入账回读和
真实 PG16 仍未完成。以下保留 7.38 的原始发现，不代表这些问题仍全部未修复。

2026-09-20 / 7.38 在通知调用方审计中读取当前源码得到以下结果。该链已有路由、计划、命令、
恢复模块和 0106 数据库表；不能误报成没有实现，但现有测试主要覆盖纯契约，不能据此放行。
本节没有修改该业务链或执行真实入账。

## 已用本地无数据库写入探针复现

1. `stock_return_inbound_commands._json_plan()` 丢失 `reason`，`preview_return_inbound()` 直接以其结果
   生成路由响应，而 `StockReturnInboundPreviewOut` 要求 `reason`。合成计划经过相同组装与
   Pydantic 输出契约验证，明确缺少 `reason`；应补真实预览 HTTP 回归并修复字段/摘要的一致性。
2. `stock_return_inbound_plan.py` 和 `stock_return_inbound_commands.py` 多处以
   `(code, category, message)` 调用公共 `_fail`。实际签名是 `(code, message, status_code=409)`。
   同形调用探针得到字符串类型的 `status_code` 与错误消息 `not_found`；路由又将该值直接传给
   HTTPException。需恢复明确的整数状态码和用户消息，验证错误路由的私有响应和事务回滚。

探针只导入测试配置、使用合成 UUID 调用纯组装和错误函数；不连接业务数据库。
输出保存在 `notification-channel-identities-20260920/inbound-audit-probe.txt`。

## 源码检查发现，尚待真实服务/路由回归确认

- `execute_return_inbound()` 同 key 的既有行只比较操作者、request_id 和 plan_hash，没有比较
  传入 receipt_id；另一 receipt 路径有机会回读不属于该路径的原结果。按 receipt 查到已有入账后，
  又会在未验证新 request/key 与原命令相同的情况下返回原结果。需以准确 receipt/request/key 的
  交叉组合证明拒绝边界，不应把错误对象结果当成幂等成功。
- `seal_return_inbound_request()` 对已完成结果取 `plan_hash` 比较传入的 `request_hash`；
  正式请求摘要实际是 receipt_id、request_id、plan_hash 的联合摘要。这两个字段不是相同概念。
  需验证响应丢失后回读、封存、重复调用和不同验收单共享包裹时的行为。
- 写入分支未显式查原请求封存；必须查清 0106 实际守卫覆盖，再验证封存后迟到写入不能生效。
  不把仅有封存表模型当成已经阻止迟到命令。
- 当前专项只有 `test_stock_return_inbound_contract.py`；受保护 PG16 主门禁未发现调用该独立入账
  命令的业务证明。需补真实迁移下的数量/SN、保管责任转移、入账与验收独立、权限、重放和故障回滚。

## 接续顺序

先复用已有退回发出→包裹→验收夹具，构造准确的接收责任与可用目标账户，执行实际入账计划、
提交和恢复服务；把上述对象/摘要/封存/错误响应问题固定为回归，再修复命令及接口。
随后将真实 API role 和 migrator 夹具接入同一受保护 PG16 环境，补齐所有写入及回滚证据。
通知、物流签收、验收和独立入账继续分开；不能通过直接改余额或伪造已验收记录避开完整链。
