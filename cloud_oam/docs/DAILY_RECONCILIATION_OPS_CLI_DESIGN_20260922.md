# 日终映射与截止：最小正式运维 CLI 接入方案

日期：2026-09-22。此页记录当时的**待实现设计与验收矩阵**；2026-09-23 已落地
本地候选和部分验证，见[实现与现存缺口](DAILY_RECONCILIATION_OPS_ENTRY_20260923.md)。
仍未执行真实映射批准、真实截止采集或部署。

已完整阅读项目正式 V1.0 基线。本方案只核查现有源码并定义一次性运维入口，承接
[真实证据缺口审计 D2](PILOT_REAL_EVIDENCE_GAP_AUDIT_20260922.md)。不修改库存、
不访问 OAM/RSC/飞书、不创建总部身份、不增加定时任务，也不把本方案当作生产写入授权。

## 1. 缺口核实与现有边界

对 `backend/app`、`scripts`、Compose 的调用检索确认：已有映射与截止核心，没有正式运维调用入口。

| 当前实现 | 已具备能力 | 接入时必须保留的事实 |
| --- | --- | --- |
| [mapping.py](../backend/app/daily_reconciliation/mapping.py)，36、157、180、188 行 | `MappingCommand`；预览、准确回读、批准/撤销 | 使用 `inventory_control.authorize`，真实当前总部会话；执行函数不替调用方提交外层事务 |
| [cutoff_service.py](../backend/app/daily_reconciliation/cutoff_service.py)，34、101、108、114 行 | `CaptureCommand`；原请求回读；采集、比较、不可变截止及审计 | 当前账号与原命令绑定；只记录截止，不批准日终、不改库存 |
| [process_entry.py](../backend/app/daily_reconciliation/process_entry.py)，25、71、136、177 行 | 三连接服务端配置；POSIX 独立进程；`capture/recover` | 所有建连、Python 工作、SQL、提交都位于绝对期限内；未知结果只能原请求恢复 |
| [deadline_entry.py](../backend/app/daily_reconciliation/deadline_entry.py)，41–92 行 | 三连接就绪校验、剩余预算、提交与连接回收 | 三者实际 host/port/dbname 相同，PG16；新建空闲连接；owner 事务由本入口拥有 |
| [formal_daily_reconciliation.py](../backend/app/routers/formal_daily_reconciliation.py)，34–176 行 | 已有截止查询；解释/审核命令及其恢复/封存 | 没有生成截止或批准日终映射的路由；已有审核请求封存不等于截止生成封存 |
| [configure_inventory_control.py](../scripts/configure_inventory_control.py)，99、165 行 | `--mapping-file` | 接入的是 `app.inventory_control_mapping` 的物料状态归一规则，不能冒充本方案的跨系统日终映射 |
| [configure_daily_capture_roles.py](../scripts/configure_daily_capture_roles.py)，17 行 | 采集账号预览、配置及灾备恢复 | 只负责账号能力；不能产生映射批准或截止 |
| [Dockerfile](../backend/Dockerfile)、[Compose](../docker-compose.yml) | 镜像包含 `app`；已有独立 ops 服务 | 镜像不含项目 `scripts`；普通 API/同步/通知服务没有调用上述日终入口 |

本批只需入口、校验、打包与测试，**预期不新增表、迁移、角色或通用 Web 权限**。
如果实现发现必须调整数据库守卫，另行列明原因和迁移验收，不能在 CLI 内绕过。

## 2. 最小入口与职责

建议新增镜像内模块 `app.daily_reconciliation.ops_cli`，固定命令：

| 操作 | 核心调用 | 事务结果 |
| --- | --- | --- |
| `mapping-preview` | `preview_inventory_control_mapping` | 计算真实审核摘要；回滚读取/锁事务，零业务事实写入 |
| `mapping-apply` | `execute_inventory_control_mapping` | 外层事务提交后返回已记录的映射决定 |
| `mapping-status` | `read_inventory_control_mapping` | 原命令与原审核摘要回读；回滚读取事务 |
| `cutoff-preview` | 新增有界的只读坐标检查 helper | 只证明当前坐标、权限与配置可检查；不产生日终候选或最终游标 |
| `cutoff-capture` | `process_entry.execute(operation='capture')` | 执行并观察到提交，或明确结果未知 |
| `cutoff-recover` | `process_entry.execute(operation='recover')` | 原命令回读；保持现有三连接和认证边界 |

模块采用 `if __name__ == '__main__'` 入口以适配 multiprocessing `spawn`。CLI 不接受可调用对象、
Python 模块名、SQL、角色名、连接字符串或重试次数。可按后续运维需要增加极薄脚本包装，
但只保留一个实现，不把仓库路径挂进生产容器临时执行。

