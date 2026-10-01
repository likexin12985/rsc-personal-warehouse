# RSC个人仓开发交接

## 当前工作树：历史迁移 CI 修复与报废契约（2026-10-02）

历史迁移修复已本地提交 `733c31a`，远端仍为 `cb0210d`。旧 SHA 的 PG16 run `36926005735` 现有两项已确认失败：migrations/0051 历史库准备和 inventory/0040 回退，均是完整版本图加载触发 180 秒进程超时；其他任务继续运行，不重启。日志、修复与证据见 [历史迁移加载修复](PG16_HISTORICAL_GRAPH_TIMEOUT_20261002.md)。客户端 CI 已通过的事实不变。此后的契约、业务开发和推送状态以 Git 及下列机器回执为准。

已实现：历史夹具按准确前驱及 depends_on 闭包复制全部 51 个原始迁移，后续升 head 仍用完整链。原生目录相等证明已通过，0051 准备由 69.769 秒降至 2.907 秒，两库正常停止；完整链进程预算另统一至本地已有的 600 秒，不改 SQL 超时、权限或业务断言，不称性能门禁通过。30 项基础聚焦、3 项夹具接入、原当前 head 来源回归已有独立终态。

完整历史三场景复验均已通过，三个私有 PG 正常停止，全部源摘要无漂移。仓库安全检查进程 31192 已正常退出，2343 个候选文件通过；`artifacts/formal-baseline-audit-20261002-0164/continuation-current.json` 已记录终态。本地修复提交前证据收齐；GitHub 新 SHA 与生产验收仍待完成，旧失败日志全部保留。

报废开发已开始：新增严格请求契约，原始/纠正两种来源、完整原请求恢复与封存，24 项输入边界测试通过。**迁移、持久事实、原子报废/恢复、生命周期和界面尚未实现，不开放新写入口。** 继续按 [完整实现方案](STOCK_SCRAP_IMPLEMENTATION_AFTER_0164.md) 推进，完整目标 active，未部署。

## 当前接续：0164 已推送，正式缺口已重新核验（2026-10-02 05:08）

本地与远端均为 `cb0210da25881d2198a6e31b2195a894e4e74409`。客户端 CI `36926005738` 成功；PG16 CI `36926005735` 共 66 项，当前 5 成功、20 运行、41 排队，未终态。继续查同一 run，不重启仍在运行的任务。准确快照在 `artifacts/formal-baseline-audit-20261002-0164/github-20261001T210839Z.json`；上一批提交机器回执已更新 pushed/remoteHead，原回执保留。

最新正式审计见 [当前验收与缺口](FORMAL_V1_CURRENT_ACCEPTANCE_AUDIT_20261002.md)。本轮直接核对了源码和 PostgreSQL 类型编译元数据：除报废/恢复、部分和下游退回补偿、旧分类纠正外，正式人员间调拨、离职交接及完整报表/打印仍未形成上线证据；登录 UUID 差异涉及 users、auth_sessions、wechat_identities，不只是 users。旧版 transfers 生产不挂载，restricted_handover 账号状态不能替代离职交接流程。

下一业务实施为 [报废与失而复得完整链](STOCK_SCRAP_IMPLEMENTATION_AFTER_0164.md)，数量/SN、原始和纠正来源、独立恢复审批、迁移/权限/原请求恢复及前端全部纳入。**方案尚未实现，完整上线目标 active，未部署。** 本轮文档改动保留在工作树；旧 0164 代码及门禁证据不被改写。下方“下一步提交”是提交前历史，已完成。

## 最新接续：0164 审计锁范围修复与门禁诊断（2026-10-02）

本批提交前基线为 `cbd9223c9daf953e7feecf0e51180a9e54b029e4`，分支 `codex/notification-delivery-worker`。累计 0158–0163 已推送，本批 0164 及门禁修复已完成本地复验。当前提交和远端状态以 Git 及 `artifacts/authentication-fence-0164-next/main-application-v2/current.json` 为准；未部署生产。禁止 reset、revert 或丢弃改动。首次接续须完整读取根目录正式 V1.0 基线；完整上线目标仍未完成。

### 已确认的问题与完成的工作

