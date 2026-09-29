# RSC 个人仓开发交接

核对时间：2026-09-30 00:03（Asia/Shanghai）。本页为接续入口；历史记录不覆盖当前 Git、准确 SHA 的 CI 和生产回读。

**当前结论：报损收货、独立入库和请求恢复已完成本地验证。HTTP 提交/封存 `3fa414f` 与 PG16 拆分 `3e67e51` 均已推送，远端准确回读为 `3e67e51714d301bf66126b8ec55741e378d0720c`；该 SHA 客户端 CI 已成功，PG16 最近观察 17 分支成功（含 control）、4 个运行中、静态 1 因 runner shutdown 失败；另有下述审批恢复候选尚未提交。正式上线未放行。**

## 0. 接手先看

### 当前未提交切片：区域/总部审批只读请求恢复

已新增两阶段 `/regional-reviews/request-lookup` 与 `/headquarters-reviews/request-lookup`。原审批人须有当前区域/全国角色及 scoped read；恢复不依赖审批写权限。精确绑定原单、人员、请求 ID、幂等键及内容/提交摘要，重建不可变审批、审计、状态、outbox 和通知证据，游标变化丢弃结果，所有结果 `retry_permitted=false`。

- 新增服务 `backend/app/formal_services/stock_loss_review_recovery.py`、合同及路由；`test_stock_loss_review_recovery.py` 的数量/SN 合成回归 **104 passed / 356.29 秒**，原始日志 `artifacts/loss-review-recovery/focused-v1.log`。
- `pg16_stock_loss_review_recovery_gate.py` 已接到已有区域/总部 PG16 门禁；使用实际 API 角色 `SET TRANSACTION READ ONLY` 的 HTTP 调用，覆盖已提交/未找到、撤销写权限、读权限撤销、错摘要及库存/历史不变。数量件已完整通过：区域/总部回查、空库往返、历史保留、实际权限及并发均通过；实例 `run-9zogl5op` 已 stopped/checks=passed/serverExitCode=0，sourceDrift=[]。SN 在新实例 `run-bcp7pjwm` 继续运行，整体终态不能据此记为通过。
- 自有原生数量/SN 运行会话 **63519**；原提交/恢复 HTTP 兼容回归 **46 passed / 147.02 秒，35039 已退出 0**。日志与当前实例坐标见 `artifacts/loss-review-recovery/continuation.json`。先轮询实际句柄；重启后先核对进程与证据。运行期间不要修改非文档源码，不因观察超时重启。
- 仓库安全检查 PASS（1906 文件）。本批没有新迁移、审批写入口或生产权限种子。区域/总部独立永久封存及迟到写入数据库互斥、待办/详情和完整客户端仍未完成；本次只读恢复不能替代这些要求。新候选不是已通过客户端 CI 的 `3e67e51`。

1. **继续使用现有工作树。** 本地/已核验远端 HEAD `3e67e51`，父提交 `3fa414f`；详见 `artifacts/pg16-runtime-suites/commit-evidence.json`。本次推送后只更新交接文档，未再次推送以免打断新 CI；禁止覆盖本地改动。
2. **原生 control 已结束。** 会话 `99829` 实际返回 exit 0；`run-8v_g0tog` 为 stopped/checks=passed/serverExitCode=0，`checks.json` 为 passed/sourceDrift=[]。不要重启旧库或继续轮询已结束句柄。
3. **本批证据：** 102 项聚焦、12 项调用点复验（有重叠不相加）；1725 份非文档源码零漂移；共享 control 1345.474 秒通过。迁移前缀、库存主体与清理保持原有完整 AST 顺序；control 仅新增真实前置历史及保留证明。详见第 2 节。
4. **下一步先取得远端证据。** 新 SHA 已推送，先核验三个 runtime、三个静态、16 个报损分支及客户端。之后补区域/总部审批的请求恢复与封存、报损发件侧。不要重复实现已完成的收货与入库。

本批涉及以下 10 个文件；提交状态以 Git 为准，路径相对仓库根：

