# RSC个人仓开发交接

## 当前状态：统一十表模型及0165候选运行时目录已验证，完整报废回归收尾中

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

### 当前唯一继续执行的测试与下一步

完整报废Python回归由父进程句柄 **69202** 管理三组独立pytest（启动PID46978/46979/46980只用于定位，不能凭历史PID判断存活），覆盖全部30个`test_stock_scrap_*.py`模块。当前仍需轮询原句柄并核对终态，禁止盲目重启。日志 `scrap-unified-regression-group{1,2,3}-v2.log`，源码清单 `scrap-unified-regression-v2-source-manifest.json`；终态汇总将写入 `scrap-unified-regression-v2-receipt.json`。这些路径均在 `artifacts/formal-baseline-audit-20261002-0164/`。**在终态/源码清单核验前保持非Markdown源不变。**

完成本轮回归后，按以下完整范围推进：正式Base/单一0165 revision/同步readiness及默认运行时校验一起接入；处理历史固定0164测试与SQLite结构工具的兼容；再验证真实旧逆向/纠正/请求键历史升级不变及有事实拒降。随后接HTTP、PC/H5原请求保存与中断恢复，并完成最终源码CI、真实身份/UAT及基线的迁移、三天对账、性能、备份回滚验收。完整目标仍活动，不需要用户补凭证或重启。

下文旧“当前/运行中”均为历史，以本节及实时终态回执为准。

## 当前状态：0165冻结迁移包与降级启动兼容性已验证，正式版本尚未激活

本轮从“测试中动态安装候选”推进为独立冻结迁移包。**数量64324、SN36085、安全71772均exit=0；随后启动兼容性修复的原生56435、聚焦73621、安全79308也均exit=0。全部测试句柄已终态，禁止继续轮询或重启。** 未提交、推送或部署，原有未提交改动全部保留，无需用户补充凭证。

### 本轮交付与证据范围

- `backend/alembic/stock_scrap_0165/catalog.json` 冻结131条安装SQL、10张新表、40张受影响的旧表（包括仅增加触发器的表）、39个新函数和13个原函数替换。SHA256为 `424d3f52cad7fe58f9d8656cabc5417beb428394a8a450c46aa23246975e07f2`。`catalog_probe.py`、`transition.py` 只依赖冻结目录/SQL和数据库元数据；运行时不导入应用模型、候选编译器或旧迁移模块。生成器及原始候选安装器保留用于来源追溯，正式安装不调用它们。
- 生成回执：`artifacts/local-scrap-catalog-pg16/run-0gm1wz3i/terminal-v1.json`。候选安装整体回滚后，逐条重放冻结SQL，表/函数/权限目录完全相同；源码无漂移、测试库正常关闭。生成v1初始化顺序、v2 psycopg百分号双重转义的问题已修复，成功证据以v3日志为准。
- 完整业务v3：数量 `run-6d4s80hi`、SN `run-clo3s7_3`；每组1558个源码文件在收据生成时全部匹配，日志/检查/终态JSON一致，数据库stopped/passed/serverExitCode=0。完整回执 `artifacts/formal-baseline-audit-20261002-0164/scrap-frozen-transition-v3-receipt.json`。
- 两组均使用冻结迁移包，完成空库升级/降级/再升级、7类目录/权限异常拒绝；分别保留409/414行前驱数据和359个原函数OID。只有1条真实封存记录时就拒降；两代业务后拒降完整保留19条封存、4条旧请求键登记、2条报废和2条找回执行，版本号及全库事实不变。旧审批/新封存竞争、并发撤权、自然到期、只读回查及原有两代业务均回归通过。
- v3旧历史覆盖是正式0164上的真实期初/报损/独立审批；**不是已有0164逆向/纠正及旧key历史的全面升级演练**。上述4条旧key登记产生于升级后的两代业务，仅证明拒降保留，不能冒充升级前旧key迁移证据。

### 已修复的两个迁移问题

1. PostgreSQL将原`IN`条件反编译成数组表达式，直接重放反编译文本会改变目录表达。降级现在恢复冻结的原始4条CHECK表达式，仍要求完整目录严格匹配；没有放宽约束。
2. 独立启动探针 `scrap-downgrade-runtime-probe-v1.log` 确认：可见目录恢复后，旧运行时校验仍会因正常删除列留下的内部占位而拒绝启动。`stock_loss_correction_security.py` 现在只允许类型已清空、不带NOT NULL、identity/generated且没有列ACL的标准已删除占位。实际可见字段及所有约束仍逐项严格验证，没有增加任何数据库权限。

启动修复v4：`artifacts/local-scrap-transition-pg16/run-rg6chf67`，原生两次降级后完整API身份的安全启动检查通过；共16个标准删除列占位可接受。真实删除必需字段、增加额外字段、增加列ACL均被拒绝，回滚后完整API启动恢复。聚焦 **305 passed (122.01s)**，安全 **2485文件通过**；回执 `artifacts/formal-baseline-audit-20261002-0164/scrap-transition-readiness-v4-receipt.json`。

**证据版本不可混用：** 完整数量/SN为v3；之后v4只改运行时占位检查、迁移测试中的启动断言、新增启动反例门禁及runner聚焦模式，共4个源码文件。冻结SQL、业务写入/回查代码没有变化。v4有当前源码的原生启动/安全聚焦证据，**没有重跑当前全部源码下的完整数量/SN业务门禁，也不是正式0165或最终CI通过**。提交前仍须按最终正式接入的实际源码收齐门禁。

迁移链核对注意：直接调用`ScriptDirectory.get_heads()`会绕过现有迁移编译缓存，重复解析历史脚本，已中止该只读收据进程（exit=130）。使用`migration_script_cache.cache_migration_compilation(Path("backend/alembic/versions"))`包围revision map读取；不得因此重启已结束的数据库测试。

### 下一步按顺序推进

1. 正式0165接入：统一Base模型及私有表句柄，补新请求键表/旧别名列；更新运行时权限/函数/触发器目录及readiness，并登记单一Alembic revision。冻结迁移不得重新依赖实时模型。现有 `stock_scrap_*_schema` 禁止Base注册及重复定义的限制需同步改造。
2. 校验旧目录叠加：0159/0163运行时验证器中的旧表、旧函数及触发器必须使用精确前后变更，不能跳过旧校验。明确正式升级后的新head与历史前驱测试的固定0164路径；当前业务runner已固定升级0164，不再将动态head当作前驱。
3. 新建正式0164旧逆向/纠正/旧key业务历史，验证真实升级后的逐行/别名保留及原请求只读回查；再完成实际Alembic版本变更、拒降时head/事实保持、最终启动和权限门禁。本轮直接transition调用不等于这些完整正式迁移证据。
4. 接HTTP、PC/H5原请求保存和中断恢复，取得最终源码对应CI及真实身份/UAT；继续完整基线的迁移、三天对账、500用户性能、备份恢复及发布/同步/业务回滚验收。

下文旧“当前/运行中/冻结”均为历史，以本节为准。整体上线目标未完成。

## 前轮已完成证据

## 当前状态：旧审批/新封存竞争及六类并发撤权完整门禁通过

**数量92002、SN77628、安全31671全部exit=0，无活跃测试句柄，源码冻结解除。** 数量 `run-7bpwerg0`、SN `run-4o4cgiy5` 均passed/stopped/serverExitCode=0/sourceDrift=[]；收据时重新生成完整源码清单，与每组1552个文件完全一致（包括新增/缺失文件）。日志末尾JSON、checks.json及terminal-v1.json一致。安全2478文件通过。完整机器回执：`artifacts/formal-baseline-audit-20261002-0164/scrap-seal-cross-revoke-v1-receipt.json`；日志 `scrap-seal-cross-revoke-{quantity,serial,safety}-v1.log`。本节句柄全部终态，不再轮询或重启。

本轮只增加原生测试与编排，未修改产品守卫；语法、diff check通过。未提交、推送或部署，既有未提交改动全部保留，无需用户提供账号或凭证。

### 已完成的新增证据

1. **旧审批/新封存同raw key竞争：每组2个，两组合计4个场景通过。** `pg16_scrap_cross_registry_races.py` 在第一代真实找回后，使用合法旧CorrectionApprove和独立新原始关闭请求，同raw key、不同request_id、同actor。两个真实API连接分别控制新seal先提交和旧审批先提交，实际观察账本锁等待。新seal先赢时，旧服务和真实key registrar正常到达实际COMMIT，由封存冲突23514拒绝并全库回滚；旧审批先赢时，新关闭准确返回冲突未知503，不造第二份事实。双方各自原命令均READ ONLY验证，另一类命令不能被误当成自己的found。旧审批先赢的结果随后用于真实纠正报废，不重复批准。
2. **六类权限目录并发撤权：每组12个，两组合计24个场景通过。** `pg16_scrap_seal_revocation.py` 每类分别控制撤权先赢/关闭先赢，一个真实API业务连接与独立fixture-owner权限事务竞争。后者只将对应write grant改成deny，read保持有效；真实权限行锁等待及不同backend PID均验证。撤权先赢时关闭服务拒绝23514且不留seal；关闭先赢时撤权等其提交后才生效，准确历史在write deny时仍可READ ONLY读取。恢复测试权限后全库精确相等；没有API身份表写权限、末尾同事务注入或时钟模拟。
3. 竞争产生的一份额外封存纳入三时区/撤write回查矩阵：**每组19份关闭回查、19次重复无新增、19次已关闭写拒绝、57项读冲突、57项写冲突、10份已执行原结果返回**均通过。原两代报废/找回、24个关闭/执行双API竞争、12个自然到期、来源/权限/审计/低层绕过COMMIT拒绝全部回归通过。

回执新增 `crossRegistrySealConcurrency` 与 `sealRevocationConcurrency`，分别保存赢家/等待者PID、实际锁等待、原命令调用次数、拒绝阶段/错误及全库证明。覆盖边界：跨接口竞争当前明确覆盖“旧纠正审批↔新原始关闭”，不宣称所有旧逆向/新登记动作排列已通过；并发撤权证明是数据库权限目录竞争，不是管理员HTTP端点或所有身份撤销方式验收。

### 下一步（完整上线目标仍未完成）

1. 推进正式0165接入：统一Base及私有表定义、冻结迁移DDL/函数目录、核对真实单head和前驱、最小ACL、运行时readiness。现有候选编译器明确禁止修改Base，不能只注册一个revision就声称完成；历史前驱测试的upgrade head也要改为明确前驱/正式升级路径，避免重复安装候选。
2. 为正式迁移取得空库升级/降级/再升级、真实0164旧历史逐行保留、旧key aliases及封存保留、有新增报废/找回/封存事实时拒降且head和事实不变的证据。候选测试库正常运行不是正式迁移证明。
3. 对其他合法旧/新动作的重复请求保护继续做覆盖审计，明确哪些由已验证共同registry/fence直接覆盖，哪些仍需真实业务竞争证据。不得拿不合法报废旧逆向的阶段拒绝冒充key竞争；也不得把本轮单对命令夸大为所有排列。
4. HTTP、PC/H5原请求保存与中断恢复、准确SHA CI、真实身份/UAT，以及完整基线的迁移/三天对账/500用户性能/备份与回滚验收仍待完成。

下文旧“当前/运行中/冻结”全部是历史阶段，以本节为准。本轮通过不等于完整产品上线。

## 前轮证据（旧句柄均结束）

## 当前状态：六类关闭请求自然到期的数量/SN完整门禁通过

**数量44802、SN75857、安全13280全部exit=0，无活跃测试句柄，源码冻结已解除。** 数量 `run-e8dmc88c`、SN `run-b57s0pfa` 均passed/stopped/serverExitCode=0/sourceDrift=[]；收据时重新生成完整源码清单，与每组1550个文件完全一致（包括新增/缺失文件）。日志末尾JSON、checks.json及terminal-v1.json一致。安全2476文件通过。完整机器回执：`artifacts/formal-baseline-audit-20261002-0164/scrap-seal-natural-expiry-v1-receipt.json`；日志 `scrap-seal-natural-expiry-{quantity,serial,safety}-v1.log`。本节句柄全部终态，不再轮询或重启。

新增 `pg16_scrap_seal_expiry.py`，在两代真实API业务历史之后、撤写权之前检查六类关闭请求。每组6类、两组合计**12个自然到期场景**，全部通过：

- owner仅在测试API事务开始前配置真实测试角色任期截止时间（数据库时钟加30秒），保持身份版本和其他权限元数据不变。实际业务调用仍使用star_oam_api，调用一次。
- 服务在授权有效时返回sealed，事务内确有seal及唯一审计；外部完整数据库快照证明它们尚未提交。
- 按数据库clock_timestamp等待自然到期后，实际COMMIT由 `rsc_scrap_seal_authority_0165` 以23514/当前read及动作权限缺失拒绝；没有末尾注入身份版本，也没有用私有探针替代真实业务服务。
- 失败提交后的全库快照与配置后的基线完全一致；恢复测试授权后全库与测试前一致。随后真正READ ONLY精确回查为not_found且retry_allowed=false，不代表可盲目重发。
- 回执 `sealNaturalExpiry.kinds` 保存各类真实backend PID、授权行ID、服务前后/到期/提交时间、拒绝阶段和回滚证明，并逐项验证“服务返回时间 < 到期时间 <= 提交尝试时间”。

原24个双API关闭/执行竞争场景、六类低层绕过COMMIT拒绝、两代库存/SN业务和历史/权限/审计均回归通过；每组18份撤write后的只读关闭回查、18次重复关闭、54项读冲突、54项写冲突和10份已执行原结果返回均通过。没有为测试增加API权限。

本轮仅新增原生测试及门禁编排、文档；产品守卫未修改。语法及diff check通过。未提交、推送或部署，既有未提交改动全部保留，无需用户补充账号或凭证。

### 下一步（完整上线目标仍未完成）

1. 跨新旧命令共用raw key的真实双连接竞争：优先在第一代找回后，以合法旧CorrectionApprove与新原始请求关闭竞争；使用同raw key、不同request_id，分别证明旧登记先赢和新关闭先赢。不能用同一新命令竞争或不合法业务阶段替代。旧逆向等其他分支仍须按其合法来源独立判断覆盖范围。
2. 并发撤权：真实关闭服务与另一个权限变更事务竞争，证明锁等待和两种提交顺序；本轮自然到期及以前的末尾身份版本注入都不替代此项。
3. 正式Base/revision/ACL/readiness/目录与带事实安全降级。目前仍为0164之后的隔离候选，未激活公开HTTP。接入正式迁移时要同步处理现有编译器的私有metadata限制，并将历史前驱测试的upgrade head改为明确前驱/正式升级路径，避免重复安装候选组件。
4. HTTP、PC/H5原请求保存和中断恢复、准确SHA CI、真实身份/UAT、完整基线迁移/对账/性能/备份及上线验收。

下文所有旧“当前/运行中/冻结”是历史阶段，以本节为准。本轮通过不等于完整产品上线。

## 前轮证据（旧句柄均已结束）

## 当前状态：六类请求关闭/执行双连接竞争的完整数量/SN门禁通过

