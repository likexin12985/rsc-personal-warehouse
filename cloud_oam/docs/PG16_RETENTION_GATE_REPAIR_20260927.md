# PG16 期初历史保留门禁修正

2026-09-27，提交 `5a919ba2bf043660749eb6097bda051a2a0d6f19` 的
[runtime job 108515956615](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36282148582)
退出 1。`_assert_0052_opening_history_downgrade_rejected` 仍期待 0054 的拒绝信息；
实际迁移链先被 0126 的 `opening authorization evidence must be retained` 阻断。
这是门禁断言未适配新版保留规则，不能作为完整 PG16 通过，也不能删除期初事实来绕过。

修正复用当前事实驱动的降级门禁：验证迁移链准确的首个拒绝原因，再以 migrator
独立事务执行历史 0054 的原始保留检查。历史迁移的源代码 hash 前置检查绑定旧版
函数，不适用于当前 head，因此只调用其原有 `_require_no_opening_evidence`。
事务无论成功或失败均回滚；当前 revision、原期初任务及 0052 目录检查继续保留。
没有更改生产迁移、数据库约束、权限或业务代码。

本地验证使用全新、进程独占的 PostgreSQL 16.15 Unix socket 实例，完整迁移至
`20261120_0141`，经签名合成来源发布建立夹具，使用真实 API 角色提交期初任务：

- 当前 0126 迁移链拒绝与独立 0054 保留检查均通过。
- 将历史保留检查替换为空操作时，门禁确实失败；未将任意数据库异常视为成功。
- 两轮之后逐表回读期初、对账、审计、通知、库存事实完全相同。
- 20 项相关迁移和诊断回归通过；本地句柄 14920、95955 均退出 0。
- 实例 `run-_havhvg0` 正常停止，检查通过、server exit 0；被测源码 hash 未变化。

证据目录：`artifacts/pg16-retention-fix-20260927/`。`local-v3.log` 与
`local-result.json` 是最终证据。前两次本地验证脚本的字段名/模块替换错误记录分别
保留为 v1/v2，不能计作通过。没有伪造 GitHub runner 标识、放开正式破坏性门禁，
也没有连接现有或生产数据库。完整 CI 结论必须读取修正提交对应的新 run。
