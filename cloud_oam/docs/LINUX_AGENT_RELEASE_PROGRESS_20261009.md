# 2026-10-09 Linux Agent 与上线条件接续

**试点 MVP，不等同完整 V1；`not_ready`，未切换生产。** 工作树为 `06f6/oam`，分支 `codex/notification-delivery-worker`；本批起点 HEAD 为 `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3`。候选已提交并推送为 `2a49cbc0f9d7715f492719affd8d659433ed9a6c`，原有改动全部保留并纳入候选；后续门禁修复与文档更新另行验收。本文中的本地、真实 Linux 隔离、hosted CI 和正式部署是分别验收的对象。

## 已完成的真实 Linux 运行边界

在用户已确认的杭州服务器 `118.31.37.87` 上执行新增一次性证明 `4beaeb8d75fb`，使用校验过的官方 OpenBao 2.7.1 Linux 二进制及服务器已有固定镜像，不拉取、不构建镜像。使用当时冻结的生产 `OpenBaoUnixDecryptTransport`，没有修改其权限/peercred 守卫。

- **19 项检查通过，exit 0，90.052 秒。** Bao / Agent / API 分别为实际非 root UID `23101 / 23102 / 23103`；共享读组 `23110`。
- API 仅获得两个只读 tmpfs 目录：socket 和 token。内核 mount 属性、实际写入拒绝、目录 `0750`、socket `0660`、token `0440`、属主和单链接均实测；API 没有 bootstrap 或 Raft 数据挂载。
- 为满足 `SO_PEERCRED` 的正 PID 守卫，API 使用 Bao 的 PID namespace，其他 UID、网络、挂载及 cgroup 仍隔离。实测 API 不能打开 Bao 的 `/proc/<pid>/environ`。正式部署必须保留这一可见性约束或提供等价已审方案，不能去掉生产 PID 守卫。
- 同一个真实 transport 两用途状态均为：初次 `200` → 官方 Agent 自然替换 token/inode 后 `200` → sealed `503` → 手动解封 `200` → Agent 停止且 token 自然过期后 `403`。
- 四个临时容器及三个 tmpfs 卷按标签、精确 ID 和成功列表回读清理；上传临时目录也已精确清理。原有六个服务 ID/running/health 与执行前一致。合计硬内存上限 608 MiB、swap 0；未使用业务库、业务卷、正式密钥或云凭据。

证据位于 `artifacts/linux-agent-runtime-20261009/attempt-01/`：`source-manifest.json`、`proof.json`、`readback.json`、`remote-cleanup.json`、`verified-receipt.json`。本证明是 **真实 Linux 上的合成隔离验证**，`productionConfigured=false`；它不是常驻正式 Agent、真实 STS/OSS 或上线回执。该终态检查不重复运行。

## 部署器与云身份接线

已修复预检只接受 Aliyun KMS、OSS 审查工具默认使用环境凭据的旧假设。OpenBao 使用现有纯配置和 wrapped registry parser；OSS 审查改用现有显式 OIDC provider。新增/直接受影响预检 47 passed、兼容节点 10 passed，分别留存来源、日志和 XML。

`pilot_release.py` 原先把所有 bind 内容当静态配置复制，这会错误保存动态 token 或 Unix socket，并让自然轮换破坏输入摘要。新增精确动态挂载契约：只验证目录/挂载身份和当前文件元数据，不读取、复制或摘要 token；静态 wrapped registry 仍完整摘要，并保留 API UID/GID、目录 `0700`/文件 `0600`。新增/直接受影响部署检查 51 passed，是本地合成元数据及协调器证据，不是正式 Linux prepare。详见 [身份目录接线](PILOT_LIVE_IDENTITY_MOUNTS_20261009.md)。

仍需把已验证的 Linux 边界落实为正式外部 Bao/Agent 与 API/gate 配置、准确 PID/container 绑定、正式 bootstrap、只读 host `/run` 目录和受控 registry。不能直接把证明脚本或临时测试镜像当发布配置。

随后部署专项已补齐三项边界：API/gate 全部能力清空与禁止提权；精确 64 位外部 Bao 容器 ID、镜像及真实 PID namespace 绑定；按官方 Agent file sink 的精确临时文件格式做有限只读复查。PNVS 使用独立 role/subject/audience/token 目录，允许与 OSS 复用同一 issuer 的 RAM OIDC provider，严格封闭 SDK 后续 profile/metadata/URI 凭据来源。模板和未验证项见 [PNVS 部署接线](PNVS_OIDC_DEPLOYMENT_20261009.md)。