**数量49455、SN26805、安全37075全部exit=0，无活跃测试句柄，源码冻结解除。** 数量 `run-9lsfip49`、SN `run-2lv3jvnl` 均passed/stopped/serverExitCode=0/sourceDrift=[]；收据时重新生成完整源码清单，与每组1549文件清单完全相同（同时校验新增/缺失文件），日志末尾结果、checks.json及terminal-v1.json相互一致。安全2475文件通过。完整回执 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-races-v1-receipt.json`，日志 `scrap-seal-races-{quantity,serial,safety}-v1.log`。不要轮询或重启这些终态句柄。

`pg16_scrap_seal_races.py` 已在原始/纠正报废、找回申请/区域/总部/实际恢复六个真实阶段完成两种提交顺序；每组12个、两组合计**24个真实并发场景**：

- 两个不同backend PID均为star_oam_api；赢家未提交时，对手通过 `pg_blocking_pids` 被实际赢家阻塞，且已持有账本表RowShareLock。每方服务只调用一次，无重试。
- seal先提交：执行方准确409拒绝；仅seal/audit/audit-chain三表变化、唯一seal和审计，库存/审批事实没有新增。
- execute先提交：等待中的关闭调用返回found及准确原结果，不造seal。对手结束事务前后完整已提交数据库快照相同。
- 两种顺序均再做真正READ ONLY准确回查。证据按kind+direction保存在回执 `sealConcurrency`，含真实PID、锁等待及快照断言。字段committedBusinessFacts=1表示一个原请求的有效业务结果，并非事务只写一行表。

没有增加API权限。完整数据库快照通过原有owner读连接取得；赢家提交后暂缓对手结束事务以取得基线，再比对其完成后的快照。seal由真实组合服务创建，低层registrar准确重复返回created=false仅用于取回后续审计反例所需真实行/payload，不伪造created标志。原六类绕过服务COMMIT拒绝、两代业务、权限/历史/审计回归均保留且通过；每组18份撤write后的关闭回查/再次执行拒绝、54项写入冲突及10份已执行原结果返回也通过。

本轮只改原生测试编排/辅助和文档，未改产品源码。语法及diff check通过；前轮20项聚焦的产品源保持一致，未重复运行相同聚焦。未提交、推送或部署，既有未提交改动全部保留。

### 下一步（完整上线目标仍未完成）

1. 跨旧/新命令命名空间复用同raw key的实际双连接竞争：必须是真实合法阶段/原命令，各自来源可验证；不能仅改哈希或只测试同一新命令。旧审批/逆向registry与新关闭/登记要双向拒绝冲突，核对没有第二份事实。可复用本轮实际锁等待/快照方法，但按真实旧/新业务返回类型分别断言，不伪装成同一请求的found。
2. 关闭授权自然到期及并发撤权。用真实服务在权限仍有效时创建待提交事实，按数据库clock_timestamp等待到期后COMMIT必须拒绝并完整回滚；不把owner末尾版本注入当成自然到期。真实库存恢复的保管到期约束继续独立，历史只读不要求当前write。
3. 正式Base/revision/ACL/readiness/目录及带事实安全降级。目前仍是完整0164前驱上安装的隔离候选，没有正式HTTP激活。
4. HTTP、PC/H5原请求保存/中断恢复、准确SHA CI、真实登录/UAT和完整正式基线验收。本轮局部门禁不替代上述上线条件。

下文旧“当前/运行中/冻结”仅是历史记录，以本节为准。当前无需要用户补充的阻塞。

## 前轮终态证据（旧句柄均结束）

## 当前状态：六类实际写入口关闭拒绝及数据库兜底全部通过

**数量62164、SN9386、安全41452全部exit=0，无活跃测试句柄，源码冻结解除。** 数量库 `run-famxou3n`、SN库 `run-pqu3jhd8` 均passed/stopped/serverExitCode=0/sourceDrift=[]；收据时各1548个受检源码文件摘要再次匹配。安全扫描2474文件通过。完整机器回执 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-write-admission-v1-receipt.json`，日志 `scrap-seal-write-admission-{quantity,serial,safety}-v1.log`。这些句柄均已结束，不要重启或轮询。

`stock_scrap/request_guard.py` 已接入 `bound_commands` 原始/纠正报废、三阶段找回复核和实际找回。严格重解析原命令，账本→权限锁，当前read+完整原请求回查；sealed明确409、found要求只读恢复、冲突/unknown阻断，只有同一受锁事务的not_found才继续既有当前write/保管/库存/审批验证。没有把查询未找到变成公开重发许可，没有放宽物理权限。

本轮证据（每个原生跟踪模式各自验证）：

- 六类真实可执行阶段，先永久关闭独立原请求，再调用实际组合服务，均明确拒绝。刻意绕过服务前检的真实库存/审批writer及真实key登记，全部在COMMIT被封存冲突拒绝；全库快照恢复，保留封存及唯一审计。
- 原始无root及其他早期阶段关闭，在两轮真实报废/找回之后仍准确回查；共18份seal在撤write后真正READ ONLY读取、18次重复关闭无新增、18次再次执行明确关闭409。
- 54项改key/请求号/原因的读取冲突，另54项相同维度的实际写入口冲突，均拒绝且全库不变；10份已执行请求的关闭调用返回原结果。
- 旧查询冲突unknown、两代业务、当前权限、来源、三时区UTC一致性和审计/防直接修改均回归通过。`closedStageWriteProofs`、`sealLookup`的准确计数保存在上述回执中。

聚焦72016已exit=0，20 passed、1依赖弃用警告（67.68s），日志 `scrap-seal-write-admission-focused-v1.log`。覆盖数量/SN的关闭拒绝、四类改动冲突、撤write、缺read、孤立关闭审计unknown、已执行不得重做、伪造model_copy重校验；拒绝路径只SELECT、快照不变。语法与diff check通过。后续未修改非Markdown源。

### 下一步实施与验证

1. 六类真实双API连接竞争：分别证明seal先提交/执行先提交，通过 `pg_blocking_pids` 观察实际账本锁等待，并核对唯一事实、失败方完整回滚和准确回查。可参考 `pg16_loss_multigeneration_races.py` 的实际锁图观测；不要只靠Barrier或同时启动线程宣称竞争已发生。结合现有 `pg16_scrap_closed_execution.py` 的六个实时业务阶段，避免用已失效阶段制造无关拒绝。
2. 跨新旧registry同raw-key竞争、当前权限自然到期与并发撤权/保管变化。本轮顺序式迟到写及owner末尾版本注入不替代这些证据。
3. 正式Base/revision/ACL/readiness/目录和带事实降级拒绝。目前仍为0164后的隔离候选，没有公开HTTP激活。
4. HTTP、PC/H5请求保存与中断恢复、准确SHA CI、真实身份/UAT及完整基线上线门槛；局部候选通过不等于完整上线。

**未提交、推送或部署，既有未提交改动全部保留；无需要用户补充的阻塞。** 下文旧“当前/运行中/冻结”是历史阶段，以本节为准。

## 前一轮已完成证据（以下无本轮活跃句柄）

## 当前状态：关闭请求组合服务及准确回查的数量/SN原生验证全部通过

**数量46955、SN51906、安全92750全部exit=0，无活跃测试句柄，源码冻结已解除。** 数量库 `run-w7ds3vwt`、SN库 `run-5so0yi8l` 均 passed/stopped/serverExitCode=0/sourceDrift=[]；收据时各1545个受检源码文件摘要再次匹配。安全扫描2471文件通过。完整机器回执：`artifacts/formal-baseline-audit-20261002-0164/scrap-seal-readback-v1-receipt.json`；日志 `scrap-seal-readback-{quantity,serial,safety}-v1.log`。不要重启或轮询这些已结束句柄。

本轮已证明（每组）：

- 六类请求通过候选 `request_seals` 组合服务实际提交；仅seal、audit_events和audit_chain_heads三表变化。
- 13份准确关闭结果在撤销写权限后仍可真正 `SET TRANSACTION READ ONLY` 回查；覆盖UTC、上海、洛杉矶时区。原始无root请求在后续业务后仍保持root为空。
- 39项原key/请求号/原因改动被拒绝；13次重复关闭无新增，10份已执行请求调用关闭服务返回原结果，全库快照不变。
- 旧查询遇两类新seal冲突返回unknown，不返回干净not_found；原两代报废/找回、真实权限、来源、审计及迟到原始执行回滚全部回归通过。

实现为 SELECT-only `seal_reads`、原始/纠正及四阶段找回封存回查、旧查询冲突适配和 `request_seals`。只读回查不调用含FOR SHARE的SQL来源函数；准确found/sealed只需当前read，新关闭才需要当前write及唯一审计。没有注册公开HTTP路由。之前的聚焦38444已exit=0，69 passed、1依赖弃用警告（288.45s）；之后仅清理测试时区导入及修正原生脚本混合命令取action，当前源已由上述两组原生验证覆盖。语法、diff check通过。

上一轮持久化UTC/审计v2回执仍保留在 `scrap-seal-persistence-v2-receipt.json`：数量61493、SN92060、安全10088全部终态。下方所有“运行中/冻结”仅为历史记录，以本节为准。

### 接续顺序与尚未满足的上线门槛

1. 在真实写服务增加准确sealed拒绝，而非仅等数据库COMMIT报错；保留低层绕过服务时的数据库提交拒绝反例。现有 `bound_commands` 仍直接调用执行/审批服务；新增前须保持严格原命令校验、账本→权限锁顺序、当前read及历史证明。
2. 六类阶段迟到写、两个实际API连接的seal先赢/执行先赢、跨新旧raw key竞争、自然权限到期。当前末尾版本注入、顺序重复/回滚证据不等于这些并发证据。
3. 正式Base/revision/ACL/readiness/目录和带事实安全降级；目前仍是0164后的隔离候选，未激活正式迁移。
4. HTTP、PC/H5原请求保存与中断恢复、准确提交SHA的CI、真实登录/UAT及完整基线上线门禁。通知送达、OAM收货、个人入库继续独立，不把本轮局部通过当作完整产品完成。

**未提交、推送或部署，全部既有未提交改动保留。** 当前无需要用户补充的阻塞；可继续开发上述缺口。

## 历史阶段记录（状态以首节为准）

## 最新终态与当前复测：受控封存v1通过，UTC/审计反例v2运行中

**v1 83871 / 5838 均exit=0**。数量 `run-i0p1z0w7`、SN `run-fns87v3u` 均 passed/stopped/serverExitCode=0/sourceDrift=[]，收据时当前源码逐文件摘要匹配；`scrap-seal-persistence-v1-receipt.json` 保存完整回执。安全57731 exit=0，2467文件。原始无root封存+唯一审计实际提交，漏/错审计拒绝、重复无新增、迟到真实报废COMMIT拒绝与全库回滚通过。每组六类封存提交、6已执行请求拒绝、6晚身份版本COMMIT拒绝、5 API直接写/私有调用拒绝、3 owner修改/删除/清空拒绝，7个seal保留；全部旧业务/回查/来源/权限回归通过。

上述源随后发现审计payload中的created_at依赖会话TimeZone，已改为固定UTC微秒格式。新增v2反例：孤立/重复seal审计COMMIT拒绝；每次成功关闭仅seal表、audit_events、audit_chain_heads三表变化；UTC/Asia/Shanghai/America/Los_Angeles三时区精确历史及payload一致。v1证据不覆盖这些修改，不把时区稳定性提前记为通过。

**当前活跃：数量61493、SN92060；安全10088已exit=0，2467文件通过**。日志 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-persistence-{quantity,serial,safety}-v2.log`；句柄和待验状态另存 `scrap-seal-persistence-candidate-v2.json`。语法/diff check通过。两native终态前冻结非Markdown源，不轮询旧v1句柄、不因观察超时重启。

下一步仍需公共服务的准确found/sealed/unknown回查、旧lookup新seal冲突接入、实际双连接竞争/自然到期、所有阶段迟到写反例、正式迁移/Base/ACL/readiness和HTTP/客户端/CI验收。当前只有受控数据库候选和实际API角色事务证据，未公开激活、提交、推送或部署。下文“当前进行”均为历史阶段。

## 当前进行：受控封存写入、唯一审计与提交栅栏

canonical/source/current-authority 已拆出无明文key私有函数，旧真实key包装入口保留。新增 `seal_persistence.sql`：DB从真实key计算封存行，ledger→principal→audit head锁；缺失检查覆盖两登记表、旧/新请求事实、库存交易/收发、孤立审计/状态/通知。新增精确历史/审计证明、seal表只追加与不可清空，以及各类事实/事件上的deferred双向检查。API只可SELECT封存表和EXECUTE受控registrar，不可直接改表；私有证明函数不开放。正式迁移/公共服务/lookup尚未接入。

原生新增 `pg16_scrap_seal_persistence.py`：无root原始请求实际seal+audit提交、遗漏/错误审计提交拒绝、重复关闭无新增、迟到原始报废COMMIT拒绝及全库回滚；两代业务之后六类新请求提交、已执行请求拒绝、晚身份版本变化拒绝、API直接写拒绝、owner修改/删除/清空拒绝。规范8向量/98非法输入同步验证无key核心与真实key包装一致。以上均待本轮终态，不能提前记通过。

数量 **83871**、SN **5838** 已启动；安全 **57731 exit=0，2467文件通过**，日志前缀 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-persistence-`。两组已通过原始无root封存+唯一审计实际COMMIT，漏/错审计提交拒绝，以及迟到真实报废COMMIT拒绝和全库回滚；其余范围仍待终态。语法及diff check通过，两native终态前冻结非Markdown源。当前无生产变更、提交、推送或部署；更早的成功回执不覆盖本次新增写入。

## 最新终态：六类数据库关闭权限的数量/SN门禁全部通过

**95533 / 5095 均 exit=0**。数量 `run-ey_wvk3b` 与SN `run-v8lc9r6i`，均 passed/stopped/serverExitCode=0/sourceDrift=[]；收据时逐文件摘要重新比对一致。安全 **12907 exit=0，2465文件通过**，语法及 `git diff --check` 通过。全部本节句柄终态，源码冻结解除，不重启或轮询旧测试。回执在 `artifacts/formal-baseline-audit-20261002-0164/`。

每组新增六类准确历史来源的当前read+write授权通过，**51个拒绝场景**覆盖身份版本、错人员、read/write缺失与deny、停用、角色到期和三类自审。六类保管到期后关闭授权分量仍通过，四个找回阶段的原物理权限函数仍拒绝，所有试验前后全库事实相同。原有两代真实业务、全部只读回查，以及10来源/49错误绑定回归也全部通过。没有创建seal事实，不能将这些结果写成永久关闭、自然到期COMMIT或双连接竞争已完成。

下一步：分离不依赖明文key的canonical/source/当前授权复核，再加入受控registrar、唯一审计及deferred提交证明、双向迟到执行拒绝和并发；具体key处理边界见设计文档7.1。正式迁移/Base/ACL/readiness、HTTP/客户端/最终CI及生产验收仍待完成。未提交、推送或部署。以下“当前进行”均为历史阶段。

## 当前进行：六类封存的数据库当前授权验证

