# PG16 非期初盘点历史保留门禁修正

2026-09-27，提交 `9fe60477a3ed399308a96e2604bf12a2658e882f` 的
[runtime job 108520111434](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36283610747)
退出 1。0054 检查已通过，随后 0055 的独立历史降级检查失败：当前 head 的函数
已被后续迁移合法更新，调用 0055 完整降级时先触发旧版函数 hash 校验，无法抵达
预期的 `0055 non-opening start audit order downgrade is unsafe` 保留检查。

本修正只改变测试调用：传入 0055 原有 `_require_no_start_graph`，在当前 migrator
回滚事务内验证真实历史事实仍被拒绝。正常 Alembic 链的首个拒绝、独立 0047
拒绝、当前 revision、目录与原任务回读检查均保留；空库历史往返继续验证完整迁移。
不把任意数据库异常视为通过，没有更改生产迁移、业务代码、权限或库存约束。
同时审阅了此 helper 的其他历史迁移调用顺序：其余保留检查在旧 hash 替换之前，
或已显式传入保留函数。本次修正不代表后续完整 runtime 已通过。

本地全新独占 PostgreSQL 16.15 实例完整迁移至 `20261120_0141`，经签名合成来源
发布建立数据，并使用真实 API 角色分别提交期初任务与非期初 sample 盘点启动：

- 当前迁移链与独立 0054、0055、0047 历史保留检查通过。
- 分别将 0054、0055 原始保留检查替换为空操作，测试均因未抛出异常而失败。
- 完整期初、盘点、库存、对账、审计及 outbox 事实在验证前后逐表一致。
- 20 项相关迁移、保留规则和诊断聚焦测试通过，168 项未选中。
- 本地句柄 24576、18842 均退出 0；实例 `run-pd4soz4f` 正常停止，
  `checks=passed`、`serverExitCode=0`，被测测试源码 hash 未漂移。

证据在 `artifacts/pg16-start-retention-fix-20260927/`：`local-v1.log`、
`local-result.json`、`focused.log`。未连接现有或生产数据库，未伪造 GitHub runner
标识，未执行专用破坏性完整门禁。准确修正提交的 GitHub 全量检查仍须单独完成。
