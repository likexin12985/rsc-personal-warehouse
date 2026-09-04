# RSC 个人仓与物资运营扩展系统

本目录沿用 `cloud_oam v0.9.0` 的 FastAPI、React 和原生微信小程序技术路线，按
[正式生产版需求与架构设计 V1.0](../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)
进行生产级迁移。V1.0 设计是代码、表结构、权限、状态和验收的唯一产品基线。

跨账号或云端接管前，必须先阅读
[私有仓库交接说明](docs/ACCOUNT_HANDOFF.md)，并从仓库根目录运行
`./cloud_oam/scripts/verify_repository_safety.sh`。该检查失败时禁止提交或推送。

## 当前开发状态

当前完成的是第一批 P0 安全与数据地基，不是完整 V1.0，也不能直接投产：

- Alembic 已接管数据库版本；`0001` 冻结 v0.9.0 的 21 张原型表，`0002` 加入
  33 张组织/身份/RBAC、同步、迁移、对账、文件、通知、Outbox、审计和参数基础表；
  `0003` 追加正式用户绑定/账号状态、身份约束、授权撤销证据、刷新令牌历史和审计链头；
  `0004` 追加省负责人配置的两个总部权限、当前授权范围唯一索引和空的授权审计链头；
  `0005` 追加正式认证审计链头、哈希 IP 频控索引和 provider-managed 验证证据约束；
  `0006` 追加认证写入的 HMAC 幂等操作账本和 AES-GCM 短期响应密文证据；`0007`
  追加 `(user_id, client_type, device_id)` 未撤销设备会话族的部分唯一索引；`0008`
  追加只保存域隔离 HMAC 的正式登录数据库限流桶；`0009` 将 v0.9 物料表原位保留为
  `legacy_v09_materials`，新建空的正式物料、追踪策略、位置/保管、库存账户、批次/SN、
  不可变交易/移动/SN 关联、余额及当前位置表，并初始化独立库存账本和审计链头；`0010`
  将 v0.9 盘点任务与明细原位隔离为 `legacy_v09_stocktake_tasks/items`，不猜测、不回填正式
  盘点事实，并新增 14 张空的正式盘点任务、范围、OAM 控制快照、冻结、截止快照、轮次、
  盘点明细/SN、差异、区域与总部复核、期初过账和期初成立事实表；`0011` 再新增截止账户之外
  的现场实物观察、逐范围完成封印和整轮提交封印，强制每个截止账户恰有一条含零值在内的
  实盘行，并保留待核实原始物料、批次和 SN
  标识；`0012` 再对同轮已解析现场 SN 增加部分唯一索引，跨范围改用串码/二维码别名也不能
  重复计件；`0013` 将已解析 SN 与现场声明的串码/二维码类型及唯一有效二维码映射精确绑定；
  `0014` 为整轮提交持久化真正封轮的 scope completion ID，禁止依赖同微秒时间猜测；`0015`
  在落地不可变边界前完整验证所有审计链头、事件归属、前序链接和规范哈希；`0016` 将实盘明细/SN
  清单与过账清单彻底分离，并新增不可变的待核实观察处置及整轮差异完成封印，禁止用同一份摘要
  冒充两个独立事实；`0017` 再从三条固定链头的完整反向链推导并持久化每条事件的
  `stream_key + stream_version`，以固定流集合、唯一坐标、链头外键和 PostgreSQL 延迟提交触发器
  阻断孤儿、断链、串链、坐标伪造或未被链头消费的事件；`0018` 新增不可变复盘 case 与逐范围
  本轮执行人/授权快照，要求每个 `round_no > 1` 精确关联前一已提交轮次、提交封印、差异完成封印
  和明确 `decision=recount` 的复核事实，并以连续轮号、完整范围覆盖、单一 counting 轮次及
  PostgreSQL 延迟完整图触发器阻断跳号、分叉和直接 `superseded`。本批不增加循环
  `current_round_id`；由 case、`current_round_no`、任务+轮次唯一键和延迟图校验提供等价证明。
  `0019` 再为逐范围实盘完成与复盘执行人快照追加工程师库位类型硬门禁：即使区域库位错误保存了
  保管人，`technician` 也只能写入精确关联 `location_type=personal` 的任务范围；升级前扫描历史
  污染并失败关闭，PostgreSQL 与 SQLite 均在插入时再次校验；`0020` 继续保护该事实，一旦已有
  工程师完成或复盘分配证据，对应个人仓库位不得再改为非 `personal`。PostgreSQL 使用
  `ENABLE ALWAYS` 更新触发器；生产启动时按实际 63 字节目录名称核验 0019 触发器，并对
  库位、范围完成和复盘分配三个敏感表的全部非内部触发器执行完整精确白名单校验。
  `0021` 不改表结构，将实盘行、现场观察和范围完成的数据库门禁改为按轮次取权：
  首轮仍精确绑定冻结范围执行人，每一个后续复盘轮则精确绑定本轮不可变 assignment 快照并
  重验完成时权限。复盘 case 门禁同时扩展为仅接受“区域 `recount/reject` 且无总部事实”，
  或“区域 `approve` 后由不同人员完成总部 `reject`”两类唯一终态图；升级前扫描历史污染并
  失败关闭，一旦存在复盘图或后续轮事实就禁止降回 `0020`；`0022` 为期初过账、过账明细、
  成立事实和底层库存流水补齐 `ENABLE ALWAYS` 不可变/延迟完整图门禁、每任务唯一过账约束及
  终态过账所需的最小运行时 ACL；`0023` 再放行一条受限终态：已提交复盘轮中的
  已核验无截止账户现场观察，必须与精确差异、过账项和 `opening` movement 逐项一一重证后
  才可过账；`StockAccount` 按规范维度复用并聚合多个 observation movement，不复制余额事实。
  API 仅可插入受门禁约束的账户，不能更新或删除账户，孤立空账户不能提交。
  `0024` 在全量撤销后按自包含清单重授完整已挂载期初流程的最小权限：二维码主数据仅可读，
  启动、实盘封轮、观察处置、两级复核和复盘事实仅可插入，任务与轮次只能更新服务实际推进的
  精确状态列；不授予这些事实表的更新/删除、序列、函数执行或主数据写权限；`0025` 不扩展
  运行时 ACL，而是为每条正式盘点范围增加资产组织、物理库位与任务区域的数据库关系门禁：
  任务区域与资产组织都必须是启用的 `region_company`，资产组织必须沿启用组织父链命中任务
  区域；目标库位必须存在、启用且为 `region/personal`，完整父库位链不得断链、失活或循环，
  链上每个库位的资产组织也必须沿启用组织父链命中同一任务区域。跨区域资产或库位、跨区域
  父库位、停用路径、断链和循环一律失败关闭。升级只扫描并加门禁，不修复历史污染；存在任何
  正式范围时禁止降回会失去该门禁的 `0024`；`0026` 在既有
  `reconciliation_runs/items` 状态投影外新增四张正式表：
  `opening_control_reconciliation_runs`、`opening_control_reconciliation_items` 两张期初控制账扩展表、
  追加式 `reconciliation_commands` 命令证据表，以及
  `opening_control_reconciliation_command_consumptions` 命令消费封印表，将已过账期初任务、
  最终轮次、过账、OAM 只读控制快照、`control_unassigned` 差异、解释人与批准人
  授权快照绑定为同一可重证图。
  解释如关联已通过隔离校验的文件，明细扩展同时冻结该文件的 SHA-256、字节数和 MIME 类型；
  三项必须同时为空或同时存在，数据库门禁和后续读取/批准/关闭都要求与 `files`
  当前可用元数据精确一致。
  升级前如旧通用对账投影非空则失败关闭，不猜测补齐正式坐标；每条命令使用连续目标版本，
  并以延迟组合外键要求事务提交前存在唯一消费封印。封印在投影、规范请求/结果、状态迁移、
  Outbox 与链式审计全部写完后最后插入；数据库再核验精确集合与确定幂等键，阻断命令单写、
  局部投影、旧命令复用、同版本竞争、多余副作用、孤立运行/明细、越级状态、删除、截断及带正式事实降级。
  PostgreSQL 运行账号只获得该路由所需的精确表读取/插入和投影列更新，不获得删除、整表
  `TRUNCATE` 或触发器门禁函数执行权限；对只读来源、追加式命令、消费封印、附件及正式权限图
  不扩大表级 `UPDATE`，仅允许调用四个迁移所有者持有、固定 `search_path`、无返回数据且按主键
  确定序加锁的 `SECURITY DEFINER` helper。幂等命令查询仍依赖事务级 advisory lock 与全局唯一键，
  不对追加式命令行建立反向锁；附件 helper 在一次调用内合并当前引用与本次候选并按 UUID 全局排序。
  SQLite 仅镜像本地顺序约束。
  `0027` 再新增五个迁移所有者持有的精确锁图函数：
  `rsc_lock_opening_control_import_0027`、`rsc_lock_opening_stocktake_start_reference_0027`、
  `rsc_lock_opening_stocktake_task_evidence_0027`、`rsc_lock_inventory_reference_graph_0027` 和
  `rsc_lock_inventory_serial_graph_0027`。期初启动范围以等长、同位置对应的
  `owner_org_ids[] + location_ids[]` 表示精确 scope pair；服务端先按 pair 去重和确定排序，数据库只锁定并
  验证这些精确资产组织/物理库位组合，不能把两个数组扩成笛卡尔积，也不会因共享库位存在其他资产组织
  账户而误拒合法任务。五个函数均为固定签名、`RETURNS void`、固定 `search_path`、无业务写入的
  `SECURITY DEFINER` 图锁；其中 serial helper 的完整锁域是串码主数据及其
  `serial_current_positions` 当前投影，随后才允许锁可变 `StockBalance`，audit head 始终是最后一个
  共享锁类别。普通 API 角色随后
  只读不可变事实和主数据，不再对仅有 `SELECT` 权限的表发起非法 `FOR UPDATE`；SQLite 保留既有本地测试语义，
  不冒充生产并发边界。
  `0028` 在此基础上新增一个五数组精确并集 helper：批量终态重证必须完整覆盖 opening
  任务、scope pair、账户和物料集合，并按 task → account → location → organization → custody →
  material → policy → lot → material QR 的唯一顺序一次加锁；随后才允许锁串码/当前位置与余额。
  调用侧在该锁后再次核对所有主数据签名，集合扩展、收缩、NULL、重复、越界或状态不符均失败关闭。
  helper 只向 API 运行角色授予固定签名 `EXECUTE`，不扩展任何主数据表写权限。
  SQLite 只提供本地顺序验证，不能替代 PostgreSQL 16 的真实并发演练。
  PostgreSQL 同时拒绝审计事件逐行
  更新/删除和整表 `TRUNCATE`，SQLite 拒绝更新/删除任何审计事件。离线 PostgreSQL SQL
  无法精确复现应用规范 JSON 哈希，因此
  审计表非空时拒绝离线升级，必须运行在线 Alembic 预检。正式快照、实盘、差异、
  复核、过账和成立事实由数据库触发器保持不可变；存在
  正式事实或已被引用时拒绝降级。
  审计链头一旦写入事件，对应迁移将拒绝降级，避免破坏审计链连续性。
  升级前会先扫描重复的未终结同范围授权及重复未撤销设备会话族；发现历史冲突时
  失败关闭，必须先出具处置清单并显式终结旧授权或撤销旧会话，迁移不会猜测保留对象。
