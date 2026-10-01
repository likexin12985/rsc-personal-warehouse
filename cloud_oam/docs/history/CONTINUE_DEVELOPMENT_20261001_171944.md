# RSC 个人仓开发交接

更新：2026-10-01 17:02:29 +0800。完整上线目标保持活动，本轮为 progress。已把正式0161、纠正HTTP/来源及H5页面共57目标应用主树，1999源逐字节匹配候选；原28份文件已备份。主树复验仍运行，未提交、推送或部署。

## 当前接续入口（主树1999源）

- 应用：`artifacts/loss-correction-request-seals-next/main-application-v1/application.json`、`backups/`及`source.json`。28修改、29新增、无删除；主树测试结束前保持1999源固定。
- 主树前端worker87722、原生当前head worker87724；后端v1因旧测试选择器名称而未收集用例，已留失败回执，正确名称重跑v2 worker88152。各步骤以实际state和进程为准，不能按PID历史判断仍运行。
- H5候选证据：`h5-next/page-verified-v1.json`。v4全量2132项及双构建通过，v5仅两个测试导入调整后13项与完整类型检查通过；非声称v5重跑过2132项。移动390px无横向溢出，明确选审批、预览、确认前禁用、勾选可用及取消均验证。公开首页无登录表单，入口/xx；知识资料仍pending。`visual/verified-v1.json`记录浏览器清理和本地预览服务停止，未执行业务提交。
- 后端运行时代码逐字节等同已通过数量/SN原生来源HTTP候选，正式迁移、权限、带旧历史升级及请求恢复证据保留原路径；这些是本地合成身份验收，不代替真实JWT/UAT。
- 新发现的发布缺口：旧`pg16_loss_multigeneration_gate`、`pg16_loss_correction_gate`和部分报损门禁仍断言0160；纠正历史降级需按0161实际保留规则检查；新增纠正封存与HTTP来源场景尚未加入GitHub矩阵。下一步在隔离候选补齐门禁，不能修改正在测试的主树，不能把当前本地passed当准确SHA CI通过。
- 剩余产品范围仍包括退回下游补偿、实际报废/失而复得、正式基线其他领域及完整生产验收。源码整合不是上线完成。

## 2026-10-01 16:44 当前接续入口

本节为最新状态；下文带旧时刻的条目保留历史经过，不能据此判断任务仍在运行。

1. 主树回归shard0已核验终态：2899通过、1跳过、15子用例通过，`artifacts/loss-formal-application-next/static-shard-0-verified-v1.json`。shard1为2983通过/2跳过；shard2为2981通过/2失败。原1969源整体不能宣称全绿。
2. 已在核验所有旧worker退出及1969源无漂移后，把离线迁移修复4目标应用主树。`artifacts/migration-offline-boundary-next/main-application-v1/application.json`、`backups/`、`source.json`保存应用证据和原字节。主树1970源18项复验全部通过，证据同目录 `verified-v1.json`；worker81702/child81705均退出，全部源与已验证候选逐字节相同。主树当前已无测试固定限制，可按备份及逐文件前置哈希规则继续集成。不能将本次聚焦复验说成重新跑过全部三组。
3. 数量/SN带旧0160历史升级全部通过：`artifacts/loss-correction-request-seals-next/populated-upgrade-{quantity,serial}-verified-v1.json`。各三轮纠正、十个历史请求保留，旧公共表数据不变，新表为空，新列为空，拒绝历史降级且数据/目录不变、完整启动权限、正常停库。两个worker均退出。
4. 纠正来源GET原生数量/SN全部通过：同目录 `sources-next/native-{quantity,serial}-verified-v1.json`，1985源，各10次来源检查、28次数据库READ ONLY请求、3次真实提交后响应丢失找回；6次真实提交，当前读写权限分别撤销，查询引用用于后续真实预览和显式执行。测试库和worker均正常退出；模拟登录身份不代表生产JWT。
5. 前端纠正契约、接口适配及原请求持久化/恢复候选：`h5-next/source-v3.json`（1995源），`h5-next/contracts-recovery-verified-v3.json`：71测试通过、TypeScript通过。后端数量/SN实际SQLite服务生成响应样本，并按公共响应模型验证；这两项样本生成不能代替原生键绑定门禁。覆盖完整命令摘要匹配、明确选择审批、原处置锁、先保存后唯一写、断联/查无保留、独立撤权、显式封存、准确清理及原报损物料对应。
6. 下一步：完成 `FormalLossCorrection` 页面及总部受限路由/原处置入口，使用新适配器与恢复模块，不重新设计自动重试；增加组件交互与路由验证。正式0161/HTTP/来源后端候选和H5新模块尚未应用主树，主树复验已结束，须检查逐文件差异、备份后应用，再按受影响范围验证。退回补偿、实际报废及其他正式基线缺口仍未完成。