新增 103 个专项节点：第一次缺 `PYTHONPATH=backend` 在 setup 失败；修正后 102 passed、1 failed，失败是测试直接调用原始 SDK 触发日志输出；将该参数化测试接到已有真实预检日志隔离边界后，两个受影响节点 2 passed。不能把重复节点相加，也未重跑旧 47/10/51 终态集。源码冻结及阶段回执为 `artifacts/oss-pilot-preflight-20261009/review-freeze-07.json`；最终提交前仍做独立只读审查。

provider 接线独立复核未发现新增明确代码阻塞：启动先校验 DB 身份/catalog、历史密文引用和独立 claims/pins，全部启动边界成功后才发布 runtime；请求在副作用前取钥，缺 runtime 或提供方失败即拒绝；固定错误不保留原 SDK 异常链。该结论仅覆盖审查的源码和已存在的分阶段证据，不替代正式密钥注册、角色/云身份、短信及恢复验收。

## 实际 Compose 合并的失败与修复

在目标 Ubuntu 已安装的 Compose `2.40.3+ds1-0ubuntu1~24.04.1` 上，只解析真实 base 加三个身份 overlay 的合成配置；使用空 Docker 配置目录、显式 `/dev/null` env 文件和独占临时目录，没有读取正式配置、启动容器或变更云资源。第一次因重复 `security_opt` 被真实解析器拒绝，记录在 `artifacts/linux-agent-runtime-20261009/compose-merge-01/`。

