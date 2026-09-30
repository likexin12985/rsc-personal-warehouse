# RSC 个人仓开发交接

核验时间：2026-09-30 08:00（Asia/Shanghai）。本页是当前接续入口；历史文档和历史 CI 不覆盖本页核验值。下文路径默认相对 `cloud_oam/`。

**当前结论：报损收货、独立入库及其请求恢复已实现并完成本地验证；提交/封存 HTTP 与 PG16 门禁拆分已推送。区域/总部审批只读请求恢复已完成本地数量件、SN 验证并本地提交 `b4a964e`，尚未推送；下一批 0156 独立审批封存已有服务/迁移候选，数量件与 SN 本地原生门禁均通过，但仍有一项历史链测试失败及未应用的 HTTP/并发补丁。父版本准确 SHA 的 CI 已结束但整体失败；新候选完整 CI、真实业务验收和生产部署尚未完成，不能宣布上线。**

## 最新开发断点（本次续跑优先于下方 08:00 交接快照）

- 四份 HTTP/head、并发、原生 HTTP、CI 草案和权限目录修正均已应用；不得再次应用或重跑生成器。两阶段审批/永久封存写入口已接入，成功回执在 COMMIT 后返回；结果未知仅回查原请求。
- HTTP/合同复验 **95 passed**；历史链与 CI 拓扑 **28 passed**。权限/head/运维回归 **389 passed / 2 failed**，两处是预期表/触发器清单漏记 0156，已修复并定点 **2 passed**，保留原失败日志，不混算总数。
- 原生 v3 会话 5345 已退出 1，`run-d2m7zif3` 正常停库：三写者的第二个等待者被队列中的另一等待者阻塞，原“直接阻塞者”断言失败。全新临时库 `lock-queue-checks/run-nvz02ob7` 已复现准确三节点阻塞链；当前改为要求真实阻塞链最终指向准确原写事务，仍验证唯一持久结果及迟到审批数据库拒绝。
- v4 会话 5283 因新发现的 SQL/Python 文本合同差异主动停止，`run-o5vvstgn` 正常停库、不计通过。仅修正未提交 0156：完整 Unicode 首尾空白和理由控制字符拒绝，运行期私有函数摘要同步更新。独立 PG16 验证 29 种空白共 58 个边界、回车拒绝和合法多行中文保留；新增合同 **41 passed**、迁移 **4 passed**。历史迁移未修改。
- 完整数量/SN 原生 **28123** 正在运行，日志 `artifacts/loss-review-seals-0156/native-v5.log`；数量库 `run-flxjwk3f`，冻结 **1739 份非 Markdown 源码**。这轮尚无完整终态；v2 已通过只覆盖旧 1736 份源码。运行中不修改非 Markdown 源码、不因观察超时重启、不重用已停旧库。
- 仓库安全 v3 PASS（1918 文件），最后文本改动后的复验会话 **89350 已退出 0，PASS（1918 文件）**，见 `repository-safety-v5.log`。
- 父版本 `3e67e51` CI 第 2 次运行中；静态 1（job `109678169916`）再次失败；本轮日志已确认 runner shutdown / operation canceled，底层原因未确认，静态 0/2 仍在跑。日志 `ci-attempt2-static1.log`，快照 `parent-ci-rerun-snapshot.json`。
- 当前仍未提交、未推送、未部署。下一步先收齐 v5、修正剩余真实失败、完成审查后再本地提交；当前候选仍需其准确 SHA 的完整 CI。接着补授权待办/详情与私有 H5 页面，不能把私有小程序接收页面算作 H5 已上线。
- 当前句柄与接续动作以 `artifacts/loss-review-seals-0156/continuation.json` 为准；下方“未应用草案”和数量统计仅是 08:00 历史快照。

## 1. 接手位置与约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`。禁止 reset、revert、丢弃或覆盖未提交改动。
- 本地 HEAD：`b4a964e5443439794429de6fee85281d9b7faa5f`，父提交/当前远端 CI SHA 为 `3e67e51714d301bf66126b8ec55741e378d0720c`。`b4a964e` 已本地提交但未推送（当时为避免取消父版本 CI）；父版本现已终结，下次推送前仍精确回读远端。
- 先完整阅读[正式 V1.0 需求与架构基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)和 [AGENTS.md](../../AGENTS.md)。
- 公开首页：“交流备件知识大全”，无登录；星星后台按钮跳转 `https://rscwz.cn/xx`。`/xx` 是路径。公开小程序仅知识查询，私有仓库页面不进入公开包。飞书知识源后置。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入库、通知送达与对账分别存证。未知结果先精确回查原请求，不自动重放。
- 开发与本地验证不授权真实短信、外部业务写入或生产迁移/部署。生产凭据、业务数据和运行日志不进 Git。
- 本地 PG16 仅新建自有临时实例；不接生产 DSN、不复用已停止旧库、不伪造 GitHub 环境绕过保护。

