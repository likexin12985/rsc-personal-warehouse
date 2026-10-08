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

- 已执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests --maxfail=20`，输出保存在 `/tmp/rsc-backend-current-20261008.log`。该命令在约 45 分钟后主动终止，保留了 SMS-only/默认凭证夹具修复后的当前树失败点：**2890 passed、7 skipped、2 failed、15 subtests passed、1 warning，exit 2**。
- 两项失败均为迁移当前头绑定漂移：控制 CLI 仍 pin 0166，历史 0167 来源链测试错误绑定当前 head/hash。随后已修复并完成相关文件 **60 passed、1 warning、exit 0**；该全套探索不构成门禁通过，也不需要继续轮询已终态进程。
- 即使最终全绿，也只代表本地当前树；hosted PostgreSQL 16、远端同 SHA CI、真实 PNVS/UAT 和部署门禁仍需独立证据。

## 2026-10-08 生产适配、KMS 与候选预检聚焦回归（当前工作树）

- 执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_production_adapter_composition.py backend/tests/test_kms_pin_gate.py backend/tests/test_pilot_preflight.py`：**91 passed, 0 failed, exit 0**（2.60s）。生产适配组合、KMS pin gate 和 `trial-mvp`/SMS-only/默认凭证链预检保持通过。
- 这是本地配置与发布前置契约证据，不启动 KMS、PNVS、PostgreSQL 或目标服务器；真实凭证链、hosted PG16 和部署回读仍未验收。

## 2026-10-08 数据库安全与部署静态聚焦回归（当前工作树）

- 执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_database_security.py backend/tests/test_database_deployment_security.py backend/tests/test_database_security_trigger_coordinates.py`：**331 passed, 0 failed, exit 0**（77.89s）。当前函数/触发器目录、ACL/安全边界、部署安全和坐标约束聚焦保持通过。
- 该结果是 SQLite/源码目录和契约层证据，不能替代 hosted PostgreSQL 16 的真实迁移、权限、并发和回滚运行；本机仍没有可审计 PG16 运行时。

## 2026-10-08 通知投递运维聚焦回归（当前工作树）

- 针对当前分支的通知投递范围执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_notification*.py`：**151 passed, 0 failed, 1 warning, exit 0**（339.95s）。覆盖通知事件、目标身份、扩展、队列/投递、未知结果、防重放、恢复、运维读回和审计链路。
- 该结果只证明本地服务/契约和状态轴独立性；真实 PNVS/微信/飞书渠道、provider message id、送达回执、真实 out_id、生产限流和目标机运维仍未验收，不得写成通知已上线。
- 版本继续为**试点 MVP，不等同完整 V1**；当前 HEAD `fc7c9269…`，工作树未提交，hosted PostgreSQL 16、同一新 SHA 远端 CI、真实 UAT 和部署/回滚证据仍缺。

## 2026-10-08 正式 HTTP/API 与默认凭证链夹具收口（当前工作树）

- `backend/tests/test_formal_material_request_api.py` 首次聚焦发现两个生产启动校验用例仍把 PNVS 夹具设为 `sms_credential_mode=static`，与默认 ECS/RAM 凭证链门禁冲突；已仅修正测试夹具为 `default_chain` 并清空 AccessKey 字段，生产配置和门禁没有放宽。复跑结果：**59 passed, 0 failed, exit 0**。
- `backend/tests/test_formal_auth_api.py backend/tests/test_formal_files_service.py` 组合结果：**88 passed, 6 skipped, 0 failed, exit 0**；其中 6 个微信 provider 历史行为用例因短信-only 试点范围显式跳过。
- `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_formal_*.py` 当前树正式 HTTP/API 套件结果：**485 passed, 6 skipped, 0 failed, 1 warning, exit 0**，耗时 555.89s。该结果覆盖正式路由、认证幂等/审计、文件服务、权限和默认凭证链夹具，不是 hosted PostgreSQL 16 或真实 PNVS/UAT 通过。
- 当前版本仍为**试点 MVP，不等同完整 V1**；当前 HEAD 仍为 `fc7c9269…`，工作树未提交，hosted PG16、同一新 SHA 远端 CI、真实 PNVS/out_id/验证码回读、浏览器/真机 UAT、目标机部署/回滚继续缺证，不提交、不推送、不部署。

## 2026-10-08 SMS-only 历史测试兼容收口（当前工作树）

- 针对上一轮后端探索中暴露的测试环境污染，`backend/tests/test_core_flows.py` 已改为短信验证码登录；停用的密码开户改为测试数据库直接种子，生产 `/api/auth/users` 仍保持关闭。聚焦结果：`PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_core_flows.py`：**5 passed, 0 failed, exit 0**。
- `backend/tests/test_formal_auth_api.py` 中仅覆盖已停用微信 provider 的历史行为已明确标记为短信-only 试点跳过，并将混合认证门禁改为验证微信返回 404；结果：**44 passed, 6 skipped, 0 failed, exit 0**。这没有重新打开微信或密码登录。
- 组合验证 `backend/tests/test_core_flows.py backend/tests/test_formal_auth_api.py`：**49 passed, 6 skipped, 0 failed, exit 0**；此前受模块级认证环境污染影响的 `test_health_readiness.py` 与 `test_startup_security_boundary.py`：**38 passed, 0 failed, exit 0**；`test_upgrade_head_matches_current_orm_and_downgrades`：**1 passed, 0 failed, exit 0**（148.43s）。
- 测试辅助函数只把本地测试库中的历史短信挑战时间移出频控窗口，生产限流、审计、未知结果和防重放实现未改。上一轮 2026-10-07 的后端全套中断汇总不能直接代表本次修复后的全套状态；尚未重跑耗时全套，也不能写成全后端通过。
- 当前版本仍为**试点 MVP，不等同完整 V1**；hosted PostgreSQL 16、同一 SHA 的远端 CI、真实 PNVS/RAM/out_id/验证码读回、浏览器/真机 UAT、目标服务器部署与回滚仍未形成证据，不提交、不推送、不部署。

## 2026-10-07 当前试点 MVP 发布证据矩阵（当前工作树）

| 类别 | 当前证据 | 状态 | 尚缺证据/限制 |
| --- | --- | --- | --- |
| 源码与首发链路 | 路由/状态轴静态检查 `PASS`；审批→分配→预留→发运→本人收货→个人仓入账服务组 **114 passed**；状态轴/发运/入账组 **38 passed** | 本地通过 | 不替代 PG16 真库事务、外部来源和真实 UAT |
| 正式迁移与权限 | 迁移/函数/ACL 指纹聚焦 **9 passed**；发布绑定/回滚契约 **105 passed**；PG16 工作流拓扑 **63 passed** | 本地候选 | 当前工作树没有 hosted PostgreSQL 16 运行证据 |
| HTTP/API 与入口 | 公开首页、`/xx`、health/live/ready、SMS-only、no-store 和 daily-ops 聚焦 **36 passed** | 本地通过 | 不替代真实域名/TLS、浏览器回读和生产服务 |
| PC/H5/小程序 | 当前树 Web **2900 passed/13 skipped**、小程序默认/串行复核均 **1086 passed/0 failed**、客户端契约 **65 passed**；试点 capability 与 SMS-only 静态检查通过 | 本地通过（保留一次并发稳定性异常） | 不替代真机/浏览器现场 UAT |
| 当前版本 CI | GitHub 远端分支仍为 `fc7c9269…`；该 SHA Client gate 成功，PG16 gate 失败；未提交树没有新 run | 未放行 | 必须用同一新 SHA 获取完整 CI 终态，不能复用旧 run |
| 真实 UAT/短信 | PNVS/RAM、真实 `out_id`、验证码精确读回、限流/审计/未知结果防重放、浏览器/真机 UAT 均未验收 | 阻塞 | 真实授权会话和隔离号码回读缺失 |
| 部署/回滚/上线 | 本地候选脚本防旁路和失败回滚已通过；目标服务器状态和部署回读未知 | 未放行 | hosted PG16、目标机、KMS、备份/回滚演练缺失 |
| 公共知识目录 | 公共目录保持 `pending/0`，知识源按用户要求暂缓 | 试点允许 | 严格公共目录 release 仍未就绪 |

上述矩阵只适用于当前工作树；历史矩阵、旧 SHA、可见页面和局部测试不能升级为正式上线证据。所有外部门禁齐全前保持“试点 MVP，不等同完整 V1”，不提交、不推送、不部署。

## 2026-10-07 后端全套探索性运行（未形成门禁通过）

- 为检查当前未提交后端树，执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests`。该套件共有约 4624 项测试；运行 20 分钟仍停留在早期 SQLite/Alembic 迁移兼容段，出现大量后续级联失败后发送一次 Ctrl-C，进程退出 **130**。
- 中断前 pytest 汇总为 **1659 passed、87 failed、59 errors、15 subtests passed、1 warning**；不能把它写成全套后端通过，也不能把中断当成 hosted PostgreSQL 16 结果。
- 失败主体已定位到旧 `backend/tests/test_core_flows.py` 在模块导入时把 `OAM_PASSWORD_LOGIN_ENABLED` 和 `OAM_WECHAT_LOGIN_ENABLED` 设为 `true`，而当前 Settings 按 SMS-only 策略拒绝这两个值，随后污染同一进程中大量 `Settings`/HTTP 测试；`test_core_flows.py` 仍包含密码/微信成功断言。这是待单独重写或隔离的历史测试兼容问题，不能通过放宽产品策略解决。
- 首发链路聚焦组、公开入口 smoke、迁移/ACL 指纹聚焦、Web/小程序当前树套件仍按本文件各自结果记录；本次探索性全套失败不覆盖或推翻这些窄范围证据。正式 CI、hosted PG16 和生产发布门禁继续保持未放行。

## 2026-10-07 原 Edge 入口与云端状态复核

- `python3 <CodexLocalEdge>/ensure_local_edge.py --compatibility`：**exit 0**，确认仍连接原 Mac Edge profile、兼容入口 `http://127.0.0.1:9224`，Windows SOCKS 配置保持不变。
- 随后通过现有浏览器接管入口执行一次只读状态读取，30 秒后超时并重置 CUA 内核；这不是 `auth_required`、SSH 失败或服务器故障证据。本轮不重试、不读取凭据、不执行远端命令或写入。
- 因此本地 Edge 入口可用，但目标服务器、hosted PostgreSQL 16、真实 PNVS/UAT 和部署状态仍为未知；不能把兼容性通过升级为云端验收通过。

## 2026-10-07 当前树 Web/小程序全套回归复核

- Web 当前树使用 bundled Node 执行 `frontend` 的 `pnpm test`：**174 个测试文件，2900 passed、13 skipped、0 failed，exit 0**；执行耗时 84.33s。
- 小程序首次默认并发 `node --test tests/*.test.js` 出现 1 个失败（总计 1069 项，1068 passed、1 failed，失败文件为 `tests/formal-stocktake-pages.test.js`）；该文件单独重跑为 **50 passed、0 failed**。为确认是否为跨文件并发竞争，使用 `node --test --test-concurrency=1 tests/*.test.js` 串行复核：**1086 passed、0 failed，exit 0**；随后再次按 CI 默认并发执行同一命令：**1086 passed、0 failed，exit 0**。
- 首次默认并发失败没有复现，仍保留为一次测试稳定性异常记录，不把它抹成历史上从未发生。上述两端仍是本地产物/契约测试，不替代真机、浏览器现场 UAT、短信真实投递或生产部署。

## 2026-10-07 公开入口与持续运行 smoke 聚焦回归

- 当前工作树执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pilot_smoke_public_entry.py backend/tests/test_daily_ops_cli.py`：**36 passed, exit 0**。
- 该组覆盖公开首页知识入口、`/xx` 私有 warehouse 入口、健康/live/ready 探针状态与 no-store、SMS-only 登录选项和未登录私有响应头、`trial-mvp` scope 绑定，以及 daily-ops CLI 参数拒绝边界。
- 这是本地合成 smoke/CLI 证据，不替代真实域名/TLS、浏览器 UAT、hosted PostgreSQL 16、PNVS/RAM、目标机部署或回滚；未产生外部写入。

## 2026-10-07 远端 ref 与当前工作树绑定复核

- GitHub API 只读回读 `codex/notification-delivery-worker`：远端分支存在，当前 commit 仍为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`；没有新的远端 SHA 或新的 CI run 可对应当前 776 项未提交改动。此前 `git ls-remote` 空输出未作为结论，避免把认证/传输探测差异误判为分支缺失。
- 因此当前本地证据与历史 run 必须分开：旧 run 只代表已提交 `fc7c926`，工作树聚焦通过代表未提交源码集；在完成外部门禁前不提交、不推送、不部署。

## 2026-10-07 首发服务顺序聚焦回归

- 当前工作树按首发顺序执行服务测试：`test_material_request_lifecycle_service.py`、`test_material_request_approval_schema.py`、`test_material_request_allocation.py`、`test_material_request_reservation.py`、`test_material_request_shipment.py`、`test_material_request_inbound_posting.py`，结果 **114 passed, 1 warning, exit 0**。
- 该组覆盖申请/审批状态、最小分配、预留、发运登记、个人收货/入账的幂等、失败回滚、权限重新核验和状态投影；不把 OAM 收货、通知或对账自动推进为个人仓入账。
- 这是本地合成服务证据，不替代 hosted PostgreSQL 16 的真实事务/ACL、真实 PNVS、浏览器/真机 UAT 或目标机部署；未产生外部写入，也未重跑终态全量门禁。

## 2026-10-07 首发状态轴与本人入账最小后端回归

- 当前工作树执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pilot_mvp_state_axes_contract.py backend/tests/test_material_request_shipment.py backend/tests/test_material_request_inbound_posting.py`：**38 passed, 1 warning, exit 0**。
- 该组覆盖冻结状态轴逐步推进、发运登记不重复库存移动、发运超量拒绝、命令结果幂等恢复，以及本人收货/个人仓入账的首次过账、重复提交、失败回滚和权限/身份重新核验。
- 这是本地合成服务/HTTP 契约证据，不替代同一 SHA 的 hosted PostgreSQL 16 事务/ACL、真实 PNVS、浏览器/真机 UAT 或部署回读；没有外部写入，也未重跑终态全量门禁。

## 2026-10-07 发布绑定与失败回滚边界复核

- 当前工作树执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pilot_release_binding.py backend/tests/test_pilot_deploy.py`：**105 passed, exit 0**。覆盖候选外状态目录、项目/tag/镜像与配置指纹、一次性不可变准备回执、端口占用与监听器拒绝、KMS pin gate 在 api/web 前、准备前置和失败不做破坏性清理。
- 该结果是本地候选发布/回滚契约证据，不启动 Docker、PostgreSQL、KMS、PNVS 或目标服务器；不替代同一 SHA 的远端 CI、hosted PostgreSQL 16、真实 UAT、部署回读或回滚演练。

## 2026-10-07 远端当前提交失败回读与工作树修复复核

- 只读回读 GitHub run `36931116983`（HEAD `fc7c9269e19f6afd180a0aced55b565f03c244b6`）：Client release gate 为 `success`；PostgreSQL 16 release gate 为 `failure`。失败的两个 static shard 共暴露 10 个失败用例：shard 2 的 4 个 H5 return fixture 参数化用例仍为 `schema_version=1.0`，另有 0106 函数指纹漂移；shard 1 的 5 个 database-security/derived-return 用例暴露 0047 目录、0052 函数/ACL、0046 触发器目录和 0152 指纹漂移。
- 两个 `pg16_runtime` 矩阵腿不是断言失败：日志显示在迁移/库存阶段收到 runner shutdown signal 后被取消；不能把取消写成 PG16 通过，也不能归因于业务代码。另一个 static shard 无步骤日志，保持未知。
- 当前未提交工作树已包含上述夹具/manifest/source-expectation 对齐。聚焦复核命令 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_return_receiving_h5_contract.py::test_h5_return_fixtures_match_current_backend_and_original_hashes backend/tests/test_stock_return_inbound_migration.py::test_0106_current_manifest_acl_and_postgresql_function_syntax backend/tests/test_database_security.py::test_0052_opening_terminal_internal_function_manifest_is_exact backend/tests/test_database_security.py::test_0052_opening_terminal_startup_rejects_function_security_body_and_acl_drift backend/tests/test_database_security.py::test_0046_material_request_guard_catalog_accepts_exact_manifest backend/tests/test_stock_loss_derived_return_migration.py::test_current_catalog_exactly_matches_private_proof_and_patches`：**9 passed, 1 warning, exit 0**。
- 该聚焦结果只证明当前工作树修复了已知旧提交失败；未提交改动尚未形成新的远端 SHA，不能把旧 run 或本地聚焦结果写成当前远端 CI 全绿。

## 2026-10-07 PostgreSQL 16 工作流聚合门禁审计

- 只读检查 `.github/workflows/postgresql16-release-gate.yml`：`pg16_runtime`、`pg16_loss`、`pg16_condition`、`static_safety` 四组矩阵均无 `continue-on-error`；最终 `postgresql16-release-gate` 使用 `always()` 收集四组结果，并逐项要求 `success`，没有失败吞掉或条件跳过旁路。
- 聚焦命令 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pg16_workflow_topology.py`：**63 passed, 1 warning, exit 0**。该结果仅证明工作流拓扑和拒绝边界，本机仍未运行 hosted PostgreSQL 16；不把 CI 契约测试升级为 PG16 运行通过。

## 2026-10-07 发布候选旁路静态审计

- 对 `cloud_oam/scripts/pilot_release.py` 与 `pilot_preflight.py` 做只读静态门禁审计，结果 `PILOT_NOGO_STATIC=PASS`。当前候选强制绑定项目/镜像 tag、`trial-mvp` scope、候选外状态目录、输入/源码/配置指纹、迁移与 head 回读、一次性不可变准备回执、80/443 占用检查、KMS pin gate 先于 api/web 启动；未发现 `PILOT_ALLOW_PORT_CUTOVER` 等旁路开关。
- 该证据只能说明发布脚本没有明显静态绕过，不能替代真实 Docker/hosted PostgreSQL 16、KMS、PNVS、目标服务器、浏览器/真机 UAT、部署和回滚；当前版本继续冻结为“试点 MVP，不等同完整 V1”，因此不提交、不部署。

# RSC个人仓开发交接

## 2026-10-07 首发 MVP 链路路由静态复核

- 当前 `formal_material_requests.py` 逐项核对首发链路：审批、最小分配、预留、出库、发运、收货证据、个人仓入账均存在正式路径并调用对应服务；结果 `MVP_FLOW_ROUTE_STATIC=PASS`。
- 该证据只证明当前源码接线没有缺失首发链路节点，不替代数据库事务、真实权限、外部 UAT 或部署回读；后续供给容量和复杂履约恢复仍不在本轮范围。

## 2026-10-07 首发状态轴静态复核

- 当前路由与 `material_request_*` 服务源码逐项核对：审批、分配、预留、出库、发运、物流签收、OAM 收货、个人仓入账、通知和对账均有独立路由/服务或状态字段；结果 `STATE_AXIS_WIRE_STATIC=PASS`。
- 该项只证明首发链路没有把状态轴合并或由需求查询接口越权推进，不替代 PostgreSQL 16 的事务/约束运行证据，也不扩大后续 V1 范围。

## 2026-10-07 技术员后置路由旁路静态复核

- 对当前 `frontend/src/App.tsx` 的库存、盘点、报废、报损、退回、对账、通知和负责人管理等 **14 个**后置路由逐一核对 capability/角色守卫；结果 `TECHNICIAN_POST_ROUTE_STATIC=PASS (14 routes)`。
- 该检查补强直接 URL 旁路边界：技术员仍只保留申请、状态查看、本人收货和本人个人仓入账；不改变后端状态、迁移或完整 V1 后置契约，也不替代 Web/UAT。

## 2026-10-07 公开首页与 /xx 接线静态复核

- 当前 `frontend/src/main.tsx` 根入口直接挂载 `PublicKnowledge`，不挂载登录页；`PublicKnowledge.tsx` 的“星星后台管理”按钮固定指向 `https://rscwz.cn/xx`。
- `warehouse-main.tsx` 使用 `BrowserRouter basename="/xx"`，`vite.config.ts` 将公开构建与 warehouse 构建分别绑定 `/`、`/xx/`；静态断言结果 `PUBLIC_HOME_PRIVATE_WAREHOUSE_STATIC=PASS`。
- 该结果只证明当前源码接线，不证明目标域名实际切流、证书、公开知识目录或真实浏览器 UAT；知识源仍按用户要求暂缓。

## 2026-10-07 云端只读会话当前状态未知

- 使用已授权的浏览器接管入口执行只读 `cua.getState()`，30 秒内超时并触发内核重置；本轮没有重试、登录、读取凭据、执行远端命令或写入服务器。
- 该超时不是 `auth_required` 或 SSH 失败证据，历史目标机/PG16 记录不提升为当前 SHA 通过。云端 UAT、目标机状态和 hosted PostgreSQL 16 继续保持未知/未验收，待可审计会话恢复后再按既有只读预检复核。

## 2026-10-07 发布范围与 SMS-only 静态复核

- 使用当前工作树源码执行静态断言：`TRIAL_MVP_UI_SCOPE` 六项后置能力均为 `false`，申请页实际导入并消费该 scope，后台后置区绑定 `can_read_allocation_options`；结果 `TRIAL_SCOPE_STATIC=PASS`。
- 复核网页/小程序登录页面源码，确认不存在 `loginWithWechat` 和 `password-login` 入口；结果 `SMS_ONLY_CLIENT_STATIC=PASS`。该项只证明源码范围与认证入口一致，不替代浏览器/真机 UAT 或真实短信回执。

## 2026-10-07 PostgreSQL 16 运行时阻塞复核

- 当前工作树执行只读环境检查：`command -v postgres pg_config psql docker podman` 均无输出，`pg_config --version` 和容器运行时版本也不可执行。
- 因此本机不能提供 PostgreSQL 16 hosted/disposable runtime，无法把迁移、运行角色 ACL、并发、连接终止或回滚写成通过；不能用 SQLite、历史隔离库或静态检查替代。该阻塞与代码门禁分开记录，未启动业务容器、未写入外部系统。
- 下一有限里程碑仍是取得可审计 PG16 实例后，用同一当前 SHA 运行既有 PG16 gate；在此之前继续完成本地发布一致性与安全审计，不提交、不部署。

## 2026-10-07 候选配置预检复核（当前 SHA）

- 当前工作树 HEAD 为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`；使用同一树执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pilot_preflight.py -k 'valid_pilot_configuration_passes_without_external_io or rejects_wechat_login_and_invalid_sms_limits or non_mvp_release_scope'`，结果 **3 passed, 66 deselected, exit 0**。
- 该预检只解析合成 Compose，确认 `trial-mvp`、SMS-only、默认凭证链和非 MVP 范围拒绝边界；没有启动容器、连接 PG/KMS/OSS/PNVS 或写入外部系统。
- `bash scripts/verify_repository_safety.sh` 已在同一当前树形成终态：**PASS，3062 candidate files，75,774,155 bytes，exit 0**；受保护本地路径保持忽略，未发现高置信凭证或个人路径泄露。该结果只证明仓库边界安全，不替代 hosted PostgreSQL 16、真实 PNVS/UAT 或部署证据。

## 2026-10-07 Web 完整门禁恢复全绿（权限回收夹具对齐）

- 完整 Web 门禁首次复核为 **2899 passed, 13 skipped, 1 failed**；唯一失败来自权限回收测试夹具仍在 `can_read=false` 时保留写能力，触发了当前正式权限契约的先行拒绝。没有放宽产品门禁。
- 已将夹具改为同时撤销 `can_create`、`can_withdraw`、`can_cancel` 和 `can_read_allocation_options`，准确模拟读取回验权限被撤销；`materialRequestInboundRecovery.test.ts` 聚焦结果 **7 passed, 0 failed**。
- 完整 Web 门禁复跑结果 **2900 passed, 13 skipped, 0 failed**（174 files）；跳过项仍仅为冻结试点之外的后续 V1 页面行为。未连接 hosted PostgreSQL 16、PNVS/RAM、生产服务或外部渠道，未产生外部写入。

## 2026-10-07 小程序完整当前门禁恢复全绿

- 先前完整小程序门禁的 3 个失败均来自测试主动调用已退役微信认证路径；已改为 SMS-only 的等价幂等/显式会话哨兵/未知结果恢复断言。当前命令 `node --test tests/*.test.js`：**1086 passed, 0 failed**。
- 该结果包含客户端 CI 结构契约（65 项）、SMS-only 页面与底层认证边界，确认没有因为测试修正重新开放微信登录；当前 `production-guard` 仍阻断退役原型路径。
- 本轮仍未触碰 PG16、PNVS/RAM、服务器、外部渠道或生产写入；完整远端 CI、真实短信回执、UAT、部署/回滚仍需独立证据。

## 2026-10-07 客户端 CI 结构契约同步

- 客户端工作流已包含试点 artifact 与正式公共目录条件步骤，旧小程序契约测试仍按 12 步白名单核对，导致工作流实际 14 步时出现 2 个测试失败；已同步白名单，保留 checkout、只读权限、固定 Node/pnpm、仓库安全、Web/小程序测试、试点和 release 目录门禁的逐步校验。
- `node --test tests/client-release-gate-contract.test.js`：**65 passed, 0 failed**；该修正只同步测试与当前工作流，不放宽权限或跳过任何 CI 步骤。
- 未连接数据库、PNVS 或生产服务；完整 CI 仍须在远端 runner 执行，hosted PostgreSQL 16、真实 PNVS、UAT、部署和回滚仍未通过。

## 2026-10-07 小程序 SMS-only 页面门禁修正

- 发现 `formal-auth-pages.test.js` 仍调用已退役的 `loginWithWechat()`，导致小程序登录页测试只因测试遗留失败；当前登录页本身已移除微信入口并只保留 SMS 登录。
- 已将断言改为验证短信登录的显式会话屏障、只发送 `/auth/miniprogram/sms-login`，且微信方法不存在。聚焦命令 `node --test tests/formal-auth-pages.test.js tests/sms-only-policy.test.js`：**11 passed, 0 failed**。
- 这是测试与 SMS-only 首发策略对齐，不开启微信渠道、不改变后端退役路由或真实短信状态；PNVS/RAM、hosted PostgreSQL 16、正式 UAT 和部署仍未通过。

## 2026-10-07 试点前端全页门禁与后续 V1 测试隔离

- 用工作区 bundled Node 对 `frontend/src/pages/FormalMaterialRequests.test.tsx` 做了当前树复核：**64 passed, 13 skipped**。13 个跳过项全部是冻结试点 MVP 之外的供给计划、复杂恢复或关闭/剩余取消页面行为；底层服务、HTTP 契约和状态事实没有删除，继续由各自聚焦测试覆盖。
- 试点范围聚焦复核：角色隐藏、本人收货/个人仓入账入口与 capability 投影 **4 passed, 95 skipped**；本人收货/入账组件和契约文件同轮通过。此前一次未带范围过滤的旧页测试暴露了上述 13 个超范围断言，已明确标记为后续完整 V1 测试，避免把故意隐藏的功能误报为当前版本回归。
- 本轮未连接 hosted PostgreSQL 16、PNVS/RAM、生产服务或外部渠道；没有库存、审计、通知或迁移写入。该门禁只证明试点前端范围可测试，不等同完整 V1 或上线通过。

## 2026-10-07 PNVS 默认凭证链收口（本地代码，不等同真实短信上线）

- `Settings.sms_credential_mode` 默认固定为 `default_chain`；PNVS/Dypnsapi 适配器通过 `alibabacloud_credentials.Client()` 注入阿里云 SDK 的默认凭证链，由目标 ECS 的 RAM 角色/实例元数据解析凭证，不把 AccessKey ID、Secret 或 STS 值放入 OpenAPI `Config`。
- `static`/`sts` 仅保留为隔离测试和迁移兼容模式；生产 `validate_api_startup()` 明确拒绝，`pilot_preflight.py` 要求 `default_chain` 且四个静态/STS字段为空。Compose、`.env.example` 和 bootstrap 默认写入 `OAM_SMS_CREDENTIAL_MODE=default_chain`。
- 新增聚焦证据：`test_sms_provider.py` **38 passed**（含默认链对象注入、构造失败关闭且不把凭证字段传给 SDK），`test_pilot_preflight.py` **69 passed**，`test_startup_security_boundary.py` **31 passed**；均为本地合成配置/客户端，不访问 IMDS、PNVS、RAM、数据库或生产会话。
- 这只证明默认凭证链代码和 fail-closed 门禁，不证明目标轻量服务器具备 ECS RAM 角色、PNVS 最小权限、来源限制、真实隔离号码回执、登录回读或短信上线；真实渠道仍关闭，hosted PostgreSQL 16、部署和 UAT 阻塞保持不变。

## 2026-10-07 MVP 读/本人写入口异常边界前置

- 路由审计发现需求列表、需求详情、可编辑草稿以及本人收货/个人仓入账写入口的部分异常边界原先在服务或请求头校验之后；现已统一前置 `no-store`，并让服务错误、数据库错误、联系人保护错误和响应投影错误都返回私有响应头。
- 受影响 HTTP 聚焦回归 **5 passed, 54 deselected, 1 warning**；全 HTTP 路由顺序门禁 **1 passed, 6 deselected**，核心 POST/全路由源码门禁组合 **3 passed, 4 deselected**；Python 编译和 `git diff --check` 通过。
- 该项只加强试点 MVP 的状态查看、本人收货/入账和申请隐私边界，不改变业务状态轴或角色范围；未连接 hosted PostgreSQL 16、PNVS 或生产服务，未重跑已终态测试。

## 2026-10-07 正式需求路由全方法私有响应门禁

- 路由源码门禁现在覆盖正式物资需求路由的全部 **73 个** HTTP 函数（GET/POST/PUT/PATCH/DELETE），每个函数都必须在服务调用和输入校验边界前建立 `_set_read_no_store`；当前 AST 结果为 `73/73`。
- 聚焦回归 **3 passed, 4 deselected**，Python 编译和 `git diff --check` 通过；这是本地源码证据，不替代 hosted PostgreSQL 16、真实 PNVS、部署或 UAT，也未重跑已终态测试。
- 该门禁只防止试点 MVP 的申请、状态、本人收货/入账及后台履约响应被缓存或漏出，不改变角色 capability、状态轴或后续迭代范围。

## 2026-10-07 试点产物后置区 capability 门禁

- `frontend/build/verify-pilot-release.mjs` 现在同时核对申请页的 `can_read_allocation_options` capability 绑定，并要求供给、占用、出库、发运、后台收货/入账、拣货和履约准备面板都由该 capability 门控；本人收货/个人仓入账面板继续作为技术员例外保留。
- 使用工作区 Node 运行时执行 `node build/verify-pilot-release.mjs`，结果 `exit 0`，公开目录仍为 `pending/0`，私有产物 `/xx/` 和知识目录一致；新增 Python 聚焦回归 **2 passed, 3 deselected**，Node 语法检查、Python 编译和 `git diff --check` 通过。
- 这是试点 MVP 产物范围防回归，不代表真实登录、PNVS、hosted PostgreSQL 16、部署或 UAT；未产生外部写入，未重跑已终态测试。

## 2026-10-07 SMS-only 认证前缀私有响应边界

- 主应用 middleware 现在对整个 `/api/auth` 前缀统一附加 `private, no-store, max-age=0`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`；生产环境退役认证路径的 410 早返回也带同一组头，避免旧登录渠道错误被缓存。
- 主 middleware 早返回/框架错误聚焦回归 **3 passed, 55 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。未访问 PNVS、数据库或生产会话，真实短信发送/核验仍未宣称完成。

## 2026-10-07 MVP 申请/审批写接口私有响应边界

- 申请草稿创建、草稿替换、提交、撤回、取消、内部审批和外部审批证据登记/复核共 **8 个**核心写路由，现在在请求头校验和业务服务前统一设置 `Cache-Control: no-store, max-age=0`、`Pragma: no-cache`、`Referrer-Policy: no-referrer`；主应用 middleware 同时覆盖整个 `/api/v1/material-requests` 路径，框架级错误也不会落入公开缓存。
- 源码 capability 门禁 **3 passed, 3 deselected**（包含当前 26 个 POST 路由全部设置私有边界）；核心 HTTP 映射与主 middleware 回归 **6 passed, 50 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。未连接 hosted PostgreSQL 16、PNVS 或生产服务。
- 该项只收口试点 MVP 的申请→审批响应隐私边界，不扩大后置履约能力，不重跑已终态测试；PG16 hosted DB、真实短信和正式 UAT 仍保持独立阻塞。

## 2026-10-07 未登录私有入口响应头门禁

- 部署 smoke 现在读取 `/api/auth/me` 的 401 响应头，要求未登录私有入口同时返回 `Cache-Control: private, no-store, max-age=0`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`；缺失任一项即失败，防止身份结果被缓存或通过来源头泄露。
- 受影响正向/负向聚焦结果 **3 passed, 12 deselected**；`sh -n scripts/smoke_test.sh`、Python 编译和 `git diff --check` 通过。该证据是本地合成 HTTP smoke，不代表部署、真实短信或 hosted PostgreSQL 16 通过。

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

- `/api/auth/login-options` 现在返回短信/退役渠道能力时统一设置 `no-store`、`Pragma` 和 `Referrer-Policy`，避免代理或客户端缓存过期的认证开关；短信 readiness 判定和返回字段未改变。
- `test_sms_only_policy.py -k 'login_options or retired_password_and_wechat_routes or change_password'` **5 passed, 3 deselected, 1 warning**；未访问 PNVS、数据库或生产会话。
- 该项只加强 SMS-only 配置可见性的缓存边界，不代表真实短信方案或登录回读已通过，未重跑终态认证门禁。

## 2026-10-07 SMS-only 退役入口私有错误头

- `/auth/login`、小程序密码/微信登录、改密和历史管理员临时密码入口继续在访问用户或 provider 前返回 `404 登录方式不可用`，现在统一附带 `no-store`、`Pragma` 和 `Referrer-Policy`，避免旧渠道错误被缓存。
- `test_sms_only_policy.py -k 'retired_password_and_wechat_routes or change_password'` **4 passed, 4 deselected, 1 warning**；未访问 PNVS、数据库或生产会话。
- 这只是短信-only 关闭边界的隐私/缓存收口，不代表真实短信方案、RAM、来源限制、隔离号码回执或登录回读已完成；未重跑已终态认证门禁。

## 2026-10-07 需求概览 API capability 前置收口

- `/api/v1/reports/material-requests` 现在在进入概览查询服务前要求 `admin` 或 `provincial_manager`；技术员直达请求返回 `403 report_forbidden`、私有缓存头，服务不会被调用。后台角色的区域范围、权限变化回读和只读事务释放保持不变。
- `test_material_request_overview_http.py` 受影响聚焦回归 **4 passed, 7 deselected, 1 warning**；未连接 hosted PostgreSQL 16、短信或生产环境。
- 概览仍属于后置报表能力，技术员首发界面继续隐藏；该项不扩大试点 MVP、不删除底层状态或报表服务，未重跑已终态测试。

## 2026-10-07 物资需求验证异常统一私有化

- 统一物资需求路径的 `RequestValidationError` 现在对默认 422 响应设置 `no-store`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`；申请/提交的非法输入仍只返回脱敏错误，不回显联系人明文。
- `test_formal_material_request_api.py` 与源码 capability 门禁聚焦回归 **5 passed, 54 deselected, 1 warning**；没有数据库、短信或生产写入。
- 该项只加强申请入口的错误缓存/隐私边界，不改变试点 MVP 状态轴、登录策略或后续迭代范围；未重跑已终态测试。

## 2026-10-07 命令状态统一 no-store 源码门禁

- 新增源码门禁覆盖当前 **19 个**命令状态/恢复接口：每个接口必须先设置 `_set_read_no_store`，带安全请求头的接口必须在 `_required_safe_header` 前建立私有缓存边界；供给查询参数仍由精确验证异常处理器收口。
- `test_formal_material_request_route_capability.py` 与命令状态 HTTP 聚焦组 **14 passed, 1 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。
- 该项只防止只读恢复接口新增缓存/错误边界回归，不扩大试点 MVP 或后续履约范围；PG16 hosted DB、PNVS 真实回执和正式 UAT 仍未通过。

## 2026-10-07 供给状态查询参数私有校验

- `material-request-supply-command-status` 不再让 FastAPI 的默认查询参数异常直接出站；缺失或非法 `trace_request_id` 由该精确路径的统一验证异常处理器收口，保持原有必填/422 语义、脱敏错误体和 `no-store`，合法请求继续执行后台 capability 与只读恢复服务。
- 新增负向回归后，命令状态/权限聚焦组 **13 passed, 1 deselected, 1 warning**；未连接 hosted PostgreSQL 16、PNVS 或生产环境。
- 该项只补齐供给状态恢复的输入边界，不扩大供给计划或后续履约范围；PG16 hosted DB 仍为外部阻塞，未重跑已终态测试。

## 2026-10-07 命令状态请求头校验统一私有化

- 分配、占用、释放、拣货、出库、发运、物流、后台收货，以及技术员本人收货/入账恢复接口均在安全请求头校验前设置 `no-store`，并让非法请求头直接返回私有 400；成功、业务错误和数据库错误继续保持同一缓存边界。
- 受影响聚焦组 **14 passed, 11 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。没有连接外部数据库、短信或生产环境。
- 该项只是只读命令恢复的缓存/错误边界统一，不改变试点 MVP 状态轴、技术员可见范围或后台履约 capability；未重跑已终态测试。

## 2026-10-07 供给命令状态恢复异常路径收口

- 后台供给命令状态查询现在在成功、供给历史服务异常、数据库异常和响应投影异常上统一保持 `no-store`；数据库异常显式回滚后再返回脱敏的 503，不提交事务。
- `test_material_request_command_status_api.py` 与后置 capability 门禁扩展聚焦回归 **11 passed, 1 deselected, 1 warning**；供给状态查询仍要求后台履约角色，未改变技术员状态查看例外。
- 该项只收紧只读恢复接口的缓存与事务边界，不扩展供给计划、后续容量或复杂恢复；PG16 hosted DB、真实 PNVS 和正式 UAT 仍未通过，未重跑已终态测试。

## 2026-10-07 生命周期状态查询错误路径 no-store 收口

- 技术员保留的生命周期命令状态查询现在在成功、非法 `X-Request-ID`、业务服务异常和数据库异常四类路径统一保持 `Cache-Control: no-store, max-age=0`、`Pragma: no-cache` 和 `Referrer-Policy: no-referrer`；数据库异常仍只回滚，不提交。
- `test_material_request_command_status_api.py` 与 capability 源码门禁聚焦回归 **9 passed, 1 deselected, 1 warning**；未连接 hosted PostgreSQL 16、短信或外部系统。
- 该项只加强试点 MVP 的只读状态查看安全边界，不改变技术员可见范围、后台命令恢复限制或底层状态轴；未重跑已终态测试。

## 2026-10-07 技术员状态查看例外显式固化

- 生命周期命令状态接口继续作为试点 MVP 的技术员状态查看入口；技术员可以恢复申请/审批状态，但不能恢复分配、占用或供给命令结果。新增源码门禁明确该接口不得出现 `_require_backend_fulfillment`，并必须保留 `no-store`。
- `test_formal_material_request_route_capability.py` 与 `test_material_request_command_status_api.py` 聚焦回归 **6 passed, 1 deselected, 1 warning**；新增运行时回归以 `technician` 角色确认生命周期状态查询 200、`no-store`、无提交/回滚。
- 该项只固化“状态查看”与“后台履约命令恢复”的 capability 分界，不扩大试点 MVP 链路或后续迭代；PG16 hosted DB 仍是外部阻塞，未重跑已终态测试。

## 2026-10-07 源侧命令恢复 capability 前置收口

- 发现分配/占用命令恢复路由此前仅依赖普通需求读取权限；现对 `material-request-allocation-command-status` 和 `material-request-reservation-command-status` 在请求头解析、数据库读取和业务服务前统一执行 `_require_backend_fulfillment`。生命周期命令状态仍保留给技术员用于申请/审批状态恢复。
- `test_formal_material_request_route_capability.py` 现覆盖 **24 条**后台后置路由；新增 HTTP 负向回归确认技术员得到 `403 fulfillment_forbidden`、`no-store`，且服务不会被调用。与状态轴恢复契约合并聚焦结果 **7 passed, 8 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。
- 该修复只收紧试点 MVP 的源侧后台 capability 边界，不删除分配/占用事实、状态轴、命令恢复或幂等审计；PG16 hosted DB、真实 PNVS 和正式 UAT 仍未通过，也没有重跑已终态测试。

## 2026-10-07 试点命令恢复状态轴独立性契约

- 补充 `backend/tests/test_material_request_allocation_options.py` 的 HTTP 恢复回归：单次分配/占用事实分别保持 `allocated`/`reserved`，同时完整透传聚合轴的 `partially_allocated`/`pending`，不会把事实状态误写成聚合状态；响应继续 `no-store` 且不触发提交或回滚。
- 聚焦证据：`-k 'command_status_keeps_fact_status_separate or router_allocation_command_status_is_read_only_and_no_store'` **2 passed, 8 deselected, 1 warning**；Python 编译和 `git diff --check` 通过。该证据只覆盖 HTTP wire contract，没有连接 hosted PostgreSQL 16、短信、外部知识源或生产环境，也没有重跑已终态测试。
- 该契约服务于当前冻结的“试点 MVP，不等同完整 V1”链路；不扩大履约后供给容量、0178 分配后建计划、释放后重分配或复杂历史恢复，PG16 hosted DB 门禁仍作为外部阻塞记录。

## 2026-10-07 MVP 状态轴闭环契约

- 新增 `backend/tests/test_pilot_mvp_state_axes_contract.py`，用纯 schema contract 固化最小序列：审批通过 → 分配 → 占用 → 出库/发运 → 本人收货 → 个人仓入账。
- **3 passed**。测试明确发运命令允许绑定推进 `outbound_status`、`shipment_status` 和 `personal_inbound_status=pending_acceptance`，但不会替代分配/占用；入账到 `posted` 时通知和 OAM 收货轴仍可保持独立状态。缺失轴或未审计值会被拒绝。
- 这只是当前 wire contract 的本地证据，不替代各服务行为、HTTP、PG16 hosted DB 或真实 UAT；没有外部写入，也没有重跑终态门禁。

## 2026-10-07 试点 smoke 绑定运行时范围

- `scripts/smoke_test.sh` 支持可选的 `SMOKE_EXPECTED_RELEASE_SCOPE`；`pilot_release.py` 在候选启动阶段固定注入 `trial-mvp`，smoke 会把 `/api/health.release_scope` 与该值精确比较。普通调用未设置该变量时仍可核对正式默认范围，不改变既有 smoke 语义。
- `tests/test_pilot_smoke_public_entry.py` **11 passed**，新增运行时范围漂移拒绝；`test_pilot_release_binding.py -k candidate_release_environment_pins_trial_mvp_scope` **1 passed, 65 deselected**。`bash -n`、Python 编译和 `git diff --check` 通过。
- 该门禁只增强部署候选的范围可观测性，不代表容器、迁移、PNVS、业务 UAT 或 hosted PostgreSQL 16 已通过；当前版本继续是“试点 MVP，不等同完整 V1”。

## 2026-10-07 试点范围运行时健康投影

- `Settings` 现在显式解析 `OAM_RELEASE_SCOPE`（`production-v1` 或 `trial-mvp`），`/api/health`、`/api/health/live`、`/api/health/ready` 的非敏感响应都会回显 `release_scope`。候选包装器注入 `trial-mvp` 后，部署监控可以直接确认运行中的范围没有漂移；默认 Compose 仍为 `production-v1`。
- `backend/tests/test_health_readiness.py` **7 passed**，覆盖数据库/KMS 失败脱敏、健康探针缓存、试点范围回显和未知范围拒绝；没有连接 hosted DB、KMS、短信或生产环境，也没有写入业务状态。
- 该字段只是持续运行的范围证据，不把健康探针通过等同迁移、业务 UAT、PNVS 真实回执或上线；当前版本仍是“试点 MVP，不等同完整 V1”。

## 2026-10-07 PNVS 运行时请求边界复核（合成 provider）

- 对 `backend/app/sms.py` 的真实 PNVS 适配器补齐了聚焦单元证据：发送与核验均固定中国区 `86`，原样绑定签名/模板/方案和 `out_id`，关闭 SDK/provider 自动重试，并要求 `max_attempts=1`、连接超时 5 秒、读取超时 8 秒。
- `backend/tests/test_sms_provider.py` **34 passed**。测试只使用合成客户端，不访问 PNVS、数据库、KMS、OSS 或生产会话；异常仍不泄露手机号、验证码或 SDK 异常因果链。
- 该证据只证明代码请求边界和未知结果防重放约束；不证明真实 RAM 最小权限、来源限制、隔离测试号码、发送回执或登录回读。短信真实渠道与投递运维继续列为后续迭代，当前版本仍为“试点 MVP，不等同完整 V1”。

## 2026-10-07 当前树复核：试点 MVP 冻结保持有效

- 本轮重新完整读取正式基线、当前交接和正式验收审计；工作树仍为 `codex/notification-delivery-worker` / `fc7c926`，未 reset、revert、丢弃或提交任何改动。该次记录的未提交差异为 761 个文件；后续新增门禁、测试、默认凭证链和交接说明后，当前 `git status --short` 为 772 条，`git diff --check` 通过。
- 冻结范围仍是“申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账”。技术员只保留申请、状态、本人收货和本人入账；后台后置区继续由 capability/角色控制。既有终态测试本轮没有重跑。
- 这次复核没有扩大后续供给容量、0178 分配后建计划、释放后重分配或复杂历史恢复；拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维仍保持后续迭代清单。
- PostgreSQL 16 hosted DB 门禁仍是外部阻塞；没有新的可审计 hosted 运行时，不把迁移、ACL、并发或连接终止写成通过。当前版本是“试点 MVP，不等同完整 V1”。

## 2026-10-07 有限里程碑验收矩阵：申请到个人仓入账

本版本冻结为可控试点，验收只沿着下面一条最小业务链推进；每一格表示独立事实，不能由相邻状态推断：

| 步骤 | 试点允许的事实 | 首发角色/入口 | 明确不在本里程碑内 |
|---|---|---|---|
| 申请/提交 | 工程师创建草稿、提交申请、保存版本和幂等坐标 | 技术员本人；PC/H5 需求提报 | OAM 写回、公共首页登录、知识源导入 |
| 审批 | 区域、蔚来总部和外部登记/复核的审批事实逐行留存 | 区域/总部审批 capability；申请人仅查看 | 把审批通过当作库存占用或供给完成 |
| 最小分配/占用 | 后台选定来源、记录分配和占用，数量/SN/账本游标可回读 | 区域/总部后台人工履约 | 履约后供给容量、0178 分配后建计划、释放后重分配 |
| 人工履约/发运 | 后台按已有出库依赖登记发运和运单事实 | 区域/总部后台；拣货 UI 继续隐藏 | 自动物流、复杂供给计划、真实渠道投递 |
| 本人收货 | 本人按包裹、版本、SN/批次和保管责任确认收货，可分批 | 技术员本人收货入口 | 拒收/退回补偿、stock-return 全链 |
| 个人仓入账 | 接受实物独立生成不可变入账流水，结果精确回读 | 技术员本人入账入口 | 工单消耗/替换/冲销、旧坏件回收 |

技术员的导航和需求详情只保留申请、状态查看、本人收货、本人入账；区域/总部保留完成上述后台分配、占用和发运所需的入口。底层迁移、状态契约、库存流水、幂等、审计、权限/安全守卫、取消/关闭守卫和最小站内通知全部保留，隐藏 UI 不等于删除能力。

后续迭代清单固定为：报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维，以及拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收。该清单不得作为本次试点的上线证据。

本矩阵是范围和验收口径，不是新的测试通过声明；本轮不重跑已终态测试。PG16 hosted DB 仍缺同一源码集的可审计运行证据，继续作为外部阻塞。

## 2026-10-07 试点短信预检坐标锁定

- `scripts/pilot_preflight.py` 现在除检查短信-only、PNVS/Dypnsapi provider、凭证形态和登录限流外，还要求与已审计的非敏感 PNVS 坐标完全一致：签名 `恒创联众`、模板 `100001`、方案名 `RSC个人仓登录`。这只防止候选配置漂移，不证明 RAM、来源限制、真实回执或隔离号码登录回读已完成。
- `.env.example` 已把同一组非敏感坐标写成部署注释，但仍保持短信关闭、签名/模板值为空；不会把示例文件误当成可上线凭据或 provider 成功证据。
- `.env.example` 现在显式保留 `OAM_RELEASE_SCOPE=production-v1`；只有隔离候选脚本在运行时注入 `trial-mvp`，避免默认部署文件把试点范围带入正式 Compose。
- 聚焦证据：`backend/tests/test_pilot_preflight.py -k 'valid_pilot_configuration_passes_without_external_io or pnvs_sts or rejects_unreviewed_sms_coordinates'` **6 passed, 61 deselected**。测试只解析合成 Compose，不连接短信 provider、数据库或 KMS；未重跑已终态认证/业务门禁。
- PNVS 真实发送、`out_id` 精确回读、验证码核验/登录回读、未知结果禁止重放仍列为外部验收项；完成前不打开生产短信流量。PG16 hosted DB 继续作为外部阻塞记录。

## 2026-10-07 正式物资需求后置路由 capability 审计

- 对 `backend/app/routers/formal_material_requests.py` 做 AST 级只读审计，覆盖货源/占用、释放/取消/关闭、拒收/补偿、履约准备、拣货/出库、发运/物流、OAM 收货证据、后台收货和后台入账共 **24 条后置路由**；每条在调用业务服务前都执行 `_require_backend_fulfillment`，并保留 `no-store`/安全错误映射。
- 新增 `backend/tests/test_formal_material_request_route_capability.py`，将该边界固化为 **2 passed** 的源码门禁：24 条后置路由必须前置守卫，9 条技术员本人收货/入账路径必须保持显式例外。
- 技术员专用 `my-receiving`、`my-receipts`、`my-inbounds` 及需求状态投影没有被这条后台守卫误伤；其本人归属、版本、包裹/出库/SN/保管责任核验仍由独立服务执行。该审计没有改代码、写数据库或重跑终态测试。
- 现有 HTTP 聚焦回归继续作为行为证据：后置路径前置拒绝、发运目标、物流、后台收货/入账和 OAM 证据组均已记录在本交接顶部；本轮只补当前源码的路由级覆盖核对。

## 2026-10-07 客户端 capability 门禁与试点产物复核

- `backend/tests/test_client_release_workflow_scope.py` 当前 **3 passed**：锁定试点分支公共/私有入口检查、正式分支完整目录条件，以及 Web 全测、小程序全测和 `/xx` 私有构建步骤持续存在于客户端 CI。
- `pnpm run verify:pilot-release` 当前通过，明确输出 `scope=private pilot artifact only`、`publicCatalogIsReady=false`、`catalog.status=pending`、`records=0`；未把公共知识目录或完整认证/外部验收误报为就绪。
- 该复核没有运行严格发布门禁、没有连接数据库或短信 provider，也没有提交或部署；PG16 hosted DB、真实 PNVS 回执、公共目录和正式 UAT 仍独立阻塞。

## 2026-10-07 公共首页后台入口目标纳入部署 smoke

- 发现只读部署 smoke 之前只校验公共包出现“星星后台管理”文字，没有证明产物中的目标仍是个人仓入口；现将 `https://rscwz.cn/xx` 加入公共 bundle 必需字符串，避免文字存在但跳转目标漂移。
- `backend/tests/test_pilot_smoke_public_entry.py` 新增目标缺失负向回归；在当前改动范围内执行 **10 passed**。测试仍使用本地只读 HTTP 夹具，没有连接生产、发送短信或写入业务数据。
- 静态产物门禁 `verify_public_entry.mjs` 已同步要求同一精确目标并通过：`status=pending`、`records=0` 仍按试点范围如实保留；无参数检查不替代 `--release` 公共目录门禁。
- 该项补强首页→`/xx` 的部署前验收，不改变试点 MVP 的业务链路、技术员 capability 隐藏、底层状态或 PG16 hosted DB 外部阻塞；本轮不重跑已终态全套测试。

## 2026-10-07 重新生成当前源码的私有 warehouse 产物

- 复核发现旧 `dist-warehouse` 仍来自 capability 守卫修复前的构建，旧 bundle 的省负责人入口没有 `technicianOnly` 硬门槛；没有把旧产物当成当前可部署证据。
- 已使用当前工作树和 bundled Node 重新执行 `pnpm run build:warehouse`：TypeScript 检查通过，Vite 转换 **2002 modules**；新 bundle 同时在导航和 `/provincial-managers` 路由保留 `!technicianOnly` 守卫。
- `verify_public_entry.mjs` 在新产物上通过；本项只刷新当前源码对应的静态产物，不重跑已终态业务测试、不连接外部系统、不提交或部署。

## 2026-10-07 技术员误授角色管理权限的前端守卫

- 发现 `role_assignment/manage_provincial` 单独存在时，技术员可能看到“省负责人”入口；现将导航和 `/provincial-managers` 路由统一加上 `!technicianOnly`，即使误授该权限也不会暴露角色管理页面。
- 新增误授权限回归并运行：`App.test.tsx` **44 passed**，覆盖技术员直达后置路由和仅允许“首页/需求提报”的可见导航集合；TypeScript 检查通过。
- 该修复只收紧首发 MVP 的 capability/角色边界，不改变后台区域/总部的角色管理能力，也未改变底层状态、迁移、审计或权限事实。

## 2026-10-07 技术员整站 capability 收口

- 技术员-only（无 `admin`/`provincial_manager`）的首发界面现在只保留首页状态投影、需求提报/查看，以及需求详情内的本人收货和本人入账；库存、需求总览、通知、盘点/期初盘点、报损/报废、退回、对账等后置导航不再显示，直接访问这些路由会回到首页。新增正向断言确保可见导航集合只有“首页”和“需求提报”。
- `scrapRecoveryStages` 不再向技术员开放；损失提交、发件、退回、库存和通知等 capability 同步收紧到区域/总部后台角色。底层 API、状态轴、迁移、出库依赖、不可变库存流水、幂等、审计、权限/安全约束、取消/关闭守卫和最小站内通知均保留，未因前端隐藏而删除或改写。
- 当前源码证据：前端 `tsc -b` 通过；`pnpm run build:warehouse` 通过（Vite 转换 2002 modules）；`App.test.tsx`、`App.lossSubmission.test.tsx`、`App.lossSending.test.tsx`、`App.scrapRecovery.test.tsx`、`App.returnCondition.test.tsx` 聚焦回归 **71 passed**，其中新增技术员直达后置路由守卫 **20 passed**；最新仓库安全门禁 **3060 candidate files / 75702215 bytes，PASS**。本轮未重跑已终态测试。
- 该收口只冻结试点 MVP 首发可见范围，不扩大后续供给容量、0178 分配后建计划、释放后重分配或复杂历史恢复；拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维继续列为后续迭代。PG16 hosted DB 仍是外部阻塞。

## 2026-10-07 小程序公开包边界复核

- 当前小程序 `app.json` 只注册 `pages/knowledge/index`；私有登录、个人仓、扫码、需求、收货/入账、盘点、通知和退回等源码仍保留在仓库，但由 `project.config.json` 的 `packOptions.ignore` 排除，不进入个人备案首页公开包。
- 公开知识页只读取本地目录，未调用登录、会话或业务 API；目录仍为 `pending/0` 时显示“资料待更新”。该边界符合首页公开查询、个人仓登录放在 `https://rscwz.cn/xx/` 的现行要求。
- `miniprogram/tests/public-knowledge.test.js` 的公开包隔离断言已复核；此项不替代真实小程序开发者工具/真机 UAT，也不改变公共知识源和严格发布门禁的 pending 阻塞。

## 2026-10-07 发运目标 HTTP 权限契约补强

- 窄回归发现并修复 `ShipmentError` 缺少路由统一需要的 HTTP 状态码与安全 detail 映射；技术员访问只读 `shipment-targets` 时此前会在异常转换处落成 500，现按 `fulfillment_forbidden` 返回 403，并保留 `no-store` 响应头。
- 变更只补齐错误契约，不写库存、发运、审计或通知事实；后台 `admin/provincial_manager` 的候选解析与前端下拉选择不变。
- 聚焦证据：`PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_material_request_my_receiving.py -k 'shipment_targets or technician_cannot_read_backend_shipment_targets'` **8 passed, 28 deselected, 1 warning**；覆盖服务层、后台 HTTP 只读/no-store/405、技术员服务层拒绝和技术员 HTTP 403 无突变。
- 该修复仍属于试点 MVP 的后台人工发运可操作性与 RBAC 收口，不扩大完整 V1；PG16 hosted DB、PNVS 真实回执、部署和正式 UAT 仍是独立门禁。

## 2026-10-07 发运后端读路径 RBAC 收口

- 发运候选、发运历史和发运命令恢复现在统一要求 `admin/provincial_manager`；服务层和 HTTP 层同时守卫，技术员即使保有普通需求/库存读取权限，也不能绕过前端直接读取后台履约坐标或恢复结果。
- 技术员权限失败在密钥检查之前返回 `403 fulfillment_forbidden`，列表/候选/恢复响应继续带 `no-store`；后台账号的发运状态、幂等恢复和既有只读契约不变。
- 受影响证据：`test_material_request_my_receiving.py -k 'shipment_targets or technician_cannot_read_backend_shipment_paths'` **14 passed, 28 deselected, 1 warning**；服务层既有 `test_material_request_shipment.py` **6 passed, 1 warning**。没有库存、审计、通知或迁移写入。
- 本里程碑仍只收口试点 MVP 的后台履约权限边界，不扩大后续迭代清单；PG16 hosted DB、PNVS、部署和正式 UAT 仍未通过。

## 2026-10-07 物流事件读路径 RBAC 收口

- 物流事件列表和命令恢复现在统一要求 `admin/provincial_manager`；技术员直连后端路径在密钥检查前返回 `403 fulfillment_forbidden`，失败响应带 `no-store`。物流事件仍是独立只读状态轴，不会改变收货或个人仓入账。
- `LogisticsEventError` 已补齐 HTTP 状态码与安全 detail，旧的只读契约测试轻量 principal 继续兼容。
- 聚焦证据：技术员物流路径回归 **4 passed, 42 deselected, 1 warning**；`test_material_request_logistics_contract.py` **4 passed, 1 warning**。无外部写入和数据库迁移。

## 2026-10-07 后台收货/入账读路径 RBAC 收口

- 后台收货列表、收货命令恢复和后台入账列表统一限制为 `admin/provincial_manager`；技术员继续使用独立“本人收货/本人入账”接口，后台路径不因前端隐藏而留下直连旁路。
- 收货服务错误现在提供稳定 HTTP 映射；入账服务沿用库存错误契约。技术员权限失败在密钥读取前返回 403 并带 `no-store`，不产生版本、库存、审计或通知变化。
- 聚焦证据：技术员后台收货/入账路径 **6 passed, 46 deselected, 1 warning**；受影响收货恢复 **16 passed, 1 warning**；入账过账 **29 passed, 1 warning**。未重跑全套终态门禁。

## 2026-10-07 OAM 收货证据读路径 RBAC 收口

- `GET /api/v1/material-requests/{id}/oam-receipt-evidence` 现在同样限制为 `admin/provincial_manager`；技术员继续只使用本人收货/入账接口，OAM 外部证据不会通过隐藏页面之外的直连路径泄露。
- `OamReceiptEvidenceError` 已补齐稳定 HTTP 状态和安全 detail 映射；技术员拒绝返回 403/no-store，投影读取仍是只读，不改变本地收货、入账或通知状态。
- 聚焦证据：技术员 OAM 证据路径 **1 passed, 52 deselected, 1 warning**；既有 OAM 只读 HTTP 契约 **1 passed, 1 warning**。没有迁移、库存或外部 OAM 写入。

## 2026-10-07 后置履约源侧 HTTP 旁路收口

- 复核发现技术员可在前端隐藏之外尝试读取部分源侧坐标：履约准备、释放/拣货/出库候选、源侧剩余履约、供给容量及对应命令恢复。路由现在统一要求 `admin/provincial_manager`；技术员在密钥、数据库和业务查询前得到 `403 fulfillment_forbidden` 与 `no-store`。
- 该收口只限制源侧后台人工履约边界，不删除分配、占用、拣货、出库、释放、供给容量或历史命令事实；本人收货/个人仓入账接口不受影响。
- 聚焦证据：技术员源侧候选/恢复及取消、关闭、拒收退回、退回补偿路径 **38 passed, 54 deselected, 1 warning**；释放、拣货、出库及供给容量现有 HTTP 契约 **13 passed, 102 deselected, 1 warning**；相关取消/关闭/拒收退回/退回补偿 HTTP 契约 **53 passed, 1 warning**。未产生库存、审计、通知、迁移或外部写入。

## 2026-10-07 本人收货/个人仓入账契约复核

- 试点 MVP 的技术员路径继续只使用 `my-receiving`、`my-receiving/{shipment_id}/candidates`、`my-receipts` 和 `my-inbounds`；服务端以当前本人、需求版本、包裹目标、保管责任、出库/SN/批次证据和权限版本重新核验，后台收货/入账/OAM 证据路径不会成为旁路。
- 收货与入账的幂等、`X-Request-ID`/原请求恢复、只读 `command-status`/`trace-status`、`no-store` 和未知结果不重放约束保持；本人入账仍生成独立不可变库存流水，拒收/异常记录不被误入账。
- 聚焦证据：`test_material_request_my_receiving.py`、`test_material_request_my_receipt_candidates.py`、`test_material_request_my_inbound_candidates.py` 合计 **104 passed, 1 warning**。未重跑已终态全套测试，没有外部写入、迁移或 hosted DB 操作。

## 2026-10-07 后置履约 HTTP 前置拒绝统一

- 将分配/占用候选与写入、发运/物流、后台收货/入账、OAM 收货证据及供给任务 HTTP 路由的 `fulfillment_forbidden` 前置到请求头解析、运行时密钥读取和业务查询之前；所有拒绝继续使用 `no-store`。服务层的角色、库存读取、组织范围和事实核验仍保留，未被路由守卫替代。
- 技术员本人接口 `my-receiving`、`my-receipts`、`my-inbounds` 和需求状态读取不受影响；底层分配、占用、发运、收货、入账、审计、通知、幂等和迁移事实未删除或改写。
- 聚焦证据：`test_material_request_my_receiving.py -k 'technician_cannot_read_backend or source_fulfillment or post_fulfillment or backend_write_routes_reject_technician_before_any_runtime_or_header_work'` **59 passed, 34 deselected, 1 warning**；`-k 'shipment_targets or technician_cannot_read_backend'` **26 passed, 66 deselected, 1 warning**；`test_material_request_completion_http.py -k 'http'` **2 passed, 1 warning**，确认完成数量状态读取仍可用；释放/拣货/出库/供给容量 **13 passed, 102 deselected, 1 warning**；取消/关闭/拒收退回/退回补偿 **53 passed, 1 warning**。未产生库存、审计、通知、迁移或外部写入。

## 2026-10-07 前端 capability 与公开首页聚焦复核

- `FormalMaterialRequests.test.tsx -t "renders only masked list/detail projections and keeps approval/fulfillment axes separate"` **1 passed, 76 skipped**：技术员详情继续隐藏供给计划、库存拣货、拒收退回、退回补偿、剩余取消和业务关闭，同时保留“本人收货与入账”，并保持敏感手机号/地址掩码。
- `PublicKnowledge.test.tsx -t "opens the public homepage without authentication"` **1 passed, 3 skipped**：首页不读取会话、不发网络请求、不显示登录/验证码/个人仓入口；“星星后台管理”仍准确跳转 `https://rscwz.cn/xx`，备案链接保持可见。
- 两项均通过现有仓库 Node 运行时执行；未改产品逻辑、未连接外部系统，也未重跑已终态全套测试。试点 MVP 范围、后续迭代清单和 PG16 hosted DB 外部阻塞不变。

## 2026-10-07 试点前端构建与发布范围复核

- 当前工作树前端 `tsc -b` 通过，`pnpm run build:warehouse` 通过（Vite 转换 2002 modules），未出现类型或构建错误。
- `pnpm run verify:pilot-release` 通过并明确输出 `scope=private pilot artifact only`、`publicCatalogIsReady=false`、`catalog.status=pending`；这证明试点产物范围标记正确，不把公开知识目录或认证、外部验收误报为已就绪。
- 严格 `pnpm run verify:release` 当前按设计失败于 `PUBLIC_CATALOG_NOT_READY: import and verify the source before release`；该失败是公开知识目录未导入的真实门禁，不改写为通过，也不影响已冻结的私有试点范围。
- 本次只验证当前源码构建与发布范围，不启动容器、不连接数据库、不发送短信、不部署；PG16 hosted DB、真实 PNVS 回执、公共知识源和正式 UAT 仍是独立门禁。

## 2026-10-07 CI 试点分支与完整公共发布门禁分层

- 修正 `.github/workflows/client-release-gate.yml`：`codex/notification-delivery-worker` 推送继续执行公共首页与 `/xx` 私有产物边界检查，但不因知识目录 `pending` 阻断试点 MVP；PR、`main` 和 `codex/production-readiness-gates` 仍执行 `--release` 完整公共目录门禁。
- 当前构建产物执行 `node cloud_oam/scripts/verify_public_entry.mjs` **通过**（目录 `pending/0` 被明确接受为试点状态）；`--release` 仍准确失败于 `PUBLIC_CATALOG_NOT_READY`。
- 新增回归测试 `backend/tests/test_client_release_workflow_scope.py`：**3 passed**，锁定分支触发、试点命令、正式分支条件，以及 Web/小程序/`/xx` 构建步骤不会从客户端门禁中消失。未连接外部系统、未部署、未放宽正式发布门禁。

## 2026-10-07 试点发布范围机器门禁

- `docker-compose.yml` 的 API 服务保留正式 profile 默认值 `OAM_RELEASE_SCOPE=production-v1`；`scripts/pilot_release.py` 仅在候选试点准备环境注入 `OAM_RELEASE_SCOPE=trial-mvp`，不会把生产默认配置误标成试点。
- `scripts/pilot_preflight.py` 将 `pilot_mvp_scope` 纳入配置检查并在输出中记录 `pilotScope=trial-mvp`；非试点范围（例如 `formal-v1`）直接失败。该字段只锁定发布范围，不替代 PG16、PNVS、真实 UAT 或部署证据。
- 聚焦验证：`backend/tests/test_pilot_preflight.py -k 'non_mvp_release_scope or valid_pilot_configuration_passes_without_external_io'` **2 passed, 62 deselected**；`backend/tests/test_pilot_deploy.py -k 'release_scope_marker_is_api_only_in_production_compose or migration_container_has_helper_module_and_console_script_path'` **2 passed, 37 deselected**；`py_compile`、`git diff --check` 和仓库安全门禁通过。未启动容器、未连接数据库、未重跑已终态业务门禁。

## 2026-10-07 MVP 发运状态投影复核

- 交接中旧记录的“已发运但需求仍显示 `shipment_status=not_started`”是 0168 之前的历史缺陷，不代表当前源码状态。当前 `record_fulfillment_command` 在发运命令同事务写入 `shipment_status='shipped'`，收货、OAM 收货、个人仓入账和通知轴仍分别维护；0168 迁移、readiness 和身份/因果守卫继续保留。
- 现有 `test_material_request_shipment.py`、分包投影测试及既有 0168 数量/SN PG16 记录（64774、43754、47363、94910）已覆盖发运投影、旧历史升级、分包回读、库存不重复移动和独立状态轴。本轮只核对当前源码与既有终态证据，未重跑已终态测试。
- 该修复只闭合 MVP 的状态可见性缺口，不扩大为完整 V1、真实 UAT 或生产上线；PG16 hosted DB 门禁仍需当前发布版本的可审计 hosted 运行证据。

## 2026-10-07 MVP 发运目标候选收口

- 新增只读 `GET /api/v1/material-requests/{id}/shipment-targets`，仅对 `admin/provincial_manager` 后台履约角色开放；按申请人的当前有效个人仓、组织归属、叶子位置和保管责任解析唯一目标；无位置返回空候选，重复或责任/组织不一致直接阻断，不生成库存、审计或通知写入。
- 发运面板在当前适配器提供该接口时改用个人仓下拉选择，并在唯一目标变化、需求版本变化或身份回读变化时再次核验；旧的人工 UUID 输入仅作为兼容适配器的后备路径。本人收货和个人仓入账契约未改变。
- 聚焦证据：`test_material_request_my_receiving.py -k 'shipment_targets or technician_cannot_read_backend_shipment_targets'` **6 passed, 28 deselected, 1 warning**，其中包含 HTTP 只读/no-store/405 及技术员拒绝回归；`FormalMaterialRequestShipmentPanel.test.tsx` 与 `materialRequestShipmentRecovery.test.ts` **22 passed**；受影响 Python 编译、TypeScript 检查和 `git diff --check` 通过。没有迁移、库存写入或外部数据库操作。
- 该里程碑只提升试点 MVP 的后台人工发运可操作性，不扩展供给容量、复杂履约后恢复或后续迭代清单；PG16 hosted DB、PNVS 真实回执、部署和用户 UAT 仍独立阻塞。

## 2026-10-07 试点 MVP capability 对齐（不等同完整 V1）

- 在既定试点链路“申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账”上，前端 `can_read_allocation_options` 现在同时要求 `admin/provincial_manager` 角色和当前 `inventory:read` 权限；与后端货源目录的硬校验一致。被显式收回库存读取权限的后台账号会整体隐藏后置履约区，不会看到必然被 API 拒绝的操作。
- 新增/执行窄回归：`formalMaterialRequestAdapter.test.ts` capability 投影与读取依赖 **2 passed, 19 skipped**；后端 `test_material_request_allocation_options.py` 的技术员货源目录拒绝及过期明细版本守卫 **2 passed, 7 deselected, 1 warning**。TypeScript 检查、`build:warehouse` 生产构建及 `git diff --check` 通过。未重跑已终态全套测试。
- 申请、审批、分配/占用、后台人工发运、本人收货和个人仓入账的底层状态、迁移、契约、出库依赖、不可变流水、幂等、审计、权限/安全约束、取消/关闭守卫和最小站内通知继续保留。拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维仍列后续迭代。
- 本版本仍是“试点 MVP，不等同完整 V1”。PG16 hosted DB 门禁继续是外部阻塞；缺少可审计 disposable PG16 runtime 时不伪造迁移、ACL、并发或连接终止通过。

## 2026-10-06 试点 MVP 范围冻结（不等同完整 V1）

- 本次冻结的可交付链路是：申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员首发页面只显示申请、状态查看、本人收货和本人入账；区域/总部账号通过 `can_read_allocation_options` 保留必要后台履约能力。
- `FormalMaterialRequests.tsx` 现在按 capability 隐藏技术员的整个后置操作区：供给计划、分配/占用、释放、拣货、出库、发运准备、OAM 收货、后台收货验收、后台入账、拒收退回、退回补偿、剩余取消和业务关闭；本人收货与个人仓入账仍保留。技术员若存在后置恢复坐标，只显示暂停写入的待核验提示，仍可进入只读详情，由后台账号继续处理。
- 本次只收口界面能力，不删除后端状态、迁移、契约、出库依赖、不可变库存流水、幂等、审计、权限/安全约束、取消/关闭守卫或最小站内通知。履约后供给容量、0178 分配后建计划、释放后重分配和复杂历史恢复，以及拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道和投递运维均列为后续迭代。
- 本轮聚焦证据：前端 TypeScript 增量检查及 `build:warehouse` 生产构建通过；`FormalMaterialRequests.test.tsx` 的 capability/恢复相关聚焦测试 **16 passed, 61 skipped**，覆盖技术员隐藏后台收货/入账面板但保留本人面板，以及恢复阻塞时仍可查看详情。不重跑已终态的全套测试。该结果只证明当前 UI 收口，不等同完整 V1 或上线。
- PostgreSQL 16 hosted DB 门禁仍为外部阻塞：缺少可审计 disposable PG16 runtime，迁移/ACL/并发/连接终止证据未通过；不得用 SQLite、历史隔离库或静态检查伪造通过。


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

- `scripts/verify_repository_safety.sh` 首轮按规则发现 3 个个人 macOS 路径：PG16 测试夹具中的本地二进制路径，以及交接/验收文档中的本地工作树路径；没有发现凭证、运行数据或受保护目录泄露。已将测试夹具改为相对仓库路径、文档改为“当前工作树”，未删除测试或业务证据。
- 当前重跑仓库安全门禁通过：**3057 candidate files，75,567,186 bytes，protected local paths ignored**。`.env.example` 的短信-only/微信关闭字段与 `git diff --check` 同时通过。
- 该门禁只证明仓库边界和内容安全；不替代当前源码集的完整 CI、PG16 hosted DB、真实 PNVS 回执、部署或生产 UAT。
- 当前源码负向扫描通过：网页/小程序公开登录 UI 不包含微信登录入口；策略层仍显式阻断旧微信路径；后端保留历史路由契约但在用户/provider 数据访问前拒绝密码、改密和微信；正式需求页及拣货/出库面板均以 `can_read_allocation_options` 作为后置区 capability 门槛。该扫描不替代浏览器 UAT。

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

- 修正密码停用页、管理员设置页及客户端 README 中残留的“微信或短信”文案，当前客户端统一显示仅手机验证码；微信小程序仍指平台形态，不代表微信登录渠道重新开放。
- 历史 `/auth/users` 创建入口现在在临时密码写入前硬拒绝，保留 `UserCreateIn`、历史密码列和迁移字段以兼容旧数据；生产旧写接口门禁仍保留。
- 新增聚焦守卫并运行：`tests/test_sms_only_policy.py -k historical_admin_user_provisioning`，**1 passed, 7 deselected, 1 warning**；`auth.py`/测试 Python 编译、前端 TypeScript 增量检查、受影响小程序 JS 语法和 `git diff --check` 均通过。
- 这仍是源码一致性证据；真实 PNVS 回执、PG16 hosted DB、页面 UAT、部署和生产发布未改变其阻塞状态。

## 2026-10-07 公开首页与个人仓入口边界复核

- 修正 `scripts/bootstrap_env.sh` 的生产提示为 SMS-only PNVS/Dypnsapi，不再提示微信登录。
- 当前静态产物边界扫描通过：公开构建标题为“交流备件知识大全”，不包含个人仓认证客户端；个人仓构建标题为“RSC个人仓”，资产和 manifest 均限定 `/xx/`；小程序注册页仍只有 `pages/knowledge/index`。
- release 产物门禁真实执行结果为 **失败**：`PUBLIC_CATALOG_NOT_READY`。源目录仍是 `status=pending`、0 条记录，因为真实飞书知识源尚未导入核对；没有伪造目录数据。该内容门禁独立于个人仓代码，继续记录为公开首页发布阻塞。

## 2026-10-07 部署 smoke 门禁收紧

- `scripts/smoke_test.sh` 现在只接受私有路径 `/xx/`，并要求 `/api/auth/login-options` 同时返回 `sms_enabled=true`、`password_enabled=false`、`wechat_enabled=false`；微信-only 配置不能再误判为无密码登录可用。
- 聚焦 smoke 回归 `tests/test_pilot_smoke_public_entry.py`：**9 passed**；`smoke_test.sh` shell 语法和 `git diff --check` 通过。测试只使用 loopback 合成站点，不代表真实域名或生产服务。

- 当前版本冻结为**试点 MVP**：申请/提交 → 审批 → 最小货源分配/占用 → 后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员首发界面只保留申请、状态查看、本人收货和本人入账；区域/总部保留必要的后台人工履约能力。
- 拣货区继续按 capability/角色隐藏：个人仓用户不显示或操作拣货，后台保留 `StockReservationPick`、`pick_id`、不可变库存流水、幂等、审计、权限、安全约束、取消/关闭守卫和最小站内通知。
- 不再把履约后供给容量、0178 分配后建计划、释放后重分配和复杂历史恢复作为本次首发阻塞；底层状态、迁移、契约和出库依赖保留，列入后续迭代并禁止用未完成证据宣称完整 V1。
- 后续迭代清单：拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维。
- 范围冻结后的页面改动已将技术员的整个后置后台区块按 capability 隐藏，并保留后台恢复阻断语义；仅做 TypeScript 增量检查并通过。遵照用户要求，不重跑此前已终态的测试；v80 的 83 项前端通过证据仍对应冻结前源码。
- 试点范围冻结只调整发布边界，不删除代码或降低安全门禁。PostgreSQL 16 hosted DB 门禁继续作为外部阻塞；此前测试句柄均已终态，本轮不重跑。

## 2026-10-06 v80 履约后供给容量恢复与个人仓拣货入口收紧

- 额度恢复后继续沿当前工作树、分支 `codex/notification-delivery-worker` 开发；未 reset、revert、丢弃未提交改动，未提交、推送、部署或写入服务器。
- 后端补齐占用/释放/拣货/出库/发运/收货/个人仓入账之后的供给容量只读证明路径：复用 `remaining_fulfillment` 严格核验不可变履约事实，供给命令历史允许经验证的后续履约后缀，仍以批准/取消/实际分配和活动计划计算新建余量，不把释放量错误地冲回原分配；更新计划也先经过相同容量边界。相关服务与容量测试已编译。
- 后端聚焦回归：容量/HTTP **28 passed**；供给、命令恢复、剩余履约、预约/释放等扩展回归 **183 passed, 1 skipped, 1 warning**。`git diff --check` 通过。
- 前端个人仓用户不再渲染“库存拣货”区域，也不会自动恢复或发起拣货请求；人工/后台拣货事实、`pick_id`、`StockReservationPick` 和出库后续接口保留。拣货权限用户的原组件测试仍保留；新增个人权限隐藏回归。前端聚焦 **83 passed**，`pnpm run build:warehouse` 通过。
- PostgreSQL 16 门禁仍未通过：`test_postgresql16_migration_acl_concurrency_and_kill_gate` 因需要明确确认的 disposable hosted DB 而失败，本地没有可用 `postgres`/`pg_config`；不能用 SQLite 或前述隔离历史代替 PG16 运行时证据。迁移/权限/并发/终止连接门禁未宣称完成。
- 正式 V1 仍未完成。下一步是取得可审计的 PG16 disposable runtime 证据，继续补齐履约后供给历史 SQL 反例及公开 HTTP/实际页面组合，然后再做同一源码集的全套门禁；人员调拨/离职交接、真实短信/微信/飞书/OSS 渠道、持续 PC/H5/小程序、真实 UAT、500 用户压测、历史期初/三日对账、RPO/RTO/回滚和正式发布授权仍分别待证。

## 2026-10-06 停止交接：额度剩余2%，按用户指令暂停

- **停止原因**：实时额度usedPercent=98、remainingPercent=2，已达到用户明确停止线。本轮只读复核后补齐接续约束，未开始新迁移或产品代码修改；不重置/购买额度，不将完整目标标记完成。恢复须由用户明确要求。
- **工作位置**：当前工作树；分支codex/notification-delivery-worker。正式迁移head20261227_0178。保留全部未提交改动，禁止reset/revert/丢弃；未提交、推送、部署、服务器写入或切流。
- **最后已完成证据**：v78公开容量数量/SN84405 exit0；v79实际页面首次创建后同单履约、入账1+取消1+独立关闭，数量82301和SN37739均exit0。对应run-za7veptd、run-j6ghbjd6、run-ct88orm9、run-x6_oe4w4在停止时再次核对均stopped/passed/serverExitCode0。v79的2788项源hash仍与当前工作树完全一致；只有文档继续更新。最终收据supply-create-final-v79-20261006.json和quota-stop-v79-20261006.json。
- **进程/页面**：本轮未启动测试或服务；以上过程均终态，不再轮询或重启。先前33810/33537也已终态，33537是已记录的旧帮助器断言失败，不能计通过。临时IAB测试页和本轮隔离PG均关闭；手机/电脑截图为联验产物，不是持久在线验收地址。
- **恢复后的第一步**：完整读取正式基线及本交接，检查当前diff与额度，复用v79证据。接着按docs/SUPPLY_AFTER_ALLOCATION_OPERATIONS.md末节推进占用/释放/发运后的供给管理及历史恢复。已实证未占用不等于未分配：批准2/分配1尚未占用时unreserved=2，而新增计划只能1；不得直接拿remaining_fulfillment.unreserved_qty当新增计划额度，不得把释放量从原分配量中减去。接续节列出可复用的历史校验入口及前向迁移/SQL反例/同单HTTP浏览器验收要求；这部分尚未实现。
- **完整范围保持**：正式人员调拨、离职交接清零/双方确认/区域总部复核仍有实际缺口；持久PC/H5、真实短信/微信/飞书/OSS等渠道与身份、全基线UAT、当前全后端和远端CI、500用户压测、历史与期初迁移、真实三日对账、RPO≤5分钟/RTO≤2小时及回滚演练、书面验收与发布授权仍待证。最新范围表位于FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md顶部，旧日期表已标明历史。
- **云端状态**：用户授权接管现有Chrome命令助手，目标Ubuntu-ipvk/118.31.37.87；最近实时打开目标命令助手被重定向至阿里云登录页，需用户在该Chrome完成登录。只阻断云端，不代表整个开发失败；没有服务器写入。用户无需发送密码、验证码或密钥。


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

## 2026-10-06 v75 真实浏览器供给管理接续完整履约：数量/SN通过

- 完整重读正式基线及v74交接，沿原工作树/分支，所有未提交改动保留。上轮实时接管Chrome确认ConsoleNeedLogin为progress；登录仅阻断云端。本轮无云端写入、提交、推送、部署或切流。额度实时已用89%、剩余11%，未到用户2%停止线，完整目标active。
- 复用既有隔离PG16浏览器工具增加单一供给计划scope，仅当前需求/任务的更新POST可写，其他创建、分配、收货/入账、关闭均403；当前单的补偿候选只读路径补齐。18项边界检查通过（61305初版、55919修正入口、27775当前边界均终态，重叠不累计）。日志supply-browser-v75-boundary-current.log。
- 真实UI是更新/取消的首次写者；成功POST后页面核验当前详情，不强制调用异常恢复用的trace端点。独立数据库READ ONLY按需求审计中的原trace恢复两次计划操作；库存/分配/SN/保管责任事实完整快照不变，计划数量保留，需求及任务各增加两个版本。
- **54450 exit0：数量run-qsfi53e3；55295 exit0：SN run-2a74e4j4**。两库均stopped/passed/serverExitCode0，各1999项受检后端源和505项前端/构建源与当前逐字一致。原生完整0177升级及空库往返、运行准入、真实期初和审批/计划准备、实际浏览器更新/取消各一次POST200、随后同单占用→拣货→出库→发运→本人收货→个人仓入账1件→独立关闭完整通过，SN逐件位置正确。两次页面POST分别有详情GET200；恢复在后续占用之前独立核验，不能声称占用/发运后的供给trace恢复已经支持。
- 已有晚计划记录时，回退由0177精确留存守卫拒绝且全部业务事实与head不变。此组合不能穿过0177去验证旧shipment/closure留存节点；收据明确lateSupplyRetentionBarrier=true、populatedClosureRoundtrip=false、retainedShipmentDowngradeDenied=false。正常旧分支的原验证未删除。checks中顶层businessClosed=false/browserCommands=false来自入账阶段旧HTTP helper；终态以supplyBrowser真实两次POST与closureDatabase.closed=true为准，最终v75收据已明确区分，后续整理runner输出时应避免混淆阶段与终态。
- 失败证据保留：98725/run-teuie1w4隔离工具漏放当前补偿候选GET导致403且尚未UI写入；55919/run-hbs67zug两次POST200后测试误要求正常路径trace GET；53587/run-rc3dd0ax两次POST200后测试按计划而非需求聚合ID找审计。均为联验帮助器缺陷，已修正，未改产品权限、迁移/服务约束。旧截图对应这些中间实例，不能冒充最终原生通过；最终截图supply-browser-v75-{quantity-verified,serial}.png。
- 390px手机页面无文档横向溢出（同一前端产物的run-hbs67zug），截图supply-browser-v75-quantity-mobile.png；桌面及手机表格仍按自身滚动显示。已恢复窗口尺寸，临时页面关闭。不是持久在线或真人UAT环境。
- 完整源original-integrated-source-v75-20261006.json仍2776项，较v74仅4项帮助器/测试/runner修改、无增删，全部产品源和历史迁移保持不变。最终收据supply-browser-final-v75-20261006.json、差异supply-browser-v75-source-changes.json。**本轮全部进程终态，源码固定解除**；不重启或重复轮询上述句柄。
- **下一项继续剩余补货及重分配**：分配后原计划更新/取消已有真实组合证据；新建计划仍被明确禁止。先复用真实需求与remainder分区，在部分分配/取消计划之后建立剩余计划，证明批准量不能被实际分配和多个活动预计计划重复覆盖；特别区分已释放占用与原分配事实，不能仅减原allocated_qty造成永久缺口或仅减cancelled_qty放出超量。需前向正式迁移及原历史兼容、独立SQL约束，再接query/PC/H5；不得修改0177或更早冻结源。
- 仍缺持久在线PC/H5、真实身份/渠道/UAT、全套后端/当前远端CI、完整V1其他功能、500用户、历史与期初迁移、真实三日OAM对账、RPO/RTO/回滚及上线授权。不能以本地两库完成声称整体目标或正式上线完成。

## 2026-10-06 v74 分配后原供给计划管理及0177前向门禁完成

- 沿原工作树/分支，完整基线重读，保留所有未提交修改。正式候选head为`20261226_0177`，前驱0176；旧迁移及支持源与v73清单逐字一致。无提交、推送、部署、切流。终态额度已用85%、剩余15%，未到用户2%停止线，完整目标active。
- 允许总部管理员在只有分配轴推进时更新/取消既有供给计划。完整历史按版本核对供给与分配交错顺序、批准量、历史角色/时间、SKU/SN、结果正文摘要、审计及状态转换；保留原结果与当前状态，恢复只读，库存和其他状态不变。分配后新建计划、占用/发运后管理及原操作者授权版本变化后的恢复尚未开放。
- 0177前向替换供给历史校验、任务写入守卫和readiness三个函数，保留OID/owner/ACL/安全参数、不增表或权限。真实失败22805暴露旧独立任务守卫仍禁止分配后更新（503），已纳入新迁移，不能只放开服务/query。供给命令和穿插分配命令均增加数据库规范化SHA256内容核对。冻结目录hash为`b4766c17b0529719e1d4429b754d8821d86ac54b07ca1ebfdffba2b486945a4c`。
- **13849 exit0：最终数量run-9v6bvspi、SN run-vyaznaj6均stopped/passed/serverExitCode0，各1999项受检源与最终后端逐字一致。**真实计划POST201、四次分配201、穿插两次更新/最终取消200，原key/trace READ ONLY恢复、不同操作者not_observed、申请人403、实际详情动作和库存不变通过；旧事实0177→0176→0177往返、新晚计划事实拒降0176且全部事实不变通过。旧/未来分配帧、缺审计、错误摘要四类SQL反例均P0001完整回滚。日志supply-late-v74-native-final.log。
- **92753 exit0：服务/恢复87通过1跳过。61293 exit0：查询/迁移图/权限等79通过。77954 exit0：最终目录/安全/head校验311通过。93197 exit0：完整SQLite升级、ORM一致和降base通过。**计数有重叠，不累加。81597的5项、57902的306项为先前子集，不重复计数。
- **87454 exit0：前端3文件52项通过**，含挂载页面分配后更新/取消、精确回读、响应丢失后重开页面并在后续分配推进后只读恢复且不重发。**95800 exit0类型通过**；31798 exit0 warehouse构建通过，此后只改前端测试，产品源码不变。20863原49通过2失败为新测试未将详情经过正式解析，已修夹具，没有放宽产品核验。日志supply-late-v74-ui-final.log。
- **56625 exit0：普通数量run-1y3b0ppm、SN run-werod2d0完整申请审批→分配占用拣货→出库发运→收货入账→独立关闭及留存/准入通过。38264 exit0：当前head部署门禁run-wgbvbp4j通过**，采集角色精确权限/锁/故障回滚/迁移往返与9种短信配置通过，未实际发短信。两者发生于最终摘要加强前，最终源仅5项供给目录/hash引用/反例脚本差异，最终双库原生覆盖该差异；收据明确记录逐文件delta，不能声称旧正常门禁的全部源清单与最终完全相同。
- 失败记录保留：13859原脚本读取不存在reference_no；22805真实数据库守卫拒绝；30556的旧head链/查询预期；20863的前端夹具。已全部终态，不重启或轮询旧句柄。**本轮全部进程终态，源码固定解除。**完整源original-integrated-source-v74-20261006.json为2776项，较v73新增5/修改39/无删除；最终收据supply-late-final-v74-20261006.json及差异supply-late-v74-source-changes.json，diff检查通过。
- Chrome命令助手接管到118.31.37.87后，新只读检查明确返回ConsoleNeedLogin；已打开同一Chrome阿里云登录页等待用户扫码。不得以凌晨04:10旧结果冒充新SSH连接；本轮没有服务器配置/业务写入。登录只阻断服务器相关核验，本地继续。
- **接续直接推进可见交付**：为现有隔离PG16浏览器工具增加精确单一供给计划scope，真实页面更新/取消并回读；同时补“晚计划命令之后继续占用至入账/结单”的组合证据。随后定义分配后新增计划的剩余量，至少逐行扣除已分配量并处理仍活动的预计任务，保留原计划不可变数量及审批量，不能简单放开按钮或用实际分配与预计量重复相加。分配/后续履约历史与有效取消、释放、退回补偿必须分别证明。
- 操作说明SUPPLY_AFTER_ALLOCATION_OPERATIONS.md；基线审计已更新。真实浏览器供给联验尚未完成，持久在线PC/H5、真人UAT/真实渠道、完整后端/远端CI、500用户、历史与期初迁移、真实三日OAM对账、RPO/RTO/回滚和发布仍独立待证，绝不能标记完整目标完成。

## 2026-10-06 v73 连续部分分配与供给历史恢复：数量/SN原生验证完成

- 完整重读正式基线，沿原工作树和分支保留全部未提交修改。上一轮Chrome接管只确认已有04:10诊断，不当作新SSH连接；本轮没有服务器写入或部署。本轮初始额度已用77%、剩余23%；终态复核已用79%、剩余21%，尚未达到用户2%停止线。
- 新聚焦场景实际调用供给创建和另一总部管理员的两次分配，发现第二次部分分配仍为partially_allocated时，服务总是插入同状态转换，被`ck_state_transition_events_changed`拒绝并包装成并发冲突（26557 exit1）。现在每笔分配保留事实/命令/版本/审计，只有分配轴变化才追加状态转换；不改数据库CHECK，不新增迁移，不改库存余额。50886 exit2仅为新测试括号笔误，已修，两个失败日志保留。
- 新只读历史验证器核对供给命令之后的连续分配命令、对应分配事实、逐行批准上限、历史角色及时间、SN绑定/生效政策、结果摘要、独立审计和确实发生的状态转换，以不可变的历史视图验证旧供给结果，绝不回写当前需求。非分配后续操作仍拒绝，不能声称占用/发运后所有供给恢复已完成；原供给操作者授权版本变化仍按既有规则阻断，未放宽成撤权恢复。
- **81020 exit0：24通过、1跳过**（数量模式无SN损坏检查）；**9871 exit0：完整供给服务/恢复/新分配历史及原分配回归96通过、1跳过，86.26s**，有重叠不相加。此前42237原恢复65通过42.74s也已回收exit0。**50270 exit0：3个前端文件47项通过**，页面协议未变，未重新构建前端。日志`supply-allocation-v73-{fixed,regression,ui}.log`。
- **2376 exit0，数量/SN两种模式全部终态**：数量`run-_wn8bdnm`、SN`run-qoilpyyl`均stopped/passed/serverExitCode0，各1994项受检源与封存点逐字一致。正式0176完整运行准入、实际供给POST201、另一管理员四次分配各POST201、每步原供给GET200（READ ONLY且关闭写开关）、不同操作者无结果及申请人403均通过；各四笔独立审计、两次真实状态转换，库存始终4。SN逐件绑定完整。保留所有原事实的0176→0168→0176迁移往返及最终运行准入通过；不是降到0067的分配留存拒绝证明。日志`supply-allocation-v73-native.log`；不要重启或继续轮询已结束句柄。
- **修正旧审计**：当前0059函数来自0168冻结正文，0069及后续早已允许旧供给事实随履约推进；实际数据库限制是供给命令必须构成最终审批后的连续前缀，不能夹杂分配等命令。服务和query仍中性轴限制。旧v72/v68不准确的“现行数据库中性轴守卫”已明确更正。
- **下一项仍是分配/履约后的供给管理**：不能只放按钮或删除版本连续验证。优先支持真实历史后的计划更新/撤销，并明确剩余缺口的数量定义，再开放新计划；旧计划目前没有与具体分配关联的“已供给”份额，不得把同一份批准量同时计入实际分配和多个新增预计任务。有效取消、释放、原分配事实和独立退回补偿必须分开证明；新规则需前向迁移、旧记录保持、SQL反例和权限证据，再接HTTP及PC/H5。本轮仅解决真实分配缺陷和分配后恢复，不冒称上述目标已完成。
- 本轮源清单`original-integrated-source-v73-20261006.json`2771项，较v72新增3/修改4/无删除，差异`supply-allocation-v73-source-changes.json`；本轮没有修改冻结迁移、ACL、前端或小程序源码。最终收据`supply-allocation-final-v73-20261006.json`；全部句柄已终态，源码固定解除，diff检查通过。无提交、推送、云端写入或部署。完整V1、持久在线PC/H5、真实UAT/渠道、当前全后端及远端CI、性能/迁移/三日对账/灾备/发布仍独立待证，目标active。

## 2026-10-06 v72 草稿补偿候选版本0修复完成

- 真实草稿由正式draft服务创建，使用实际读取角色与HTTP路由，复现候选GET返回503（61957 exit1，draft-candidates-v72-reproduced-authorized.log）。最初测试误读CreateResult.version（45713 exit1）、后因草稿专用夹具未配置读取权限返回403（92389 exit1）均为测试装配问题；已分别改为读取实际MaterialRequest以及复用_read_world，不能当作产品修复证据。
- 仅候选响应允许非负整数版本；版本0必须返回空items。前端采用同样规则，负数、布尔、字符串、小数及版本0非空候选继续拒绝。实际补偿POST的expected_request_version仍严格大于0，未放宽权限、来源历史、库存或数据库守卫，也未新增迁移。
- **88420 exit0：75项后端聚焦通过，216.08s**，覆盖真实草稿HTTP空候选及全SQL只读、版本/非空反例、原补偿/撤权恢复/数量分区回归。**80351 exit0：7项UI通过**；**83182 exit0类型检查**；**99736 exit0 warehouse构建**。日志draft-candidates-v72-{backend,ui,types,build}.log；全部句柄终态，无等待任务。唯一warning是已有Starlette/AnyIO弃用提示，不是失败。
- 源清单original-integrated-source-v72-20261006.json仍2768项，较v71仅4文件修改（候选DTO、HTTP测试、前端校验、前端测试），无增删，迁移/ACL/库存代码未改。最终收据draft-candidates-final-v72-20261006.json。v71数量/SN原生浏览器证据保持其封存源码，未重跑；v72是上述四文件差异的聚焦验证，不声称同一完整源码已通过GitHub或生产门禁。
- **下一项：履约后剩余补货/供给管理**。基线1.7/1.8要求缺货补货、多来源及分批履约；当前material_request_supply._require_final_approval_graph与query._supply_management_allowed要求履约轴中性，因此开始分配后管理入口被禁止。此处原称0059现行数据库守卫也要求中性不准确：后续迁移已允许旧供给事实随履约保留，但仍要求供给命令构成最终审批后的连续前缀（v73核对0168冻结正文）。容量计算仍用原批准减原cancelled_qty；历史恢复亦要求命令轴等于当前轴。需实证前向的剩余数量边界和历史恢复，再做新迁移/公开接线；不能改0059、只放按钮、删除守卫或简单减取消量来绕过。既有数量/SN退回验收/入账、补偿取消、关闭均有已封存证据，避免重造候选。
- 现有Chrome命令助手已接管，用户无需重复登录；没有云端写入/提交/推送/部署。临时浏览器页与两个隔离PG实例已正常关闭，截图可看但不是持续可访问的用户验收环境。完整V1目标active；用户新增**剩余额度2%写交接并停止**，最新实时读数已用75%、剩余25%，尚未到停止线；禁止使用重置/购买规避边界。真实UAT/外部渠道、远端CI、持续PC/H5入口、性能/历史迁移/三日对账/灾备/发布仍分别待证。

## 2026-10-06 v71 来源仓数量/SN真实浏览器验证通过

- 用户已授权接管现有Chrome命令助手。当前页面确认Ubuntu-ipvk、118.31.37.87，旧只读SSH诊断退出0；没有重新登录、修改凭据或云端写入。用户新增持续执行边界：**剩余额度到2%时填写交接并停止**。本轮实时查询周额度已用73%、剩余27%，尚未到停止线，不得调用额度重置或购买来继续。
- 浏览器隔离入口仅允许当前拒收退回的来源仓验收和独立入账；互斥模式、准确路径、Origin/Host限制及原key/trace读取边界17项通过（21655 exit0）。新PG16帮助器停靠在交运完成且尚未验收的阶段，由实际页面首次提交，之后只读验证库存余额、流水、SN位置和原业务事实。
- 修复页面成功后列表仍显示旧数量的问题，详情与对应列表同步更新；补齐电脑/手机布局。页面6项测试、类型和warehouse构建通过（57262、55280 exit0）。最初数量浏览器95208 exit1，业务POST均201，但帮助器误把退回目的账户当转出账户；现改为核对in_transit账户及实际目标账户，失败证据run-b2fy6zys保留，不记通过。
- **数量9578 exit0**，run-ahltm8ro已stopped/passed/serverExitCode0，正式0176运行准入、真实浏览器验收和入账各一次POST201、两次原命令GET200、库存实际入账及原业务事实不变、非空降级拒绝均通过。刷新后列表/详情0待确认、0待入账、1已入账。手机390px宽度无横向溢出；截图rejection-browser-v71-quantity-{mobile,desktop}.jpg。1991项后端源与505项前端/构建源无漂移。
- **SN 91736 exit0**：run-7jc7ixn3已stopped/passed/serverExitCode0。实际浏览器录入合成实物SKU、SN、二维码，验收与独立入账各一次POST201，两类原请求GET200；只读PG验证在途减少、目标余额增加、逐件当前位置及posted流水，原审批/履约/退运事实不变。0176完整运行准入、留存拒降及受检1991项后端/505项前端源均通过且无漂移。截图rejection-browser-v71-serial-desktop.jpg。两库STOP-BROWSER已写入、全部句柄终态，不再轮询或重跑同版证据。
- 本轮源清单original-integrated-source-v71-20261006.json（2768项，较v70新增1/修改6/删除0），差异rejection-browser-v71-source-changes.json，最终收据rejection-browser-final-v71-20261006.json。下一项修退回补偿候选GET对真实草稿版本0的拒绝：仅候选读取允许0且必须为空，补偿写命令仍要求正版本；再继续履约后剩余补货/重分配及完整V1缺口。真实用户UAT、远端CI、外部渠道、性能、历史迁移、三日对账、灾备/上线仍待证，无提交/推送/部署/切流。

## 2026-10-06 v70 来源仓公开验收、独立入账与PC/H5接线

- 新增`/api/v1/rejection-returns/my-warehouse`、逐退回`warehouse`详情、验收POST、独立入账预览/POST及两类原key/trace恢复。先按当前保管仓及组织读取权限筛选，再完整核验当前责任/唯一角色/授权和逐笔历史；对象历史损坏只返回受权对象的blocked记录。短少保留未确认，接受/拒收/破损/未确认/待入账/已入账独立核对，公开预览不泄漏内部库存账户方案。
- 命令沿用正式0174/0175事务和HMAC坐标，增加原输入指纹核验；响应严格验证后一次commit，异常rollback，框架错误也no-store。未新增迁移或ACL，正式head仍0176。
- **1520 exit0：131 passed、2 skipped，195.03s**，覆盖新投影/HTTP、原来源/验收/入账回归；日志`rejection-warehouse-v70-focused.log`。两个跳过是已有模式特定案例，不能计为通过。
- **58570 exit0**：数量`run-_igena1g`、SN`run-w3xs6irl`均stopped/passed/serverExitCode0，各1990项受检源码与当前后端相同。正式0176完整运行准入、首次实际HTTP短少验收/破损接受及独立混合成色入账、准确PG并发阻塞及超量/重复拒绝、撤权READ ONLY原key/trace恢复、错指纹409、关闭写入口POST503、当前仓详情/队列、SQL反例/ACL/留存均通过。分批尾部第二次验收/入账仍由真实服务直接执行，不声称全部由浏览器操作。
- 前端新增`/rejection-return-receiving`，从“退回收货与入库”进入；要求当前admin/provincial_manager和stock_operation/inventory读取权限。显示当前仓库可选记录、逐次验收、独立库存入账；数量与SN实物核对、异常凭证上传、入账目标和成色/SN预览分别确认。提交前可靠保存原请求，丢响应/离开保留；恢复只GET、完整历史与当前身份核验后清理，撤销写权限仍可恢复。未知结果不自动重发。
- 实际原生HTTP导出已作为数量/SN契约夹具（明确合成身份/附件），与Python标准化请求指纹一致。共享`formalReturnReceipt.amounts`提取不伪造shipment/work-order身份。**68093 exit0：5文件51项前端聚焦通过**，含新契约、原请求恢复、挂载页面及旧收货表单回归；**59690 exit0：新增权限路由后10项通过，warehouse构建通过**，其中路由旧6项与51项有重叠。62446的26项、80309的23项是先前子集，不累加。
- 类型：67806/34694/71234通过；8410 exit1只因新增测试fixture的`schema_version`被推断为string，已加字面量类型，**最终83776 exit0通过**。本轮所有句柄已终态。构建日志`rejection-warehouse-v70-build.log`。源码和最终收据为`original-integrated-source-v70-20261006.json`、`rejection-warehouse-final-v70-20261006.json`。
- 用户Chrome接管已完成，17:31:12 SSH实时只读确认118.31.37.87可达、ssh active、旧API/DB健康、磁盘33GB空闲；无需重复登录。本轮没有云端写入、提交、推送、部署或切流。
- **下一项直接做真实浏览器闭环**：现有`pg16_fulfillment_browser.py`与`run_local_pg16_fulfillment_checks.py`只覆盖旧收货/入账/关闭，需要为拒收退回来源仓选择一个明确隔离库的浏览器停靠阶段，复用正式主App/当前身份，浏览器实际操作本轮页面；不要把夹具或TestClient当作浏览器/UAT。保留数量/SN既有终态结果，后端改变后按差异验证，不重复整套已证范围。另需修v68草稿版本0候选GET、履约后剩余补货/重分配、全后端/远端CI与完整V1审计。
- 真实用户UAT、外部渠道、500用户、历史/期初迁移、真实三日OAM对账、灾备与正式上线仍未完成。完整V1目标保持active，禁止以本轮门禁替代生产验收。

## 2026-10-06 v69 原拒收退回登记与实物进展公开接口及PC/H5

- 已接正式来源分页GET、退回登记POST、独立撤销登记/实物发出/承运交接POST、进展GET和原key/trace恢复。保留完整取消历史后计算可再登记量和SN，逐动作当前权限、读范围、请求版本及审计游标复核；损坏历史拒绝，不能冒充空来源。原请求必须准确指纹，写开关关闭仍可读；POST按原正式0172/0173守卫一次事务提交，没有变更迁移/ACL。
- PC/H5需求详情新增“拒收退回”：按物料名称/SKU/原验收单选来源、数量或逐件SN；撤销、实物发出、承运交接是独立操作，交运绑定准确前一事件与运单。先可靠保存原请求再单次POST；重开原需求可只读恢复，未核验不清理、不重发，并阻断其他写操作。UTC时间转换与服务器六位微秒规则一致，避免恢复指纹不匹配。页面显示“已交承运”，不推断仓库已经验收或入账。
- **8297 exit1，107通过/1失败**，唯一失败为测试UTC字符串`+00:00`与规范化`Z`表示不同，非业务失败；修正测试预期后**18975 exit0，HTTP21项通过**。来源/取消恢复额度/原SN/撤权只读/损坏历史/输入指纹及原服务均在107项范围内通过，不重复累加。日志`rejection-http-v69-{focused,fixed}.log`。
- **74272 exit0**：数量`run-lkopxbuj`、SN`run-z_vbg75z`两库均stopped/passed/serverExitCode0，各1984项受检源与v69后端逐字一致。正式0176完整运行准入、首次真实HTTP登记/撤销/发出/交运、实际PG并发阻塞及重复/取消冲突拒绝、取消后并发重新登记、原key/trace READ ONLY恢复、错指纹409、写入口关闭503、来源历史GET及原SQL反例和留存通过。并发重新登记仍由真实服务直接提交，其首笔登记/进展已由公开HTTP提交；不是每一步都由浏览器执行。
- **5639 exit0，页面/适配器集成105通过**；最后新增SN精确选择后**10069 exit0，组件10通过**；**20672 exit0，父页面原退回保留1通过/75未选**。这几组有重叠，不累加。34200/19502及最终92836类型检查通过，warehouse构建exit0；本轮所有句柄已终态。早期8942类型失败为history未显式收窄，已修；33694因shell工作目录错误没有执行修正，旧错误日志保留，34200是修正后证据。无真实浏览器/UAT。
- 收据`rejection-http-final-v69-20261006.json`；完整源码`original-integrated-source-v69-20261006.json`2753项（新增9/修改9/无删除），diff检查通过。上一轮v68的64117也已回收exit0，双模式完整证据已封存，不重跑已证范围。17:10:54 SSH只读恢复证据沿用，本轮没有云端写入、提交、推送、部署或切流。
- **直接接v70来源仓操作**：`material_request_rejection_receiving._authorized`已经按当前region/headquarters仓责任人、唯一admin/provincial_manager角色、stock_operation/inventory读取权限及唯一CustodyAssignment范围严格限定。新增队列必须先以当前保管仓过滤，再逐记录完整核验，不能向任意需求读者曝光全库退回。`rejection_return_receiving_detail`目前固定awaiting状态仅是原始交运来源，已验收/已入账要独立计算，不能重复称待收货。
- 仓库验收历史复用`material_request_rejection_receipt._history`（返回完整结果、已确认量、已处理SN；短少不耗后续份额）；分别提供当前剩余验收与独立入账预览/命令/恢复。既有HMAC坐标是`/api/v1/rejection-returns/{return_id}/warehouse-receipts`及入账服务`key_for`内坐标，路由必须一致。复用0174/0175服务和正式库存守卫，补HTTP、来源仓页面、真实浏览器闭环，不另造候选。
- **仍待证/待做**：v68补偿来源GET的request_version字段gt0使草稿版本0需单独修正（当前面板隐藏草稿）；履约后剩余补货/重分配及取消后的新供给保护、仓库公开验收/入账、真实浏览器/用户UAT、全后端/远端CI、外部渠道、500用户/历史迁移/三日对账/灾备/上线均未完成。完整V1目标保持active。

## 2026-10-06 v68 已入账退回补偿HTTP与PC/H5接线

- 新增正式退回补偿来源选择GET、独立POST、按原key或trace的command-status。来源完全核验原拒收/交运/仓库验收/真实入账/补偿审计，返回物料名称、SKU、数量和时间，不让用户填UUID。当前原申请人权限决定可补偿；撤权后保留历史读取。恢复要求准确输入指纹，错指纹409；写开关关闭时仍可只读恢复。
- PC/H5需求详情已挂接“已退回物资补偿”面板：选择已入账记录、原因、先可靠保存原请求再单次POST，之后核验原命令/来源/详情总取消/版本2分区和当前身份才清理。未知结果保留，禁止自动重发；离开再打开有恢复入口，原补偿待核验时阻断其他写操作。修正父页面closureBlockingRef只初始化自己的raw状态，避免混入兄弟面板历史锁。
- **98745 exit1**：初轮10通过/3失败（2个HTTP业务错误漏显式捕获；缺请求指纹按现有通用header规则返回400而非测试期待422）。已修业务错误映射及精确测试预期；其中数量/SN来源选择与撤权后历史/指纹核验2项已通过。**92702 exit0，HTTP11项通过**。
- **36458 exit0，前端33项通过**（新补偿提交/未知恢复/来源变更/总量矛盾/离开保留及既有数量取消测试）；**16853 exit0，页面与适配器95项通过**，含跨页面原请求保留及no-replay传输。70389类型通过，99671最终类型及warehouse构建exit0，日志为`return-compensation-http-v68-{types-release,build-final}.log`。更早84521/76925构建通过，76875仅旧组件测试（新测试文件路径写错，已改正确src路径），不算新面板证据。
- **64117 exit0**：数量`run-cfqngxf_`及SN`run-erokv6za`均stopped/serverExitCode0、checks与正式0176完整准入/留存通过；首次补偿由真实HTTP POST提交201，并发竞争实际PG阻塞者可见，另一请求拒重复。撤权后READ ONLY按key/trace恢复、错指纹409、来源GET一致、关闭写入口POST503均通过。两库各1979项源与v68当前后端逐字一致；最终收据`return-compensation-http-final-v68-20261006.json`。完整源2744项，较v67新增5/修改9/无删除。本轮全部句柄终态，不复跑同版已证范围。
- **供给任务复核修正**：`material_request_supply._require_final_approval_graph`与0059数据库守卫都要求全部履约状态轴为初始状态；退回后新增/修改供给计划当前会被拒。因此尚未证实此前推测的“退回后超量补货”路径，不能只在旧容量函数扣数就声称修好。真正基线缺口是支持履约后的剩余补货/重分配，以及尚未履约的独立剩余取消后禁止新计划，需独立前向守卫/完整历史验证，不改旧迁移或放松状态校验。
- 下一段最接近可见交付：把现有0172—0175退回登记、撤销/重登、独立实物发出/承运交接、当前来源仓责任人验收与分成色/SN入账服务挂到正式HTTP和页面；列表来源要按当前本人/仓库范围可选，不能输入原始UUID。已入账补偿页面尚未经过真实浏览器/用户UAT，当前不能替代完整退回闭环。
- 无提交、推送、部署、生产迁移或切流，正式V1全范围目标active；真实UAT/渠道、全套当前后端/远端CI、历史期初迁移/三日对账/500用户/灾备/发布各自待证。

## 2026-10-06 v67 正式数量接口与数量/SN原生通过

- query详情合并未履约取消和退回补偿；批量读取列表存在性后完整核验有事实的需求。正式完成覆盖/版本2分区及剩余取消/独立关闭启用正式0176事实，旧0171历史保持v1。
- **79410 exit0**：数量run-1k8gxy0i、SN run-nxn7yq5r均stopped/serverExitCode0，checks、正式0176运行准入/留存通过。实际HTTP在剩余取消前后读取详情/完成覆盖/分区/列表，READ ONLY、写入口关闭、刷新一致、全部事实不变通过。两库1978源与v67最终封存仅一个测试文件断言修正，业务源完全一致。
- 33956原130通过/3失败保留；新增批量探测使固定SELECT数为21，不随页行数增加，54541查询22项通过；两项新增DTO Decimal/字符串断言已修，**36548 exit0，2 passed/11.29s**。10448前端45项通过。不累加重叠测试。
- 收据`artifacts/formal-0165-integration/return-http-v67-evidence-20261006.json`与完整源`original-integrated-source-v67-20261006.json`2739项已保存。所有v67句柄终态，不重跑相同范围。16:39:57SSH只读连接及服务健康证据仍保留，无云端写入。

## 2026-10-06 v66 正式0176迁移与原生门禁通过

- 同一候选冻结为正式revision `20261225_0176`，精确前驱0175；固定22条DDL、完整目录及摘要，Base/ACL/readiness/运行准入同步。新增不可变补偿表、4个私有函数、前向替换3个函数，历史迁移不改。
- 76368 exit0：58项0175兼容/补偿服务回归通过。75570终态321通过/1失败，唯一失败为测试总触发器数漏加5；71910终态2失败为测试链漏176→175及表清单漏补偿表。修正上述测试预期和已审核版本夹具后，**76165 exit0，49 passed/98.91s**，含完整SQLite升级/ORM一致/降级、唯一head、权限清单和版本拒绝检查。计数存在重叠，不累加。
- **88653 exit0**：旧取消数量run-e5a0h3z8、SN run-fl525tna；**27157 exit0**：新组合数量run-nmq7igre、SN run-hkb6flth。四库均stopped、serverExitCode0，正式0176完整运行准入；新组合批准3=个人入账1+退回补偿1+未履约取消1，独立关闭、SQL旁路拒绝、撤权后READ ONLY恢复及已有补偿拒降175且事实不变通过。
- **74844 exit0**：当前head部署准入run-ermqnru9，status=passed，采集角色精确权限/往返及9项短信配置恢复通过；不是实际短信发送、GitHub CI或完整报表导入演练。所有本轮句柄均终态，不再轮询或重复已证范围。
- 最终收据 `artifacts/formal-0165-integration/return-compensation-final-v66-20261006.json`；完整最终源 `original-integrated-source-v66-final-20261006.json`2739项。四个原生库各1978源，较最终封存仅3个测试文件预期修正，产品/运行/迁移源码完全一致；49项补测覆盖该差异。初始源清单保留，323旧冻结源不变。
- 原始失败48983/72890、54918/42241、75570/71910保留，不冒充首次全通过。正式补偿迁移已激活；公共退回HTTP/PC/H5、真实UAT/渠道、全V1其他验收、性能/历史迁移/三日对账/灾备及上线分别待证。无提交、推送、生产迁移、部署或切流。

## 2026-10-06 v65 组合剩余取消：数量/SN原生与只读恢复通过

- 16:10:42实时SSH只读确认118.31.37.87连接成功、ssh active、数据库容器健康、磁盘剩余33GB；Chrome命令助手已接管，用户无需重复登录。沿既有工作树和分支，完整重读基线、核对diff，未提交或部署。
- 同一0176候选新增版本2剩余取消证据：批准3件中正常个人入账1、拒收退回来源仓入账并补偿1，实际释放第三件的占用后准确取消剩余1，之后管理员独立关闭。独立个人入账证据读取避免取消/完成覆盖递归；SQL保留原0171无补偿分支，新增分支独立核验完整来源和逐行数量。
- 80341 exit0：127项聚焦回归通过（141.11s）。73872 exit1原生在撤权后READ ONLY恢复时发现历史释放校验仍尝试FOR UPDATE；现已使历史读取使用完整只读审计链校验，写流程仍保留原锁。99222 exit0：相关取消/关闭60项回归通过（34.33s），不与127项简单相加。
- PC/H5取消输入支持严格版本化分区；原请求恢复按unfulfilled_cancelled_qty核对原取消，按详情总量与退回补偿分区核对总数，不再错误要求总取消等于本次取消。错误分区保持原请求且不重发。69661 exit0：46项UI/契约测试通过；34021 exit0类型通过；warehouse构建通过。9327 exit1的唯一失败是旧错误文案断言，保留日志并修正；新增完整页面提交/恢复组合验证。
- **36884 exit0**，日志`artifacts/formal-0165-integration/remaining-return-v65-native-readonly-fixed.log`。数量`run-xo9rokfg`、SN`run-ur__48su`两库均stopped/passed/serverExitCode0，各1965项源码与封存点逐字一致；批准3=个人入账1+退回补偿1+未履约取消1，独立关闭通过。5类补偿/3类剩余取消/4类关闭SQL反例均拒绝并全量回滚，撤权后READ ONLY恢复通过。两库完整前后目录和22条DDL完全一致。全部本轮句柄已终态，不再轮询或重跑同版已证范围。
- 先前4344 exit1（`run-3228bwaa`）因新增实际释放后，降级正确先触发0170部分释放留存守卫而非旧发运守卫，已按场景修正预期并保留失败记录；73872失败库`run-57yohpum`保留。所有失败都不能记作门禁通过。
- 已封存v65完整源码2726项：新增1、修改14、无删除，323个旧冻结迁移/支持源与v64逐字一致。源清单`original-integrated-source-v65-20261006.json`及差异`remaining-return-v65-source-changes.json`；最终收据`remaining-return-final-v65-20261006.json`已保存。
- 接续：冻结并正式激活同一0176、Base、权限/readiness、空库往返及历史留存，验证旧0171无补偿原生分支。必须同步query列表/详情取消总量、完成覆盖、八分区、剩余取消、关闭及公开退回HTTP/PC/H5，不能只翻内部开关或放宽前端校验。目前正式head仍0175，候选安装后runtimeAdmission=false。完整V1、真实渠道/UAT、远端CI、迁移/三日对账/性能/灾备/发布仍独立待证，目标active。

## 2026-10-06 v64 退回补偿后的独立关闭：混合/分批四组原生通过

- 15:39:18 SSH只读复核118.31.37.87成功、ssh active、数据库健康、剩余33GB，用户无需重复登录。完整基线再次读取，截断的3.1—3.3已补读；保持原工作树/分支和未提交改动。
- 完成覆盖新增内部前向模式，完整核验逐笔补偿后计入取消总量；原正式0175入口仍用原数量契约。新增版本2八分区读取，保留0171原七分区证据。关闭按每个原拒收明细校验已回仓并补偿数量，生成含准确补偿来源的v2证据；普通关闭仍生成原v1。v2恢复重新核验全部退回库存、补偿、覆盖与结清来源，不依赖历史仓库人员的当前权限。
- 延伸同一个0176编译器，精确验证并前向替换原关闭SQL函数，不修改旧冻结迁移。新SQL独立核对补偿来源、取消覆盖、逐原拒收数量及完整v2证据；尚未激活正式迁移或HTTP。
- **5118 exit0，87 passed/109.70s**：新补偿/数量分区、原关闭和完成覆盖聚焦回归。日志`return-closure-v64-focused.log`。
- **14191 exit0**：普通数量`run-2rju8vxs`、SN`run-gxk6zz9s`均正式0175完整履约/关闭/留存/运行准入通过；每库1964项受检源。随后只改原生夹具的混合场景阶段断言，业务及冻结迁移未改，正常双库结果可按差异复用，不能声称整个源清单与修改后完全相同。
- **9644 exit1**：混合数量`run-pz4pz7b7`完成正常1件入账和另1件拒收，失败于旧阶段断言仍期待全量拒收。已改为逐分区明确断言posted1/rejected1，保留原数量守恒要求。失败日志`return-closure-v64-native.log`。
- **68273 exit0**：混合数量`run-73s4nafe`、SN`run-9h6ttbnz`；**40269 exit0**：分批数量`run-zluhkxkf`、SN`run-5b55hexp`。四库均stopped/passed/serverExitCode0、各1964项受检源与当前封存点逐字相同。真实个人仓入账1件/拒收退回补偿1件同线结清，及3件两笔补偿后的独立关闭均通过；仅回仓或仅部分补偿不能关闭。缺关闭审计、遗漏补偿来源、错误原拒收绑定、篡改覆盖数量的直接SQL被独立数据库守卫拒绝且全量回滚；撤销关闭授权后仍以READ ONLY核验原关闭证据。全部库存及原需求事实不变。
- 四库完整前后目录和20条固定DDL完全相同，均安装→回滚→固定SQL重放验证。最终收据`return-closure-final-v64-20261006.json`；完整源`original-integrated-source-v64-20261006.json`2725项，比v63新增1、修改15、无删除，323个旧冻结迁移/支持源逐字不变。正常双库仅较最终源少一处混合测试断言修正，业务源码一致，不重复整套正常门禁。
- **54318 exit0，62 passed/41.59s**：补跑旧剩余取消与七分区回归（共享关闭结清检查的调用方）。日志`return-closure-v64-legacy-cancel.log`。本轮所有句柄均终态，已通过相同范围不再复跑；后续直接进入下一组合。
- PC/H5数量核对卡支持严格v1/v2读取与独立退回补偿显示，旧取消输入校验仍明确只接受v1；未伪造旧证据。适配器接受v2，但正式0175后端仍未返回该版本。前端初次npm入口不存在，改用现有Node直接运行；初轮38通过/1失败（夹具未同步详情取消总量），后续修复类型/只读夹具：**9489 exit0，类型检查及39项聚焦测试通过**。warehouse Vite构建通过。日志`return-closure-v64-{ui-fixed,typecheck-fixed,web-build}.log`；更早失败日志保留。
- 正式激活前必须同时把`material_request_query`列表/详情中的取消总量与completion/八分区接入完整补偿核验，否则前端会正确拒绝不一致详情；不得删除严格一致性检查。还需未履约取消与退回补偿同单的版本2取消证据/守卫、正式0176冻结/ACL/readiness/留存及公开退回HTTP与页面写闭环。当前没有上线或真实UAT证据，无提交/推送/云端写入/部署。

## 2026-10-06 v63 逐笔退回补偿服务、数量/SN原生验证通过

- 上轮接管Chrome并于15:12:33实时SSH只读确认118.31.37.87可连接、ssh active，旧站及相关DB健康，磁盘剩余33GB；无需重复登录。本轮完整重读正式基线并补读截断段，沿v62交接继续，保留原分支和全部未提交改动。
- 新增独立退回补偿schema/契约、原始入账证据读取器、补偿服务和八分区数量函数。按单笔真实仓库入账全量取消，唯一入账引用防重复，同需求分批回仓可逐笔补偿。当前原申请人cancel授权、版本、原键/trace、摘要、审计均独立校验；不伪造个人入账，不改原批准/0171取消事实、需求版本或库存，不自动关闭。
- 内部读取完整校验拒收→登记→交运→仓库验收→库存/份额/SN/原游标/通知审计，当前请求读取授权与历史仓库身份分开。0171七分区原契约不改，新八分区尚未公开接线。
- **79235 exit0：80 passed/163.89s**，新补偿及原入账服务回归通过。初始化发现0175纳入Base后旧夹具重复建表，已改为checkfirst；篡改不存在的request/line被真实外键提前拒绝，测试改为明确验证外键拒绝和原请求可恢复，未削弱约束。失败记录：11157 exit1，6 passed/36 errors；63455 exit1首次定位；74291 exit1，23 passed/1 failed。日志`return-compensation-v63-{focused,first-error,after-fixture,final-focused}.log`。
- 前向0176编译器只在隔离PG16安装：新表仅SELECT/INSERT、4新私有函数、不可变及延迟审计约束；正式head仍0175，未注册Base/HTTP/页面。新增`--return-compensation`复用三件两批回仓轨迹，观察正式0175准入后再安装新约束，末态明确runtimeAdmission=false。
- **71337 exit1**，数量库`run-ejfv2gui`已停止。正式0175流程/退回入账已走通，新约束首次安装/目录回滚已完成；回放cursor捕获的DDL时`%ROWTYPE`被重复转义为`%%ROWTYPE`，未进入补偿正向提交。已在捕获处还原驱动百分号转义并要求无bind参数，不修改业务守卫。失败日志`return-compensation-v63-native.log`保留。
- **30420 exit0**：数量`run-6ffoteai`、SN`run-xft14ixu`均stopped/passed/serverExitCode0；每库1963项受检源与封存点逐字相同。真实三件两批回仓，分别逐笔补偿，总量3；库存及原需求事实不变。同源并发观察精确PG阻塞者后拒绝重复，撤权后新请求拒绝、原key/trace在READ ONLY恢复。缺审计/改数量/版本/操作者/证据摘要的直接SQL被数据库拒绝且全量回滚，API及migrator不可变检查通过。
- 两模式完整前后目录及19条固定DDL逐字一致，安装→回滚→固定SQL重放验证通过。最终日志`return-compensation-v63-native-final.log`；收据`return-compensation-final-v63-20261006.json`，最终完整源`original-integrated-source-v63-20261006.json`2724项，较v62新增8项、修改2项、无删除。323个旧冻结迁移/支持源完全不变，Python编译/diff检查通过。**本轮所有句柄均终态，无需继续轮询或重跑同版范围**。
- 后续仍需补偿数量进入完成覆盖、版本化八分区、独立关闭及同线正常入账/拒收混合场景，再冻结正式0176、ACL/readiness、往返留存、HTTP/PC/H5。未履约取消与退回补偿同单的新取消证据版本也需实现。具体接点见`REJECTED_RECEIPT_RETURN_IMPLEMENTATION.md`末节。完整V1/UAT/真实渠道/远端CI/性能/迁移/三日对账/灾备与发布仍独立待证；无提交、推送、部署或切流，目标active。

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

## 2026-10-06 v49 取消与撤权竞争：数量/SN正式PG16补证通过

- 上轮为progress：v48数量/SN实际本人收货入账和0171门禁已终态。当前只扩展`backend/tests/pg16_material_request_remaining_cancel_gate.py`，不改业务/迁移/权限或前端。
- 复用正式0171部分履约轨迹，增加撤权先提交的HTTP/直接API角色INSERT拒绝、取消先取得锁的撤权等待、撤权提交后原命令只读恢复。使用实际PG连接及`pg_blocking_pids`核对精确阻塞者，临时撤销仅发生在自建隔离集群。
- 首轮35431 exit1：撤权优先HTTP403及直接INSERT权限拒绝均实际发生，测试误期待SQLSTATE23514，实际正确返回42501/`0171 explicit requester deny`。已精确修正断言，并将直接SQL样本时间改为合法CURRENT_TIMESTAMP；业务/迁移未变，失败日志保留。
- 第二轮82223 exit1：两种先后顺序的精确PG锁均已观察；撤权后新键提交正确403，但旧重复提交检查还期望409。已区分为撤权时403，恢复测试授权后重复新键409，保留两条独立检查。
- **最终27339 exit0**：数量`run-t700ibsl`、SN`run-mxli7zck`均stopped/passed/serverExitCode0；每库1869项门禁源码与当前逐字一致。撤权优先HTTP403、直接API角色INSERT 42501，以及取消优先撤权等待三条精确阻塞者证据均通过。实际先入账1、释放1、再取消1，随后独立关闭；已提交库存与原业务事实不变，完整运行准入和留存部分释放拒降通过。
- 撤权后原键原内容仍返回原取消事实，变内容409，新键403；写停用+READ ONLY的状态/原命令GET保持200且全表不变。恢复隔离测试授权后，新键仍因已有取消返回409，未放宽重复业务检查。此补证范围是工程师`material_request.cancel`全局权限目录由allow变deny，不能据此声称每一种身份/组织变更都完成新并发验证。
- 汇总`remaining-cancel-authority-final-v49-20261006.json`；完整源码`original-integrated-source-v49-20261006.json`2630项，仅上述门禁文件较v48改变。v48最终485项前端/构建清单仍完全匹配，复用其实际PC/H5证据。编译及git diff --check通过；所有v48、35431、82223、27339句柄终态，不再轮询或重跑同版本已通过范围。
- 并行只读核对目标118.31.37.87：新候选目录存在，但根`.env`/`.env.production`不存在，未见017x迁移文件；候选仅DB容器healthy，实际API/Web来自`/opt/star-oam`旧目录。见`deployment-presence-v49-20261006.json`。这些根文件缺失不代表所有外部注入配置不存在；没有读取/复制旧站凭据或修改服务器。
- **接续优先**：持续PC/H5入口的候选配置与部署准备，以及拒收退回/补偿闭环。拒收物资仍在原发运在途账户，不能伪造个人仓入账再套普通退回；需要独立真实退回接收、库存/SN守恒和原批准明细补偿依据。其余完整V1、当前版本全套回归/远端CI、真实渠道/UAT、500用户、期初/历史迁移、三日生产对账、备份恢复/RPO/RTO与发布仍独立待证。未提交、推送、部署或切流，目标active。

## 2026-10-06 v48 PC/H5本人收货与入账已接通，数量/SN真实浏览器门禁通过

- 上轮progress：v47真实取消和正式0171门禁已终态。本轮完整重读基线678行（365–430截断补读），读取最新交接及AGENTS，保留当前分支/全部diff。无云端操作。
- 新增`myFulfillmentContract.ts`、`myFulfillmentAdapter.ts`、`myFulfillmentRecovery.ts`、`FormalPersonalFulfillmentPanel.tsx`及3个契约/组件/夹具文件。复用正式my-receiving、candidates、my-receipts、my-inbounds及原key+trace只读恢复接口；不改后端业务/迁移/权限。本人包裹分页、物料名称与数量、数量/SN逐件验收、精确SN/二维码匹配、异常文件上传、独立入账、原请求持久化和未知结果只读恢复已接入PC/H5。
- 提交前重读本人身份/权限、候选数量/SN/版本；先持久化原键/trace/内容哈希再POST，仅一笔。恢复同时核对原键和原trace返回、原输入/哈希、当前需求及身份/权限不变后清理。网络未知/迟到响应/页面离开/存储异常保留；不盲重发。与其他履约、取消、关闭写互锁，列表可打开原待核验需求。
- 工程师不再加载管理shipments/receipts/inbound/OAM来源账户读，使用现有总部/区域角色投影门控；旧管理请求未核验时仍挂载其恢复面板。个人入口对本人数据服务端授权，未扩大工程师权限。异常上传失败原坐标重试按钮保持可用，业务提交在文件确认前阻断。
- 现有页面/适配器92项通过（77790），新增初轮24项通过（56860）；最终4文件118项通过（45734，15.88s），包含工程师不读管理来源账户与上传失败重试回归；16预览边界通过（44385，7.15s）；最终tsc64712、warehouse构建28539 exit0。git diff --check通过，未新增外部依赖。
- 新增原生runner显式`--browser-personal`，与其他浏览器模式/partial-release互斥。原申请人真实浏览器收货+入账，只开放当前单两POST和精确读取；要求只有my-receipts201、my-inbounds201各一次、key/trace恢复GET200、无业务读取错误，后续复用已有真实余额/SN/通知、独立关闭、运行准入和保留事实拒降。
- **数量13093 exit0 / run-ddwr2evu**：原申请人页面实际验收1，再独立入账1；POST仅my-receipts201、my-inbounds201各一次，原key/trace四次GET均200。刷新后同一入账单`INB-20261006-4540CD0705D4`；批准1=已入账1、七阶段剩余0。业务读取错误0、控制台错误0；H5 body/dialog/viewport均390。正式0171迁移、完整运行准入、留存发运拒降、已有入账退168再升171全业务不变、独立管理员关闭及留存关闭拒降通过，stopped/passed/serverExitCode0，1869项门禁源逐字匹配。
- **实页发现并修复**：全部入账后取消面板将零剩余错误显示为“取消明细不完整”。在已经严格校验数量守恒后，零待取消量显示“没有待取消的剩余数量，业务关闭另行确认。”，不生成空取消请求，原输入非空约束保留。新增组件回归及原输入反例，两文件14通过（92003），最终tsc与warehouse构建65602 exit0。数量截图保留修复前问题，不冒充最终构建。
- **最终SN19622 exit0 / run-ssmfnetk**：H5精确匹配`PG16-CONCURRENT-SERIAL-948EBC7CEFF6413B`后实际验收，再确认个人仓入账；两POST201各一次，原key/trace四GET200，业务读取错误0、控制台错误0。PC/H5刷新后同一`INB-20261006-BB067A3911E8`和原合格SN，最终零剩余提示正确；390无横向溢出。后续独立关闭、完整运行准入、历史不变/拒降全部通过；stopped/passed/serverExitCode0，1869项门禁源和485项最终前端/构建清单逐字匹配。
- 完整源`original-integrated-source-v48-20261006.json`2630项（较v47新增7、修改10、无删除）；汇总`personal-fulfillment-final-v48-20261006.json`。最终图片`artifacts/personal-v48-serial-{pc,h5}.jpg`，两库各自`browser-ui-observations.json`保留实页证据与边界。所有本轮句柄已终态，18087已停止，不再轮询13093/19622或重复启动同一证明。
- 本轮最后一次云端检查：10:35:28既有root SSH连接118.31.37.87 exit0，候选数据库和现有API/数据库healthy；Chrome命令助手目标核实一致。无需用户重登；没有服务器写入。
- **下一步**：接续持续可打开PC/H5的部署配置与验收准备；功能审计仍需取消授权撤销竞争与拒收后的退回/补偿闭环。`material_request_remainder.py`目前把累计拒收全部作为`rejected_unsettled_qty`，普通退回从已入个人仓库存出发，不能用于伪造尚未入账的拒收物资退回。必须保持原批准/验收/在途/库存事实，基于真实退回接收或其他合法补偿依据解除未结数量。完整V1其他功能、全套当前版本回归/远端CI、真实渠道/UAT、性能/期初迁移/三日生产对账/灾备/回滚/上线仍独立待证。
- 未提交/推送/部署/切流，尚非持续可打开环境或真实UAT；完整V1其他门禁仍待完成，目标active。


## 2026-10-06 v47 数量与SN真实PC/H5取消、独立关闭及留存通过

- 已接管现有Chrome命令助手，10:05:35 SSH只读连接目标118.31.37.87成功，现有API/数据库健康。无需用户重新登录。v46最终HTTP数量/SN门禁20086已exit0并更新下节，不再轮询。
- 复用原生runner新增`--browser-cancellation`（只允许单个tracking），先实际批准2、HTTP入账1、释放1，然后以原申请人身份暂停本地18087。精确GET白名单加入取消状态/原命令恢复；当前需求POST只允许cancel-remaining，不能收货/入账/关闭或操作其他需求。边界15项通过（93989 exit0，6.92s），没有扩大产品权限。
- **数量真实浏览器4678 exit0 / run-4dqmhplt**：H5实际取消1.000，随后只读核验原键；PC刷新重开仍同一取消事实519ec718-7bfc-451e-85ec-b60c90c44c50。全程仅1次POST201，批准2=入账1+取消1，7阶段剩余全0；390宽下body/dialog均390。后续独立管理员关闭、旧业务全表不变、运行准入、取消留存拒降通过，stopped/passed/0、1869源码一致。当前为合成身份的本地集成验收，不是真实用户UAT。
- **浏览器发现缺陷**：H5取消成功提示原被alert flex排成多列，已仅扩展原关闭提示CSS为纵向块，warehouse构建76705 exit0。工程师页面还有来源库存403：receipt/inbound/OAM面板调用管理接口，原申请人不具备来源账户read。这是PC/H5本人入口未接通的产品缺口，不能通过授予工程师来源库存权限、吞掉403或冒用管理员来消除。
- **最终SN浏览器69657 exit0 / run-40rh7ys9**：正式0171、真实HTTP发运/收货/入账与部分释放后，H5实际取消1.000；PC刷新重开仍同一记录，原命令GET核验，全程仅1次POST201。最终构建成功提示block纵向、body/dialog均390，控制台error0（权限403另记，未隐去）；随后独立关闭、旧业务全表不变、完整运行准入与留存拒降通过。stopped/passed/0，1869后端门禁源及最终前端构建清单逐字匹配。数量模式与当前后台源也匹配；数量截图为CSS修复前，最终样式证据用SN。所有句柄已终态，临时18087停止，不能称持续在线。
- 完整源`original-integrated-source-v47-20261006.json`2623项，仅较v46修改预览helper、边界测试、runner、样式4项；汇总`remaining-cancel-browser-final-v47-20261006.json`。截图`artifacts/remaining-cancel-v47-final-{pc,h5}.jpg`。v46产品业务代码/迁移及94前端单测未变，复用其聚焦证据；本轮新增15边界检查、warehouse构建和真实双模式浏览器门禁。git diff --check通过。
- **下一项明确缺口**：PC/H5需复用已有后端`my-receiving`、`my-receiving/{shipment}/candidates`、`my-receipts`与`my-inbounds/candidates`、`my-inbounds`及各自command/trace-status。小程序已有`miniprogram/pages/formal-my-receipt`和`formal-my-inbound`、契约/原请求恢复可参考；frontend目前没有调用这些本人接口。以当前角色和作用域展示对应入口，保留管理路径原未知请求恢复；每次收货/入账独立事实、身份/授权/候选前后复核、原键持久化且只读恢复。先补本人收货入账可见闭环，再补取消授权撤销竞争/拒收补偿，不要用隐藏功能冒充完成。
- 本轮没有提交、推送、部署、切流、reset/revert或丢弃；全部本地测试证据不替代其余V1门禁，目标active。


## 2026-10-06 v46 正式0171、取消HTTP与PC/H5已整合，双模式PG16通过

- 上轮为progress：已接管用户现有Chrome命令助手；09:39 SSH只读连接118.31.37.87成功，候选数据库及现有API健康，无需用户重复登录。本轮完整重读基线678行（截断中段已补读），保留原工作树/分支及全部改动。无云端写入。
- 旧句柄28063 exit0：候选数量run-5626x2bi、SN run-04f6xwnn均stopped/passed/serverExitCode0；新增关闭SQL核验批准2.000=入账1.000+取消1.000，catalog完全一致。按该实测目录冻结正式0171，前驱0170；不是重建候选。
- 新增0171迁移、精确前驱/角色/隔离/所有权/ACL/函数/触发器验证、只追加事实与留存拒降、SQLite仅结构工具、两表Base注册和运行只读目录。原关闭目录保持启用并精确叠加取消后写屏障/有效关闭数量/取消审计。沿用原cancel权限，没有新增角色。旧审批状态、line.cancelled_qty、分配/占用累计和库存流水不改。
- completion/cancellation-history读取经原命令/逐行事实/审计验证的有效取消，拒绝与旧整单取消事实冲突。remaining同时检查累计reserved≤approved及净reserved−released≤approved−effective_cancelled。详情返回有效取消、列表/详情停止后续履约动作。列表批量读取取消头/明细存在性，保持原固定查询次数和≤20上限，不以放宽测试消除N+1。
- 新增GET remaining-cancellation、GET cancel-remaining-command-status、POST cancel-remaining。写仍需当前原申请人身份和取消授权；恢复只需当前读权限、原键及准确内容指纹。事务提交失败返回未知/503；状态/查询no-store，不盲重放。
- PC/H5新增取消剩余需求面板，先显示逐行待取消数量，提交前重新核验版本/身份/当前权限/库存阶段。只取消全部剩余未履约量，不冒充任意子集取消。发送前持久保存原键/内容SHA256/身份；网络未知、身份变化、迟到回执或只读核验未找到时保留，不重发POST。确认后详情数量与取消事实双向匹配再清理；取消后禁止新增履约但允许独立关闭，退出详情只解除页面锁，原未知请求可从列表打开。
- **正式DB阶段36208 exit0**：数量run-850b5iqn、SN run-u1_wmhr4均stopped/passed/serverExitCode0、1868项源一致；0171完整迁移/空库往返/运行准入、真实入账1释放1再取消1和关闭、15项反例、SELECT-only恢复、旧业务全表不变、保留取消拒降通过。此为HTTP接入前的阶段证据，不能替代下项。
- **最终HTTP原生20086 exit0**：`remaining-cancel-v46-http-native-final.log`。数量run-gecpf787、SN run-v7gp6rk0均stopped/passed/serverExitCode0；各1869项源逐字匹配。正式0171迁移/空库往返/完整运行准入、真实mounted取消POST201、同键重放/变内容及新键冲突、写停用+READ ONLY状态与原请求GET200、详情有效取消、准确指纹/未观察到语义均通过。before_commit暂停观察另一PG连接确实等父锁，提交后由取消屏障拒绝；真实批准2=入账1+取消1、独立关闭、旧业务全表不变及留存拒降通过。两模式已停，不再轮询/重启该句柄。
- v46源码清单`original-integrated-source-v46-20261006.json`2623项（较v45新增21、移除已改名旧门禁1、修改37），汇总`remaining-cancel-final-v46-20261006.json`。10:05:35再次只读SSH成功，原API/数据库健康；已核对Chrome当前命令助手，未写云端。
- **聚焦证据**：56448 exit0 / `remaining-cancel-v46-http-query-final.log`，63通过（当前取消服务/HTTP/查询，90.18s）；77458 exit0 / migration-unit，44通过（冻结结构工具/权限夹具）；1355 exit0 / head，唯一迁移链1通过；99338 exit0 / sqlite-head-second，完整SQLite head/ORM/权限/降级2通过（166 deselected，183.41s）。之前整合服务第二轮128通过/2失败中的失败已由上述63项修复验证；未变的completion/closure/remainder仍有对应通过证据，最终真库实际取消/关闭再验证。
- **前端证据**：84602 exit0 / frontend-tests-final，面板/主页面/关闭联动87通过；73065 exit0 / frontend-contract，指纹与原请求恢复/非重放适配器7通过，共94项；62010 exit0最终tsc；23199 exit0 warehouse构建。13308为同87项重复选择（最初shell写路径错误导致新增契约文件未创建），不重复累计。最终已创建契约文件并单独验证，所有前端句柄终态。未做新真实浏览器操作，不是持续在线交付。
- **失败已保留**：24850首轮迁移%%ROWTYPE语法失败，改为实测pg_get_functiondef原文后通过；88748服务101通过/1旧边界断言失败，修正累计/净占用反例；83212 SQLite固定表名单漏两新表，修后99338通过；2649查询128通过/2失败（新测试漏limit与N+1），80091再61通过/2失败（测试HQ缺read授予及固定查询上限），均修后56448通过。64693原生已走实际HTTP并发和恢复，但门禁同名original变量覆盖业务快照导致比较失败，修为business_before后重跑20086；未放宽业务检查。95000首次TS夹具缺类型，修后通过。
- **下一项为真实PC/H5验收与持续入口**：现有`pg16_fulfillment_browser.py`的精确GET白名单尚未加入remaining-cancellation/cancel-remaining-command-status；新版页面会读取该状态，先补精确当前需求URL和边界测试。复用原生履约runner，在实际部分履约释放后增加显式浏览器取消阶段，使用原申请人；由浏览器实际提交取消并刷新PC/H5，禁止在已取消轨迹上再盲重放。现有--browser-tail与--partial-release互斥，不可直接混用。总部/区域关闭仍独立，不冒充原申请人。再补取消自身授权撤销并发证明。
- 拒收补偿和完整V1其余功能、真实短信/微信/KMS/OSS、远端CI、真实用户UAT、500用户、连续三日生产对账、历史迁移/期初、备份恢复RPO/RTO、回滚/TLS与正式上线仍分别待证。未提交/推送/部署/切流；禁止reset/revert/丢弃，目标active。

## 2026-10-06 v45 剩余需求取消事实与双模式数据库候选验证通过

- 上轮归类progress：0170剩余释放与四个双模式原生门禁已完成。本轮重新完整阅读正式基线678行与v44交接，保留原分支/HEAD和全部未提交改动。未重复启动旧句柄，没有云端操作。
- 新增 `material_request_remaining_cancel_{schema,schemas}.py` 与 `formal_services/material_request_remaining_cancel.py`。命令明确取消全部剩余未履约数量，申请人提供当前版本、逐行准确正数量及原因；逐行核验批准/入账和七阶段，未释放占用、未出库拣货、在途、待入账、拒收或开放供给/替代均阻断。沿用当前原申请人的唯一person技术员cancel权限，重读授权和父需求锁，不能由管理员冒充申请人。
- 独立头/逐行不可变取消事实记录原请求HMAC、输入哈希、补偿证据SHA256、审计和原分配/占用/释放/拣货/发运/验收证据ID；不改旧审批状态、旧取消投影、原分配累计额度或库存流水。当前读权限下可只读核验原请求，撤销cancel权限后仍可恢复；同键不同内容、篡改、版本变化拒绝。
- **当前仍是未激活前向候选**：新表没有注册Base，没有0171 Alembic版本，没有挂载API/PC/H5。当前正式head仍0170，正式completion/query/closure尚未消费新取消事实；不能称已完成产品部分取消或结单。先证明数据库约束，再冻结迁移/运行目录并接入正式读取，不能通过表存在判断或放宽旧投影绕过迁移。
- `55613 exit0`：新服务27项通过 /16.79s；含真实原审批/占用/释放服务夹具（库存过账是合成单测）、原事实不变、数量/权限/幂等/篡改和SELECT-only恢复。6个候选SQL/PLpgSQL函数语法解析通过。
- 复用已有 `run_local_pg16_fulfillment_checks.py --remaining-cancellation-candidate`，自动走两件批准/占用、一件实际HTTP发运入账、另一件实际释放；先通过完整0170迁移、运行准入和留存释放拒降，再只在自有本地PG16安装 `backend/alembic/remaining_cancel_0171/candidate.py`。记录安装前后真实catalog供冻结；API只有新两表SELECT/INSERT，函数私有，UPDATE/DELETE/TRUNCATE由数据库拒绝。原关闭写屏障扩展为取消后禁止继续履约，独立物流/通知/OAM/对账轴沿用原例外；关闭入口本身不被该屏障禁止。
- 首轮 `15660 exit1`：PostgreSQL指出authority的person_id参数与users.person_id歧义，未成功提交取消。已改checked_person_id，同时补created_by_user绑定、当前人员组织树锁前后比较、选定assignment的cancel+read权限；保留失败日志 `remaining-cancel-v45-native.log`。
- 第二轮 `17070 exit0`，日志 `remaining-cancel-v45-native-second.log`：数量run-13w_a4rj、SN run-__26x0pn均stopped/passed/serverExitCode0，1855项源码逐字一致。两模式真实入账1、释放1后提交取消剩余1.000；原业务全表不变、原键重放/READ ONLY恢复、9项伪造插入及6项取消后写/篡改反例拒绝。安装前后catalog在数量/SN间完全一致（3个变化表、7个新增/变化函数），可供正式冻结。候选scope明确unactivated，安装后runtimeAdmission=false；完整0170运行准入发生在候选安装前。
- 本轮所有测试已终态，勿再轮询旧句柄。完整源码清单 `original-integrated-source-v45-20261006.json` 2603项（较v44新增6、修改1，修改为复用原生门禁增加显式候选模式），汇总 `remaining-cancel-final-v45-20261006.json`。git diff --check通过。正式产品head与现有读取/页面仍保持0170，不能把新候选真库通过等同于正式可用。

- **紧接着做正式整合**：数量/SN终态及catalog一致性已通过；补并发取消与继续履约/权限撤销、私有函数ACL及留存拒降证明；冻结0171结构/SQL/准确前驱、迁移与运行目录、SQLite仅结构路径。正式有效取消读取需合并旧整单取消与新逐行事实，不改0037/0045旧事实；remaining.partition要保留原累计reserved≤approved，并改净占用reserved−released≤approved−effective_cancelled。closure SQL与service以有效取消量逐行核对；query/UI显示和取消后写保护须接通。**不得只增加取消按钮或把本候选测试当成已发布能力**。
- 此后继续拒收退回补偿、完整V1其余功能及持续PC/H5入口；真实渠道/云配置/UAT/远端CI/500用户/三日生产对账/迁移期初/灾备回滚/正式上线仍各自待证。没有提交、推送、部署、reset/revert或丢弃改动，目标active。


## 2026-10-06 v44 部分履约后释放剩余占用与整合双模式PG16通过

- 已接管用户现有Chrome命令助手，确认杭州Ubuntu-ipvk / 118.31.37.87；09:11:55实际SSH root只读连接exit0，候选db、旧API/db及edge-api健康、旧web运行。收据 `chrome-takeover-v44-20261006.json`。当前无需用户重复登录；没有云端写入、重启或部署。
- 占用释放改为逐原始占用核验已释放和已拣货事实，剩余=原占用−已释放−已拣货；SN排除全部已消耗SN，已冲销/历史不完整拒绝。允许同需求已有出库/发运/入账时释放另一未拣货部分，保留原占用、分配、库存流水与下游状态。候选读取沿用当前授权和历史证据核验。
- 新增正式前向0170（严格前驱0169），冻结0070原释放守卫，只移除整单出库状态限制并增加已拣货数量扣减；既有0071数量/SN图约束保留。迁移检查PG16直接迁移角色、最小权限、隔离级别、准确head及函数旧内容，保持OID/owner/ACL；有拣货后释放事实禁止降回0169。运行时目录/readiness和日终入口同步0170，不改历史迁移。
- 首轮真库14475 exit1：数量实际晚释放已提交，但原请求只读恢复调用审计FOR UPDATE，触发ReadOnlySqlTransaction。已修为状态恢复走完整只读审计快照，写入同键恢复仍保留原锁；追加禁止ORM写入/行锁的回归。初轮聚焦76304 exit1为0070历史测试错误比较最新守卫，改为精确核验冻结before和当前after；历史SQL仍解析。最终99939 exit0：84 passed、1个SN专属项在数量模式skip /56.82s。新迁移3项及唯一head1项之前分别通过，不与84重复累计。
- 53408 exit0：部分释放数量run-iwvbknez、SN run-c6tqjs3v均stopped/passed/serverExitCode0，1849项源码一致。真实批准2/占用2/拣货发运入账1/释放剩余1，原请求READ ONLY恢复、余额和SN位置、完整运行准入、留存释放拒降及拒降后全部事实不变通过。此证据为门禁入口版本同步前；后续仅清理一个未使用import并同步17个当前版本入口/测试，未改此业务逻辑。
- 已将当前PG16/报损/退回/日终门禁head同步0170。0169权限迁移独立步骤、历史迁移与未知未来head拒绝仍保留；旧事实升级先验证0169权限增量，再验证0170不变。71910 exit0：聚焦权限/CI拓扑/日终/迁移135项通过 /42.15s。
- **最终整合终态**：4435 exit0 原有完整履约/关闭数量run-8xlkb9h0、SN run-xc9rmglm；72608 exit0 部分释放数量run-lrr4aypo、SN run-lklkln1z。四库均stopped/passed/serverExitCode0，1849项后端/scripts/deployment/CI源码逐字一致；正式0170迁移、完整运行准入、空库/已履约往返及留存事实拒降通过。原关闭反例/并发/原请求核验继续通过。日志分别为 `partial-release-v44-fulfillment-regression.log` / `partial-release-v44-integrated-native.log`。
- 87106 exit0：SQLite完整head/ORM/降级与PG16 head一致性2项通过、166 deselected /121.27s，日志 `partial-release-v44-sqlite-head.log`。本轮所有任务已终态，不再轮询旧句柄。完整源 `original-integrated-source-v44-20261006.json` 2597项（较v43新增6、修改27）；汇总 `partial-release-final-v44-20261006.json`。当前542项未提交状态保留，git diff --check通过。新head尚未重跑全部报损/退回/日终真库长门禁和远端CI；本轮未做新PC/H5浏览器验收。

- **仍未完成实际部分取消**：释放只退回原可用库存，不恢复累计分配额度，不追加取消事实，不满足剩余批准数量的结单覆盖。下一步独立补偿取消需衔接0037/0045/0068历史约束与0170释放证据，保留已发运/入账事实；拒收必须先完成退回/补偿。原有完整V1、持续PC/H5入口、真实渠道/配置、UAT、远端CI、500用户、生产三日对账、期初迁移、备份恢复/RPO/RTO和发布仍分别待验。
- 沿用原工作树/分支/HEAD，全部未提交改动保留。没有提交、推送、reset/revert或丢弃改动；目标active。


## 2026-10-06 剩余履约逐行核对、双模式PG16与PC/H5已通过（v43）

- 本轮重新完整阅读678行正式基线，核对v42交接及原工作树；未提交改动从529项继续保留。Chrome接管后北京时间08:51:31已有root SSH连接成功，候选db与旧API/db健康。上轮归类为progress（得到当前连接证据、解除连接阻塞）；目标保持active。
- 新增 `material_request_remainder_schemas.py` / `formal_services/material_request_remainder.py` 与受当前read权限约束的 `GET /remaining-fulfillment`，只读、no-store。对每条最终批准明细独立划分未占用（含已释放）、占用待拣货、拣货待出库、出库待交运、发运待验收、验收待入账、拒收待处理；与已取消、已入账相加必须恰等于批准量。逐父记录校验累计数量和因果版本，核对历史命令、库存事实和审计；读取期间身份/范围/版本变化或事实不完整则拒绝整份结果。
- 占用释放历史验证增加 `lock_audit=False` 只读方式，默认写流程保持原行为。前端复用现有结单核对卡片，显示七阶段及开放补货/替代数；两份核对必须属于同一需求版本/修订且入账、取消一致，失败清除旧结果，迟到响应不覆盖新版本。不新增取消按钮；未占用包含已释放但尚有原分配的数量，**不是可以直接取消的授权或数量**。
- 后端新增首轮28项通过，完整聚焦回归83项通过/46.30s；前端最终5文件120项通过/9.75s，正式TypeScript/warehouse构建通过。保留首次调用npm缺失日志（改用已安装node_modules/.bin），首次TypeScript因测试fixture未声明类型失败，修后通过；未安装依赖或换技术栈。
- 当前正式0169 PostgreSQL16数量 `run-1qvqhr95`、SN `run-p0v1yp53`（句柄15537 exit0）均 **stopped/passed/serverExitCode0**，各1843项源码逐字一致；8个实际履约阶段以API角色READ ONLY核验分布，最后HTTP200/no-store。原关闭/并发屏障/运行准入、空库及已有事实升降级、留存关闭拒降继续通过。复用既有迁移，没有新增迁移。原生轨迹每次单个单位，混合阶段/部分释放为本地服务测试；不宣称完成原生部分取消。
- 独立实际浏览器 `run-5jqr50da` / 61333 exit0：H5关闭POST201恰1次，PC刷新重开仍显示同一关闭结果；5次剩余数量GET全部200，批准=已入账1.000、七阶段全0。PC body/viewport1366；H5 body/viewport390、卡片client/scroll330；控制台error0。截图 `artifacts/remainder-v43-{pc,h5}.png`，可见证据 `browser-remainder-evidence.json`。472项前端源码/构建匹配，1843项后端源码匹配，集群stopped/passed/serverExitCode0。临时18087和测试标签已关闭，非持续在线入口。
- 完整源码清单 `original-integrated-source-v43-20261006.json` 为2591项（较v42新增5、修改7），汇总 `remainder-final-v43-20261006.json`。日志 `remainder-v43-{backend,frontend-final,build-final,native,browser}.log` 均在 `artifacts/formal-0165-integration/`。全部任务终态，不再轮询旧句柄；最终532项未提交状态保持。
- **下一项必须进入实际补偿取消**：本批为事实读取和显示，不是部分取消实现。0037取消事实要求整行全部取消且中性履约轴；0045审批/明细守卫禁止批准中非零取消；0068及allocation服务按原分配累计与批准减取消校验。仅减cancelled_qty、仅释放占用或只在页面放行都会破坏历史/数量约束。需要独立前向迁移、不可变补偿事实、原分配剩余与释放关联、当前取消/库存权限及父需求锁下原子验证；保留已入账和已交运事实，拒收先完成退回/补偿。不要修改历史迁移，也不要重复搭建数量预览框架。
- 全量V1、持续可访问入口、真实渠道和UAT、远端CI、500用户、连续三日对账、期初迁移、备份恢复RPO/RTO和回滚/正式发布仍按v42逐项待验。本轮无提交、推送、云写入、部署、切流、reset/revert或丢弃改动。

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

## 2026-10-06 04:54 已接管 Chrome，已有 SSH 连接验证成功

- Chrome 当前为杭州 Ubuntu-ipvk / 118.31.37.87 的命令助手；已回读最近诊断 `t-hz06z5l6mfdhfy8`，04:10:40 exit0，SSH 服务和22端口正常。无需用户再次登录控制台。
- 04:54 重新测试 `admin` + 已有专用密钥仍返回 `Permission denied (publickey)`。核对原 checkout 的 `cloud_oam/deployment/offhost_backup/pull_once.py:91`，其固定账号实际为 **root**。沿用同一密钥、BatchMode/IdentitiesOnly/StrictHostKeyChecking，用 root 登录成功，exit0；返回主机 `iZbp11p1r66g3dq6h25c6qZ`，服务器时间 `2026-10-05T20:54:14Z`。不要继续使用 Workbench 标签中的 admin 账号推断旧备份 SSH 配置，不要再把当前阻塞记为 SSH 不可用。
- 第二次独立 SSH 只读检查于 `2026-10-05T20:54:29Z` exit0：候选 db healthy，旧 star-oam API/db healthy，旧 web 继续占用80/443；候选根目录存在，`.env` 与 `.env.production` 均不存在。这只证明访问通道和上述状态，不代表迁移/ACL/应用上线验收完成。
- 未重置密码、添加/复制密钥、修改服务器权限/配置、重启、部署或切流。后续使用原备份脚本同一 root SSH 连接继续精准只读核查及既有上线门禁；全部未提交改动保留。

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

## 2026-10-06 明确0164回滚维护版，需求履约概览查询已准备

- 上轮88893的实际根因已定位：0165/0167给旧表增加的列在降级后留下3个合法PostgreSQL已删除字段槽，原Git0164的0159启动检查把任何 `attisdropped` 视为错误。原失败库 `run-sipe7qr4` 保留；只在核验二进制SHA、准确data目录、systemIdentifier、子PID、私有socket/无TCP后恢复为全局只读，未做迁移/业务写入，最后正常停库；没有把原失败记录改成通过。
- `rollback0164-readonly-zkymlpzg/receipt.json`：同一只读失败库，**原Git1421项 exit1，明确维护版1421项 exit0**、完整旧应用启动通过。维护版仅修 `stock_loss_correction_security.py` 中正常已删除字段槽的判断，其余1420项逐字保留；完整可见列/约束/FK/索引/ACL/属主/RLS校验仍执行。不是生产恢复证明。
- 历史提取器增加显式 `--rollback-compatibility`，只允许0164；原Git生成历史与回滚维护源分别保留。维护profile `0164-dropped-column-compat-v1`、manifest `8e64c916054a4212cc946ed5ccb4bf22fa5ba8af28d26a020d2884f3efed7743`、维护归档SHA `8027b73828ea5cd7d1bc66e678b4b604f52843a95c2bac9e191e07bef391ce10`。`source.tar` 是原Git基础归档，真正维护源归档为 `rollback-source.tar`。固定目标 `artifacts/formal-0165-integration/rollback-0164-compat-v1`。来源差异收据 `rollback-pair-proof-v16.json`；发布说明 `AUDIT_LOCK_SCOPE_0164_RELEASE_NOTES.md` 已补边界。尚未构建/发布维护镜像。
- CLI与CI显式增加 `--rollback-source`，不能冒充 `--predecessor-source`：原版先在升级前完整启动，再生成真实旧业务；回退后使用唯一维护修补，双方来源在前后重新逐字验证。**91157 exit0：72 passed /28.09s**（打包隔离、篡改拒绝、CLI参数及完整CI拓扑），日志 `rollback-source-focused-v16b.log`；首次13039因本地命令遗漏 `PYTHONPATH=backend` 在fixture阶段失败，未当作产品失败或通过，保留原日志。
- 当前原仓源码绑定 **v16 /2513项**，相对v15精确7文件变更，本轮已全量核对无漂移；候选仍v10 /2511项，未改64509源码。新真实原生句柄 **43247**：`git-history-maintenance-rollback-v16.log`，数量库 `run-8k3m3nab`，已验证原Git在升级前完整启动并生成11个真实旧请求，整轮数量/SN仍待终态。继续原句柄，不重启或改动原仓固定源码。
- **64509数量整轮已核验通过**：`run-u0kb4tnb` stopped/passed/serverExitCode0，1785项门禁源与2511项候选源均无漂移；真实完整应用HTTP/API角色23次提交、12动作、原请求恢复、2个缺失请求封存/迟到写入拒绝、附件绑定和留存事实拒降通过。仍为注入身份配合真实当前权限、FakeStorage，不是短信/OSS/UAT。收据 `condition-formal-http-quantity-final-v10-20261006.json`。同句柄SN `run-w1cmspcb` 仍在运行，不把数量通过扩大成双模式通过。
- 等待期间只在有限 `demand-overview-stage/` 准备5个新文件（schema/service/router/2测试），没有复制完整候选或改动活跃门禁源码。需求概览单条SQL同时统计10独立状态轴与三级审批最新尝试；沿用当前总部/区域需求read范围、前后撤权复查，排除仅本人与外部审批身份，筛选为创建时间半开区间，不返回联系人、不推断库存数量。
- **94825 exit0：6 passed /2.78s**，SQLite真实业务覆盖跨区域隔离、完整审批不推进入库、退回重提不重计、空结果、时区/边界与读中撤权。**22317 exit0：11 passed /3.69s**，另含5项ASGI适配器（服务测试替身）只读/no-store/错误脱敏/rollback。阶段加载器先加载正式conftest，日志 `demand-overview-stage/tests-v3.log`；最初v1缺测试环境配置的加载失败已纠正。不等于普通入口/PG/PC-H5通过。
- `demand-overview-stage/integration-plan-v1.json` 保存5文件SHA、目标均不存在及main.py expected-before。**尚未合入/挂载概览接口**；43247终态后按计划整合，补主应用框架401/404/405/422隐私响应、普通测试、PG实际聚合与PC/H5页面。完整报表的逐SKU数量、Excel、其他报表以及完整V1其余范围继续保留，不能用单量统计代替全部报表。未提交、推送、部署或切流，目标active。

## 2026-10-06 04:10 Chrome 接管与 SSH 只读诊断

- 用户要求接管已打开的 Chrome 命令助手；已在目标 Ubuntu-ipvk / 118.31.37.87 执行 `rsc-readonly-ssh-diagnosis-20261006`，执行ID `t-hz06z5l6mfdhfy8`，04:10:39–04:10:40，退出码0，完整输出已回读。
- ssh.service 与 ssh.socket 均 active，IPv4/IPv6 的22端口监听正常；服务器本机读取到 OpenSSH 9.6p1 握手。仅证明服务器本机 SSH 可用，不代表外部 SSH 超时已解决。根盘剩余32.47 GiB。
- 候选目录存在，`.env`/`.env.production` 仍不存在；候选db healthy，旧 star-oam API/db healthy、web继续运行。未重启服务、修改权限/配置、迁移或切流。截图 `artifacts/aliyun-ssh-readonly-20261006-041040.png`。
- 同轮接续既有本地句柄：64509仍运行；**88893 exit1**，不可继续写为运行中。真实Git0164历史已完成0166/0167升级、十一原请求回查及降级0164，但随后旧0164应用启动校验失败，错误 `preserved 0164 startup failed; inspect owned gate log`。现场 `artifacts/local-scrap-legacy-history-pg16/run-sipe7qr4`，日志 `artifacts/formal-0165-integration/git-history-native-v15.log`。尚未诊断根因，SN未完成，不得宣称整轮通过或重写历史来源。
- 接下来保留失败现场并定位旧应用启动错误，继续原64509；真实云资源与部署配置缺口仍待补齐。此次仅补交接记录，未改两项门禁绑定的业务源码。

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

## 2026-10-06 03:47 Chrome 命令助手重新接管

- 按用户指示接管其已打开的 Chrome，目标 Ubuntu-ipvk / 118.31.37.87。新的只读命令 `rsc-readonly-takeover-20261006-current`，执行ID `t-hz06z5j567wh534`，03:47:47回执 exit0；没有重启、迁移、修改配置或切流。
- 实读候选目录 `/opt/rsc-pilot-20261004-fc7c926-dirty` 存在，`.env` 与 `.env.production` 均不存在；候选仅db容器运行且healthy。旧star-oam API/db healthy、web运行五周。不能据此宣称新应用上线、真实渠道或迁移验收完成。
- Chrome命令助手可用，当前不需要用户再次登录。截图 `artifacts/aliyun-takeover-20261006-034747.png`。后续需继续补部署配置及既定完整门禁；不读取/回显密钥。
- 同轮真实轮询64509、46976仍运行，未重复启动、未修改其固定源码；当前源代码未因本次浏览器接管改变。

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

## 2026-10-06 02:23 Chrome 命令助手重新接管

- 用户明确指定现有 Chrome。通过原生应用接口接管 Ubuntu-ipvk / 118.31.37.87；无需用户再次登录。只读命令 `t-hz06z5bn9n7hvr4` 于02:23:44完成，exit0。准确候选根目录 `/opt/rsc-pilot-20261004-fc7c926-dirty` 存在，`.env`和`.env.production`仍不存在；候选运行容器仅数据库且healthy，旧star-oam API/DB healthy、Web运行。
- 前一只读命令 `t-hz06z5bjhm62bcw` 错误追加cloud_oam路径，因此路径不存在输出不代表候选目录丢失；已由上述准确根路径核查纠正。本轮未读取凭据、启动/重启服务、迁移或切流；数据库版本与应用验收未重新核验。收据 `artifacts/formal-0165-integration/chrome-takeover-readback-20261006-022344.json`。后续继续安全配置落地与同源门禁，不能将通道恢复视为上线完成。

## 2026-10-06 十二类动作客户端契约与原请求恢复已接入

- 原工作树新增 `returnConditionCommands.ts`、`returnConditionRecovery.ts`、`returnConditionAdapter.ts` 和 `ConditionRequestRecovery.tsx`；恢复面板已挂到 `FormalLossCorrection`。完整原命令发送前持久保存并读回，同一人员/原入库明细的全部动作共用Web Lock和待处理槽；未知结果保留完整请求并阻止替代请求。刷新后只读回查不再核验新写权限/现场来源；明确封存先查询，再单次封存并回读，失联保留记录、不自动重发。UI封存须明确确认，失败关闭旧确认框；找到结果显示原动作当时状态，不能用案件后来状态冒充原结果。
- 关键契约：`original_input_hash` 不等于业务事件的 `request_hash`。由现有候选实际Python规范化函数生成12类合成命令及摘要，保存规范化源码SHA；TypeScript对数量规范化、排序、带域幂等键SHA和完整原输入逐项匹配。样本在 `frontend/src/test-fixtures/return-condition/`，不是业务COMMIT证据。所有12动作的保存、失联、撤写权后回查、未知阻断、明确封存都有客户端覆盖。
- adapter使用 `requestNoReplay`，完整原命令/原操作人/请求头绑定；提交前只读来源或案件最新事件核验，校验区域/总部动作角色、当前权限、申请人与审批人独立、SN份额及最新事件引用。精确组织权限、实际实物与COMMIT仍由服务校验。**新提交/审批/结算表单及逐件现场输入尚未接入**，不能把这些核心模块算完整操作页。
- 5163 **exit0：9文件153 passed**，日志 `condition-read-ui-stage/recovery-client-v3.log`。57357 **exit0：4 passed**，最后单独增加原结果与案件后来状态区分用例；其中3项与153重复。62122 TypeScript完整检查exit0，仓库版Vite构建exit0，日志 `recovery-types-v5.log` / `recovery-build-v1.log`。中间31648为152通过/1失败：旧父页面历史测试未见读取结果；增加加载完成/当前DOM控件及准确调用同步断言，保留原业务断言后同组通过。初次失败日志保留，不冒充全后端/全前端/真实HTTP/用户验收。
- 原工作树源码绑定推进到 `original-integrated-source-v4-20261006.json`（2433项）；候选v7的2475项逐项hash仍未改变。整合计划推进 `condition-read-ui-integration-plan-v2-20261006.json`（26文件），待60629终态后按expected-before核验应用。收据 `condition-client-recovery-final-v1-20261006.json`。未复制完整候选，未提交/推送/部署/切流。
- 60629本轮实轮询仍live：数量正式默认权限下已完成七次审批实际COMMIT及相应九类缺失请求封存检查，日志推进到cancel_approved实际COMMIT；release/execute/有历史拒降以及SN整轮尚待终态，不记正式全业务通过。继续同一句柄；等待时实现提交/审批/结算表单与区域来源选择，随后按同源版本进行真实PG HTTP与PC/H5联验。完整V1及生产验收范围不变。


## 2026-10-06 成色纠正历史读取与PC/H5入口已实现，等待同源整合

- 新增有限阶段 `artifacts/formal-0165-integration/condition-read-ui-stage/`，不是新的完整候选。公开案件读取复用完整原入库/成色图证明，前后核验当前组织读取权限和观察边界；响应仅包含有界案件、事件、附件ID及历史份额，不返回原命令JSON、幂等键、来源快照或下载URL。历史冻结、已纠正、未申请份额守恒；审批/释放/执行状态与流水分开。新增路由计划为 `GET /api/v1/stock-operations/loss-reports/return-condition-corrections/history/{inbound_line_id}`，始终rollback、不COMMIT，错误脱敏并no-store。**尚未挂到候选或原应用**。
- 原工作树前端已接入异常入库明细的“查看成色纠正历史”：从已核验明细选择，无手填UUID；客户端校验来源/物料、完整事件链和末事件/状态/份额一致，读取前后检查身份权限；账户/版本变化丢弃迟到响应、失败清除旧结果、无自动重试。界面明确“已批准，待执行”和“已取消/拒绝，待释放冻结”，不冒充当前库存。此入口现阶段尚无同源后端，不能称为可验收上线页面；提交、审批、结算、原请求保存/恢复页面仍待接入。
- 后端阶段11707：数量、SN两项真实服务历史读取通过（SQLite、显式合成授权、FakeStorage），HTTP测试受到上游autouse夹具缺少stock参数影响，整轮exit1；已把HTTP测试拆成独立模块。57545 **exit0，2 passed/2 deselected**，实际ASGI读取权限/非法参数/SQL与附件完整性错误脱敏/no-store/rollback通过。中间v3附件反例分类参数写错，v4在回调外构造真实FormalFileError后重新通过；只引用v4。不是PG HTTP真实事务验收。
- 前端79514 **exit0，4文件55 passed**；31532 TypeScript完整检查exit0；仓库版Vite构建exit0。日志 `client-tests-v1.log`、`types-v3.log`、`build-warehouse-v1.log`。修正Mac大小写模块解析冲突及从原报损明细取得material_id的类型错误，未放宽校验。前端样本明确为合成传输/UI夹具，不是UAT。
- 服务候选v7的2475项源逐项hash不变；本轮原工作树仅新增5份前端文件、修改4份前端文件。原源绑定推进为 `original-integrated-source-v3-20261006.json`（2423项），不再把v2当原树现状。阶段绑定7文件；收据 `condition-read-ui-final-v1-20261006.json`，整合计划 `condition-read-ui-integration-plan-v1-20261006.json` 共15文件（6后端含挂载补丁、9前端），含每项原候选hash，需60629终态后应用。
- 60629已实轮询仍live；数量日志已通过old-business/current-upgrade/formal-condition-upgrade及正式升级保留旧事实，仍未整轮终态。继续同一句柄，不改其v7源码、不重复启动。之后先完成案件读取/客户端同源整合与普通测试，再补真实PG HTTP提交和PC/H5动作及请求恢复；其余正式V1、远端CI、渠道、UAT、性能、三日对账、灾备/回滚仍为完整目标。未提交、推送、部署或切流。


## 2026-10-06 数量/SN完整候选联验通过，0167与HTTP已原地整合并验证普通入口

- **64414已exit0**：数量 `run-vy639gg7` 与SN `run-_l9qjsoq` 均实读 stopped/passed/serverExitCode0，分别1754项门禁源码及同一v6候选2453项源码hash无漂移。两模式完成真实初始提交、七次审批COMMIT、九动作缺失请求封存、release/execute、两类结算封存、晚到写入阻塞/拒绝和后续业务后的旧请求/封存SQL只读回查。收据 `condition-complete-native-dual-final-v6-20261006.json`。仍为FakeStorage与候选测试授权，不能替代正式0167默认权限联验；不再轮询或重启64414。
- 按 `condition-formal-integration-plan-v4-20261006.json` 逐项验证后，**46文件原地合入现有服务候选**，没有复制新的完整候选；覆盖前的24份文件原字节保存在 `condition-before-0167-files-v6/`，整合日志 `condition-formal-integration-applied-v1-20261006.json`。新增22文件，服务候选绑定推进为 `condition-settlement-service-candidate/source-binding-v7.json`（2475项）。原工作树2418项源仍未变，原HEAD/未提交工作全部保留。
- HTTP已挂载至候选完整应用 `/api/v1/stock-operations/loss-reports/return-condition-corrections`：`POST /commands`、`POST /request-lookup`、`POST /request-seal`、`GET /sources/{inbound_line_id}`。12种动作按当前权限分流；回查只需读取权限；原操作人/完整输入/请求头/来源与结果严格绑定；验证响应后单次COMMIT，未知结果不自动重发。公开封存结果不返回原命令JSON或幂等键；来源待核实时份额为null，预览不授权过账且及时rollback释放锁。
- 阶段接口 **49620 exit0：218 passed /4.23s**，收据 `condition-http-adapter-stage-final-v2-20261006.json`；先前209/216/217轮是被后续覆盖的阶段检查，不相加。接口服务结果为明确测试替身，覆盖真实完整应用挂载、未登录401及404/405隐私响应，但**尚无真实PG HTTP业务COMMIT或PC/H5验收**。
- **15088 exit0：266 passed /102.15s**：整合后使用普通pytest入口，权限/模型/运行目录/SQLite/HTTP五模块通过，真实独立子进程注册夹具通过；未用阶段加载器或捕获替身。收据 `condition-formal-normal-focused-final-v7-20261006.json`。不是完整后端或远端CI。
- **57966 exit0**：普通默认CLI在自有PG16 `run-6rd_iski` 全链实际迁移至0167、实际API目录校验、多授DELETE与禁用触发器拒绝、空库降级/再升级、可选对账采集角色配置/幂等/旧head拒绝通过。stopped/passed/serverExitCode0；**1776项完整门禁源与2475项候选源hash均核对一致**。收据 `condition-formal-normal-native-final-v7-20261006.json`。修正了旧CLI依赖Git发现导致隔离候选基础源码漏记的问题；历史38项阶段源收据保留原边界。
- **60629为当前唯一新业务句柄，运行中**：同一v7候选 `condition-formal-business-v7.log`，命令 `scripts/run_local_pg16_return_condition_checks.py --formal`（沿用既有postgres-bin及准确0157前驱路径）。真实0166旧历史升级至0167，核验原字段/旧权限行保留及身份版本推进；不临时补成色动作权限；复用完整数量/SN业务，末尾执行有真实留存事实时Alembic降级拒绝及事实/目录不变。该新轮尚未终态，不能计为正式业务通过。运行期间固定v7源，不再启动重复候选。
- 接下来先核验60629终态，再做真实PG HTTP事务与PC/H5来源选择、审批/结算及原请求保存/恢复；随后按准确通过源码合入原工作树。来源列表/完整案件时间线与PC/H5仍待开发，HTTP不能单独算可用业务页面。其余正式V1业务、最终同源CI、真实渠道、UAT、性能、连续三日对账、迁移/期初、灾备与回滚仍是独立必需验收。未提交、推送、部署、切流；完整目标active。


## 2026-10-06 Chrome页面已确认，可随迁移合入的测试入口已核验

- 本次重新接管用户现有 Chrome，实读目标 Ubuntu-ipvk/118.31.37.87 命令助手及 `t-hz06z50395rrls0` 历史成功回执；没有重复发送云命令。回执来自00:14:12，候选 `.env`/`.env.production` 缺失；不能当成新的服务器探测或迁移验收。当前无需用户再登录。
- **54404 exit0：48 passed /30.65s**。将权限、模型、运行目录与SQLite迁移测试整理为四份正式 backend/tests 文件，扩展既有注册夹具；在有限阶段加载器下通过。**19636 exit0：1 passed /45.27s**，可移植原生门禁脚本在自有PG16 `run-qo2c7xr9` 完成真实Alembic全链升级、API权限/触发器反例、空库往返及可选对账采集角色验证。实读 stopped/passed/serverExitCode0，38项阶段源码hash逐项一致。
- 这次仍使用阶段加载器，普通独立进程注册夹具和默认CLI必须合入后验证。原生检查记录只覆盖38项显式阶段文件，不能声称覆盖全部导入源码；原仓2418项与候选v6的2453项另在收据时逐项核验未变。没有PG业务留存拒降或正式默认授权下完整业务的新证据。
- 收据 `artifacts/formal-0165-integration/condition-durable-entrypoints-final-v1-20261006.json`；阶段清单推进为 `condition-migration-stage/source-binding-v5.json`（54项）；合入计划推进为 `condition-formal-integration-plan-v2-20261006.json`（37文件、19新增）。只合入backend/scripts，阶段alembic.ini与加载器不交付；原工作树及服务候选尚未应用。
- 64414的数量已通过，SN日志推进到永久封存检查，尚无整轮终态。保持同一运行源码，不重复启动。终态后按v2计划原地整合并验证正常入口，再推进HTTP/PC/H5及完整V1余项；未提交、推送、部署或切流。


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

## 2026-10-06 Chrome接管实读与结算封存权限组件终态

- 已按用户指示接管现有Chrome命令助手。新只读命令 `t-hz06z50395rrls0` 于00:14:12完成、exit0；Ubuntu-ipvk/118.31.37.87候选目录存在，`.env`与`.env.production`仍不存在。旧star-oam API/DB healthy、Web运行。迁移容器d显示Exited(0)，本次没有重新验证数据库版本，不能据此宣布迁移验收。收据 `chrome-takeover-readonly-20261006.json`；未读取密钥、未重启或部署。
- **48156 exit0：1 passed /13.24s**。结算execute/release纳入权限组件，原生PG16实际身份十表、当前动作权限及提交时到期拒绝、只读权限与私有函数直接执行拒绝通过。run-ccpo1dpw stopped/passed/serverExitCode0，三个源码hash复核无漂移；收据 `condition-settlement-closure-authority-final-v1-20261006.json`。测试探针和合成人员不替代历史来源、正式登记器或完整业务验收。
- 39441、45855原句柄仍在运行。39441数量日志到实际API release COMMIT通过；45855数量/SN执行/释放四场景均有通过标记，既有审批两项及整组终态仍待收齐。不得重复启动或修改运行源码。
- 下一步保持15文件整合前置条件，收齐两原进程终态后再核验合入；继续正式迁移、权限目录、HTTP与PC/H5及完整V1门槛。无需用户再次登录；未提交、推送或切流。

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

## 2026-10-05 集成current-head通过，后续审批原生启动（接续入口）

- 独立集成目录 **1487 exit=0**：run-3j3mcabm已实读stopped/passed/serverExitCode=0，实际head0166；2个capture角色配置后完整API安全验证、10类权限反例与精确恢复及后续current-head检查通过。此轮运行正常候选源/脚本，不是内存替换；2418项源码逐项hash无漂移。收据 `capture-current-head-integration-final-v1-20261005.json`。不要再轮询1487。
- 同一固定集成源码启动 **9441**：`scripts/run_local_pg16_return_condition_checks.py --decision-candidate`，默认数量/SN双模式；日志 `capture-integration-candidate/repository/cloud_oam/condition-decisions-native-integration-v1.log`。先完整初始提交/封存，再实际COMMIT后续7个审批动作、晚撤权/效果回滚和精确回查。尚未终态，不计审批通过。
- 当前仍应跟进原84788、集成静态99385/93201/20115以及新9441共5个句柄。两份非文档源码保持固定，不重启旧库，不把候选通过当原工作树、远端同SHA CI或生产验收。下文“1487运行中”和“审批原生尚未启动”已被本节更新。
- 原工作树尚未应用7文件组合修改，全部未提交改动保留。真实渠道/配置、执行/释放、正式迁移、UAT、性能与灾备等完整范围不变，未提交、推送、部署或采购。

## 2026-10-05 独立完整源码集成与静态全量复验（最新）

- 原84788再次实读仍运行，进程73897有CPU/psycopg执行活动；采样89350 exit=0，不构成卡死、性能达标或业务通过证据。继续原句柄，不重启。原2415项非文档源码仍固定。
- 下一审批验证已准备：`return-condition-decisions-native-candidate-v1.patch`为2文件，添加`--decision-candidate`（包含完整初始提交门禁）及后续7个非过账审批动作的真实API COMMIT、库存不变、原请求SQL READ ONLY回查、效果故障回滚和晚撤权COMMIT拒绝。候选仅语法/差异检查，**尚未执行此原生审批门禁**。驳回/撤回分支、永久动作封存、执行/释放、正式迁移和客户端仍单独待补。另把不准确的整段`allReadFactsUnchanged`标记改为`readPhaseFactsUnchanged`，避免可选业务提交后误称全库事实不变。
- 为不修改原运行源码，建立 `artifacts/formal-0165-integration/capture-integration-candidate/repository`，复制原清单并合入5文件权限兼容补丁与上述2文件审批门禁。实际2418项非文档源码，准确7文件差异，清单`capture-integration-candidate/source-binding-v1.json`；无Git提交。只复用已安装的venv与frontend/node_modules，原文件未动。
- 集成目录聚焦47069 exit=0：**56 passed / 11.23s**，日志`repository/cloud_oam/capture-integration-focused-v1.log`，覆盖新旧报废目录、采集角色、0166认证fence。
- 集成目录原生current-head **1487**运行中，日志`repository/cloud_oam/current-head-capture-integration-v1.log`，使用正常脚本和实际候选源，不是上轮内存替换。当前未取终态。
- 集成目录完整静态首次v1三片均在pytest之前exit=1，原因是终端PATH缺Node；未记为测试通过。使用客户端已安装Node路径后启动v2：**99385(index0/195文件)、93201(index1/166文件)、20115(index2/177文件)**，共538测试文件。进度目录分别`shard-0-ntgo_bkh`、`shard-1-51d47se4`、`shard-2-vfeb6qgj`，日志`static-shard-{0,1,2}-capture-integration-v2.log`。尚未有整片终态，保持集成源码固定。
- 当前只跟进原84788与集成1487/99385/93201/20115。原84788终态后也不能立即把集成源算成原源验收，须核对集成完整结果和清单再合入、复验。当前head终态后可在固定集成源上执行新增decision原生门禁。收据`capture-integration-progress-v1-20261005.json`。未提交、推送、部署；完整正式V1目标和真实渠道、配置、UAT、性能、灾备缺口保持。

## 2026-10-05 日终采集角色与报废目录兼容修复预验（最新）

- 本轮取得根因证据与候选修复，不是仅等待。独立诊断82035 exit=1，run-agw78whx已stopped/failed；异常链定位到 `stock_scrap_security.verify` 的完整表目录比较。受控capture角色为审计/库存等6张被0165触及的表增加SELECT，旧冻结ACL未包含这些可选运行配置。
- 修复已准备为5文件补丁 `artifacts/formal-0165-integration/stock-scrap-capture-complete-v1.patch`，仓库根目录git apply --check通过，**尚未应用原工作树**。先完整校验两个capture角色（或明确均未安装），再对本地期望目录增加固定角色/固定表/SELECT/不可转授权；不过滤实际ACL、不修改冻结JSON，其他列、索引、触发器、函数、旧角色权限仍全量比较。补齐15项拒绝回归、真实PG16十类破坏/恢复，并接入current-head本地门禁。
- 外部候选15项聚焦通过（37298 exit=0）；冻结0165来源与实际0166运行时overlay回归7项通过（92522 exit=0，4.32s）。该测试外置运行时前两次分别因缺少测试配置与相对目录错误失败（7256 exit=2；91361 exit=1），原日志保留，v3使用正式conftest初始化并显式绑定原冻结catalog路径；不计为生产代码失败或原目录全套通过。
- 原生候选25565 exit=0，run-qtstdr7y实读stopped/passed/serverExitCode=0。在新自有PG16中仅内存替换候选verify函数，真实安装capture角色后完整API安全检查、10类越权/漂移拒绝及精确恢复、原current-head后续检查均通过。包含9项短信profile检查，仍不是实际短信发送/登录。收据 `scrap-capture-compatibility-native-final-v1-20261005.json`、`scrap-capture-staged-review-v1-20261005.json`；原2415文件hash无漂移。
- **唯一仍运行84788**：成色纠正数量/SN，数量run-so_rg17g已输出历史收货兼容、候选安装、认证兼容、缺失审计拒绝通过；尚无整轮终态。不可重新启动、提前计SN或全流程通过。其源码清单固定，待自然终态后合入上述5文件补丁，在原工作树重跑聚焦和current-head门禁，并启动稳定版本完整静态/原生检查。
- 下文原current-head失败已在独立候选复现并修复预验，尚不能标为原工作树关闭。完整成色审批/执行/释放、真实渠道/KMS/OSS、同SHA CI、性能、UAT、灾备及其余正式基线缺口继续保留。未提交、推送、部署或采购。

## 2026-10-05 15:35 接管复核、0166原工作树接入与门禁终态（优先于下文）

- Chrome 命令助手已实际接管，目标 Ubuntu-ipvk / 118.31.37.87。本次只读命令 `t-hz06z3ptfvm28lc` 于15:35:28–15:35:29执行，exit=0。候选目录存在、`.env`和`.env.production`不存在；候选只有db运行且healthy，旧API/数据库healthy，旧Web运行。收据 `artifacts/formal-0165-integration/chrome-takeover-readback-20261005-153528.json`。未重启、部署、切流、采购或读取凭据。
- 原工作树已应用24文件0166组合补丁，随后修正当前head固定值、成色纠正提交门禁和迁移权限只读断言。旧0165复现保留明确历史revision，旧冻结JSON不变。下文“补丁未应用”已过时；不得重复应用。当前非文档源码绑定 `original-0166-integration-source-v1-20261005.json`（2415项）。
- 原聚焦v1为43 passed/1 failed，暴露test_alembic_migrations的旧HEAD常量；修正后v2终态94 passed/42.09s，句柄5873 exit=0，日志 `0166-original-focused-v2-20261005.log`。git diff --check与相关AST检查通过；不代表完整静态通过。
- 第一副本62270已经数量/SN双模式终态通过，run-74mw95q8与run-vz9cosde均stopped/passed/serverExitCode=0、sourceDrift=[]；收据 `loss-correction-authentication-0166-final-v1-20261005.json`。覆盖restore_available纠正与真实认证隔离，源码不含后续迁移准入加固和原工作树固定值修正，不冒充当前完整版本。
- 第二副本41816因确认的历史迁移重复编译热点有意SIGINT，exit=1；原脚本get_heads加入既有字节码缓存后，原工作树14899 exit=0、28项原生迁移准入检查通过，run-f2s7vxog已stopped/passed/serverExitCode=0。收据 `scrap-auth-migration-admission-final-v3-20261005.json`。包含跨schema触发器反例、双向准入与干净往返，但发生在后续固定值修正之前。
- 原运行时目录43186本次回读exit=0，run-qnjxkewl实读stopped/passed/serverExitCode=0。12项真实API目录破坏拒绝与恢复、当前0166准入、0164降级后旧应用启动、重新升级通过，sourceDrift=[]。日志 `runtime-catalog-0166-v1-20261005.log`；不是完整业务或生产验收。
- **当前明确阻塞：** 当前head完整门禁48819 exit=1，run-vqo6edkv已stopped/failed/serverExitCode=0。安装并单独验证日终capture角色后，完整API安全验证抛出 `DatabaseSecurityBoundaryError`（database_security.py:7166包装异常）。根因尚未定位；不能删除权限验证、放宽准入或重启已停库来冒充通过。
- **唯一继续跟进句柄84788**：原成色纠正提交候选数量/SN门禁，首库run-so_rg17g；日志已通过old-upgrade、edge-provision、old-business、current-upgrade，尚未整轮终态。日志 `return-condition-0166-v1-20261005.log`。保留原句柄及非文档源码固定；完整成色纠正审批、执行、解除冻结、正式迁移、HTTP/UI/UAT仍独立待完成。
- 旧全静态58746/10571/16958已因确定的旧head失败和版本推进有意终止，全部exit=2，不得再轮询或计全绿；分别1108 passed、1329 passed/1 failed、842 passed/1 failed，日志与停止收据保留。稳定源码后须重新完整验证。
- 未提交、推送或上线。后续先收84788终态并定位capture角色兼容性，再推进同版本完整PG16/静态/远端CI；真实短信/微信、KMS/私有OSS、业务剩余范围、性能、UAT和灾备继续保留正式验收门槛。

## 2026-10-05 0166数量终态、迁移准入缺口修复（最新）

- **62270数量库已通过**：`run-74mw95q8`实读checks=passed、stopped/passed/serverExitCode=0，head0166，sourceDrift=[]。原处置、实际冲销、独立纠正批准/执行及精确恢复通过；RC/RR真实session+refresh token+认证状态/审计在库存锁占用期间仍可COMMIT，库存/通知不变。新0165封存函数的13类锁阻断、4类伪造封存拒绝、私有执行权限拒绝均通过。收据`loss-correction-authentication-0166-quantity-v1-20261005.json`。同一父句柄62270已进入SN `run-vz9cosde`，不能重启或计双模式通过；这份源码不含下项迁移准入加固。
- **新增真实PG16迁移反例失败已终态**：外部诊断35766 exit=1，`run-gxofcajo`已stopped/failed/serverExitCode=0。24项升级/降级反例中，22项正确拒绝并保持迁移函数不变；额外外部schema挂载同一封存函数的升级/降级2项被错误接受，原因是触发器目录查询只查public。所有诊断注入均在自有临时事务回滚；干净目录往返通过，不影响原工作树或服务器。失败收据`scrap-auth-migration-admission-failure-20261005.json`，不要再轮询35766。
- 修复在第二份完整隔离副本 `isolation-candidate/admission-candidate/repository`：触发器图查询包含所有schema，并与明确public的34项冻结记录完整比较；旧冻结catalog和业务函数体未变。同时修复静态index2发现的CLI旧head固定值：`configure_inventory_control.py`从0164推进至已审查0166，继续精确拒绝其他版本。两处原副本/原工作树仍因各自门禁固定。
- 新增可保留的原生门禁脚本 `scripts/run_local_pg16_scrap_auth_migration_admission.py`，仅新建自有PG16，覆盖双向函数/权限/search_path/重载/触发器/版本/isolation反例、全目录往返、CLI真实当前head通过及旧/未来head和API角色拒绝。**句柄41816**，库`run-ketaq0jv`，日志`isolation-candidate/admission-candidate/repository/migration-admission-0166-v2.log`；目前初次全链升级已通过，整轮仍待终态，不能提前计修复通过。
- 新最终组合补丁为 **24文件** `isolation-candidate/formal-permissions-authentication-admission-0166-v2.patch`，SHA256 `8000594f9c78a39134748b6e865635c7f213199b79b03ce1fe8da4d1a67c394a`；原仓根目录git apply --check通过但未应用。2415项清单和差异绑定在`admission-candidate/source-binding-v2.json`。该补丁替代旧22文件组合补丁，不要依次重复应用。
- 原静态三片58746/10571/16958仍运行，已见两项失败：index1旧多代readiness head断言，index2 CLI固定head不符；对应修复在副本，但未冒充完整原工作树通过。继续原句柄，三处非文档源码均固定直到各自门禁终态。未提交、推送、部署或采购。
- 后续成色纠正接入另有明确缺口：旧candidate `forward_seal_auth.py`重复要求0165原函数，提交/结构/当前权限门禁仍有0165固定版本；不能把新0166正向认证通过等同于完整成色纠正通过。审计`return-condition-0166-integration-followup-20261005.json`列出逐项后续，保留历史0165复现边界。完整正式迁移、权限、HTTP/UI/UAT及原0165业务历史前向升级证明仍须完成。


## 2026-10-05 15:10 Chrome 接管与0166副本验证（当前接续入口）

- Chrome 命令助手已接管并执行一次只读回查：`t-hz06z3nkzp9raps`，15:10:25–15:10:26，exit=0；目标 Ubuntu-ipvk / 118.31.37.87。候选目录存在，`.env`/`.env.production`仍不存在；候选只有数据库容器运行且healthy，旧API/数据库healthy，旧Web仍占80/443。未改配置、重启、迁移或切流。收据 `artifacts/formal-0165-integration/chrome-takeover-service-readback-20261005-151025.json`。
- 0165认证事务争用库存锁的前向修复已在独立副本准备为 **20261215_0166**；原工作树及服务器尚未应用。保留旧0165冻结文件、函数身份、ACL和34项触发器，独立认证审计/状态在获取库存锁前识别；业务键、伪装封存和不合规状态继续拒绝。运行时清单与readiness版本同步。
- 副本聚焦测试 **42 passed / 60.92s**，日志 `artifacts/formal-0165-integration/isolation-candidate/repository/scrap-authentication-fence-0166-focused-v1.log`；这不等于完整PG16通过。合并补丁22文件 `formal-permissions-and-authentication-0166.patch`，SHA256 `fe5eeebb43e3533519c469d88a9798a3ac7766c3b01f7331e6ecb664fbc3335e`，原仓根目录 git apply --check通过，尚未应用。
- 数量/SN原生纠正同一父句柄 **62270** 正在执行；首库 `run-74mw95q8` 实读running_checks，日志已通过初次升级、空库降级/再升级及合成期初完整链。日志 `isolation-candidate/repository/loss-correction-authentication-0166-native-v1.log`；2414项源码绑定 `scrap-authentication-fence-0166-source-v1.json`。完整认证正反例、保留历史和两模式终态尚待核验，不能计通过。
- 原工作树静态 **58746/10571/16958** 本次回读仍存活；index1已有旧head断言失败。两处非文档源码继续固定，保留所有未提交改动，不另启同轮门禁。旧94878/77146/24461/23214已终态，不再轮询。下一步跟进62270和三片静态，终态后再决定原工作树补丁应用与复验；正式CI、真实渠道、性能和UAT仍是独立条件。


## 2026-10-05 处置双模式通过、隔离修复合入与新门禁启动（最新）

- **副本原生纠正发现正式兼容性失败**：94878 已 exit=1；数量库 run-cq4gbptw 停止，checks=failed/serverExitCode=0，SN 尚未开始，不再轮询94878。空库升级/降级/再升级、原处置与实际冲销提交已执行，但认证隔离门禁失败：真实 session/audit COMMIT 在另一事务持有库存头锁时被 `rsc_fence_scrap_seals_0165()` 阻塞，500ms lock_timeout 报错。该触发器在识别独立认证审计/状态之前，无条件要求 read committed 并获取库存锁；不能提高超时或删除认证隔离门禁掩盖。下一步需从确切0165目录做前向修复，保留函数身份/ACL、业务封存与伪造碎片拒绝，并覆盖 RC/RR 独立认证、错误流/伪装业务审计、保留历史和运行时清单。失败收据 `loss-correction-authentication-lock-failure-v1-20261005.json`。副本源码冻结解除；原工作树仍因三个静态分片保持固定。
- **破损收货入库性能剖析已终态**：77146 exit=0，1 passed/208.25s（含 cProfile 开销及并行负载）；setup 77.09s、call 108.93s。整个测试过程执行98,075次 SQLite cursor.execute，verify_chain 被调用114次、load_inventory_history 570次，重复历史证明是明确热点。原文件 `damaged-inbound-profile-v1-20261005.pstats`、日志和同名JSON绑定摘要。不是单个HTTP请求/P95，累计时间互相包含不可相加。后续减少同一有效读快照内的重复证明，必须保留当前授权、篡改拒绝和写入后的重新核验；仍须真实PG16/API性能及500用户门禁。不要再轮询77146。
- 权限补丁原生预验已在临时副本启动：`artifacts/formal-0165-integration/isolation-candidate/repository` 建立独立本地 Git 索引（无提交），只绑定原工作树 2408 项非文档源码；实际 runner manifest 逐项比对通过。副本与原工作树仅有已审查的 8 文件补丁差异，绑定收据为 `native-candidate-source-binding-v1.json`。原工作树源码未改，两个补丁在仓库根目录组合 `git apply --check` 通过但未应用。
- 副本原生纠正默认数量/SN 已启动，句柄 **94878**，日志 `isolation-candidate/repository/loss-correction-formal-permissions-native-v1.log`，首库 `repository/cloud_oam/artifacts/local-loss-correction-pg16/checks/run-cq4gbptw` 已确认为独立 PG16.15、running_checks。该入口默认 restore_available；其余纠正类型、封存、停止、多代仍须独立覆盖。尚未终态，不计通过，不冒充原工作树或正式 CI 验收。当前只跟进原工作树静态 58746/10571/16958 和副本原生 94878，两处非文档源码均保持固定直至对应门禁终态。
- **审核专用封存双模式已终态通过**：24461 exit=0，数量run-4s10a1ps/SN run-q4jj6lg4的checks与cluster-state均实读passed、stopped/passed/serverExitCode=0；head0165，2408项源码清单一致，sourceDrift=[]。两级专用封存、HTTP未知结果恢复、撤权后回查、库存/通知不变、空库往返和保留封存历史拒绝降级均通过。汇总`artifacts/formal-0165-integration/loss-review-seals-final-v1-20261005.json`绑定两库原文件摘要。不要再轮询24461。此轮仍使用旧条件read授权夹具、合成存储，不能冒充待应用权限补丁或真实OSS验证。现在仅静态58746/10571/16958继续，源码冻结保持至整轮终态。
- 剩余0164引用已分类审计，见`remaining-0164-reference-audit-20261005.json`：历史scrap结构/forward诊断脚本不在当前正式CI入口，部分仍以head迁移但期望0164；不能批量改成0165或当作当前版门禁。历史重放必须绑定保留的旧源码/运行时及明确版本。现有`pg16_predecessor_runtime.py`边界保留，本轮未改这些历史脚本。
- 完整静态index1新增明确失败：`test_loss_multigeneration_release_sources.py`仍断言当前head=0164。副本准确复现为70851 exit=1（1 failed/1 passed）；已准备`isolation-candidate/readiness-head-forward.patch`，保留0164历史冻结校验并逐项核对0165前后函数体、迁移链、冻结摘要与当前运行时manifest。副本16572 exit=0：**2 passed / 56.69s**，收据`readiness-head-forward-v1.json`；23587工作目录错误、无测试执行，已排除。补丁尚未应用原工作树，git apply --check通过，原生SN源码清单仍无漂移。index1全片已有失败，不能称全绿；四个原句柄继续，待终态后连同权限夹具补丁应用及原工作树复验。
- 审核专用封存数量模式已独立核验终态：`run-4s10a1ps` checks/state均passed，stopped/passed/serverExitCode=0，head0165、sourceDrift=[]。区域/总部专用封存分别12/19类原始提交回滚、真实到期、完整迟到命令/碎片拒绝、精确锁竞争、HTTP响应丢失后回查、撤权后的只读边界与库存/通知不变均通过；保留审核封存历史拒绝降级。数量摘要 `loss-review-seals-quantity-v1-20261005.json`；同一父句柄24461继续SN，不能计双模式通过。完整静态三片仍在原句柄持续执行，无终态；权限夹具补丁仍未应用。
- 权限夹具修复已在临时副本准备并验证，尚未应用原工作树。补丁 `artifacts/formal-0165-integration/isolation-candidate/formal-fixture-permissions.patch` 涉及7个测试支持文件：当前head的纠正/纠正封存/退回停止/审核封存只读校验正式权限；共享多代夹具仅明确0164保留旧准备分支，0165用正式权限，空/多head/未知版本拒绝。新增0100查看权限反例和版本准入回归，副本句柄 `92214` 已exit=0：**64 passed / 8.43s**，日志 `repository/formal-fixture-permissions-v1.log`、摘要 `formal-fixture-permissions-v1.json`。原生审核封存源码清单重新比较为[]，四个原句柄继续；后续须在本轮结束后应用及原工作树/真实PG16复验，不把副本结果当完整原生验收。
- 接续只读审计发现五处旧条件授权夹具；详见 `artifacts/formal-0165-integration/loss-fixture-permission-followup-audit-20261005.json`。0100已提供三个内部角色read，0165已提供审核/纠正动作；当前head不得由夹具临时补缺权。共享 `pg16_loss_multigeneration_fixture.py` 同时供显式0164真实历史使用，修复时保留该历史边界。当前四个句柄仍存活，未为此改变源码；该后续审计不冒充已修复或原生通过。
- `41713` 已exit=0。数量 `run-oocu6uoj`、SN `run-5b7mea7b` 均实读checks和cluster-state：passed，stopped/passed/serverExitCode=0，head0165，sourceDrift=[]，2406项源码清单一致。汇总 `artifacts/formal-0165-integration/loss-disposition-final-v1-20261005.json` 绑定原检查和清单摘要，不把后续测试改动冒充原轮源码。不要再轮询41713。
- 双模式各完成恢复可用/转旧/转坏三笔真实API角色提交、原请求重放、共享冻结份额保留、历史校验及通知去重；损坏提交整笔回滚数量30类/SN32类，SN还有精确序列替换拒绝。真实权限到期拒绝、并发唯一记账、保管冲突锁、保留历史拒绝降级均通过。附件仍为合成存储，不代表真实OSS或完整上线。
- 上轮准备的测试隔离补丁已合入原工作树：`local_test_runtime.py`为每个进程创建独立artifact目录，conftest和core流程共享本进程路径；新增双进程回归验证相同表主键/上传文件名互不干扰，拒用继承外部DSN/目录。没有修改或删除旧`.test_oam.db`及未提交工作。
- 五处当前head脚本已修正：sender HTTP/correction/multigeneration使用真实唯一当前head；correction-request/return-stop保留未来版本拒绝，只将已审查版本推进到0165。空库往返、历史保留、运行角色安全和业务断言保留。独立历史0164夹具未批量替换。仍需这些脚本各自的完整原生复验，尤其纠正/多代夹具的旧条件授权设置仍需独立审计。
- 副本调度/拓扑v2为80 passed/13.29s；v1由于补丁工作目录错误，测试跑在旧副本代码，已在`isolation-candidate/head-admission-review.json`排除，不能当修复证据。原工作树聚焦句柄 `82430` 已exit=0，日志 `isolation-head-original-focused-v1-20261005.log`：**87 passed / 30.92s**，1项既有Starlette弃用警告；覆盖并发隔离、核心流程、SQLite外键、调度和拓扑。
- 新全范围静态三片已启动，共536文件：index0/195文件，句柄 `58746`，进度`shard-0-vnfy2c23/progress.jsonl`；index1/164文件，句柄 `10571`，进度`shard-1-665yboe1/progress.jsonl`；index2/177文件，句柄 `16958`，进度`shard-2-g139vjl2/progress.jsonl`。主日志 `artifacts/formal-0165-integration/static-shard-{0,1,2}-isolated-v2-20261005.log`，进度在`artifacts/static-safety/`。目前未有整片终态，不能计完整通过。
- 审核专用封存 `run_local_pg16_stock_loss_review_seal_checks.py` 数量/SN双模式已启动，句柄 `24461`，日志 `loss-review-seals-formal-permissions-v1-20261005.log`，首库 `local-stock-loss-review-seals-pg16/checks/run-4s10a1ps`。本次显式 `check_review_seals=true`，补齐此前提交审核入口未覆盖的专用审核封存证据；尚无结果。
- 当前只跟进 `58746/10571/16958/24461` 四个原句柄，继续固定非文档源码直至本轮门禁结束。未提交、推送、采购或部署；真实渠道、环境配置、公开知识目录、完整CI、性能与正式UAT等上线门槛继续保留。

## 2026-10-05 静态并行隔离修复准备、处置数量终态（优先于下文）

- 三片静态原句柄 `79273`、`10819`、`80478` 已因确证的共享测试路径缺口有意SIGINT，并均回读exit=2。`conftest.py`设同一`.test_oam.db/.test_uploads`，真实app lifespan通过全局engine执行bootstrap/seed；shard2的核心流程会unlink该文件。因此不把并行结果作为独立通过证据，也没有证据断言已发生数据损坏。中止前call通过739/1200/90；原日志全部保留，汇总 `artifacts/formal-0165-integration/static-shards-v1-isolation-interruption-20261005.json`。不要再轮询三个旧句柄。
- 最小修复与真实双进程并发回归已准备在 `artifacts/formal-0165-integration/isolation-candidate/isolate-test-runtime.patch`，尚未应用原工作树：每进程独立数据库/上传路径、核心流程共享本进程路径；继承外部DSN不会覆盖测试隔离。`git apply --check`通过，不能据此宣称功能测试通过。
- 原生处置仍固定原源码，同一父句柄 `41713` 保持运行。数量库 `run-oocu6uoj` checks/state已独立读取：passed，stopped/passed/serverExitCode=0，head0165，sourceDrift=[]；覆盖三类实际处置、30类损坏提交回滚、真实到期拒绝、同请求并发唯一记账、共享冻结份额保留、通知事务/去重、历史与降级保护。SN库 `run-5b7mea7b` 尚无整轮终态，不能以数量替代SN。
- 为不造成上述运行中门禁源码漂移，用2670项、约56MB当前文件的临时副本验证隔离补丁；原工作树源码未变。副本位于 `artifacts/formal-0165-integration/isolation-candidate/repository`，原清单为相邻`snapshot-manifest.json`。副本聚焦测试句柄 `19170` 已exit=0，日志 `repository/isolation-focused-v1.log`：新增真实双进程并发隔离、核心流程、SQLite外键检查共 **7 passed / 36.32s**，1项既有Starlette弃用警告。两个并发子进程同时写相同主键/上传文件名，各自回读本进程内容，确认继承外部DSN/上传目录未被使用。后续待41713结束后应用补丁并在原工作树复验，重新执行三片完整门禁。当前仅41713需继续轮询；运行中SN源码清单再次检查无变化。

## 2026-10-05 14:09 Chrome 命令助手接管回读

- **14:38 新回读**：用户再次要求接管现有 Chrome，已完成只读检查 `rsc-takeover-config-resource-check-20261005`，执行 ID `t-hz06z3krgwdlzwg`，14:38:48–14:38:49，exit=0。候选目录存在，`.env`/`.env.production`仍不存在；候选运行容器仅数据库，healthy。根盘可用32.48 GiB，内存available 2160 MiB（瞬时值，不是负载验收）。通道可用，但应用配置和API/Web仍未就绪；未重启、迁移、改配置或切流。收据 `artifacts/formal-0165-integration/chrome-takeover-resource-check-20261005-143848.json`。四个本地门禁原句柄本轮回读仍在运行，非文档源码保持固定。

- 已使用用户指定的现有 Chrome 会话接管目标 `Ubuntu-ipvk / 118.31.37.87`，执行只读命令 `rsc-takeover-readonly-20261005`，执行ID `t-hz06z3i5qpotq80`，14:09:37–14:09:38，ExitCode=0。
- 本次实际回读：候选 `rsc-pilot-20261004-fc7c926-dirty-db-1` 为 healthy，运行中列表没有该候选的 API/Web；旧 `star-oam-api-1` 与 `star-oam-db-1` healthy，`star-oam-web-1` 仍占用80/443。这只证明运维通道恢复与容器运行状态，不证明候选应用已上线。
- 页面显示输出在 `df` 起始处截断，资源容量未取得完整回执，不作结论。12:41旧回执仅确认当时候选目录存在、`.env`和`.env.production`不存在，本次未重新核验这些配置路径。未重启服务、迁移数据库、修改权限或切换流量。
- Chrome 命令编辑器的直接setValue只更新可访问文本，不可靠地写入编辑器模型；本次最终使用选中全文后typeText，核对实际命令与可用提交按钮后仅执行一次。

## 2026-10-05 提交审核双模式终态，三片静态全范围与处置继续（最新）

- `47777` 已exit=0。数量 `run-twfwj4wq`、SN `run-gwa2c48r` 的checks与cluster-state均实读通过：提交、区域核实、总部审核passed；两库stopped/passed/serverExitCode=0，head0165，sourceDrift=[]，2406项源码清单完全一致。汇总收据 `artifacts/formal-0165-integration/loss-submit-reviews-final-v1-20261005.json` 绑定checks摘要与原清单。不要再轮询47777。
- 这两轮证明当前正式迁移权限下的原子冻结、请求回查/封存、两级审核及其回查、到期/撤权/并发、事务全回滚和历史保留；不替代审核专用封存变体、实际处置、真实存储或完整上线。本入口的审核专用 `check_review_seals` 为false，汇总回执明确记录。
- 静态全范围三个分片已全部启动，共535文件、10209项收集：index0/194文件/3311项，句柄 `79273`，`shard-0-y8x6p1n4/progress.jsonl`；index1/164文件/3612项，句柄 `10819`，`shard-1-7453x8u_/progress.jsonl`；index2/177文件/3286项，句柄 `80478`，`shard-2-pyjp25o2/progress.jsonl`。逐项日志均位于 `artifacts/static-safety/`，主日志为 `artifacts/formal-0165-integration/static-shard-{0,1,2}-full-v1-20261005.log`。最近观察通过735/328/1，index0有1项预期SN夹具跳过，无失败；所有分片尚无终态，不能宣称10209项通过。
- 新原生处置门禁 `run_local_pg16_stock_loss_disposition_checks.py` 已启动数量/SN双模式，句柄 `41713`，日志 `loss-disposition-formal-permissions-v1-20261005.log`。继续验证恢复可用、转旧/坏件等批准后实际处置、库存与审计/通知事务、历史保留；尚无结果。
- 当前仅上述四个句柄需要跟进，继续固定非文档源码，保持全部未提交改动。静态分片是本地同一runner实测，不伪装GitHub；完整远端CI、真实渠道、性能与生产验收仍未完成。

## 2026-10-05 提交审核数量模式通过、完整静态分片启动

- `47777` 仍为当前运行句柄，数量库 `run-twfwj4wq` 已由脚本输出整轮passed，head0165、sourceDrift=[]，覆盖提交原子冻结、四类不完整事务回滚、真实到期全回滚、原请求恢复、封存迟到写入拒绝、区域10类/总部16类提交回滚、真实锁竞争、审核只读HTTP/权限撤销及保留历史拒绝降级。审核仍不代表处置完成；本入口 `check_review_seals=false`，审核专用封存场景另待验证。SN正在同一父进程继续，不另启或提前计整组通过。
- 完整静态门禁按仓库正式发现器 `run_static_shard.py` 执行：三片文件数194/164/177；第一片共收集3311项，句柄 `79273` 运行中。日志 `artifacts/formal-0165-integration/static-shard-0-full-v1-20261005.log`，逐项证据 `artifacts/static-safety/shard-0-y8x6p1n4/progress.jsonl`。最近观察530项call通过，无失败；这不是整片终态。使用工作区Node路径，保持runner原有环境清洗，不伪造GitHub环境。
- 当前两个进程都需继续原句柄；非文档源码仍固定。第二、三静态分片、审核封存、处置及其他当前head门禁仍待执行；不得把收货/附件/当前数量审核证据合并成全部正式CI通过。
- 最新回读：数量库 `run-twfwj4wq` 的checks/state已独立核验stopped/passed/serverExitCode=0；SN库 `run-gwa2c48r` 已完成期初盘点、不完整提交回滚、真实到期整笔回滚、封存与迟到写入拒绝，尚无整轮终态。静态第一片已683项call通过、1项预期跳过，未见失败；跳过的是 `test_rejected_sn_cannot_be_bound_as_personal_inventory[quantity]`，源码明确该反例使用SN夹具。两个句柄均已重新轮询确认存活，不能因本段交接就重启。

## 2026-10-05 收货双模式终态与其余门禁修复（本节优先）

- `21621` 已回读exit=0。v5数量 `run-t506pfdr`、SN `run-3bp2xg11` 均已实际读取 `checks.json` 和 `cluster-state.json`：passed、stopped/passed/serverExitCode=0，head=0165，sourceDrift=[]；两份完整源码清单相同。汇总收据 `artifacts/formal-0165-integration/loss-return-receipt-final-v5-20261005.json` 绑定每库checks摘要与原源码清单。不要再轮询21621。
- SN额外完成错误SKU/SN/二维码/序列ID拒绝、缺失SN时COMMIT整笔回滚；与数量模式相同，合法并发唯一成功、原请求精确重放、收货不改库存、独立入库、通知完整性、请求封存/恢复、入库后历史可验证及保留历史阻止降级均通过。SN准备/执行/锁探测合计23.423秒，到期窗口57秒；依旧不等于生产性能验收。
- 双模式终态后才解除源码固定。现为其余当前head门禁新增 `require_formal_grant` 只读断言，区域/总部审核、处置和附件门禁不再合成授权；附件门禁在创建用户前独立核查11项完整迁移角色矩阵。当前head回读改为唯一实际revision与 `HEAD_REVISION` 比较，历史0164迁移用例未批量替换。
- 新增 `test_pg16_loss_fixture_permissions.py` **7 passed / 0.81s**，句柄70096 exit=0：合法、权限缺失、grant缺失、deny、字段不符、角色停用、外部角色；所有读取/拒绝均只SELECT，授权表快照完全不变。日志 `loss-fixture-policy-readonly-v1-20261005.log`。这证明夹具不会掩盖策略缺陷，不替代真实API门禁。
- 附件门禁 `7770` 已exit=0，`artifacts/local-stock-loss-evidence-pg16/checks/run-zhv2hxz2` 已核验stopped/passed/serverExitCode=0、head0165、sourceDrift=[]。11项正式角色策略、零外部grant、三种角色上传/完成/重放/下载、其他审核者不可下载未绑定证据、SQL拒绝/过期/未验证身份、到期文件与审计一起回滚、保留历史拒绝降级均通过；库存写入0。日志 `loss-evidence-formal-permissions-v1-20261005.log`。存储仍为内存适配器，不是真实OSS证明。
- 下一轮 `run_local_pg16_stock_loss_submit_checks.py` 已启动数量/SN双模式，句柄 `47777`，日志 `loss-submit-reviews-formal-permissions-v1-20261005.log`，验证提交与区域/总部审核，尚无终态；终态前保持非文档源码固定。处置完整原生复验仍待完成；收货v5证据严格绑定其原清单，不把后续未验证夹具改动冒充同一完整发布版本。
- 本轮未提交、推送、部署或购买资源。后续仍需处理其他当前head门禁、完整CI、性能、真实渠道和正式基线缺口；详见当前验收审计。

## 2026-10-05 收货数量模式整轮通过，SN继续（最新）

- 原生v5数量库 `artifacts/local-stock-loss-return-receipt-pg16/checks/run-t506pfdr` 的 `checks.json` 为passed，`cluster-state.json` 已实读 stopped/passed/serverExitCode=0，结束时间05:31:16 UTC。真实当前head为0165，源码清单 `sourceDrift=[]`，使用迁移正式权限，`syntheticPermissionOnly=false`。
- 通过范围：0165→0154→0165空库往返目录/ACL完全一致、运行角色与私有函数权限、报损审批/退回/出库/发运准备、接收人与保管锁、真实到期COMMIT拒绝、5类收货损坏回滚、两请求并发仅一笔成功、原请求并发精确重放、收货不提前记库存、独立正常入库、两类入库通知损坏回滚、收货及入库原请求回查/封存拒绝迟到写入、入库后原验收历史可验证、有历史时降级被拒绝。
- 同一父句柄 `21621` 仍运行，已进入SN库 `run-3bp2xg11`。不能把数量通过替代SN或完整远端CI；继续原句柄，源码冻结仍有效。日志仍为 `loss-return-receipt-expiry-v5-20261005.log`。
- 性能直接观察：数量合成用例的请求准备/真实执行/锁探测合计20.473秒，到期窗口按公式为51秒；该值含测试探测开销，不是API P95。源码预览测试段合计7798条SELECT，包含多次预览与反例，不是单次请求SQL数。正式上线前须按真实单请求剖析并完成500用户门槛，不能通过放大测试有效期宣称性能已解决。
- 本轮完成官方部署身份文档核查并同步KMS runbook，尚未建立目标轻量机的应用KMS身份；未购买、授权、写入外部业务、提交、推送或上线。

## 2026-10-05 收货到期边界复验（本节优先）

- 上轮为实质进展：修复0165权限与旧预览夹具冲突并完成缺口复核。本轮已回读原句柄 `34444` 的 exit=1，数量库 `run-u8fre3n_` stopped/failed/serverExitCode=0。迁移往返、期初盘点、预览撤权、发运前/出库后HTTP和收货接收人/保管锁检查通过；SN未开始。
- v4 失败点为收货到期测试的固定30秒有效窗口：`request_for` 加 `execute_receipt` 的重复来源证明期间，真实权限已过期，`_require_current_actor` 在服务执行内正确拒绝；尚未执行到该用例预期的 COMMIT 拒绝。不能把这一失败说成数据库提交保护失效，也不能预记边界通过。原日志 `loss-return-receipt-permissions-v4-20261005.log` 保留。
- v5 从紧邻的同一路径锁校验计量准备耗时，将测试到期窗口设为 `max(30, ceil(2 * 实测耗时 + 10))` 秒；保留执行完成早于到期的断言，增加数据库真实时钟晚于到期的断言，仍要求 COMMIT 抛23514。未改运行时权限、业务SQL或迁移超时。回执额外保存准备耗时与到期窗口，慢业务路径仍须另做生产性能验收。
- 数量/SN完整原生 v5 句柄 `21621` 运行中，数量库 `run-t506pfdr`，日志 `artifacts/formal-0165-integration/loss-return-receipt-expiry-v5-20261005.log`。已再次通过迁移往返、期初盘点、退回预览/撤权与发运前HTTP，后续未终态。终态前不修改非文档源码，不另启同一门禁。
- 权限种子聚焦句柄 `75867` 已exit=0：`test_stock_operation_permission_policy.py` **13 passed / 0.41s**，覆盖自定义allow/deny保留、唯一策略、事务回滚和降级保留；日志 `permission-policy-refresh-20261005.log`。这不是PG16业务或完整CI通过。
- 对本轮自有本机数据库只读观察到 `star_oam_api` 为 ClientRead，未证明数据库锁死；运行中Python采样句柄 `67077` exit=0，样本 `receipt-v5-runtime-sample-20261005.txt` 仅定位调用等待与Python执行，不足以归因完整性能瓶颈。未改变集群配置、应用权限或业务数据。真实500用户性能门槛保留。
- v5 数量模式进一步实读：真实接收权限到期后的 COMMIT 拒绝、5类异常收货整笔回滚、不同请求并发唯一成功、原请求并发精确重放，以及收货/恢复不提前改库存均通过；入库的通知摘要错误与通知目标缺失也已各自COMMIT回滚通过。正常入库/历史保留及SN整轮尚待终态，句柄仍为 `21621`。
- 部署独立复核：官方ECS实例角色与轻量服务关联角色是不同机制。KMS runbook已明确轻量目标的应用凭据来源未验证，不能用 `AliyunServiceRoleForSwas` 替代应用解密身份；同时移除过时0058示例head，要求按实际发布版本核对。无RAM创建/授权、凭据读取或远端部署。
- 后续 CI 静态审查发现同类旧夹具仍在 `pg16_stock_loss_regional_review_gate.py`、`pg16_stock_loss_headquarters_review_gate.py`、`pg16_stock_loss_disposition_gate.py` 无条件插权限；`pg16_stock_loss_release_checks.py`、disposition/evidence门禁仍硬编码0164，但入口为upgrade head。当前仅是已核对调用路径的静态缺口，不声称已跑失败；待本轮源码清单冻结结束后统一修正并验证，不能只修一个可通过的收货门禁便提交。

## 2026-10-05 迁移加载性能与降级校验修复（本节优先）

- 原 `55780` 已 exit=1：单次 Alembic `upgrade head` 在 600 秒达到超时，数量库 `run-tc72n5ie` stopped/failed/serverExitCode=0，SN 未开始；保留全部失败日志。实时数据库处于 ClientRead，Python 采样显示反复编译；不是已证明的数据库死锁。旧句柄不再轮询。
- 编译但不执行全部 165 个迁移，源码 5,554,333 字节，CPython 3.12 编译对象估算 8,886,332 字节；最大迁移 833,774 字节。原每文件 128 KiB/总量 1 MiB/32 项字节码缓存排除了该热点。现改为每文件 1 MiB/总量 8 MiB/192 项，仍有上限、仍重新执行每次 runpy 的 globals 和副作用；没有启用 direct execution cache、改历史业务 SQL、放宽超时或提高角色权限。缓存测试 **12 passed / 0.59s**。
- v2 原生 `15246` 已 exit=1，`run-xworo5l6` stopped/failed/serverExitCode=0：整轮约 46 秒内已完成真实全链升级到0165、edge权限预检及API权限校验，随后在空库降级0161时暴露旧0159校验器对 PostgreSQL DROP COLUMN 占位的误拒。该轮不是业务通过，收货/入库尚未开始。
- 修复历史迁移支持校验器 `stock_loss_corrections_0159/frozen_install.py`，与已有运行时规则保持一致：仅允许类型OID清零且无非空/identity/generated状态的规范已删除属性，任何列ACL仍拒绝；有效列、FK、索引、函数、属主、RLS及全部冻结catalog仍逐项验证。冻结JSON及DDL未改，不复制/重建旧事实表、不清理数据。另将收货门禁的硬编码0164回执改为从 `alembic_version` 实读，并要求精确等于当前唯一 head。
- 默认环境下远端失败的四项数据库安全检查原 `17977` 已 exit=1，**2 failed, 2 passed / 668.44s**。真正问题是源码链优化漏掉0150继承的 `ACCOUNT_SIGNATURE`，并非可直接更新预期hash。现不再依靠函数名字面量跳过动态 `_sources`，保持逐步 before/after 连续性；源码链遍历只缓存字节码。补充动态继承key反例后，`17754` exit=0：缓存/源码链与四项原失败检查共 **20 passed / 55.26s**。较早4项H5通过与此范围不同，不将其替代整套CI。
- 原生 v3 `18157` 已 exit=1：日志 `artifacts/formal-0165-integration/loss-return-receipt-catalog-v3-20261005.log`，数量库 `run-qx8p8h6a` stopped/failed/serverExitCode=0。真实降级到0154、再升级0165、catalog与权限校验、合成期初盘点全链通过；随后旧退回预览夹具重复插入0165已提供的 `review_loss_regional`，触发 `uq_permissions_definition`。这不是收货/入库通过，SN未开始。
- `66742` 已 exit=0：0106/0152远端失败摘要回归 **2 passed / 70.34s**，日志 `remaining-ci-migration-hashes-v3-20261005.log`。`78637` 已 exit=0：独立PG16真实新增有效列拒绝、规范删除占位通过、列ACL与RLS拒绝四项通过，日志 `frozen-column-metadata-native-20261005.log`；`local-frozen-column-metadata-pg16/run-pxaa439x/cluster-state.json` 确认 stopped/passed/serverExitCode=0。
- v4 修正预览夹具：按完整权限键复用迁移权限及对应角色grant；0165缺失权限/grant或已有deny均拒绝，不覆盖迁移策略。旧revision仍可使用原合成夹具。原生数量/SN双模式句柄 `34444` 运行中，日志 `loss-return-receipt-permissions-v4-20261005.log`，终态前固定非文档源码。
- v4 最新实读：数量库 `run-u8fre3n_` 已完成迁移往返、期初盘点全链、报损退回审批/只读计划/实时权限和保管责任校验，以及 `pending_departure`、`departed_not_shipped` 两个真实只读HTTP阶段。父进程仍在运行，未取得收货/入库/恢复或SN整轮终态；下一轮继续句柄 `34444`，不得重启或提前计通过。Chrome 已回到目标 `Ubuntu-ipvk / 118.31.37.87` 命令助手，当前打开既有12:41路径回执；本轮没有远端命令写入、采购或切流。正式基线缺口复核已同步至 `FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md` 顶部。

## 2026-10-05 远端 CI 失败定位与当前源复验（运行中）

- GitHub CLI 只读访问已恢复。已提交 `fc7c9269e19f6afd180a0aced55b565f03c244b6` 的客户端运行 `36931116967` 成功，PG16 运行 `36931116983` 失败；这些运行不包含当前未提交改动，不代表本工作树验收。
- 直接读取 job 日志：`static_safety (2)` 的四项 H5 收货/入库样本仍为旧 1.0 预览，另有 0106 后继函数摘要不符；`static_safety (1)` 包含四项数据库权限/函数/触发器清单断言及 0152 后继函数摘要不符。当前 H5 样本已有修正，本轮默认环境精确复验 `test_return_receiving_h5_contract.py` 终态 **4 passed / 1.98s**，句柄 `73741` exit=0。
- `pg16_runtime (inventory)`、`pg16_runtime (migrations)` 的 check annotations 均为 `The operation was canceled.`，没有足够证据归因为业务 SQL 失败；`static_safety (0)` 的 annotation 是 hosted runner 失联，提示可能为 CPU/内存/网络原因，但未证明具体根因。批量 `--log-failed` 因 job `110600790428` 日志缺失失败，随后成功按 job 读取另两组日志；这不是当前 GitHub 连接故障。
- 当前运行 `17977`：默认环境复验远端失败的四项 `test_database_security.py` 精确 node；日志 `artifacts/formal-0165-integration/database-security-ci-failures-refresh-20261005.log`。尚无整组终态，不计通过。
- 当前运行 `55780`：`run_local_pg16_stock_loss_return_receipt_checks.py` 的数量/SN双模式，在全新自有 socket-only PostgreSQL **16.15** 上验证真实迁移、默认权限、收货/入库和请求恢复；只设置与候选 Compose 一致的 discovery execution cache，未设置 direct execution cache。当前数量目录 `artifacts/local-stock-loss-return-receipt-pg16/checks/run-tc72n5ie`，主日志 `artifacts/formal-0165-integration/loss-return-receipt-refresh-20261005.log`。这是本地补充门禁，不伪装 GitHub Actions，不连接现有数据库。迁移子进程仍有 CPU 活动；继续原句柄，不因暂时无输出重启。
- 待修正的证据准确性问题：`pg16_stock_loss_return_receipt_gate.release` 返回 `migrationHead='20261213_0164'`，但实际命令是 `upgrade head`。必须在当前整轮终态后改为实际读取 `alembic_version` 并与仓库唯一 head 核对，不能把该硬编码字段作为迁移完成证据。本轮运行期间固定非文档源码，避免清单漂移；未提前修改该文件。
- 原始远端日志与 run JSON 已保存在 `artifacts/formal-0165-integration/github-*-refresh.*`。当前无提交、推送、采购或部署；完整正式基线范围保持，不能以这批聚焦结果替代其他业务、真实渠道、UAT、性能和灾备门槛。

## 2026-10-05 KMS 费用与接入核查

- Chrome 接管仍可用。杭州 KMS 3.0 软件实例页为空；从控制台链接进入报价页，默认一个月一个实例显示 `¥4,997.00`，含数据密钥、凭据和日志等附加项；自动续费未勾选。未提交订单、开通 OSS、创建凭据或修改服务器。
- 官方 FAQ 基础价为 2499 元/月，但另有按量文档，口径存在冲突；当前账号页面仅观察到月/年选项。默认报价不等于最低项目配置，按量文档不证明该账号可购买。已向用户征询优先评估低成本托管或继续整理阿里云最低配置报价，尚未收到选择或费用授权。
- 补齐 `ALIYUN_KMS_ENVELOPE_KEY_RUNBOOK.md` 的报价边界、公网访问前提和专属 VPC CA 接入限制。代码仍只支持 `aliyun_kms`，没有降低加密、pin、TLS 或真实门禁要求。
- 下一步：确定费用与接入方案后再形成精确资源配置；继续独立处理同版本 PG16 CI、业务链与发布证据。资源采购、配置落地、真实解密、短信登录、API/Web 启动、UAT 是不同完成条件。当前仍无上线结论。

## 2026-10-05 云资源只读审计（接管恢复后）

| 门槛 | 当前直接证据 | 下一步 |
| --- | --- | --- |
| PNVS 产品与套餐 | 刷新号码认证概览后，明确为短信认证套餐包，余量 1000 次/100%；不是凭标准短信订单猜测 | 沿用现有 `aliyun_pnvs` 适配器；独立运行身份、安全配置、实际发送/核验/登录回读仍待完成 |
| PNVS 签名/模板 | 短信认证参数页的赠送签名 `恒创联众` 审核通过；赠送登录/注册模板 `100001`，参数 `code`、`min` | 可准备非敏感参数；尚未配置服务器或发测试短信；有效期/实际扣费口径未核验 |
| KMS | 杭州新版用户主密钥页显示已创建 0 个密钥；历史专属 KMS 页显示当前地域无实例 | 核实部署所需实例/密钥及适配端点；不能把未核查地域推断为无资源；未购买或创建密钥 |
| 私有 OSS | 当前账号 OSS 控制台明确显示“尚未开通对象存储服务 OSS”，开通后默认按量付费 | 先由用户决定开通和费用，再准备私有 Bucket 与独立最小权限身份；未开通、未上传文件 |

本次仅使用用户已有 Chrome 登录态读取控制台，未读取 AccessKey、token 或明文密钥；未绑定测试手机号、发送短信、购买资源、创建权限或修改服务器。非敏感 PNVS 参数已补入 `ALIYUN_KMS_ENVELOPE_KEY_RUNBOOK.md`。这解除的是套餐归属疑点，不是短信联调或应用上线门禁。运行环境缺失之外，同版本正式 PG16 CI、完整业务链、备份恢复和 UAT 仍须继续完成。

## 2026-10-05 12:41 接管恢复与当前阻塞

- Chrome 原窗口已通过原生应用接管恢复，已进入目标 `Ubuntu-ipvk / 118.31.37.87` 的命令助手。此前 `cua.getState()` 初始化超时不能当作云服务器或登录故障；本次没有要求用户重新登录。
- 实时只读容器检查 `rsc-readiness-refresh-20261005`，执行 ID `t-hz06z3a5j587qww`，12:39:53 完成、ExitCode=0：匹配 `rsc-pilot` 的运行容器只有 `rsc-pilot-20261004-fc7c926-dirty-db-1 Up 28 hours (healthy) 5432/tcp`。未启动 API/Web、未切流。
- 实时路径检查 `rsc-env-presence-refresh-20261005`，执行 ID `t-hz06z3a9d19xce8`，12:41:05 完成、ExitCode=0：`CANDIDATE_DIR True`、`ENV_EXISTS False`、`PRODUCTION_ENV_EXISTS False`。只检查路径存在性，未读取凭据值。候选目录为 `/opt/rsc-pilot-20261004-fc7c926-dirty`。
- 上一轮测试句柄 66754 已回读终态、exit=0：默认环境下 `test_migration_script_cache.py`、`test_stock_loss_return_receipt_migration.py`、`test_migration_source_expectations.py` 共 `18 passed / 1230.58s`。这证明 0155 组合在未全局启用直接执行缓存时同样通过；不要重复启动该句柄。较短的 opt-in 运行只是诊断加速证据。
- 下一步首先核实 KMS、私有 OSS、PNVS 的现有资源和安全配置路径；真实值只在目标机安全配置，不通过聊天、命令助手明文命令或日志传递。`.env.example` 是字段契约，不能直接当可上线配置：默认关闭写入/文件/短信，正式启动要求真实免密渠道和 KMS。已有候选数据库不得盲目重新生成密码或复用旧站凭据。
- 配置就绪后按现有 `PILOT_DEPLOY_ENTRY_20260922.md` 和 KMS runbook 做候选预检，再推进 API/Web、真实身份绑定、申请至个人仓入账的 HTTP/数据库回读。正式 PG16 CI、同版本发布证据、通知、备份恢复及 UAT 仍独立待验；无提交、推送或生产发布。

## 2026-10-05 继续开发：公开首页入口与退回入库客户端契约

- 公开首页已保持为无需登录的“交流备件知识大全”查询页；“星星后台管理”按钮现固定跳转 `https://rscwz.cn/xx`，私有仓库仍由 `build:warehouse` 生成 `/xx/` 入口。Feishu 知识源仍为 `pending`，没有伪造目录数据或把空目录当作公开资料已就绪。
- 前端证据：`PublicKnowledge.test.tsx` 与 `App.test.tsx` 共 28 项通过；公开模式 `pnpm run build` 和仓库模式 `pnpm run build:warehouse` 均通过（使用工作区 Node 24.19.0 / pnpm 11.19.0）。
- 退回入库 HTTP 测试曾因测试进程未继承工作区 Node 而出现环境失败；补入工作区 Node 后发现并修复真实契约问题：2.0 预览必须绑定原验收历史，测试现按小程序 `prepareInbound` 传入原验收记录。单项 `test_completed_inbound_seal_returns_exact_original_http_result` 通过；小程序 `stock-return-inbound` 契约 5/5 通过。
- 后端退回入库组合在有限窗口内取得 84 passed 后人工中断，第二次契约/事实/迁移组合取得 33 passed 后人工中断，均未跑到终态，不能记为整组通过；完整长夹具仍是待优化验证项。中断不代表失败，也不替代 PG16 原生门禁。
- 本轮将源码迁移链校验器改为静态读取 revision/down_revision 图，并只执行包含目标源码替换键的迁移或冻结 catalog；此前 0106 manifest 用例会递归加载全部后继迁移、内存升至约 4.9 GiB，现已降为可终态验证。退回入库事实 + 迁移组合最终终态为 `38 passed / 462.92s`；源码链辅助与 0106 迁移回归最新 `15 passed / 37.27s`，均保留 1 个 Starlette 弃用警告。
- 同一优化后的源码链对 0105/0103/0104/0111 扩展组合仍发现更早的 0155/0154 后继补丁链会长时间递归加载；组合在无终态时已中断，未把它记为通过或失败。当前只采纳上面的 0106 终态，扩展门禁仍需单独优化后再跑。
- 进一步修正了源码链校验：冻结 JSON 现在只解析 `patches`/`functions`/`replacedFunctions`/`readiness` 的真实 before/after 键，不再把 0165 catalog 中的触发器引用当成函数补丁；`test_migration_source_expectations.py` 与 0106 迁移回归已有 `15 passed` 的终态。0155 完整迁移 fixture 当时仍因其 predecessor 执行链超过观测窗口而中断，后续已通过受控缓存开关取得终态，详见下一条。
- 本轮为迁移源码校验增加了默认关闭的直接 predecessor 执行缓存开关 `OAM_MIGRATION_CACHE_EXECUTION_DIRECT=1`，并补齐动态 `SIGNATURE` 形式的 `_sources()` 识别；默认迁移语义仍保持每次直接 `runpy` 执行。缓存单测 `11 passed / 0.53s`，0155 退回收货迁移文件终态 `4 passed / 14.97s`，源码辅助 + 0106 组合终态 `15 passed / 36.73s`。这只证明本地迁移事实与 SQLite/SQL 门禁，未替代候选库或正式 PG16 运行门禁。
- 本轮 PostgreSQL 16/部署静态回归取得 `116 passed, 1 warning`；两个真实运行门禁明确失败关闭：`test_postgresql16_migration_acl_concurrency_and_kill_gate` 与 `test_postgresql16_stock_loss_release_gate` 均要求 GitHub Actions 的一次性 PostgreSQL 16、明确 `I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL` 确认和矩阵环境。本地或候选服务器不能冒充该证据，故不设置伪环境变量、不计为通过。
- 已恢复 Chrome 阿里云命令助手接管并完成只读候选状态核查：执行 ID `t-hz06z2efvitjls0`，退出码 0。回读显示候选 `rsc-pilot-20261004-fc7c926-dirty-db-1` 数据库容器在运行，但没有候选 API/Web 容器；`star-oam-web-1` 仍占用宿主 80/443，候选 HTTP 监听为空。未执行启动、重启、切流或旧站改写，因此候选仍不能作为可访问上线地址。
- 通过短命令只读回读候选容器标签：执行 ID `t-hz06z2f5gcw0b28`，退出码 0；Compose 项目为 `rsc-pilot-20261004-fc7c926-dirty`，配置文件位于 `/opt/rsc-pilot-20261004-fc7c926-dirty/docker-compose.yml`。前一次长命令 `t-hz06z2eu6yj9fk0` 仅因命令编辑器拼接出 Python SyntaxError 失败，没有服务器副作用；不把它当作应用或 SSH 故障。
- 候选配置键名只读核查执行 ID `t-hz06z2ff9fvt4ow` 退出码 0，但 `.env` 与 `.env.production` 均未输出，说明候选目录根部尚未落地 Compose 所需的密钥/KMS/身份/短信等配置；未读取任何值，也未尝试补写配置。该事实直接阻止候选 API/Web 启动和正式预检。
- Compose 声明只读核查执行 ID `t-hz06z2fmrdisj5s` 退出码 0，`docker-compose.yml` 直接插值 `POSTGRES_DB/USER/PASSWORD` 及 `OAM_DB_MIGRATOR_PASSWORD`、`OAM_DB_API_PASSWORD`、`OAM_DB_BACKUP_PASSWORD`、`OAM_DB_PROJECTOR_PASSWORD` 等变量，未发现可替代根目录 `.env` 的 `env_file` 或 secret 注入声明；不能以旧 `star-oam` 配置代替候选配置。
- 本地候选发布门禁回归 `test_pilot_preflight.py` + `test_pilot_deploy.py` 为 `100 passed / 76.57s`；这只证明脚本的拒绝条件和模拟部署行为，仍不替代真实候选的密钥、身份、迁移、HTTP 和切流证据。
- 当前 `test_pilot_deploy.py` 终态为 `38 passed / 48.29s`，新增覆盖迁移容器 helper/console-script 路径、1 GiB/1 CPU/256 PID 资源边界及执行缓存配置；这只证明 Compose 声明和部署脚本绑定正确，仍不代表目标机已启动。
- 本轮追加 `test_pilot_release_binding.py` 与 `test_pilot_preflight.py` 回归，终态为 `127 passed / 93.90s`；覆盖候选配置来源绑定、回执/镜像漂移拒绝、外部 secret/config/env_file 拒绝及预检失败关闭。仍属于本地脚本门禁，未改变远端候选“无配置、无 API/Web”的事实。
- 使用 `.env.example` 做本地只读预检时得到明确失败关闭：`status=fail`、`deploymentReady=false`、唯一环境阻断为 `docker_cli` 不可用；预检同时保留 HTTPS、短信/KMS、OSS、迁移/角色、身份/期初、备份恢复/UAT 为未验证项。该命令没有容器、网络或业务写入。
- 通知投递 worker 聚焦回归 `test_notification_delivery_operations.py`、`test_notification_unknown_outcomes.py`、`test_notification_delivery.py` 终态为 `36 passed / 8.31s`；这只证明本地事务、租约、未知结果和重试边界，仍未证明真实 provider、回调和生产投递。
- 当前仍未启动完整 Compose、未切流、未做真实身份/种子、API 全链路写后回读、通知渠道、备份恢复和正式 UAT；命令助手自动化接管接口本轮两次超时，未将其视为服务器登录或部署证据。

## 最新候选迁移回读（2026-10-05 05:46，优先于下文）

- 已通过 Chrome 阿里云命令助手在隔离候选实例 `Ubuntu-ipvk (118.31.37.87)` 上完成真实终态核对；本次未重启、停机、切换 80/443 或触碰旧服务。
- 候选迁移容器 `rsc-pilot-migrate-cache-2g-20261005d` 使用当前 `migration_runner.py`/有界脚本缓存，在 2 GiB、1.5 CPU 限制下退出 `0`，`OOM=false`；日志已推进到 `20261214_0165`。
- 候选数据库 `rsc-pilot-20261004-fc7c926-dirty-db-1` 的实际迁移库是 `rsc_check`（不是 `postgres`）。命令助手 `rsc-read-alembic-version-rsc-check-20261005` 回读 `20261214_0165`，ExitCode=0。
- 同一候选库门禁回读 `rsc-verify-candidate-db-owners-20261005`：六个应用角色 `edge_inbox`、`star_oam_api`、`star_oam_backup`、`star_oam_edge`、`star_oam_migrator`、`star_oam_projector` 均存在；`PG|16.15`；`rsc_check` 数据库和 `public` Schema 所有者均为 `star_oam_migrator`；ExitCode=0。缺少 `star_oam_edge` 是此前迁移失败的直接原因，已在候选库补齐。
- 临时环境快照 `/run/rsc-migrate-env-20261005` 已由 `rsc-remove-migration-env-snapshot-20261005` 清理，命令状态为执行完成；未打印或保存任何密码/令牌。
- 历史直接迁移仍保留为失败证据：1 GiB/2 GiB 旧路径曾以 `137`/`OOMKilled=true` 结束；本次缓存入口修正后才取得 `EXIT=0`。这组证据只证明隔离候选迁移/PG16/角色门禁，不证明生产部署或上线。
- 根据候选实测发现，已将 `star_oam_edge` 的迁移前置契约补入 `deployment/postgres-init/10-create-application-roles.sh`：新库初始化时创建并强制保持 `NOLOGIN`，不再依赖人工临时补角色；已有数据卷仍须按受控 DBA 角色迁移步骤补齐并回读，初始化脚本不会重写现有 ACL。独立 `edge_inbox` 仍按 `deployment/provision_edge_receiver_role.sql` 的显式授权流程处理。`test_database_deployment_security.py` 全部 16 项通过，脚本 `sh -n` 通过。
- 本轮本地证据：迁移入口/部署/源码期望聚焦 16 passed，通知投递/未知结果/调度器聚焦 42 passed，部署安全全文件 16 passed，`py_compile` 与 `git diff --check` 通过。通知全量 `test_*notification*.py` 曾运行超过 5 分钟未返回终态，已中止并明确不计入通过/失败；正式结论仍只采用有终态的聚焦证据。
- 当前仍未启动完整 Compose、未切流、未做 API/HTTP/写后回读、真实身份/种子、通知渠道、备份恢复和正式 UAT。下一步回到本地完成交接文档和候选应用启动前静态门禁，再决定是否进行隔离 API 冒烟；生产目标地址与旧服务保持不变。


## 估算偏差复盘（2026-10-03）

- **目标机候选实测（2026-10-03）**：已只读核验 `118.31.37.87` 为 Ubuntu 24.04/x86_64、Docker 29.1.3；现有 `star-oam-web-1` 仍占用 80/443，公网 `/` 和 `/xx/` 都返回旧的 `RSC个人仓` 登录页。当前工作树候选已复制到隔离目录 `/opt/rsc-pilot-20261003-fc7c926-dirty`，没有停止旧服务、切换端口或连接旧数据库。
- 目标机用阿里云 PyPI 镜像成功构建当前候选 API 镜像 `sha256:174bbaefd06e7f0cb74eb52dbfdeefd374b6243a9414e288aa5f96f92ee40d4a`（linux/amd64），Web 试点镜像 `sha256:ef420ebc666d8fb1590ee6cca39222285cb040e8023e9b178fca94334d36b8c2`（linux/amd64）；API 导入和静态双入口文件检查通过。官方 PyPI 构建因 `setuptools==80.9.0` 无可用版本失败，已保留为失败证据。
- **此前候选的 DB 镜像构建**在 Alpine `apk add python3` 阶段超过 10 分钟后主动停止并保留现场；该次没有生成 DB 镜像，已由 2026-10-04 的多阶段构建候选替代。旧站 `.env` 只有 9 个基础键，缺少当前 Compose 所需的数据库角色、KMS registry、短信/OSS、独立 HMAC 等配置；用它解析当前 Compose 已因空的 KMS registry 挂载失败。故当前仍不能执行 prepare/migrate/start，也不能把旧配置或旧镜像当上线凭据。
- 进一步只读定位显示：目标机宿主机访问 Alpine CDN 的 HTTPS 正常，但此前 `postgres:16-alpine` 容器内 `apk update` 在解析到 Fastly 地址后持续无响应；因此原始 DB 构建阻塞是容器到 Alpine 软件源的网络路径问题，不是 Python 代码或 PostgreSQL 16 迁移失败。已停止诊断容器，旧服务保持不变；固定 Python 多阶段构建已解除这一构建阻塞。隔离 Web 候选的实际双入口静态回归已通过：`/`、`/xx/` 均返回 200，`catalog_status=pending records=0`，所以只证明候选静态服务可启动，不能绕过公开目录门禁。
- 目标机候选回执和只读镜像状态保存在 `artifacts/pilot-target-preflight-20261003/target-preflight-receipt.json` 与 `target-readonly-build-status.log`；这两个文件只记录摘要、镜像 digest 和终态，不包含凭据。
- **2026-10-04 构建阻塞已解除（仍未启动 Compose）**：将 `deployment/backup/Postgres.Dockerfile` 改为从固定 digest 的 `python:3.12-alpine` 多阶段复制标准库，保留 `postgres:16-alpine` 基线，避免在目标容器内执行卡住的 `apk`。中间候选归档 `f67a37bca8179feb11b0b91aa0753805a2b0cb95d086135a64aeba785432633d` 已在目标机生成 amd64 DB 镜像 `sha256:1170d33c33e81725361dc6d50c831c70682c323f6bb0526541f5898f34d67eff`；临时 DB 容器的 `pg_isready`、`psql`、Python 3.12.15、PostgreSQL 16.15、API import、Web 双入口静态文件检查均通过并已清理临时容器。中间回执为 `artifacts/pilot-target-preflight-20261003/target-preflight-receipt-20261004.json`。这只解决镜像构建问题，未绕过配置、迁移、权限、目录和 UAT 门禁。
- **迁移接线修正后的状态**：API 镜像已补入 `migration_script_cache.py`，`migrate` 服务已补 `PYTHONPATH=/app`；两项修正各自的直接错误已定位并消除。隔离 `upgrade head` 已启动并输出 PostgreSQL 上下文，但超过观测窗口没有终态，期间目标机 SSH banner 超时；只中断了该隔离项目，最终迁移仍为 **unverified**，不是通过。迁移尝试和具体错误见 `artifacts/pilot-target-preflight-20261003/migration-attempt-20261004.log`，最终候选回执为 `artifacts/pilot-target-preflight-20261003/target-preflight-receipt-20261004-v2.json`。
- 重新核验时目标机仍可 ping，但 SSH 仍卡在 banner 阶段，无法确认隔离 Compose 清理状态；记录见 `artifacts/pilot-target-preflight-20261003/target-ssh-banner-timeout-20261004.log`。在恢复 SSH 前，不执行任何重试、旧服务停止或 80/443 切换。
- 本地完整前端门禁为 **143 个文件 / 2509 项通过**；部署、预检、双入口、数据库安全、备份配置和 PG16 拓扑聚焦门禁为 **194 项通过、1 个弃用警告**（含迁移容器接线和资源上限回归）。2026-10-04 的最新摘要回执为 `artifacts/pilot-target-preflight-20261003/local-gates-20261004-v4.json`；完整公开 release 仍准确失败于 `PUBLIC_CATALOG_NOT_READY`，pilot 静态档通过但不代表生产。

- 原先按 **1–3 个工作日**估算的是“隔离预生产中的可访问演示切片”，不是正式上线；目前这个目标尚未交付。过去两周实际完成的主要是 PostgreSQL 16 候选门禁、历史凭证兼容、初始请求封存和成色动作服务回归，耗时集中在长时间原生数据库门禁（单轮可达 28 分钟）及其失败后的精确修正。这些工作降低了数据破坏风险，但没有产出用户可直接打开的地址。
- 当前已核实的可见基础是：物资申请后端已有申请、审批、分配、占用、出库、发货、签收、OAM 收货和个人仓入库路由；PC/H5 前端已有 `/material-requests` 页面。前端聚焦测试 **92 passed**、warehouse 构建通过；后端物资申请聚焦测试 **197 passed**。这些只证明代码和测试环境，不证明预生产可用。
- 目前三个硬阻塞按优先级排列：①没有隔离预生产数据库/种子数据与真实身份绑定，无法做一条可重复的申请→审批→分配→发货→收货→个人入库演示；②物资申请链还没有同一提交 SHA 的正式迁移、权限目录、HTTP 冒烟和写后回读证据；③真实文件存储、通知渠道、物流/收货回执和用户 UAT 尚未授权或接通。默认终端还需显式使用工作区 Node/Python 运行时，不能把环境报错当业务通过。
- 可取消或冻结的范围：继续扩展成色纠正异常分支、执行/释放细分、人员调拨/离职交接、报表打印、真实短信/微信/飞书投递、性能与恢复演练。它们保留为正式上线门槛，不再占用当前演示切片的开发窗口。
- 下一步只做一条可审计演示链：准备隔离数据和测试身份，启动现有 PC/H5 `/material-requests`，逐步走完申请→审批→分配→发货→人工签收→个人仓入库，并为每一步保存 HTTP 请求、响应、数据库回读和权限版本。完成这条证据链前，不承诺上线日期，也不把本地测试数量当作上线进度。


## 当前执行快照（2026-10-03，优先于后面的历史过程）

### 用户可见交付计划（按当前真实状态，不把测试数量当完成率）

- **为什么现在看不到成品**：当前 HEAD 是 `fc7c9269e19f6afd180a0aced55b565f03c244b6`，工作树有 **299** 条未提交变更；当前成色纠正仍是私有候选服务，HTTP 路由、PC/H5 入口、正式迁移和生产部署尚未接通，也没有真实身份/文件/provider UAT。因此目前没有可访问的演示网址或已部署成品。
- **最早可见演示切片（1–3 个工作日目标）**：PC/H5 登录后由工程师提交一条“历史破损收货 → 成色申请/冻结 → 区域核实 → 总部批准/退回 → 申请人撤回/补证 → 只读回查”的完整链路；演示使用隔离预生产数据、人工上传/人工通知、执行和释放暂不纳入。必须先完成后续动作服务测试终态、HTTP 只读/写入口、最小 PC/H5 页面和隔离部署；不能承诺具体自然日。
- **小范围试用版本**：在上述链路通过真实预生产 UAT 后，采用邀请制 HTTPS H5、单区域、5–10 个用户、约20个 SKU，人工收货/物流/文件和通知，执行/释放与异常补偿由后台人工操作；仍缺正式迁移、真实 provider、备份恢复演练和回滚证据。
- **正式上线**：必须完成 V1 全部门槛：审批/执行/释放及后续份额、人员调拨/离职交接、报表打印、真实 OSS/短信/微信/飞书回执、真实身份端到端 UAT、授权迁移/期初、连续至少3天解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、回滚演练、同一提交 SHA 的 CI、正式迁移和部署授权。当前证据不足以给出可靠日期。
- **当前瓶颈与边界**：瓶颈是后续动作事务及其 HTTP/PC/H5 接线，随后才是正式迁移和真实 UAT；停止继续增加新的报损细分支线，先完成这条有限演示链。人员调拨、离职交接、报表、真实通知、性能和恢复继续作为独立上线门槛登记，不以本切片替代。


- 工作树 `06f6/oam`（使用当前既有工作树），分支 `codex/notification-delivery-worker`；保留全部未提交改动，未提交、推送或部署。
- **永久初始请求封存完整候选门禁通过**：原生v4数量 `run-ipsl7425`、SN `run-g5i0bg2p` 均 stopped/passed/serverExitCode=0；两库checks及源码清单已逐项核验，当前1700源与固定v5一致，各执行510条候选DDL，DDL摘要相同。日志 `artifacts/formal-0165-integration/return-condition-permanent-seal-native-v4.log`。旧句柄33904已不可用、进程不存在；父进程退出码未重新取得，不伪造exit=0，终态依据为两库checks/停止状态/完整日志。
- 两模式实际API提交通过：只有封存及唯一审计新增；缺审计/错原键/旧请求冲突/API裸写被拒绝；晚撤权提交拒绝；只读准确回查和重复封存不增加记录；独立迟到写入先实际阻塞、封存提交后其COMMIT拒绝；封存不可修改。普通`condition-seal:`前缀请求提交正常，伪造内部封存审计COMMIT仍拒绝。认证证据不再争用库存锁。
- 历史收货修正也在本轮复验：旧凭证权限版本1、当前用户版本2的原错拒已复现；前向修正保留历史收货/入库、当前新写入和六类坏凭证仍拒绝，原事实不变。独立聚焦26600此前已通过数量/SN，收据 `condition-receipt-history-forward-v1-receipt.json`，不覆盖后来新增前缀回归。
- 服务v3的4项（33119 exit=0，670.44秒）保留；已核验backend/app及这两份服务测试源码相较该批清单未改变，不称为本次重跑。最终候选收据目标为 `return-condition-permanent-seal-final-v1-receipt.json`；最终安全v57待终态，以收据为准。v56（55993 exit=1）仅发现交接文档包含本机个人目录，已改为可移植工作树标识，保留失败日志。
- 所有业务门禁均终态；不要重启或轮询旧26600/33904/20872。源码固定解除后可继续下批开发。旧失败与清单保留。

- 当前有限切片已新增 `return_condition_decision_requests.py`、`return_condition_decisions.py`、`return_condition_decision_recovery.py`，覆盖补证、撤回、区域核实/退回/驳回、总部退回/驳回/批准、取消批准的精确请求、当前权限、附件、事件链、审计/状态/Outbox/通知和只读回查；服务测试已终态：`return-condition-decisions-service-v1.log`，8 passed/1680.35秒；收据 `return-condition-decisions-service-final-v1-receipt.json`。覆盖状态链、精确回查、库存不变和事务异常回滚；这仍不是PG16提交证明。执行/释放库存动作尚未接入，先不扩展异常分支。
- **下一开发主线：先把现有物资申请链做成隔离预生产可回放演示，再回到成色纠正的正式迁移。** 当前成色纠正后续动作（执行/释放及其份额）冻结，不作为物资申请演示的隐含依赖；本次候选仍不是正式迁移或上线。
- 其余完整上线范围不变：审批代理、六类下游退回补偿、人员调拨/离职交接、UUID兼容、报表打印、真实文件及短信/微信/通知、真实身份端到端UAT、授权迁移/期初、至少三天有解释对账、500用户性能、RPO≤5分钟/RTO≤2小时恢复与回滚演练、同提交CI/发布。不得把组件通过当生产验收。

下方保留历史过程；旧“运行中/待验证”不覆盖本快照。绝不丢弃未提交改动，不因局部门禁通过自行提交或部署。

## 2026-10-03 接续：永久请求封存已接服务，正在验证（尚未通过）

### 当前终态：原生 v3 在历史收货附件校验失败

**聚焦最新结果：数量模式已通过，SN正在运行。** `run-0cx8a2uv` 为 stopped/passed/serverExitCode=0；`condition-receipt-history-checks.json` 实测旧凭证/收货权限版本1，当前用户版本2，修正前真实23514复现，修正后历史收货和入库校验通过；当前写入仍拒绝，6类错误凭证值（用途/上传人/人员/版本/缺完成/完成晚于收货）仍拒绝，历史事实和函数属主/ACL/搜索路径不变、无源码漂移。SN库为 `run-l1rgboke`，继续等待原句柄 **26600**。仓库安全 **95556 exit=0**，v55为2663文件/56090403字节；不代替业务门禁。进度收据 `return-condition-permanent-seal-progress-v4.json` 尚非最终收据。

前向候选 `forward_receipt_history.py` 已实现并完成 SQL/PLpgSQL 解析：旧函数体 SHA256 `13d4ffac2caf45c22be48e1a1441598cf58b0d5270f9c94420b9807577398ed4`，新体 `c12018e53c2d59697a3bbf421f499d1f35c69eb57fe38f286f9d53e7563cc1b0`。只在 `require_current IS FALSE` 时使用原凭证历史绑定；其余情况保留原当前身份绑定，原函数的收货权限版本对比和所有其他规则保留。尚未进入正式迁移。

原生聚焦运行 **26600** 已启动，数量库 `run-0cx8a2uv`，随后跑 SN；日志 `artifacts/formal-0165-integration/condition-receipt-history-forward-v1.log`，固定源 `return-condition-permanent-seal-source-v4.json` 共1700项。命令为既有原生脚本加 `--receipt-history-candidate`（同 PG16 binary 与 preserved 0157 source）。该模式不运行新成色提交/封存完整 COMMIT，只验证旧错误复现、历史收货/入库校验、当前写入拒绝、6类原凭证错误值拒绝、函数安全目录及历史事实不变。运行期间不修改受测源码；等待此句柄终态，不重启旧20872。后续仍需完整 `--submission-candidate` 两模式门禁。

最新日志 `artifacts/formal-0165-integration/return-condition-permanent-seal-native-v3.log` 已记录失败；数量库 `run-wrlm1s76/cluster-state.json` 为 stopped/failed/serverExitCode=0，SN 未开始，不再将 20872 视为存活进程。旧迁移、旧业务、当前迁移、候选安装与认证兼容均已通过；在新封存登记的来源复核中，`rsc_check_stock_return_inbound_0111` 调用 `rsc_check_loss_receipt_0155(...,false)`，触发 SQLSTATE 23514：0105 exception file purpose, recipient, completion or original binding mismatch。封存缺审计 COMMIT 探针尚未到达。

静态定位发现：0155 的历史分支仍复用 0105 `require_current_identity=True` 的附件绑定，要求上传人当前权限版本与旧附件一致。新增动作授权会更新当前权限版本；正准备原生复现及准确函数体前向修正，尚不能称为已修复。历史校验须保留当时上传人、人员、权限版本、用途、完成时间与收货绑定，新收货仍须实时身份/权限。禁止修改旧迁移、重标旧附件或关闭约束。

本段优先于下文“v3 运行中”和旧源码固定状态。当前整轮已失败停止，可以继续修改候选并建立新源码清单；旧清单和失败证据必须保留。未提交、推送或部署。

### 最新阻断与修正：0165认证提交仍争用库存锁

原生v2 **29522 exit=1**，数量库 **run-x8brzp1a** 已核验stopped/failed/serverExitCode=0；SN模式未开始。失败在新增独立连接认证提交测试，真实SQLSTATE **55P03**：`rsc_fence_scrap_seals_0165()`在COMMIT时无条件锁定inventory_ledger_heads。新成色约束已排除普通认证，但旧0165报废封存约束仍造成锁等待。不能计整轮通过，也尚未完成新永久封存COMMIT验证。服务v3的4项通过、目录观察及输入规范化观察仍是各自范围的证据。

新增 `return_condition_candidate/forward_seal_auth.py`，从固定0165 catalog派生前向替换，安装前必须核验准确原函数体。只排除现有适配器产生的普通认证审计和符合准确契约的认证状态；保留报废封存动作标记、异常元数据及库存引用继续走原有锁和约束。保留原函数其余逻辑、属主、SECURITY DEFINER、search_path与ACL，不修改旧迁移或关闭触发器。原生组合门禁已将认证兼容和缺审计拒绝探针提前，并增加阶段日志。

聚焦脚本 `scripts/run_local_pg16_condition_auth_fence_checks.py` 已终态通过，**90734 exit=0**，日志 `artifacts/formal-0165-integration/condition-auth-fence-forward-v1.log`，隔离库 **run-k12qo4dv** stopped/passed/serverExitCode=0。流程是新建自有socket-only PG16、升级当前0165、先复现原锁超时，再应用准确前向补丁，验证独立API认证提交及三类不能排除的反例、无库存/封存写入、运行角色仍无私有函数EXECUTE。已实际复现原55P03；修正后普通认证审计和状态COMMIT通过，reserved_marker/malformed_state/inventory_reference三类反例仍不能越过库存锁；库中无库存交易/封存新增，私有函数无运行角色EXECUTE，sourceDrift=[]。这不是完整成色业务门禁或正式迁移通过。

已固定新清单 `return-condition-permanent-seal-source-v3.json` 共1698项，启动整轮原生v3：句柄 **20872**，日志 `return-condition-permanent-seal-native-v3.log`，数量/SN均须取得终态，当前数量隔离库为 **run-wrlm1s76**。相对服务v3所用v2清单仅改变前向SQL编译器、两个原生助手和新增聚焦脚本4项；backend/app及服务测试源码未变，保留服务v3的4项证据，不将其称为重跑。当前源码固定中，不要修改受测文件或重启存活进程；不要轮询已终态29522/90734。其余完整上线范围不变，未提交、推送、部署或写外部业务。


本节优先于下方历史记录。新增第七张隔离候选表 `stock_condition_request_seals`，保存完整原输入及来源引用；数据库受控登记从原键计算全局摘要和11个别名。新增来源/权限证明、唯一审计校验、不可变保护及34张请求表和4张效果表的迟到写入约束。新增 `return_condition_seal_reads.py`、`return_condition_request_seals.py`，原请求回查接入sealed分支；新增关闭操作先返回已执行/已关闭结果，否则在锁内复核准入、登记并追加审计，由调用方提交。关闭不生成库存流水、状态进展、Outbox或通知。

这些实现仍为私有候选，**没有正式迁移、HTTP/页面或生产部署**。七表构造与Python解析通过，162项新增候选SQL及PL/pgSQL已解析通过，但解析不能证明实际数据库行为。

首轮固定清单 `artifacts/formal-0165-integration/return-condition-permanent-seal-source-v1.json` 共1696项。服务 **80757 exit=2**：数量模式在“删除审计”反例处触发既有外键，1失败后主动中断；正向sealed、动作撤权/保管结束、原输入冲突已执行到该位置，但不计整组通过。已改为审计关联错位，不删除审计、不关闭外键。原生 **86092 exit=1** 为修改源码主动中断，**run-oajtyvp8** 核验stopped/failed/serverExitCode=0，完成旧迁移/edge配置，但不计原生业务通过。

服务v2 **53902 exit=0，4项通过/546.37秒**，日志 `return-condition-permanent-seal-service-v2.log`。两模式sealed及原found/unknown回归通过。之后补充认证域同UUID的读侧排除与保留标记/伪认证拒绝反例，需要新一轮服务结果，不能沿用v2证明新分支。

当前固定清单 `return-condition-permanent-seal-source-v2.json` 共1696项。服务v3 **33119 exit=0，4项通过/670.44秒**，日志 `return-condition-permanent-seal-service-v3.log`，含补充的认证同UUID排除、保留封存标记和伪认证拒绝。当前1696项源码与固定v2清单仍一致；中间进度收据 `return-condition-permanent-seal-progress-v2.json` 不是最终验收收据。原生v2 **29522 exit=1**，失败和后续修正见本节最新说明；v2清单仅证明失败运行时的源码，不代表新增前向补丁。 当前数量库为 **run-x8brzp1a**。只读目录观察脚本在 `artifacts/formal-0165-integration/observe_condition_seal_catalog.py`；首次检查候选尚未安装；后续只读观察 **70098 exit=0**，数量库已核验10个SECURITY DEFINER函数的属主/固定搜索路径、78个ALWAYS触发器，以及5种运行角色的表/函数最小权限。API和备份仅能读取封存表；仅API可执行登记函数，其余私有函数无运行角色执行权限。证据 `run-x8brzp1a/condition-seal-catalog-observation.json`。这是当前隔离候选目录观察，不替代正式运行时目录或整轮业务终态。 同库另有 **21331 exit=0** 的SQL READ ONLY输入函数观察：数量/SN两种完整规范输入与Python摘要一致；额外字段、JSON浮点数量、未裁剪理由、重复附件、零来源UUID共10个反例均以23514拒绝。证据 `condition-seal-canonical-observation.json`，明确sourceProven=false、durableSeal=false，不替代真实来源/封存COMMIT。当前源码清单复核无漂移，diff检查通过；最终仓库安全检查和整轮收据尚未生成。

原生运行停止后，还修正候选触发器对认证记录无条件取得库存锁的问题：按既有准确认证事件契约排除，保留封存保留标记检查。独立原生检查 **21038 exit=0**，**run-f7503_e5** stopped/passed/serverExitCode=0，7个认证排除/异常不排除判断通过；这是单个数据库函数的隔离验证，不是关闭COMMIT或全量权限目录验证。完整原生助手已增加两个独立API连接下“持有库存锁时，真实认证证据适配器仍可提交”的测试，尚未执行。跨业务锁顺序与旧服务兼容仍是正式迁移前必须完成的门槛。

服务覆盖原请求found/unknown回归及sealed只读回查、动作撤权/保管结束后保留、原输入冲突、来源/审计损坏拒绝；SQLite封存行是明确测试夹具，不是数据库写入证明。新增原生助手计划验证真实API关闭COMMIT、唯一审计且全表无库存效果、缺审计提交拒绝、旧键冲突、迟撤权拒绝、独立连接迟到写入、重复关闭不变及历史读取。结果尚未终态，不计通过。

完整生命周期（审核/执行/释放、后续份额）、正式迁移/权限目录、HTTP/PC/H5、真实文件和渠道、人员调拨/离职交接、其余退回补偿及全部原上线门槛继续保留。未提交、推送、部署或写外部业务。

## 2026-10-03 接续：未知请求封存准入与原输入保全验证通过

新增私有只读 `return_condition_seal_admission.py`：要求当前真实身份、准确来源区域的 inventory/read 与 stock_operation/read，以及 submit_return_condition 动作权限；只能处理原入库保管人自己的请求。独立检查有效组织树，复核完整旧入库/纠正历史、33表请求坐标和审计/状态/Outbox/通知残留，并在长查询后再次检查当前权限与观察边界。当前保管责任结束不等于旧来源失效，因此没有复用新增库存操作的当前保管校验。

返回值保留完整原命令的规范 JSON、摘要和全部键别名，原 source hash 与扫码输入原样保留，不使用今天的 SKU/余额重造旧输入；这不证明未保存的旧预检或实物扫码为真。明确返回 absence_sealed=false、retry_allowed=false、current_stock_verified=false。该组件没有写接口，没有插入封存，也不是可传给库存过账的授权凭据。已执行的准确原请求仍走既有只读回查，不能因动作权限后来撤销而丢失历史结果。

服务v1 **28393 exit=1**：4通过、1失败/256.84秒，失败为新测试将Outbox构造字段误写为event_key（实际为idempotency_key），还没进入该反例的业务检查。已修正测试字段并独立核验构造；产品代码未放宽。为修正后固定源码，原生v1 **79504 exit=1** 主动停止，数量库run-lwx9ya38已核验stopped/failed/serverExitCode=0，不计整轮通过。

服务v2 **25180 exit=0，8项通过/484.28秒**，日志 `artifacts/formal-0165-integration/return-condition-seal-admission-service-v2.log`。数量/SN两模式均验证结束保管后新动作拒绝而封存准备可只读进行、完整旧输入原样保留、当前身份/查看/动作权限与原保管人限制、旧入库请求冲突、损坏历史、普通Outbox残留、长查询后动作/查看权限撤销拒绝，以及已执行原请求在新动作权限撤销后仍可只读回查。全表快照检查准备/拒绝不写入。仅既有anyio弃用提示；SQLite服务检查不代替数据库封存COMMIT证据。

原生v2 **17270 exit=0**，日志 `return-condition-seal-admission-native-v2.log`。数量 **run-ohk5jitk**、SN **run-ctf2lj_w** 均核验stopped/passed/serverExitCode=0、sourceDrift=[]，两库源码清单与固定1687项及当前源码完全相同。每个模式从实际0157旧应用生成历史并升级0165，346项候选安装、既有完整首次申请/冻结、真实提交/回滚、权限/键/业务效果约束与旧历史保留均继续通过。

两模式新增absenceSealAdmission检查均通过：真实API执行SQL READ ONLY准备、结束保管后新动作拒绝而旧请求封存准备允许、长查询后动作权限撤销拒绝、完整旧输入保留。夹具变更由所属隔离库所有者在事务内设置并全部回滚。该检查明确durableSeal=false、lateRequestCommitFence=false，不能称为永久封存已提交或封存后迟到请求已被数据库拒绝。对象存储仍为FakeStorage，没有真实渠道投递验收。

最终证据归档在 `artifacts/formal-0165-integration/return-condition-seal-admission-final-v1-receipt.json`，固定清单为 `return-condition-seal-admission-source-v2.json`，当前仓库安全检查日志为 `repository-safety-v54.log`，以收据中的终态和摘要为准。本轮较业务效果收据仅增加准入产品模块、服务测试和原生助手并修改原生组合门禁；其余产品源码未变。所有业务测试已终态，无待轮询句柄，源码固定解除。

永久封存登记表、数据库受控登记与审计、双向迟到写入约束以及封存原请求回查仍待实现；本轮不改变 unknown 的业务行为。完整审核/执行/释放、HTTP/页面、正式迁移及全部上线门槛继续保留。未提交、推送或部署。

## 2026-10-03 接续：首次纠正业务效果数据库约束候选验证通过

新增 `return_condition_candidate/business_effects.py/.sql`，组合在持久键登记之后。4个私有数据库函数和7表触发器从真实纠正案件/事件重构 `condition_result/1`，要求业务审计属于真实inventory审计链，状态、Outbox、通知头与准确person目标一一对应；按业务引用或派生键的并集要求唯一，不能通过错绑重复记录绕过。OLD/NEW两侧都检查，禁止移出命名空间、改业务字段或删除。Outbox重试/调度元数据和通知status可变，不重新授权历史申请人，不将expanded当成送达。仍是候选，尚未加入正式迁移/运行时目录。

新增原生助手 `pg16_return_condition_effect_gate.py`，已验证实际API提交时缺审计/状态/Outbox/通知/目标分别拒绝、额外重复效果的真实COMMIT拒绝、所有者修改/删除仍拒绝，以及通过已有通知展开服务以真实API角色提交、幂等重复展开后原请求可SQL READ ONLY回查。Outbox运行角色裸UPDATE必须拒绝；其元数据兼容性仅由隔离库所有者提交探测并恢复，不称为生产调度器。通知expanded及新queued记录合法保留，快照只准许准确事件的这一个状态变化和准确新delivery IDs，其他事实必须相同；没有调用提供方。原生跨动作残留探针改用普通未知业务类型，因为保留的stock_condition标记现在会在数据库入口拒绝，普通残留仍须由服务回查识别。原数量/SN登记、原输入、完整历史、权限和并发用例保留。

静态解析：4个Python文件、29个候选编译项及4个PL/pgSQL函数通过，仅语法证据。原生v1（94065/run-ipe2ls5f）在复核发现测试省略范围过大后主动停止，exit=1，所属库已stopped/failed/serverExitCode=0；不计通过。已将省略记录限制为准确stock_condition_event，保留库存流水自身的Outbox/状态。

原生v2 **5840 exit=1**，数量库 `run-uejyzm7j` 已stopped/failed/serverExitCode=0。缺5类效果、额外4类效果和所有者不可变字段拒绝已执行，但队列元数据测试错误地用API裸UPDATE Outbox，遭42501权限拒绝，整轮不计通过。核对现有权限后保留该拒绝；改用真正的API `expand_notification_event` 验证通知展开，不放宽权限，也不把expanded强行改回pending。Outbox调度入口未获本轮验证。

数量v2在实时PID/datadir/systemIdentifier归属核验后，只读检查过候选目录：4个函数均属star_oam_migrator、SECURITY DEFINER、固定pg_catalog/public搜索路径，5种运行角色均无EXECUTE；7表共14个触发器均ENABLE ALWAYS。证据为 `run-uejyzm7j/condition-effects-catalog-observation.json`，仅本地候选权限观察，不是整轮通过。

原生v3 **24300 exit=0**：数量库 **run-2lefyook**、SN库 **run-vfhp4zbl** 均已核验 **stopped/passed/serverExitCode=0**。日志 `artifacts/formal-0165-integration/return-condition-effects-native-v3.log`；两个模式各执行346项候选安装，缺5类效果、额外4类效果的实际COMMIT拒绝及4种所有者不可变修改/删除拒绝均通过。API裸改Outbox继续42501；所有者可变元数据提交/恢复及真实API通知展开、重复展开和展开后原请求SQL READ ONLY回查通过。原输入、登记、并发、晚撤权、故障回滚及旧历史保留继续通过。

两个模式均为 `createdDeliveryIds=[]`，只证明无新投递记录时的展开状态兼容和回查，不能称为非空渠道投递去重或真实送达验收；Outbox所有者探针也不是生产调度器。最终通知status为expanded，未强制回写pending。业务单仍为awaiting_regional，仅首次申请/冻结，不等于已经纠正成色。

固定清单 `return-condition-effects-source-v3.json` 共1684项，已与两库终态及当前源码逐项核验一致。与上批持久键收据相比，仅新增2个候选SQL编译文件、1个原生测试助手并修改2个原生助手；537个backend/app源码文件未变，未将上批11项服务回归冒充本轮重跑。当前SN库的 `condition-effects-catalog-observation.json` 另保留4个私有函数、14个ALWAYS触发器与5种运行角色无EXECUTE的实时只读证据，不替代正式运行时目录。

最终收据 `artifacts/formal-0165-integration/return-condition-effects-final-v1-receipt.json` 记录终态、检查/DDL/源码摘要及文档补齐后的 `repository-safety-v53.log`。所有业务测试已终态，无待轮询测试句柄，源码固定解除；未提交、推送、部署或写外部业务。

下一步继续原键缺失封存、完整审核/执行/释放和后续份额、HTTP/页面、正式迁移及原完整上线门槛；不能以审计/通知数据库约束替代完整业务验收。

## 2026-10-03 接续：持久键登记与迟到请求约束候选验证通过

本节优先于后续历史状态。新增 `return_condition_key_schema.py`、`return_condition_keys.py` 和候选 `request_keys.py/.sql`。在原四表及原输入表之后组合第六张数据库拥有的登记表，绑定事件/案件/用户/人员/请求/摘要/时间，保存全局摘要与11个派生键别名；裸键不保存到登记行。产品登记函数仅支持PostgreSQL；SQLite测试通过明确测试替身插入登记，不能据此声称数据库授权或并发通过。

登记表API仅可SELECT，唯一受控函数从真实事件和完整原始键派生记录，校验当前动作权限；事件缺登记不能COMMIT，登记不可修改或删除。跨动作检查扩为33表；正向登记检查既有旧动作/登记/封存，反向触发器使旧表写入也取得共享库存头锁，并在提交时再次检查冲突。所有锁写入要求READ COMMITTED。没有替换旧函数体或重写历史事实；新增约束仍是隔离候选，正式迁移、运行时目录及完整业务效果SQL还未完成。

提交事务已接登记，完整历史采集登记并核对事件集合，按完整原请求回查再核对全部键摘要。**永久缺失封存仍未实现**，未知请求继续禁止重发。本轮新增原生检查已验证API裸INSERT/UPDATE/DELETE拒绝、遗漏登记的实际COMMIT拒绝、错原键拒绝、已存在真实旧入库请求拒绝、双向旧发运头键碰撞、不可变登记及两个独立API连接的真实等待/提交竞争。合成旧发运头只用于键约束证明，不声称发生实物发运。

服务 v3 **78120 exit=0，7项通过/616.59秒**；跨动作回查回归 **12942 exit=0，4项通过/482.92秒**，日志 `return-condition-keys-coordinate-regression-v1.log`。两组共11项互不重复的当前源码测试，SQLite登记为测试替身、篡改为服务拒绝证明，不是数据库权限证据；仅有既有anyio弃用提示。

原生 v2 **85607 exit=0**：数量库 **run-sxkpudhm**、SN库 **run-65vsuqnx** 均核验 **stopped/passed/serverExitCode=0**。两个模式分别从实际0157旧应用生成历史并升级0165，执行317项候选安装；真实API整笔提交/回滚、缺登记实际COMMIT拒绝、错误原键/旧入库请求拒绝、双向迟到旧发运头拒绝、独立API连接真实阻塞后新提交成功/旧头拒绝、登记不可变与最小角色权限均通过。原完整历史和SQL READ ONLY回查继续通过；通知仍为独立pending状态。合成旧发运头只证明键约束，旧服务在安装候选后正常新写入的完整兼容性仍待另验。

主日志为 `artifacts/formal-0165-integration/return-condition-keys-service-v3.log` 和 `return-condition-keys-native-v2.log`，固定清单 `return-condition-keys-source-v2.json` 共1681项，已与两库终态及当前源码逐项核验一致。基础回查 v1（42322）exit=0、2项/167.88秒通过；编译136条候选语句已过SQL解析，仅为静态解析。服务v2（15983）exit=1，纯结构测试误继承业务autouse夹具且未指定stock参数，已拆到独立结构测试模块；未改业务规则。原生v1（56215/run-pyixqng0）为修正该测试主动停止，exit=1，库已核验stopped/failed/serverExitCode=0，不计通过。

最终收据为 `artifacts/formal-0165-integration/return-condition-keys-final-v1-receipt.json`，记录两组服务终态、两库checks/状态/清单和SQL摘要及当前安全检查。所有业务测试进程已终态，无待轮询测试句柄，源码固定解除。先前安全v51（34401）已通过；文档补齐后的最终安全状态见收据及 `repository-safety-v52.log`。

下一步补业务效果反向SQL、未知请求永久缺失封存，再接审核/执行/释放、后续份额、HTTP/页面及正式前向迁移/运行时目录。全部真实渠道、UAT、历史迁移/期初、三天对账、500用户性能、RPO/RTO和回滚演练等正式上线门槛保持未完成。未提交、推送或部署，无生产或外部业务写入。

## 2026-10-03 接续：跨动作请求冲突与残留证据检查通过

本节优先于以下历史记录。新增 `return_condition_coordinates.py`，检查 32 张业务/登记/封存/库存表和 11 个键命名空间，补旧处置封存两种键别名、既有登记全局键摘要、请求审计/状态及无法排除归属的 Outbox/通知。已接入首次提交的写前检查及原请求只读回查；回查两次比对请求证据，并在长查询之后再次核验当前查看权限。无记录仍为 unknown，禁止自动重放。

这只是服务侧检查，**未完成持久键登记、反向数据库约束或缺失结果封存**。完整实施边界见 `RETURN_CONDITION_REQUEST_RECOVERY_CONTRACT.md`。没有开放新 HTTP 路由或生产权限，没有提交、推送、部署。

服务 v2 **34358 exit=0，8 项聚焦通过/804.16 秒**。数量/SN分别覆盖11种键别名、7种既有登记碰撞；无结果与已有结果下的状态/Outbox/通知残留、伪称其他操作者的无归属消息、读取期间新增消息、第二次坐标扫描末尾撤权均拒绝。准确原请求恢复、原理由/键冲突及完整历史损坏回归继续通过。服务日志只有既有 anyio 弃用提示，无失败；SQLite修改记录只证明服务拒绝，不是数据库防篡改证据。

原生 v3 **23666 exit=0**，数量库 `run-142n4dhn`、SN库 `run-hkcan7ym` 均核验 **stopped/passed/serverExitCode=0**，1674项源码与两库及当前工作树逐项一致。每模式增加7种提交前/无结果检查（准确旧入库请求号1种，状态/Outbox/通知按原请求号或派生引用6种）及已有结果下6种残留拒绝；合计26种场景。合成残留均仅在API角色事务内插入并回滚，整库事实快照保持一致，没有关闭数据库守卫。实际首次冻结COMMIT、原输入约束、晚撤权COMMIT拒绝、原入库回查和SQL READ ONLY原纠正回查继续通过。此处证明服务识别既有冲突，不证明未来迟到写入已被持久封存。

日志为 `artifacts/formal-0165-integration/return-condition-coordinates-service-v2.log`、`return-condition-coordinates-native-v3.log`，源码清单为 `return-condition-coordinates-source-v3.json`。最终收据为 `return-condition-coordinates-final-v1-receipt.json`，包含当前安全检查、两库状态/checks/source清单摘要和本轮静态基线审计。服务与原生验证均已终态，源码固定解除。

前序服务 v1（27260）为 2 通过、2 失败后主动停止，exit=2：残留 Outbox 测试漏填必填 available_at，已补测试数据，未放宽约束；此轮也不含后补的末尾撤权测试，不是最终证据。原生 v1（50183/run-d2o1r6l7）为修正最后权限复核顺序主动停止，v2（55802/run-2a_ceeal）为补测试数据主动停止，两次均 exit=1、不计通过；两库已核验 stopped/failed/serverExitCode=0。安全 v49（47223）exit=0、2636 文件/55889881 字节通过，但后续测试和文档已变化，最终归档前再跑当前快照。

下一步继续持久键登记、未知结果封存、业务效果反向 SQL、审核/执行/释放、后续份额证明、HTTP/页面、正式迁移和运行时目录。其余正式基线缺口、真实渠道/UAT/迁移/期初/三天对账/性能/RPO/RTO/回滚继续保留；局部通过不等于上线。当前完整缺口复核见 `FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md` 顶部，本轮不沿用已过时的“报废执行不存在”结论。

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

## 2026-10-03 接续：新动作最终提交权限候选

新增 `return_condition_candidate/authority.sql`：读取并锁定真实当前用户/人员/身份/角色权限图，按动作限定当前区域负责人或全国总部管理员，核验准确区域仓、当前唯一保管责任及同一保管责任记录；在锁取得后使用数据库时钟检查授权和责任到期。专用动作权限必须来自正确角色与准确范围，不拼接其他角色许可，匹配 deny 优先。函数私有，不给运行时直接 EXECUTE。

新增 `authority_event.sql` 及编译入口 `authority.py`：从准确持久事件、纠正单及原1.0入库行派生区域和保管坐标；申请动作绑定原用户/人员，审核人与申请人独立，总部与最近一次区域核实人独立。仅新 INSERT 事件触发延迟权限检查，不重授权旧审核历史。它尚未接入正式迁移、完整原子写入或生产权限；审批代理尚待独立版本化授权合同。

83534 exit=1：首轮在复用旧恢复夹具初始化时触发 `uq_permissions_definition`，0165 已包含 apply_scrap_recovery 权限，未进入新检查。隔离库 run-e99e2q7o 已 stopped/failed/serverExitCode=0。改为独立构造本功能用户、区域仓、保管责任和专用权限，未放宽业务规则；同时消除旧夹具同区域重复授权风险。首轮日志 `artifacts/formal-0165-integration/return-condition-current-authority-native-v1.log` 保留。

7488 exit=1：第二轮 run-s2scdegl 已 stopped/failed/serverExitCode=0。在全部动作权限、延迟提交拒绝和到期检查后，并发新增责任记录因缺少结束时间被 `uq_custody_assignments_current_location` 提前拒绝，尚未证明预期锁等待。测试补齐非重叠的起止时间，不放宽业务约束；首两轮日志保留，不计为全组通过。

89296 第三轮运行中，日志 `artifacts/formal-0165-integration/return-condition-current-authority-native-v3.log`。测试使用完整0165真实身份目录及仅测试用的命令表，验证 BEFORE 和真实 COMMIT、到期、撤权锁等待与依赖行锁；事件包装函数目前仅编译和未知事件拒绝，不能把此探针称为真实纠正单写入。SQL/PLpgSQL解析8条已通过。74663 exit=0：原41项权限服务回归通过/101.20秒。安全v36为第二轮源码终态（97373 exit=0），第三轮源码安全v37正在运行；终态前固定源码。仍须实际事件绑定、历史保留、完整流水/审计/Outbox及请求恢复、页面和正式迁移验证。未提交/推送/部署。

## 2026-10-03 附件绑定兼容性终态及下一步权限检查

77206 exit=0：数量 run-_o4z1yk6、SN run-e8xgbypq 均 stopped/passed/serverExitCode=0。1644 源文件在终态归档前与运行快照逐项一致，实际158条结构DDL及6条文件DDL的哈希均吻合。239旧表分别564/577行、415原函数、856外键保持；候选结构、临时授权和文件测试数据全部回滚，运行时目录恢复。最终收据 `artifacts/formal-0165-integration/return-condition-binding-final-v1-receipt.json`，取代下文77206运行中状态；源码冻结解除，未提交/推送/部署。

下一步正在实现新成色纠正动作的提交时当前权限保护。仍须完成真实事件绑定写入、原子流水/审计/Outbox、请求恢复、完整页面和正式迁移；本轮附件组件及旧历史兼容性通过不代表这些已完成，也不代表生产验收。

## 2026-10-03 接续：纠正事件附件绑定与跨用途隔离

新增内部`return_condition_event_files.py`，按准确持久事件UUID读取文件关联，使用事件保存的上传用户/人员/权限版本及时间核验已完成文件；逐项比较元数据摘要、事件附件清单和关联时间，拒绝事后替换。历史证明不重新授权旧上传者，人员后来停用不抹掉历史；该函数不授当前查看权限，不能单独用于签发下载链接。

候选`return_condition_candidate/evidence.py`新增4私有函数、3个延迟反向触发器及20个跨用途入口触发器，共54条DDL。基于完整0165模型覆盖19类带文件引用的表（含没有真实FK的旧UUID引用），并补日审JSON附件；不允许专用纠正凭证进入其他业务。准确事件最多20份附件，申请/补证/区域核实必须有凭证；完成元数据、原上传身份、用途/provider、时间、请求摘要和关联摘要相符。files单独变化及先验证后再变化均重新核验。公共文件用途/下载仍未开放，完整纠正写入和当前权限COMMIT栅栏仍待集成。

**4955 exit=0：附件读取与上传服务46项通过/14.08秒**。13269 exit=0：54条SQL/PLpgSQL解析通过；80980首次解析暴露生成文本多余加号，已修正。14079首轮组件因第二事件沿用序号1被原历史顺序守卫先拒绝，已改为正确序号2，保留日志return-condition-binding-native-v1.log；没有放宽唯一性或顺序规则。

**10765 exit=0：附件组件run-gt3ar9we终态通过，152拒绝、6正向**；覆盖数量/批次/SN/批次+SN、完成凭证补证→核实→批准→执行、20份附件上限正向；重新计算摘要仍不能借旧用途/他人/其他版本/错误时间/未完成凭证，文件事后改变与先验证后变更在真实COMMIT拒绝。跨事件复用由唯一性约束即时拒绝，跨业务引用在入口即时拒绝；152项中64项在真实COMMIT拒绝、88项在语句执行时拒绝；不能把全部152项写成COMMIT拒绝。**14085 exit=0：原命令组件run-qbft5ujr的110拒绝/8正向回归通过**。两库均stopped/passed/serverExitCode=0，1644源与当前一致；收据return-condition-binding-component-v1-receipt.json，日志return-condition-binding-native-v2.log、return-condition-binding-identity-regression-v1.log。组件使用最小合成库存父表和完成附件元数据，真实上传服务由前一双模式门禁独立证明；不合并声称完整业务提交已通过。

当前**77206**旧应用真实历史双模式兼容性运行中，数量库run-_o4z1yk6，日志return-condition-binding-compatibility-v1.log，命令为原检查器追加--regional-source-candidate --file-candidate --evidence-candidate。源码固定，终态前不改backend/scripts。候选结构包含158条DDL；将检查旧事实、全部原函数/外键和权限目录恢复。安全22400 exit=0：v34通过（2606文件/55611673字节）；diff检查通过。

下一步：本轮终态归档后补当前查看范围下的完整纠正历史/附件下载、准确上传字节校验、完整事件写入及新动作COMMIT权限保护、请求登记/封存/恢复和正式迁移/运行时目录。继续完整上线目标及其余基线缺口，未提交/推送/部署。

## 2026-10-03 接续：成色纠正专用上传和完成证据

新增内部`return_condition_evidence.py`及独立用途`return_condition_evidence`。共用文件服务按真实当前身份，限定准确角色/范围/动作：区域申请、补证、核实；全国总部审核或取消批准。旧submit_loss/finalize_loss权限、工程师角色或另一角色携带的纠正动作不能拼成上传权限。正式文件HMAC幂等、私有存储路径、防覆盖上传头、完成时HEAD核验及库存审计链保持；完成核验绑定准确用户、人员、权限版本、provider、用途、声明的内容摘要和元数据摘要。当前共用完成协议比较HEAD中的摘要元数据，尚未读取对象字节重新计算SHA256；真实OSS内容校验和实物证据均不得据此宣称完成。历史完成证据使用原事件的身份坐标，不因上传者后来停用而失效。它不证明实物正确或单据独占绑定。

公共`FilePurpose`尚未开放新用途，共用下载对该用途明确拒绝，避免未完成的纠正历史/附件绑定校验被“无绑定附件”分支绕过；待专用单据附件读取、单次绑定及反向约束齐全再开放。没有生产授权或正式Alembic head变化。

候选`backend/alembic/return_condition_candidate/files.py`保留0143文件函数全部旧用途保护，添加6条独立DDL：锁定真实权限图后按数据库当前时钟核验纠正上传权限；INSERT/UPDATE及延迟约束均核验；记录原函数body和候选body哈希。仅在隔离PG16事务验证后整体回滚，尚未进入正式迁移或运行时目录。延迟约束检查不等于整个纠正业务已在COMMIT受保护。

**25782 exit=0：90项专用/原报损/共用文件服务测试通过（23.93秒）**，日志`artifacts/formal-0165-integration/return-condition-evidence-service-v2.log`。首轮87710有89通过/1夹具错误（工程师被配置组织范围）；已改为正确本人范围并由全组终态覆盖。**67464 exit=0：6条候选SQL及PL/pgSQL解析通过**。

原生**8121 exit=1**：旧升级、旧业务和当前升级通过；附件反例试图将权限改名为已有submit_loss，触发uq_permissions_definition，未到预期延迟权限拒绝。隔离库run-2ps9k2t7已stopped/failed/serverExitCode=0。修复为准确查询确认不存在的测试动作名，未放宽业务约束。

同时补`completed_evidence(recorded_at=...)`：完成时间不得早于创建或晚于准确事件时间，历史读取传原事件时间，预检默认当前时间。拒绝未来完成、早于创建、事后凭证和无时区事件时间。声明摘要仍只是HEAD元数据一致性，未证明真实文件字节。

**81988 exit=0：文件相关94项通过/23.26秒（含新增4项时间边界）；74262 exit=0：备份对象/流式回归69项通过/2.18秒**。**94705 exit=0：数量run-9ap2sd23、SN run-l7b0fcx8均stopped/passed/serverExitCode=0**。1639源终态逐项一致，实际6条文件候选及104条结构DDL哈希吻合。每模式真实API角色文件上传/完成/幂等通过；插入后deny/动作变化/到期/版本/停用/人员变化6类延迟约束拒绝，撤权后历史完成证据保留；原事实及全部原函数恢复，临时文件/审计/授权/DDL完整回滚。94项服务和69项备份回归、安全v33终态证据保持。最终收据return-condition-evidence-final-v1-receipt.json，日志return-condition-evidence-native-v2.log。使用FakeStorage、不访问OSS；SET CONSTRAINTS并非实际COMMIT验收。没有纠正事件独占绑定、实物或完整上线证明。源码固定解除，未提交/推送/部署。

下一步：精确事件文件绑定、跨用途/跨事件复用拒绝及files反向保护；受当前查看范围控制的完整历史附件下载；正式迁移保留策略/运行时目录、真实COMMIT验证；纠正业务当前权限提交栅栏、原子写入、请求恢复和页面。其余完整上线缺口继续保留，未提交/推送/部署。

## 2026-10-03 当前：区域保管人的准确来源预检与接收回查范围修复

新增内部 `return_condition_submission_source.py`，只接收准确入库明细UUID。先按真实当前身份/区域纠正权限/当前保管责任及库存查看权限准入，再通过准确入库→发运→退回单关系派生唯一原报损处置。调用原内部完整历史证明 `_verified_graph`，独立核验原破损子集、可信期初、当前余额/SN/物料策略及后续移动；第二遍重读权限、原关联、全历史和库存，不返回跨两个状态拼出的证据。只输出所选来源，无总部全局历史、审批意见或他人原请求。`return_history.read`的原总部边界不变，没有临时总部principal或历史核验mock。

输出版本`return_condition_submission_source/1`，`submission_permission_checked=true`仅表示本次读检查通过。`posting_allowed=false`、`correction_authorized=false`、`physical_verification_required=true`保持；普通短事务SELECT但完整期初证明持有行锁，不宣传SQL READ ONLY。没有冻结、账户创建、新纠正请求/审计/Outbox、审批或过账，尚未挂HTTP路由。已转出又补入的数量仍返回later_activity_requires_reconciliation，不推测原破损份额还在。

集成发现并修复原`stock_return_receiving._authorize`的问题：原先将stock_operation:read作用于操作者person目标，会把实际保管区域仓且获准确区域授权、但所属组织在总部的人员拒绝。改为检查当前admin/provincial_manager获授的操作范围；每个具体位置仍由`_locations`独立检查当前保管人、唯一有效责任及该资产区域的stock_operation/read和inventory/read。未改变验收/入库写权限或原请求坐标。对其他获授区域、保管责任已变化的原请求仍拒绝。

**86374 exit=0：13项新来源/恢复用例通过/88.23秒**；**91206 exit=0：新增2项区域/保管变更回查拒绝通过/52.47秒，13项未选**（两个独立运行，不写成单次15项）；**90845 exit=0：原接收查询21项回归通过/168.16秒**。测试使用实际当前权限表和旧服务生成的SQLite历史；区域账号显式撤总部角色仍可读所选来源，所有历史核验与当前身份加载真实执行；确认只加载当前操作者身份、无业务写入、原总部历史读取服务仍拒绝。覆盖缺纠正/库存权限、其他保管人、只具HQ角色、准确原单不存在、中途撤权/原入库篡改/投影变化/保管变化、旧历史哈希篡改、真实后续预留再释放不得假称原份额保留。原请求独立回查哈希保持。9432首轮12通过/1失败暴露前述接收范围问题，已由修复后终态覆盖，日志保留。Starlette只有既有弃用警告。

日志在`artifacts/formal-0165-integration/return-condition-regional-source-v2.log`、`return-condition-regional-recovery-scope-v1.log`、`return-condition-receiving-scope-v1.log`。**96612 exit=0：安全v31通过（2597文件/55535482字节）**，diff检查通过。聚焦收据 `return-condition-regional-source-focused-v1-receipt.json`，生成时1635源与原生运行快照一致。

**原生10822 exit=0：数量/SN均终态通过**。数量库`artifacts/local-return-condition-pg16/run-ebem2d35`、SN库`run-u3gfw3qy`均stopped/passed/serverExitCode=0；1635源在终态归档时逐项与当前一致，实际104条DDL哈希吻合。每模式真实API角色/纯区域负责人通过所选来源全历史预检、原入库请求独立恢复及4种权限拒绝；新提交准入另有1正向/7拒绝。临时授权与候选DDL全部回滚，239旧表564/577行、415函数及856外键保持。最终收据`artifacts/formal-0165-integration/return-condition-regional-source-final-v1-receipt.json`，日志`return-condition-regional-source-native-v1.log`。本轮无运行中进程，源码固定解除；尚无新纠正业务写入、COMMIT权限栅栏、正式迁移或生产验收。未提交、推送或部署。

下一步：成色纠正专用完成上传/实物证据（不能复用工程师个人报损上传权限）、新动作的COMMIT身份/范围/到期保护、来源完整历史与后续已证明纠正移动、审计及Outbox、原请求登记/封存与恢复、原子业务写服务和页面。必须保留其余完整上线缺口，不将本次来源查询当作可执行纠正或正式上线。


## 2026-10-03 接续中：当前成色纠正动作权限准入

新增内部 `return_condition_authority.py`。所有当前权限从真实授权表重新加载，不信任传入principal中的角色和权限；区域、账户及保管范围只从准确原1.0入库明细派生。核验区域仓有效状态、所有者组织及完整祖先链、唯一当前保管责任。后续动作按准确纠正单、当前最新事件和状态绑定；申请人动作仍须原用户和原人员，区域和总部审核按用户、人员双重独立。每种动作使用专用 `stock_operation` permission action，未写入生产目录或授正式角色权限。

| 动作 | 当前角色与范围 | 身份约束 |
|---|---|---|
| 申请、补证、撤回、执行、释放 | 当前区域负责人，准确来源区域 | 当前保管人；后续动作须原申请用户及人员 |
| 核实、退回补证、区域驳回 | 当前区域负责人，准确来源区域 | 不得是申请用户或人员 |
| 总部同意、驳回、退回区域、取消批准 | 当前全国总部管理员 | 不得是申请人或本次区域核实人 |

它是非写入准入阶段；两遍读取只能检测准备期间变化，不是COMMIT权限栅栏。不得用于历史事实或原请求回查；原审核人停用/过期不能取消既有审批。完整来源历史、数量/SN、附件、审计/Outbox、提交时锁与延期到COMMIT的权限校验尚须集成。没有新API、正式迁移或生产权限。

**53139 exit=0：41项新权限测试通过/93.57秒**，`return-condition-authority-v8.log`。使用真实身份/角色/权限表和旧入库服务生成的数量场景，实际数据库加载器未替换。候选纠正表仅最小引用行，不是完整纠正业务事实；完整SQLite快照用于隔离各例，权限仍逐次重新查询。覆盖全部12个后续动作/状态组合、deny/缺权限、授权到期与撤销、身份撤销、版本变化、停用/离职、异区域、保管人/唯一责任/到期、组织停用/多节点循环、自己核实、其他用户执行、HQ和区域按用户/人员双重分离、旧申请人/核实人停用后独立HQ仍可新审核、错误原单/最新事件、两遍之间真实撤权。Starlette只有既有弃用警告。

**39651 exit=0**：原规则/关系/指纹144项通过/12.33秒，`return-condition-authority-regression-v1.log`。**99298 exit=0**：安全v30通过，2594文件/55508705字节，diff检查通过。聚焦收据 `artifacts/formal-0165-integration/return-condition-authority-focused-v1-receipt.json`，生成时1632源与原生运行快照完全一致；未声称仍在运行的5004通过。

开发期错误已保留：29999首例没有当前区域grant（已在本地测试准确授权，未放宽规则）；83757/61465为夹具性能改进主动中止。3056因autouse别名导致重复建立库存账户；34850撤销授权夹具未同时设置status/revoked_by；17464祖先用例假定区域有父组织；70448单节点自环被既有数据库约束提前拒绝，改成真实多节点循环。上述夹具均已修正，并由53139全组终态覆盖，不把被中止或部分通过计为最终通过。

44514 exit=1：旧版升级/旧应用业务/当前升级均通过，但迁移角色无权SET ROLE到API，隔离库`run-6k6ahugn`已stopped/failed/serverExitCode=0。测试执行器改为只允许准确本轮新建socket库，在验证路径、PID、system_identifier和无TCP后，由本库DBA会话切换有效角色；读取实际以API角色执行，临时数据修改以migrator执行，所有临时授权最终回滚，不授予任何新的数据库角色成员关系。

**原生5004 exit=0，数量/SN均终态通过，1632源逐项与当前一致**。数量 `artifacts/local-return-condition-pg16/run-2t32fnqa`、SN `run-p9zo52_7` 均stopped/passed/serverExitCode=0。每模式实际API角色的新提交准入1正向、7种拒绝（deny/到期/版本变化/停用/保管人变化/保管到期/其他操作者），每次反例回滚后正向恢复；所有读取语句为SELECT，临时授权最终完整回滚，全库原事实保持。旧应用0157产生真实合成收货/1.0入库→当前0165升级→原请求及来源回查→新准入检查→104条结构/三层候选检查→完整DDL事务回滚通过；239旧表564/577行、415函数、856外键保持。最终收据 `artifacts/formal-0165-integration/return-condition-authority-final-v1-receipt.json`，日志 `return-condition-authority-native-v2.log`。后续审核动作的PG业务写入、COMMIT权限栅栏、正式纠正迁移、真实上传/API/UAT仍未证明。本轮所有进程已终态，无需继续轮询5004等；源码固定解除，未提交/推送/部署。

下一步优先把当前权限准入接到区域保管人的准确来源预检，不能冒用总部principal或扩大原总部历史接口。`return_history._verified_graph`已有内部完整历史证明；新路径须先从准确入库明细授权、派生唯一根处置、确认选中的历史异常属于该根，再核验全图/当前库存，最后双读权限及原引用；仅返回所选来源证据。原总部`read`保留。随后完成上传与实物证明、全历史/期初/后续自己的纠正流水、业务审计及Outbox、原请求登记/封存恢复、原子服务与COMMIT权限保护，继续完整基线其余上线缺口。当前41项权限测试中的纠正表是最小引用夹具；不得把它当成完整纠正业务提交证明。


## 2026-10-03 当前：纠正命令指纹、共用单据及库存幂等绑定

本节优先于下方历史状态。新增内部 `return_condition_identity.py` 和候选 `return_condition_candidate/identity.sql`，按保存的案例、事件、前序请求、SN及附件引用生成明确版本的计划/命令；SQL独立重建期望内容再比对规范化哈希，重算伪造JSON哈希不能冒充准确业务事实。共用作业头/明细、申请人及提交上下文、逐件三码确认与纠正事件绑定；库存交易的编号、业务来源、独立posting_key、库存幂等哈希和库存请求哈希与同一事件完全对应。新增2私有函数和8个延迟触发器，共用父表单独变化也反向检查。没有安装HTTP写入口、新Alembic head或生产授权。

同时修复既有 `inventory_posting._canonical_decimal`：Decimal.normalize会受环境精度影响，可能舍入合法numeric(18,3)再计算请求哈希；改为精确十进制格式化，只去除小数末尾无效0，整数0不截断。新回归在精度2/6/28并打开Inexact/Rounded异常下验证0.375及最大18位数量，确认相同数量不同小数位仍同指纹、真实数量变化则不同，兼容正式库存命令哈希。

**33992 exit=0**，最终命令原生组件 `artifacts/local-return-condition-identity-pg16/run-zg4hktl5` stopped/passed/serverExitCode=0：110项篡改拒绝/回滚后全组件库不变，8组补证重审→审批执行/释放后重新申领通过。包括命令/计划改值并重算所有相关哈希、借用另一请求/操作者、缺附件引用、共用头/行错误、库存哈希错误、只改共用父表，以及先成功证明后再次篡改。规范化JSON函数取自真实0026迁移源码；未伪造证明函数。此仍是最小合成外部父表与附件元数据引用的API SQL组件，不证明附件上传完成/实物、当前权限、完整审计/通知或真实HTTP。6237为前一版本同组通过，不累加。

**完整回归**：16342 exit=0，过账组件 `artifacts/local-return-condition-posting-pg16/run-951hnn3t` 146拒绝/4正向；79626 exit=0，预算组件 `artifacts/local-return-condition-invariants-pg16/run-gt5l3pgi` 72拒绝/18正向/6实际竞争。它们各自安装对应层，不冒充完整三层合并并发。69655 exit=0：144项规则/关系/新命令精度测试，12.66秒；85031 exit=0：既有库存准确幂等重放/载荷冲突、另一幂等键复用posting_key拒绝2项/2.91秒。日志在 `artifacts/formal-0165-integration/return-condition-identity-native-v2.log`、`return-condition-posting-native-v5.log`、`return-condition-invariants-native-v5.log`、`return-condition-identity-regression-v1.log`、`return-condition-canonical-inventory-regression-v1.log`。三个原生组件1629源无漂移且均已停止。

**完整旧历史兼容37964 exit=0，数量/SN均终态通过**：数量 `artifacts/local-return-condition-pg16/run-j68oq51m`、SN `run-e5v9zp8m` 均stopped/passed/serverExitCode=0。各自104条候选DDL，新增8私有函数/28触发器；239旧表564/577行、415旧函数、856外键及原入库请求保持。完整事务回滚、正式运行时恢复通过。实际执行SQL SHA `b5c5e91aa70e3e09e4d42685c3ad60aa7d200de9eb0aa5abdd1ac9d680511c09`。三个组件和两个兼容库1629源逐项与当前一致。最终收据 `artifacts/formal-0165-integration/return-condition-identity-final-v1-receipt.json`。候选新表没有API写权；这不是新纠正API、正式迁移、真实上传或生产验收。所有上述进程已终态，无需再轮询37964，源码固定解除。

安全 **72912 exit=0**，v28为2591文件/55464271字节，diff检查通过。已完成的三组件/144规则关系精度/2既有幂等服务/安全收据 `artifacts/formal-0165-integration/return-condition-identity-component-v1-receipt.json`，生成时三组件1629源码均与当前一致；收据未将运行中的37964计为通过。

下一步：新增事件的当前角色/范围/代理与身份真值、实物/完成上传证据、完整旧历史/期初和后续移动、库存余额/SN完整账本、业务审计/Outbox、原请求登记/封存与恢复、原子服务/页面。当前命令是内部事实规范化，不是可直接接受的HTTP写请求或授权；生成正确哈希不授写权限。当前权限检查必须只用于新动作的提交准入，不能在历史回查中重验老审批人的现时权限而令过期旧审批失效；此边界可参考0165 recovery_authority.sql。完整基线其余缺口不变。未提交、推送或部署，原未提交改动全部保留。

## 2026-10-03 当前：成色纠正库存关联与父表反向COMMIT检查

本节优先于下方历史状态，完整上线目标未完成。新增 `backend/alembic/return_condition_candidate/posting.sql`，由 `return_condition_guards.statements(posting=True)` 显式安装。未激活Base、未发布新迁移、未授正式API写权限。2个私有函数和8个延迟触发器补充原不变量，不替换旧库存/审计保护。

原入库行、交易和流水按准确交易ID、游标、行序、接受量、两端账户及物料/批次绑定。冻结账户必须保持原资产组织、保管人、位置、物料、批次及成色，只改变可用状态；执行目标仅允许同维度坏件可用账户。提交/释放/执行分别绑定精确事件、操作者、交易类型、时间和唯一单笔移动；全部事件顺序必须与实际流水游标一致。关联SN集合、物料/批次和上一笔实际移动逐一核对；只有本异常已经证明的释放才允许再次从原账户申领。非SN原入库后的非纠正转出仍要求先核对，不能以余额补足代替归属证明。

反向COMMIT触发器覆盖交易、移动、移动SN、账户和SN主记录，以及纠正案例/事件/SN。只改父表也必须重查，拒绝带纠正类型或posting_key却没有准确事件的孤立交易；同一事务先成功核验、随后篡改流水仍失败，不缓存证明。父表本身的不可变、库存余额/完整序列号账本、当前权限、真实证据、业务与库存请求哈希、审计/Outbox及正式历史证明仍由后续完整集成负责，本层不是独立过账许可。

**最终组件50430 exit=0**：`artifacts/local-return-condition-posting-pg16/run-1qa9qzk7` stopped/passed/serverExitCode=0；4种追踪模式，146项错误交易/账户/SN/孤立父表写入被拒绝，各次回滚后完整组件库快照不变，4组完整正常释放→重新申请→独立审批→执行通过。真实star_oam_api直接执行SQL，外部父表仍为明确的最小合成关系/库存行，不含完整旧业务触发器，不是HTTP/UAT/生产证明。日志 `artifacts/formal-0165-integration/return-condition-posting-native-v4.log`。40785为此前相同测试中间通过版本，不累加。

**预算并发回归55755 exit=0**：`artifacts/local-return-condition-invariants-pg16/run-rqlb3k_g` stopped/passed/serverExitCode=0，72拒绝/18正向/6真实竞争保持；该回归安装原基础不变量层，posting层的并发与完整库存锁顺序仍待服务集成验证。**规则/关系60908 exit=0**：126项/13.67秒，`return-condition-posting-regression-v1.log`。各组件1624源无漂移。

两次开发期失败已修复并保留：42227 exit=1，exec_driver_sql把PL/pgSQL的%ROWTYPE当驱动占位符，测试安装器改用SQLAlchemy text；11647 exit=1，SQL表别名e与PL/pgSQL事件record重名，改用独立别名，未放宽业务规则。日志分别 `return-condition-posting-native-v1.log`、`v2.log`；两个隔离库均已停止。

**完整旧历史兼容99699 exit=0，数量/SN均终态通过**：`run_local_pg16_return_condition_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin --predecessor-source artifacts/formal-0165-integration/condition-predecessor-0157/source/cloud_oam --posting-candidate`。数量 `artifacts/local-return-condition-pg16/run-jrkof7ei`、SN `run-gv6rw_bx` 均stopped/passed/serverExitCode=0。各自实际0157应用生成合成验收/1.0入库→0165升级→完整来源及原请求回查→93条候选DDL（6新函数、20触发器）安装→完整事务回滚通过；239旧表564/577行、415旧函数、856外键、原请求哈希保持，回滚后正式运行时恢复。新函数的直接执行及新表写权限仍关闭。日志 `return-condition-posting-compatibility-v1.log`。此为候选安装/事务回滚与实际旧应用合成历史兼容，不是正式迁移或新纠正API业务写入验收。

安全 **57194 exit=0**：v27为2586文件/55423230字节，diff检查通过。组件/规则/安全联合收据 `artifacts/formal-0165-integration/return-condition-posting-component-v1-receipt.json`；最终收据 `return-condition-posting-final-v1-receipt.json` 补齐99699双模式终态及实际执行DDL摘要，生成时两个组件和两个兼容库的1624源均与当前逐项一致。**全部本轮进程已结束，无需再轮询或重启99699/50430/55755/60908/57194等；源码固定已解除。**

下一步：补准确共用单头/明细与事件上下文、规范化业务/库存命令及哈希、当前角色/范围/代理、真实附件/实物、完整旧历史/期初和后续流水、审计/Outbox/原请求与封存的双向COMMIT关联，再接原子写服务、恢复和页面。保留人员交接/下游补偿、真实渠道、全链路UAT、迁移期初/三天对账、500用户压测、灾备/回滚、CI/正式发布等完整目标。原改动保留，未提交/推送/部署。

## 2026-10-03 当前：纠正不可变事实、异常预算及并发保护组件

本节优先于下方历史状态。新增 `backend/app/return_condition_guards.py` 和 `backend/alembic/return_condition_candidate/invariants.sql`，仅供候选安装器调用；未注册Base、未新增Alembic head、未开放正式API写权限。四张纠正事实表拒绝UPDATE/DELETE/TRUNCATE；插入按准确原入库行持有NO KEY UPDATE锁，限定READ COMMITTED，拒绝倒插旧事件顺序。该锁与外键KEY SHARE兼容，不使用全局互斥锁。

原异常预算直接读旧1.0入库对应验收行的破损量，不信任调用者的affected_quantity或JSON。逐事件核对累计份额，只有准确释放事件归还份额；驳回/撤销不归还，已执行永久消耗。SN逐个绑定原破损验收，数量齐全；检查全部历史占用区间，后续释放不能掩盖以前的重复申领。复核与申请人、总部与本次区域核实者按用户和人员双重分离；这仍不等于当前权限或代理有效性证明。

**12534 exit=0**，最终原生组件 `artifacts/local-return-condition-invariants-pg16/run-s90joasb` stopped/passed/serverExitCode=0，1621源无漂移。四追踪模式下72项违规写入预期拒绝且各自事务回滚后全库事实不变，18组正向流程，6组真实双连接竞争。竞争均实际观察到同一来源阻塞；不同来源在等待期间成功提交；前请求提交/回滚/释放后，后请求分别按最新事实拒绝或通过。实际使用star_oam_api执行SQL，并在隔离夹具中刻意授予DML，以验证触发器拦截而非缺权限。**外部父表为明确的最小关系夹具，不含旧业务完整触发器，不是HTTP、真实身份、完整迁移或生产证据。** 日志 `artifacts/formal-0165-integration/return-condition-invariants-native-v3.log`。中间48756也通过同组测试，不累加。

首轮11728 exit=1：共用触发器在非事件表上通过AND表达式引用NEW.event_sequence，PG解析记录字段时拒绝。已改为独立表名分支后再读取事件字段；没有削弱规则。失败库已停止，日志 `return-condition-invariants-native-v1.log` 保留。最终纯规则/关系回归7673 exit=0：126项通过/12.22秒，`return-condition-invariants-regression-v1.log`。

**完整旧历史兼容74854 exit=0，数量/SN均终态通过**：`run_local_pg16_return_condition_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin --predecessor-source artifacts/formal-0165-integration/condition-predecessor-0157/source/cloud_oam --invariant-candidate`。数量库 `artifacts/local-return-condition-pg16/run-xsim7tdc`、SN库 `run-sjli17d2` 均stopped/passed/serverExitCode=0。各自实际0157应用生成合成破损验收/1.0入库，经当前0165升级、来源核验及原请求回查，再安装82条候选DDL（含4函数/12触发器），用原旧应用破损验收事实执行新增检查，最后完整事务回滚；239旧表564/577行、415旧函数、856外键保持，函数不授API等角色直接执行权限，回滚后正式运行时恢复。日志 `return-condition-invariants-compatibility-v1.log`。本轮是候选兼容和事务回滚，不是正式迁移/纠正写流程或生产历史验收。

安全检查 **6162 exit=0**，v26为2583文件/55379074字节；diff检查通过。组件收据 `artifacts/formal-0165-integration/return-condition-invariants-component-v1-receipt.json` 记录12534/7673/6162的终态、源和日志摘要、6组真实竞争及证明边界；联合最终收据 `return-condition-invariants-final-v1-receipt.json` 补齐74854双模式终态及实际执行DDL摘要，生成时1621源码在组件及两个兼容库均逐项匹配。**全部本轮测试/扫描均已结束，无需再轮询或重启74854/12534/7673/6162等；源码固定已解除。**

下一步仍需完整原历史/期初及后续移动证明、当前权限与实物/附件证据、流水/审计/Outbox和原请求/封存的双向COMMIT关联，然后写服务及页面；不能把本组件的身份分离和关系事实检查当作过账授权。与既有库存/审计锁顺序的完整服务集成须另验。保留所有原改动，未提交/推送/部署，全部原上线缺口仍有效。

## 2026-10-03 当前：纠正数据库关系候选，数量/SN原生旧历史验证通过

本节优先于下方状态。修正原生约束重名后的126项结构/规则联合验证已通过（62520 exit=0，17.76秒），日志 `artifacts/formal-0165-integration/return-condition-schema-rules-v3.log`。早期42734/2809分别126/30项通过，仅属中间版本，不与最终结果重复累加。

新增 `backend/app/return_condition_schema.py`：在独立0165模型副本中定义案例、事件、SN及附件四表，扩展统一库存作业单/明细与原入库/流水四个父表；新单头/明细和案例双向绑定。准确前序状态/顺序、唯一后继、唯一提交根、固定份额、准确执行/释放决定及交易/移动/账户/数量由关系约束绑定，审核动作不能携带库存移动。没有注册到Base、没有新增Alembic head、没有开放API权限；不可变/并发预算/权限与全业务COMMIT保护、写服务、请求恢复仍未实现。详细边界见 `RETURN_CONDITION_CORRECTION_IMPLEMENTATION.md`。

**80626 exit=0，数量/SN均终态通过**：命令 `.venv/bin/python -u scripts/run_local_pg16_return_condition_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin --predecessor-source artifacts/formal-0165-integration/condition-predecessor-0157/source/cloud_oam --structure-candidate`。日志 `artifacts/formal-0165-integration/return-condition-structure-native-v2.log`。数量目录 `artifacts/local-return-condition-pg16/run-uincl44n`、SN目录 `run-ymuzc7n9` 均stopped/passed/serverExitCode=0。每种模式均应用65条候选DDL、4新表/4父表；239旧表的564/577行、415函数、856外键保留，16个表/角色权限组合保持拒绝。DDL事务回滚后完整结构与正式0165运行时恢复，原入库请求哈希及全部业务数据保持；1616源无漂移，收据生成时再次逐项匹配当前源。两个模式实际DDL SHA均为 `ef326fb1a6e4f130774e7db8137d9492314b258a4e28b4b44e0b01abb53e87bf`。这证明真实旧应用生成的合成历史兼容及候选结构事务回滚，不证明纠正写入、正式迁移或生产上线。

首轮50663已exit=1，数量目录 `artifacts/local-return-condition-pg16/run-kmgjp0x8` stopped/failed/serverExitCode=0。实际旧业务、0157→0165升级已通过；候选新事件表因共用context助手和局部检查都命名 `ck_condition_event_context` 被PG拒绝。SQLite和语法解析不拒绝重复约束名，因此补了逐表约束名唯一性检查，将局部名改为 `ck_condition_event_sequence_context`，未放宽业务规则。失败DDL事务由finally回滚；首轮未完成全部回滚后比较和SN模式，不能算门禁通过。失败日志 `return-condition-structure-native-v1.log` 保留，不再轮询50663。

本轮独立重做实际0157服务生成的合成破损验收/1.0入库及0165升级/来源/原请求回查，没有借用旧53821成功片段。每个原生目录保存实际执行的 `condition-candidate.sql` 及SHA；独立进程编译的约束排列可能不同，不能用另一份SQL文件哈希冒充实际执行字节。联合收据 `artifacts/formal-0165-integration/return-condition-structure-native-v2-receipt.json`；单元收据 `return-condition-schema-unit-v3-receipt.json` 的nativeStatus是生成时状态，由联合终态收据补齐。安全v25：39720 exit=0，2578文件/55339962字节；diff检查通过。所有测试、扫描均终态，80626/39720/62520等无需再轮询；源码固定已解除。

下一步：为此候选添加不可变及双向延迟COMMIT保护、完整旧入库破损子集/后续移动证明、当前权限与身份分离、原子份额/SN并发预算、审计/Outbox/原请求及封存；对应写服务和真实API角色反例通过后才可考虑正式迁移与页面。关系FK不能替代上述业务保护，不把JSON计划当可信过账授权。所有原改动保留，未提交、推送或部署。完整上线目标和全部基线缺口保留；没有需要用户才能继续的阻塞。

## 2026-10-03 接续：成色纠正份额和状态规则通过，尚未接入写流程

本节优先于下方历史状态；原工作树、分支和全部原改动保留。完整上线目标未完成，未提交、推送或部署。

- 新增内部纠正事实、计划和完整历史投影：`return_condition_contracts.py`、`return_condition_planning.py`、`return_condition_projection.py`。一张案例固定准确异常子集，申请即绑定冻结；区域复核/补证/总部审批、驳回、撤销、释放、执行分别变化。批准不自动过账，驳回/撤销不自动释放，已执行份额不重新开放。
- 支持部分数量和四种追踪模式；逐个事件检查份额/SN，后续释放不能掩盖中途重复申领。精确校验原交易/流水不复用、同一案例份额与冻结账户、释放到原账户及坏件转换维度。申请人与审批人分离，区域核实与总部终审分离；旧决定不能跨补证/退回/取消后再次执行。数量按整数千分单位运算，低Decimal精度不损失份额。
- **71757 exit=0，96项通过 / 1.72秒**。包含新增投影测试、数量/SN各35种独立预算对照的事件交错、原报损计划回归；日志 `artifacts/formal-0165-integration/return-condition-planning-v3.log`。此前72413/32917也已exit=0，分别为88/96项的中间版本，不与最终结果累加。新增五个非Markdown源/测试文件；未修改原来源读取、旧迁移或前端。
- 这次是纯规则验证，不是数据库并发、库存写入或上线验收。当前角色/范围/代理、文件/实物证据、完整审计/Outbox/请求登记与真实流水存在性都必须由下一阶段正式适配器和DB保护证明。没有HTTP写入口、没有新数据库迁移，没有生产数据操作；旧1607源PG收据不能充当新增文件后的全量当前源证明。

继续顺序：按 `RETURN_CONDITION_CORRECTION_IMPLEMENTATION.md` 接纠正单持久化/权限和原子冻结→审核→释放/执行、原请求恢复、延迟COMMIT及API角色反例；同时处理既有来源预检对已证明纠正冻结/释放的识别，不能简单跳过后续移动检查。再补页面/真实浏览器API联通、下游补偿及完整基线剩余验收。当前不需要用户补资料才能继续。

收据 `artifacts/formal-0165-integration/return-condition-planning-v3-receipt.json` 保存本轮源码与日志摘要、范围限制及安全检查终态。所有已启动规则测试已结束，无需轮询旧句柄。

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


## 2026-10-03 历史批次：公共根历史核验优化通过（旧历史终态见顶部）

本节优先于下方历史状态。保留全部改动，未提交、推送或部署；完整上线目标仍未完成。

- 当前源数量模式性能采样 **77139 exit=0**：`artifacts/local-scrap-http-pg16/run-c1932rb8` stopped/passed/serverExitCode=0，1601源与结束时源码一致。22次真实COMMIT、103次READ ONLY通过；最大COMMIT14.305401秒、完整HTTP22.114820秒。审计扫描3103次、规范化JSON递归6608569次；函数self_time与inclusive_time不能混加。收据`artifacts/formal-0165-integration/scrap-current-profile-v1-receipt.json`。
- 据此修改尚未发布的0165候选编译器：同一次根历史证明只完整核验一次公共上游及审计成员，然后逐事实保留完整事件/附件/计划/库存边/期初核验。原独立历史入口不变；新私有函数无API/PUBLIC执行权，不跨请求/事务缓存。原候选与运行时目录完整备份在`artifacts/formal-0165-integration/pre-root-proof-reuse/`。
- 编译器守卫逐字还原对照 **5项通过**；原生门禁新增逐事实独立/共享计划对照、每根每次调用计数、成功后同事务上游/审计失败必须重新拒绝、外根/空审计/API越权反例。新增原生反例现已在数量与SN两模式通过。
- **6209 exit=0**：候选目录`artifacts/local-scrap-catalog-pg16/run-ve7rzckk` stopped/passed，生成/回滚/字面SQL回放通过。已审阅并更新尚未发布的本地0165冻结目录：表/权限不变，仅新增2私有函数、修改完整历史图及证明构建器两函数，其余独立函数逐项完全一致。目录SHA`32d18ea227d77bf2512a2fccb563716e306ef9de75a5777433825aec2ac6294c`，运行时只读投影SHA`cf8d50ae0eba240a4e229902e1ff6e9b0995b7b9df8a948b8da0728664ff6251`，审阅收据`scrap-root-proof-catalog-v1-review.json`。
- **25647 exit=0**：编译器/目录安全/readiness/HTTP适配聚焦184项通过（`scrap-root-proof-preflight-v1.log`），仅Starlette既有弃用警告。**52591 exit=0**：`artifacts/local-scrap-runtime-pg16/run-ng6w_go7` stopped/passed，12类目录/授权篡改拒绝、精确恢复、0165→0164旧版完整API启动→0165通过；11默认权限无外部授权，源码收据与当前一致。
- **37488 exit=0，双模式完整终态**：数量`artifacts/local-scrap-http-pg16/run-dan6wgpo`、SN `artifacts/local-scrap-http-pg16/run-qhi9aqsj`，均stopped/passed/serverExitCode=0，当前1601源一致，每模式22COMMIT/103 READ ONLY/12旧原请求回读。数量最大COMMIT8.400978秒（同模式前14.305401）、完整HTTP14.802255秒（前22.114820）；SN最大COMMIT10.121456秒、完整HTTP19.498692秒。数量审计扫描3103→1305、规范化JSON递归6608569→3960836。每模式4事实独立/共享计划一致，每次图调用每根上游仅1次；19项拒绝反例、连续两次不复用旧证明及catalog原样恢复通过。收据`scrap-root-proof-http-v1-receipt.json`，日志`scrap-root-proof-http-v1.log`。仍不是500用户性能验收，服务端响应仍偏慢。
- **45118 当前在运行**：当前源真实0164旧历史→0165→0164保留旧版API→0165，数量/SN模式，日志`scrap-root-proof-legacy-v1.log`。命令为`.venv/bin/python -u scripts/run_local_pg16_scrap_business_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin --legacy-history-only --predecessor-source artifacts/formal-0165-integration/predecessor-source-v1/source/cloud_oam`。继续轮询原句柄；终态前保持后端/脚本/部署源不变，不重复启动。该门禁必须另取完整终态，不能沿用前次旧候选收据。
- **7420 exit=0**：编译产物真实Chrome双标签页，实测Web Locks和storage事件；审核意见切换废弃旧确认，第一笔提交锁定时第二页禁止重复提交且0回查，响应丢失后第二页只读回查1次，完整原请求保持不变。接口仍为合成拦截，并非真实后端UAT；脚本、日志、含构建SHA的收据和手机截图在`artifacts/formal-0165-integration/browser-serial-scope/cross-tab*`。首轮27957因预览命令漏warehouse mode导致资源路径错误而超时，修正测试启动参数后通过；无产品代码变动。预览48837已主动停止（143正常清理）。

下一步保留完整性能、退回下游补偿、成色纠正、真实渠道、联通UAT、迁移/对账、压力与灾备、CI和发布验收要求。安全检查v20（65548）已exit=0，diff检查通过。没有等待用户资料的阻塞。

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

## 2026-10-03 最新：双模式完整 HTTP 门禁通过，原始报废页面接入

本节取代下方旧状态；本次所有启动的测试均已终态。保留全部未提交改动，未提交、推送或部署。新增 `backend/tests/pg16_scrap_http_business.py`，将已通过的 artifacts 临时入口转为正式测试 helper；`scripts/run_local_pg16_scrap_business_checks.py --http-only` 默认依次验证 quantity、serial，每种模式使用独立全新 PG16。保留旧临时入口和原收据，不覆盖旧证据。

本次新增纠正报废 HTTP、重新审批、第二轮找回、后继发生后全部旧原请求再查、不可变历史包含关系、最终冻结份额及 SN 位置核对。继续验证实际提交后丢响应、提交前回滚、撤写权限后只读回查、封存阻止迟到执行。认证仍为本地合成会话；不冒充短信/微信供应商验收。已将 `scrap_http` 登记到 GitHub PG16 quantity/serial 矩阵；CI 调度、失败清理和真实隔离边界聚焦 68 通过（`scrap-http-ci-dispatch-v1.log`），尚未推送或运行远端 CI。

v2 句柄 **99306 已 exit=1**，quantity 目录 `artifacts/local-scrap-http-pg16/run-_24s76eh` 已 stopped/failed/serverExitCode=0。原始五阶段通过；纠正报废未提交，数据库以 `uq_stock_scrap_files_file` 拒绝测试重复绑定首轮附件。已修复测试为每次报废/找回申请独立上传证据，未放宽唯一约束；同时检查提交前故障确实到达 COMMIT 调用，防止其他异常冒充故障注入成功。失败日志 `scrap-http-formal-v2.log` 保留。

修复后的 v3 **63136 已 exit=0**，quantity `run-8b4ecz5u`、serial `run-q79rizce` 均 stopped/passed/serverExitCode=0。每种模式实际 21 次 COMMIT、51 次 READ ONLY，完成原始和纠正两代报废/找回、11 份历史原请求回查、旧不可变事实保留、最终冻结份额与 SN 位置一致。1591 后端源无漂移，终态核验时仍与当前一致。收据 `artifacts/formal-0165-integration/scrap-http-formal-v3-receipt.json`，日志 `scrap-http-formal-v3.log`；原始运行目录都在 `artifacts/local-scrap-http-pg16/`。不要再轮询或重启63136；本轮全部已启动进程终态，源码固定解除。

前端新增 `scrapOriginalAdapter.ts` 与 `FormalOriginalScrap.tsx`，已在 `App.tsx` 注册 `/loss-scraps/:operationId?/:decisionId?`。报损处置页通过真实批准行进入，不要求手填标识；另有“报废请求回查”入口，无选中原单也能回查本人的完整原请求。真实文件上传组件绑定本次报废证据，预览后明确勾选确认；原请求发送前持久化，断网后仅回查，not_found 不重发，封存另需确认。已过账行不再给执行按钮，撤写权限后保留只读恢复。

适配器仅处理 original 阶段，严格核对来源、批准、数量/成色/SN、预览时间和方案摘要，发送前再次回读。刷新后恢复不依赖内存来源映射，不重新预览或借用写权限。页面、路由权限、适配器及原三恢复文件共 **179 项通过**（`scrap-page-v2.log`）；TypeScript 构建 exit=0（`scrap-page-types-v3.log`）；`/xx/` warehouse Vite 构建 exit=0（`scrap-page-warehouse-build-v1.log`，保留大 bundle 警告）。收据 `scrap-original-page-v1-receipt.json`。页面测试使用合成传输，尚无真实浏览器到 PG16 的整页验收；本轮原生 HTTP 门禁独立证明其写入/回查接口，未覆盖页面用到的来源 GET。

新增性能观察：serial 第二轮恢复的一次真实 API COMMIT 在实时只读诊断中持续至少 96 秒（active、无锁等待）；随后 execute、全部历史回查和最终库存核验均通过。证据 `scrap-http-serial-commit-observation-v1.json` 只记录语句类型/状态/耗时，不包含 SQL 正文或凭据。这不是压测结果，应在正确性门禁终态后剖析约束/历史核验开销，不能以增加超时或放松库存/SN/审计约束作为上线验收。安全 v11 已 exit=0（2532 文件），`repository-safety-v11.log`；git diff --check 通过。

页面接入审计：原始批准来源已有 `stock_loss_execution_sources.execution_sources` 与客户端 `lossExecutionAdapter.read`；原始报废页面本轮已接入，来源 GET 的原生 HTTP 联测还需补齐。纠正查询 `stock_loss_corrections/read_sources.py` 对 scrap 返回 `dedicated_flow_required` 且 preview_reference 为空，不能直接拿旧纠正 adapter 执行新报废。还须提供专用纠正来源及找回申请/区域/总部/执行的受限只读来源查询，之后接入附件、预览、显式确认和原请求恢复；禁止让用户手填 UUID 或 JSON。

下一步：先补原始报废页面所用来源 GET 的真实鉴权/PG16 联测（批准前、原报废后、找回后、纠正后都需只读保留历史），并补专用纠正与找回四阶段来源查询，之后接对应 PC/H5 页面。对 serial 慢提交做可重复计时和约束调用剖析，保持完整库存/SN/幂等/审计约束。全量本地/远端 CI、真实浏览器、真实身份/短信微信、正式基线其他业务和全部上线验收仍未完成，不能因本批聚焦通过提交发布。

## 2026-10-03 最新：权限迁移及旧历史已通过，客户端恢复及五阶段真实HTTP联测已通过

**本节取代下方旧的运行中状态。** 保留工作树和全部未提交改动，未提交/推送/部署。完整目标仍是正式基线全部功能及生产验收。

- 正式权限接入后的运行时v5 **60079已exit=0**，目录`artifacts/local-scrap-runtime-pg16/run-oxgk2asl`：11项默认权限由迁移生成、无用户/无夹具授权；完整API启动、8类目录拒绝、空库降级/旧版启动/再升级通过。1589后端源无漂移且收据时与当前一致。收据`artifacts/formal-0165-integration/formal-runtime-v5-receipt.json`。
- 真实旧历史v2 **39557已exit=0**：quantity `run-ob2nxuud`、serial `run-_mrvah0j`，均stopped/passed/serverExitCode=0。各自旧业务行519/531和359个旧函数OID保留，11份旧原请求在升级/再升级后查回；原版0164降级后完整API启动通过。迁移仅新增4项旧库缺少的权限/角色绑定，并将5个受影响用户授权版本各增加一次；其他旧字段/业务事实不变，降级/再升级授权快照不变。收据`legacy-history-v2-receipt.json`位于上述formal证据目录；1589源与当前相同，不再轮询39557。
- 正式前端新增`formalScrapCommands.ts`和`formalScrapRecovery.ts`：严格六类完整命令、Python一致摘要、原操作者/阶段/业务对象绑定、发送前保存且回读、Web Locks跨页互斥、原请求不可覆盖、未知结果只读回查、not_found禁止自动重发、明确封存后再回查清理。写权限撤销后仍支持符合当前read权限的历史回查；新提交拒绝旧预览授权版本；页面/身份/权限变化和返回原对象错配保留原请求。
- 前端最新三文件聚焦 **137通过**，日志`scrap-client-recovery-v2.log`；TypeScript构建exit=0，日志`scrap-client-types-v3.log`；收据`scrap-client-recovery-v2-receipt.json`。12份`command-hashes.json`由真实后端模型/摘要函数导出，但属于合成请求，不能当业务事实。README已说明恢复编排样本的边界。**页面、真实来源列表和网络adapter尚未接入。**
- 安全v8 **85603已exit=0**，2520文件；加入客户端后v9 **49992已exit=0**，2525文件/54276954字节，`repository-safety-v9.log`；`git diff --check`通过。

### 实际HTTP联测1393已终态，无运行中的验证进程

`artifacts/formal-0165-integration/check_scrap_http_native.py`是本轮已结束的联测入口，日志`scrap-http-native-v1.log`。**句柄1393已exit=0**，目录`artifacts/local-scrap-business-pg16/run-pipakjwj`，stopped/passed/serverExitCode=0；1589源无漂移且收据时与当前一致。五阶段10次实际COMMIT、20次READ ONLY请求全部通过，收据`scrap-http-native-v1-receipt.json`记录运行入口SHA。不要再轮询或重启1393。当前所有已启动验证均终态，源码固定解除。

该入口自建全新quantity PG16，真实Alembic升级/默认角色验证，实际生成期初库存和独立报废批准，再走正式挂载HTTP。仅覆盖原始报废、找回申请、区域、总部、恢复执行五阶段；纠正来源HTTP和serial后续仍要补齐。认证使用本地合成AuthSession及签名token，调用真实get_current_user/get_formal_principal与API-role数据库，未替换principal；只有测试环境配置和数据库依赖定向到独立测试库。验证真正COMMIT后丢响应、提交前失败回滚、READ ONLY回查、封存后迟到写拒绝、撤写权限后查回原结果，库存审批状态分离。它不是微信/短信供应商真实登录验收。入口在artifacts内，不是已登记正式CI门禁；源码SHA及终态证据已保留，接续应按覆盖范围转入正式测试。

接续先将已通过的真实HTTP入口拆为正式helper并加入原生runner的独立模式（保持合成登录/外部供应商边界），补纠正/SN的HTTP覆盖；再补PC/H5可选业务来源/预览/附件与确认页面，把上述已测试恢复模块实际接上。继续保留旧退回补偿、人员调拨、离职交接、UUID迁移、报表打印、真实身份/渠道/UAT、历史迁移/期初、三天对账、性能、备份回滚等完整缺口；不能用本地测试代替上线验收。


## 2026-10-03 接续：完整原生业务已结束，HTTP已接入，正式权限迁移在验证

**最新状态以本节为准。** 下文“63688运行中”“HTTP尚未挂载”“权限只有草稿”均保留为历史，不能作为当前状态。仍使用06f6工作树、`codex/notification-delivery-worker`，HEAD `fc7c926`；未提交、推送或部署，禁止reset/revert或丢弃改动。

### 已取得终态证据

- 完整原生业务父句柄 **63688已exit=0**。数量 `run-4goc02ia` 与SN `run-1bmkpfhr` 均stopped/passed/serverExitCode=0，1582源运行期间无漂移、采集收据时与当时源码完全一致。汇总 `artifacts/formal-0165-integration/formal-business-v2-receipt.json`。真实全新0165迁移、完整默认API启动、两代业务、并发/撤权/到期、只读原请求恢复、封存及实际有事实拒降均通过。**这是接入HTTP和正式权限之前的源码清单，不是后续源码全量通过。** 不要再轮询或重启63688。
- 权限草稿13项SQLite聚焦通过；独立原生PG16验证v2句柄 **72476已exit=0**，目录 `artifacts/local-scrap-runtime-pg16/run-_qsk63hy`，1582源无漂移。精确11项默认授权、4个受影响账号各一次版本更新、自定义deny/原ID保留、外部账号不新增权限、重复安装不变、调用方完整回滚、旧principal拒绝及完整API启动通过。收据 `permission-policy-native-v2-receipt.json` 位于上述formal证据目录；它验证候选数据策略，不是正式Alembic接入后的验收，也未验证真实HTTP会话或旧请求。
- 权限原生v1句柄45784已exit=1，`run-patey53j`：测试把admin人员放进区域组织，被正式角色范围规则拒绝。只修正合成夹具的总部/区域/外部组织绑定，未放宽产品权限；v2重新建独立测试库通过。
- HTTP草稿全应用路由/未登录及无权限拒绝/错误脱敏/404、405隐私响应聚焦 **56通过**，句柄39914已exit=0。随后实际落入4个文件：`backend/app/stock_scrap_http_schemas.py`、`backend/app/routers/formal_stock_scrap.py`、`backend/tests/test_stock_scrap_http_adapter.py`、`backend/tests/test_stock_scrap_application.py`，并在`formal_stock_losses.py`包含router。实际源码新接口及原纠正接口聚焦 **280通过**，句柄50684已exit=0，日志 `http-integrated-v1.log`。21条路由正式挂在`/api/v1/stock-operations/loss-reports/scraps`；不是完整HTTP↔PG事务/UAT验收。

### 本轮正式权限改动及待验收

0165尚未提交、发布或部署，故把报损/纠正/找回的11项默认权限补入这一未发布迁移，不修改已发布0164，也不额外改变版本/目录链。新冻结数据策略 `backend/alembic/stock_scrap_0165/permission_policy.py` 由0165真实upgrade调用；SQLite仅做工具结构支持。已有权限/自定义ID/deny保持不变，受影响用户授权版本只递增一次；空库应用downgrade保留授权配置，不撤销后来修改的授权。授权撤销必须走独立审计流程；有新业务事实时原拒降规则继续有效。

- 单元测试已转为正式文件 `test_stock_operation_permission_policy.py`，不再依赖临时模块注入。策略正式文件额外限定`pg_catalog`引用，候选原生SHA不可冒充最终文件SHA。
- `pg16_stock_operation_permission_policy.py` 在任何业务夹具运行前检查新迁移库的11项精确角色/action/default-allow，拒绝缺项及外部授予；完整业务/运行时门禁已调用。旧报损来源夹具复用迁移权限，不再无条件新增同名权限；正式0165缺失时明确失败。
- 真实旧历史门禁独立核对：除新增的精确权限/角色绑定和受影响users授权版本外，旧表每列每行必须不变；原ID/deny/用户其他字段保持原样。降级和再升级仍须保留同一授权快照，并逐份查回11个历史原请求。此新版历史门禁尚未取得终态。
- 正式迁移聚焦句柄 **14514已exit=0，46通过**，日志 `permission-migration-focused-v1.log`：策略、真实SQLite Alembic往返/保留授权及readiness。无失败、无跳过。
- 当前运行时v5句柄 **60079**，日志 `formal-runtime-v5.log`；真实旧历史数量/SN往返v2句柄 **39557**，日志 `legacy-history-v2.log`。均在 `artifacts/formal-0165-integration/`。当前再次固定非Markdown后端/迁移/scripts/edge_sync/deployment/workflow源码直至两者终态；先轮询同一句柄，观察超时不是停止。之后补新HTTP与PG的实际事务/认证集成。
- 安全扫描v7退出127仅因从repo根调用不存在的相对脚本路径，未执行扫描；已改从cloud_oam目录用同一现有脚本运行v8，句柄85603、日志`repository-safety-v8.log`，不是依赖或客户端故障。

下一步还包括PC/H5来源列表、预览、附件、原请求发送前保存、跨页互斥、只读回查和明确封存。旧退回补偿、人员调拨、离职交接、身份UUID及完整基线生产验收继续保留。不要用本轮280或原生绿灯替代真实身份、历史迁移/期初、三天对账、性能、备份回滚和UAT。


## 最新接续：真实旧业务历史往返已通过，完整业务正式门禁正在运行

真实旧0164源码在隔离进程实际生成三轮冲销、独立纠正审批、纠正过账及晚到请求封存，每种模式保存11份原请求；没有用新版代码伪造“升级前历史”。旧历史句柄 **5862已exit=0**，禁止重启或继续轮询。汇总收据 `artifacts/formal-0165-integration/legacy-history-v1-receipt.json`；两个测试库均stopped/passed/serverExitCode=0，1581源运行期间无漂移且与收据时当前源一致。后续仅调整完整业务gate及新增拒降验证helper，不能把旧清单说成后续所有源码完全相同。

- 数量模式 `artifacts/local-scrap-legacy-history-pg16/run-el3z_jar`：519行旧记录、359个旧函数OID保留。
- SN模式 `artifacts/local-scrap-legacy-history-pg16/run-5esafhcx`：531行旧记录、359个旧函数OID保留。
- 两种模式均真实Alembic 0164→0165→0164→0165成功，降级后的原版0164完整API启动通过。各11份原请求在升级和再升级后只读查回原结果，每次回查前后全库事实不变；原始/三代逆向/三代审批/三代纠正/封存均覆盖。
- 收货/独立入库/请求恢复前端聚焦**32通过（2文件）**，日志 `artifacts/formal-0165-integration/return-inbound-client-v2.log`，收据 `return-inbound-client-v2-receipt.json`。首次PATH没有Node导致v1退出127；使用客户端既有Node绝对路径后v2退出0，无依赖安装或客户端重启。

### 当前唯一业务测试：句柄63688

完整报废业务gate已改为真实Alembic直接升级0165及默认API启动，不重复安装候选DDL或临时授权。新增 `backend/tests/pg16_scrap_formal_retention.py`：分别在仅有新封存、已有报废/找回/多代纠正事实时执行真实Alembic降级，要求明确拒绝，并核对全部表行、列、目录、函数定义/OID、版本均不变，随后重新验证默认API启动。现有数量/SN、并发、撤权、到期、原请求回查及封存用例全部保留。空库往返和真实旧历史往返由各自已通过的独立gate覆盖。

- **句柄63688正在运行**，日志 `artifacts/formal-0165-integration/formal-business-v2.log`，目录 `artifacts/local-scrap-business-pg16/run-*`。quantity目录 `artifacts/local-scrap-business-pg16/run-4goc02ia` 已终态stopped/passed/serverExitCode=0，1582源码无漂移且与核验时当前后端清单一致。真实0165全链升级、两代报废/找回、12场同请求竞争、2场跨登记竞争、12场撤权竞争、6类自然到期、原请求只读恢复及两类实际Alembic拒降均通过；收据 `formal-business-v2-quantity-receipt.json`。父句柄仍运行，当前serial目录 `artifacts/local-scrap-business-pg16/run-1bmkpfhr`，不能提前报两种模式全通过。
- 运行期间固定非Markdown的backend/edge_sync/scripts/deployment/迁移及PG workflow源码。先轮询同一句柄、核验具体owned run的日志与cluster-state；观察超时不代表终止，不另起重复测试。修改源码前必须先确认终态。
- 安全扫描v4句柄81189已exit=0；搜索路径修复后v5句柄36298也已exit=0，日志 `artifacts/formal-0165-integration/repository-safety-v5.log` 为PASS。后续仅有本交接的状态文字调整。
- 原生完整业务v1句柄2036已exit=1，目录 `run-trlgndtl` 已stopped/failed/serverExitCode=0。发现真实全新安装缺陷：全链同事务中前序迁移残留pg_catalog优先的search_path，0165冻结的非限定CREATE TABLE误选系统schema，权限拒绝。正式0165迁移现保存原search_path，仅在迁移范围设置LOCAL public（pg_catalog仍隐式优先查找），成功后恢复原值；失败由Alembic事务回滚。没有给系统schema授权、改冻结SQL、改函数内部search_path或分段迁移掩盖问题。v2仍从空库一次升级0165，需待终态。
- HTTP及PC/H5报废/找回原请求保存与中断恢复仍需正式router及客户端接入。完整基线的下游退回补偿、旧成色纠正、人员调拨、离职交接、UUID迁移、报表打印与真实身份/渠道/UAT/历史迁移/三天对账/性能/备份回滚仍未完成。
- 未提交、推送或部署；GitHub当前源码未验收。完成必要门禁及正式基线缺口后才考虑提交和上线。

### 并行准备的HTTP接入草稿（未覆盖运行中的源码）

`artifacts/formal-0165-integration/http-entry-draft/` 保留三个完整文件：`stock_scrap_http_schemas.py`、`formal_stock_scrap.py`、`test_stock_scrap_http_adapter.py`。在独立Python进程按真实app模块名加载草稿，不改当前backend源码，不使原生运行清单漂移。

- 21个显式POST路由：原始/纠正报废各自预览、执行、回查、封存；找回申请、区域复核、总部复核、执行各自写入/回查/封存，执行另有预览。当前尚未挂到正式应用。
- 每阶段精确权限；只读回查只要求read，服务仍校验对象范围/原操作者；请求头必须匹配完整原请求；结果序列化校验后最多一次COMMIT。未知结果503不重发，封存只接受found或sealed。审批响应stock_effect固定none，恢复响应明确恢复原冻结份额。
- 每条新路由自带RequestValidationError脱敏，防止框架把原请求幂等键/附件信息回显。不会改旧路由错误格式。新表内部命令、键hash及证明不对外输出。
- 草稿适配器v1 **144通过**，增加预览/验证错误脱敏/完整路由集合后v2 **166通过**，句柄19352/18010均exit=0。日志 `http-adapter-draft-v{1,2}.log`；收据 `http-adapter-draft-v2-receipt.json`，均在 `artifacts/formal-0165-integration/`。
- 另对当前owned PG16数量测试库做REPEATABLE READ READ ONLY查询，取实际已提交的2份报废、2份申请、2份区域、2份总部、2份恢复结果，严格公开schema解析后JSON逐项一致（10份）。句柄81567已exit=0，收据 `http-native-payload-v1-receipt.json`。这只是返回契约与真实业务结果的兼容证明，不是完整HTTP事务或生产验收。
- 发现四种找回权限目前由原生夹具显式授予，公共服务还要求精确角色/范围/独立审批，不能借通用冲销权限。正式权限登记/授予及撤权、授权版本变更的迁移或受控配置流程仍须实现和验收；不得把合成夹具的grant称为正式权限开通。

**接续步骤**：先等63688的quantity/serial两种模式完整终态并核对manifest，再把上述草稿分别落入 `backend/app/stock_scrap_http_schemas.py`、`backend/app/routers/formal_stock_scrap.py`、`backend/tests/test_stock_scrap_http_adapter.py`，在 `formal_stock_losses.py` 下包含新router。重新运行实际源码适配器测试及完整应用路由/鉴权测试，再补真实HTTP事务集成。旧历史门禁通过后又修改过0165迁移search_path，新的空库往返/降级旧版启动仍需按改动范围复核；不能把修复前的旧清单当作当前源完全相同。

随后扩展来源查询和PC/H5：已有 `stock_loss_execution_sources.py`/`FormalLossExecution.tsx` 可作为原始批准入口；纠正来源、找回申请及两级复核/执行需各自真实只读查询，不能要求用户手填业务UUID或JSON。完整原请求须发送前持久保存并跨页互斥；刷新/断网后只读回查，未找到不自动重发，明确封存后才能结束原请求。当前这些客户端流程还没接入。

### 新增终态、前端契约及正式权限阻塞（最新）

- 搜索路径修复后的原生运行时v4句柄 **42866已exit=0**，目录 `artifacts/local-scrap-runtime-pg16/run-26o8yhek`：默认API启动、8类目录篡改拒绝、空库降级/原版0164完整启动/再升级均通过。stopped/passed/serverExitCode=0，1582源无漂移且核验时一致。收据 `artifacts/formal-0165-integration/formal-runtime-v4-receipt.json`，不可重启此终态句柄。
- 新增实际前端源码 `frontend/src/formalScrapFacts.ts`，严格区分六种原请求阶段的报废、审批、恢复和封存结果；匹配原请求ID/hash，拒绝“审批=过账”、跨阶段/跨请求结果、缺失流水、错误资产边界、内部字段及retry_allowed=true。找回只表示恢复原冻结份额，历史结果不冒充当前库存。
- 前端样本 `frontend/src/test-fixtures/stock-scrap/committed-quantity-results.json` 来自quantity owned PG16实际提交的10份结果（README记录证据边界/SHA），只读可重复读导出。前端聚焦 **48通过**、TypeScript完整构建exit=0；日志 `scrap-client-facts-v1.log`/`scrap-client-types-v1.log` 在上述证据目录。页面、网络adapter和原请求存储尚未接入，不能称客户端功能完成。
- HTTP草稿增加原逆向来源及封存根绑定校验，并读取同一10份原生样本证明Python/TypeScript结果契约兼容。最新 **167通过**，句柄90377已exit=0，日志 `http-adapter-draft-v3.log`；此前166为旧稿。汇总当前草稿和前端源码摘要在 `scrap-public-contract-v1-receipt.json`。草稿仍未覆盖backend，未影响正在运行的原生清单。

**新增明确上线缺口**：在无用户、无业务夹具的正式0165迁移库中查询权限，只发现17条stock_operation角色配置（read、submit/cancel/outbound/ship/receive_return）；缺少11种正式操作权限及其默认角色配置。证据 `stock-operation-seed-audit-v1.json` 与 `stock-operation-required-policy-v1.json`。不是只缺新找回的四项，旧报损/纠正的七项也由测试夹具补齐，不能把夹具授权当作正式可用。

| 角色 | 缺失操作 |
| --- | --- |
| technician（本人范围） | submit_loss、apply_scrap_recovery |
| provincial_manager（本区域） | review_loss_regional、review_scrap_recovery_regional |
| admin（总部） | finalize_loss、dispose_loss、reverse_loss、approve_loss_correction、correct_loss、review_scrap_recovery_headquarters、execute_scrap_recovery |

解除源码固定后，补正式权限迁移/配置：保留显式deny与既有自定义配置，禁止给外部审批角色授权，正确失效受影响账号的授权版本，并验证新库、已有账号/旧请求、冲突、自审禁止、撤权和回退。原生业务夹具应核验正式权限而不是补权限后掩盖缺失；专门的撤权/竞争夹具仍应保留。再落入HTTP草稿、完整应用挂载/鉴权与真实HTTP事务，继续PC/H5来源列表、预览、原请求持久化、只读恢复/显式封存。

## 当前状态：0165正式入口已接入，原生升级与旧版回退启动已通过

本轮将十表模型注册到Base，新增唯一后继迁移`20261214_0165_stock_scrap_and_recovery.py`，并接入默认API目录校验、OAM同步readiness及日终对账版本检查。只读应用目录与冻结迁移互不导入；readiness仅替换确切版本字面量，保留ACL、所有权和其他函数正文。HEAD仍为`fc7c926`；未提交、推送或部署，不等于正式上线。

- 旧0164源码完整快照保留在`artifacts/formal-0165-integration/predecessor-source-v1/source/cloud_oam`；1568文件逐字节复制，用于隔离进程的旧版启动验证。它是激活前工作区快照，不冒充Git发布版本。
- 最新模型聚焦28通过。新增SQLite冻结迁移仅用于结构工具，保留五张父表旧列、索引、触发器和视图；十张新表拒绝业务写入，任何新事实或绑定保留时拒绝降级。真实Alembic往返、结构/可空性对齐、失败回滚和同名触发器检查合计**38通过**，句柄60084已exit=0，日志`artifacts/formal-0165-integration/sqlite-v1/formal-roundtrip-v3.log`。
- 完整数据库安全、正式目录注册/readiness及单一迁移head图检查**329通过**，句柄82342已exit=0，日志`artifacts/formal-0165-integration/formal-security-v2.log`。与38项中的触发器用例有重叠，不能相加为独立用例总数。
- 原生v1句柄43958已exit=0，目录`artifacts/local-scrap-runtime-pg16/run-drd4yftk`；真实Alembic升级、默认API启动、8类真实目录篡改拒绝、空库降级/再升级全部通过，stopped/passed/serverExitCode=0，1578源运行期间无漂移。v1未运行原版0164启动，不能替代下一项。
- 原生v3句柄4949已exit=0、目录`artifacts/local-scrap-runtime-pg16/run-hczbkyaa`，日志`artifacts/formal-0165-integration/formal-native-v3.log`：已通过升级、8类篡改拒绝、空库降级、**原版0164独立进程完整API启动**及重新升级后的新版完整启动。stopped/passed/serverExitCode=0，1579源码运行期间无漂移且与收据时当前源码完全一致；日志/checks/terminal核对一致。收据`artifacts/formal-0165-integration/formal-native-v3-receipt.json`。源码固定已解除，禁止轮询或重启此终态句柄。
- 最终安全扫描`repository-safety-v3.log`通过（2506文件、54106835字节），句柄70065已exit=0；`git diff --check`通过。汇总收据`artifacts/formal-0165-integration/formal-0165-integration-final-v1.json`，本轮所有测试进程已终态。原生v2失败记录保留：测试子进程误用API账号读取`alembic_version`，被最小权限正确拒绝；现由迁移账号核对版本，旧API进程不新增权限，旧源码快照不变。

### 本轮失败与修复边界

1. SQLite初轮22失败/4通过，旧SQLAlchemy迁移跨进程的约束排列顺序不固定。现在仅规范化表约束顺序，保留列顺序、完整表达式、重复约束和表选项；索引/触发器逐字核对，PG验证未放宽。修复后27通过，加入正式Alembic及触发器检查后38通过。
2. 首轮正式注册19通过/5失败，旧测试夹具把复合触发器key当字符串，已修复并包含在38/329通过内。
3. 完整安全初轮300通过/5失败：新增十表权限清单、单独标量函数夹具清理集合返回登记、布尔篡改必须确实改变值，以及153个新增触发器预期需同步。现逐元组比对冻结迁移目录，保留拒绝验证；复跑329通过。

### 接下来按正式业务目标推进

1. 原生v3终态和源码对齐已收齐；保留本轮证据，继续开发后按改动范围决定复测，不能将本轮空库迁移结论扩大为有业务历史的升级验收。
2. 将数量/SN完整业务gate接入真实0165迁移。`pg16_scrap_business_gate.py`仍是候选时期的0164基座和显式安装流程，不能直接用已激活的新默认运行时执行它，也不能重复安装0165。保留其现有并发、撤销、到期、反向事实、只读请求回查等覆盖。
3. 用保留的真实0164源码生成升级前已有逆向、纠正及请求键记录，正式升级后验证原记录/流水/原请求恢复。空库回退启动通过不能当作这项有数据升级证明。有事实/封存拒绝正式降级仍须接实际Alembic入口。
4. HTTP及PC/H5原请求保存与中断恢复：当前`stock_scrap_*`服务尚无对应正式router；参考现有`formal_loss_execution.py`、`formal_loss_execution_recovery.py`和`formal_loss_corrections.py`的独立预览/写入/回查/封存边界。禁止把“未查到”当自动重发授权。
5. 完整生产范围仍包含部分/下游退回补偿、旧成色授权纠正、人员调拨、离职交接、UUID兼容迁移、报表打印和全部真实身份/渠道/UAT/历史迁移/期初/三天对账/500用户性能/RPO与RTO/三种回滚验收。GitHub当前SHA尚未重新验证；完成必要门禁后才考虑提交。

以下是正式接入前已结束批次，源码已变化，不能当成当前全套通过。

## 前一批：统一十表模型、候选运行时目录及完整报废回归通过

本轮保留既有06f6/oam工作树（`${RSC_REPO_ROOT}`）、分支 `codex/notification-delivery-worker`，HEAD仍 `fc7c926`。未提交、推送或部署，没有reset/revert或丢弃原改动。正式Alembic/Base默认入口仍为0164；不得把下面的候选运行时通过称为正式0165已发布。

### 本轮实现及已结束证据

- `stock_scrap_schema.py` 统一八张业务事实表、新请求键表和封存表，并包含四张父表扩展及旧请求键的两个别名列。写入、原请求回查、封存回查共用同一份完整表句柄；复制元数据保留PostgreSQL/SQLite条件约束。尚未在Base全局注册。
- 新请求键表补齐七个真实外键、四类精确来源、摘要格式、别名互斥及唯一约束。最初聚焦 `41788` 已exit=0，**51 passed**（`scrap-unified-schema-focused-v1.log`）。SQLite读取故障注入保留明确降级的测试存储，真实关系约束另由完整模型测试及原生迁移验证，不能混淆。
- `stock_scrap_security.json` 是冻结迁移目录的只读投影，不含安装SQL；API模块不依赖Alembic代码。运行目录增加10张可读表，其中8张可INSERT；两个登记/封存表依然不能由API直接INSERT。登记39个新函数、严格替换13个旧函数，并更新0159/0163独立验证器中的精确目录，未跳过旧验证器。
- 原生 `41899` **exit=0**，目录 `artifacts/local-scrap-runtime-pg16/run-xyxawpab`。完整前向API安全检查通过；真实提交7类目录篡改（PUBLIC函数权限、新登记表INSERT、封存表INSERT、单表同名触发器禁用、旧函数正文、旧别名CHECK、同名函数重载）均被拒绝；逐次恢复完整目录后再次通过。1568个源码文件与收据时当前源完全一致，日志/checks/terminal一致，测试库stopped/passed/serverExitCode=0。收据：`artifacts/formal-baseline-audit-20261002-0164/scrap-runtime-catalog-v5-receipt.json`。
- 本原生helper在隔离进程显式登记候选目录并刷新派生触发器查询，再调用真实API角色的完整既有启动验证；新增精确目录验证单独调用。**正式应用默认导入、Alembic版本提升和同步readiness还没有接入**。本轮也不是完整数量/SN业务原生重跑。
- 安全聚焦 `42907` **exit=0，322 passed（213.94s）**，日志 `scrap-runtime-catalog-focused-v7.log`。历史夹具修复聚焦 `90366` **exit=0，15 passed（189.36s）**，日志 `scrap-unified-fixture-focused-v2.log`。上述数量有重叠，不能相加当全套用例数。

### 本轮真实问题及修复

1. 旧全局触发器校验假设名称在全schema唯一。实际新流程在多表使用同名触发器，审计事件/链头也有同名绑定。业务和审计校验均改为“表名+触发器名”，保留缺失、额外、重复、禁用、错误表和完整属性拒绝；旧字段诊断继续保留。
2. 旧函数目录的`uuid,text`与冻结快照的`uuid, text`造成漏更新。现在仅规范化参数逗号空白来匹配同一签名，正文、ACL、所有权与哈希仍严格一致。
3. 原校验只允许标量、输入参数，并假定新增函数都是PL/pgSQL。现精确登记一个内部TABLE返回函数的输入/输出模式及真实SQL函数语言；其他函数仍沿用原限制，不给API额外执行权限。
4. 统一元数据后，历史快照包含了新请求键表，但旧SQLite夹具未创建它。已补齐所有十表的夹具存储。原完整回归 `93795` 在8个相同“表不存在”失败独立复现后，主动SIGINT退出2（76 passed、8 failed），没有把观察超时当终止；旧失败日志保留。原生v1–v4定位失败也保留，全部测试库正常退出。

### 完整回归已终态及下一步

完整报废Python回归父句柄 **69202已exit=0**，三组独立pytest均exit=0，覆盖全部30个`test_stock_scrap_*.py`模块，无重复遗漏。共 **354 passed、3 skipped**：三项为数量模式不适用的SN准入、位置和生命周期缓存测试，相应SN分支已执行。分组分别117通过、132通过/1跳过、105通过/2跳过。日志 `scrap-unified-regression-group{1,2,3}-v2.log`，完整源码清单 `scrap-unified-regression-v2-source-manifest.json`，终态收据 `scrap-unified-regression-v2-receipt.json`；1568源与本轮原生运行时及收据时当前源完全一致。上述路径均在 `artifacts/formal-baseline-audit-20261002-0164/`。**所有业务/安全测试均已结束，禁止轮询或重启旧句柄；源码固定解除。**

静态模型核对 `scrap-model-column-parity-v2.json` 确认15张相关表的字段集合、类型及可空性相同，10张新表列顺序也完全一致。三张旧表的模型/物理列顺序差异原已存在，新增字段仍按相同顺序追加；没有为对齐显示顺序重建旧表。此项不冒充原生FK/CHECK模型等价验证。

接下来正式Base/单一0165 revision/同步readiness及默认运行时校验一起接入；处理历史固定0164测试与SQLite结构工具的兼容；再验证真实旧逆向/纠正/请求键历史升级不变及有事实拒降。随后接HTTP、PC/H5原请求保存与中断恢复，并完成最终源码CI、真实身份/UAT及基线的迁移、三天对账、性能、备份回滚验收。完整目标仍活动，不需要用户补凭证或重启。

下文旧“当前/运行中”均为历史，以本节及实时终态回执为准。

## 前置证据与不能混用的范围

- 冻结安装包：`backend/alembic/stock_scrap_0165/catalog.json`，SHA256 `424d3f52cad7fe58f9d8656cabc5417beb428394a8a450c46aa23246975e07f2`；131条SQL，10新表、40个受影响旧表、39新函数和13旧函数替换。`transition.py`/`catalog_probe.py`不导入实时应用模型或旧迁移模块。
- 前轮完整数量/SN业务v3：`run-6d4s80hi` / `run-clo3s7_3`，收据 `scrap-frozen-transition-v3-receipt.json`。空库往返、409/414行前驱事实、359个旧函数OID保留，封存和两代业务事实存在时拒降；只有期初/报损/独立审批是升级前真实历史，**不能当成已有0164逆向/纠正/旧key历史升级证明**。
- 降级启动修复v4：`run-rg6chf67`，收据 `scrap-transition-readiness-v4-receipt.json`。两次空库降级后的真实API启动通过；允许正常删除列占位，仍拒绝必需列缺失、额外可见列和列ACL。完整业务v3、降级启动v4、本轮候选运行时v5是不同源码快照。
- 上述收据均在 `artifacts/formal-baseline-audit-20261002-0164/`；原生目录在各自`artifacts/local-scrap-*-pg16/`。先读取具体terminal/cluster-state/source-manifest，再引用结果；不轮询历史终态句柄。

## 正式接入注意

1. 应用镜像只复制`backend/app`，所以候选运行时目录和只读probe已放在app内，不能改为运行时导入Alembic。正式导入顺序应在0159/0162/0163/0164目录登记后、派生SQL构建前登记0165，并在完整启动入口调用新增精确验证。
2. 旧全局安全目录保留字符串触发器key；新目录使用`(table, trigger)`。实际校验已兼容两者。正式激活时需同步更新仍按字符串排序/生成假的系统目录行的旧测试夹具，不能删掉真实目录拒绝测试。
3. 新TABLE返回函数的形状三元组第三项是`strict`，不是`returns_set`；集合返回和输入/输出模式单独精确登记。新函数语言从冻结定义取得；SQL和PL/pgSQL不能混写。
4. 核对迁移图必须使用 `migration_script_cache.cache_migration_compilation(Path("backend/alembic/versions"))` 包围ScriptDirectory读取。裸`get_heads()`会反复解析历史runpy依赖，前轮已中止一个耗时只读进程，不能因此重启已结束的PG测试。
5. 保留正式基线全部范围：人员调拨、离职交接、下游退回补偿、旧成色纠正、身份UUID兼容、报表打印及全部生产验收。公开首页查询、星星按钮跳转`/xx`、飞书知识源低优先级的用户决定继续有效。

## 历史交接完整保留

整理前803行交接的原字节保存在 [历史完整快照](history/CONTINUE_DEVELOPMENT_before_runtime_catalog_20261002_132047.md)，SHA256 `d2381dfaf44893ce82a3206924b3fb87faa71d037e917dfec4a318c4f0fb790d`。历史“当前/运行中”不代表实时状态；本页只保留当前入口和必要前置证据。

## 2026-10-07 试点 MVP 界面冻结增量（当前工作树）

本次只推进有限里程碑：`申请/提交 → 审批 → 最小货源分配/占用 → 区域/总部后台人工履约并记录发运 → 本人收货 → 个人仓入账`。当前版本明确标记为**试点 MVP，不等同完整 V1**。

- 试点前端在 `FormalMaterialRequests` 中传入 `allowSupplyPlanning={false}`：隐藏供给容量/跨区域计划的创建、列表和编辑入口，保留已批准需求的货源候选读取、最小分配事实、幂等坐标和结果待核验恢复。组件默认值仍为 `true`，后端 API、状态轴、迁移和契约未删除，供后续迭代使用。
- 发运面板新增 `allowLogisticsEvents` capability；试点页面关闭物流事件读取、登记和回读，只保留人工发运记录。物流事件的 API、恢复哨兵和默认组件能力继续保留。
- 试点页面同步隐藏 OAM 收货证据面板（`TRIAL_MVP_UI_SCOPE.showOamReceipt=false`）；OAM 收货状态轴、只读适配器和审计事实保留，待后续对账迭代重新开放。
- 试点页面默认隐藏拒收退回/退回补偿操作（`TRIAL_MVP_UI_SCOPE.showReturnOperations=false`）；已有恢复哨兵会重新显示核验入口，防止未知结果被遮蔽。
- 试点页面默认隐藏释放、剩余取消和复杂关闭操作（`TRIAL_MVP_UI_SCOPE.showReleaseOperations=false`、`TRIAL_MVP_UI_SCOPE.showComplexLifecycle=false`）；已有释放/取消/关闭哨兵仍会重新显示核验入口，底层取消/关闭守卫不变。
- 拣货隐藏保持；技术员只保留申请、状态查看、本人收货和本人入账。区域/总部仍按 capability 看到必要的后台履约入口；复杂后置区块按角色/能力隔离。
- 后续迭代清单继续保留：拒收/退回补偿、`stock-return` 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实渠道及投递运维；不得将隐藏入口解释为已验收。
- 新增范围回归：`FormalMaterialRequestSupplyPanel.test.tsx` 试点供给计划隐藏 `1 passed`，`FormalMaterialRequestShipmentPanel.test.tsx` 物流事件隐藏 `1 passed`；TypeScript `tsc -b --pretty false`：`exit 0`。未重跑已终态测试，也未提交、推送或部署。
- 新增 `trialMvpScope.test.ts` 聚焦测试（`1 passed`）与源码范围门禁 `trial-mvp-ui-scope=PASS`：OAM、退回补偿、释放、复杂生命周期均默认关闭，且待核验哨兵重新打开入口；物流事件和供给计划入口均确认关闭。
- 发布绑定继续收紧：`frontend/build/verify-pilot-release.mjs` 现在静态读取 `src/trialMvpScope.ts`，逐项锁定六个后置 capability 为 `false`，并要求 scope 对象使用 `Object.freeze`；`node --check`、`pilot-ui-scope-release-binding=PASS` 和一次试点 artifact 验证均通过（`exit 0`，`privatePath=/xx/`）。该验证明确回报公开目录 `status=pending, records=0, publicCatalogIsReady=false`，因此只证明私有试点构建自洽，不代表公开/完整生产发布；hosted PostgreSQL 16 仍是外部阻塞，未用本地结果替代。
- 全局入口复核：`App.tsx` 对技术员菜单和直接 URL 继续统一隐藏/重定向库存、盘点、报损、退回、报表、通知、对账及人员管理等后置模块；需求提报路由仍保留。该边界使用既有聚焦覆盖，本轮未重跑已终态测试。
- PG16 hosted DB 门禁仍是外部阻塞；本地编译/聚焦测试不构成 hosted DB 通过、生产迁移或上线证据。
# 2026-10-07 前端双入口构建复核

- 首次执行 `pnpm run build` 的退出码为 1，原因是当前 shell 的 PATH 没有 `node`；`frontend/node_modules/.bin/tsc` 因此报 `exec: node: not found`。这属于运行时环境问题，不是 TypeScript/Vite 编译断言失败；未安装新依赖、未改系统环境。
- 使用仓库既有 bundled Node（`<bundled-node-runtime>/bin`）置于 PATH 后，`pnpm run build`：**exit 0**；`pnpm run build:warehouse`：**exit 0**。
- `pnpm run verify:pilot-release`：**exit 0**，回读 `catalog.status=pending`、`records=0`、`privatePath=/xx/`、`publicCatalogIsReady=false`；`cloud_oam/scripts/verify_public_entry.mjs`：**exit 0**，确认公开 bundle 无登录客户端且 `/xx` 产物/worker scope/迷你目录契约一致。
- 以上是当前工作树本地产物证据，不替代真实域名/TLS、浏览器/真机 UAT、hosted PostgreSQL 16、短信投递、目标机部署或回滚；公共知识源按用户要求继续暂缓，版本仍为“试点 MVP，不等同完整 V1”。

# 2026-10-08 后端当前头绑定修复与受控复核

- 受控执行 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests` 在 45 分钟后因已定位的可修复失败主动中断，终端汇总为 **2890 passed、7 skipped、2 failed、15 subtests passed、1 warning，exit 2**。两项失败均为迁移当前头绑定，不是认证或业务流程失败：`scripts/configure_inventory_control.py` 仍固定 0166，而 Alembic 单头已是 0178；历史 0167 来源链测试把历史 revision 与当前 head 及当前 readiness hash 错绑。
- 已做最小修复：控制配置预检的 `REQUIRED_HEAD` 改为 `20261227_0178`；历史来源测试改为对比冻结的 `OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0167`，当前 manifest 仍单独对比当前 head helper，未修改任何历史迁移正文、状态轴或后置供给迁移。
- 修复后相关文件复核 **60 passed、1 warning、exit 0**，其中库存控制 CLI 单头绑定与 0164→0167 来源链均通过。未重跑已终态的 Web/小程序/通知/数据库安全等测试，也未把这次局部通过扩大为完整后端通过。
- 当前本地代码门禁继续为“可推进但未发布”：hosted PostgreSQL 16/远程 CI 新 SHA、真实 PNVS/SMS、浏览器/真机 UAT、目标机部署与回滚仍缺证据；不提交、不推送、不部署，直到外部证据补齐。
- 迁移头交叉复核使用 `cache_migration_compilation` 后通过：`alembic_heads=['20261227_0178']`、CLI `REQUIRED_HEAD`、PG16 gate `HEAD_REVISION` 三者一致，当前 readiness manifest hash `ee5e4a7b51850fda0ea41b5a71b34547cd822c1069075fc31c7ec948b905fb3d` 一致；两份改动文件 `py_compile` 与 `git diff --check` 均 exit 0。首次复核因命令遗漏 `backend/tests` 的 import path 退出 1，已按明确原因修正，未重跑业务测试。

## 2026-10-11 当前树仓库安全扫描复核

- 当前树首次执行 `bash scripts/verify_repository_safety.sh` 发现 1 项文档问题并以 **exit 1** 终止：`CONTINUE_DEVELOPMENT.md` 仍含个人 macOS home 绝对路径。该问题不是凭证泄露，但违反仓库个人路径门禁。
- 已将两处文档中的本机 Edge/Node 绝对路径替换为不含个人路径的占位描述，未改变证据含义，也未修改业务代码或测试策略。
- 修复后同一扫描命令通过：**3062 candidate files、75,817,630 bytes、exit 0**。当前 `git diff --check` 继续通过；该结果是当前未提交树的新安全证据，不替代 hosted PostgreSQL 16、真实 PNVS/UAT 或部署回滚。
- 当前树安全回执已保存为 `artifacts/formal-0165-integration/current-tree-safety-20261007T220734Z.json`：HEAD `fc7c9269e19f6afd180a0aced55b565f03c244b6`、工作树状态计数 776、仓库安全扫描 exit 0、个人路径/凭证发现均为 0；同时记录交接和正式审计文档 SHA。该回执明确将 hosted PG16、当前头远程 CI、真实 PNVS/UAT、部署回滚标为 `unverified`。

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
