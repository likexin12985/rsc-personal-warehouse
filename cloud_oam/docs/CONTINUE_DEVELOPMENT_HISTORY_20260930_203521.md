# RSC 个人仓开发交接

2026-09-30，路径默认相对 `cloud_oam/`。本文件为当前入口；历史记录不能覆盖下列状态。

**当前 HEAD 为 `5e847d2`，退回收货、独立入库与请求恢复 H5 已提交发布；本人报损 H5 已接入但尚未提交、未部署。本地数量/SN PG16 门禁已通过，发件读取已整合正式源码，隔离回归 51 项通过；正式目录测试的时间夹具修正后 12 项通过；远端完整 CI、真实 UAT 和正式上线均未完成。**

## 最新接续状态（优先于下方历史过程记录）

- 11 个发件读取后端模块和 3 个测试已经整合到正式源码；没有开放新的发件写入/封存接口。`integration-v1.patch` 已应用，不能再次盲目应用。
- `read-regression-v2.log` 已结束：51 passed / exit 0；会话 64381 已结束，不再轮询。
- `formal-read-v1.log` 已结束：23 passed / 2 failed。两项均为目录历史保管测试：夹具把 valid_to 设为当前时间减一秒，运行较快时会早于原派生时间，正确触发 `stock_loss_return_evidence_invalid`。不能把它归为网络故障。
- 仅修正正式目录测试的时间夹具，明确建单时间 < 保管责任结束时间 ≤ 当前查询时间；生产历史证据和权限校验未放宽。全目录复验 `formal-directory-v2.log` 已结束：12 passed / exit 0（121.31 秒）；会话 2373 已结束，不再轮询。
- 已通过的本人报损 H5 PG16 不覆盖新增发件读取；新增读取仍需独立 PG16 门禁。未提交、未部署。

- 新发件读取 PG16 已启动：会话 68460、父 PID 16674，`loss-sender-read-next/native-read-pg16-v2.log`；首个隔离库 `local-stock-loss-return-shipment-pg16/checks/run-olw99neq`，1803 个非 Markdown 源文件冻结。只更新文档及 ignored 候选，直到门禁终态。
- 新 PG16 检查通过真实 HTTP 及数据库权限验证退回待出库、已出库未发运、发运后历史三个阶段；正式服务和 require_permission 不被替换，仅注入合成身份和 API 会话，用 SELECT-only 语句监测及完整事实快照证明不写入，同时保留期初核验的行锁。还未终态，不能计通过。
- 独立恢复候选 `loss-sender-read-next/loss_return_sender_recovery.py` 保存完整命令比对、幂等键/计划摘要/跨操作冲突和前后审计游标检查；未观察到结果仍 retry_allowed=false。会话 36144 已退出 0，`recovery-candidate-v1.log` 为 12 passed（345.83 秒）。尚未正式整合、没有新 HTTP 路由，封存仍需 0157 和并发验证。
- 远端 5e 的 PG16 attempt 1 已终态：21 成功（含 inventory）、3 个 static_safety 失败、最终汇总失败，共 4 failure；证据 `new-sha-pg16-v8.json`。三个 static_safety 的失败步骤仍是 cancelled/runner shutdown。已对同一 run 36690470476 仅请求重跑失败作业，命令退出 0，证据 `new-sha-pg16-rerun-v1.json`；已回读 attempt 2 in_progress，证据 `new-sha-pg16-attempt2-v1.json`；不能把接受重跑当成功。

- 封存迁移候选 SQL／SQLite 检查已终态：`seal-migration-candidate-v1.log`，3 passed / exit 0，会话 85267 已结束；只证明解析、SQLite 保持拒绝、守卫原样保留及有历史阻断降级，不证明 0157 PostgreSQL 已通过。恢复候选 36144 的 12 项亦已通过。

- 新读取 PG16 v1 会话 15528 已失败退出 1，`run-5dt0qpip` 已 stopped/serverExitCode=0。失败原因是测试强制 PostgreSQL READ ONLY，但现有期初证据核验需要 SELECT FOR UPDATE；没有修改生产查询或取消一致性锁。门禁 v2 改为 API SQL 仅 SELECT/SHOW、拒绝 DML，并前后核对全部业务事实。v2 尚在运行。