## 2. 已完成、未完成分别是什么

| 范围 | 已完成 | 尚未完成 |
| --- | --- | --- |
| 报损接收侧（0155） | 报损来源列表/详情、收货预检/提交/回查/封存；独立入库、数量/SN、客户端恢复隔离；本地迁移/权限/并发验证 | 真实身份、真实业务 UAT 与生产验收 |
| 报损发起 HTTP（`3fa414f`） | 提交、原请求永久封存、COMMIT 后回执、结果未知回查；本地实际 API 角色 HTTP 验证 | 完整报损客户端业务闭环 |
| PG16 门禁拆分（`3e67e51`） | migrations/inventory/control 独立分支；完整检查顺序保留；本地 control 及远端 control 成功 | 完整准确 SHA 的 runtime/static 总门禁 |
| 审批只读恢复（`b4a964e`，未推送） | 区域/总部独立回查服务及 HTTP；撤销写权限后仍可按当前读权限恢复；数量/SN 本地通过 | 推送及其准确 SHA CI；独立审批封存、正式审批写 HTTP、待办/详情和客户端 |
| 报损发件侧 | 已有内部退回/出库/发运服务和相应门禁 | 正式发件 HTTP 的报损来源合同及客户端恢复闭环 |

收货只确认实物验收，入库另行过账；区域核实/总部批准均不改变库存，总部通过仍待处置。不得将以上局部完成当成全流程完成。

## 3. 当前断点与已提交证据

审批只读恢复和交接整理共 10 个文件已提交 `b4a964e`。提交时工作树 clean，证据 `artifacts/loss-review-recovery/commit-evidence.json`；仅本地提交，没有推送或部署。

**下一批未提交候选：0156 独立审批请求封存。** 已新增 ORM 事实、严格原命令合同、封存服务、只读恢复分支、两个原审批服务的迟到请求检查和新迁移 `20261205_0156`；运行期私有 SQL/ACL/触发器/摘要目录与 head 前置检查一并更新。没有修改历史迁移，没有审批写 HTTP 或生产权限种子。`git status --short` 是完整文件清单。

- 合同 31 项通过；新迁移 SQL 解析、目录、SQLite 写拒绝和保留历史 4 项通过。服务/恢复合成回归 **155 passed / 482.71 秒 / exit 0**；移除测试警告并补显式库存中立断言后，复验和 ORM/head 会话 **86332** 已退出 1：14 passed / 1 failed / 8 deselected；实际 ORM 升降级、ready head 与库存中立通过，剩余历史链断言少走 0155 父节点一层。修复已备为下述 patch，后续应用并复验，整体不能计全绿。首次 head/ORM 3 项因旧路径、父版本断言和预期表清单失败，已修正，不能将首次结果计通过。
- 原生数量/SN 会话 **15314 已退出 0**，禁止继续轮询或重启历史临时库。数量件 `run-6mzrinyg` 于 01:03:30、SN `run-rtw6agjv` 于 01:14:12（Asia/Shanghai）正常结束，两份 checks 均 passed/sourceDrift=[]，两库均 stopped/checks=passed/serverExitCode=0；本次重新核对 **1736 份非 Markdown 源码零漂移**。终态及文件 SHA256 已保存至 `artifacts/loss-review-seals-0156/native-terminal-v2.json`，源码冻结已结束。
- 两种模式的区域/总部封存均通过 4 类错误 COMMIT 回滚、授权到期、3 类完整迟到审批拒绝、只读恢复、库存/通知不变和 API 不可修改；0156 保留历史拒绝降级通过。此结果不覆盖尚未应用的补丁。上一会话 97131 因旧 head 引用被主动中断，`run-ebsinnao` stopped/checks=failed/serverExitCode=0，仍不计通过。
- 本批仍缺封存/审批双向真实并发、独立未提交/回滚读事务、更多原始碎片/错坐标覆盖、正式 HTTP COMMIT/回执丢失、独立 CI 分支及新 SHA 全部门禁。0156 原生专用分支会优先验证新封存历史阻止降级，不声称同时穿透到旧 0147/0148；默认原审批门禁保留旧迁移证明。
- 已在 ignored 证据目录准备 `pending-http-and-head.patch` 及 `test_stock_loss_review_write_routes.py`：两阶段审批/封存写接口、COMMIT 前后故障与精确回查测试草案。仅 patch 可应用检查和语法检查通过，**尚未应用、尚未执行**；另有 `pending-concurrency.patch`（双向准确锁竞争与独立未提交/回滚读取）、`pending-native-http.patch`（第二张真实原单的 API 角色 HTTP 回执丢失/回滚/撤权验证）和 `pending-ci.patch`（独立数量/SN review_seals 分支）。全部须从仓库根目录执行 patch；原生会话已终结；后续开发可按第 5 节依次检查并应用，必须形成新的源码与验证证据。
- 本批未提交、未推送、未部署。此前31项合同日志在 `artifacts/loss-review-recovery/seal-contracts-v2.log`；首次 v1 工作目录错误没有执行测试，不计通过。