| 文件 | 本批用途 |
| --- | --- |
| `.github/workflows/postgresql16-release-gate.yml` | runtime 三分支及汇总 |
| `cloud_oam/backend/tests/test_postgresql16_release_gate.py` | 分支入口、共享 control 和历史降级阻断选择 |
| `cloud_oam/backend/tests/test_pg16_workflow_topology.py` | 分支及未授权入口回归 |
| `cloud_oam/backend/tests/test_stocktake_history_pg_acceptance.py` | 历史检查指向新迁移入口 |
| `cloud_oam/backend/tests/pg16_runtime_control_history.py`（新） | 真实前置库存和历史不变性证明 |
| `cloud_oam/backend/tests/test_pg16_retention_selection.py`（新） | 降级路径选择回归 |
| `cloud_oam/scripts/run_local_pg16_control_runtime_checks.py`（新） | 自有临时 PG16 共享 control 验证 |
| `cloud_oam/docs/CONTINUE_DEVELOPMENT.md` | 当前接续入口 |
| `cloud_oam/docs/FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md` | 正式基线剩余缺口 |
| `cloud_oam/docs/FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md` | UAT 和生产证据边界 |

## 1. 工作位置与固定边界

- 工作树：`~/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`。下文路径默认相对 `cloud_oam/`。
- 先完整阅读[正式 V1.0 需求基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)及根目录 [AGENTS.md](../../AGENTS.md)。不得 reset、revert、丢弃或覆盖未提交改动。
- 公开首页为“交流备件知识大全”，无登录界面；星星后台按钮进入 `https://rscwz.cn/xx`。`/xx` 是路径，不是另一个二级域名。个人主体小程序当前仅公开知识查询，私有业务页面不进入公开包。
- 飞书知识源按用户要求后置；真实目录及公开性审核未完成，不宣称公开知识站已验收。若恢复飞书读取，遵循 AGENTS.md 的 NIO Chat 托管只读路径及凭据边界。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入账、通知、对账分别存证。结果未知先精确回查原请求，禁止盲目重发。
- 本地 PG16 只用全新自有临时实例；不传外部/生产 DSN，不重启已停止的历史库，不伪装 GitHub CI 在本机执行破坏性门禁。
- 不把开发授权扩成真实短信、外部业务写入、生产迁移或切换授权。凭据、生产数据和运行日志不进 Git。
- 历史交接记录的额度约定为剩余 10% 时停止新增开发并交接；接手核验当前额度，不继承历史百分比。

## 2. 版本与远端门禁

本轮推送：普通 Git 在 45 秒超时后精确回读远端仍为 `8990ebd`，才使用更新后的 Git Data API 脚本；逐个验证 `3fa414f` / `3e67e51` 原始 tree 和 commit SHA，`force=false` 更新后再次回读一致。证据：`artifacts/pg16-runtime-suites/push-attempt.json`、`push-result.json`、`commit-evidence.json`。旧脚本未原样重跑。

当前准确候选 `3e67e51714d301bf66126b8ec55741e378d0720c`：

- [客户端 CI 36593125653](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125653)：completed / success（已回读）。
- [PG16 CI 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765)：最后观察 in_progress，17 分支成功（control 与 16 个报损）、4 个运行、静态 1 失败；必须收齐三个 runtime、三个静态和 16 个报损分支，不能继承下表旧版本结果。

新 CI 静态 1（job `109491289567`）日志确认 `runner has received a shutdown signal` 后取消，未观察到断言失败。原始日志含终端控制符，使用 CLI 明示允许后保存并清理展示，证据 `artifacts/pg16-runtime-suites/ci-static-1.raw.log` / `ci-static-1.log`。单 job 重跑请求返回 `job cannot be rerun`；保持其他运行，待工作流终态再只重跑失败任务，不把运行器错误改成通过。

下表 `8990ebd` 等状态为上一轮证据，保留用于说明历史失败；当前候选以上述两个链接为准。

