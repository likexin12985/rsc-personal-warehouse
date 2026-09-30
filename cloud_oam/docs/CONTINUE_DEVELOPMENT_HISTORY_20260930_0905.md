# RSC 个人仓开发交接

核验时间：2026-09-30 09:05（Asia/Shanghai）。这是当前接续入口，路径默认相对 `cloud_oam/`。本轮已提交 0156，并接入下一批审批查询与照片授权；未推送、未部署。

**报损收货、独立入库及请求恢复已完成本地实现和验证。0156 审批封存/写 HTTP/并发保护已提交 `1f65dcb`，其数量/SN PG16 v5 均通过。下一批审批查询/详情和原单照片授权已接入源码，84 项聚焦测试通过，新原生 PG16 正在验证；私有 H5 页面、准确新 SHA 完整 CI、真实 UAT 和上线仍未完成。**

## 本轮接续断点

- 最新本地提交 `1f65dcbed655a0a1e7c4054ba8b6fe1f2d86d3d1`；42 文件提交，提交时工作树干净。证据 `artifacts/loss-review-seals-0156/commit-evidence.json`。它包含前述 0156、HTTP、并发和迁移权限修复；不要重复提交或应用旧 patch。
- 当前新改动：`stock_loss_review_query_schemas.py`、`stock_loss_review_query.py`、`stock_loss_review_evidence.py`、正式路由和文件下载接入，以及应用/HTTP/PG16 测试。已从 ignored 草案转入源码，不能继续标为“尚未接入”。
- 新增 GET `/api/v1/stock-operations/loss-reports/reviews/{regional|headquarters}` 及 `/{operation_id}`，分页允许 pending/all；按当前同一角色授权绑定和本区域/全国范围读取，申请人不能自审，原单/审批证据不完整则阻断。照片通过已有 `/api/v1/files/{file_id}/download-intent` 按原单读权限签发并留审计，不要求保留审批写权限。
- `artifacts/loss-review-queue-next/integrated-v2.log`：**84 passed / 255.60 秒**；文件服务 `file-compatibility-v1.log`：**44 passed**；原报损附件 `loss-evidence-compatibility-v1.log`：**16 passed**。早期 integrated-v1 因工作目录错误没有执行测试，不计通过。草案 v2 的 46 passed / 2 skipped 是历史，不当作当前集成结果。
- **原生会话 58080 / runner PID 83337 已确认存活**，当前数量库 `artifacts/local-stock-loss-review-seals-pg16/checks/run-qztav2m4`，日志 `artifacts/loss-review-queue-next/native-v1.log`。冻结 **1745 份非 Markdown 源码**至数量/SN 完整终态；不要因观察超时重启。新门禁在真实 API 角色下验证只读 HTTP、分页/区域越权、撤销审批写权限仍读、照片下载审计和读权限撤销拒绝。
- 当前批次未提交。先回收现有原生会话终态及停库、清单一致性；再审查本批并补私有 H5。旧 0156 终态只覆盖提交时的 1739 份源码，不覆盖新增查询/文件下载。
- 父版本 CI attempt 2 最近实时回查仍为 static 0/2 运行、static 1 runner shutdown 失败。未推送，避免取消父运行；新候选最终仍需准确 SHA CI。
- 当前机读断点见 `artifacts/loss-review-queue-next/continuation.json`；`loss-review-seals-0156/continuation.json` 已指向该入口。


