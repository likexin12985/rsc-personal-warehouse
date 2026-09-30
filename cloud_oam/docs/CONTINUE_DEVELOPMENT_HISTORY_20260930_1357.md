# RSC 个人仓开发交接

> 历史快照：仅将个人主目录规范为 `~/` 以满足仓库安全门禁，历史结论保留。

> 2026-09-30 开发增量：HEAD 仍为已发布 ff25289；新增未提交的退回 H5 合同、原请求恢复和 HTTP 适配模块，页面/导航未接入。74 项聚焦、4 项后端互验、类型检查通过。当前代码与下一步见第 9 节及 `artifacts/loss-return-h5-next/continuation.json`。ff25289 客户端 CI 已成功；PG16 仍运行，静态 0/1 失败且均有明确 runner shutdown/canceled 日志。下列 13:24 状态是此前交接时点，不代表当前工作树干净或新代码已通过 PG16。

核验时间：2026-09-30 13:24（Asia/Shanghai）。这是当前唯一开发接续入口；下列路径默认相对 `cloud_oam/`。本次用户要求整理交接，未新增业务代码、未部署。整理前工作树干净；整理后仅交接文档及 ignored 机读断点更新，尚未提交这些文档。

**报损收货、独立入库和恢复已有本地验证。当前审批查询/照片授权与 H5 本地验收完成；类型检查、1699 项前端测试、1079 项小程序测试、4 项后端合同互验、双入口构建通过。390px 布局及重入后原请求保留已用浏览器实测。数量/SN PG16 native-v4 均通过、正常停库、1759 文件零漂移，源码冻结结束。最新代码 `ff25289` 已成功推送；准确 SHA 的客户端 CI 运行中、PG16 CI 排队中，尚无完整终态，不能宣布上线。父 SHA CI 的失败单独保留。**

## 1. 接手位置与不可破坏的约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支 `codex/notification-delivery-worker`。禁止 reset、revert、丢弃未提交改动；不要在默认 checkout 继续。
- 当前 HEAD/已发布代码：`ff25289a6d497711dd4275260e05b5eab6454b52`（审批查询、照片授权、H5 审批与恢复）。其前序 `1f65dcbed655a0a1e7c4054ba8b6fe1f2d86d3d1`（0156）及 `b4a964e5443439794429de6fee85281d9b7faa5f`（审批恢复）一并发布。Git Data API 精确 tree/commit SHA 核验、force=false 更新及分支回读成功，见 `artifacts/loss-review-queue-next/push-result.json`。父 CI 对应 `3e67e51714d301bf66126b8ec55741e378d0720c`，不能覆盖新提交。
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
| 区域/总部审批恢复 | 已发布 `b4a964e`；104 项聚焦、46 项 HTTP 兼容、数量/SN PG16 | 新准确 SHA 完整 CI |
| 审批写入/封存 0156 | 已发布 `1f65dcb`；数量/SN v5 通过 | 新 SHA CI、真实验收 |
| 审批待办/详情、照片授权 | `ff25289` 已发布；84 项聚焦、44 项文件兼容、16 项附件兼容通过 | 准确新 SHA CI 和真实 UAT |
| 私有 H5 报损审批及原请求恢复 | `ff25289` 已发布；1699 项前端测试、类型/构建通过，含 42 项审批测试及 6 项路由权限测试 | 准确新 SHA CI、真实设备/真实服务验收 |
| 报损发件侧 | 内部退回/出库/发运服务及既有门禁 | 正式 HTTP 精确来源合同与客户端恢复 |
| 正式上线 | 尚未放行 | 完整 CI、短信/文件实测、UAT、数据与运维验收 |

区域核实/总部批准均为 `stock_effect=none`，总部批准仍待处置；收货与库存入库分别落事实。私有小程序代码不代表私有 H5 可用。以上测试集合有重叠，不能相加当作总覆盖率。

## 3. 本批源码：直接继续正式源码

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

