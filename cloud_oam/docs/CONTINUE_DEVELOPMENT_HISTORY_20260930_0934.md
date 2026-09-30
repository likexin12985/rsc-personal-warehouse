# RSC 个人仓开发交接

核验时间：2026-09-30 09:34（Asia/Shanghai）。当前接续入口；下列路径默认相对 `cloud_oam/`。本次只整理交接和回读证据，没有修改业务代码、重跑测试、提交、推送或部署。

**报损收货、独立入库和请求恢复已有本地实现与验证；审批封存 0156 已本地提交。当前审批查询、照片授权和私有 H5 已接入源码但尚未通过全部门禁，不能提交或宣称上线。最新断点是前端测试文件缺 Node 类型；新 PG16 的两次失败已修正夹具，尚未复验。父版本远端 CI 已结束且失败。**

## 1. 接手位置与不可破坏的约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支 `codex/notification-delivery-worker`。禁止 reset、revert、丢弃未提交改动；不要在默认 checkout 继续。
- 本地 HEAD：`1f65dcbed655a0a1e7c4054ba8b6fe1f2d86d3d1`；前一提交 `b4a964e5443439794429de6fee85281d9b7faa5f`。两者尚未推送；远端分支上次回读为 `3e67e51714d301bf66126b8ec55741e378d0720c`，本次 CI 回读也对应该 SHA，推送前重新核对远端分支。
- 先完整阅读[正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)和 [AGENTS.md](../../AGENTS.md)，遵守用户最新路由约束。
- 首页“交流备件知识大全”无登录，星星管理按钮指向 `https://rscwz.cn/xx`；`/xx` 是路径。公开小程序仅知识查询；飞书知识源优先级低，暂缓。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入库、通知、对账独立存证。未知写结果先精确回查原请求，禁止自动重放或换 key 重试。
- 用户已确认目标服务器是旧备份脚本中的 `118.31.37.87`，不重复询问；Ubuntu 24.04、旧 star-oam 占用 80/443 为历史观察，部署前需重查。本次未连接服务器。
- 本地测试不等于真实业务验收；不发送真实短信、不写外部业务、不迁移生产。OAM 凭据不出本地。飞书需求遵循 NIO Chat CLI 托管路由；本轮无需调用业务系统。

## 2. 完成范围与剩余缺口

| 范围 | 当前证据 | 接下来缺什么 |
| --- | --- | --- |
| 报损收货、独立入库、恢复 0155 | `d76efd1`；数量/SN 本地 PG16、迁移/权限及客户端恢复验证完成 | 私有 H5 接收/入库页面、真实 UAT |
| 原报损提交 HTTP | `3fa414f`；COMMIT 后返回、未知结果回查 | H5 发起页面与真实业务闭环 |
| 区域/总部审批恢复 | 本地提交 `b4a964e`；104 项聚焦、46 项 HTTP 兼容、数量/SN PG16 | 新准确 SHA 完整 CI |
| 审批写入/封存 0156 | 本地提交 `1f65dcb`；数量/SN v5 通过 | 推送、新 SHA CI、真实验收 |
| 审批待办/详情、照片授权 | 正式源码未提交；84 项聚焦、44 项文件兼容、16 项附件兼容通过 | 修复后的新 PG16 数量/SN 完整复验 |
| 私有 H5 报损审批及原请求恢复 | 正式源码已接入；草案阶段 42 项通过 | 集成类型检查、集成测试、路由权限、构建及 UI 验收 |
| 报损发件侧 | 内部退回/出库/发运服务及既有门禁 | 正式 HTTP 精确来源合同与客户端恢复 |
| 正式上线 | 尚未放行 | 完整 CI、短信/文件实测、UAT、数据与运维验收 |

区域核实/总部批准均为 `stock_effect=none`，总部批准仍待处置；收货与库存入库分别落事实。私有小程序代码不代表私有 H5 可用。以上测试集合有重叠，不能相加当作总覆盖率。

## 3. 当前未提交改动：直接继续正式源码

后端新增：

- `backend/app/stock_loss_review_query_schemas.py`。
- `backend/app/formal_services/stock_loss_review_query.py`、`stock_loss_review_evidence.py`。
- `backend/tests/test_stock_loss_review_query.py`、`test_stock_loss_review_query_routes.py`、`pg16_stock_loss_review_query_gate.py`。

后端修改：`formal_services/formal_files.py`、`routers/formal_stock_losses.py`、`tests/pg16_stock_loss_review_recovery_gate.py`。

查询入口为 GET `/api/v1/stock-operations/loss-reports/reviews/{regional|headquarters}` 及 `/{operation_id}`：pending/all、UUID 游标、每页最多 20；稀疏待办页可能为空但仍有下一页。按当前同一角色授权绑定限定区域/全国，禁止申请人自审，重新核验原单/审批事实；损坏队列行隔离，详情拒绝。DTO 不暴露 request/key、库存账户、二维码或 storage key。

照片沿用 `/api/v1/files/{file_id}/download-intent`，按原报损精确唯一绑定、当前读权限授权，拒绝跨业务混合绑定；下载签名和审计不等于真实 OSS 验收。

