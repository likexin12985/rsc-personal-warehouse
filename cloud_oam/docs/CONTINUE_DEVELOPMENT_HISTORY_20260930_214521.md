# RSC 个人仓开发交接

更新时间：2026-09-30T21:28:20.086686+08:00。完整历史见下方证据与历史交接文件。

**目标仍是正式上线版本，尚未达成。本轮没有提交或部署。本人报损 H5 与发件读取已在工作树；发件写入、封存和恢复仍为独立候选，尚未开放正式 POST 路由。**

## 工作树与不可破坏的边界

- 固定工作树 `/Users/replace-with-local-user/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`；默认 checkout 不是目标。禁止 reset、revert 或丢弃未提交改动。
- HEAD `5e847d282cb1e6abdd1bc3a5af31e4c2c10301c4`，已推送 GitHub，不等于生产部署。先读完整 [正式基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)，再查 diff 和未跟踪文件。
- 公开首页“交流备件知识大全”无登录，星星按钮跳 `https://rscwz.cn/xx`；公开小程序仅知识查询；飞书知识源延后。
- 审批、冻结、出库、发运、签收、验收、独立入账、OAM 收货、通知和对账分别取证。未知写结果保存完整原请求，只回查，禁止自动重发或更换 key。
- 当前迁移 head 为 `20261205_0156`。0157 仅在 ignored artifacts 中，未应用正式数据库或迁移链。

## 当前活动门禁：先回读，不重复启动

正式非 Markdown 源码再次冻结，读取者是 0157 业务封存 v3 两个会话。不要改正式代码/脚本/测试/工作流，也不要改候选迁移、服务、runner 和业务门禁；具体文件以新库 source-manifest.json / candidate-manifest.json 为准。可推进尚未被引用的 ignored H5 页面候选及 Markdown。

- **数量：会话 26956 / PID 62481**，`native-sender-seals-quantity-v3.log`。
- **SN：会话 80565 / PID 62490**，`native-sender-seals-serial-v3.log`。
- 从 cloud_oam 执行 `.venv/bin/python artifacts/loss-sender-read-next/run_native_sender_seal_cached.py --postgres-bin artifacts/pg16-native-20260920/install/bin --tracking quantity`（另一会话 serial）。每次新建自己的 PG16，不接既有数据库。只缓存源码 hash 绑定的编译字节码，迁移 globals/SQL 每次实际执行。
- 两个 v1（10661、5911）均 exit 1，库 run-bo133gmh / run-r3s22uvb 均 stopped/failed/serverExitCode=0；各自已通过两种操作的封存/回查/同请求并发/COMMIT 权限到期，但尚未整体验收。失败是 COMMIT 先由既有 0110 namespace 约束拒绝，测试仅期待 0101 消息；原 v1 源码保存在 `native-seals-v1-source/`。
- v2 两会话 71113/33424 均 exit 1，run-0lcm60vl / run-fcxszo3n 均 stopped/failed/0。已通过 executed outbound 的 COMMIT 拒绝及指定 0101 proof，后续 late shipment 由 0154 继承的 `EXISTS stock_operation_command_seals` 条件先拒绝为 0104 parcel identity/original/request mismatch。旧源码保存在 native-seals-v2-source/，不能算通过。
- v3 增加同内容、未封存 request/key 的正常对照，实际执行后 SET CONSTRAINTS ALL IMMEDIATE 必须通过，再回滚保持原数量；负例须证明原 seal 存在、COMMIT 23514、指定 0104 seal trigger 的准确拒绝及事实完全回滚。另有真实执行/封存并发、权限撤销和历史保留禁止降级。未关闭任何数据库约束，仍未计通过。

## 已完成的读取 PG16 与迁移证据

- 发件读取 v2 **会话 68460 exit 0**。数量 run-olw99neq、SN run-mdjnvsng 均 passed/stopped/serverExitCode=0，1803 个当时源码零漂移。
- 两模式均验证待出库、出库未发运、历史已发运三个实际 HTTP 阶段；库存/审计/通知/收货/入库事实不变，真实权限撤销检查通过。保留既有期初证据 SELECT FOR UPDATE，用 SELECT/SHOW 语句监测及业务快照保证只读。
- 0157 原生空库迁移会话 24295 exit 0，run-enw3dcs6 passed/stopped/0；0156→0157→0156→0157、目录精确回环、权限及私有函数拒绝已过。它不等于业务封存验证，更不等于生产验收。
- 上述所有结束会话不要重复轮询；新静态诊断文件是这些旧快照之后新增，旧 1803 文件证据不覆盖它们。