- 客户端 CI 原因是 36 个文件包含本机路径。已修正版本化证据定位元数据、文档路径及 `dist-public` 忽略/门禁；原字节保存在 `artifacts/repository-safety-20261002/preserved/`。0161 SQL、权限及业务状态未改，仅同步证据目录元数据摘要。
- 历史迁移临时数据库缺少私有 UUID 扩展。已在该临时库的 postgres 引导阶段安装已审查扩展脚本；主树聚焦 19 项及临时库/部署夹具 22 项通过。另已在新建 PG16 中实际调用共用 scratch helper：临时库私有 UUID 安装及重复引导通过，migrator 可生成 UUID、API 调用被拒绝，临时库删除后主库 projector CONNECT 恢复；正常停库终态见 `E/cache-and-scratch-native-verified.json`。
- 主树 PG16.15 升 0163、降 0140、再升 0163，启动/ACL/UUID 漂移拒绝及恢复、9 项短信配置保护通过。旧期初历史三场景（逐级回填、直接升当前、历史规则漂移拒绝）均完成，原事实保留，进程退出且三个私有数据库正常停止；见 `repository-safety-20261002/legacy-opening-v2.log`。
- 原 SHA 的登录及报损纠正/封存失败，均在认证审计 COMMIT 中等待库存头；控制数发布并发死锁也由 0161 守卫对独立授权审计取库存锁引起。根因是 `rsc_fence_loss_correction_seal_0161` 的审计域范围过大。0164 第二版现已完成候选门禁并应用主树，合并后的 83 项聚焦回归、仓库安全及 PG16 完整启动/权限/往返已通过；准确新 SHA 的 CI 及生产验收仍待完成。
- 库存矩阵降至 0040 在 CI 超时。新建本地库复用真实短信单一持有者/进程中断夹具后，144.40 秒触发预期 `cannot downgrade 0041` 保留保护，head 保持 0163，正常停止。采样未见数据库锁阻塞，主要准备阶段数据库等待 Python 客户端；不能因此声称 CI 已通过或放宽 180 秒限制。性能剖析在继续。

### 当前候选与准确接续入口

`A=artifacts/authentication-fence-0164-next`，`E=artifacts/repository-safety-20261002`。主树已应用 0164 的 29 项变更（6 新增、23 修改），原字节保存在 `A/main-application-v2/preserved/`；同时保留已有路径、UUID 引导和缓存修复。提交前复验已全部结束，2336 文件无漂移，所有测试进程和私有 PG 正常退出。原始证据见 `A/main-application-v2/verified-v1.json`；最后交接说明更新仅涉及两个 Markdown 文件，另留文档差异及最终源码清单。

- `A/source` 是第一版“仅规范认证审计”候选，原生迁移/权限证明通过，但不足以解决授权发布锁问题，已被取代。其聚焦测试 55 通过、1 个旧版本断言失败；保留全部失败证据。
- **当前候选为 `A/source-v2`**，完整源码固定于 `A/broader-source-v1.json`（2335 文件）。仅对非库存/非需求审计跳过库存锁；三个报损逆向/审批/执行封存 aggregate 无论审计 stream 都仍受保护。0164 是前向迁移，核验精确前驱函数/权限/属主，保留全部业务行，更新当前版本准入及运行时冻结目录。
- 新回归直接强制执行被修改的 0161 延迟约束，避免其他旧守卫掩盖缺陷；覆盖库存/需求审计、跨 authentication/authorization 的三种封存事件、脱离事实的伪造封存拒绝、私有触发器不可被 API 直接执行、RC/RR 认证与授权审计独立提交。原实际发布竞争由共用 control helper 验证。
- 第二版聚焦 **56 项全部通过**，仓库安全 **2335 文件通过**。审计锁原生证明也已完成：RC/RR 登录各 1 组、授权各 1 组；8 组库存/封存锁保护、6 组脱离事实封存拒绝；精确前驱漂移拒绝、全启动/ACL、带数据往返保留全部通过，私有 PG 正常停止且 2335 文件摘要无漂移，见 `A/audit-domain-native-verified-v2.json`。`A/current.json` 保存原生迁移、控制数并发、数量/SN 报损纠正的当前会话句柄；其中数量/SN 两种模式 `restore_available` 均已完成真实原过账、反向冲销、独立审批、纠正提交、权限撤销回滚、精确回查及历史保留门禁，私有库正常停止，源码无漂移，见 `A/restore-both-verified-v2.json`；该进程已正常退出。control 整套共用业务检查已完成，包含发布竞争、对账、权限、真实 XLSX 导入、报表、请求恢复和历史保留；终态与正常停库见 `A/control-native-verified-v2.json`。三种纠正结果 × 数量/SN 六组也全部完成，见 `A/correction-matrix-verified-v2.json`。均固定于第二版源清单，不能替代合并后的主树或 GitHub 验证。
- `E/inventory-downgrade/run-az3wsdqs/diagnostic.json` 与 `activity.json` 保存回退诊断。纯迁移图剖析已完成：163 版产生 102755 次 runpy 执行（编译缓存已启用，复用 102652 次），耗时 125.58 秒。`E/migration-graph-profile.json`/`.txt` 保存结果。独立缓存优化候选仅跳过同一源码文件重复 zip 探测及重复祖先路径解析，保留每次源码 SHA 校验、独立模块变量和执行；性能对比实测 83.57 秒（原 125.58 秒，均有并行负载，不作性能 SLA）；编译/复用次数完全一致。已保留原字节后应用主树两个文件，主树 8 项回归通过；原生回退复验已在原 180 秒限制内完成（88.83 秒，预期 0041 保留保护、head 保持 0163）；2075 个非文档源文件无漂移、私有库正常停止，见 `E/cache-and-scratch-native-verified.json` 与 `E/cache-main-application.json`。这仍不是新 SHA 的 CI 结果。准确句柄见 `E/current.json`。不接生产库，不重放任何外部业务写入。

