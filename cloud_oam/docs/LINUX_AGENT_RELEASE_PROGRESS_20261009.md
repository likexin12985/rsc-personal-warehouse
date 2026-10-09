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

北京时间 2026-10-09 18:05:07 的新快照为 **66 success / 5 failure / 4 running**；新完成的 quantity correction_generations 为 success，未出现第三类失败。余下 migrations、inventory、static_safety (0)/(2) 仍运行。回执 `candidate-ci-2a49cbc/20261009T100503Z-bounded-readback-report.json`（SHA-256 `40c0832c8de65cf314b4290ba5e79cde30916053217c205eb8062e9ed911d741`）只覆盖旧已发候选；没有重跑终态节点或推送新候选。

北京时间 18:17:49 再次有界回读仍为 66 success / 5 failure / 4 running，没有新增状态变化；不能据此断言剩余任务卡住或超时。最终全局结论仍待；快照 `candidate-ci-2a49cbc/20261009T101744Z-pg16.json` 与回读报告已保存，没有反复下载旧失败日志。

北京时间 **2026-10-09 18:44:40** 的一次有界精确回读仍为 **66 success / 5 failure / 4 running**，run `37908563226` 为 `in_progress`；与 18:17 快照无 job 状态变化，没有新增失败，仍不能推断剩余任务卡死或超时。余下 migrations、inventory、static_safety (0)/(2) 尚未终态。原始快照 `candidate-ci-2a49cbc/20261009T104433Z-pg16.json`，有界报告 `20261009T104433Z-bounded-readback-report.json`（SHA-256 `97490a5b4cff2328236fe9effaff05e2901fe14c15f20b2d5be9e9fde9f31092`）绑定 `2a49cbc0f9d7715f492719affd8d659433ed9a6c`；没有重下旧日志、取消、重跑或推送，不替本地修复提交验收。

北京时间 **2026-10-09 19:29:50** 的 PG16 精确回读为 **67 success / 6 failure / 2 running**，run `37908563226` 仍为 `in_progress`：migrations 新近成功，inventory 新增失败，仅 static_safety (0)/(2) 尚未终态。新失败是直接耦合入库负例已被 0169 守卫拒绝，而测试仍只期待旧错误码；原日志有精确消息，23503 由未改动的固定 SQL 源码确认，不能说日志已直接打印 SQLSTATE。仅测试侧改为精确 `23503` + 完整 `diag.message_primary`，保留 raises、rollback 和事实回读。新增 **14 项 mock 聚焦通过、6.82 秒**，真实诊断插件 42 个阶段通过；冻结回执 `candidate-ci-2a49cbc/inventory-receipt-boundary-fix/freeze-receipt.json`（SHA-256 `977e737f03cd472e7cddf610054dfeadac8ed3dcd063a86fd8a2979c1ceade30`）已双审。没有重跑旧 inventory 全链或修改生产守卫；新候选真实 PG16 仍待验收。最新 CI 回读报告 `20261009T112943Z-bounded-readback-report.json` 的 SHA-256 为 `11bc15d5e0166d37d204786bfada903990a02f269ba9f2f6844d25ab0f3c989e`；不取消旧 run、不在其终态前推送。

GitHub 传输使用当前系统代理 `127.0.0.1:7890`。第一次 generic `http.proxy` 覆盖未生效，仓库旧 URL 专用代理 `11304` 优先而连接拒绝；精确回读确认远端未变后，使用单次 `http.https://github.com.proxy` 覆盖正常推送成功。未改变永久代理配置，后续仍须先核对当前有效代理。原失败和成功/远端 SHA 回执分别保留。

## 官方 Agent template 的独立有限实验

锁定 OpenBao 2.7.1 与实际依赖 openbao-template v1.0.1 的只读源码审查表明，可以复用官方 template 投影 OIDC JWT；OSS/PNVS 必须使用两份独立 auto-auth entity/Agent，单 Agent 两个模板不会产生独立 subject。template 使用无 leaf 前缀的临时文件，与当前已验证的 file-sink 临时文件合同不同，不能直接扩大白名单。研究和固定来源见 `artifacts/agent-template-oidc-review-20261009/assessment.md`；这是方案可行性，不是正式部署证据。