## 2026-10-01 16:29 新增权威结果

- 正式0161迁移数量/SN：`artifacts/loss-correction-request-seals-next/formal-native-quantity-verified-v2.json`、`formal-native-serial-verified-v1.json`。1978源及辅助脚本哈希复核一致，真实PG16.15、空库降级再升级、完整启动权限、批准/执行封存并发与迟到写拒绝、有历史时拒绝降级且精确快照不变，测试库及worker均正常退出。
- 纠正HTTP数量/SN：同目录 `http-next/native-{quantity,serial}-verified-v1.json`。1981源候选、每组6次真实提交、3次提交后响应丢失、18次只读找回；封存后的迟到写拒绝，当前读写权限分别校验。模拟登录身份，不能代替生产JWT或用户验收。
- 携带旧0160历史升级：数量终态通过：`populated-upgrade-quantity-verified-v1.json`，三轮旧纠正、升级后十条原请求只读恢复、全部旧公共表行不变、新封存表为空、历史绑定拒绝降级及目录/数据不变、启动权限与正常停库均核验；worker71819已退出。SN worker76951仍在运行。目录 `populated-upgrade-{quantity,serial}-v1`，继续按state+实际进程判定。
- 只读纠正来源：`sources-next/source-v2.json`（1985源）新增GET `/api/v1/stock-operations/loss-reports/corrections/sources/{root_disposition_id}`。完整历史证明、当前总部读权限、准确执行/冲销/决定引用；独立审批作为全部选项返回，不自动选择，不把查询当写授权。`focused-v1`因测试引用不存在的原处置reason字段而7通过/1失败，原始记录保留；改为篡改真实request_hash后的v2已14项全部通过，1985源复核、worker76862已退出；证据 `sources-next/focused-verified-v2.json`。涵盖数量/SN、全部五种独立批准选项、准确纠正后引用、撤权、篡改、游标变化、注册路由隐私。原生数据库与真实登录仍单独验收。
- 原生只读来源验证已启动：`sources-next/native-{quantity,serial}-v1`，worker77796/77797；固定1985源与4辅助脚本，真实API角色GET使用READ ONLY事务，查询引用用于后续显式预览/命令，当前读写分别撤销，响应隐私和前后事实比较。运行脚本：`sources-next/{native_sources,run_native_sources}.py`。不得修改任何仍被在跑任务读取的候选源/辅助脚本。

## 工作位置与约束

- 工作树：`/Users/lizhiwang/.codex/worktrees/06f6/oam`；代码目录：`cloud_oam`。
- 分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。本批未提交、推送、部署。
- 继续前完整阅读根目录 [正式需求基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)，检查当前 diff。禁止 reset、revert 或丢弃未提交改动。
- 主树已应用正式0160多代集成包（58目标），另应用15目标：三个旧断言测试文件、审计批读及测试、六个处置HTTP接口及来源接口、新原生写HTTP辅助门禁。备份和回执：`artifacts/loss-multigeneration-main-integration-next/application.json`、`artifacts/loss-execution-command-http-next/main-application-v1/application.json`。
- 首页保持公开知识查询、无登录界面，星星管理入口到 `https://rscwz.cn/xx`。飞书知识源暂缓；后续按 NIO Chat CLI 路由。
- 本轮只操作本地代码和可销毁测试库，无生产/外部业务写入。不能用本地通过替代上线验收。

## 已完成及证据边界