新增私有 `seal_authority.sql`，从已验证真实source推导范围，以当前身份、角色图、read及动作write权限、显式deny和区域/总部独立性授权；不调用当前保管检查，也不修改原物理恢复函数。所有API等运行角色均无EXECUTE。它只返回来源与授权证明，不写seal，不是永久关闭完成。

新增 `pg16_scrap_seal_authority.py` 在两代真实业务后、撤销写权限之前验证六类来源：缺少/deny read与write、旧身份版本、错人员、停用、角色到期及自审拒绝；保管到期后该授权分量仍可通过，而四阶段物理恢复守卫仍拒绝。全部变更在savepoint回滚并比较全库事实。未宣称COMMIT/自然到期/双连接竞争已通过。

原生数量 **95533**、SN **5095** 已启动；安全 **12907 exit=0，2465文件通过**，日志前缀 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-current-authority-`，两原生待终态。语法与diff check通过。两原生结束前冻结非Markdown源，保留原进程，不因观察超时重启；未提交/推送/部署。

## 最新终态：六类封存来源的数量/SN原生验证通过

原数量 `run-6_ar83xs`、SN `run-elx0z6jg` 均已 passed/stopped/serverExitCode=0，sourceDrift=[]；收据时逐文件重新比对源码摘要一致。机器回执为 `artifacts/formal-baseline-audit-20261002-0164/scrap-seal-source-current.json`。原93530/18734对应测试已终态，不再重启或以旧“仍运行”记录为准。

每组两代真实报废/找回业务及完整原请求回查通过，新增10个历史来源、49次错误来源绑定拒绝，覆盖六类命令；原始终审尚无root的来源检查及API私有调用拒绝通过。安全2463文件通过。历史来源检查没有新增seal、库存或审批事实，不是关闭请求业务完成。

源码冻结解除。下一步补数据库当前read+动作write权限和独立性，再接受控封存写入、唯一审计、双向迟到拒绝和真实并发。正式迁移/公开入口/最终CI仍未完成；未提交、推送或部署。下方“当前进行”是历史阶段记录。

## 当前进行：六类封存真实来源的 PostgreSQL 历史验证

新增私有 `seal_source.sql` 的 `rsc_prepare_scrap_seal_source_0165`：首先调用已验证规范命令/真实key派生，再从真实decision/review/line/order推导原始来源；无root时直接校验报损提交、区域与总部历史，不伪造处置根。纠正绑定真实root/inverse/独立纠正决定；找回四阶段绑定实际报废与申请/区域/总部来源，并调用完整历史图和找回复核证明。原始root始终NULL，申请/复核不合成plan；所有封存锚点、原报损范围及来源时间由数据库派生。此函数不创建seal，也不替代当前授权、缺失或审计证明；所有运行角色均无EXECUTE权限。

新增 `pg16_scrap_seal_sources.py`，集成既有真实业务门禁：原始报废尚无root时核验来源且全表不变；完成两代真实报废/找回后，验证原始、纠正及八个找回请求的历史来源，逐一篡改来源UUID/hash，以及换用第二代实际父记录及其匹配hash，均须拒绝。API私有调用必须42501。来源检查在移除写授权后以owner运行，不能称为当前关闭授权已通过。

原生数量 **93530**（`scrap-seal-source-quantity-v1.log`）、SN **18734**（`scrap-seal-source-serial-v1.log`）已启动，日志位于 `artifacts/formal-baseline-audit-20261002-0164/`，结果待终态。安全 **17097 exit=0，2463文件通过**（`scrap-seal-source-safety-v1.log`）。两原生均已通过新增的“实际原始批准尚无root时来源证明、API私有调用拒绝及全表不变”，后续两代业务与最终历史来源矩阵仍在运行。期间冻结非Markdown源码，不因观察超时重启。之前通过的规范/结构回执不覆盖本次新增来源函数。受控写入、审计/提交、精确回查、竞争及正式迁移仍未完成；未提交、推送、部署。

## 当前进行：受控封存写入的数据库请求规范与真实键派生

新增私有 `backend/alembic/stock_scrap_0165/seal_request.sql`：`rsc_prepare_scrap_seal_request_0165` 对 original/correction/apply/regional/headquarters/execute 六类规范命令重建比对，检查明确动作、来源UUID/hash、请求号、规范理由、排序唯一附件和仅三类执行命令拥有原plan hash。根据真实client key派生既有共同token与全部五个动作hash，不接受调用方提供的hash，不返回明文key。函数不写任何seal事实，也不证明来源存在、当前权限、缺失或审计；API/backup/edge等均无EXECUTE权限。

新增原生 `pg16_scrap_seal_request_contract.py`，使用Pydantic产生八份语法向量（含两种复核退回决定），验证Python/PG的规范JSON、hash和真实键派生一致，非法内容拒绝及API私有调用拒绝。向量UUID只是语法样本，不是实际业务来源。既有新建库结构runner集成此项；此前结构/权限证据不覆盖这份新SQL。

首次原生 **70668 exit=1**：完整0164迁移及结构安装通过，新函数的附件展开 `value` 与同名PL/pgSQL变量歧义；失败库 `run-vhu5f1qc` 已 stopped/checks=failed/serverExitCode=0，日志保留。已改用明确 `f.identifier` 列引用。安全 **24180 exit=0，2461文件通过**，早于此SQL修复。

v2 原生 **84499 exit=0**（`scrap-seal-request-native-v2.log`）：新库 `artifacts/local-scrap-seal-structure-pg16/run-ke4t0icg` passed/stopped/serverExitCode=0/sourceDrift=[]，源码收据时核对一致。8份规范向量全部与Python的请求hash、共同token和五个动作hash一致，98个非法输入实际23514拒绝，API私有函数调用42501拒绝，seal行仍为0。完整0164迁移、18外键/34约束、359旧函数/782旧外键及表直接访问拒绝再次通过。`scrap-seal-request-current.json` 保存全部回执与源摘要，不包含Markdown/前端。

安全 **47195 exit=0，2461文件通过**（`scrap-seal-request-safety-v2.log`），`git diff --check`通过。所有本节句柄已终态，源码冻结解除；不再轮询或重启同版检查。仍需六类真实来源/历史证明、受控registrar、唯一审计/COMMIT、回查及并发迟到拒绝；无公开入口或正式迁移，未提交、推送、部署。

## 最新终态与新实现：封存原生结构通过，开始封存权限分离

**62547 / 7350 均 exit=0**。v2 新库 `artifacts/local-scrap-seal-structure-pg16/run-_46mrq0f` 已 passed/stopped/serverExitCode=0/sourceDrift=[]；完整0164迁移后的封存候选18个外键、34个约束有效，359个旧函数、782个旧外键保留，API SELECT/INSERT/UPDATE/DELETE/TRUNCATE 均实际42501拒绝。安全2458文件通过。`scrap-seal-structure-current.json` 保存原生结果、正常停库及收据时核对的源摘要；11项SQLite结构检查另有前节日志。没有受控封存业务写入、正式迁移或上线验收，此处不要扩大为业务完成。

上述两个句柄已终态，不再轮询，源码冻结解除。随后修改 `stock_scrap/recovery_authority.py`：提取共同当前角色/范围/身份/独立性检查；原 `authorize` 仍无条件要求当前物理保管。新增 `authorize_closure` 仅提供关闭原请求的写授权分量，并校验准确申请及总部所需区域来源，不提供库存permit，也不替代当前read、历史、缺失、审计与锁证明。新结构原生摘要早于此权限修改，不覆盖修改后的整体源码。

聚焦 **44162 exit=0，8 passed（74.88s）**（`scrap-seal-authority-v1.log`）验证数量/SN保管到期时关闭授权仍可检查、真实物理恢复继续拒绝，以及现有数据库撤权/角色到期回归。包括借角色、错范围、缺授权、自审、过期身份版本和错误区域来源；库存与审批快照不变。安全 **77930 exit=0，2459文件通过**（`scrap-seal-authority-safety-v1.log`）；`git diff --check` 通过，当前无活跃测试句柄。该权限帮助函数不创建封存事实；没有新数据库COMMIT或自然到期竞争证据。

下一步：受控registrar从真实client key派生token及五个aliases，从真实来源派生六类typed锚点；以同一canonical算法验证command/hash，在账本及权限锁内完成不存在证明与唯一审计。再把准确seal纳入原始/纠正/找回/旧查询，以及双向迟到执行拒绝与实际双连接竞争。当前read、完整历史、原计划hash和物理库存授权必须继续分开；不得把 `authorize_closure` 当成写库存permit。未提交、推送、部署。

## 最新进行：封存六类来源结构 11 项通过，原生结构检查中断后恢复

新增三份 `test_stock_scrap_seal*_schema.py`，数量/SN 的实际批准来源验证共 **11 passed**：原始7项、找回四阶段2项、纠正2项。包括原始无 root、六类准确形状、必填字段 NULL 拒绝、真实审批 FK、重复 token/actor-request 拒绝和库存不变。只是 SQLite 结构证据，不是数据库受控封存业务成功。日志为 `scrap-seal-schema-v2.log`、`scrap-seal-recovery-schema-v1.log`、`scrap-seal-correction-schema-v1.log`；原始 v1 的测试模块路径错误已修正且保留失败日志。

新增 `scripts/run_local_pg16_scrap_seal_structure_checks.py`，只接受新建的本地 PG16：完整升级0164后安装候选结构，验证真实外键、旧函数/外键保留及 API 直接操作拒绝。v1 句柄15719、安全7798在客户端中断后已消失，OS确认脚本、迁移和PG进程均不存在；`run-z7cj4tib` 的postgres日志记录 unexpected postmaster exit，没有checks或正常停库证据。保留原目录与原状态文件，另存 `scrap-seal-structure-interrupted-v1.json`，不能记为通过或业务断言失败。

同源恢复 v2：原生 **62547**（`scrap-seal-native-structure-v2.log`）、安全 **7350**（`scrap-seal-structure-safety-v2.log`），均在 `artifacts/formal-baseline-audit-20261002-0164/`，终态待收。这两个进程结束前重新冻结非Markdown源码。旧六库成功不覆盖新seal结构；无正式迁移、受控registrar、封存服务/回查或并发关闭证明，仍未提交、推送、部署。

## 最新接续：六个原生子库全部终态，开始新永久封存结构

下方旧段落的“仍运行/源码冻结”均为历史状态，已由本节取代。37035、15070、30038、4112 全部 exit=0；合计六个数量/SN 原生子库均 passed/stopped/serverExitCode=0/sourceDrift=[]。对应机器回执位于 `artifacts/formal-baseline-audit-20261002-0164/`：`scrap-recovery-closure-business-current.json`、`scrap-pre-upgrade-seals-current.json`、`scrap-registry-legacy-generations-current.json`。不要重启或轮询旧句柄。

本批已验证两代报废/找回、实际找回提交前身份/保管变更拒绝及回滚、旧查询遇新键返回 unknown、真实升级前旧封存保留及完整旧三轮业务。晚变更采用 owner 注入，尚不等于双连接并发撤权或自然到期。原生源码清单不包含 Markdown/前端，并且不覆盖本批之后新增的封存源。

已解除源码冻结，新增私有 `backend/app/stock_scrap_seal_schema.py`，六类真实来源与 raw-key provenance 结构候选。原始报废封存只绑定真实终审，不生成虚假 root。新增 `test_stock_scrap_seal_schema.py` 正在验证结构；首次 v1 因测试引用不存在的模型模块而收集失败，日志保留，已改用实际 `stock_operation_models`。尚无受控 seal registrar、封存写入/回查入口、正式迁移或新封存并发证据；结构测试不能替代这些门禁。未提交、推送或部署。

## 当前进行：旧查询交叉、新找回晚撤权、升级前封存（固定新源）

已新增私有 `stock_scrap/legacy_request_lookup.py`：组合旧完整只读恢复与新登记两遍冲突检查、当前只读授权及游标一致性；新表缺失返回 unknown，不回退为空、不写登记。正式 0164 路由保持现状，最终新迁移/路由激活仍待完成。原生将撤去旧批准写授权，验证准确旧结果、干净未找到、原始报废 key 和另一操作者找回 key 均不能成为旧请求的干净未找到。

已补两代**实际物理找回**在完整服务与 registrar 返回后、COMMIT 前注入执行人版本变化/保管到期；必须由找回权限函数拒绝并恢复全库快照。这是 owner 注入的晚变化验证，不是声称 API 能改身份，也不是自然到期或双连接撤权竞争证明。

已修正 `seal_retention`：第一轮旧纠正与旧永久封存均实际 COMMIT 后才安装全部候选，安装点必须有四个旧绑定；保留每个原字段、准确旧封存结果及迟到写入拒绝。`after_seal` 比较安装前完整字段投影，新增 nullable 字段不误判为旧历史改写。完整旧三轮回归也使用新私有查询组合。以上新增范围均待原生终态。

活跃句柄：**37035** 新业务 quantity（`scrap-recovery-closure-quantity-v1.log`）；**15070** 新业务 serial（`scrap-recovery-closure-serial-v1.log`）；**30038** 升级前封存（`scrap-pre-upgrade-seals-v1.log`，依次 quantity/SN）；**4112** 完整旧三轮（`scrap-registry-legacy-generations-v1.log`，依次 quantity/SN）；安全 **29460 exit=0，2452 文件通过**（`scrap-recovery-closure-safety-v1.log`）。均位于 `artifacts/formal-baseline-audit-20261002-0164/`。`scrap-recovery-closure-candidate.json` 保存新源清单及句柄。四个 native 全部终态前冻结非 Markdown 源，保留原进程、不因观察超时重启。语法及 diff check 已通过；没有无故重跑未受影响的 52 项 Python 聚焦测试。

当前中途实证：两组新业务均已通过第一代实际恢复的晚身份/保管变更 COMMIT 拒绝及全库回滚。30038 内 quantity `run-93e6eljj` 已 passed/stopped/serverExitCode=0/sourceDrift=[]，真实旧 seal 在升级前已提交，四个旧 key 绑定、359 个旧函数 OID、4015 个旧触发器、782 个旧 FK 和准确封存结果保留，迟到写入拒绝；该句柄仍继续 SN。完整旧三轮及新业务其他部分仍待各自终态，不能提前合并为全通过。

下一实现设计已记录于 [报废与找回请求永久封存](STOCK_SCRAP_REQUEST_SEAL_DESIGN_AFTER_0164.md)：六类准确来源锚点、数据库拥有的 seal/key provenance、原始无 root 时不造假、当前授权、双向竞争及回查矩阵。此文是待实施设计，尚未新增 seal 持久表或入口；源仍冻结。安全2452文件的运行早于新增此设计文档，最终提交前仍需按最终工作树取证。

升级前封存完整终态已收：**30038 exit=0**，quantity `run-93e6eljj` / serial `run-6ei3jkal` 均 passed/stopped/serverExitCode=0/sourceDrift=[]，实际 source manifest 与当前源一致。两组均先提交旧 seal（四个旧 key 绑定）再升级，旧全部字段及359函数OID/4015触发器/782FK保留，14次前驱篡改拒绝、三个私有API调用拒绝，封存准确回查及迟到写入回滚通过。机器回执 `scrap-pre-upgrade-seals-current.json`。此句柄已终态，不再轮询；37035/15070/4112仍运行，仍禁止修改非Markdown源。前文30038继续SN为历史进度，已被本段取代。

完整旧三轮补充中途证据：4112 内 quantity `run-hyvig0em` 已 passed/stopped/serverExitCode=0/sourceDrift=[]，候选安装后两轮旧业务及9个历史请求/原始结果回查、晚撤权、封存及迟到写入回滚通过；源码与本轮候选相同。4112仍继续SN，37035/15070仍在第二代找回，不把单组成功写成总门禁完成。所有已完成子库已核验并收录 `scrap-recovery-closure-candidate.json.completedParts`。

## 最新终态：真实请求键登记完整数量/SN通过

**40730 / 18486 均 exit=0**；quantity `run-q9a6uvvi` / serial `run-09urhklt`，stopped / passed / serverExitCode=0 / sourceDrift=[]，源码在收据时与当前源一致（随后才增加本节新范围）。`scrap-raw-bindings-current.json` 保存全部结果与源码：每组原始报废及三段找回登记、两代实际业务、18 次新绑定非法事务拒绝、6 次 API 直接写/私有调用拒绝、7 个新绑定保留和 3 次 owner 修改/删除/清空拒绝；旧三个 aliases 保留。新→新、新→旧、旧→新原始 key 复用均有准确阶段拒绝及全表回滚证据。每组完整回查 8 个找回和 2 个报废原请求，8+2 个未找到禁重发、16+6 次篡改拒绝，撤写权限后 fresh API READ ONLY 全库快照不变。

聚焦38393/4144合计52通过，安全7437通过2449文件。v1数组类型失败已由显式text[]转换修复，并由v2真实事务证明；保留v1失败库/日志，不再轮询这些旧句柄。下方“当前进行”及待补三点为历史阶段，以上方新范围为准。仍未提交、推送、部署，正式迁移/Base/ACL/readiness、新永久封存与竞争、批次/共享矩阵、客户端/UAT/CI及其他基线上线条件仍待完成。


## 当前进行：真实请求键统一登记（新增候选，待原生终态）

原始报废和三段找回复核已增加 `stock_scrap_request_key_bindings`：真实 client key 经受控数据库 registrar 计算共同 token 与五个真实动作 hash；API 只能 SELECT，不能直接写绑定。新旧登记表、24 类请求事实及库存交易/收发事实增加 deferred 交叉检查；旧三个 aliases、旧登记行和旧函数体不改。原始报废与审批事务使用 `bound_commands.original/review`；只读 lookup 同时核对新登记的原操作者、来源、事实、时间和全部 hash。当前仍是私有候选，正式 Base/revision/readiness/永久封存尚未完成。

当前 v2 源已冻结：数量原生 **40730**、SN 原生 **18486**，日志 `scrap-raw-bindings-native-quantity-v2.log` / `scrap-raw-bindings-native-serial-v2.log`；安全 **7437 exit=0，2449 文件通过**，日志 `scrap-raw-bindings-safety-v2.log`。两原生句柄仍待终态，已完成原始报废、第一次物理找回及独立纠正批准；这也越过旧审批复用新找回 key 的 COMMIT 拒绝场景。文件位于 `artifacts/formal-baseline-audit-20261002-0164/`；`scrap-raw-bindings-candidate-v2.json` 固定修复后的源码摘要。语法及 diff check 通过；两 native 结束前禁止修改非 Markdown 源，不能因观察超时重启。

聚焦 **38393 exit=0，36 passed**（313.31s），新增登记破损聚焦 **4144 exit=0，16 passed**（177.61s）；安全 v1 **59933 exit=0，2449 文件**。首次原生 **58566 / 22554 均 exit=1**：完整迁移、期初及原始报废独立终审通过，服务内 registrar 的数组交叉比较出现 `varchar[] && text[]` 类型错误；数量 `run-gdsqqalo` / SN `run-m1mo7ux9` 均 stopped / checks failed / serverExitCode=0，失败证据保留。修复只涉及 `scrap_bindings.sql` 的显式 `::text[]` 转换，Python 源和已通过 52 项聚焦范围未改变；不重复这 52 项测试。v1 回执已写真实终态，不再轮询旧句柄。

本轮原生新增：原始/三段审批漏登记 COMMIT 拒绝、错误真实 key 拒绝、新→新、新→旧及旧→新复用原始 key 拒绝，整库快照回滚；API 直接 INSERT/UPDATE/DELETE/TRUNCATE 与私有函数调用拒绝，7 个绑定保留及 owner 修改/删除/清空拒绝，最后仍跑两代真实业务与全请求 READ ONLY 回查。上述为本轮待验范围，不提前宣称通过。后续需完整候选旧业务/旧封存保留回归、并发跨登记竞争、旧 lookup 对新登记的结果未知判定，以及正式迁移和全部新请求永久封存。

原生终态之后的具体补齐点：

- 当前 `pg16_scrap_forward_history_gate.py --scenario seal_retention` 的安装钩子仍在第一轮纠正之后、seal 之前，**不证明升级前已有 seal 的保留**。应在 seal 已 COMMIT 后安装候选，保留四个旧绑定（原三种事实 + seal），随后验证精确回查及迟到写入拒绝；不要仅改场景名称。
- 实际物理找回目前的全迁移负向只覆盖漏登记/错 key，不能拿原始报废的 late authority 场景代替。应在两代真实找回服务及登记之后、COMMIT 之前撤销执行人版本/保管有效性，核对对应找回当前权限函数拒绝及全库回滚。
- 旧 `stock_loss_corrections/bound_recovery._read` 只读取旧登记，尚未组合新登记的冲突判定。新正式入口激活前必须覆盖旧查新键、跨操作者同 raw key 的 unknown；不得把旧 not_found 当作可重发。

## 已收终态：原始/纠正报废与四阶段找回组合回查

**88589 / 63260 均 exit=0**，数量 `run-bgp8tcad`、SN `run-qt7fqfzo`，均 stopped / passed / serverExitCode=0 / sourceDrift=[]。每组先完成两代真实报废/找回，再撤去对应写权限，fresh API READ ONLY 回查 8 个找回与 2 个报废原请求。合计找回 16 found / 16 not_found / 32 conflict；报废 4 found / 4 not_found / 12 conflict，全部未找到均禁止重发，完整数据库快照不变。聚焦 **48894 exit=0，36 passed**；安全 **38369 exit=0，2443 文件**。机器回执 `scrap-all-lookup-current.json` 已核对当时完整源码摘要；它覆盖新增统一登记前的版本，不能替代本轮门禁。下节 running 为历史，勿再轮询上述已结束句柄。未提交、推送或部署。


## 当前进行：原始/纠正报废回查与找回回查组合

新增 `stock_scrap/request_lookup.py`：`ScrapRequestLookup` 完整原请求，准确原始终审或纠正决定来源、原操作者和当前总部 read 范围校验；组合全链历史、报废父/子/过账事件，不要求当前库存仍处于原报废。新旧坐标/孤立事件和两遍游标保护与四阶段找回共用。`lookup_coordinates.verify` 已扩展原始/纠正报废；纠正必须有精确 `scrap_key_hash` 数据库登记，原始仍以准确不可变父/子事实和实际 key 证明，**原始全局原始键登记仍待实现，不能称全局 raw-key 防重已齐全**。所有 not_found 均禁止重发；无新永久封存或公开路由。

聚焦 **48894 exit=0**，`scrap-all-lookup-focused-v1.log`：原始报废新测试与四阶段找回组合 **36 passed**（246.93s）。原生 **88589 quantity / 63260 serial**，日志 `scrap-all-lookup-native-quantity-v1.log` / `scrap-all-lookup-native-serial-v1.log`；安全 **38369 exit=0**，`scrap-all-lookup-safety-v1.log`，2443 文件通过。两原生库已完成完整迁移及期初盘点/入账，实际业务和组合回查仍运行。目录均为 `artifacts/formal-baseline-audit-20261002-0164/`，候选回执 `scrap-all-lookup-candidate.json` 保存当前源摘要。原生每组从完整迁移与两代实际业务开始，先回查 8 个找回原请求，再移除 dispose_loss/correct_loss 合成写权限，在真正 fresh API READ ONLY 中回查原始/纠正报废、两个干净未找到和 6 个篡改拒绝；原请求仅在进程内保留，未输出真实 key。两原生句柄终态前冻结全部非 Markdown 源，不重启、不要重新轮询下节已结束的 4729/24868。

语法检查及 diff check 已通过；尚未提交、推送或部署。候选正式迁移/Base/ACL/readiness、全部新请求永久封存与双向竞争、原始全局 key 注册、找回晚撤权、批次/共享原生矩阵和客户端/UAT 均仍是目标内缺口。


## 最新终态：四阶段找回原请求在 PG16 只读回查通过

**4729 exit=0**（数量 `run-4orjdy5t`）与 **24868 exit=0**（SN `run-zyor8c31`），均完成完整 0164 迁移、两代实际报废/找回后正常停库，checks passed / serverExitCode=0 / sourceDrift=[]。每组移除四种找回写授权后，fresh API `SET TRANSACTION READ ONLY` 准确回查 8 个申请/区域/总部/恢复原请求、8 个干净未找到（禁止重发）、拒绝 16 个内容/key 篡改；全库快照不变。合计 16 found / 16 not_found / 32 conflict。源码摘要与当前版本核对相等，机器回执 `scrap-recovery-lookup-current.json`；安全 **6301 exit=0**（2440 文件）。下节 running 状态为此前历史，不再轮询上述句柄。

接下来实现原始和纠正报废的原请求回查；再贯通全部新请求的永久封存、双向竞争与正式迁移/客户端验收。当前无新永久封存、公开 HTTP 或生产验收；未提交、推送、部署。


## 当前进行：四阶段找回原请求回查（2026-10-02）

新增私有 `stock_scrap/recovery_lookup.py` 和 `lookup_coordinates.py`：完整原申请/区域/总部/执行请求，校验原操作者与当前 read 范围权限；不借写权限、不要求历史阶段仍为当前、不执行库存写入或通知重放。组合真实全链历史、原事件及专用执行 key 绑定；扫描新旧请求坐标/旧封存、审计、状态、Outbox、通知和孤立证据；前后两遍读与账本/审计游标一致，`not_found` 仍 `retry_allowed=false`。不提供新永久封存或公开 HTTP，也尚未实现原始/纠正报废本身的 lookup。

聚焦 22 场景已通过：**82763 exit=1** 前 16 项通过后，孤立 outbox 测试漏填 `available_at` 导致夹具失败；修正后 **35035 exit=0** 剩余 6 项通过、16 deselected，保留两份日志。首次启动漏 `PYTHONPATH=backend` 的收集失败也保留在 v1 日志，不是依赖丢失。覆盖两类物料的三阶段历史回查、未找到禁重发、篡改内容/key/request、当前只读权限、身份/拒绝范围、孤立事件及游标变化；真正物理恢复的只读绑定验证在原生门禁中进行。语法与 `git diff --check` 通过。

当前真实原生句柄 **4729（quantity）** / **24868（serial）**，日志分别 `scrap-recovery-lookup-native-quantity-v1.log` / `scrap-recovery-lookup-native-serial-v1.log`；安全扫描 **6301 exit=0**：`scrap-recovery-lookup-safety-v1.log`，2440 文件通过。两原生库已完成完整 0164 迁移及期初实盘复核入账，业务/回查仍在运行。路径均在 `artifacts/formal-baseline-audit-20261002-0164/`。每个原生库实际完成两代业务，再删除仅该合成库四类写授权，用 fresh API `SET TRANSACTION READ ONLY` 回查 8 个原请求、8 个干净未找到和 16 个篡改拒绝，比较全库快照。两个原生句柄终态前，不改非 Markdown 源、不重启已有运行。候选回执 `scrap-recovery-lookup-candidate.json` 保存源码；下节两代通过是本次 lookup 加入之前的版本，不可替代本轮结果。没有提交、推送、部署或生产业务调用。


## 最新终态：两代报废/找回及完整候选旧回归通过（2026-10-02）

**37209 exit=0**，`scrap-forward-all-legacy-v2.log`：数量 `run-4x1emq6r`、SN `run-eqknxzko` 均 stopped/passed/serverExitCode=0。每组先实际执行一轮旧业务、留下三个数据库请求绑定，再安装全部候选；后续两轮旧业务、九个历史请求回查、晚撤权回滚、新永久封存及迟到写入拒绝通过。保留 359 个旧函数 OID、4015 触发器、782 外键、全部升级前字段值；13 个最终函数前向替换、14 次篡改前驱拒绝、3 个 API 私有调用拒绝。迁移前封存保留尚未覆盖。首次 **59202 exit=1** 的夹具把新增空列误判为历史修改，已改为比较升级前全部字段；失败库 `run-_uayube1` 已 stopped/serverExitCode=0，保留失败证据。

**69772 exit=0**，`scrap-business-generations-v1.log`：数量 `run-dxv3bocq`、SN `run-9tkt3mv7` 均 stopped/passed/serverExitCode=0。每组实际完成原始报废、独立三段找回复核和第一次恢复、独立重新审批、纠正报废及第二次三段复核/恢复；两组共 8 次库存提交、14 次不动库存的审批提交。18 个负向事务（10 次 COMMIT 拒绝、8 次 registrar 拒绝）全部快照回滚，覆盖原始报废晚身份/保管变化、两次找回漏绑定/错 key、纠正报废漏绑定/错 key/复用审批 raw key。没有覆盖物理找回末尾撤权，不得混淆。

新增 `scrap_key_hash` 与先前 `recovery_key_hash` 保留原三个 legacy aliases/key_token 语义；`bound_commands.correct/recover` 在实际服务事务内通过受控 registrar 绑定真实 client key。新两代完整历史和第一代不可变事实保留，原纠正审批 lookup 在后继操作后仍准确。两份机器回执 `scrap-forward-all-legacy-current.json`、`scrap-business-generations-current.json` 保存完整源码摘要，记录时四库源码一致无漂移。摘要范围为 backend/edge_sync/scripts/deployment、alembic.ini、PG16 workflow，排除 Markdown 和 frontend。安全 **87144 exit=0**（2436 文件），`git diff --check` 通过。

上述两句柄均终态，不再轮询、不重跑同版已通过矩阵。接下来开发新找回四阶段原请求只读恢复：完整命令、原操作者、当前只读权限、历史结果及孤立证据检查；未找到也不允许重发。新请求永久封存、原始/纠正报废回查、找回晚撤权、并发矩阵、正式 registry/Base/revision/readiness/降级、HTTP/PC/H5 及准确 SHA 的 CI 仍待完成。未提交、推送或部署。

## 最新接续：第一代报废与独立找回实际 PG16 提交通过（2026-10-02）

本节为当前终态，下文保留此前阶段的历史证据。使用原工作树和原分支，HEAD 仍为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`；未提交、推送、部署，也未连接生产/外部业务系统。