下一步：提交并推送本批修复，检查准确新 SHA 的 GitHub 门禁，然后继续正式基线缺口审计。主树已通过 83 项聚焦回归、2336 文件安全扫描、0164→0140→0164 原生往返、完整运行时 ACL、UUID 漂移拒绝及恢复和 9 项短信配置保护。候选六组报损纠正和完整控制数流程证据保持独立列示，不混称为生产验收。真实短信/微信、UAT、期初迁移、连续三天对账、性能与灾备，以及正式基线其他功能缺口仍分别待验收。下文是早期开发历史，以本节、Git 和当前机器回执为准。

## 最新接续（2026-10-02 03:39，主树0163本地复验完成）

当前工作树`${RSC_REPO_ROOT}`、分支`codex/notification-delivery-worker`，本批提交前基线HEAD为`9dff36f7feca44626b82ceb6e40297b3732a22f0`。**主树已整合为2074源/0163并通过本批本地复验。实际提交以Git和机器回执为准，尚未完成GitHub或生产验收，未部署。** 应用81项（39新增、42修改、无删除）；覆盖前逐文件核对2035源摘要，全部原字节保留于`artifacts/loss-return-compensation-next/main-application-v1/preserved`。应用时主树与已验证的最终候选源字节完全一致；随后提交检查发现新SQLite DDL行尾空格，当前`main-application-v1/source-v2.json`仅含DDL空白及相应冻结SHA修正、Python/TS两处EOF空行清理。原字节保留于`formatting-v2/preserved`，PostgreSQL目录及运行逻辑未变，SQLite全迁移/ORM/降级复验1项通过（92.51秒），源摘要无漂移且进程退出，汇总见`main-application-v1/verified-v2.json`。

C=`artifacts/loss-return-compensation-next`；本批以下路径相对C。原2073源固定在`seal-work/source`，最终2074源在`uuid-namespace-ci-work/source`；不要把旧副本当成当前主树。