| 范围 | 当前证据 | 限制 |
| --- | --- | --- |
| 报损退回收货、独立入库、请求恢复 | 历史数量/SN PG16回归通过，`artifacts/loss-formal-application-next/receipt-{quantity,serial}-verified-v2.json` | 旧1901源快照；不得冒充本轮全量发布 |
| 原处置经历一轮纠正后的恢复 | 主树已集成，34项聚焦及1918源数量/SN PG16通过，`artifacts/loss-recovery-main-integration-next/main-*-verified-v1.json` | 后续代码需按实际受影响范围重验 |
| 原处置/衍生退回只读HTTP | 主树已注册2个POST request-lookup；52项新用例+34项既有回归共86通过，`artifacts/loss-execution-recovery-http-next/verified-v1.json` | 当前读权限独立于写权限；返回原历史事实；无执行/封存回退。认证依赖为测试替代 |
| 后继历史HTTP | 同目录 `history-verified-v1.json`：6项数量/SN×三种纠正，84次路由请求通过；1306源核验 | 覆盖冲销/批准/纠正、证据损坏、游标变化、撤权；非原生HTTP/JWT验收 |
| 正式0160多代规范服务候选 | 43项恢复/版本头、6项多代/防伪/封存、81项路由/启动检查通过；早期25文件候选另210项通过 | 不把不同快照合并为同一次回归 |
| 正式0160原生generations | `artifacts/loss-multigeneration-release-next/native-{quantity,serial}-generations-verified-v2.json`：三轮、九请求、封存、双API并发单赢家、权限回滚、迁移历史保留均通过，测试库正常停止 | 1305外层/1304内层源；内层仅省略Alembic README。数量/SN的seal_retention也已终态通过；四场景证据齐全 |
| 审计批读优化 | `artifacts/audit-chain-batched-read-next/fixture-v2-verified.json`：34通过；`native-verified-v1.json`：15次原生新旧结果一致，SELECT 261→4，三类审计改写均拒绝 | 已应用主树；真实报损组合数量/SN各9条新旧回查一致且只读，测试库已停止，`business-{quantity,serial}-verified-v1.json`；当前组合回归运行中，非500用户验收 |

两个HTTP入口为 `/api/v1/stock-operations/loss-reports/dispositions/request-lookup` 和 `/api/v1/stock-operations/loss-reports/derived-returns/request-lookup`。都要求完整原请求，`retry_permitted=false`、`result_scope=original_command`，found/not_found/sealed相互区分，响应不泄露原始键或完整命令。

## 当前验证及在跑任务

上一批1937源主树：基础检查90通过、恢复HTTP92通过；原生退回数量/SN两组通过，均核验真实API角色、READ ONLY事务、200/409/403、迁移回环、历史保留及正常停库。证据：`artifacts/loss-multigeneration-main-integration-next/{runtime,http}-verified-v1.json`、`native-{quantity,serial}-return-verified-v1.json`。

历史原生处置失败源于旧0150断言，实际数据库返回0159约束、SQLSTATE=23514。原始失败保留，修正后的数量/SN恢复和四组HTTP均已通过，准确证据见下方“当前权威结果”；该历史失败不再作为当前阻塞。

新接口候选证据：`artifacts/loss-execution-command-http-next/verified-v1.json` 44通过；`sources-verified-v1.json` 28通过。已应用主树：六个预览/执行/请求封存接口，以及 `GET /api/v1/stock-operations/loss-reports/execution-sources/{operation_id}`。来源接口返回准确总部决定引用、明确标注的原处置事实、真实且经当前保管责任核验的退回路线；未核验路线返回unavailable，不能猜UUID。

### 当前权威结果