新增 `pg16_scrap_business_recovery.py`，在完整 0164 前驱迁移及候选守卫下，通过真实 API 服务依次提交原始报废、工程师找回申请、独立区域复核、独立总部批准、准确库存反向恢复。每次审批后完整库存/SN 快照不变；恢复仍进入原冻结账户，保留原报废历史。仅合成库补专用权限和候选表授权；移除合成 admin 的旧 `reverse_loss` 权限后，找回仍凭专用 `execute_scrap_recovery` 权限完成。

首次 **38814 exit=1**（`scrap-business-recovery-v1.log`，库 `run-8ws2460h`）：三段审批均提交，恢复在 COMMIT 被旧两条审计事件假设拒绝，事务完整回滚。新增 `request_coordinates.sql`，准确限定父单、子单及唯一过账三组事件，并检查旧/新命令碰撞；`forward_recovery.py` 前向接入专用当前权限和受控反向证明，旧 seal 规则保留。第二次 **23163 exit=1**（v2，库 `run-1tumhm0z`）：已通过新的当前/审计证明，随后被原数据库拥有的请求绑定门禁拒绝，完整回滚。两失败库均正常停止，失败日志原样保留。

新增 `recovery_bindings.sql` / `forward_recovery_bindings.py`：旧 registry 追加 nullable `recovery_key_hash`，不改原三个 legacy 键值/含义及共同 key_token 唯一保护。registrar 从真实 client key 计算专用找回键，校验与实际反向事实一致；四张找回表追加碰撞 fence，只有准确找回子事实可与父反向共享键。`stock_scrap/bound_commands.recover` 在同一事务组合服务和注册，调用方提交，没有公开路由。该 registry 新列还需纳入最终结构编译、正式 Base/迁移/ACL/目录，不能认为候选 SQL 已完成正式发布。