## 发件 H5 候选：协议/恢复 75 项，新增页面 14 项通过，正式路由尚未接入

以下均位于 `artifacts/loss-sender-read-next/`：

- `formalLossSenderReads.ts`：目录、原单详情、出库/发运 options，14 项。
- `formalLossSenderCommands.ts`：完整请求/精确数量/微秒时间摘要、preview/result/lookup/sealed 证明，12 项。
- `lossSenderRecovery.ts`：一人一退回单跨出库/发运的 Web Locks、本机持久化写后回读、单次 POST、刷新恢复、显式封存、权限前后核验，32 项。未知结果只回查；只读授权可恢复；导航发生在异步最终核验期间也不清理原请求。
- `lossSenderAdapter.ts`：实际路由/独立权限/无缓存/完整请求与 header 绑定，17 项。页面须注入已有 `apiNoReplay`，禁止普通自动重放 transport。封存只带 X-Request-ID；完整原 key 保留在 body，不声称 key/plan 被封存。
- 合计 **75 passed，exit 0**：`h5-sender-combined-v1.log`；四模块 strict TypeScript noEmit exit 0：`h5-sender-combined-typecheck-v1.log`。
- 实际合成 HTTP 写协议导出 **4 passed / 2 warnings / exit 0**，`h5-write-contract-export-v1.log`，quantity/serial × outbound_return/ship_return 四组 JSON。其候选 ORM source-check 替换仅供协议夹具，不代表 PG16 验收。
- 初轮恢复测试因夹具读取了不存在的 detail.operation_id（真实 ID 在 detail.origin.operation_id）失败，已修正测试；保留 v1 日志。后续完整测试已过。
- 新增 `FormalLossSendingPage.tsx` 与 `LossSendingForm.tsx`：本人目录、分页快照、独立出库/交运、手工实物 SKU/SN/QR、精确数量、实际时间、包裹信息、独立确认、原请求恢复/封存。每单未知请求同时阻断新出库和交运；其他单不全局串行。
- 页面 **14 passed / exit 0**：`h5-sender-page-tests-v4.log`。严格 TS（含页面、表单、测试、合成预览）exit 0：`h5-sender-ui-typecheck-v2.log`；合成 Vite 构建 exit 0：`h5-sender-ui-build-v1.log`。测试初轮因 JSON 相对 URL 解析错误失败，v2 发现 datetime-local 可能产生 `.000` 毫秒格式；修正输入校验并明确类型分支后通过。时间默认空，用户可主动“使用当前时间”，仍需最终确认。
- 真实 IAB 浏览器已验：数量件出库成功；SN 件手工证明不代填后成功；390px 手机交运丢回执，刷新并切回同一合成人员后原请求仍在，只读回查 not_observed 不清理、封存及新发件按钮禁用。DOM clientWidth=scrollWidth=390。截图 `h5-sender-mobile-recovery.png` / `h5-sender-mobile-sn.png`。
- 浏览器仅调用内存合成 transport，无业务系统 HTTP；刷新后合成已执行事实不保留，故刷新测试只证明原请求保留和 unknown 安全边界。不能把它当真实后端端到端证明。
- `sender-preview.tsx/html/css`、`vite.sender.config.mts`、`vitest.sender.config.mts` 是独立合成测试入口；已恢复 viewport、关闭临时页、停止 Vite PID 57254。测试脚本位于 ignored 目录，node_modules 只链接现有 frontend 依赖。
- 尚须合入正式前端、完整 API 联调、正式 CSS、真实后端浏览器端到端与 SN 交运浏览器补验；不能算已对用户开放。

## 静态门禁中断诊断

- `scripts/static_gate_diagnostics.py` 和 `backend/tests/test_static_gate_diagnostics.py` 已正式接入未提交工作树；`scripts/run_static_shard.py` 默认加载插件，每次运行创建唯一的 artifacts/static-safety/shard-*/progress.jsonl。
- 每个测试开始时把测试 ID、资源计数写入 JSONL 和原始 stdout，以便 runner 关闭、无法上传附件时仍有线索；不记录异常 payload、环境或凭据，不改变测试选择/结果。
- 正式聚焦测试 **5 passed / 25 deselected / exit 0**，`static-diagnostics-formal-v2.log`。覆盖真实失败仍 exit 1、强制中断保留最后测试、两次 runner 运行独立产物、所有静态文件完整分片。v1 因漏设 PYTHONPATH=backend 在 conftest 收集失败；修正启动环境后通过。
- 尚未提交或推送，远端 runner shutdown 原因仍未查明；不能称 CI 已修复。

