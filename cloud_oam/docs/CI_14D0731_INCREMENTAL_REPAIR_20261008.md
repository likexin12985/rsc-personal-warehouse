# 14d0731 PG16 增量修复记录

**试点 MVP，不等同完整 V1。** 原 run [37772507579](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37772507579) 绑定 `14d0731387f8a24e8b25ed9222f89fec248ea563`，本文件记录其运行期间暴露的问题与工作树修复；不把修复写成该 run 已验证，也不取消正在执行的作业。

## 精确失败

| Job | 失败 | 原因与处理 |
| --- | --- | --- |
| 113295275558 / migrations | `test_postgresql16_migration_acl_concurrency_and_kill_gate`，0051 历史完成事实升级至 HEAD 时 `users.authorization_version` 从 1 到 4 | 新的授权策略有合法会话失效增量。预期须在升级前根据旧身份/授权和冻结策略精确推导，其他历史事实仍逐值相等，不接受任意更大版本 |
| 113295276143 / quantity review_seals | `my-receipts` 返回 412，`my_receiving_location_invalid` | 原报损夹具已有工程师个人仓，嵌套普通申请收货夹具又建第二个；本人收货的唯一有效个人仓守卫正确拒绝 |
| 113295276271 / serial submission | 同一 412 | 与上一项共用嵌套普通 quantity 包裹准备函数，故根因相同，不需要放宽正式收货权限 |

另两个已终态 job `113295276301`（quantity submission）、`113295276398`（serial review_seals）也确认是同一重复个人仓 412；不新增一套修复。`113295275509`（control）在 `pg16_daily_review_gate.py:209` 恢复结果断言失败，原日志没有实际恢复结果，详见下方限定修复。`113295275219`（inventory）是权限扰动夹具误把 0168 已合法授予的 `shipment_status` UPDATE 当成非法，最小夹具修复及聚焦验证已完成。

完整原始日志和去 ANSI 日志保存在 `artifacts/formal-0165-integration/ci-14d0731/<job-id>.raw.log` / `.log`。最初下载被 gh 的控制序列输出保护拒绝，返回空 stdout；显式允许 gh 输出后再去 ANSI 保存成功。没有将下载失败混作测试失败。

## 个人仓夹具修复

`pg16_stock_loss_seal_gate.prepare_generic_receipt_parent` 传入已建立的准确个人仓；`pg16_personal_target_fixture.existing_personal_target` 只读确认唯一有效个人仓、准确 ID/区域、叶子位置及当前保管责任。复用路径仅用于普通 quantity 的 key-probe 父包裹，禁止与拒收、混合退回、浏览器或供给恢复变体混用。

原个人仓的真实已复核 opening 保留，不再为第二个仓创建零期初。普通申请仍完整通过申请/审批/分配/占用/出库/发运/本人收货/入账；实际服务继续验证开账、权限、来源、数量、状态、审计和覆盖率。该嵌套夹具每次使用新物料，最终入账断言按准确目标和物料过滤，不混入原报损库存。

新增聚焦 `backend/tests/test_pg16_personal_target_fixture.py`：**10 passed / 1 warning / 23.00s**（quantity/serial 底层夹具参数展开），日志 `/tmp/rsc-personal-target-14d-fix.log`。警告为 Starlette/AnyIO 已知弃用提示。测试用真实 SQL 查询验证重复仓、错误 ID/区域及过期 custody 均拒绝，选择过程没有写 SQL。

## 历史升级的精确预期

0051 fixture 与冻结迁移 SQL 保持原字节。升级前独立记录角色、权限、grant、身份和历史事实；只允许已审阅迁移产生的具体版本增量及具体种子转换。已有 allow/deny 和自定义 ID 不得被假定为缺失，新授权不能扩大成任意通配权限。旧证据内保存的授权版本不随 live user 版本改写。