- `main-focused-verified-v1.json`：主树1947源的202项组合终态通过，源码复核无漂移。
- 原v1三个worker均结束。四个HTTP步骤的失败原因都是preview被辅助门禁误设为READ ONLY；两个disposition-recovery步骤在之后发现另一条0150旧断言。实际数据库拒绝通用冲销并返回 `0159 exact dedicated disposition inverse required`、SQLSTATE23514。六次失败的源码、日志及正常停库记录保留在 `artifacts/loss-execution-command-http-next/main-application-v2/preceding-failures.json`。
- 已应用6个测试/CI目标：preview测试保留SELECT-only/无COMMIT/无ORM写入/事实不变，GET与恢复仍数据库READ ONLY；专用冲销旧断言改为精确0159文字；4个CI草案应用。未修改生产约束或服务。原字节备份与逐项应用回执在同目录 `application.json`。
- 同目录 `quantity-http-disposition-verified.json`、`quantity-http-return-verified.json`、`serial-http-disposition-verified.json`、`serial-http-return-verified.json`：**四组全通过**；1948内外源清单一致；原生PG16.15、真实star_oam_api、启动权限前后检查、期初锁下SELECT预览、真实COMMIT后模拟断联503→原请求找回、永久封存拒绝迟到写、撤销write仍可read、撤销read拒绝，全部正常停库。模拟登录身份，不能当生产JWT验收。
- `ci-topology-dispatch-verified.json`：59项本地CI矩阵/分派通过，包含数量/SN×处置/退回两个新增矩阵。**未运行GitHub Actions**。

两组完整处置恢复也已终态通过：同目录 `quantity-disposition-recovery-verified.json`、`serial-disposition-recovery-verified.json`。每组覆盖三种处置、3次只读预览、3次幂等重放、通用冲销拒绝、数量/SN与共享冻结保护、并发同请求单笔记账、过期权限/保管关系整笔回滚、通知去重、当前write撤销后的原请求read；数量30次/SN32次畸形提交全回滚。迁移空往返、保留处置/保管历史时拒绝降级、前后启动ACL通过，PG16.15正常停库，1948内外源一致。worker18941/18942/18943全部结束；不能再把它们当在跑任务。

**历史1969源全量静态发布回归（现已终态，见顶部）**：`artifacts/loss-formal-application-next/static-release-current.json`，主源固定1969文件，三组覆盖443个测试模块：

| shard | worker / child | 目录 |
| --- | --- | --- |
| 0（159模块） | 26868 / 26873 | `artifacts/durable-development-gates/static-release-1969-0-v1` |
| 1（135模块，已终态通过） | 原26869 / 26875，均已退出 | `artifacts/durable-development-gates/static-release-1969-1-v1` |
| 2（149模块） | 26870 / 26874 | `artifacts/durable-development-gates/static-release-1969-2-v1` |

每组使用既有 `scripts/run_static_shard.py --index N --count 3`，覆盖后端/边缘同步，原生PG运行模块由独立已述门禁负责。三组现已全部终态；原1969源校验记录已保留。当前主树已修复至1970源，其在跑复验及固定范围见顶部。准确child以后以state和ps为准；`artifacts/static-safety/shard-*-*/progress.jsonl`提供当前用例进展，不将点号或单项PASS当整组通过。

### 本轮静态失败及已验证修复候选

16:10更新：组2也已终态结束，2981通过/2失败；仍仅下述两项已定位的旧离线迁移失败，没有新增失败。worker/child/pytest已退出，1969源无漂移；完整回执 `static-shard-2-reviewed-v1.json`。当前只剩组0 worker26868实际运行，继续保留主源固定。

2026-10-01 16:05复核：组1已通过，2983 passed、2 skipped、4 warnings，实际5828.77秒；worker/child/pytest均退出、1969源摘要一致。回执为 `artifacts/loss-formal-application-next/static-shard-1-verified-v1.json`。组0/2仍实际运行，主源继续固定；不能把组1终态当全量门禁通过。此前“三组在跑”是启动时状态。

主树组2记录两项失败：`test_0041_postgresql_offline_sql_covers_preflight_truncate_acl_and_pg_guards`、`test_postgresql_offline_sql_preserves_type_boundary`。两者把旧离线SQL检查一路运行到需要真实角色/目录查询的0159，出现 `NoneType.one`；生产env.py原本即禁止离线迁移。收窄到完整旧版范围后，第二项还要求0158的SQL包含0159新增的 `rsc_register_loss_request_binding_0159`，已单独定位；原日志与生成SQL全部保留。