| 对象 | 本次确认的状态 | 证据/边界 |
| --- | --- | --- |
| 0155 功能提交 | `d76efd135bb7e0f476a187809e14fd5b41e3c235` | 报损收货、独立入库与恢复 |
| 上一已推送分支头 | `8990ebddfe16d11265e7c6524fa7357d2771e26e` | 包含 0155 和交接整理；Git Data API 保留原 commit/tree，force=false，已精确回读 |
| HTTP 基础提交 | `3fa414f43a9818a424c34401fcb7964f64cc7856`，已随后续 `3e67e51` 推送 | `artifacts/loss-http-submit/commit-evidence.json`；由后续准确候选的 CI 验证 |
| PG16 拆分候选 | runtime 三分支、workflow/拓扑测试、真实前置库存夹具及原生 control 运行器 | 102 项聚焦、12 项调用点复验和共享 control 原生通过；完整新 SHA CI 待验证 |
| 迁移 head | `20261204_0155`，父节点 `20261203_0154` | 本轮 HTTP 接线不新增迁移或生产权限种子 |
| 8990ebd 客户端 CI | `36560246356`：completed / success | [准确运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36560246356) |
| 8990ebd PG16 CI | `36560246385`：整体仍运行；14 个报损分支成功 | [准确运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36560246385)；runtime 仍运行，静态 0/1/2 已失败；0/1 原始日志确认 runner 收到 shutdown signal 后取消，2 的日志 API 返回 404/BlobNotFound，失败原因未确认；不能视为通过 |
| 上一版 a03761e CI 终态 | `36521873050`：failure | [历史运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36521873050)；静态 0 runner 失联，runtime 超过 6 小时取消，12 个报损分支成功 |
| 生产状态 | 未部署本批，没有正式上线验收 | 本地、准确 SHA CI、真实业务 UAT、生产回读分别核验 |

Git 推送超时后先回读远端仍为 a03761e，才使用既有 Git Data API 精确提交并回读 8990ebd；没有 force push。证据：`artifacts/continue-0155-release/push-result.json`。上一版 runtime 原始日志和阶段耗时分析同目录；222 个阶段完成，但后续检查未执行，不能按通过验收。退回出库单阶段约 2250 秒，首次账户边界约 1430 秒，累计触及 6 小时上限；需要在保留全部场景和迁移顺序依赖的前提下拆分综合门禁，不能仅跳过慢检查。

### 接续断点：PG16 拆分本地验证完成，远端待验

本批代码把原单体入口抽成 `_run_migration_suite()`、`_run_inventory_suite()`、`_run_control_suite()`，由 `RSC_PG16_RUNTIME_SUITE` 显式选择；inventory/control 各自准备全新临时库。原测试入口名称保留。

工作流现已接上三个必需分支 `migrations / inventory / control`，每个分支使用独立的新 PG16 服务，`fail-fast: false`，最终汇总继续要求 runtime/loss/static 全部成功。入口未授权、缺失或非法 suite 均在数据库准备前失败；业务异常原样传到门禁。**本地聚焦通过不等于三个分支已在准确 SHA 上跑通。**

