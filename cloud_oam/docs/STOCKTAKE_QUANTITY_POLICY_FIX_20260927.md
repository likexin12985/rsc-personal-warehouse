# 盘点数量策略：忽略无数值意义的末尾零

日期：2026-09-27。候选未提交、未部署。

## 问题与修复

准确远端提交 `feffe0ebb319f831f1569e8fa2da11902c43ba8c` 的
[PG16 runtime 任务](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36285335032/job/108524958179)
失败于 `_assert_dynamic_sn_cutoff_replay_multiscope`：SN 盘点数量 `Decimal('2.000')`
被 `stocktake_count._validate_quantity` 当作超出整数物料的 0 位精度。
此前 0054/0055 独立保留检查已通过，本次是后续盘点服务的数值校验问题。

`stocktake_count.py` 现在按数值实际所需的小数位判断物料策略，忽略存储/传输补齐的
末尾零。仅处理 Decimal 的原始数字和指数，不用可能受上下文精度影响而舍入的
`normalize()`；`2.001` 仍不是整数，`1.210` 仍不符合一位小数策略。
输入层最多三位小数、有限数、数量正负约束和 SN 逐件数量匹配全部保留。
没有改 CI 的 `2.000` 夹具规避失败，也没有改数据库权限或库存记账。

## 验证与范围

证据目录：`artifacts/loss-typed-context-20260927/`。

- 修复前：`quantity-policy-before.log`，6 个带末尾零的合法值全部失败、11 个既有
  拒绝场景通过，句柄 85443 退出 1，确认回归能暴露原缺陷。
- 修复后：`quantity-policy-after.log`，37 通过，句柄 20920 退出 0。包含实际
  SN 盘点服务的 `1` / `1.0` / `1.000`，仍拒绝重复 SN；覆盖小数、零、负数、
  非有限数、超长输入以及低 Decimal 上下文精度下不舍入。
- 历史读取、原请求状态与复盘分配回归：`quantity-history-regressions.log`，
  118 通过，句柄 29728 退出 0；与前述 37 项为不同测试文件，共 155 项。
- 真实 PG16.15：`quantity-pg16-v1.log`，句柄 87099 退出 0，实例 `run-a1a1lnre`
  正常停止、源码清单无漂移，迁移 head 为 0142。调用未改动的准确 CI 场景函数，
  只将其集群管理员连接路由到新建且已验证归属的本机 socket；数量、权限、
  审批、时钟、服务与数据库约束未替换。
- PG16 实际完成：两个位置正式零期初、一次合法 SN 入库、`2.000` SN 实盘、
  各范围独立截止游标回放、两级复核、审计锁等待期间组织撤权使整次过账回滚、
  恢复权限后的正式过账、总量 `3.000` 对账及全部任务关闭。

## 独立提交前验证

另通过 Codex 管理的 `stocktake-quantity-validation/oam` 工作树，在准确父提交
`feffe0ebb319f831f1569e8fa2da11902c43ba8c` 上只复制三个代码/测试文件，未包含
报损、0142 迁移或其他未提交代码。依赖使用现有本机 Python 环境与 Node 24.19.0。

- `artifacts/quantity-isolated-20260927/focused.log`：155 通过，句柄 90348 退出 0。
- 同目录 `pg16.log`：新建 PG16.15 实例 `run-7k0dhxca`，迁移 head **0141**，
  准确 CI 动态 SN 场景全部通过，句柄 24833 退出 0，sourceDrift 为空，实例正常停止。
- 三个候选文件的 SHA256 与开发工作树一致；实际提交范围限这三个文件及本文。

以上是真实本机 PG16 的聚焦证明，不是完整 GitHub 门禁或生产验收。旧远端提交的
runtime 已失败，static 分片仍以真实终态为准；不能把它们或客户端通过当作修复后
的完整成功。提交后必须启动并回读准确新 SHA 的完整 CI，生产发布仍未授权执行。