下表为 `b4a964e` 已提交文件，供审查定位；当前未提交文件以实时 `git status --short` 为准。

| 路径 | 状态/用途 |
| --- | --- |
| `backend/app/formal_services/stock_loss_review_recovery.py` | 新增：区域/总部原请求只读恢复 |
| `backend/app/routers/formal_stock_losses.py` | 修改：两个审批 `request-lookup` HTTP 入口 |
| `backend/app/stock_loss_schemas.py` | 修改：严格请求坐标和阶段结果合同 |
| `backend/tests/test_stock_loss_review_recovery.py` | 新增：权限、错坐标、证据破坏、游标变化、HTTP 等回归 |
| `backend/tests/pg16_stock_loss_review_recovery_gate.py` | 新增：实际 API 角色只读事务 HTTP 证明 |
| `backend/tests/pg16_stock_loss_regional_review_gate.py` | 修改：接入区域恢复门禁 |
| `backend/tests/pg16_stock_loss_headquarters_review_gate.py` | 修改：接入总部恢复门禁 |
| `docs/CONTINUE_DEVELOPMENT.md` | 当前交接入口 |
| `docs/FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md` | 缺口审计及后续审批封存设计 |
| `docs/CONTINUE_DEVELOPMENT_HISTORY_20260930.md` | 本次整理前入口原文，逐字归档 |

两个入口为 `POST /api/v1/stock-operations/loss-reports/regional-reviews/request-lookup` 和 `.../headquarters-reviews/request-lookup`。绑定原单、原审批人、request ID、幂等键、请求摘要与提交计划摘要；核验不可变审批/审计/状态/outbox/通知证据。当前读权限或游标变化则拒绝结果；所有结果 `retry_permitted=false`。`b4a964e` 批次没有新迁移、审批写入口或生产权限种子，其迁移 head 为 `20261204_0155`；当前未提交候选另含上述 0156，不能继承旧批次证明。

### 已核验的本批证据

证据目录：`artifacts/loss-review-recovery/`。全部为 ignored 本地文件，不随 Git 克隆；跨机器交接须取得受控证据或重跑。

| 证据 | 结果 |
| --- | --- |
| `focused-v1.log` | 104 passed，356.29 秒，exit 0 |
| `http-compatibility-v1.log` | 原提交/恢复 HTTP 兼容：46 passed，147.02 秒，exit 0 |
| `native-v1.log`、`native-terminal.json` | 会话 63519 已退出 0；数量件与 SN 均完成，不能继续按运行中处理 |
| `repository-safety-final.log` | 提交前完整仓库安全检查 PASS，1907 文件，exit 0 |
| `continuation.json` | 已更新终态及接续坐标；其中 CI 状态仅代表注明的观察时点 |

数量实例：`artifacts/local-stock-loss-submit-pg16/checks/run-9zogl5op`。
SN 实例：`artifacts/local-stock-loss-submit-pg16/checks/run-bcp7pjwm`。