- `artifacts/pg16-runtime-suites/focused-v3.log`：拓扑、真实 pytest 入口、历史阻断版本选择、阶段记录和完整历史测试 **102 passed**。早期 `static-v1.log` 为 35 项；两组有重叠，不能相加，也不与旧 HTTP 的 86/13 项混算。
- `extraction-v3.json`：对照本地 HEAD 原入口，67 个断言、749 个调用的 AST 多重集无遗漏；此前 `extraction-v2.json` 还核对抽取函数无未定义全局依赖。旧 `extraction-proof.json` 与 `original-runtime.py` 继续保留。文本统计不等于数据库语义验证。
- 已抽出 `_run_control_business_checks()` 供 CI/native 共同执行；`pg16_runtime_control_history.py` 通过真实来源发布、实盘、区域/总部复核、过账和差异复核建立一件既有库存，单独生成通知事实，末尾核对旧库存/通知/outbox 行未改动。没有直接写余额、跳过触发器或调用真实通知渠道。
- 新 `scripts/run_local_pg16_control_runtime_checks.py` 只接受 PostgreSQL 二进制路径，创建自有 Unix socket 临时库；仅将共享检查的连接坐标绑定到该实例，不伪造 GitHub 环境、不运行 CI 破坏性 bootstrap、不接外部 DSN。
- `native-control-v1.log`：实际迁移及一件库存前置已完成，但夹具误把期初 outbox 等同通知接收人记录，断言失败。已改为单独调用通知事实服务；原实例 `run-y23c1gyi` 已 stopped / checks=failed / serverExitCode=0，不能计通过。
- `native-control-v2.log`：在 `run-g1oblc0p` 完成真实前置库存、来源发布/权限、控制数标准化、并发和请求封存检查；进入每日对账后，旧门禁把 0128 封存错误当成降至 0129 的首个阻断。PG 日志实际返回 **0130 daily facts retention**，证明是门禁的路径判断错误。实例已 stopped / checks=failed / serverExitCode=0，不能记为通过。
- 已修复 `_retention_chain_blocker()`：只选择目标降级实际经过、且比本次必验版本更高的已存在阻断；必验版本自身的真实拒绝和更老迁移独立证明保持不变。新增测试覆盖每日/导入目标、非相邻降级、0149/0151 较新事实优先与非法范围。旧迁移未改。
- 已通过真实 pytest 子进程复现原入口缺少安全确认时 **1 skipped / exit 0**（`unacknowledged-entry-v1.log`）。现移除模块级跳过，入口仍先要求真实 hosted 确认；未确认返回 exit 1 且不进入数据库。相关回归包含在 102 项中，不伪造 CI 环境来执行本机破坏性门禁。
- 旧调用点回归 v1：**11 passed / 1 failed / 294 deselected**；唯一失败是 `test_stocktake_history_pg_acceptance.py:97` 仍检查旧单体入口。已改查迁移分支，完整该文件已在 102 项中通过；其他旧调用点的 v3 复验已退出 0：**12 passed / 294 deselected**，见 `callsite-regression-v3.log`；与 102 项有重叠，不累加。
- `native-control-v3.log` / `native-terminal-v3.json`：全新 `run-8v_g0tog` 完整共享 control **passed**，进程 exit 0，数据库 stopped/checks=passed/serverExitCode=0，结束于 2026-09-29 20:36:35（北京时间），本次实际回收终态于 23:42。包含来源、控制数、请求封存、每日 12 场景、短信配置、真实 XLSX 期初、报表和导入历史保留；未发送真实短信。1725 份源码与开始清单一致，sourceDrift=[]，前置库存/通知/outbox 历史保留。
- `ordered-extraction-v3.json`：直接对照 Git `3fa414f`，106 条迁移顶层语句完全一致；库存 196 条顶层语句及引擎/清理结构完全一致；control 尾部除三条新增历史准备/校验语句外 AST 顺序完全一致。此证据证明抽取顺序，不能代替 hosted 三分支运行。
- `safety-v3.log`：修复后 PASS（1903 文件）；交接后同范围安全检查亦通过。无本批业务迁移、生产权限种子或供应商实发。
- 本批已提交并推送 `3e67e51`；准确新 SHA 的完整三分支、静态三片、16 个报损分支和客户端 CI 仍须远端验证。前两轮失败记录保留，不计通过。
- workflow 的 `cancel-in-progress: true` 会在同分支新推送时取消旧运行。先重新核验在跑 CI 与证据；不要为赶进度中断尚需取证的运行。
- 旧 `artifacts/continue-0155-release/push-exact-api.py` 固定旧 BASE 和提交数，不可原样重跑。常规 push 失败须先精确回读远端，再决定恢复方式。

本次只读 GitHub 快照、未提交文件清单、源码漂移核验和原生阶段观察保存在 `artifacts/handoff-20260929-202715/ci-snapshot.json`、`verification.json`。该 20:27 快照中 runtime/静态 2 仍运行；23:42 重新核验静态 2 也已失败、runtime 仍运行，客户端和 14 个报损分支成功。早期 `artifacts/handoff-20260929/` 保留。快照均只代表观察时点，运行中的检查必须重新取终态。

## 3. 最新一批完成了什么