- `legacy0162-work/native-quantity-v3-verified.json`和`native-serial-v3-verified.json`：真正0162旧代码创建数量/SN退回历史，再升0163；各227张既有业务表逐行不变，旧请求在停止前后均只读恢复，新停止真实提交，原历史保留，有停止历史时拒降且全部事实/head不变。两库正常停止、所有相关进程退出，源码无漂移。
- 旧历史v1错误使用API读取迁移表、v2错误把文本User.id转UUID，两次仅改测试夹具，失败与正常停库回执分别保留`v1-failure-reviewed.json`、`v2-failure-reviewed.json`，未扩大业务权限。
- `ci-stop-work/canonical-verified-v1.json`：共用正式helper全部7种分支均有PG16.15终态（seals quantity、http serial、negative quantity、seal_first quantity、execute_first serial、stop_first quantity、outbound_first serial），真实事务/锁竞争/单一结果及正常停库、退出和2073源摘要齐全。**这不是14腿全矩阵或GitHub已通过**；两跟踪方式的完整CI矩阵已注册，GitHub尚未执行。原数量/SN双模式底层证据另在`formal-boundary-work`及`migration-work`。
- `ci-stop-work/focused-verified-v1.json`：88项分派、准入、清理及既有矩阵回归通过。`uuid-namespace-ci-work/native-head-v1-verified.json`：2074源正式本地head入口通过，包含UUID成员移错schema时0159/0163/完整启动同时拒绝、还原后正常、最小权限与9项短信配置保护；不是实际短信发送。`source-equivalence.json`证明2073→2074仅增加回归helper和两个CI/本地入口挂接，运行时、迁移和前端完全未变。
- 可见停止组件、父页、确认/回查/封存流程已应用主树。先前`http-stop-work/component-full-verified-v1.json`的2236项和双构建、`component-verified-v1.json`的119聚焦及类型检查均对应准确候选源；浏览器桌面/390px验证使用合成数据。主树全前端复验也已通过，见下方当前证据。

`main-application-v1/verified-v1.json`收齐主树证据：**后端166项、全前端2236项、TypeScript及两种构建、实际PG16.15升级0163/完整启动/权限/UUID漂移拒绝和恢复/9项短信配置保护全部通过**；所有worker、child及私有PG均退出，源码摘要无漂移。不是全部后端测试、真实短信、14腿GitHub或生产验收。主树源固定已解除；生成物原字节也保留在`preserved-generated`。

接下来按累计0158–0163依赖闭合范围提交，再运行准确SHA的GitHub门禁；保留所有旧证据并继续正式基线缺口开发。准确SHA GitHub、实际身份/权限/短信微信、UAT、期初迁移、连续对账、500用户性能和灾备仍待各自验收。部分及下游退回实物逆向、旧分类纠正、报废/失而复得，以及遗留登录ID物理类型差异继续列为基线缺口；完整上线目标未完成。

发布操作与恢复边界见[0163发布说明](LOSS_RETURN_STOP_0163_RELEASE_RUNBOOK.md)。机器入口：`artifacts/loss-return-compensation-next/main-application-v1/current.json`。


更新：2026-10-02 03:39（北京时间）。完整上线目标仍 active；主树0163本地复验已完成，Git/CI发布状态见最新机器回执。

## 接续入口与边界

- 工作树：`${RSC_REPO_ROOT}`；分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 先完整阅读根目录 `docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md`。本轮已读；所有后续开发遵循其正式范围。
- 禁止 reset、revert 或丢弃未提交改动。既定证据全部收齐后才提交。不要将候选验证通过表述为主树、GitHub 或生产已通过。
- Python：`cloud_oam/.venv/bin/python`，不得 resolve 解释器软链接；pytest 从 cloud 目录用 `-o pythonpath=backend`。Node：`${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`。
- PG 16.15：`cloud_oam/artifacts/pg16-native-20260920/install/bin`。仅用任务创建、TCP 关闭的私有 Unix socket 测试库；不连接生产数据库或执行外部业务写入。
- 公开首页知识查询，星星按钮到 `/xx`。小程序保持公开知识查询；飞书知识源优先级低。短信、微信、真实登录和生产验收分别核验。

以下路径均相对 `cloud_oam`：P=`artifacts/loss-correction-request-seals-next`；R=`P/return-history-next`；D=`artifacts/damaged-return-inbound-next`。

## 当前代码位置

| 位置 | 当前内容 | 是否已进入主树 |
| --- | --- | --- |
| 主工作树 | **2035源，0162**；原2034源验证后，追加退回补偿预检关联修复及测试 | **是，未提交；新修复24项回归通过** |
| `D/source` | 上一批2034源，`migration-source-v7.json`；已完成主树复验的固定版本 | 已应用，后续主树变更另行验证 |
| `D/history-quality-work-v1` / `D/history-http-work-v2` | 只读投影和HTTP的开发及验证来源，保留旧证据 | 已应用候选和主树 |
| `D/history-ui-work-v1` / `D/history-ui-source-v1` | H5开发与独立验证副本，当前UI清单为`history-ui-source-v2.json` | 已应用候选和主树 |

