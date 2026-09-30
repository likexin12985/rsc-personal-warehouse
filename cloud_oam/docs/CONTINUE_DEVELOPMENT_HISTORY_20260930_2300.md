# RSC 个人仓开发交接

更新时间：2026-09-30T22:48:08.682023+08:00。历史说明见 [CONTINUE_DEVELOPMENT_HISTORY_20260930_214521.md](CONTINUE_DEVELOPMENT_HISTORY_20260930_214521.md)。

**目标仍为完整正式上线版本，尚未完成。本轮已将报损退回发件的 0157 迁移、正式 API、原请求恢复/封存及 H5 页面接入工作树；未提交、未部署，尚无生产验收。**

验收范围与尚缺证据见 [0157 验收清单](LOSS_SENDER_0157_ACCEPTANCE_20260930.md)。仓库内路径使用本地用户占位符；实际工作树仍以用户指定路径为准。

## 工作树与要求

- 固定工作树 `/Users/replace-with-local-user/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`；HEAD 仍为 `5e847d282cb1e6abdd1bc3a5af31e4c2c10301c4`。
- 禁止 reset、revert、丢弃未提交改动。默认 checkout 不是目标；所有命令指定 workdir。
- 先完整读取 [正式基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)。本轮已复读。
- 公开首页“交流备件知识大全”不含登录；星星入口到 `https://rscwz.cn/xx`；小程序仅知识查询。飞书知识源按用户指示暂缓。
- 审批、出库、交运、签收、验收、入库、OAM 收货、通知、对账分别验证。未知结果保存完整原请求，只回查，不自动重发或换 key。
- 当前源码迁移 head 已接入 `20261206_0157`。尚未迁移任何生产数据库。新正式门禁进行中，不能引用旧 0156 证据宣称新正式 head 全通过。

## 验收进程终态与新运行（2026-09-30 接续更新）

- 报损收货/独立入库/恢复数量与 SN 全部通过，会话 97209 exit 0。数量 `run-vovimg7o`、SN `run-o7vqxy7h` 均 passed/stopped/serverExitCode=0、sourceDrift=[]。不要重复轮询已结束会话。
- 真实浏览器数量件会话 24963 exit 0；`local-loss-sender-browser-pg16/checks/run-vgus4wx6` passed/stopped/serverExitCode=0、sourceDrift=[]。浏览器 SN 验收也已通过，见下。
- 上述两个旧读者已结束。当前新读者如下，正式非 Markdown 源码再次冻结：
  - SN 真实浏览器 v3 已结束：会话 47623 exit 0，库 `run-gopaw_9_` passed/stopped/serverExitCode=0、sourceDrift=[]。身份隔离修复后未再遇到 401，2 次物理 POST、刷新后只读恢复通过；Vite 90790 已主动发送 SIGTERM。
  - 普通退料首入库账户完整兼容已通过：会话 91123 exit 0（勿再轮询），`formal-ordinary-return-account-pg16-v1.log`，库 `local-return-account-pg16/checks/run-m2_fd41o` passed/stopped/serverExitCode=0、源码无漂移；终态 `ordinary-return-account-terminal-v1.json`，含数量/SN、批次/多行、15 畸形图、权限/并发与迁移往返/保留历史。
  - 普通出库/交运/收货/入库完整既有 CI 用例组合：会话 91787 / PID 63632，`formal-ordinary-fulfillment-pg16-v1.log`；仅本地连接坐标重绑定，包含原 mini SDK、SQL 拒绝、封存与并发用例。源码在受忽略 artifacts，runner 绑定自身和正式输入摘要。
- 7 组既有终态清单与当前文件逐项比对：后续差异仅已审查的 CI/门禁补丁，生产应用和迁移未改变；证据 `pre-ci-evidence-source-delta.json`。
- 正式 CI 拓扑接入后 31 passed / 1 deselected / 1 warning / exit 0；扫描 v3 2030 文件 PASS。两者不替代业务/远端验收。