前端新增：

- `frontend/src/formalLossReview.ts`：严格合同、原命令规范化及 SHA256，与 Python 响应互验。
- `frontend/src/lossReviewRecovery.ts`：按人员/阶段/原单保存原命令，Web Locks 排他；未知结果保留，先回查，可显式封存，不能自动重放。
- `frontend/src/lossReviewAdapter.ts`：使用 `apiNoReplay`，前后重读身份/权限/范围摘要，临时照片链接。
- `frontend/src/FormalLossReviews.tsx`：区域/总部待办、详情、照片、逐行决定、恢复与封存确认。
- 上述页面/恢复/adapter 测试及 `lossReviewBackendContract.test.ts`；4 份合成 JSON 在 `frontend/src/test-fixtures/loss-review/`。

`App.tsx` 已增加 `/loss-reviews` 私有路由及“报损审批”导航；`styles.css` 已加入响应式样式。读权限与写权限分开；权属变更、跨人、切页或过期异步响应不得清除原待恢复命令。

**不要把 ignored 草案拷回正式源码。** 正式测试已有类型修正、路径和样式变更；0156 旧 patch/生成器也都已应用，禁止重复套用。完整清单以 `git status --short --untracked-files=all` 为准，`git diff --stat` 不包含未跟踪新文件。

## 4. 当前断点与失败证据

### 前端类型检查尚未通过

`artifacts/loss-review-ui-next/integrated-typecheck-v2.log` 最新失败为：

```text
FormalLossReviews.test.tsx: Cannot find type definition file for 'node'
FormalLossReviews.test.tsx: Cannot find name 'node:crypto'
lossReviewBackendContract.test.ts: Cannot find type definition file for 'node'
lossReviewBackendContract.test.ts: Cannot find name 'node:fs'
```

测试的字面量类型已加 `as const`；此前添加的 `reference types="node"` 不能解决当前没有可解析 Node 类型的问题。先检查既有测试方式及依赖，选择最小修复；可将静态 JSON fixture 改为静态 import。不要只排除测试或放宽生产 tsconfig 来掩盖问题。草案 tsconfig 只覆盖运行时代码，其通过不能替代集成 `tsc -b`。

### 新查询批次 PG16：两次终止失败，暂无运行中实例

| 轮次 | 数量库目录尾部 | 失败原因与当前修复 |
| --- | --- | --- |
| native-v1 | `run-qztav2m4` | 区域权限夹具误选总部组织；已改为明确 `region_company`，聚焦 2 项通过 |
| native-v2 | `run-o91wn_xt` | Session 关闭后读 `other_org.id` 导致 `DetachedInstanceError`；已在提交前保存 `other_org_id`，尚未完整复验 |

日志为 `artifacts/loss-review-queue-next/native-v1.log` / `native-v2.log`。实例在 `artifacts/local-stock-loss-review-seals-pg16/checks/`；本次回读两份 `cluster-state.json` 均为 `stopped / checks=failed / serverExitCode=0`。停库成功不等于测试通过；两轮均未提供完整数量/SN 新候选通过证据。

旧会话 58080、24163 已结束；本次进程扫描未发现相关 pytest/vitest/PG16 runner 或这些临时 PostgreSQL。源码冻结已结束，native-v3 尚未启动；不要再轮询旧会话、重启旧库或沿用 1745 文件冻结结论。

## 5. 测试证据索引与有效边界

| 路径（artifacts/ 下） | 结果及范围 |
| --- | --- |
| loss-review-queue-next/integrated-v2.log | 正式查询/HTTP 聚焦 84 passed，255.60 秒；不覆盖后续整个 H5 集成 |
| loss-review-queue-next/file-compatibility-v1.log | 文件兼容 44 passed |
| loss-review-queue-next/loss-evidence-compatibility-v1.log | 既有报损附件兼容 16 passed |
| loss-review-queue-next/real-scope-fixture-v1.log | 真实区域权限夹具回归 2 passed |
| loss-review-ui-next/backend-contract-export-v1.log | Python 服务生成数量/SN、区域/总部合成合同 4 passed |
| loss-review-ui-next/client-contract-v4.log | 草案页面/恢复/adapter/真实响应合同 42 passed；不是正式集成复验 |
| loss-review-ui-next/integrated-typecheck-v2.log | 正式集成类型检查失败，见上节 |
| loss-review-seals-0156/native-terminal-v5.json | 0156 数量/SN 通过，1739 文件零漂移；仅覆盖该历史候选 |
| loss-review-recovery/native-terminal.json | b4a964e 审批恢复本地数量/SN 终态 |
| loss-receipt-inbound-0155/native-v4-terminal.json | 0155 收货/独立入库/恢复本地数量/SN 终态 |
| loss-http-submit/native-terminal.json | 原报损提交 HTTP 本地终态 |