新增三策略计算及篡改反例最初 20 passed；参数化警告修正后仅对应 3 项复测通过。审查发现 0083 固定 technician seed 会合法从 fulfill 转向 receive，因此再补精确转换及冻结身份范围限制，升级前拒绝缺失/错绑 seed、既存 receive、技术员或混合人口。最终只复测受影响项：**19 passed / 19 deselected / 0.35s / exit 0**，日志 `/tmp/rsc-0051-auth-expectation-0083-review.log`；不把这些重叠结果相加成完整套件数量。

此修复不执行真实生产迁移。本地合成断言只验证预期计算及篡改拒绝；完整 0051→HEAD、历史目录和 PG16 权限/事务行为仍须新候选的托管门禁证明。

## 日审丢响应夹具的限定修复

`pg16_daily_review_gate.py` 原顶层导入连带 HTTP、数据库和多批测试夹具。spawn 子进程在同一 8 秒预算内重载它们，本机隔离测量为 **8.13 秒**；移动到父进程使用函数后为 **0.008 秒**。这证明存在预算风险，不足以认定旧 CI 的唯一根因。原日志仅给出裸断言失败，历史实际恢复结果保持未知。

子 worker 仅在真实返回 `observed` 且 `receipt.recorded is True` 后注入丢响应；未提交或拒绝结果原样通过监督管道返回并明确失败，不再用 worker 内 assert 将拒绝掩为 unknown。8 秒预算、严格 `found/version1` 回读、同一回执重放、最终事件/封存数量及全部业务守卫保留。unknown 本身仍不能证明 COMMIT，只能进入准确对象恢复查询，不会重试写命令。

仅新增 `test_pg16_daily_review_response_loss_fixture.py`：**15 passed / 0.34s / exit 0**，日志 `/tmp/rsc-daily-review-response-loss-fixture.log`；导入测量日志 `/tmp/rsc-daily-gate-import-before.log`、`/tmp/rsc-daily-gate-import-after.log`。测试验证导入边界、拒绝不丢失、记录后才注入、监督结果/清理失败不误判和无写重放，不证明真实 PG16 COMMIT。生产代码和迁移未改。

## 库存权限反例夹具修复

`pg16_reservation_release_gate._assert_release_catalog` 在 `_0071` 检查分支里给 `shipment_status` 追加 UPDATE 后预期拒绝，但当前 HEAD 的冻结 0168 迁移和正式列 ACL 都已允许该投影列。因此 GRANT 实际未扰动授权，`DID NOT RAISE` 是正确校验结果；原 finally 还会撤销这项合法权限。

现在对该列验证“撤销合法权限→必须拒绝→恢复”；另用不可变的 `requester_person_id` 验证“新增非法权限→必须拒绝→撤销”。生产权限清单、冻结 0168 和所有函数/触发器反例保持不变，没有开放拣货界面或扩展后置能力。

新增 `test_pg16_release_catalog_fixture.py` 使用内存权限目录调用真实 `_assert_runtime_column_acl`，验证三处变更以及异常时 finally 恢复准确权限。正确工作目录下 **4 passed / 1 warning / 4.02s / exit 0**，日志 `/tmp/rsc-inventory-catalog-fixture-14d0731.log`。首次从 `cloud_oam/` 未设置 `PYTHONPATH` 运行时为收集错误，随后从 `cloud_oam/backend/` 运行新增文件通过；这不是业务失败或真实 PG16 通过。警告为已知 Starlette/AnyIO 弃用提示。

## 接续

2026-10-08 20:36（Asia/Shanghai）最新只读快照：`14d0731` PG16 run `37772507579` 为 `in_progress`，64 success / 7 failure / 3 in_progress；余下三个 static_safety job 尚无终态。前述七个失败共对应四类问题，本批修复不改变该 run 的历史结果。Client run `37772507561` 已 success。

待本轮新增工具/夹具和审查完成后按当前交接集中处理提交。继续读取原 run 的新失败和终态，不反复推送取消运行，不重跑未受影响的终态全套。新低成本部署方案及工具不改变既有 API、密码策略、冻结数据库守卫或后置业务范围；见 [低成本方案](LOW_COST_PILOT_DEPLOYMENT_20261008.md)。
