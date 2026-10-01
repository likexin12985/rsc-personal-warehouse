# RSC个人仓开发交接

更新：2026-10-01 19:09:55（北京时间）。完整上线目标仍 active，本轮 progress；未提交、推送、部署。本地证据不等于生产验收。

## 工作位置与约束

- 工作树`/Users/lizhiwang/.codex/worktrees/06f6/oam`，分支`codex/notification-delivery-worker`，HEAD仍为`9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 根目录正式V1.0基线已完整阅读；接续工作仍须按该文件。禁止reset/revert/丢弃未提交改动；收齐既定证据后才提交。
- Python用`cloud_oam/.venv/bin/python`，从cloud目录pytest显式`-o pythonpath=backend`。原生PG16.15在`artifacts/pg16-native-20260920/install/bin`，只使用本任务创建的私有Unix socket测试库。
- 首页公开知识查询，星星按钮到`/xx`；小程序仅公开知识查询。飞书知识源低优先级；短信/微信真实渠道、JWT和生产验收独立。

## 当前主树与已收齐证据

统一证据根`artifacts/loss-correction-request-seals-next/`，下称P。

主树0161迁移、独立批准/执行原请求封存、纠正HTTP来源/命令/恢复及H5页面已整合。本轮再应用17项门禁/runner/CI修正，6新增、11修改、无删除，全部原字节保存。该批2005源逐字节等于CI v3，清单`P/ci-next/main-application-v1/source.json`，应用回执`application.json`。本轮新增只读退回事实图及测试3文件，**当前2008源**见`P/return-history-next/source-v1.json`；没有修改既有运行时文件。

- **主树144项聚焦测试通过**，15.20秒；`P/ci-next/main-application-v1/focused-verified-v1.json`。源摘要无漂移，worker97768/child97769均退出，主树未被在跑测试固定。
- 先前主树1999源的本批综合回执`P/main-application-v1/verified-v2.json`：当前合成样本对应全前端2132通过，接口/契约65通过；类型与后台/公开构建通过。该轮真实PG16.15当前head升级、空降0140再升、完整启动/边缘权限、9个短信数据库场景、期初报表检查通过且正常停库。
- 本轮17项只改变测试/runner/CI，产品运行时和前端与上一条相同；不要声称2005源重新运行了全部前端或完整后端。
- 旧接口测试漏来源GET、契约测试无条件重写前端样本已修复。正常生成只写tmp_path；显式导出需要绝对目录环境变量。两代样本与原失败147通过/1失败均保留，不改写为全绿、不相加重叠测试数。
- 页面390px检查无横向溢出、明确审批选择、预览/确认/取消正常；公开首页无登录表单、知识目录pending。`P/h5-next/visual/verified-v1.json`；浏览器临时页及预览服务已关闭，无业务写入。

## 新增原生门禁：四场景全部通过

`P/ci-next/native-seals-v1-verified.json`、`native-http_sources-v1-verified.json`均已核验真实终态、PG16.15、源码无漂移、正常停库和所有进程消失。

- seals×quantity/serial：批准/执行分别11类边界、各3类并发，真实提交/回查、封存后迟到写拒绝、显式新批准与执行不改变原封存结果。
- http_sources×quantity/serial：每个场景6次真实提交、3次提交后响应丢失、28次只读查询；来源GET只读、读写权限独立撤销、原请求恢复与永久封存。
- 各场景均覆盖正式0161迁移、空库往返、完整运行时权限、保留历史拒绝降级且全部公共行/函数/触发器不变。登录主体仍是合成身份，不是生产登录验收。
- 上述四场景源为v1的2005源，当前主树等于v3。两版本只有6处差异：2后端契约/路由测试、2合成样本、旧多代门禁比较及CI入口空白。所有产品运行时和6个新增辅助文件完全相同，见`v1-v3-source-comparison.json`；不能称两套完整源码相同。

## 多代门禁：四场景终态已核验

`P/ci-next/native-multigeneration-v2-verified.json` 已收齐 quantity/serial × generations/seal_retention 四场景终态、checks、真实PG16.15、源码摘要、正常停库和进程退出。数量/SN均覆盖三轮九个历史请求、并发单一赢家、第一代与后续请求保留。worker94283及全部子进程已退出，source-v2物理副本（source-v3.json清单）不再被运行进程固定。

旧v1原始OID比较失败的日志保留；修正仅针对成功空库往返规范化比较。被拒绝的历史降级仍比较全部原始事实、函数和触发器。上述结果是本地门禁，不是GitHub CI或生产验收。

## 新增退回事实图与确认的破损入库缺口

- `backend/app/formal_services/stock_loss_corrections/return_history.py`逐项验证原处置及出库/发运/验收/入库事实，检查跨行错误关联、完整快照、游标和当前总部读权限；`return_progress.py`给出互斥的履约份额及SN集合。只读内部组件，不提供补偿写授权，不把累计入库当当前可用库存。
- `backend/tests/test_loss_return_history.py` **29项通过，332.23秒**；query_only证明无写入，覆盖数量/SN、各阶段分离、部分验收、短少补收、损坏证据、读取期间撤权、原发件人停用。回执`P/return-history-next/focused-verified-v1.json`。原终端session65422已exit0、PID1131消失；该次无单独原始日志，回执明确引用工具终态。
- 独立2008源副本`P/return-history-next/source`保持不变。原生 quantity、serial 均完成：`native-verified-v1.json`核对终端与checks相同、PG16.15、API角色READ ONLY、完整迁移/权限/历史保留、源码摘要、正常停库和所有进程退出。固定副本已释放；这是只读图证明，不是破损分账或补偿写验收。
- **上线阻断：破损接受量错误入新件可用账户，数量/SN均已复现，尚未修复。** 验收接受1、其中破损1，当前服务仍入`new/available`余额1；真实合成服务测试2次，48.25秒。`P/return-history-next/damage-probe/verified-defect-v1.json`和两份观测是缺陷证据，2 passed仅表示复现成功，不是正确入账验收。
- [破损入库修复范围](RETURN_DAMAGED_INBOUND_GAP_20261001.md)明确需前向迁移、按验收结果拆分数量/SN及目标成色、原历史恢复、SQL提交约束和客户端合同一起修。不能仅阻断破损功能后称完成；禁止静默改旧余额或原流水。

## 当前开发候选：破损按成色入库

证据根 `artifacts/damaged-return-inbound-next`，以下简称 D。候选在 `D/source`，**尚未应用主树，也没有提交或部署**。

- 后端新增接受份额拆分、原子多目标入库和版本2.0预览；同一验收行可有原成色与坏件两份，数量/SN守恒。目标账户保留组织、位置、责任人、物料及批次，仅按原验收变化成色。已有坏件不重复转坏。
- 历史核验按1.0/2.0区分；原请求摘要、历史流水不改写。新版读取验证原验收破损子集，不以当前余额证明旧流水。
- **候选0162前向迁移已注册，尚未通过完整原生门禁，不能部署。** 包括验收行+成色唯一约束、按验收破损事实核验分账、禁止新增旧版1.0入库、精确运行时函数/触发器/权限验证和历史保留。3个前驱函数摘要匹配0161，保留后续账户准入补丁；旧1.0流水和请求摘要不改写。
- `service-verified-v1.json`：55项纯逻辑/契约；真实合成业务3项（数量全破损、SN全破损、数量混合）通过52.53秒；只读预览、目标库存、独立入账、回查和重复请求均验证。前端23项、小程序解析5项和TypeScript通过。前端列表使用验收行+成色作为唯一键。
- 扩展回归 `backend-quality-v3.log`：15通过、1失败297.19秒，失败为Node子进程路径缺失。固定已安装Node路径后，`remaining-verified-v4.json`核验剩余14通过、6排除251.04秒、源摘要未变、进程退出。前后两轮合计覆盖新增9项及原入库20项服务用例；不把重叠客户端/纯逻辑测试相加当全量门禁。
- 0162首次启动因Python路径被resolve到全局解释器而缺psycopg，第二次因PL/pgSQL CASE表达式缺括号失败；各自原始日志及failure-reviewed.json保留，均未进行生产写入。已修复解释器路径和SQL括号，未放宽业务断言。
- **0162普通数量原生回归已通过**：`D/native-normal-quantity-verified-v3.json`核验终态与checks一致、真实PG16.15、空降0154再升、完整启动/边缘权限、正常收货与入库、并发收货、原请求恢复/封存、错误写入整笔回滚、历史降级拒绝及正常停库；15125/15128/15156均退出。证据固定于`migration-source-v2.json`的2016源，不包含破损原生场景。
- 随后修正一个被批量替换错的历史迁移文件名和Alembic当前head测试，加入混合SN/分批验收服务测试；**当前2017源，migration-source-v3.json**。产品运行时/迁移与上一条相同。
- **两组原生破损门禁已通过**：`native-quality-verified-v1.json`核验数量混合、SN全破损实际API过账、并发入库单一赢家、原请求只读回查与重试后全部业务行不变。数量包含新增旧版伪造、错成色、保持总量但错分份额、遗漏份额4类；SN包含旧版伪造与错成色2类，均由数据库23514拒绝且整笔回滚。完整迁移/启动权限/空库往返/历史拒降及正常停库已核验，18268/18269及子进程、18320/18327均退出。固定2017源的migration-source-v3.json；不是混合SN、批次或所有验收场景的证明。
- 新服务测试：混合SN正常/破损准确分账与恢复已通过。分批验收首次复用已绑定异常文件被正确拒绝，修正每次验收独立完成凭证后1通过1排除28.39秒；旧日志和`extended-service-verified-v2.json`保留。修正已应用候选。
- **当前候选2018源，migration-source-v4.json**：应用`candidate-application-v4.json`中的20文件，含14项当前head门禁/runner对齐、正确分批凭证fixture、新0162离线边界测试及4项小程序修改；全部原字节存`migration-v3-preserved`。后端运行时/0162迁移与已过原生场景相同，主树仍2008源，未合入候选。
- 小程序入库现在先读取并验证原验收，再绑定预览的收货摘要、目标仓/责任、数量、成色及SN；拒绝仅凭ID的2.0预览。`mini-next/verified-v2.json`：**37项通过**，含页面实际流程、混合数量、SN全破损、原请求恢复与拒绝错误分账；首次29通过5失败源于旧页面假数据的仓库/摘要/物料/SN与原验收不一致，已修正fixture而非放宽校验。主树尚未应用。
- `migration-focused-v4`：**6通过1失败144.42秒**，失败为当前head链测试仍把0160作为直接前驱；真实链正确为0162→0161→0160。`failure-reviewed.json`保留，22330/22331已退出。精确修正准备在`migration-work/test_alembic_migrations-next.py`，前后SHA见`head-chain-fix.json`；固定候选仍未改动。剩余ORM迁移往返及2项扩展服务由独立`migration-remaining-v4`继续执行。
- **旧历史升级数量/SN均已终态核验通过**：`native-legacy-verified-v1.json`证明用真实0161旧代码与旧数据库生成v1破损历史，升级0162原请求回查/重试不改全部业务行；仅旧历史可降回0161，用旧代码恢复后再升0162。两代精确目录/权限、新守卫禁止API直接执行、PG16.15、正常停库和所有进程退出已核验。此证明不等于更正旧库存分类。
- **当前候选2018源，migration-source-v5.json**：只再修正`test_alembic_migrations.py`一文件，补0161前驱和2张封存表清单；原文件保留`migration-v4-preserved`，应用回执`candidate-application-v5.json`。运行时/迁移不变，主树仍2008源。
- `migration-verified-v5.json`：**4项通过223.64秒**，包括完整前驱链、全表结构与ORM一致及升降往返、混合SN服务、分批破损验收。此前6通过1失败及剩余ORM失败分别为前驱遗漏和表名清单遗漏，原始日志保留；不是忽略断言变绿。
- `client-verified-v5.json`：**前端2134通过**、类型、公开及`/xx`构建、公开入口开发模式和共享协议检查通过。小程序全套1083项首次1082通过1失败：候选副本在父仓被忽略的artifacts目录内，Git边界测试因此误读父仓规则。原断言与固定Git文件复制到独立空测试仓后65项通过，覆盖该失败项；不把重叠65再相加。独立仓第一版漏package.json的64通过1失败也保留，补原文件后通过。知识目录仍pending，不能称发布验收通过。
- **混合SN原生v2已终态核验通过**：`native-mixed-sn-verified-v2.json`核对终端/checks、PG16.15、全部源摘要、正常停库和27221/27232/27260进程退出。两SN实盘及真实报损批准、实际API验收/入库、并发一次入库、原请求恢复且全部业务行不变；旧版本、错成色、交换SN、遗漏份额分别被数据库23514拒绝。固定v5源；旧v1单SN计数断言失败保留。
- 普通退回原生v1已失败并正常停库，准确停在business.py:125的份额数量断言；并非数据库把坏件再次转坏。源实物来自正式工单换件回收，原本成色damaged，必须只入一份。修正`ordinary-quality-native-v2`现在验证原坏件、分批账户复用、数量/SN/lot/lot+SN和原请求恢复；`native-ordinary-quality-v2`正在执行，尚无终态通过。
- 多包裹数量/SN、正反入库顺序及后来入账后的原请求恢复测试在`multiparcel-service-v3`，`multiparcel-service-check-v3`已完成，**4项通过137.22秒**；`multiparcel-service-verified-v3.json`核验终态、源码摘要和进程退出。覆盖数量/SN及两种入库顺序；仍是服务测试，不是原生PG证明。v1失败为旧abnormal helper先把破损量设1再校验分数接受量；v2失败为测试错误读取不保存的明文幂等键；v3转发并保留准确原始测试请求用于后续重试。均未放宽业务校验，也不从幂等键摘要重造原请求。
- **当前2024源，migration-source-v6.json**：`candidate-application-v6.json`新增/修改9文件（3处现有CI/入口/拓扑，6个新测试/helper/runner），原文件保留`migration-v5-preserved`。正式CI矩阵新增`return_quality_whole`和`return_quality_mixed`，分别数量/SN，共4个独立库场景；共用真实业务helper、当前迁移/最小权限/历史保留驱动。新增新守卫API直接执行42501检查。未在GitHub运行，主树仍2008源。
- 本地正通过同一个正式helper验证四场景：`formal-quality-{quantity,serial}-{whole,mixed}-v6`，worker32483–32486；聚焦拓扑/参数拒绝/CI路由/多包裹服务`ci-quality-focused-v6` **67通过271.99秒**；`ci-quality-focused-verified-v6.json`核验终态、源摘要及32482/32489退出。普通原坏件门禁worker32487。这些都固定v6，worker和child以各state及实时ps为准，不得重复启动或改源码。
- `history-quality-work-v1`另外准备只读历史分类异常投影：总部权限、完整原事实证明和前后快照仍按`return_history.read`；将v1破损错误入new/used的准确份额标成异常，原请求和流水保持不变，明确不证明当前可用库存、不提供纠正授权。无公开接口，尚未应用候选。`history-quality-check-v1` **6通过221.39秒**；`history-quality-service-verified-v1.json`核验源摘要、终态、33769/33781退出。已启动`history-quality-regression-v2` worker35297，通过相同两份只读代码复跑原29项历史/权限/关联校验；新runner在`history-quality-work-v2`，尚无终态。后续仍需原生旧历史升级下的查询证明，不能用合成SQLite历史替代。
- **固定源码边界**：v6原生、聚焦和历史投影测试都读取`D/source`，全部读者退出前不可修改。机器入口`D/status.json`列出当前所有任务；历史运行失败保留独立回执。前一版CI尚未接入的限制已通过v6注册修正，但其本地新入口及真实GitHub结果仍待核验。
- 批次、多包裹、原已坏件、完整客户端回归及补偿/报废仍需继续；这些不能被已通过的数量/SN局部门禁替代。
- 初次服务测试因合成接收仓未建立期初而失败；已补用正式两级复核期初fixture，不放宽应用期初约束。历史源生成的无缓存只读进程10344已主动结束，无数据库连接；改用既有源码摘要约束的编译缓存后成功，旧迁移未修改。

后续：先收v6正式CI共享helper四原生场景、原坏件/批次门禁和历史异常服务终态，再补历史异常原生及完整只读权限回归；补混合SN、原已坏件、部分/多包裹真实服务及客户端合同；完成0162前向迁移、精确启动目录、直接SQL拒绝与并发/恢复、旧历史升级和新历史保留，再整合主树。现有候选本地通过不等于缺陷已经上线修复。

## 下一批业务与正式上线

1. **优先修复已确认的破损入库缺口**，多代及新事实图原生终态已核验。对各门禁检查checks/terminal/cluster-state/进程/源摘要；失败只修真实原因，不放宽库存或权限约束。主树17项已应用，无需再次应用。
2. [退回补偿设计](LOSS_RETURN_COMPENSATION_DESIGN_20261001.md)：普通整单取消不能用于报损派生单。只读事实图已有本地29项证明，原生数量/SN已通过；修好入库分类后实现未出库补偿及并发隔离，继而逐阶段逆向物流。仅未出库通过不代表下游补偿完成。
3. 报废原处置/纠正报废、SN生命周期和失而复得仍缺；保留当前明确不可用入口直到事实/迁移/权限/恢复完整。
4. 完整上线仍需真实短信/微信/通知/附件、正式身份角色、迁移和期初、准确SHA GitHub CI、多角色UAT、至少3天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚及真实业务冲销演练。
5. [发布说明](LOSS_CORRECTION_0159_RELEASE_RUNBOOK.md)已改为0159–0161当前边界，旧版逐字归档；[范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)按当前证据更新。

## 历史与机器入口

修订前交接完整保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_173018.md)，SHA256`477bc9ba18aa86c0dc3a6dd5706bd3f6491ed941329e221e52cc06dc43bc37de`。更早历史链保留。机器入口为`artifacts/loss-formal-application-next/continuation.json`及`P/status.json`，本轮旧JSON也按时间存档。

本轮更新前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_174601.md)，SHA256`3b1d428b4eb837b8f543ba568f3a153a9876df7f2972be98e13d2f86905ba731`。

本轮更新前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_180704.md)，SHA256`8e35a5317c5ac38d08e45a8dae22cbda31b79dc48119aeb3fec32056feb1fc9f`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_182148.md)，SHA256`217e9b87482e4ac96ce86f5c9cff15b86be740de7fed35ee229cde23cd07fe16`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_183029.md)，SHA256`5baa76a7cc900764f4ebdb28131cb82c7cbf7734f648ab7478912a6084d2ae98`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_184419.md)，SHA256`d31179dd7a50b07a5a0e5a385ec2fdc6bee7a74941d98c94dc8a20ff9af2550a`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_185842.md)，SHA256`d15edc6b9504a4448f616a391521d7f18e5e7c4d16536578ba4edc702d9fcfba`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_185938.md)，SHA256`7f6c535172d6208046c6e8ba510fc636467fb23e14d36f4de5701c5f44fe175a`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_190740.md)，SHA256`5159e2b4840a39754d6aa04b4e2842327f8c4378a26cebaaa61f49d7dc8bbeac`。

本轮修订前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_190955.md)，SHA256`327126345638dc50da20ed3457234f1dbddefe3f12341a218222f72958a457d0`。
