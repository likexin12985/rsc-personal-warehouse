# RSC 个人仓开发交接

核验时间：2026-09-30 00:24（Asia/Shanghai）。本页是当前接续入口；历史文档和历史 CI 不覆盖本页核验值。下文路径默认相对 `cloud_oam/`。

**当前结论：报损收货、独立入库及其请求恢复已实现并完成本地验证；提交/封存 HTTP 与 PG16 门禁拆分已推送。新一批区域/总部审批只读请求恢复已完成本地数量件、SN 验证，尚未提交。准确 SHA 的完整 CI、真实业务验收和生产部署尚未完成，不能宣布上线。**

## 1. 接手位置与约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`。禁止 reset、revert、丢弃或覆盖未提交改动。
- 本地 HEAD：`3e67e51714d301bf66126b8ec55741e378d0720c`。已推送该 SHA，当前两个 GitHub 运行的 headSha 均为此值；远端分支仍应在下次推送前精确回读。
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
| 审批只读恢复（未提交） | 区域/总部独立回查服务及 HTTP；撤销写权限后仍可按当前读权限恢复；数量/SN 本地通过 | 新提交、其准确 SHA CI；独立审批封存、正式审批写 HTTP、待办/详情和客户端 |
| 报损发件侧 | 已有内部退回/出库/发运服务和相应门禁 | 正式发件 HTTP 的报损来源合同及客户端恢复闭环 |

收货只确认实物验收，入库另行过账；区域核实/总部批准均不改变库存，总部通过仍待处置。不得将以上局部完成当成全流程完成。

## 3. 当前未提交改动和本地终态

本次交接保留全部业务改动，不做业务提交或推送。交接前 9 个变更文件，加本次历史归档共 10 个；以实时 `git status --short` 为准。

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

两个入口为 `POST /api/v1/stock-operations/loss-reports/regional-reviews/request-lookup` 和 `.../headquarters-reviews/request-lookup`。绑定原单、原审批人、request ID、幂等键、请求摘要与提交计划摘要；核验不可变审批/审计/状态/outbox/通知证据。当前读权限或游标变化则拒绝结果；所有结果 `retry_permitted=false`。本批没有新迁移、审批写入口或生产权限种子，迁移 head 仍为 `20261204_0155`。

### 已核验的本批证据

证据目录：`artifacts/loss-review-recovery/`。全部为 ignored 本地文件，不随 Git 克隆；跨机器交接须取得受控证据或重跑。

| 证据 | 结果 |
| --- | --- |
| `focused-v1.log` | 104 passed，356.29 秒，exit 0 |
| `http-compatibility-v1.log` | 原提交/恢复 HTTP 兼容：46 passed，147.02 秒，exit 0 |
| `native-v1.log`、`native-terminal.json` | 会话 63519 已退出 0；数量件与 SN 均完成，不能继续按运行中处理 |
| `repository-safety-v1.log` | 本批代码安全检查 PASS，1906 文件；属于整理前快照 |
| `continuation.json` | 已更新终态及接续坐标；其中 CI 状态仅代表注明的观察时点 |

数量实例：`artifacts/local-stock-loss-submit-pg16/checks/run-9zogl5op`。
SN 实例：`artifacts/local-stock-loss-submit-pg16/checks/run-bcp7pjwm`。

两实例均 `checks.json: passed=true / sourceDrift=[]`、`cluster-state.json: stopped / checks=passed / serverExitCode=0`。数量于 00:12:13、SN 于 00:21:50 正常结束；本次 00:23 后回收进程终态。重新比对全部 **1728 份非 Markdown 源码零漂移**。区域和总部恢复均通过 API 角色 `SET TRANSACTION READ ONLY`、撤销写权限仍可读、撤销读权限拒绝、精确坐标和库存/历史不变校验；空库迁移往返、保留历史拒绝降级、原有权限/并发检查也通过。身份由合成夹具及真实 principal loader 提供，不是生产 JWT 或真实业务验收。

先前三批证据继续保留，不混算测试总数：

- 0155：`artifacts/loss-receipt-inbound-0155/native-v4-terminal.json`、`completed-checks.json`、`final-source-v4.json`；数量/SN 原生终态通过。提交 `d76efd1`，后由 `8990ebd` 推送。
- HTTP：`artifacts/loss-http-submit/evidence-index.json`、`native-terminal.json`；86 项后端、13 项拓扑及数量/SN API HTTP 通过。提交 `3fa414f`。
- PG16 拆分：`artifacts/pg16-runtime-suites/native-terminal-v3.json`、`ordered-extraction-v3.json`、`commit-evidence.json`；102 项聚焦、12 项有重叠调用点复验，本地 control 通过、1725 份源码零漂移。提交 `3e67e51`。

## 4. 远端 CI：准确 SHA 与当前问题

本次 00:23:49 快照：`artifacts/handoff-20260930/ci-snapshot.json`。两运行 headSha 均为 `3e67e51714d301bf66126b8ec55741e378d0720c`，**不包含未提交审批恢复代码**。