- 发件恢复/封存候选升级到 v2：独立 `loss_return_sender_seals.py` 为当前用户/原请求 ID 创建永久封存，保留原单来源与审计，不声称记录了尚未存储的 key/plan。候选测试元数据仅在隔离进程使用拟定 0157 的来源 CHECK，绝不是实际 PostgreSQL 迁移通过。
- 服务 v2：会话 93740 / PID 19305，`recovery-candidate-v2.log`，20 项仍运行。v1 已通过 12 项的精确源码另存 `recovery-v1-verified/`，不能用 v1 结果证明 v2。
- 完整 HTTP 候选：`formal_loss_return_sending_with_recovery.py` 及 `loss_return_sender_recovery_schemas.py` 增加预检、提交、回查、封存；`sender-write-http-candidate-v1.log` 已 4 passed / exit 0（136.79 秒），会话 39428 已结束。覆盖数量/SN 出库和发运、错误请求头、不重复恢复、独立封存、迟到提交拒绝和写权限失效后的回查。没有整合/开放正式 POST 路由。
- PG16 v2 已走到数量 pending_departure 的真实 HTTP 成功，后续出库/发运阶段与 SN 仍需终态，源冻结继续有效。
- 远端 attempt 2 的 static_safety (1)，job 109880081232 再次在约 65% 收到 runner shutdown 后 cancelled，未出现测试断言失败；原因未定，其余两个静态作业仍在运行。最新状态和日志为 `new-sha-pg16-attempt2-v2.json`、`ci-job-109880081232.log` 和 annotations。取日志需 gh api --allow-escape-sequences 后捕获并清除控制字符；首次读取被 gh 拒绝输出 ANSI，不是网络失败。不盲目重跑第三轮。

## 1. 工作树和边界

- 工作树 `~/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`。默认 checkout 不是目标；禁止 reset、revert 或丢弃未提交改动。
- 完整 HEAD：`5e847d282cb1e6abdd1bc3a5af31e4c2c10301c4`；父版本 `ff25289a6d497711dd4275260e05b5eab6454b52` 为审批待办与 H5，`1f65dcb` 为 0156 审批封存。先读基线及当前 diff，含未跟踪文件。
- [正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)仍是要求；用户后续入口变更优先。公开首页“交流备件知识大全”无登录，星星管理按钮跳 `https://rscwz.cn/xx`；公开小程序仅知识查询，飞书知识源延后。
- 审批、冻结、出库、发运、签收、验收、个人仓入账、OAM 收货、通知、对账分别取证。未知写结果保存原请求，只回查，不自动重发或更换 key。
- 目标服务器为用户确认的旧备份服务器 `118.31.37.87`。Ubuntu 24.04、star-oam 占用 80/443 是历史观察，部署前重查。本轮未连接服务器或真实业务系统。

## 2. 已发布版本及远端门禁

5e847d2 已非强推发布，Git Data API 对象和最终 ref SHA 均核对一致，证据 `artifacts/loss-return-h5-next/push-result.json`。普通 Git push 曾 TCP 443 超时，不能按网络错误推断代码丢失。旧发布脚本内固定 SHA，禁止直接拿来发布新候选。

| 准确 SHA 的门禁 | 最新有效回读 | 证据 |
| --- | --- | --- |
| [Client 36690470446](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36690470446) | completed / success | loss-return-h5-next/new-sha-ci-v2.json |
| [PG16 36690470476](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36690470476) | in_progress；20 成功、3 失败、inventory 运行中；整体未通过 | loss-return-h5-next/new-sha-pg16-v6.json |

PG16 已成功的是 18 个报损模式/流程分支和 migrations、control。三个 static_safety 日志均明确 runner shutdown / operation canceled，未得到完整断言终态；关闭原因尚未确定。日志为 `ci-job-109806745912.log`、`ci-job-109806746179.log`、`ci-job-109806746234.log`。不要把失败说成通过，也不要在原 inventory 仍活跃时盲目重启整套门禁。

5e 收货候选此前本地 PG16 quantity `run-ouwc9w3y`、serial `run-hpbc7t_f` 均已正常停库，1778 文件无漂移，终态在 `loss-return-h5-next/native-terminal-v1.json`。旧会话 57217 已结束，不再轮询。此清单不覆盖下面新报损代码。

## 3. 当前未提交：本人报损 H5

- `formalLossSubmission.ts`：本人来源、固定精度数量、实物 SKU/SN/二维码、SN 精确查询/分页、证据、预检和原请求回执；hash 与 Python 一致。
- `lossSubmissionRecovery.ts`：完整确认快照写前持久化及回读，个人仓 Web Locks，未知提交保留，读权限恢复，单独确认封存。
- `lossSubmissionAdapter.ts`：所有接口使用 apiNoReplay；当前身份、角色有效期、权限指纹前后核验。来源/提交/封存需要写权限，已有原请求回查只需要读权限。
- `LossSubmissionForm.tsx`、`FormalLossSubmissionPage.tsx`：真实来源选择、SN 查询及空白实物证明、正式凭证上传、原因、整单预检、明确确认、恢复和封存。私有路由 `/loss-reports/new`，菜单“本人报损”，三类内部角色与 stock_operation.read 门禁。
- `backend/app/inventory_schemas.py`：仅 JSON 输出将 projected_at 统一 UTC；SQLite 的无时区测试值与 PG16 aware 值保持同一语义，内部 datetime 不变。无新迁移或权限种子。
- 5 份前后端合成 fixture、合同互验和页面/恢复/adapter 测试。`styles.css` 的上传控件窄屏修复限定本人报损页。