三个 overlay 的能力及安全选项列表已改用 Compose `!override`，最低版本 `2.24.4`（[官方合并语义](https://docs.docker.com/reference/compose-file/merge/)）；共享组保持追加。第二次真实解析 exit 0：API 三个共享 GID、gate 单共享 GID、一个 NNP、全部能力清空、精确 PID namespace、API 五个/gate 三个身份目录和 PNVS SDK 坐标全部符合。第二次本地结果解释器因读取省略的 `create_host_path=false` 字段抛出 KeyError，原始成功输出保留，没有因此重跑解析。

补充四种最小输入的真实解析及 JSON 再解析：显式 false 得到 `bind:{}`；true 和短语法得到 `create_host_path:true`；长语法未声明 bind 时仍无该字段；四者往返稳定。证据为 `compose-defaults-03/receipt.json`。据此回读第二次原输出得到 22 项结构检查通过、源摘要一致，记录 `compose-merge-02/adjudicated-receipt.json`，明确不代表正式 prepare/start。部署预检仅接受存在的 bind 对象中 false 的规范省略，缺 bind/非法类型/true/null/字符串均继续拒绝；不得全局将缺字段当安全默认值。

这一规范化兼容修复仅共用一个严格 helper，没有放宽只读、实际存在、tmpfs、来源/owner/权限或轮换检查；新增定向组 **14 passed / 0.982 秒**，源码前后一致，回执 `artifacts/oss-pilot-preflight-20261009/compose-canonical-08/receipt.json`。独立审查未发现新增实质问题；旧终态组没有重跑。候选提交与同 SHA hosted 门禁仍分别验收。

## 当前候选 PG16

旧 HEAD 的 [PG16 run 37805075682](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37805075682) 已明确 `completed/failure`：75 jobs 中 68 success、7 failure（6 测试 leg 与汇总门禁）；同 SHA Client run 成功。此结果不覆盖当前未提交候选，不能继续描述为运行中或缺 hosted DB。

六个失败 leg 已分类：四个 loss 夹具键锁等待、migrations 合法历史函数 hash 比较遗漏、inventory 已先触发 0168 发运事实降级保护而旧测试仍期待较早保护位置。保留生产保护规则；本批只补精确测试断言，库存、申请、发运、审计等 23 表的事实及 catalog 前后绑定。新增/受影响节点 20 passed，不等于 hosted 通过。

新增独立 `contact_envelope` hosted runtime leg，保持原有矩阵，不借 populated 库存夹具证明 0181。首轮本机真实 PostgreSQL 16.15 已完成空库→0180、部署 ACL、只读来源和 API v1 草稿，然后在 0181 报 SQL 语法错误。精确回读仍为 0180、申请/历史各 1、pin 与库存事实 0；本次 owned PG 正常停止、来源稳定。失败保留在 `artifacts/linux-agent-runtime-20261009/native-contact-0181-61lf11m0/`，不得计为 0181 通过。

修复限于尚未提交的 0181 两段 PostgreSQL function 的 `IF CASE` 表达式外加括号及对应冻结摘要；001–0180、SQLite/history 验证和业务守卫保持。随后精确恢复本任务的已停用隔离 PG16，校验同一 systemIdentifier、二进制、目录身份、0180 和既有 v1 事实，只续失败的 0181 边界，没有重做已通过的空库→0180/ACL/草稿创建。

**修复后真实 PostgreSQL 16.15 定向验证通过，32.732 秒、exit 0；1972 个输入文件前后稳定，本任务 PG 已正常停止。** 包括 v1 原样保留、0181 事务降/升及外层回滚、精确 `pg_blocking_pids` 证明迁移锁等待、释放后合法 no-op 接受而非法写入 P0001、两表 66 种守卫拒绝、14 项 42501 权限拒绝、当前 v2 与仅历史 v2 两种拒降且事实/catalog 不变，最终 head0181/runtime security 通过。它使用合成 pin，不连接真实密钥提供方，也不是 hosted CI。

新回执 `artifacts/linux-agent-runtime-20261009/native-contact-0181-_stmlu6h/receipt.json`，SHA-256 `da047e9ee1b54be2afec84aa4475e733c0cea53295f9882eaa5797b4e95d91a0`。完整测试/文件/hash/失败与证据分级见 `ci-candidate-contact-retention-final-receipt.json`。新语法回归使用锁定 `pglast==7.18`（内置 PG17 parser），仅作语法回归；真实 PG16.15 证据以上述独立运行计算。尚未取得本候选 hosted 终态，不发布。

恢复介质的 3 项既有安全测试已通过新的 `backend/tests/test_recovery_vault.py` 包装入口进入 CI；仅 `collect-only` 收集 3 项、唯一 static shard 0，不重复执行旧终态。原 helper、原测试、静态入口及 workflow 摘要保持，回执 `artifacts/linux-agent-runtime-20261009/recovery-vault-ci-collection-receipt.json`。

候选 `2a49cbc` 的 [Client run 37908563220](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37908563220) 已成功；[PG16 run 37908563226](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37908563226) 中 `contact_envelope` job `113747886604` 已成功，证明本候选的 0181 hosted leg 通过。PG16 全局尚未通过：`pg16_loss (quantity, submission)` job `113747887798` 已失败，当前从 HEAD 降至 0145 先被 0168 发运事实保护拒绝，旧测试却固定期待 0146 的报损历史拒绝。不修改生产迁移/保护规则，不取消或重跑旧 run，也不把单 leg 成功当全局通过。证据位于 `artifacts/linux-agent-runtime-20261009/candidate-ci-2a49cbc/`。

该共因已在工作树做测试侧最小修复：完整链要求 stderr 末尾精确的 0168 ValueError；原 0146/0147/0148/0156 downgrade 各在独立迁移事务中要求 `P0001` 与精确主消息，finally 显式回滚；完整链前/后及独立拒绝后，用三个新只读连接比较全部 public 普通/分区表完整行摘要、HEAD 和表/函数语义 catalog。native 回调保留真实 stdout/stderr 分流。新增 **24 项 mock 聚焦检查通过**，XML 为 0 failure/error/skip，5 个源文件前后摘要一致；独立只读审查未发现阻断问题，已复核5源/6证据摘要。冻结回执为 `loss-retention-fix-freeze.json`（SHA-256 `f16ef4247b43eba9ca817b403cba9b0a2e5cbe4c802771cf99fac8ffe064ee30`）。当前已发 hosted 候选不含这项修复；没有新真实 PG16 通过证据，仍需下一候选门禁。catalog 对比为语义结构，不能解释为 PostgreSQL 物理 OID/tombstone 全部不变。

随后 `pg16_loss (serial, review_seals)` job `113747888040` 也失败，完整日志确认是同一共享入口旧 0146 预期被 0168 遮挡，已包含在上述修复。另 `static_safety (1)` job `113747886436` 失败原因不同：PNVS 声明验证测试全局替换 `Path.read_text`，在 fixture 清理前误伤诊断插件的 cgroup 资源读取，触发 pytest INTERNALERROR。仅将该测试的文件读取禁令限制在 `monkeypatch.context()` 内，产品检查和诊断插件均未改；启用真实 `static_gate_diagnostics` 后只复测该节点 **1 passed**，JSONL 的 setup/call/teardown 全通过且 session_finish exit 0。新证据 `pnvs-path-scope-fix-freeze.json`（SHA-256 `cb6a0ff16dc53ca32e57b09f662117b582cf1919919d0cb10fa548d158bbbb9c`）不代表整个静态分片已经重新通过。

上述两项测试修复共 6 个文件经双审、摘要和直接受影响验证后，已本地提交为 `122e5b892f294ead01e0cb4580143c14400fbdca`，没有推送以免取消正在运行的 `2a49cbc` CI。提交回执为 `candidate-ci-2a49cbc/test-fix-local-commit.json`；当前交接文档继续更新，后续发布候选还须包含这些修复并取得独立 hosted 结果。

北京时间 2026-10-09 17:43:29 的精确快照：75 jobs 中 56 success / 5 failure / 14 running / 0 queued，尚非全局终态。四个 loss submission/review_seals（quantity/serial）失败均已完整日志确认为同一 0146/0168 预期共因，第五个是上述 static Path 补丁作用域问题；截至该快照没有第三类根因。快照 `candidate-ci-2a49cbc/20261009T094329Z-pg16.json` 仅对应 `2a49cbc`，不能替本地修复提交验收。

GitHub 传输使用当前系统代理 `127.0.0.1:7890`。第一次 generic `http.proxy` 覆盖未生效，仓库旧 URL 专用代理 `11304` 优先而连接拒绝；精确回读确认远端未变后，使用单次 `http.https://github.com.proxy` 覆盖正常推送成功。未改变永久代理配置，后续仍须先核对当前有效代理。原失败和成功/远端 SHA 回执分别保留。

## 官方 Agent template 的独立有限实验

锁定 OpenBao 2.7.1 与实际依赖 openbao-template v1.0.1 的只读源码审查表明，可以复用官方 template 投影 OIDC JWT；OSS/PNVS 必须使用两份独立 auto-auth entity/Agent，单 Agent 两个模板不会产生独立 subject。template 使用无 leaf 前缀的临时文件，与当前已验证的 file-sink 临时文件合同不同，不能直接扩大白名单。研究和固定来源见 `artifacts/agent-template-oidc-review-20261009/assessment.md`；这是方案可行性，不是正式部署证据。

随后执行三轮有界合成实验，**没有一项 template 运行检查通过，模板 Agent 尚未启动**：第一轮 `cbc9de3aa044` 在目录初始化失败，先 chown 后 chmod 与 keeper 无 FOWNER 的边界冲突；仅改为 chmod→chown，不加 capabilities。第二轮 `258346e7b70c` 已实际完成目录准备和合成 Bao 初始化/解封，停在合成身份准备；首次固定失败码未保留端点信息，不能猜测具体原因。第三轮 `2cbfdded5078` 仅补非密白名单 method/path/status，明确定位为 `PUT /v1/sys/audit/template → HTTP 400`；此前初始化/解封/健康请求为 200。官方固定 SDK 也使用 PUT，因此不得把方法错误当作已证根因，具体审计拒绝原因仍待下一批安全诊断。不取消审计、放宽权限或将这次失败说成模板不支持。

三轮临时容器/卷和上传目录都已精确清理并独立回读，原六个服务的 ID、StartedAt、health 和 OOM 状态保持；各轮源摘要稳定，诊断文件均为空，合成秘密仅使用内存/私有管道/tmpfs。没有正式初始化、云端资源操作或第四次启动。实验源全部位于 ignored artifact，不修改产品配置；原 sink 的 19 项终态证明未重跑。完整收尾为 `artifacts/agent-template-oidc-review-20261009/experiment/outcome.md` 与 `bounded-outcome.json`（SHA-256 `526ec41a61dd0045503367421da81fd73d3a48bd6c0c9e1f867d09fdfc3ca111`）。正式 OIDC 投影、真实 RAM→STS→OSS/PNVS、短信/UAT 门禁仍未通过。

## 用户恢复安排与现场依赖

用户本轮明确决定：**由本人保管全部恢复材料，保存到已连接的希捷移动硬盘**。因此不再等待第二名人员，也不得写成双人分持、独立复核或已有第二副本。单人、单盘事实应一直保留在恢复记录中。

已只读核验 `/Volumes/Seagate Backup Plus Drive` 为实际 USB/APFS 外盘、UUID `4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC`、可写；卷本身未加密。准备工具仅新建独立 AES-256 小型映像，不能修改整盘或在未挂载时创建本地替代目录。`hdiutil -agentpass` 不保证 GUI；本机非交互创建未成功，后续密码由用户在获准的系统界面亲自输入。

首次空容器创建 `7ab884150e2e` 返回失败；用户确认没有看到密码窗口。随后精确 `status` 回读 `imageExists=false`，未生成或写入任何正式恢复材料。源码在创建进程期间有末尾防拔盘补强，故该创建尝试不声明与最终源码摘要一致；后续用冻结 `status` 独立核验。原生磁盘工具接管曾被电脑操作工具拒绝；该历史失败保留。

后续磁盘工具应用授权实际生效，GUI 在已核验原目录中创建此前不存在的 `RSC-recovery.dmg`，报告操作成功。实际为 **100 MB、AES-256、Journaled HFS+、UDRW**；大小由 GUI 切换格式后变为 100 MB，不再记为原计划 64 MiB。仅该新映像被精确卸载，物理希捷盘和其他映像未卸载。关闭后 `isencrypted` 返回 `encrypted=true`，`imageinfo` 独立返回 `CEncryptedEncoding / AES-256`，冻结 helper 的只读 `status` 返回 `encrypted_detached`，两次 SHA-256 一致为 `d204aa49c2ec72b7c7da52d1c85274ecf3e1cec9c976ee271f77c3c68cb668eb`。挂载时 `isencrypted` 曾返回资源暂时不可用，关闭后成功；这不是未加密证据。映像宗卷 UUID 为 `67A771D2-90A4-3F23-B837-F2EB70BFC7F8`。

**密码设置与保管过程未观察到，正在等待用户确认并实际重开解锁；正式恢复材料仍未生成。** 不把 GUI 成功、加密头或 passphrase-count 当作用户持有密码的证明，不读取钥匙串或要求聊天发送密码。新证据在 `artifacts/linux-agent-runtime-20261009/recovery-vault-01/gui-{image-diagnostic,closed-verification,frozen-status}.json`；外盘另存不含秘密的 `gui-creation-receipt.json`，原失败 receipt 未覆盖。后续每次写入并关闭后须重记摘要，当前摘要仅对应空容器。

随后通过磁盘工具精确打开同一文件，系统重新挂载为相同宗卷 UUID，但仍未观察到密码窗口/本人输入。此处只证明当前系统会话能够重新打开，**不能证明保管人持有密码或冷恢复可用**。再次仅卸载该映像后，加密头仍为 true，新的封闭摘要为 `fd9a84e061039721b06951f483a8a64c67adbd8b78ae472bf205cde0bacaf4e3`，覆盖初次关闭摘要作为当前字节身份；挂载生命周期导致摘要变化不作内容被篡改的推断。回执 `recovery-vault-01/gui-reopen-observation.json` 和外盘 `gui-reopen-receipt.json` 保留这一边界；映像当前关闭，仍没有正式材料。

阿里云 RAM 角色页在原 Mac Edge 的 9224 兼容入口实际返回 `ConsoleNeedLogin`。角色清单为未知，不能把错误页的空表当零角色。已请用户在原 Edge 刷新登录；没有导出凭据、创建云资源或发送短信。SSH 连接成功与 GitHub 代理可用分别核验，不要求为此重登 SSH/GitHub。

## 后续执行顺序

1. 已推送的 `2a49cbc` 客户端及 0181 hosted leg 已成功；收齐该候选 PG16 全局终态，完成新增 loss 测试守卫修复的聚焦验证和独立审查。旧 run 结束后再推送下一候选，不以局部通过放行。
2. 在加密恢复容器实际可用且解锁验证后初始化正式 Bao、登记恢复材料、正式两用途 wrapped registry/pins 和运行身份；完成封存、受控重启、离机恢复演练。
3. 恢复阿里云会话后核验或配置公开 issuer/JWKS、精确信任的 RAM OIDC、独立 PNVS/OSS 角色和私有 Bucket，实测身份回读、跨窗口刷新及拒绝边界。配置解析不是云身份通过。
4. 最后完成真实 SMS-only 登录、正式人员唯一映射、分角色/真机主链 UAT、附件私有访问、DB/密钥/附件恢复及发布回滚，证据绑定后执行 prepare/start 和域名切换。

范围保持申请/提交→审批→最小货源分配/占用→后台人工履约并记录发运→本人收货→个人仓入账。拣货和全部已延期后置区块保持隐藏，保留底层迁移/契约/依赖。真实短信登录为首发必要项；短信/微信/飞书业务通知及投递运维仍属后续迭代，不因本次部署扩大业务范围。