**native-v4 会话 45500 已退出 0**。数量库 `run-18vij09f` 于 09:57:30 完成、SN 库 `run-l35n10_y` 于 10:07:33 完成；两者均 `passed=true / sourceDrift=[] / stopped / checks=passed / serverExitCode=0`。12:52 回读与当前 1759 文件清单逐项一致，终态 `artifacts/loss-review-queue-next/native-terminal-v4.json`。源码冻结结束；不要重启已停库或继续轮询 45500。

两模式的区域/总部 reviewQuery 均通过真实 API 角色 HTTP、分页/范围、撤写后读取、附件审计与撤读拒绝。合成对象存储不代表真实 OSS。v4 有历史审批封存拒绝降级通过；旧 0147/0148 各自拒绝降级由既有独立门禁验证，本轮相应 flag=false 表示未重复该子流程，不把它宣传为本轮通过。

| 历史轮次 | 实例 | 终态与修复 |
| --- | --- | --- |
| v1 | run-qztav2m4 | 区域夹具误选总部组织，已改 region_company；failed/stopped/0 |
| v2 | run-o91wn_xt | Session 外读取 ORM ID，已提前保存 scalar ID；failed/stopped/0 |
| v3 | run-p_qiwloj | 为修复浏览器实测布局主动 SIGINT；KeyboardInterrupt、failed/stopped/0；不是断言失败，也不是通过 |

旧会话 58080、24163、83651 均结束，不再轮询或重启旧库。v4 独立完整通过全部数量/SN；之前任一轮的局部门禁输出不能代替完整终态。

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
| loss-review-ui-next/repository-safety-v3.log | 提交前最终安全检查 PASS 1944 文件；本次新增交接文字不在其历史范围 |
| loss-review-queue-next/native-terminal-v4.json | 本批完整数量/SN PG16、正常停库、1759 文件零漂移 |
| loss-review-seals-0156/native-terminal-v5.json | 0156 数量/SN 通过，1739 文件零漂移；仅覆盖该历史候选 |
| loss-review-recovery/native-terminal.json | b4a964e 审批恢复本地数量/SN 终态 |
| loss-receipt-inbound-0155/native-v4-terminal.json | 0155 收货/独立入库/恢复本地数量/SN 终态 |
| loss-http-submit/native-terminal.json | 原报损提交 HTTP 本地终态 |

0156 v5 数量库 `run-flxjwk3f`、SN 库 `run-o500b2w_` 均正常停止，runner 28123 已退出 0。其迁移/权限、实际 COMMIT 双向互斥、API 原始 SQL 拒绝、HTTP、空库往返和有历史拒绝降级已通过。该证据的 `matchesCurrentSource=true` 是生成时历史断言，不代表当前新增源码。

本轮最终代码安全检查、依赖 pip check 和 diff 检查通过；修改源码后仍需对应复验。失败日志保留，不能把修正后的定点通过改写成原整轮全绿。

## 6. GitHub 发布与准确 SHA CI

2026-09-30 13:24 实时只读查询，两个新运行的 `headSha` 均为 `ff25289a6d497711dd4275260e05b5eab6454b52`：

| 门禁 | 本次状态 | 运行 |
| --- | --- | --- |
| Client release gate | in_progress，尚无结论 | [36672988759](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36672988759) |
| PostgreSQL 16 release gate | queued，尚无结论 | [36672988843](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36672988843) |

快照：`artifacts/loss-review-queue-next/handoff-exact-sha-ci-20260930.json`。接手必须刷新，不能把排队或运行中写成通过。本次仅交接，不等待 CI 或触发重跑。

发布过程：普通 Git push 443 连接失败；Git 读取曾遇 HTTP/2 framing，API 曾遇 TLS 超时。之后按不可变对象 SHA 回读并续传，三份提交与本地 SHA 完全一致，非强推更新后精确分支回读为 ff25289。故“尚未推送”已过时，网络错误不是凭据失效。最终证据：`push-api-v3.log`、`api-ref-after-resume-result.json`、`push-result.json`（均在 loss-review-queue-next 下）。不要重跑固定旧坐标的上传脚本；结果未知先回读原对象。