**修复候选已验证，尚未应用主树。** `artifacts/migration-offline-boundary-next/verified-v3.json` 为当前权威回执，实际候选在 `source-v3`，4目标/1970源：0159与0160入口明确拒绝离线升降级；短信历史SQL限定到0041；完整旧SQL仍检查至0158；0159新增登记函数改由摘要固定的安装定义校验声明、参数、正文和运行时摘要，未删除任何函数摘要断言。新增真实Alembic离线拒绝及SQLite在线往返/冻结写保护测试。

- v3聚焦18通过，183.97秒，worker37227/child37228终态退出0，1970源复核无漂移。证据 `validation-v3/state.json`、`focused.log`、`candidate-source-v3.json`。
- 原生PG16.15通过并正常停库：`native-v2-verified.json`。真实star_oam_api权限/启动检查、在线升级0160、空库降至0140再重升、边缘权限反例、9个短信数据库场景及报表/期初权限检查；不代表真实运营商发送或生产验收。worker32263的native步骤退出0，child35623与数据库35638均已结束，完整cluster/checks已核验。worker总状态仍failed，因为保留了此前v2聚焦17通过/1失败，不得把总状态改成passed。
- 原生使用v2候选，v3仅改了一个测试文件；所有运行时、迁移和其他文件逐一相同，`v2-v3-runtime-equality.json`明确差异。不能宣称两轮完整源码完全相同。此前11项通过、v2失败及诊断命令路径错误也保留原证据。

主树1969源仍逐文件与三个静态回归起点一致；当前仅三个原静态worker26868/26869/26870继续运行。后续先收这三组真实终态和全部失败，再按 `candidate-source-v3.json` 的before摘要备份应用4目标。不得直接覆盖仍被测试使用的源码。应用后进行当前主树聚焦及受影响发布门禁，全部既定证据齐全才提交；GitHub/生产验收仍独立。

### 纠正批准/执行独立封存候选（未应用主树）

16:18更新：真实HTTP数量门禁已终态通过，`http-next/native-quantity-verified-v1.json`。1981源无漂移、worker70123/child70129和PG进程已退出；PG16.15检查passed、正常停库。实际注册11路由中的冲销/批准/执行各完成独立封存、迟到请求409、真实提交后断联503和完整原请求找回；共6次提交（含3次封存）、3次提交后断联、18次数据库READ ONLY查询，写权限与读权限分别拒绝。真实0161升级、前后完整启动权限、有历史拒绝降级及全表/函数/触发器快照不变均通过。登录主体和业务角色授权仍为合成夹具，非真实登录/UAT。相同源码SN门禁 `http-next/native-serial-v1` 已启动，worker73397。

16:17更新：版本引用修正后的61项聚焦检查已终态通过（200.51秒、1978源无漂移），回执 `formal-head-focused-verified-v3.json`。现有数量/SN原生worker69249/69250仍运行。另启动 `populated-upgrade-quantity-v1` worker71819：在独立测试库用固定的0160旧应用生成三轮真实纠正及逆向请求封存，再由0161迁移升级；计划逐行核对全部旧表数据、10份完整原请求只读回查、新列为空及已有绑定历史拒绝降级。该验证尚无终态。

真实HTTP数量门禁worker70123已实际完成三类HTTP提交后断联503、完整原请求READ ONLY恢复、独立权限拒绝和永久封存；还在执行保留历史拒绝降级及终态停库，不能提前写为passed。11个候选接口仍不足以交付整个纠正界面：还需要经完整历史证明的只读来源引用和客户端交互，不能让用户猜UUID或把原posted当当前库存。

16:10更新：完整应用HTTP候选57项已通过、11条路由注册准确，1981源复核无漂移，worker69607已退出。权威回执 `http-next/registered-adapter-verified-v1.json`。真实PG16 HTTP数量门禁已启动，worker70123，状态 `http-next/native-quantity-v1/state.json`；登录身份仍为合成夹具，库存服务、数据库角色、事务与提交后断联回查将使用真实实现。该门禁未取得终态，不是生产验收。正式v2数量/SN均已实际升级0161，完整后续验证仍运行。