## 1. 接手位置和约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`。不要在另一默认 checkout 接续。禁止 reset、revert、丢弃或覆盖未提交改动。
- 本地 HEAD：`1f65dcbed655a0a1e7c4054ba8b6fe1f2d86d3d1`，未推送；前一提交 `b4a964e` 也尚未推送；远端分支本次实时回读为 `3e67e51714d301bf66126b8ec55741e378d0720c`。
- 09:00 前的 42 文件已提交；当前查询批次文件以实时状态为准。早期交接快照不代表当前 diff。完整清单以 `git status --short` 为准；快照 `artifacts/loss-review-seals-0156/handoff-worktree-20260930-latest.json`。只拉远端会遗漏本地提交、未提交源码和 ignored 草案。
- 先完整阅读[正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)及 [AGENTS.md](../../AGENTS.md)，遵守用户最新业务路由要求。
- 公开首页为“交流备件知识大全”，无登录；星星按钮跳转 `https://rscwz.cn/xx`，`/xx` 是路径。公开小程序仅知识查询，不包含私有仓库页面；飞书知识源后置。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入库、通知与对账分别存证。结果未知先精确回查原请求，不自动重放。
- 本地合成验证不代表真实身份或业务验收，不授权真实短信、外部业务写入或生产迁移。凭据、生产数据不进 Git。

## 2. 已完成与未完成

| 范围 | 已有实现和本地证据 | 尚缺 |
| --- | --- | --- |
| 报损接收侧 0155 | 来源列表/详情，收货预检/提交/回查/封存，独立入库，数量/SN 与客户端恢复隔离；原生 PG16 完成 | 私有 H5 接入、真实 UAT 和生产验收 |
| 原报损提交 HTTP | `3fa414f`：提交/封存、COMMIT 后回执、结果未知回查，实际 API 角色 HTTP 验证 | 完整报损客户端闭环 |
| PG16 门禁拆分 | `3e67e51`：migrations/inventory/control 与报损独立分支；父 SHA 实质任务成功 | 静态及汇总门禁未全绿，不能覆盖新 SHA |
| 审批只读恢复 | `b4a964e` 本地提交：区域/总部独立回查，当前读写权限分离，数量/SN 通过 | 推送及该准确 SHA 完整 CI |
| 审批写入/独立封存 0156 | 已提交 1f65dcb，数量/SN v5 完整通过 | 推送及新 SHA 完整 CI |
| 审批查询/照片授权 | 正式服务、路由和下载已接入，84 项集成聚焦通过 | 新 PG16 终态、私有 H5 与新 SHA CI |
| 报损发件侧 | 内部退回/出库/发运服务及门禁 | 正式发件 HTTP 的精确来源合同与客户端恢复 |

收货只确认实物验收，入库独立过账；区域核实/总部批准均不改变库存，总部批准仍待处置。私有小程序接收页面不等于 H5 页面上线。

## 3. 当前 PG16：v5 已完整通过

日志 `artifacts/loss-review-seals-0156/native-v5.log`；会话 **28123 已退出 0**，不能继续轮询或重启历史实例。整理期间任务完成，最终核验替代 08:45 的运行中观察。

| 模式 | 实例（前缀 `artifacts/local-stock-loss-review-seals-pg16/checks/`） | 终态 |
| --- | --- | --- |
| 数量件 | `run-flxjwk3f` | 08:38:22 完成，passed=true、sourceDrift=[]；stopped/checks=passed/serverExitCode=0 |
| SN | `run-o500b2w_` | 08:49:13 完成，passed=true、sourceDrift=[]；stopped/checks=passed/serverExitCode=0 |

统一终态证据 `artifacts/loss-review-seals-0156/native-terminal-v5.json`，包含两种模式的 checks、cluster-state、source-manifest 文件摘要。本次重新生成 manifest，两份清单均与当前 **1739 份非 Markdown 源码**逐项一致。

区域/总部审批与封存、API 角色 HTTP、原始 SQL 拒绝、真实锁竞争、空库迁移、运行期权限和保留历史拒绝降级均通过。**0156 这一轮冻结已结束**；新增查询批次当前另有 1745 文件冻结，以上方断点为准。新增源码须产生自己的验证证据；该本地证明不等于 GitHub 或生产验收。

历史：v2 通过只覆盖旧 1736 份源码；v3 因三写者间接阻塞断言失败；v4 在确认 SQL/Python 文本规则差异后主动停止。v3/v4 均正常停库，不算通过，不替代 v5。所有已停旧库均不重启。

## 4. 当前 0156 改动和测试证据