- `0029`—`0030` 新建正式需求单、逐行需求明细和区域审批、总部审批、总部外部审批三段
  因果链；外部审批证据由一名总部管理员登记、另一名总部管理员核验。该流程只形成已批准
  需求，不生成分配、占用、出库或发货事实。`0031`—`0034` 为非期初盘点补齐独立差异生成、
  区域/总部复核、指定范围复盘和逐范围账本截止游标；`0035` 只负责审批后安全过账，保持对账、
  关闭、通知和外部同步未完成。`0036` 将正式附件限定为单一业务用途并冻结可用文件元数据；
  `0037` 只允许在全部履约轴仍为空时安全撤回或取消需求。`0038` 再将非期初盘点内部对账与
  `posted -> closed` 关闭拆成两次独立、可幂等重放的管理员动作，以不可变账户/SN 证明、技术性
  transition ack、专属审计/状态事件唯一性门禁、升级前 posted 版本/时间尾预检、最小 ACL 和
  数据库完整图阻断直接 SQL 绕过；它不创建通知送达、OAM/RSC
  收货或任何外部同步事实。`0039` 只对需求撤回/取消审计子集建立
  `X-Request-ID` 部分唯一索引，不改变创建、提交、审批或附件审计的历史重放语义；
  新增的纯只读 command-status 必须以新鲜本人权限将审计、命令、动作、状态转换、
  审批锚点和十个独立状态轴完整重证后才返回 `confirmed`。取消命令继续保留
  `0037` 的原始明细顺序幂等摘要；仅新命令在不可变审计中封存非敏感的明细 UUID 顺序，
  旧审计缺少该证据时失败关闭，不猜测或改写旧事实。`0040` 新增不可变
  `kms_data_key_pins` 账本，只保存用途、KMS Key ID、应用版本、KMS KeyVersionId 和
  `CiphertextBlob` SHA-256；API/备份身份只读，迁移身份仅可在双人复核后执行普通 `INSERT`，
  数据库触发器拒绝 update/delete/truncate，存在 pin 或加密引用时拒绝降级。`0041` 为每个正式
  短信挑战新增独立 `sms_challenge_dispatches` 单 owner 事实，将 `prepared`、`sending`、
  `accepted`、`uncertain`、`expired` 与挑战验证状态分离；升级只回填具有精确旧审计证据的已接受发送及已过期
  不确定事实，未过期歧义、重复 provider reference 或不可证明历史一律阻断。PostgreSQL 使用
  `ENABLE ALWAYS` 行/清空触发器、部分唯一索引和最小列级更新 ACL，运行时启动再核验完整目录与
  guard 函数体指纹；存在任何新格式 dispatch 事实时拒绝降级。
- 数据库只初始化固定正式角色及由各迁移显式声明的权限与角色授权映射。`0026` 新增
  `reconciliation/read`、`create_opening`、`explain_opening`、`approve_opening` 四个权限：总部管理员可读取、
  创建和批准，省负责人只可读取并解释本区域全量差异。不初始化用户、
  手机号/OpenID、人员绑定或数据范围；历史账号统一保持 `pending_identity`，禁止猜测映射。
- 后端已有不读取旧 `role/province` 的正式 Principal：总部全国、区域组织树、工程师本人、
  星星总部精确单据范围分别求值，多角色冲突执行 deny 优先；`/api/access/context` 返回
  当前有效角色、范围和权限快照。
- 生产会话建立、刷新和每次受保护请求均重新校验人员绑定、已验证身份、账号/人员状态、
  有效角色授权和范围。授权撤销立即失效；人员离职/停用只保留交接自助资源，账号本身
  处于 `suspended` 或 `disabled` 时则完全阻断。
- 已固化本次确认的角色策略：活跃内部组织中唯一绑定账号的人员默认获得本人范围工程师
  角色；李珂鑫、张鑫、张洋洋、张福利仅在活跃总部组织内按精确姓名、在职状态和唯一账号
  全批次预检通过后获得全国管理员角色；省背包负责人不自动创建。策略服务不创建账号、
  不启用身份、不读取旧 `role/province/mobile`，且只能由既有正式全国管理员以新幂等键执行。
  首名管理员仍须通过稳定 `person_id` 的书面复核开通清单完成，禁止用姓名隐式自举。