本轮新增正式 `POST /api/v1/stock-operations/loss-reports` 与 `/request-seal`：当前本人/submit_loss 权限重检，请求头与原 request/key 一致性，COMMIT 完成才返回成功；数据库/审计故障返回结果未确认，保留原请求回查，不自动重发。封存若发现原单已提交，返回原单而不另造封存。区域核实/总部审批、客户端报损页面及发件接口仍待补齐。

新增 `test_stock_loss_write_routes.py`、`pg16_stock_loss_write_http_gate.py` 和 `run_local_pg16_stock_loss_http_checks.py`。后端路由/来源/回查/封存 **86 passed**，CI 拓扑 **13 passed**。数量 `run-fn3k7kzl`、SN `run-xjv3qzx8` 实际 PG16 API 角色 HTTP 通过：真实提交回执丢失后找回、提交前故障回滚、封存回执丢失后找回、迟到提交拒绝、精确重试不重复冻结、撤销写权限后仍能按读权限回查。两库 stopped / serverExitCode=0，1722 份非文档源码零漂移；首次探测因测试配置源码更新主动中止并正常停库，不计通过。首次 pytest 因工作目录错误无法导入 app，改在 backend 执行后通过，原日志保留。证据见 `artifacts/loss-http-submit/evidence-index.json` 与 `native-terminal.json`。CI 增加独立数量/SN `submission_http`，当前候选矩阵为 **16 分支**；已上传版本仍为 14 分支。

下面是已推送 0155 的完成范围：

1. 报损包裹接收列表/详情及收货预检、提交、回查、封存支持真实五字段来源：`origin_kind`、`loss_operation_id`、`loss_line_id`、`headquarters_decision_id`、`disposition_id`。保留 new/used/damaged 成色，不补假工单，不与普通工单合同混用。
2. 收货只保存实物验收，不变更库存；独立入库经统一流水过账，覆盖数量件和 SN、新件区域账户首次创建。
3. 客户端恢复记录按报损处置与包裹隔离，保留原 request/hash；网络结果未知、切页及再次进入先查询。收货和入库分别封存，迟到命令拒绝。
4. 0155 迁移补齐 PostgreSQL 当前授权与历史事实证明、精确通知接收人和来源互斥；内部证明函数不授 API 直接执行权。SQLite 对不能等价证明的报损写入失败关闭。未修改旧迁移。
5. 0155 CI 新增数量/SN `return_receipt`，已推送的 `8990ebd` 报损矩阵为 14 分支；本地 `3fa414f` 再增加 `submission_http` 后为 16 分支。远端 a03761e 的 12 分支不包含这些增量。

| 代码入口 | 用途 |
| --- | --- |
| [0155 迁移](../backend/alembic/versions/20261204_0155_stock_loss_return_receipts.py) | SQL 证明、封存、账户准入与升降级 |
| [来源解析](../backend/app/formal_services/stock_return_receipt_origin.py)、[接收投影](../backend/app/formal_services/stock_return_receiving.py) | 报损来源与普通工单隔离 |
| [收货合同](../backend/app/stock_return_receipt_schemas.py)、[入库合同](../backend/app/stock_return_inbound_schemas.py) | HTTP 输入输出边界 |
| [客户端来源工具](../miniprogram/utils/loss-return-origin.js) | 来源比较及恢复坐标 |
| [服务回归](../backend/tests/test_stock_loss_return_receipt.py)、[迁移回归](../backend/tests/test_stock_loss_return_receipt_migration.py) | 数量/SN、事务与合同验证 |
| [自有 PG16 运行器](../scripts/run_local_pg16_stock_loss_return_receipt_checks.py)、[原生检查](../backend/tests/pg16_stock_loss_return_receipt_gate.py) | 两类独立新库、权限、并发、迁移及历史证明 |

## 4. 本地证据索引与测试口径

日志根目录：`artifacts/loss-receipt-inbound-0155/`。这些是 **ignored 本地证据，不随 Git 克隆**；交接到另一台机器须取得受控证据或重跑，不能将缺失文件视为通过。