最终 **33955 exit=0**，`artifacts/formal-baseline-audit-20261002-0164/scrap-business-recovery-v3.log`：数量库 `run-jqozlz1r`、SN 库 `run-wh6jlzh2` 均 stopped/checks passed/serverExitCode=0，运行期间源无漂移，两份源码摘要与当前工作树核对相等。两组共 4 次新库存实际提交（各原始报废及找回）、6 次不动库存的找回复核提交；4 个原始报废 owner 晚撤权/保管到期在 COMMIT 拒绝，2 个 API 漏绑定在 COMMIT 拒绝，2 个 API 错误 client key 在注册阶段拒绝，全部失败事务完整表快照不变。SN 从 active→scrapped→active，恢复准确冻结账户、最后流水及完整数据库生命周期重放通过。不得把原始报废的晚撤权测试称为找回晚撤权测试。

安全扫描 **55072 exit=0**，`scrap-business-recovery-safety-v1.log`：2435 文件通过；`git diff --check` 通过。机器回执 `scrap-business-recovery-current.json` 保存完整当前源摘要、两个成功库、失败证据和准确限制。所有本节 native/safety 句柄均终态，无需重新轮询或重复已通过矩阵。

下一步：把本次扩展纳入旧请求绑定/封存的原生回归，再贯通纠正后报废与多代找回；补原始/纠正报废及找回复核/执行的完整请求 lookup、孤立结果未知判定、永久封存和竞争。随后验证晚找回撤权/责任变化、批次/共享/并发完整矩阵，落实正式 revision/readiness/权限和降级保留、HTTP/PC/H5、准确 SHA 的 CI 与其余基线上线验收。旧 lookup/seal 尚不能读取或封存新的找回命令；不得以本轮首次物料找回成功宣称完整请求恢复或正式上线完成。

## 最新接续：新原始报废已进入完整 PG16 的真实 API 提交（2026-10-02）

新增 `pg16_scrap_business_gate.py` 和 `scripts/run_local_pg16_scrap_business_checks.py`，从完整 Alembic 0164、真实 API 期初盘点/报损/两级复核开始，运行新增报废服务；八张候选新表仅在本次私有测试库给予 API SELECT/INSERT，所有旧触发器/外键保持启用，未改生产权限。`pg16_loss_multigeneration_fixture.exercise` 增加准确批准来源的可选 continuation，默认仍执行原普通处置。

首次 **1006 exit=1**（`scrap-business-native-v1.log`）：服务已生成全套事实，但 COMMIT 在 `rsc_check_loss_current_normal_original_0159` 因报废无目标账户被拒绝。新增 `current_stock.sql` 和 `forward_business.py`，原始报废及共用 scrap 作业单转入专用当前事实证明：准确来源/原冻结账户、当前身份权限、完整历史及期初、冻结份额、唯一现行保管责任、物料/策略和硬冻结，保留原普通/退回分支。随后数量模式 **88555 exit=0**（v2），完整库实际 API 报废 **0.250** 成功提交并复查完整历史；库 `artifacts/local-scrap-business-pg16/run-5vvt7k4i` 正常停止且源无漂移。v2 尚未包含新增晚撤权用例和 SN 分支，不作为最新完整矩阵。

新增 `forward_serial.py` 与固定 `serial-predecessor.json`：前驱来自上述完整 0164 库，body SHA `334f3ca3d06af1d1dfe047b135d00859376c671c9f8140e9d56a2c56055415d9` 同时匹配现行 `database_security.py` 的独立预期。保留 0092+0093+0098 当前完整体，只增加有准确历史事实的 active→scrapped 及严格最后原报废反向→active 分支；后者尚待真实找回业务测试，不能因代码存在标通过。

v3 **15118 exit=1** 在安装 SN 函数时发现 PL/pgSQL IF 中 CASE 比较缺少括号，已修正；失败库正常停止，日志保留。最终 v4 **17085 exit=0**，`artifacts/formal-baseline-audit-20261002-0164/scrap-business-native-v4.log`，默认数量/SN 两组。新增服务返回后在同一合成 owner 事务中撤销 actor version 或令保管记录到期，要求 COMMIT 从专用当前证明拒绝并完整数据库快照不变；正常路径仍以真实 API COMMIT，复查准确冻结量和 SN scrapped/空位置/最后流水。数量库 `run-3m9gcwtt` 与 SN 库 `run-ivv644it` 均 stopped/checks passed/serverExitCode=0，4 个晚变更 COMMIT 拒绝及全库快照回滚、两个原始报废实际 API 提交均通过，运行期间源无漂移。修改下一批源之前核对两份源摘要等于当前工作树；回执 `scrap-business-original-current.json`。这仅证明原始报废，独立找回继续接入。

仍待完成：独立找回的实际 PG API 提交、纠正来源与多代新报废/恢复、新命名空间请求绑定及 lookup/封存、完整批次/共享/并发矩阵、正式 revision/readiness/权限目录、HTTP/PC/H5 和全部正式上线验收。当前权限 helper 含纠正/找回分支，但相应旧 dispatch/请求绑定尚未全接，不得称这些路径已通过。未提交、推送或部署。

## 最新接续：完整 0164 库上的历史前向补丁集成（2026-10-02）

新增 `stock_scrap_0165/history_bridge.sql` 和 `forward_history.py`。原始报废、纠正报废及找回反向通过准确事实绑定调用完整计划/流水/附件/执行事件证明，并组合原报损提交、区域/总部历史和已建立期初证明。对现行完整历史图、逆向计划、纠正计划、历史请求和事件五个函数生成准确 before→after；冻结的 0159–0164 文件保持原样。四份来源 catalog 固定 SHA，替换前逐个验证函数定义、属主、安全属性及 ACL，不允许覆盖未知前驱。

可复现入口：`.venv/bin/python scripts/run_local_pg16_scrap_forward_history_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin`。使用新建 Unix socket PG16、实际 Alembic 全历史升级到 0164，以及真实 API 期初/报损/审批/处置；随后安装候选组件和五个前向补丁，保留全部旧触发器和外键，执行三轮普通冲销→新审批→纠正处置、末尾撤权回滚、原请求只读回查和永久封存后的迟到写入拒绝。不是从 SQLite 导入账本。

最终证据：原生进程 **42130 exit=0**，日志 `artifacts/formal-baseline-audit-20261002-0164/scrap-forward-history-native-v1.log`。数量库 `artifacts/local-scrap-forward-history-pg16/run-kjp08rkj`、SN 库 `run-io9ekmg_` 均 stopped/checks passed/serverExitCode=0。每库 3 轮/9 个历史请求及原始结果、撤权回滚与封存拒写通过；合计 6 轮/18 个历史请求。每库 359 旧函数 OID、4015 旧触发器、782 旧 FK 保留，旧字段/行在结构与函数转换前后逐行相等；5 个被篡改安全属性的前驱均拒绝覆盖，API 调用新私有历史函数拒绝。两库源码摘要无漂移，终态摘要与当前覆盖范围内的非 Markdown 源一致。安全扫描 **72954 exit=0，2423 文件通过**，日志 `scrap-forward-history-safety-v2.log`；`git diff --check` 通过。所有句柄终态，不再轮询或重启旧任务。机器回执 `scrap-forward-history-current.json` 固定准确源、日志、库终态和验证限制。

明确边界：**本轮是完整旧迁移上的前向函数组合及旧业务 API 回归，新报废/找回分支尚未真实 PG COMMIT 验证**。新表仍空、API INSERT 仍拒绝，正式 revision/head/运行时准入未更新，也未注册 HTTP。不能因旧业务通过宣称新分支业务已通过；原生新增计划等组件证据仍沿用前节明确范围。

下一步直接做新业务贯通：当前处置/纠正 COMMIT 权限与无目标账户分支、SN active→scrapped→active 全历史、数据库拥有的准确新请求绑定，再执行真实 API 新报废/独立找回和多代矩阵。SN 当前函数虽名为 `rsc_check_serial_lifecycle_0092`，实际还包含 0093 换件及 0098 工单反向补丁，必须从该准确现行定义继续，不能回到 0092 基础体覆盖旧能力。旧 `rsc_register_loss_request_binding_0159` 的三种 `stock-loss:*` 动作 hash 不能直接冒充 `stock-scrap:*` / 找回命名空间；新父子事实共用请求的准确绑定必须先设计并验证，不能删除旧跨动作碰撞/封存保护来放行。随后补正式迁移/readiness、lookup/封存/HTTP/PC/H5、准确 SHA CI 和其余基线验收。未提交、推送或部署。

## 最新接续：报废/找回执行事件与原上传审计 SQL 通过（2026-10-02）

新增候选 `stock_scrap_0165/execution_evidence.sql`，组合已通过的完整原游标计划/共享冻结份额与流水证明。原始/纠正报废、严格找回逆向的业务父事件、库存过账事件、报废/找回子事件逐项核对 actor、时间、原请求、canonical payload、独立幂等键和审计链成员。每个实物事实只允许父事件保留一份准确收件人通知意图；子事件不能重复发通知。报废附件同时验证准确上传 manifest、原创建/完成审计、内容/HEAD 摘要和先上传后执行的时序，不能靠重算 metadata/hash 冒充原始上传事实。

15 个 deferred ALWAYS 触发器从业务事实、文件、审计链头和事件 OLD/NEW 引用反向核验，专用孤立子事件拒绝；五个私有函数属主、固定 search_path 和所有运行时 EXECUTE 拒绝已核验。未修改冻结的 0159 历史函数；新过账/域事件编码按原定义构建，后续须通过正式前向迁移组合。

版本化入口 `.venv/bin/python scripts/run_local_pg16_scrap_execution_evidence_checks.py --postgres-bin <PG16 bin> --fixture-directory artifacts/scrap-ledger-exports/run-o02bnzd6`。最终 **34266 exit=0**：数量/SN × 独占/共享四组，各 4 正常、103 拒绝；合计 **16 正常、412 拒绝（408 COMMIT、4 API statement）**。覆盖缺失/改挂/重复 outbox、canonical 数值类型或内容伪造、状态和创建时间、重算完整审计链后的错误业务/原上传内容、通知目标缺失/摘要错误、子通知重复及孤立事件。每次拒绝后完整数据库快照不变；通知重试字段及 expanded 状态可通过全部延迟约束，且无关事件在库存头被另一事务锁住时仍可完成约束校验。这两类正常修改校验后回滚保留原样本，未调用任何真实通知渠道。

四个准确测试库与源码摘要详见 `artifacts/formal-baseline-audit-20261002-0164/scrap-execution-evidence-current.json`，均 stopped/checks passed/serverExitCode=0、无源漂移，终态摘要与当前非 Markdown 源文件相等。v1（87587 exit=1）是状态伪造用例先触发通用 from/to 必须不同的约束，改为能进入专用 COMMIT 校验的非法状态后 v2 完整通过；没有修改或关闭该通用约束，失败库已正常停止。安全扫描 **55748 exit=0，2419 文件通过**，`git diff --check` 通过；日志 `scrap-execution-evidence-native-v1/v2.log` 和 `scrap-execution-evidence-safety-v1.log`。本轮所有句柄均终态，不重启或轮询旧句柄。

边界：这是完整模型约束与真实 SQLite 服务全表合成账本上的原生候选组件验证，未加载完整 0164 历史触发器目录，也未执行 Python 服务在 PG 上的新业务过账。owner 级破坏性事务用于证明内容校验；生产只追加、文件、审计和运行时 ACL 仍须随完整迁移保留。未连接外部存储、通知渠道或生产数据库，未提交、推送、部署。静态迁移声明核对仍为唯一 `20261213_0164` head，无缺失父修订；这不替代迁移执行验证。

下一步直接组合原报损提交/终审/期初、0159 原始/逆向/纠正完整历史和 0092 SN 全历史前向分支，保留旧拒绝直到完整迁移和真实 API 新业务 COMMIT 通过。随后补请求结果回查/封存、HTTP、PC/H5、准确 SHA CI，以及其余正式上线验收。完整上线目标保持 active，不能将本组件通过当成上线。

## 最新接续：原游标完整计划与共享冻结份额 SQL 已验证（2026-10-02）

新增候选 `stock_scrap_0165/historical_plans.sql`，原样组合冻结 0159 的 chain/hold projection，按每笔原交易前的游标重建原始/纠正报废及找回计划。核对来源批准、canonical 命令/摘要、父单上下文、唯一历史保管责任、完整余额/版本/逐行冻结份额、SN 前序/首次入账、附件元数据与历史策略指纹；不使用当前余额或今天权限替代历史。20 个 deferred ALWAYS 触发器覆盖相关单据、共享账户流水、物料策略、保管和附件的 OLD/NEW 引用；三个新增私有函数属主、search_path、运行时 EXECUTE 拒绝已验证。完整原报废上传审计与业务/过账事件、上游期初和 SN 全历史证明仍需组合，当前组件不授权上线。

真实服务两代流程扩展为数量/SN × 独占/共享四组；共享组在第一次报废之后、第一次找回之前另建真实待处理报损单。第一次找回、第二次纠正报废、第二次找回后均核对另一单的准确份额与 SN 历史不变。预检因新增冻结而变化时重新生成计划。**1181 exit=0，4 passed，197.62 秒**，四份完整账本导出到 `artifacts/scrap-ledger-exports/run-o02bnzd6`。此范围尚未覆盖“第一次原始报废之前就有另一张冻结单”，不可将该项提前标成通过。旧流水入口现在兼容两份或四份导出；未重复旧已通过矩阵。