- 总部管理员可通过 `/api/access/provincial-managers/*` 查询最小人员候选、查看当前授权并
  手工授予或撤销 `provincial_manager + organization + region_company`。写入要求幂等键、
  请求 ID、目标授权版本和原因；角色授权、授权版本、状态事件及链式审计在同一事务完成。
  列表与响应不返回手机号、身份 hash 或内部登录账号 ID。
- PC 端登录后读取 `/api/access/context`，按正式多角色与权限快照展示菜单，不再用旧账号的
  单一 `role/province` 判权。正式首页可只读 `/api/v1/inventory/summary` 的账本游标和期初状态，
  并严格校验响应契约；期初未建立时不显示数量。已验收的“省负责人”页面初始不预置任何
  人员，并隔离受限交接、外部审批及未知身份。客户端对 v0.9 业务路径的 GET 与写请求均阻断，
  库存明细、工单、调拨、审计和旧设置页在正式 API 完成前不挂载。
- 生产启动不执行 DDL、不创建默认管理员、禁止密码登录，并要求至少一个完整配置的
  微信或短信无密码登录通道。数据库固定分为仅初始化使用的 `star_oam_bootstrap`、非超级用户
  迁移所有者 `star_oam_migrator`、最小权限运行账号 `star_oam_api` 和只读备份账号
  `star_oam_backup`；API 启动前只读核验真实 PostgreSQL 角色、owner、成员关系、schema/DDL、
  搜索路径、临时表、会话复制模式、数据库/schema/table/sequence 授权选项、列级残留授权、
  序列、函数、固定链头、每条审计事件的精确流坐标及不可变触发器，任一不符即拒绝启动。
  生产启动按每个安全触发器声明的精确 enabled 状态核验；需要抗复制会话绕过的写边界使用
  `ENABLE ALWAYS`，并拒绝带 `WHEN` 或 `UPDATE OF` 的弱化重建。当前授权清单覆盖已经挂载的正式认证、授权、库存只读和期初盘点
  读写路由；`0022`—`0024` 只为期初任务、实盘、观察处置、两级复核、复盘、过账、关闭及其
  精确流水图扩展逐表最小 ACL。API 不能更新、删除或截断审计/库存终态事实，不能控制触发器，
  也不能执行迁移白名单之外的 `public` 函数或读取 Alembic 版本表；除 `0023` 受延迟门禁保护的账户插入外，也不能借
  期初 ACL 创建任意账户或通用库存事实。后续每个业务写入口仍必须随受评审迁移显式扩权，
  不能依赖默认权限。
  启动门禁现按 11 个运行时函数的精确清单核验 `pg_proc`：除 owner、语言、波动性、
  `SECURITY DEFINER`、固定 `search_path` 与参数类型外，还校验 `prokind`、返回类型、无 OUT 参数、
  无默认参数和 `STRICT` 形态，并对 `prosrc` 做 SHA-256 精确指纹；API 仅获得清单内所需的
  `EXECUTE` 且不得转授权，`PUBLIC`、备份、边缘及未知 grantee 均不得获得执行权。任一函数形状、函数体或
  ACL 漂移都拒绝启动。
- 生产主 API 不再挂载任何 v0.9 业务读写路由；旧账号管理接口返回 `410`。开发/测试
  环境仍保留兼容路由用于迁移回归，并由集中式默认拒绝门禁阻止外部角色和空范围越权。
- 生产短信/微信登录已改为 `identity_type + provider_key + hash_version + HMAC-SHA256`
  精确匹配；不再读取旧 `users.mobile` 或 `wechat_identities`，也不允许微信手机号授权在
  首次登录时自助绑定。短信挑战只保存手机号/IP 哈希，阿里云托管验证码保持
  `code_hash=NULL`，请求、发送、失败、尝试、锁定、验证和消费分别留存状态与链式审计。
  登录挑战与设备会话使用不同 HMAC 域，禁止跨表关联；生产登录、刷新、列表和缓存响应重放
  均要求当前版本的会话 IP 证据，缺少客户端 IP 或发现明文、旧版、跨域证据时在 provider、
  幂等账本、令牌消费和业务写入前失败关闭。
- 新会话写入独立刷新令牌历史；每次刷新只消费一次并链接替代令牌，旧令牌重放会在同一
  事务撤销整条设备会话。生产 `/auth/me`、登录与刷新响应只返回 `person_id`、人员/组织、
  账号状态、权限版本和正式多角色，不返回内部用户 ID、手机号、旧角色或旧省份。
- Web 使用 HttpOnly `auth_device_id` 维持同一浏览器设备身份；生产会话中的 IP 仅保存域隔离
  HMAC。缺少或无效刷新令牌、短信/微信身份拒绝、provider 故障和确认的 refresh replay
  均追加脱敏认证证据，公开响应不泄露内部失败细节。微信登录会在兑换一次性 code 前短事务
  提交唯一 `pending` 幂等 owner；同键并发只能在 provider 前失败关闭。provider 失败审计与
  同一加密失败终态在后续事务原子提交，不再生成脱离幂等操作的独立失败证据。
- 总部全国管理员且具有 `auth_session/manage` 权限时，可读取正式脱敏设备会话并以请求 ID
  和幂等键强制下线整个刷新令牌族；撤销事实时点、状态事件和认证审计在同一事务提交，
  同键同请求返回原证据并标记 `Idempotency-Replayed`。
- 正式短信/微信登录、Web/小程序刷新和退出均强制使用独立的 `Idempotency-Key`。账本只保存
  域隔离 HMAC、引用和 KMS/AES-256-GCM 密文；同键同请求在 30–120 秒受控窗口内返回原始
  成功或失败结果，同键异请求返回 `409`。刷新成功、旧令牌消费、新令牌链、审计和密文在
  同一事务提交；相同键的网络重放不会误触发令牌家族吊销，不同键再次提交已消费令牌仍会
  吊销整个家族。每个请求只解析一次当前 KMS 数据密钥，预检与终态密文使用同一版本，避免
  第二次 KMS 抖动回滚已确认的令牌家族吊销。密文过期、篡改、权限已变化或密钥不可用均
  失败关闭，不生成第二套凭据。
- 正式登录的新幂等键先完成 KMS 预检，并用独立短事务按
  `hash-version guard -> global -> IP` 消费数据库桶；微信随后短事务提交唯一幂等 owner，
  再由 owner 调用 provider，返回可信 appid/openid 后独立消费 identity 桶。限流证据不保存原 IP、手机号、code、
  openid 或 unionid。终态同键同请求先只读核对并继续返回原幂等结果，不会在桶满后变成
  `429`；随机新键受统一 `429 + Retry-After` 约束。同键异请求仍返回 `409`。hash version、
  HMAC secret 或窗口长度变更在旧窗口仍活跃时失败关闭，避免静默获得第二份额度。
- 当前验证码 provider 使用号码认证服务 `dypnsapi` 的 PNVS 接口，不是标准
  `dysmsapi`。用户已购买的 1000 条标准短信套餐不当然兼容；套餐所属产品、适用 API、签名、
  模板、有效期及计费口径未以非敏感证据确认前，生产短信保持关闭。本地源码现已实现 `0041`
  单 owner dispatch、租约/迟到调用门禁、跨 Web/小程序的同手机号未决阻断，以及
  `accepted/uncertain` 独立审计；发送与校验均禁用 SDK 自动重试，并要求 `Success=true`、
  `Code=OK`、`OutId` 精确回显，发送还必须取得非空 `BizId`。网络调用在 HTTP 响应发送后执行，
  校验结果只接受 SDK 的 `PASS`（通过）与 `UNKNOWN`（错码），其他响应均不消耗尝试次数。发送调用前
  以 challenge+dispatch 双行锁重证 owner、租约和有效期，直到结果提交前禁止过期替换超车；发送使用
  独立无 overflow 数据库池，发送和校验共用每进程默认 `2` 个 provider permit，校验在身份判断前获取，
  超载时已知/未知手机号返回同一脱敏结果。未决发送复用原挑战且零新增写，频控窗口只保存首条拒绝证据，
  后续新幂等键不会放大挑战、状态或审计行。
  `OutId` 只是关联字段，不作为 provider 幂等证明；provider 已接受但数据库提交失败时保持
  `uncertain` 且禁止自动重发。正式启用仍须完成真实 PostgreSQL 16 并发/进程中断测试、PNVS
  隔离号码联调、回执对账恢复、backup/edge 间接角色继承的有效权限门禁，以及套餐/API/签名/模板/计费兼容确认。