两实例均 `checks.json: passed=true / sourceDrift=[]`、`cluster-state.json: stopped / checks=passed / serverExitCode=0`。数量于 00:12:13、SN 于 00:21:50 正常结束；本次 00:23 后回收进程终态。重新比对全部 **1728 份非 Markdown 源码零漂移**。区域和总部恢复均通过 API 角色 `SET TRANSACTION READ ONLY`、撤销写权限仍可读、撤销读权限拒绝、精确坐标和库存/历史不变校验；空库迁移往返、保留历史拒绝降级、原有权限/并发检查也通过。身份由合成夹具及真实 principal loader 提供，不是生产 JWT 或真实业务验收。

先前三批证据继续保留，不混算测试总数：

- 0155：`artifacts/loss-receipt-inbound-0155/native-v4-terminal.json`、`completed-checks.json`、`final-source-v4.json`；数量/SN 原生终态通过。提交 `d76efd1`，后由 `8990ebd` 推送。
- HTTP：`artifacts/loss-http-submit/evidence-index.json`、`native-terminal.json`；86 项后端、13 项拓扑及数量/SN API HTTP 通过。提交 `3fa414f`。
- PG16 拆分：`artifacts/pg16-runtime-suites/native-terminal-v3.json`、`ordered-extraction-v3.json`、`commit-evidence.json`；102 项聚焦、12 项有重叠调用点复验，本地 control 通过、1725 份源码零漂移。提交 `3e67e51`。

## 4. 远端 CI：准确 SHA 与当前问题

本次只读回查快照：`artifacts/loss-review-seals-0156/handoff-pg16-ci.json`、`handoff-client-ci.json`；旧 `parent-ci-snapshot.json` 仅作历史。远端分支精确回读仍为 `3e67e51`。两运行 headSha 均为 `3e67e51714d301bf66126b8ec55741e378d0720c`，**不包含本地 `b4a964e` 审批恢复代码**。

| 门禁 | 当前状态 |
| --- | --- |
| [客户端 36593125653](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125653) | completed / success |
| [PG16 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765) | completed / failure；19 个实质任务成功、3 个静态任务失败，汇总门禁另为 failure |
| PG16 已成功 | migrations、inventory、control + 全部 16 个报损分支 |
| 仍运行 | 无；该工作流于 03:14:53（Asia/Shanghai）更新为终态 |
| 已失败 | static_safety (0/1/2)，job `109491288960` / `109491289567` / `109491289303` |

静态 0/1/2 原始日志均证实运行器收到 shutdown signal，随后 operation canceled；未观察到测试断言失败，底层关闭原因未确认。日志：`artifacts/pg16-runtime-suites/ci-static-{0,1,2}.log`；单 job 重跑尝试返回 `job cannot be rerun`，见 `ci-static-1-rerun.json`。工作流现已终结，可在恢复开发时重跑失败任务并取得成功证据；本次整理没有触发重跑。

工作流 `cancel-in-progress: true`：新推送会取消同分支在跑 CI。本次回查已无在跑任务；若后续先重跑旧 SHA，必须等其终态再推送，以免取消取证。新提交最终仍要通过自己的准确 SHA 门禁，不能继承 `3e67e51` 的结果。

此前普通 push 超时后已精确回读，再通过 Git Data API 保留原 commit/tree、force=false 推送成功。`artifacts/pg16-runtime-suites/push-exact-api.py` 固定旧 BASE 和两个 SHA，**不得原样用于下次推送**。其他旧推送脚本同样不可盲目重跑。

## 5. 下一步执行顺序

本次仅整理交接、回收终态证据；没有应用下列草案、修改业务源码、提交或推送。当前 **25 个已跟踪文件修改、8 个未跟踪新文件**，完整列表与内容摘要已保存到 ignored `artifacts/loss-review-seals-0156/handoff-worktree.json`；不要只迁移已提交分支而遗漏工作区。