原生入口：`.venv/bin/python scripts/run_local_pg16_scrap_historical_plans_checks.py --postgres-bin <PG16 bin> --fixture-directory <导出目录>`。最终 **58526 exit=0**，`scrap-historical-plans-native-v4.log`：14 组正常检查、166 个拒绝（78 COMMIT、88 statement），数量共享/独占分别 36/35 拒绝，SN 共享/独占 48/47 拒绝。正常检查包含每个原始/纠正事实的报废与找回计划、共享另一单从其冻结前到最后流水的逐游标份额，以及策略在全部操作之后到期仍保留原始计划。恶意余额/版本/游标/冻结份额/SN/类型/额外字段即使重算计划摘要仍拒绝；策略指纹四类伪造及私有 API 调用均拒绝。非法事务后完整表快照不变；四库均 stopped/checks passed/serverExitCode=0，源码摘要无漂移。

第一代的计划攻击显式调用准确历史检查并在 statement 拒绝，防止后代引用检查遮蔽目标；纠正代攻击到 COMMIT。报废攻击同时更新父计划和命令摘要，找回攻击只更新计划及计划摘要，保留被后代引用的原请求摘要；不可声称修改了全部历史后代或不可变子事实。v1/v2 失败是负向测试先被后代摘要/批准保护拒绝，已调整测试隔离，不弱化原保护；v3 基础矩阵通过，v4 增加历史策略关闭和指纹用例，次数不累加。所有失败和成功日志保留在 `artifacts/formal-baseline-audit-20261002-0164/`。

最终安全扫描 **32619 exit=0，2416 文件通过**；`git diff --check` 通过，四份最终非 Markdown 源摘要与当前工作树相等。本轮所有测试/导出/安全句柄均终态，无需重复轮询；两个失败测试库也均正常停止。

本门禁创建完整模型约束并导入真实 SQLite 服务的全表合成账本，**未加载完整 0164 迁移/旧触发器目录，也不是 Python 服务在 PG 上实际新过账**。未连接生产或外部业务系统，未提交、未推送、未部署。机器回执 `scrap-historical-plans-current.json` 记录准确源文件、日志、测试库终态和限制；完整上线目标继续 active。

下一步直接补原报废附件上传审计和父/子/过账事件证明，再组合 0159 原始/逆向/纠正历史与 0092 SN 全历史的前向分支；保持旧拒绝规则，直到完整 0164 前向迁移和真实 API 新过账矩阵通过。之后继续请求结果回查/封存、HTTP、PC/H5、准确 SHA 的 CI 和其余正式基线验收。

## 最新接续：实际服务完整账本的报废/找回流水校验通过（2026-10-02）

新增候选 `stock_scrap_0165/inventory_edges.sql`：原始/纠正报废必须绑定准确单笔交易和流水、原冻结账户/数量、独立外边界、操作者、业务键、时间及重新计算的 canonical 过账请求 hash。恢复执行和反向单的命令、计划及上下文逐项一致，并与原交易、原流水、独立批准严格绑定；不能借用其他反向或附带额外流水。SN 集合必须等于原报损行、报废流水及恢复流水，原首次入账与准确前一流水保留，恢复前最后活动必须是被恢复的报废。校验使用各自历史游标，不依赖当前 SN 缓存或今天的权限。

报废和恢复各自时点均验证历史数量精度、是否允许小数、唯一有效追踪策略、批次要求与 SN 数量。13 个 deferred ALWAYS 触发器覆盖业务事实、流水、SN 关联、账户、原报损 SN 和物料策略，反向检查 OLD/NEW 坐标及相关历史 SN；孤立报废边界交易不能绕过。三个私有函数属主、search_path 和 API/其他运行时 EXECUTE 拒绝均核验。**这是流水边界组件，尚不替代完整冻结份额、原游标计划、上游批准/期初/审计和全链生命周期证明。**

新增 `scripts/export_scrap_ledger_fixtures.py` 调用真实本地业务服务运行两代“原始报废→独立找回→新批准→纠正报废→独立找回”，在内存 SQLite 导出全表合成快照，不访问生产或外部系统。**22512 exit=0，2 passed，89.59 秒**；导出目录 `artifacts/scrap-ledger-exports/run-fv8d6wm1`。原生入口 `scripts/run_local_pg16_scrap_inventory_edges_checks.py --postgres-bin <PG16 bin> --fixture-directory <该导出目录>`；新建完整模型约束库导入真实服务快照，数量 64 张有数据表/298 行，SN 71 表/337 行，全部字段在导入后逐行相等，没有最小库存父表替代。审计链头在单一导入事务中先建立空头再接回准确原事件，最终完整原值不变；所有约束保留。

最终 **16534 exit=0**，`scrap-inventory-edges-native-v4.log`：数量两种来源正常、49 拒绝（46 COMMIT）；SN 两种来源正常、63 拒绝（60 COMMIT）；各含独立策略精度拒绝和 API 私有调用拒绝。非法事务后完整账本快照不变。数量库 `artifacts/local-scrap-inventory-edges-pg16/run-bqvm6yoa`、SN 库 `.../run-tltcv2ek` 均 stopped/checks passed/serverExitCode=0，全非 Markdown 源摘要无漂移。原生库使用完整模型结构及本批组件，**没有加载完整 0164 迁移/旧触发器目录，也不是 Python 服务在 PG 上实际新过账**，不得称正式迁移或完整业务门禁通过。

失败证据保留：v1 因审计头/事件的即时外键循环导致导入失败，改为按准确非延迟行引用拓扑导入，不关闭外键；v2 暴露新 SQL 把无追踪枚举写成 quantity，已按正式模型修为 none；两失败库均正常停止。v3 基础流水检查已通过，两库停止，之后 v4 新增完整历史策略检查覆盖其结果，数量不累加。日志均位于 `artifacts/formal-baseline-audit-20261002-0164/`。

安全扫描 **87436 exit=0，2413 文件通过**；`git diff --check` 通过。机器回执 `artifacts/formal-baseline-audit-20261002-0164/scrap-inventory-edges-current.json` 固定五份新增源文件、导出样本、全部失败/成功日志及最终两库源摘要。所有本轮句柄均终态，不轮询或重启旧句柄。

下一步直接补完整原游标计划/共享冻结份额与报废原始命令及事件证明，再将 `rsc_check_loss_history_graph_0159`、原始/逆向/纠正计划和 SN 全历史检查通过前向补丁接入新分支。保留现有拒绝，直到全组件在完整 0164 前向迁移及真实 API 过账验证通过；随后接原请求恢复/封存、HTTP 和 PC/H5。本批未提交、未部署，完整上线目标保持 active。

## 最新接续：真实找回事实表的来源与权限准入已组合（2026-10-02）

新增私有候选 `backend/alembic/stock_scrap_0165/recovery_admission.sql`。由准确报废行、原报损行/单、原始或纠正执行、原冻结账户和历史保管记录推导资产所属组织、库位、申请人及原请求摘要；不接受调用者提供范围。核验数量、SKU、成色、冻结状态、原决定、报废单头、流水引用、执行时间及空目标/无退回绑定。四个实际找回事实表各有 BEFORE 和 deferred COMMIT 准入，遵循库存头→主体图→库位锁顺序，只验证本次 NEW 操作的当前权限，不重新授权历史审核人。

版本化入口 `scripts/run_local_pg16_scrap_recovery_admission_checks.py --postgres-bin <PG16 bin>`，组合既有审批图、附件、审计/通知事件和当前权限 SQL。初次原生执行 **85950 exit=0**：10 个正常场景，89 个拒绝（81 statement、8 COMMIT），失败事务完整快照不变；两种来源 × 四阶段均验证准确命令及末尾撤权。原申请人撤权后，其已提交申请仍可由有权区域负责人继续复核；API 直接调用来源私有函数被 42501 拒绝。8 个新增 ALWAYS 触发器和私有函数属主/search_path/ACL 均核验。

准确库 `artifacts/local-scrap-recovery-admission-pg16/run-20ij4bbc` 已 stopped/checks passed/serverExitCode=0，非 Markdown 源摘要无漂移。日志 `artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-admission-native-v1.log`。测试使用真实候选找回事实/文件/事件表、完整身份与权限/组织/保管表定义；**库存父表仍是明确最小列夹具，执行测试中的反向关联不是统一库存过账。不能替代完整 0164 新报废/恢复库存与 SN COMMIT、正式迁移或上线门禁。** 没有删除或关闭旧业务拒绝规则。

原审批/附件/事件默认夹具兼容回归 **65843 exit=0**，5 组正常历史、133 个拒绝（118 COMMIT、15 statement）、两组并发、投递状态及无关事件锁范围均通过。准确库 `artifacts/local-scrap-recovery-approval-pg16/run-karxemwo` 正常停止、无源摘要漂移；日志 `scrap-recovery-approval-admission-regression-v1.log`。仓库安全 **80831 exit=0，2408 文件通过**；日志 `scrap-recovery-admission-safety-v1.log`。本轮所有句柄终态，不重复轮询；机器入口 `artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-admission-current.json` 固定准确源字节和终态日志。

当前来源准入已接候选事实表，下一步应直接补原始/纠正报废及找回逆向的库存与 SN SQL 证明，再组合正式迁移、请求恢复/封存与客户端；不要退回只测试 probe 权限。本批未提交、未部署，完整上线目标保持 active。

## 最新接续：第二静态分片的 5 项失败已本地修复（2026-10-02）

`test_return_receiving_h5_contract.py` 四份旧样本已在本地复现失败（80355 exit=1）。新增版本化生成入口 `scripts/export_return_receiving_fixtures.py`、显式导出夹具及共用服务生成器，使用独立内存 SQLite 和真实验收/预览/入库/回查服务，在新 artifacts 目录生成四份当前样本；不访问现有库或外部系统，不直接覆盖前端文件。**4194 exit=0，4 passed**，输出目录 `artifacts/return-receiving-exports/run-w39wdati`。核验成功后更新四份前端样本，旧四份保存在 `receiving-fixtures-before/`；一份旧序列报损样本另在前端版本化保存，专门验证 v1 原请求摘要及已过账结果回查。未手改 schema_version 或伪造 plan hash，未放宽当前后端 2.0 预览 schema。

0106 历史迁移测试仍证明原始 FUNCTION_HASHES 与函数源码相等，同时通过连续后继补丁验证当前摘要；没有修改历史迁移或运行时注册表。完整该测试文件 **77546 exit=0，12 passed**。当前后端 H5 合同 **27857 exit=0，4 passed**；前端退回接收/验收/独立入库/恢复/adapter/页面/路由 **94222 exit=0，7 文件、93 passed**；完整 TypeScript 检查 **80379 exit=0**。协议导出为合成 SQLite 服务输出，仍不能替代最终实际 PG 客户端协议和业务提交门禁。

本节日志均在 `artifacts/formal-baseline-audit-20261002-0164/`；机器入口 `receiving-ci-fix-current.json` 固定本次源文件与已终态日志。安全扫描 **23464 exit=0，2405 文件通过**，日志 `receiving-h5-safety-v1.log`。本轮所有进程均终态，准确源摘要无漂移，`git diff --check` 通过；不重复轮询旧句柄。GitHub 旧 run 已终态，两个静态分片的共 10 个断言失败均已有本地修复证据；三个运行器中断/失联任务保持独立失败事实。未提交新 SHA、未重新获得 GitHub 通过、未部署。

下一开发步骤仍为真实报废/找回准入来源绑定、库存及 SN SQL 证明、正式迁移与请求恢复；当前权限组件终态见下节及 `scrap-recovery-authority-current.json`。禁止因为测试修复通过而删除旧拒绝规则或将完整上线目标置为完成。

## 最新接续：完整 0164 库的找回当前权限组件通过（2026-10-02）

`stock_scrap_0165/recovery_authority.sql` 已通过真实完整 0164 PostgreSQL16 身份/角色/授权/组织/库位/保管表验证。四阶段分别要求当前有效的本人 technician、准确区域 provincial_manager 或总部 admin；允许必须来自指定角色的同一范围授权，匹配 deny 优先，组织/身份/授权版本/保管责任失效拒绝。申请、区域及总部的历史图不调用当前权限校验；旧审核人后来离职不撤销历史事实。

可复现入口：`.venv/bin/python scripts/run_local_pg16_scrap_recovery_authority_checks.py --postgres-bin <PG16 bin>`。只新建独占 Unix socket 库，不接受 DSN。最终 **91093 exit=0**，日志 `artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-authority-native-v3.log`：6 个正常场景、103 个语句阶段拒绝、16 个 owner 同事务末尾变更的 COMMIT 拒绝，以及 2 个真实 API 授权/保管到期 COMMIT 拒绝。另用真实 pg_locks 证明等待先发生的撤权并重读拒绝；相反顺序下 API 持有锁时，另一个事务不能撤权或插入 FK 绑定的新保管关系，API 正常提交。借用其他角色 allow 的用例先证明通用 allows 为真，再要求指定角色校验拒绝。直接 API EXECUTE 返回 42501。

准确库 `artifacts/local-scrap-recovery-authority-pg16/run-yu91kw9q` stopped/checks passed/serverExitCode=0，全非 Markdown 源摘要无漂移，原 public 函数定义不变、无库存交易。测试开始先通过完整启动/ACL 检查。**这是测试专用 probe schema 的 BEFORE/deferred 权限组件证明，未将来源绑定安装到真实报废/找回表，也不是新库存业务 COMMIT 或正式迁移完成。** 下一步要从准确来源事实推导 helper 输入，组合库存计划及 SN 生命周期证明。

v1 完整迁移成功后因驱动把 `%ROWTYPE` 当参数占位符安装失败，已改用 SQLAlchemy text 编译，不改权限 SQL；失败库正常停止。v2 基础 6/79/16 已通过且停库；与最终 v3 重叠，不累计。安全扫描 **15596 exit=0**；机器回执 `scrap-recovery-authority-current.json` 固定源码、各轮终态和证据摘要。

GitHub 原 SHA 的 run `36931116983` 已全部结束：61 success、6 failure（含最终汇总门禁）。static_safety(2) 新确认 **5 failed、3081 passed**：四份 H5 入库样本仍为 schema_version 1.0，而当前预览要求 2.0；0106 迁移测试仍把原始函数 hash 当当前值。日志 `github-static-2-failure.log`。之前 static(1) 五项已本地修复，330 回归不覆盖本次新发现的五项。接下来修 H5 样本生成和该历史断言，保留旧失败证据，不盲重跑已知代码失败。所有本轮本地句柄终态，无提交、无部署，完整目标 active。

## 最新接续：CI 目录断言修复，330 项本地回归通过（2026-10-02）

GitHub run `36931116983` 的 static_safety(1) 已确认 **5 failed、3168 passed、2 skipped**，不能继续归因于运行器中断。5 项均已在本地准确复现（78205 exit=1，`ci-catalog-repro-v2.log`）。另一个 static_safety(0) 的官方 annotation 为 `The hosted runner lost communication with the server`，日志下载 404；该项与上述断言失败分开记录。两个 PG runtime 仍保留此前 runner shutdown 证据，不把其取消步骤算作业务断言失败。

