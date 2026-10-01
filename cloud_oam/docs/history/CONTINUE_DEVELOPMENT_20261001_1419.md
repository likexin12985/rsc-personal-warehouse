# RSC 个人仓开发交接

更新：2026-10-01 13:55（北京时间）。完整上线目标保持活动；本轮为 progress。0160、处置HTTP/来源接口和审计批读已应用主树，当前验证运行中，未提交或部署。

## 工作位置与约束

- 工作树：`${RSC_REPO_ROOT}`；代码目录：`cloud_oam`。
- 分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。本批未提交、推送、部署。
- 继续前完整阅读根目录 [正式需求基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)，检查当前 diff。禁止 reset、revert 或丢弃未提交改动。
- 主树已应用正式0160多代集成包（58目标），另应用15目标：三个旧断言测试文件、审计批读及测试、六个处置HTTP接口及来源接口、新原生写HTTP辅助门禁。备份和回执：`artifacts/loss-multigeneration-main-integration-next/application.json`、`artifacts/loss-execution-command-http-next/main-application-v1/application.json`。
- 首页保持公开知识查询、无登录界面，星星管理入口到 `https://rscwz.cn/xx`。飞书知识源暂缓；后续按 NIO Chat CLI 路由。
- 本轮只操作本地代码和可销毁测试库，无生产/外部业务写入。不能用本地通过替代上线验收。

## 已完成及证据边界

| 范围 | 当前证据 | 限制 |
| --- | --- | --- |
| 报损退回收货、独立入库、请求恢复 | 历史数量/SN PG16回归通过，`artifacts/loss-formal-application-next/receipt-{quantity,serial}-verified-v2.json` | 旧1901源快照；不得冒充本轮全量发布 |
| 原处置经历一轮纠正后的恢复 | 主树已集成，34项聚焦及1918源数量/SN PG16通过，`artifacts/loss-recovery-main-integration-next/main-*-verified-v1.json` | 后续代码需按实际受影响范围重验 |
| 原处置/衍生退回只读HTTP | 主树已注册2个POST request-lookup；52项新用例+34项既有回归共86通过，`artifacts/loss-execution-recovery-http-next/verified-v1.json` | 当前读权限独立于写权限；返回原历史事实；无执行/封存回退。认证依赖为测试替代 |
| 后继历史HTTP | 同目录 `history-verified-v1.json`：6项数量/SN×三种纠正，84次路由请求通过；1306源核验 | 覆盖冲销/批准/纠正、证据损坏、游标变化、撤权；非原生HTTP/JWT验收 |
| 正式0160多代规范服务候选 | 43项恢复/版本头、6项多代/防伪/封存、81项路由/启动检查通过；早期25文件候选另210项通过 | 不把不同快照合并为同一次回归 |
| 正式0160原生generations | `artifacts/loss-multigeneration-release-next/native-{quantity,serial}-generations-verified-v2.json`：三轮、九请求、封存、双API并发单赢家、权限回滚、迁移历史保留均通过，测试库正常停止 | 1305外层/1304内层源；内层仅省略Alembic README。数量/SN的seal_retention也已终态通过；四场景证据齐全 |
| 审计批读优化 | `artifacts/audit-chain-batched-read-next/fixture-v2-verified.json`：34通过；`native-verified-v1.json`：15次原生新旧结果一致，SELECT 261→4，三类审计改写均拒绝 | 已应用主树；真实报损组合数量/SN各9条新旧回查一致且只读，测试库已停止，`business-{quantity,serial}-verified-v1.json`；当前组合回归运行中，非500用户验收 |

两个HTTP入口为 `/api/v1/stock-operations/loss-reports/dispositions/request-lookup` 和 `/api/v1/stock-operations/loss-reports/derived-returns/request-lookup`。都要求完整原请求，`retry_permitted=false`、`result_scope=original_command`，found/not_found/sealed相互区分，响应不泄露原始键或完整命令。

## 当前验证及在跑任务

上一批1937源主树：基础检查90通过、恢复HTTP92通过；原生退回数量/SN两组通过，均核验真实API角色、READ ONLY事务、200/409/403、迁移回环、历史保留及正常停库。证据：`artifacts/loss-multigeneration-main-integration-next/{runtime,http}-verified-v1.json`、`native-{quantity,serial}-return-verified-v1.json`。

上一批原生处置失败是旧测试仍匹配0150冻结约束文字，实际数据库正确返回0159约束且SQLSTATE=23514。两个失败库已正常停止，原始失败保留于同目录 `native-boundary-expectation-failure-v1.json`。三个测试文件已修正并应用，不改业务或SQL约束；本轮会重跑失败场景，尚不能声称处置原生已通过。