2026-10-01 16:09更新：正式v1数量门禁已完成真实0161升级、空库降级重升、完整启动ACL、两类封存并发/回滚/恢复/不可变业务检查；随后因旧业务门禁结果仍报告0160，在runner第60行失败并正常停库，尚未执行历史拒绝降级。失败原证据 `formal-native-quantity-v1/failure-reviewed.json` 保留。聚焦v2为46通过/1失败，失败仅新head的前置revision断言未增加0160节点。所有旧候选读者已退出后，`prepare_formal_v2.py`已备份并修正版本引用、当前就绪摘要入口和配置脚本；`formal-source-v2.json`仍1978源。新在跑：`formal-head-focused-v3` worker69248、`formal-native-quantity-v2` worker69249、`formal-native-serial-v1` worker69250。

纠正HTTP候选：`http-next/adapter-verified-v1.json`为56项模拟服务接口测试通过，覆盖预览、三类写入/封存/回查、提交前响应校验、提交异常、权限分离和隐藏内部字段，不是原生数据库或真实登录验收。`http-next/source-v1.json`独立1981源候选已注册11路由；`registered-adapter-v1` worker69607正在验证完整应用注册和契约。`http-next/native_http.py`与`run_native_http.py`已准备真实API角色及提交后断联测试，但尚未运行。不得把这些候选称为主树已集成。

最新状态（2026-10-01 15:55）：数量v6与SN v2均已终态通过，退出0、源码无漂移，PG16.15检查通过并正常停库；权威回执为 `native-quantity-verified-v1.json` 和 `native-serial-verified-v1.json`。下方先前运行中的描述只代表历史阶段。已从两组实际目录生成一致的向前迁移提案及运行时目录。独立 `formal-source` 已注册0161入口，1978源清单见 `formal-source-v1.json`；尚未应用主树或完成正式迁移验收。

正式候选验证：`formal-native-quantity-v1/state.json`（worker64601），`formal-focused-v2/state.json`（worker64769）。首次启动前误用系统Python3.9解析既有3.12语法，未启动测试库；已使用项目3.12解释器。聚焦v1因使用旧测试名称退出4、未执行用例，原记录保留；v2只修正命令中的实际测试名。正在验证真实Alembic升级、空降重升、启动权限、库存业务及有历史拒绝降级；不得用之前覆盖层通过代替该验收。

`artifacts/loss-correction-request-seals-next/`：以已验证迁移修复1970源为基础，12个服务/模型/测试目标，当前1973源；新增两张独立封存事实，原请求完整匹配、无库存变化、原请求永久关闭、新明确请求可执行、只读恢复与当前权限复核。候选服务尚无公共接口，不是发布版本。