本次只修测试及测试辅助函数：ACL 增量清单补齐 0161 两张封存表和 0163 退回停止表；触发器增加 37+1+10 项，并逐项核对独立迁移目录的表、函数、ALWAYS、类型和延迟属性，未仅更改数量。0052 的账户函数原始证明仍保留，历史辅助函数改为追踪后续全部 `_sources`；0152 同时验证原版本摘要与当前版本摘要。辅助函数统一参数逗号空格但保留 schema 和重载，逐级断言前驱源码连续性，拒绝重复等价签名。原先 `uuid,boolean` 与 `uuid, boolean` 的差异会漏查 0163 替换，现已补负例验证。生产安全注册表、函数、迁移及授权均未因此修改。

聚焦 **8 passed，68537 exit=0**（原 5 项和 3 项辅助函数边界测试），仓库安全 **2397 文件通过，20896 exit=0**。初次复现命令缺 PYTHONPATH 导致 collection error，保留 `ci-catalog-repro-v1.log`；正式复现/修复验证均使用 `PYTHONPATH=backend:scripts`，不能把初次命令错误混成代码缺依赖。

完整 `test_database_security.py`、`test_stock_loss_derived_return_migration.py`、新辅助函数测试以及受影响的历史迁移源码链断言已 **330 passed，4 个既有弃用警告，35239 exit=0，246.65 秒**。日志 `artifacts/formal-baseline-audit-20261002-0164/ci-catalog-regression-v1.log`，覆盖 8 项聚焦用例，数量不能累加。机器入口 `ci-catalog-fix-current.json` 已封存全部终态和日志摘要，四份修复源文件无漂移；本轮进程均结束，无需继续轮询旧句柄。`git diff --check` 通过。本地修复尚未提交，GitHub 原 SHA 的失败结果不能写成通过。 最后真实回读仍为 `fc7c926` 的 61 成功/4 失败/1 运行，保留 `github-status-after-catalog-fix.json`；未重启活动任务。

后续回到 `backend/alembic/stock_scrap_0165/recovery_authority.sql`：该候选已有当前身份、分阶段同角色权限、deny、范围和保管责任校验代码，**尚未测试**。须用完整 0164 原生库验证后再组合新业务准入、库存/SN、正式迁移和请求恢复。本批没有提交或部署，完整上线目标 active。

## 最新接续：找回附件与事件束的 PostgreSQL 证明已合并（2026-10-02）

新增 `backend/alembic/stock_scrap_0165/recovery_evidence.sql`，并接入上一轮审批图校验。找回附件校验原申请人/人员/授权版本、用途/提供方、准确文件/存储键、尺寸/MIME/扩展名、完成时间、规范化上传意图及绑定摘要；通过保留的 0159 审计链证明核对原上传创建和完成审计，完成审计必须早于申请审计。HEAD 摘要绑定原完成审计，允许提供方附加元数据，**不猜测其完整 HEAD 内容，不声称重新访问 OSS 或验证了真实生产对象**。

申请、区域及总部逐条重建准确审计、状态、outbox、通知和唯一目标人员。反向事件触发器同时检查 INSERT 和 UPDATE 的旧/新坐标，拒绝无申请的孤立事件、借换 aggregate 绕过和删除历史；不把投递失败当成审批失败。拒绝使用审批幂等键或审批单据身份附带库存交易。7 个函数属主/search_path/ACL 与 26 个 ALWAYS 触发器（10 个 deferred）均核验，仍仅为私有候选，未注册正式 revision 或生产 grants。

原生 PostgreSQL 最终终态：`artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-evidence-native-v3.log`，**98980 exit=0**。5 组正常审批历史、**133 个非法场景拒绝（118 COMMIT、15 statement）**，其中新增附件/事件/附带过账攻击 **77 项**；两个同报废/跨表同键并发仍各一笔成功。投递状态更新保持审批不变、无关 outbox 在库存头被另一事务锁定时仍能提交。准确库 `artifacts/local-scrap-recovery-approval-pg16/run-__2e927n` stopped/checks passed/serverExitCode=0，全非 Markdown 源摘要无漂移。仓库安全扫描 **83970 exit=0，2395 文件通过**；`git diff --check` 通过。

保留问题与修复证据：v2 的通知删除攻击先被目标表 FK 拒绝，调整为同一合成事务先删目标再删通知，验证真正 deferred 禁删守卫。独立原生复现发现 JSONB 将整数 `1` 和小数 `1.0` 视作相等，旧候选接受了变形载荷；已改为规范化文本比较，`scrap-evidence-number-before.log` 记录失败，`scrap-evidence-number-after.log` 记录修复后拒绝。最终矩阵分别覆盖通知、outbox、状态、重新计算审计 hash 的数值变形；旧 v1/v2 与最终数量不累加。

机器接续入口：`artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-evidence-sql-current.json`，可复现命令沿用 `scripts/run_local_pg16_scrap_recovery_approval_checks.py --postgres-bin <PG16 bin>`。文件/事件表使用真实列和约束，但原库存父表仍是最小夹具，上传/审计为合成数据。**不等于真实 0164 全迁移或新报废/找回库存 COMMIT**；既有文件/审计准入触发器仍须在正式迁移中原样保留并验证。下一步为原报废及反向计划、当前权限、SN 生命周期 SQL 证明，再组合前向迁移/运行时准入、请求回查/封存及 HTTP/PC/H5。旧 0159/0163 拒绝未删除，本批未提交、未部署，完整上线目标 active。

GitHub 同 SHA `fc7c926` 的 run `36931116983` 本轮真实回读仍 61 成功、3 static 运行、2 失败；未重启活动任务。准确观察保存 `github-status-during-recovery-evidence.json`，不得写成新候选 CI 已通过。

## 最新接续：找回审批的 PostgreSQL 提交保护已实现并验证（2026-10-02）

新增候选 `backend/alembic/stock_scrap_0165/recovery_approval.sql`：严格重建原始/纠正报废来源的找回申请、区域及总部命令，核验原请求 hash、申请人与两级审核人的独立性、时间顺序、总部退回后的新区域复核、补证关闭后的新申请、准确最终批准及跨四阶段请求坐标唯一性。申请附件必须与规范化命令中的有序 ID/绑定时间一致；**文件内容/完成上传证明仍需后续专用 SQL 校验，不能把 ID 绑定当作文件有效性**。五个 deferred INSERT 触发器在同一库存头锁下复核；八张新表 UPDATE/DELETE/TRUNCATE 均安装只追加保护。四个函数属主为非超级用户 migrator，固定 search_path，运行时角色没有直接 EXECUTE。

可复现入口 `scripts/run_local_pg16_scrap_recovery_approval_checks.py --postgres-bin <PG16 bin>`，仅接受二进制路径，创建独占 Unix socket 测试库，不接受现有 DSN。最终日志 `artifacts/formal-baseline-audit-20261002-0164/scrap-approval-sql-native-v4.log`，进程 **89754 exit=0**：5 组正常历史（含原始/纠正、退回重审、补证后新申请）、**56 个非法场景拒绝（41 COMMIT、15 statement）**，以及同报废双申请和跨表同键两组真实并发均仅一笔提交。跨阶段同人同请求号的用例特意排除自审、同表唯一键干扰，确认由共享请求规则拒绝。真实非超级 API 角色 INSERT/COMMIT；三种运行时身份直接调用四函数均 42501。测试库 `artifacts/local-scrap-recovery-approval-pg16/run-59nl6k3a` 为 stopped/checks passed/serverExitCode=0，全非 Markdown 源摘要无漂移。

首轮 v1 失败原因是 TRUNCATE 测试先被 PostgreSQL 外键限制挡住，未触发待验证函数；在独立合成测试库改为 CASCADE 以实际触发禁止清空保护。v2/v3 通过回执保留，最终以可复现入口 v4 为准，数量不能累加。新 SQL 组件未注册 Alembic revision，未修改 0159/0163 的既有拒绝规则，未安装生产 grants。

**验证范围必须保留**：本门禁使用准确新表及最小外部父表，尚不是实际 0164 升级或新报废/恢复完整过账。下一步为原报废计划、附件内容和事件束、当前权限及 SN 生命周期的完整数据库证明；再接正式前向迁移/运行时准入、原请求 lookup/结果未知判定/封存和 HTTP/PC/H5。此前 Python 恢复 20 项及旧库存 105 项结果保持有效，但不能替代新业务 PG COMMIT。本批未提交、未部署，全部未提交改动保留，完整上线目标继续 active。 仓库安全扫描 79909 exit=0；`git diff --check` 通过。机器回执：`artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-approval-sql-current.json`。

## 最新接续：实际找回恢复与多代报废链已接统一过账（2026-10-02）

新增 `recovery_plan.py`、`recovery_posting_authority.py`、`recovery_execution.py`、`recovery_history.py`、`recovery_serial_proof.py`。预检准确的独立总部批准、原报废当前性、原冻结份额、期初、当前保管责任/策略、余额投影及 SN 原流水。私有许可绑定对象、事务/savepoint、操作者、完整命令、库存游标、请求和原交易；数量模式也不能借通用反向/入库接口绕过独立找回批准。

通过统一过账追加严格原交易反向流水、准确找回执行↔冲销双向事实及事件，恢复原冻结账户；从原报废前的不可变历史恢复 SN active/位置/首次入账来源。父冲销通知一次，找回执行子事实保留独立审计/状态/outbox，不重复发通知。完整历史链已支持原始及纠正报废的找回反向，并按原游标重新构建库存计划。未改旧事实、未自动提交、未加公开入口或生产权限。

初步终态：`scrap-recovery-execution-v2.log` 12 通过，`scrap-recovery-generations-v1.log` 数量/SN 两种“原始报废→找回→新批准→纠正报废→再次找回”均通过；原根保留，冻结余额只恢复准确份额。首次 v1 因 SQLite 回读时间无时区导致子审计校验失败，已按现有 `_aware` 规范化，失败日志保留。随后补齐当前/历史物料策略校验，最终合并聚焦 `scrap-recovery-stock-focused-v1.log` **20 全部通过，63477 exit=0**，包含两代实际恢复、许可/事务防绕过、末尾撤权、篡改批准与子事件以及重算 hash 的伪造余额拒绝；初步 14 项与最终 20 项重叠，不相加。

原库存/SN/工单冲销回归 `scrap-recovery-inventory-regression-v1.log` **105 通过，7782 exit=0**；安全扫描 `scrap-recovery-stock-safety-v1.log` **2389 文件通过，72765 exit=0**。原 PG16 旧数量/SN 历史兼容 **84217 exit=0**，两测试库均 stopped/checks passed/serverExitCode=0、源码无漂移；92 条候选结构 SQL，229 旧表、数量 442 行/SN 449 行、782 原 FK、359 原函数、原请求回查保持不变；新表 API INSERT 仍拒绝。机器入口 `artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-stock-current.json` 固定准确源码和所有终态。此轮进程均已结束，无需重复启动已通过门禁；真正新业务 PG COMMIT 尚未验证。

下一步转向真正新业务的 PG16 COMMIT/生命周期/权限证明和正式前向迁移。现有 0163 版本的 `rsc_check_loss_history_graph_0159(uuid)` 仍明确拒绝未证明的 scrap，0159 权限检查也不包含独立找回动作；保留拒绝直到专用证明齐全。候选结构兼容通过仍不代表新业务可提交。原请求 lookup、孤立事件结果未知判定、缺失请求封存、PC/H5、批次和共享冻结账户更完整矩阵及全基线上线门禁继续待完成。本批仍未提交、未部署，完整目标 active。

## 最新接续：独立找回申请和区域/总部复核已实现（2026-10-02）

新增私有 `stock_scrap/recovery_approval.py`、`recovery_authority.py`、`recovery_facts.py`、`recovery_events.py`。找回申请准确绑定原始或纠正报废行/请求指纹、本人当前保管责任与完成上传的证据；区域核实和总部复核分别要求当前范围的独立权限，申请人与两级复核人不能为同一人。每次写入均验证完整报废历史和当前 SN 生命周期，持久申请/附件、审计、状态、outbox 和通知意图，末尾再次核验权限和来源。调用者负责 commit/rollback，不自动提交。

总部退回后必须新增区域核实，旧区域结论不可复用；区域要求补证后另建新申请及证据，原事实保留。申请、区域、总部和预留执行表共用找回幂等命名空间，已有请求拒绝盲目重放。审批的 `stock_effect` 始终为 `none`，总部通过仅是 `approved_pending_execution`，SN 仍 scrapped，不把批准当成恢复入库。

终态 **39 通过、1 个数量模式不适用的 SN 检查跳过**：`scrap-recovery-approval-v3.log` 22 通过（28562 exit=0）；`scrap-recovery-boundaries-v1.log` 11 通过/1 跳过（18421 exit=0）；`scrap-recovery-corrected-authority-v1.log` 6 通过（27075 exit=0）。涵盖原始/纠正来源 × 数量/SN、准确附件/来源、自审与两级同人拒绝、跨阶段幂等冲突、待处理申请冲突、退回及补证流程、通知记录失败及末尾撤权整笔回滚、真实权限加载器观察数据库授权到期/deny 后拒绝。

保留两次失败：v1 使用了不存在的 engineer 角色名，已改用现有 technician 并创建真实测试角色绑定；v2 原库存夹具没有正式 AuthIdentity，已补合成测试身份。这些是本地代码/夹具问题，不是用户的线上账号失效，无需登录。v3 已完整复验。仓库安全检查 46179 exit=0，2381 文件通过；`git diff --check` 通过。机器入口 `artifacts/formal-baseline-audit-20261002-0164/scrap-recovery-approval-current.json` 固定当前后端源摘要和日志摘要。

**下一步直接补实际找回恢复**：独立批准驱动准确原交易的反向流水，回原冻结账户，SN 按原不可变入账/报废历史恢复，形成多代链。仍未实现新请求 lookup/封存及孤立事件结果未知检查；不能将当前内部证据验证函数当成可对外使用的原请求恢复接口。实际 PG16 新报废/恢复业务 COMMIT、SQL 生命周期/权限守卫、正式前向迁移/运行时准入和 PC/H5 仍待完成。下节 PG16 回执只覆盖旧历史及候选结构，不包含本轮新审批业务。

HEAD 保持 fc7c926，所有本批候选未提交、未推送、未部署。GitHub 同 SHA 门禁最近回读 61 成功/3 static 分片运行/2 runner shutdown 失败；没有重启活动任务。完整上线目标继续 active，不能以本轮局部通过宣称上线。

## 最新接续：报废父事件与完整历史证明已接通（2026-10-02）

已补齐原处置/纠正父事件、报废子事件及 canonical request/posting command。每次实物报废只通过父事实生成一次通知意图；子单独立留审计、状态和 outbox，不重复通知。历史核验按原交易游标重建冻结份额、原始/纠正批准、SN 前序与首次入账、证据附件和完整 plan；不能只改 JSON 后重算 hash 冒充合法历史。写服务返回前执行完整依赖链证明。