随后执行三轮有界合成实验，**没有一项 template 运行检查通过，模板 Agent 尚未启动**：第一轮 `cbc9de3aa044` 在目录初始化失败，先 chown 后 chmod 与 keeper 无 FOWNER 的边界冲突；仅改为 chmod→chown，不加 capabilities。第二轮 `258346e7b70c` 已实际完成目录准备和合成 Bao 初始化/解封，停在合成身份准备；首次固定失败码未保留端点信息，不能猜测具体原因。第三轮 `2cbfdded5078` 仅补非密白名单 method/path/status，明确定位为 `PUT /v1/sys/audit/template → HTTP 400`；此前初始化/解封/健康请求为 200。官方固定 SDK 也使用 PUT，因此不得把方法错误当作已证根因，当时具体审计拒绝原因待后续安全诊断；新源码定位见下一段。不取消审计、放宽权限或将这次失败说成模板不支持。

后续对同一固定 commit 的只读源码与冻结配置交叉核验，已确定一个先行静态阻断：`UnsafeAllowAPIAuditCreation` 默认为 false，本实验未开启；`handleEnableAudit` 在 file backend 的 Factory/open/test 之前直接拒绝，错误映射为 HTTP 400。该证据能解释已观测结果，但原 attempt-03 的服务端错误正文仍未采集，不补写为已读原始原因。下一实验候选采用独立 `template-server.json` 的声明式 file/template 审计，并显式保持 unsafe 开关 false；controller 去掉 API enable，先用受控 GET 列表及审计文件元数据确认设备实际可用再启动身份/Agent。固定来源在 `artifacts/agent-template-oidc-review-20261009/audit-endpoint-static-20261009/source-receipt.json`；新 JSON 是草案，不算运行通过。

最小修复随后已形成候选 04：新增 19 项离线检查通过（含纯函数、模拟元数据负例和 AST/接线，不能与旧 19 项真实 Linux sink 证明混计），61 个冻结输入经独立及协调任务只读复核一致。`source-freeze-04.json` SHA-256 为 `50538a530d065e76d88c609f1c2b319ea73fb89375f62e3f58e6eeed13df2683`；原三轮源码和回执保持。后续运行结果必须另记，静态检查不提升 template/OSS/PNVS readiness。

三轮临时容器/卷和上传目录都已精确清理并独立回读，原六个服务的 ID、StartedAt、health 和 OOM 状态保持；各轮源摘要稳定，诊断文件均为空，合成秘密仅使用内存/私有管道/tmpfs。没有正式初始化、云端资源操作或第四次启动。实验源全部位于 ignored artifact，不修改产品配置；原 sink 的 19 项终态证明未重跑。完整收尾为 `artifacts/agent-template-oidc-review-20261009/experiment/outcome.md` 与 `bounded-outcome.json`（SHA-256 `526ec41a61dd0045503367421da81fd73d3a48bd6c0c9e1f867d09fdfc3ca111`）。正式 OIDC 投影、真实 RAM→STS→OSS/PNVS、短信/UAT 门禁仍未通过。

候选 04 随后单次有界执行为 `64d47c8f1705`，**26.822 秒、exit 1、0 项通过**；停在 `server_start / server_deadline`，20 秒等待期未得到就绪回执，尚未进入初始化、声明式审计、身份或 Agent。该次没有保存足以判定根因的启动状态/错误正文，原始根因记录保持 unknown；不能用后续证据回填为当时已观测配置解析错误，或声称前一 API 400 已在该次运行中解决。2 个容器、5 个卷与上传目录都已精确清理，原六服务的 ID、StartedAt、health/OOM 前后一致，61 个冻结输入稳定。原三轮证据没有覆盖；本轮源与关闭回执保存于 `experiment/attempt-04/`，`terminal-receipt.json` SHA-256 `4ce24b9bd4a1754dd3cb5e46dbd48ee12231a93cabae0a743dfadff48758bb3a`。截至该次初步只读核对，仅确认新配置上传/hash/只读挂载/启动路径一致、二进制匹配原官方清单，尚未发现可确定根因的新静态问题；此后发现的固定 HCL 缺陷及新诊断见下文。原 attempt-04 缺少启动时的白名单容器退出/OOM 状态、UDS 元数据和连接 errno；detached Bao 的日志关闭，通用轮询吞并错误，不能从空 RPC 回执区分这些原因。最小诊断方案已存 `experiment/startup-diagnostic-plan-04.md`（SHA-256 `19f69f5b7e9c609532ef3f6a79b724e8955d720dd2ea04efdbb0030d85d91329`）；当时仅准备后续启动/seal-status 诊断，不自动重放原尝试，不初始化/解封/建身份，不扩权限或关闭审计。