当前迁移 head `20261205_0156`。实际源码与精确清单用 `git status --short --untracked-files=all` 查看，不用 diff --stat 代替未跟踪文件检查。

## 4. 当前候选验证与运行句柄

证据目录 `artifacts/loss-submission-h5-next/`。合成浏览器不连接生产，上传为模拟，不证明真实 OSS 或相机扫码。

| 验证 | 已有结果 | 证据 |
| --- | --- | --- |
| 页面/路由/合同/恢复/adapter | 77 passed，含未知封存、重复确认、上传未完成、查询失败保留实物输入 | page-focused-v4.log |
| 后端合同及库存读取回归 | 62 passed | backend-inventory-parity-v1.log |
| 完整前端（新增 4 项前） | 1862 passed，107 文件 | frontend-full-v3.log |
| 最终完整前端 | 1866 passed，107 文件，exit 0 | frontend-full-v4.log |
| 小程序 | 1079 passed | mini-full-v1.log |
| 最终 TypeScript 与双构建 | exit 0；私有 bundle 仍有体积提示 | build-public-v3.log、build-private-v3.log |
| 公开入口隔离 | 通过；catalog pending / 0，内容未就绪 | public-entry-v3.log |
| 共享期初协议、依赖 | PASS / No broken requirements | opening-protocol-v1.log、pip-check-v1.log |
| 单一迁移 head | 1 passed，exit 0 | migration-head-v2.log |
| 浏览器 | 数量/SN 确认、未知结果刷新保留、只读回查、390/1280 宽度通过 | browser-acceptance-v1.json、3 张 browser PNG |
| 当前安全扫描 | PASS，1984 文件，exit 0 | repository-safety-v2.log |
| 本人报损 H5 PG16 | 数量/SN 均通过、临时库正常停止、1794 文件零漂移；会话 66660 exit 0；不覆盖新增发件候选 | native-pg16-v2.log、native-terminal-v2.json |

本轮已修正：新增上传测试直接赋值 readonly mock 导致 TypeScript 失败，改用 mockImplementation 后最终双构建成功。为保持最终源码清单一致，主动中断旧 PG16 v1 会话 52276（exit 1），其自建库 `run-4oun39r8` 已正常 stopped/serverExitCode=0，不能计通过。修正后的 v2 使用全新库，不访问旧库或生产。迁移 v1 命令文件名写错，未运行测试；v2 是精确已存在的 test_revision_history_has_single_current_head。

当前源码冻结期间只更新文档与 ignored 证据；若需业务代码修复，先确认原运行状态并由所有者正常结束，禁止更改冻结源码后沿用旧证据。会话只是线索，每次接手先核对实际句柄/进程终态。

## 5. 接续顺序

1. 核对工作树、分支、HEAD、全部 diff；完整读正式基线和本批 [H5 验收](LOSS_SUBMISSION_H5_ACCEPTANCE_20260930.md)。
2. 31646（前端）、96461（单 head）、22480（安全扫描）、66660（本人报损 H5 PG16）均已退出 0，不再轮询。数量 run-cbiuel99、SN run-ejl5h_at 均 passed/stopped/serverExitCode=0，1794 文件零漂移；证据 native-terminal-v2.json。发件候选另需正式整合与对应数据库门禁，不能沿用 H5 终态声称新增发件已验证。
3. 完成当前最终安全扫描和差异审查。按用户要求，证据齐全再精确暂存、提交；本地 PG16、完整远端 CI、真实 UAT 各自记录，不能互相替代。当前尚未提交。
4. 回读准确 5e CI 的终态及失败日志；后续发布新候选必须核对远端 ref 和新准确 SHA，禁止旧固定脚本/强推。
5. 继续 [报损发件 HTTP/恢复审计](LOSS_SENDER_HTTP_RECOVERY_AUDIT_20260930.md)：内部派生退回/出库/发运已有；正式报损发件接口与恢复仍缺。现行封存约束只允许 loss receive_return，须在 0156 后新增迁移及双向并发、来源/审计证明；不伪造 work_order_id，不只挂路由。
6. 发件读路径新增精确诊断已终态：数量/SN × 出库/发运 options/history 共 8 项复现缺口，query_only 和前后快照不变；options 拒绝普通来源空值，history 误用总部历史 actor 拒绝工程师。证据 sender-query-gap-result-v1.json；会话 26835 已退出 0，不再轮询。需同时补本人目录、来源选项、历史和恢复，不能只挂 POST。
7. 继续处置/反向冲销、人员调拨、离职交接及通知真实渠道等基线缺口，不把已有通用流水原语算完整业务闭环。