映射操作目前没有正式进程包装。新增固定 mapping worker，复用 `run_owned_job` 的期限与父子租约，
在 worker 内建立一个全新 owner 连接、校验、执行、序列化并提交/回滚。不得仅复制旧配置 CLI
的 SQL timeout 后声称覆盖 DNS、建连、Python 或 COMMIT。worker 函数来自代码固定分支，
不能由输入决定导入位置。`cutoff-preview` 同样在受控进程内完成。

Compose 增加单独 `daily-reconciliation-ops`、`profiles: [ops]`、`restart: "no"`、
`init: true`、只读根文件系统、受限 tmpfs/进程数/内存及无对外端口。仅此服务装载三种数据库
连接秘密和必要认证配置，API/Web/同步/通知服务不接收它们。建议用专用 internal 数据库网络
连接 ops 与 db，验证 pg_hba/TLS 与目标平台策略；不因“无端口”就宣称无外联。
首次只支持显式一次性运行，禁止自动启动、失败重启和后台定时补跑。

## 3. 输入契约：业务坐标与凭据分离

统一 `--command-file` 指向操作者准备的 UTF-8 JSON，`--access-token-fd` 为继承的私有只读
文件描述符；交互终端可用隐藏输入。凭据不得进入参数值、环境变量、JSON、stdout/stderr、
历史命令或审核附件。使用现有隐藏输入/FD 读取模式，但必须测试新入口的完整脱敏边界。
非交互容器的凭据交付需验证实际 FD/终端支持，不能用 `--token ...` 或打印环境作为兜底。

解析器拒绝重复 JSON 键、未知字段、非有限数、无效编码、超长文档与错误参数，不回显原始输入。
建议首版命令文件上限 64 KiB；超限明确拒绝，由容量评估另行调整，不截断规则。
身份来自会话，禁止 `actor_user_id`、`person_id`、伪造 principal 或管理员布尔值。

### 3.1 日终映射

顶层字段：`command`、正整数 `expected_authorization_version`；apply/status 另需
原预览的 `review_sha256`。preview 不接受该字段，避免让输入伪装成已审核结果。

`command` 完整采用当前 `MappingCommand`：

| 字段 | 约束与来源 |
| --- | --- |
| `action` | `grant` 或 `revoke` |
| `binding_id`、`catalog_id` | 已有正式来源绑定及目录的非零 UUID，不能从省份名称猜测 |
| `rules` | grant 必填：`revision` 与完整 `mapping`；复用 `mapping_validation.validate_rules` |
| `revoked_grant_id` | revoke 必填的原批准 ID；grant 必须为空 |
| `expected_subject_sha256` | grant 为准确目录 SHA256；revoke 为原批准 payload SHA256 |
| `evidence_file_id`、`evidence_sha256` | 已完成正式 OSS 验证、用途为 `source_configuration_evidence` 的准确附件；不自动上传或补造附件 |
| `reason` | 非空，最大 1,000 字符 |
| `valid_from`、`valid_to` | grant 可选带时区时间，执行时遵循现有有效期检查；revoke 均为空、即时撤销 |
| `idempotency_key`、`request_id` | 分别 16–128、8–160 字符，仅 `[A-Za-z0-9._:-]`，执行与恢复保持一致 |

映射包含来源系统/区域/目录摘要、明确目标仓、每个库位与区域/仓的对应关系、纳入状态以及
全部组织/库位事实摘要。策略固定为 `explicit_physical_location` 和
`published_quantity_locked_separate`；外区域库位必须显式排除，不能删掉后视为没有库存。
CLI 只验证、展示操作者的映射；不得自动选择映射、自动批准或修正歧义。

### 3.2 日终截止

顶层字段仅为 `command`、正整数 `expected_authorization_version`。三个操作使用相同
`CaptureCommand`，没有客户端提供的库存快照、数量、最后游标或差异结论：

- `business_date`：明确的 `YYYY-MM-DD`，按 `Asia/Shanghai` 解释。
- `source_publication_id`、`source_publication_sha256`：准确且已发布的控制源及其 payload 摘要。
- `mapping_decision_id`、`mapping_decision_sha256`：准确的**日终映射**批准及其 payload 摘要。
- `idempotency_key`、`request_id`：与映射同字符及长度规则，首次执行后不得自动改号。

来源发布自身引用的 normalization mapping 仍由截止核心独立验证，两种 mapping 不能混用。
审核版本值来自当前真实身份查询，不在 CLI 中自行刷新为最新值后静默执行。