- 正式短信登录在挑战/provider 阶段不锁全局认证审计链头；会话、刷新令牌和挑战状态完成后
  才按事实顺序追加挑战与会话审计，锁序与 refresh/logout 统一为业务行在前、审计链在后。
  Web refresh 的新鲜或缓存 `401` 会用两个独立 `Set-Cookie` 同时清除 access/refresh；Web
  logout 在请求标识校验通过后的服务端失败也清除本地两项凭据，但仍以 `503` 如实表示服务端
  撤销未获确认，稳定 `auth_device_id` 不随故障清除。
- Web 与小程序认证客户端使用至少 144-bit CSPRNG 键，并在同一逻辑请求/进程内刷新中复用；
  无安全随机源时拒绝认证写入，不回退时间戳或 `Math.random()`，也不增加网络盲重试。
- 正式库存地基已实现低层原子过账服务：不可变交易流水、余额及 SN 当前位置在同一事务
  `flush`，统一处理幂等、负库存、追踪策略、精确冲销、权限、状态事件、Outbox 和链式审计；
  调用方仍负责提交或回滚。通用入口禁止直接创建或冲销 `opening`，新增普通库存事实前必须
  逐个 `owner_org_id + location_id` 重读完整期初任务、全库位范围、提交轮次、区域/总部两级
  独立复核、期初过账与成立证据，且交易生效时间不得早于盘点截止时间、不得命中有效硬冻结。
  当前通用过账的固定类别顺序是：inventory ledger → 全部关联终态期初 task 行 → 当前调用方与全部
  task 历史人员的一次完整 principal/RBAC union → 各 task 的 evidence/start-reference 历史签名图 →
  当前命令一次合并的 inventory reference 图 → 当前命令一次合并的 serial 图（含
  `serial_current_positions`）→ `StockBalance` → 必要的已关闭期初对账图 → inventory audit head →
  opening/reconciliation 纯重证。一个事务命中多个共享 SKU、SN、账户或历史人员的终态期初任务时，
  不得逐 task 获取可能重叠的 owner helper 锁；历史 master/reference 使用完整候选集合与关键字段签名
  做只读重证，当前命令的 principal、inventory-reference 和 serial owner helper 均按全事务候选集合
  至多调用一次。
  待核实 OAM 控制差异只有两级复核均明确保留为 `pending_verification` 时才可随成立事实存在，
  永远不能转成个人仓或区域仓库存变动。通用非期初库存过账仍未开放业务 HTTP 路由，也未连接
  需求、履约或工单状态机；期初专用过账则由下述正式路由独立编排。
- 正式期初盘点启动服务已挂载 `POST /api/v1/stocktakes/opening`：只接受已完整落库且可重算的
  OAM 只读镜像批次，
  以 `owner_org_id + location_id` 建立全库位 scope，在同一调用方事务内固定账本截止游标、
  OAM 控制快照、零正式账本快照、冻结和首轮实盘任务。任何非零正式余额、既有库存流水、
  SN 当前位置或已成立范围均失败关闭；OAM 数量只进入控制快照，绝不创建账户、余额、流水
  或个人仓数量。资产 owner 与库位 owner 必须位于 task region 的有效组织树内；全国管理员也不能
  把 sibling/cross-region owner 纳入任务。省负责人必须由同一个选中区域授权同时覆盖 task region、
  资产 owner 和 location owner，禁止把多个区域角色拼接成一次授权。启动与初盘提交共用 task/round 并发坐标，
  新建期初事实的固定锁序是：0027 opening-control import source → inventory ledger → principal/RBAC →
  opening start reference → serial 图（含当前位置）→ `StockBalance`/freeze → inventory audit head。
  audit head 之后只允许按首轮封存的账户、余额、当前位置和主数据签名做普通重读并拒绝集合扩展，
  不得再进入任何 owner helper 或新增锁类别。同一启动幂等键即使任务随后进入
  已提交状态，仍只读核验原始证据图并返回原始启动结果。HTTP 适配器在成功时提交、异常时回滚，
  内部领域服务仍只 `flush`。
- 正式逐范围实盘已挂载 round-aware `count` 路由：每次只接受本轮不可变授权执行人的一份完整实物集合，
  由服务端重新解析 SKU、二维码、批次和 SN，不接受账面数、OAM 数量或库存账户号；无截止账户
  且确无实物的范围必须显式零确认。每个范围写入不可变完成封印，最后一个范围机械封存首轮并
  独立记录 scope、round、task 状态及 Outbox/链式审计。首轮只生成可由零账面证明的实物多余和
  独立 `control_unassigned` 对账差异；待核实标识保持不可过账，不推导缺失、错位或错成色，也
  不创建账户、余额、流水、SN 主数据或期初成立事实。串码、二维码及其唯一有效映射由数据库
  触发器再次校验，整轮封印显式引用最后一条 completion；状态、Outbox 和审计证据按精确集合
  与完整链可达性重放。所有单行数量及 scope、round、difference、最终过账聚合均严格小于
  `10^15`，在写入对应 `numeric(18,3)` 完成或库存事实前失败关闭。首轮与连续复盘轮共用正式
  契约，HTTP 层只负责事务提交/回滚。
- 待核实观察处置已挂载按 task/round/observation 精确寻址的正式路由：只处理当前已提交轮中的
  `pending` 现场观察，
  由全国管理员处理全部合法范围，或由省负责人按 task region、资产 owner 与 location owner
  的精确交集处理本省；每次重算完整实盘与差异封印，并按截止追踪策略唯一解析 SKU、二维码、
  批次和 SN。`resolved_existing_master` 只表示不可变的归属证据，不修改主数据、余额或既有流水；
  只有后续复盘形成已核验的规范维度，且完整两级复核和终态图均通过，才可能由期初专用过账建立
  对应账户和不可变流水。
- 区域与总部两级独立复核均已挂载正式路由，并把待核实观察的不可变处置证据纳入
  逐项复核和幂等重放校验。区域复核可以明确要求复盘；只有区域和总部在不同正式授权事实下
  依次通过，任务才进入 `approved`。复盘开启、历史复盘因果链和最终过账均重新验证复核阶段、
  历史范围授权、逐项摘要、State、Outbox 与完整库存审计哈希链；State 与 Outbox 按任务及复核
  业务坐标校验完整集合，异键重复或坐标矛盾均失败关闭。复核服务不合并盘点、过账、成立或
  通知状态；HTTP 层分别提交或回滚每一次状态动作。