**前向破损入库缺陷已在主树修复，尚未发布。** 0161错误入`new/available`的复现保留在`R/damage-probe/verified-defect-v1.json`。旧库存分类没有被迁移静默改写；异常可只读识别，授权逆向纠正仍未实现。

`D/main-application-v1/application.json`记录73项应用（26新增、47修改、无删除）；全部原字节在其`preserved/`目录。`source.json`证明应用时主树2034源等于已验证的整合候选；后续四文件补偿预检改动另见下方新一批记录。应用前原2008源完全无漂移；没有覆盖其他任务改动。这批主树复验已全部结束，2034源无漂移，测试读者和PG均已退出；`D/main-application-v1/verified-v1.json`保存终态核验。后续改动须另建源清单，不覆盖本批证据。

## 已应用的修复

- 入库预览 2.0 按原验收接受量拆为原成色与坏件份额；破损是接受量子集，不重复相加；SN 逐件归类。原本坏件仍只入一份坏件。
- 原子多账户过账保留组织、责任人、位置、SKU 和批次，验收、入库、通知各自独立。历史 1.0 请求保留原始结果，响应丢失后只读回查。
- 0162 前向迁移冻结函数目录、约束及最小权限；禁止新增 1.0 入库，直接 SQL 错分账被拒绝；有新历史时拒绝降级。旧历史不会被静默改成新分类。
- H5 和小程序验证原验收、份额、成色及 SN；小程序先读取原验收再核对预览。
- 正式 CI 矩阵已在主树注册 `return_quality_whole` / `return_quality_mixed` × quantity / serial，共用本地和 CI 业务 helper。**尚未在 GitHub 执行**。

## 已收齐的验证证据

| 范围 | 当前证据 | 能证明什么 |
| --- | --- | --- |
| 整合候选全量前端及相关后端 | `D/integrated-verified-v7.json` | **前端2161项、后端55项通过**；该证据固定于2034源；主树后续2035源的补偿预检变更另行验证。UI类型及双构建通过，后台JS包较大，正式性能验收仍未完成 |
| 主树小程序 | `D/main-application-v1/mini-verified.json` | **1083项全过、0失败**，已在真实Git工作树验证，旧候选Git边界错误消除 |
| 四组正式 helper 原生门禁 | `D/formal-quality-verified-v6.json`，各目录 `verified.json` | **全部通过**：数量/SN × 整批/混合破损；真实 PG16.15 API 角色提交、并发单一赢家、原请求恢复、SQL 伪造拒绝、0162 迁移/权限/空库往返/历史保留；终态与 checks 一致、源无漂移、正常停库、进程退出 |
| CI 路由/参数/多包裹服务 | `D/ci-quality-focused-verified-v6.json` | 67 项通过；不是 GitHub 运行 |
| 只读历史分类异常服务 | `D/history-quality-service-verified-v1.json` | 6 项通过；数量整批/部分和 SN、旧分类异常与正确新版分开；原事实不变 |
| 总部历史 HTTP 接口 | `D/history-http-verified-v2.json` | **6 项通过**，128.65 秒；真实数量/SN旧版和新版业务、部分破损、原事实不变、准确旧异常、私有无缓存、主体过期412与总部越权403分开；404、无原始请求泄漏；40443/40447退出。现已应用主树，只读API不是补偿写入口 |
| 原历史查询回归叠加新投影 | `D/history-quality-regression-verified-v2.json` | **29 项通过**，617.96 秒；原权限、完整证据、快照、游标和阶段分离保留；35297/35300 退出 |
| 当前 head/ORM/往返/扩展业务 | `D/migration-verified-v5.json` | 4 项通过；全迁移链、全部表与 ORM、混合 SN、分批验收 |
| 前端及小程序 | `D/client-verified-v5.json`、`D/mini-next/verified-v2.json` | 前端 2134 项、类型和两种构建通过；小程序全套首次 1082 通过/1 个 Git 边界失败，独立 Git 夹具 65 项通过覆盖该失败；另 37 项入库聚焦通过。重叠数量不可相加 |
| 多包裹服务 | `D/multiparcel-service-verified-v3.json` | 数量/SN × 两种入库顺序共 4 项通过；后来入账后原请求仍准确恢复 |
| 真旧代码升级历史兼容 | `D/native-legacy-verified-v1.json` | 0161 旧应用实际写旧历史，0162 升级保持全部业务行、原回查/重试；旧历史可降旧版恢复再升级。此版本未含新异常投影 |
| 较早独立原生场景 | `D/native-normal-quantity-verified-v3.json`、`D/native-quality-verified-v1.json`、`D/native-mixed-sn-verified-v2.json` | 正常入库、破损数量/整批 SN、混合 SN，分别固定对应较早源清单；不混称最新完整源码 |