PG16 HTTP 数量库 `artifacts/local-loss-sender-http-pg16/checks/run-8ot549t6` 已 passed/stopped/serverExitCode=0，源码零漂移、0157 正式迁移注册、完整 HTTP 和保留历史拒绝降级通过；SN 库 `run-q0y9xh2n` 也已通过并正常停库；会话 92124 已确认 exit 0，数量/SN 全部终态证据为 `formal-http-terminal-v1.json`，不要再轮询。完整封存数量 v1 库 `run-ftom1ri2` 因测试驱动未定义 `execute` 失败并正常停库；v2 修复后 `run-jz1yjjdn` 已完整通过，会话 61797 exit 0，源码零漂移。证据 `formal-seal-quantity-terminal-v2.json`；勿再轮询两次已结束的数量会话。实际终态以 checks.json / failure.json / cluster-state.json 和进程退出码为准。运行命令：`.venv/bin/python scripts/run_local_pg16_loss_sender_http_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin`。

普通退料请求恢复会话 25541 已结束：**24 个数量/SN 提交/取消 SQL 双向互斥证明通过，exit 0**；`run-bqkgzbd6` 正常停库，正式 head 0157、安全目录前后验证及源码零漂移。此范围不替代普通出库/交运/收货入库的完整数据库回归。

正式工作树聚焦会话 78020 已结束：**22 passed / 1 warning / exit 0，559.74 秒**，`formal-integration-backend-v1.log`；不要再轮询该已结束会话。

独立后端副本 `artifacts/loss-sender-read-next/integration-backend-check-v1/` 已 **37 passed / 1 warning / exit 0，1269.33 秒**（会话 78813 已结束）；其中应用与迁移 555 个源码文件和正式工作树逐项相同，证据 `integration-backend-formal-source-match.json`。与正式导入检查分开记录，不重复计为独立用例。

数量与 SN 报损收货/独立入库/恢复均已通过；包括权限到期、并发唯一验收、独立入库、原请求恢复、封存防迟到写、迁移往返和保留历史拒绝降级。

## 真实浏览器联调：数量件和 SN 均通过

- v1 首次读取 500 的原因是本地测试代理被 Uvicorn 按 ASGI2 调用；已修为显式 ASGI3。v1 日志及源文件保留，零物理提交，数据库正常停止。
- v2 数量件完整通过，日志 `artifacts/loss-sender-read-next/browser-sender-pg16-quantity-v2.log`，原始请求记录 `browser-quantity-commands.json`，截图 `browser-quantity-recovery-v2.png`。正式 H5、adapter、apiNoReplay、真实 API 与 PostgreSQL 16 API 角色均参与验证，仅登录身份为合成注入；不代表生产认证验收。
- 实际出库与交运各 1 次物理 POST。代理在交运真实 COMMIT 后撤销实际写权限并返回模拟 503；刷新页面后原请求仍保留，只读回查成功，待核验请求消除，新出库/交运按钮禁用。数据库核对仅 1 出库、1 包裹，无推断收货或入库。
- v2 出库成功后的身份复查曾出现“登录已失效”：共享 fixture 的 `sender_read_gate` 临时替换同一 app 的认证依赖，与浏览器后置复查重叠。按原请求只读回查恢复成功，未重发。SN 测试前应隔离该测试驱动的生命周期，不能改生产认证或放宽业务断言。
- 完成标记已在真实 UI 恢复后写入，runner 后续断言全部通过，exit 0，API 与临时数据库正常停止。该标记不得复用于新 operation。
- 本次数量联调的 Vite（会话 3844 / PID 34373）已发送 SIGTERM 停止；后续 SN 联调需按既有配置重启。
- SN v3 已通过，证据 `browser-serial-terminal.json` / `browser-serial-commands.json` / `browser-serial-recovery-v3.png`。390px 视口下确认页和恢复页 documentWidth=390、无横向溢出；已复原视口。只有合成身份与合成实物证明，不代替真实设备和认证 UAT。

## 新增验收补丁（已应用，勿重复）

