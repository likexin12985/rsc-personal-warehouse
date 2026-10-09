## 2026-10-09 最新接续：提供方运行入口已接通，部署未放行

**试点 MVP，不等同完整 V1；`not_ready`，仍未上线。** 本批已经把独立 provider claims/pins 和存量引用检查接入 Settings、认证/联系人请求工厂、API 启动、readiness 与 CLI。九项 OpenBao 非密配置由 API/gate 同源映射，默认仍关闭；请求只使用启动审核后的不可变快照，不跨请求缓存明文，不新增短信限流前的数据库事务。全部启动门禁通过后才发布 runtime；缺失/漂移/不可解仍失败关闭。

完整实现、逐阶段本地/本机 PG16 证据、失败保留、联系人预检补修与受控停机换钥限制见 [运行入口接线交接](PROVIDER_RUNTIME_WIRING_20261009.md)。本批不代表真实 Linux 身份/挂载、Transit/STS/OSS、0181 hosted PG16、SMS-only/UAT、恢复或部署已通过；未提交、推送或变更生产。后文“未接线”等旧记录是各时点历史，当前代码状态以本节和新交接为准。

# 2026-10-09 C2 与附件运行身份接续

**试点 MVP，不等同完整 V1；未上线，发布判定仍为 `not_ready`。** 本批基于 `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3` 的未提交改动。原工作树改动、失败记录和旧 CI 均保留。生产库、服务、端口和旧系统配置未切换。

## 已完成的代码与聚焦证据

| 范围 | 本批实现 | 验证与边界 |
| --- | --- | --- |
| 联系人密文 | 精确 v2 十三字段，绑定 provider、用途、环境、实例、key path、应用版本、Transit 版本和本人/request UUID；旧 v1 九字段、AAD 和历史 revision 双读保留 | `contact-v2-focused-receipt.json`：39 passed；真实 AES-GCM、合成解封 transport；不代表真实运行身份 |
| 认证重放 | 按不可变 `(purpose, application version)` claim 选择完整 Aliyun/OpenBao pin，不按 active provider 猜历史来源，无 latest/fallback | `authentication-claims-focused-receipt.json`：23 个独立节点通过；首轮 7 个 SQLite 时间比较断言失败已保留，仅修复并复验该 7 项 |
| 独立数据库读取 | 单条有界 SELECT、禁止 autoflush、只读非秘密坐标与 hash/time，拒绝跨 provider 冲突及不一致 pin | `authentication-reader-focused-receipt.json`：24 passed；尚未接入生产工厂 |
| 联系人前向迁移 | 新 0181 从真实 0180 catalog 冻结前序；只修改两项 contact guard 与 readiness，保留旧 migration、ACL 和累计业务守卫；任一 v2 当前/历史记录阻止降级 | SQL/SQLite 69 passed，迁移结构 11 passed；首轮 contact-0181 admission 为 10 passed/4 failed，失败原因已定位为 readiness/predecessor overlay 导入错误，修复后的 readiness 复验为 4 passed。真实 0181 PG16 正反例仍待执行，不能以静态结果替代 |
| 私有附件运行身份 | 显式 `oidc_role_arn`，保留临时凭据真实到期时间；单次签名固定同一身份，链接不越过凭据到期前 60 秒；不在 OIDC 失败后选环境凭据 | 新身份测试 31 个独立节点通过，另有真实锁定 SDK 的离线签名复验；全部使用合成凭据，未兑换真实 STS |
| 附件组合与发布预检 | API/工作进程共享显式身份工厂，缓存包含完整身份坐标；试点预检要求单一只读目录投影、同账号角色/provider 和静态凭据为空 | composition 13 passed；试点 preflight 18 passed。配置可解析不代表投影归属、身份续期或 Bucket 已验收 |
| SDK 异常脱敏 | 公共入口只输出白名单固定错误，无 SDK cause/context；未知 PUT 仍仅精确 HEAD 回读 | 新反例 12 passed；后续对最终异常边界只复验受影响的 4 个实际 SDK 签名节点，4 passed |
| OpenBao 私有连接 | 新增独立 Linux Unix socket transport，严格 UID/目录/socket/只读 token 文件与 peercred；固定两条解密路径、3 秒总超时、响应限额、严格 JSON、无重试、每次重读投影 token | `transport-focused-receipt.json`：99 个独立节点通过；Mac 为真实 UDS 协议，Linux peercred/只读挂载为模拟边界；真实 Linux 身份、token 生命周期及生产工厂仍未验 |