| 证据文件 | 结果及范围 |
| --- | --- |
| `service-v6.log` | 11 passed，含 2 个收货 HTTP 合同测试；`http-v5.log` 与其重叠，不加总 |
| `ordinary-regression-v1.log` | 80 passed，普通工单收货、入库、封存、权限和 HTTP 兼容 |
| `mini-all-v2.log` / `mini-focused-v3.log` | 小程序全量 1079 passed；后续来源比较改动聚焦 33 passed，属于复验，不加总 |
| `migration-v3.log` / `orm-head-v2.log` | 20 项迁移回归、3 项 ORM/迁移图通过 |
| `security-focused-v2.log` / `security-current-account-v3.log` | 首轮 342 passed / 2 failed；旧测试账户函数哈希修正后两项单独通过。不得写成同一进程 344 全绿 |
| `loss-inbound-http-v2.log` | 额外数量/SN 入库 HTTP 与 Node 合同 2 passed；辅助 `test_loss_inbound_http.py` 位于 ignored 证据目录 |
| `native-v4-terminal.json` | 数量/SN 原生 PG16 均 exit 0，checks=passed、sourceDrift=[]，两实例 stopped / serverExitCode=0 |
| `final-source-v4.json` | 1719 份非文档源码摘要；文档整理后另核对无漂移 |
| `repository-safety-final.log` / `pip-check.log` | 功能提交前仓库安全 1896 文件 PASS；依赖无冲突。文档整理后的安全检查单独记录 |
| `completed-checks.json` / `verification-log-sha256.json` | 各次终态、重叠范围与原始日志摘要索引 |

原生实例目录：`artifacts/local-stock-loss-return-receipt-pg16/checks/run-zam7t1r3`（数量）和 `run-w437n0sp`（SN）。只读检查结果，不复用旧库。

原生已证明：空库双向迁移目录/ACL 复原、有历史拒绝降级、API 不能执行私有证明、实时授权过期拒绝、4 类锁、并发超量单赢家、同键精确重试、收货库存中立、独立入库、原请求恢复及封存阻止迟到写入。数量/SN 各有 5/6 类畸形收货及 2 类畸形入库在 COMMIT 回滚；入库后历史可回读。

早期失败日志保留。v3 原生检查因执行中源码变化最终失败，不能算完整通过；提交依据为 v4 零漂移终态。测试总数不跨批、跨 SHA、跨重叠用例相加。

## 5. 接下来按什么顺序做

| 顺序 | 工作 | 完成标准 |
| --- | --- | --- |
| 1 | 发布已验证的 PG16 拆分并调查静态 runner 取消 | 本地 control 与完整抽取顺序已证明；准确新 SHA 仍须通过三个 runtime 和静态矩阵。0/1 日志只证实 shutdown signal，静态 2 日志 API 返回 404/BlobNotFound，失败原因待取证 |
| 2 | 验证准确版本远端门禁 | HTTP 增量已提交 `3fa414f`，无需重复提交；拆分证据齐备后形成新提交，再推送并验证准确 SHA 的客户端、完整 runtime/静态及 16 个报损分支 |
| 3 | 补报损发起/审批正式接口及请求恢复 | 提交及永久封存 HTTP 本轮已补；[报损路由](../backend/app/routers/formal_stock_losses.py) 的区域/总部审批及相应请求恢复仍缺，客户端完整报损流仍缺。沿用角色/范围、同键原子性及旧权限拒绝 |
| 4 | 补报损发件侧出库、发运接口/客户端恢复 | [退回路由](../backend/app/routers/formal_stock_returns.py) 发件坐标仍要求工单；使用精确报损来源，分别回查/封存，数量/SN 与未知结果端到端验收 |
| 5 | 补报废反向、人员间调拨及离职交接 | 报废审批/处置/反向分别留事实；独立人员调拨闭环；交接案、唯一未结、双方确认、未结事项清理、个人仓归零及双层关闭 |
| 6 | 继续基线逐项审计及真实试点准备 | 报表范围/筛选/订阅、打印、真实来源/身份/期初、短信及其他通知渠道、OSS/KMS、设备 UAT、备份回滚、性能和连续三天对账逐项补证 |

下一批开发先闭合一个可验收切片，不同时打开上述全部写入口。接收侧 0155 本地已完成，不能重写成“仍无报损收货”；也不能据此宣称整个报损闭环或正式上线完成。