- 新迁移 `backend/alembic/versions/20261205_0156_stock_loss_review_request_seals.py`，配套 `stock_loss_review_seals.py`、`stock_loss_review_seal_schemas.py`、ORM、运行期权限目录和 head 检查。历史迁移未改；API 无私有 SQL EXECUTE 权限；封存不可改删，保留历史阻止降级。
- 区域/总部各自封存原命令、request/key/hash；迟到审批与封存在实际 COMMIT 双向互斥。恢复使用当前读权限，所有结果 retry_permitted=false，查不到不自动重发。
- 路由 `backend/app/routers/formal_stock_losses.py`：`/api/v1/stock-operations/loss-reports/regional-reviews`、`/headquarters-reviews`，各自含 `/request-lookup`、`/request-seal`。成功回执在 COMMIT 后返回，未知只回查。
- 并发验证通过真实 pg_blocking_pids 链找到准确原写事务，支持第二等待者先被第一等待者阻塞。独立复现 `lock-queue-checks/run-nvz02ob7/checks.json`。
- 修复 Python strip 与 SQL btrim 的 Unicode 首尾空白差异、理由 CR 漏拒绝。29 种空白共 58 个边界实测，保留合法多行中文；只更新未提交 0156 及函数摘要。诊断 `text-contract-checks/run-zjr8ms2a/checks.json`。
- CI 新增 review_seals，9 个报损流程 × 数量/SN = 18 分支；旧 0147/0148 的历史拒绝降级仍由原独立门禁证明。
- **四份旧 HTTP/head、并发、原生 HTTP、CI patch 和权限目录修正全部已应用。不得再次应用或重跑生成器覆盖现有文件。**

证据在 `artifacts/loss-review-seals-0156/`；集合存在重叠，不累加总数。

| 日志 | 实测结果 |
| --- | --- |
| http-contracts-v2.log | 95 passed；v1 的 4 个断言问题已修复并复验 |
| topology-head-v4.log | 28 passed；历史链漏 0155 父节点已修正 |
| security-head-cli-v1.log | 389 passed / 2 failed；保留原失败，不能称该轮全绿 |
| security-catalog-v2.log | 上述表/触发器清单修复后，2 个失败用例定点通过 |
| text-contracts-v3.log | 41 passed，包含旧合同，与 HTTP 集合重叠 |
| migration-text-v2.log | 当前文本规则和迁移 4 passed |
| services-v1.log | 早期服务/恢复 155 passed；后续改动以最新对应门禁为准 |
| repository-safety-v5.log | PASS，1918 文件；本次整理前结果 |

0156 的最终审查与仓库安全已完成并提交；新查询批次独立验证，证据齐全前不提交。

已有独立历史证据：0155 的 `artifacts/loss-receipt-inbound-0155/native-v4-terminal.json`；提交 HTTP 的 `artifacts/loss-http-submit/native-terminal.json`；审批恢复的 `artifacts/loss-review-recovery/native-terminal.json`（104 项聚焦、46 项 HTTP 兼容、数量/SN 完成，1728 份源码零漂移）。这些证明各自候选，不覆盖当前新增代码。

## 5. 草案历史与后续页面工作

`artifacts/loss-review-queue-next/` 被 Git 忽略，含：

- stock_loss_review_query_schemas.py：待办/详情 DTO。
- stock_loss_review_query.py：角色同一授权绑定、区域/全国范围、分页、原提交/审批事实核验和只读查询。
- stock_loss_review_evidence.py：审核人按原单当前读权限看照片，拒绝跨业务混合绑定。
- test_stock_loss_review_query_draft.py、run_draft_tests.py、draft-v1.log。

上述草案已经接入正式源码并补齐测试：84 项集成聚焦通过。ignored 旧版 36 项及草案 v2 结果仅作历史，继续修改正式源码而不要覆盖回旧草案。

分页/稀疏页、真实文件下载意图及审计、HTTP 隐私和错误边界已加入当前集成测试。新 PG16 终态待回收，私有 H5 的权限显隐、待办/详情、审核及原请求保存/恢复仍需开发。