0156 v5 数量库 `run-flxjwk3f`、SN 库 `run-o500b2w_` 均正常停止，runner 28123 已退出 0。其迁移/权限、实际 COMMIT 双向互斥、API 原始 SQL 拒绝、HTTP、空库往返和有历史拒绝降级已通过。该证据的 `matchesCurrentSource=true` 是生成时历史断言，不代表当前新增源码。

历史安全检查通过不覆盖本次新增全部文件；提交前必须重新跑仓库安全和 diff 检查。失败日志保留，不能把修正后的定点通过改写成原整轮全绿。

## 6. GitHub 最新终态（本次实时回读）

[PG16 运行 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765) 已 `completed / failure`，更新时间 2026-09-30 09:29:12（上海）。对应父 SHA `3e67e51` 的最近记录为 attempt 2。

- 19 个 migrations/inventory/control/报损任务成功。
- static_safety 0、1、2 全部失败，汇总门禁失败。
- 静态 1 此前日志含 runner shutdown / operation canceled；不能把同一原因未经核验推广到另外两片。需读取本次各失败任务的日志，区分 runner 中断与测试断言。
- 本次快照：`artifacts/loss-review-queue-next/handoff-parent-ci-20260930.json`。
- 父版本客户端运行 36593125653 的历史回读为成功，本次未再次查询；不能覆盖当前新源码。
- 现在不再受“父任务仍运行”的旧等待条件限制，但当前本地候选尚未完成门禁，仍不能提交/推送作为合格版本。最终必须验证准确新 SHA 完整 CI。
- 推送结果未知先精确回读；旧 `push-exact-api.py` 含固定旧坐标，禁止原样复用。

## 7. 接续执行顺序

1. 重新确认分支/HEAD/diff，读本页和机读断点；保留所有未提交文件。
2. 修复前端两个测试文件的 Node 类型问题，跑正式 `tsc -b`，再跑已接入的 4 份测试。补 App 导航/直接路由/角色读写权限测试；补后端对静态 JSON 合同的 schema 校验，避免 fixture 与后端漂移。
3. 完成前端集成回归、公开/私有构建及客户端发布门禁；核验公开首页无登录、星星入口和公开小程序边界。
4. 检查新 PG16 夹具其余 Session 外 ORM 访问，使用新日志启动 native-v3；记录当前源码清单并冻结非 Markdown 源码，直到数量/SN 全部终态、正常停库、零源码漂移。超时只是观察超时，不重启。失败后保留日志，修复后另启新轮次。
5. 审查全部 diff、新文件、迁移/权限和安全检查；收齐证据后才能提交。回读父 CI 三片失败日志，处理必要问题，再推送并等待准确新 SHA 客户端/完整 PG16 通过。
6. 补私有 H5 报损发起、退回收货、独立入库及原请求恢复；补发件出库/发运正式 HTTP 和客户端恢复。不要重复开发已验证的 0155 后端。
7. 继续基线缺口审计：报废反向冲销、人员调拨、离职交接；真实 UAT、试点和完整生产分别验收。

起步命令（只读）：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git diff --check
cd cloud_oam
cat artifacts/loss-review-queue-next/continuation.json
cat artifacts/loss-review-ui-next/integrated-typecheck-v2.log
```

当前 Node 不在默认 PATH；使用 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`。后端解释器 `cloud_oam/.venv/bin/python`；前端依赖在 `frontend/node_modules`。以下命令是**后续修复后执行**，本次未运行：

```sh
# 工作目录 cloud_oam/frontend；确认 v3 日志尚不存在，保留已有日志
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/typescript/bin/tsc -b > ../artifacts/loss-review-ui-next/integrated-typecheck-v3.log 2>&1
~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node node_modules/vitest/vitest.mjs run src/lossReviewRecovery.test.ts src/lossReviewAdapter.test.ts src/FormalLossReviews.test.tsx src/lossReviewBackendContract.test.ts
# 完成源码修改后，工作目录 cloud_oam；确认日志尚不存在
.venv/bin/python scripts/run_local_pg16_stock_loss_review_seal_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin > artifacts/loss-review-queue-next/native-v3.log 2>&1
```

注意 cwd，避免在 frontend 下再次拼 frontend/src。不要重新执行旧草案生成器。ignored artifacts 不随 Git 克隆；换机器应受控复制必要日志/合成 fixture 或重跑，不能复制真实数据、凭据或临时数据库目录。

## 8. 上线验收仍缺的独立事实

真实短信 PNVS、人员唯一身份映射与实发回读；真实 OSS/KMS；公开知识来源授权；期初数据与 OAM 只读批次语义；真实设备 UAT；至少连续三天对账差异解释；500 用户压测；RPO≤5 分钟/RTO≤2 小时备份恢复、发布回滚及业务冲销演练。备案通过截图不代替域名/HTTPS/双入口生产验收。

受邀 HTTPS H5 小范围试点与完整 V1.0 分开验收，不提供无证据的完成百分比或上线日期。

相关文档：[基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT/上线矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。

历史入口：[09:05 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0905.md)、[08:45 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0845.md)、[9 月 30 日早期](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)。旧文档的运行中状态和下一步已被本页替代，保留仅供追溯。