## 已结束的本地证据

路径以 `cloud_oam/artifacts/` 为基准。

| 范围 | 终态 | 证据 |
| --- | --- | --- |
| 本人报损 H5 前端全量 | 1866 passed，107 文件 | loss-submission-h5-next/frontend-full-v4.log |
| 小程序 | 1079 passed | loss-submission-h5-next/mini-full-v1.log |
| H5 后端协议/库存回归 | 62 passed | loss-submission-h5-next/backend-inventory-parity-v1.log |
| TypeScript、公开/私有构建 | exit 0，私有 bundle 仍有体积提示 | loss-submission-h5-next/build-*-v3.log |
| H5 数量/SN PG16 | passed，1794 文件零漂移，两库正常停止 | loss-submission-h5-next/native-terminal-v2.json |
| 新发件读取和普通退料回归 | 51 passed | loss-sender-read-next/read-regression-v2.log |
| 正式目录复验 | 12 passed | loss-sender-read-next/formal-directory-v2.log |
| 读取 HTTP / 详情隐私 | 9 passed / 14 passed | loss-sender-read-next/http-v2.log、detail-privacy-v1.log |
| 发件恢复/封存服务候选 v2 | 20 passed，566.66 秒，exit 0 | loss-sender-read-next/recovery-candidate-v2.log |
| 完整发件 HTTP 候选 | 4 passed，136.79 秒，exit 0 | loss-sender-read-next/sender-write-http-candidate-v1.log |
| 0157 草稿 SQL / SQLite | 3 passed，exit 0；不是 PG16 证明 | loss-sender-read-next/seal-migration-candidate-v1.log |

会话 66660、64381、2373、36144、85267、93740、39428 均已结束，不再轮询。正式 HTTP/目录首轮 23 passed / 2 failed 的时间夹具问题已修复：责任结束时间必须晚于派生时间，不能粗暴设 now−1 秒。没有更改生产历史证明。

H5 PG16 的数量 `run-cbiuel99`、SN `run-ejl5h_at` 不覆盖新增发件读取。旧安全扫描 PASS/1984 文件也不覆盖后续新文件，提交前须刷新。H5 浏览器数量/SN、未知结果刷新保留和只读回查已做；浏览器已关闭，预览 PID 40676 已停止。

## 代码与候选的位置

- 正式未提交 H5：`frontend/src/FormalLossSubmissionPage.tsx`、`formalLossSubmission.ts`、`lossSubmissionRecovery.ts`、`lossSubmissionAdapter.ts`、表单、App 路由和对应测试；后端 `inventory_schemas.py` 时间序列化与协议测试。已通过 H5 门禁的 22 个源码快照另存 `loss-submission-h5-next/verified-source-snapshot/`。
- 正式未提交发件读取：本人目录、原单详情、出库/发运 options/history、严格 loss-only schema、GET 路由和错误脱敏，共 11 模块、3 个测试。`integration-v1.patch` 已应用，不能重复应用。
- 正式新增数据库门禁：`backend/tests/pg16_loss_return_sender_read_gate.py`，接在 `pg16_stock_loss_return_shipment_gate.py` 的三个真实阶段；目前由上述 v2 运行覆盖。
- 独立候选目录 `artifacts/loss-sender-read-next/`：`loss_return_sender_recovery.py`、`loss_return_sender_seals.py`、`loss_return_sender_recovery_schemas.py`、`formal_loss_return_sending_with_recovery.py`，以及服务/HTTP 测试和两个显式加载 runner。仅这些进程的合成 ORM 使用拟定 0157 的来源约束，不能代替真正迁移。
- 恢复同时核对完整命令、原请求 ID、幂等键、内容、计划摘要和真实派生来源；未观察到结果仍 retry_allowed=false。封存范围明确为 actor_request_id，当前表不保存 key/plan，不能声称它们已被封存。封存仅增加一条封存与一条审计，迟到执行被拒绝；只读权限仍可恢复。
- 0157 草稿 `20261206_0157_loss_return_sender_seals.py` 已保留普通工单原 actor 约束，只为 loss 来源分别证明原总部派生及当前工程师权限；SQL/SQLite 与原生 PG16 空库升降级/权限/目录核对已通过；实际业务封存 v1 因拒绝消息预期过窄而失败，v3 复验中，尚未完成。
- `continuation.json` 保存候选路径、摘要与各轮证据；v1 12 项恢复测试对应源码另存 `recovery-v1-verified/`，不能用旧摘要证明 v2。