以上不直接相加为“总门禁通过数”：有不同源码阶段、重复的受影响节点和不同证明范围。C2 回执在 `artifacts/openbao-c2-20261009/`；OSS 组合回执在 `artifacts/deployment-20261009/oss-oidc-composition-receipt.json`，绑定 18 个源码文件及每次日志/XML 摘要。0181 的实时进度以本节后续补充和实际 runner 回执为准。

客户端恢复后新增了独立的 `production_openbao_composition.py`：它提供认证 claim 路由和 contact-v2 组合的显式、默认关闭工厂，校验完整环境/实例/用途/版本/Transit 坐标；历史 Aliyun 只在显式 loader 存在时读取，OpenBao 失败不回退、不猜 latest，旧 v1 contact pair 不完整即拒绝。聚焦组合、registry 和 transit 候选共 **149 passed**，随后受影响 C2 聚焦回归为 **262 passed**；回执为 `artifacts/openbao-c2-20261009/production-openbao-composition-receipt.json`。该模块未接入 `Settings`、API startup 或迁移，`productionReady=false`；七项真实运行 attestation、数据库身份/ACL、投影 token、真实 Transit/STS/私有 OSS 仍未验证。

0181 的静态证据已补齐当前 HEAD 聚合回执 `artifacts/deployment-20261009/contact-0181-static-evidence-receipt.json`，绑定 migration、catalog、三份测试源码、runner 和 XML/log 摘要。SQL/SQLite 为 **69 passed**，迁移结构为 **11 passed**；首轮 admission 为 **10 passed/4 failed**，修复后对同 4 个失败节点复验 **4 passed**，因此是跨两阶段 14 个独立节点最终通过，不能表述为一次 14/14 通过。该回执明确 0181 hosted PostgreSQL 16、ACL、并发和回滚仍未验证。

## 隔离 PostgreSQL 的资源阻断与回读

2026-10-09 01:07:31 的 quantity 运行已进入真实个人仓期初测试夹具，但在盘点数量 COMMIT 时触及 PG 容器 512 MiB 内存上限：后台进程 signal 9，cgroup `memory.peak=536870912`、`oom=1/oom_kill=1`。这不是锁竞态通过，也不能据此宣称夹具修复无误；竞态函数尚未到达，serial 未启动。

01:09–01:11 精确回读确认仍为同一 cluster/head0180、已经恢复；失败 count 事务未提交。此前合成基础人员/物料/账户/投影和一项 counting 状态任务已经提交，observations、countlines、submissions、reviews、postings 均为 0；库存交易/流水、申请、发运、收货和 loss seal 均为 0。没有把已有合成基础行说成全库空白。

随后 exact 四个自建容器正常清理，PG SIGTERM/exit 0/shutdown 日志已保存，**仍保留 priorOOM=true**。原六个业务容器 ID、启动时间、健康与 OOM 值均保持；未删生产资源。原始证据使用 `loss-key-quantity-625eb223e032-` 前缀，包括 `post-oom-resource-facts.json` 和 cleanup receipt。API 退出后 cgroup 已移除，无法取得峰值，明确未知而非估算。

下一轮验证改为 C2 最小人员/物料/申请数据，避免让联系人兼容验证依赖整条盘点入账链；测试内存总上限仍 1280 MiB，重新分配为 PG 768 MiB、API 512 MiB，并单独记录 `jit=off` 的执行资源策略。该策略没有修改业务 SQL/权限/触发器，但其结果不能冒充原默认 JIT 的 hosted 环境。新运行前须重新核验主机余量和旧服务状态，当前文字不是新运行通过回执。

## 真实 SDK 发现与失败保留

锁定的 `alibabacloud-oss-v2==1.3.2` 没有旧代码调用的 `PresignOptions`，原模拟测试曾错误地提供该类型。现改为实际支持的 `expires`/`expiration` 调用，返回 SDK 的实际签名到期时间。

该版本 `presign_inner` 还会忽略操作级 `credentials_provider` 参数；首次临时身份测试观察到取了两次凭据。现为每个签名构造持有已验证临时快照的专用 SDK client，避免共享 client 的并发身份改变。失败的两项原日志保留，修复后仅复验这两项；新增公共异常边界后再针对受影响签名路径完成四项复验。HEAD/PUT 继续使用可刷新的 provider，不固定长期快照。

显式 environment 路径仅保留旧实现兼容；**新的试点部署预检不接纳此模式**。不把静态 AK/手工 STS 填入环境绕过续期条件。`deployment/oss-oidc.compose.yml.example` 是待核实目录归属后使用的模板，`create_host_path: false`，不会自动创建发行者、投影器或云权限。

## 密钥保管安排