主树先前证据仍有效于其固定版本：`P/main-application-v1/verified-v2.json`（1999源前端2132及后端65等）、`P/ci-next/main-application-v1/focused-verified-v1.json`（2005源144项）、`R/focused-verified-v1.json`和`R/native-verified-v1.json`（2008源只读图服务29及原生数量/SN）。`P/ci-next/native-seals-v1-verified.json`、`native-http_sources-v1-verified.json`、`native-multigeneration-v2-verified.json`分别记录封存/HTTP/多代门禁；不要把不同版本或重叠用例相加为全量上线验收。

## 当前主树复验与已结束任务

| D 下目录 | 状态 | worker / child PID | 子进程存活 |
| --- | --- | --- | --- |
| `main-application-v1/backend` | passed（8项） | 32468 / 32473 | 否 |
| `main-application-v1/mini` | passed | 32469 / 32474 | 否 |
| `main-application-v1/native-head` | passed | 32470 / 32475 | 否 |

上述终态已用实时ps、退出码、2034源摘要和正常停库日志核对；没有活跃读者，无须重跑。`state.json`、`checks.log`、`worker.log`和`job.json`保留各目录；观察超时不代表执行终止。

此前三组原生已全部结束并核验：

- `D/native-ordinary-quality-verified-v2.json`：数量、SN、批次、批次+SN原坏件入库；分批复用精确账户且不重复转坏；多行同账户、重算流水；4种当前权限拒绝、15种畸形关联拒绝、不同收货并发及显式新预览；迁移、权限、历史拒降、正常停库与进程退出。
- `D/native-legacy-quality-verified-v2.json`：数量/SN真0161旧代码生成历史，0162升级后的总部API角色READ ONLY异常识别、准确数量/SN、接收人总部权限拒绝；全部业务行不变。降级后旧代码恢复、再升级再验证，正常停库、摘要及进程退出均齐全。旧`R/source`和开发harness已不被活进程固定。
- H5聚焦111项、父页接入9项（重叠不得相加）和类型/双构建通过，见`history-ui-verified-v1.json`、`history-ui-parent-verified-v2.json`、`history-ui-build-verified-v2.json`；之后整合前端全套2161通过。

总部正式只读入口：`GET /api/v1/stock-operations/loss-reports/corrections/return-history/{root_disposition_id}`。H5“报损纠正”中的原退回处置提供显式“查询退回历史”；六个互斥履约阶段、短少观测、破损子集和旧分类异常分别展示。身份变更后丢弃迟到响应，刷新失败不保留旧结果，不自动重试或写库存。

## 最近失败及处理

- 新 HTTP v1 测试的身份切换夹具仍保留上一总部主体，先返回 `actor_principal_stale` 412；测试原本只预期权限 403。v2 保留准确 412 断言，再切换夹具当前主体，独立要求总部越权 403；应用权限未放宽。原日志与 `D/history-http-check-v1/failure-reviewed.json` 保留，39906/39909 已退出。
- 普通退回 v1 预期两份入库，但正式工单拆回的来源已是坏件，正确结果只有一份；v2 验证原坏件、分批总量、批次/SN和账户复用。旧失败正常停库回执保留。
- 更早失败包括解释器 resolve 丢 venv、SQL CASE 括号、迁移前驱/封存表清单遗漏、测试凭证复用和候选 Git 边界。均保留原始失败及修正证据，不重写为通过。详细历史见下方归档。

## 新一批退回补偿开发（0162之后，尚未开放补偿写入）

`artifacts/loss-return-compensation-next/preflight-v1/reproduction.json`记录已复现的预检缺陷：只按下游单头查依赖会漏掉反向关联明细。修复在`reversal_stock.py`复用`return_history._verified_graph`，核验原退回完整图及复读摘要，再把子单明细、数量/SN和证据摘要绑定预检。权限仍为当前总部`reverse_loss`，没有借用或扩展读权限；真正补偿过账仍被阻止。