- 在 `0018` 不可变复盘因果表和 `0021` 按轮次数据库门禁之上，内部事务服务已支持连续
  round-N 复盘：每条 edge 都从已提交来源轮的独立实盘、差异封印和唯一合法终态复核图打开，
  保留所有旧轮，并逐范围冻结本轮执行人和历史授权快照。每轮实盘、观察、完成封印、差异集、
  区域/总部复核、继续复盘和最终过账都重算完整前驱链与 State/Outbox/审计精确集；同键重放
  支持后续轮已提交、复核、过账或关闭后的只读重证，异键重复或任一前序快照被篡改均失败关闭。
  首轮已发布的事件文档保持原形状，复盘轮事件则显式携带 round/case 坐标。复盘、`post` 和
  `close` 已分别挂载正式命令路由。`control_unassigned` 可按两级复核结论保留在独立成立/对账证据中，
  但只要任一成立事实仍标记待对账，任务就只能停在 `posted`，不得进入 `closed`。现已挂载
  `/api/v1/reconciliations/opening` 独立路由族：总部从精确已过账任务创建运行，省负责人一次全量解释
  本区域差异，总部管理员在全部已解释后独立批准，且批准人不得与任一解释人相同。
  创建、解释、重新解释和
  批准均使用幂等命令、连续目标版本、唯一消费封印、
  乐观版本、授权快照和独立 State/Outbox/链式审计证据；每次读取从当前正式图重建命令结果，
  并反向核对每条命令的规范请求/结果和精确状态迁移、
  Outbox 和可达审计事件，读取、批准及后续关闭也均重证原期初过账与
  OAM 只读控制快照。批准只解锁 `posted -> closed`，不改写库存、期初差异历史标志或 OAM；
  批量强读先普通解析候选 run/task 坐标，再统一执行 inventory ledger → 全部 opening task UUID 行 →
  当前调用方与 opening/reconciliation 全部历史人员的一次完整 principal/RBAC union → 完整 opening
  evidence/start-reference/inventory-reference/serial/`StockBalance` 图 → 按稳定顺序一次锁定全部 0026
  reconciliation advisory/source/run/items/files 图 → inventory audit head 一次 → opening 与
  reconciliation 两套纯重证，不再先锁单个 run 后回头获取 task。对账写命令另按稳定顺序串行化其
  幂等键与任务 advisory 坐标；进入共享任务图后沿用相同的 ledger/task/principal/opening/reconciliation/
  audit 类别顺序。audit 之后只能凭同一 Session、同一数据库事务及同一 audit stream 的私有封存证明
  执行预先规划的普通读纯重证；这些内部证明直接持有 Session 与事务对象的强引用并使用对象身份
  比较，禁止以可复用的进程内数字地址代替事务归属。公开 replay 入口只接受 UUID，不接受 ORM 对象或调用方声明的
  “已预锁”布尔值，也不再调用 public replay、owner helper 或新增 `FOR UPDATE`/advisory。期初关闭
  仍是独立状态动作，不由对账批准自动代替。
  对应提交 `20c29f724d46112d06fd1734091ecee90500d636` 的 disposable PostgreSQL 16
  迁移、双会话并发和死锁发布门禁已通过；这不等于预生产迁移、真实业务 UAT 或生产授权完成。
- 正式库存只读接口挂载 `/api/v1/inventory/summary` 与 `/api/v1/inventory/personal/me`，校验
  账本头与最新交易游标精确一致、组织/位置树、人员保管关系和正式数据范围；内部账户列表及
  交易明细不对外开放。期初任务另有 `GET /api/v1/stocktakes/opening` 游标分页列表和精确 task
  详情，以及启动、实盘、观察处置、区域/总部复核、继续复盘、`post`、`close` 独立命令路由。
  库存查询涉及多个终态期初任务时，以一次账本游标快照、全部 task 行、完整历史人员 union、共享
  reference/serial/balance 图和已关闭任务对账图形成批量证明，audit 后只做普通重读，并对账本游标、
  closed task 状态集合做精确等集校验。期初列表/详情同样按整页构造一个 principal union 与一个
  serial union；空页仍先验证正式账本已经初始化，禁止用空结果绕过健康门。
  这些是当前源码的正式契约和最小 ACL 边界；准确提交上的一次性 PostgreSQL 16 并发验收已通过，
  但预生产迁移、UAT 和生产授权仍未完成，未复核/未成立范围的数量继续失败关闭。
- PC 已提供期初任务列表/详情、实盘、观察处置、区域/总部复核、复盘、过账和关闭，并新增
  “控制账对账”工作台，按 `reconciliation/read` 显示列表/详情，按独立权限提供创建、全量解释和
  总部批准，不自动关闭盘点，也不访问 `/integrations/oam`。小程序提供盘点列表/详情、工程师实盘及
  管理员过账/关闭；独立期初控制账对账、复杂复核、复盘和观察处置仅在 PC 承载。
  盘点功能在两个客户端都
  只调用 `/api/v1/stocktakes/opening` 正式接口族，旧 v0.9 盘点页不作为正式能力。小程序对 count、
  `post`、`close` 的 timeout/5xx 结果保留原幂等键、请求 ID 和实盘草稿；普通详情状态变化不能证明
  原请求成功。对象仍在原状态时只允许复用原坐标人工重放，被其他请求推进时冻结新写并转 PC 核验。
  PC 对实盘、观察处置、两级复核、复盘、`post`、`close` 按 task 最多保留一个未确认 intent：
  原状态仍可精确重放时复用原 path/body/幂等键/request ID；严格写响应会以传输副本保存并在恢复时
  重新校验；普通状态推进不自确认，同 task 其他动作被阻断并返回安全核验坐标。已有任务的
  `allowed_actions` 不再包含从未由查询服务合法返回的 `start`；PC 受控启动未开放，正式方案必须由
  服务端生成并封存完整启动计划，PC 最终只确认 `plan_id + plan_sha256`，不允许浏览器手填或
  删减组织、库位、人员、同步运行及控制行内部 UUID。
- 生产 KMS envelope-key 加载、密文注册表校验、`0040` 不可变 pin 账本/只读 gate、请求前置
  解密和 live/ready 单飞 TTL 健康门已在本地源码实现；这不等于生产 KMS 已可用。首管理员稳定
  ID 双签开通清单、正式身份激活工具、身份哈希密钥轮换流程、真实 KMS 数据密钥与 ECS RAM
  最小权限、`0040` pin 双人受控落库、外部审批字段白名单、预生产 PostgreSQL 16 迁移、
  WAF/ALB 前置限流、微信 code 兑换后的不确定结果恢复、短信 provider 回执恢复/外部幂等证据、真实附件/身份 UAT，以及过期认证
  密文的保留与不可复用 tombstone 策略仍未完成，因此当前阶段仍不得启用正式账号或部署生产。
- `0027` 已在本地源码中以迁移所有者精确图锁收口既有期初启动、实盘、观察处置、复核、复盘和过账
  服务对仅授 `SELECT/INSERT` 的不可变事实或主数据发起非法 `FOR UPDATE` 的问题；调用侧改为先锁固定图、
  再由普通 API 角色只读，并有静态锁序、迁移、ACL 和离线 SQL 回归覆盖。`0028` 已在本地加入真正的
  多 task exact-union owner helper，终态批量路径不再依赖“当前没有 master writer”这一弱假设，也不退回
  逐 task 获取 shared master 锁。该实现仍只形成受控源码与离线回归证据；任何在线 master writer 或 ACL
  扩展都必须等待下述真实数据库验收，不能仅凭迁移文件存在而放行。
  当前准确提交已在一次性 PostgreSQL 16 上通过迁移 SQL、`SET ROLE star_oam_api` 全链烟测以及
  双会话语法/并发/死锁/TOCTOU 演练；预生产仍须使用相同工件重做迁移、备份恢复、隔离 UAT 和
  环境参数复核，全部通过前不得放行生产。
  PC 受控启动也必须等待正式
  `SyncRun.mode=full` 的 OAM 控制投影发布链与经用户确认的快照 SLA/计划 TTL，不能复用 legacy
  edge 镜像或让浏览器直接提交内部 UUID。