- `service-verified-v1.json`：24项服务用例通过，288.64秒。
- `service-verified-v2.json`：另8项通过，119.33秒；覆盖批准/执行×数量/SN的新明确请求、旧请求继续封存、回读证明后权限变化拒绝。1973源复核无漂移，worker42610和child42623已退出。v1原测试字节保存在 `source-backups`，不混称为同一次32项全量执行。
- 数据库候选：`build_native_candidate.py`、`native_overlay.py`、`native_business.py`、`run_native_overlay.py`，输出 `native-candidate/` 八个函数定义。包括两类原请求规范/祖先/审计证明、六类请求键登记、提交权限和跨表迟到写拒绝；旧0159/0160冻结文件保持原字节。
- 当前数量测试指针 `native-current.json`，worker60272，日志 `native-quantity-v6/native.log`；SN指针 `native-serial-current.json`，worker60273，日志 `native-serial-v2/native.log`。两者固定同一服务/数据库候选字节，分别使用自己新建的PG16 Unix socket库、真实0160前置迁移和API数据库角色。它们结束前不能改所用源文件。**此为隔离开发覆盖层，不是正式0161迁移，不作为启动目录/权限全量验收。** 当前原生业务夹具的新明确纠正处置为restore_available，不能冒充其他四种处置均已原生验收。
- `native-quantity-v1`未启动测试子进程：durable job遗漏nodeBin，KeyError；原失败保留，v2只补runner配置后启动。后续看真实state和ps，不按本段旧PID猜测存活。
- 原生v2真实升级0160、覆盖层安装和API最小权限已通过；首次业务fixture提交发现新增数组交集比较的varchar[]/text[]不匹配，失败已保留于 `native-quantity-v2/failure-reviewed.json`，库正常停止、源码逐字备份。已补两个显式text[]转换。v3随后因新runner遗漏标准边缘暂存初始化而在合成导入时报external_sync_snapshots权限不足，外层包装为EdgeSyncError；不代表用户Edge或企业登录失效。v3已正常停库、源码与失败保留。v4补回标准初始化SQL和原校验器，已通过迁移、边缘权限校验、API权限与合成个人库存盘点/复核/记账/对账；后续业务仍运行，包含两类封存各三个并发场景和前后目录比对，尚无终态。
- 原生v4现已结束并正常停库：批准封存的三类并发已执行，缺少绑定、错误原始键、迟到撤权均整笔回滚；随后迟到Outbox反例遗漏available_at，实际NOT NULL约束在flush阶段拒绝，尚未到达预期提交检查，整组仍failed。原字节和回执保留 `native-quantity-v4/failure-reviewed.json`，不能把局部结果当全通过。v5仅补合成Outbox的必填时间，无生产SQL或约束变化，并启动同源码SN验证。
- SQLite工具候选已终态通过：`sqlite-verified-v2.json`。16条升级语句和6个无条件写入拒绝触发器；实际Alembic0160前置库完成空库升降重升、旧结构精确恢复、新结构再次一致、FK检查以及FK开启/关闭下4次拒绝写入。worker53160/child53164已退出，1973源及4份工具输入摘要无漂移。v1因SQLite重命名自动给表名加引号被旧目录验证拒绝，原字节保留；v2采用事务内复制数据并按原SQL重建，不降低原断言。仍为工具覆盖层，非正式0161迁移。构建工具首次app.db导入错误已改为实际app.database，未操作数据库。
- `assemble_forward_catalog.py`已准备但尚未执行：只接受真实passed、无源码漂移且正常停库的原生结果，生成待审0161目录提案。正式0161迁移入口与运行时目录仍未安装。
- 数量v5/SN v1均终态failed并正常停库：三类批准封存并发、请求绑定/原始键/迟到撤权回滚及迟到Outbox/状态拒绝已执行；不可变UPDATE正确触发0090冻结函数，但测试误断言SQLSTATE，原函数明确使用55000。已保存两份failure-reviewed.json及源字节，当前数量v6/SN v2修为精确55000和原错误文字；TRUNCATE同时包含引用绑定表，让用例实际到达不可变触发器，而非被外键提前挡住。无业务函数或SQL约束修改。
- 向前迁移配套已准备：`forward_transition.py`核验冻结目录、完整触发器、最小ACL、就绪函数及迁移锁后版本；降级仅允许空封存/空请求绑定历史，并通过重建空绑定表恢复旧目录，绝不删除生产请求历史。`build_runtime_catalog.py`准备与真实目录对应的运行时校验文件；两者尚待原生提案和正式迁移集成测试，不能视为已验证或已应用。
- SQLite冻结目录已生成：`forward-sqlite-catalog.json`，SHA256为073dc69ab861041c6db6f0eecc7c6506d8e2cbf96d4008fa10add711c395c340，7表/21触发器。新的`forward_sqlite_transition.py`在已验证本地夹具的独立副本上通过：FK开启/关闭两轮往返、两次中途故障整份结构回滚、两次结构漂移拒绝及四次业务写拒绝；证据`forward-sqlite-verified-v1.json`。此测试模拟Alembic版本推进，尚未注册正式0161入口。
- 集成正式0161时，要让旧离线函数摘要测试沿新迁移的_sources()核验0159登记函数到0161的实际正文变化；不得删除摘要断言。随后验证新的准确版本头、运行时目录/ACL、空库往返、保留历史拒绝降级及完整原生业务。
- 必须继续：原生数量/SN业务和并发、权限/原始键/迟到写直接SQL反例；冻结正式0161向前迁移、精确目录与最小ACL、空库升降往返和保留历史拒绝降级、SQLite禁止纠正写；随后正式纠正API/客户端及发布验证。不能把候选封存误记为主树已完成或上线。

### H5处置（已应用并完成主树验证）