父版本 `3e67e51` 的 [PG16 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765) attempt 2 为 completed/failure：19 个运行任务成功，静态 0/1/2 与汇总失败。`parent-static-{0,1,2}-final.log` 中 1 明确 runner shutdown，0/2 operation canceled；未见最终断言失败汇总，底层取消原因未确认。该历史失败不能替代新 SHA 结论。父客户端 36593125653 历史成功同样不覆盖新提交。

## 7. 接续执行顺序

1. 重新确认分支/HEAD/diff，读本页和机读断点；保留所有未提交文件。
2. 本轮 H5 类型、集成测试、导航/路由权限、后端 fixture schema/命令互验已完成；不要重复解决旧类型错误或重拷草案。
3. 前端/小程序回归与双入口构建已完成，记录其证据边界；真实服务/设备/身份 UAT 仍需独立验收。
4. native-v4 已通过并结束，回读终态文件即可；不要重复启动。
5. 当前代码已提交并发布，先只读跟进 ff25289 的两个准确 SHA CI（第 6 节）。若失败，保留日志并区分断言失败与 runner 中断；不要重复提交或盲目重跑。当前本地验收记录见 LOSS_REVIEW_H5_LOCAL_ACCEPTANCE_20260930.md。
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
cat artifacts/loss-review-ui-next/integrated-typecheck-v4.log
~/.local/bin/gh run list --repo likexin12985/rsc-personal-warehouse --commit ff25289a6d497711dd4275260e05b5eab6454b52 --limit 10 --json databaseId,headSha,name,status,conclusion,url
```

当前 Node 不在默认 PATH，使用 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`；后端解释器 `.venv/bin/python`。本轮本地验证已结束，无需再轮询旧会话。新增代码须独立验证，不能复用本轮 source-manifest 当作新源码证据。

注意 cwd，避免在 frontend 下再次拼 frontend/src。不要重新执行旧草案生成器。ignored artifacts 不随 Git 克隆；换机器应受控复制必要日志/合成 fixture 或重跑，不能复制真实数据、凭据或临时数据库目录。

### 下一批已准备到哪里：退回接收 H5

`artifacts/loss-return-h5-next/export-v2.log` 已有终态 **4 passed，142.24 秒**，工单/报损 × 数量/SN 四份样本全部生成。这些是本地合成数据经真实后端服务输出的合同，不是真实业务数据，也不证明 H5 已完成：

- `loss-receiving-{quantity|serial}.json`、`work-order-receiving-{quantity|serial}.json`：身份、接收目录、验收前历史、原命令、验收结果、验收后历史。
- 生成器 `export_receiving.py` 和两个 `test_export_*.py` 仅在 ignored artifacts 内；下次接入正式合同测试时审核、筛选字段并复制合成 fixture。当前样本尚未覆盖独立入库合同。
- 下一步先补 H5 严格目录/验收/入库合同和独立请求恢复模块，再做页面、导航、权限与异常证据上传/扫码交互。验收 API 使用 shipment_id；入库 API 使用 receipt_id。详见 [退回 H5 接入验收](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)。
- 保留报损 origin 五字段与工单 work_order_id 的互斥来源；数量按三位小数精确处理；破损是接受的子集，短少不入账；验收与入库独立幂等。未知请求先读原 request，不重新 POST 或换 key。
- 本次进程检查未见该 fixture 导出器、发布恢复脚本或 PG16 检查 runner 仍运行；旧会话 42182、91875 不作为新任务句柄复用。两份 PG16 库已正常停止。

## 8. 上线验收仍缺的独立事实