## 6. 上线环境与仍缺的现场证据

- 用户已确认旧备份脚本连接的 `118.31.37.87` 是目标机，不再重复询问。**主机状态仅有 2026-09-24 历史只读快照**：Ubuntu 24.04/x86_64，旧 star-oam 占用 80/443；本次未连接或修改服务器。部署前须重新核验容器、端口、网络、数据卷、镜像和回滚点。
- 2026-09-24 公网曾仍显示旧登录页，准确新版本尚无双入口公网验收。不能把当时 HTTP 200 或旧镜像可达当作新版本部署成功。
- 备案截图显示申请审核通过；最终备案记录、实际域名/网站名称、公开内容及 HTTPS 需分别核对。截图不代替部署或上线验收。
- 短信认证已有配置和合成验证；真实 PNVS/身份映射/最小权限、实发及回读尚无验收证据，不回答“短信已搞定”。通知 outbox 和恢复也不等于真实渠道送达。
- 真实 OSS、KMS、期初盘点/历史迁移、OAM 批次语义、设备 UAT、500 用户压测、RPO≤5 分钟/RTO≤2 小时与演练、连续至少三天差异解释仍分别待验。
- 受邀 `/xx` 小范围试点与全量正式 V1.0 是不同放行范围，均需准确候选和书面验收；不提供未经依据的完成百分比或上线时间承诺。

## 7. 接手命令与文档导航

在指定工作树执行只读检查：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git status --short
git log -5 --oneline
git diff --stat
git diff --check
```

已完成的 control 证据可只读查看（工作目录 `cloud_oam/`）：

```sh
cat artifacts/pg16-runtime-suites/native-terminal-v3.json
cat artifacts/local-control-runtime-pg16/checks/run-8v_g0tog/checks.json
cat artifacts/local-control-runtime-pg16/checks/run-8v_g0tog/cluster-state.json
```

仅有相关源码修改或新失败时再创建新自有实例；不得重启旧库：

```sh
.venv/bin/python scripts/run_local_pg16_control_runtime_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

阅读基线后，先查本页证据目录及 GitHub 链接。若需要重跑本地报损 PG16，在 `cloud_oam/` 使用已安装的 PG16 二进制；例：

```sh
.venv/bin/python scripts/run_local_pg16_stock_loss_return_receipt_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

运行前确认该路径存在、测试配置及 Node 可用，冻结非文档源码；脚本自动创建并停止两类自有实例，保存完整终态。不要为了文档改动重复运行整批业务门禁。

HTTP 提交切片的聚焦命令（仅在需要验证相关代码时运行）：

```sh
# 工作目录：cloud_oam/backend
../.venv/bin/python -m pytest -q tests/test_stock_loss_write_routes.py tests/test_pg16_workflow_topology.py
# 工作目录：cloud_oam
.venv/bin/python scripts/run_local_pg16_stock_loss_http_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

以上聚焦命令不能替代完整 runtime 拆分验证；禁止设置虚假的 GitHub 环境标记来绕过本机破坏性测试限制。文档交接时只核对既有结果；之后的本轮拆分聚焦及原生验证记录在第 2 节，不能与旧功能测试混算。

- [正式基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)：当前业务缺口与详细历史审计。
- [V1.0 UAT 与上线证据矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)：现场验收清单；旧日期的候选状态不覆盖本页。
- [部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[公网双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)：目标准备、健康、路由和切换门禁。
- [短信认证专项](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)：供应商/渠道/恢复的独立缺口。
- [截至本次整理前的完整交接原文](CONTINUE_DEVELOPMENT_HISTORY_20260929.md)：3048 行逐字保留，含过期 HEAD、阶段性失败和当时待确认记录；仅作历史证据，不执行其中旧“下一步”。
- [更早交接归档](CONTINUE_DEVELOPMENT_HISTORY_20260921.md)：早期实现与验证过程。

维护方式：更新本页现行值及核验时间，详细日志留在受控证据目录；不再往入口重复追加“最新/当前”时间块。历史状态不可覆盖实际源码、准确 SHA 的 CI 或生产回读。