后续对 OpenBao 锁定的 `github.com/hashicorp/hcl v1.0.1-vault-7`（commit `02db4972906a1b43a46e2ffb0d2aae2c71875d94`）继续只读核对，已经发现原审查遗漏的配置缺陷：JSON flatten 对值全部为对象的层继续压平，原 template 层仅含 options 对象，因而形成 `audit/file/template/options` 四级 keys；过滤 audit 后余下三级，不能满足 OpenBao `parseAuditDevices` 的两级类型/路径要求。官方示例的 description 标量会阻止 options 继续升格。六份固定来源及 Git blob 已核验，回执为 `experiment/startup-diagnostic/upstream/hcl-source-receipt.json`；未伪称执行了 Go parser 或验证整个模块归档。此新静态发现不改写原 attempt-04 未保存错误正文的事实。

随后独立 startup-only 诊断 `0403aa17a5bc` 已取得真实失败终态：**20.134 秒、Bao exit 1、OOM=false、0 项通过**，固定错误分类 `config_parse_audit=1`、`audit_type_missing=1`，清理前 socket 不存在且连接为 ENOENT。启动输出采集完整、未超限，原始正文仅在内存分类，没有持久化；本次已确认运行失败类别，不能继续描述为尚无新静态问题或当前完全未知。2 个容器、1 个卷及上传目录精确清理，原六服务保持，24 个冻结输入核验通过；没有初始化、解封、创建身份或启动 Agent。新回执 `experiment/startup-diagnostic/attempt-01/terminal-receipt.json`，SHA-256 `48afe3e4aa9c84372115e3ddfcde345e469ec83ca05e026cdcb9dca1a8f9ed80`，与原 attempt-04 的历史回执分别保留。

随后仅在 template 审计配置补 description 标量，保持 unsafe 开关 false、原权限/资源限制和审计配置；独立 startup-only 验证 `07ce8a508e73` 已真实通过：**15.117 秒、exit 0、1 项通过**。seal-status 返回 HTTP 200，`initialized=false`、`sealed=true`、`versionMatches=true`，清理前 Bao 存活且 OOM=false。启动输出采集完整、未超限，37 个冻结输入稳定；2 个容器、1 个卷及上传目录精确清理，原六服务保持。没有初始化、解封、创建身份或启动 Agent。新回执 `experiment/startup-fix/attempt-01/terminal-receipt.json`，SHA-256 `1eb009b865c52a52d68015bf6c4f1e8e919bb8b9f9b5445f3f7c3ad2210d999d`；原失败回执保留。

上述 startup-only 的 1 项检查仅验收启动；后续模板链已在新的独立合成实例中完成，结果如下。

修正后的官方 Agent template 合成链已在真实 Linux 上通过：run `0f6c2982debd`，**90.209 秒、exit 0、14 项通过**。声明式审计实际注册、独立身份/最小 ACL、跨用途 403、JWT 签名/claims、最终 0440 文件及只读挂载、自然刷新/再认证、取令牌失败后的保留与同一令牌自然过期拒绝均有实际证据。两个用途各有 3 次成功登录，扣除 1 次人工基线后为 2 次 Agent 登录，各 4 次成功 GET；各观察到 5 对原子 rename。日志采集完整、未超限，无 JWT/已知合成秘密匹配，原文未保存。5 个容器、5 个卷及上传目录已清理，原六服务不变，88 个冻结输入保持。回执 `experiment/template-description-candidate/attempt-01/terminal-receipt.json`，SHA-256 `e7eed7ca260575aea792558d5653b87ddddf230fa2100f225b095601e43aba5e`。瞬时临时文件的 0600 模式没有采到，不得描述为已实测；正式投影读取守卫适配仍待完成。此为合成隔离证明，未配置正式 Bao/Agent 或真实 RAM/STS/OSS/PNVS，未生成正式恢复材料，试点仍为 not_ready。

为保证本次验收可靠，候选另补两项验证脚本缺口：日志读取/关闭异常必须阻断通过，审计响应存在 data.error 或缺少有效 token 字段不得计为 GET 成功。固定源码确认 EntityID 不做 HMAC，data.token 可为 HMAC 字符串，只检查其存在性而不输出值。13 项第一阶段离线证据原样保留，第二阶段仅运行新增审计反例和受影响来源绑定的 23 项检查；两者均不冒充实际 Linux 的 14 项结果，旧终态套件没有重跑。

## 用户恢复安排与现场依赖

用户本轮明确决定：**由本人保管全部恢复材料，保存到已连接的希捷移动硬盘**。因此不再等待第二名人员，也不得写成双人分持、独立复核或已有第二副本。单人、单盘事实应一直保留在恢复记录中。