新接口候选证据：`artifacts/loss-execution-command-http-next/verified-v1.json` 44通过；`sources-verified-v1.json` 28通过。已应用主树：六个预览/执行/请求封存接口，以及 `GET /api/v1/stock-operations/loss-reports/execution-sources/{operation_id}`。来源接口返回准确总部决定引用、明确标注的原处置事实、真实且经当前保管责任核验的退回路线；未核验路线返回unavailable，不能猜UUID。

当前任务均在 `artifacts/durable-development-gates/` 下，固定主树1947个非Markdown文件。完成前不改被固定源；不要因日志静默重启。

| 任务 | worker / 当前首步child PID | 范围 |
| --- | --- | --- |
| `loss-execution-main-focused-v1` | 12689 / 12694 | 处置44、来源28、审计34、恢复92、冷导入4，预期202项 |
| `loss-execution-main-quantity-v1` | 12690 / 13717 | HTTP处置因测试事务设置失败；HTTP退回运行中，之后三种处置/共享冻结份额/恢复门禁 |
| `loss-execution-main-serial-v1` | 12691 / 13755 | 同上，SN独立测试库 |

准确指针：`artifacts/loss-execution-command-http-next/main-application-v1/validation-current.json`。三个原生步骤各用全新PG16库；首步child仅代表启动时，后续以state和真实ps为准。原生HTTP辅助门禁使用真实数据库角色、真实权限、真实提交；仅登录身份和一次提交后断联为模拟。不能当作真实JWT/短信或生产验收。

**新发现的测试设置错误**：两个 `http-disposition` 在preview处返回503，PostgreSQL明确拒绝 `SELECT FOR UPDATE` in READ ONLY。预览依赖现有 `_validated_opening_evidence` 的期初证据锁，此锁是原生预览门禁已确认的要求；新HTTP辅助门禁误将preview与request-lookup一同设为数据库READ ONLY。修正仅针对辅助门禁：GET来源/请求恢复仍为数据库READ ONLY；preview保留行锁，但逐条只允许SELECT、禁止COMMIT、禁止ORM写入并核对前后事实。候选未应用：`artifacts/loss-execution-command-http-next/native-http-v2/pg16_loss_execution_http_gate.py`；完整失败、两个停库和before/after摘要：`native-http-preview-transaction-failure-v1.json`。不能放宽真正的请求恢复只读要求，也不能移除期初证据锁或将503当成功。

## 下一步顺序

1. 收当前202项聚焦及两个原生worker的每步终态。逐步核验退出码、1947源摘要、stdout/checks、实际PG16版本与正常停库。worker会继续执行后续步骤，必须区分整组failed与个别step通过。
2. 当前源固定解除后应用上述native-http-v2辅助门禁修正，重跑四个HTTP写场景；生产服务不因此改动。其他失败按准确日志修复，保留原始证据。证据齐全后才提交，不丢弃任何改动。
3. 主源固定解除后应用并验证 `artifacts/loss-execution-command-http-next/ci-next/application-review.json`：4个CI文件草案，为数量/SN增加execution_http_disposition和execution_http_return矩阵。草案尚未测试，GitHub未运行。
4. 连接H5/小程序处置页面：由新来源接口取得准确决定ID、原审批hash和可选路线；选择批准的动作，预览，单独确认；完整原请求必须先可靠保存再发送。未知结果只回查，封存单独确认；不能将历史原处置结果显示为当前库存，也不能把派生退回显示为已发货/入库。
5. 继续纠正批准/执行独立封存、退回补偿、报废/失而复得。`loss-history-proof-reuse-next`仍未集成，不与已验证审计批读混淆。
6. 继续完整基线审计：真实角色范围、微信/短信/通知/附件、正式迁移与期初、准确发布SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO/RTO及恢复回滚演练。当前不是上线验收完成。

## 历史与故障记录

- 本次整理前的交接内容已逐字保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_132445.md)；保留摘要在 `artifacts/loss-formal-application-next/handoff-archive-20261001_132445.json`。
- 功能缺口：[报损纠正范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)、[正式基线审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)。其他审计中的旧段落只代表当时状态。
- 已修复的API读取迁移表权限错误、审计fixture非法流、启动前manifest路径错误和README清单范围差异都有原始回执；未扩权、未删除约束、未修改历史库存记录。
- 机器可读状态：`artifacts/loss-formal-application-next/continuation.json`。有新状态时优先更新本入口及对应准确回执，避免继续叠加相互矛盾的“当前状态”段落。