- PC 与小程序现已分别接入正式 `/api/v1/material-requests`、`/api/v1/materials`、
  `/api/v1/material-request-options/work-orders`、`/api/v1/files` 和非期初
  `/api/v1/stocktakes` 契约。需求提报的工单引用不再允许两端手填内部 UUID；选择器只读取
  当前工程师本人、当前区域内 `pending/active` 的本地正式 OAM 工单投影，并绑定人员与授权版本。
  每项还必须具备唯一当前 `starcharge_oam/work_order` 来源版本、规范投影哈希、来源时间不晚于
  同步时间且同步时间不超过 45 分钟；响应只返回最小来源 ID、版本、来源/同步时间与 `fresh` 证据。
  `0042` 仅向 API 角色开放一个迁移所有者持有的精确 `FOR SHARE` 工单行锁函数，API 仍无
  `oam_work_orders` 更新权；创建、修订和提交在幂等重放未命中后锁定并重读同一工单，阻断同步更新
  与需求写入之间的 TOCTOU。旧草稿引用若当前不可选，只能通过明确清除或重新选择后保存；401/403、
  网络错误、响应漂移和写后工单/修订回读不一致都继续保留原请求坐标并失败关闭。搜索、分页和编辑
  恢复无 v0.9 回退；选择接口不直连 OAM，也不返回工单业务载荷、地址、人员或履约事实。正式 OAM
  工单发布器已在本地源码形成 `edge staging → sync run/batch/inbox → external object/version →
  oam_work_orders` 原子链；边缘工单行只向该正式链提供来源 ID、编号、原始状态、执行人来源 ID、
  企业、省份和来源更新时间七个字段。`0043` 先为独立 `star_oam_projector` 角色收口所需只读/追加
  更新列，`0044` 再以精确来源坐标绑定和 PostgreSQL 强制 RLS 将这些表/列权限限制到获准行；主 API
  仍不能写工单投影，边缘接收角色仍不能写任何正式表；发布器先在默认隔离级别获取
  精确 source/scope 的 PostgreSQL session advisory lock，结束短事务后才开启 Repeatable Read，
  并在发布事务内继续持有同坐标 transaction advisory lock；完成接收同样使用该事务锁。旧快照、
  未知状态、时间回退、hash/数量不一致和人员映射冲突均不覆盖既有投影。
  30 天滚动窗口的缺席不作为删除证据；内容未变化的再次观察仍刷新同步新鲜度，来源时间变化则追加
  版本。该实现尚未在生产启用：`sync` Compose profile 默认不启动，必须先由迁移角色运行
  `deployment/provision_oam_work_order_source.sql` 固化人工复核的企业/组织/scope，并具备唯一当前
  OAM employee → Person 显式映射。人员来源版本必须与快照企业/组织精确一致，映射后的在职人员
  所属有效组织祖先链也必须到达已配置 org；条件不成立时发布器失败关闭，选择器安全为空，不能改用旧工单
  数据补齐。需求提报支持草稿、编辑、提交、退回后修订、
  撤回、安全取消、区域/总部审批以及外部审批证据双人登记核验；非期初盘点支持列表/详情、创建、
  启动、逐范围初盘、独立差异生成、指定范围复盘、区域/总部复核、差异过账、独立内部对账和关闭。
  两端均按最新权限快照与服务端 `allowed_actions` 双重判定，写请求使用固定幂等键和请求 ID，并在
  响应丢失时只依据精确版本和完成事实重证原请求，不把普通状态推进当作成功。小程序不承载复杂
  管理配置；所有正式文件均走 prepare → 签名 PUT → complete，不保存手填文件 ID 或临时媒体 ID。
- 客户端 IP 只接受显式可信代理提供的 `X-Forwarded-For`，默认可信 peer 仅为本机回环。
- 边缘同步只允许完整快照协议、HMAC 来源白名单和专用数据库角色；历史待发包不会
  自动重放，旧时间快照不会覆盖当前投影。生产主 API 不暴露边缘同步写入口，边缘
  接收器也不暴露用户或管理接口。云端会把每个 `source_instance + scope_key` 永久绑定到首次确认的
  source system、company 和 org，并在落库前将工单范围限制为精确七字段 `work_order`；详情、关系、
  额外字段或范围重标均拒绝。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、RSC/个人仓入库、通知送达、
  同步/对账仍是十条独立状态轴。低层正式库存流水、期初盘点及其
  `control_unassigned` 独立解释/批准/关闭门禁、一期需求三级审批以及非期初盘点至独立对账/关闭
  已在本地源码建立。需求获批仍只表示需求成立；分配/占用、出库、发运、签收、OAM 收货证据、
  RSC/个人仓履约入库、通知送达及跨系统对账闭环仍未开发，
  不能借用启动任务、打开的复盘轮次、一笔库存流水、表中占位事实或 v0.9 Transfer 状态替代。

## 安全边界

- OAM 凭据只允许由本地只读采集端通过 `work/oam_shared_session.py` 获取，绝不进入
  云端、日志、代码或运行配置；任何代码和测试都不得自动登录或调用验证码端点。
- RSC 新系统库存禁止回写 OAM。OAM 镜像与本地库存只能对账，禁止合并求和。
- 同步失败、数量/hash 不一致、来源不在白名单、分页不完整、业务键重复或来源时间
  回退时一律失败关闭，不更新当前投影。
- 生产数据库结构只能通过显式 Alembic 迁移变更；应用启动和边缘接收进程均不建表。
- 当前开发和测试只使用本地 SQLite/临时文件，不应连接 OAM、RSC、Workflow、飞书
  或生产数据库。

## 本地验证

Python 3.12 虚拟环境安装依赖后，在本目录执行：

```bash
PYTHONPATH=backend .venv/bin/pytest -q backend/tests
PYTHONPATH=backend .venv/bin/pytest -q edge_sync
pnpm --dir frontend test
pnpm --dir frontend build
node --test miniprogram/tests/*.test.js
find miniprogram -type f -name '*.js' -not -path '*/node_modules/*' -print0 \
  | xargs -0 -n1 node --check
```

数据库迁移的空库升级、重复升级、降级、v0.9 stamp 兼容、旧数据保留和 PostgreSQL
离线 DDL 均由 `backend/tests/test_alembic_migrations.py` 覆盖。现有 v0.9 数据库接入
Alembic 前，必须严格执行 [迁移指纹与 stamp 流程](backend/alembic/README.md)，禁止
直接对已存在表的数据库运行 `0001 upgrade`，也禁止跳过 `0002` 直接 stamp head。

限流桶清理只删除已经结束且额外保留完整上一窗口后的记录，单批最多 10000 行。工具不读取
默认数据库或环境变量，必须显式给出目标、带时区截止点和批量上限；PostgreSQL 还需显式
确认。示例仅针对本地 SQLite：

```bash
PYTHONPATH=backend .venv/bin/python scripts/cleanup_auth_login_rate_limits.py \
  --database-url sqlite+pysqlite:///./local-test.db \
  --as-of 2026-08-30T12:00:00+08:00 \
  --batch-size 1000
```

生产调度须由部署方在 Worker/运维任务中审查并显式添加 `--allow-postgresql`；本阶段没有连接
或清理任何外部/生产数据库。`0008` 降级要求先停登录写入并将已安全过期桶分批清空，表非空
时拒绝降级，避免在仍有生效额度事实时静默移除保护。

## 部署边界

`docker-compose.yml` 仅作为部署骨架：先由一次性 `migrate` 服务升级数据库，成功后
才启动 API。正式部署前仍需具备独立 Git 仓库和评审流程、开发/测试/预生产/生产四套
环境、RDS PostgreSQL 16、Redis/Celery、私有 OSS、KMS/密钥托管、HTTPS/WAF、监控
告警、备份恢复演练以及真实微信/短信配置。