已只读核验 `/Volumes/Seagate Backup Plus Drive` 为实际 USB/APFS 外盘、UUID `4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC`、可写；卷本身未加密。准备工具仅新建独立 AES-256 小型映像，不能修改整盘或在未挂载时创建本地替代目录。`hdiutil -agentpass` 不保证 GUI；本机非交互创建未成功，后续密码由用户在获准的系统界面亲自输入。

首次空容器创建 `7ab884150e2e` 返回失败；用户确认没有看到密码窗口。随后精确 `status` 回读 `imageExists=false`，未生成或写入任何正式恢复材料。源码在创建进程期间有末尾防拔盘补强，故该创建尝试不声明与最终源码摘要一致；后续用冻结 `status` 独立核验。原生磁盘工具接管曾被电脑操作工具拒绝；该历史失败保留。

后续磁盘工具应用授权实际生效，GUI 在已核验原目录中创建此前不存在的 `RSC-recovery.dmg`，报告操作成功。实际为 **100 MB、AES-256、Journaled HFS+、UDRW**；大小由 GUI 切换格式后变为 100 MB，不再记为原计划 64 MiB。仅该新映像被精确卸载，物理希捷盘和其他映像未卸载。关闭后 `isencrypted` 返回 `encrypted=true`，`imageinfo` 独立返回 `CEncryptedEncoding / AES-256`，冻结 helper 的只读 `status` 返回 `encrypted_detached`，两次 SHA-256 一致为 `d204aa49c2ec72b7c7da52d1c85274ecf3e1cec9c976ee271f77c3c68cb668eb`。挂载时 `isencrypted` 曾返回资源暂时不可用，关闭后成功；这不是未加密证据。映像宗卷 UUID 为 `67A771D2-90A4-3F23-B837-F2EB70BFC7F8`。

**首次 GUI 创建时没有观察到密码设置与保管过程，当时待用户确认并实际重开解锁；未生成正式恢复材料。** 不把 GUI 成功、加密头或 passphrase-count 当作用户持有密码的证明，不读取钥匙串或要求聊天发送密码。新证据在 `artifacts/linux-agent-runtime-20261009/recovery-vault-01/gui-{image-diagnostic,closed-verification,frozen-status}.json`；外盘另存不含秘密的 `gui-creation-receipt.json`，原失败 receipt 未覆盖。后续每次写入并关闭后须重记摘要，当前摘要仅对应空容器。

随后通过磁盘工具精确打开同一文件，系统重新挂载为相同宗卷 UUID，但仍未观察到密码窗口/本人输入。此处只证明当前系统会话能够重新打开，**不能证明保管人持有密码或冷恢复可用**。再次仅卸载该映像后，加密头仍为 true，新的封闭摘要为 `fd9a84e061039721b06951f483a8a64c67adbd8b78ae472bf205cde0bacaf4e3`，覆盖初次关闭摘要作为当前字节身份；挂载生命周期导致摘要变化不作内容被篡改的推断。回执 `recovery-vault-01/gui-reopen-observation.json` 和外盘 `gui-reopen-receipt.json` 保留这一边界；映像当前关闭，仍没有正式材料。

用户随后明确确认密码已经设置并由本人保管，并按交接命令报告已完成加强密码修改；未提供、读取或保存任何密码值。改密后 `status` 仍为 `encrypted_detached`，摘要为 `17d788a36051bdb1a10970b1a741a2045198ba59c60d6de8a752dc189c6a6125`。GUI 随后重新打开同一宗卷 UUID，系统自动解锁，仍不能算本人独立输入新密码的恢复证明；精确关闭后摘要为 `60b3472fcdc673f541ef79d967270ed9c17fb6f736f0a24e45c73ab133bbbd68`。已交由本人在自己的终端用 `hdiutil attach -readonly -stdinpass` 直接交互输入新密码（无管道/参数/聊天传密码）；该系统选项在 tty 使用 `readpassphrase(3)`，不由代理读取 stdin。实际输入成功及只读挂载回读仍待完成。证据 `recovery-vault-01/password-{custody-handoff,change-readback,change-gui-reopen}.json`；正式材料仍未生成。

本人随后回复“新密码解锁成功”，并澄清对“关闭”是否指终端窗口存在理解差异。协调任务的精确回读为目标映像匹配数 0、目标挂载点不存在，冻结 helper 为 `encrypted_detached`；加密头与上次关闭摘要 `60b3472f…bbbd68` 保持。本人反馈保留，但没有取得成功时终端非密结果和只读挂载后态，因此手动解锁/只读挂载验收继续为未核验，不能直接升级为通过，也不推断密码错误。未重放 attach、未新建/覆盖映像、未读取密码；下一步仅核对本人终端末尾非密码输出。回执 `recovery-vault-01/manual-unlock-reconciliation.json`。

