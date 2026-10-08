## 2026-10-08 接续：低成本密钥托管隔离实测完成

**试点 MVP，不等同完整 V1，仍未上线。** 本节覆盖下方“只有离线模板、未启动原型”的历史状态。当前本地提交基线为 `8d0d142`，远端仍为 `14d0731`；本批源码在该基线后形成，提交状态以 Git/操作回执为准。详细证据及接续顺序见 [隔离原型与兼容准备交接](OPENBAO_ISOLATED_EVIDENCE_20261008.md)。

- 固定 OpenBao 2.7.1 并校验官方签名/摘要；Mac 真实密钥轮换、手动解封、新目录恢复，以及 Identity 签名/轮换/过期/撤销已通过。外部公钥撤销复核、真实保管/灾备仍待完成。
- 目标服务器上的一次性 Linux 容器组合验证通过：两用途各 v1/v2 共 4 条 DEK 恢复可读，Identity 与候选 adapter 真实解密通过；运行采用非 root、断网、只读 rootfs、512 MiB/0.5 CPU/零 swap 边界。实测峰值约 235.9 MiB，临时容器及上传目录已清理，原六个运行容器 ID/名称集合保持。
- 本批 115 项新增聚焦测试通过，失败首轮保留；没有重跑已终态且未修改的旧测试。新 adapter 未注册到 Settings/factory/readiness，旧 Aliyun 加密/信封/pins/SQL 保留，未迁移生产数据库。
- 下一有限批次是从实际 head 追加新 provider 不可变绑定及兼容迁移：跨提供方版本唯一、旧密文/引用可读、新信封、factory/readiness、ACL 与精确 PG16；再准备生产身份、公网 TLS/JWKS、RAM/STS 和私有 OSS 配置。当前没有创建收费资源、启用正式服务或发送短信。
- `14d0731` Client 已成功，PG16 `37772507579` 在 21:06 的快照仍为 64 成功 / 7 失败 / 3 static_safety 运行中；本地 `8d0d142` 的修复不冒充新 SHA 远端通过。旧 run 终态前不推送以免取消运行，候选核验通过后可先本地提交。

业务范围仍为申请/提交→审批→最小分配/占用→后台人工履约/发运→本人收货→个人仓入账。拣货及后置区块继续按角色/capability 隐藏；底层依赖、不可变流水、幂等、审计、权限和取消/关闭守卫保留。真实短信登录、角色/真机 UAT、HTTPS/API、备份恢复与回滚依旧是独立上线门禁。

## 2026-10-08 低成本试点接续（当前工作树）

本节覆盖下方历史状态。已提交基线为 `14d0731387f8a24e8b25ed9222f89fec248ea563`；本批候选在该基线之后形成，准确提交状态以操作回执为准，下方旧 SHA、CI 状态和未提交说明只代表各自记录时点。

**试点 MVP，不等同完整 V1，仍未上线。** 已按小规模使用、暂不购买昂贵 KMS 的方向完成[低成本部署方案](LOW_COST_PILOT_DEPLOYMENT_20261008.md)，区分基线安全目标与既有 Aliyun 实现选择。继续复用现轻量服务器；OpenBao 自托管和 OIDC 联邦仅为待验证候选，不能假称运行身份或新 provider 已落地。私有 OSS 按量保守例算约 5.611 元/30 天（平均 5 GB 存储、10 GB 公网下载、1,000 PUT、10,000 GET），仅是附件基础用量，不含备份/短信/服务器，账号实际价格未核验。

- 已新增 OSS 只读预检工具，默认帮助不读凭据，显式 inspect 才执行四类 GET；40 项合成 SDK transport 测试通过，默认 SDK 构造分支另增单项验证 1 passed。私有 ACL、归属、BPA、SSE-OSS 与未开版本控制独立检查；解析/权限/网络异常保持 unknown，releaseReady 始终 false。锁定 SDK 对带 XML namespace 的版本控制响应仍有兼容缺口，尚未读取真实 Bucket。
- OpenBao 两用途最小解密权限、Unix socket 配置片段和离线检查已准备，7 项模板反例测试通过；没有生成密钥、启动服务或实现新 provider。存储/集群/固定二进制和双人解封、异机恢复仍需验证。
- 同一工程师在嵌套 PG16 夹具里被创建第二个个人仓的问题已修复：仅复用精确既有个人仓，真实收货/入账、开账证明和权限守卫保留；10 项聚焦验证通过。历史升级期待已纳入 0083 精确种子转换及 0165/0167/0169 逐人合法增量，严格限于冻结的单区域身份；19 项受影响复核通过，其余旧事实仍逐值比较。原失败日志保留，详见 [增量修复记录](CI_14D0731_INCREMENTAL_REPAIR_20261008.md)。
- `14d0731` Client run `37772507561` 已 success；PG16 run `37772507579` 已为 in_progress。20:36（Asia/Shanghai）中途快照 64 成功 / 7 失败 / 3 static_safety 运行中，尚无整个 run 的终态，最新明细见 `artifacts/formal-0165-integration/ci-14d0731/latest-jobs.json`。日审丢响应夹具已降低 spawn 导入开销并保留真实拒绝结果，新增 15 项聚焦通过；旧 CI 具体恢复结果仍未知，须新候选 PG16 验证。库存权限反例已修正为撤销合法投影权限及新增非法身份列权限必须拒绝，新增 4 项聚焦通过，含异常后的准确恢复。本批候选集中提交后等待原 run 终态再推送，不取消该 run、不重复终态大套件。

目标机本轮只读资源快照约 2 CPU、2.0 GiB 可用内存、32.3 GiB 磁盘空闲，原六容器保持；这不是新密钥服务负载或恢复验收。正式放行继续需要精确 SHA PG16、受控身份与续期、真实数据加密/私有附件、短信和角色/真机 UAT、HTTPS/API readiness、备份恢复与回滚。没有购买云资源、迁移数据库、切换服务或发送真实短信；不再要求重复登录。

## 2026-10-08 19:43 接续：旧 CI 失败增量修复已聚焦验证

**试点 MVP，不等同完整 V1，仍未上线。** PNVS/镜像准备已提交 `094f01766563b7d6ef1a866d2e499599af3d7e72`。旧 run `37746456944` 已失败终态，不再等待或取消；在其结果上新增的目录、元数据污染、历史结构和读取夹具修复累计 **147 项聚焦通过**（53 + 3 + 81 + 10），没有重跑完整套件、关闭断言或修改生产 SQL/权限。原生 PG16 安装测试改用 CI 明确安装的 PostgreSQL 16 路径，仍须远端实跑。

修复详情与失败保留见 [静态 CI 增量修复](STATIC_CI_REPAIR_20261008.md)。下一步完成候选安全扫描和集中提交后，一次正常推送取得准确新 SHA 的 Client/PG16 run；本地通过和三类候选镜像构建完成均不等于发布放行。真实运行身份、KMS/私有 OSS、正式人员及短信/UAT、TLS/API readiness、备份恢复与回滚继续分别验收。小程序候选仍不接旧 API，不要求重复登录。

## 2026-10-08 19:27 接续：镜像构建完成，发布仍未放行

**试点 MVP，不等同完整 V1。** 已提交候选为 `8ad721686fd1f5516d0531c724005af6076147b4`，尚未推送；后续 Docker 摘要固定和 PNVS 预检改动待本地审查提交。目标机三个隔离候选镜像均构建成功，API 文件/依赖及 Web 页面/Caddy 配置离线核验通过，未启动应用或迁移数据库。镜像来源是 8ad7216 加三个 Dockerfile 修改，不能标成纯净 SHA 发布产物。

PNVS 脱敏身份预检及部署配置组 122 passed；候选 Compose 已解析，十项真实配置缺口仍阻止部署。旧 CI 已终态 48 成功/27 失败，新增静态失败正在逐项修复；旧成功项不替代新 SHA。微信登录/端口已解决，公众平台 URL 工具限制仍独立存在。小程序 API 留空并拒绝网络请求，不接入仍关闭短信的旧 API。

详细镜像 ID、来源摘要、失败保留、验证边界和接续材料见 [候选镜像与 PNVS 预检](PILOT_CANDIDATE_BUILD_20261008.md)。原 Goal 回读仍为 blocked，本次按已授权普通开发继续，没有伪造 Goal 恢复或完成。

## 2026-10-08 17:20 接续：0179 修复候选与真实上线条件

**当前仍为试点 MVP，不等同完整 V1，发布状态 `not_ready`。** 本节覆盖下方旧 SHA、未提交和登录状态；历史证据原样保留。当前已提交/推送基线是 `27ca13e10bf36165b19341a9aac9679df22e0e8c`，后续修复仍在同一工作树，不能用基线 CI 计数宣称新修复通过。

- GitHub Client run `37746456913` 成功；PG16 run `37746456944` 最近读取为 48 成功、23 失败、3 个 static_safety 运行中。托管 PG16 已实际运行，当前阻塞是失败项和未完成门禁，不能继续笼统记录为“没有 hosted DB”。不取消当前运行，不连续推送触发取消。
- 新增 0179 仅修正共享收货表与普通申请关闭守卫的兼容：退回收货仍须原 0105 COMMIT 完整证明，普通申请关闭/取消约束保留。后置 stock-return 功能没有向试点开放；冻结历史迁移、底层状态和依赖未删除。另修复临时库默认 ACL、授权版本精确预期、并发夹具阶段和旧版本/触发器坐标绑定。
- 本地新增迁移/目录组 20 passed；数据库安全、权限夹具和 SQLite 迁移组原为 353 passed / 1 failed，唯一旧版本反例修复后对应权限文件 48 passed。原失败保留，不拼成完整 PG16 通过。当前头静态契约通过并保持 `releaseDecision=not_ready`。
- 小程序聚焦原为 59 passed / 1 failed；新增隐藏断言修复后只复测该项，1 passed。独立打包/来源绑定及拒绝篡改组 8 passed。没有重跑已终态大套件。
- 微信开发者工具官方 CLI 已重新核实 `login=true`、端口 55909 已开启；本人登录/服务端口不再列作待办。实际打开项目 `artifacts/miniprogram-trial/20261008-bound-candidate`，AppID `wx15b6d1a74dd92eb8`，查询首页已显示。它与原候选包内容一致（833343 bytes），新增回执绑定源文件和构建脚本；API 尚未配置，不是可用真机体验版，未上传/提审/备案通过/发布。
- 目标域名 `/api/auth/login-options` 实际返回短信关闭、密码开启，`/api/health/live` 和 `/api/health/ready` 均 404，证明当前仍是旧 API。不得将此目标冒充已部署的新试点 API。旧 API 容器未见所查默认凭证链配置；IMDS 加固探测 404，身份可用性未证实。没有调用真实短信发送/校验、没有读取或输出密钥。
- 微信公众平台备案详情因电脑操作工具 URL 访问限制停止读取，不绕过。用户截图中的微信认证完成与小程序备案未备案分别记录；备案驳回原文尚未取得。保留用户提供完整原文这一待办，不要求重复登录。

详见 [本批修复、证据与接续顺序](TRIAL_MVP_INTEGRATION_20261008.md)。后续顺序：补齐修复审查/必要验证和安全扫描 → 集中提交 → 当前 run 终态后一次正常推送 → 验证新 SHA CI；同步准备运行身份、KMS/PNVS、新版 HTTPS/API 及正确小程序包，正式放行仍需真实 UAT、备份恢复与回滚证据。

## 2026-10-08 续接：试点候选提交与远端门禁的顺序

**当前范围：试点 MVP，不等同完整 V1。** 申请/提交 → 审批 → 最小货源分配/占用 → 后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员仅保留申请、状态、本人收货/入账；区域/总部保留必要后台履约。拣货和复杂后置区块保持隐藏，既有底层状态、迁移、契约、出库依赖、库存流水、幂等、审计、权限、安全与取消/关闭守卫、最小站内通知全部保留。

### 候选提交与生产放行分别验收

- 用户已经明确授权提交、推送和部署。审查候选并完成必要本地验证及敏感文件检查后，可以提交并正常推送 `codex/notification-delivery-worker`，以产生同一新 SHA 的远端 CI；不要求尚未产生的远端同 SHA 结果先于提交存在。下文历史“所有外部门禁齐全前不提交、不推送”不再作为当前执行规则。
- 只读核实 `.github/workflows/postgresql16-release-gate.yml` 已为本分支 push 配置 GitHub 托管 runner 和一次性 `postgres:16-alpine` 服务，另有独立私有 PG16 集群的历史/业务门禁。没有当前候选 CI 证据仍是待验收项；本机没有 PG16/Docker 不构成必须另购 RDS 的理由。只有实际权限、runner 或服务失败才按具体结果登记外部阻塞。
- 提交测试候选不等于生产放行。部署/上线仍分别核验当前版本 CI、正式迁移/ACL、身份和真实 PNVS 登录、多角色流程 UAT、备份恢复及回滚。真实短信登录属于首发必需；后置的是业务短信/微信/飞书通知渠道及投递运维。
- 不 force-push，不连续推送取消同版运行，不关闭失败门禁，不将旧 SHA、取消、跳过或本地静态通过当成 hosted PG16 通过。当前 Goal 工具回读仍是 `blocked`；本次续接没有伪造 `active` 或 `complete`，没有重建 Goal。

### 本地增量证据与候选范围

- 完整暂存后 `git diff --cached --check` 为 **exit 2**：两份冻结 SQL（`stock_scrap_0165/execution_evidence.sql:191`、`recovery_evidence.sql:119`）含行尾空格，两份测试文件（`test_return_condition_case_read.py:92`、`test_return_condition_source.py:154`）末尾有空行。它们此前属于未跟踪文件，因此未暂存 diff 检查没有覆盖；这是格式告警，不能写成全部 diff 检查通过。本候选保留已核验原字节，不为消除告警修改冻结迁移正文、hash 或删除测试；没有关闭任何 CI、安全或业务门禁。

- 保留上一份 `current-head-sms-contract-20261008.json` 成功回执。审计脚本增量只修正证据质量：真实生成时间、已存在回执拒绝覆盖、受检源码 SHA、当前 runtime manifest 与 head hash 的相等校验、合成凭证预检拒绝静态/STS 输入，并明确字符串检查不等于认证行为或实际部署证明。
- 在 `cloud_oam/` 执行 `PYTHONPATH=backend .venv/bin/python scripts/verify_current_head_sms_contract.py --output artifacts/formal-0165-integration/current-head-sms-contract-20261008-v2.json`：**exit 0 / PASS**。回执 SHA256 `82a4fe3a8de7edc7cce5f67390481db0e69e10c380e5a6b85dcdc88baedc900f`，Alembic/控制 CLI/PG16 gate 均为 `20261227_0178`，readiness hash `ee5e4a7b51850fda0ea41b5a71b34547cd822c1069075fc31c7ec948b905fb3d`，`releaseDecision=not_ready`。这是新增审计器的核验，未重跑已终态业务测试。
- 提交前展开目录的候选共 **933 个文件**（777 个 `git status --short` 条目包含折叠目录），其中 **623 个 Python 文件 AST 语法检查通过**；`git diff --check` 通过，GitHub CLI 身份检查成功。认证检查不等于拥有 Actions 运行结果；不输出或保存凭证。
- 候选保留本工作树累积的 0165–0178 迁移和相关后端/契约/测试依赖，没有删掉后置功能以缩减候选，也没有新增后置业务范围。拒收/退回补偿、stock-return 全链、工单消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、真实业务通知渠道和运维仍为后续迭代；真实试点库存必须有可核验的来源/期初事实。
- 本轮未改业务实现或客户端视觉。此前 60 项迁移绑定修复、91 项生产适配预检、485 项 HTTP、Web 2900 项及小程序 1086 项等证据按原范围保留；全后端探索曾中断并有失败，不能据此宣称全套通过。下一步是候选安全检查、提交/推送后记录准确 SHA、Client 与 PG16 run 链接和实际终态，再推进首发真实验收。

## 2026-10-08 当前树全后端受控探索（已终止，非门禁结果）

- 当前树全后端受控命令 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests --maxfail=20` 已主动终止，日志为 `/tmp/rsc-backend-current-20261008.log`；终端汇总为 **2890 passed、7 skipped、2 failed、15 subtests passed、1 warning，exit 2**。
- 两项失败均已定位为迁移当前头绑定漂移，相关修复后的复核为 **60 passed、1 warning、exit 0**。该探索不构成全后端门禁通过，终态进程不再轮询，也不以旧日志替代当前 hosted PG16/远端 CI 证据。

## 2026-10-08 生产适配、KMS 与候选预检聚焦回归（当前工作树）

- 生产适配组合、KMS pin gate、候选预检聚焦组：**91 passed, 0 failed, exit 0**（2.60s）。`trial-mvp`、SMS-only、默认凭证链和 KMS gate 顺序保持通过。
- 该结果不替代真实 KMS/PNVS、hosted PostgreSQL 16、目标机部署、浏览器/真机 UAT 或回滚演练。

## 2026-10-08 数据库安全与部署静态聚焦回归（当前工作树）

- 数据库安全/部署安全/触发器坐标聚焦组：**331 passed, 0 failed, exit 0**（77.89s）。函数、触发器、ACL、部署拒绝边界和目录坐标的当前工作树检查保持通过。
- 这是静态/SQLite 契约证据，不是 hosted PostgreSQL 16 迁移、权限、并发或回滚通过；PG16 外部阻塞不变。

## 2026-10-08 通知投递运维聚焦回归（当前工作树）

- 通知投递测试族 `backend/tests/test_notification*.py`：**151 passed, 0 failed, 1 warning, exit 0**（339.95s）。本地队列、投递、未知结果、恢复、幂等、审计和运维查询保持通过。
- 该结果不替代真实短信/微信/飞书 provider、回执/out_id、hosted PG16、浏览器/真机 UAT 或部署回滚；通知真实渠道仍列为后续迭代门禁。
- 当前版本仍标记为**试点 MVP，不等同完整 V1**，未提交、未推送、未部署。

## 2026-10-08 正式 HTTP/API 与默认凭证链夹具收口（当前工作树）

- 正式物料请求 HTTP 套件原先有 2 个夹具失败，原因是测试配置使用静态 PNVS 凭证；已改为 `default_chain` 并清空 AccessKey 字段，生产默认凭证链门禁保持不变。该文件结果 **59 passed, 0 failed, exit 0**。
- 正式认证与文件服务组合结果 **88 passed, 6 skipped, 0 failed, exit 0**；微信历史 provider 行为仅作为短信-only 后续迭代保留，不作为当前缺陷。
- 正式文件族 `test_formal_*.py` 结果 **485 passed, 6 skipped, 0 failed, 1 warning, exit 0**（555.89s）。这证明当前工作树的正式 HTTP/API 测试夹具和权限/审计契约一致，不替代 hosted PostgreSQL 16、真实 PNVS、UAT 或部署证据。
- 当前版本仍标记为**试点 MVP，不等同完整 V1**；HEAD `fc7c9269…` 未提交，PG16、同一 SHA CI、真实短信、浏览器/真机和目标机门禁仍未完成。

## 2026-10-08 SMS-only 历史测试兼容收口（当前工作树）

- `test_core_flows.py` 已去除密码/微信成功登录断言并通过 SMS-only 测试路径；结果 **5 passed, 0 failed, exit 0**。测试数据库用户使用直接种子，生产密码开户接口继续关闭。
- `test_formal_auth_api.py` 的微信 provider 历史行为明确标记为短信-only 试点跳过，混合认证门禁改为验证停用路由 404；结果 **44 passed, 6 skipped, 0 failed, exit 0**。组合结果 **49 passed, 6 skipped, 0 failed**。
- `test_health_readiness.py` + `test_startup_security_boundary.py`：**38 passed, 0 failed, exit 0**；Alembic ORM/head 回归：**1 passed, 0 failed, exit 0**（148.43s）。这些是当前工作树的新聚焦证据，不是 hosted PG16 或远端 CI 证据。
- 该收口只修正历史测试与当前 SMS-only 产品边界的冲突，未放宽认证策略；上一轮全后端探索性中断结果需重新跑有限范围后才能更新，不得宣称全套通过。
- 试点范围、后续迭代清单和 PG16 外部阻塞保持不变；未提交、未推送、未部署。

## 2026-10-07 当前试点 MVP 发布证据矩阵（当前工作树）

| 类别 | 当前证据 | 状态 | 尚缺证据/限制 |
| --- | --- | --- | --- |
| 源码与首发链路 | 路由/状态轴静态检查 `PASS`；审批→分配→预留→发运→本人收货→个人仓入账服务组 **114 passed**；状态轴/发运/入账组 **38 passed** | 本地通过 | 不替代 PG16 真库事务、外部来源和真实 UAT |
| 正式迁移与权限 | 迁移/函数/ACL 指纹聚焦 **9 passed**；发布绑定/回滚契约 **105 passed**；PG16 工作流拓扑 **63 passed** | 本地候选 | 当前工作树没有 hosted PostgreSQL 16 运行证据 |
| HTTP/API 与入口 | 公开首页、`/xx`、health/live/ready、SMS-only、no-store 和 daily-ops 聚焦 **36 passed** | 本地通过 | 不替代真实域名/TLS、浏览器回读和生产服务 |
| PC/H5/小程序 | 已有当前树 Web **2900 passed/13 skipped**、小程序 **1086 passed**、客户端契约 **65 passed**；试点 capability 与 SMS-only 静态检查通过 | 本地通过 | 不替代真机/浏览器现场 UAT |
| 当前版本 CI | GitHub 远端分支仍为 `fc7c9269…`；该 SHA Client gate 成功，PG16 gate 失败；未提交树没有新 run | 未放行 | 必须用同一新 SHA 获取完整 CI 终态，不能复用旧 run |
| 真实 UAT/短信 | PNVS/RAM、真实 `out_id`、验证码精确读回、限流/审计/未知结果防重放、浏览器/真机 UAT 均未验收 | 阻塞 | 真实授权会话和隔离号码回读缺失 |
| 部署/回滚/上线 | 本地候选脚本防旁路和失败回滚已通过；目标服务器状态和部署回读未知 | 未放行 | hosted PG16、目标机、KMS、备份/回滚演练缺失 |
| 公共知识目录 | 公共目录保持 `pending/0`，知识源按用户要求暂缓 | 试点允许 | 严格公共目录 release 仍未就绪 |

上述矩阵只适用于当前工作树；历史矩阵、旧 SHA、可见页面和局部测试不能升级为正式上线证据。所有外部门禁齐全前保持“试点 MVP，不等同完整 V1”，不提交、不推送、不部署。

## 2026-10-07 公开入口与持续运行 smoke 聚焦回归

- 当前树的公开首页、`/xx` 私有入口、健康探针缓存边界、SMS-only 选项/未登录响应和 daily-ops 参数契约聚焦组 **36 passed, exit 0**。
- 该结果只证明本地 smoke/CLI 合同，不是实际域名、TLS、浏览器/真机、hosted PostgreSQL 16、PNVS、部署或回滚通过；版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 远端 ref 与当前工作树绑定复核

- GitHub API 已确认远端 `codex/notification-delivery-worker` 存在且仍指向 `fc7c9269e19f6afd180a0aced55b565f03c244b6`；当前未提交工作树没有对应的新远端 SHA/CI run。
- 旧 run 和当前本地聚焦证据不混用：远端结果仅适用于已提交 SHA，本地结果仅适用于未提交树；外部门禁未齐前不提交、不推送、不部署，版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 首发服务顺序聚焦回归

- 当前树按申请/审批→最小分配→预留→发运→本人收货/个人仓入账顺序的后端服务聚焦组 **114 passed, 1 warning, exit 0**。独立状态轴、幂等、失败回滚和权限重新核验保持通过。
- 该证据只覆盖本地合成服务/契约，不是 hosted PostgreSQL 16、真实 PNVS、真实 UAT、部署或回滚演练通过；版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 首发状态轴与本人入账最小后端回归

- 当前树的状态轴、发运和本人入账聚焦组结果 **38 passed, 1 warning, exit 0**；申请/审批、分配、预留、出库、发运、个人收货和个人仓入账仍保持独立事实，通知与 OAM 收货没有被隐式推进。
- 该结果只证明本地服务/HTTP 契约和失败回滚边界，不是 hosted PostgreSQL 16、真实 PNVS、真实 UAT、部署或回滚演练证据；当前版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 发布绑定与失败回滚边界复核

- 当前工作树执行发布绑定/部署聚焦组：**105 passed, exit 0**。候选外状态目录、不可变回执、镜像/配置指纹、80/443 保护、KMS pin gate 顺序和失败不清理均有本地契约覆盖。
- 这是本地发布脚本证据，不是 Docker、hosted PostgreSQL 16、PNVS、目标机部署、真实 UAT 或回滚演练通过；当前版本仍为“试点 MVP，不等同完整 V1”，未提交、未部署。

## 2026-10-07 远端当前提交失败回读与工作树修复复核

- GitHub run `36931116983`（提交 `fc7c9269e19f6afd180a0aced55b565f03c244b6`）只读结果：Client gate 成功，PostgreSQL 16 gate 失败。两个 static shard 的 10 个失败用例分别来自旧 H5 schema fixture、0106 函数指纹，以及 0047/0052/0046/0152 的数据库函数、ACL、目录指纹漂移；当前未提交工作树已对齐这些检查。
- `pg16_runtime (inventory/migrations)` 日志在迁移阶段显示 runner shutdown signal，属于 CI runner 取消，不能作为业务门禁通过或失败的替代证据；另一个 static shard 没有可读步骤日志，保持未知。
- 当前树针对所有已知失败复核为 **9 passed, 1 warning, exit 0**（聚焦 pytest 命令见交接文档）。这不等同新的远端 CI 运行，因为修复仍未提交；版本仍保持“试点 MVP，不等同完整 V1”，不提交、不部署。

## 2026-10-07 PostgreSQL 16 工作流聚合门禁审计

- 当前 `.github/workflows/postgresql16-release-gate.yml` 的四组矩阵门禁均无 `continue-on-error`；聚合 job 即使前置失败也会执行，随后逐项要求 `runtime/loss/condition/static=success`，未发现跳过或吞错旁路。
- `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pg16_workflow_topology.py` 结果 **63 passed, 1 warning, exit 0**。这是工作流结构证据，不是 hosted PostgreSQL 16 运行、迁移、ACL、并发或回滚证据；当前版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 发布候选旁路静态审计

- `pilot_release.py`/`pilot_preflight.py` 当前静态结果 `PILOT_NOGO_STATIC=PASS`：项目/tag 完整匹配、`trial-mvp` scope、候选外状态目录、指纹与不可变回执、迁移/head 回读、80/443 端口保护、KMS pin gate 先于应用启动均已绑定；未发现可绕过这些门禁的切换变量。
- 这是源码静态证据，不是 Docker、hosted PostgreSQL 16、KMS、PNVS、目标服务器、真实 UAT、部署或回滚通过证据。版本仍为“试点 MVP，不等同完整 V1”，外部门禁未满足前不提交、不部署。

# 正式 V1.0 当前验收与缺口审计（请求恢复已整合，日终对账与维护版回退通过）

## 2026-10-07 首发 MVP 链路路由静态复核

- 当前首发链路路由静态结果 `MVP_FLOW_ROUTE_STATIC=PASS`：审批→分配→预留→出库→发运→收货证据→个人仓入账各节点均有正式接口和服务调用。
- 该结果是源码接线证据，不替代 PG16 事务/权限、真实 PNVS/UAT、远端 CI、部署或回滚；版本继续是“试点 MVP，不等同完整 V1”。

## 2026-10-07 首发状态轴静态复核

- 当前源码保留审批、分配、预留、出库、发运、物流签收、OAM 收货、个人仓入账、通知与对账的独立接口/状态轴，结果 `STATE_AXIS_WIRE_STATIC=PASS`。
- 这是本地源码契约证据，不替代 PG16 事务、权限、并发、回滚、真实 PNVS/UAT 或部署验收；版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 技术员后置路由旁路静态复核

- 当前 `App.tsx` 后置路由逐项核对结果 `TECHNICIAN_POST_ROUTE_STATIC=PASS (14 routes)`，包含库存、盘点、报废、报损、退回、对账、通知和负责人管理入口。
- 该证据只证明前端直接 URL 旁路被 capability/角色守卫拦截，不替代后端权限、PG16、真实 UAT 或部署；版本继续是“试点 MVP，不等同完整 V1”。

## 2026-10-07 公开首页与 /xx 接线静态复核

- 根入口挂载 `PublicKnowledge`，首页不挂载登录组件；后台按钮固定为 `https://rscwz.cn/xx`，warehouse 构建使用 `/xx/` basename。
- 当前静态断言 `PUBLIC_HOME_PRIVATE_WAREHOUSE_STATIC=PASS`；这不替代真实域名切流、TLS、知识源导入或浏览器/真机 UAT。
- 版本继续是“试点 MVP，不等同完整 V1”，知识源按用户要求暂缓，PG16、PNVS、部署与回滚门禁不变。

## 2026-10-07 云端只读会话当前状态未知

- 当前已授权浏览器的只读接管调用 `cua.getState()` 在 30 秒内超时并重置内核；没有凭据读取、重复登录、远端命令或服务器写入。
- 该结果不能归因为认证失效，也不能把历史目标机/PG16 回执当作当前 SHA 证据；云端 UAT、目标机状态和 hosted PostgreSQL 16 继续未验收。

## 2026-10-07 发布范围与 SMS-only 静态复核

- 当前源码静态断言确认 `TRIAL_MVP_UI_SCOPE` 六项后置能力关闭，申请页绑定 scope 与 `can_read_allocation_options`；结果 `TRIAL_SCOPE_STATIC=PASS`。
- 网页/小程序登录源码不存在微信和密码登录入口，结果 `SMS_ONLY_CLIENT_STATIC=PASS`；这不是真实短信、浏览器或真机 UAT 证据。
- 版本仍为“试点 MVP，不等同完整 V1”，不解除 PG16、PNVS、UAT、部署和回滚门禁。

## 2026-10-07 PostgreSQL 16 运行时阻塞复核

- 当前只读环境检查显示 `postgres`、`pg_config`、`psql`、`docker`、`podman` 均不可用；本机没有可审计 PostgreSQL 16 运行时。
- 迁移、运行角色 ACL、并发、连接终止与回滚证据继续保持未完成，不能用 SQLite、旧隔离库或静态源码检查替代；未启动容器、未产生外部写入。
- 版本继续是“试点 MVP，不等同完整 V1”；下一步需在同一当前 SHA 接入可审计 PG16 实例后运行既有门禁。

## 2026-10-07 候选配置预检复核（当前 SHA）

- 当前 HEAD 为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`；`test_pilot_preflight.py` 聚焦结果 **3 passed, 66 deselected, exit 0**，覆盖 `trial-mvp`、SMS-only、默认凭证链和非 MVP 范围拒绝。
- 预检是本地合成配置证据，不启动容器、不连接 PG/KMS/OSS/PNVS，也不代表部署或真实短信通过。
- 当前树执行 `bash scripts/verify_repository_safety.sh` 已 **PASS（3062 candidate files，75,774,155 bytes，exit 0）**；未发现高置信凭证或个人路径泄露。该仓库边界证据不替代 hosted PostgreSQL 16、真实 PNVS/UAT、远端 CI、部署和回滚。
- 版本继续是“试点 MVP，不等同完整 V1”；下一有限里程碑为同一 SHA 的外部 PG16/PNVS/UAT 与部署证据。

## 2026-10-07 Web 完整门禁恢复全绿（权限回收夹具对齐）

- Web 全量复核首次出现 **2899 passed, 13 skipped, 1 failed**；失败仅是权限回收测试夹具在 `can_read=false` 时仍保留写能力，与正式 capability 先行校验不一致。产品代码和权限门禁未被削弱。
- 夹具现已同时撤销所有依赖读取回验的写能力；`materialRequestInboundRecovery.test.ts` 聚焦 **7 passed, 0 failed**，恢复逻辑继续在读回权限变化时保留原命令。
- 完整 Web 复跑为 **2900 passed, 13 skipped, 0 failed**（174 files）。该证据只覆盖本地 Web 代码和合成夹具，跳过项仍列入后续完整 V1；不替代 hosted PostgreSQL 16、真实 PNVS/RAM、UAT、远端 CI、部署或回滚。
- 版本继续冻结为“试点 MVP，不等同完整 V1”，没有外部数据库、短信、库存、审计、通知或迁移写入。

## 2026-10-07 小程序完整当前门禁恢复全绿

- 当前工作树使用 bundled Node 执行 `node --test tests/*.test.js`：**1086 passed, 0 failed**。此前 3 个失败是测试对已退役微信认证路径的主动调用，现已改为 SMS-only 安全边界断言；没有放开生产守卫。
- 这提供当前小程序/客户端门禁证据，但不替代远端 CI、真实 PNVS 发送与回调 `out_id`、验证码精确读回、hosted PostgreSQL 16、真实用户 UAT、部署或回滚。
- 版本仍保持“试点 MVP，不等同完整 V1”，没有外部系统写入。

## 2026-10-07 客户端 CI 结构契约同步

- 当前客户端工作流实际包含 14 个固定步骤（含试点 artifact 和正式公共目录条件门禁）；小程序契约测试原来仍按旧 12 步白名单核对，已同步为当前审查过的 14 步执行结构。
- `node --test tests/client-release-gate-contract.test.js` **65 passed, 0 failed**；没有放宽仓库安全、只读权限、固定工具链或 release 条件。
- 这只是本地 CI 契约证据，不是远端 CI 成功、hosted PostgreSQL 16、真实 PNVS/RAM、UAT、部署或回滚证据；版本继续保持“试点 MVP，不等同完整 V1”。

## 2026-10-07 小程序 SMS-only 页面门禁修正

- 小程序登录页的 SMS-only 实现已经移除微信登录方法；旧页面测试仍尝试调用 `loginWithWechat()`，造成一项测试遗留失败。现已改为断言短信显式会话屏障和微信入口不存在。
- `node --test tests/formal-auth-pages.test.js tests/sms-only-policy.test.js` **11 passed, 0 failed**；没有访问 PNVS、数据库或生产会话。该证据只覆盖页面策略与会话边界，不证明真实短信发送、回调 `out_id`、验证码读回或上线。
- 版本仍为“试点 MVP，不等同完整 V1”；hosted PostgreSQL 16、真实 PNVS/RAM、UAT、部署和回滚门禁继续保持未完成。

## 2026-10-07 试点前端全页门禁与后续 V1 测试隔离

- 当前 `FormalMaterialRequests.test.tsx` 在 bundled Node 下复核为 **64 passed, 13 skipped**。跳过项明确对应冻结范围之外的供给计划、复杂恢复、关闭/剩余取消页面操作；后端路由、状态轴、迁移、不可变流水、幂等、审计和权限契约继续保留，不把这些后续能力宣称为试点已交付。
- 角色 capability 与本人收货/个人仓入账聚焦复核 **4 passed, 95 skipped**；技术员仍只看到申请、状态、本人收货和本人入账，区域/总部后台区仍由 `can_read_allocation_options` 与角色/库存权限控制。该证据修正了测试口径冲突，不解除 hosted PostgreSQL 16、真实 PNVS/RAM、部署、设备 UAT 或正式发布门禁。
- 仅使用本地合成夹具和工作区 Node；没有外部数据库、短信、库存、审计、通知或迁移写入。版本继续标记为“试点 MVP，不等同完整 V1”。

## 2026-10-07 PNVS 默认凭证链收口（不等同真实短信通过）

- PNVS/Dypnsapi 适配器默认使用阿里云 SDK 默认凭证链（目标 ECS RAM 角色/实例元数据），OpenAPI `Config` 只接收凭证对象，不接收静态 AccessKey 字段；静态/STS 模式仅供隔离测试和迁移兼容，生产启动拒绝。
- `pilot_preflight.py`、Compose、`.env.example` 和 bootstrap 已绑定 `OAM_SMS_CREDENTIAL_MODE=default_chain`，并拒绝任何静态/STS 值进入候选试点配置。
- 聚焦证据：`test_sms_provider.py` **38 passed**、`test_pilot_preflight.py` **69 passed**、`test_startup_security_boundary.py` **31 passed**；没有访问 IMDS、PNVS、RAM、数据库或生产会话。
- 该证据不替代 ECS RAM 角色绑定、PNVS 最小权限/来源限制、真实隔离号码发送回执、`out_id` 精确回读、登录 UAT、hosted PostgreSQL 16、部署和回滚；真实短信保持关闭。

## 2026-10-07 MVP 读/本人写入口异常边界前置

- 需求列表、详情、可编辑草稿、本人收货和个人仓入账路由现在在查询服务、请求头校验或运行时保护前建立私有响应边界；统一异常映射也固定 `Cache-Control: no-store, max-age=0`、`Pragma` 和 `Referrer-Policy`。
- HTTP 聚焦回归 **5 passed, 54 deselected, 1 warning**；正式需求路由顺序门禁 **1 passed, 6 deselected**，核心路由门禁组合 **3 passed, 4 deselected**；Python 编译和 `git diff --check` 通过。
- 证据仅覆盖本地 ASGI/源码边界，不替代 hosted PostgreSQL 16、真实 PNVS、部署或正式 UAT；未重跑已终态测试，也未产生外部写入。

## 2026-10-07 正式需求路由全方法私有响应门禁

- 源码审计把正式物资需求路由从仅 POST 扩展到全部 **73 个** HTTP 函数（GET/POST/PUT/PATCH/DELETE），当前全部在业务服务与输入错误边界前设置 `_set_read_no_store`，避免技术员状态、本人收货/入账和后台履约响应落入公开缓存。
- 聚焦回归 **3 passed, 4 deselected**，Python 编译和 `git diff --check` 通过；证据只覆盖本地源码，不替代 hosted PostgreSQL 16、真实 PNVS、部署或正式 UAT，未重跑已终态测试。
- 该项是试点 MVP 的响应隐私安全收口，不扩大后置履约能力，也不解除 PG16 hosted DB 外部阻塞。

## 2026-10-07 试点构建产物后置区 capability 门禁

- 候选 `verify:pilot-release` 现读取申请页源码，锁定 `can_read_allocation_options` 为后台后置区总门槛，并逐项检查供给、占用、出库、发运、后台收货/入账、拣货、履约准备面板没有脱离该门槛；技术员本人收货/个人仓入账仍是保留入口。
- 工作区 Node 执行 `node build/verify-pilot-release.mjs` **exit 0**；产物核对保持 `/xx/` 私有路径和 `pending/0` 公开目录。Python 聚焦回归 **2 passed, 3 deselected**，Node 语法、Python 编译、`git diff --check` 通过。
- 证据只证明候选产物的源码范围绑定，不替代真实 PNVS、hosted PostgreSQL 16、部署、设备验证或正式 UAT；未重跑已终态测试，也未产生外部写入。

## 2026-10-07 SMS-only 认证前缀私有响应边界

- 主应用对 `/api/auth` 整个认证前缀统一设置私有 `no-store`、`Pragma` 和 `Referrer-Policy`；生产环境退役认证路径在 middleware 早返回的 410 也覆盖同一边界。
- 聚焦回归 **3 passed, 55 deselected, 1 warning**；证据只覆盖本地 middleware，未连接 PNVS、数据库或生产会话，不能替代真实短信送达/核验、部署或 UAT，未重跑已终态测试。
- 该项属于当前冻结“试点 MVP，不等同完整 V1”的认证安全收口，不改变 SMS-only 策略、会话撤销、幂等/审计和未知结果防重放约束，也不解除 PG16 hosted DB 外部阻塞。

## 2026-10-07 MVP 申请/审批写接口私有响应边界

- 申请草稿创建、替换、提交、撤回、取消、内部审批和外部审批证据登记/复核共 8 个写路由，在请求头校验前建立 `no-store`、`Pragma` 和 `Referrer-Policy`；主应用对 `/api/v1/material-requests` 整体补同一边界，覆盖路由函数未执行的框架级错误。
- 源码 capability 门禁 **3 passed, 3 deselected**（包含当前 26 个 POST 路由全部设置私有边界），HTTP 映射与主 middleware 回归 **6 passed, 50 deselected, 1 warning**；这是本地路由/合成依赖证据，不替代 hosted PostgreSQL 16、真实 PNVS、部署或正式 UAT，未重跑已终态测试。
- 该项服务于冻结的“试点 MVP，不等同完整 V1”申请→审批链，不改变分配/占用、后台人工履约发运、本人收货和个人仓入账状态轴，也不解除 PG16 hosted DB 外部阻塞。

## 2026-10-07 未登录私有入口响应头门禁

- `/api/auth/me` 的未登录 401 响应现在纳入部署 smoke 头部核对：必须返回 `Cache-Control: private, no-store, max-age=0`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`，与应用 middleware 的私有身份读取边界一致。
- 受影响正向/负向聚焦结果 **3 passed, 12 deselected**；证据仅来自本地合成 HTTP smoke，未连接 hosted PostgreSQL 16、PNVS 或生产服务，未重跑已终态测试。
- 该项属于当前冻结“试点 MVP，不等同完整 V1”的认证/缓存安全收口；不改变申请→审批→分配/占用→后台人工履约发运→本人收货→个人仓入账范围，也不解除 PG16 hosted DB 外部阻塞。

## 2026-10-07 SMS-only 登录选项完整响应头门禁

- smoke 对 `/api/auth/login-options` 现在同时要求 `Cache-Control: no-store, max-age=0`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`，与当前 SMS-only 路由实现一致。
- 受影响正向/负向聚焦结果 **2 passed, 12 deselected**；shell 语法、Python 编译和 `git diff --check` 通过。该项仍不代表 PNVS 真实发送/核验或短信上线。

## 2026-10-07 SMS-only 登录选项 smoke 缓存边界

- 部署 smoke 现在同时读取 `/api/auth/login-options` 的响应头，要求 SMS-only 能力开关返回 `Cache-Control: no-store, max-age=0`；缺失时候选检查失败，避免旧客户端缓存过期登录能力。
- 新增登录选项缓存头缺失负向回归；受影响正向/负向聚焦结果 **2 passed, 12 deselected**，`sh -n`、Python 编译和 `git diff --check` 通过。
- 该项不代表 PNVS 真实发送/核验或短信上线；未连接外部服务，未提交、未部署。

## 2026-10-07 健康探针缓存边界

- 部署 smoke 现在读取三个健康探针的响应头，要求 `/api/health`、`/api/health/live`、`/api/health/ready` 都返回 `Cache-Control: no-store, max-age=0`；缺失任一私有缓存头会在候选检查阶段失败。
- 新增缺失 ready 探针缓存头的负向回归 **1 passed, 12 deselected**；`sh -n`、Python 编译和 `git diff --check` 通过。该检查只读，不连接数据库、PNVS 或生产服务。

## 2026-10-07 试点持续运行探针统一

- `scripts/smoke_test.sh` 现在只读核对 `/api/health`、`/api/health/live`、`/api/health/ready` 三个探针：分别要求 `ready`、`live`、`ready` 状态，并在候选 smoke 设置期望值时要求三者都返回同一 `release_scope=trial-mvp`。
- 新增 live 探针漂移负向回归；受影响正向入口与负向用例聚焦结果 **2 passed, 10 deselected**，`sh -n` 和 `git diff --check` 通过。未连接数据库、PNVS 或生产服务。
- 该项只增强候选持续运行和范围漂移检测，不改变业务状态轴或完整发布门禁；hosted PostgreSQL 16、真实短信和正式 UAT 仍未通过。

## 2026-10-07 试点 scope 实际页面绑定复核

- `verify:pilot-release` 现在除检查六项 scope 均为 `false` 和 `Object.freeze` 外，还读取 `FormalMaterialRequests.tsx`，要求集中式 `TRIAL_MVP_UI_SCOPE` 被实际导入并逐项消费；scope 未挂载到申请页时，试点 artifact 门禁直接失败。
- `frontend/src/trialMvpScope.test.ts` 聚焦结果 **1 passed**；TypeScript、Node 语法检查、试点 artifact 验证（`exit 0`，`/xx/`）及 `git diff --check` 均通过。公开目录仍如实为 `pending/0`，不构成完整发布通过。

## 2026-10-07 试点 CI 绑定 scope 门禁

- 客户端 CI 在构建 Web 与 `/xx` 私有 warehouse 产物后，新增 `pnpm run verify:pilot-release` 步骤；该步骤读取 `src/trialMvpScope.ts`，锁定六项后置 capability 为关闭并校验 `/xx` artifact，防止试点构建漏回完整 V1 入口。
- 新增 `backend/tests/test_client_release_workflow_scope.py::test_client_gate_verifies_frozen_trial_mvp_scope_after_private_build`，聚焦结果 **1 passed, 3 deselected**；测试文件 `py_compile` 与 `git diff --check` 通过。
- 该 CI 门只验证当前试点范围与私有产物，不放宽正式 `--release` 公共目录门禁；公开知识目录、真实 PNVS、hosted PostgreSQL 16、部署和 UAT 仍分别验收，未提交或部署。

## 2026-10-07 登录选项响应私有化

- 登录选项接口现在对短信开关、时效和退役渠道字段使用私有缓存头，避免旧客户端/代理继续使用过期认证能力；SMS-only 策略字段保持不变。
- 聚焦证据 **5 passed, 3 deselected, 1 warning**；不包含真实 PNVS 回执、登录回读、PG16 hosted DB 或正式 UAT。
- 该项属于试点 MVP 认证边界安全收口，不等同完整 V1，未重跑终态认证门禁。

## 2026-10-07 SMS-only 退役入口私有错误头

- 退役的密码/微信/改密/临时密码路由继续返回非枚举 404，并统一使用私有错误缓存头；历史字段、路由和迁移契约保留。
- 聚焦证据 **4 passed, 4 deselected, 1 warning**；不包含真实 PNVS 发送、核验或登录回读，不替代 hosted PostgreSQL 16 和正式 UAT。
- 该项属于试点 MVP 认证边界收口，不等同完整 V1，未重跑终态认证门禁。

## 2026-10-07 需求概览 API capability 前置收口

- 需求概览 API 在服务查询前增加后台角色守卫；技术员直达 API 不能借服务层范围计算绕过后置报表隐藏，返回 `403 report_forbidden` 和私有响应。
- 聚焦证据 **4 passed, 7 deselected, 1 warning**；后台概览只读/事务释放行为未改变，证据不替代 PG16 hosted DB、正式 UAT 或部署验收。
- 该项属于试点 MVP 的后置报表 capability 防旁路收口，不等同完整 V1，未重跑终态门禁。

## 2026-10-07 物资需求验证异常统一私有化

- 物资需求路径的统一请求校验异常现在返回私有 `no-store` 响应，并保持脱敏错误体；申请/提交非法联系人输入仍不回显姓名、手机号或地址。
- 聚焦证据 **5 passed, 54 deselected, 1 warning**；这是本地 ASGI/合成夹具证据，不替代 PG16 hosted DB、真实 PNVS 或正式 UAT。
- 该项属于试点 MVP 的申请入口隐私/缓存收口，不等同完整 V1，未重跑终态门禁。

## 2026-10-07 命令状态统一 no-store 源码门禁

- 源码门禁现在锁定 **19 个**命令状态/恢复接口的私有缓存前置顺序；新增接口若在请求头或服务读取前缺少 `_set_read_no_store` 将直接失败。
- 聚焦证据 **14 passed, 1 deselected, 1 warning**；这是源码/本地 ASGI 证据，不替代 hosted PostgreSQL 16、真实 PNVS、PC/H5 UAT 或部署验收。
- 该项属于试点 MVP 的安全回归防护，不等同完整 V1，未重跑终态门禁。

## 2026-10-07 供给状态查询参数私有校验

- 供给命令状态查询的缺失/非法 `trace_request_id` 现在由该精确路径的统一验证异常处理器收口，保留原有必填/422 语义并固定 `no-store`、脱敏错误体；不会由默认 FastAPI 参数处理器产生可缓存的公开错误。
- 聚焦证据 **13 passed, 1 deselected, 1 warning**；这是本地 ASGI/合成夹具证据，不替代 hosted PostgreSQL 16、真实 PNVS 或正式 UAT。
- 该项仍属于试点 MVP 的输入与恢复边界，不等同完整 V1；未重跑终态门禁。

## 2026-10-07 命令状态请求头校验统一私有化

- 关键命令状态读取接口在请求头校验前统一设置 `no-store`；非法头、业务错误和数据库错误不会落入可缓存的公开响应，数据库只读路径继续回滚不提交。
- 聚焦证据 **14 passed, 11 deselected, 1 warning**；证据仍是本地 ASGI/合成夹具，不替代 hosted PostgreSQL 16、真实 PNVS 或正式 UAT。
- 该项属于试点 MVP 的错误/缓存边界收口，不扩大完整 V1；未重跑终态门禁。

## 2026-10-07 供给命令状态恢复异常路径收口

- 后台供给命令状态查询的服务异常、数据库异常和响应投影异常均返回 `no-store`；数据库异常在脱敏 503 前显式回滚，避免只读恢复请求留下未决事务。
- 聚焦回归 **11 passed, 1 deselected, 1 warning**；技术员仍被后台供给命令 capability 拒绝，生命周期状态查看例外保持不变。
- 这是试点 MVP 的只读恢复安全收口，不等同完整 V1；没有 hosted PostgreSQL 16、真实短信或生产运行证据，未重跑终态门禁。

## 2026-10-07 生命周期状态查询错误路径 no-store 收口

- 技术员允许的生命周期状态查询在成功、非法请求头、生命周期服务异常和数据库异常上统一返回私有 `no-store` 响应；数据库异常保持只读回滚，避免缓存错误投影或泄露历史状态。
- 聚焦回归 **9 passed, 1 deselected, 1 warning**；证据仅覆盖本地 ASGI/合成夹具，不替代 hosted PostgreSQL 16、正式 UAT 或部署上线。
- 该项属于试点 MVP 的状态查看安全边界收口，不扩大完整 V1；PG16 hosted DB 仍为外部阻塞，未重跑终态门禁。

## 2026-10-07 技术员状态查看例外显式固化

- 生命周期命令状态查询保留为技术员可见的申请/审批状态恢复入口；分配、占用和供给命令恢复仍要求 `admin`/`provincial_manager` 后台人工履约 capability。新增源码门禁锁定该例外不得误加 `_require_backend_fulfillment`，且必须保留 `no-store`。
- 聚焦回归 **6 passed, 1 deselected, 1 warning**：技术员状态查询返回 200、`no-store`，没有数据库提交/回滚；后台命令恢复拒绝回归仍保持 `403 fulfillment_forbidden`、`no-store` 和服务未调用。
- 这只是试点 MVP 的 capability/状态查看边界证据，不等同完整 V1；PG16 hosted DB 继续作为外部阻塞，未重跑终态门禁。

## 2026-10-07 源侧命令恢复 capability 前置收口

- 分配/占用命令恢复接口现在与候选、写入和后续履约路径一致，先验证 `admin/provincial_manager` capability，再解析 `X-Request-ID` 或访问服务；生命周期命令状态仍是技术员可见的申请/审批状态路径。
- 源码门禁扩展为 **24 条**后台后置路由；技术员 HTTP 负向回归和状态轴恢复组合 **7 passed, 8 deselected, 1 warning**，拒绝响应保持 `403`/`no-store` 且无服务调用或事务副作用。Python 编译和 `git diff --check` 通过。
- 这是试点 MVP 的权限旁路修复，不等同完整 V1；底层命令、幂等、审计和出库依赖保留，PG16 hosted DB 门禁继续记录为外部阻塞，未重跑终态门禁。

## 2026-10-07 试点命令恢复状态轴独立性契约

- 新增 HTTP 恢复回归，固定单次分配/占用事实状态与申请聚合状态轴的分层：响应可同时表达 `allocation_status=allocated` + `states.allocation_status=partially_allocated`，以及 `reservation_status=reserved` + `states.reservation_status=pending`；其余发运、收货、入账、通知和对账轴继续原样保留。
- 聚焦证据：`test_material_request_allocation_options.py -k 'command_status_keeps_fact_status_separate or router_allocation_command_status_is_read_only_and_no_store'` **2 passed, 8 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。没有外部写入、hosted PostgreSQL 16、短信或知识源证据，也未重跑终态门禁。
- 该项仅巩固试点 MVP 的状态投影契约，不等同完整 V1；PG16 hosted DB 门禁继续外部阻塞，后续迭代清单和 capability 隐藏边界不变。

## 2026-10-07 MVP 状态轴闭环契约

- 新增纯 schema contract `backend/tests/test_pilot_mvp_state_axes_contract.py`，锁定审批→分配→占用→出库/发运→本人收货→个人仓入账的最小投影序列。
- 聚焦证据 **3 passed**：发运可同时推进出库/发运/待收货三项相关事实；个人仓入账达到 `posted` 时，通知和 OAM 收货仍不被隐式改写；缺失或未知状态值拒绝。该证据不替代服务/HTTP/PG16 hosted DB/真实 UAT，未产生外部写入。

## 2026-10-07 试点 smoke 绑定运行时范围

- 候选发布包装器现在把 `SMOKE_EXPECTED_RELEASE_SCOPE=trial-mvp` 传给只读 smoke；smoke 会要求 `/api/health.release_scope` 精确匹配，发现“预检是试点、运行实例漂移到正式范围”时直接失败。未设置该变量的普通 smoke 不受影响。
- 聚焦证据：`tests/test_pilot_smoke_public_entry.py` **11 passed**；候选环境绑定回归 **1 passed, 65 deselected**；无容器、数据库、PNVS 或生产写入，未重跑已终态测试。
- 这是发布候选范围一致性证据，不替代 PG16 hosted DB、真实 PNVS、迁移、HTTP、PC/H5 或正式 UAT 门禁；当前版本仍明确为“试点 MVP，不等同完整 V1”。

## 2026-10-07 试点范围运行时健康投影

- 应用配置新增 `release_scope`（`production-v1` / `trial-mvp`），三条健康路径统一返回非敏感 `release_scope`；这样隔离候选启动后可由监控直接核对试点范围，而不会暴露凭据或业务数据。
- 聚焦证据：`backend/tests/test_health_readiness.py` **7 passed**，覆盖 ready/live、数据库/KMS 失败脱敏、`trial-mvp` 回显以及未知范围拒绝。无 hosted DB、KMS、PNVS 或生产写入；未重跑已终态测试。
- 这是部署前/持续运行的范围可观测性补强，不替代 PG16 hosted DB、真实 PNVS、迁移、HTTP、PC/H5 或正式 UAT 门禁；当前版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 PNVS 运行时请求边界复核（不等同真实渠道通过）

- `AliyunDypnsProvider` 的发送/核验请求继续绑定 `country_code=86`、签名、模板、方案和精确 `out_id`；provider 与 SDK 均关闭自动重试，运行时只允许一次调用并固定连接/读取超时。
- 聚焦证据：`backend/tests/test_sms_provider.py` **34 passed**。仅合成客户端和合成响应，无 PNVS、数据库、KMS、OSS 或生产写入；未重跑已终态测试。
- 这项只补齐运行时请求边界审计，不把真实 RAM、来源限制、隔离手机号发送/登录回读、投递运维或 hosted PostgreSQL 16 证据写成通过。短信真实渠道仍是后续迭代，当前版本明确为“试点 MVP，不等同完整 V1”。

## 2026-10-07 当前树复核：冻结为试点 MVP（不等同完整 V1）

- 复核前已完整读取正式基线、开发交接和本审计；当前工作树保持 `codex/notification-delivery-worker` / `fc7c926`，未 reset、revert、丢弃或提交未提交改动。该次记录的差异为 761 个文件；后续新增门禁、测试、默认凭证链和交接说明后，当前 `git status --short` 为 772 条，`git diff --check` 通过。
- 本次发布边界只承诺：申请/提交、审批、最小货源分配/占用、区域/总部后台人工履约并记录发运、本人收货、个人仓入账。技术员的后台后置操作区由 capability/角色整块隐藏，保留申请、状态查看、本人收货和本人入账；区域/总部仍保留必要后台人工履约能力。
- 不删除或降级底层状态轴、迁移、契约、出库依赖、不可变库存流水、幂等、审计、权限/安全约束、取消/关闭守卫和最小站内通知。履约后供给容量、0178 分配后建计划、释放后重分配、复杂历史恢复及其他异常/运营能力继续列为后续迭代；本轮不重跑已终态测试。
- PostgreSQL 16 hosted DB 门禁继续登记为外部阻塞；缺少同一源码集的可审计 hosted 运行证据时，不宣称迁移、ACL、并发、连接终止或正式上线通过。该范围冻结仅代表试点 MVP，不等同完整 V1。

## 2026-10-07 正式验收审计：有限里程碑边界

本次审计将当前候选的可验收业务链固定为：

`申请/提交 → 三级审批事实 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账`

| 审计项 | 当前结论 | 角色边界与证据口径 |
|---|---|---|
| 申请/提交、审批 | 保留在试点范围 | 技术员可以创建/提交并查看审批状态；审批事实逐行、版本化、可审计；不能把审批结果当作库存事实 |
| 最小分配/占用 | 保留在试点范围 | 仅 `admin`/`provincial_manager` 的后台 capability；事实状态与聚合状态轴分离，幂等恢复保持只读 |
| 人工履约并记录发运 | 保留在试点范围 | 后台使用已有出库依赖登记发运；拣货界面继续隐藏，物流/OAM 收货/通知仍是独立状态 |
| 本人收货、个人仓入账 | 保留在试点范围 | 技术员只走本人接口，重新核验本人、版本、包裹、SN/批次和保管责任；入账独立写不可变库存流水 |
| 技术员后置区 | 已冻结隐藏 | 不显示库存、供给、分配、占用、拣货、出库、发运、后台收货/入账、报表和运营入口；直达请求由后端 capability 拒绝 |
| 区域/总部后台 | 保留必要入口 | 只承担本里程碑所需的分配、占用和人工发运；后续能力不构成首发承诺 |

下列项目从本候选的验收结论中排除但继续保留底层实现、迁移和契约：履约后供给容量、0178 分配后建计划、释放后重分配、复杂历史恢复；拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维。

本节是范围冻结和正式验收审计记录，明确“试点 MVP，不等同完整 V1”；不重跑已终态测试，不把本地聚焦回归、健康探针、可见页面或历史 PG16 运行收据升级为当前 hosted DB 通过。PG16 hosted DB 门禁因缺少同一源码集的可审计 hosted 运行证据继续记为外部阻塞。

## 2026-10-07 试点短信预检坐标锁定（不等同真实渠道通过）

- 候选试点预检现在要求短信-only PNVS/Dypnsapi 配置同时匹配已审计的非敏感坐标：签名 `恒创联众`、模板 `100001`、方案名 `RSC个人仓登录`；provider 仍接受 `aliyun_dypns`/`aliyun_pnvs` 兼容别名。该门禁只防止配置漂移，不把密钥、RAM 最小动作、服务器来源限制、真实发送回执或隔离测试手机号登录回读写成通过。
- `.env.example` 只记录这组坐标的注释并保持短信关闭、签名/模板为空；示例文件不承担生产 Secret，也不构成真实 provider 成功证据。
- `.env.example` 同时显式写入正式默认范围 `OAM_RELEASE_SCOPE=production-v1`；试点脚本只在隔离候选运行时注入 `trial-mvp`，因此范围标记不会从示例环境漂移。
- 聚焦证据：`backend/tests/test_pilot_preflight.py -k 'valid_pilot_configuration_passes_without_external_io or pnvs_sts or rejects_unreviewed_sms_coordinates'` **6 passed, 61 deselected**。无短信、数据库、KMS 或生产写入，也未重跑已终态测试。
- PNVS 真实渠道和投递运维继续属于后续迭代/外部验收项；PG16 hosted DB 仍缺同一源码集的可审计运行证据，继续阻塞正式 V1 与上线声明。

## 2026-10-07 后置履约路由 capability 审计

- 当前源码 AST 审计覆盖正式需求路由中的 **24 条**后置履约路径：货源/占用、释放/取消/关闭、拒收/补偿、履约准备、拣货/出库、发运/物流、OAM 收货证据、后台收货与后台入账；全部在业务服务调用前执行 `_require_backend_fulfillment`，同时保留 `no-store` 和安全错误映射。
- 新增源码门禁 `test_formal_material_request_route_capability.py` **2 passed**：锁定 24 条后台路径和 9 条技术员本人收货/入账例外，防止后续新增路由绕过冻结边界。
- `my-receiving`、`my-receipts`、`my-inbounds` 及需求状态读取是技术员允许的独立路径，没有被后台守卫覆盖；本人身份、需求版本、包裹/出库/SN/保管责任核验仍在各自服务中。此项是当前源码审查证据，不替代 HTTP/UAT 或 PG16 hosted DB 运行证据，也未重跑终态测试。

## 2026-10-07 客户端 capability 门禁与试点产物复核

- `backend/tests/test_client_release_workflow_scope.py` **3 passed**：验证试点/正式分支目录门禁条件，并锁定 Web、小程序和 `/xx` 私有构建步骤不会从 CI 消失。
- `pnpm run verify:pilot-release` 通过，输出 `scope=private pilot artifact only`、`publicCatalogIsReady=false`、`catalog.status=pending`、`records=0`；该结果只证明私有试点产物边界，不能替代公共目录、短信、PG16 或正式 UAT。
- 本轮未连接数据库、短信 provider 或生产环境，未提交、未部署；PG16 hosted DB 和真实 PNVS 回执继续按外部阻塞记录。

## 2026-10-07 公共首页后台入口目标纳入部署 smoke

- 公共 bundle 的部署前 smoke 现在同时要求“星星后台管理”文字和精确目标 `https://rscwz.cn/xx`；防止首页文字保留但个人仓入口被错误改写。
- `backend/tests/test_pilot_smoke_public_entry.py` 在新增目标缺失负向回归后 **10 passed**。这是本地只读 HTTP 夹具证据，不是生产页面、短信、数据库或正式 UAT 证据。
- `verify_public_entry.mjs` 静态产物门禁同步通过，并继续报告 `status=pending`、`records=0`；该命令没有把公共知识目录 pending 误报为正式 release 通过。
- 本项只收口公开首页到二级域名入口的发布边界；试点 MVP 范围、技术员后置区隐藏、PG16 hosted DB 和真实 PNVS 阻塞状态不变，未重跑已终态全套测试。

## 2026-10-07 当前源码 warehouse 产物重建

- 发现旧 `dist-warehouse` 仍是 capability 修复前的 bundle，省负责人导航未体现技术员硬门槛；旧产物已不再作为当前发布证据。
- 基于当前源码重新执行 `pnpm run build:warehouse`，`tsc -b` 通过、Vite 转换 **2002 modules**；新 bundle 的导航及 `/provincial-managers` 路由都保留 `!technicianOnly` 守卫。
- 新产物通过 `verify_public_entry.mjs` 静态边界检查；未重跑终态业务测试、未连接 PG16/PNVS、未提交或部署。

## 2026-10-07 技术员误授角色管理权限的前端守卫

- 修复前端 capability 漏洞：`role_assignment/manage_provincial` 即使被错误授予技术员，也不再显示或允许访问“省负责人”页面；导航和路由都以 `technicianOnly` 作为硬门槛。
- `App.test.tsx` **44 passed**，其中包含带误授角色管理权限的技术员回归；`tsc -b` 通过。该项只收紧首发可见面，不删除角色管理底层权限或状态事实。
- 未产生外部写入、数据库迁移或部署；PG16 hosted DB、PNVS、公共目录和正式 UAT 状态不变。

## 2026-10-07 技术员整站 capability 收口与首发验收边界

- 技术员-only（无 `admin`/`provincial_manager`）的首发可见面收敛为首页状态投影、申请/提交、状态查看，以及需求详情内的本人收货和个人仓入账；库存、通知、盘点/期初盘点、报损/报废、退回、对账和其他后台履约路由均按 capability 隐藏，直接访问时回首页。正向回归同时锁定可见导航集合只有“首页”和“需求提报”。
- 区域/总部角色仍保留申请审批后的最小货源分配/占用和人工履约发运入口；底层分配、占用、出库、收货、入账状态轴、迁移、契约、不可变流水、幂等、审计、权限/安全约束、取消/关闭守卫和最小站内通知继续保留。隐藏 UI 不构成删除后置能力或放宽后台权限。
- 验收证据：`tsc -b` 通过；`pnpm run build:warehouse` 通过（2002 modules）；前端 capability 聚焦组 **71 passed**（5 个测试文件），其中技术员直达后置路由守卫 **20 passed**；最新仓库安全门禁 **3060 candidate files / 75702215 bytes，PASS**。本轮没有重跑已终态测试，也没有外部系统、数据库、短信或部署写入。
- 拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维继续作为后续迭代；履约后供给容量、0178 分配后建计划、释放后重分配、复杂历史恢复也不进入本次首发。当前版本明确为“试点 MVP，不等同完整 V1”。PG16 hosted DB 门禁继续是外部阻塞。

## 2026-10-07 小程序公开包边界验收

- 当前 `miniprogram/app.json` 仅注册 `pages/knowledge/index`；个人仓登录、需求、本人收货/入账、扫码、盘点、通知和退回源码继续保留但通过 `packOptions.ignore` 排除，避免个人备案首页暴露业务登录和后置履约入口。
- 公开页只读本地知识目录，不读取 `/auth`、会话或业务接口；目录尚未导入时明确显示待更新。该验收与 PC/H5 的 `rscwz.cn/xx/` 私人入口边界一致。
- `public-knowledge.test.js` 的公开包隔离测试可复核；未进行真实开发者工具/真机 UAT，公共目录 `pending/0` 和严格发布门禁仍保持阻塞。

## 2026-10-07 发运目标 HTTP 权限契约补强

- `ShipmentError` 已补齐统一 HTTP 状态码和安全 detail 映射；技术员请求 `GET /api/v1/material-requests/{id}/shipment-targets` 现在稳定返回 `403 fulfillment_forbidden`，不会因异常转换缺口泄漏为 500，且继续带 `Cache-Control: no-store`。
- 本次只修复边界错误契约并加入 HTTP 回归，不改变申请、审批、分配/占用、发运、收货或个人仓入账事实，也没有外部写入或迁移。
- 聚焦证据：`test_material_request_my_receiving.py -k 'shipment_targets or technician_cannot_read_backend_shipment_targets'` **8 passed, 28 deselected, 1 warning**；该结果覆盖服务层拒绝、后台只读/no-store/405 和技术员 HTTP 403 无突变。没有重跑已终态全套测试。
- 该项属于试点 MVP RBAC/后台履约契约收口，不等同完整 V1；PG16 hosted DB、PNVS、部署和正式 UAT 仍未通过。

## 2026-10-07 发运后端读路径 RBAC 收口

- 发运候选、发运历史和发运命令恢复的服务层与 HTTP 层均统一限制为 `admin/provincial_manager`。技术员直接请求这些路径会得到 `403 fulfillment_forbidden`，不会因写密钥缺失退化为 503，也不会返回可缓存的权限失败响应。
- 聚焦证据：`test_material_request_my_receiving.py -k 'shipment_targets or technician_cannot_read_backend_shipment_paths'` **14 passed, 28 deselected, 1 warning**；受影响的 `test_material_request_shipment.py` **6 passed, 1 warning**。测试未产生库存、审计、通知或迁移写入。
- 该项仅强化试点 MVP 的角色/能力边界；申请、审批、分配/占用、后台人工发运、本人收货和个人仓入账状态轴未改变，完整 V1 和外部门禁仍未完成。

## 2026-10-07 物流事件读路径 RBAC 收口

- 物流事件列表和命令恢复服务层/HTTP 层均限制为 `admin/provincial_manager`；技术员直连在写密钥检查前得到 `403 fulfillment_forbidden` 和 `no-store`，物流事件状态轴仍与收货、入账分离。
- 证据：技术员物流路径 **4 passed, 42 deselected, 1 warning**；物流契约 **4 passed, 1 warning**。轻量只读 principal 兼容测试保持通过。

## 2026-10-07 后台收货/入账读路径 RBAC 收口

- 后台收货列表、收货命令恢复和后台入账列表仅对 `admin/provincial_manager` 开放；技术员继续通过独立本人接口收货和入账，前端隐藏与后端拒绝一致。
- 证据：技术员后台收货/入账路径 **6 passed, 46 deselected, 1 warning**；收货恢复 **16 passed, 1 warning**；入账过账 **29 passed, 1 warning**。未产生库存、审计、通知或迁移写入。
- 这两项仍属于试点 MVP 的权限边界收口，不等同完整 V1；PG16 hosted DB、PNVS、部署和正式 UAT 继续阻塞。

## 2026-10-07 OAM 收货证据读路径 RBAC 收口

- OAM 收货证据查询仅对 `admin/provincial_manager` 开放；技术员的本人收货/个人仓入账接口不受影响，外部 OAM 证据仍保持独立只读状态轴。
- 证据：技术员路径 **1 passed, 52 deselected, 1 warning**；既有 OAM HTTP 契约 **1 passed, 1 warning**。未产生库存、审计、通知、迁移或 OAM 写入。
- 该项继续属于试点 MVP 的后置区权限收口，不等同完整 V1；PG16 hosted DB、PNVS、部署和正式 UAT 仍未通过。

## 2026-10-07 后置履约源侧 HTTP 旁路收口

- 发现并修复源侧后置接口的 capability 旁路：履约准备、释放/拣货/出库候选、源侧剩余履约、供给容量及命令恢复路由不再只依赖普通需求读取权限，统一要求 `admin/provincial_manager`。技术员直连在密钥/数据库/业务查询前返回 `403 fulfillment_forbidden` 和 `no-store`。
- 这只收紧首发 MVP 的后台人工履约边界，保留底层分配、占用、释放、拣货、出库、供给容量、状态轴、命令历史和不可变流水；本人收货/个人仓入账契约未改变。
- 聚焦证据：技术员源侧候选/恢复及取消、关闭、拒收退回、退回补偿路径 **38 passed, 54 deselected, 1 warning**；释放、拣货、出库及供给容量 HTTP 契约 **13 passed, 102 deselected, 1 warning**；相关取消/关闭/拒收退回/退回补偿 HTTP 契约 **53 passed, 1 warning**。无库存、审计、通知、迁移或外部写入；PG16 hosted DB 门禁仍为外部阻塞。

## 2026-10-07 本人收货/个人仓入账契约复核

- 技术员首发链路继续限定为 `my-receiving`、`my-receiving/{shipment_id}/candidates`、`my-receipts` 和 `my-inbounds`；每次读取或写入都重新核对当前本人、需求版本、包裹目标、保管责任、出库/SN/批次事实与授权版本。后台收货、后台入账和 OAM 证据接口已单独守卫，不能通过隐藏 UI 绕过。
- `command-status`/`trace-status` 只读恢复、`no-store`、幂等和未知结果禁止重放继续有效；个人仓入账与本人收货保持独立不可变库存流水，拒收/异常数量不会自动进入个人仓。
- 聚焦证据：本人收货、收货候选和本人入账候选测试合计 **104 passed, 1 warning**。本轮未重跑已终态全套测试，也未产生外部写入、迁移或 hosted DB 证据；PG16 hosted DB 门禁仍为外部阻塞。

## 2026-10-07 后置履约 HTTP 前置拒绝统一

- 分配/占用候选与写入、发运/物流、后台收货/入账、OAM 收货证据及供给任务 HTTP 路由现在都在请求头解析、运行时密钥读取和业务查询之前执行 `fulfillment_forbidden`；拒绝响应统一 `403` 和 `no-store`。服务层的角色、库存读取、组织范围和事实核验继续保留。
- 技术员本人收货/本人入账和需求状态读取不受影响；底层状态、迁移、审计、通知、幂等和库存流水没有删除或改写。
- 聚焦证据：技术员/源侧/后置路径及所有后台写入口前置拒绝 **59 passed, 34 deselected, 1 warning**；发运目标及后台路径 **26 passed, 66 deselected, 1 warning**；`test_material_request_completion_http.py -k 'http'` **2 passed, 1 warning**，确认完成数量状态读取仍可用；释放/拣货/出库/供给容量 **13 passed, 102 deselected, 1 warning**；取消/关闭/拒收退回/退回补偿 **53 passed, 1 warning**。未产生库存、审计、通知、迁移或外部写入。

## 2026-10-07 前端 capability 与公开首页聚焦复核

- `FormalMaterialRequests.test.tsx -t "renders only masked list/detail projections and keeps approval/fulfillment axes separate"` **1 passed, 76 skipped**：技术员详情隐藏整个后置履约操作面板，仍保留“本人收货与入账”和状态轴读取；手机号、地址继续掩码。
- `PublicKnowledge.test.tsx -t "opens the public homepage without authentication"` **1 passed, 3 skipped**：公共首页不读取会话、不发网络请求、不渲染登录/验证码/个人仓入口；“星星后台管理”链接准确指向 `https://rscwz.cn/xx`，备案链接保持可见。
- 这是试点 MVP capability/公开入口的当前聚焦证据，不扩大为完整 V1、真实 UAT 或上线通过；未重跑已终态全套测试，PG16 hosted DB 门禁仍为外部阻塞。

## 2026-10-07 试点前端构建与发布范围复核

- 当前前端 `tsc -b` 与 `pnpm run build:warehouse` 均通过（Vite 转换 2002 modules）。这只证明当前源码可构建，不替代浏览器 UAT、远端 CI 或生产部署。
- `pnpm run verify:pilot-release` 通过，输出 `scope=private pilot artifact only`、`publicCatalogIsReady=false`、`catalog.status=pending`；发布范围和公共目录未就绪状态均被机器门禁明确记录。
- 严格 `pnpm run verify:release` 当前按设计失败于 `PUBLIC_CATALOG_NOT_READY: import and verify the source before release`。这是真实的公开目录门禁失败，不伪造完整发布通过；公开知识源按当前优先级继续后置，私有试点范围不因此扩大。
- 没有容器、数据库、短信、外部知识源或生产写入；PG16 hosted DB、真实 PNVS 回执、公共知识源和正式 UAT 仍未通过，试点 MVP 仍不等同完整 V1。

## 2026-10-07 CI 试点分支与完整公共发布门禁分层

- `client-release-gate` 现在对试点分支只执行公共首页/`/xx` 产物边界检查；完整 `--release` 公共目录门禁继续限定于 PR、`main` 和 `codex/production-readiness-gates`。因此私有试点不会被低优先级的知识源 pending 误阻断，正式发布仍不能绕过目录审核。
- 当前无参数 `verify_public_entry.mjs` **通过**；带 `--release` 的同一脚本仍因 `PUBLIC_CATALOG_NOT_READY` 失败，正式发布阻塞证据保持有效。
- `backend/tests/test_client_release_workflow_scope.py` **3 passed**，验证 CI 分层条件、当前分支触发以及 Web/小程序/`/xx` 构建步骤保留；这只改变 CI 入口分层，不改变产品权限、短信真实性或 PG16 hosted DB 门禁。

## 2026-10-07 试点发布范围机器门禁

- API Compose 的正式默认范围为 `OAM_RELEASE_SCOPE=production-v1`；候选发布脚本只为试点准备阶段注入 `OAM_RELEASE_SCOPE=trial-mvp`。因此“试点 MVP”不再只存在于文档口径，也进入候选配置边界。
- `pilot_preflight` 现在强制检查 `pilot_mvp_scope`，并在只读输出中记录 `pilotScope=trial-mvp`；传入 `formal-v1` 等范围会在容器启动前失败。该检查是范围门禁，不宣称完整 V1 已实现。
- 证据：`test_pilot_preflight.py` 的有效试点配置与非 MVP 范围拒绝 **2 passed, 62 deselected**；`test_pilot_deploy.py` 的生产 Compose 范围标记隔离与迁移容器契约 **2 passed, 37 deselected**；Python 编译、`git diff --check` 和仓库安全门禁通过。没有容器、数据库或业务写入；PG16 hosted DB、PNVS 真实回执和上线 UAT 仍为独立未完成门禁。

## 2026-10-07 MVP 发运状态投影复核

- 审计中较早记录的“Shipment 已发运但需求 `shipment_status` 仍为 `not_started`”属于 0168 前历史缺陷。当前树已由 `record_fulfillment_command` 在发运命令同事务写入 `shipment_status='shipped'`；收货、OAM 收货、个人仓入账和通知继续保持独立状态轴。
- 0168 迁移、readiness、身份/因果守卫及旧历史升级仍在树中；既有 `test_material_request_shipment.py`、分包投影测试和数量/SN PG16 记录 64774、43754、47363、94910 提供了当前修复的终态证据。本轮只做源码与记录核对，未重跑已终态测试。
- 这只解决 MVP 状态投影缺口，不等同完整 V1、真实 UAT 或上线；当前发布版本仍需可审计 PostgreSQL 16 hosted DB 证据。

## 2026-10-07 MVP 发运目标候选收口

- 本版本新增只读发运目标候选契约，仅对 `admin/provincial_manager` 后台履约角色开放；服务端从申请人的当前有效个人仓绑定解析唯一目标，并复核组织路径、叶子位置和当前保管责任；空、重复或不一致证据均保持阻断，不猜测 UUID。
- 前端发运区在候选接口可用时使用“目标个人仓”下拉框，并在提交前重新读取目标候选和需求版本；旧适配器仍可走人工坐标兼容路径。该改动没有删除 `Shipment` 目标字段，也没有改变收货/入账状态轴。
- 当前证据为后端 **6 passed / 28 deselected / 1 warning**（含 HTTP 只读、`no-store`、POST 405 和技术员拒绝回归）、前端 **22 passed**、受影响 Python/TypeScript 编译和 `git diff --check` 通过；证据均为本地只读/合成夹具，不替代 PG16 hosted DB、真实 PNVS、部署或正式 UAT。

## 2026-10-07 试点 MVP capability 对齐（不等同完整 V1）

- 本次冻结的首发链路仍为：申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员只保留申请、状态查看、本人收货和本人入账；区域/总部的后台履约能力继续受 capability 控制。
- `can_read_allocation_options` 的前端投影已与后端货源目录守卫对齐：必须同时具备 `admin/provincial_manager` 角色和 `inventory:read` 权限。显式失去库存读取权限的账号整体隐藏后置履约区，避免界面暴露无法通过 API 权限核验的操作；底层状态、迁移、契约、出库依赖和安全事实未删改。
- 聚焦证据：`formalMaterialRequestAdapter.test.ts` capability 投影与读取依赖 **2 passed, 19 skipped**；后端 `test_material_request_allocation_options.py` 的技术员货源目录拒绝及过期明细版本守卫 **2 passed, 7 deselected, 1 warning**；TypeScript、`build:warehouse` 生产构建、`git diff --check` 通过。按要求不重跑已终态测试；该证据仅覆盖 capability/RBAC 对齐，不扩大为完整 V1 验收。
- 后续清单保持：履约后供给容量、0178 分配后建计划、释放后重分配、复杂历史恢复、拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维。PG16 hosted DB 门禁继续作为外部阻塞，不以 SQLite、历史隔离库或静态检查伪造通过。

## 2026-10-06 试点 MVP 冻结边界（不等同完整 V1）

- 本次正式验收审计采用试点 MVP 口径：申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员首发只保留申请、状态查看、本人收货和本人入账；区域/总部保留必要后台履约能力。
- 当前前端已以 `can_read_allocation_options` 隐藏技术员的整个后置操作区，包括供给计划、分配/占用、释放、拣货、出库、发运准备、OAM 收货、后台收货验收、后台入账、拒收退回、退回补偿、剩余取消和业务关闭；本人收货与个人仓入账仍可用。后置恢复坐标只保留通用阻塞提示，详情读取仍可用，后台账号按原恢复流程核验。
- 冻结只改变首发 capability/角色可见范围，不删除底层状态、迁移、契约、出库依赖、不可变库存流水、幂等、审计、权限/安全约束、取消/关闭守卫和最小站内通知。履约后供给容量、0178 分配后建计划、释放后重分配、复杂历史恢复，以及拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维均为后续迭代。
- 本轮 focused evidence：`FormalMaterialRequests.test.tsx` capability/恢复相关 **16 passed, 61 skipped**；TypeScript 增量检查及 `build:warehouse` 生产构建通过，覆盖技术员隐藏后台收货/入账面板但保留本人面板，以及恢复阻塞时仍可查看详情。未重跑已终态测试，且该证据不扩大为完整 V1 验收。
- PostgreSQL 16 hosted DB 门禁继续记录为外部阻塞：当前没有可审计 disposable PG16 runtime，迁移/ACL/并发/连接终止证据未通过，不以 SQLite、历史隔离库或静态检查替代。


## 2026-10-06 已完成视觉里程碑（未完成上线门禁）

- 当前工作树已核对出该视觉里程碑对应的产品结构：PC 正式工作台在 `frontend/src/App.tsx` 提供深青/青橙主视觉、欢迎区、正式身份验证、角色/组织/权限版本卡和只读正式库存账卡；`Shell` 保留五项底部导航。
- 微信小程序在 `pages/home/index.wxml` 保留“我的物资/个人仓”和“统一扫码”双入口；`pages/formal-scan/index.wxml` 使用大号“开始扫描”按钮，扫码只读、安全约束和权限回验仍由原 JS/adapter 执行。
- 当前角色隐藏边界保持：`FormalMaterialRequests.tsx` 以 `can_read_allocation_options` 隐藏整个后置履约区，技术员仍只保留申请、状态、本人收货和个人仓入账；API、状态轴、不可变流水、幂等和审计未改。
- 用户提供的视觉验证证据为 Web 63 个测试文件/1074 个测试、小程序 23 个测试、生产构建成功、`git diff --check` 通过。本轮不重跑这些已终态结果；它们只证明视觉里程碑，不证明 PNVS 真实回执、PG16 hosted DB、部署或生产上线。构建遗留 JS chunk 体积提示列为后续优化。



## 2026-10-06 部署前预检补强

- `scripts/pilot_preflight.py` 现在明确检查 `OAM_WECHAT_LOGIN_ENABLED=false`，并验证独立登录限流 HMAC、窗口、全局/IP/身份限额均为合法有界数字；PNVS provider 仍只接受 `aliyun_dypns` 或兼容别名 `aliyun_pnvs`。
- 新增聚焦预检证据：`tests/test_pilot_preflight.py -k rejects_wechat_login_and_invalid_sms_limits`，**1 passed, 62 deselected**。该检查只解析合成 Compose，不启动容器、不连接 PG/KMS/OSS/SMS。
- 这只是部署配置门禁，不是 PNVS 真实发送/回执或 PostgreSQL 16 hosted DB 通过证据。

## 2026-10-06 仓库安全门禁收口

- `scripts/verify_repository_safety.sh` 首轮发现 3 个个人 macOS 路径命中：PG16 测试夹具的本地二进制路径和两份交接/验收文档的工作树路径；未发现凭证、运行数据或受保护目录泄露。已改为相对测试路径及“当前工作树”文档表述，保留测试与审计证据。
- 当前重跑门禁通过：**3057 candidate files，75,567,186 bytes，protected local paths ignored**；`.env.example` 短信-only/微信关闭静态核对及 `git diff --check` 通过。
- 这是仓库安全证据，不等于正式版本 CI、PG16 hosted DB、PNVS 真实回执、部署或生产 UAT 通过。
- 当前源码负向扫描通过：网页/小程序登录 UI 不展示微信入口；策略层阻断旧微信路径；后端历史路由在数据/provider 访问前拒绝密码、改密和微信；正式需求、拣货和出库面板均以 `can_read_allocation_options` 作为后置能力门槛。仅作静态边界证据，仍需真实页面/UAT。

## 下一有限里程碑：PNVS 与 PG16 门禁准备清单

- PNVS 非敏感参数已核对：`dypnsapi`、`SendSmsVerifyCode`/`CheckSmsVerifyCode`、CountryCode `86`、签名 `恒创联众`、模板 `100001`、变量 `code`/`min`、方案名沿用 `RSC个人仓登录`。密钥只能进入服务器环境变量或 Secret。
- 待验收项按顺序保留：独立 RAM 身份及最小动作 `dypns:SendSmsVerifyCode`、`dypns:CheckSmsVerifyCode`；服务器来源限制；隔离测试手机号；发送回执与 `out_id` 精确回读；验证码核验和登录回读；限流、审计、未知结果禁止重放。1000 条额度只作 provider 额度记录，不在代码硬编码余额。
- 当前适配器还没有真实回执/查询证据，不能打开生产流量；完成前保持 `OAM_SMS_LOGIN_ENABLED=false` 或 provider readiness false。
- PG16 hosted DB 准备项：用户提供可审计 disposable PostgreSQL 16 实例；同一工作树/源码证据运行迁移、运行角色 ACL、并发、连接终止和回滚；保存实例版本、SHA、日志和退出码。SQLite、本地历史隔离库和静态检查不能替代 hosted DB 门禁。

## 2026-10-06 SMS-only 认证里程碑（真实回执与隔离号码 UAT 前不宣称上线）

- 网页与小程序认证入口统一为手机验证码；登录选项仍保留历史 `password_enabled`/`wechat_enabled` 字段以兼容旧客户端，但服务端恒返回 `false`，客户端不再显示密码、改密、微信或模式切换入口。
- `/auth/login`、`/auth/miniprogram/password-login`、`/auth/change-password` 与 `/auth/miniprogram/wechat-login` 保留路由/数据契约供迁移兼容，但统一在访问用户数据或外部 provider 前以非枚举方式拒绝；历史 `password_hash`、`require_password_change`、微信绑定字段不删除。
- 配置层对 `password_login_enabled=true` 或 `wechat_login_enabled=true` 在任何环境都失败关闭；生产 API 只接受完整短信通道，`sms_configuration_ready()` 同时要求真实 provider、签名/模板/方案、凭证和独立登录限流 HMAC。未完整配置时 `/auth/login-options` 不开放短信，发送/登录也拒绝。
- 已确认使用阿里云号码认证服务 PNVS/Dypnsapi（不是 Dysmsapi），现有短信认证套餐余量 1000 条；适配器调用 SendSmsVerifyCode/CheckSmsVerifyCode。额度不硬编码余额，仍须由用户完成方案/签名/模板、RAM 最小权限、来源限制、真实隔离号码回执和登录回读，未完成前不能宣称短信 UAT 或上线。服务端不写入密钥、不伪造短信、不自动重放超时未知请求；现有幂等、限频、一次性验证码、审计、会话刷新/退出/撤销/恢复约束保留。
- 本轮只做源码静态/聚焦配置与负向路由验证；不重跑此前已终态的认证、前端或完整门禁测试。PG16 hosted DB 仍是外部阻塞，未提交、未部署。
- 新增证据：`cloud_oam/backend/tests/test_sms_only_policy.py` 使用现有 `.venv` 聚焦运行 **7 passed, 1 warning**；`cloud_oam/miniprogram/tests/sms-only-policy.test.js` 通过；前端 TypeScript 增量检查通过；`git diff --check` 通过。初次 shell 缺少 `node`/`pytest` 仅是工具入口问题，未安装依赖；没有重跑既有终态测试。

## 2026-10-07 SMS-only 客户端一致性收口

- 密码停用页、管理员设置页及客户端 README 的残留微信登录文案已统一为仅手机验证码；“微信小程序”仅指客户端平台，不重新开放微信认证。
- 历史 `/auth/users` 创建实现已在临时密码写入前硬拒绝，保留请求模型、密码字段和迁移契约；生产旧写接口阻断不变。
- 聚焦守卫 `tests/test_sms_only_policy.py -k historical_admin_user_provisioning` 通过：**1 passed, 7 deselected, 1 warning**；Python 编译、前端 TypeScript 增量检查、小程序受影响 JS 语法和 `git diff --check` 通过。
- 该项只收紧源码边界，不证明 PNVS 真实回执、PG16 hosted DB、真实页面/UAT、部署或正式上线。

## 2026-10-07 公开首页与个人仓入口边界复核

- `scripts/bootstrap_env.sh` 的生产提示已改为 SMS-only PNVS/Dypnsapi，避免把微信登录作为可用渠道。
- 静态产物边界通过：公开构建不含个人仓认证客户端；个人仓构建与 manifest 仅使用 `/xx/`；小程序注册页仅为 `pages/knowledge/index`。
- `verify_public_entry.mjs --release` 当前明确失败于 `PUBLIC_CATALOG_NOT_READY`：知识目录仍为 `pending`/0 条，真实飞书源尚未导入和核对。未伪造数据；该失败作为公开首页内容门禁记录，不等同个人仓代码失败。

## 2026-10-07 部署 smoke 门禁收紧

- `scripts/smoke_test.sh` 已收紧为 `/xx/` 私有路径，并要求登录选项为 SMS-only：`sms_enabled=true`、`password_enabled=false`、`wechat_enabled=false`。微信-only 配置现在被拒绝。
- `tests/test_pilot_smoke_public_entry.py` 聚焦验证 **9 passed**；shell 语法与差异检查通过。该证据来自 loopback 合成站点，不是线上 HTTP、真实短信或生产 UAT。

- 首发只承诺：申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员按 capability 只看到申请、状态查看、本人收货和本人入账；区域/总部保留必要后台人工履约区。
- 个人仓端已隐藏整个后置后台区块，包括供给计划、分配/占用、释放、拣货、出库、发运准备和 OAM 收货操作；本人收货与个人仓入账仍保留。底层状态轴、迁移、契约、出库依赖及安全守卫不删除、不降级。
- 履约后供给容量、0178 分配后建计划、释放后重分配和复杂历史恢复移出本次首发，列为后续迭代；后续清单还包括拒收/退回补偿、stock-return 全链、工单消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维。
- 此冻结是发布范围控制，**试点 MVP 不等同完整 V1**。PG16 hosted DB 门禁仍为外部阻塞；不重跑已终态测试，也不以 SQLite 或历史隔离结果冒充 PostgreSQL 16 证据。
- 冻结后的页面改动已通过 TypeScript 增量检查；此前 v80 的前端测试证据对应冻结前源码，按用户要求不重跑已终态测试，因此不能把 83 项历史结果扩大解释为冻结后全套回归。

## 2026-10-06 v80 当前审计：履约后供给证明与个人仓拣货界面边界

- 额度恢复后继续使用原工作树和分支，完整保留未提交改动。后端新增履约后供给历史证明：`remaining_fulfillment` 先核验预约、释放、拣货、出库、发运、签收、个人仓入账等不可变事实，供给历史再核对后续命令帧；新增计划仍按批准/取消/实际分配/活动计划计算，释放不会冲回原分配。供给计划更新也经过容量证明。
- 个人仓权限 (`can_read_allocation_options=false`) 下，正式需求页不渲染库存拣货区，也不自动恢复或提交拣货；人工/后台 `StockReservationPick`、`pick_id` 与出库链路保留。仓库操作权限的原拣货组件测试继续通过。
- 当前证据：后端容量/HTTP 28 项通过；供给、命令恢复、剩余履约、预约/释放扩展回归 183 项通过、1 项跳过；前端拣货与正式需求聚焦 83 项通过；`pnpm run build:warehouse` 通过；`git diff --check` 通过。
- PostgreSQL 16 门禁未通过：迁移 ACL/并发/连接终止门禁因缺少明确确认的 disposable hosted DB 而失败，本地没有 `postgres`/`pg_config`。因此迁移运行时、权限、并发和 kill 证据仍为未完成；没有提交、部署、切流或生产写入。
- 未完成范围保持：人员调拨/离职交接、真实短信/微信/飞书/OSS、持续 PC/H5/小程序、真人 UAT、远端全套 CI、500 用户压测、历史期初及三日对账、RPO/RTO/回滚与正式发布授权。

## 2026-10-06 停止交接：额度剩余2%，按用户指令暂停

- **停止原因**：实时额度usedPercent=98、remainingPercent=2，已达到用户明确停止线。本轮只读复核后补齐接续约束，未开始新迁移或产品代码修改；不重置/购买额度，不将完整目标标记完成。恢复须由用户明确要求。
- **工作位置**：当前工作树；分支codex/notification-delivery-worker。正式迁移head20261227_0178。保留全部未提交改动，禁止reset/revert/丢弃；未提交、推送、部署、服务器写入或切流。
- **最后已完成证据**：v78公开容量数量/SN84405 exit0；v79实际页面首次创建后同单履约、入账1+取消1+独立关闭，数量82301和SN37739均exit0。对应run-za7veptd、run-j6ghbjd6、run-ct88orm9、run-x6_oe4w4在停止时再次核对均stopped/passed/serverExitCode0。v79的2788项源hash仍与当前工作树完全一致；只有文档继续更新。最终收据supply-create-final-v79-20261006.json和quota-stop-v79-20261006.json。
- **进程/页面**：本轮未启动测试或服务；以上过程均终态，不再轮询或重启。先前33810/33537也已终态，33537是已记录的旧帮助器断言失败，不能计通过。临时IAB测试页和本轮隔离PG均关闭；手机/电脑截图为联验产物，不是持久在线验收地址。
- **恢复后的第一步**：完整读取正式基线及本交接，检查当前diff与额度，复用v79证据。接着按docs/SUPPLY_AFTER_ALLOCATION_OPERATIONS.md末节推进占用/释放/发运后的供给管理及历史恢复。已实证未占用不等于未分配：批准2/分配1尚未占用时unreserved=2，而新增计划只能1；不得直接拿remaining_fulfillment.unreserved_qty当新增计划额度，不得把释放量从原分配量中减去。接续节列出可复用的历史校验入口及前向迁移/SQL反例/同单HTTP浏览器验收要求；这部分尚未实现。
- **完整范围保持**：正式人员调拨、离职交接清零/双方确认/区域总部复核仍有实际缺口；持久PC/H5、真实短信/微信/飞书/OSS等渠道与身份、全基线UAT、当前全后端和远端CI、500用户压测、历史与期初迁移、真实三日对账、RPO≤5分钟/RTO≤2小时及回滚演练、书面验收与发布授权仍待证。最新范围表位于FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md顶部，旧日期表已标明历史。
- **云端状态**：用户授权接管现有Chrome命令助手，目标Ubuntu-ipvk/118.31.37.87；最近实时打开目标命令助手被重定向至阿里云登录页，需用户在该Chrome完成登录。只阻断云端，不代表整个开发失败；没有服务器写入。用户无需发送密码、验证码或密钥。


## 2026-10-06 正式V1范围复核（v79期间）

本表区分已核实的局部交付与完整上线要求。后面的旧日期段落保留历史证据，不能用其中的“当前”“最新”覆盖本表及顶部最新交接。没有同版通过证据的项目继续记未完成，不用已有菜单或测试数量推算完成率。

| 基线范围 | 当前直接证据 | 下一项完成标准 |
|---|---|---|
| 1.7/1.8 分配后补货、履约闭环 | 0178与v78公开容量数量/SN门禁已封存；v79数量浏览器首次创建后同单入账1、剩余取消1及关闭通过；v80本地真实释放/履约容量回归通过 | 在同一源码集取得 PG16 运行时、后续履约 HTTP/页面组合与历史恢复证据 |
| 1.7/1.8 后续供给管理 | v80 已接入 `remaining_fulfillment` 不可变事实核验和后续履约命令帧；新增量仍按批准/取消/实际分配/活动计划计算，释放不冲回原分配 | PG16 SQL 反例、迁移/权限门禁及同单占用→释放/发运→供给恢复证据；不能将原分配减释放当作已撤销分配 |
| 1.6/1.11 人员调拨与离职交接 | `main.py`中的旧`transfers`只在非生产挂载；`CustodyAssignment.handover_case_id`仍为无FK预留；本轮app/alembic精确搜索未找到`employee_handover_cases/checks` | 正式双方确认、分批移交、保管责任、未结工单/库存检查、清零及区域/总部复核；不能宣称旧调拨页已满足V1 |
| 1.4 登录身份与真实渠道 | 已冻结为 SMS-only；密码/微信入口负向拒绝，PNVS/Dypnsapi 适配器已确认但真实隔离号码回执尚缺 | 真实 provider 接口/隔离号码回执、唯一映射、限频/幂等/审计、设备会话及撤权验收；不能把配置当作短信 UAT |
| 1.2/1.3 PC/H5与小程序 | **视觉里程碑已完成**：深青欢迎区、身份/角色/组织/权限卡、库存账卡/表格、个人仓/扫码双入口和扫码大按钮已在当前工作树对应结构中；用户报告 Web 63 文件/1074 测试、小程序 23 测试及生产构建通过 | 视觉交付不等于上线；仍需同版 SMS-only PNVS 登录、角色验收、持续环境、真实 UAT、PG16 hosted DB 和部署证据；chunk 体积提示后续优化 |
| 1.5 OAM只读与历史迁移 | 本轮未执行外部采集或导入 | 完整授权范围的数量/hash/附件核对、期初实盘、两次演练和真实连续三日对账；凭据留本地 |
| 1.6/1.9/1.10 库存、工单与盘点 | 已有独立实现与历史门禁，本轮未重新完成其全部验收 | 当前源码完整检查、多人并发、SN/批次/流水重建、关单未结检查及真人UAT |
| 1.11 报损、报废、退回与纠正 | 已有v69-v71等独立HTTP/浏览器与数据库证据；不使用下方旧候选表倒退描述实现 | 同一发布版本集成回归、真实证据文件和业务UAT；局部轨迹不能代表所有异常组合 |
| 1.12 报表、Excel、二维码与打印 | 有局部入口及历史验证，本轮未完成全部口径复核 | 全报表范围、导入预检/确认、异步导出、下载审计、打印和数据范围验证 |
| 1.13 通知与机器人 | 存在正式通知路由；本轮未实发 | 微信/短信/飞书provider真实送达与失败回执，回调验签、鉴权、幂等及重试；不改变库存事实 |
| 3/4/5/6 数据库与发布 | 当前head0178；工作树仍未提交；PG16局部门禁有准确源证据，未完成当前全套和远端CI | 同一发布SHA的完整门禁、500用户压测、备份恢复RPO≤5分钟/RTO≤2小时、应用回滚/同步回退/业务冲销、书面验收及生产授权 |

阿里云依赖当前Chrome登录，最近实时访问命令助手被重定向至登录页。此条件仅阻断服务器检查/部署，不阻断上述本地实现。正式V1仍未完成，未提交、未部署、未切流，未冒充真人验收。

## 2026-10-06 v79 剩余计划浏览器首次创建与同单后续履约：数量/SN通过

- 完整重读正式基线，保留原工作树全部改动，分支codex/notification-delivery-worker，正式head20261227_0178。先回收v78数量/SN公开接口门禁84405 exit0并封存准确源，没有重跑。v79未修改产品源码、迁移、ACL或前端，仅扩展既有3个原生/浏览器帮助器及边界测试。
- 新--browser-supply-create：正式期初2件、三级批准2件、先分配1件；实际浏览器首次为剩余1件创建计划，打开/提交前两次capacity GET及成功详情回读。仅本需求创建POST可写，其他需求、计划更新/取消、库存写入及跨站请求被拒绝。33810 exit0，19项边界测试通过。
- **数量82301 exit0/run-ct88orm9，SN37739 exit0/run-x6_oe4w4**，两库均stopped/passed/serverExitCode0；各2009项后端源、507项前端/构建源与v79封存点逐字一致。浏览器POST201、只读原trace和库存不变通过；随后正式HTTP独立取消预计计划，同单占用→拣货→出库→发运→本人验收→本人入账1件，原申请人取消另1件未分配需求，总部正式服务独立关闭通过。批准2=入账1+取消1，不能称发货2，也不能称所有环节由浏览器完成。
- 数量和SN同单关闭后正式0178运行准入、拒降0177及全部事实保持通过。SN逐件验证一件在目标个人仓、另一件仍在来源仓；计划不改变库存。前端与产品源沿用v78，没有无谓重构或重复其全套门禁。
- 初轮33537 exit1/run-0mtenh5h：实际首次创建已成功；后续占用已提交但旧帮助器checkpoint仍按全部批准2件断言，修正为未占用1+阶段数量1。失败证据保留，不计完整通过。390px移动端documentWidth=390无页面横向溢出；截图supply-create-v79-quantity-mobile.jpg来自该初轮相同前端。最终桌面截图supply-create-v79-quantity-final.jpg与supply-create-v79-serial.jpg分别对应最终两库。
- 最终源original-integrated-source-v79-20261006.json共2788项，较v78仅4项帮助器/测试修改、无增删；supply-create-v79-source-changes.json与supply-create-final-v79-20261006.json保存准确范围。全部句柄已终态，源码固定解除；临时页面与隔离PG已关闭，不能把截图当作持续可用环境。后续不要继续轮询33810/33537/82301/37739/84405或重跑同版证据。
- **接续有限里程碑**：占用/释放/发运后的供给计划管理和历史恢复仍被planning_capacity及连续命令历史明确拒绝。先用真实已占用/部分发运的同单构造缺口，证明未分配批准量与实际占用、已释放、已发运、已入账、退回补偿独立口径；旧分配不会因释放而自动撤销，不能直接减释放量扩大新增计划额度。复用现有remaining_fulfillment/历史验证及锁，逐项补连续命令证明；需前向迁移、旧事实保留、SQL反例、公开HTTP与实际页面证据后才能开放，不删状态守卫或修改旧迁移。
- **全基线审计已更新**：FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md顶部区分局部交付和未完成正式范围，并把旧候选验收表标为历史。当前main.py仍只在非生产挂载旧transfers，app/alembic无employee_handover_cases/checks，CustodyAssignment.handover_case_id仍无FK预留；正式人员调拨、离职清零/双方确认/区域总部复核不是已完成功能。持续PC/H5、真实身份/渠道/UAT、当前全后端与远端CI、500用户、历史期初/三日对账、RPO/RTO/回滚及发布授权全部独立待证。
- Chrome实时导航再次回到阿里云登录页，只阻断云端。无服务器写入、提交、推送、部署、UAT或切流。额度最近已用97%、剩余3%；到2%按用户要求填写停止交接并停止，不可重置/购买绕过。完整目标保持active，不能标记完成。

## 2026-10-06 v78 公开供给余量与PC/H5接线：数量/SN原生门禁通过

- 延续v77，无旧迁移/权限/写服务变化。新增严格SupplyPlanningCapacityOut和GET /api/v1/material-requests/{id}/supply-planning-capacity，先校验需求read再由完整服务核对总部管理员及管理权限；no-store、只读、不受写开关影响。历史不完整503、占用/释放等未支持阶段412，不能伪装成零余量。
- 详情在部分分配且权限适用时独立证明容量，再决定create_supply_task；证明失败保持原详情可读但不授予新建动作。列表批量查询保持原行为，避免逐行增加历史查询。前端点击新建时及保存前分别核验当前版本容量，只有精确数量/全部行覆盖/状态一致才允许一次POST，成功详情回读及原请求恢复规则保留。
- **45219 exit0：26项真实SQLite HTTP与查询回归通过**，包含实际审批/分配、公开GET全SQL只读、容量用尽后详情动作关闭、篡改历史503、释放后412。16393初轮24失败为_shared _common未传新参数的NameError，已显式加可选参数并保留列表默认，未放宽查询/权限。
- 前端**10973 exit0，4文件138通过**及37915类型/warehouse构建通过，沿用v77封存前端源；覆盖部分分配后实际挂载创建、过期容量不提交、两次只读核验及写后详情。当前还不是实际浏览器对新入口的首次写入证明。
- **84405 exit0：数量run-za7veptd、SN run-j6ghbjd6均stopped/passed/serverExitCode0**；各2009项受检源与v78封存点一致。公开容量GET、no-store、关闭写开关可读、申请人403、实际详情创建动作、首次剩余创建及后续计划恢复、库存不变、SQL拒绝和0178留存拒降通过。全部句柄已终态，解除源码固定；不得继续轮询或重启同版门禁。最终收据supply-capacity-final-v78-20261006.json、源清单original-integrated-source-v78-20261006.json。
- v78工作源original-integrated-source-v78-working-20261006.json为2788项，较v77仅新增容量schema/真实HTTP测试2项、修改query/router/native helper3项；差异supply-capacity-v78-source-changes.json，进行中收据supply-capacity-v78-working-20261006.json。v77四个2007源门禁是真实通过，但其源与当前上述5项差异必须明确保留，不称当前全套已通过。
- **接续步骤**：v78终态及准确源已封存，下一步扩展既有隔离浏览器工具支持本单剩余计划首次创建的精确POST及capacity GET，实际PC/390px H5展示/提交/回读，并验证新计划之后继续实际占用至收货入账/独立关闭；不要重造候选框架。现browser-supply仅测试原计划更新取消，不能当作新增计划页面证据。
- Chrome命令助手本轮实时访问仍回跳登录页，云端待用户在当前Chrome扫码，没有服务器写入。额度最近已用93%、剩余7%，到2%写交接并停止，不能重置/购买绕过。无提交/推送/部署/切流，完整目标active，真实UAT及正式发布未完成。

## 2026-10-06 v77 正式0178：分配后创建剩余计划及独立SQL限额通过

- 0178前向替换供给任务INSERT守卫、供给历史校验和readiness三个函数；精确0177前驱，OID/属主/ACL/安全参数保留，无新增表或执行授权。旧迁移与v76逐字一致。冻结目录e23e5756863b12ecbadacb6f673ebeedf1f5c303883d2fb04997f00086aa22cb，readiness hash ee5e4a7b51850fda0ea41b5a71b34547cd822c1069075fc31c7ec948b905fb3d。
- 新创建必须在需求及身份锁内证明最终审批、分配和全部计划历史，数量不超过未分配且未被活动计划覆盖部分。SQL INSERT独立检查当前数量；历史SQL按每条创建命令的版本重建当时各计划状态及分配量，不用今天的取消投影掩盖历史超量。原计划数量保留，后续真实分配可以与预计计划重叠；更新/取消不自动修改分配或库存。
- **94712 exit0**：数量run-6opxo_jo、SN run-2mrn3eaw均stopped/passed/0，各2007源与v77封存点一致。正式0178升级/空库往返、旧供给事实往返、真实期初/三级审批、旧计划+部分分配后HTTP新建1件、后续四次分配及两计划取消、原key/trace READ ONLY恢复/原结果不变、实际库存不变通过。应用capacity被测试故意放大后超量2.001仍被SQL拒绝，两库postgres.log精确指向任务守卫第158行；原事实全量不变。新创建事实拒绝退0177，head及所有事实保留。
- **71089 exit0**：普通数量run-j8suzgfp、SN run-s1qjd7e6在同一0178/2007源完成申请→审批→分配→占用→拣货→出库→发运→收货→个人仓入账1件→独立关闭及留存/准入。**9019 exit0**：部署run-impy0cgd通过2采集角色精确权限、错误授权拒绝、读锁、故障回滚及正式迁移往返和9项短信配置；actualSmsSent=false、ciReleaseGate=false。
- **15314 exit0：316项迁移/权限/唯一head通过；65865 exit0：2项PG gate head/hash及SQLite全链/ORM/降base通过。**39892初轮121通过1跳过1失败为错误码兼容，已恢复原超量错误码；11765最终11项通过覆盖该失败及4个数量/SN、有/无旧计划、取消后重建和原trace场景。49986缺少可空构造参数导致4失败已修。计数重叠不相加。
- 前端已准备严格容量契约与创建前/提交前两次只读核验、显示已分配/活动计划/可新增/旧重叠；默认值取精确可新增量，批准数量不再误称批准余量。138项页面/协议通过（10973），类型/warehouse构建37915通过。94203的2个旧预期失败已更新：部分分配可创建，全分配仍拒绝。此封存点尚未接容量GET和详情创建动作，下一v78接线。
- 完整源original-integrated-source-v77-20261006.json为2786项，较v76新增8/修改39/无删除；最终收据supply-capacity-final-v77-20261006.json及差异保留。全部v77句柄终态，不重跑/重等。未提交、推送、部署或切流。
- 两条证明必须分开：新计划轨迹尚止于分配及计划取消；普通入账关闭轨迹没有新晚计划。仍需真实页面首次新建及同单后续入账关闭组合，不能将两条轨迹相加当作完成。占用/释放/发运之后的计划新建及恢复仍未实现；完整V1/真实UAT/渠道、持续环境、当前全套与远端CI、500用户、历史期初/三日对账/灾备及正式上线仍待证。

## 2026-10-06 v76 分配后新增计划余量及审批锚点验证通过

- 已完整重读678行正式基线、v75交接及当前diff；沿原工作树/分支，未丢弃任何改动。上一轮Chrome接管实时访问命令助手后跳回阿里云登录页，云端仍需用户扫码；本轮仅本地推进，没有服务器写入。当前额度已用90%、剩余10%，未到2%停止线。
- 供给/分配历史现在以实际第三级最终审批命令为锚点，证明其后所有连续命令、准确批准量及历史分配；支持此前没有任何计划的真实分配场景。审批异常统一映射为既有供给503边界，不放宽核验。
- 新内部只读capacity按每行分别返回批准、取消、已分配、活动计划、未分配、旧计划重叠及可新增计划量。可新增量=max(0,批准-取消-已分配-活动计划)；原计划可与后续分配重叠，原数量不自动消费或修改。取消旧计划只恢复未分配部分。占用/释放/发运后的历史尚未证明，明确412，不能返回猜测的0或直接扣原分配量。现有未分配创建复用相同Decimal数量校验。
- **87441 exit0：112通过、1不适用跳过，50.41s**；包括无旧计划、有旧计划、实际数量/SN分配、取消后的容量、最终审批/分配摘要/版本篡改、身份及只读边界。32292与44397失败日志保留：前者审批异常类型未统一，后者测试夹具数量/过期ORM访问/篡改数据CHECK问题，均已修复，未绕过产品约束。
- **63074 exit0：数量run-ygf5quq5、SN run-wcm50xgp均stopped/passed/serverExitCode0。**每库2001项受检源与v76封存点一致；正式0177迁移/运行准入、真实期初/三级审批、计划和四次分配、穿插计划更新/取消及原trace READ ONLY恢复通过。各阶段可新增量实测2、1、0、0；库存不变、实际详情仍禁止分配后新建、SQL反例和0177留存拒降通过。不是0178写入或占用/发运后余量的证明。
- 完整源original-integrated-source-v76-20261006.json为2778项，较v75新增2、修改4、无删除，所有冻结迁移不变；最终收据supply-capacity-final-v76-20261006.json及source-changes保存。全部本轮进程终态，63074不再等待，源码固定解除。v75浏览器与完整履约证据保持其原版本，不声称当前后端与v75完全一致。
- **下一项落实前向0178及公开接线**：在创建时按同需求锁下的已证明容量限额，数据库独立按创建版本重建当时各活动计划及分配，禁止超量与伪造历史；旧计划更新/取消仍保留其原数量。不能仅按当前cancelled_qty校验旧创建，否则后续合法取消会损坏原历史。正式迁移、运行准入、留存拒降及数量/SN原生证据后，接query/HTTP/PC/H5的准确容量显示与创建。还须继续覆盖占用释放/后续履约，不缩小完整V1。
- 当前head仍20261226_0177，没有0178、容量HTTP/UI或分配后创建激活。无提交/推送/部署/切流；真实UAT/渠道、持久在线PC/H5、全部V1、当前全套/远端CI、500用户、历史/期初迁移、三日OAM对账、RPO/RTO/回滚及正式验收仍分别待证，完整目标active。

## 2026-10-06 v75 供给计划实际页面与后续履约组合证据

数量54450/run-qsfi53e3和SN55295/run-2a74e4j4均exit0、stopped/passed/serverExitCode0；每库1999后端源和505前端/构建源与当前完全一致。实际页面首次更新/取消既有计划、每次成功后详情回读、数据库READ ONLY原trace恢复、库存及SN分配不变，随后同单独立占用/拣货/出库/发运/本人收货/入账1件/关闭通过。供应计划取消未取消真实分配，也未冒充库存已入账。18项浏览器边界通过。

实际降级在0177晚计划留存节点拒绝，原facts与head不变；不是旧shipment/closure节点拒绝的覆盖或带该历史降到0168的证明。前端手机390px无文档横向溢出，但仍是临时合成身份联验，非真人UAT或持久在线。两次正常POST核验走详情GET；原trace另由只读服务核验，未证明后续占用/发运之后的供给恢复。早期三次帮助器失败及修正均记录在v75收据，不能记为首次全绿。

产品源、前端构建、迁移和权限均与v74不变，4项改动仅联验帮助器/测试/runner；2776项封存源、supply-browser-final-v75-20261006.json包含准确终态。供给管理组合缺口已经补证，仍须实现分配/履约后的新计划剩余数量及重新分配、撤权恢复，并完成完整V1、持续环境、真实UAT/渠道、全套/远端CI、性能/迁移/对账/灾备及上线门槛。额度剩余11%，目标active，无提交/发布。

## 2026-10-06 v74 分配后的原计划管理、前向0177及当前证据

已实现仅分配轴推进后的原供给计划更新/取消与交错历史恢复。新0177同时修正连续供给前缀限制和旧独立任务写入守卫，数据库及服务各自核对历史和结果摘要，原迁移不改、权限不扩。旧计划历史可往返升级；已有晚计划事实拒绝降级0176且完整回滚。需要纠正历史表述：0168供给历史校验允许旧计划随履约保留，但0059独立任务写入守卫确实仍要求中性轴；22805已真实复现其拒绝新更新的503，本轮前向修复二者。

最终13849 exit0：数量run-9v6bvspi、SN run-vyaznaj6均stopped/passed/0，每库1999源与最终后端相同。四次分配与计划更新/取消真实HTTP提交、完整只读原请求恢复、角色隔离、库存不变、实际详情动作、历史升级/拒降及四类SQL反例通过。77954最终安全/新迁移311项，服务87通过1跳过、查询/图等79项、全链SQLite通过；重复范围不相加。前端87454共52项通过，包括响应丢失后重新挂载只读恢复；95800类型和同产品源码warehouse构建通过。完整源2776项，旧迁移源无漂移，最终收据supply-late-final-v74-20261006.json。

56625普通数量/SN完整履约和38264部署/采集角色门禁通过，但其捕获源在最终供给摘要加强之前，5项delta和最终双库差异覆盖已记录；不是同一完整源码的全量远端CI。9种短信配置不等于真实发送。所有进程终态，无提交或发布。

**仍需完成**：真实浏览器供给联验；新晚计划记录之后继续完整履约的组合证据；部分分配后的剩余补货新计划；占用/发运后的计划管理和恢复；撤权恢复。以及持久在线可验收PC/H5、真实身份/渠道/UAT、全量后端/当前远端CI、性能、历史/期初、三日对账及灾备/正式发布。当前Chrome控制台新请求返回ConsoleNeedLogin，用户登录后才能继续云端核验，不能用旧成功历史代替实时结果。额度剩余15%，完整目标active。


## 2026-10-06 v73 连续部分分配修复及供给恢复证据

第二次部分分配因总是生成“部分分配→部分分配”转换而触发CHECK的真实缺陷已修复；每笔分配仍有不可变事实、命令和审计，只有轴变化才写转换。旧供给请求可在另一管理员后续分配后只读恢复，完整核验连续分配、批准上限、历史身份/时间、SN及审计，不把较新版本猜成成功。原请求结果与当前状态分别保留。

9871 exit0：后端供给/恢复/分配96通过、1不适用跳过；50270 exit0：前端47通过。2376 exit0：数量run-_wn8bdnm、SN run-qoilpyyl均stopped/passed/0，各1994源与当前封存点相同；正式0176准入、四次HTTP分配、每次READ ONLY恢复、独立审计/真实状态转换、库存不变、原事实迁移往返和最小角色边界通过。原50886语法失败与26557真实缺陷复现保留。完整源2771项，新增3/修改4/无删除；无迁移、ACL、前端源码或生产变化。收据supply-allocation-final-v73-20261006.json。不是当前全套后端/远端CI、真实浏览器或真人UAT证明。

旧审计的“现行0059守卫要求所有轴中性”已纠正：0168版本允许旧供给事实随履约保留，仍限制供给命令为最终审批后的连续前缀。服务/query中性限制仍在。**下一项尚未完成：履约后的供给更新/撤销及有数量证明的新计划**；须处理有效取消、释放、现有分配与预计任务重复覆盖，做前向迁移与历史兼容后接HTTP/PC/H5。当前新增恢复只支持纯分配后缀，占用/发运之后及原操作者授权版本变化的恢复仍未实现。持久在线页面、真实身份/渠道/UAT、全量/远端CI、性能、历史/期初、三日对账和灾备/正式发布仍未达标。最新额度剩余21%，完整目标保持active。

## 2026-10-06 v72 草稿候选读取修复与下一基线缺口

真实草稿版本0候选GET原503已复现并修复，合法响应为空候选；版本0非空、非法版本和版本0补偿写仍拒绝。88420 exit0后端75项通过，80351前端7项、83182类型及99736warehouse构建通过。仅4项源变化，无迁移/ACL/库存改动；v71原生浏览器证据仍对应其封存源，v72差异由真实服务HTTP及聚焦回归覆盖，不冒称新完整PG16/远端CI已通过。最终收据draft-candidates-final-v72-20261006.json。

下一缺口已对照基线1.7/1.8复核：部分履约后继续管理余量供给。服务和查询授权要求中性履约轴，历史命令恢复也绑定中性轴；原称现行0059数据库守卫同样要求中性不准确，v73核对0168现行函数确认其已允许旧供给事实随履约保留，但限制供给命令为最终审批后的连续前缀；安全前向变更必须同时处理剩余需求数量、既有供给任务及完整历史，禁止单纯放宽按钮/旧迁移。正式上线的持续PC/H5、真人UAT/真实渠道、远端CI、性能、迁移/对账和灾备门槛仍未完成。

## 2026-10-06 v71 来源仓数量/SN真实浏览器门禁通过

数量9578、SN91736均exit0；run-ahltm8ro与run-7jc7ixn3均stopped/passed/serverExitCode0。正式0176下浏览器首次来源仓验收、独立入账各一次POST201，并按原key/trace回读；实际PG库存余额、流水及SN位置正确，原审批/履约/退运历史不变，留存拒降通过。每库1991项后端与505项前端/构建源均与封存版本一致。手机390px无横向溢出，数量刷新后状态持续一致；修复原列表未同步问题。边界17项、页面6项、类型及warehouse构建通过。初次数量门禁95208因帮助器账户选择错误失败，已修正并保留失败证据。

证明范围是隔离数据库、合成身份和实物标签下的浏览器验收/入账；此前申请、审批、发运和交运由正式服务/HTTP准备，不能声称整链均由真人浏览器操作或已完成真实用户UAT。最终收据rejection-browser-final-v71-20261006.json；完整V1、草稿版本0候选、履约后补货/重分配、远端CI及生产验收缺口仍待完成。用户新增额度剩余2%写交接并停止的边界，最近工具读数剩余27%；目标保持active，无发布操作。

## 2026-10-06 v70 当前来源仓操作已接线，真实浏览器仍待验证

仓库队列先按当前责任仓及读权限筛选，逐笔核验交运、验收及独立入账。已接公开HTTP、PC/H5可选记录、实物SKU/SN/异常凭证、分成色入账预览、原请求持久化与只读恢复。1520终态131通过/2跳过；58570终态数量run-_igena1g和SN run-w3xs6irl均stopped/passed/0，1990项后端源一致，实际HTTP、PG并发/撤权后恢复/SQL旁路拒绝/迁移ACL留存通过。51项前端聚焦及后加10项权限路由通过（有重叠），warehouse构建通过；最终类型与封存结果见v70收据。

此次是隔离库真实HTTP与组件操作证据，身份和附件仍合成；不存在真实浏览器拒收退回全闭环或用户UAT证明。下一步直接验证已挂载页面与正式服务联动；全部生产发布、外部渠道、履约后补货/重分配及完整V1门禁缺口不变。无提交、云端写入或部署。

## 2026-10-06 v69 原申请人拒收退回可见操作

v68与v69原生句柄均已终态。v69公开原拒收来源、登记、撤销、实物发出和承运交接及精确原请求恢复；PC/H5详情挂接选择来源、逐件SN、一次提交/保留和当前身份只读核验。107项初轮通过/1项UTC格式预期失败修正后HTTP21项通过；UI105项集成通过，末轮组件10项与父页面恢复1项通过，类型/warehouse构建通过（有重叠不累加）。74272 exit0，数量run-lkopxbuj/SN run-z_vbg75z均stopped/passed/0，两库1984项源与v69后端相同，真实HTTP及PG并发/撤权恢复/留存通过。收据rejection-http-final-v69-20261006.json。

来源仓验收/独立库存入账的公开HTTP、按当前保管责任过滤队列及页面仍待接线。已交承运不能当作已验收或已入账；当前无真实浏览器/UAT、远端CI或生产部署结果。全V1剩余功能和真实验收门禁不变。

## 2026-10-06 v68 已入账补偿可见操作接线

v66正式0176迁移及v67数量HTTP数量/SN两库均通过。v68新增来源选择、补偿POST、原key/trace恢复HTTP，PC/H5需求详情已接入来源选择与保留原请求的单次提交/恢复。HTTP11项、真实来源服务数量/SN2项、组件33项、页面/传输95项及类型/warehouse构建通过。64117 exit0：数量run-cfqngxf_与SN run-erokv6za均stopped/passed/0，各1979项受检源与v68后端逐字一致。实际首次补偿HTTP201、精确并发、撤权后READ ONLY原key/trace恢复、错误指纹409及写开关关闭503均通过；收据return-compensation-http-final-v68-20261006.json。

尚无真实浏览器或用户UAT证据；退回登记/交运/仓库验收/入账的服务已有正式数据库边界，但公共页面操作仍待接线。供给任务服务阻断履约开始后的新建/更新；现行0059函数限制供给命令连续前缀（v73核对修正，旧供给事实可随履约保留），因此“退回后超量补货”先前推测未获实证；需要解决的生产功能缺口是履约后剩余补货/重分配，以及独立剩余取消后的计划保护。完整V1、外部渠道和正式上线仍未完成。

## 2026-10-06 v65 当前：未履约取消与退回补偿共存

- 新增v2剩余取消证据及同一0176编译器前向SQL；完整个人入账/退回补偿/原取消历史独立核验，禁止递归读取或伪造个人入账。旧0171证据和SQL无补偿分支保留。
- 原生组合发现并修复READ ONLY恢复误用审计写锁，写命令保留锁、历史回读完整验证只读审计前缀。127项前置聚焦通过，修改后60项相关回归通过；前端46项及类型、warehouse构建通过。36884 exit0，数量run-xo9rokfg及SN run-ur__48su均stopped/passed/0，两库各1965项受检源码与当前封存点相同，完整目录及22条DDL完全相同；收据remaining-return-final-v65-20261006.json，不累加重叠测试数。
- 当前批准3 = 个人入账1 + 退回补偿1 + 未履约取消1；取消/关闭不改库存事实，实际释放1另有库存流水。撤权后只读恢复与SQL反例在数量/SN两库通过。完整源码2726项、323旧冻结源不变。
- 正式0176尚未激活，现有正式HTTP仍0175。下一项是同一迁移冻结/ACL/readiness/往返/留存及旧无补偿分支原生兼容，再同时接query取消总量和HTTP/PC/H5。不能以候选库或组件通过宣称正式页面闭环、用户UAT或上线完成。

## 2026-10-06 v64 补证：退回补偿数量及独立关闭

- 前向完成覆盖与八分区服务已完整核验退回及补偿，不把来源仓入账冒充个人仓入账。含补偿的关闭生成v2证据，逐原拒收明细结清；原v1关闭/取消历史保持原格式。
- 5118 exit0、87项服务回归；54318 exit0、62项原剩余取消/七分区回归；9489 exit0、前端类型检查及39项组件/契约/旧取消测试。warehouse构建通过，公开退回HTTP尚未激活，不能以组件测试称实际页面闭环已完成。
- 68273/40269均exit0，混合正常/拒收和三件两批退回各自数量/SN四库均stopped/passed/0，1964项原生受检源逐字相同。真实独立关闭、未补偿/部分补偿拒绝关闭、4类直接SQL关闭反例及精确回滚、撤权后只读恢复通过。四组完整目录与20条固定DDL相同。收据`return-closure-final-v64-20261006.json`，完整源码2725项；323个旧冻结迁移源不变。
- 14191原正常数量/SN正式0175履约/关闭/留存/运行准入通过；之后仅修混合夹具的数量断言，业务源码未变。9644的首次混合断言失败保留，修正后的4组不能冒充正式0176已激活。
- **仍未完成**：同请求未履约取消与退回补偿共存的新取消证据、正式0176迁移及readiness/留存、query列表/详情取消总量和HTTP同步接线、退回PC/H5/小程序真实写闭环。真实渠道/UAT、完整V1其他验收、当前全套远端CI、500用户、历史/期初迁移、三日对账及生产灾备/发布仍独立待证。无提交、部署或切流，目标继续active。

## 2026-10-06 v63 退回补偿实现状态

- 新增按真实仓库入账逐笔补偿、完整历史证据读取和八分区数量服务；旧0171取消语义及库存事实不改。补偿与关闭独立。
- 聚焦服务/原入账回归80项通过（79235 exit0），日志`return-compensation-v63-final-focused.log`。此项使用继承的合成期初夹具，不代替PG16或真实UAT。
- **30420 exit0**：前向PG16数量`run-6ffoteai`、SN`run-xft14ixu`均stopped/passed/0，各1963项受检源与当前逐字相同。实际两笔补偿共3件，库存/原需求不变，精确阻塞者、撤权只读恢复、直接SQL反例、DDL回滚/固定回放均通过。两模式目录及19条固定SQL一致；71337的驱动转义回放失败保留。正式head仍0175，0176未激活。
- 最终收据`return-compensation-final-v63-20261006.json`；完整源`original-integrated-source-v63-20261006.json`2724项（8新增/2修改/无删除），323个旧冻结迁移源未变；本轮所有句柄终态。
- **缺口仍在**：完成数量合并、版本化八分区HTTP/页面、同明细正常入账+拒收退回补偿后的独立关闭、未履约/退回两类取消共存、正式0176迁移/权限/readiness及留存。不能以服务80项通过代替这些验收。
- 持续可访问PC/H5、全量/远端CI、真实渠道/UAT、500用户、历史/期初迁移、三日对账、生产备份恢复/回滚和发布仍须各自真实证据；没有本轮生产变更或上线。当前句柄和文件接续位置以`CONTINUE_DEVELOPMENT.md`v63为准。

## 2026-10-06 v62 采集授权与迁移事务共存、期初导入留存和完整报表通过

- 前轮v61为progress，全部句柄已终态；本轮沿既有完整基线阅读和最新交接继续，未改175或更早冻结迁移。新增`backend/migration_capture_roles.py`，由Alembic在线事务调用，Docker镜像同时包含该依赖。
- 无采集角色时只读判断后保持原迁移路径；有角色时必须完整契约有效且直接非特权migrator。取得与配置工具相同咨询锁及全部契约表排他锁，精确验证owner-issued SELECT授权，事务内暂撤，再执行原冻结迁移；成功后精确恢复并重新核验。SQL错误/留存拒降/恢复失败均交由同一外层事务回滚。不修改账号、密码、登录状态、RLS策略或业务数据，不允许额外授权。
- 新增`pg16_capture_migration_gate.py`，接现有current-head runner；实际PG验证超级用户身份拒绝、SQL异常后的表/ACL统一回滚、额外SELECT拒绝且原权限不变、准确读事务阻塞者，以及配置采集角色后的0175→0174→0175实际迁移。后续原有`--report-full-flow`仍要到达0141非空导入留存守卫，不能只接受提前ACL拒绝。
- **64557 exit0：31 passed/120.31s**，全链SQLite与ORM一致/降至base、生产迁移角色拒绝和采集权限聚焦检查通过。Python编译及diff检查通过。没有增删旧业务事实或重跑v61四个已通过业务场景。
- **48568 exit0**：`artifacts/local-current-head-pg16/checks/run-23v05xg8` stopped/passed/serverExitCode0，1955项受检源与当前逐字一致。完整升级、配置采集角色后的0175→0174→0175往返、精确ACL恢复/读锁/故障回滚、10类采集权限漂移拒绝全部通过；正式API完整运行准入通过。
- 扩展真实期初（数量/SN/复盘/在途/新增库位/正差异）及导入子进程超时/请求恢复通过。完整报表HTTP/幂等/后台生成私有工作簿/下载核验通过；存储仍为合成。非空导入实际降级到0141留存守卫并被准确拒绝（不是175 ACL提前拒绝），回滚后文件、永久seal、全部库存/导入事实及完整运行目录均不变。短信9种配置通过，actualSmsSent=false；没有真实OAM binding时保持关闭符合预期，ciReleaseGate=false。
- **本轮全部句柄终态**，64557/48568均exit0，不再轮询或复跑相同范围。日志`capture-migration-v62-native.log`；最终收据`capture-migration-final-v62-20261006.json`，完整源`original-integrated-source-v62-20261006.json`2716项（新增2、修改3、无删除），323个冻结迁移/支持源未变。业务/冻结迁移/运行安全源未变，复用v61四库履约结果；本轮独立补证新迁移环境，不声称旧四库整份源码清单与当前相同。
- 旧v61首次子进程supervision错误根因仍未证实，诊断重跑未复现；本轮保留相同15秒测试和60秒生产预算及异常链诊断。运行全门禁时如再次出现，必须依据进度事件定位，不能放宽超时或吞错。
- 更新`DAILY_CAPTURE_ROLE_OPERATIONS.md`说明事务维护、准确回滚、中断后版本/权限回读和生产授权边界。**下一项直接继续退回补偿数量分区、原申请人补偿取消、独立关闭及HTTP/PC/H5**；具体接点在`REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`末节。无云端写入、提交、部署或上线；完整V1、真实UAT/渠道、全后端与远端CI、500用户、历史/期初迁移、三日对账及生产灾备仍独立待证，目标active。

## 2026-10-06 v61 正式0175入账通过；采集权限与迁移组合仍待修复

- 前一轮为progress：正式0175迁移/运行接线已完成，原生检查持续运行；中断后实际轮询原句柄确认终态，没有重启已完成履约门禁。完整基线678行已重读（中段截断已补读），沿用指定工作树/分支，保留全部原有改动。
- 冻结v60两库相同的48条SQL为`20261224_0175`，精确前驱174；3新表、6新私有函数、1账户准入函数替换、17新触发器。Base、readiness、OAM readiness manifest、最小权限及全部旧catalog前向核验均接通。旧174及更早冻结迁移保持不变，新API表仅SELECT/INSERT；新函数不授予API EXECUTE。
- **21186 exit0**：数量`run-liv51s3n`、SN`run-5glt6wrx`均stopped/passed/serverExitCode0。正式完整升级、空库退168/重升175、实际拒收/退运/仓库验收及三件分两批混合入账、绕过拒绝、并发/撤权恢复、完整运行准入、已有入账拒降174且全量事实不变通过。安装来自正式迁移，已删除门禁中的开发编译器安装路径。
- **51865 exit0**：正常数量`run-ulznsgxr`、SN`run-boasw97b`均stopped/passed/serverExitCode0。正式HTTP申请/三级审批/分配/占用/拣货/出库/发运/本人收货/个人仓入账、独立关闭及历史留存验证通过。
- 四库各1953项受检源，结束时匹配。之后仅两处测试预期及扩展期初导入诊断/runner更改，业务、迁移和运行权限代码未变。详细差异`rejection-inbound-v61-working-source-audit.json`；当前完整工作源`original-integrated-source-v61-working-20261006.json`2714项，较v60新增13/修改31/无删除。
- 聚焦初轮**47241 exit1，357通过/5失败**：3项为新SQLite辅助迁移前驱笔误（已修正），1项旧ACL测试遗漏3新表（已补），1项旧触发器计数遗漏17项（已补并按冻结目录逐项校验）。后续迁移组**50332 exit1，18通过/1失败**：新4项SQLite/元数据、全链SQLite与ORM一致/降至base、缓存和PG head摘要通过；唯一迁移图断言缺174前驱层，已补。**84275 exit0，7 passed/86.74s**：全部修正后节点通过，包含新0175的4项、ACL精确集合、触发器目录及完整迁移图。日志`rejection-inbound-v61-corrected-tests.log`。有重叠，不与首轮计数相加。
- **65023 exit1**，扩展当前head部署检查`run-1pzr62i6` stopped/failed/serverExitCode0。已跑过部署权限及反例、短信配置和实际多种期初盘点；`--report-full-flow`的真实子进程导入超时演练抛出`OpeningImportWorkerSupervisionError`，外层失败收据没有根因，因此此扩展门禁未通过。没有绕过或调大生产超时。
- 已新增不含输入、凭据或异常原文的诊断：故障阶段/进度事件/是否超时/耗时，以及异常cause的类型和栈坐标。**31359 exit1**，`run-4hns0fh9` stopped/failed/serverExitCode0；这次真实子进程超时/请求恢复及后续实际期初过账、对账关闭、完整报表流程均走过，首次supervision错误未复现，不能声称根因已修复。生产60秒及测试15秒均未改。
- **当前准确失败**：`import-job-nonempty-downgrade-rejected.log`显示0175在`audit_events`完整ACL检查发现11项而冻结默认10项，提前拒绝降级。多出的授权来自已按完整契约启用的两个只读采集角色；测试原本期望到达0141的非空导入留存拒绝，实际尚未到达。不是入账业务或数据库数据丢失，也不能将提前拒绝冒充0141留存证明。日志`rejection-inbound-v61-current-head-diagnostic.log`和failure.json保留。
- **下一项优先修复迁移与可选采集角色共存**：核对`backend/alembic/env.py`、`daily_reconciliation/capture_{provisioning,security,role_contract}.py`及冻结迁移严格目录。需同时验证完整只读角色契约、默认冻结目录和事务回滚后所有ACL/事实不变；不能关闭历史验证器、容忍任意额外授权、为过测永久删除采集角色或只把预期错误换为0175。考虑受控事务内迁移权限维护路径，必须精确验证并恢复合法权限；生产操作仍未授权。复用本轮已通过的业务门禁，只补受变化影响的组合证据。
- **全部本轮句柄已终态**：21186/51865/84275 exit0，47241/50332/65023/31359 exit1（其历史失败及修正/未复现边界如上）；不要再等待旧句柄。最终收据`rejection-inbound-formal-v61-20261006.json`，最终源`original-integrated-source-v61-20261006.json`2714项，Python编译/diff检查通过。扩展部署门禁明确passed=false，完整目标未完成。
- `REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`末节补齐后续补偿接点：保持0171七分区历史语义，独立已退回补偿事实/逐笔入账消费，累计取消与未占用分开，前向修订关闭守卫，再接HTTP/PC/H5。不能以仓库入账自动取消或自动关闭。
- 无提交/推送/部署/切流；没有云端写入。身份/文件仍合成，真实UAT/渠道、全V1、当前全套后端/远端CI、500用户、历史/期初迁移、三日对账、灾备/回滚和正式上线仍独立待证。目标active，当前没有用户必须提供的信息。

## 2026-10-06 v60 三件SN分批混合入账、SQL绕过拒绝及固定DDL重放通过

- 上轮v59为实际进展，本轮继续同一工作树/分支。正式基线已在持续任务中完整阅读；核对v59交接和当前源码，没有重跑不相关回归或改动业务服务。新增原生旁路/分批helper，扩展既有履约、仓库验收、入账gate及runner，未复制新业务候选。
- `--rejection-inbound-split` 通过正式期初建立三件库存；同一真实申请/三级审批/占用/拣货/出库/发运/拒收后，仓库第一次接受两件（原成色1、破损1）分别过账，第三件随后独立验收并过账。最终原成色2、破损1、在途0；SN模式为三个不同实物标识，逐件位置核验一致。第二笔改变目标余额后，第一笔原trace仍在READ ONLY事务准确恢复。
- **49040 exit0**：数量`run-53n6gia8`、SN`run-motp_0cn`均stopped/passed/serverExitCode0；每库1940项受检源与封存点逐字一致。保留原0174正式迁移/空库往返、完整准入、实际拒收退运/仓库验收和留存拒降链；新入账仍为开发安装，安装后runtimeAdmission=false，不扩大原0174准入证明。
- 新增无入账头的统一库存过账、改名来源但保留入账posting_key、空账户单独提交、原交易为入账却改名逆向来源的直接SQL反例；由数据库拒绝，失败前后全量事实快照不变。SN另有错误成色分份反例，复合外键合法后仍由完整份额守卫拒绝。v59的缺审计/通知、篡改数量/授权/方案/份额、并发、撤权恢复和不可变检查保留。数量26、SN29条拒绝记录包含同类ACL检查，不累计成全套测试数。
- 两库均记录安装器实际DDL、验证完整目录，rollback确认前驱无变化，再仅重放**48条固定SQL**，完整目录完全一致。两模式`rejection-inbound-catalog-candidate.json`和delta逐字相同，产品目录也与v59一致：3新表、6新私有函数、1账户函数替换、17新触发器；没有放宽或修改产品守卫。
- 失败保留：78857/run-d84g851c，DDL记录器误将WITH只读角色查询当作变更，改为仅记录CREATE/ALTER/GRANT/REVOKE并以完整目录重放核验；70427/run-_rrwgy11，第二批前的测试把quantity字符串直接与Decimal比较，改为显式Decimal转换。两个失败均终态，不是当前业务失败。日志为`rejection-inbound-v60-split-native.log`、`...native2.log`；最终`...native-final.log`。
- 收据`rejection-inbound-split-v60-20261006.json`；完整源`original-integrated-source-v60-20261006.json`2701项，较v59新增1/修改4、无删除，均为原生验证入口。Python编译/diff检查通过。所有本轮句柄已终态；无需再次轮询或重验相同开发候选。
- **下一项直接正式激活0175**：取两库一致的48条固定SQL及完整目录冻结迁移，精确前驱174、Base/最小权限/readiness/完整运行目录和OAM readiness manifest同步；空库往返、留存事实拒降、当前完整运行准入和必要正常履约回归有正式版本证据后，接已退回待补偿数量分区、原申请人取消/独立关闭及HTTP/PC/H5。具体注册坐标和旧测试适配已写入`REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`末节，避免重建候选或漏掉旧catalog校验。
- 当前head仍`20261223_0174`，无正式0175迁移/公共退回页面/补偿关闭。当前验证是仓库接受中的完好/破损混合，不能替代同单“个人已入账+拒收退回+补偿取消”完整守恒证明；此场景随补偿闭环继续验证。身份/文件仍合成，真实UAT、外部渠道、远端CI、全部V1验收、500用户、历史/期初迁移、三日对账和灾备/回滚仍各自待证。无云端写入/提交/推送/部署，目标active。

## 2026-10-06 v59 来源仓入账数据库约束与数量/SN原生验证通过（0175未正式激活）

- 上轮接管Chrome并实时SSH只读确认118.31.37.87可用；本轮恢复开发，完整重读正式基线、当前交接与AGENTS，保留原分支及全部未提交改动。本轮无云端写入。
- 新增`rejection_inbound_0175/guards.py`、`validation.py`和原生gate；复用0174当前仓库授权及0172登记/0173交运/0174验收历史核验。三张入账表API仅SELECT/INSERT，6个新私有函数、1个精确账户准入函数替换、17个新触发器。更新/删除/TRUNCATE受不可变约束；库存新来源及通用逆向来源有独立提交守卫。
- 提交时从验收事实与原库存游标重建完整入账方案：原成色/破损份额、目标维度、数量/逐件SN、原余额/版本/SN前驱和库存命令摘要；库存审计/状态/outbox、业务审计/outbox、通知与准确人员清单必须完整。账户首笔例外严格绑定相同入账与库存交易、有效期初、第一笔余额/游标与创建时间，没有放宽独立建账权限。
- **11725 exit0**：数量`run-erdo31vg`、SN`run-cqycok4h`均stopped/passed/serverExitCode0；每库1939项受检源与封存点逐字一致。正式0174完整迁移、空库退168/重升174、真实期初、原申请三级审批至发运拒收、退回取消/重登/交运、短少后破损实收及留存拒降均保持通过，随后安装新约束，实际完成来源仓库存入账。
- 两模式都实际新建准确坏件目标账户，无期初或授权mock。首次完整入账rollback后全部事实不变；缺业务审计、缺通知、篡改数量/授权版本/方案摘要/份额直接INSERT在数据库被拒并全量回滚。并发第二次入账观察到精确PG阻塞者后拒绝，同键回读不重写；撤权后新请求拒绝，原key/trace在READ ONLY事务恢复。原需求/审批/发运/拒收/退回/验收事实不变。数量22项、SN24项拒绝记录含同类ACL/不可变检查，不累计成全套测试数量。
- **两模式完整目录变更一致**：8张变化表（3张新增）、7个变化函数（6新增/1替换）。`rejection-inbound-catalog-delta.json`可作为正式冻结输入；目前还不是带固定SQL的正式迁移，不复制另一个候选。
- 失败证据保留：55911/run-c07lxljj在安装前因旧账户函数指纹被拒，改为0167冻结后延续至0174的准确指纹；25001/run-x1cwl3hl在首次完整提交校验因SQL NULL/JSON null区别失败，沿用既有库存审计空值语义修复。日志分别为`rejection-inbound-v59-native-quantity.log`和`rejection-inbound-v59-native-final.log`；最终`rejection-inbound-v59-native-final2.log`。
- 最终收据`rejection-inbound-native-v59-20261006.json`；完整源`original-integrated-source-v59-20261006.json`2700项，较v58新增3/修改1、无删除。Python编译及git diff --check通过；v58应用服务源码未改，其聚焦测试保留。所有本轮句柄已终态，无需继续轮询。
- **真实边界**：正式head仍`20261223_0174`；安装开发约束后runtimeAdmission=false，生产Base、正式0175迁移/readiness/全运行目录尚未启用。没有退回HTTP/PC/H5/补偿取消关闭或真实UAT；当前SN模式仍是单件轨迹，不能代替多SN分批/混合证明。无提交/推送/部署/切流。
- **下一项**：补裸库存来源无业务头、改名逆向绕过、空账户单独提交、错SN分份及多SN分批/混合反例；随后冻结同一0175目录、固定SQL/ACL、Base、readiness与完整运行准入，验证正式空库往返及留存拒降。然后实现已退回待补偿数量分区、原申请人补偿取消/独立关闭，接通HTTP/PC/H5。完整V1其余功能、远端CI、真实渠道/UAT、500用户、历史/期初迁移、三日对账、灾备/回滚及正式发布仍按各自证据推进，目标active。

## 2026-10-06 v58 来源仓独立入账服务与请求恢复（未正式激活）

- v57正式0174及五个原生库终态证据保留，不重复运行已证明范围。本轮新增独立拒收退回入账schema/schemas、只读plan、原子service、历史facts及测试，共6个新文件；唯一共用改动是通用库存冲销拒绝新来源，即使重命名逆向单据source也不能绕过。
- 只读方案由原退回/验收、准确在途账户、来源仓当前唯一责任人及receive_return权限生成；分出原成色与破损接受量，匹配逐件SN、当前余额版本/游标及当前位置。目标账户由服务器精确维度解析，预览无写入；短少/拒收不能入账，旧方案/数量不足/已过账拒绝。
- 新入账按ledger→principal→原需求→库存引用/SN锁序重算方案，从原在途扣减并调用统一post_inventory_transaction；同事务写独立不可变头/份额/SN、业务审计/outbox和目标工程师/保管人通知。接受事实、原需求版本、取消和关闭均不自动变更。新账户仅在本事务创建，晚期通知失败整体回滚。
- 历史恢复核验原验收、实际成色份额、精确库存交易/全部移动/SN、原过账游标前余额/版本及SN前驱、审计链、库存/业务outbox和通知目标清单。后续库存流转不影响原请求恢复；当前读范围与原actor/key或trace必需，撤销receive_return后仍可回读，不能新写。
- **97080 exit0：36通过/75.52s**，包含数量/SN、破损及混合数量分份、原键幂等、SQL全SELECT恢复、撤权、证据篡改、晚期失败原子回滚和通用冲销拒绝。32065 exit0：新增后续真实库存移动后历史恢复2通过/36 deselected/9.17s。12242 exit0：既有普通库存冲销及普通退回禁止通用冲销2通过/19.81s。88160的4项分份复核与36项重叠，不累计。
- **测试边界**：继承的原申请履约链仍为合成库存夹具，没有正式期初历史。7876首轮10通过/2失败正是实际过账正确拒绝未完成期初；保留单独反例证明该门禁。正向组合只替换缺失的期初历史检查，仍运行真实当前权限、账户、数量/SN投影、库存流水、审计/outbox/通知与恢复。89960发现夹具缺inventory审计头，显式建立空审计流后55143两项通过。没有放宽生产代码的期初校验；本轮不是完整原生PG16过账证据。
- **真实边界**：head仍0174。新三表仅独立schema，未注册生产Base、无0175迁移/PG触发器/运行准入，也没有HTTP/PC/H5；不得上线或声称生产入账已完成。单SN夹具只证明全件成色移动，真实多SN分批/混合仍待验。
- **下一步直接接0175**：复用0174私有来源仓授权和原验收历史SQL核验；新增入账/份额/SN不可变守卫及同事务验收→流水→审计→通知完整性，库存交易新增来源/逆向绕过均受约束；精确扩展rsc_require_opening_observation_account_0023首笔账户准入，只接受此已验收事实和相同库存交易/账户/数量/SN。先在自有PG16正式0174真实期初/履约链验证，再冻结迁移/权限/readiness和运行目录，后接需求退回補偿及公共页面，不伪造个人仓入账或工单/报损来源。
- 证据`rejection-inbound-service-v58-20261006.json`；源码`original-integrated-source-v58-20261006.json`2697项，较v57新增/修改7项，无删除；git diff --check通过。本轮全部句柄终态，未进行云端写入/提交/部署，全目标active。

## 2026-10-06 v57 正式0174仓库验收迁移与数量/SN原生验证通过

- 已将v56两模式一致的原生目录冻结为正式0174：三张验收表、五个私有函数、13个触发器及38条固定SQL；精确前驱0173、迁移身份/结构/ACL检查、空库往返与历史留存拒降。应用Base、完整运行目录和readiness同步激活；旧0173/0172冻结迁移未改。
- 修复OAM运行清单仍指向0173 readiness摘要的接线错误；API新表权限仅SELECT/INSERT，五个校验函数不授予API直接EXECUTE。既有审计触发器目录保持严格核验，没有放宽权限或历史完整性。
- 99933 exit0：0174/0173/0172迁移、运行权限、历史fixture策略360通过/83.05s；16969 exit0：当前唯一head及PG门禁摘要2通过/178 deselected/92.89s；97646 exit0：全链SQLite迁移与ORM相同、角色权限、空库降至base及编译缓存13通过/108.87s。是聚焦检查，不是当前后端全套或远端CI。
- **87842 exit0**：数量`run-_6cj277i`、SN`run-bof4qmp8`正式0174验收通过。完整迁移、退168/重升174、原申请三级审批到发运拒收、取消/重新登记、实物交运、短少后破损实收、精确并发阻塞与超收拒绝、撤权后只读key/trace恢复、直接SQL反例、完整运行准入、已有验收拒降173且所有事实不变全部通过。正式迁移安装，没有临时候选覆盖。
- **50320 exit0**：数量`run-h_2c9d0k`、SN`run-joi1hu2y`正常履约通过；真实本地HTTP申请/三级审批/分配/占用/拣货/出库/发运/本人收货/个人仓入账、独立关闭、历史守恒/留存拒降与完整运行准入通过。
- **49409 exit0**：当前head部署准入`run-vahyb5ab`通过，包含采集角色精确权限及越权反例、边缘部署SQL验证、报表作业/下载权限、期初文件来源和9项短信配置恢复反例。actualSmsSent=false；隔离库尚无真实OAM binding而保持关闭是预期，不是生产采集已打通；ciReleaseGate=false。
- 五库均stopped/passed/serverExitCode0，四个业务库各1930项受检源与封存点逐字一致。源码`original-integrated-source-v57-20261006.json`2691项，较v56新增/修改43项、无删除；最终收据`rejection-acceptance-final-v57-20261006.json`。全部本轮句柄已终态，不再轮询或复跑同版本已通过范围。
- **边界与接续**：正式head现为`20261223_0174`，仓库验收已经正式激活；实际独立仓库库存过账、退回补偿取消/关闭、退回HTTP/PC/H5尚未实现。下一项直接实现来源仓统一库存过账和准确账户首笔准入，再接补偿数量分区与公共界面。多SN分批/合格拒收混合闭环仍需补证；当前单份验收不能代表全场景或真实UAT。
- 本轮读服务器13:19:21 SSH exit0/ssh active，未进行云端写入。未提交/推送/部署/切流/reset/revert/丢弃；全V1、当前全套回归/远端CI、真实渠道/UAT/期初迁移/三日对账/灾备回滚/正式上线仍独立待证，目标active。

## 2026-10-06 v56 来源仓实际验收与双模式 PG16 候选通过（尚未正式激活）

- 已实现来源仓当前责任人的实际验收：准确绑定原拒收登记、交运、保管责任、SKU 与逐件 SN；按 receive_return 权限办理。短少只记录观察，不消耗下一次验收份额；破损是接受量子集，原破损不能自动按完好件接收。验收头、SN、异常凭证与审计同事务追加，库存不变。
- 新服务 `material_request_rejection_receipt.py` 及 schema/schemas 提供分批数量校验、同键幂等及 key/trace SELECT-only 恢复，撤销写权限后按当前读范围恢复。异常文件要求已完成上传、当前办理身份与授权版本、登记前完成且未重复绑定；原请求/数量/SN/证据/审计篡改拒绝。共用 `ReceiptAmountsIn` 抽取保持原普通与报损退回契约。
- 服务证据：9701 exit0 初始23通过/1跳过；8313 exit0 最终主服务25通过/1跳过，46.25s；19033 exit0 最终篡改与审计失败原子回滚7通过/1跳过/26 deselected，22.87s。单SN夹具无法证明分批多SN，跳过如实保留；有重叠，不累计成全套。
- 36225 exit1：普通/报损退回组合51通过/2失败，799.42s。两失败仅因调用小程序真实解析器时 PATH 无 node；17094 补现有 Node PATH 只重跑这两项，exit0、2通过/9 deselected、69.51s。未修改或跳过客户端断言，HTTP收货与原请求回读契约保持通过。
- 新 `backend/alembic/rejection_receipt_0174/guards.py` 只用于自有 PG16 开发候选：三表 SELECT/INSERT 最小权限、不可变保护、当前仓库唯一角色/责任/deny优先、累计数量/SN排他、原SKU/在途SN实扫、已完成异常凭证、完整哈希与审计延迟约束。五个SQL/PLpgSQL函数解析通过；尚未注册生产Base或正式迁移。
- **最终原生32251 exit0**：数量 `run-h24qy3mh`、SN `run-f9fc09zt` 均 stopped/passed/serverExitCode0；各1917项受检源逐字匹配。正式0173完整迁移/准入和真实原申请→三级审批→发运→拒收→退回登记→误登记取消/重新登记→发出/交运后安装新候选。实际短少后破损接收提交、精确PG并发阻塞与超收拒绝、按已提交验收的准确角色撤权后新写拒绝及 READ ONLY key/trace 恢复、直接SQL篡改/缺审计/缺SN/不可变写拒绝、全部原业务和库存不变通过。两模式目录差异完全相同：4张变化表（含audit_events）与5个新增私有函数。
- **失败全部留存**：43733/run-avbaosxm，damaged变量/列歧义，改显式sn.damaged；65192/run-16cghl0e，撤权夹具误选admin，改读验收记录绑定的真实role；21865数量run-1bjrjfl6通过、SN run-0c9mkynn因old别名与触发器OLD冲突失败，改prior_seen后最终双模式重验。没有放宽业务约束；所有旧句柄与集群均终态，不再轮询或复跑已证明同范围。
- 证据封存：`rejection-acceptance-final-v56-20261006.json`；完整源 `original-integrated-source-v56-20261006.json`2678项，较v55新增/修改8项、无删除。原生日志 `rejection-acceptance-v56-native-final.log`；更早未修SN别名的源码及阶段收据另存before-serial-fix/progress，不能替代最终收据。
- **真实边界**：正式head仍 `20261222_0173`。新表没有0174迁移、生产Base、运行目录准入、HTTP或PC/H5；候选安装后 runtimeAdmission=false。没有仓库库存入账、需求退回补偿或因此关闭，不能称已退回入库。合成身份/文件存储，不是真实用户UAT。
- **接续动作**：利用两库一致目录冻结正式0174 SQL/结构、精确前驱/最小权限/运行目录与readiness、空库往返及留存拒降；无需复制新候选。随后实现来源仓独立统一库存过账（原成色与坏件分份、当前保管人、精确首笔目标账户准入、同事务审计及通知），再补已退回待补偿、原申请人补偿取消/关闭与HTTP/PC/H5。真实多SN分批及合格/拒收混合轨迹与权限撤销竞争仍需随闭环补验，不能扩大本轮单份验收证明。
- 12:53:27 已重新接管用户原Chrome命令助手并实测118.31.37.87 SSH exit0、ssh active；本轮无云端写入。未提交/推送/部署/切流/reset/revert/丢弃。其他全V1门禁仍按真实结果独立记录，目标active。

## 2026-10-06 v55 来源仓独立身份读取与数量/SN原生门禁通过

- 上轮0173完整证据已封存（见v54）；本轮分离原验收/拒收登记/退运历史核验与当前办理人授权。新增内部来源仓接收详情，要求来源总部/区域仓当前唯一保管责任、对应角色和stock_operation/inventory读取权限；只读显示准确原物料/数量/SN/批次/异常、交运指纹和运单，不接受货物、不改库存。未挂HTTP或按钮。
- 历史核验只使用原user/person事实坐标，不加载原工程师当前principal；核验原验收时点保管责任/追踪规则、命令/审计链、原出库及退运因果链。当前本人接口仍保留当前权限/个人仓校验。新测试已覆盖原工程师停用、个人仓停用/保管责任结束后仓库读取，以及当前仓库撤权/责任变化/历史篡改拒绝。
- 39735 exit0：初始验收抽取回归150通过/137.52s（登记/进展共享核验抽取前，不可视为所有最终文件测试）。27796 exit1：最终共享核验+仓库组合105通过/2失败/115.96s；失败为重叠保管责任夹具先触发数据库唯一索引。15998首次仅修valid_to仍因相同valid_from唯一索引失败；60077最终改为不同起点但有效期重叠，2通过/9.94s，产品守卫未放宽。3097 exit0：本人入账候选21通过/26.74s。上述测试有重叠，不累计为全套。
- **91587 exit0**：正常数量run-lfw9a3xv、SN run-tb4r2wqn均stopped/passed/serverExitCode0；完整173迁移/运行准入、真实原收货入账关闭及留存拒降通过。每库1911项源码在封存时逐字匹配，收据rejection-warehouse-v55-normal-before-test-fix.json；之后仅两测试文件修正，业务源码未变，不必重跑正常长门禁。
- 退回专项首轮46015 exit1 / run-3f5cj1w5：独立仓库API READ ONLY详情已经执行，停用原工程师反例试图用migrator SET ROLE API，数据库正确拒绝；失败日志rejection-warehouse-v55-native.log保留，集群stopped/failed/serverExitCode0。现用核对Unix socket/data_directory/systemIdentifier的自建PG16 bootstrap连接临时变更夹具、切至API读取并始终rollback，未扩权。
- **30787 exit0**：最终数量run-5bza0hws、SN run-pchafa30均stopped/passed/serverExitCode0，1911项原生源与封存点逐字一致。173完整准入、实际取消/重新登记/退运进展、仓库API READ ONLY连续读取、原工程师停用后仓库仍按自身权限读取、bootstrap临时夹具rollback后原事实全表不变、留存拒降通过。最终日志rejection-warehouse-v55-native-final.log；本轮全部任务终态，不再重复轮询。
- 完整源original-integrated-source-v55-20261006.json共2672项，较v54新增/修改8项，无删除；最终汇总rejection-warehouse-final-v55-20261006.json，之前进行中收据保留。后续源码修改须以此封存点区分。
- 下一步直接实现仓库实际验收和独立统一库存入账；具体接点、份额/SN/异常处理及补偿路径已追加REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md。写闭环尚未实现，不能把可读取详情称为已收货或已退回入库。
- 无云端写入/提交/推送/部署/reset/revert/丢弃；全V1发布目标active，其他正式验收门禁同v54仍待证。


## 2026-10-06 v54 正式0173活动登记/取消/退运与迁移权限门禁通过

- 0173已正式激活：冻结24条迁移语句，新增进展事实、取消后活动数量/SN排他规则，保留全部旧登记/SN行；Base、运行目录/readiness和当前门禁版本同步。原迁移事实未重写。取消提交后重新登记、实物发出和承运交接仍是独立事实，不改库存或原需求版本。
- 7804 exit0：登记与进展服务77通过/74.12s。26177 exit0：迁移/安全357通过/128.93s。31547 exit0：完整ORM升级降级、迁移缓存及CI拓扑76通过/208.71s。最终迁移修正后80004 exit0：7通过/2.09s；测试有重叠，不累计为全套结果。
- 82650 exit0：激活前数量run-qpuml1cg、SN run-6l6j8x93通过并封存；取消实际COMMIT后并发重新登记，旧SN事实保留。安装后尚非正式准入，准确记录于rejection-progress-active-v54-before-activation.json。
- 首次正式14927 exit1 / run-pi62nytm：冻结触发器用pg_get_triggerdef反解析的ANY语句重放，产生不同目录表达式。仅改为原始IN谓词重放，预期目录和安全核验不放宽。保留rejection-progress-v54-formal-native.log。修复后的正式迁移、空库退168/重升173均通过。
- **9438 exit0**：退回数量run-kbb8n_ey、SN run-7w6onimb均stopped/passed/serverExitCode0。正式173完整准入、原拒收登记、真实取消后重新登记、精确PG并发阻塞者、退运发出/交运、权限撤销/只读原请求恢复、直接SQL伪造/不可变拒绝、留存拒降及拒降后事实不变通过。
- **70954 exit0**：正常数量run-ted2fhys、SN run-pq2odjfh均stopped/passed/serverExitCode0；实际申请/三级审批/分配/占用/拣货/发运/收货/独立个人入账/关闭和完整173准入、往返/留存拒降通过。四个履约库各1907项源在封存时与工作树逐字一致；合成身份与文件存储，不是真实用户UAT。
- 部署任务39579已结束，run-h4w6555g的终态stopped/passed/serverExitCode0及最终JSON明确通过：26个边缘部署场景、两只读采集角色及10类配置漂移拒绝、完整API准入、报表HTTP下载/撤权、期初文件来源边界、UUID私有依赖反例和9个短信配置场景。没有发送真实短信，未配置业务采集绑定仍拒绝。
- 当前全部原生任务终态，不再重复轮询。完整源original-integrated-source-v54-20261006.json共2668项，较v53新增/修改48项、无删除；综合证据rejection-progress-formal-final-v54-20261006.json。源码证据精确到封存点，后续修改需标注差异。
- Chrome接管和12:26:50北京时间root SSH只读复验确认目标118.31.37.87、ssh active；无需重登。本轮没有云端写入。
- **下一项**：来源仓当前合法责任人的实际验收、独立统一库存入账、已退回待补偿及原申请人补偿取消/关闭，再接通HTTP/PC/H5。原本人验收查询要求原工程师当前权限，不能借其身份替仓库验证旧事实；先分离历史证据核验与当前办理人授权，避免工程师停用后阻塞仓库收回。
- 完整V1其余功能、持续PC/H5、真实渠道/UAT、全套与远端CI、500用户、历史/期初迁移、三日对账、灾备恢复/回滚及正式上线仍独立待证。无提交/推送/部署/切流/reset/revert/丢弃；目标active。


## 2026-10-06 v53 误登记取消/退运发出/交运服务与双模式PG16通过（0173未激活）

- 上轮为progress：Chrome现有命令助手目标Ubuntu-ipvk，11:58:34北京时间root SSH只读连接118.31.37.87成功、ssh active、现有API/数据库健康。本轮无云端写入。已重读正式基线及补读330–435行、当前交接与AGENTS；保留原分支/全部未提交改动。
- 新增退回进展表/输入输出/服务及`rejection_progress_0173/guards.py`。取消仅限未发出登记；发出、交运分别追加事实，交运绑定本单唯一发出ID/指纹、承运商与运单。物理时间与系统登记时间分离；分别使用cancel_return/outbound_return/ship_return权限；原key/trace只读恢复和当前因果状态独立。原登记/请求版本/库存均不改，未注册生产Base或挂载HTTP/按钮。
- **44965 exit0：47 passed /44.55s**。数量/SN真实原收货夹具组合、取消/发出/交运、越序/错来源/时间/重复、逐动作拒权、篡改历史拒绝、撤权后原key/trace SELECT-only恢复、原登记保留及库存不变。日志`rejection-progress-v53-service.log`。
- **12335 exit1 / run-bv__23bq**：数量模式正确执行物理进展及并发/撤权验证后，测试错误期望owner不可变保护返回23514/42501，实际为55000/append-only。仅修正角色对应错误码断言（API42501、owner55000并核对消息）；失败日志`rejection-progress-v53-native.log`保留，集群stopped/serverExitCode0。
- **60955 exit0**：数量`run-9nh6gnru`、SN`run-z1wxyx_3`均stopped/passed/serverExitCode0，各1894项原生受检源与当前逐字一致。正式0172完整迁移/准入及原拒收登记/留存拒降通过后安装新守卫；发出与交运实际提交、发出/取消竞争精确PG阻塞者、撤权后新写拒绝/原key和trace只读恢复、12项直接SQL/不可变反例、原全业务事实与库存不变通过。
- **准确限制**：两模式取消分支执行全部延迟约束后主动rollback，以同一原登记验证实物发出；不等于已提交取消再登记。0172原累计数量预算和永久SN唯一约束尚未变，取消后数量/SN重新登记仍未实现。安装守卫后runtimeAdmission=false，不能将安装前0172准入扩大为0173正式准入；没有新HTTP/PC/H5/UAT证明。
- 两模式目录差异完全相同：2张变化表（audit_events、新进展表）及4个新增私有函数。最终日志`rejection-progress-v53-native-final.log`；汇总`rejection-progress-final-v53-20261006.json`；完整源`original-integrated-source-v53-20261006.json`2655项，较v52仅6新增/1修改，无删除。编译/diff检查通过；本轮全部句柄终态，不再轮询或重跑同范围。
- **接续直接完成0173活动占用及正式激活**：在同一父需求/验收行锁下，完整核验原登记与取消链，仅未取消登记计数量预算；替换永久SN唯一约束为活动登记SN排他规则并保留旧行。实际取消提交后数量/SN可重新登记、不能重复占用；补精确竞争/撤权证明，再把该最终目录、Base、正式前向/回退、readiness和服务预算一起启用，不能查询缺表后降级。当前无需重新搭建进展服务或复制候选。
- 随后完成来源仓实际验收/独立库存过账、已退回待补偿、原请求补偿取消/关闭及HTTP/PC/H5。完整V1其他功能、持续入口、全量/远端CI、真实渠道/UAT、性能、迁移/期初、三日对账、灾备/回滚与正式发布仍独立待验。未提交/推送/部署/切流，目标active。

## 2026-10-06 v52 拒收退回登记正式0172、正常履约与部署权限PG16通过

- 上轮为progress：11:33:23现有root SSH只读连接118.31.37.87，ssh服务active；Chrome命令助手目标一致。无需重登，无线上写入。
- v51原句柄29956、74257均exit0。服务26项通过；数量`run-zoxpgk9o`、SN`run-x0isbx78`均stopped/passed/serverExitCode0，各1875项当时原生源逐字匹配。原发运/拒收后登记保持库存不变，直接SQL伪造拒绝、精确PG阻塞者、撤权后新写拒绝及原key/trace只读恢复通过。SN另有外来SN与半件数量反例。v51收据`rejection-return-final-v51-20261006.json`和2636项完整源保留，未启用候选边界未篡改。
- 正式`20261221_0172`冻结同一候选：精确0171前驱、两张只追加事实表、5个私有函数、API仅SELECT/INSERT、独立审计约束、空库退回及留存拒降；纳入Base/运行安全目录/readiness/当前门禁head。旧迁移不改。没有公共HTTP/按钮，登记不等于物理退回或库存入账。
- **78554 exit0**：数量`run-dsx17pja`、SN`run-522ggt1b`均stopped/passed/0；正式迁移、空库退168重升172、完整运行准入、真实原拒收后登记、权限/并发/恢复/直接SQL反例、留存登记拒降至171均通过。1888项源码在封存时逐字匹配；之后仅两个测试预期文件变化，业务/迁移/运行源码无漂移。收据`rejection-return-formal-v52-native-20261006.json`。
- **17043 exit0**：正常数量`run-9r9tpn24`、SN`run-ct1rv6zr`均stopped/passed/0；实际申请/三级审批/分配/占用/拣货/出库/发运/收货/独立个人入账/独立关闭、正式172准入、既有履约退168重升及留存关闭/发运拒降通过。终态1888项逐字匹配；之后仅Alembic测试文件继续修正。全为受控合成身份与文件存储，不是真实用户UAT或上线证明。
- **85183 exit0**：新增/前驱迁移与登记32项通过。**77640 exit1**：选定迁移/安全5文件521项，513通过/8失败（695.65s）；6项为旧表/带条件审计触发器/最新链头预期，16719定向重验6通过/467 deselected、exit0。剩余角色测试回退后实际保留32权限/47授权（旧断言23/37遗漏成色纠正与关闭），旧离线0158前缀不含0167函数；已改为准确保留权限矩阵及独立冻结定义核验，未放宽产品守卫。单独复现97161 exit1日志保留。
- **回归修复终态**：30279 exit1为角色范围用例已通过、离线用例新引用runpy尚未前置；此纯测试错误已修复，11803 exit0，离线用例1通过/82.53s。首轮8个失败节点均已有修复后通过证据。因共用WHEN夹具改变，再完整重跑安全文件：**65764 exit0，305 passed/52.89s**。不简单相加重叠测试数，也不声称整仓后端全套通过。
- **6636 exit0**：部署权限隔离PG16 `run-8s91okcm` stopped/passed/serverExitCode0。172完整升级、26个边缘部署校验场景、2个可选只读采集角色及10类越权/配置漂移拒绝、API完整准入、报表HTTP/下载审计与撤权、期初文件来源边界、UUID私有依赖反例、短信配置9场景通过。真实SMS未发送，未配置业务采集绑定保持拒绝；不是外部渠道验收或GitHub门禁。
- **本轮全部句柄已终态，无需继续等待或重跑同一证据**。综合收据`rejection-return-formal-final-v52-20261006.json`，完整源`original-integrated-source-v52-20261006.json`2649项与当前逐字一致，较v51新增/修改43项、无删除，diff检查通过。4组履约PG16绑定1888项源，结束后仅测试预期文件更新；收据列出准确差异，业务/迁移/运行源码逐字未变。
- 接续：推进误登记取消、独立退回实物发出/承运交接/来源仓验收入账，以及已退回待补偿和原申请人取消/关闭。`REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`已明确分开未履约取消与退回补偿取消的数量公式；不得用改旧占用/拒收投影配平。
- 完整V1其他功能、持续PC/H5、真实渠道/UAT、当前全后端/远端CI、500用户、历史/期初迁移、三日对账、备份恢复与回滚、正式发布仍独立待证。没有提交、推送、部署、切流、reset/revert或丢弃改动，目标active。

## 2026-10-06 v50 拒收历史逐件可见；数量/SN拒收及正常履约PG16通过

- 已完整重读正式基线（补读365–430行）及v49交接/AGENTS，检查并保留原分支和全部未提交diff。上轮Chrome接管/SSH提供了当前服务器连接证据；本轮没有云端写入。
- 修复本人`my-inbounds/candidates`遗漏拒收SN和原验收条件：使用原命令核验结果和验收时点唯一有效的物料追踪规则，一次查询合格/拒收SN并核对物料与各组数量；原来源账户不外泄，坏历史只阻断该验收。没有改变库存写入、数据库迁移或权限。
- PC/H5与小程序严格字段白名单同步`condition`、`tracking_mode`、`rejected_serials`，分别核对两组数量/SN及跨组唯一性。显示原条件、拒收SN（不入账）；拒收记录刷新后仍可见，不显示入账按钮。小程序正式公开首页与路由范围保持原状。
- 后端21项通过（51063 exit0，39.99s）；前端契约/组件27项通过（48956 exit0），页面/适配器93项通过（9234 exit0），小程序13项通过；tsc/warehouse构建18842 exit0。编译、git diff --check通过。日志在`artifacts/formal-0165-integration/rejected-history-v50-*`。
- 复用原生履约门禁新增显式`--rejected-receipt`，真实发运后本人拒收，数量/SN保留在途，GET两次READ ONLY无事实变化，实际关闭请求须返回412；另跑正常收货独立入账及关闭回归。合成身份/文件存储，非真实UAT。
- 首轮32939 exit1 / `run-uhi366l0`：实际拒收、HTTP只读刷新和在途库存已经通过；测试误把`close_permitted`（角色授权）当作数量结单条件。已改为实际POST close验证412/quantity_incomplete；产品关闭服务未改。保留`rejected-history-v50-pg16.log`。并行正常门禁56754经显式SIGINT退出130 / `run-l5pqm4sz`停止清理，为同一门禁文件修正避免源码漂移，不能算通过。
- **最终全部终态**：47460 exit0，拒收数量`run-wm2tikpj`、SN`run-plwp3h9d`均stopped/passed/serverExitCode0；实际my-receipts201，候选GET两次200/no-store且全业务表不变，close412/quantity_incomplete，个人仓0、原在途1、SN仍在原在途账户，拒收待处理1，运行准入与留存发运拒降通过。
- 正常回归49159 exit0：数量`run-_y3iuhtc`、SN`run-j4_wj9mk`均stopped/passed/serverExitCode0，正常候选读取、实际收货/独立入账/关闭、完整运行准入、已履约往返和留存关闭/发运拒降通过。四库1869项后端/scripts/deployment/CI源码均与当前逐字一致。所有本轮句柄已终态，不再轮询或重复运行同范围。
- 汇总`rejected-history-final-v50-20261006.json`；最终两组日志`rejected-history-v50-{pg16-final,normal-pg16-final}.log`。本轮PC/H5为组件/契约/构建证据，没有新增真实浏览器截图或持续在线环境；不得将v48截图冒充v50构建。
- 完整源`original-integrated-source-v50-20261006.json`2630项，较v49仅修改15项、无删除；文档新增`REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`记录退回原来源绑定、真实收货/仓库入账、独立补偿取消及前向迁移路径。
- **接续真实缺口**：拒收退回与补偿写闭环尚未实现。本轮只修复历史展示并验证拒收不入账；不能据此声称已完成退回。现有普通退回源于工单回收或报损，拒收物资不能伪造个人仓入账再套用。后续需真实退回接收/库存移动及“已退回待补偿”独立阶段，不覆盖旧批准、占用、发运、拒收或0171取消历史。
- 全V1其余功能、持续PC/H5部署入口、真实渠道/UAT、当前全套回归/远端CI、性能/期初迁移/三日对账/灾备回滚及上线仍独立待证。无提交、推送、部署或切流；目标active。

## 2026-10-06 v49：取消权限撤销竞争补证通过

- 仅扩展正式0171既有原生门禁，产品/迁移/前端未改。数量`run-t700ibsl`、SN`run-mxli7zck`均stopped/passed/0，各1869项门禁源逐字一致；27339 exit0，完整源2630项。
- 工程师cancel权限目录allow→deny先持锁时，实际HTTP与直接API角色INSERT均等待精确PG阻塞者，撤权提交后分别403/42501。取消先完成权限锁定时，撤权连接实际等待取消提交后才完成。原入账1、释放1、取消1及独立关闭、旧业务全表不变、正式准入和留存拒降均通过。
- 撤权后原请求可只读恢复，变内容409、新键403；写停用/READ ONLY回读不改变事实。恢复隔离测试权限后，新键仍409。此证明覆盖取消权限目录变化，不扩展为所有身份/组织变更的并发证明。前两轮测试预期错误均已保留并修正，未更改产品来迎合测试。
- 汇总`artifacts/formal-0165-integration/remaining-cancel-authority-final-v49-20261006.json`；v48前端/构建清单仍匹配。云端只读证明仍是旧目录API/Web和候选DB分别运行，新候选尚无017x源，不能宣称新版持续入口已交付。
- 剩余重点：拒收退回补偿、持续访问环境及完整V1其他功能/全套当前版回归/远端CI/真实渠道/UAT/性能/迁移/生产对账/灾备/回滚。未提交、推送、部署或上线，目标active。

## 2026-10-06 v48：PC/H5本人收货与入账完成双模式本地集成验收

- PC/H5已使用本人包裹、验收候选、本人验收/入账和原key/trace恢复接口。管理来源库存读取依现有角色范围展示，工程师不再误读管理接口；未扩权。提交前重读候选/身份/权限，原请求可靠落盘后仅一次POST，未知结果只读核验，验收与入账保持独立。
- 数量`run-ddwr2evu`、SN`run-ssmfnetk`均stopped/passed/serverExitCode0，各1869项后端/门禁源码一致。两模式真实浏览器验收201与入账201各一次，原key/trace四次GET200，业务读取错误0；个人仓1.000，独立管理员关闭、正式0171完整运行准入、既有履约升降级不变及留存事实拒降通过。
- 数量实页暴露全入账后零剩余误报，已修复并增加回归；最终SN实页显示正确提示，PC/H5刷新保持同一入账单及精确SN，390宽无溢出、控制台error0。最终前端构建485项与SN证据逐字一致；数量证据为提示修复前构建，已明确保留这一差异。
- 118项本人/主页面/适配器测试、14项取消回归、16项预览边界检查通过；tsc与warehouse构建通过。完整源2630项，详见`artifacts/formal-0165-integration/personal-fulfillment-final-v48-20261006.json`。最终截图`artifacts/personal-v48-serial-{pc,h5}.jpg`。
- 尚无持续用户入口、真实UAT或正式部署。取消授权撤销竞争、拒收退回补偿及完整V1其他功能/当前版本全套门禁/真实渠道/远端CI/容量/迁移/生产对账/灾备/回滚仍未全部完成；不得据此提交或宣称上线。目标active。

## 2026-10-06 v47：真实PC/H5剩余取消已补证，本人收货入口仍缺

- 数量run-4dqmhplt、SN run-40rh7ys9均stopped/passed/0；各1869后端源匹配。原申请人H5各1次POST取消1，PC刷新重开保持同一事实，批准2=入账1+取消1；独立管理员关闭、旧业务事实不变及保留取消拒降通过。SN使用最终构建，手机成功提示纵向，390无横向溢出。
- 15项预览边界检查及warehouse构建通过。完整源2623项，仅测试helper/边界/runner/CSS四项变化；正式迁移/业务服务未变化，v46聚焦证据仍适用。详见`artifacts/formal-0165-integration/remaining-cancel-browser-final-v47-20261006.json`。
- **新实测缺口**：PC/H5工程师详情调用管理收货/入账/OAM接口，被来源库存作用域正确拒绝403。后端及小程序已有本人接口，PC/H5尚未接入；下一步补本人收货/入账并保留现有原请求恢复，不能扩大工程师权限或吞掉错误来冒充完成。
- 临时18087已停；合成身份浏览器验收不等于真实用户UAT、持续可访问环境或正式上线。未提交/推送/部署，其余V1门禁仍待完成。


## 2026-10-06 v46：0171剩余取消正式整合与双模式HTTP真库通过

- 正式0171迁移、最小权限/精确运行目录、保留事实拒降、HTTP原命令恢复、有效取消查询和PC/H5面板已整合。旧审批状态与库存事实保持不变；允许单独业务关闭。
- 数量run-gecpf787、SN run-v7gp6rk0均stopped/passed/0，1869项源匹配。真实HTTP入账1、释放1、取消1及关闭通过；竞争履约写等待后被拒，写停用+READ ONLY恢复通过，15项伪造/篡改反例拒绝。
- 后端当前取消服务/HTTP/查询63项、迁移/权限夹具44项、唯一head1项、SQLite完整head/ORM/权限/降级2项通过；前端94项、tsc和warehouse构建通过。证据`artifacts/formal-0165-integration/remaining-cancel-final-v46-20261006.json`，完整源2623项。
- **尚无新取消面板真实浏览器验收**：下一步补预览精确GET/POST边界，使用原申请人进行PC/H5实际取消与只读恢复，再补取消授权撤销并发。不得把本地测试视作真实用户UAT、持续可访问环境或正式上线。其余V1外部渠道/CI/UAT/容量/迁移/灾备/生产对账门禁仍独立待证。


## 2026-10-06 v45 剩余取消前向候选

新增申请人逐行明确提交的“取消全部剩余未履约数量”服务及独立头/逐行事实，保留审批、原分配和库存流水。27项服务测试通过；在实际批准2、发运入账1、释放1的PG16数量/SN轨迹后，候选完成剩余1的真实取消提交、只读恢复和原业务不变检查，两库各15项伪造或后续写反例拒绝，均stopped/passed/serverExitCode0、1855项源码一致。两模式数据库catalog一致，完整源2603项；证据 `remaining-cancel-final-v45-20261006.json`。

**未激活**：当前正式head仍0170；0171迁移/运行目录、有效取消数量与结单读取、HTTP及PC/H5尚未接通。新表未注册生产Base，也没有页面写入口。这批是下一次正式前向迁移的实测依据，不能称部分取消已经正式可用。并发取消/继续履约和权限撤销、留存回退及正式运行准入仍待验证；拒收补偿与其他完整V1/生产门禁继续保留。


## 2026-10-06 v44 部分履约后的剩余占用释放

正式head推进0170：逐原占用核验已释放/已拣货及SN证据，允许释放未拣货的剩余部分，保留原事实和原0071数量/SN约束。只读原请求恢复不再取得审计写锁；有部分履约后释放事实时拒绝降回旧版本。数量/SN原生正例为批准2、入账1、释放1，见v44交接和对应源清单；完整履约/关闭、最终整合源及SQLite门禁结果逐项记录，不将早期通过替代最新源验证。

最终当前源：数量/SN部分释放和数量/SN完整履约关闭共四个原生PG16集群全部stopped/passed/serverExitCode0，1849项源码一致；聚焦84通过/1跳过、版本整合135通过、SQLite当前结构/降级2通过。完整源2597项及准确日志见 `partial-release-final-v44-20261006.json`。完整报损/退回/日终长门禁与远端CI尚未在0170全套重跑。

**缺口保持**：实际部分取消、取消与原分配/释放的不可变补偿关系、拒收退回和结单覆盖仍待实现。释放后的未占用数量不是已取消。新版本尚无新增PC/H5实页操作验收，已有v43截图只证明当时构建。云端SSH已恢复且现有服务健康，不等于当前版本已部署；生产配置/真实渠道/UAT/远端CI/性能/三日对账/迁移灾备及上线均保留独立门禁。



## 2026-10-06 v43 剩余履约事实展示

现有详情的结单核对增加逐行七阶段数量，受当前需求read权限、版本/范围复核和no-store约束。实际批准/已取消/已入账与七阶段严格守恒，不把释放、验收、交运或未知结果互相代替；核对不是取消许可。后端83项、前端120项和正式构建通过；PG16数量run-1qvqhr95、SN run-p0v1yp53及浏览器run-5jqr50da均stopped/passed/serverExitCode0，与1843项后端源码一致，浏览器472项前端源码/构建一致。单单位原生8阶段顺序轨迹、当前接口、H5关闭恰一次201及PC刷新、布局/控制台已验证。混合数量和部分释放目前为本地服务测试，不是原生补偿取消。

收据：`artifacts/formal-0165-integration/remainder-final-v43-20261006.json`，全量源码2591项。原生已结束且18087已停，无持续入口。实际部分剩余取消/拒收补偿和其前向迁移尚待实现；完整V1其他功能、真实外部渠道/UAT/CI、500用户、三日生产对账、期初迁移、灾备及发布仍未全部完成。未提交/推送/部署，目标active。

## 2026-10-06 业务关闭 HTTP、PC/H5 与原请求恢复已接通（v42）

- Chrome 接管已确认杭州 `Ubuntu-ipvk / 118.31.37.87`。北京时间08:32:21沿用既有root SSH只读连接成功，候选数据库及旧API/db健康、旧web运行；无需重复登录。收据 `artifacts/formal-0165-integration/chrome-takeover-readonly-v42-20261006.json`。没有服务器写入或生产部署。
- 正式新增需求 `GET /closure`、`POST /close`、`GET /close-command-status`。读取遵守当前范围，关闭遵守当前总部/区域close权限；原请求查询同时绑定幂等键与内容SHA256，写停用后仍支持只读核验。关闭不改审批状态/原版本/库存事实。状态读取允许初始草稿版本0，防止关闭面板误锁草稿编辑；关闭写入仍要求正版本和全部业务条件。
- PC/H5挂载独立关闭面板，提交前重新核验身份、关闭权限及批准数量覆盖；浏览器必须先可靠保存原键/内容/指纹才可POST。POST成功仍要读取原命令、当前关闭事实和需求，身份/权限前后稳定才清除原记录。断网、未查到、身份变化、迟到响应或存储损坏均保留记录；恢复不重发POST。关闭状态尚未读到、提交中或结果未知时拦截其他写入，确认关闭后永久拦截该需求的新增履约；退出详情只解除页面锁，未核验原请求仍从列表恢复。结单核对不再对已关闭需求显示“尚未完成结单”。
- **后端40项通过 /20.65s**（`closure-http-v42-unit-final.log`）；**前端5文件113项通过 /11.56s**（`closure-ui-v42-tests-final.log`），含页面联动、草稿版本0、同键内容指纹、换身份/版本、未知POST、迟到响应和只读恢复；预览边界 **13项通过 /4.56s**。warehouse TypeScript/正式构建通过，最终日志 `closure-ui-v42-build-layout.log`。首次后端37项有2失败（缺头契约应400，测试原误期望422），修后39通过；随后增加草稿版本0得到40。首轮前端构建因测试直接导入node类型失败，改用项目现有vi.importActual方式后通过，未改产品依赖。保留所有原日志。
- 最终原生数量 `run-hwti91hh`、SN `run-dg9pi1kd` 均 **stopped/passed/serverExitCode0**，各1840项后端/scripts/deployment/CI源码与当前逐字一致。真实正式0169迁移、完整运行准入、空库/已有入账升降级、全履约后实际HTTP关闭201、同键重放/不同内容拒绝、关闭竞争写实际等待后拒绝、写停用+READ ONLY恢复及旧业务全表不变通过；各12项INSERT反例、24项屏障/ACL反例、留存关闭拒降继续通过。日志 `closure-http-v42-native-final.log`，复用v41未变化的迁移结构/SQLite证据。
- **真实浏览器最终联验 `run-8ocr10lp` / 13148 exit0**：当前正式构建、独立PG16/API角色、H5实际提交关闭201，PC刷新重开仍显示同一关闭事实；整个浏览器过程仅1次POST。关闭ID `33c92bf6-489d-482b-a324-673ad8da012e`；批准/入账1.000，关闭后预留/收货/入账按钮禁用，待核验按钮0，控制台error0，H5 body/dialog均390。完整运行准入、旧业务事实不变和留存关闭拒降通过，集群stopped/passed/serverExitCode0。前序 `run-nk86idtv` 也通过，但H5提示被flex挤成竖排；仅修CSS后完成此次最终实测。截图 `artifacts/closure-posted-v42b-{pc,h5}.png`；临时18087已停，不是持续在线入口。合成身份和前段审批夹具不等于短信登录/真实用户UAT。
- 完整源码清单 `original-integrated-source-v42-20261006.json` **2586项**（较v41新增7、修改13）；汇总 `closure-http-ui-final-v42-20261006.json`，全部当前源码哈希一致。所有本轮测试已终态，勿重复轮询旧句柄；原分支/HEAD不变，526项未提交状态保留，未提交/推送/切流。
- **接续优先**：补整单取消关闭与授权撤销竞争的原生证明（当前取消关闭仅服务单测）；推进部分履约剩余量取消与拒收补偿，保留逐行批准=取消+实际入账和库存守恒。随后继续受权业务名称选择、完整V1其余功能及持续PC/H5入口。真实短信/微信/KMS/OSS配置、远端CI、用户UAT、500用户、生产连续三日对账、期初迁移、备份恢复RPO/RTO、回滚/TLS和正式发布仍分别待验；本批关闭通过不构成上线。目标保持active。

## 2026-10-06 正式0169关闭迁移与完整运行准入双模式通过（v41）

- 在原工作树激活 `20261218_0169_material_request_closure.py`，前驱严格为0168；生产Base注册关闭表。用v40数量/SN完全相同的实测目录冻结18个变化表、9个新增函数及41条DDL，新增两个关闭审计触发器的ENABLE ALWAYS要求（合计43条）。迁移和只读运行目录分别固定SHA；不修改任何历史迁移、不覆盖原审批/库存/审计事实。
- 前向迁移检查直接PG16迁移角色、最小权限、READ COMMITTED、唯一前驱、准确旧目录/所有权/ACL/函数和触发器；在同一事务追加表、守卫、总部/区域close权限与0169 readiness。回退先核验目录并检查关闭表留存；有关闭记录拒绝回退，无记录只撤销新增结构，保留授权数据。
- 保留原运行验证器，对其重叠表/触发器做精确前驱匹配后叠加新目录，OAM readiness和当前日终入口同步0169。审计通用检查为关闭审计登记唯一已冻结的WHEN条件；完整目录仍逐字验证条件表达式，旧审计条件规则不变。SQLite仅提供结构工具路径，INSERT/UPDATE/DELETE关闭均明确拒绝并要求PG16，不能当成生产业务替代。
- 聚焦服务/权限/历史门禁初轮 **55047 exit1：69 passed、1 failed /76.48s**；唯一失败是历史链断言未插入0168节点，已修。随后 **13390 exit0：34 passed、273 deselected /68.23s**，当前head/readiness来源及审批/审计目录检查通过。SQLite小范围首轮1失败/2通过因测试未导入完整生产metadata，修后 **3 passed /1.43s**；保留全部失败日志。
- 原生首轮 **47304 exit1 / run-ddz8h2e9**：正式迁移和空库升降级通过，运行审计检查发现新触发器普通启用/WHEN未登记；已按上述精确契约修正。第二轮 **85849 exit1 / run-lndgfisd**：完整运行准入、数量完整履约、保留发运拒降、已入账事实往返不变通过；关闭验证脚本遗漏barrier常量导入导致NameError，已修，未放宽业务判定。
- **20182 exit0**：数量`run-0s1rhijl`与SN`run-5otfougg`均stopped/passed/serverExitCode0。各自真实正式迁移/空库退168再升169、完整运行准入、HTTP完整履约、已有入账事实退168再升169保持不变、12项INSERT反例+24项屏障/ACL反例、关闭并发等待后拒绝、原键重放/READ ONLY恢复、保留关闭事实拒降及拒降后完整准入均通过。关闭只新增独立事实/对应审计，旧业务全表逐行不变。两库1839项源码均逐字匹配当前；全部任务终态，不再轮询旧句柄。
- **57898 exit0：67 passed /169.31s**，含完整SQLite head与生产ORM/权限/降级一致性、SQLite关闭业务明确拒绝、准确单head和CI拓扑；1项anyio弃用警告，不影响通过。日志`closure-activation-v41-tooling.log`。当前完整源码清单`original-integrated-source-v41-20261006.json`为2579项（较v40新增13、修改26），汇总`closure-activation-final-v41-20261006.json`。`git diff --check`通过，未提交/推送/上线。
- 本轮用户Chrome接管已确认目标Ubuntu-ipvk，既有root SSH北京时间08:11:55只读连接成功，候选db及旧API/db健康；未做云写入、提交、推送、部署、切流或丢弃改动。HTTP/PC/H5关闭入口仍未挂载，完整目标active。
- 接续直接开发关闭/原请求恢复接口与PC/H5关闭动作，显示独立关闭事实并禁止关闭后新增履约；复用本批迁移/数量/SN证据，不重复搭建关闭数据库候选。补正式整单取消关闭及撤权并发原生证明（当前取消关闭为服务单测，数据库原生正例为实际入账），并对新接口做真实HTTP与浏览器闭环。部分履约剩余取消、拒收补偿及完整V1其他要求、真实渠道/配置、持续入口、UAT、500用户、生产三日对账、期初迁移、灾备和正式发布仍各自待验。

## 2026-10-06 关闭INSERT权限/数量/审计与权限默认值双模式通过（v40）

- 新增 `request_closure_0169/insert_guard.py`：直接SQL关闭也必须锁定开放父需求、核对当前有效账号/已验证身份/人员/授权版本、选定总部或区域授权、组织祖先范围、read与close显式deny；锁定组织路径并核对锁前后树一致，锁后用当前时钟核验有效期，完整证据计算后再次核权。幂等/请求/证据字段、当前修订/版本及事务时间必须准确。
- 数据库复用既有冻结审批/取消/履约因果校验，再按真实过账事实逐行重算批准=取消+已入账，拒绝缺过账/冲销、未结束占用/拣货/出库/验收、拒收待补偿及开放供给/替代；精确重建整个证据JSON，并核对原请求与证据SHA256。**原入账单保持pending/无posting_transaction_id是不可变原始记录，实际过账由inbound_postings和库存事务证明**，不能要求改写原单为posted。
- 延迟约束要求关闭与唯一精确审计在同一事务提交；额外审计触发器只处理关闭action/aggregate，不介入其他审计。新增 `permission_policy.py` 为一个共享material_request.close权限追加总部/区域两条grant，保留自定义ID、描述和显式deny，不给工程师/外部角色扩权；重复应用不写数据，每个受影响账号只推进一次授权版本。
- **22493 exit0**：数量 `run-nffo79m8`、SN `run-8_9zloqj` 均 stopped/passed/serverExitCode0。各自在真实完整HTTP履约后关闭个人仓1.000，12项INSERT反例（版本/修订/身份/授权/请求哈希/证据哈希/数量伪造/缺审计/合法审计链但内容错误/显式deny等）、24项既有屏障/ACL/隔离级别反例、真实竞争锁等待与关闭后拒绝、原键重放和READ ONLY恢复通过。完整旧业务表逐行快照不变，仅新增关闭和相应审计。权限策略均创建1定义/2grant、推进3用户，第二次安装无推进。
- 两库1826项原生源码均与当前逐字一致；两份 `closure-candidate-catalog.json` 完全相同，含18个变化表和9个新增函数的精确列/约束/FK守卫/索引/触发器/函数体/所有权/ACL前后目录，后续正式迁移直接复用这些真实观测，不另起模板或猜测定义。
- 权限策略单测 **5 passed /0.19s，exit0**，日志 `closure-permission-policy-v40b.log`。前序47955 exit1为上述原入账单状态假设错误，修正后93623 exit0为数量INSERT阶段验证；最终增加权限策略、错误审计、全业务事实不变和目录快照后以22493双模式为准。权限单测初轮1失败/4通过为CursorResult需先.all()再dict的测试写法，已修复；日志全部保留。
- 完整源码 `original-integrated-source-v40-20261006.json`2566项，较v39仅3新增/1既有文件改变；汇总 `closure-insert-candidate-final-v40-20261006.json`，原生日志 `closure-insert-native-v40c.log`。全部新句柄终态，无遗留临时PG进程；`git diff --check`通过。
- **当前仍未激活0169**：Alembic仍0168，关闭表未注册生产Base，HTTP/PC/H5关闭入口未挂载；候选安装前0168完整运行准入通过，安装后的正式准入明确false。没有云端写入、提交、推送、部署或切流，不等同生产/UAT。
- **下一步直接做正式0169迁移/准入接线**：从两库相同的目录冻结迁移与只读运行目录；接schema/权限策略/两个守卫安装器，精确锁定0168前驱，更新readiness版本及各已有验证器的重叠触发器/表目录，保留所有旧函数/ACL校验。验证空库前向、已有履约保留、闭单留存拒降、权限/完整运行准入及SQLite工具边界，再接HTTP与原请求恢复、PC/H5实际关闭。补充原生整单取消关闭和授权撤销并发证明，不要重复已有数量/SN同版本证明。部分履约剩余取消及完整V1其他上线门槛仍独立待办，目标active。

## 2026-10-06 关闭后履约屏障：数量/SN真实PG16候选通过（v39，未激活迁移）

- 前一轮为实质进展（v38服务与40项测试）；本轮新增 `backend/alembic/request_closure_0169/{schema,write_barrier}.py`，未添加Alembic版本入口或生产Base注册。16个前置触发器将需求业务更新、版本命令、修订/明细、分配、占用/释放/拣货/出库、供给/替代、发运明细、验收及待入账/过账统一解析到所属需求，排序锁定父需求后拒绝已关闭写入。更新同时检查新旧归属，关闭事实UPDATE/DELETE/TRUNCATE另有2个触发器拦截；API不可调用内部SECURITY DEFINER函数。物流签收、OAM回执、通知、对账的独立状态列不纳入业务冻结（仍受既有各自权限/守卫约束）。
- 为避免旧快照在锁等待后漏看刚提交的关闭，履约屏障明确要求READ COMMITTED；REPEATABLE READ/SERIALIZABLE写入拒绝。`pg16_material_request_closure_gate.py`接入既有长期原生runner的可选 `--closure-barrier-candidate`，不复制候选，不改变默认CI。它仅在自有临时集群完成0168完整准入和7步实际HTTP履约后安装候选表/保护，以合成管理员的限定测试close权限调用真实关闭服务；这是向前开发验证，不是正式0169权限迁移。
- **38278 exit0**：数量 `run-svn0adku`、SN `run-m2fjrrhp` 均 `stopped/passed/serverExitCode0`。各自真实申请/三级审批、分配/占用/拣货/出库/发运/本人验收/本人过账后个人仓1.000，再实际提交独立关闭记录；竞争履约写实际等待父需求锁，关闭提交后被拒绝。同键回放和READ ONLY事务恢复返回同一关闭ID；各24项直接SQL/隔离级别/不可变/ACL反例通过。1823项原生源码逐字哈希与当前一致，不能把该证明扩展为关闭HTTP/浏览器或UAT。
- **46299 exit0：63项CI拓扑测试通过 /26.67s**。原生日志 `closure-barrier-native-v39d.log`。前序13804 exit1（迁移账号看不到API等待状态，改由同一API角色观察自身连接）；18817 exit130为发现参数化多语句测试写法后主动中断，改为分开SET/INSERT，临时库已正常停止；27900 exit1为替代料API原本没有直接INSERT权限，现分别验证原API拒绝和owner级触发器拒绝，未放宽权限。所有失败记录保留，未把失败算通过。
- **准入边界**：0168完整运行准入在安装候选之前通过；安装后有额外表/触发器，runner明确记 `runtimeAdmissionBeforeCandidate=true`、`runtimeAdmission=false`。当前Alembic仍0168，关闭路由/页面仍未开放。尚缺关闭INSERT自身的数据库当前权限、精确覆盖、原请求指纹及延迟审计绑定校验，也未完成0169正式前向/回退、冻结目录及运行准入。不能据此部署。
- 完整源码 `original-integrated-source-v39-20261006.json`2563项，较v38仅3个新增文件和既有runner改变；汇总 `closure-barrier-candidate-final-v39-20261006.json`，详细结果在两库的 `closure-barrier-candidate.json`。`git diff --check`通过。没有服务器写入、提交、推送、部署或切流，未丢弃任何改动。
- **下一步直接完成0169关闭INSERT守卫和正式迁移**：复用已冻结当前审批/取消/真实入账因果校验，逐行核验批准=取消+真实过账且无待履约；当前账号/身份/区域close授权和显式deny、请求/证据哈希、唯一审计延迟绑定必须数据库独立验证。将此次屏障与权限目录一起纳入精确运行注册，验证关闭伪造、缺审计、并发及保留关闭事实拒降，再接HTTP/PC/H5；不要重建同一数量核验或重复本批已终态句柄。部分履约剩余取消及其他完整V1上线验收继续保留，目标active。

## 2026-10-06 Chrome 接管复核与关闭命令服务阶段（v38，尚未开放）

- 已复核用户 Chrome 原命令助手页面，目标为杭州 Ubuntu-ipvk / 118.31.37.87，最近只读诊断 `t-hz06z5l6mfdhfy8` 退出码0。沿用原备份的 root SSH 于北京时间07:42:26连接成功，目标主机 `iZbp11p1r66g3dq6h25c6qZ`；候选数据库、旧API/数据库均healthy，旧web仍运行。本轮仅回读，没有服务器写入；无需用户重复登录。
- 新增独立关闭Core表定义、输入/输出契约及 `material_request_closure.py` 服务。关闭使用当前读取范围及总部/区域close权限，父需求锁和当前授权图锁下核对最终批准数量、真实入账/取消、待入账、占用/拣货/出库/验收及供给任务；记录独立关闭事实和审计，不覆盖原审批状态或库存流水。当前仅支持已核验的完整覆盖，不虚构部分履约补偿或拒收处理。
- 原键原内容返回同一关闭结果；变更内容、新键重复关闭和旧版本拒绝。恢复要求当前读取权限及原操作者/原键绑定，可在关闭权限撤销、授权版本更新后只读恢复；篡改事实/审计、读取期间权限变化均拒绝。输出额外要求非空且无重复明细、数量全覆盖、非零标识、带时区时间和严格布尔值。
- **61966 exit0：3文件40 passed /24.52s**，其中新关闭25项，既有数量服务/HTTP15项；覆盖取消单独立关闭、重复提交、权限/版本冲突、只读恢复、篡改、审计失败回滚及输出反例。日志 `artifacts/formal-0165-integration/closure-service-v38d.log`。前序14861 exit1（测试键不足16字符，15失败/1通过）、99790 exit1（测试引用不存在的current_revision_id，2失败/14通过）已修正，17181 exit0为16项阶段验证；失败日志保留。现有anyio弃用警告不影响通过。
- 本批**关闭表尚未注册生产Base，关闭路由尚未挂载，没有关闭按钮，没有新增迁移或PG16关闭门禁证明**。该表仅由本地测试显式创建，不应绕过数据库门禁部署。当前迁移仍0168，云候选最近核验为0165；v37的PG16/浏览器证明仍只覆盖v37范围。
- 完整源码清单 `original-integrated-source-v38-20261006.json` 为2560项，较v37仅4个关闭相关新增文件；收据 `closure-service-stage-v38-20261006.json` 保存准确边界及测试/SSH证据。`git diff --check`通过，未提交、推送、部署、切流或丢弃改动。
- **下一步先完成关闭的前向数据库保护**：冻结迁移定义、只追加/审计绑定、数据库当前close授权和精确覆盖校验；所有新增履约与关闭争用同一父需求锁并拒绝关闭后写入（包含创建待入账单），保留独立物流/OAM/通知/对账。再做实际全入账数量/SN正例、直接SQL反例、并发、权限、保留事实拒降及完整运行准入，完成后才挂载HTTP/原请求恢复和PC/H5关闭动作。部分履约剩余取消和完整V1其余上线门槛仍未完成；目标active。

## 2026-10-06 结单数量核对已接入正式 HTTP 与 PC/H5（v37）

- 新增 `GET /v1/material-requests/{id}/completion-quantities`：使用当前需求读取权限、本人/区域范围，完成后再次检查需求版本、权限版本、角色有效期及组织范围；全程 SELECT、无写锁、no-store。逐行显示最终批准、真实已过账、取消和剩余数量，另列待过账单数。数量核验不是关闭许可，不返回 `can_close` 或虚构关闭事实。
- `material_request_approval_history.py` 从三级不可变决定、前驱链、原命令和审计链重建最终批准数量；外部登记额外核验原附件清单与独立复核。`material_request_cancellation_history.py` 核验既有整单取消的原请求指纹、逐行事实、原因、身份/修订/版本/时间及审计链；不能把投影中的取消数量或缺失证据当作成功。与已整合入账历史及数量守恒模块共同组成只读核对服务。仍未实现部分履约剩余取消或真正关闭命令。
- **60732 exit0：6文件68 passed /35.52s**；之后新增跨工程师拒绝及读取中版本/授权变化拒绝，**57016 exit0：13 passed /8.33s**（其中3项为增量，合计覆盖71项，不是81项）。前序60137为测试导入位置错误；22393为21通过/1失败，冻结时钟下篡改成同一时间没有真正制造反例，改为明确加1秒后通过；原日志均保留。
- PC/H5详情新增“结单核对”卡片，使用精确千分位整数校验数量守恒、完整明细集合、请求/修订/版本匹配；错误或手动刷新时清空旧结果，旧版本迟到响应不覆盖当前页面。**87300 exit0：3文件85 passed /10.30s**；最终类型标注及 Button tone 修正后，**52376 exit0：2文件15 passed /1.39s**，**7396 exit0**：TypeScript和warehouse构建。48473的1失败来自未安装的测试断言扩展，5198是构建发现的类型/Button参数错误，已修复且保留失败日志。
- **73379 exit0 / `run-sofqj3_e`**：SN实际HTTP7步、当前0168迁移及完整运行权限、原发运只读恢复、结单数量GET 200/no-store、完整只读事实不变、投影伪造拒绝与保留事实拒降均通过，个人仓1.000。1816项原生源码；此后只增加上述3项授权测试，应用/迁移源码完全相同，不重跑同一业务证明。
- **29957 exit0 / `run-4ja0sgj4`**：数量模式5步实际HTTP后，PC实际收货与创建待入账单，H5390实际确认入账；浏览器3次POST为201/201/200。收货及待入账时已入账0.000、剩余1.000、待过账1；确认后已入账1.000、剩余0.000，仍明确“尚未完成结单”。6次结单核对GET均200；刷新后仍为同一入账单 `INB-20261005-7A30E4D198D7`、事务 `fd712ef8-1d18-41b9-8a00-928310e66e9e`，POST仍3、恢复待核验0、控制台error0，body/dialog/viewport均390且卡片无溢出。图片 `artifacts/completion-v37-{pc-pending,h5-posted}.png`；原目录保存请求、页面证据及只读HTTP结果。
- 两库均 **stopped/passed/serverExitCode0**，所有新句柄终态；18087已停止，不是持续在线交付。数量原生1816项和浏览器前端清单无漂移，当前完整源码2556项，较v36有17项新增/变化。汇总 `artifacts/formal-0165-integration/completion-final-v37-20261006.json`，源码 `original-integrated-source-v37-20261006.json`。本批不新增迁移/权限，仅使用既有受权读取；不是完整远端CI、真实短信/OSS或用户UAT。
- 07:23接管实时只读证明 root SSH可用，云候选PG16.15仍0165、候选.env/.env.production缺失、旧网站容器正常，见 `chrome-takeover-db-readback-20261006-0723.json`。不要要求用户重复登录。本轮未做云写入、提交、推送、部署、切流、reset/revert或丢弃改动。
- **紧接着做实际业务关闭及剩余取消，不再重复搭建数量核验框架**：复用本批事实重建；关闭应记录独立不可变事实，保持审批状态不变，独立当前权限和原请求恢复；在同一父需求锁下检查未释放占用、未出库拣货、未发运出库、未验收/未入账及开放供给任务，并通过数据库约束拒绝关闭后的新增履约。补偿取消必须有独立批准/释放或退回依据，不放宽现有整单取消事实的全量约束。随后完成正式前向迁移、权限/并发/回滚验证和关闭页面。完整V1其他功能、真实渠道、可持续访问入口、UAT、500用户、真实三日对账、历史迁移/期初、备份恢复/RPO/RTO及发布仍独立待验；目标active。

## 2026-10-06 浏览器长期入口与入账关闭基础核验已整合

- **79897 exit0**：数量`run-qv4xkjqh`和SN`run-4ld8jmnd`均stopped/passed/serverExitCode0；各23次完整应用/API角色真实提交，原请求恢复、2次缺失请求封存/迟到写拒绝、附件绑定、撤写后读取和保留事实拒降均通过，0168运行准入恢复正常。封存时1803项源码逐字一致；收据`condition-formal-http-dual-final-v35-20261006.json`。身份注入和FakeStorage边界保留，不算真实短信/OSS或UAT；该句柄已终态，不再轮询。
- 已把`browser-tail-stage-v33`的原门禁hook整合到`backend/tests/pg16_material_request_http_gate.py`。现有`scripts/run_local_pg16_fulfillment_checks.py --tracking quantity --browser-tail`提供长期入口，默认CI仍无人值守；不复制整棵候选、不再用临时模块替换。新增`pg16_fulfillment_browser.py`，限制本机地址/Host/同源POST/当前测试需求指定动作，拒绝跨站和其他动作；记录请求、前端源码及构建哈希，超时不自动重放，完成后仍执行原库存/SN/权限/拒降/停库检查。用法见`PERSONAL_INBOUND_ACCEPTANCE.md`。
- `material_request_inbound_history.py`和`material_request_closure_coverage.py`及原34项测试已从暂存整合后端；真实履约门禁新增只读核验准确入账数量。它们仅证明关闭的数量基础，**未实现关闭命令或部分履约剩余取消**，不会将posted改称closed。原阶段和证据保留。
- **39747 exit0：4文件109 passed /62.37s**，包含34项入账/覆盖核验、12项浏览器边界和既有CI拓扑检查；日志`integrated-browser-closure-v36.log`，脚本help及`git diff --check`通过。当前全量源码`original-integrated-source-v36-20261006.json`2545项，相比v35共8个文件新增/改变；原生绑定1809项。
- **74772 exit0 / `run-95cwrw63`**：当前长期入口，PC登记收货/创建入账、H5确认过账，浏览器POST为201/201/200，五步前置实际HTTP加三步浏览器写共8步。刷新仍为同一入账单`INB-20261005-29422A876B5F`及事务`edc3bc20-f4cb-4f32-a0f7-05ea2f79a26c`，POST仍3，原请求待核验0，已收完选项disabled，H5 body/dialog/viewport均390，控制台error0。截图`artifacts/fulfillment-integrated-v36-{pc,h5}.png`，真实请求/恢复证据在原生目录。已停止18087，不是持续在线入口。
- **14976 exit0 / `run-9mbjucf3`**：当前SN无人值守实际HTTP7步通过。两库均stopped/passed/serverExitCode0，个人仓数量1.000，准确库存移动/命令/审计链/逐明细入账覆盖、SN位置、原发运只读恢复、全表不变/反伪造、完整当前权限及保留事实拒降通过。1809项原生源码与2545项完整源码逐字一致，浏览器460项前端源码/构建也无漂移。汇总`integrated-fulfillment-final-v36-20261006.json`；启动时的`integration-progress-v36-20261006.json`保留为历史过程记录。本轮所有PG句柄已终态，不重复轮询；没有遗留原生进程。
- 仍是合成身份与受控审批/期初夹具：本轮浏览器只操作收货和入账尾段，不能说申请到审批都由用户浏览器完成，更不能代替真实短信登录、用户UAT或正式上线。关闭基础模块已获数量/SN真实PG16证据，但businessClosed仍false；当前没有关闭接口/剩余取消迁移。
- 未新增云端写入、提交、推送、部署或切流。后续继续当前真库终态、业务关闭与补偿取消、受权业务名称选择、完整V1其余功能，以及独立的真实渠道/配置/远端CI/UAT/性能/三日对账/迁移/灾备验收；目标active。

## 2026-10-06 业务关闭基础核验已暂存，34项聚焦测试通过

- 基线186/527/674要求每条最终批准明细全部入账或取消后才可关闭；当前完整履约仍为`approved`，不能把`personal_inbound_status=posted`当作业务关闭。核对发现现有取消事实要求整条最终批准量取消，生命周期仅支持未履约整单取消，**部分履约剩余量取消、关闭事实/权限/接口尚缺**。
- `artifacts/formal-0165-integration/request-closure-stage/`仅暂存4个小文件，尚未整合正式后端：历史核验将入账单、库存事务/移动/SN、原版本命令、发运/收货因果、当前修订明细、出库/拣货历史和审计链绑定；只读且不锁审计写头，已有冲销不作为原始入账覆盖。数量规则逐明细精确计算批准−取消−已过账，拒绝重复收货明细、超量、跨明细抵扣和非法精度。批准/取消来源的真实性、当前授权、未结履约及并发锁仍由后续关闭命令补齐。
- **8858 exit0：34 passed /30.95s**，日志`closure-facts-v4.log`；覆盖数量/SN只读、流水篡改、错误请求关联、冲销、审计链破损和数量规则。复用原测试夹具的合成ledger commit，**不是原生PG16或业务关闭验收**。此前v1收集因生产默认配置拒绝；v2为3失败/11通过（夹具实际数量及账户CHECK假设），修正测试后v3为14通过；均保留原日志。阶段源码哈希、边界和后续项见`evidence-v4.json`。
- **79897仍live**，SN原目录`run-4ld8jmnd`已完成末轮提交和区域复核；继续原句柄，不重复启动。1803项后端/scripts/deployment/CI绑定源码逐项哈希一致，数量已通过，不能先记双模式终态。本轮没有修改正式后端。终态后先封存准确证据并整合现有浏览器尾段长期入口，再推进关闭命令、迁移/权限/PG16与PC/H5。
- Chrome/SSH接管沿用同日04:54和06:38只读实测记录，无需用户再登录；本轮未新增云端写入。全部未提交改动保留，未提交/推送/部署/切流，完整V1目标仍active。

## 2026-10-06 H5收货与入账历史改为可读卡片

- 相比v34c本轮仅修改 `frontend/src/styles.css`。复用同一历史表及同一业务事件处理，760px及以下以带字段名的卡片排版，收货/入账单号与库存事务自然换行，隐藏已过账记录的空操作格；PC保留表格，状态文字不拆行。没有另建移动端状态或写入逻辑。
- **88820 exit0**：TypeScript/warehouse正式构建。复用上一轮v34c的90项业务/恢复测试和`run-mvafmv49`真实浏览器/PG16证明，不为纯CSS重复执行库存门禁，也不宣称v35重新进行了实际入账。
- 从仍保留的真实PG16回读页面取得DOM，移除脚本、加载当前正式构建CSS，明确标注为静态布局核验。320/390/760/1366四档实际浏览器检查：两个历史区均scrollWidth=clientWidth，body=viewport，业务文字逐字相同；前三档为卡片、PC为表格。截图 `artifacts/fulfillment-history-v35-{h5,pc}.png`，DOM/CSS哈希与尺寸证据在 `fulfillment-history-layout-v35/`。静态快照未复制品牌图片，图片404仅属于快照，不冒称当前产品控制台零错误；不连接API、不是可办理业务的持续入口或UAT。临时18835服务器71667已主动中断并exit0，临时标签页关闭/viewport恢复。
- 源清单 `original-integrated-source-v35-20261006.json` 2539项；汇总 `fulfillment-history-layout-final-v35-20261006.json`。**79897仍live**：SN已完成区域驳回后释放，原1803项后端/scripts/CI源码无漂移；继续原句柄，禁止重复启动。浏览器长期测试入口整合仍等原门禁终态。下一步继续整合、真实业务名称选择、入账后业务关闭及完整V1缺口；部署配置/渠道/UAT/远端CI/性能/三日对账/迁移/灾备/正式发布尚未全验收。未提交、推送、生产写入或切流，目标active。

## 2026-10-06 收货剩余数量预检与当前 PC/H5 实际入账通过

- 原树新增 `frontend/src/materialRequestReceiptAvailability.ts`，收货面板按本需求发运和真实收货历史计算剩余可收数量；已收完明细保留显示但禁用。合格和拒收都扣减可收数，使用千分位整数运算；历史空明细、重复/异包绑定、超量或 SN 不一致停止读取，不当作零已收。发送前再次读取发运及收货，预检余量、未验收 SN、异常条件及证据是否填写，然后才保存原请求坐标并 POST；最终权限、证据所有权、版本和库存约束仍由后端决定。
- **55004 exit0：3文件90 passed /10.73s**，包含已收完禁止重收、分批验收/拒收扣减、并发历史更新、错误明细绑定、已验收 SN、缺异常证据及空历史明细等真实使用边界；**96239 exit0**：TypeScript/warehouse构建。此前45595为86 passed/3 failed，新测试误以为共用夹具只发1件，实际2件；仅明确这三项测试的发运数量，未放宽产品守卫，失败日志保留。中间39938为89项通过、54670构建通过；最终采用90项及96239证据。
- **87671 exit0 / `run-mvafmv49`**：真实独立PG16/0168，stopped/passed/serverExitCode0；5个实际API履约写入后，PC实测输入超收2件时仅页面提示、服务端POST为0，改为1件后完成收货和创建待入账单，再由390像素H5确认过账，实际浏览器POST为201/201/200。最终个人仓1.000；库存事务 `bcc5023b-b25f-4800-9c60-8091d378eb15`。刷新重开同一单据、事务不变，POST仍3，待核验按钮0，已收完选项的原生disabled属性为true；H5 body/dialog均390，无新控制台error。完整权限准入、原发运只读回读、全表不变/反伪造及保留事实拒降通过。
- 源版本 `original-integrated-source-v34c-20261006.json` **2539项**，较v33仅收货面板、其测试及新预检模块3文件；原生后端/scripts/CI **1803项完全未变**。原始v34清单保留，最终以v34c为准。复用已有 `browser-tail-stage-v33` 小范围暂存测试入口，未复制整棵候选，业务源码不是替身；仍未持久接入CI。截图 `artifacts/receipt-availability-posted-v34-{pc,h5}.png`；运行目录含 `browser-invalid-input-evidence.json`、`browser-ui-evidence.json`、真实请求及最终checks。18087已停止，截图不代表持续在线入口；合成身份/外部审批证据，不是短信登录或用户UAT。
- 汇总 `receipt-availability-final-v34c-20261006.json`。**79897仍live**，SN原目录`run-4ld8jmnd`已走到批准后取消和释放，继续原句柄，不改绑定的后端、scripts和CI源；数量已终态通过。其终态后优先把浏览器尾段按既有暂存差异整合为长期入口，随后继续业务单关闭、受权物料/人员/SN名称选择及完整V1范围。不能把个人仓posted当业务单closed。
- 同日06:38只读实测原SSH root可用，旧线上容器正常；候选PG16.15仍0165，`.env`/`.env.production`仍缺，收据 `chrome-takeover-db-readback-20261006-0638.json`；不再要求重复登录。真实外部渠道/配置、远端CI、用户UAT、持续PC/H5入口、500用户性能、连续三日对账、期初迁移、备份恢复/回滚和正式发布仍独立待验。未提交、推送、生产写入或切流，完整目标active。

## 2026-10-06 PC收货与H5个人仓入账已完成真实浏览器写入

- **72357 exit0**：`run-dbb549u8` stopped/passed/serverExitCode0。复用正式受控期初/独立复核与三级审批夹具，5个真实后台HTTP完成至发运，再由Codex浏览器PC页面登记收货、创建待入账单，H5页面确认库存过账；三个实际浏览器POST分别201/201/200。最终个人仓1.000，来源/占用/拣货/在途账户归零；完整运行权限、原发运只读恢复、反伪造和保留事实拒降仍通过。是合成身份+真实API角色/PG16，不是短信登录或用户UAT；申请和审批等前段仍由夹具完成，不能称全链均由浏览器操作。
- 页面按原请求回读后显示已验收/已过账，库存事务 `f282d304-7b77-455c-a6c6-9af3dd361696`；刷新重开保持同一已过账单，无遗留核验按钮，POST总数仍3。发运shipped、签收not_signed、OAM not_occurred彼此独立；390像素对话框无横向溢出。截图 `artifacts/browser-tail-posted-v33-{pc,h5}.png`，精确浏览器请求与UI证据位于原生目录。已主动停止18087并取得终态，不作为持续在线入口。
- 收货界面接受用户输入整数“1”或最多三位小数，复用既有精确数量转换，保存原请求前固定为1.000，避免要求用户手工输入三位小数；未放宽API规范或请求哈希。输入增加decimal键盘提示。**40064 exit0：13项收货/恢复测试 /3.64s**；**24666 exit0**：TypeScript/warehouse构建。当前2538项 `original-integrated-source-v33-20261006.json`，仅较v32改变收货面板与相应测试，后端1803项无漂移。
- 浏览器尾段使用 `browser-tail-stage-v33/plan.json` 明确记录的**两份暂存测试源码**：在既有受控夹具中替换收货/入账尾段，保留全部最终事实/权限验证；应用业务源码没有替身。因为79897仍绑定原树，暂存测试入口尚未整合CI/长期脚本，不能冒称已持久接入。
- 预览辅助器正确挂载真实构建的 `/xx/sw.js` 与manifest，SW返回200/JavaScript，本轮浏览器无新控制台error；只证明当前本地在线预览，不等于完整生产PWA/offline验收。首轮**39211被主动SIGINT，exit130**：审查发现过账接口成功码应为200而非201，停止后修正检查再启动72357；首轮库正常停库但checks=failed，保留日志/原辅助器，不当产品失败或通过。
- **79897数量模式正式HTTP已终态通过**：`run-qv4xkjqh` stopped/passed/serverExitCode0，23个实际完整应用/API角色提交，原请求恢复、缺失请求封存/迟到写拒绝、附件绑定、读权限及保留事实拒降通过；1803项源码逐字一致。认证为测试注入、存储FakeStorage，不是生产身份/OSS证明。**同句柄SN已启动** `run-4ld8jmnd`，继续原79897，尚未宣称双模式完整通过。
- 汇总收据 `actual-browser-tail-final-v33-20261006.json`。下一步接续SN终态后整合浏览器尾段的长期入口；继续受权业务选择（真实物料/人员/SN名称）、全批准明细入账/取消后业务单关闭、持续PC/H5交付及其余完整V1缺口。真实配置/渠道、远端CI、UAT、性能、连续三日对账、迁移期初、灾备与正式上线仍独立待验；未提交、推送或生产变更，目标active。

## 2026-10-06 收货/入账当前 PC/H5 联验及隔离履约终态

- 在v31基础上修正详情返回：收货/入账结果待核验时允许关闭详情返回列表，原持久记录和全局写保护继续保留，避免两个待核验对象互相阻挡入口。**59184 exit0：70项页面测试 /10.65s**；**32742 exit0**：最终TypeScript/warehouse构建。此前五文件98项为v31，当前页面70项为v32增量，不冒称一次全套。
- **58204 exit0**：`run-53dlv5y0` stopped/passed/serverExitCode0，真实隔离PG16/0168、受控期初及独立复核、7个履约HTTP写入、只读恢复/候选及全表事实不变、反伪造/保留事实拒降通过。原生1803项与当前源逐项一致。
- 实际最终构建在PC1366/H5390打开同一测试单，发运明细自动带出收货人，选择收货单自动带出入账人员和位置，字段只读；已过账收货禁止再建待入账单。已验收/已过账与物流未签收/OAM未发生分开显示；H5 body/dialog均390，无页面横向溢出。23条实际API GET均200；只读页面，合成身份，非浏览器写入或UAT。截图 `artifacts/receipt-bound-recipient-v32-{pc,h5}.png`、`artifacts/inbound-bound-target-v32-{pc,h5}.png`。18087已主动停止以完成原生终态，截图不代表当前在线服务。
- 发现独立预览辅助器问题：它只挂载assets，`/xx/sw.js`被SPA兜底返回HTML，浏览器ServiceWorker注册报MIME错误；产品实际注册路径已查明，当前服务页面读取正常。该预览错误保留于 `current-ui-v32.json`，不能称控制台无错误；后续预览入口须正确处理该路径，不能用此次证据证明生产PWA。
- 当前2538项源绑定 `original-integrated-source-v32-20261006.json`，准确汇总 `receipt-inbound-current-final-v32-20261006.json`。唯一剩余长门禁仍为**79897**（数量已推进撤回/释放，尚无整轮终态）。未修改其后端源、未提交/推送/部署/切流。
- 后续优先补实际浏览器收货/入账写入闭环、真实用户可用的持续PC/H5入口，核验业务单关闭（基线要求所有最终批准明细全部入账或取消；当前完整履约测试单仍approved，不能当作已关闭），并继续完整V1其余范围；云配置、真实渠道、UAT、远端CI与正式上线仍各自待证。目标active。

## 2026-10-06 收货与个人仓入账恢复接入整单保护

- 收货面板从发运明细选择并只读带出指定收货人，提交前重新核对身份、包裹、人员与位置绑定；保存原请求后才发送，POST响应不能直接清理记录，必须通过原幂等键/请求指纹/当前权限与详情回读。重复点击、切单、卸载后的迟到响应均不产生新写入或清除待核验记录。
- 个人仓入账修复实际恢复缺陷：旧逻辑可能用同收货的其他入账单或pending结果解除过账锁；现在创建与过账坐标分开，过账仅匹配原入账单ID，必须posted且绑定库存事务。校验唯一性、来源目标、前后身份权限、详情版本及页面仍有效后才清理；创建成功只确认待入账单，不自动过账。损坏/不可读记录保留并阻止覆盖。
- 两面板接入整单的同步写保护：预检开始即阻止其他写入；待核验时其他履约、草稿/审批操作保持阻止，自己的只读恢复按钮仍可用。重开页面有“打开待核验收货/入账”入口，按保存的准确需求ID读取，避免所有按钮被锁却无法恢复。
- **24144 exit0：78 passed /10.46s**，原收货绑定聚焦；**36901 exit0：82 passed /10.17s**，收货整单锁；**81961 exit0：97 passed /9.33s**，收货+入账；**74435 exit0：5文件98 passed /9.86s**，当前新增双恢复入口回归通过。**61026 exit0**：TypeScript/warehouse构建；随后仅增加一项页面恢复测试。`git diff --check`通过。日志 `receipt-bound-recipient-v30.log`、`receipt-page-lock-v30.log`、`inbound-page-recovery-v31{,b}.log`、`inbound-page-build-v31.log`。
- 当前源绑定 `original-integrated-source-v31-20261006.json` **2538项**，相对v29为7文件修改+1个收货面板测试新文件；汇总 `receipt-inbound-recovery-progress-v31-20261006.json`。本轮只改前端，原生成色门禁1803项源码全部逐字相同。没有提交、推送、生产写入或切流。
- **79897仍live**：`condition-http-current-v27.log`，数量库 `run-qv4xkjqh` 已推进取消/释放/重新提交与复审；数量终态与随后SN尚未完成，继续原句柄。
- **58204新live**：`receipt-inbound-visible-v31.log`，复用已有隔离履约/页面联验入口，为本次重写页面建立新的实际PC/H5证据。启动真实自有PG16数量库，完成既有HTTP后开放18087只读预览；尚未就绪，不能称当前页面联验已通过。测试身份/合成数据，禁止当作短信登录、浏览器写入或UAT。后端/scripts/deployment/CI仍受两条live门禁源绑定，不要修改或重复启动。
- 下一步：接续58204完成当前构建PC/H5视觉和只读HTTP联验，再补浏览器真实写入闭环；继续收取79897终态、准确业务人/物料展示与入账后整单关闭，以及完整V1剩余功能。远端CI、真实渠道/云配置、用户UAT、500用户性能、连续3日生产对账、历史迁移/期初、备份恢复/RPO/RTO及正式上线继续独立待验。目标保持active。

## 2026-10-06 请求恢复已整合，日终对账版本修复与维护版回退通过

- **7567 exit0**：原0164历史数量 `run-bi1t2lzl`、SN `run-wfi9vogj` 均 stopped/passed/serverExitCode0；分别保留519/531行、359个旧函数OID、11个原请求。真实Git0164历史→独立0166/0167授权增量→0168→显式维护版0164回退启动→再升级0168通过，1803项源在整合前逐项核对。原版Git0164仍不能充当回退版。该轮已结束，不再轮询。
- 数量/SN暂存只读恢复修复双通过后，向**原工作树**按expected-before整合16文件：发运回查/候选GET继续完整审计快照校验，但不再取得写锁；发运写入锁不变。13份当前门禁/权限夹具锚点更新为0168，保留冻结历史及未知版本拒绝。原字节保存 `original-before-readonly-current-gates-v25/`，未丢弃未提交改动。
- **56174 exit0：109 passed /37.74s**，发运、投影、权限夹具和CI拓扑聚焦验证。持久原树HTTP数量 **47076 exit0 / run-enp13sv4**，SN **92917 exit0 / run-4uevfuf4**，两库均 stopped/passed/serverExitCode0、源1803项与v25一致：7个真实履约HTTP写入、只读状态恢复/候选GET、全表事实不变、反伪造、带发运事实拒降及完整运行时通过。不是暂存模块覆盖版。
- 数量轮同时对实际构建页面完成PC1366/H5390只读联验：单号和十状态轴已移到操作区之前，显示已发货/个人已过账与物流未签收/OAM未发生；40个允许GET均200，无横向溢出。截图 `artifacts/fulfillment-summary-first-v25-{pc,h5}.png`。合成身份和数据，非短信登录、浏览器写入或用户UAT；18087已正常停服，不作为当前在线链接。
- **57872 exit1**暴露日终对账入口仍要求0167；修复后capture provisioning、mapping与deadline入口共用已审查的0168要求，权限规则不变。**21539 exit0：50 passed /1.86s**。**99838 exit0 / run-s4qiogt0**：原生迁移、成色权限/触发器异常拒绝、空库退166再升168、旧/未来head拒绝、实际只读采集角色配置及全运行时校验通过。**58345 exit0 / run-wit6dbww**：真实进程入口完成1次映射登记/审计、3个合成截点、CLI准确回查和恢复；均停库passed/源1803项一致。3个测试截点不代表生产连续3日对账。
- **50669 exit1 / run-mqvy4tsv**保留：当前运行时12项异常拒绝通过，空库回退后原版Git0164启动失败，符合已知不支持边界。CLI现在允许runtime catalog显式传入原Git来源与维护回退来源，并前后校验两者精确关系。**68127 exit0 / run-3qebl18h**：12种异常拒绝/精确恢复、显式维护版0164启动、再升级0168均通过；正常停库，sourceDrift空。`originalGitRollbackSupported=false`，不能把维护版通过说成原版可回退。
- 收货异常选项改为正常/短少/破损/错料/错SN/拒收；收货历史及入账历史分别使用中文业务标签，未知值不伪装成功，API值和提交规则不变。**69949 exit0：4文件76 passed /12.34s**；随后仅补标签字典自有属性判断，原生Node直接14项标签/未知值检查通过，**4877 exit0**：最终TypeScript与warehouse构建通过。后补标签尚未做新的实页截图，不将v25截图充作后补标签视觉证据。
- 当前完整源 `original-integrated-source-v29-20261006.json` **2537项**，汇总收据 `integrated-recovery-progress-v29-20261006.json`。各项证据按v25履约、v26对账/迁移、v27回退、v28页面回归、v29最终标签分别记录，不冒称一次当前版本全套通过。`git diff --check`通过，无提交、推送、部署或切流。
- **当前唯一长门禁：79897**，原树 `--formal-http` 数量随后SN，日志 `condition-http-current-v27.log`，数量库 `run-qv4xkjqh`，准确0157前驱为 `condition-ci-stage/fresh-0157-check/source/cloud_oam`（1221项）。已完成旧版升级及边缘角色配置，完整HTTP和两模式终态待验；只接续原句柄，不重复启动，不改其绑定后端/scripts/deployment/CI源码。前端与文档可独立推进。
- **下一步**：收取79897真实终态、修实际失败；补全详情/发运/收货中UUID输入的受权业务选择、入账后整单关闭及其他完整V1功能。完整远端CI、真实渠道/云配置、UAT、500用户性能、生产连续3日对账、历史迁移/期初、RPO/RTO、备份恢复和正式上线仍独立未全验收。目标active。

## 2026-10-06 05:55 Chrome 接管与实时 SSH 回读

- 已按用户指示接管现有 Chrome 命令助手，核对 Ubuntu-ipvk / 118.31.37.87。页面当前详情为04:10历史成功记录，未当作本次执行；另以既有root SSH配置实时只读连接成功，退出码0，服务器时间21:55:04 UTC（北京时间05:55:04）。无需用户再次登录。
- 实际 READ ONLY 事务回读候选 `rsc_check`：PostgreSQL **16.15 / 20261214_0165**；候选db及旧API/db健康，旧web继续占用80/443。候选目录存在，`.env`与`.env.production`仍缺失。未重启、修改权限/配置、迁移或切流。收据 `artifacts/formal-0165-integration/chrome-takeover-ssh-readback-20261006-055504.json`。
- 暂存只读锁修复的54716已经exit0；本轮复读数量 `run-xyxkctb3` 与SN `run-23p2xicf` 均为 stopped/passed/serverExitCode0，日志末尾确认两轮前后staged overrides一致。仍是暂存修复，尚未整合原树，不再轮询54716。
- 当前需求详情已把单号与十状态轴移到操作区前。56108：68项页面测试通过；本轮收取15953构建exit0。尚未补新版实际PC/H5截图，不沿用旧截图当新布局证据。
- 7567本轮仍live：数量已通过；SN已打印升级0166、0167和0168通过，原请求回查/回退/再升级及最终停库尚待终态。继续原句柄，禁止修改其绑定后端源码或重复启动。
- 0168当前门禁补丁清单需继续审查：除已暂存5文件外，`pg16_stock_operation_permission_policy.py`与相应测试仍只接受到0167；`pg16_loss_correction_request_gate.py`、`pg16_return_stop_gate.py`、`pg16_scrap_formal_retention.py`、`pg16_scrap_runtime_catalog_gate.py`、`pg16_scrap_business_gate.py`也有当前版本锚点。逐一分辨当前准入和冻结历史，不批量替换，不按旧“8文件”计划直接整合。保持未知版本拒绝。
- 下一步收取7567终态，整合已验证的只读恢复修复和完整当前门禁清单，再做必要增量验证。上线配置、真实渠道、远端CI、UAT及完整V1剩余验收仍未齐；未提交、推送、部署或切流，目标active。

## 2026-10-06 0168 长期门禁接入、中文状态联验与只读恢复缺陷

- 原工作树已新增 `backend/tests/pg16_material_request_http_gate.py` 与 `scripts/run_local_pg16_fulfillment_checks.py`：复用已实跑的受控期初/盘点/复核夹具，默认数量/SN两个独立自有PG16库，真实API角色及完整应用7个履约HTTP写入、当前详情、反伪造、带发运事实拒降和全表只读快照。CI既有 `pg16_condition` 扩为 `condition_http/legacy_history/fulfillment_http × quantity/serial` 六组合；只有新链跳过前驱源码提取，全部业务组合仍被最终汇总强制依赖。不是新增生产部署或远端CI通过。
- 旧历史门禁保留0166、0167两个独立授权增量，再升级当前0168；回退0164后再次升级0168。**7567仍为原live句柄**，日志 `legacy-history-0168-native-v23.log`。数量库 `run-bi1t2lzl` 已实读 stopped/passed/serverExitCode0：1421项真实Git0164旧应用、519行历史/359函数OID/11原请求、升级0168、显式维护版回退启动及再升级通过，1803项源无漂移。SN `run-wfi9vogj` 正在运行，未宣称双模式通过，不重复启动。
- 页面将十状态轴、紧急程度、审批来源与步骤译为业务标签，移除当前需求页的原型/实现术语。**36908 exit0：68 passed /8.77s**，**51077 exit0**：TypeScript与warehouse构建通过。首次78789是67通过/1失败：新展示测试把已批准状态放在无审批实例的草稿，正式校验正确拒绝；改为已有已批准夹具后通过，未放宽契约。
- **61339 exit0：71 passed /33.28s**，CI拓扑与既有数量/SN发运、分包投影聚焦测试通过。它不包含随后暂存的只读锁修复，不能混称该修复已整合。
- **23327 exit0**，真实PG16 `run-jcqh88bu` stopped/passed/serverExitCode0、1803项源与v23一致。数量正式期初及三级审批后7个HTTP写入成功；实际构建页面PC1366/H5390显示已发货、物流未签收、OAM未发生、个人已过账；40个浏览器允许GET全部200，无横向溢出。截图 `artifacts/fulfillment-labels-v23-{pc,h5}.png`，只读联验/合成身份，不是浏览器写入闭环、短信登录或UAT。18087已主动停服以完成终态，截图不代表当前在线入口。明细物料/收货与目标位置UUID、其他履约面板英文以及详情区位置仍是可见使用缺口。
- **60631 exit1**，新持久门禁数量 `run-4kxi0snh` stopped/failed/serverExitCode0。7个履约HTTP已成功，失败在只读发运原请求恢复：`shipment_command_status` 调用 `verified_outbound_history` 默认锁审计头，触发 `ReadOnlySqlTransaction: cannot execute SELECT FOR UPDATE in a read-only transaction`。这是已定位的恢复读取缺陷，保留原失败与现场；SN当时未开始。
- 有限修复暂存在 `shipment-readonly-stage/`：只把发运回查及候选读取改为既有 `lock_audit=False` 完整审计快照校验，写入仍保留原锁。**94725 exit0：暂存版本6 passed /5.32s**，包括禁止读取拿写锁及原篡改/撤权拒绝。同时扩充持久PG回查，要求原状态与候选两个实际GET在READ ONLY事务中200/no-store。**54716 live**，`shipment-readonly-stage/native-v1.log`：数量 `run-xyxkctb3` 已实读 stopped/passed/serverExitCode0，7个HTTP写入、READ ONLY状态恢复与候选GET200/no-store、全表事实不变、伪改拒绝与保留发运拒降通过；SN仍运行，暂存修复尚未合入原树。每库 `staged-source-overrides.json` 明确3文件expected-before/after，不冒称原树已修改；未复制完整候选。
- 另外已准备 `current-gates-0168-stage/plan.json` 的5文件补丁：成色正式HTTP/提交/保留事实、报废HTTP及current-head工具仍有旧当前版本硬编码。只更新当前准入为0168，冻结0167迁移/权限/前驱断言不变。仅准备，未应用/验证，不记作完成。
- **接续顺序**：继续原7567与54716，必要时诊断原句柄；两者终态后，逐项核对两个plan的before/after再整合8文件，跑必要聚焦及当前源码原生验证。不得在live源上修改、不能把暂存修复或数量单轮称双模式整合通过。远端CI、完整V1其他功能、真实渠道/部署配置/UAT/500用户/连续三日对账/迁移期初/灾备回滚继续独立保留。未提交、推送、部署或切流；完整目标active。

## 2026-10-06 0168 发运投影修复：数量/SN、旧历史、完整HTTP与PC/H5证据

- 已在现有工作树实现 `20261217_0168_shipment_projection.py`：发运命令同事务推进 `shipment_status=shipped`，只开放该列的 UPDATE；0029身份守卫要求真实发运命令、版本和时间绑定，0059因果守卫验证事实投影。旧供给命令保留原来的中性快照，不重写不可变JSON、哈希、库存或审计。旧历史升级在锁内只修复发运投影，临时迁移专用守卫在事务内恢复为最终守卫；保留业务拒绝降回0167。其他物流签收/OAM收货/个人入账/通知状态仍独立。
- **64774 exit0**，数量新链 `artifacts/local-demand-overview-pg16/run-5b2deoia`：完整生产数据库权限校验通过；正式期初/独立复核/控制账关闭、申请和三级审批后，7个完整应用HTTP履约POST均201/no-store，最终个人仓1件、发运shipped、个人入账posted，签收/OAM收货未发生。1800项原生源码；比最终版本少一个新增分包测试，后续差异仅验收测试清单。
- **43754 exit0**，SN新链 `run-y331g2fl`：当前1801项源码逐字一致，完整运行权限及上述7个HTTP写入通过，SN最终位置精确落在个人仓。此前 **12785 exit1** 是SN期初测试缺少该物料的截止账户，被受控复盘规则正确拦截；仅补空账户维度后经实盘及双复核产生库存，没有放宽守卫或直接灌余额。
- **47363 exit0**，数量旧历史 `artifacts/local-shipment-history-pg16/run-gsj8cfs5`；**94910 exit0**，SN旧历史 `run-eh3un6kl`：使用已冻结候选v10的**2511项真实0167前驱源码**完成实际HTTP入账，随后当前迁移升级0168。247张其他表完整行保持不变，需求行仅发运投影变化，版本/时间不变，函数OID/owner/ACL不变；新运行时完整安全校验、原发运命令恢复、当前完整应用HTTP详情200、伪改状态拒绝和保留事实拒降均通过。SN门禁1801项与最终源码一致；数量门禁后只改一处安全测试预期，产品源码相同。旧 `fulfillment-result.json` 特意保留升级前not_started，升级后证明见 `current-http-readback.json` 和 `checks.json`，不要覆写旧结果。
- 数量历史门禁同时实测PC1366/H5390，40条允许范围内GET均200、页面无横向溢出；同单显示发运shipped、个人posted、签收not_signed、OAM not_occurred。截图 `artifacts/shipment-projection-0168-{pc,h5}.png`。浏览器只读、合成身份，写入由真实隔离HTTP完成，不是用户UAT或真实短信登录。18087已主动停服且库stopped/passed/serverExitCode0，不能称当前在线入口。
- **15214 exit0：8 passed /7.30s**，数量/SN部分包裹与后续包裹、不可变原结果、当前授权恢复、库存不重复移动；此前34889的6项也通过。**87428 exit0：1 passed /99.14s**，完整SQLite升级/ORM/精确表与权限/空库退至base。95883原失败是0167十表/八权限遗漏，已补准确清单及125项授权，不删历史断言。
- 安全验收 **39274 exit1：334 passed、1 failed /117.05s**；最后一项误把成色决策封存表列入直接INSERT，按冻结ACL保持其仅SELECT后，**91294 exit0：该项1 passed /1.99s**。335项覆盖为334+1增量通过，不冒称一次整套335通过。更早64295的6失败/329通过已保留；修复的是0167冻结313触发器/表权限与0036、0069至0168函数前后链，实际数据库安全门槛未放宽。一次补丁未落地后的重复测试36478被主动中止，日志security-v2不作为通过证据。
- `1212 exit1` 的历史v2仅因验证脚本把详情字段错读成顶层而非 `states`；真实HTTP已200，改验证路径后47363数量与94910 SN均终态通过。所有上述新PG句柄均已终态，不重复轮询，不复用失败为成功。
- 当前完整源绑定 `artifacts/formal-0165-integration/original-integrated-source-v22-20261006.json` **2534项**；汇总收据 `shipment-projection-0168-final-v22-20261006.json` 逐项保留原生源差异和门禁边界。未提交/推送/部署/切流，完整V1目标仍active。
- **下一步**：将本批0168历史/HTTP/反伪造证明收敛入既有长期PostgreSQL16门禁，检查现有0167硬编码是否为历史断言还是当前准入；运行必要增量与远端CI，不泛化替换冻结历史。继续解决详情英文状态/原型说明/UUID选择等实际PC/H5使用缺口，以及整单关闭、其他正式基线功能。真实短信/微信/KMS/OSS、生产配置、用户UAT、500用户性能、连续3日对账、期初/迁移、备份恢复/回滚和正式发布仍分别未全验收；本批局部证据不代表正式上线。

## 2026-10-06 主链实际入账与完整应用HTTP回读：发现发运状态投影缺陷

- **97186 exit0**，隔离PG16 `run-orbb0195`：复用签名SKU/控制数发布及真实期初盘点，来源仓盘点1件并经区域/总部复核、独立对账关闭；目标个人仓由本人零盘点及两级复核关闭。随后正式API角色分别提交草稿、申请、区域/总部批准、外部证据登记/独立复核、分配、占用、拣货、出库、发运、本人收货和本人入账。个人仓1件，来源及中间账户0；物流签收/OAM收货未发生，未伪造送达。最初 **27993 exit1** 在本人入账被目标仓未建立期初正确拦截，失败保留，未放宽门禁。
- **31127 exit0**，`run-l0h2l2g9`：上述期初及申请审批继续正式服务事务；分配、占用、拣货、出库、发运、本人收货、本人入账 **7个实际完整应用HTTP POST均201/no-store**，各自真实事务提交。使用合成身份、合成外部审批证据，不含短信/KMS/OSS实通。完整构建应用PC/H5通过真实GET打开同一张单据 `c1599411-18c4-4aa6-85fe-e41f2426968a`，个人入库posted、收货/发运历史存在；H5无横向溢出。浏览器只读，不能称浏览器逐步写入或用户UAT通过。
- **重要未解决缺陷**：实际Shipment.status=shipped且个人已入账，但MaterialRequest.shipment_status仍not_started，概览/详情会错误显示发运未开始。`material_request_fulfillment_command.record_fulfillment_command`只更新personal_inbound_status，数据库0029身份守卫（0084修订后）及0059因果守卫也强制shipment_status=not_started。因此不能仅加Python字段赋值或页面推断，必须做版本化数据库/权限/服务修复，保持历史命令结果及原请求恢复，验证旧历史和新分包均不误判。
- 在既有 `backend/tests/test_material_request_shipment.py` 增加准确发运投影与其他独立状态断言；**88014 exit1：数量/SN两项均复现该缺陷，3.25s**。日志 `artifacts/formal-0165-integration/demand-overview-stage/shipment-projection-red-v1.log`。当前源码 **v21/2526项**相对v20仅此测试改变；产品仍v20，尚未修复，不声明门禁全绿，不提交。
- **18060 exit0**：仅在新自有PG16 `artifacts/local-shipment-projection-pg16/run-7uj_qe6j` 迁移0167并读取三个准确函数定义/hash（0029身份、0059供给因果、0044 readiness），正常停库。产物 `demand-overview-stage/shipment-predecessor-0167.json` 含前驱源码manifest；是新迁移的前驱证据，不是0168已实现。身份hash349ba279…，供给hash ef61be0a…；保留初建草稿neutral检查，仅规划改变有真实发运证据的更新边界，避免重写旧函数文件。
- 收据 `artifacts/formal-0165-integration/fulfillment-http-observation-v20-20261006.json` 严格记录七项HTTP、当前已知缺陷、各自v20源码和v21测试差异。截图 `artifacts/fulfillment-detail-pg16-{pc,h5}-v20.png`。18087已正常停止以完成终态/源码核验，不能再称可打开的在线预览。所有本轮PG进程已终态，不要重复轮询或把原失败记绿。
- 可见产品缺口也实际观察到：需求详情仍有原型术语、英文状态、物料/目标位置UUID输入。需在主链事实投影修复后改为业务标签和受权候选选择。整单关闭、逐SKU报表、其他完整V1项、真实渠道/生产配置、UAT/性能/三日对账/迁移/灾备仍未全部完成。SSH按旧备份root已恢复，不能再把连接账号错误当上线阻塞。目标active；无提交/推送/部署/切流。

## 2026-10-06 04:56 当前源码概览真实 PG16 与 PC/H5 联验完成

- **60365 exit0** 的已批准分支（`run-8n8lz8nz`）与 **39675 exit0** 的实际浏览器分支（`run-23ii0ogr`）均完成并正常停库。持久概览门禁的审批中、三级已批准两分支已在本地真实 PostgreSQL16 执行；此前“已批准未实跑”只代表当时记录，远端 GitHub 门禁仍未执行。
- 当前 v20 **2526项**与原生 **1793项**源码逐字hash核验一致。实际构建的完整应用连接隔离 PG16，使用真实 API角色和当前授权，注入合成身份；并非短信登录/生产启动/UAT。浏览器未来开始日2027-10-07结果0，同日2026-10-06结果1，H5清除筛选结果1；全部5次概览GET返回200。原生date控件须真实键盘输入触发change；不因自动化fill问题改产品实现。
- PC1366、H5 390无横向溢出，截图 `artifacts/demand-overview-pg16-pc-v20.png`、`artifacts/demand-overview-pg16-h5-v20.png`。三级审批已批准，但未分配、未出库、未发运、个人仓未入账，状态分离正确。查询前后需求/审批/库存/审计/outbox事实保持不变。收据 `artifacts/formal-0165-integration/demand-overview-browser-final-v20-20261006.json`。
- 18087有限预览已主动正常停止以完成快照/源码/停库核验，不能再提供为在线入口。18086固定合成预览不是PG页面。下一步复用已存在的签名SKU/控制数发布、正式期初盘点与复核过账，准备完整申请到个人仓入账流程；仍不能用概览查询代替业务PC/H5闭环。
- SSH已按旧备份脚本的root账号于04:54实际登录成功；候选数据库健康，`.env`/`.env.production`仍缺失。未改凭据、部署或切流。完整V1、真实渠道/UAT、迁移/期初、性能、连续三日对账、灾备/回滚门槛全部保留，目标active。

## 2026-10-06 成色正式HTTP双模式通过，概览长期PG门禁已接入

- **64509 exit0**：候选v10/2511项，数量 `run-u0kb4tnb` 和SN `run-w1cmspcb` 均 stopped/passed/serverExitCode0；各1785项门禁源码与候选全量源码逐字核验。真实完整应用HTTP/API角色事务、提交/区域/总部/取消/拒绝/撤回/释放/执行、原请求恢复、缺失请求封存、迟到写拒绝及保留事实拒降通过。使用注入合成身份和FakeStorage，未验真实短信/OSS/UAT。收据 `artifacts/formal-0165-integration/condition-formal-http-dual-final-v10-20261006.json`。64509已终止成功，不再当作运行或重新轮询。
- 当前原仓相对候选11份既有文件不同、15份新增，差异清单在同收据：既有成色业务模块字节相同；差异涉及CI/本地测试工具、维护版回滚、readiness测试和新概览的main/App挂载。没有将候选HTTP通过说成当前原仓完整相同源码通过。
- 新增 `backend/tests/pg16_material_request_overview_gate.py`，接入既有完整PG16审批路径，在三级已批准后用只读事务核对概览、入账仍未发生、日期边界与工程师无区域报表权限。**20460 exit0**：本地新库 `artifacts/local-demand-overview-pg16/run-9dt6lbus` 实际执行这个持久门禁的审批中分支、完整mounted HTTP、快照不变检查；1793项源码逐字一致，stopped/passed/serverExitCode0。**73133 exit0：63项CI拓扑测试通过 /30.77s**。正式CI中的已批准分支尚未实跑，远端GitHub门禁未执行，不提前算通过。
- 当前完整原仓绑定为 `original-integrated-source-v20-20261006.json` **2526项**，全量hash核验；产品概览代码相对v19不变，仅增加长期PG检查及CI调用。收据 `demand-overview-pg16-final-v20-20261006.json`。此前43247回滚双模式、18698后端19项、4031路由110项、46863概览18项+类型/构建通过继续按各自准确范围复用。
- 当前没有需要继续等待的本轮数据库进程。可见预览74047（18086）与组合入口34695（18085）保留；前者是明确标识合成数据的实际概览组件，后者登录stub。下一项补已批准PG检查及当前原仓业务PC/H5闭环，再继续逐SKU数量/导出和其他V1功能；真实云资源、生产配置/UAT/全量迁移/三日对账/性能/灾备仍独立待验。保留全部未提交改动；未提交、推送、切流或声明上线，目标active。

## 2026-10-06 需求履约概览已整合，PG16读取通过，维护版回滚双模式通过

- **43247 exit0**：原仓v16的Git0164旧历史数量 `run-8k3m3nab` 与SN `run-kh4in83w` 均 stopped/passed/serverExitCode0；519/531行旧事实、359个函数OID和11个准确原请求保留。原Git0164升级前启动，0166/0167升级、权限增量、回退0164后**明确维护版**启动、再升级回查均通过。两份1787项源码全部逐字核验后才合入新概览。收据 `artifacts/formal-0165-integration/git-history-maintenance-dual-final-v16-20261006.json`。原Git未经维护直接回滚仍不受支持；原失败未覆盖。这不是生产灾备验收。
- 概览五个后端文件已从stage按SHA/目标不存在检查合入原工作树，并在main挂载 `GET /api/v1/reports/material-requests`、纳入框架错误no-store边界。10个独立状态轴及三级审批当前版本最新尝试，单条SQL一致观察，当前总部/区域需求read权限与读后撤权复查；个人与外部审批身份不能据此获取区域报表。创建时间为含起点、不含终点的区间，零结果仅表示成功查询为空。
- PC/H5页面、严格客户端解析、角色/权限菜单与直接URL守卫已整合，入口 `/xx/reports/material-requests`。首轮视觉检查发现通用panel没有全局样式，已补模块内卡片与手机布局。**4031 exit0：110项路由/策略回归；46863 exit0：18项最终概览测试、tsc、warehouse构建通过；18698 exit0：19项后端普通入口测试**（含主应用401/404/405/422隐私响应）。先前测试mock初始化返回值和测试identity可选字段类型错误均已修正，不改写失败日志。
- **85353 exit0**：新自有PG16 `artifacts/local-demand-overview-pg16/run-n0_niiju`，真实迁移至0167、默认API角色、正式草稿与提交、聚合读取、半开日期范围、个人身份拒绝及实际挂载HTTP通过；查询前后需求/审批/库存/审计/outbox快照不变。1792项门禁源码全部hash核对。身份是合成注入，不能当短信登录或UAT。首次25655因测试让API读取受保护的alembic_version表失败，已改为迁移角色读取版本，未放宽权限；原失败库已正常停止保留。
- 当前原仓绑定 `original-integrated-source-v19-20261006.json` **2525项**；原生新查询证据对应1792项后端/脚本/部署/CI源码。概览整合收据 `demand-overview-integrated-final-v19-20261006.json`。旧v16完整绑定不再代表当前前端/概览代码，不以旧门禁覆盖新增代码。
- 可打开 **http://127.0.0.1:18086/** 查看实际概览组件；页面顶部明确标注固定合成数据、未连接业务数据库，句柄 **74047**。PC 1366与H5 390均无横向溢出、13个独立状态卡片可见；截图 `artifacts/demand-overview-{pc,h5}-v19.png`。浏览器原生日期填值未取得提交结果证据，日期逻辑有单元与PG测试，不能宣称浏览器完整筛选验收。预览未替代真实业务PC/H5闭环；组合入口18085继续为登录stub。
- **64509仍实读live**，候选v10固定源码未改；数量HTTP整轮已通过，SN已到实际execute提交，仍待整轮终态、源哈希及保留事实检查。继续原句柄，不重启。不要把本轮旧历史双模式通过写成成色HTTP双模式通过。
- 本轮接管后本机到118.31.37.87收到SSH banner；现有专用密钥以admin登录返回Permission denied(publickey)，命令助手仍可用。未更改凭据、重启、部署或切流。已有OSS/KMS资源问题保持待用户回答，不重复索要登录。
- 下一项：收取64509终态，补概览原生验证的长期CI入口与真实PC/H5业务联验；继续逐SKU数量/异步导出及其他一期报表、人员调拨/离职交接、UUID等完整V1缺口。真实渠道、配置、迁移/期初、500用户压测、连续三日对账、RPO/RTO和UAT仍独立未完成。未提交/推送/正式上线，目标active。

## 2026-10-06 Git 版0164历史已纳入CI，真实资源缺口已只读核对

- 保存的 `predecessor-source-v1` 是1568项、0164迁移头的未提交预激活快照，不能冒充Git正式发布来源。对比HEAD提交发现19份已有源码不同，并包含后来新增文件。该副本的既有数量/SN通过仍保留原证据边界。
- 历史提取器新增显式0157/0164选择，默认0157不变；0164锁定Git提交 `fc7c9269e19f6afd180a0aced55b565f03c244b6`、归档SHA `fb422a00689e22fb760c224a772049241ed56b8f42bf464548ed639ed90b2b0c`、**1421项**。新来源 `git-predecessor-0164/source/cloud_oam` 是真正Git历史夹具，不是另复制当前完整候选。已实际提取，0157旧1221项复用检查仍通过。
- 旧应用读取器兼容CI相对manifest路径与既有本地绝对路径，核对可用manifest摘要、路径边界、符号链接及实际字节。CI `pg16_condition` 现在为 `condition_http/legacy_history × quantity/serial` 四个独立自有库组合，固定0157与0164来源，聚合全部必需。**23587 exit0：63 passed /27.41s**，普通调度/边界测试通过；未执行远端CI或Linux原生。收据 `git-history-ci-progress-v15-20261006.json`。
- 原仓当前固定 `original-integrated-source-v15-20261006.json` **2512项**，四文件变更，本轮全部核对一致。**88893正在原仓运行**Git 0164数量/SN `--legacy-history-only`，日志 `git-history-native-v15.log`，仅前驱迁移通过，旧业务生成/升级/回退整轮待验。不要因之前1568项副本通过而提前宣布新Git来源通过，也不要重复启动或修改原仓固定源。
- **64509仍在未修改的候选v10**运行成色正式HTTP。数量五条路径已推进最后的总部批准，实际执行及最终历史/权限/拒降、SN整轮仍待验。两个原生句柄分别属于不同固定来源，继续原句柄。
- Chrome只读查看现有账号资源：OSS明确“尚未开通对象存储服务”，杭州KMS新版软件实例列表无记录、用户主密钥为0；仅覆盖这个账号/地域，不推断其他账号资源。截图 `artifacts/{oss,kms}-availability-20261006.png`；收据 `cloud-resource-readonly-20261006.json`。未购买、创建资源、读取凭据或改变权限，已回到原命令助手。已异步询问用户是否有其他地域/账号可复用资源；此问题只影响依赖真实云资源的配置，其他开发继续。
- 当前本机组合预览34695继续在18085；仍为登录stub。下一步收取64509及88893真实终态并修实际缺陷，再推进业务PC/H5和完整V1其他缺口。未提交、推送、部署或切流；目标active。

## 2026-10-06 数量/SN旧历史通过，CI已接入，前端全套通过与组合入口可打开

- **46976 exit0**：原仓v12数量 `run-30_zilb1` 与SN `run-_teo8y5a` 均实读 stopped/passed/serverExitCode0；分别保留519/531行、359个旧函数OID、11个准确原请求。0164真实旧业务→0166→0167、独立授权增量、旧事实只读回查、带旧历史回退0164后原应用启动、再升级0167通过。两个1785项门禁源及原仓2511项全部哈希一致。收据 `condition-legacy-history-dual-final-v12-20261006.json`；不是生产迁移/灾备证明，不再轮询46976。
- 按expected-before合入CI五文件到**原工作树**；四份原字节备份 `original-before-condition-ci-v12/`，新增准确0157历史提取器。独立 `pg16_condition` 数量/SN矩阵执行正式HTTP CLI，汇总必须等它成功；保留runtime/loss/static。实际1221文件首次提取、复用、篡改/错误提交/越界拒绝通过，正常脚本路径复验通过。**44138 exit0：89 passed /26.09s**（全部本地cluster/CI拓扑模块），收据 `condition-ci-integrated-final-v13-20261006.json`。未执行GitHub/Linux原生；0164旧历史CI接入仍待完成，不能以0157成色历史替代。
- **26555 exit0：155文件、2670项前端全套通过 /82.25s**。后端当前0167九文件回归 **76360 exit1：162通过、1失败 /173.91s**；唯一失败是报废历史测试把已合法推进的当前readiness仍当0166。保持冻结0165断言，新增精确0166→0167 before/after及SHA衔接；仅改测试，不改产品守卫。**13181 exit0：7 passed /22.91s**，报废readiness与完整历史链模块通过。收据 `integrated-client-readiness-final-v14-20261006.json`。不改写旧失败为全套新通过。
- 当前原仓绑定 `original-integrated-source-v14-20261006.json` **2512项**，全部哈希核对。候选仍v10/2511项，未修改正在运行64509的源码；原仓相比候选仅新增CI/本地版本识别及历史测试衔接，不新增业务变化。
- 可打开本机组合入口 **http://127.0.0.1:18085/**，私有入口 **http://127.0.0.1:18085/xx/**。复用现有 `preview_public_entry.py` 与真实Caddy路由，16条路由/资源/安全响应检查通过；官方Caddy 2.10.2 Mac arm64归档SHA及二进制记录在 `artifacts/caddy-native-2.10.2/verification.json`。这是本地开发工具，不替换生产软件。
- 浏览器实读：首页无登录表单、星星指向 `https://rscwz.cn/xx`，资料明确待导入；390×844 H5无横向溢出，私有品牌图标实际naturalWidth=512。早先仅启动私有Vite预览造成根路径公共图标缺失，改用完整路由后恢复，未改产品路径。旧4183服务已按PID/命令/cwd确认后停止。当前预览句柄 **34695**，只绑定loopback，登录是显式stub，不能宣称真实登录、业务PC/H5闭环或UAT。收据 `combined-entry-browser-preview-v14-20261006.json`，截图 `artifacts/{public,warehouse}-entry-{pc,h5}-v14.jpg`。
- **64509仍实读live**：正式成色HTTP数量轮已通过取消批准/释放、区域拒绝/释放、总部拒绝/释放及撤回；数量末尾、SN整轮仍待终态。只接续原句柄，不重启。下一步收取真实HTTP终态、修实际失败，再补0164旧历史CI与正式业务PC/H5闭环；人员调拨/离职交接、六类下游补偿、UUID、报表等完整V1缺口及真实渠道/部署配置/UAT/性能/连续对账/恢复回滚仍独立未完成。未提交、推送、部署或切流，目标active。

## 2026-10-06 成色HTTP推进，远端CI接入草稿准备完成

- 64509与46976仍经真实句柄确认运行；原仓v12/候选v10各2511项本轮哈希复核一致、无漂移。成色HTTP数量库 `run-u0kb4tnb` 已完成真实0157历史、0167正式迁移保留事实，并越过已封存迟到写入检查，打印 `condition HTTP actual COMMIT submit PASS`；不是数量或SN整轮终态。旧业务升级已打印 `upgrade-populated-0166 PASS` 与 `upgrade-populated-0167 PASS`，后续完整历史回查及回退仍待核验。
- 在候选外准备 `condition-ci-stage/` 五文件接入草稿：GitHub独立 `pg16_condition` 数量/SN矩阵，复用完整 `--formal-http` CLI及自建私有Unix socket新库；主聚合必须等待该矩阵成功，保留原runtime/loss/static门禁。依赖继续使用现有三个哈希锁。不是已合入配置，也没有执行GitHub任务。
- `prepare_condition_predecessor.py` 固定真实0157 git提交及归档SHA，拒绝路径穿越/符号链接/重复成员/错误条目数；已存在目录只核验，不覆盖。原git归档重算SHA与已验证历史归档完全一致；复用已有1221源文件核对通过，没有新复制完整候选。首次解包分支尚需聚焦验证。
- PostgreSQL版本识别草稿允许16.x发行版括注，仍拒绝15/17/beta/多行，实际SQL版本、PID、datadir、systemid、空库、私有socket证明均保留。阶段 **22 passed /4 deselected /0.50s**（排除依赖原相对路径的四项CLI测试）；91458 exit0，三个拓扑检查直接执行通过，聚合覆盖2401种结果组合；YAML解析与六步骤结构通过。不是Linux实机/完整普通入口或远端CI证据。
- 待应用清单 `condition-ci-stage/integration-plan-v1.json`，含逐项expected-before。先继续两个原生句柄、修复实际失败；终态后再验证/整合CI草稿，保留固定源码、历史失败及准确证据边界。其余完整V1与正式发布门槛未缩小，未提交、推送、部署或切流。

## 2026-10-06 同源整合、旧运行时通过，HTTP封存响应已修复并复验中

- **89988 exit0：4 passed /84.00s**。通用门禁当前head已明确0167，单头历史保持逐段断言，readiness完整验证0164→0165→0166→0167冻结正文与SHA。三文件已同步原仓v10/候选v9，2511项逐字一致；收据 `condition-current-head-alignment/final-v1.json`。
- 随后九份既有门禁/夹具更新当前0167准入，保留历史0166组件前置检查。真实旧历史升级分两步独立核验0165既有11项授权增量与0167新增8项授权增量，旧权限身份/显式deny/全部其他旧事实保持不变。**54804 exit0：126 passed /10.65s**，权限策略、夹具准入、CLI/CI调度聚焦检查；不是原生业务证明。
- **36329 exit0**：原仓v11 `run-rm_gvl4w` 原生PG16 runtime目录门禁 stopped/passed/serverExitCode0，1785项来源无漂移。正式升级0167、12类真实权限/函数/触发器/约束异常拒绝及精确恢复、空库降至0164、1568文件旧应用实际启动、再升级完整准入通过。收据 `condition-legacy-runtime-final-v11-20261006.json`。无合成业务事实，不替代业务回归或生产灾备演练。
- **12396 exit1**：候选v9 HTTP数量轮在首次封存测试停止，`run-ly8pim16` stopped/failed/serverExitCode0，1785项来源无漂移；SN未运行。准确原因：封存和只读回查均成功，测试故意再次POST已封存原命令时收到通用未知503，而不是明确封存409；不是封存或回查失败。收据 `condition-formal-http-failure-v9-20261006.json`。
- 已修复共享写前坐标检查：发现封存坐标后必须经过现有完整原命令/当前读取权限/不可变历史/审计证明，只有真实sealed+无库存效果+禁止重试才返回明确 `return_condition_request_sealed` 409；碰撞、不完整证据仍503，撤权仍403。所有原动作写入保护及数据库约束不变，不自动重发。**71009 exit0：245 passed /7.35s**，共享坐标与HTTP适配器，包括初始、撤回、执行、释放的已封存/未知/撤权分支。真实PG HTTP复验尚待终态。
- 当前原仓 `original-integrated-source-v12-20261006.json` 与同一候选 `source-binding-v10.json` **2511项逐字相同**；本轮同步11文件（九门禁/夹具加上述服务与测试），候选旧字节备份 `condition-current-head-alignment/candidate-v9-before-v10/`；收据 `condition-sealed-write-fix-v12-20261006.json`。原308项通过对应候选v8；本轮服务变化由245项增量覆盖，不改写旧结果为新全套通过。
- **当前仅两项运行中，禁止重启或修改其固定源码**：64509在候选运行 `--formal-http` 数量然后SN，日志 `condition-formal-http-v10.log`；46976在原仓运行 `--legacy-history-only` 数量然后SN，日志 `condition-current-head-alignment/legacy-history-v12.log`。旧63662/89988/12396/36329/54804/71009均终态，不再轮询。
- 下一步收取两原句柄并修实际失败；HTTP通过后还须将成色HTTP/历史升级纳入远端CI（当前CI矩阵尚只有scrap_http，没有成色HTTP项），并完成可打开PC/H5、完整V1其他功能及真实UAT/外部渠道/性能/连续对账/期初/备份恢复/回滚/生产配置。历史0157来源已定位固定git提交 `9dff36f7feca44626b82ceb6e40297b3732a22f0`，不能用当前模型重写旧历史替代原应用。未提交、推送、部署或切流，目标active。

## 2026-10-06 原仓整合完成，普通后端308项通过

- **63662 exit0：308 passed /605.92s**，七文件普通pytest入口完成，无阶段模块覆盖；候选v8全部2511项哈希无漂移。附件事件绑定、独立复核列表、读中撤权、历史/收货来源及HTTP适配器等均纳入。本轮为SQLite实际业务与ASGI适配器证据，不冒充真实PG16 HTTP；终态收据 `condition-integrated-normal-final-v8-20261006.json`。不再轮询63662。
- 核对原仓2445项及候选2511项后，将95文件实现差异及3文件当前head修复共**98文件合回现有主工作树**；32份原字节完整备份到 `original-before-condition-integration-v9/`，66文件新增，未丢弃原有未提交改动。原仓现注册正式0167元数据与路由，保留较新前端。原仓固定清单 `original-integrated-source-v10-20261006.json` **2511项**，收据 `condition-original-integration-v10-20261006.json`。
- 原仓比已通过308项的候选仅多三份测试/门禁版本对齐：显式当前head=0167、单头逐段历史断言、0164至0167完整冻结readiness正文与SHA链。**89988正在原仓复验四项**，日志 `condition-current-head-alignment/original-v10-tests.log`，尚未宣称修复通过。候选v8仍未改变。下一步收取89988，按同字节对齐候选，再启动真实PG16完整应用HTTP数量/SN门禁；其他完整V1必需门槛保持未完成，未提交、推送、部署、切流。

## 2026-10-06 正式数量/SN门禁终态通过，55文件已原地整合

- **60629 exit0**：数量 `run-9xua4568`、SN `run-ztyc4l08` 均实读 stopped/passed/serverExitCode0。正式0167迁移、默认权限、旧238表事实保留、实际审批与执行/释放COMMIT、永久请求封存及后续业务后准确只读回查、保留业务拒降通过。两模式各1776项门禁来源及候选v7的2475项在整合前已核验无漂移；终态收据 `condition-formal-business-dual-final-v7-20261006.json`。该轮使用合成人员与FakeStorage，不是HTTP、真实渠道或UAT验收；无需再轮询60629。
- 新增“成色纠正案件”私有列表入口 `/return-condition-corrections`，区域独立复核人无需成为原收货责任人即可发现当前授权范围内的待处理案件；总部可查看其授权案件。按原入库UUID分页，已关闭案件默认排除；读取前后复核当前身份、组织范围与权限，禁止把其他地区案件或迟到响应带入当前页面。历史、附件与十二动作仍按原案件办理。
- 前端 **54199 exit0：20文件248 passed**，16908类型检查exit0，仓库版构建exit0。后端阶段 **97419 exit0：2 passed /42 deselected /133.05s** 覆盖数量/SN实际SQLite案件列表、跨区隔离及只读事实；之后还调整了最后权限核验顺序并增加读中撤权反例，最终版本须以整合后普通入口验证为准，不借旧通过结果覆盖新版本。
- 原正式门禁结束后，按逐项expected-before计划向**同一个候选原地整合55文件**：19份被覆盖的旧字节已备份，36份新增，未复制新的完整候选。收据 `condition-read-ui-integration-applied-v1-20261006.json`；备份 `condition-before-read-ui-v7/`；候选绑定 `condition-settlement-service-candidate/source-binding-v8.json` **2511项**，原仓绑定 `original-integrated-source-v9-20261006.json` **2445项**，本轮复核全部匹配。
- **63662实轮询仍运行**：普通 `PYTHONPATH=backend .venv/bin/python -u -m pytest` 运行适配器、历史/收货/列表HTTP、真实案件读取、证据、附件下载、独立复核列表及原文件服务七文件；日志 `condition-integrated-normal-v8b.log`。此前49586因未设置PYTHONPATH而collection失败，未执行业务测试，保留 `condition-integrated-normal-v8.log`。不要重启63662或修改其固定源码。
- 下一步：收取63662终态并校验源码，通过后在同一候选启动新 `--formal-http` 数量/SN门禁；修复实际暴露问题，再同源整合原仓及可打开PC/H5联验。当前阶段收据 `condition-inbox-integration-progress-v1-20261006.json`。完整V1、远端CI、真实渠道、UAT、性能、三日对账、备份恢复、回滚及生产配置仍未全部验收；未提交、推送、部署或切流。以下较早“运行中/待整合”记录保留历史含义，以本节为当前状态。

- 补充版本对齐发现：**90998 exit1，2 failed /63.42s**。通用 `test_postgresql16_release_gate.HEAD_REVISION` 与迁移历史测试仍为0166，候选真实单头已0167；失败日志 `condition-head-alignment-v8.log`。已在候选外准备三文件修复（当前版本、完整0164→0165→0166→0167冻结readiness链），计划 `condition-current-head-alignment/plan.json`，尚未应用；等63662终态后再写入固定候选并聚焦复验。另列明依赖当前head的历史业务门禁需审查，候选组件明确0166前置锚点不能批量替换。原仓与候选95文件差异已记录 `condition-candidate-to-original-review-v8-20261006.json`，未应用。

## 2026-10-06 真实PG HTTP门禁已实现，待原正式门禁结束后运行

- 在同一有限阶段增加 `backend/tests/pg16_return_condition_http_gate.py`，并给原原生CLI增加互斥 `--formal-http` 模式。复用现有0157真实旧应用夹具、正式0167迁移、默认权限和保留事实拒降；不再复制完整候选，不绕过现有SQL/COMMIT检查。
- 新模式通过完整FastAPI应用ASGI路由调用真实API数据库会话：十二动作分别覆盖证据退回/补充、区域核实、退回区域、总部批准/取消、两级拒绝、撤回、释放和执行；新请求只读回查、明确封存及封存后拒写、写权撤销后旧结果恢复、公开文件上传/完成/事件绑定下载、历史及收货来源读取均有断言。登录身份是显式注入的合成用户，仍实时加载真实principal；存储为FakeStorage，不能声称真实身份登录、OSS、网络部署或UAT完成。
- **当前仅实现和入口检查通过，真实HTTP业务尚未运行**：两份Python源码compile通过，CLI `--help` exit0，冲突 `--formal-http --formal` 明确exit2并在触碰数据库之前拒绝。没有编造实际HTTP数量/SN通过结果。阶段绑定v7 **15项**，整合计划v7 **52文件**（延续上一批50文件）；收据 `condition-native-http-preparation-v1-20261006.json`。
- 60629实轮询仍live，SN已完成总部批准/取消批准实际COMMIT，后续结算、全轮回读与终态仍待核验；同一候选v7 **2475项hash无漂移**。只读采样PID74817（65659 exit0）显示Python计算及psycopg数据库往返，不是Python函数级热点定位，不能当性能验收；采样 `condition-formal-business-v7-sample-20261006.txt`。
- 下一步：取得60629终态并核验两个cluster的checks/source-manifest/cluster-state；再按52文件expected-before原地整合、运行普通入口聚焦及新 `--formal-http` 数量/SN门禁，修复其实际暴露问题，推进可打开PC/H5及完整V1验收。原源码绑定仍v8 **2443项**；未改运行候选、未提交/推送/部署/切流。


## 2026-10-06 成色纠正附件闭环与原报损附件响应修复

- 有限阶段新增 `return_condition_file_download`：无绑定附件即使上传人本人也拒绝；下载须匹配唯一原事件、完整入库/纠正历史、原附件完成上传摘要及当前区域inventory/stock_operation读取权限，签发前再次校验绑定、历史与权限边界。旧上传人停用、失去写权限不代替当前独立复核人的读取授权。复用原文件服务短时签名与审计，不修改库存。
- 修复实际公开契约缺口：`formal_file_schemas.FilePurpose` 原先仍拒绝 `return_condition_evidence`，使客户端上传无法真实使用；阶段现同步接受上传/完成/下载响应。对应旧“公开用途关闭”测试更新为准确用途允许/近似用途拒绝，保留全部上传授权和未绑定下载拒绝测试。待与正式0167候选同源整合后才可启用。
- 前端办理页按原事件显示附件核验/打开入口，申请URL前后检查身份/范围、精确事件与附件，严格接受完整文件接口schema/status/GET合同与HTTPS短时链接；切换身份、案件、动作丢弃迟到响应，过期自动移除链接。另修复原 `lossReviewAdapter.download` 遗漏schema_version/status/method导致真实响应被拒绝的问题，新增完整响应正例，保留不安全URL反例。
- **58066 exit0：2 passed /182.61s**，数量/SN真实SQLite业务历史及实际文件路由函数通过；FakeStorage，非ASGI/PG HTTP/真实OSS。日志 `file-download-v3.log`，阶段v5。此前14560因阶段旧响应类未刷新失败、23656因测试误把Pydantic请求当dataclass失败；均保留失败记录，不放宽产品校验。**41152 exit0：38 passed /4 deselected /10.24s**，既有文件授权/完整性及历史/收货ASGI错误回归；阶段v6只更改加载顺序及旧测试期望，应用源码与v5逐字相同。
- **30603 exit0：17文件234 passed**；**3119 exit0：另2文件11 passed**（原报损附件修复）。最终 **72176类型检查exit0**、仓库版Vite构建exit0；日志 `file-client-v2.log`、`loss-file-client-v1.log`、`file-types-v2.log`、`file-build-v2.log`。234对应原源v7，随后仅原报损adapter/测试变化由11项增量及最终类型/构建覆盖；不冒充最终全后端或全前端回归。
- 当前原源码绑定 `original-integrated-source-v8-20261006.json` **2443项**，阶段 `source-binding-v6.json` **13项**，待整合计划 `condition-read-ui-integration-plan-v6-20261006.json` **50文件**，逐项expected-before已核对。终态收据 `condition-file-ui-final-v1-20261006.json`；所有路径相对 `artifacts/formal-0165-integration/`。工作树差异检查通过，无丢弃、提交、推送、部署或切流。
- **60629仍live**；候选v7 **2475项无漂移**。SN正式门禁已推进到return_evidence/supplement/verify_region实际COMMIT及多类封存，整体未终态。接续原句柄，不改运行候选、不复制完整候选。终态后按50文件计划原地整合、普通入口验证，再完成真实PG HTTP事务与可打开PC/H5验收；原始完整V1、CI、真实渠道/UAT、性能、三日对账、期初迁移、灾备/回滚及生产配置仍是必需项。


## 2026-10-06 成色纠正办理页及区域收货来源入口

- **终态补充：43713 exit0，4 passed /270.22s**。数量/SN实际SQLite历史均完成按准确收货单发现原成色异常明细、真实纠正提交前后回读，以及PRAGMA query_only和数据库快照不变。其余两项为历史HTTP错误分支；FakeStorage/合成授权边界保留。不要再轮询43713。
- 新收货来源路由补充ASGI准入验证：**44928 exit0，4 passed /2 deselected /2.26s**，历史与收货两入口均验证403在业务读取之前、SQL/附件错误503及非法参数422脱敏、no-store/rollback；不是实际PG HTTP。日志 `receipt-http-v1.log`，无应用源码变化，仅扩展HTTP测试。当前阶段绑定v4、待整合计划v5（40文件）；终态收据 `condition-receipt-ui-final-v1-20261006.json`。下一步优先补原附件的事件绑定下载授权，等待60629正式SN终态后原地整合，不再复制完整候选。

- 原工作树新增 `FormalReturnConditionPage`，接入十二类动作准备、单次提交及已有原请求恢复；路由 `/return-condition-corrections/:inboundLineId` 按库存/库存作业查看权限及总部/区域角色准入。总部原异常明细有“办理成色纠正”入口；区域“退回收货与入库”在已过账且有破损接受量的报损收货单上提供来源核查，按真实收货/包裹/原处置坐标进入准确入库明细，不要求手填UUID。仍待后端同源整合，不能称可用线上页面。
- 明确选择案件与动作；提交/区域核实/执行/释放要求现场确认，SN须输入实际物料号并逐件扫码或录入，重复、不唯一、错误标识不计数。准备使用最新来源摘要或案件末事件，身份/权限/页面变化作废确认；确认展示数量、成色方向及本次SN。审批与库存执行保持分离。附件上传保留 `return_condition_evidence` 用途，未知写入保留原请求，不自动重发。
- **65324 exit0：4 passed /182.27s**，阶段案件读取增强SKU、物料名、单位和SN/二维码，数量/SN真实SQLite历史加ASGI错误测试通过（FakeStorage/合成授权）。对应阶段 `source-binding-v2.json` 和 `read-tests-v5.log`。办理页版本 **82094 exit0：13文件192 passed**，78767类型检查exit0、仓库版Vite构建exit0；收据 `condition-operation-ui-final-v1-20261006.json`。此前70208为191通过/1失败，恢复面板尚未完成渲染即断言按钮；添加明确控件等待后同组通过，未放宽恢复规则。
- 区域来源发现新增阶段 `GET /return-condition-corrections/receipts/{receipt_id}`：核验当前接收责任、原验收、独立入库、完整退回图和每条成色案件，读后再次校验范围及历史；原成色无异常返回空列表，不能当作执行过纠正。**43713运行中**，日志 `condition-read-ui-stage/read-tests-v6.log`；数量/SN真实业务历史验证尚未整轮终态，不记通过，继续原句柄。首个启动因工作目录错误未执行测试，随后在现有候选正确目录启动此句柄，没有重启运行中的测试。
- 含区域入口的当前前端 **31371 exit0：16文件219 passed**，35750完整类型检查exit0、仓库版Vite构建exit0；日志 `receipt-client-v2.log` / `receipt-types-v2.log` / `receipt-build-v1.log`。为合成HTTP/UI测试，数量/SN真实后端与PG HTTP、UAT分别验收。收据 `condition-receipt-ui-progress-v1-20261006.json`。当前原源码绑定 `original-integrated-source-v6-20261006.json` **2441项**、阶段v3 **7项**、待整合计划v4 **40文件**；v5原仓/阶段v2收据保留上一版本边界。
- **复核发现剩余实际缺口**：通用 `formal_files._authorize_download` 仍明确拒绝 `return_condition_evidence` 下载。因此虽然新附件可上传，审批人尚不能查看原案件附件，不能把当前办理页称完整审批验收。下一步实现不可变事件绑定+当前区域读取权限下的附件下载、过期链接/撤权测试，再完成同源整合与实际PG HTTP联验。
- 60629已实轮询live；数量模式正式权限下release/execute实际API COMMIT与保留业务拒降已PASS，SN已完成旧业务及0167升级保留事实，仍待整体终态。候选v7 **2475项**逐项无漂移，未向运行候选复制任何新源。原正式V1、远端CI、渠道、UAT、性能、连续对账、期初迁移、灾备/回滚及生产配置门槛全部保留；未提交、推送、部署或切流。

## 2026-10-06 客户端原请求恢复增量

十二动作原输入摘要已与实际Python规范化函数一致，恢复面板挂到原纠正页；同明细互斥、发送前持久化、失联只读回查、明确封存与结果回读已实现。聚焦153通过，补充面板4通过（3重叠），类型/仓库构建通过；传输业务结果为明确模拟，不能当实际HTTP/PG/UAT。原树v4/2433项，候选v7/2475项仍固定；26文件整合计划等待60629正式业务门禁终态。新提交/审批/结算表单和逐件实物输入、区域来源入口仍待接通；完整V1上线目标未达成，未发布。详见最新交接及 `condition-client-recovery-final-v1-20261006.json`。


## 2026-10-06 案件读取/前端入口增量（未部署）

成色纠正历史读取完成有限阶段实现；数量/SN真实服务历史读取2项通过，独立HTTP错误/权限2项通过，前端55项及TypeScript/仓库版构建通过。历史份额、审批、释放与实际纠正分开展示；新增读取不授予写权限、不自动重放请求。服务候选v7保持2475项不变，原工作树前端推进到v3/2423项；15文件整合计划待唯一live60629终态后应用。当前后端尚未挂载新读取路由，不能算用户可验收的同源页面。完整提交/审批/结算与请求恢复UI、PG HTTP、正式渠道/UAT及完整生产门禁仍未完成。准确边界见 `CONTINUE_DEVELOPMENT.md` 最新节和 `condition-read-ui-final-v1-20261006.json`。


## 2026-10-06 数量/SN完整候选联验通过，0167与HTTP已原地整合并验证普通入口

- **64414已exit0**：数量 `run-vy639gg7` 与SN `run-_l9qjsoq` 均实读 stopped/passed/serverExitCode0，分别1754项门禁源码及同一v6候选2453项源码hash无漂移。两模式完成真实初始提交、七次审批COMMIT、九动作缺失请求封存、release/execute、两类结算封存、晚到写入阻塞/拒绝和后续业务后的旧请求/封存SQL只读回查。收据 `condition-complete-native-dual-final-v6-20261006.json`。仍为FakeStorage与候选测试授权，不能替代正式0167默认权限联验；不再轮询或重启64414。
- 按 `condition-formal-integration-plan-v4-20261006.json` 逐项验证后，**46文件原地合入现有服务候选**，没有复制新的完整候选；覆盖前的24份文件原字节保存在 `condition-before-0167-files-v6/`，整合日志 `condition-formal-integration-applied-v1-20261006.json`。新增22文件，服务候选绑定推进为 `condition-settlement-service-candidate/source-binding-v7.json`（2475项）。原工作树2418项源仍未变，原HEAD/未提交工作全部保留。
- HTTP已挂载至候选完整应用 `/api/v1/stock-operations/loss-reports/return-condition-corrections`：`POST /commands`、`POST /request-lookup`、`POST /request-seal`、`GET /sources/{inbound_line_id}`。12种动作按当前权限分流；回查只需读取权限；原操作人/完整输入/请求头/来源与结果严格绑定；验证响应后单次COMMIT，未知结果不自动重发。公开封存结果不返回原命令JSON或幂等键；来源待核实时份额为null，预览不授权过账且及时rollback释放锁。
- 阶段接口 **49620 exit0：218 passed /4.23s**，收据 `condition-http-adapter-stage-final-v2-20261006.json`；先前209/216/217轮是被后续覆盖的阶段检查，不相加。接口服务结果为明确测试替身，覆盖真实完整应用挂载、未登录401及404/405隐私响应，但**尚无真实PG HTTP业务COMMIT或PC/H5验收**。
- **15088 exit0：266 passed /102.15s**：整合后使用普通pytest入口，权限/模型/运行目录/SQLite/HTTP五模块通过，真实独立子进程注册夹具通过；未用阶段加载器或捕获替身。收据 `condition-formal-normal-focused-final-v7-20261006.json`。不是完整后端或远端CI。
- **57966 exit0**：普通默认CLI在自有PG16 `run-6rd_iski` 全链实际迁移至0167、实际API目录校验、多授DELETE与禁用触发器拒绝、空库降级/再升级、可选对账采集角色配置/幂等/旧head拒绝通过。stopped/passed/serverExitCode0；**1776项完整门禁源与2475项候选源hash均核对一致**。收据 `condition-formal-normal-native-final-v7-20261006.json`。修正了旧CLI依赖Git发现导致隔离候选基础源码漏记的问题；历史38项阶段源收据保留原边界。
- **60629为当前唯一新业务句柄，运行中**：同一v7候选 `condition-formal-business-v7.log`，命令 `scripts/run_local_pg16_return_condition_checks.py --formal`（沿用既有postgres-bin及准确0157前驱路径）。真实0166旧历史升级至0167，核验原字段/旧权限行保留及身份版本推进；不临时补成色动作权限；复用完整数量/SN业务，末尾执行有真实留存事实时Alembic降级拒绝及事实/目录不变。该新轮尚未终态，不能计为正式业务通过。运行期间固定v7源，不再启动重复候选。
- 接下来先核验60629终态，再做真实PG HTTP事务与PC/H5来源选择、审批/结算及原请求保存/恢复；随后按准确通过源码合入原工作树。来源列表/完整案件时间线与PC/H5仍待开发，HTTP不能单独算可用业务页面。其余正式V1业务、最终同源CI、真实渠道、UAT、性能、连续三日对账、迁移/期初、灾备与回滚仍是独立必需验收。未提交、推送、部署、切流；完整目标active。


## 2026-10-06 成色纠正迁移与正式模型已分阶段接通，数量完整联验通过

- **56275 exit0：1 passed /43.58s**。同一有限 `condition-migration-stage/` 中增加真实 Alembic 0167 wrapper、精确 readiness/运行时目录和完整十表模型；在新自有PG16从空库实际全链升级到0167，实际API身份完整启动校验通过。多授DELETE与禁用成色触发器均被拒绝，恢复准确目录后通过；空库降级到0166、再升级也通过。`run-mwph0sk2` stopped/passed/serverExitCode0、测试记录的阶段源码hash逐项复核。收据 `condition-migration-runtime-final-v3-20261006.json`。81230失败来自测试把原O模式触发器恢复成ALWAYS，已修正测试，未放宽产品校验。
- **67346 exit0：1 passed /41.27s（该阶段最新）**。三个每日对账入口的head绑定已推进到0167；自有PG16 `run-6bb420sr` 实际采集角色配置、重复配置无变化、旧0166拒绝、加入仅限SELECT的采集角色后完整API启动通过，同时再次执行真实全链迁移/目录反例/空库往返。stopped/passed/serverExitCode0与阶段源码hash复核；收据 `condition-migration-runtime-final-v4-20261006.json`。没有执行真实日终对账业务，也没有授予生产角色。
- SQLite固定结构包含4张父表扩展、10张新表、34条写入保护；**13616 exit0：27 passed /25.38s**，实际Alembic往返、保留旧行/触发器/视图、故障回滚、所有新表拒绝SQLite业务写入、留存行拒绝降级通过。留存行是明确构造的SQLite反例，不是PG真实历史留存证明。
- 完整模型保持既有ORM父表对象，新增明确0166历史结构视图；原0164工具通过该视图排除后来成色表。**44361 exit1：65 passed/1 failed**，唯一失败为旧测试假定键表尚未注册Base；按已激活模型修正该假设及同类封存测试，**47527 exit0：172 passed /5.50s**。两段累计覆盖237项通过，不能写为全后端新执行。较早70678约束顺序比较失败也保留，改为已测试的仅表约束顺序归一，未忽略列顺序或SQL字面量。收据 `condition-formal-model-schema-final-v1-20261006.json`。
- **64414数量整轮通过，SN仍运行**。数量 `run-vy639gg7` 实读 stopped/passed/serverExitCode0，1754门禁源文件和2453候选源码hash无漂移；初始真实提交、七次审批、九种审批缺失请求封存、实际release/execute、两种结算封存、晚到旧键拒绝、释放后新申请及旧请求/封存SQL只读回查通过。收据 `condition-complete-native-quantity-final-v6-20261006.json`。继续同一进程SN `run-_l9qjsoq`，不改v6源码、不重复启动；FakeStorage与候选测试授权边界不变，不能代替正式0167权限下的业务联验。
- 本轮原工作树2418项与服务候选2453项源码均未改动。阶段最新清单 `condition-migration-stage/source-binding-v4.json` 固定46文件，**仅阶段已注册模型/迁移，尚未合入原工作树或在现有服务候选启用0167**。93117、18411、54797、13616、76756、70678、44361、81230、56275、47527、67346均终态，不再轮询。
- 下一步：整理可独立落库的正式测试；64414终态后按 `condition-formal-integration-plan-v1-20261006.json` 核验并把31个文件（其中14新增）原地合入同一服务候选，验证正式权限下的已有历史升级/降级拒绝及完整业务，再接HTTP/PC/H5。完整V1其他功能、同源码远端CI、真实渠道、UAT、性能、连续对账与灾备仍须验收。未提交、推送、部署或切流，完整目标active。

## 2026-10-06 固定SQL生成、迁移事务与八动作权限策略通过

- **96061 exit0**：在新自有PG16 `run-4wb66b91` 从准确0166生成完整成色纠正DDL；720条固定SQL、10张新表、60个新建/替换函数。编译事务回滚与固定SQL重放后的完整表/函数/ACL目录逐项相同，2453项v6候选源码无漂移。收据 `condition-frozen-catalog-final-v1-20261006.json`。未推进Alembic、未注册正式ORM或开启接口。
- 新增有限 `condition-migration-stage/`：冻结catalog/probe、准确前序CHECK恢复表达式和transition；复用现有正式迁移的前序版本、直连无特权migrator、PG16、隔离级别、UUID依赖、目录校验与留存保护设计，保留无CASCADE路径。该阶段未改运行中64414源码。
- **93836 exit0：13 passed /0.51s**。冻结八动作权限策略：区域申请/补充/撤回/执行/释放/核实，总部复核/取消；保留显式deny和自定义身份，重复应用无变化，相关用户身份版本只推进一次，冲突/溢出提前拒绝、事务回滚及降级保留授权数据。SQLite安全测试收据 `condition-permission-policy-final-v1-20261006.json`。
- **55348 exit0：1 passed /32.20s**。真实PG16 `run-d_syp4b0` 安装固定SQL、八项默认权限（无用户种子）、重复权限无变化、错误目录侧拒绝、空库降级/再次升级目录精确一致、后续失败整笔回滚、API角色迁移拒绝通过。stopped/passed/serverExitCode0及源码hash核验；收据 `condition-frozen-transition-final-v1-20261006.json`。**尚未测试已有业务留存时的降级拒绝，也尚未接正式Alembic wrapper/readiness/runtime目录**。
- 64414完整数量/SN联验仍运行，数量完成旧历史/升级/兼容/初始封存来源检查，无整轮终态。保持同一进程和v6源码，不重复启动。
- 下一步将冻结变更接正式迁移、readiness和运行时允许目录；补已有历史留存、多代升级及正式权限下的完整业务，再推进HTTP/PC/H5。`condition-migration-stage/source-binding-v1.json` 固定阶段源码；未提交、推送、部署或切流，完整目标active。

## 2026-10-06 六项历史回查通过，完整封存候选已整合并启动联验

- **45855 exit0：6 passed /889.48s**。数量/SN执行与释放四项及既有审批两项历史回查全部通过；v3分阶段源码逐项hash无漂移，SQLite封存/审计为明确种子。收据 `condition-settlement-closure-reads-final-v1-20261006.json`，不能代替原生登记器。
- **39441 exit1**：数量实际API release与execute均有COMMIT通过标记，但最后旧封存响应整体相等断言失败；返回的observed_ledger_cursor会随真实过账推进，不能要求与旧读取游标相同。已把断言改为所有其他响应字段完全相等，且新游标准确等于已提交交易最大游标并大于旧值。没有改产品保护；旧库run-upsfn68t stopped/failed/serverExitCode0、2452源码无漂移，失败收据 `condition-complete-native-v1-failure-20261006.json`。数量整轮失败、SN未开始，不能写为完整通过。
- 两原句柄终态后，**17文件封存扩展及上述断言修复已整合到同一个现有服务候选**，同时复制原仓九份已通过的较新静态测试，保留已有前端修复。固定清单 `condition-settlement-service-candidate/source-binding-v6.json` 共2453项；未复制新的完整候选，未合入原工作树业务源码。
- **72975 exit0：1 passed /4.59s**。补齐独立安装器的结算输入/扫描依赖和双向请求约束；原生完整依赖安装、ACL、实际API拒绝及事务回滚通过。run-9gzda6jl stopped/passed/serverExitCode0，完整v6与组件源码hash复核。收据 `condition-complete-installer-final-v6-20261006.json`；只是安装组件证据。
- **64414运行中**，唯一当前完整原生句柄：同一候选 `condition-complete-native-v6.log`，`--complete-candidate`，新增execute/release永久封存、迟到旧键拒绝及跨后续业务的旧封存只读回查；数量然后SN。旧39441/45855/48156/72975均终态，不再轮询或重启。
- 下一步继续64414，同时准备正式冻结迁移；通过后按同源码合入正式权限/HTTP/PC/H5，保留完整V1其他业务及真实渠道/UAT/性能/对账/灾备验收。目标active，未提交、推送、部署或切流。

## 2026-10-06 执行/释放封存扩展组件通过，历史回查继续

- 现有共同服务候选2452项与原仓2418项v2本轮逐项hash无漂移，39441仍实读运行；联合原生门禁已完成旧历史、当前升级、初始封存，并推进实际九动作审批封存。尚无数量/SN整轮终态，不重复启动或修改该候选。
- 新增有限 `condition-settlement-closure-stage/`，复用现有动作封存表与双向键约束，将execute/release纳入完整原命令、原申请人、当前动作权限和历史引用校验。保留原扫码文本和claimed摘要，不把关闭未知请求冒充原预检或当前库存可执行。SQL完整来源校验增加执行/释放原输入证明；安装器明确要求完整结算输入/扫描表和反向请求约束。未增加新的业务表、未注册Base、未授生产权限。
- **19142 exit0：172 passed /6.36s**。171项关系约束与真实PG16输入组件（136条检查）通过；run-hk_2kkdk实读stopped/passed/serverExitCode0，源码与checks/state哈希复核。收据 `condition-settlement-closure-components-v2-20261006.json`。仅结构和规范输入/摘要，不能称封存登记器或完整事务已通过。34252因未加载本地测试配置collection失败，55061因SQL CASE表达式缺括号而171通过/1失败，日志保留；修复测试接入与SQL语法，没有放宽输入规则。
- **45855实读运行中**：隔离测试进程仅加载分阶段的六个变更模块，不改39441候选文件；数量/SN×执行/释放4场景及既有审批2场景，验证真实库存后续变化、撤销新增动作权限后的旧封存只读回查、准确已执行请求和改扫码/改原命令拒绝。SQLite封存与审计明确为测试种子，不能代替原生登记。日志 `condition-settlement-closure-stage/reads-v1.log`，固定清单 `source-binding-v3.json`。
- 15文件整合计划 `condition-settlement-closure-stage/plan.json` 已准备，包含共同实际PG16生命周期增加两个结算封存、迟到旧键拒绝和之后旧封存回查；这些完整业务新增检查尚未运行。须等待39441、45855终态并核对源码，再原地整合，保留原仓九份较新静态测试，不另复制完整候选。
- 未提交、推送、部署、采购或切流。后续仍为完整结算封存业务证据、正式迁移/权限目录、HTTP/PC/H5与其余完整V1验收；真实渠道/UAT/性能/对账/备份恢复/回滚边界保持，目标active。

## 2026-10-05 释放重申请双模式通过，九文件静态修复已合入（最新）

- **46954 exit0：2 passed /460.26s**。v5共2452项候选源码逐项hash无漂移；数量/SN完整服务回归：占用份额超额拒绝、撤回后释放、重新取证且使用独立新请求完整申请、两级审批后执行、资产守恒及旧释放请求准确回查。收据 `condition-reclaim-service-final-v5-20261005.json`。使用SQLite、FakeStorage及明确键登记测试替身；不替代原生COMMIT、HTTP或UAT。55888夹具失败证据保留，不再轮询。
- **95992 exit0：150 passed /162.17s**。修复原50个失败涉及的九个测试文件，包含旧迁移与当前目录版本绑定、严格多头拒绝、当前23条实际路由、最新CHECK所需列、原始整行不变及历史0159夹具排除后来的0165子表。通过前失败45068（141通过/1失败）和68947（11通过/2失败）日志保留；最终历史readiness使用真实THROUGH_0165目录，反例断言具体拒绝原因。
- 已逐项校验候选2418项源码、原仓2418项与补丁hash，git apply --check通过后将**九个测试文件合入原工作树**，后端全部源码与通过候选相同；十份候选旧前端快照明确排除，保留原仓较新版本。新原仓清单 `original-integrated-source-v2-20261005.json`，收据 `static-fixture-repairs-integrated-v1-20261005.json`。原三片仍是50失败/10203通过的历史结果，不能把聚焦复验改写为全套新执行或远端CI通过。
- **39441运行中**：同一现有服务候选、v5源码固定，以 `--complete-candidate` 联合初始提交、九动作缺失封存与审批、释放、新申请、执行和旧封存回查。数量自有PG16库 `run-upsfn68t` 已完成old-upgrade/edge-provision；日志 `condition-complete-native-v1.log`。尚无联合业务或SN终态，不再启动独立重复候选。
- 下一轮先继续39441并核对终态及固定源码；随后整合服务候选时保留本轮九份新测试，补执行/释放未知请求永久封存、正式迁移/权限目录和HTTP、PC/H5。真实渠道、部署配置、UAT、性能、三日对账、备份恢复与回滚仍是独立未完成门槛。完整目标active；未提交、推送、部署、切流或采购。

## 2026-10-05 终态证据收齐、释放后重新申请与静态版本修复（最新）

- 原81477已失败停止：真实释放后，新申请被旧“已有后续移动”判断拒绝；数量模式未完整通过，SN未执行。失败收据 `condition-settlement-native-failure-v1-20261005.json`。不能重启原句柄或标为结算完成。
- 原68858两模式原生审批封存通过：九动作缺失封存、七次审批、迟到写入拒绝和只读回查。两库证据hash本轮重新核验；收据 `decision-seal-native-dual-final-v1-20261005.json`。不证明执行/释放、正式迁移或生产验收。原shell句柄已丢失，不能杜撰shell退出码。
- 共同24文件已按计划合入现有服务候选，保留较新前端测试；随后加入完整历史和准确可重新申请份额核验，未放宽SQL过账保护。原工作树仍未合入该候选。55888 exit1仅为initial_db夹具未导入，业务未进入；修复后2452项v5清单固定，**46954**继续 `condition-reclaim-v2.log`，数量场景已打印完整释放→新申请→执行→旧请求回查通过，SN及整组终态未完成。
- 原静态三片已全部停止：**50失败、10203通过、6跳过**；准确失败列表与日志hash已保存 `static-capture-terminal-failures-20261005.json`。旧99385/93201/20115不再运行。七文件修正复验 **45068 exit1：141通过、1失败 /106.50s**；剩余失败为0165测试引用已被0166合法推进的当前目录。继续在同一capture候选修正历史版本隔离、0098到当前函数hash链及0159合成夹具不混入0165子表；**68947**运行 `static-repair-focused-v2.log`，v3清单固定。均为测试版本/夹具修正，正式冻结迁移与业务守卫未修改。
- Chrome已重新接管，仅查看既有15:35成功回执，没有重复发云命令。候选环境配置缺失不阻断本地工作；生产切流、真实渠道、UAT、性能、连续对账及灾备仍未验收。未提交、推送、部署或修改现有生产服务。
- 下一步：收取上述两个原句柄终态，修复实际失败；通过后同一服务候选运行完整联合PG16生命周期，再补执行/释放未知请求永久封存、正式迁移/权限、HTTP与PC/H5。持续目标保持active，完整V1范围不缩小。

## 2026-10-05 审批双模式终态、共同schema与完整联验整合准备

- **9441 exit=0**。数量run-rr9z2ffh和SN run-3vdls9u_均stopped/passed/serverExitCode0，各1719项门禁源码无漂移；七次真实审批COMMIT、准确原请求SQL只读回查、效果失败整笔回滚及晚撤权提交拒绝通过。收据 `condition-decisions-dual-final-v1-20261005.json`。本门禁不含九动作永久封存或执行/释放；不再轮询9441。
- 新增 `condition-complete-schema-stage/`，整合初始七表、审批封存表及执行/释放输入和扫码两表，并为新原请求表增加审批封存的正向冲突查询及反向锁/提交约束。**3686 exit=0：1 passed / 18.40s**，run-ros3e9bc实读stopped/passed/serverExitCode0；共同10表、94个ALWAYS触发器、18个函数、105项表权限及函数权限、整体DDL回滚、8次实际API拒绝通过。收据 `condition-complete-schema-final-v1-20261005.json`。只证明共同结构/安装/权限，不是联合业务COMMIT或正式迁移。首轮1184测试配置未加载、42613产物路径不在运行候选所有权范围，均已保留失败日志；修正测试接入和产物目录，没有放宽保护。
- 新增 `condition-shared-runtime-stage/`：共同历史metadata、跨审批封存/结算输入的请求证据扫描、只接受准确封存行和审计的归一校验。**19374 exit=0：15 passed / 2.58s**，覆盖跨动作残留、错误/重复封存行、缺失/错审计、封存产生库存效果及结算缺原输入等拒绝。仅纯证据契约反例，非完整历史数据库证据；首轮55213断言异常类型写错，修正为实际InventoryReadError，业务拒绝未变。
- 已准备同一个PG16生命周期 `--complete-candidate`：初始提交→九动作缺失请求封存和实际审批→取消后释放→独立新申请→审批后执行→全部旧封存只读回查。按同一个图共同验证，不将独立通过累加为整体验收。代码仅编译，**尚未应用/启动**；整合清单 `condition-shared-runtime-stage/integration-ready-v1.json`，24个待应用文件，预期共同源码2450项。必须先等81477和68858终态并校验其固定源码，再应用到现有服务候选，不重复复制完整候选。
- 对比发现审批候选含10份较旧前端测试快照，整合清单明确跳过并保留已验证的新版本，禁止覆盖。原仓2418项、运行服务2431项和审批封存2435项均已逐项校验无漂移；所有未提交改动保留。
- 当前继续81477原生结算、68858九动作封存、静态99385/93201/20115原句柄。正式迁移/权限、未知执行/释放请求永久封存、HTTP及PC/H5、同源码远端门禁和真实UAT尚未完成；真实渠道、500用户压测、三天对账、RPO/RTO及切流验收仍保留。未提交、推送、部署、切流或修改生产。

## 2026-10-05 执行/释放服务双模式通过，原生结算门禁启动

- **47356 exit=0：4 passed / 1639.06s**。数量/SN × execute/release 实际服务过账、效果后故障整笔回滚、库存/SN方向、资产数量守恒、准确原输入只读回查及拒绝重放通过；2429项源码hash无漂移。收据 `condition-settlement-service-final-v1-20261005.json`。仍为SQLite及明确存储/键登记测试替身，不替代原生COMMIT、HTTP或用户UAT。不要重启或继续轮询47356。
- **90961 exit=0：1 passed / 14.38s**。原生新坏件账户前向准入安装器验证通过，run-z8cqn3_i为stopped/passed/serverExitCode0；严格核对前序函数内容、owner、私有权限和search_path，缺依赖/目录漂移/错误执行身份拒绝，DDL可事务回滚且保留旧分支。首轮12999失败为测试夹具未还原私有ACL，已修复夹具；未放宽产品检查。收据 `condition-settlement-account-final-v2-20261005.json`。只证明安装/权限，不证明库存业务。
- 47356终态后，将预备的3份原生门禁文件及1份账户准入模块应用到同一独立服务候选，**2431项**绑定 `condition-settlement-service-candidate/source-binding-v2.json`，原工作树保持固定。**81477已启动**：`--settlement-candidate`，日志 `settlement-native-v1.log`，数量自有库run-ezwuivwq；先验证完整旧历史/初始纠正，再取消后释放、独立新申请和审批后执行，覆盖实际API COMMIT、回滚、晚撤权及旧释放请求回查。尚未终态，不计原生结算通过。
- 共同schema与跨动作封存整合仍需完成，执行/释放未知请求永久封存、正式迁移/权限、HTTP及PC/H5可见交付仍未验收。继续81477、9441、68858及静态99385/93201/20115原进程。未提交、推送、部署、切流或修改生产；全部正式V1、真实渠道、用户UAT、性能、对账与灾备门槛保持。

## 2026-10-05 执行/释放事务授权终态，实际过账与恢复候选已接入

- **36522 exit=0：13 passed / 791.52s**。11项真实会话/事务边界和数量/SN实际历史permit全部通过，包括execute同维度坏件目标、取消后的准确原账户release、命令/数量/权限/游标/终态错绑拒绝，核验过程未过账。5份writer模块、2份准备依赖及原仓2418项hash无漂移。收据 `condition-settlement-permit-final-v2-20261005.json`；不要再轮询36522。SQLite/FakeStorage/测试键登记器边界保留，不代表原生库存结算通过。
- 新增 `condition-settlement-input-stage/`：独立原执行/释放请求及逐SN扫码快照两张关系表；规范完整请求不保存裸幂等键，准确绑定案件、终审事件及摘要、操作者、附件、原扫码SKU/SN/QR。历史回查使用不可变扫码快照，不拿后续主数据标签重写原输入。录入时独立核验当前物料/SN，SQL捕获锁定真实主数据行，提交时要求事件、原输入和扫描集合完整一致，禁止更新/删除/TRUNCATE。
- **22855 exit=0：38 passed / 5.76s**，规范请求、错案件/前序/身份/扫描关系拒绝、SQLite真实延迟FK提交及正式PG DDL编译通过。父对象为明确最小关系夹具，不是库存业务证据。**44586 exit=0：1 passed / 15.92s**，原生自有run-cy6__po4实读stopped/passed/serverExitCode0；完整实际metadata安装、事务回滚/重装、12个ALWAYS触发器、角色表/函数权限、孤立API输入及owner清空拒绝通过。未调用成功结算输入业务COMMIT；收据 `condition-settlement-input-final-v1-20261005.json`，固定 `condition-settlement-input-stage/source-binding-v1.json`。
- 新建独立 `condition-settlement-service-candidate/repository`，复制原2418源码并加入上述模块及实际writer/recovery；总**2429项**绑定 `source-binding-v1.json`。接入真实统一过账typed dispatch；完整历史包含新输入/扫描及一一验证；跨动作坐标扫描包含原结算请求；私有lookup核对全历史、完整原输入/键、唯一库存及业务效果，并在长查询末尾重新核验当前read权限。缺失保持unknown/retry_allowed=false，不自动重放。尚无HTTP入口或原仓合入。
- **47356仍运行**：`backend/tests/test_return_condition_settlement.py`，数量/SN × execute/release共4场景，实际服务过账、业务效果后故障整笔回滚、准确库存/SN方向、资产总量守恒、通知pending、SQL只读回查、改原输入/重复写拒绝、撤销动作权限后旧结果仍可读。日志 `settlement-service-v1.log`。未终态，不能计为实账通过；SQLite使用既有明确存储/键登记测试替身。候选源码在此运行期间固定。
- 原生新目标账户准入仍未接入，正式迁移/权限目录、全链原生COMMIT/迟到写入及未知执行请求的永久封存仍缺。此服务候选基于原七表加两表，不包含仍在独立原生验证中的审批动作封存表；最终需合并并重新验证完整共同schema，不能各自通过后直接称整体通过。
- 继续原句柄99385/93201/20115、9441（SN run-3vdls9u_）、68858（9动作封存）及新47356。原工作树非文档2418项保持固定，未提交、推送、部署或更改云端服务；完整V1、真实渠道、UAT、性能、对账、灾备与上线门槛不变。

## 2026-10-05 执行/释放准备双模式终态，事务permit候选继续

- **44016 exit=0：2 passed / 659.15s**，数量/SN真实历史夹具完成批准后execute准备、取消批准后release准备、原冻结份额/准确目标/当前SN三码及旧审批拒绝，完整数据库快照未发生业务变更。原仓2418项和5份准备源码hash逐项无漂移；收据 `condition-settlement-source-final-v1-20261005.json`。使用SQLite、FakeStorage及明确测试键登记器，不构成原生COMMIT或库存结算通过。不要再轮询44016。
- 新增独立 `condition-settlement-writer-stage/` 的执行/释放事务permit及私有dispatch，尚未接入原统一过账入口。绑定准确当前事务、原审批/案件/冻结份额、完整历史、合法终态、未过账事件、真实附件与规范命令摘要、准确目标维度、当前冻结余额/策略和SN三码；原初始冻结permit保持独立。新账户仅允许execute的同次创建目标，正式SQL账户准入仍缺。
- 首轮 **81250 exit=0：11 passed / 4.31s**，使用真实SQLAlchemy会话/保存点验证跨会话、事务提交/回滚/关闭、保存点结束、授权重叠和复制对象拒绝；合成来源只用于拒绝测试，不证明业务许可。此后增加规范事件/附件绑定校验，当前版本以 `source-binding-v2.json` 为准，不能用首轮结果替代当前版完整验证。
- **36522仍运行**：当前5份writer-stage源码、两份准备依赖和原仓2418项固定；运行11项边界复验及数量/SN实际冻结与审批历史的permit测试。日志 `condition-settlement-writer-stage/permit-v2.log`，目前11个通过标记，实际历史测试尚无终态。成功permit也不等于已经过账；测试仅核验并回滚新目标账户。后续必须补准确原结算输入持久保存/恢复、统一写服务、SQL目标账户准入、完整执行/释放COMMIT与正式迁移，不开放HTTP写入口。
- **9441数量模式已完整通过**：run-rr9z2ffh实读stopped/passed/serverExitCode0；1719项门禁源码hash无漂移，7次真实审批COMMIT、原请求SQL只读回查、业务效果整笔回滚和迟到撤权拒绝通过。收据 `condition-decisions-quantity-final-v1-20261005.json`。同一进程已进入SN run-3vdls9u_，尚不能计为双模式通过；此门禁也不含新9动作永久封存或执行/释放。
- 原句柄99385/93201/20115、9441、68858仍实读存活，新36522继续。源码未提交/推送/部署，完整V1上线门槛保持。Chrome命令助手已确认接管，未重复执行云命令或修改现有服务。

## 2026-10-05 审批封存回查双模式通过，执行/释放当前核验候选启动

- **48549 exit=0：2 passed / 865.60s**。v6的2433项源码逐项无漂移，数量/SN真实历史夹具中：另一审批推进后仍能只读回查旧封存、撤销动作权限后仍按查看权限读取、准确原输入/键冲突拒绝、库存事实不变通过。封存行及审计为明确读取测试种子，不证明原生受控登记或迟到写入COMMIT。收据 `decision-seal-recovery-final-v6-20261005.json`；不要再轮询48549。68858独立原生门禁仍运行，已完成old-upgrade与edge-provision，尚未封存业务终态。
- 检查发现现有condition过账permit只支持首次freeze，execute/release尚未实现。新增隔离 `condition-settlement-stage/`：准确执行/释放输入只接受原案件、前序事件及摘要、理由、附件和SN三码，不允许调用者覆盖数量/冻结份额/目标账户；准备服务重新证明当前申请人/保管责任、完整案件历史与终审事件、期初和当前余额投影、原冻结份额、追踪策略、SN位置与三码。执行目标按同维度damaged/available推导；释放必须为准确原账户。缺少执行目标时不创建账户，结果 `posting_allowed=false`，后续写服务必须在锁内重新核验并绑定真实事务permit。
- **29236 exit=0：10 passed / 5.57s**，输入越权字段、非结算动作、复制绕过校验及重复附件拒绝。首轮18280的10项初始化错误来自纯输入测试误用需要stock参数的业务autouse夹具；拆分文件后复验通过，没有放宽业务规则。两轮日志保留 `contract-v1.log` / `contract-v2.log`。
- **44016运行中**：`condition-settlement-stage/test_settlement_source.py`，数量/SN实际审批后execute准备与取消批准后release准备，核对冻结份额、目标方向、旧审批拒绝、SN三码及准备前后无业务写入；日志 `source-v1.log`。五份staged模块hash绑定 `source-binding-v1.json`，原仓2418项无漂移。尚无终态，不能计为执行/释放完成。进度收据 `condition-settlement-progress-v1-20261005.json`。
- 下一步继续原生封存68858、审批9441、静态99385/93201/20115及新44016；完成准备后仍需统一过账服务接入、事务绑定执行/释放permit、新坏件目标账户准入、SQL COMMIT约束、准确请求恢复及数量/SN实账验证。未提交、推送、部署、采购或改变云端配置，完整V1其余门槛不变。

## 2026-10-05 原初始纠正双模式门禁终态，17文件补丁已合入工作树

- **84788 exit=0**。数量 `run-so_rg17g` 与 SN `run-tw_tvz35` 均实读 stopped/passed/serverExitCode0；两份checks、1716项门禁源码hash和原2415项非文档源码在合入前全部核对无漂移。收据 `return-condition-0166-dual-final-v1-20261005.json` 固定准确结果与证据hash。真实API初始提交/冻结、原请求READ ONLY回查、原入库历史、唯一业务效果、初始永久封存、晚撤权与独立迟到写入COMMIT拒绝均双模式通过。不要再轮询84788。
- 此门禁仍使用FakeStorage，后续审批、动作封存、执行/释放、正式迁移和生产验收不在其通过范围。旧checks中 `allReadFactsUnchanged` 名称只表示候选业务提交之前的查询阶段，不能解读为整个门禁没有写事实；收据明确保留此边界。
- 前置等待已满足，按既有 `capture-client-integration-ready-v1-20261005.json` 再校验补丁hash/git apply --check后，**已应用17文件**：采集角色安全目录兼容5文件、原生后续审批门禁2文件、Web真实路由预加载测试10文件。原工作树**2418项expectedSource逐项一致**，其他未提交工作保留。收据 `capture-client-integration-applied-v1-20261005.json`；新的原仓基线为 `original-integrated-source-v1-20261005.json`，旧2415清单只作为合入前历史，之后不能继续拿它判定当前原仓无漂移。
- 实际原工作树 **61600 exit0：27 passed / 17.31s**，采集/报废权限目录及0166认证fence三模块聚焦复验通过，2418项hash再次无漂移。日志 `original-integrated-focused-v1.log`，收据 `original-integrated-focused-final-v1-20261005.json`；不要再轮询61600。此前完整Web及独立集成current-head证据保留原作用域；新审批封存候选未随此次补丁应用。
- 9441已有7次实际审批COMMIT日志标记，但后续历史回查、SN及整体终态尚未确认。99385、93201、20115、9441、48549、68858继续原句柄。68858已创建数量自有库 `run-rpvdm2_3`，尚未完整业务通过。未提交、推送、部署或改变云端配置；完整V1门槛仍未满足。

## 2026-10-05 九种审批动作封存原生事务门禁已启动

- 准备 `decision-seal-native-stage/` 三文件：完整封存生命周期门禁、九动作业务门禁及新增 `--decision-seal-candidate` 的驱动脚本。门禁在真实0157历史升级/初始纠正提交基础上安装第八表，覆盖 supplement、withdraw、verify_region、return_evidence、reject_region、return_region、reject_hq、approve_hq、cancel_approved 九种缺失动作的API封存COMMIT，并另用新键完成原审批路径，再在SQL READ ONLY下回查全部旧封存。
- 生命周期反例包括：保留审计命名空间伪造、直接表写入、缺审计提交、错客户端键、先出现旧业务键、事务内晚撤权；成功封存与独立迟到旧业务写入之间必须观察到真实锁阻塞，封存提交后迟到写入实际COMMIT必须拒绝。重复封存、撤销动作权限后读取和不可变行也纳入。只在新自有PG16运行；角色/附件存储仍为候选测试设置，不能替代真实渠道、正式迁移和生产验收。
- 为保留48549运行中的v6源码，另建 `decision-seal-native-candidate/repository`，**2435项**清单绑定 `source-binding-v1.json`；只复用venv，不要求前端node_modules。首次复制因不必要的前端依赖断言停止，已核对全部复制源码并原位补完；预先导入88317在文件未复制时失败，补齐后的**42421 exit0导入通过**。这不是业务门禁通过，未修改原运行候选或丢弃数据。
- **68858运行中**：默认数量后SN，日志 `decision-seal-native-candidate/repository/cloud_oam/decision-seals-native-v1.log`；准确进度收据 `decision-seal-native-progress-v1-20261005.json`。此时尚无完整业务终态，不计已通过。原2415项与新2435项源码逐项无漂移；运行期均保持固定。
- **48549仍运行**，其读取种子测试不替代68858。既有84788、99385、93201、20115、9441也实读存活。接续这7个句柄，观察超时不重启；任何真正失败先定位准确阶段再修复。未提交、推送、部署或改变云端配置；全部正式V1余项继续保留。

## 2026-10-05 审批动作封存服务与独立回查候选 v6

- 在独立候选新增 `return_condition_decision_seal_reads.py`、`return_condition_decision_sealed_recovery.py` 与 `return_condition_decision_request_seals.py`。登记服务持有库存/身份锁，先查询原请求，只有未知结果才重新准备并调用 SQL 受控登记、追加同事务唯一审计，随后准确回查；外层 COMMIT 成功前不视为永久封存。已找到或已封存请求走当前查看权限，不重新执行原动作。
- 八表回查使用独立 metadata，不改七表旧读取器和正式 Base。完整原命令、11域键摘要、来源入库、准确前序事件、原申请人/历史区域核实人与当前查询人、唯一审计和无库存/状态/通知副作用均校验；较新事件不会使旧封存失效。旧 claimed 摘要不会被当成预检通过，封存结果明确 `retry_allowed=false`、`current_stock_verified=false`、`original_preflight_verified=false`。保留跨动作残留证据拒绝与读后范围/游标/封存记录复查。
- **48549 正在运行** `backend/tests/test_return_condition_decision_sealed_recovery.py`，日志 `decision-seal-candidate/repository/cloud_oam/decision-seal-recovery-v6.log`。数量/SN使用真实历史业务夹具，封存行与审计为明确的读取测试种子；覆盖另一次实际审批推进后读取旧封存、撤销写权限后查看、原输入/键冲突拒绝及无写入。即使通过，也不替代 PostgreSQL 登记/双向 COMMIT 门禁；当前尚无终态。
- v6固定源码 **2433项**，原2415项逐项无漂移；15新增文件补丁 `return-condition-decision-seal-components-v6.patch` 已git apply --check，未应用。进度收据 `decision-seal-service-progress-v6-20261005.json`，完整终态需接续48549；运行期间不编辑候选现有源码。
- **9441有实际业务进展但未终态**：日志已有5次COMMIT（return_evidence、supplement、verify_region、return_region、verify_region），剩余批准/撤销和全部历史回查及SN仍不能计为完成。84788、99385、93201、20115、9441及新48549均实读存活；不重启。
- 下一步：读取48549终态并处理真实失败；将新的登记服务及安装器接入完整原生业务门禁，验证准确来源、唯一审计、晚撤权及跨表迟到写入的实际COMMIT/回滚，然后完成正式迁移、HTTP/UI及完整V1余项。未提交、推送、部署、改云端配置或采购；17文件原仓集成仍待84788双模式终态。

## 2026-10-05 审批请求封存安装器 v5 原生验证通过

- 在独立候选中补齐 `decision_seals.py` 与 `decision_seal_persistence.sql`：先为已有案件表增加准确来源复合唯一键，再建第八张封存表；组合原输入、历史来源、当前权限、受控登记、唯一审计和全局请求缺失约束。新封存与旧初始封存均纳入双向迟到写入检查，跨表锁与延迟校验触发器使用 ENABLE ALWAYS；完整历史源证明补入历史事件附件校验。原工作树及仍在运行的两份门禁源码未修改。
- **29621 exit=0，1 passed / 15.52s**。独立 PG16 `decision-seal-candidate/repository/cloud_oam/artifacts/decision-seal-install-v5/run-a_gbgdge` 实读 stopped/passed/serverExitCode0。使用真实完整 metadata 安装 SQL，验证新增来源唯一键、全部触发器目录和延迟属性、15 个函数的 75 项角色执行权限、表权限、3 次实际 API 越权拒绝，以及整套安装事务回滚后表/约束/函数消失并可重新安装。未用伪历史函数替换依赖。
- **此结果仅证明安装、目录、权限及回滚。** 完整 source/authority、登记后审计、封存后迟到业务写入的真实 COMMIT 尚未执行，不能据已安装函数宣称持久封存可发布。下一步接入服务登记与原请求回查，完成数量/SN真实历史业务和双向 COMMIT 反例，再进入正式迁移/HTTP/UI。
- 固定候选 `source-binding-v5.json` **2429项**已逐项核对，原工作树 **2415项**无漂移；11 个新增文件补丁 `return-condition-decision-seal-components-v5.patch` 已通过原仓 git apply --check，未应用。准确日志、数据库状态和源码收据为 `decision-seal-installer-final-v5-20261005.json`，较早 v4 收据保留原作用域。
- 本轮实读 **84788、99385、93201、20115、9441** 仍运行，没有重复启动。原17文件集成继续等待84788双模式终态；未提交、推送、部署或改动云端配置。完整 V1、真实渠道、UAT、性能和灾备门槛仍未齐全。

当前门禁与候选范围索引：[当前门禁索引](../artifacts/formal-0165-integration/current-release-gates-20261005.json)。该文件是带时间的实读快照；继续工作前须重读其现有进程句柄，不能把日志未更新当作停止。


## 2026-10-05 审批封存双模式矩阵终态，组件已整理为统一候选v4

- **41722 exit=0：2 passed / 972.04s**，数量/SN真实历史只读封存准入矩阵均通过；已有状态推进后的旧请求仍可准备，原申请人/原区域核实人即使调总部并取得有效admin授权仍不得自审。v3的2421项源码逐项无漂移，两份profile实读；收据 `decision-seal-matrix-final-v3-20261005.json`。不要再轮询41722；较早80844夹具失败保留。此证据仍是SQLite准入，不能替代持久封存原生COMMIT。
- 新增 `decision-seal-source-stage/decision_seal_source.sql`，从准确案件/前序事件推导原入库、来源区域、原申请人和该事件之前最近的区域核实人；不取最新事件代替旧引用，不将claimed摘要当已通过旧预检。人员身份与用户身份同时约束，换账号也不能消除自审限制。独立的完整source/authority组合调用既有真实业务校验函数并两次检查当前权限，锁顺序保持inventory head→principal/source；尚未在完整业务上验证该组合，不能据函数已创建就宣称历史证明完成。
- **28397 exit=0：1 passed / 10.39s**，`decision-seal-source-stage/native/run-amwv0sdo`已stopped/passed/serverExitCode0。23条记录覆盖状态已批准后的旧撤回/旧HQ引用、后续重新核实不改写旧核实人、原申请人/区域审核人及同人新账号自审拒绝、来源错绑、错误事件/动作和私有helper拒绝。父表是最小合成关系夹具，没有替换真实历史校验函数来伪造source成功；完整source/authority未调用。收据 `decision-seal-reference-final-v1-20261005.json`。
- v3终态后将五份新增文件整理进同一独立候选：app中的第八表schema、alembic候选目录中的input/permission/source三SQL，以及适配正式app导入的schema测试。`decision-seal-candidate/source-binding-v4.json`共**2426项**，原先3个准入/矩阵文件保持不变。**85354 exit=0：171 passed / 15.50s**，新目录实际导入复验通过；尚不是整套新候选静态/原生业务。
- 合并原3文件与本次5文件为**8个新增文件**补丁 `return-condition-decision-seal-components-v4.patch`，原仓git apply --check通过，未应用。收据 `decision-seal-components-integrated-v4-20261005.json`绑定完整候选清单/补丁/测试，原2415项hash仍无漂移。源码未注册Base、未授登记写权限、未开放HTTP。
- 后续优先在统一v4候选完成：核对完整source证明覆盖历史附件/政策等所需事实，准确当前actor/source绑定、唯一审计、全局请求缺失检查、双向迟到写入COMMIT约束、受控登记和回查；随后完整数量/SN原生业务/迁移/权限/客户端。不能把已分项通过的组件当成完整持久封存。
- 当前长测试仅继续**84788、99385、93201、20115、9441**；17文件集成仍等待84788双模式终态与源码核对。未提交、推送、部署；其余完整V1、真实渠道、性能/UAT/灾备门槛不变。

## 2026-10-05 审批封存当前权限组件原生验证通过

- 新增 `decision-seal-authority-stage/decision_seal_permission.sql`，私有函数校验当前真实用户/人员/已验证身份、authorization_version、有效角色及组织范围、组织树、双重read与各动作权限，显式deny优先。9种非过账动作分别绑定区域provincial_manager或总部admin；只读模式不要求仍有新动作权限。READ COMMITTED、明确模式和准确owner参数为硬条件；保管责任、当前库存、最新案件状态不由此函数授予。owner与历史申请人/审核人必须由后续准确来源函数推导，API不能直接调用该组件。
- **29867 exit=0：1 passed / 22.01s，39项结果**。使用真实模型定义的10张身份目录表及既有0026人员图锁函数，在新自有PG16 `decision-seal-authority-stage/native/run-uzw01geb` 中经测试专用BEFORE/延迟COMMIT探针验证：9动作允许、错版本/人员/区域/动作拒绝、RR/serializable拒绝、用户停用/离职/身份撤销/角色撤销/外部角色/两类read或动作deny拒绝；新动作deny后仍可read；区域及总部授权在COMMIT前到期均整笔回滚；停用组织、真实两节点环拒绝；API直接调用helper拒绝。已stopped/passed/serverExitCode0。
- 这只是当前权限组件，**不证明**历史案件绑定、独立审核人、真实库存业务、唯一审计、缺失封存或迟到写入约束。测试专用探针授权仅存在于新建的本地隔离数据库，不是生产权限变更。尚未应用、授予封存登记入口或合入正式迁移。
- 首轮30682 exit1是夹具使用不在正式枚举中的departed，未到目标权限反例；修正为正式left，并将自引用组织反例改为通过单行CHECK的双节点环，保留首轮日志。SQL候选未因该失败改动。
- 收据 `decision-seal-permission-final-v1-20261005.json` 绑定源码/日志/数据库终态；原2415项hash仍无漂移。补丁 `return-condition-decision-seal-permission-v1.patch` 已git apply --check，未应用。后续将此组件接准确历史source、原申请人/区域/总部独立性、审计及双向COMMIT约束后，再进行完整数量/SN恢复验证。
- 84788、99385、93201、20115、9441、41722均实读继续运行；41722数量场景已有通过标记，SN尚无终态，不计双模式完成。v3本地profile首个总部准备调用约38.90秒，历史basis约37.63秒、verify_chain调用16次，是重复证明热点诊断，不是PG/生产P95，累计时间不可相加。未重启、提交、推送或部署。

## 2026-10-05 审批封存原命令绑定与不可变行原生验证通过

- 新增私有SQL候选 `decision-seal-input-stage/decision_seal_input.sql`：完整原命令的类型、准确UUID、理由、9种动作、附件唯一/排序/边界、客户端键与11域摘要一致性；复用正式0026 JSON规范序列化，使Python与数据库摘要一致。旧事件摘要按claimed字段保存，不声明旧预检通过。两项输入函数及触发器函数均撤销PUBLIC/API等角色直接执行权限，未授予写入口。
- 新增INSERT前校验：表中的动作、案件、前序事件、claimed摘要、原命令、请求号、理由与请求/键摘要必须完全一致；UPDATE（包括no-op）、DELETE、TRUNCATE一律拒绝，行与语句触发器均ENABLE ALWAYS。独立最小父表夹具中的合法插入实际COMMIT/回读及所有拒绝后原行完整保留通过，仍不冒充当前权限/完整历史/审计/缺失证明。
- **35891 exit=0：2 passed / 23.80s**。`decision-seal-input-stage/native/run-adwm2ld8`与`native-row/run-37hizlfd`均stopped/passed/serverExitCode0；输入记录100条正反例（含9动作中英文/换行/制表/emoji/引号摘要对齐、缺字段/非法字段/错键/重复附件等），行绑定记录12条拒绝与保留结果，另实际检查API等4角色执行拒绝、正确INSERT和两条ALWAYS触发器。收据 `decision-seal-input-and-immutability-final-v1-20261005.json` 绑定准确源码、日志与两库状态。
- 较早输入单测 **71123 exit=0：1 passed / 13.48s**，只含前两函数，其精确旧SQL已按原checks的sha256复原保存 `decision_seal_input_v1_verified.sql`。后续含不可变触发器的当前版本以35891终态为准，不将旧源码结果重新标注为新版本通过。
- 原2415项和审批矩阵候选v3的2421项源码逐项hash均无漂移；模块补丁 `return-condition-decision-seal-input-v1.patch` 已git apply --check，未应用。候选文件仍在ignored artifacts，未提交、推送或部署。
- **下一步仍必须完成：** 数据库准确历史来源/当前操作者与动作权限、原请求全量缺失证明、唯一审计、跨动作双向迟到写入约束、受控登记及历史回查接入；随后完整数量/SN原生业务、正式迁移/权限目录和HTTP/UI验收。不能将当前“输入/不可变行”两组件称为已完成永久请求封存。
- 六个原句柄84788、99385、93201、20115、9441、41722本轮末均实读仍运行；不重启、不把慢日志当失败。17文件合入仍等待原84788双模式终态；全部正式V1上线门槛保持。

## 2026-10-05 审批封存关系表原生验证通过，矩阵夹具修复复验中

- 新增第八张隔离表候选 `stock_condition_decision_seals`，位于 `artifacts/formal-0165-integration/decision-seal-schema-stage/`，未修改运行中的原2415项或集成2418项源码。通过完整复合外键绑定案件原处置、入库、来源账户、原交易/移动/流水游标，以及准确前序事件、历史状态和序号；限制9种非过账动作/11种合法历史状态组合、全部11个键别名的唯一性与格式。旧请求声称的事件摘要独立保存，不升级为旧预检已通过的事实。
- **22074 exit=0：171 passed / 14.91s**。包括隔离Base、实际PostgreSQL DDL编译/引用唯一键、SQLite外键开启后的合法/非法动作、来源错绑、摘要和请求/别名冲突。首轮81496因测试表复制丢失方言条件发生69项初始化错误后主动中断exit2；已使用项目已有 `copy_conditions` 保留全部摘要约束，不删约束。
- **28896 exit=0：原生PG16 1 passed / 9.80s，35条提交/拒绝结果**。新自有 `decision-seal-schema-stage/native/run-un9bvr02` 已stopped/passed/serverExitCode0；合法关系实际COMMIT回读、延迟来源/事件FK拒绝、重复坐标拒绝、API直接INSERT拒绝通过。父表是明确合成的最小关系夹具，**不证明**完整历史业务、受控登记、不可变审计、双向迟到写入约束或正式迁移。首轮4814 exit1是两条同时违反的FK报告顺序断言错误，拒绝本身正确；修复仅允许该案件错绑报告两条准确FK之一，其余精确断言不变。
- 收据 `decision-seal-schema-relational-final-v1-20261005.json` 已绑定三份候选源码和终态日志/数据库状态；原2415项逐项hash无漂移。仅模块补丁 `return-condition-decision-seal-schema-v1.patch` 已git apply --check，未实际应用；后续需要把测试按正式目录适配，再接受控登记、原命令/唯一审计、跨动作晚写/不可变COMMIT约束和完整原生业务。
- **80844 exit=1：数量/SN矩阵2项失败 / 873.83s**。旧请求在实际状态推进后准备和两模式profile已执行，但在自审反例前，夹具给仍属区域公司的人员授总部admin，被真实身份加载正确拒绝；不得称完整矩阵通过。2421项v2清单已核对无漂移，失败收据 `decision-seal-matrix-v2-failure-20261005.json` 保留。不要再轮询80844。
- 仅修复独立候选矩阵：先将同一人员在夹具内调往真实总部组织，再给合法admin授权，验证原申请人/原区域核实人身份仍禁止自审，逐项回滚；没有更改生产权限逻辑。v3清单 `decision-seal-candidate/source-binding-v3.json` 仍2421项。**41722**复验中，日志 `decision-seal-matrix-profile-v3.log`，独占新basetemp `artifacts/decision-seal-matrix-v3-hq-transfer`，尚未终态。
- 继续原句柄84788、99385、93201、20115、9441及新41722。原17文件集成仍等待84788双模式终态与源码复核后才能应用；本轮未提交、推送、部署或修改云端配置。完整V1/真实渠道/UAT/性能/灾备门槛保留。

## 2026-10-05 审批封存准入4项终态通过，独立性矩阵已启动

- **40917 exit=0：4 passed / 1414.49s**，仅1项既有Starlette弃用警告；数量/SN真实历史夹具、旧状态推进/责任到期后的只读准备、已执行坐标拒绝伪装缺失、当前权限/自审/错引用拒绝与长查询后撤权重查通过。2420项源码逐项hash无漂移，终态收据`decision-seal-admission-final-v1-20261005.json`。不要再轮询40917。
- 将已准备的矩阵文件复制为候选`backend/tests/test_return_condition_decision_seal_matrix.py`，不修改原2文件或其他固定源码；v2清单`decision-seal-candidate/source-binding-v2.json`共2421项。**80844**已启动数量/SN两个场景，日志`decision-seal-candidate/repository/cloud_oam/decision-seal-matrix-profile-v2.log`；实际申请人/区域核实/总部批准之后验证旧请求准备和自审限制，每种模式采样一次总部准备调用。独占新临时目录`artifacts/decision-seal-matrix-v2-c42b5a25b268`用于profile，未复用或清除旧工件。
- 这些结果仍只证明只读封存准入，**不证明**持久封存、SQL登记/双向迟到写入约束、当前库存可用、允许重试、正式迁移或生产接口。新增矩阵未终态，耗时采样不替代PG/500用户性能。
- 当前需跟进**80844、84788、99385、93201、20115、9441**。17文件集成补丁仍等待原84788双模式终态后核验再应用；本轮未合入、提交、推送或部署。门禁索引已将40917转为完成证据并替换为80844。

## 2026-10-05 17文件集成补丁已就绪，原门禁仍在执行

- 组合补丁`artifacts/formal-0165-integration/capture-client-integration-v1.patch`已生成并通过原工作树`git apply --check`。它合并5文件采集权限兼容、2文件审批原生门禁入口、10文件Web路由测试加载修复，共17文件；尚未实际应用、提交或推送。
- `capture-client-integration-ready-v1-20261005.json`记录原2415项完整非文档源码无漂移、预期2418项集成源码摘要及各子补丁摘要。**必须先取得原84788双模式终态并核对两库checks/cluster-state/source，然后重新检查原清单和补丁，才可应用**；若失败则按实际错误处理，不盲目合入。集成静态99385/93201/20115及审批9441有独立固定目录，可继续原进程；源码合入不代表它们通过，也不解除提交/上线门槛。
- 原84788的SN已到旧历史兼容、候选安装、认证和初始封存来源检查通过，仍未终态。新增40917仍存活，日志两个通过标记、整组未结束；待终态后校验2420项源码再加入已准备的申请人/总部测试及cProfile。当前6个句柄均继续，未因耗时重启。
- 新增只读封存准入及未运行的扩展矩阵**不在本次17文件补丁内**；其持久封存、SQL登记和双向迟到提交约束仍缺，不能提前开放HTTP或生产授权。

## 2026-10-05 Web完整终态通过，审批未知请求封存校验候选启动（最新）

- 接续实读：40917仍存活，日志已出现首个通过标记，尚无整组终态。申请人/总部独立性扩展测试已保存为`test_decision_seal_matrix_staged_v1.py`，仅语法检查，尚未复制进运行候选或执行；待40917结束并核对源码后再加入，覆盖真实区域核实/总部批准后旧请求准备、申请人身份绑定及后续管理员授权不能消除自审限制。此项不能计为已验收。 扩展测试另加入每种数量模式一次真实总部准备调用的cProfile函数统计，仅写隔离测试产物，用于后续重复历史核验定位；累计耗时会重叠，不代表PG/真实请求或500用户性能。尚未执行该采样。

- **43739 exit=0：143文件/2509项全部通过，474.48s**；v3前端390项源码逐项hash无漂移，类型68872 exit=0。完整结果写入`frontend-route-preload-review-v3-20261005.json`。这是独立副本中10文件测试加载修复的结果，原工作树与固定后端集成目录未合入补丁，不冒充同SHA远端CI。此前v1/v2失败保留，不能重标为通过。不要再轮询43739/68872。
- 审批非过账动作仍缺永久请求封存。已在独立`decision-seal-candidate/repository`（复制固定2418项源码）新增只读封存准入模块和真实历史夹具测试，共2420项源码、2新增文件。模块核验当前身份/动作/read作用域、独立审核人、准确原事件、完整历史、跨动作残留及晚撤权；历史状态推进/保管到期不产生重发或库存授权，完整旧输入保留但不声明旧preflight已验证。
- 新候选**40917**正在运行数量/SN共4项真实历史夹具测试，日志`decision-seal-candidate/repository/cloud_oam/decision-seal-admission-focused-v1.log`；尚未终态。补丁`return-condition-decision-seal-admission-v1.patch`已通过原目录git apply --check，源码清单`decision-seal-candidate/source-binding-v1.json`。此模块**没有**持久封存写入、SQL登记/双向迟到写入COMMIT约束或生产接口；SQLite准备校验不能替代完整请求恢复。
- 当前需跟进原84788（SN run-tw_tvz35）、集成静态99385/93201/20115、原生审批9441和新增40917共6个句柄。9441已完成前序旧历史升级及认证/初始封存源检查，尚未取得7动作审批终态；3静态分片仍在运行、最近未见失败。
- 原工作树未提交/推送/部署；候选数据库、正式迁移/权限、真实短信/微信/OSS、实际HTTP/H5、全基线业务、性能、UAT和灾备门槛仍保留。

## 2026-10-05 路由聚焦终态，Web完整复验继续

- **15016 exit=0：11文件/113项全部通过**，包括10个App入口权限模块和完整32项日常盘点页面测试。v3的390项前端源码hash无漂移。上一节“15016运行中”已被本节更新。
- v3单worker完整Web **43739**已启动，日志`client-route-candidate/frontend-full-preload-v3.log`；类型检查**68872 exit=0**已通过，日志`frontend-types-preload-v3.log`。以准确终态为准，113项聚焦不能替代全量。v3补丁仍未应用原工作树或固定后端集成目录。
- 原84788继续SN；后端静态99385/93201/20115、审批9441继续原句柄。完整上线尚未验收，未提交/推送/部署。

## 2026-10-05 成色数量终态、首页实测与客户端稳定性修复（最新）

- 原生84788的数量库`run-so_rg17g`已实读checks=passed、stopped/passed/serverExitCode=0；门禁自身1716项源码逐项hash无漂移。实际API提交冻结、前后效果回滚、晚撤权/原输入损坏拒绝、原入库及新请求SQL READ ONLY回查、全局键竞争、永久初始请求封存/迟到写入拒绝通过。收据`return-condition-0166-quantity-final-v1-20261005.json`保存准确边界；存储仍为合成，后续审批/执行/释放/正式迁移未通过。父句柄**84788继续SN**，新库`run-tw_tvz35`，不要重启或将数量通过计成双模式。
- 公开生产包已通过实际本地浏览器验收：桌面首页及390×844手机宽度、无登录表单、后台链接准确、手机documentWidth=390无横向溢出，查询ADQCOM0088仍如实显示pending/0。两张截图和收据`public-home-browser-evidence-v1-20261005.json`已保存。临时页关闭、viewport恢复、仅127.0.0.1预览进程57886已主动Ctrl-C退出130；不是故障或云端发布。尚无资料匹配、真实设备、生产TLS或后台登录验收。
- Web副本v2全量**41313 exit=1：2504 passed/5 failed，138文件通过/5失败**。4个操作路由首例在加载中超时，盘点51项分页触发5秒测试上限。后者原测试单独87982 exit=0（1 passed/31 filtered，测试2.49s），未修改分页断言或放宽超时；单例不替代全量失败。
- v3仅修复10个App路由测试的真实模块加载准备，保留全部角色、权限与no-replay断言；应用源码及分页测试不变，原工作树和固定后端集成目录未修改。补丁`frontend-route-import-preload-v3.patch`已通过原目录git apply --check，源码清单`frontend-route-preload-review-v3-20261005.json`。**15016**单worker运行10个App模块及FormalStocktakes共11模块，日志`client-route-candidate/frontend-routes-preload-v3-focused.log`；尚未终态，后续需完整Web复验。
- 其余集成静态**99385/93201/20115**和原生审批**9441**保持运行，尚无终态。最近三个静态分片688/1201/104个call通过、无失败，不等于整片通过；审批9441仍在前序旧历史准备，不构成新审批通过。原及集成非文档源码继续固定。

## 2026-10-05 客户端产物通过，Web冷加载测试修复复验中（最新接续）

- 固定独立集成源码2418项hash仍无漂移；小程序97249 exit=0，1083 passed；Web类型83841 exit=0，公开构建28824/私有构建22026均exit=0。公开入口、私有pilot产物、共享开账协议检查exit=0。公开包无认证客户端，管理按钮准确指向`https://rscwz.cn/xx`。知识目录pending/0，严格公开发布仍未通过。收据`client-integration-evidence-v1-20261005.json`绑定源码/日志与两套产物hash。
- Web全量85807已exit=1：142文件/2508项通过，App.test.tsx的需求页挂载1项失败。单独诊断78523 exit=1（19 passed/5 failed），快照显示懒加载页面仍在加载；不是登录、服务器或GitHub错误。不要再轮询这些终态句柄。
- 独立`client-route-candidate/frontend`只修改App.test.tsx，提前载入真实受测路由模块，原有权限/请求断言及默认DOM等待不变。v1立即等待dynamicImportSettled过早，87891 exit=1，已保留并弃用。v2聚焦3700 exit=0：24 passed；原目录git apply --check通过。正确补丁`frontend-route-import-preload-v2.patch`尚未应用原工作树或固定集成目录。
- 副本Web全量**41313**运行中，日志`client-route-candidate/frontend-full-preload-v2.log`。源码逐项绑定于`frontend-route-preload-review-v2-20261005.json`；不能用24项聚焦替代全量结论。
- 其余仍跟进原84788、集成99385/93201/20115、原生审批9441。原84788已到condition_permanent_seal_checks START，未终态。后端三片最近无失败，仍运行。所有运行中的非文档源码固定。
- Chrome命令助手已接管并再次看到15:35成功回执：旧服务仍运行、候选仅DB、环境文件缺失。未因此重复执行云命令；本轮未提交/推送/部署/采购。真实渠道、UAT、性能和灾备等完整门槛不变。

## 2026-10-05 集成数据库兼容复验终态

- 集成current-head1487 exit=0，run-3j3mcabm停止且checks=passed；固定2418源码的真实配置角色/API安全兼容与10类反例通过。原工作树尚未合入，不等于正式CI。
- 后续审批原生9441已启动数量/SN，静态三片与原84788仍待终态。当前5个句柄与源码范围见交接顶部；完整上线仍未验收。

## 2026-10-05 独立集成源码全量复验启动

- 2418项源码的独立集成目录已合入权限兼容与审批原生门禁候选，共7文件差异，原工作树保持不变。56项聚焦通过；原生current-head1487与静态三片99385/93201/20115仍待终态。
- 原成色提交84788仍运行；新增后续审批原生门禁只完成候选代码，未运行，不可视为数据库审批验收。全部进程/日志/清单见交接文档顶部。
- 仍未完成同版本完整验收，不提交、不部署、不缩减正式上线范围。

## 2026-10-05 采集角色兼容修复预验

- 当前head失败根因定位为报废模块的冻结ACL与可选capture角色SELECT配置不兼容。5文件精确修复已准备，未应用原工作树；15项权限回归与7项来源/overlay回归通过。
- 新自有PG16候选验证25565 exit=0，完整API准入及10类权限反例/精确恢复通过，run-qtstdr7y已停止且checks=passed。不是原工作树或同SHA远端CI通过，不替代真实短信登录。
- 84788成色纠正原门禁仍运行并持有源码清单。终态后合入候选、原目录复验并继续完整发布门禁。具体证据、5文件补丁与恢复步骤见交接文档顶部；完整正式V1目标未缩减。

## 2026-10-05 15:35 最新验收边界

- 0166组合补丁已进入原工作树；94项聚焦通过，28项原生迁移准入通过，各自源码证据范围见交接文档，不能合并为完整发布。
- 原运行时目录门禁43186 exit=0；12项目录拒绝、0164旧应用降级兼容、重新升级0166通过，run-qnjxkewl已stopped/passed/serverExitCode=0、sourceDrift=[]。
- 当前head门禁48819在capture角色安装后完整API安全验证失败，根因尚待定位；成色纠正候选84788仍运行。旧静态三片有意终止exit=2，需完整重验。
- Chrome15:35只读命令exit=0，候选仅数据库运行、环境文件仍缺失，旧服务运行；通道可用不代表部署完成。完整业务、真实渠道、性能、UAT、灾备与同SHA远端CI门槛保持，未提交、推送或上线。

## 2026-10-05 0166数量业务终态与迁移反例

- 第一份0166副本数量纠正整轮及真实RC/RR认证隔离通过，SN仍运行；不能合并为双模式或正式上线。原工作树未应用。
- 新增真实PG16反例确认0166迁移遗漏外部schema触发器挂载，升级/降级均错误接受。第二份副本已加固全schema图核对，并将运维CLI准确固定0166；41816原生复验未终态，阻塞尚未关闭。见CONTINUE_DEVELOPMENT.md最新入口和两份独立源码清单。
- 成色纠正候选仍包含旧0165认证替换及固定版本，须在正式166基础上重新组合；不以标准处置或单模式认证证明替代其完整生命周期验收。


## 2026-10-05 15:10 接续验证

- 0166前向认证隔离修复仅存在于独立源码副本，42项聚焦通过；原生数量/SN父句柄62270仍运行，不能宣称认证阻塞已完整消除。原工作树/远端仍未应用，完整静态已有失败，保留发布阻塞。具体清单及接续入口见 CONTINUE_DEVELOPMENT.md 顶部。
- 本次Chrome只读执行 t-hz06z3nkzp9raps exit=0：候选数据库healthy，API/Web未运行，候选两份环境配置缺失；旧站保持运行。无需用户重复登录，仍不能认定新版上线。


## 2026-10-05 报损处置终态与权限夹具审计补充

- 新增发布阻塞：权限补丁副本原生纠正整轮失败，94878 exit=1；0165 报废封存触发器无条件获取库存头锁，真实登录 session/audit COMMIT 在库存锁持有期间触发 lock_timeout。需要正式前向迁移修复独立认证/审计域边界，不能以超时调整或删除测试代替。证据 `loss-correction-authentication-lock-failure-v1-20261005.json`；数量未通过，SN未开始。
- 性能剖析的完整破损收货/入库/回查用例通过，但98,075次SQLite执行和114次完整历史链证明暴露重复核验成本。该带剖析器的全测试时间不代表接口P95；后续保留安全边界做有界去重和真实负载验收，详见 `damaged-inbound-profile-v1-20261005.json`。
- 审核专用封存数量/SN整轮终态通过：24461 exit=0，head0165、2408项同源清单、两库正常停止、无漂移；收据`loss-review-seals-final-v1-20261005.json`。此证据补齐专用审核封存变体，不包含待应用的权限夹具修复、真实OSS或上线验收。完整静态仍运行且已有一项旧head断言失败。
- 完整静态门禁已发现旧head断言失败：多代来源测试仍把当前版本视为0164。修复在独立副本2项通过，准确绑定0164→0165冻结函数链与当前manifest；原工作树仍待本轮门禁结束后应用并复验。当前完整静态门禁不能计通过。
- 标准报损处置（`restore_available` / `convert_used` / `convert_damaged`）的当前0165原生数量与SN门禁均已终态通过；汇总`artifacts/formal-0165-integration/loss-disposition-final-v1-20261005.json`逐库绑定检查与源码清单。各三笔API角色提交、原请求重放、迟到权限/异常事务拒绝、历史保留及通知展开去重已验证。该证据不包含真实消息送达、OSS、生产身份或UAT。
- “标准报损批准后的处置”与下表的“破损收货/入库后的成色纠正”是独立流程。后者仍有候选DDL/正式运行目录、HTTP/页面和端到端验收缺口；不得使用前者的绿色结果填平后者的验收表。
- 本地静态并行测试的共享默认SQLite/上传目录已修复为每进程隔离；原工作树87项聚焦通过。536文件/10210项完整静态验证正在原句柄执行，仍非通过；审核专用封存数量/SN门禁亦运行中。最新句柄和日志以`CONTINUE_DEVELOPMENT.md`顶端为准。
- 新增权限夹具审计：0100迁移已为三类内部角色提供`stock_operation/read`，0165提供报损审核/纠正动作默认权限。五处夹具仍保留缺失时补权限的旧逻辑（纠正写入、纠正封存、共享多代历史、退回停止、审核封存），可能掩盖迁移缺权；此为已确认的测试证明缺口，不是已证明生产权限缺失。后续当前head路径必须只读验证完整权限并拒绝missing/deny；共享旧0164历史夹具必须保留其显式历史初始化边界。详细对象见`artifacts/formal-0165-integration/loss-fixture-permission-followup-audit-20261005.json`。
- 当前测试运行期间保持非文档源码固定。仍无同一提交SHA的完整远端CI、真实渠道、500用户性能、连续三天解释对账、恢复/回滚和正式UAT证据；不能提交或宣称上线。


## 2026-10-05 复核：测试恢复与上线仍分开

本节仅覆盖本次直接核查，下面带日期的记录保留为历史证据，不能累计成当前版本的全量通过。

- 最新原生收货v5父进程exit=0：数量 `run-t506pfdr` 与SN `run-3bp2xg11` 均stopped/passed/serverExitCode=0；迁移head=0165、两模式源码清单一致且无漂移。正常报损退回的收货/独立入库/请求回查与封存、防重复、通知完整性、权限到期及历史保留整轮通过。汇总证据 `artifacts/formal-0165-integration/loss-return-receipt-final-v5-20261005.json`。这是原生本地业务证明，**不是后续入库成色纠正的完整生命周期，也不是真实OSS、短信、浏览器UAT或正式上线**。
- 同批观察到准备/执行/锁探测数量20.473秒、SN23.423秒；该测量含测试开销，不能作为单接口P95，但要求后续做真实调用剖析与500用户性能验收。当前只读策略夹具7项反例已通过；双模式完成后的其他门禁夹具修正仍待各自原生验证。
- 当前分支仍为 `codex/notification-delivery-worker`，HEAD 为 `fc7c926`，未提交改动尚未形成同版本远端 CI。该 HEAD 客户端 CI 成功、PG16 CI 失败；当前本地修正后的 H5 四项、数据库安全四项及 0106/0152 两项已分别取得终态，但完整 CI 尚未重验通过。
- 本地原生 PG16 收货门禁 v3 已完成0165升级、0154降级、0165再升级和目录/权限核验，随后因旧夹具重复创建迁移权限失败；收货/入库/恢复整轮不能记为通过。v4 已改为复用确切权限，缺失正式权限或已有 deny 时拒绝；最新终态以 `CONTINUE_DEVELOPMENT.md` 顶部为准。
- 直接核对 `main.py`：旧 transfers 路由仍仅在非 production 环境挂载；`CustodyAssignment.handover_case_id` 仍为无 FK 预留。正式人员调拨和离职交接仍需完整模型、双方确认、未结项检查与区域/总部关闭流程。
- `formal_reports.py` 当前路由仍以 `/v1/reports/inventory-balances` 为前缀；一期其他报表与打印不能以库存余额报表代替。`stock_loss_corrections/return_boundary.py` 仍要求全部原份额为 not_outbound；已出库/已发运/已验收入库的补偿仍需分别建立真实业务链。
- Chrome 命令助手接管正常。12:41 的既有成功回执显示候选目录存在但 `.env`、`.env.production` 均不存在；尚无候选 API/Web 启动证据。KMS/私有 OSS 配置、短信真实登录、通知回执、真实 UAT、历史迁移与期初、连续三天对账、500 用户性能及恢复/回滚演练仍是独立上线条件。



## 历史验收表（2026-10-05候选收敛切片；不得作为当前实现状态）

| 业务流程 | 源码实现 | 正式迁移/权限 | HTTP | PC/H5 | 当前测试 | 真实 UAT | 剩余阻塞与完成标准 |
|---|---|---|---|---|---|---|---|
| 首次报损成色申请/冻结 | 已有原子服务、来源/身份/附件/流水/审计/Outbox | 仅候选 DDL；PG16 运行时目录未正式接入 | 未接 | 未接 | 数量/SN 完整 PG16 通过 | 未开始 | 正式迁移、接口和端到端身份；需真实文件字节 |
| 原请求回查/永久封存 | 初始请求回查与封存已接入私有服务 | 仅候选；封存权限/目录待正式迁移 | 未接 | 未接 | 数量/SN 封存门禁通过；认证锁兼容通过 | 未开始 | HTTP/客户端、正式目录、未知/冲突真实 UAT |
| 补证/区域核实/撤回/审批 | 状态模型和当前权限已有；后续动作服务本轮新增，正在服务测试 | 尚无后续动作正式 DDL/运行时 ACL | 未接 | 未接 | 新增服务切片运行中；未计通过 | 未开始 | 逐动作 PG16 COMMIT、迟到写入/撤权、完整恢复；执行/释放库存过账 |
| 发运/收货/个人仓入账 | 既有流程和历史校验 | 现有迁移；收货历史兼容前向候选未正式迁移 | 部分既有 | 部分既有 | 历史收货兼容数量/SN通过 | 未开始 | 真实 OSS、物流/收货 UAT、异常收货守恒 |
| 通知投递 | Outbox、delivery worker 基础存在 | 需正式权限/队列目录 | 部分内部 | 页面未闭环 | 仅展开/回查，非空渠道未验收 | 未开始 | 短信/微信/飞书真实 provider、送达回执和重试 |
| 人员调拨/离职交接 | 未形成当前切片 | 未形成 | 未接 | 未接 | 未验收 | 未开始 | 独立实现、迁移、权限、UAT |
| 报表/打印 | 局部前端/打印组件 | 未形成正式生产门禁 | 部分 | 部分 | 未完成全量验收 | 未开始 | 报表口径、A4 打印、导入导出和权限 |
| 生产发布 | CI/PG16 局部门禁存在 | 当前候选未提交；无生产迁移授权 | N/A | N/A | 仓库安全通过；完整上线门禁未通过 | 未开始 | 同一提交 SHA 的全量 CI、迁移演练、备份恢复、灰度和部署授权 |

完成标准：每一行必须同时具备源码、正式迁移/ACL、接口/页面、针对性测试和真实 UAT 证据；局部 PG16 绿灯、历史收据或页面可见不替代整行完成。外部前置条件包括真实 OSS bucket/凭证、短信/微信 provider、飞书机器人回执、预生产数据库、微信身份、物流/收货测试数据、UAT 人员和部署授权。

> 2026-10-03 最新终态：永久初始请求封存完整候选在数量run-ipsl7425/SN run-g5i0bg2p两库均stopped/passed/serverExitCode=0，1700源与固定v5一致，510条DDL一致。实际API封存/唯一审计、重复幂等、晚撤权、并发迟到写入拒绝、只读回查及历史收货兼容均通过；合法前缀请求提交与伪造封存审计拒绝均通过。父句柄33904已不可用，父退出码未重取，不伪造；终态以两库checks/状态和完整日志为准。最终收据见return-condition-permanent-seal-final-v1-receipt.json；审核/执行/释放、正式迁移/目录、HTTP/PC/H5和完整上线范围尚未完成。旧过程记录不覆盖本条。


## 2026-10-03 最新验证状态

永久请求封存原生 v3 的数量模式失败，SN未开始；不是上线通过。认证库存锁兼容已在本轮通过，当前失败点是历史报损收货附件仍检查上传人当前权限版本。前向历史绑定候选已解析，原生聚焦 **26600** 在数量/SN旧0157真实业务数据上验证（日志 `condition-receipt-history-forward-v1.log`，源清单v4/1700项）；数量模式已终态通过（旧版本1/当前版本2的错拒已真实复现，修正后历史校验通过，当前写入及6类坏凭证仍拒绝），SN尚在运行。正式迁移、HTTP/页面、全请求恢复生命周期和完整上线门槛仍未完成。此段优先于下方旧“运行中”描述。

静态待验证：当前请求格式允许 `condition-seal:` 前缀，但封存触发器将所有表上该前缀都视为内部封存效果；普通请求可能在COMMIT被错误拒绝。需补真实普通提交和内部审计冲突的原生反例，不能只限制客户端格式规避。此项目前仅为代码比对发现，未宣称已运行复现；本轮源码固定期间不修改。

## 2026-10-03 当前增量：永久请求关闭候选，未完成验收

已接入独立封存表、数据库登记/审计/迟到写入约束及sealed回查；新增库存与认证域的排除边界。补充认证边界后的服务v3已终态通过：33119 exit=0、4项/670.44秒。数量库只读目录观察已核验10个函数、78个ALWAYS触发器及5种运行角色最小权限。原生v2（29522）已失败：旧0165报废封存触发器令普通认证提交等待库存锁，55P03；数量库已安全停止、SN未开始。准确函数体前向修正已由聚焦原生90734验证通过（复现旧锁超时、修正后认证真实提交、三类反例仍被拦截、权限保留），整轮原生v3已用1698项固定源码启动（20872），详见 `CONTINUE_DEVELOPMENT.md` 顶部，尚无修正后的整轮通过收据，不计正式迁移、权限目录、HTTP/PC/H5或完整生命周期完成。

审核/执行/释放、后续份额、其他退回补偿、人员调拨/离职交接、UUID兼容、报表打印、真实文件/短信/微信/通知、UAT、历史迁移与期初、三天对账、500用户性能、RPO/RTO及回滚演练等原上线门槛仍须逐项取得证据。未提交、推送、部署或写外部业务。


## 2026-10-03 当前缺口复核（不以历史段落替代现状）

本轮继续保留全部正式上线范围。当前源码已存在报废/找回路由和页面：`formal_stock_losses.py` 挂载 `formal_stock_scrap.router`，`FormalOperationRoutes.tsx` 接入原始报废、纠正报废及找回适配器。因此，下方早期 0164 表中“报废执行不存在”等描述属于当时状态，不能继续当作当前缺口；路由和页面存在也不等于本轮已完成真实用户或生产验收。

| 当前仍待完成 | 本轮直接核对的依据 | 下一步验收要求 |
| --- | --- | --- |
| 历史入库成色纠正完整流程 | 私有首次冻结、完整历史、原请求回查已接持久键登记候选及33表双向冲突约束，上批检查已通过；新增首次申请/冻结业务效果SQL候选亦已通过原生数量/SN验证；`routers/` 和业务页面尚未接入 return_condition/stock_condition | 完整生命周期效果和真实渠道、候选安装后旧服务新写入兼容证明、缺失封存、审核/执行/释放、后续份额及 HTTP/页面、正式迁移；最新运行状态见交接文档 |
| 已出库及部分份额的退回补偿 | `stock_loss_corrections/return_boundary.py::document` 仍要求全部原数量/SN 留在 not_outbound，其他份额为零 | 按实际出库、发运、验收和入库分别提供补偿业务，不伪造实物退回 |
| 正式人员调拨及离职交接 | `main.py` 的旧 transfers 仅非生产挂载；`CustodyAssignment.handover_case_id` 仍为无 FK 预留，注释明确交接表尚未建立 | 双方确认、分批转交、保管责任、未结项和库存清零、区域/总部复核及受限页面 |
| 原生 UUID 兼容 | `models.py` 的 users、auth_sessions、wechat_identities 主键仍为 String(36) | 盘点关联与原值，完成无损兼容迁移及旧会话/请求证明，不覆盖不可变事实 |
| 一期报表完整覆盖 | `formal_reports.py` 当前以 `/v1/reports/inventory-balances` 为路由前缀 | 需求/履约漏斗、工单、盘点、账龄周转、全部打印和订阅逐项提供真实产物及范围权限证明 |
| 完整生产验收 | 本轮仅本地开发/隔离测试，无生产写入 | 真实短信/微信/OSS、当前提交 CI、浏览器 UAT、历史迁移/期初、连续三天对账、500 用户、RPO/RTO及备份/回滚演练仍各自需要证据 |

本轮静态核对证据保存在 `artifacts/formal-0165-integration/return-condition-coordinates-baseline-audit.json`：包含七个审查源文件摘要、三类文本主键的 PostgreSQL 类型编译、交接表和成色纠正表未登记到正式 Base 的实际结果。它没有连接生产数据库，也不代替迁移/运行时或完整功能验收。

请求恢复的准确实施约束见 `RETURN_CONDITION_REQUEST_RECOVERY_CONTRACT.md`。新增登记候选已超出原跨动作SELECT检查，但仍不等于完整恢复或正式迁移通过；本轮未提交、推送或部署。

新增 `return_condition_seal_admission.py` 只读封存准入已通过8项聚焦及数量/SN原生PG16验证：从完整原请求、真实来源历史和当前动作/查看权限生成准备结果，结束保管责任不抹掉历史。没有封存表或登记调用，不能把它计为“永久缺失封存完成”；本轮终态、固定源码和前序失败以交接顶部及对应收据为准。

## 2026-10-03 接续：完整初始纠正历史与原请求只读回查通过

新增 `return_condition_history.py`、`return_condition_history_read.py`、`return_condition_recovery.py`。由真实原入库账户派生当前区域查看范围，要求inventory/read与stock_operation/read；独立验证原报损退回/收货/入库历史，再双向收集原入库下的全部纠正事实（含从原库存移动反查错绑案例），核对共用业务单、原始输入、规范计划/命令、附件、库存/审计/状态/Outbox/通知及完整事件顺序，并投影各申请的冻结、已纠正和未认领份额。读取结束复核权限、历史摘要和审计/流水边界。保存的source_jsonb/hash只绑定原始观察与请求，不能替代真实来源证明。

历史时点的策略/保管责任与当前新增写入权限分开：撤销新申请权限或在原事件之后结束保管责任，不抹掉原结果；当前查看权限缺失或读取期间撤销时拒绝返回。私有lookup接受完整ConditionSubmit，绑定原用户/人员、请求号、键摘要及完整原输入，返回原提交结果和当前案件状态；current_stock_verified=false、retry_allowed=false。缺失记录保持unknown、absence_sealed=false，不证明从未执行，不允许自动重放。没有HTTP入口、重新提交或缺失结果封存功能。

复核同时修正 `return_condition_business_events.verify`：原先按全部匹配字段查Outbox/状态，可能忽略同一事件下额外的错误记录；现在按业务引用与幂等键先要求唯一，再核验准确内容。读取检查不等于数据库业务效果反向SQL约束已完成，后者仍待补齐。

**12288 exit=0，4组服务回归通过/342.31秒**。数量/SN各12次持久记录缺失、篡改、重复或错绑拒绝，共24次；包括重算计划/命令摘要仍拒绝、额外Outbox/状态记录拒绝、改错入库关联仍由原移动发现。另验证当前写权限撤销、历史保管责任结束后的历史保留、查看权限事前/读取期间撤销拒绝、准确请求只读回查、修改原理由/键的冲突，以及缺失原请求保持未知。SQLite反例只证明读侧拒绝，不称为原生防篡改。

**16993 exit=0：数量run-yqp3db6k、SN run-1u5nh5nt均stopped/passed/serverExitCode=0**。实际0157旧应用生成历史后升级0165，181个候选编译项，真实API角色整笔提交及原有回滚/提交约束继续通过；新增完整历史和准确初始请求回查均在 `SET TRANSACTION READ ONLY` 下通过。1671项源码与两库终态清单逐项一致。源库存可用性、审核/执行/释放及其持久历史尚不能据此宣称验收；本次原生业务为首次申请/冻结，通知仍为独立pending状态。

前序80478基础2组通过/153.86秒、20346中间4组通过/344.32秒、19914中间4组通过/358.59秒，覆盖重叠，不相加。8564/v1因补业务效果唯一性主动中断，run-7xnfa6sn已stopped/failed/serverExitCode=0，不计通过。安全v48：81959 exit=0，2633文件/55862224字节通过，diff检查通过。最终收据 `artifacts/formal-0165-integration/return-condition-history-final-v1-receipt.json`，源码清单 `return-condition-history-source-v1.json`，日志 `return-condition-history-service-v4.log`、`return-condition-history-native-v2.log`。本轮全部进程终态，无待轮询句柄，源码固定解除。

下一步补跨动作持久键登记、未知请求封存、业务效果反向SQL约束，再完成审核/执行/释放、自己的后续流水证明、公开接口/页面、正式前向迁移及运行时目录。当前内部只读回查通过不等于完整恢复生命周期或正式上线通过；FakeStorage不代表真实OSS/字节验收。其余完整基线缺口和生产门禁继续保留，未提交/推送/部署，无生产或外部业务写入。

## 2026-10-03 接续：纠正事件库存历史核验通过

新增内部 `return_condition_ledger_facts.py`，从真实库存表核对单个纠正事件的交易头、移动、数量/SN、规范过账键与请求摘要、操作者/时间、库存审计链、状态记录和Outbox。按业务引用和唯一键双向取数，拒绝隐藏的额外交易、通用冲销、错绑移动/SN及缺失效果；非库存动作不能夹带过账。读取不写业务事实，不把当前余额或通知投递进度当作原提交结果。

**18484 exit=0：2组通过/117.02秒**。数量与SN分别独立创建真实服务历史；两组共28次SQLite记录损坏/缺失拒绝（13+15），并验证队列重试元数据变化不改写历史结果、非库存动作不能掩盖已有交易、读取及拒绝不增加业务事实。首轮31723为1通过/1夹具失败：模块级历史快照被跨追踪模式复用，已改每用例独立快照；SQLite反例不声称原生防篡改。

**9944 exit=0：原生数量run-b_9c43h8、SN run-vk8n0dad均stopped/passed/serverExitCode=0**。实际0157旧服务生成历史后升级0165，181个候选编译项，实际API连接完整提交申请后由新增读取器核验库存证据；原输入完整性、两种故障回滚、晚撤权、裸账户拒绝及原入库请求回读继续通过。1666项源码与两库终态清单逐项一致。25153/v1因修复测试源码主动中断，run-2v3x0u42正常停止但checks=failed，不计通过。安全v46为87480 exit=0，2628文件/55803405字节通过，diff检查通过。

最终收据 `artifacts/formal-0165-integration/return-condition-ledger-facts-final-v1-receipt.json`，日志 `return-condition-ledger-facts-service-v2.log`、`return-condition-ledger-facts-native-v2.log`，源码清单 `return-condition-ledger-facts-source-v1.json`。本轮全部进程终态，无待轮询句柄，源码固定解除。未提交/推送/部署，无生产或外部业务写入。

**下一步仍是完整历史及受当前权限控制的请求回查，尚未完成。** 本组件的事件和SN参数必须来自完整历史适配器；单独调用不证明来源入库、纠正审批链、当前查看权限、库存现状或可重试性。后续组合顺序：准确源对象当前查看范围 → 完整旧退回/入库历史 → 全部纠正事件及单据/账户/附件/原输入 → 本库存证据核验与业务效果 → 状态投影 → 当前权限、事实快照及审计/流水边界复核。历史结果与当前可用库存分别表达；找不到请求仍为未知，禁止据此重放。仍需跨动作键登记/缺失封存、业务效果反向SQL约束、审核/执行/释放、页面、正式迁移及其余完整上线门禁；FakeStorage不代表真实OSS/文件字节验收。

## 2026-10-03 接续：原纠正提交内容持久化与提交约束通过

新增 `return_condition_request_schema.py`、`return_condition_request_inputs.py` 和候选 `request_inputs.py/.sql`。私有申请服务现在把完整原始提交内容与事件、冻结流水放在同一事务保存：准确入库行、预检摘要、数量、附件清单、理由、原请求号、幂等键摘要、逐件SKU/SN/二维码；保存当时的SKU文字，避免后续主数据改名重写原请求含义。原始幂等键不落库。事件、操作者/人员/权限版本、请求坐标及时间必须一致；登记表只追加，反向约束要求每个submit事件都有且仅有准确输入记录，输入与来源/选定SN/附件可独立重建比对。当前SKU只在新增记录时校验，历史输入比较不重新授权原上传者/申请人。

这是**原始提交输入记录**，尚非完整请求恢复。内部 `verify` / `match_original` 不授予查看权限、不独立证明完整业务历史，不能直接作为HTTP回查或业务结果。改变完整原请求返回冲突；缺失或重算摘要后仍不一致的记录返回结果未知；均不允许重放。跨动作幂等键登记、缺失结果封存和受当前权限控制的完整回查仍待实现。记录随业务提交才生效，没有记录不证明请求从未执行。正式迁移还需处理保留策略、运行时权限/目录和已有事实前置检查，禁止猜测或重建缺失原扫描内容。

**15456 exit=0：15项服务测试通过/340.45秒**，日志 `return-condition-original-input-service-v1.log`。覆盖完整原输入保留/不保存裸键、修改数量/来源摘要/请求号/键/理由/附件的冲突、后续SKU文字变化不重写快照、缺失/损坏记录未知，及原数量/SN提交、回滚和通用入口保护回归。SQLite测试的损坏持久化用于证明读侧拒绝，不等于原生防篡改。

**6588 exit=0：原生数量 `run-w5nf6vzq`、SN `run-bg_nlecm` 均 stopped/passed/serverExitCode=0**。每模式由实际0157旧应用写入历史，再升级0165并执行181个候选编译项；1664项源码与两库终态快照一致。正常申请/原始输入以实际API连接一并COMMIT，事后核验输入摘要和完整原请求匹配、库存/SN和业务效果；原入库请求回查、旧不可变行保留、过账前后故障回滚、晚撤权及裸账户拒绝回归通过。每模式额外将缺失输入、改数量、改请求号、错hash在实际COMMIT拒绝，错误SKU在INSERT拒绝。该反例刻意旁路Python输入比较器，仅改造测试输入记录写入，未禁用数据库约束；不能把5项全称为COMMIT拒绝。

最终收据 `artifacts/formal-0165-integration/return-condition-original-input-final-v1-receipt.json`，源码清单 `return-condition-original-input-source-v1.json`；安全v44为46460 exit=0，2626文件/55775466字节通过，diff检查通过。**本轮所有进程终态，无待轮询句柄，源码固定解除**。FakeStorage仍非真实OSS或文件字节验收。未提交/推送/部署，无外部业务写入。

下一步接完整纠正历史适配器、库存/业务审计/Outbox反向完整性和当前查看权限，再实现按完整原请求回查及缺失结果封存；不能将本轮输入匹配称为“新纠正请求恢复完成”。继续审核/执行/释放、已证明自身后续移动、页面、正式前向迁移与运行时目录，以及下文保留的全部正式上线缺口。


## 2026-10-03 接续：纠正申请原生 PG16 整笔提交通过

**19570 exit=0**：`return-condition-submission-native-v6.log` 的数量库 `run-c6w8glqp`、SN库 `run-uvh3s9of` 均 `stopped/passed/serverExitCode=0`。两个运行各自用实际0157旧应用生成破损收货/入库历史，再升级至当前0165，安装168条候选DDL及仅隔离库使用的新表最小授权。1659项源码与两库终态清单逐项一致，实际执行DDL摘要一致。该证据取代上条“尚无原生整笔提交证明”的状态，但仅覆盖本次私有首次申请/冻结。

以真实 `star_oam_api` 连接调用实际私有服务并COMMIT：数量从原0.050破损份额冻结0.025，SN模式冻结准确1件；两者均保持 `awaiting_regional`，尚未改变成色或完成审批。提交后新事务核对余额/SN归属、原单和候选关联、业务审计/状态/Outbox及pending通知；所有旧不可变行保留，**旧入库原请求**仍可准确回查。每模式均验证过账后、业务事件写入后两种故障全库回滚，以及无纠正单/流水的裸冻结账户COMMIT拒绝。晚撤权反例使用受归属验证的本地DBA连接，在API角色执行服务、所有者角色修改测试权限、再以API角色COMMIT拒绝；不表述为API自行拥有权限管理能力。

新增 `backend/tests/pg16_return_condition_submission_gate.py`，原检查器增加 `--submission-candidate`。完整验证发现并补齐两处候选兼容规则：`return_condition_candidate/forward_dispatch.py` 为共用库存作业单增加专用纠正分支，调用完整来源/过账/身份/附件校验；`forward_account.py` 仅准入有准确纠正单及首笔冻结流水、期初来源和余额证明的新冻结账户。原退回/报损/报废和账户校验分支完整保留，旧迁移文件未改。账户补丁从冻结0159目录严格衔接0162成色迁移，保留现有SECURITY DEFINER/session_user边界，安装前校验真实函数全文。

保留失败证据：56594/v1因旧分派器把纠正单当退回单拒绝；4499/v2已通过撤权拒绝但正向COMMIT缺新冻结账户准入；12946/v3、46542/v5因引用偏旧账户函数而被安装前精确校验阻断；84243/v4发现编译前提错误后主动中断，所属新建库正常停止。最终v6覆盖修正后的数量/SN完整结果，不能把前述失败或中断轮计为通过。

最终收据：`artifacts/formal-0165-integration/return-condition-submission-native-final-v1-receipt.json`；源码清单 `return-condition-submission-native-source-v1.json`。安全v43：80208 exit=0，2621文件/55740804字节通过；diff检查通过。本轮所有进程已终态，**没有待轮询旧句柄，源码固定解除**。未提交、推送、部署或写外部业务。

边界与下一步：FakeStorage仅提供上传/完成元数据，不是真实OSS或文件字节验收；当前候选不是正式迁移或运行时权限目录。新纠正请求登记/封存/恢复尚未实现，不能将“旧入库请求可回查”写成“新纠正请求恢复完成”。下一步补齐完整纠正历史与业务事件反向SQL保护、准确原请求恢复，再接审核/执行/释放、已证明自身后续移动、页面和正式前向迁移。其余六类实物流转退回补偿、人员调拨/离职交接、UUID兼容、报表打印、真实短信/微信/OSS、真实API端到端UAT、授权迁移/期初、至少3天对账、500用户性能、RPO/RTO及回滚演练、同一提交SHA的CI和正式上线门禁仍保留，未缩小上线范围。


## 2026-10-03 接续：纠正申请原子冻结写入

新增内部 `return_condition_requests.py`、`return_condition_submission.py`、`return_condition_posting_authority.py` 和 `return_condition_business_events.py`。严格接收原入库行、预检摘要、准确数量、逐件SKU/SN/二维码、附件及原请求坐标；账户、资产区域、保管责任、原交易/移动均从当前实际来源派生。锁定统一库存游标并重读完整原历史/期初/当前投影，数量不得超过准确破损子集，后续已有不明移动继续明确阻断。重复请求只返回必须回查，不在写入口重放。

冻结使用原统一库存过账入口。新增授权仅绑定本次Session事务、准确操作者/原入库、命令、游标及来源/冻结两账户；区域仓保管人的所属组织与资产区域可不同，专用许可只按已证实资产区域准入，不放宽通用库存权限。申请单/行/事件、SN、附件关联、不可变流水、余额投影、业务审计、状态转换、Outbox和待发送通知在同一调用事务内保存；调用方负责commit/rollback。持久后逐字段核验及完成附件复核，不将待发送通知当作送达。

通用过账入口在查重/重放前明确拒绝 `stock_condition_event`，私有原子入口也要求准确事务许可；通用冲销检查原交易真实类型，改写冲销来源名称仍不能绕过。锁定引用数据后重新检查实际扫描SKU文本，避免仅比较不包含SKU文字的库存维度摘要。

已完成：53173 exit=0，数量写入/回滚/错误申请与契约14项通过/134.20秒；71517 exit=0，SN实际冻结、通用过账与伪装通用冲销拒绝、错SN/二维码3项通过/91.37秒；6606 exit=0，原内部调拨/权限/调用方回滚/准确冲销与报废过账8项通过/24.49秒。新增SKU锁后复核后，2446 exit=0：4个SN用例与数量正向共5项通过/169.43秒；新增通用入口查重前拒绝后，7393 exit=0，契约7项通过/1.12秒，45134 exit=0：最终共享过账8项回归通过/25.32秒。安全v39为81739 exit=0：2618文件/55711682字节通过，diff检查通过。各次测试存在覆盖重叠，不将通过数简单相加。收据 `artifacts/formal-0165-integration/return-condition-submission-service-v1-receipt.json`，最终源码清单共1656项；本轮所有进程已终态。无生产或外部业务写入。

测试采用完整候选SQLite约束、真实当前权限加载器、旧服务仿真1.0业务历史、真实统一过账/审计/通知服务；OSS提供方为FakeStorage。首轮89689 exit=2为纯契约测试错误继承stock参数，已拆独立模块；27257 exit=1为旧夹具残留文件身份mock，已恢复真实文件身份加载和图锁。失败日志保留。这些结果不是原生PG16新纠正单COMMIT证明；此前1649源的PG16权限探针/旧历史兼容性只证明彼时组件，不能替代当前完整写入验证。

下一步：用实际0157旧应用历史，在隔离PG16安装全部候选结构/文件/身份/过账/当前权限检查，以真实API角色执行数量/SN申请及故障回滚和晚撤权，核验旧原请求仍可回读；然后补事件完整历史、原请求登记/封存/恢复、审核/执行/释放及已证明自身后续移动、页面与正式前向迁移。当前仍未开放HTTP、新公共文件用途或生产授权；物理证据和对象真实字节尚待验收，未提交/推送/部署。完整上线范围保持。

## 2026-10-03 新动作权限组件终态：尚待真实纠正单集成

89296 exit=0：`artifacts/local-return-condition-current-authority-pg16/run-9h6mueog` 已 stopped/passed/serverExitCode=0；1649源与终态快照逐项一致。第三轮原生PG16在完整0165身份目录上验证12类动作正向、268类无效准入拒绝、72类事务内权限/保管/组织变化在实际COMMIT拒绝，以及API角色的授权到期、保管责任到期2类实际COMMIT拒绝。精确 `pg_blocking_pids` 证明请求等待被持有的操作者权限锁，等待中撤权后拒绝；已准入事务持有锁时，并发修改用户、责任记录、新增责任和停用组织4类操作均被阻止。

新增候选 `backend/alembic/return_condition_candidate/authority.sql`、`authority_event.sql` 与 `authority.py`。当前权限函数保持私有，动作许可绑定准确角色/资产区域；只为新事件检查当前身份，不重授权保留的历史审核。事件包装函数从持久单据和原入库派生范围并检查申请人与审核人独立，但**本轮只在完整候选表上编译并执行未知事件拒绝，尚无完整真实事件绑定正向证明**。原生写入的是仅测试使用的命令探针，没有真实纠正库存、审计、Outbox、请求封存或恢复写入，不是业务UAT。审批代理、正式迁移和生产权限目录尚未集成。

74663 exit=0：41项原当前权限服务回归通过/101.20秒；72120 exit=0：安全v37通过（2611文件/55656810字节），diff检查通过；8条SQL/PLpgSQL与3个Python模块解析通过。最终收据 `artifacts/formal-0165-integration/return-condition-current-authority-final-v1-receipt.json`，日志 `return-condition-current-authority-native-v3.log`。83534与7488首两轮夹具错误已修正，保留失败日志，由第三轮完整终态覆盖。

本条取代下文89296运行中状态。本轮所有进程终态，无需重启旧句柄；源码固定解除。下一步优先将事件范围/审核人检查接入真实纠正单原子写入与请求恢复，补全部历史证明、审计和Outbox，完成页面及正式迁移，再推进其余基线与上线门禁。未提交/推送/部署，完整上线目标未完成。

## 2026-10-03 附件绑定兼容性终态及下一步权限检查

77206 exit=0：数量 run-_o4z1yk6、SN run-e8xgbypq 均 stopped/passed/serverExitCode=0。1644 源文件在终态归档前与运行快照逐项一致，实际158条结构DDL及6条文件DDL的哈希均吻合。239旧表分别564/577行、415原函数、856外键保持；候选结构、临时授权和文件测试数据全部回滚，运行时目录恢复。最终收据 `artifacts/formal-0165-integration/return-condition-binding-final-v1-receipt.json`，取代下文77206运行中状态；源码冻结解除，未提交/推送/部署。

下一步正在实现新成色纠正动作的提交时当前权限保护。仍须完成真实事件绑定写入、原子流水/审计/Outbox、请求恢复、完整页面和正式迁移；本轮附件组件及旧历史兼容性通过不代表这些已完成，也不代表生产验收。

## 2026-10-03 接续：纠正事件附件绑定

附件读取46项、附件PG组件152拒绝/6正向、原命令组件110拒绝/8正向终态通过；1644源一致。收据return-condition-binding-component-v1-receipt.json。19类文件引用表及日审JSON跨用途边界已覆盖；真实旧应用历史兼容性77206、安全22400 exit=0：v34通过（2606文件/55611673字节）；diff检查通过。详细失败修复及验证边界见CONTINUE_DEVELOPMENT顶部。仍无完整纠正业务写入/当前权限COMMIT/正式上线证明。

## 2026-10-03 接续：专用纠正附件

新增内部专用上传、完成证据和PG候选权限约束，公共用途及共用下载保持未开放。25782文件相关90项通过；67464候选SQL解析通过。原生8121因测试权限名称唯一冲突失败，已修复夹具；94705数量/SN均终态通过，1639源终态一致、全部旧事实/函数恢复；最终收据return-condition-evidence-final-v1-receipt.json。81988文件相关94项、74262备份回归69项、24315安全v33均终态通过。聚焦收据return-condition-evidence-focused-v2-receipt.json。准确命令、范围、失败修复及待办见CONTINUE_DEVELOPMENT顶部；完成上传不证明实物或完整纠正业务。

## 2026-10-03 区域来源预检

新增内部 `return_condition_submission_source.py`，只接收准确入库明细UUID。先按真实当前身份/区域纠正权限/当前保管责任及库存查看权限准入，再通过准确入库→发运→退回单关系派生唯一原报损处置。调用原内部完整历史证明 `_verified_graph`，独立核验原破损子集、可信期初、当前余额/SN/物料策略及后续移动；第二遍重读权限、原关联、全历史和库存，不返回跨两个状态拼出的证据。只输出所选来源，无总部全局历史、审批意见或他人原请求。`return_history.read`的原总部边界不变，没有临时总部principal或历史核验mock。

输出版本`return_condition_submission_source/1`，`submission_permission_checked=true`仅表示本次读检查通过。`posting_allowed=false`、`correction_authorized=false`、`physical_verification_required=true`保持；普通短事务SELECT但完整期初证明持有行锁，不宣传SQL READ ONLY。没有冻结、账户创建、新纠正请求/审计/Outbox、审批或过账，尚未挂HTTP路由。已转出又补入的数量仍返回later_activity_requires_reconciliation，不推测原破损份额还在。

集成发现并修复原`stock_return_receiving._authorize`的问题：原先将stock_operation:read作用于操作者person目标，会把实际保管区域仓且获准确区域授权、但所属组织在总部的人员拒绝。改为检查当前admin/provincial_manager获授的操作范围；每个具体位置仍由`_locations`独立检查当前保管人、唯一有效责任及该资产区域的stock_operation/read和inventory/read。未改变验收/入库写权限或原请求坐标。对其他获授区域、保管责任已变化的原请求仍拒绝。

13项新来源/原请求测试、2项追加越范围拒绝及21项接收回归分别终态通过，安全v31通过。原生10822 exit=0，数量/SN均终态通过（1635源归档时一致、原事实保持、完整回滚）；最终收据return-condition-regional-source-final-v1-receipt.json。准确日志、收据、失败修复及接续事项见CONTINUE_DEVELOPMENT顶部；新纠正业务写入和完整上线门禁仍未完成。

## 2026-10-03 新动作当前权限准入

独立 `return_condition_authority.py` 已实现准确原入库/纠正单/最新事件派生范围、当前真实身份与专用权限、当前保管责任及自批分离；仅新动作准备阶段，未接写服务或COMMIT保护。41新权限测试及144原规则关系指纹回归已终态通过，安全v30通过。完整旧历史上的API角色新增提交准入双模式门禁5004已exit=0：每模式1正向/7拒绝，临时授权及DDL完整回滚，原请求和旧事实保持；1632源一致。最终收据return-condition-authority-final-v1-receipt.json。此不证明后续动作PG写入或COMMIT权限保护。详情与失败修复/准确收据见CONTINUE_DEVELOPMENT顶部；实物完成上传、完整业务事实、原请求恢复、正式迁移及全部上线门禁仍未完成。

## 2026-10-03 命令身份兼容门禁终态

**完整旧历史兼容37964 exit=0，数量/SN均终态通过**：数量 `artifacts/local-return-condition-pg16/run-j68oq51m`、SN `run-e5v9zp8m` 均stopped/passed/serverExitCode=0。各自104条候选DDL，新增8私有函数/28触发器；239旧表564/577行、415旧函数、856外键及原入库请求保持。完整事务回滚、正式运行时恢复通过。实际执行SQL SHA `b5c5e91aa70e3e09e4d42685c3ad60aa7d200de9eb0aa5abdd1ac9d680511c09`。三个组件和两个兼容库1629源逐项与当前一致。最终收据 `artifacts/formal-0165-integration/return-condition-identity-final-v1-receipt.json`。候选新表没有API写权；这不是新纠正API、正式迁移、真实上传或生产验收。所有上述进程已终态，无需再轮询37964，源码固定解除。

## 2026-10-03 增量：纠正命令和共用单据指纹

内部规范化计划/命令、前序请求、共用头/行/SN及独立库存幂等/请求哈希已有候选校验；重算篡改JSON哈希仍拒绝。33992原生最小父表组件110拒绝/8正向通过；16342过账146拒绝/4正向、79626预算72拒绝/18正向/6竞争回归通过。修复公共库存数量哈希依赖Decimal精度的问题：144规则/关系/精度测试通过，另2项既有真实服务幂等回查与冲突回归通过。组件1629源一致。完整旧历史兼容37964状态见交接顶部。

命令哈希不是授权或真实文件证据；有效权限/代理、实物与上传完成、完整历史/审计/Outbox、原请求登记/封存/恢复、原子服务与UI仍缺。历史回查应证明当时审批事实，不能套用审批人的现时权限。全部V1发布要求保持。

## 2026-10-03 增量：纠正实际库存关联及反向提交检查

候选posting.sql新增2私有函数、8延迟触发器：精确原入库流水/游标、账户保管及物料批次维度、事件唯一交易和移动、SN集合与上一移动、原异常释放后再申领；修改库存父表而不插入业务事件仍重新检查。50430 exit=0，原生API SQL组件146预期拒绝/4正向通过；55755基础预算72拒绝/18正向/6竞争回归通过；60908规则/关系126项通过。组件库最小父表，不证明完整权限/审计/Outbox、写服务或生产验收。当前完整旧历史兼容99699状态及源码固定要求见交接顶部。

补齐终态：99699 exit=0，数量/SN实际旧0157应用历史经0165及93条候选DDL安装/完整回滚通过，564/577旧行、415函数、856外键与原请求保持；1624源一致。安全57194 exit=0。联合收据 `artifacts/formal-0165-integration/return-condition-posting-final-v1-receipt.json`，全部已终态，源码固定解除。仍非正式纠正写服务或生产迁移验收。

仍需共用单头/事件与业务/库存命令哈希、当前权限/代理、实物/附件、完整历史/期初、审计/Outbox/请求封存、原子服务/恢复/UI及全部正式V1验收。不得将本轮过账关联组件标为完整过账服务。

## 2026-10-03 增量：纠正数据库不变量与真实并发组件

不可变四表、原破损验收预算、每个事件前缀的数量守恒、SN历史区间防重复、精确来源行锁及申请/区域/总部身份分离已有候选SQL实现。原生12534 exit=0：72拒绝、18正向、6真实竞争；126项纯规则/关系回归通过（7673）。源码1621无漂移，组件库已停止。明确最小外部父表夹具及API SQL权限，未证明正式历史完整性、当前业务权限、真实API/身份或生产上线。日志 `artifacts/formal-0165-integration/return-condition-invariants-native-v3.log`；最新完整兼容任务状态见交接顶部。

74854 exit=0：实际0157应用合成历史→0165→82条候选结构/不变量DDL→完整回滚，数量/SN均通过；564/577旧行、415旧函数、856外键和原入库请求保持，回滚后正式运行时恢复。1621源与当前一致，安全v26通过。联合收据 `artifacts/formal-0165-integration/return-condition-invariants-final-v1-receipt.json`。全部终态、源码固定解除；不将候选事务回滚当正式迁移验收。

剩余缺口：完整旧历史与后续库存移动、真实实物/文件证据、当前权限/代理、审计/Outbox/请求封存双向COMMIT、正式迁移/写服务/恢复/UI。此前“未实现不可变/并发预算”仅指此前版本；本轮完成组件层，不将整条成色纠正标为完成。完整V1、CI/UAT、压测、迁移对账和灾备要求不变。

## 2026-10-03 增量：成色纠正关系候选和原生旧历史兼容

新增案例、事件、SN、附件四表候选，扩展统一作业单/明细、原入库行和流水的精确关系。未注册Base、未加正式迁移或授API权限。126项关系/规则联合测试通过（62520 exit=0）；原生首轮发现约束重名，已修复并重跑完整数量/SN门禁。

80626 exit=0，两模式均由真实0157应用生成合成损坏验收/1.0入库，经0165升级和原请求回查，再执行65条结构DDL及完整事务回滚。保留239旧表564/577行、415函数、856外键；每模式16个新表/角色权限组合保持关闭，回滚后正式运行时恢复。1616源逐项一致。证据 `artifacts/formal-0165-integration/return-condition-structure-native-v2-receipt.json`；安全v25（39720）通过。全部已终态，源码固定解除。

仍未实现纠正业务的数据库不可变/双向COMMIT保护、有效权限/真实证据、并发预算、原子写服务、请求恢复/封存及页面；候选DDL事务回滚不等于发布迁移升降级。完整基线、CI/UAT/正式发布条件不变，未提交/推送/部署。

## 2026-10-03 增量：历史成色纠正规则，尚未形成数据库业务闭环

内部案例状态、冻结/释放/执行计划及完整事件份额核算新增实现；支持部分数量/SN，审批不等于过账，撤销/驳回不等于释放，逐事件阻止重复申领，执行永久消耗原异常预算。71757 exit=0：新增投影、交错顺序独立预算验证及既有报损计划合计96项通过。数量/SN分别35种交错是两个参数化用例的内部枚举，不冒充PG16并发；代码/日志摘要见 `artifacts/formal-0165-integration/return-condition-planning-v3-receipt.json`。

未完成：当前权限/证据适配、纠正单数据库持久化、原子冻结/审批/执行/释放、请求恢复、延迟COMMIT、真实PG16并发及页面。源码新增5个文件，之前1607源PG证据只适用于其保存版本；最终当前源门禁仍需重取。没有提交、推送或部署，不更改完整基线缺口和上线结论。详细状态语义、后续专用冻结流水来源核验问题见 `RETURN_CONDITION_CORRECTION_IMPLEMENTATION.md` 与交接顶部。

## 2026-10-03 接续：历史成色纠正来源核验，数量/SN原生旧版本门禁通过

本节优先于下方历史状态。完整上线目标仍未完成，保留全部原改动；未提交、推送或部署。

- 新增内部 `return_condition_source.inspect_source`：按准确旧退回历史指纹与入库明细选出破损子集，完整证明原记录，重新计算当前余额和SN；验证当前总部历史查看及区域库存读取权限、唯一保管责任、期初和账本一致性。无SN后续转出即需实物与后续责任核对，余额补足不等于原货仍在；指定SN往返也不认定未移动。`current_projection_verified`只指账面投影，`physical_verification_required=true`、`posting_allowed=false`、`correction_authorized=false`。未安装HTTP写入口。
- **66823 exit=0，18项通过/520.44秒**：当前源SQLite实际服务及库存流水测试，覆盖0.375破损子集、SN、旧指纹/外来行、余额篡改、真实权限加载与中途撤权、错误但存在的SN流水，以及数量/SN转出和转出后转回。来源读取在query_only中保持业务快照不变。首次3项失败为测试夹具缓存身份及错误外键构造，已修正并复测；没有放宽权限或关闭外键。收据 `artifacts/formal-0165-integration/return-condition-source-v2-receipt.json`。这不是原生PG或完整纠正写入验收。
- 已保存Git实际旧版本 `9dff36f7feca44626b82ceb6e40297b3732a22f0`（0157、入库契约1.0）1221个源文件，位于 `artifacts/formal-0165-integration/condition-predecessor-0157/source/cloud_oam`，逐文件清单和归档SHA在父级。旧源不修改。新门禁用旧服务真实生成损坏验收/旧成色入库，升级到当前head后以API角色核验：历史/旧原请求使用SQL READ ONLY；当前来源的完整期初证明使用带行锁的短普通事务，断言仅SELECT、无ORM写入并回滚，前后全库事实不变。
- **53821 exit=0，数量/SN双模式终态通过**：数量 `artifacts/local-return-condition-pg16/run-pm3l9t44`、SN `run-7op2gtdg` 均stopped/passed/serverExitCode=0。每种模式均由Git保存的实际0157应用完成损坏验收和1.0入库，升级到0165后完整运行时校验通过；历史/原请求回查在SQL READ ONLY中通过，准确旧请求哈希保留。当前来源的完整期初证明使用带行锁的短普通事务，断言仅SELECT、没有ORM新增/修改/删除，结束回滚后全库事实不变。1607个当前源文件逐项一致、运行无漂移。数量/破损份额及SN对应关系已核对。收据 `artifacts/formal-0165-integration/return-condition-native-v3-receipt.json`，日志 `return-condition-native-v3.log`。源码固定已解除，不再轮询或重启53821。
- 这次证明的是实际旧服务生成的**合成业务历史**兼容与当前来源核验；并非生产历史导入、纠正执行/部分纠正预算、真实身份/UAT或整个PG16发布矩阵通过。
- 首轮53429已exit=1，`run-7oe1swk_`已停止、serverExitCode=0；旧迁移和边缘预置通过，测试脚本使用API读取alembic_version被权限拒绝。已改为迁移角色检查版本，未增加API权限；没有执行到旧业务生成，不能称迁移验收失败或成功。
- 第二轮58951已exit=1，`run-pfko0h0j`中实际旧1.0验收/入库和0157→0165升级已通过；新来源调用完整期初证明时使用FOR UPDATE，被SQL READ ONLY拒绝。保留既有完整证明和锁序，改为与现有库存预检一致的非修改短事务；没有移除校验。第三轮已独立验证该事务只发SELECT、结束即回滚，全库事实不变；采用第三轮完整终态，未沿用第二轮成功片段。
- 最终安全v23（59315）exit=0，2569文件/55248126字节，`repository-safety-v23.log`；完整diff检查通过。66823/53821/59315均已终态，没有剩余门禁进程。上一批175项前端/5组浏览器/A4打印及0164旧历史终态见下一节，不能当本轮新增后端的PG证据。

完整实现路径见 `RETURN_CONDITION_CORRECTION_IMPLEMENTATION.md`：区域实物核对、独立纠正单/冻结、区域复核和总部终审、原子成色转换、数据库COMMIT保护、原请求恢复、页面及日终解释尚待完成。退回下游物理补偿、人员交接、真实渠道/身份、实际迁移/期初、三天对账、压测、灾备/UAT/CI/发布仍全部保留。


## 2026-10-03 接续：旧历史门禁完成，退回收货单打印已验证

本节优先于下方历史状态。完整上线目标未完成，全部原改动保留，未提交、推送或部署。没有真实业务或生产写入。

- **45118 exit=0**：数量 `local-scrap-legacy-history-pg16/run-4vbeostz`、SN `run-bnc168r8` 均已停止且通过；保留519/531行旧记录及359个旧函数OID，各11份旧原请求准确恢复。0164实际旧版服务生成的合成历史→0165→0164旧版完整API启动→0165通过，授权保留、只读快照不变。收据生成时1601个后端源文件与当前一致；见 `artifacts/formal-0165-integration/scrap-root-proof-legacy-v1-receipt.json`。此前HTTP收据中legacy未完成标志是当时状态，由此独立终态证据补齐。不可将合成历史兼容当作生产历史迁移验收。源码固定已解除，不再轮询或重启45118。
- **退回收货单打印**：报损退回、工单旧坏件退回均支持准确单张收货记录的A4预览和打印；数量及SN分开，破损是接受子集、短少仅为本次观察，不推断累计损失或库存入账。只读权限可用，打开及打印前重新回读准确记录和当前权限；内容变化、撤权、关闭或卸载后的迟到响应不打印。操作者只展示已核验人员ID，不猜姓名；不打印附件私有地址。
- 前端路由/收货/恢复联合 **175项、16文件通过（59205 exit=0）**；新增打印及既有收货聚焦100项通过；TypeScript和warehouse构建通过。日志 `return-receipt-print-routes-v1.log`、`return-receipt-print-client-v3.log`、`return-receipt-print-types-v3.log`、`return-receipt-print-build-v4.log`。
- 实际编译产物Chrome **5组通过（83274 exit=0）**：报损数量、报损SN手机、50行多页、工单数量、工单SN。接口/身份为合成拦截、未接真实后端；业务POST为0，显式打印调用用测试替身验证，没有发送到实体打印机。PDF检查数量/SN各1页、多页5页，均为A4、每页有单号和页码，50行完整保留；所有页已视觉检查。早期手机溢出、Letter纸型和后续页背景问题均已修复。收据 `return-receipt-print-v1-receipt.json`，浏览器/PDF证据在 `receipt-print/`；PDF为测试样本，非真实业务单。
- 安全检查v21 **5183 exit=0**，2562文件/55182218字节；打印预览进程60313经核对后已停止。原生门禁没有遗留运行任务。

打印仅补齐上述两类退回收货单；出库单、交接单、盘点单及其完整验收仍待完成。其余完整缺口保留：退回下游补偿、历史成色纠正、人员调拨/离职交接、UUID身份兼容、报表、真实短信/微信/OSS、真实浏览器→API→PG联通、授权历史迁移/期初、三天解释对账、500用户压测、RPO/RTO与回退演练、完整CI/UAT/发布验收。服务端本地最慢HTTP仍约14.80/19.50秒，不能认定生产性能达标。

下一步先补历史成色纠正的当前库存证明：完整旧验收/入库证据不代表物资仍在原账户，必须核验当前余额、流水、SN、保管责任及权限，再建设独立审批与不可变纠正流水。不能改写旧入库单，也不能用区域仓库存瞬间恢复个人保管责任。


## 2026-10-03 接续：公共根证明复用双模式通过，旧历史门禁执行中

当前实现及进程以 `CONTINUE_DEVELOPMENT.md` 顶部为准。当前数量/SN完整HTTP均通过（37488 exit=0），最大COMMIT分别8.400978/10.121456秒，完整HTTP14.802255/19.498692秒；每模式新增19项私有证明拒绝反例及4事实独立计划一致性通过。当前源184项聚焦、运行时/权限篡改12项、空库升降级/旧版API恢复通过。45118正在执行当前源真实旧历史保留门禁；正式生产历史迁移、完整CI和生产性能验收仍未完成，不提交/推送/部署。

另补编译产物真实Chrome双标签页验证：意见切换取消旧确认，实际Web Locks排他、storage同步、仅1次业务提交、锁内0回查、释放后1次只读回查且原请求不变。接口合成，不能记作真实后端UAT。预览已停止。

## 2026-10-03 当前：SN核对、跨区域隔离、浏览器与页面拆包已验证

本节优先于下方历史状态。使用06f6工作树、`codex/notification-delivery-worker`，保留所有未提交改动；未提交、推送或部署，没有真实外部业务或生产写入。完整上线目标仍在推进。上一轮和本轮均有实现及终态证据，不存在等待用户才能继续的阻塞。

### 本轮实现

- 找回来源DTO新增 `serials`（serial_id、serial_no、qr_code），从原报废绑定的准确InventorySerial查询，验证物料及完整ID集合；双读显式刷新ORM记录，标签变化会使核验失败。保留原serial_ids，未修改库存写命令、数据库迁移或授权规则。
- `ScrapSerialCheck.tsx`及找回页面显示真实SN/二维码；申请、区域“实物已核实”、执行入库需要先输入准确物料号，再逐件用扫码枪/键盘录入SN或二维码。严格区分两种标识，区分大小写；错误/重复不能计数，无批量自动确认；切换审核意见、物料或当前来源会清空核对。要求补证据不强迫确认缺失实物；总部审核看证据，不虚称实物经总部扫描。没有新增摄像头扫码或微信原生扫描功能。
- 客户端准备与新提交前对照完整当前SN引用；标签发生变化则重新核对，不复用旧确认。原请求恢复不要求重新扫描/重建命令。此处是用户实物核对体验，不能替代后端权限及不可变库存事实校验。
- 增加同角色隔离：其他区域负责人、其他区域工程师及同区域另一工程师，准确详情均404、列表为空；不存在ID和已有但越权ID的外部表现一致。真实HTTP使用各自新建的合成用户会话，没有替换principal。
- 页面与业务适配器按需加载：`FormalOperationRoutes.tsx`保留原路由绑定，`formalOperationScopes.ts`保留菜单/路由范围判断，App仍先做角色和read检查。`RouteLoadBoundary.tsx`处理资源加载失败，保留整体导航和原请求，明确重新加载，不重发业务操作。

### 完整终态证据

- **70293 exit=0**，真实PG16新增SN/隔离HTTP双模式：quantity `artifacts/local-scrap-http-pg16/run-99zhr6de`，serial `artifacts/local-scrap-http-pg16/run-4agi182g`。两库均stopped/passed/serverExitCode=0；1601源无漂移、收据时与当前完全一致。每模式22实际COMMIT、103 READ ONLY、34找回来源GET（含6项新隔离详情/列表），两代报废/找回、12旧原请求回读、不可变历史、原冻结份额/SN恢复均通过。每个返回SN标签与库内原报废绑定逐项一致，GET前后业务事实不变。收据 `scrap-serial-scope-native-v1-receipt.json`、日志`scrap-serial-scope-native-v1.log`。源码固定已解除；不再轮询或重启70293。
- 后端来源/路由/调度聚焦 **52项通过**，22514 exit=0，`scrap-recovery-serial-scope-v1.log`；其中6个新增跨区域/跨人员用例同时覆盖数量/SN。曾有fixture对NULL父组织调用get的警告，已改为条件查询；最终原生覆盖的是修改后的代码。Starlette既有弃用警告仍在。
- 前端最终合并回归 **406项/22文件通过**，54918 exit=0，`scrap-serial-final-client-v1.log`。包含所有App正式路由/角色、原请求持久化/回查/封存、报废/纠正/找回、逐件SN和审核意见切换重置、加载失败边界；最终TypeScript `scrap-serial-final-types-v1.log` exit=0。
- warehouse构建 `route-split-build-v2.log` exit=0。首包266323字节（约266.32KB，gzip约85KB），上一轮约1397.99KB；最大JS包298566字节，所有JS包均低于500000字节，未提高警告阈值。初版只拆页面仍653KB，随后将适配器和路由包装一起按需加载才达到当前结果。首包减少不代表服务端或500用户压测合格。
- **真实Chrome浏览器**：编译产物在本机4180预览，桌面1440×1000、手机390×844，四阶段各一组，共8组通过（56941 exit=0）；申请包含完整合成上传意图→对象PUT→完成确认，再进行SN核对。全部角色确认提交响应丢失后保存原请求，刷新并撤写权后只能只读回查、没有重复业务提交，横向溢出检查通过。另1组真实编译chunk故障→显式重新加载通过（93743 exit=0），原请求完整保留且0业务写入。**接口、上传及认证均为合成拦截，Service Worker关闭，未连接真实PG/API或短信/微信；不是生产UAT。** 当前浏览器证据在`browser-serial-scope/production-build/`，脚本在其父目录；已查看手机截图。根品牌图由测试路由提供本仓库public中的文件，模拟公开首页静态资源映射。
- 开发服务器4179、预览4180在核对PID后主动停止，13666/63276退出143为正常清理，不是验证失败。所有门禁、前端测试、浏览器进程均已终态，无需再轮询。
- 安全v19 **39219 exit=0**，`repository-safety-v19.log`；diff检查通过。源码、日志及编译产物SHA/包大小/浏览器边界收据：`scrap-serial-client-and-split-v1-receipt.json`。上述日志及收据未注明目录时均在 `artifacts/formal-0165-integration/`。

### 仍然未达到的上线要求

本轮完成基线1.1数据范围和1.11找回流程的一部分客户端与数据库验收，不能把报损/报废/全部库存作业或正式V1整体标为完成。真实浏览器和原生PG分别验证通过，仍需同一部署上的真实身份→浏览器→API→数据库连通验收、附件真实存储/下载、补证据/退回重审/跨标签页/会话切换等浏览器扩展场景及微信原生扫码。

当前原生本地合成场景：数量最大COMMIT13.750584秒、最大完整HTTP20.176134秒；SN最大COMMIT16.650873秒、最大HTTP27.639740秒。**服务端性能仍不满足上线准备要求。** 继续分析完整历史核验的重复调用和查询；不得删除审计/库存检查、缓存跨事务证明或扩大超时冒充优化。尚未500用户压测。

完整基线缺口继续保留：退回下游补偿、成色纠正、人员调拨/离职交接、身份UUID兼容、报表打印、真实短信/微信/渠道、授权历史迁移/期初、至少3天差异解释对账、500用户压测、RPO≤5分钟/RTO≤2小时恢复、应用和同步回退/业务冲销、UAT/CI及正式发布。公开知识查询首页、星星管理入口`/xx`、飞书知识源低优先级不变。所有必要上线证据齐全前不提交、推送或部署。

下一轮优先：在现有完整联测上继续服务端性能定位，并补退回下游补偿及正式基线缺口；浏览器联测要明确区分合成接口与真实后端，不能因已有8组浏览器绿灯就取消真实部署验收。

## 2026-10-03 最新：审计重复核验第一轮优化完成，双模式终态通过

本节优先于下方历史状态。完整上线目标仍未完成；全部原改动保留，未提交、推送或部署。本轮没有真实业务或生产写入。所有本轮测试进程已终态，无需继续轮询；源码固定已解除。下一轮修改后必须重新取得对应证据，不能沿用本轮 source manifest 宣称新版本已验证。

### 最慢提交和当前剩余问题

同一套完整 HTTP 联测、相同22次提交顺序、相同恢复/权限/来源查询断言的本地观测：

| 模式 | 前次最大 COMMIT | 本轮最大 COMMIT | 降幅 | 本轮最大完整 HTTP 请求 | 全段审计扫描次数（前→后） |
|---|---:|---:|---:|---:|---:|
| 数量 | 113.711916秒 | 38.821962秒 | 65.86% | 46.243251秒 | 32410→6671 |
| 序列号 | 129.508182秒 | 45.107798秒 | 65.17% | 55.081510秒 | 35391→7271 |

**性能仍不满足上线要求。** 这是本地合成场景计时，不是500用户压测；COMMIT时间不是用户请求的完整响应时间；函数总耗时包含嵌套调用，不能相加。数量模式剩余重点是旧完整历史图反复调用报废历史2798次、上游历史3160次，并重复核验同一根单据和期初证明。下一步先审查这条纯读调用链、锁顺序和校验覆盖，再减少同一次完整证明内部的重复工作；禁止减少校验、扩大超时或跨写入保存证明。

### 本轮实现及约束

未发布0165的5个SQL文件中增加9个私有 `*_with_audit_0165` 辅助函数；原入口签名保留，先按库存头→审计头顺序加锁并重新验证完整审计链，再将UUID数组传入同一次同步调用内的子校验。内部校验不写业务数据，不跨请求/事务保存，不开放API/PUBLIC调用。全部原业务判断逐字对照保留，审计、哈希、附件、权限、流水、SN和通知事实检查未删除。代码与旧候选备份及对照证据在 `artifacts/formal-0165-integration/pre-audit-reuse/`。

catalog生成17649 exit=0，`artifacts/local-scrap-catalog-pg16/run-p3xwlzwy`已stopped/passed/serverExitCode=0，验证编译事务回滚和原样DDL回放；133条语句、10新表、40旧表变更、48新函数、15旧函数替换。与上一候选相比表结构完全相同，仅新增9个私有函数并改变9个原入口。冻结catalog SHA `0021fb5cd3bafec854e888e5301785cf7f54270d7805d961d935cf957336acf1`；运行时只读投影SHA `b0f01d7a36dd06659dfe7fd252a3bc47f1a773343b857d54683bb88987fe796b`。旧已发布迁移文件未改。

### 当前终态证据

- **完整HTTP/profile：92910 exit=0。** quantity `run-ianl9swz`、serial `run-sttecn9b`，均在 `artifacts/local-scrap-http-pg16/` 下stopped/passed/serverExitCode=0。每组22次真实COMMIT、63次READ ONLY、11次来源GET（含401/403和撤权拒绝）、两代报废/找回、12份旧原请求回读、不可变历史与最终冻结份额/SN一致。提交前回滚、提交后丢响应、撤写权限后回查和封存拒绝迟到执行均保留。1592源文件无漂移，收据时与当前完全一致。收据 `artifacts/formal-0165-integration/scrap-audit-reuse-http-v1-receipt.json`；日志 `scrap-audit-reuse-http-v1.log`。仍为合成身份和HTTP传输，不是真实短信/微信或浏览器验收。
- **原生反例：8975 exit=0。** 最新合成服务导出26728 exit=0（4通过，`artifacts/scrap-ledger-exports/run-9gujvbcl`）；数量/SN×独立/共享库存四组，各113次预期拒绝、4项正向检查，共452次拒绝。包括9个伪造审计数组API调用拒绝、同一事务成功核验后修改审计链必须重新拒绝，以及原有重新计算哈希、通知、附件、延迟COMMIT反例。全部回滚后原快照不变；完整源码收据匹配。收据 `scrap-audit-reuse-evidence-v1-receipt.json`。该组件门禁使用完整合成导出，不冒充正式历史迁移。
- **正式运行时：16996 exit=0。** `artifacts/local-scrap-runtime-pg16/run-kx322r90` stopped/passed/serverExitCode=0。完整默认API启动、9类目录/授权篡改拒绝并精确恢复、空库升到0165→降到0164→保留的原0164完整API启动→再升级通过。新增私有证明函数被误授API调用权时，启动必须拒绝。11项默认角色权限由迁移生成；1592源匹配。收据 `scrap-audit-reuse-runtime-v1-receipt.json`。空库往返不等于正式生产历史迁移验收。
- **聚焦及安全：** 8039 exit=0，17项readiness/security/HTTP调度通过，`scrap-audit-reuse-preflight-v1.log`；68544 exit=0，离线迁移边界1项通过，`scrap-audit-reuse-offline-v1.log`；12409 exit=0，安全v14为2533文件/54657110字节，`repository-safety-v14.log`；diff检查通过。
- **此前3项迁移断言已修复：** 50869 exit=0，`scrap-migration-assertions-v7.log`为3 passed / 113.39秒；10表、11权限、降级保留授权的精确角色/动作集合与在线函数目录已核对。与v6的177通过分属不同执行，不声称一次全量180通过。该3项执行早于本轮SQL优化，本轮补了上述当前源运行时及离线边界验证。

上述收据及日志未另注明目录时均位于 `artifacts/formal-0165-integration/`。17649/26728/8039/8975/16996/68544/12409/92910及上一批71853/50869全部终态，不要重启或继续轮询旧句柄。

### 接下来与上线边界

1. 继续压缩旧完整历史图→报废历史→上游历史/期初证明的重复核验，保留全部不可变事实和延迟提交保护；每次改动后重新核验目录、权限、反例和实际双模式耗时。
2. 最终源码还须补正式保留旧历史的迁移/降级验证及全量本地/远端CI。旧 `legacy-history-v2-receipt.json` 早于两项0161前向封存兼容修复及本轮性能修改，不能当当前源终态。
3. 补纠正与找回四阶段的受限来源查询、PC/H5页面和真实浏览器请求恢复。原始报废页已有页面测试，不能以接口联测替代整页验收。
4. 保留完整基线缺口：退回下游补偿、旧坏件纠正、人员调拨/交接、UUID身份兼容、报表/打印、真实短信微信、历史迁移/期初、连续三天对账、500用户性能、RPO≤5分钟/RTO≤2小时、UAT与发布回滚。公开知识首页和星星 `/xx` 入口保持既定方向，飞书知识源低优先级。未满足全部要求，不能宣布正式上线。

## 2026-10-03 接续：数量全链与运行时通过，序列号仍在跑

本节优先于下方历史状态。完整上线目标未完成；保留全部未提交改动，未提交、推送或部署。本轮没有真实业务或生产写入，原生测试均在自有临时Unix socket PG16库内运行。

**已经定位和处理的缺陷：**

- 原生来源GET/profile v1（5192 exit=1，`run-aik5rx9i`）发现报废找回提交成功后，GET误报`stock_loss_disposition_history_unknown`。`original_recovery`现按已经证明的业务事实选择recovery/scrap哈希，严格检查五类别名、所有typed列和两个请求登记表的冲突；三类未执行封存不当成库存边。v2已证明after-first-recovery GET恢复正常。
- 扩大场景后的v2（25788 exit=1，`run-5ewt6on2`）在纠正审批封存时触发`0161 complete approval seal context required`。旧SQL仅接受原始恢复/转旧/转坏，遗漏scrap root。现由未发布的0165精确前向替换两个纠正封存函数的root白名单；保留祖先、完整请求、权限、审计与冲突检查，未修改旧0161文件。独立catalog生成/事务回滚/原样回放81500 exit=0，目录`artifacts/local-scrap-catalog-pg16/run-hgpd9jls`，stopped/passed/serverExitCode=0。与旧catalog相比表结构完全相同，只增加两项函数替换。已提升冻结catalog及运行时只读投影，SHA为`43b964135d6707a942511b6b53fc6542fef748b4e06a804dcf4f102866aa9bed`；旧版本在`artifacts/formal-0165-integration/pre-source-fix-catalog/`保留。
- 旧来源回归38874 exit=1：50通过、4项退回来源失败。根因是动态新增数据库列不是旧ORM属性，原历史指纹用getattr报错。`return_history`现读取完整实际数据库列并核对行身份，不跳过新增字段；4项退回来源已在v5恢复通过。v5共15通过/1失败，剩下是迁移函数计数从14到16的测试预期，已更新并重跑v6。

**已终态聚焦证据：** `scrap-source-regression-v3.log` 27通过（4项真实数量/SN两代只读来源/冲突测试+23项runner/dispatch）；`scrap-source-preflight-v4.log` 23通过。早期v1测试缺read授权、v2误用子行ID均只涉及夹具，已修正并保留失败日志。安全v13（37535 exit=0）2533文件/54578185字节通过，已包含SQL与return_history修复；日志`repository-safety-v13.log`。diff检查通过。

**当前终态与唯一活跃句柄：**

- **71853仍活跃**：正式双模式HTTP+来源GET+profile v3，日志`artifacts/formal-0165-integration/scrap-http-sources-profile-v3.log`。quantity目录`artifacts/local-scrap-http-pg16/run-o8k5vu_8`已stopped/passed/serverExitCode=0：22次真实COMMIT、63次READ ONLY、11次来源GET（包括401/403拒绝）、12份历史原请求、两代报废/找回及冻结份额/SN校验通过。收据`scrap-http-source-quantity-v3-receipt.json`；1592源无漂移且收据时匹配。serial已启动在`artifacts/local-scrap-http-pg16/run-larnrcfb`，尚无终态；先轮询71853，不因观察超时重启。
- **80723 exit=0**：正式runtime v6在`run-j8r4ufl6` stopped/passed/serverExitCode=0，完整API、8类篡改拒绝、空库升级/降级/原版0164完整API启动/再升级通过。11项默认授权无夹具账号生成，1592源匹配；`scrap-source-runtime-v6-receipt.json`及`scrap-source-runtime-v6.log`。不可把空库往返当作生产历史迁移或500用户性能验收。
- **71519 exit=1**：完整readiness/security/Alembic聚焦v6，177通过/3失败，日志`scrap-source-migration-regression-v6.log`。三项为旧迁移测试尚未列入0165的10张新增表/11权限与角色绑定、应用降级保留授权后的数量（23/37而非12/26）、新增0165运行时函数只通过在线冻结DDL安装而不在0158离线前缀中。已核对正式候选和原生runtime，不修改生产实现去迎合旧断言。

**源码仍固定到71853终态。** 非Markdown后端、迁移、scripts、edge_sync、deployment、workflow都不能改。81500/80723/71519/37535和其他上文旧句柄均终态，不再轮询。为避免改动活跃门禁源码，迁移测试修订仅准备在`artifacts/formal-0165-integration/test_alembic_migrations.v7.py`，原文件SHA256为`e77e8e2b8af1f75050e48b8078835c244f925d6ce5286e98be89358ee2684c2e`；生成脚本同目录`prepare_migration_assertions_v7.py`，只输出候选、不改原文件。71853终态后先检查源码未漂移，再审查/应用候选，重跑3项失败测试，若新断言暴露后续差异继续修复，不直接宣布全180项通过。新增计数必须与独立列出的角色/动作精确集合一致。后续测试文件改变会使当前完整source manifest不再全量相同，须在最终提交前补当前源门禁。

**慢提交仍未解决：** quantity第一次找回COMMIT约30.64秒，实时只读观察active且无锁等待。v2函数计时显示canonical JSON约720万次、self约23.79秒；库存审计成员证明6438次、self约6.21秒。它们是整段合成联测累计，非单次提交独占耗时，更不是500用户压测；同时有本地回归进程。quantity v3第二次找回COMMIT约113.71秒；整段约4947万次canonical调用/self162.87秒、审计成员证明32410次/self43.74秒，详见quantity收据及原始terminal。完整v2统计保留在`scrap-http-sources-profile-v2-diagnostics.json`；失效或失败运行现在也保留函数名/计数/耗时，不记录SQL正文或凭据。

下一步：取得上述终态，核对来源GET、两代业务、封存、历史原请求、账/SN、catalog及source manifest；必要时修复继续复测。再处理重复历史/审计核验的性能成本，补纠正与找回四阶段受限来源查询及PC/H5页面。真实浏览器、真实身份/短信微信、退回下游补偿、调拨交接、正式历史迁移/期初、三天对账、500用户、RPO/RTO、UAT和发布回滚等全部基线验收仍保留，不得以局部门禁通过宣称上线。

## 2026-10-03 最新接续：原始报废页面已接入，双模式纠正/找回 HTTP 已通过

正式 HTTP helper 和 `--http-only` 原生 runner 已接入，并登记 GitHub PG16 `scrap_http` quantity/serial 矩阵，调度/隔离/清理检查 68 通过；未推送、未运行远端 CI。修复测试复用旧附件引发的唯一约束拒绝，保留约束和失败证据；v3 quantity 与 serial 均已终态通过，句柄63136 exit=0，每种模式两代业务、21 次真实 COMMIT、51 次 READ ONLY、11 份历史原请求和冻结份额/SN恢复；1591源无漂移，终态时与当前一致。收据 `scrap-http-formal-v3-receipt.json` 位于formal证据目录。serial一次提交观察到持续至少96秒，完整正确性通过后仍须性能剖析，未满足生产压测验收。

原始报废 PC/H5 页面已接入真实批准来源、文件上传、严格预览、显式确认、发送前持久化、只读恢复和显式封存。新旧前端八文件 179 项通过，TypeScript 与 warehouse Vite 构建通过。尚缺真实浏览器整页联测与来源 GET 原生测试；纠正报废和找回四阶段来源查询/页面仍未接入，不能将此页推广为全业务已完成。具体日志、收据、当前句柄和源冻结边界见 `CONTINUE_DEVELOPMENT.md` 顶部；此前旧节保留为历史。未提交、推送或部署，正式基线其他业务/生产验收缺口全部保留。

## 2026-10-03 权限与旧历史终态、客户端恢复增量

权限迁移后的运行时60079与真实旧历史数量/SN39557均已exit=0，1589源无漂移；新库11项默认授权及旧库仅4项新增/5用户版本推进已核验，旧业务事实和原请求回查保持。客户端完整原请求保存/互斥/只读恢复模块137项及TypeScript构建通过，页面和网络接入尚未完成。真实挂载HTTP与API-role数据库联测1393已exit=0：quantity五阶段、10次COMMIT、20次READ ONLY均通过，纠正来源/SN/真实供应商身份仍未覆盖；见`CONTINUE_DEVELOPMENT.md`最新节。未提交或部署，全部生产门槛继续保留。


## 2026-10-03 当前增量

完整0165数量/SN原生业务句柄63688已exit=0，两库均终态passed；该1582源证据属于HTTP/权限接入前。21个报废/找回HTTP路由现已挂入正式应用，实际源码新接口及原纠正接口280项聚焦通过。正式11项权限策略已补入尚未发布的0165迁移，候选真实PG16数据策略通过，但正式迁移后的旧账号/历史请求/回退与全量门禁仍待终态验证。正式权限迁移聚焦46项已通过；当前原生运行时60079、旧历史39557及后续运行入口见`CONTINUE_DEVELOPMENT.md`最新节。未提交、推送或部署；下文运行中/尚未挂载等为历史。


## 最新核对：数量业务通过，正式权限缺口尚未补齐

0165数量完整原生回归已终态通过，serial仍由句柄63688继续；搜索路径修复后的运行时v4、空库往返和旧版启动也已终态通过。前端结果契约48项及TypeScript构建通过，HTTP草稿167项通过，但HTTP挂载和PC/H5完整流程仍待接入。以交接文档和各自当前源码收据为准，不将单模式通过扩大为全套验收。

新建正式迁移库的只读审计确认：stock_operation已有17条退回/read角色配置，但报损、纠正、找回的11种操作权限及默认角色配置缺失。此前业务夹具显式创建这些权限，因此测试通过不能证明正式角色可操作。需补带旧配置/deny保护、授权版本失效和回退证据的正式权限迁移或配置流程。准确角色/动作清单见 `CONTINUE_DEVELOPMENT.md`；证据 `artifacts/formal-0165-integration/stock-operation-seed-audit-v1.json`。未向任何真实账号授权，未提交或部署。

## 新增已验证证据：真实旧历史升级与恢复

保留原版0164源码实际写入三轮冲销、纠正审批、纠正过账和封存后，数量/SN均通过正式0165升级、只读回查、降级后的原版完整API启动及再升级。分别保留519/531行旧记录和359个旧函数OID，各11份完整原请求准确恢复且全库事实不变。句柄5862已exit=0，两库均stopped/passed/serverExitCode=0；1581源运行无漂移，证据 `artifacts/formal-0165-integration/legacy-history-v1-receipt.json`。它验证的是合成旧业务历史兼容，不能替代生产历史导入或全业务上线验收。

当前完整报废/找回gate已接真实0165和实际Alembic拒降，v1暴露全链search_path继承导致新表误建系统schema的实际缺陷，正式迁移已修正局部路径；复跑句柄63688尚在运行；HTTP/PC/H5及其他正式缺口继续未完成。收货/独立入库恢复的32项前端聚焦另已通过。接续以 `CONTINUE_DEVELOPMENT.md` 的实时句柄和证据为准，以下段落按历史批次保留。

## 最新开发增量：0165正式入口已接入，验收未完成

当前代码已有Base十表登记、正式0165迁移、默认API完整目录校验、同步readiness及日终对账版本衔接。SQLite结构/真实Alembic往返和同名触发器聚焦38通过，安全/登记/迁移head聚焦329通过。原生PG16已通过真实Alembic空库升级/降级/再升级、默认API启动、8类篡改拒绝和降级后原版0164独立进程启动；终态stopped/passed/serverExitCode=0，当前源与原生清单一致。下文“正式入口尚未激活”描述属于前一源码快照。HTTP/PC/H5恢复、真实旧版历史迁移以及全部正式基线缺口仍未据此验收；继续以`CONTINUE_DEVELOPMENT.md`及实际终态收据为入口。未提交、推送或部署。

## 增量核对：统一模型和候选运行时目录

当前工作树HEAD为`fc7c926`，报废/找回的后续实现仍有未提交改动。下方`cb0210d`和CI描述是当时快照，**本轮没有重新查询GitHub，不代表CI仍在运行或当前源码通过**。

报废原始/纠正来源、数量/SN、独立找回审批、库存反向事实及请求封存已有候选实现和前轮原生业务证据；不能再把下表前三行的“执行不存在/拒绝SN报废”当作当前候选代码状态。但正式Alembic/Base/默认运行时入口仍未激活0165，HTTP及PC/H5完整接入未完成，这三项基线仍不能标为已验收。

本轮统一10表模型和共享写读句柄；候选完整API启动在真实PG16.15通过，保留旧独立目录验证器，7种真实目录篡改拒绝及精确恢复通过，322项安全聚焦通过。全部30个报废Python测试模块也已完成：354通过、3项数量模式不适用的SN检查按设计跳过，三进程均退出0。`scrap-runtime-catalog-v5-receipt.json`与`scrap-unified-regression-v2-receipt.json`位于`artifacts/formal-baseline-audit-20261002-0164/`，对应同一1568源且无漂移；它们不是正式版本迁移、完整当前数量/SN原生业务重跑或生产验收。具体证据及下一步见[开发交接](CONTINUE_DEVELOPMENT.md)。

部分/下游退回补偿、旧成色授权纠正、正式人员调拨、离职交接、遗留身份UUID迁移及报表打印等缺口继续保留；本轮未重新验收这些功能。真实渠道、身份/UAT、历史迁移/期初、三天对账、500用户性能、RPO/RTO及三种回滚均仍需各自证据。未提交、推送或部署。

## 以下为0164发布时的审计快照

审计源码：`cb0210da25881d2198a6e31b2195a894e4e74409`，分支 `codex/notification-delivery-worker`。本文件取代旧审计入口中的“当前状态”；历史证据保留，不能把不同版本的测试数累加成当前版本全量通过。依据根目录正式 V1.0 基线及用户后续明确的公开入口调整。

**结论：0164 修复已提交并推送，完整正式版本尚未达到上线条件。** 除生产验收外，源码仍有明确业务缺口。客户端门禁已通过，数据库门禁尚未终态；不提供缺少分母和验收依据的完成百分比。

## 1. 已发布到开发分支的证据

- 本地、远端分支均为上述完整 SHA，原有改动没有 reset、revert 或丢弃。
- [客户端 CI](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36926005738) 为 `success`。
- [PG16 CI](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36926005735) 于北京时间 2026-10-02 05:08 核验：66 项中 5 项成功、20 项运行、41 项排队，无终态失败。运行总状态的 queued 不代表所有子任务都未启动；接续必须读取同一 run 的 jobs。
- 合并后本地 83 项聚焦、仓库安全、0164→0140→0164 迁移往返、完整启动及 ACL 已有终态。候选六组报损纠正和完整控制数流程另有独立证据，不冒充当前 SHA 云端结果。
- 0164 只修复认证/授权审计误争库存锁；库存、需求及三类报损封存继续保护。UUID 临时库引导、迁移加载性能修复也在同批提交内。详见 [0164 发布说明](AUDIT_LOCK_SCOPE_0164_RELEASE_NOTES.md)。

本轮本机证据目录 `artifacts/formal-baseline-audit-20261002-0164/`：`source-audit.json` 保存审查文件摘要、ORM 元数据与 PostgreSQL 类型编译结果；`github-20261001T210839Z.json` 保存远端 SHA 和两条 CI 的准确任务快照。元数据检查没有连接数据库，不能代替实际迁移或数据检查。

## 2. 本轮确认的功能缺口

以下为直接读到的实现限制，不能被其他业务的绿色测试抵消。路径相对 `cloud_oam/`。

| 基线要求 | 当前可核验状态 | 未完成范围及验收依据 |
| --- | --- | --- |
| §1.11 报损五种处置，批准后报废 | 普通处置仅支持恢复可用、转旧、转坏；退回有独立完整前向履约；总部决定枚举包含 scrap | 报废执行不存在。`stock_operation_models.py` 的单头 CHECK 只有 return/loss_report，原始处置 CHECK 不接受 scrap，目标账户仍非空。不能把“总部选了报废”当作资产已经移出 |
| §1.11 SN 报废及失而复得 | `formal_services/inventory_posting.py` 明确拒绝 SN scrap；`serial_ledger.py` 重建同样拒绝 | 必须同时补持久报废单、精确批准/原流水/SN 关联、原子过账、生命周期重建和 PG COMMIT 证明，以及有审批的反向单；禁止只删除拒绝分支 |
| §1.11 误报纠正覆盖所有处置 | 原始恢复/旧/坏支持冲销及多代纠正；`stock_loss_corrections/correction_stock.py` 仍只接受这三种纠正结果 | 纠正批准为退回/报废时仍不可执行。应覆盖原始与纠正两种来源，不能只实现首次报废而遗漏冲销后重新批准 |
| §1.6、1.8、1.11 退回补偿 | 0163 已实现整单未出库停止，含恢复、永久封存及出库竞争 | `reversal_stock._return_boundary` 对任一下游事实拒绝，`return_boundary.document` 要求全量原数量/SN 未出库。部分出库余量停止、已出库/发运/验收/入账后的实际逆向业务仍缺。物理未退回时不得凭数据库逆账声称保管责任已恢复 |
| §1.6、3.11.9 旧错误成色纠正 | 0162 新入库按原成色/坏件正确拆分；`stock_loss_corrections/return_quality.py` 能只读识别旧 1.0 分类异常 | 旧异常返回 `current_stock_verified=False`、`correction_authorized=False`。仍需当前库存核验、权限/独立批准、准确数量/SN、追加转换或补偿事实及原请求恢复；不得改原验收、入库或审计 |
| §1.6 人员间调拨，§1.11 双方确认交接 | 库存原子 transfer 类型与需求履约存在；旧 `routers/transfers.py` 仍保留 | `main.py` 仅在非生产环境挂载旧 transfers；正式作业 CHECK 也没有人员间调拨类型。需要正式调拨来源、目的保管人、双方确认、数量/SN、独立实物状态、恢复和客户端。旧原型不作为生产实现 |
| §1.4、1.11 离职受限登录与清零交接 | 账号已有 restricted_handover，权限层限制新业务 | ORM 元数据无 employee_handover_cases/checks；`inventory_models.CustodyAssignment.handover_case_id` 明确是无 FK 预留；`App.tsx` 无交接路由。预警、清零检查、分批移交、双方确认、区域/总部复核及受限页面均未形成完整闭环 |
| §3.1 主键使用原生 UUID | 正式新增事实多使用 UUID；原 `User.id` 仍文本 | 元数据编译同时发现 users/auth_sessions/wechat_identities 的主键是 VARCHAR(36)，auth_identities 为 UUID。不是只改 users 一列；须盘点所有 FK、会话及历史请求，并先证明现存值可无损一一映射。不可静默重写不可变历史 |
| §1.12 一期报表与打印 | 正式库存余额异步导出、下载计数/恢复及私有存储有实现；日终对账有独立入口 | `routers/formal_reports.py` 仅挂载 inventory-balances。尚无证据证明需求/履约漏斗、工单、盘点、账龄周转、全部打印模板及报表订阅均已完成。需逐项实际产物和权限校验，不能以一个库存 Excel 覆盖整项 |

这次没有重跑全部业务测试，也未对每项尚未核验的功能断言“代码不存在”。表中对不存在/被拒绝的结论都有具体模型、生产挂载或执行分支依据；其余保留为待核验。

## 3. 其余正式范围与上线证据台账

| 要求 | 现有实现或边界 | 仍需取得的正式证据 |
| --- | --- | --- |
| §1.1 身份、角色、审批代理与禁止自审 | 当前主体/权限版本、作用域、deny 优先和外部审批受限框架 | 三内部角色及外部审批角色真实账号 UAT；代理范围/生效时间、撤权及自审的完整矩阵 |
| §1.2 工作台、任务中心、PC 菜单 | `App.tsx` 有库存、需求、盘点、通知、报损及对账路由 | 全基线菜单对应可操作流程，包含遗漏的交接/人员调拨/报表等；任务中心全部待办与超时不能仅用菜单存在证明 |
| §1.3、用户后续入口要求 | 公开首页知识查询；星星按钮 `/xx`；小程序保持公开查询 | 公开页、移动端与 `/xx` 实际发布验收；飞书知识源依用户要求低优先级，仍未作为已完成来源 |
| §1.4 无密码登录 | 短信/微信、挑战频控、会话与身份映射代码及配置门禁 | 真实短信送达和成功认证、微信真实身份绑定、停用限制/设备撤销。9 项短信配置保护不是短信送达证据 |
| §1.5 OAM 只读层 | 控制数/映射/发布/日终流程有原生证明；凭据留本地 | 获授权的实际组织、人员、SKU、工单与历史附件数量/hash/关联校验，新鲜度及冲突处置；不把省级控制数当个人库存 |
| §1.6 库存/SN/批次 | 不可变流水、投影、账户/期初/权限门禁 | 全正式业务的数量守恒、SN 唯一、批次/账龄、投影重建与并发证据；缺口见上表 |
| §1.7 需求/三级逐行审批 | 正式 demand、分配/占用、供应参考任务、替代料及取消接口存在 | 三级部分审批、退回重审、自审禁止、替代比例、取消释放、外部登记双人复核的准确 SHA 测试及真实 UAT |
| §1.8 履约 | 正式出库、包裹、物流事件、分批验收与个人入账分别存在 | 多来源/多包裹/分批、短少/破损/错料/错 SN/拒收与最终批准数量关闭的实际完整流程；OAM 收货和通知分别验收 |
| §1.9 工单 | 正式工单物料占用/释放/消耗/回收/冲销入口 | 本人工单、三码、批量原子失败、配对回收、关单未结检查及旧坏件退回 UAT |
| §1.10 盘点 | 期初与非期初任务/实盘/复盘/差异/过账/关闭入口 | 明盲盘、冻结/截止游标、差异各类别、逐 SN 及闭单对账全覆盖；离职盘点不能代替交接流程 |
| §1.13 通知 | 通知事件、投递、回执、未知结果/重试与管理入口 | 实际微信、短信、飞书各渠道送达/失败/回执；机器人回调身份、验签与幂等；消息成功不能代替业务状态 |
| §3、§6 数据约束 | PG16、迁移、权限/不可变事实/幂等守卫 | 当前 SHA 全门禁终态；升级历史、回退保留和新功能 SQL 绕过测试；UUID 物理类型缺口独立保留 |
| §4–5 部署与正式上线 | 有部署、备份及预检工具，本轮无生产写入 | 目标环境配置、真实身份/OSS/HTTPS；历史迁移及期初实盘复核；连续至少三天差异有解释；500 用户性能；RPO≤5分钟/RTO≤2小时；备份恢复、应用回滚、同步回退及业务冲销演练；书面 UAT/上线验收 |

## 4. 接下来的实施顺序

1. 持续读取当前 SHA 的 66 项 PG16 门禁，失败按准确 job 日志定位；不因观察超时重启，不以旧 SHA 或本地结果替代。已提交实现不因审计而回滚。
2. 补报废与失而复得完整业务链：原报损终审和纠正终审两种来源，数量/SN 同时覆盖；共用原子过账、请求恢复/封存、独立审批与前端。详见 [0164 后报废实现方案](STOCK_SCRAP_IMPLEMENTATION_AFTER_0164.md)。
3. 补部分/下游退回补偿及旧分类纠正，按真实物理节点设计，保留原事实与旧请求。
4. 补正式人员间调拨、离职交接，随后完成菜单/任务中心、剩余报表和打印。
5. 并行于设计安排 UUID 兼容迁移评估；功能实现完成后仍逐项执行真实渠道、迁移、UAT、对账及非功能验收。不得把试点、CI 全绿或只读公开网站等同完整正式上线。

完整目标继续有效。本审计不授权外部业务写入或生产迁移；当前不需要用户重新登录、重启客户端或提供密码/密钥。

## 2026-10-07 正式验收审计增量：试点 MVP 范围冻结

当前工作树将首发范围冻结为**试点 MVP，不等同完整 V1**：`申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账`。

- `FormalMaterialRequests` 对试点页面关闭 `allowSupplyPlanning`，不展示供给容量计划的创建、列表或编辑；最小货源候选读取、分配事实、幂等/恢复哨兵和后端接口仍保留。该界面门禁不删除底层状态轴、迁移、契约、出库依赖或审计事实。
- 发运面板以 `allowLogisticsEvents` capability 隐藏物流事件读取、登记和回读，试点只保留人工发运记录；物流 API、恢复哨兵和默认组件能力仍保留。
- 试点页面通过 `TRIAL_MVP_UI_SCOPE.showOamReceipt=false` 隐藏 OAM 收货证据面板；OAM 收货状态轴、只读适配器与审计事实仍保留，待后续对账迭代。
- 试点页面默认关闭 `TRIAL_MVP_UI_SCOPE.showReturnOperations`，隐藏拒收退回/退回补偿操作；已有恢复哨兵会重新显示核验入口，避免未知结果被遮蔽。
- 试点页面默认关闭 `TRIAL_MVP_UI_SCOPE.showReleaseOperations` 与 `TRIAL_MVP_UI_SCOPE.showComplexLifecycle`，隐藏释放、剩余取消和复杂关闭操作；已有哨兵仍显示核验入口，底层取消/关闭守卫保留。
- 拣货隐藏保持。技术员 capability 只开放申请、状态查看、本人收货和本人入账；区域/总部保留必要的后台人工履约入口。拒收/退回补偿、`stock-return` 全链、工单消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维均列入后续迭代，不作为本次验收通过项。
- 新增范围测试：`FormalMaterialRequestSupplyPanel.test.tsx` 试点供给计划隐藏 `1 passed`，`FormalMaterialRequestShipmentPanel.test.tsx` 物流事件隐藏 `1 passed`；TypeScript `tsc -b --pretty false`：`exit 0`。本次未重跑已终态测试，未提交、推送或部署。
- 新增 `trialMvpScope.test.ts` 聚焦测试（`1 passed`）与源码范围门禁 `trial-mvp-ui-scope=PASS`：OAM、退回补偿、释放、复杂生命周期默认关闭，待核验哨兵可重新打开；物流事件和供给计划入口关闭。
- 发布绑定继续收紧：`frontend/build/verify-pilot-release.mjs` 静态读取 `src/trialMvpScope.ts`，逐项锁定六个后置 capability 为 `false`，并要求 scope 对象使用 `Object.freeze`；`node --check`、`pilot-ui-scope-release-binding=PASS` 和一次试点 artifact 验证均通过（`exit 0`，`privatePath=/xx/`）。该验证明确回报公开目录 `status=pending, records=0, publicCatalogIsReady=false`，因此只证明私有试点构建自洽，不代表公开/完整生产发布；hosted PostgreSQL 16 仍是外部阻塞，未用本地结果替代。
- `App.tsx` 全局入口复核确认技术员菜单与直接 URL 均隐藏/重定向库存、盘点、报损、退回、报表、通知、对账和人员管理后置模块，需求提报仍可进入；依赖既有聚焦覆盖，本轮未重跑已终态测试。
- PG16 hosted DB 门禁仍记录为外部阻塞；本地编译和聚焦 UI 测试不能伪造 hosted DB、生产迁移、真实渠道或上线通过。
# 2026-10-07 当前树 Web/小程序全套回归复核

- Web 当前树 `pnpm test`：**174 个测试文件，2900 passed、13 skipped、0 failed，exit 0**。
- 小程序默认并发首次出现 1 个失败（1068/1069 passed，失败文件 `tests/formal-stocktake-pages.test.js`）；该文件单独复跑 50/50 通过。改用 `node --test --test-concurrency=1 tests/*.test.js` 后当前树 **1086 passed、0 failed，exit 0**；随后再次使用 CI 默认并发命令也为 **1086 passed、0 failed，exit 0**。首次异常保留为稳定性记录，不伪装成从未发生。
- 该结果只证明当前源码在本地 Web/小程序契约层可复核，不替代真机/浏览器 UAT、真实短信、hosted PostgreSQL 16、目标机部署或回滚。

# 2026-10-07 后端全套探索性运行（未通过）

- 当前树执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`，20 分钟后因早期 SQLite/Alembic 测试持续占用而中断（exit 130）；中断前汇总为 **1659 passed、87 failed、59 errors、15 subtests passed、1 warning**。
- 失败主要来自历史 `test_core_flows.py` 模块级启用密码/微信，与当前 SMS-only Settings 保护冲突，并造成同一进程后续配置/HTTP 测试级联失败。该结果不能伪装成后端全套通过，也不能替代 hosted PostgreSQL 16；首发窄门禁和前端/小程序证据仍按各自范围单独记录。

# 2026-10-07 原 Edge 入口与云端状态复核

- 原 Edge 兼容性检查 exit 0，确认 `http://127.0.0.1:9224` 入口及原 profile 可用。
- 浏览器接管只读状态读取在 30 秒后超时并重置内核；未将其解释为登录失败或服务器失败，也未重试或执行远端写入。目标服务器、hosted PostgreSQL 16、PNVS/UAT 和部署回读继续保持未知。

# 2026-10-07 前端双入口构建复核

- 首次 `pnpm run build` 仅因 shell PATH 缺少 `node` 退出 1；在不改代码、不安装运行时的前提下，使用仓库 bundled Node 重跑 `pnpm run build` 与 `pnpm run build:warehouse`，均 **exit 0**。
- `pnpm run verify:pilot-release` 与 `cloud_oam/scripts/verify_public_entry.mjs` 均 **exit 0**；产物回读保持 `catalog=pending/0`、私有入口 `/xx/`、公开 bundle 不含登录客户端。该结果支持试点入口构建，不把知识目录或真实登录写成生产通过。
- 构建结果仍不替代 hosted PostgreSQL 16、PNVS/RAM、短信真实投递、浏览器/真机 UAT、目标服务器部署和回滚证据；当前版本继续冻结为“试点 MVP，不等同完整 V1”。

# 2026-10-08 当前树后端受控运行与迁移头绑定修复

本轮仅处理受控后端探索中暴露的两处版本绑定漂移，不改变“试点 MVP，不等同完整 V1”的验收范围。全后端命令在发现两个明确失败后主动停止，保留终端结果：**2890 passed、7 skipped、2 failed、15 subtests passed、1 warning，exit 2**。失败分别是库存控制 CLI 仍 pin `20261215_0166`、而实际 Alembic 单头为 `20261227_0178`，以及历史 0167 来源链测试错误要求当前 gate head/hash 等于历史 0167。

修复后，`scripts/configure_inventory_control.py` 只将当前预检 head 更新为 `20261227_0178`；历史来源测试只对比冻结的 0167 manifest，并继续独立校验当前 manifest。相关文件复核 **60 passed、1 warning、exit 0**。该修复没有改写历史迁移、删除 0177/0178、放开后置供给能力或重跑已终态客户端/通知/数据库安全测试。

本轮不能关闭 hosted PostgreSQL 16 门禁：本机没有可审计 PG16，远程当前 SHA/CI 尚未重新产生；PNVS、真实短信投递、浏览器/真机 UAT、部署、备份恢复和回滚也仍无证据。因此当前结论仍为：本地代码与局部门禁可继续推进，版本冻结为试点 MVP，未达到正式发布/完整 V1 验收，也未提交、推送或部署。

补充当前头一致性证据：使用仓库规定的 `cache_migration_compilation` 读取 Alembic 图，结果为单头 `20261227_0178`；控制 CLI `REQUIRED_HEAD`、PG16 gate `HEAD_REVISION` 与当前 readiness manifest hash 均一致。改动脚本编译和 `git diff --check` 通过。首次复核仅因测试模块 import path 漏配退出 1，修正路径后通过，不影响产品代码，也未重跑已终态业务测试。

## 2026-10-11 当前树安全扫描修复

当前树仓库安全扫描首次发现交接文档含两处个人 macOS 绝对路径，按个人路径门禁以 exit 1 拒绝；未发现 AccessKey 或其他凭证。已将路径改为不含个人目录的占位描述，随后同一 `bash scripts/verify_repository_safety.sh` 复核通过：**3062 candidate files、75,817,630 bytes、exit 0**。这是当前未提交树的安全证据，不替代 hosted PostgreSQL 16、真实 PNVS/UAT、部署或回滚验收。

当前树安全回执 `artifacts/formal-0165-integration/current-tree-safety-20261007T220734Z.json` 固定记录了 HEAD、工作树计数 776、仓库安全扫描 exit 0、个人路径/凭证发现为 0，以及交接/审计文档哈希；同一回执明确 hosted PG16、当前头远程 CI、真实 PNVS/UAT 和部署回滚仍为 `unverified`。

## 2026-10-08 当前树部署预检回执（仅配置预检，非上线证据）

- 在当前工作树 HEAD `fc7c9269e19f6afd180a0aced55b565f03c244b6` 上执行 `python3 scripts/pilot_preflight.py`（工作目录 `cloud_oam/`），退出码 **1**。
- 失败项精确为 `env_file` 和 `docker_cli`：当前工作树没有本地 `.env`，本机也没有 Docker CLI。因此不能在本机解析真实 Compose 配置、启动数据库或服务；未创建占位密钥、未读取/打印任何凭证，也未自动补齐环境文件。
- 预检明确保持 `deploymentReady=false`，并继续将目标引擎/镜像、HTTPS、短信送验、KMS 解密、OSS、数据库迁移/角色、身份/期初、备份恢复和业务 UAT 标为未验证。
- 原始 JSON 回执：`artifacts/formal-0165-integration/pilot-preflight-current-20261008.json`，SHA256 `03cd6dbaa0bcb59f6fa8fe0975f401497914c0c64d769f4d75e1a601eda96070`。该回执只记录配置预检结果，不替代 hosted PostgreSQL 16、真实 PNVS/SMS、UAT、部署或回滚。
- 本次没有重跑已终态 Web/小程序/通知/数据库安全/完整后端测试，没有提交、推送或部署；试点 MVP 范围和“非完整 V1”结论保持不变。

## 2026-10-08 当前树与远端 ref 绑定复核（只读）

- 只读执行 `git ls-remote origin refs/heads/codex/notification-delivery-worker refs/heads/main`：远端试点分支仍为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`，`main` 为 `0bb7091e907a4090c71ab5a2da303bcf66e7cd53`。
- 本地 HEAD 也为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`，但工作树有 **776** 项未提交改动；因此远端 ref 只覆盖旧提交，不能证明当前工作树代码已通过远程 CI。
- 回执：`artifacts/formal-0165-integration/current-tree-remote-ref-20261008.json`，SHA256 `d709586ab056594b16b5d0b6bc351baa7b76dc160b94ce034d39d40909eee73f`。未执行推送，未触发新 CI；远程当前树验证保持 `unverified`。

## 2026-10-08 本地数据库探针（不是 PG16 门禁）

- 只读进程探针发现本机 `127.0.0.1:5432` 由 `<local-postgres-runtime>/18.1.0/bin/postgres` 提供，数据目录的 `PG_VERSION` 为 `18`；因此它不是要求的 PostgreSQL 16 hosted DB。
- 连接探针在未提供密码时被服务端拒绝；没有读取、猜测、打印或保存密码，也没有停止或重启该长期运行进程。
- 回执：`artifacts/formal-0165-integration/local-database-probe-20261008.json`，SHA256 `577c7fc88c3d93505d51740612995d58151ad05728510e283fb802100ff3c829`。该结果只排除了“本地已有可用 PG16”的假设，不能替代 hosted PostgreSQL 16。

## 2026-10-08 当前发布门禁台账

- 当前台账：`artifacts/formal-0165-integration/current-release-gate-ledger-20261008.json`，SHA256 `44b8f8583767caac3480915a34b6e0d5cdd417e56c1795b3a08e50fbd0cedb56`。
- 台账一致性校验读取当前 HEAD、工作树状态计数及四份独立证据文件并逐一核对 SHA：**PASS，exit 0**。台账结论为 `releaseDecision=not_ready`，明确分开记录源码、迁移/权限、HTTP/API、客户端、当前 CI、PNVS/UAT 和部署回滚状态。
- 台账不改变任何业务状态，不替代 hosted PostgreSQL 16、真实 PNVS/UAT 或部署回滚证据。
