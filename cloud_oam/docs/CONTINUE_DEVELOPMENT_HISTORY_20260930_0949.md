# RSC 个人仓开发交接

核验时间：2026-09-30 09:49（Asia/Shanghai）。当前接续入口；下列路径默认相对 `cloud_oam/`。本轮修复 H5 类型/移动端布局，完成前端与小程序回归，启动新的 PG16；未提交、推送或部署。

**报损收货、独立入库和恢复已有本地验证。当前审批查询/照片授权与 H5 仍未提交；类型检查、1699 项前端测试、1079 项小程序测试、4 项后端合同互验、双入口构建通过。390px 布局及重入后原请求保留已用浏览器实测。数量/SN PG16 native-v4 会话 45500 正在运行，1759 文件冻结；不可提交或宣布上线。父 SHA CI 仍为整体失败。**

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
| 审批待办/详情、照片授权 | 正式源码未提交；84 项聚焦、44 项文件兼容、16 项附件兼容通过 | native-v4 数量/SN 完整终态 |
| 私有 H5 报损审批及原请求恢复 | 正式源码已接入；1699 项前端测试、类型/构建通过，含 42 项审批测试及 6 项路由权限测试 | 当前 native-v4、真实设备/真实服务验收 |
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

## 4. 当前断点与失败历史

**native-v4 会话 45500 / runner PID 50811 已实测存活**，数量库 `artifacts/local-stock-loss-review-seals-pg16/checks/run-18vij09f`，日志 `artifacts/loss-review-queue-next/native-v4.log`。1759 份非 Markdown 源码冻结直至数量/SN 全部终态；不要超时重启，不修改非 Markdown 源码。

| 历史轮次 | 实例 | 终态与修复 |
| --- | --- | --- |
| v1 | run-qztav2m4 | 区域夹具误选总部组织，已改 region_company；failed/stopped/0 |
| v2 | run-o91wn_xt | Session 外读取 ORM ID，已提前保存 scalar ID；failed/stopped/0 |
| v3 | run-p_qiwloj | 为修复浏览器实测布局主动 SIGINT；KeyboardInterrupt、failed/stopped/0；不是断言失败，也不是通过 |

旧会话 58080、24163、83651 均结束，不再轮询或重启旧库。v4 独立重新跑全部数量/SN，之前任一轮的局部门禁输出都不能代替完整终态。

前端旧类型失败 `integrated-typecheck-v2.log` 已修复：合成 JSON 改静态 import，WebCrypto 使用项目既有 `vi.importActual<{webcrypto:Crypto}>`，没有新增依赖或放宽 tsconfig。`integrated-typecheck-v4.log` 通过。路由新增测试首轮 3 个断言未计 no-cache 参数失败，修正后由全量 1699 项通过覆盖，原日志保留。

浏览器发现 390px 页面被 grid/table 撑至 814px，复选框被全局输入样式拉宽。正式 `styles.css` 已加入子项 min-width、标题换行、checkbox 独立尺寸；移除预览草案后复验 documentWidth=390、checkboxWidth=13。最后仅三行 CSS 修改，重新双构建和公开入口检查通过。证据 `artifacts/loss-review-ui-next/browser-layout-final.json`、`browser-headquarters-mobile-final.png`；原请求未知结果→刷新→精确回查仍保留已在真实浏览器合成页面实测，见 `browser-recovery-final.png`。无生产连接，预览标签/服务已关闭。

## 5. 测试证据索引与有效边界