此前阿里云 RAM 角色页返回 `ConsoleNeedLogin`，当时的角色清单未知。用户随后确认原 Edge 已登录；重新验证原资料/9224 归属并刷新同一 RAM 页后，北京时间 **18:04:30** 实际列出 3 个服务关联角色（SmartService、ResourceMetaCenter、DNS），总数 3、1/1 页，无 RSC 专用角色，错误提示已消失。这确认 RAM 会话恢复，不代表 OIDC/OSS/PNVS 已配置。原始脱敏 DOM 回执 `recovery-vault-01/ram-session-restored-20261009.json`（SHA-256 `518804dd79c20780d3141c0ed8d33b20560b91eda659f42c1ac146abdd3553bc`）；没有导出凭据、创建云资源或发送短信。

北京时间 18:13:02 同一 RAM SSO 页已切换并精确回读 OIDC 标签，列表为“没有数据”、1/1 页且无登录错误。故本账号当前没有可见的 OIDC 身份提供商；不能把“已登录”当作云身份已接通。非密回执 `recovery-vault-01/ram-oidc-provider-readback.json` SHA-256 为 `6609a022a98d99b32ccaaa478a613546f7f08fb82ba09cc8fd0aac075f3d9bcb`，没有创建任何身份提供商/角色。

### 本人新密码解锁与空映像只读挂载已验收

本人最初提供的终端输出实际为 `hdiutil chpass`，前述“没有挂载”来自尚未执行 attach，并非密码错误。随后本人在自己的终端运行 `hdiutil attach -readonly -stdinpass` 并提供正常设备/挂载输出。协调任务精确回读唯一目标映像，宗卷 UUID 为 `67A771D2-90A4-3F23-B837-F2EB70BFC7F8`，`Writable=false`，内核 `ST_RDONLY=true`。

确认父设备属于同一映像且不是物理外盘后，仅关闭该映像。关闭后状态为 `encrypted_detached`，希捷外盘仍挂载且 UUID 保持；映像摘要前后均为 `60b3472fcdc673f541ef79d967270ed9c17fb6f736f0a24e45c73ab133bbbd68`。没有读取或保存密码，没有生成正式恢复材料。

本次回执为 `recovery-vault-01/manual-unlock-verified.json`，SHA-256 `af9e6eed2e3873f9f1de5cdf43b05629ece611c40923ceeaa3fe954dba041e6a`；外盘同目录保存同摘要的非密 `manual-unlock-verification-receipt.json`。上文密码/挂载“待验证”均保留为历史中间态，由本节覆盖。**空容器的本人密码解锁和只读挂载验证已通过**；正式材料封存、受控重启/离机恢复、第二副本、UAT 和上线仍需各自验收。

## 后续执行顺序

1. 已推送的 `2a49cbc` 客户端及 0181 hosted leg 已成功；保留本地 `122e5b8` 的 24+1 项直接证据、本批库存修复的 14 项 mock 与各自双审，收齐旧候选 PG16 全局终态后再推送下一候选，验收新 SHA 的独立 hosted 结果，不重跑无关终态检查或以局部通过放行。
2. 空加密恢复容器的本人解锁和只读挂载已验收；保留已通过的 startup-only 和合成 template 证据，完成正式投影读取守卫适配与正式运行配置验收，再初始化正式 Bao、登记恢复材料及两用途 wrapped registry/pins/运行身份，完成封存、受控重启和离机恢复演练。
3. 利用已恢复的原 Edge 阿里云会话核验或配置公开 issuer/JWKS、精确信任的 RAM OIDC、独立 PNVS/OSS 角色和私有 Bucket，实测身份回读、跨窗口刷新及拒绝边界。配置解析不是云身份通过。
4. 最后完成真实 SMS-only 登录、正式人员唯一映射、分角色/真机主链 UAT、附件私有访问、DB/密钥/附件恢复及发布回滚，证据绑定后执行 prepare/start 和域名切换。

范围保持申请/提交→审批→最小货源分配/占用→后台人工履约并记录发运→本人收货→个人仓入账。拣货和全部已延期后置区块保持隐藏，保留底层迁移/契约/依赖。真实短信登录为首发必要项；短信/微信/飞书业务通知及投递运维仍属后续迭代，不因本次部署扩大业务范围。