`0040` 后的正式 KMS 顺序固定为：`migrate -> 隔离 kms-pin-plan 服务 -> API 身份只读
查询既有 pins -> 双人复核 manifest_sha256 与新增差集 -> star_oam_migrator 普通 INSERT ->
star_oam_api 只读 pin gate -> API`。首次空 pin 表才全量插入，轮换只插入既有 pins 中不存在、
但 plan 新增的 purpose+version；匹配旧行不重插，任何旧坐标/KMS version/hash 不一致立即停止。禁止
upsert、update、delete、truncate 或跳过 gate；注册表变更必须滚动重启。认证 Key ID 切换前必须
证明全部幂等重放窗口清零并使用该用途从未用过的新应用版本，或先完成迁移重加密；同一用途禁止
跨 Key ID 复用应用版本。需求联系人当前记录和全部历史 revision 引用的
旧 registry entry 与 pin 必须保留到独立审计迁移完成，不能按时间直接清理。具体步骤见
[KMS envelope-key 部署清单](docs/ALIYUN_KMS_ENVELOPE_KEY_RUNBOOK.md)。

主 API 的 `/api/health/live` 仅检查进程，`/api/health/ready` 检查数据库及 TTL/单飞 KMS
Decrypt 探针，旧 `/api/health` 是 readiness 兼容别名。Compose/ALB 使用 ready，容器 liveness
使用 live；数据库检查最长 2.5 秒，KMS 等待和探针预算均强制不超过 4 秒，以落在 8 秒 ready
超时内。生产启动拒绝 `DEBUG=sdk`，并强制禁用凭据与 Tea SDK 自带的流式日志，防止签名头或
临时令牌绕过脱敏边界。健康失败只返回脱敏 `503` 且不推进任何业务状态。正式 WAF/ALB 还必须在 KMS 之前
完成认证写接口的外层限流，并限制 readiness 只对可信监控开放；仓库内应用限流不能替代该外部门禁。

全新 PostgreSQL 数据卷会通过 `deployment/postgres-init/10-create-application-roles.sh`
一次性创建迁移、API、备份和 OAM 投影四类隔离身份及后续对象默认 ACL；`migrate`、`api`、
`oam-work-order-projector` 和备份脚本分别只接收自己的密码。运行账号不获得未来表、序列或函数的
默认权限；每个新正式写入口都必须随版本迁移显式
授权。该初始化目录只对空数据卷生效。任何既有 `postgres_data` 或 RDS 实例必须先停写、备份、
盘点现有 owner/ACL，再由数据库管理员在预生产逐对象转移所有权和重放授权；仅重启 Compose
不会补角色，迁移会因 `star_oam_migrator` 不存在而失败关闭。当前未执行这项既有库改造，也未
连接任何 PostgreSQL 实例。

`sync` profile 中的 `oam-work-order-projector` 只加入与 `db` 共享的内部
`projector_db` 网络，不加入 API/Web 使用的 `backend` 网络，也不发布端口或挂载业务文件。
容器以固定非 root UID/GID、只读根文件系统、移除全部 capability、禁止提权和受限 `/tmp`
运行，并设置 CPU、内存、PID、优雅停机及数据库边界健康检查。健康检查只读 PostgreSQL ACL，
不会访问 OAM、RSC、Workflow、飞书或推进同步状态。沿用当前 Compose 可落地的环境变量机制时，
`OAM_DB_PROJECTOR_PASSWORD` 必须是独立随机数据库密码；它只注入 PostgreSQL 初始化和投影器，
投影器不得接收 JWT、KMS、短信、微信、边缘 HMAC、API、迁移或备份凭据。`.env` 文件和 Docker
控制面访问仍须由部署主机权限保护，不能提交仓库或复制到采集端。

边缘接收器的 `edge_inbox` 不属于上述四类主 Compose 身份，必须由 bootstrap 管理员用
`deployment/provision_edge_receiver_role.sql` 从进程环境读取独立密码后幂等建立/轮换，再由迁移角色
运行 `deployment/create_oam_edge_staging.sql` 授予精确暂存 ACL。生产启动与健康检查会复核角色、
成员关系、跨库/跨 schema、PUBLIC、表/列、函数、序列、Large Object 和参数 ACL；应用不会自动
建角色或扩权。

`0044` 已在本地源码中实现迁移所有者独占的精确
`principal + capability + source_system + source_instance + scope + company + org + entity` 绑定表，并对
edge 暂存子表、来源/批次/收件箱、外部对象版本、组织、人员和 OAM 工单正式投影子图启用
PostgreSQL `ENABLE/FORCE ROW LEVEL SECURITY`。授权从真实 `session_user` 和迁移所有者持有的固定
`SECURITY DEFINER` 函数求值，不依赖登录角色可自行 `SET` 的 custom GUC；零绑定默认全拒绝，
edge/projector 不能读写绑定表或执行私有 helper，只能执行 RLS 判定与绑定就绪两个固定运行入口。
`deployment/provision_oam_work_order_source.sql` 只建立人工复核后相互一致的工单接收/读取/写入组合；
`deployment/provision_oam_edge_scope.sql` 每次只幂等增加一条获准的 edge 接收范围，严格限制实体与 scope
组合，既有 company/org/enabled 或确定性身份漂移时不覆盖并在事务内复读失败关闭。
由于 `0043` 的宽行权限从未获准承载正式同步，`0044` 首次升级会先锁定完整 edge/formal sync 子图
并硬性要求 12 张同步表全部为空；不得把 `0043` 中即使结构和自报 hash 一致的暂存或正式行直接提升为
可信数据。升级并配置精确绑定后，必须重新只读观察来源并发布。相同规则也会阻止在同步图非空时降回
`0043`，避免重新暴露宽行边界；任何清理、去投影或迁移必须另行双签、备份和审计，迁移本身不会
自动删改业务数据。

提交 `474f0b1` 已由私有仓库的
[PostgreSQL 16 release gate 33667859479](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33667859479)
在全新服务容器中实际通过：包括 290 项静态安全门，以及零绑定、跨来源、跨范围、跨企业/组织、
跨实体、父子引用篡改、`row_security=off`、函数/PUBLIC ACL、伪造旧图、并发升级、非空降级、权限清空
和重升级的真实 SQL/迁移矩阵。这是源码与一次性 CI 数据库证据，不代表预生产或生产验收通过；所有
正式绑定参数仍须由用户和部署负责人逐项复核，不能由应用推断或自动扩权。

备份脚本在导出前以 `star_oam_backup` 自检双向角色成员关系、会话复制模式、数据库/schema/
表/序列授权选项、列级残留写权限以及所有 `public` 表、序列和函数的只读权限；数据库与上传
文件分别校验后连同 SHA-256 清单打成一个 `backup_*.tar`，只通过一次同文件系统原子重命名
发布。任一步失败都不会留下可被误认完整的最终备份包。该机制仍需在预生产 PostgreSQL 16
上完成真实备份恢复演练。

边缘接收器使用 `deployment/docker-compose.edge.yml` 和独立 `runtime.env`。运行配置只
能由 `deployment/build_edge_runtime_env.py` 从专用数据库账号、同步密钥和精确来源
白名单生成；示例占位密钥会被拒绝。表结构由 Alembic 创建，
`deployment/create_oam_edge_staging.sql` 只授予快照镜像最小权限，不执行 DDL，也不
授予 `users` 或 `inventory_balances` 权限。

历史部署若曾存入 `work_order_detail/work_order_relation` 明文，只能使用
`deployment/redact_legacy_oam_work_order_details.sql` 先执行默认 `ROLLBACK` 的 dry-run，核对严格更新、
完整且只含 `work_order` 的替代快照、候选数量和审计 SHA-256；不可逆执行还需要精确替代快照 ID、
审计哈希及硬确认词。v2 审计会逐条重构七字段载荷、source time、最终/增量 SHA 和原始批次 canonical
body，并把实际替代快照行与批次证据纳入哈希。该脚本保留 snapshot/manifest 和正式 run/batch/inbox
证据，只清理历史详情/关系载荷并将旧快照标记为 `redacted_legacy`。本阶段只提交工件与静态测试，
尚未执行任何数据库清理；进入维护窗口前仍须在 disposable PostgreSQL 16 上完成 dry-run/执行/回滚演练。