1. 先核对 branch/HEAD/diff 和本页。已提交 `b4a964e` 的审批只读恢复不重复开发；0155 接收侧已有独立证据。0156 当前仍有 `test_revision_history_has_single_current_head` 一项失败：测试缺少 0155 收货父节点的一跳，修复在下述第一份补丁中。
2. 在仓库根目录依次 `git apply --check`、审查后应用 ignored 目录中的 `pending-http-and-head.patch`、`pending-concurrency.patch`、`pending-native-http.patch`、`pending-ci.patch`。每份在前一份应用后重新检查；任何冲突先检查现有改动，禁止 reset/revert。不要重跑草案生成器覆盖后续工作。
3. 将同目录 `test_stock_loss_review_write_routes.py` 草案审查后复制至 `backend/tests/`。上述四份补丁和测试草案**尚未应用、尚未通过行为测试**；包括正式审批/封存 HTTP、真实双向锁竞争、独立读取、API 角色回执丢失/回滚及独立 review_seals CI 分支。
4. 执行受影响的合同、HTTP、迁移历史和 CI 拓扑聚焦测试；再运行全新自有数量/SN PG16 实例。补齐原始碎片、总部精确区域坐标和当前权限撤销覆盖。封存/审批均库存中立；查不到原请求仍禁止自动重发。全部相关证据和仓库安全检查完成后，才提交当前批次。
5. 远端 `3e67e51` 的静态任务可重跑失败项；保存准确 SHA 与终态。旧候选全绿不能覆盖本地 `b4a964e` 或 0156，新提交推送后仍需自己的客户端及完整 PG16 门禁。旧固定 SHA 推送脚本不得原样重用。
6. 继续补审批待办/详情和客户端恢复、报损发件侧出库/发运精确来源及恢复；随后按基线审计补报废反向、人员调拨和离职交接。真实 UAT 与部署条件分别准备、分别验收。

## 6. 上线仍缺哪些证据

- 目标服务器已由用户确认是旧备份脚本的 `118.31.37.87`，无需重复询问。Ubuntu 24.04/x86_64、旧 star-oam 占用 80/443 是 **2026-09-24 历史快照**，本次未连接服务器；部署前重新检查容器/卷/端口/镜像/回滚点。
- 新版公开首页与 `/xx` 尚无生产双入口验收；备案审核通过截图不代替实际域名、公开内容、HTTPS 与部署回读。
- 短信仅有配置及合成验证；真实 PNVS/身份映射、最小权限、实发及回读尚无验收，不可称“短信已完成”。通知 outbox 不代表渠道送达。
- 真实 OSS/KMS、来源目录/公开性审核、期初数据、OAM 批次语义、设备 UAT、500 用户负载、备份恢复（RPO≤5 分钟、RTO≤2 小时）及至少连续三天对账解释待补证。
- 受邀小范围 H5 试点与完整 V1.0 正式发布分别验收，不给缺乏依据的完成百分比或上线日期。

## 7. 接手命令和导航

先只读确认，终态会话 63519、15314 不再轮询，已停止旧库不重启：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git status --short
git log -5 --oneline
git diff --check
cd cloud_oam
cat artifacts/loss-review-recovery/native-terminal.json
cat artifacts/loss-review-seals-0156/native-terminal-v2.json
cat artifacts/loss-review-seals-0156/continuation.json
cat artifacts/loss-review-seals-0156/handoff-pg16-ci.json
```

后续应用补丁并审查源码后，按影响范围复验；以下为核心命令，不能代替完整验收：

```sh
# 工作目录 cloud_oam/backend
../.venv/bin/python -m pytest -q tests/test_stock_loss_review_seal_contracts.py tests/test_stock_loss_review_seals.py tests/test_stock_loss_review_seal_migration.py tests/test_stock_loss_review_recovery.py
# 新测试文件只在步骤 3 应用后存在
../.venv/bin/python -m pytest -q tests/test_stock_loss_review_write_routes.py tests/test_alembic_migrations.py::test_revision_history_has_single_current_head
# 工作目录 cloud_oam；新建自有数量/SN 实例
.venv/bin/python scripts/run_local_pg16_stock_loss_review_seal_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

- [正式基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)：剩余业务及后续审批封存设计。
- [UAT 与上线证据矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)：现场验收；旧候选值不覆盖本页。
- [部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[公网双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)。
- [短信认证专项](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。
- [本次整理前交接原文](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)：含中间运行状态和详细门禁修复经过，原文保留，只作历史。
- [9 月 29 日归档](CONTINUE_DEVELOPMENT_HISTORY_20260929.md)、[9 月 21 日归档](CONTINUE_DEVELOPMENT_HISTORY_20260921.md)。

维护时更新本页的当前值与核验时间，详细过程放证据或历史归档；不得执行历史文档中已过期的“下一步”。本地通过、准确 SHA CI、真实 UAT、生产上线必须分别报告。