## 4. 服务端配置、认证与三连接

以下是**拟新增的服务端配置名**，当前尚不存在：

| 配置 | 用途 |
| --- | --- |
| `OAM_DAILY_RECONCILIATION_OWNER_CONNINFO_FILE` | ops 专用秘密文件；直接 `star_oam_migrator` 连接 |
| `OAM_DAILY_RECONCILIATION_SOURCE_CONNINFO_FILE` | 独立秘密文件；直接 `rsc_control_capture` 连接 |
| `OAM_DAILY_RECONCILIATION_LEDGER_CONNINFO_FILE` | 独立秘密文件；直接 `rsc_reconciliation_capture` 连接 |
| `OAM_DAILY_RECONCILIATION_DATABASE_NAME` | 受审核目标数据库名，不能从命令 JSON 获取 |
| `OAM_DAILY_RECONCILIATION_MAXIMUM_SECONDS` | 服务端整数，默认 10，范围 1–60 秒；CLI 不允许扩大 |

秘密文件只读挂载，路径与内容都不能由业务命令覆盖；不得将 URL/conninfo 写进回执或部署清单。
配置须与固定发布候选、当前迁移 HEAD、PG16 及正式安全目录相符。预检使用实际
`current_user/session_user/current_database()`，不仅检查 URL 用户名；拒绝冒充 owner 的
`SET ROLE`、错误数据库、跨 host/port/dbname、错误版本或隐式备用目标。
不加载本地 `.env` 猜测目标，不修角色、不升迁、不激活灾备账号。

认证复用 [inventory_control_configuration.py](../backend/app/inventory_control_configuration.py)
35–120 行与 [inventory_control_authority.py](../backend/app/inventory_control_authority.py)79–96 行：

- 签名 Web JWT 只有既定 claims，正式 JWT secret、TTL、身份哈希版本与该环境一致。
- 锁定当前 AuthSession 和 principal 图，复查会话未撤销/未过期、Web/device 与当前 IP 证明有效。
- 当前用户有效、access_mode 为 active，具有全国 `admin` assignment 及
  `inventory_control.authorize` 的全国权限；全局拒绝规则仍由真实权限计算处理。
- 存在未撤销且验证时间不晚于数据库当前时间的正式身份；auth_version 必须与显式输入一致。
- 最终复核及数据库提交时守卫保留。预览成功不授权未来执行，不复用缓存 principal。

这里的会话/IP 检查是现有会话证据检查，**不是 CLI 网络地址重新登录证明**。首版不提供
自动登录、刷新凭据、模拟用户或脱离用户的 service admin。真实操作者缺失即停止该对象。

三连接职责不可合并：

| 连接 | 事务/能力 | 要求 |
| --- | --- | --- |
| owner | READ COMMITTED；权限、绑定/参考及账本头锁；截止与审计原子提交 | 直接 migrator；新建、无池、清洁事务；不复用 API Session |
| source | REPEATABLE READ READ ONLY，重证已发布控制源及其证据图 | 直接 `rsc_control_capture`；固定 SELECT/RLS/角色能力校验；不请求 OAM |
| ledger | REPEATABLE READ READ ONLY，独立读取流水和游标 | 直接 `rsc_reconciliation_capture`；固定 SELECT/RLS/角色能力校验；不接受 owner 伪造快照 |

复用现有角色契约校验：无超管/提权/继承/成员关系/写权限，固定表权限与 RLS 均通过。
发现恢复禁用态或权限异常，转既有[账号恢复流程](DAILY_CAPTURE_RESTORE_OPERATIONS.md)，
不能由本入口自动修复。映射命令只需 owner；截止 capture/recover 沿用当前三连接初始化。

## 5. 只读预览与执行顺序

### 映射

1. 执行 `mapping-preview`，真实鉴权、锁定准确绑定/附件/参考目录，计算完整 core `review_sha256`。
2. 输出受限 JSON：操作名、当前操作者/授权版本、准确命令摘要、目标/附件摘要、规则版本与数量、
   有效期、历史决定摘要和 `review_sha256`；本地原 command 文件共同构成可复核输入。
3. `run_owned_job` 回执有 64 KiB 上限。预览核心完整 review 可能包含大规则/历史，不能直接
   无界穿过管道；worker 计算完整审核摘要后只返回固定、受限摘要，不泄露 storage_key，
   不截断摘要计算的输入。完整规则由原命令文件审核，目录历史变化在 apply 时重新核对。
4. 操作者针对准确命令及审核摘要执行 `mapping-apply`；core 重算审核摘要，不一致即拒绝。
5. 使用同一命令、同一 review SHA 执行 `mapping-status`，核对决定 ID/payload SHA/审计 ID。
   `projection_published=false`、`start_ready=false` 保持原语义，映射批准不等于发布或期初启动。