用户本轮回复“按你推荐的来，我听你的”，已接受继续按手动解封、离机分开保管的方向准备。建议用户为主要保管人、落实另一位可信复核/保管人，并准备分开存放的离线加密介质。已发出具体人员和介质是否就绪的信息请求。

这不等于第二名人员已落实、介质已到位或恢复材料已交付。未生成正式恢复份额、未将恢复秘密写入聊天/仓库，未把一个人的两份文件记成双人复核。真实初始化与恢复验收仍须落实这些事实。

## 继续执行的必要条件

### 2026-10-09 认证组合 pin 绑定补充

此前 149/262 项属于修复前源码阶段。现已修复认证组合只核对坐标/Transit 版本而遗漏 claim 摘要的问题：构造、readiness、请求取钥均复用完整 ciphertext/context/AAD pin 验证和精确 entry 集合；取钥使用本次核验快照，历史 reader 拒绝完整 binding 漂移、provider 切换及未声明版本，合法混合历史读取保留。

新增 39 项先在旧代码上得到 31 failed / 8 passed，修复后该文件 57 passed（含原有 18 项）；独立审查再补真实 registry loader 正向控制，仅复验该同一节点 1 passed。各阶段源码摘要、退出码和原日志/XML独立留存，没有累计成 58 个独立节点，也没有重跑旧大套件。证据见 `artifacts/openbao-pin-binding-fix-20261009/repair-receipt.json` 及当前交接末段。

该修复仍是默认关闭组合的本地验证。下一步先补 provider-aware 存量当前/历史密文引用扫描与精确准入，再接生产 Settings/factory/readiness；不能通过直接启用新 provider 绕过旧引用检查。下列真实门禁仍全部独立保留，发布判定仍为 `not_ready`。

1. 完成冻结 0180 源码上的损失请求键真实 PG16 竞态复验，并在 0181 上独立执行真实 PG16 正反例、ACL、并发和回滚验证；0181 的静态 SQL/SQLite、迁移结构和 admission 证据已经完成，但不能替代 hosted PG16。旧成功迁移不因测试依赖遗漏而重跑。准确候选 hosted PG16 与 Client 结果分别登记。
2. 完成新 provider 的生产 Settings/factory、存量引用扫描、完整坐标 readiness 和 pin gate。当前 C2 service/reader 可用不代表这些入口已放行；不提前打开生产 OpenBao 配置。
3. 将有权限/归属证明、总超时和投影 token 的 Unix transport、OpenBao 服务/Agent、发行者 HTTPS/JWKS、RAM 信任及 OSS/PNVS 各自角色接起来。真实 token 续期、撤销、sealed/过期失败关闭必须实测。
4. 取得私有 OSS ACL/BPA/SSE/版本控制及前缀权限证据，验证真实附件；完成正式人员映射、真实 SMS-only 登录和角色/真机 MVP 主链 UAT。
5. 完成密钥、数据库和附件的隔离恢复及回滚演练，按最终候选准备镜像和部署回执后才切换。恢复不得删除已发生的库存或密钥事实。

试点主链、角色隐藏、最小站内通知和后续业务迭代清单保持原冻结范围。飞书知识源及业务通知真实渠道仍不在本次扩展范围；SMS-only 登录仍是首发要求。

## 2026-10-09 最新接续：存量引用扫描完成，生产取钥接线待验

`persisted_key_references.py` 已按独立 claim/pin 读取 Aliyun/OpenBao 存量目录：完整联系人当前/历史引用和严格未过期的终态认证记录，保留实际历史 CMK 与完整 OpenBao 坐标。显式检查 JSON 类型、schema/provider 配对和完整绑定，拒绝缺失/歧义/来源变化；只读、有界、无 autoflush 或解密。现有 `production_adapters.validate_persisted_kms_key_references` 已接该目录；API startup/CLI 仍限原 Aliyun 运行能力，拒绝未接线 OpenBao 或旧请求工厂不能读的历史 auth CMK。

新扫描 124 passed；现有入口聚焦 18 passed（15 deselected）；本机独占 PostgreSQL 16.15 的 6 表元数据与 API 只读角色实测 44/44 通过，进程 exit 0，64 文件无漂移，集群正常停止、数据日志保留。不同类型证据分开记录于 `artifacts/provider-reference-scan-20261009/milestone-receipt.json`，没有累计成完整发布门禁，也没有重跑旧终态套件。

下一批为请求作用域 claim/contact-v2 工厂、Settings/readiness 的一致接线。0181 真实迁移/正式 ACL/并发/回滚、准确候选 hosted CI、真实运行身份与 Transit/STS/OSS、SMS-only/角色/真机 UAT、恢复保管和部署回滚继续待验。**试点 MVP，不等同完整 V1；仍未上线，`not_ready`。**