新增反例覆盖数量/SN畸形关联、真实整批/部分出库不得直接补偿、读取间子单变化；原四种预检与只读历史HTTP一起回归。`source-v1.json`的2035源已完成`focused-v1`验证：24项通过、162.65秒，源摘要无漂移，worker38593及child38594均退出。`verified-v1.json`记录证据，源固定已解除。源清单与上一批2034证据分开，不能把上一批通过推广为新补偿已完成。

## 历史退回补偿候选（2026-10-02 02:17）

C=`artifacts/loss-return-compensation-next`，W=`C/seal-work`，M=`C/migration-work`。主工作树仍为0162及2035源，未开放补偿写入。旧2039源在`C/source`保留；当前整合候选为`W/source`，正式0163候选清单为`M/source-v3.json`（2047源）。

- `C/stop-verified-v3.json`：2038源46项服务回归；`C/stop-verified-v4.json`：2039源30项停止/预检回归。重叠用例不可相加。
- `C/native-stop-quantity-verified-v4.json`与`C/native-stop-serial-verified-v1.json`：数量/SN实际API角色停止/冲销、请求绑定、新旧请求只读恢复、不可变保护、私有函数拒绝、四种畸形提交整笔回滚通过。
- `C/native-stop-quantity-stop-race-verified-v1.json`、`C/native-stop-quantity-outbound-race-verified-v1.json`及上述SN证据：两个真实API连接、观察到数据库阻塞、停止先赢/出库先赢、数量部分出库及SN单件边界、单一提交和准确库存。全部测试库正常关闭，进程退出，2039源无漂移。
- `W/verified-v1.json`：独立2040源永久封存服务32项通过；`W/native-verified-v1.json`：数量/SN封存提交不改库存、三种旧请求坐标拒绝、显式新请求可执行、旧封存保留、原请求恢复及撤权读写分离通过。该组当时只在0162后手动装候选SQL，不能替代正式0163迁移验证。
- 当前2047源已接入正式Alembic0163、冻结目录（11个函数、10个触发器）、停止模型注册、权限和运行时校验、当前head引用；历史迁移没有改写。SQL仅对准确报损退回且有停止证明的冲销开放，通用退回/报废限制保留。
- `M/native-schema-v1`已实际升级0163、空降0140并重升0163，随后在启动校验导入时因本轮新增调用缩进错误失败，正常停库。`focused-v1`缺测试导入路径；`focused-v2`同一缩进错误。原失败完整保留。当前v2源码已修复缩进、补ORM表清单、使用冻结SQLite规范DDL；Python3.12全源解析和运行时注册导入通过。
- `M/focused-verified-v3.json`：2047源v2的53项通过（477.85秒），包括head链、当前哈希链接、ORM完整结构及空库往返、0163离线拒绝、停止/封存和旧通用封存回归；源无漂移、58874/58880退出。不能将它当作PG启动通过。
- `M/native-schema-v2`空库升级/降级/重升后在完整启动校验失败；`native-business-v1`两探针在业务前失败。原因是新运行校验对私有UUID schema用了`to_regprocedure`，API没有该schema USAGE。已改为与既有0159运行校验一致的系统目录OID/名称/参数类型核验，没有新增权限；仅运行校验文件变化，见`M/repair-v3.json`。
- 队列后续同类步骤已停止排队；当时活跃child59500未被中断，已自行失败清理并退出。`M/integration-failures-reviewed-v1.json`核对各失败日志、正常停库及进程退出；不要把取消后的worker state里残留的running当活跃进程。
- **正式0163门禁已通过**：`M/native-schema-v3-verified.json`，真实PG16.15升级0163、空降0140再升级；完整API启动、边缘部署/权限、11种新目录漂移拒绝/回滚恢复、5个私有函数无API EXECUTE、停止表仅SELECT/INSERT、9项短信配置保护通过。短信配置保护不等于真实短信发送。
- **数量正式业务已通过**：`M/native-business-v2-verified.json`，真实0163迁移后API封存提交不改库存、三种旧坐标拒绝、晚到身份变化整笔回滚、独立新请求完成停止/冲销、旧封存和原请求恢复、撤权只读恢复与写拒绝。只封存时和已冲销时均拒降，所有事实/版本/目录不变。两库正常停止、进程退出、固定源无漂移，不能视为生产验收。
- **当前正在验证（2026-10-02 02:31实查）**：`M/native-serial-seals-v1` worker63568；`M/native-quantity-races-v1` worker63569；`M/native-serial-races-v1` worker63570。后两者各顺序测试封存先赢、执行先赢；所有步骤都用真实0163及两个API连接，要求实际锁等待及准确只读恢复。
- 任务固定`M/source-v3.json`的2047源及job.json中的3个`formal_*.py`。退出前不修改固定文件；已通过的schema/quantity无需无故重跑。先收上述5个场景终态，再处理剩余问题。
- 尚未完成：SN和封存/执行竞争的正式终态、0163组合的出库竞争/直接SQL反例回归、历史0162来源兼容证据、HTTP/停止状态UI、主树应用及全依赖复验、GitHub及生产验收。部分已出库/已入库的逆向实物流转、旧成色纠正、报废/失而复得等基线缺口仍保留。运行校验下一轮还应增加UUID扩展成员移出私有schema的反例，核对成员自身namespace是否被准确限制；不能靠授予API USAGE解决。