真实短信 PNVS、人员唯一身份映射与实发回读；真实 OSS/KMS；公开知识来源授权；期初数据与 OAM 只读批次语义；真实设备 UAT；至少连续三天对账差异解释；500 用户压测；RPO≤5 分钟/RTO≤2 小时备份恢复、发布回滚及业务冲销演练。备案通过截图不代替域名/HTTPS/双入口生产验收。

受邀 HTTPS H5 小范围试点与完整 V1.0 分开验收，不提供无证据的完成百分比或上线日期。

相关文档：[本批本地验收](LOSS_REVIEW_H5_LOCAL_ACCEPTANCE_20260930.md)、[退回 H5 接入验收](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)、[基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT/上线矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。

历史入口：[09:34 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0934.md)、[09:05 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0905.md)、[08:45 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_0845.md)、[9 月 30 日早期](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)。旧文档的运行中状态和下一步已被本页替代，保留仅供追溯。

## 9. 退回 H5 开发增量（2026-09-30，尚未提交）

正式源码新增：

- `frontend/src/formalReturnReceiving.ts`：当前身份、互斥来源、分页、包裹、逐笔验收累计量/SN。数量使用 bigint 毫单位，保留微秒时序；短少不消耗后续验收额度。
- `frontend/src/formalReturnReceipt.ts`：原验收命令规范化及 Python 一致的 hash；预检与选择核对；验收/封存原请求结果。
- `frontend/src/formalReturnInbound.ts`：独立入库状态、已接受量/SN 计划、原入库请求，以及 posted/transaction/sealed 事实。
- `frontend/src/returnReceivingRecovery.ts`：保存并回读确认后仅发送一次；按人员/包裹共享锁阻断验收和入库冲突。刷新、切页、失权、错误回执保留原请求；永久封存必须明确确认，精确 GET 后才清理。
- `frontend/src/returnReceivingAdapter.ts`：默认 apiNoReplay，当前身份/权限摘要前后核验。仅对应接口明确的 404 not_observed 可转待回查；验收用 shipment_id，入库用 receipt_id，封存不发幂等键。
- 五份模块各有测试；`frontend/src/test-fixtures/return-receiving/` 四份合成数据；`backend/tests/test_return_receiving_h5_contract.py` 校验当前 schema roundtrip、验收/入库原 hash 及独立过账事实。

证据位于 `artifacts/loss-return-h5-next/`：`contracts-recovery-v2.log` 74 passed、`backend-parity-v2.log` 4 passed、`typecheck-v6.log` exit 0。最新后端服务导出 `export-preview-v1.log` 4 passed，已包含验收预检及独立入库前后/预检/命令，覆盖工单/报损与数量/SN 四组合。早期类型检查的合成 JSON 空数组推断错误已使用解析后的类型修复，没有放宽 tsconfig。

**下一步直接接页面和路由，不重复移植这些模块。** 仍需初次预检/明确确认 UI、异常文件正式上传、实际扫码证明、重入待核验列表、明确封存交互、区域/总部导航及直达权限、浏览器验收。模块测试不等于 H5 页面可用。没有新增生产迁移/权限种子，未提交、未部署。

13:57 全量前端终态：`frontend-full-v1.log` 为 100 文件、1773 passed，43.93 秒；runner 21843 exit 0，当前无待轮询本地测试。新源码摘要已写入机读断点。此结果包含上述 74 项，不能相加。

现有独立入库接口保持原成色，只转移已接受物料的保管责任；破损观察不是授权成色转换交易。页面保留异常并与独立处置区分，不把破损数量重复相加。

ff25289 CI 快照：`parent-ci-progress-v2.json`；静态 0/1 日志分别为 `ci-job-109751834828-v2.log`、`ci-job-109751834630-v2.log`，均明确 shutdown/canceled。继续回读原运行 36672988843 终态，不因观察失败重启。gh 首次读日志因终端转义序列保护拒绝，第二次允许后只保存并去除 ANSI，已成功；不是 GitHub 身份失效。
