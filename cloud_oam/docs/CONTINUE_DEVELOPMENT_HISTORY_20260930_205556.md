# RSC 个人仓开发交接

更新时间：2026-09-30 20:55:56（北京时间）。下列为当前状态；旧过程记录保存在 [CONTINUE_DEVELOPMENT_HISTORY_20260930_203521.md](CONTINUE_DEVELOPMENT_HISTORY_20260930_203521.md)。

**目标仍是正式上线版本，尚未达成。本轮没有提交或部署。本人报损 H5 与发件读取已在工作树；发件写入、封存和恢复仍为独立候选，尚未开放正式 POST 路由。**

## 工作树与不可破坏的边界

- 固定工作树 `/Users/replace-with-local-user/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`；默认 checkout 不是目标。禁止 reset、revert 或丢弃未提交改动。
- HEAD `5e847d282cb1e6abdd1bc3a5af31e4c2c10301c4`，已推送 GitHub，不等于生产部署。先读完整 [正式基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)，再查 diff 和未跟踪文件。
- 公开首页“交流备件知识大全”无登录，星星按钮跳 `https://rscwz.cn/xx`；公开小程序仅知识查询；飞书知识源延后。
- 审批、冻结、出库、发运、签收、验收、独立入账、OAM 收货、通知和对账分别取证。未知写结果保存完整原请求，只回查，禁止自动重发或更换 key。
- 当前迁移 head 为 `20261205_0156`。0157 仅在 ignored artifacts 中，未应用正式数据库或迁移链。

## 当前活动门禁：先回读，不重复启动

**会话 68460 / 父 PID 16674**：新增发件读取和既有发运流程的本地 PG16 v2。日志 `artifacts/loss-sender-read-next/native-read-pg16-v2.log`，数量首库 `artifacts/local-stock-loss-return-shipment-pg16/checks/run-olw99neq`。

- 启动命令：从 `cloud_oam/` 执行 `.venv/bin/python scripts/run_local_pg16_stock_loss_return_shipment_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin`；默认数量和 SN 两个新建隔离库。
- **1803 个非 Markdown 源文件处于冻结，不能改正式代码、脚本、测试或工作流。** 可更新 Markdown 和 ignored 候选；以各库 `source-manifest.json` 为准。只有实际终态并正常停库后解除冻结。
- 数量模式 `run-olw99neq` 已完整 passed/stopped/serverExitCode=0，三阶段读取、真实权限撤销、并发、防超发、迟到冻结、迁移/ACL 均通过，1803 文件零漂移。序列号库 `run-mdjnvsng` 已启动，仍无终态；两模式总门禁不能计通过。
- API 查询使用 SELECT/SHOW 语句监测、禁止 DML，前后核对库存、审计、通知、收货、入库和封存事实。保留期初证明必要的行锁；测试不替换正式 require_permission 或服务授权。
- v1 会话 15528 已失败退出 1，旧库 `run-5dt0qpip` 已 stopped/serverExitCode=0。原因是门禁误设 READ ONLY，拒绝现有期初证据核验的 SELECT FOR UPDATE。v2 只修正门禁，未放宽生产一致性校验。

## 本轮新增候选与活动句柄