### 截止

当前没有 cutoff preview helper，必须明确补充，不能把 `_capture_and_record` 后 rollback
称作只读预览，也不能将客户端离线数量当候选事实。首版 `cutoff-preview` 仅做以下检查：

- 当前真实操作者、精确命令解析、数据库/迁移/三角色配置可用；全程有总期限。
- 精确来源/映射 ID 与 SHA、相同 binding/catalog/region、当前有效期与证据、源捕获时间及日期。
- 原请求是否已经记录；有则证明并返回原 receipt，不生成新截止。
- 没有记录时返回 `inspection_only=true`、`capture_performed=false`、准确坐标摘要、
  服务端检查时间与可见有效期；不返回 `ready=true`、最终游标或对账成功结论。

新增 helper 应从 cutoff 核心抽取/复用坐标校验，避免复制另一套权限或有效性规则。全程无
ORM 新增/修改/删除、不调用 audit append，最后回滚。它可以持有短暂行锁，但不是零锁查询。

确认准确命令后，`cutoff-capture` 调用现有 `process_entry.execute`，不替换 `_cutoff_worker`：

- source 来自已发布控制源；owner 冻结 stock_accounts 参考和 inventory head 后由独立 ledger
  连接读取，游标必须一致；保持现有参考→账本头锁顺序，不新增全局业务锁。
- 源/本地捕获时间均必须属于给定上海日期；当前默认允许最大差 300 秒。执行中来源、映射、
  供应授权及身份需持续有效。不能在次日伪造前日捕获时间、自动放宽时差或使用 stale 来源。
- 核心采集上限仍为 100,000；首次入口不扩大。完整库游标/参考规模可能触及上限，超限停止，
  不分页截断成“完整日终”。
- 提交观察成功只表示截止已记录，receipt 的 `daily_reconciliation_approved=false` 与
  `stock_written=false` 必须保留；差异仍由既有页面解释和独立审核。

## 6. 原坐标恢复与结果未知

操作前保存不含凭据的原 command、审核 SHA（映射）、操作者标识、候选版本及请求号。
恢复必须回到同一个目标库、同一操作者、原 command 的全部字段；映射还需同一 review SHA。
鉴权仍使用当前有效会话和当前显式授权版本，不能为“恢复”跳过撤权。若原人会话过期，
经正式登录取得同一账号的新会话后再查；其他账号不能替他复用请求键得出“没有执行”。

`process_entry` 子进程异常、失联或没有完整回执均保守转为 unknown，包括部分实际上未提交
的错误。CLI 不解析数据库异常文本断言已回滚。COMMIT 后来不及回执也可能已经写入。
父进程退出、KeyboardInterrupt、输出写失败或容器终止同样保留原坐标，不启动第二次 capture。
进程 kill/reap 未确认时记录唯一工作进程身份供运维确认，不清理其他会话/进程。

| CLI 退出结果建议 | 含义与后续 |
| --- | --- |
| `0` | 观察到本操作回执；mapping/status 或 cutoff/recover 仍须检查 `recorded`，不泛称生成成功 |
| `2` | 启动业务 worker 前的输入/配置拒绝，或可证明没有执行业务写的只读预检拒绝；固定脱敏 code |
| `3` | 写操作或其观察结果未知/工作进程清理未确认；仅提示原坐标 status/recover，禁止自动重试 |
| `4` | 原坐标回读成功但 `recorded=false`；未观察到记录，不能解释为永久不会提交或允许新请求 |

既有截止 core 返回的 `outcome='committed'` 在 recover 时仅表示该读取事务完成，CLI 必须结合
operation 和 receipt 判断，不能将其打印成“新截止提交成功”。`deadline_exceeded_after_commit`
为 true 且已有完整 receipt 时保留已提交事实，不改报未提交。

`recorded=false` 不是封存：截止生成与日终映射都没有审核命令的 request-seal 能力。
应先确认原工作进程及对应数据库工作已结束，继续精确回读；重新执行、改请求号或换来源
属于明确的新运维决定，不在 CLI 自动分支内。不得把 review 的封存接口用于 cutoff/mapping。

## 7. 必须停止的条件