## 远端准确 SHA 的 CI

- HEAD 5e 的 Client run 36690470446 已 success，证据 `loss-return-h5-next/new-sha-ci-v2.json`。
- PG16 run 36690470476 的 attempt 1 已终态：21 success（含 inventory）、3 个 static_safety 失败、汇总失败。证据 `new-sha-pg16-v8.json`。
- attempt 2 已 completed/failure；三个 static_safety 均 runner shutdown（(1) 约 65%、(0) 约 60%、(2) 约 76%）。全部日志已回读保存；没有可见测试断言失败记录，关闭根因未知。超时配置 360 分钟，不能把当前中断猜成超时。终态证据 `loss-return-h5-next/new-sha-pg16-attempt2-terminal.json`。
- 日志 `ci-job-109880081232.log` 与 annotations 已保存。gh 默认拒绝 ANSI 输出，需 `--allow-escape-sequences` 捕获后清除控制字符；此前取日志失败不是 GitHub 连接失败。
- 不盲目重跑第三轮，不把未结束或 runner 中断算通过。远端 5e CI 不覆盖当前未提交代码。禁止复用固定旧 SHA 发布脚本或强推。

## 接续顺序

1. 回读 26956/PID 62481（封存 quantity v3）、80565/PID 62490（封存 serial v3）的实际进程、日志、checks 与正常停库终态；复核源码及候选摘要。不能只因日志暂时无输出或观察超时重复启动。活跃时只推进未被运行引用的 ignored 候选或文档。
2. 完成并审查 0157：正式 ORM source CHECK、database_security 函数摘要、oam_sync_scope_security 的新 readiness head、release gate HEAD 与历史源摘要链须同步；现有测试对 0155 固定终态摘要的假设也须按完整后继链验证。
3. 为封存补实际 PG16：空库 roundtrip、API 权限/私有函数、原总部 actor 与工程师 actor 区分、封存和执行双向排斥、并发一方生效、授权 COMMIT 到期、审计/来源损坏拒绝及历史禁止降级。没有这些证据，不能只挂 POST。
4. 冻结解除后整合受审查的完整发件 API/恢复候选并跑正式导入路径测试、新完整 PG16、迁移、权限、安全扫描和准确 SHA CI；补发件 H5 的完整原请求持久化和真实页面验收。
5. 证据齐全后才精确暂存、提交。保留所有已有未提交改动；不得把候选日志或旧版本 CI 当新版本验收。
6. 按正式基线继续反向冲销、报废、人员调拨、离职交接，以及真实渠道、迁移与生产验收缺口。

## 上线仍需的独立证据

真实短信 PNVS/微信与唯一身份绑定、私有 OSS/KMS；授权 OAM 只读批次、历史数量/关键字段/附件校验；真实期初实盘与多角色设备 UAT；连续至少 3 天对账；500 用户压测；RPO≤5 分钟、RTO≤2 小时恢复；应用回滚、同步批次回退和真实业务冲销。备案截图、推送和本地测试均不等于正式上线。

目标服务器是用户确认的旧备份服务器 `118.31.37.87`；Ubuntu 24.04、star-oam 占用 80/443 是历史观察，部署前重查。本轮没有连接服务器、真实业务系统或发送短信/通知。

从 `cloud_oam/` 使用 `.venv/bin/python`、`PYTHONPATH=backend`；Node 位于 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`，前端全量用 `--maxWorkers=2`。PG16 为 `artifacts/pg16-native-20260920/install/bin`。**artifacts 被 Git 忽略，不随 clone/push 转移**，移交应保留受审查的源码/日志摘要，不复制生产数据库、令牌或会话。

相关：[H5 验收](LOSS_SUBMISSION_H5_ACCEPTANCE_20260930.md)、[发件审计](LOSS_SENDER_HTTP_RECOVERY_AUDIT_20260930.md)、[基线缺口](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT 与上线证据](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)。