本轮终态：`scrap-history-writer-v1.log` 17 通过、1 个数量模式不适用的 SN 用例跳过（10361 exit=0）；`scrap-history-attacks-v1.log` 16 通过（67480 exit=0）；`scrap-history-existing-regression-v1.log` 旧处置、旧冲销及原请求回查 30 通过（45311 exit=0）。安全扫描 `scrap-history-safety-v1.log` 2373 文件通过（15525 exit=0）。PG16 旧数量/SN 历史兼容进程 47827 exit=0，两私有库正常停止、源码无漂移。92 条候选 DDL、229 张旧表、数量 442 行/SN 449 行、782 原 FK、359 原函数及旧请求回查保持不变；新表 API INSERT 仍拒绝。此门禁不执行新报废业务。机器入口 `artifacts/formal-baseline-audit-20261002-0164/scrap-history-current.json`。

后续推进独立找回申请及区域/总部复核，再接准确原冻结账户与原 SN 恢复。实际 PG 报废/恢复业务 COMMIT、SQL 守卫、正式迁移/权限、请求回查封存和界面仍待补齐。本批未提交、未部署。下节“父事件及历史分支尚未接通”已被本节取代，其旧回执只证明旧字节。

## 最新接续：专用报废写服务已接统一过账，尚未生产激活（2026-10-02）

本轮新增 `formal_services/stock_scrap/execution.py`、`posting_authority.py`、`events.py`、`serial_proof.py` 和候选 Core table 句柄。私有服务锁定账本及期初/引用/主体图后复核完整报废准备，使用同一统一过账事务写原始或纠正报废、专用单头/行/SN/附件、库存余额及通知意图。SN active→scrapped、位置为空，并能从准确前序与首次入账流水重建。调用者仍负责 commit/rollback；没有路由注册、自动安装表、生产权限或正式迁移。

统一过账新增不可序列化、精确对象与事务/savepoint绑定的私有许可。普通库存接口借用合法报废命令也不能越过许可；原始与纠正来源都重新核验当前总部权限。`serial_ledger` 仅在遇到报废流水时加载窄范围新事实证明；这不能替代完整审批、事件、历史及数据库 COMMIT 证明。

**必须保留的缺口**：当前新写服务只发出专用 scrap 事件，完整原处置/纠正父事件及原历史 command 分支尚未接通；实际 PostgreSQL 报废仍没有业务守卫/权限激活证据，不能对外启用。找回审批及恢复、SN 恢复、多代链、原请求 lookup/seal、正式迁移/权限/界面均未完成。不能把本轮 SQLite 成功说成新业务 PG16 或上线完成。

验证：进程 28233 exit=0，26 通过、1 个数量模式不适用的 SN 用例跳过；涵盖两种来源×数量/SN 真实统一过账、原根不变、同请求拒重放、通知失败/末尾撤权整笔回滚、许可克隆/换事务/savepoint拒绝、伪造 SN 前序和无专用事实拒绝。进程 25304 exit=0，既有库存过账、SN 和工单冲销回归 103 通过；与新聚焦存在 SN 测试重叠，不相加为总覆盖数。第一版 14 个 fixture 建表错误是 SQLAlchemy 元数据复制未保留方言条件，SQLite 错执行 PG 正则 CHECK；已保留原约束并恢复精确方言分派，未删除约束或绕过业务验证。

真实 PG16 旧数量/SN 历史兼容复验进程 **75528 已退出 0**（不执行新报废），两库正常停止、源码无漂移。92 条候选结构 SQL、229 张旧表、数量 442 行/SN 449 行历史、782 个原 FK、359 个原函数及原请求恢复保持不变；新表 API 写入仍拒绝。日志 `scrap-writer-existing-history-v1.log`。安全扫描进程 **82979 已退出 0**，2368 文件通过；`git diff --check` 通过。最新机器入口为 `artifacts/formal-baseline-audit-20261002-0164/scrap-writer-current.json`；所有候选仍未提交，HEAD fc7c926，GitHub 61 成功/3 static 分片运行/2 runner shutdown 失败，未重启现有任务。

上述进程终态已收齐；下一步补父事件与 canonical history/request 证明、PG COMMIT 与 SN 生命周期函数，再接独立找回恢复。原先结构验证 92 条 SQL/28 个非法组合拒绝继续只覆盖其固定 schema；本轮修改了统一过账和序列重建，应以上述新回执证明运行时兼容。完整上线目标 active，未部署。

## 最新接续：报废/找回双向事实绑定（2026-10-02）

本轮补齐主树未提交候选的两个缺口：纠正执行必须存在同一根记录、单头及流水的报废行；找回执行与冲销互相引用同一报废行，冲销绑定原根、原交易/流水、原冻结账户、准确数量及原始/纠正来源。新增 `scrap_line_id` / `scrap_source_kind` 保留空值仅用于旧普通冲销；CHECK 显式阻止 NULL 绕过。定义仍只构建独立 metadata，尚未注册正式迁移或写入口。

47 项关系/结构测试通过；执行记录组装回归 13 通过、1 个仅数量模式不适用的 SN 用例跳过（进程 12994 已退出 0）。真实 PG16 原始/纠正两库全部通过并正常停止：正确双向记录支持两种插入顺序，28 个非法组合被拒绝（含 COMMIT 延迟外键与立即 CHECK），每次回滚后所有既有行保持原值。此原生关系夹具的外部父表仍为最小 stub，不能替代完整业务、权限、审批或库存过账证明。

完整 0164 数量/SN 旧历史结构验证进程 **97034 已退出 0**，两库正常停止、源码无漂移；92 条结构 SQL 应用后，229 张旧表、数量 442 行/SN 449 行旧事实、782 个原 FK、359 个原函数和原请求回查全部保持不变，新表仍拒绝 API 插入。日志 `scrap-reciprocal-history-v2.log`；首次调用遗漏 `--postgres-bin`，退出 2 且未创建测试库，保留 v1 日志。安全扫描进程 25656 已退出 0，2360 文件通过；`git diff --check` 通过。准确源摘要与状态入口：`artifacts/formal-baseline-audit-20261002-0164/scrap-reciprocal-current.json`。前节 81 条 SQL 和 schema 摘要只覆盖旧候选，不作为本轮新结构证据。

GitHub `36931116983` 当前 61 成功、3 个 static_safety 分片仍运行、2 个 runtime 执行器关闭失败；不重启活跃任务。待整轮终态后只补跑中断任务。完整上线目标仍 active；当前 HEAD 仍为 fc7c926，所有本批候选未提交、未部署。

后续直接接专用事务写服务及完整历史/SQL/SN 证明，不再把已有双向结构列为未完成。原子记账、独立找回审批和受控恢复、请求回查/封存、版本迁移/权限、PC/H5 及完整基线上线门禁仍待完成。

## 最新接续：报废执行记录组装及准确数据库绑定（2026-10-02）

HEAD 仍为已推送的 `fc7c9269e19f6afd180a0aced55b565f03c244b6`，本批改动均未提交。上一轮状态为进展，不是上线完成。完整目标 active，未部署、不连接生产。PG16 CI `36931116983` 最新回读 56 成功、8 运行、2 个执行器关闭导致的 failure；两条日志确认 shutdown signal，并非本轮代码断言失败。客户端 run `36931116967` 已成功。继续观察同一 run；其余完成后仅补跑被中断任务。

已把 8 张持久事实表和 4 个父表扩展定义纳入 `backend/app/stock_scrap_persistence_schema.py`，仍只构造独立 metadata，不注册运行时或 Alembic。新增准确复合 FK：单头/流水、原报损行/冻结账户/数量、原始批准/处置、纠正批准/逆向/执行与实际报废流水必须一致；原始报废的根记录与报废行互相约束。它们是结构保护，不替代权限、审计、完整历史和生命周期 COMMIT 证明。

`formal_services/stock_scrap/execution_bundle.py` 已组装同一原子事务需要的单头、原始或纠正执行、报废行、SN 和证据记录，以及统一库存过账命令。纠正分支不更新原根记录；两分支都从准确冻结账户移到 `stock_operation_scrap` 外边界，没有伪造目标账户。记录组装不执行 DML，也不是 SN 过账授权。当前仍缺真正事务写服务、专用 SQL 守卫、SN 生命周期/恢复和正式迁移。

预检新增 SN 的 `admission_movement_id`，来自不可变流水重建；原 56 项/原生回执对应先前字节，不能直接当作本批新增字段已验证。本轮 45 项通过、1 项仅数量模式不适用的 SN admission 检查跳过；随后加强 FK 的组装测试通过，关系测试中的 10 个清理断言曾失败，根因是 SQLite 延迟 FK COMMIT 失败后事务仍存在，已显式清理 DBAPI 事务；修正后 17 项关系测试全部通过。完整合并聚焦复验进程 **93663 已退出 0：115 项通过，1 项数量模式不适用的 SN 检查跳过**，只有既有 Starlette/anyio 弃用警告；日志 `scrap-composed-focused-v1.log`。仓库安全扫描进程 42804 exit=0，2357 文件通过。

两套原生证据分开：

- **原生结构关系测试已通过**（进程 90313 exit=0）：真实 PG16 上原始/纠正两种准确组合提交成功，15 组“引用存在但绑定错误”的更改均在 COMMIT 被准确复合 FK 拒绝（23503），失败事务无残行；两库正常停止。外部父表是明确最小 stub，不能当完整业务证明。日志 `scrap-binding-native-v1.log`。
- **完整 0164 旧历史结构验证已通过**（进程 76591 exit=0）：81 条候选结构 SQL、不改 head，真实 API 生成报损/原处置/冲销/独立批准后验证旧事实、原函数/FK和三类原请求恢复；新表 API 不能插入。数量模式已通过（229 张旧表、442 行历史、782 个原 FK、359 个函数），SN 模式也已通过（229 张旧表、449 行历史，同样保留 782 个原 FK、359 个函数），两库正常停止，源码摘要无漂移。同轮还用 API 角色验证新 SN admission 预检及未持久化记录组装不改变库存、不遗留锁。日志 `scrap-bound-structure-native-v1.log`。

本节日志和机器回执均位于 `artifacts/formal-baseline-audit-20261002-0164/`，最新入口 **`scrap-execution-current.json`**。此前 `scrap-stock-current.json`、`scrap-structure-current.json` 是上一版证据，源摘要不能覆盖本节改动。不要将早期 artifacts 的 schema/ddl 副本覆盖新的版本化定义。

下一实施必须把纠正执行→报废行和恢复执行↔逆向的反向存在性约束补齐；目前新增准确 FK 不能替代这两个完整业务守卫。继续从 `inventory_posting._lock_and_validate_serials`、`serial_ledger.rebuild_serial_states`、`stock_loss_corrections.history_inventory`/`correction_facts.verify_plan` 及 PG 的 `rsc_check_loss_history_graph_0159`、当前 SN 生命周期守卫接入新分支，保留旧历史规则。

下一步完成原子报废写入和对应的数据库/历史/SN 证明，之后接独立找回审批与反向、请求恢复/封存、前向迁移、权限及 PC/H5。所有既有运行时拒绝仍保留，不以取消拒绝来绕过未完成证明。其他正式基线缺口和生产验收继续按审计文档逐项完成。

## 最新接续：报废预检及持久结构原生验证通过，正式写入待实现（2026-10-02）

本地与已推送 HEAD 为 `fc7c9269e19f6afd180a0aced55b565f03c244b6`，包含历史迁移加载修复和报废请求契约。准确 SHA 的客户端 CI `36931116967` 已成功；PG16 run `36931116983` 仍在运行/排队，最近两项 runtime（inventory/migrations）显示 failure，但步骤为 cancelled；完整日志确认 runner 收到 shutdown signal，无本轮断言失败或子进程超时结论。其他任务继续运行，待其结束后只补跑已中断任务。继续回读同一 run，不重启。旧 run 的两项超时失败及推送前快照全部保留，旧取消不算新失败。

未提交的 `stock_scrap_plan.prepare` 及三份测试已完成：56 项聚焦测试通过；真实 PG16 API 角色下，数量/SN 两种纠正来源预检均通过，进程 8742 exit=0，两库正常停止、源码无漂移。短事务保留完整期初证据与行锁，没有 DML、库存变化或锁泄漏。原 `SET TRANSACTION READ ONLY` 诊断因 `SELECT FOR UPDATE` 失败仍保留；不能称为支持 SQL READ ONLY。原始来源已有聚焦测试，但本轮原生证明仅覆盖纠正来源；批次及正式过账仍待覆盖。机器证据：`artifacts/formal-baseline-audit-20261002-0164/scrap-stock-current.json`。

报废/找回持久结构候选在 `artifacts/formal-baseline-audit-20261002-0164/scrap-persistence-candidate/`：8 张新事实表，扩展 4 个既有父表，严格区分原始/纠正来源、区域核实/总部批准/实际反向，保留原表 FK 和旧业务分支。7 项结构测试通过，覆盖来源 NULL 组合、准确复合 FK、重复执行及 DDL 编译。**候选尚未注册应用元数据、Alembic revision、权限或业务入口，不能视为正式迁移完成。**

新建本地 PG16 结构验证第一版进程 40322 已因测试把原处置 lookup_status 误写为 request_state 而退出 1；失败发生于结构 DDL 前，测试库正常停止。修正后的进程 61859 已正常退出 0，数量/SN 两种结构验证均通过：以真实 API 生成旧报损、原处置、冲销和批准，执行候选结构 SQL，逐表比较所有既有列、原函数/FK，并验证原请求只读恢复及新表 API 写入拒绝。数量模式已通过：229 张旧表、439 行旧事实、782 个原 FK、359 个原函数保留，三个旧请求准确只读恢复，新表 API 插入全部被拒绝；SN 模式也通过：229 张旧表、446 行旧事实，同样保留 782 个原 FK、359 个原函数和旧请求恢复；两库均正常停止，源摘要无漂移。两模式分别使用新建私有库。入口及源摘要在 `artifacts/formal-baseline-audit-20261002-0164/scrap-structure-current.json`。该验证不修改迁移 head，不安装报废/恢复业务守卫，也不替代最终版本化迁移、权限及业务验收。

找回请求契约已应用到 `backend/app/stock_scrap_recovery_schemas.py`：申请、区域核实、总部审批和执行独立；请求结果查询/封存带完整原命令和动作判别，禁止客户端数量/SN/目标账户/权限覆盖。候选 29 项测试通过；应用后与已有报废契约合并 53 项全部通过，进程 87215 exit=0，日志 `scrap-all-contracts-main-v1.log`。该模块不注册路由，不代表恢复审批服务已经完成。结构 SQL 与模型仍留在 artifacts 候选目录，不能丢失或误当生产迁移执行。

后续必须完成正式持久模型/前向迁移、不可变/延迟业务约束、原子报废及受控找回反向、SN 生命周期重建、原请求恢复/封存、权限和 PC/H5。不能为了通过测试去掉历史校验或把报废写成内部“已报废库存”。完整基线其他缺口、真实短信/微信、UAT、迁移/对账/性能/灾备保持独立待办，未部署生产。下方为历史记录，以本节及最新 Git/机器证据为准。

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