| 路径（artifacts/ 下） | 结果及范围 |
| --- | --- |
| loss-review-queue-next/integrated-v2.log | 正式查询/HTTP 聚焦 84 passed，255.60 秒；不覆盖后续整个 H5 集成 |
| loss-review-queue-next/file-compatibility-v1.log | 文件兼容 44 passed |
| loss-review-queue-next/loss-evidence-compatibility-v1.log | 既有报损附件兼容 16 passed |
| loss-review-queue-next/real-scope-fixture-v1.log | 真实区域权限夹具回归 2 passed |
| loss-review-ui-next/backend-contract-export-v1.log | Python 服务生成数量/SN、区域/总部合成合同 4 passed |
| loss-review-ui-next/client-contract-v4.log | 草案页面/恢复/adapter/真实响应合同 42 passed；不是正式集成复验 |
| loss-review-ui-next/integrated-typecheck-v4.log | 正式类型检查通过；v2 失败保留 |
| loss-review-ui-next/frontend-full-v1.log | 95 文件、1699 项通过，含页面、6 项新增路由权限测试 |
| loss-review-ui-next/mini-full-v1.log | 1079 项小程序测试通过 |
| loss-review-ui-next/backend-fixture-parity-v2.log | 4 项当前后端 schema/命令 envelope/hash 与前端 fixture 完全互验 |
| loss-review-ui-next/build-public-v2.log / build-warehouse-v2.log | 最终 CSS 后双入口构建通过；私有包有 >500kB 提示 |
| loss-review-ui-next/public-entry-v2.log | 开发产物检查通过；知识目录 pending/0 条，不代表资料上线 |
| loss-review-ui-next/opening-protocol-v1.log | 共享期初协议生成一致性检查通过 |
| loss-review-ui-next/repository-safety-v1.log | PASS 1940 文件，早于最后三行 CSS；最终审查仍需确认 |
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
- 本次已取三片完整日志 `parent-static-{0,1,2}-final.log`：静态 1 明确 runner shutdown，0/2 为 operation canceled；三片均未出现最终断言失败汇总。不能把取消写成通过，底层取消原因未确认。
- 本次快照：`artifacts/loss-review-queue-next/handoff-parent-ci-20260930.json`。
- 父版本客户端运行 36593125653 的历史回读为成功，本次未再次查询；不能覆盖当前新源码。
- 现在不再受“父任务仍运行”的旧等待条件限制，但当前本地候选尚未完成门禁，仍不能提交/推送作为合格版本。最终必须验证准确新 SHA 完整 CI。
- 推送结果未知先精确回读；旧 `push-exact-api.py` 含固定旧坐标，禁止原样复用。

## 7. 接续执行顺序

1. 重新确认分支/HEAD/diff，读本页和机读断点；保留所有未提交文件。
2. 本轮 H5 类型、集成测试、导航/路由权限、后端 fixture schema/命令互验已完成；不要重复解决旧类型错误或重拷草案。
3. 前端/小程序回归与双入口构建已完成，记录其证据边界；真实服务/设备/身份 UAT 仍需独立验收。
4. 回收现有 native-v4 会话 45500，核验数量/SN 全部终态、正常停库、1759 文件零漂移；保持源码冻结。若出现终态失败再修复另启新轮次，观察超时不重启。
5. 审查全部 diff、新文件、迁移/权限和安全检查；收齐证据后才能提交。父 CI 三片取消日志已取证，处理必要问题，再推送并等待准确新 SHA 客户端/完整 PG16 通过。
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

当前 Node 不在默认 PATH，使用 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`；后端解释器 `.venv/bin/python`。当前已有 native-v4 活跃进程，**不要再启动第二个 runner**。通过工具会话 45500 回收输出；失去会话时先核对 PID、进程命令、日志和 cluster-state，不因客户端重启就重跑。

注意 cwd，避免在 frontend 下再次拼 frontend/src。不要重新执行旧草案生成器。ignored artifacts 不随 Git 克隆；换机器应受控复制必要日志/合成 fixture 或重跑，不能复制真实数据、凭据或临时数据库目录。

## 8. 上线验收仍缺的独立事实

真实短信 PNVS、人员唯一身份映射与实发回读；真实 OSS/KMS；公开知识来源授权；期初数据与 OAM 只读批次语义；真实设备 UAT；至少连续三天对账差异解释；500 用户压测；RPO≤5 分钟/RTO≤2 小时备份恢复、发布回滚及业务冲销演练。备案通过截图不代替域名/HTTPS/双入口生产验收。

受邀 HTTPS H5 小范围试点与完整 V1.0 分开验收，不提供无证据的完成百分比或上线日期。

相关文档：[退回 H5 接入验收](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)、[基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT/上线矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。

历史入口：[09:34 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0934.md)、[09:05 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0905.md)、[08:45 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0845.md)、[9 月 30 日早期](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)。旧文档的运行中状态和下一步已被本页替代，保留仅供追溯。