- **会话 24295 / PID 30599**：`run_native_seal_migration_candidate.py`，日志 `native-seal-migration-v1.log`，库 `native-seal-migration/run-enw3dcs6`。实际 PG16 0156→0157→0156→0157、函数/触发器/ACL、运行角色迁移与私有执行拒绝均通过；会话 exit 0，库 passed/stopped/serverExitCode=0，1803 文件与候选摘要均无漂移。该会话已结束，不再轮询；业务封存验收仍独立待证。只在自己新建的隔离库调用草稿迁移，不注册正式源码迁移链。
- **会话 10661 / PID 32187**：`run_native_sender_seal_candidate.py --tracking quantity`，日志 `native-sender-seals-quantity-v1.log`，库 `native-sender-seals/run-bo133gmh`。使用真实期初/报损审批/派生退回/出库事实，执行 `pg16_sender_seal_candidate.py` 的实际封存、库存不变、完整请求恢复、并发同请求、数据库 COMMIT 权限到期、缺失审计、迟到发运冲突、历史禁止降级。正在运行，未计通过。
- **会话 5911 / PID 39521**：`run_native_sender_seal_cached.py --tracking serial`，日志 `native-sender-seals-serial-v1.log`，同一业务门禁的序列号模式已启动。外层使用仓库已有的源码 hash 绑定字节码缓存，仍每次执行全新迁移 globals/SQL，不缓存迁移结果；相当于补上常规 Alembic env 已有的编译缓存范围。
- 上述脚本、0157 草稿及其恢复/封存服务也处于各自摘要冻结，不能在运行期间改写；正式源码冻结须同时照顾这两个读取者。所有新文件均在 `artifacts/loss-sender-read-next/`，没有生产数据或真实业务操作。
- GitHub 诊断候选 `static_gate_diagnostics.py` 已通过真实子进程 **2 项测试**（`static-diagnostics-candidate-v3.log`，会话 18652 exit 0）：失败仍失败；强制中断保留最后测试名；进度与资源计数同步 JSONL 和原始 stdout；不输出异常内容/环境/凭据，不覆盖旧日志。尚未接入正式 CI，不能声称已解决 runner shutdown。

## 发件 H5 独立读取契约

- `formalLossSenderReads.ts` 已实现目录、原单详情、待出库及待发运选项的严格解析；不混用报损行与普通工单回收行，不推断收货/入库。尚未接入正式前端页面。
- `test_export_sender_reads.py` 通过真实合成 HTTP 导出 quantity/serial × 待出库/已出库未发运 **4 组样本**（4 passed，136.79 秒，exit 0），保存在 `h5-read-contracts/`。
- `formalLossSenderReads.test.mjs` **14 passed**，`h5-sender-read-contract-v2.log`；严格 TypeScript noEmit 检查 exit 0（`h5-sender-read-typecheck-v1.log`）。覆盖错误身份/原单、混杂来源、QR 泄漏、替换 SN、数量不守恒、超出 JS 安全整数的精确数量、向下取整、分页快照/重复/游标异常。
- 后续仍须发件写入与未知请求持久化控制器、确认页面、真实浏览器验收；当前读取契约通过不等于发件 H5 完成。

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
- 0157 草稿 `20261206_0157_loss_return_sender_seals.py` 已保留普通工单原 actor 约束，只为 loss 来源分别证明原总部派生及当前工程师权限；SQL/SQLite 与原生 PG16 空库升降级/权限/目录核对已通过；实际业务封存与双向排斥/并发仍在运行，尚未完成。
- `continuation.json` 保存候选路径、摘要与各轮证据；v1 12 项恢复测试对应源码另存 `recovery-v1-verified/`，不能用旧摘要证明 v2。

## 远端准确 SHA 的 CI

- HEAD 5e 的 Client run 36690470446 已 success，证据 `loss-return-h5-next/new-sha-ci-v2.json`。
- PG16 run 36690470476 的 attempt 1 已终态：21 success（含 inventory）、3 个 static_safety 失败、汇总失败。证据 `new-sha-pg16-v8.json`。
- 仅失败作业重跑已接受，并回读 attempt 2 in_progress。再次在线核验：static_safety (1) job 109880081232 在约 65%、static_safety (0) job 109880081588 在约 60% 收到 runner shutdown 并 canceled；static_safety (2) job 109880081468 仍在运行。关闭原因未确定，无已观察到的测试断言失败记录。超时配置为 360 分钟，不能据此把 10～22 分钟中断解释为超时。
- 日志 `ci-job-109880081232.log` 与 annotations 已保存。gh 默认拒绝 ANSI 输出，需 `--allow-escape-sequences` 捕获后清除控制字符；此前取日志失败不是 GitHub 连接失败。
- 不盲目重跑第三轮，不把未结束或 runner 中断算通过。远端 5e CI 不覆盖当前未提交代码。禁止复用固定旧 SHA 发布脚本或强推。

## 接续顺序

1. 回读 68460/PID 16674（serial）以及 10661/PID 32187（封存 quantity）、5911/PID 39521（封存 serial） 的实际进程、日志、checks 与正常停库终态；复核源码及候选摘要。不能只因日志暂时无输出或观察超时重复启动。活跃时只推进未被运行引用的 ignored 候选或文档。
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