- 配置/镜像/迁移或实际数据库目标不符；任一所需角色缺失、提权、禁用、ACL/RLS 漂移。
- 缺真实当前操作者、权限/身份/会话失效、授权版本不符；不能自动补身份或提升权限。
- 精确来源/目录/映射/附件缺失、摘要错误、归属不符、目录或审核摘要变化、有效期不覆盖。
- 映射版本复用/窗口重叠、撤销对象错误、请求键冲突；原命令冻结，不能静默修改后重放。
- 来源不是已发布的完整可信记录、日期/时差不符、非目标范围未明确、数量或事实不完整。
- 超期限、超行数/大小/内存预算、无法取得必要锁或无法确认 COMMIT/进程退出。
- 缺少所操作真实对象的授权或仍只有合成证据。只阻断该对象，正常的查询/解释/审核独立继续。

## 8. 必要测试矩阵与落地顺序

以下均为**待补的入口验收**，现有核心历史测试不等于新 CLI/镜像已通过。
使用全新自有 PostgreSQL 16 合成库，完整升迁，不读取真实密钥或触发业务系统。

| 层 | 必测场景 | 断言 |
| --- | --- | --- |
| CLI 纯单测 | 非法/重复字段、重复 JSON 键、bool 授权版本、错误 UUID/hash、超限、注入参数、误传 token/DSN | 连接前拒绝；stdout/stderr/异常不含原始秘密；无自动执行/重试 |
| 配置 | 无秘密文件、错误目标、跨三连接数据库、PG15/17、迁移 HEAD 漂移、owner SET ROLE | 严格拒绝；不会借 API URL 或本地环境回退 |
| 身份 PG16 | 假 JWT、普通员工、失效 identity/session/IP 证明、过期/撤销、auth_version 改变、deny | 所有 preview/apply/status/capture/recover 入口均保持当前真实鉴权；零未授权事实 |
| 映射 PG16 | 真实预览/批准/回读/撤销；错审核 SHA；预览后目录或历史变化；未来/已过期时间；附件失效 | 映射决定和审计准确一对一；预览/status 不改变任何业务表或 audit head |
| 映射 PG16 | 两个相同请求并发、同键不同内容、重叠 grant、版本复用、目录写入并发 | 只有一个决定；冲突拒绝；现有锁顺序与目录完整性不被 CLI 改变 |
| 截止预览 PG16 | 有/无历史请求、源与映射不一致、日期/时差错误、角色故障 | 零截止/审计/库存写入；没有最终游标或虚假 ready 声明 |
| 截止全链 PG16 | 新截止、精确恢复、再次恢复、同命令重放、来源/映射撤销后的历史恢复 | 只有一个截止及对应审计；历史被证明；拒绝新无效截止；库存六表摘要不变 |
| 截止时间 PG16 | 上海午夜、两侧跨日、>300 秒、采集中来源/映射/操作者权限/会话到期 | 不伪造历史时点；提交守卫拒绝，保留原失败与恢复证据 |
| 三连接安全 PG16 | source/ledger 互换、owner 冒充、成员关系/列权限/隐藏写权限/RLS 变更 | 实际最小权限校验拒绝；无 owner 替代读取 |
| 故障与进程 | 三处建连停顿、锁等待、Python 卡住、SIGSTOP、父进程丢失、提交前后失联、半帧/超大回执、stdout 失败 | 总期限及最多 1 秒回收等待；连接退出；unknown 无重试；原坐标准确恢复 |
| 恢复边界 | 另一账号查询原键、原人新会话、权限已撤销、recorded=false、未确认 worker 退出 | 不跨人断言结果；不生成新请求/自动补跑；无虚假封存 |
| 容器与候选 | 固定 API 镜像中 module 可用、spawn 启动、只读挂载、FD/交互输入、信号传递、重启策略、网络隔离 | 精确最终工件实跑；API/Web 无 owner/reader 凭据，无外部业务访问 |

建议实现顺序：输入/配置与进程包装 → 映射预览/批准/回读 → 截止只读检查及 capture/recover →
新鲜 PG16 权限、并发和未知结果验收 → Compose 一次性入口及目标 Linux 容器 →
固定最终候选的完整静态/PG16 门禁。基线检查、角色/安全目录、旧日终审核和恢复测试一起回归。
新入口应把原有 artifacts 演练中的关键进程负向场景提升为正式可重复门禁，不能只用 Mock。

最后才按已批准的真实对象进行现场验收：真实映射证据及批准、可信来源发布、首日截止和
差异解释/独立审核，随后连续三天记录。现有 OAM 来源适配、OSS、身份与真实开账缺口分别
完成；本 CLI 不代替它们。每轮保留准确命令摘要、候选清单、迁移/权限核验、执行和精确恢复
回执、失败日志、进程/实例终止证据，并以不含凭据的文档交接。

本次只新增本设计文档；没有运行命令示例中的业务动作，也没有生成真实上线通过结论。