目录 `artifacts/loss-execution-h5-next/`；22个目标的候选before/after摘要见 `application-review.json`，327份候选前端源码摘要见 `frontend-source-manifest.json`。实现总部只读可达的“报损处置”导航与路由、准确已批准明细与真实路线、预览/单独确认、完整原请求先保存再单次发送、Web Locks跨标签协调、失联只回查、not_found禁止重发、永久封存二次确认、授权变化保留原请求。写权限专用dispose_loss，不能用finalize_loss代替。原处置明确标注历史事实，退回生成与发货/收货/入库分开；报废仍明确待实现。

主树逐项应用备份：`main-application-v1/application.json`，22目标、仅覆盖匹配before摘要的App.tsx并新增其余文件。主树终态：`main-application-v1/verified-v1.json`，worker26280六步骤全通过，1969源复核无漂移：**前端118文件2046项、后端契约8项、TypeScript、仓库构建、公开构建、公开入口开发模式验证**。公开bundle无认证客户端，/xx资源与worker隔离，小程序目录一致；知识目录pending/0条，不冒充release模式通过。候选临时HTML/TSX展示入口没有复制到主树。

之前候选验证回执 `verified-candidate-v1.json`：
- 真实注册HTTP生成数量/SN×四类共8份合成契约（`export-v2.log`），当前后端契约回读8通过（`backend-contract-v1.log`）。初次外置导出未加载conftest导致收集失败，已仅补显式本地测试设置并成功，不是生产依赖故障。
- 候选全前端118文件2046项通过（`all-frontend-v1.log`，随后仅加页面样式和小屏表格展示）；聚焦118通过（`focused-v3.log`，含公开目录/Service Worker测试）；最终移动端展示调整后页面/入口10通过（`ui-final-v1.log`）。
- TypeScript、/xx仓库构建、公开首页构建成功（`warehouse-build-v3.log`、`public-build-v2.log`）。公开bundle不含处置页，私有bundle不含合成测试数据。仓库包仍有既有大块体积警告，不能算500用户/弱网性能验收。
- 浏览器已检查桌面和390px手机布局，确认前执行按钮禁用；本地模拟执行后明确仍待发货/收货/入库。截图 `desktop-preview.png`、`mobile-preview.png`、`desktop-result.png`；仅本地合成数据和模拟transport。临时tab已关、视口已恢复、5187测试server已停。
- 本轮一次错误使用node --test启动Vitest文件，属于命令错误；保留`build-checks-v1.log`，已用正确Vitest入口全部通过，不修改断言。

## 下一步顺序

1. 跟随26868/26869/26870三个当前静态回归worker，收终态/失败用例、源码1969一致及退出码。原生6组和H5主树6步骤已通过，不无故重复。
2. 三组静态worker确实结束后解除主源固定，按准确失败证据修复并重验实际受影响范围。通过后审查整批diff及必要安全检查，满足既定本地门禁才提交；GitHub准确SHA CI、生产部署与业务验收仍独立，不能凭本地通过宣称上线。
3. H5主树已集成，继续纠正批准/执行独立封存、退回补偿、报废/失而复得。已核验小程序当前仅发布公开知识查询；保持app.json只注册knowledge，不把本次总部处置加入公开小程序包。`loss-history-proof-reuse-next`仍未集成，不与已验证审计批读混淆。
4. 持续按正式基线审计：真实角色范围、微信/短信/通知/附件、正式迁移与期初、准确发布SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO/RTO及恢复回滚演练。飞书知识源继续按用户要求低优先级。当前不是上线验收完成。

## 历史与故障记录

- 本次整理前的交接内容已逐字保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_132445.md)；保留摘要在 `artifacts/loss-formal-application-next/handoff-archive-20261001_132445.json`。
- 功能缺口：[报损纠正范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)、[正式基线审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)。其他审计中的旧段落只代表当时状态。
- 已修复的API读取迁移表权限错误、审计fixture非法流、启动前manifest路径错误和README清单范围差异都有原始回执；未扩权、未删除约束、未修改历史库存记录。
- 机器可读状态：`artifacts/loss-formal-application-next/continuation.json`。有新状态时优先更新本入口及对应准确回执，避免继续叠加相互矛盾的“当前状态”段落。