- `artifacts/loss-sender-read-next/sender-ci-next-v2.patch` 已应用，原/结果摘要全部吻合，记录 `sender-ci-next-v2/applied.json`。6 文件：完整 `pg16_loss_sender_seal_gate.py`、独立原生 runner、HTTP helper 的非法 tracking 前置拒绝、GitHub sender_http/sender_seals 两个新 flow、CI 分派及拓扑测试。原任务、临时库确认和汇总规则不删除。
- `sender-ci-candidate-v2.log`：14 passed / 17 deselected / 1 warning / exit 0，仅证明 CI 任务边界；不代表 PostgreSQL 业务验收。
- 完整封存候选补数量/SN 两种操作的两个排斥方向、真实执行/封存并发及指定数据库约束；quantity 和 serial 均已全部通过，两个会话均 exit 0，临时库正常停止、源码零漂移。共同终态 `formal-seals-terminal-v2.json`；测试驱动已正式接入。
- 数量件驱动 `run_formal_sender_seal_candidate_v2.py` 使用当前正式 0157 应用/迁移，曾以独立路径加载候选模块；现模块已正式接入且摘要相同。数量/SN 封存与数量/SN 收货入库旧会话都已通过并停止，不再保留运行中标记。
- `git apply --check` 必须从仓库根执行（含 .github 路径）；已通过。数量浏览器和数量/SN 收货入库门禁现已结束；补丁已接入并复验；新运行结束前禁止改动其绑定源码。准确 SHA CI 尚未补齐。

仓库安全扫描 v1 因三份交接文档包含个人主目录路径而失败；原副本保留在受忽略 artifacts 中，仓库文档已改占位符。v2 **PASS / 2028 个候选文件 / exit 0**。后续最终接入补丁后仍须做最终扫描。

## 本轮已完成的证据

1. **0157 草稿业务封存原生 PG16 v3：数量和 SN 均 PASS / exit 0 / 正常停库。**
   - 数量旧会话 26956，库 `native-sender-seals/run-e91wu_pz`；SN 旧会话 80565，库 `native-sender-seals/run-dmhaybqq`。两进程已结束，勿再轮询。
   - 各库 checks.passed=true、sourceDrift=[]、cluster stopped/passed/serverExitCode=0；整合前再次核对 1805 个当时源码无漂移。
   - 两种操作实际封存/精确回查、同原请求并发、畸形审计回滚、真实 COMMIT 权限到期、已执行出库不能封存、封存后迟到发运不能执行；正常未封存包裹通过全部约束；指定 seal 触发器独立拒绝；实际执行与封存并发只有一方生效；读写权限区分及保留历史禁止降级。
   - 这是草稿通过实际 Operations 的证明，formalMigrationRegistered=false，不能覆盖之后正式整合源码；已保留 v1/v2 失败日志，没有放松生产约束。
2. **正式接入补丁 52 文件**：`formal-integration-v3.patch`；应用前所有原文件摘要与候选摘要逐一匹配，git apply --check 通过，应用后逐文件摘要匹配。清单及结果在 `formal-integration-v3/applied.json`。不要重复应用。
3. **前端全量 1962 passed / 113 files / exit 0**：`integration-frontend-full-v2.log`，会话 66405 已结束。运行副本的 277 个 src 文件与正式源码全部逐项相同，证据 `integration-frontend-formal-source-match.json`。
   - 新发件聚焦 96 passed / 6 files：`integration-frontend-tests-v1.log`。
   - 第一次全量 1961 passed / 1 failed：既有收货用例等待确认框超过默认 1 秒，当时页面仍在核验；独立该文件 10 passed。测试现先等按钮可用，再有界等待 5 秒，原业务断言全部保留。全量复跑通过，不把首次失败抹掉。