GitHub 的 `PostgreSQL 16 release gate` 已覆盖 main push、面向 main 的 PR 及当前受控分支，使用固定
PostgreSQL 16 容器执行迁移、角色/ACL 漂移、跨库连接、双会话锁，以及真实 projector 登录的发布、
重复、冲突和失败隔离测试；本地没有受控 PostgreSQL 16，因此每次发布仍以对应 GitHub run 的实际
成功结果为准，不能用本地 skip 替代。

提交 `20c29f724d46112d06fd1734091ecee90500d636` 已由私有仓库的
[PostgreSQL 16 release gate 33907501757](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33907501757)
在准确分支 `codex/production-readiness-gates` 上通过（run attempt 1，push，job
`postgresql16-release-gate`，2026-09-04T18:44:25Z 至 18:55:55Z）。固定 PostgreSQL 16 镜像摘要为
`sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`，Python 为 `3.12.13`；
静态清单为 `1044 passed, 1 skipped, 1 warning`，真实 PostgreSQL 动态门禁为 `1 passed`。该成功只证明
一次性数据库上的准确源码、迁移和并发门禁，不等于预生产或生产放行；预生产迁移、备份恢复、
真实身份/附件 UAT、部署参数与双人变更审批仍须独立完成。

当前 Alembic 唯一 head 为 `20260905_0061`。供给计划准确代码提交
`2677546f5ef6041ecb92acee75d1af868c9d80ff` 已通过
[PostgreSQL 16 run 33924289472](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33924289472)：
静态 `1234 passed, 1 skipped, 1 warning`，动态 `1 passed, 1 warning`；本地完整后端及边缘
`2266 passed, 1 skipped`（826.43 秒）。以上 `0058` 记录仅为历史基线。
新增原物料供给计划创建/跟进/取消、总部权限及幂等因果证据、PC/小程序严格回读和历史命令恢复；
这些操作不改变库存或任何履约状态，仍不包含替代料供给、多来源分配、实物交接及生产启用。
`0059` 升级预检要求供给事实图为空；`0060` 补齐数据库即时权限、历史版本唯一连续、孤儿证据
拒绝，并对已有图进行因果预检；`0061` 仅前向修复供给审计键的 JSON 提取运算优先级并推进
readiness，保持函数身份、权限、安全属性和触发器绑定。有供给事实时禁止降级；完整清单见
[0059–0061 供给计划发布清单](docs/SUPPLY_TASK_0059_RELEASE_RUNBOOK.md)。
后续客户端脱敏已移除期初盘点/对账错误和小程序页面中的原始幂等键。恢复修复提交
`e2043bbfcaa9fedbf7320adb4f7a773bfa4be0c6` 已补 Web 首次直接拒绝白名单及覆盖前后读取的
目标级防重、小程序已确认历史响应与当前同轮投影的独立核验；Web 476 项、TypeScript/构建、
小程序 220 项和后端路由定向 95 项通过。该提交不改后端业务、迁移和 ACL，准确 SHA 的官方
[run 33926859550](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33926859550)
已通过：静态 `1234 passed, 1 skipped, 1 warning`，动态 `1 passed, 1 warning`。
该提交时小程序通用 4xx 清标、两端身份/硬刷新恢复和命令核验仍待补齐，
不得将数据库门禁通过或脱敏完成等同于一期、预生产或生产放行。

以下为 `0052–0058` 已完成盘点迁移的兼容背景，不是当前 head 的发布证据。
`20260903_0052` 的期初盘点请求证据链不支持新旧应用与数据库混合运行；
`0053` 以前向迁移修复十五表共享延迟触发器在非 `stocktake_rounds` 记录上访问轮次专属字段的问题。
`0054` 不改业务状态、表、业务行或权限，只原位修复复盘建立后对已封印来源初盘轮的历史复证：
当前轮证明仍拒绝提前存在的复盘范围分配，历史来源轮证明才允许观察由复盘图独立封印的后继分配；
函数 OID、签名、所有者、ACL、六个调用方和触发器绑定保持不变。`0055` 不改写历史 `0047`，以前向
替换把非期初盘点启动 guard/validator 的审计图排序修正为 `audit.stream_version, audit.id`，并保持
函数身份、所有者、ACL、安全属性和触发器绑定。`0056` 修复所有非期初类型的初盘 assignment guard；
`0057` 统一差异回放的 ledger-head-first owner-lock 图，封印附件证据并拒绝非期初 control snapshot；
`0058` 使已批准复核证据在 `posted/closed` 终态仍可被严格重证。数据库安全清单已更新对应函数体
SHA-256，readiness 精确推进到 `0058`。`0052` 至 `0057` 均为不可改写的迁移历史，当前版本不得停留
在任一中间版本。正式升级必须先冻结全部盘点写入口并排空旧事务，再备份、迁移到 `0058`、
一次性替换全部后端、验证 readiness 与隔离 UAT，
最后恢复写入；期初盘点写入事务必须使用 PostgreSQL `READ COMMITTED`，其他隔离级别会被数据库以
`23514` 拒绝。PostgreSQL 的逐级降级仅允许不存在对应期初或非期初盘点事实的空历史场景；存在相关
事实时必须前向修复或按获批事故流程恢复完整备份，禁止破坏性降级。准确 SHA 的 disposable
PostgreSQL 16 release gate 已通过，但不得据此跳过预生产与生产放行。完整顺序与恢复边界见
[0052–0058 盘点证据链发布清单](docs/OPENING_STOCKTAKE_0052_RELEASE_RUNBOOK.md)。

## 后续开发顺序

1. 完成首管理员稳定 ID 双签开通清单、正式身份激活与 hash 密钥轮换、真实 KMS/RAM、
   `0040` pin 双签落库、短信 PNVS 回执恢复/套餐兼容/隔离号码联调、WAF/ALB 前置限流、文件存储
   生产凭据和字段级授权；在预生产以当前绿色 PostgreSQL 16 基线复做迁移、权限、攻击与中断演练，
   随后补齐
   组织/人员正式只读发布器、同步健康/冲突处理和日终对账，人工确认 OAM 企业/组织/scope 后才可
   受控启用工单投影器，且不得扩大为 OAM 写入。
2. 对已经完成本地正式路由的一期需求提报/三级审批/安全取消，以及期初和非期初盘点的实盘、
   差异、复核、复盘、过账、独立对账和关闭，以当前绿色门禁工件完成预生产迁移、备份恢复和故障
   回滚演练；补齐 PC 受控任务启动、大数据量列表/对账读取性能和
   全局锁序审查，完成追踪策略防重叠约束及
   真实身份、附件上传、需求处理和盘点 UAT 后才可申请预生产发布。启用撤回/取消恢复哨兵时，
   必须先完成全部后端实例的 `0042`（包含 `0039`、`0040`、`0041`）迁移、KMS pin gate 与新版代码切换，
   再发布 PC/小程序客户端；
   禁止在滚动窗口先放出客户端，以免新取消事实缺少可恢复的审计顺序证据。
3. 在已批准需求之后建立缺货处置、审批代理及安全补偿机制，再建立多来源分配与占用/释放；
   不得用需求审批状态替代分配或库存占用事实。
4. 建立拣货/出库、发运、物流签收、OAM 收货只读镜像以及 RSC/个人仓分批验收入账，并逐轴
   验收数量守恒、幂等和冲销。
5. 接入通知送达证据、Worker、打印/导入导出、报表、跨系统对账闭环和生产非功能验收。

每一阶段必须新增约束、迁移、权限越权测试、幂等/并发测试和独立状态验收；测试通过
不等于获得任何外部系统写权限。