## 接下来按顺序执行

1. 主树复验已全部收齐：HTTP/head 8项、小程序1083项、PG16.15升级0162及空库降至0140再升级、权限漂移拒绝/恢复和9项短信配置保护。仅本地合成验证，没有真实短信发送，未运行完整生产验收；证据`D/main-application-v1/verified-v1.json`。
2. 0162前向入库修复、只读异常接口和H5已应用主树，无需再次应用73项文件。既定证据全部齐全后再处理提交；GitHub精确SHA门禁和生产验收仍分别待办。
3. 按 [退回补偿设计](LOSS_RETURN_COMPENSATION_DESIGN_20261001.md)实现未出库补偿及正常履约并发隔离，继而逐阶段逆向物流；不能调用普通整单取消替代报损派生退回。部分出库、已发运、拒收/短少、已入库需各自实物证据和逆向库存事实。
4. 旧成色异常已经可查询，授权历史分类纠正仍缺；不能把当前余额或同SKU其他SN当成原物资仍可回收的证明。
5. 报废原处置/纠正报废、SN生命周期和失而复得仍缺，保留明确不可用入口直到事实/迁移/权限/恢复完整。
6. 完整正式上线还需真实短信/微信/通知/附件、身份角色、全量迁移及期初、准确SHA GitHub CI、多角色UAT、连续至少3天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚及真实业务冲销演练。局部合成门禁不能替代这些条件。

## 历史与机器入口

本次精简前全文保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_192400.md)，SHA256 `f3935512439197f64308e2b63dacbf0d82038a50d037d64c664907fca174e7f2`；其中包含更早归档链和各批修正经过。

当前机器入口：`D/status.json`、`P/status.json`、`artifacts/loss-formal-application-next/continuation.json`。引用旧版 source 清单时必须保留其版本边界，不将历史 PID 当当前进程。

HTTP终态更新前交接保留于 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_192435.md)，SHA256 `29f43853ed7f1fe8a03e444ae7f5f9f5d1655f06453bbfe5f3229d85d7be4e45`。

本次主树整合更新前全文：[历史交接](history/CONTINUE_DEVELOPMENT_20261002_010112.md)，SHA256 `c4064cc405f47a59d9150916dd79d892a6b0b70adf6c5f29a722b1509afc1870`。

本次终态更新前全文：[历史交接](history/CONTINUE_DEVELOPMENT_20261002_010721.md)，SHA256 `d2f3d1ff38180f398b31b1ddfd1c711dc050af66894555913df65bbb7a127013`。

本轮0163接入更新前全文：[历史交接](history/CONTINUE_DEVELOPMENT_20261002_021730.md)，SHA256 `c66358df53f079cddd168d2da896cd1378166a6a0c5470c17a45c63b5a76b40e`。

本轮UUID权限查询修复前交接：[历史交接](history/CONTINUE_DEVELOPMENT_20261002_022611.md)，SHA256 `0c437e568cea982bc14e0ebc576a63a5b6754c58acec87cd4b60ebb5ed813628`。

本轮更新前全文：[CONTINUE_DEVELOPMENT_20261002_025130.md](history/CONTINUE_DEVELOPMENT_20261002_025130.md)，SHA256 `b1676abc0203c0bfc576b4c3a999576bce0186c2f2d06520ebc6f95f256bfe7c`。