草案及原生证据不随 Git 克隆；换机器须受控复制必要文件或重跑，不复制数据库数据目录、凭据或真实业务数据。

## 6. 远端 CI：父 SHA 第 2 次仍运行

本次 GitHub CLI 只读快照：`artifacts/loss-review-seals-0156/handoff-pg16-ci-20260930-latest.json`。

- [客户端 36593125653](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125653)：父 SHA 3e67e51 成功。
- [PG16 36593125765](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36593125765)：attempt 2 / in_progress；static 0/2 仍运行，static 1 失败；19 个实质任务显示成功。
- static 1 job 109678169916 日志含 runner shutdown / operation canceled，见 ci-attempt2-static1.log；未观察到断言失败，底层原因未确认，不概括成“GitHub 链接失败”。
- 第 1 次整体失败是历史，不能把第 2 次写成“无在跑任务”；未终结时不要反复重跑。
- cancel-in-progress=true；先回读父运行终态再推送，避免取消取证。新提交必须有自己准确 SHA 的客户端/完整 PG16 门禁，不能继承父版本绿灯。
- 旧 push-exact-api.py 固定旧 BASE/commit/tree，禁止原样重用；推送结果未知先精确回读，禁止盲目重试。

## 7. 下一步顺序及命令

1. 核对工作树、HEAD、diff、进程与 continuation.json；不重复开发已完成的 0155 和 b4a964e。
2. 回读 native-terminal-v5.json 与两份实例证据；v5 已完成，不再轮询会话 28123 或重启旧库。
3. 0156 已完成审查并提交；当前应收齐查询批次会话 58080 的完整终态及新源码清单，再审查提交。
4. 回读父 CI 终态，再推送已审查提交；要求新准确 SHA 完整 CI，不把文档完成当上线完成。
5. 完成待办/详情、原单照片授权、私有 H5，再补发件出库/发运精确来源及客户端恢复。
6. 继续报废反向冲销、人员调拨、离职交接；分别准备真实 UAT、试点及正式生产验收。

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git rev-parse HEAD
git status --short
git diff --check
cd cloud_oam
cat artifacts/loss-review-queue-next/continuation.json
cat artifacts/local-stock-loss-review-seals-pg16/checks/run-o500b2w_/cluster-state.json
cat artifacts/loss-review-seals-0156/native-terminal-v5.json
```

草案后续复验（不集成正式源码，保留旧日志）：

```sh
# 工作目录 cloud_oam
.venv/bin/python artifacts/loss-review-queue-next/run_draft_tests.py > artifacts/loss-review-queue-next/draft-v2.log 2>&1
```

## 8. 上线缺口与相关文档

- 用户已确认目标为旧备份脚本服务器 118.31.37.87，不再重复问。Ubuntu 24.04/x86_64、旧 star-oam 占用 80/443 是 9 月 24 日历史；本次未连服务器，部署前重查容器/卷/端口/镜像/回滚点。
- 新版首页与 /xx 无生产双入口验收；备案通过截图不等于域名、HTTPS、内容和部署验收。
- 短信仅配置及合成验证，真实 PNVS、身份唯一映射、实发及回读未验收；outbox 不等于送达。
- 真实 OSS/KMS、来源公开性、期初数据、OAM 批次语义、设备 UAT、500 用户负载、RPO≤5 分钟/RTO≤2 小时恢复及至少连续三天对账解释待补。
- 受邀小范围 HTTPS H5 试点与完整 V1.0 发布分别验收，不给无依据的完成百分比或上线日期。

导航：[基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT/上线矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。

历史：[本次整理前完整入口](CONTINUE_DEVELOPMENT_HISTORY_20260930_0845.md)、[9 月 30 日早期归档](CONTINUE_DEVELOPMENT_HISTORY_20260930.md)、[9 月 29 日归档](CONTINUE_DEVELOPMENT_HISTORY_20260929.md)。历史“下一步”不直接执行；当前状态以本页和最新实际产物为准。