## 6. 运行环境与上线边界

从 `cloud_oam/` 使用 `.venv/bin/python`、`PYTHONPATH=backend`。Node 在 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`，pnpm 在相邻 `dependencies/bin/fallback/`。前端完整测试用 `--maxWorkers=2`；曾在高负载默认并发下出现超时，未调断言或超时掩盖失败。PG16 为 `artifacts/pg16-native-20260920/install/bin`，运行器只创建自己拥有的隔离库。CI 定义在仓库根 `.github/workflows/`。

**artifacts 被 Git 忽略，不随 clone/push 转移。** 保存受审查的日志、终态 JSON、源码摘要及合成 fixture，或按脚本重建；不复制生产数据库、令牌或会话。

正式上线仍需：真实短信 PNVS/微信与唯一身份绑定、OSS/KMS；授权 OAM 只读批次、历史数量/附件校验和真实期初实盘；多角色真实设备 UAT；连续至少 3 天对账；500 用户压测；RPO≤5 分钟、RTO≤2 小时恢复；应用回滚、同步批次回退和真实业务冲销。备案截图、提交和本地测试均不等于上线。

相关索引：[退回收货 H5](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)、[基线审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT 矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)。整理前全文保留在 [19:08 历史快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_1908.md)，更早在 [15:01 快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_1501.md)。

本轮临时 IAB 浏览器已关闭、viewport 已复原，精确 PID 40676 的合成 Vite 预览已停止。候选审查 24 文件索引为 `artifacts/loss-submission-h5-next/candidate-review-v1.json`，commitAllowed=false；文档后续更新需在最终提交前刷新摘要。

本轮额外只读展开迁移源码的会话 1397 因递归加载成本高已主动结束（exit 130），未执行迁移或改动源码；它不是 PG16 门禁。门禁 66660 / PID 45265 保持运行，1794 文件中间回读零漂移。

### 最新失败核对与接续

- `artifacts/loss-sender-read-next/queries-v1.log` 为独立发件读取候选的测试，2 failed / 6 passed。两个失败均源于把两次查询的 `queried_at` 一并要求相等。只修正候选测试：全部业务字段仍严格比较，查询时间另验带时区及先后顺序。`queries-v2.log` 已终态 8 passed（181.27 秒）。这是 ignored 候选验证，尚未整合到正式后端或 HTTP 路由。
- 发件读取候选现有 11 个模块：本人目录、原单详情、出库/发运选项与历史、严格报损 schema、正式报损 router 的 GET 接入和错误脱敏。均在 ignored `artifacts/loss-sender-read-next/`，目标路径/base hash/candidate hash 见其中 `continuation.json`；尚未整合正式源码。
- 候选 `http-v2.log` 已终态 9 passed / exit 0，包括数量/SN、两张实际派生退回单分页和旧快照拒绝、权限分离、框架错误隐私。`detail-privacy-v1.log` 已终态 14 passed / exit 0，包括原单详情、读权限拒绝、损坏/变化证据拒绝和新旧路由的隐私错误响应。会话 45095、1990 均已结束，不再轮询。
- 当前完整读取及普通工单退料回归：**会话 64381、PID 83403**，日志 `loss-sender-read-next/read-regression-v2.log`，仍运行。旧会话 28187 因合成夹具漏配独立 read 权限主动中断，6 failed / 8 passed、exit 2，不计通过；原服务权限限制未放宽。HTTP v1 两个字段错误回显缺口已在候选 `formal_stock_returns.is_return_path` 补齐报损发件路径，正式源码尚未整合。
- 当前本人报损 H5 PG16 v2 已完整通过：数量 `run-cbiuel99`、SN `run-ejl5h_at` 均 `passed: true`、`sourceDrift: []`，临时库 stopped / checks passed / serverExitCode 0。会话 66660 已退出 0，不再轮询；最终 1794 文件与两次启动清单一致。冻结解除；最终摘要在 `native-terminal-v2.json`。远端完整 CI 仍未通过，未提交新候选。
- 发件候选整合补丁 `loss-sender-read-next/integration-v1.patch` 包含 11 个后端模块与 3 个测试，基准/候选 hash 均复核一致，git apply --check 通过，尚未应用。先回收 64381 的回归终态，再整合，禁止覆盖发生变化的基准文件。
- 准确 5e CI 最新回读更新到 `loss-return-h5-next/new-sha-pg16-v7.json`：仍 in_progress，20 success / 3 failure / 1 inventory in_progress。三个 runner shutdown 失败保持未解决，不重启整个活跃任务。