| 门禁 | 当前状态 |
| --- | --- |
| [客户端 36593125653](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125653) | completed / success |
| [PG16 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765) | in_progress；17 个成功、4 个运行、1 个失败 |
| PG16 已成功 | control + 全部 16 个报损分支 |
| 仍运行 | migrations、inventory、static_safety (0)、static_safety (2) |
| 已失败 | static_safety (1)，job `109491289567` |

静态 1 原始日志证实运行器收到 shutdown signal，随后 operation canceled；未观察到测试断言失败，底层关闭原因未确认。日志：`artifacts/pg16-runtime-suites/ci-static-1.log`；单 job 重跑尝试返回 `job cannot be rerun`，见 `ci-static-1-rerun.json`。待工作流终态后重跑失败任务，再取得真实成功结果。

工作流 `cancel-in-progress: true`：新推送会取消同分支在跑 CI。可在证据齐备后形成本地提交，但不要为推送新批次打断尚需取证的迁移/库存门禁。新提交最终仍要通过自己的准确 SHA 门禁，不能继承 `3e67e51` 的结果。

此前普通 push 超时后已精确回读，再通过 Git Data API 保留原 commit/tree、force=false 推送成功。`artifacts/pg16-runtime-suites/push-exact-api.py` 固定旧 BASE 和两个 SHA，**不得原样用于下次推送**。其他旧推送脚本同样不可盲目重跑。

## 5. 下一步执行顺序

1. 复核 Git diff、上述原生终态和源码摘要；对当前审批只读恢复做最后审查，证据齐备再提交本地业务改动。无需重复跑无变更的整批原生门禁。
2. 回读 `3e67e51` 在跑 CI；工作流终态后只重跑失败任务。保存确切失败/成功证据，再安排新批次推送和新 SHA 验证。
3. 补区域/总部**独立审批请求永久封存及迟到审批数据库互斥**。具体字段、锁序、当前/历史权限及验收要求见[缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)的“后续审批封存切片”。这是设计，尚无 0156 实现；新增迁移，不修改 0146/0147/0148。
4. 补正式审批写 HTTP、待办/详情和客户端恢复，验证 COMMIT 前故障回滚、回执丢失回查、原请求封存和真实并发；区域/总部审批仍不直接产生库存效果。
5. 补报损发件侧出库/发运的精确来源合同和客户端恢复。不要重复实现已完成的接收侧 0155。
6. 继续补报废反向、独立人员调拨、离职交接等基线缺口；并行准备真实 UAT 所需条件，但不越权执行生产或外部业务写入。

## 6. 上线仍缺哪些证据

- 目标服务器已由用户确认是旧备份脚本的 `118.31.37.87`，无需重复询问。Ubuntu 24.04/x86_64、旧 star-oam 占用 80/443 是 **2026-09-24 历史快照**，本次未连接服务器；部署前重新检查容器/卷/端口/镜像/回滚点。
- 新版公开首页与 `/xx` 尚无生产双入口验收；备案审核通过截图不代替实际域名、公开内容、HTTPS 与部署回读。
- 短信仅有配置及合成验证；真实 PNVS/身份映射、最小权限、实发及回读尚无验收，不可称“短信已完成”。通知 outbox 不代表渠道送达。
- 真实 OSS/KMS、来源目录/公开性审核、期初数据、OAM 批次语义、设备 UAT、500 用户负载、备份恢复（RPO≤5 分钟、RTO≤2 小时）及至少连续三天对账解释待补证。
- 受邀小范围 H5 试点与完整 V1.0 正式发布分别验收，不给缺乏依据的完成百分比或上线日期。

## 7. 接手命令和导航

先只读确认，终态会话 63519 不再轮询，已停止旧库不重启：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git status --short
git log -5 --oneline
git diff --check
cd cloud_oam
cat artifacts/loss-review-recovery/native-terminal.json
cat artifacts/handoff-20260930/ci-snapshot.json
```

后续确有相关源码变更或新失败时，才按以下命令复验：

```sh
# 工作目录 cloud_oam/backend
../.venv/bin/python -m pytest -q tests/test_stock_loss_review_recovery.py
../.venv/bin/python -m pytest -q tests/test_stock_loss_recovery_routes.py tests/test_stock_loss_write_routes.py
# 工作目录 cloud_oam；新建自有数量/SN 实例
.venv/bin/python scripts/run_local_pg16_stock_loss_submit_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

- [正式基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)：剩余业务及后续审批封存设计。
- [UAT 与上线证据矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)：现场验收；旧候选值不覆盖本页。
- [部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[公网双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)。
- [短信认证专项](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。
- [本次整理前交接原文](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)：含中间运行状态和详细门禁修复经过，原文保留，只作历史。
- [9 月 29 日归档](CONTINUE_DEVELOPMENT_HISTORY_20260929.md)、[9 月 21 日归档](CONTINUE_DEVELOPMENT_HISTORY_20260921.md)。

维护时更新本页的当前值与核验时间，详细过程放证据或历史归档；不得执行历史文档中已过期的“下一步”。本地通过、准确 SHA CI、真实 UAT、生产上线必须分别报告。