4. **正式 TypeScript、公开和私有构建均完成**：`formal-types-v1.log` / `formal-public-build-v1.log` / `formal-private-build-v1.log`。私有 JS 仍约 1.25 MB（gzip 321 KB），构建有体积提示。
5. **公开入口边界检查 PASS**：`formal-public-entry-v1.log`，公开包无认证客户端，/xx 资源和 worker scope、公开小程序目录匹配。
6. **完整公开目录发布检查仍失败**：`formal-entry-gate-v1.log`，明确 `PUBLIC_CATALOG_NOT_READY`，目录 pending / 0 条。用户暂缓飞书源，不能造数据或降低完整发布门槛。
   - 既有私有试用产物检查 `formal-pilot-artifact-gate-v1.log` PASS，仅证明私有构建完整；不能替代正式上线或真实认证。

## 本轮正式接入内容

- `backend/alembic/versions/20261206_0157_loss_return_sender_seals.py`，ORM 来源互斥 CHECK；封存函数摘要、0157 readiness manifest 与所有已查明当前 head 运行入口/门禁同步更新；0155 历史测试按实际后继源码链核对，旧迁移不改写。
- `loss_return_sender_recovery.py`、`loss_return_sender_seals.py`、恢复 schema 和正式 `formal_loss_return_sending.py`：出库/交运 preview、submit、完整原请求 lookup、显式 seal。封存范围为 actor_request_id，不声称封存了 key/plan；授权、原总部派生人和当前工程师分别验证。
- H5 `/loss-returns/sending`：本人目录、数量/SN 实物证明、预览和独立确认、Web Locks 持久化完整原请求、刷新恢复、显式封存。只读权限可回查，未知请求阻断同单新出库/交运；使用 apiNoReplay。
- 正式新增 `backend/tests/pg16_loss_sender_http_gate.py` 与 `scripts/run_local_pg16_loss_sender_http_checks.py`：API 角色真实 HTTP 提交、原请求新会话恢复、封存和迟到拒绝、真实权限撤销、库存中立和不推断收货/入库；**数量和 SN 全部通过，见 `formal-http-terminal-v1.json`；不代表生产验收**。
- 既有 PG16 shipment fixture 仅增加可选 departure/after_preview 回调，用于调用真实 HTTP；默认原路径保留。
- 报损提交 H5、发件 GET、静态 CI 中断诊断等此前未提交改动全部保留。详见上一版交接。

## 远端 CI 与其他未完成事项

- 准确 SHA 5e 的 Client run 36690470446 success；PG16 run 36690470476 attempt 2 completed/failure。21 个运行门禁成功，3 个静态分片被 runner shutdown 中断，根因未知；不是已证明的业务断言错误，也不能猜为网络问题。
- 诊断插件已接入且 5 项聚焦通过，但未推送；当前改动没有新的远端 CI 证据。不盲目重跑旧第三轮。只读重查同工作流/分支运行时间线，最新仍是 36690470476 attempt 2，没有更晚的可见运行；`ci-shutdown-run-timeline-v1.json`。不能据此认定具体关闭原因。
- 下一步先回读以上活跃验收；处理具体失败后补正式 head 权限/迁移与完整 PG16、HTTP/浏览器联调、普通工单退料回归、安全扫描及准确 SHA CI。证据齐全才提交。
- 草稿 PG16 覆盖出库已执行后拒绝封存、已封存后拒绝交运等场景；正式双操作完整双向数据库排斥/并发覆盖仍需逐项审查，不以汇总 PASS 替代缺项。
- 按正式基线继续反向冲销、报废、人员间调拨和离职交接。
- 上线仍缺真实短信 PNVS/微信唯一身份绑定、OSS/KMS、授权 OAM 历史/附件迁移、真实期初盘点、多角色设备 UAT、至少 3 天差异解释、500 用户压测、RPO≤5 分钟/RTO≤2 小时恢复，以及应用/同步/业务冲销演练与最终部署验收。

服务器历史为用户确认的旧备份服务器 118.31.37.87；本轮没有 SSH、生产迁移、真实业务 API、短信或通知操作。

从 cloud_oam 使用 `.venv/bin/python`、`PYTHONPATH=backend`。Node 为 `/Users/replace-with-local-user/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`。artifacts 被 Git 忽略，交接不要误以为随 push 保存，也不要复制数据库/凭据。
