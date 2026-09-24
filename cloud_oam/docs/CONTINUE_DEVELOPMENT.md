# RSC 个人仓续开发与上线交接

更新于 **2026-09-25**。本页只保留现行状态；前 3,694 行接续记录已原样保存在
[历史交接归档](CONTINUE_DEVELOPMENT_HISTORY_20260921.md)，不得把历史“当前进程”当作仍在运行。

## 2026-09-25 当前门禁与目标机接续

以下状态覆盖下方尚未整理的 2026-09-24 增量描述。候选在现有 `06f6/oam`
工作树，分支 `codex/notification-delivery-worker`，保留所有未提交改动。
当前 Git 可见产品文件 1,679 个；相对本轮原冻结快照，业务逻辑未改动，
增量为两份 CI 测试工具依赖锁文件、两份 CI 契约测试、本页、正式基线缺口审计，
以及冻结迁移的单文件 Git 空格例外；共享小程序生成器只清除了类型剥离留下的行尾空格。
交接文档会继续更新，不在文档内固定包含自身的聚合哈希；准确文件清单与哈希
见 `artifacts/pilot-live-route-preflight-20260924/current-input-manifest-latest-20260925.json`。

- PostgreSQL 16 与客户端两份 GitHub 工作流现包含当前分支的 push 触发，
  `actionlint 1.7.12` 退出 0；拓扑 **4 passed**、客户端契约 **65 passed**，
  完整小程序 **1,071 passed**，仓库安全检查 1,685 文件 PASS，
  暂存与工作区的 `git diff --check` 通过。
  证据在 `artifacts/pilot-live-route-preflight-20260924/ci-trigger-local-evidence-20260925.json`。
  当前尚无此未提交候选的 GitHub 作业结果，也没有 PR。
- CI 的 `pytest`、`pglast`、`httpx` 及传递依赖新增 13 项哈希锁；本机按
  Linux x86_64/Python 3.12 下载并逐包验签成功，工作流改用 `--require-hashes`
  和仅二进制安装，依赖/拓扑聚焦 **7 passed**，`actionlint` 通过。
  目标机当前 API 预检镜像另在断网隔离容器内从这 13 个 wheel 强制重装，
  `pip check` 与三项测试工具导入通过，容器已清理；证据
  `artifacts/pilot-live-route-preflight-20260924/ci-test-lock-target-container-20260925.json`。
  该验证证明目标架构安装输入可复现，不代替 GitHub 实际作业或业务运行配置。
- 通知投递/运维聚焦 **78 passed**；本地全新 PostgreSQL 16.15 空库迁移至
  `20261119_0140`，运行角色/权限、九项合成短信配置与期初/报表完整流通过且实例已停，
  见 `artifacts/local-current-head-pg16/checks/run-qnu2gtts/checks.json` 与
  `cluster-state.json`。该检查 `ciReleaseGate=false`、仅合成存储，没有真实短信或 OSS。
- 三份完整 Python 静态片均已独立退出 0：**6,853 passed / 3 skipped /
  15 subtests passed**，覆盖 332 个互不重复的静态模块。原始日志为
  `artifacts/pilot-live-route-preflight-20260924/static-resume-20260925-shard-{0,1,2}.log`，
  三份退出码、汇总和 SHA-256 见同目录 `resume-20260925-run-bindings.json`。
  它们从本轮原 1,676 文件快照启动；随后新增的两份 CI 契约测试分别聚焦复跑通过，
  运行/测试依赖锁经目标架构隔离验证，文档不参与业务执行。
  原 1,678 文件清单在终态核对时逐文件零漂移；此后只做上述生成器/格式修正，
  受影响的后端 **13 passed**、完整小程序 **1,071 passed** 且生成一致性检查通过。
  当前 1,679 文件清单见同目录最新 manifest。这是本地静态证据，
  GitHub 当前候选门禁仍未运行。
- 目标 `118.31.37.87` 的当前 `linux/amd64` API 与 Web 隔离预检镜像已构建。
  Web 在仅绑定 `127.0.0.1` 的临时端口实跑：`/`、`/login` 为知识查询页，
  `/xx/` 为个人仓，`/xx` 返回 308；API 在非特权/只读/断网合成配置下加载成功，
  无生产数据库配置时按预期拒绝。探针容器已清理，五个原服务未变。
  证据分别为 `current-web-route-probe-20260925.json` 和
  `current-api-target-import-20260925.json`（同一 artifacts 目录）。
- 公网 `rscwz.cn/` 仍是旧“RSC个人仓”首页，旧认证选项密码启用、短信/微信关闭；
  新站没有切换。公开知识目录仍 `pending / 0`，正式公开发布检查继续拒绝。
  真实 KMS/PNVS/OSS、人员映射、期初、UAT、GitHub CI 和书面上线验收仍缺。
  不能把镜像预检、本地 PG16 或备案申请页误记为上线。当前未提交、未推送、未部署。
当前处于**本地候选验证和上线缺口开发阶段，尚未提交、推送或部署**。不能按测试项数估算上线完成比例。
最新目标机预检：公开 Web `amd64` 镜像已隔离构建；API 从官方 PyPI 两次因
`setuptools` 下载超时失败，后以官方 PyPI 生成的运行/构建工具哈希锁文件、
阿里云镜像站通过哈希下载，构建出最终仓库 Dockerfile 的隔离预检镜像。非特权、
无网络 `pip check` 通过。它们只证明目标架构和依赖可解，尚无准确候选发布回执、
真实配置、迁移、路由切换或业务验收。当前增量聚焦 **95 passed**；
旧公网仍把公开首页和 `/xx/` 落到密码登录，发布门禁继续阻断。
后续已从官方 PyPI 生成 72 项运行时哈希锁定清单，目标镜像站逐包哈希验证通过；
另将 `setuptools`、`wheel` 两项构建工具哈希锁定并禁用临时隔离下载。
最终仓库 Dockerfile 的 API `amd64` 预检镜像构建与非特权 `pip check` 通过，
当前增量聚焦 **95 passed**。见[依赖来源证据](PILOT_API_DEPENDENCY_PROVENANCE_20260924.md)。
目标服务器已由用户再次确认为旧异机备份脚本的 `118.31.37.87`。只读重查
`/opt` 现有试点目录均只有 `.env.example`，没有 RSC 专用运行配置；试点容器未启动。
目标独立预生产容器的 SQL 只读查询确认 PostgreSQL **16.15**，其业务库
`public.alembic_version` 当前不存在；这不是已迁移的试点数据库，不得将本地
空库门禁外推为目标库迁移通过。查询只返回版本和表存在性，没有读取业务数据或凭据。
本轮仓库安全扫描 **1,682 个候选文件 PASS**，`git diff --check` 通过。
首轮三片完整静态测试启动时系统 `PATH` 缺少 Node，并且开始后候选源又增加
依赖锁定文件，故该轮不能作为准确候选门禁。已把当时出现的 7 个失败节点独立
复跑：缺 Node 时 7 failed，加入桌面自带 Node 后 **7 passed**，均为运行环境
缺失而非这些节点的业务断言失败。`scripts/run_static_shard.py` 现于启动前检查
Node，以便下次立即发现该条件；原三片仍在运行，须等最终退出码和完整失败汇总。
GitHub PostgreSQL 16 的运行/静态作业已改为使用同一运行与构建工具哈希锁，
之后再安装测试专用依赖、执行 `pip check`；本轮 CI 拓扑/锁定聚焦
**7 passed**，工作流 YAML 可解析。测试专用依赖的传递版本仍未哈希锁定，
真实 GitHub 作业也尚未运行。
基础镜像摘要、正式 `prepare/start` 回执仍缺，
不能将预检镜像提升为生产候选。
全新本地 PostgreSQL **16.15** 重新升至迁移 `0140`，运行角色/权限及合成期初、
报表完整流通过，实例正常停止；证据
`artifacts/local-current-head-pg16/checks/run-bs81fmwf/checks.json`，
`ciReleaseGate=false`、`syntheticStorageOnly=true`。
完整证据和镜像 ID 见[双入口烟测与目标构建](PILOT_LIVE_ROUTE_SMOKE_20260924.md)。
本日后续增加[期初盘点 Excel 格式预校验切片](OPENING_COUNT_IMPORT_FORMAT_20260924.md)：
该轮 Git 可见文件 **1,663**，较下文 1,659 文件冻结快照新增三份后端源码/测试及
一份说明文档、改动两份后端源码及部署入口。模板、格式检查和不回显原始值的错误报告 API 已编码，
伪造 XLSX 行列尺寸的藏匿反例也已封闭，相关 **86 passed**，
仓库安全检查及 diff 检查通过；**任务/范围绑定、业务预校验、持久任务与确认执行未实现**。
下文 6,785 项、PG16 和 API/Web 镜像均属于此前准确冻结快照，不能当作这批新增代码
的完整放行证据。当前候选另在全新本地 PG16.15 空库重跑现有迁移至 `0139`、运行角色
和报表任务权限、九项短信配置夹具，结果 PASS、实例已停止；证据
`artifacts/local-current-head-pg16/checks/run-5reyib22/checks.json`，`ciReleaseGate=false`。
目标机确认后的部署入口将 Compose 构建并行度固定为 1，部署回执/漂移拒绝聚焦测试
**95 passed**。新候选须重新冻结、跑完整静态/客户端/PG16 发布门禁和构建镜像后才能提交。

## 1. 固定工作树与产品边界

- 工作树：`~/.codex/worktrees/06f6/oam`。
- 分支：`codex/notification-delivery-worker`；HEAD：`f713999d4693aedae11b5ebb379ef994aa50aeea`。
- 本地跟踪状态为相对 `origin/codex/notification-delivery-worker` **ahead 7 / behind 4**；
  远端通知运维提交需在当前未提交候选与完整门禁明确后逐项比对、无损整合，不能
  reset/revert 或直接覆盖远端。此处仅是现有跟踪引用的状态，未在本轮推送。
- 保留全部未提交改动；禁止 reset、revert、丢弃改动或切回旧工作树重做。
- 接手先完整阅读[正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)
  和根目录 AGENTS.md；基线是产品要求，不是外部系统写入许可。
- 用户最新要求：公开网站首页和个人主体小程序均为“交流备件知识大全”；网页保留星星后台
  按钮，进入 `https://rscwz.cn/xx`。小程序当前只注册 `pages/knowledge/index`，不发布私有登录/业务页。
- 知识目录当前 `status=pending`、0 条，真实飞书原表及公开性审核未完成，正式发布检查拒绝。
- 用户最新优先级：**暂缓飞书知识源，继续其他上线开发**。目录保持 `pending / 0`，不阻断受邀 `/xx` 试点开发；严格公开发布仍要求真实目录和公开性审核。
- 先前 NIO Chat CLI 在外部 shell 缺少 user identity、代理调用失败，不代表桌面登录失效。用户最新确认官方本机 `127.0.0.1:8765` 已授权且用户/通讯录/私聊只读已实测；后续按 `nio-chat-cli/references/hosted-read.md` 使用托管只读客户端，不再裸 CLI 重试。专用本机连接凭据仅内存使用，不导出企业/Desktop/tool token；其他域和业务写入不据此扩大。本任务仍暂缓知识源，没有复测该通道。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入账、通知和对账保持独立。
- 正式 V1.0 仍有真实来源/期初/UAT、报损报废与离职交接、报表导入导出/打印、真实渠道
  适配器及非功能门禁缺口；本轮[正式基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)
  将受邀 `/xx` 试点与全量正式上线分开列证据。
- P0 [库存 Excel 导出实施契约](REPORT_EXPORT_IMPLEMENTATION_PLAN_20260923.md)已有首批候选：
  `0135–0139` 为通用 `FileJob` 增加导出快照/结果字段、受控任务状态、专用 XLSX 文件
  用途及结果文件唯一索引；`report.export` 只授总部管理员与区域负责人。受限 Excel 渲染器及一次性私有
  对象写入组件已经落地。申请/快照/单任务领取、写后重验、结果绑定与受控下载服务已有候选；
  申请、状态、下载及只读能力/原键找回路由已挂载；业务入口由默认关闭的开关保护。
  一次性 worker 和可选持续轮询进程
  已有候选，Compose `reports` profile 默认不启动。真实 OSS/身份/期初及端到端验收尚缺，
  不能称为报表导出已可用。
- 2026-09-24 续作：正式库存页已接入权限及服务端能力双重门控的库存 Excel 申请、
  原幂等键找回、状态刷新和短时受控下载；后端补只读能力提示与申请回执丢失后的
  申请人绑定找回。未知 POST 结果不会自动重发；默认报表开关保持关闭。
- 用户提供的备案申请详情截图显示“审核意见：审核通过”、主域名 `rscwz.cn`、
  网站名称“RSC个人使用记录”和备案号 `豫ICP备2026043964号-1`；本地留存于
  `artifacts/pilot-filing-20260924/filing-review.png`（忽略目录，未提交）。截图只证明
  该申请详情页可见状态；最终备案状态、实际网站访问地址、首页内容与目标机 HTTPS
  仍须分别回读，不能把它当成部署或公开发布验收。备案页网站名称与拟公开首页标题
  “交流备件知识大全”不同，正式发布前按实际审核资料核对，不自行改写已备案名称。
- 目标持续推进至上线；用户要求**额度剩余 10% 时停止，汇报整体进度并更新交接**。
  用户已在 2026-09-24 明确要求恢复开发；最近一次查询周额度剩余 54%。

## 1.1 上一冻结候选的 0135–0139 报表基础切片和门禁

本次续作报表邻近后端 **26 passed**，前端完整 **81 文件 / 1,382 passed**；
TypeScript、公开与 `/xx` 构建和 `verify:pilot-release` 退出 0，知识目录仍
`pending / 0`。新增“回执丢失不重发”和“页面重载按原键找回”聚焦测试通过。
全新 PG16.15 空库 `--report-full-flow` 已对准确增量源码退出 0；证据
`artifacts/local-current-head-pg16/checks/run-9kw5kso6/checks.json`：HEAD `0139`、
申请 HTTP/原键找回、实际 API 角色 worker、文件绑定、状态/下载 HTTP 与库存事实
不变均通过，错误键被拒绝，`ciReleaseGate=false`；`cluster-state.json`
记录实例正常停止。这是本地合成对象/身份门禁，不能借此推断真实 OSS 或上线许可。详情见
[报表导出实施契约](REPORT_EXPORT_IMPLEMENTATION_PLAN_20260923.md)。
本次增量后的准确候选已冻结 1,659 个 Git 可见文件，三片静态测试各自完整退出 0：
**2,089 passed / 1 skipped / 15 subtests passed**、**2,409 passed / 2 skipped**、
**2,287 passed**，合计 **6,785 passed / 3 skipped / 15 subtests passed**；
325 个静态模块分片覆盖完整且无重复，独立破坏性 PG16 runtime 模块按门禁设计排除。
冻结前后文件摘要零漂移；证据 `artifacts/static-final-20260924/result.json` 与原始三片
日志。Web 81 文件 / 1,382 项、小程序 1,070 项、类型检查、公开及 `/xx` 构建、
试点静态检查和仓库安全扫描均退出 0。两类跳过的原因在日志中明确，第三方
Starlette/anyio 弃用警告不属于业务测试失败。
这证明本地准确候选的静态、客户端及合成 PG16 门禁，不等于 GitHub/目标机门禁。
分支仍相对跟踪引用 ahead 7 / behind 4，未提交、未推送；下一步无损核对远端分歧，
再取得同一候选的 GitHub PG16 runtime 与 Client 结果。
只读比较本地远端跟踪引用 `68573f1`：通知运维迁移及其主测试与本地工作文件一致；
本地 PG16 工作流已改为完整发现的三片矩阵，安全目录已扩展到后续迁移及采集角色，
均比远端对应文件更进。远端文档仍需逐段核对后整合，不能用覆盖或 reset 处理分歧。
本轮再读远端分支与 GitHub 两套工作流时连接 `github.com` / `api.github.com:443`
曾超时；随后只读重试确认远端仍为 `68573f1`，未见当前未提交候选的 CI 运行。
最近 PG16 失败运行属于旧 `822a3e0`，且没有启动 job，不能代表当前工作树。
本地两份工作流经官方发布包 SHA-256 核对后的 `actionlint 1.7.12` 检查退出 0，
证据 `artifacts/static-final-20260924/workflow-lint.json`；它不替代 GitHub 执行。
当前源码已在隔离 Linux VM 中构建为 API 镜像
`sha256:07674551b92f68d08f28e0f36bc82ce7f74014ecb1871a08ba284ecc947c1dd3`：
340 个源码输入、338 个镜像内 COPY 输入逐字节核对，72 个 wheel 核验；非特权、无网络、
只读容器中日终 CLI 与报表 worker 帮助入口通过，错误轮询参数和无配置运行均拒绝。
Linux Compose 5.5.1 实际解析显示 `reports` profile 默认不启用，启用后绑定当前镜像；
VM/容器已停止。证据 `artifacts/api-image-final-20260924/result.json`。
当前前端公开/私有构件与 Caddyfile 另在隔离 Caddy 2.10 容器中完成 16 条实际
HTTP 路由核对：`/` 与 `/login` 均为“交流备件知识大全”，`/xx` 跳转私有入口，
静态资源、缺失资源、Service Worker、知识目录及 API 鉴权代理按预期响应；
鉴权上游为本地替身，证据 `artifacts/caddy-current-20260924/result.json`，VM 已停止。
完整前端 Dockerfile 以 `RELEASE_PROFILE=pilot` 在同一隔离 Linux VM 构建退出 0，
Node 22 与 Caddy 2.10 基础镜像的官方摘要均已校验。Web 镜像摘要
`sha256:5a07763d5709ec6524e7e801e82ccc930812538239bce8ed112f949a3a5850dc`；
镜像内公开首页、`/xx` 首页、`pending / 0` 知识目录及 Caddyfile 与本地受检产物
逐文件 SHA-256 一致。镜像内 Caddy 配置验证及隔离 VM 本机 HTTPS 的 `/`、`/xx`、
`/xx/`、`/knowledge-catalog.json` 四条探针通过，镜像归档 SHA-256 为
`e1d2237607e17c7afb884c62f611f21a2e7cc0c76025d8964cc2e409307b2278`；
容器和 VM 已停止。证据 `artifacts/web-image-final-20260924/build-result.json` 与
`verify-result.json`。本地自签 HTTPS 不证明真实域名证书、备案终态或业务登录。
两份本地镜像均在 `linux/arm64` 隔离 VM 构建；2026-09-24 已只读核实目标机为
`x86_64`，归档不能直接当作目标机镜像交付，须在目标架构构建/拉取同候选镜像。
正式公开目录检查在 `pending / 0` 上按设计退出 1，证据
`artifacts/web-image-final-20260924/production-release-gate.json`；用户已降低飞书
知识源优先级，不能把试点 profile 的成功当作正式公开发布通过。
这仍是本地合成容器验证，真实 OSS、目标 Linux/HTTPS 和 GitHub 门禁继续缺失。
真实 OSS、目标 Linux/HTTPS、外部身份和期初/UAT 仍是受邀试点放行缺口，
GitHub 运行结果也不能由本地 PG16 替代。用户先前要求证据齐全后才提交，当前不满足。
用户最终确认旧异机备份脚本的固定 SSH 目标就是本次目标机；本机已有的固定专用密钥及
`StrictHostKeyChecking=yes` 已通过只读连接。目标机为 Ubuntu 24.04 / `x86_64`，
Docker 29.1.3、Compose 2.40.3，根盘 49 GiB、剩余约 35 GiB，内存约 3.5 GiB、
可用约 2.1 GiB。现有 `star-oam`、`edge-receiver` 及独立预生产数据库 Compose
项目在运行；旧 `star-oam-web` 独占主机 80/443，其 `APP_DOMAIN` 正是
`rscwz.cn`，现有 Caddyfile 将该域名的 `/api/*` 转到旧 API。当前 HTTPS 根页
只读核验标题仍为“RSC个人仓”。因此新公开首页须走**受控 Web 入口切换**，不能
并行占用同域名/端口；保留旧服务可回退。目标机上留有 2026-09-22 的旧试点源码
与镜像（清单最多 1,613 文件），不属于当前 1,663 文件候选，不得直接复用。
没有改动远端配置、容器、网络或数据；脱敏只读结果见
`artifacts/target-readonly-20260924/result.json`。实际部署、真实业务依赖及 UAT 仍未验收。

2026-09-24 再次只读回读该已确认目标：`x86_64`，根盘余量约 35 GiB、可用内存
约 2.1 GiB；`star-oam-web-1` 仍发布并占用 IPv4/IPv6 的 80/443。新候选没有
在主机启动。部署协调器的镜像检查已增加 Linux OS 与宿主 CPU 架构匹配要求：
在 `prepare` 构建后、启动数据库前以及 `start` 核对回执时分别拒绝错误平台镜像。
这只收紧镜像门禁，不代表当前 `arm64` 归档已变为目标可用镜像。回读的脱敏事实
另存于 `artifacts/target-readonly-20260924/recheck-085646Z.json`。
目标 Docker 实际 `image inspect` 对旧 API/Web 返回 `linux/amd64`，daemon
`Architecture` 返回 `x86_64`；新门禁检查的两个字段与目标平台格式一致。
合成部署正反例聚焦 **102 passed**，仅证明门禁行为，未构建当前候选目标镜像。

期初 Excel 业务预校验新增内部只读入口，复用正式实盘命令写入前的校验，
返回任务/授权版本、请求摘要和按原输入顺序定位的待核实项；工作簿另保留真实
Excel 行号。内部组合入口还核对预期源 SHA，将格式和业务预检接通，并将
待核实项写成无原值回显的真实行号错误。盘点命令回归 **194 passed**、
导入相关 **32 passed**，业务错误报告有 1,000 行硬上限并拒绝错位索引。
全新本地
PG16.15 的 API 角色以 `count_method=import` 预检后无事实变化、同一幂等键
正式提交，迁移 HEAD `20261119_0140` 与原报表/短信夹具通过，证据
`artifacts/local-current-head-pg16/checks/run-pylub4f5/checks.json`；实例停止。
目前尚无持久导入任务、私有 OSS 源文件/范围授权绑定、持久业务错误文件及确认 API，不能将
此只读预检当作可执行导入，更不能将本地 PG 门禁当作目标机/GitHub 验收。

- `0138` 为 `file_jobs.result_file_id` 建立唯一索引，阻断两条任务复用同一结果文件；
  升级前若已有重复引用则原样拒绝。SQLite 历史重复拒绝、升级后重复写入拒绝及空结果
  降级 **1 passed（786.52 秒）**。全新 PG16.15 空库升至 `0138`、安全清单、API 窄列
  权限、结果索引实测和 9 项短信夹具通过，证据
  `artifacts/local-current-head-pg16/checks/run-7r_5_s87/checks.json`，`ciReleaseGate=false`。
  2026-09-23 接续时再次用全新 PG16.15 空库复核相同 HEAD，结果通过，证据
  `artifacts/local-current-head-pg16/checks/run-l2dtbeea/checks.json`，仍非完整 CI 放行。
  Alembic 单 HEAD 与运行绑定哈希检查 **2 passed（486.15 秒）**。
  同一 HEAD 的独立全新 PG16.15 日终门禁也退出 0：1 条映射决定、1 条审计、3 个截止，
  CLI 映射精确回读与截止精确恢复通过，证据
  `artifacts/local-daily-ops-pg16/checks/run-kuogm4et/checks.json`，`ciReleaseGate=false`。
  全库 ORM/迁移对照与空库降级回归另行 **1 passed（462.22 秒）**。

- `0136` 仅授 API 运行角色 `file_jobs` SELECT/INSERT 和指定列 UPDATE，并用
  PostgreSQL/SQLite 守卫限制排队、领取、成功/失败与下载意图计数；`0137` 扩展正式文件
  守卫为专用 XLSX 目的、当前报表角色/权限校验，通用文件上传服务拒绝该用途。
  报表对象写入使用禁覆盖的单次 PUT、大小/MIME/元数据/ETag 精确 HEAD；失去 PUT 应答
  不自动覆盖重试。私有对象适配器 3 项聚焦测试通过，工作簿/用途等合计 13 项通过。
  新增库存报表行、正式范围快照、任务申请/领取/收尾、写后重验及单次 worker 入口；
  库存/报表/文件与 HTTP 接口聚焦合并 **56 passed**，包括结果未知后的 `running` 恢复、
  默认关闭的接口拒绝和结果文件隔离后的不可下载状态。测试中的身份、期初和 OSS 为合成
  替身；新增 `0139` 把结果文件幂等摘要与精确任务绑定。全新 PG16.15 空库升至
  `0139`、运行时安全清单/窄列权限和 9 项短信夹具已通过，证据
  `artifacts/local-current-head-pg16/checks/run-4i1ebvxz/checks.json`，`ciReleaseGate=false`；
  新增 API 运行身份伪造 `succeeded` 导出任务的真实 PG16 负向反例后，再用全新实例
  重跑通过，证据 `artifacts/local-current-head-pg16/checks/run-hd_yxur0/checks.json`：
  `reportJobForgedCompletionRejected=true`，迁移 HEAD `0139`，实例已停止；
  首次反例脚本因 JSON 字面量被 SQLAlchemy 误解析而失败，记录在 `run-9ourbg8o`，
  修复参数绑定后才取得上述通过结果。该反例不替代完整的申请/收尾/下载运行角色业务测试。
  再用全新自有 PG16.15 实例验证真实 API 角色的有效排队→领取→失败状态轴，以及
  跳步完成、修改冻结参数、停用申请人再提交的拒绝，库存交易和余额不变；
  `artifacts/local-current-head-pg16/checks/run-mfbt8ix6/checks.json` 为该阶段通过证据，
  `cluster-state.json` 记录实例停止，`ciReleaseGate=false`。这仍不覆盖真实导出文件完成/下载事务。
  更进一步的全新实例以真实 API 角色完成合成 XLSX 文件 `pending→available`、任务
  `running→succeeded`、下载意图计数加一，并拒绝计数跳增；证据
  `artifacts/local-current-head-pg16/checks/run-7uq_h6rb/checks.json`，实例已停止。
  前两次扩展门禁 `run-amteoxqf`、`run-86o_rua2` 因夹具未使用事务数据库时间而失败，
  修正夹具后才取得通过。该门禁未调用真实 OSS 或 HTTP 下载意图服务。
  接续门禁 `artifacts/local-current-head-pg16/checks/run-wlug5e73/checks.json`
  在实际 `star_oam_api` 角色上调用正式报表状态与下载意图服务，验证合成私有对象
  HEAD、短时签发、计数及同事务审计，并拒绝同请求标识重放、停用申请人再次查询/
  签发和计数跳增；退出 0，`cluster-state.json` 证实 PG16.15 实例停止。
  此阶段证据仍使用合成身份/文件/OSS，`ciReleaseGate=false`；申请 HTTP、真实
  OSS 及目标机链路未验，报表入口保持关闭。
  同轮报表读取/下载及 `0135` 升降迁移回归以 bundled Node `PATH` 运行，
  **29 passed（1130.94 秒）**；其中迁移全图用例耗时较长，终端退出 0。
  新的全新 PG16.15 门禁 `artifacts/local-current-head-pg16/checks/run-8cm79ymz/checks.json`
  将正式状态和下载路由接到实际 API 角色会话：HTTP 正向签发使计数/审计各到 2，
  同请求重放返回 409，停用申请人状态/下载均返回 403；`cluster-state.json`
  记录实例正常停止，`ciReleaseGate=false`。路由聚焦 **3 passed**。
  仅隔离测试应用临时开启依赖，生产默认开关未变；正式期初支持的申请/worker、
  真实私有 OSS 与目标 HTTPS/CI 仍缺。
  可选 `--report-full-flow` 门禁复用完整期初夹具；首轮
  `artifacts/local-current-head-pg16/checks/run-bwwf7ghw/failure.json` 因一个可见库位
  未建立期初而按设计拒绝报表申请，实例已停止。补齐该库位的实盘、两级审核、过账
  与关闭后，`artifacts/local-current-head-pg16/checks/run-m04005fm/checks.json`
  通过申请 HTTP 幂等回放、实际 API 角色 worker、XLSX 私有文件绑定、状态/下载 HTTP、
  SHA-256、审计与库存事实不变；PG16.15 实例正常停止，邻近回归 **31 passed**。
  传输仍为合成私有对象，身份/期初为本地夹具，`ciReleaseGate=false`；真实 OSS、
  目标 HTTPS/CI、外部身份/来源和 UAT 仍未验，正式入口仍关闭。
  同一 HEAD 的独立日终 PG16.15 门禁也通过：1 条映射决定、1 条审计、3 个截止，
  CLI 精确回读/恢复均通过，证据
  `artifacts/local-daily-ops-pg16/checks/run-3_5kt60m/checks.json`，`ciReleaseGate=false`。
  `0139` SQLite 全图空库升降 **1 passed（656.67 秒）**；该进程启动后又加严了升级前置
  校验，因此另以最终源码单独验证两种历史坏数据拒绝 **2 passed**、现有表结构上的
  触发器错绑拒绝/正确绑定放行和降级 **1 passed**。本地完整静态/客户端与合成
  PG16 门禁现已通过；仍缺旧库真实数据升级、GitHub PG16 runtime/Client、真实 OSS、
  目标机与外部身份/来源/期初及 UAT，**不能据此放行生产**。
  对象结果未知后的 `running` 恢复已收紧为只做精确 HEAD/MD5/元数据核对，
  不再发第二次 PUT；HEAD 缺失或内容不符保留任务待核查，不自动重放写入。
  完整回归发现 openpyxl 的修改时间使同一快照的 XLSX 字节跨秒变化；现已固定核心
  时间元数据与 ZIP 条目时间戳，跨秒字节相等及结果未知后恢复测试通过。
  另一组耗时迁移回归在首项 **1 passed（852.21 秒）** 后主动中止重复运行；不把该组记为
  全绿，保留前述已完成的迁移和全新 PG16 证据。
- `0137` SQLite 新库升迁与空库降回 `0136` **1 passed（424.02 秒）**；全新自有
  PostgreSQL 16.15 从空库升到 `0137`、数据库安全清单、API 窄列权限、两采集角色关闭
  和 9 项短信夹具通过，证据 `artifacts/local-current-head-pg16/checks/run-z5f79gwl/checks.json`，
  `ciReleaseGate=false`。先前 `0137` PG 尝试因单命令 180 秒超时而停在历史迁移中，
  并非 `0137` 失败；已将本地辅助脚本超时调至 600 秒并在新实例通过。
- `0137` 的独立全新 PG16 日终门禁也退出 0：1 条映射决定、1 条审计、3 个截止，
  CLI 映射精确回读及截止精确恢复通过，证据
  `artifacts/local-daily-ops-pg16/checks/run-iou838wo/checks.json`，集群停止。
  首次尝试 `run-tmujoibn` 在旧 180 秒上限超时，未形成业务结论；重跑前已把该脚本
  上限调至 600 秒。两套本地 PG16 均不是 GitHub 或目标机门禁。
- `0137` 的 Alembic 图两项检查通过；全库 ORM/迁移对照和空库降级回归另行
  **1 passed（287.68 秒）**。这些是上一 HEAD 的本地证据，
  仍不代替完整静态、真实旧库升级和正式 CI 门禁。
- 成功结果文件已有唯一索引与精确任务绑定守卫，内部结果事务及失败恢复已编码；
  申请、状态和专用下载接口由 `INVENTORY_REPORT_EXPORT_ENABLED=false` 默认封闭，
  一次性与持续轮询 worker 入口已有候选；持续进程启动前校验数据库安全目录，
  `reports` Compose profile 默认关闭。真实 OSS/目标机及运行角色端到端验收尚缺。此切片还没有
  新 HEAD 的完整静态、客户端或远端 CI 门禁。
  Compose API 环境与 `.env.example` 已显式保留 `OAM_INVENTORY_REPORT_EXPORT_ENABLED=false`；
  本机无 `docker` CLI，本轮只完成 Compose YAML 解析与默认值核对，不能当作容器实跑。
  一次性 worker 入口已延后数据库模块加载；无生产连接配置时输出固定的
  `inventory_report_worker_not_configured` 并退出 2，`--help` 可直接运行，不泄漏异常栈。
  当前候选的 Web 测试为 **80 文件 / 1,379 passed**，公开与 `/xx` 私有构建、
  `verify:pilot-release` 通过；小程序 **1,070 passed**，仓库安全扫描通过
  （1,654 个 Git 可见候选文件）。`verify:release` 按设计以
  `PUBLIC_CATALOG_NOT_READY` 拒绝 `pending / 0` 的公开知识目录。用户为更新
  Codex 客户端要求暂停时，三片 pytest 已受控 SIGINT 并确认无残留进程：
  第 1 片 **1,491 passed / 2 failed / 1 skipped / 15 subtests passed**，
  第 2 片 **1,278 passed / 3 failed / 1 skipped**，第 3 片 **7 passed**；
  全部只是中断前局部结果，不构成整片通过。
  第 2 片的 3 个早期失败已单独复现：`0133` 文件守卫历史哈希被误与 `0137`
  现行哈希直接相等比较，且旧权限差集未列入 `0136` 授权的 `file_jobs` 读、写及
  精确更新列。已按迁移阶段修订测试契约，3 个定点断言分别复跑通过；原三分片
  启动早于修订。第 1 片两项也已定点处理：`test_material_catalog_service` 因原
  shell 的 `PATH` 缺少已安装 Node 而主动失败，加入 Codex bundled Node 后 **1 passed**；
  `test_oam_projection_security` 错把最新只声明 `NEW_READY_HASH` 的 `0139` 报表
  迁移当成只声明 `NEW_HASH` 的旧迁移，现要求 HEAD 恰有一个 readiness 哈希字段，
  再与当前安全清单精确相等，单测 **1 passed（262.10 秒）**。修正后仍需冻结输入并
  重跑完整静态三片，旧进程的局部通过数不可当作新候选整组通过。

- 新迁移 `20261114_0135_inventory_report_export_jobs.py` 与 `FileJob` ORM、HEAD 常量同步。
  新建 SQLite 空库升迁及空任务回退测试通过；已有导出任务或权限目录漂移时降级拒绝，
  并保留已写任务。`test_report_export_migration.py`、旧 0134 回归与首轮 Excel 测试共
  **41 passed**；增强的迁移单测又独立通过。
- 全新自有 PostgreSQL 16.15 升迁、安全 ACL 和短信夹具门禁退出 0；最新
  `artifacts/local-current-head-pg16/checks/run-tic3gfq1/checks.json` 明确记录
  `head=20261114_0135`、`reportExportGrants=[admin, provincial_manager]`、
  `reportJobRuntimeAclClosed=true`、`ciReleaseGate=false`，无 OAM 绑定时边缘角色仍关闭，
  集群已停止。
- 同一 `0135` 源码的另一全新 PG16.15 日终门禁也退出 0：1 条映射决定/审计、
  3 条截止快照、映射精确回读与截止精确恢复通过，证据为
  `artifacts/local-daily-ops-pg16/checks/run-7qkvug_3/`；仍非 GitHub runtime 门禁。
- 首次 ORM/迁移对照发现 `file_jobs` 新检查约束仅有空格差异；已修正迁移源码，
  全库 ORM/迁移对照复跑 **1 passed**。Excel 测试初次出现 openpyxl 异常退出清理警告，已改为先校验全部
  输入再创建工作簿；独立重跑 **9 passed，无该警告**。
- `0135` PostgreSQL 升迁与降级 SQL 经 `pglast` 解析通过；`pip check` 无损坏依赖，
  `git diff --check` 与仓库安全检查通过。此处只证明 SQL 可解析，不能替代生产降级演练。
- 迁移历史单 HEAD 测试初次仍把新 HEAD 的父版本断言为 `0133`，现已更正为 `0134`
  并独立复跑 **1 passed**；PostgreSQL runtime 静态 HEAD/函数哈希检查 **1 passed**。
- `0135` 时安全审计发现的两处权限缺口已由 `0136–0137` 迁移收窄解决，`0139` 又补
  精确任务绑定；下一切片须补私有 OSS 运行与实际权限/下载验收，不得开放宽泛表权限。
- 第四轮静态三分片中的第三片因长期未结束，已记录受控停止原因并发送 TERM，
  `shard-2-controlled-stop-20260923.json` 与原日志保留，两个 PID 已确认退出。
  前两片通过不构成完整静态通过。`0139` 及报表源码已改变冻结输入，必须重冻重跑。

## 2. 当前完整静态运行与历史 CLI 修复

**2026-09-24 当前准确候选：** HEAD 仍为 `f713999d4693aedae11b5ebb379ef994aa50aeea`，
未提交/推送/部署。`artifacts/static-global-cache-20260924/input-manifest-v3.json` 冻结
1,655 个 Git 可见文件；相对初轮只改了三份测试文件：
`backend/tests/test_source_configuration_files.py` 将 0120→0133→0137 的正式文件函数
哈希承接关系核对到最终 0137，`backend/tests/migration_source_expectations.py` 缓存进程内
只读迁移来源并跳过没有 `_sources` 入口的迁移脚本；
`backend/tests/test_postgresql16_release_gate.py` 将纯函数 `_head_runtime_ready_hash()` 的
结果限于同一测试进程缓存，避免库存历史参数化用例重复执行完整迁移加载。两项退回迁移
哈希、来源配置、15 项库存历史及 Alembic HEAD 对照聚焦通过；生产迁移源码未改。
原并发静态第一片因旧哈希断言失败而受控停止：`1 failed / 1597 passed /
1 skipped / 15 subtests passed`，退出 2；原第二片因重复加载迁移图耗时过长而受控停止：
`1896 passed / 2 skipped`，退出 2。原始日志均保留，不计作通过。

新候选的第三片完整退出 **0，2,287 passed**，日志
`artifacts/static-global-cache-20260924/shard-2.log`；第二片独立复跑完整退出
**0，2,408 passed / 2 skipped**，日志同目录 `shard-1-v2.log`。
第三片不包含前两份变化的测试文件；其调用的 HEAD 对照用例已在第三份文件修改后定点
复跑通过。第一片的 v2 运行在发现库存历史用例反复加载迁移图后受控停止，原始日志
`shard-0-v2.log` 保留；修复后以 v3 冻结清单独立重跑，`shard-0-v3.log` 完整退出
**0，2,089 passed / 1 skipped / 15 subtests passed**，耗时 6,502.81 秒。三片均有
完整退出 0 的日志；相加为 6,784 passed / 3 skipped / 15 subtests passed，第三片
唯一受第三份测试文件变化影响的调用点另有变更后定点复跑通过。首轮客户端与三片
并行时出现多项 5 秒超时，受控退出 130，`client.log` 保留；当前候选客户端门禁在
后端释放资源后单独重跑，`client-v2.log` **完整退出 0**：仓库安全扫描 1,655 个文件
通过；Web 80 文件 / 1,379 项通过，`tsc -b`、公开与 `/xx` 私有构建、公开入口静态核对
通过；小程序 1,070 项与期初共享脚本检查通过。私有构建仍有单块超过 500 kB 的提示，
不影响本次构建退出码，但属于后续性能复核项。v3 冻结清单与测试后的源文件对比仅有
三份交接/审计文档漂移，业务代码、迁移及测试输入未漂移；`git diff --check` 通过。
逐项日志 SHA-256、PG16 实例停止状态及外部门禁限制汇总在忽略提交的
`artifacts/static-global-cache-20260924/result.json`。
新候选的 PG16.15 合成期初→报表申请 HTTP→API 角色 worker→状态/下载 HTTP 已通过，
见 `artifacts/local-current-head-pg16/checks/run-m04005fm/checks.json`；独立日终 PG16
见 `artifacts/local-daily-ops-pg16/checks/run-3_5kt60m/checks.json`，两个门禁均为
`ciReleaseGate=false` 且实例已停止。GitHub PG16/Client、目标 Linux、真实 OSS/身份/来源/
期初与 UAT 仍缺，不能把本地合成通过写成上线。

**本轮新增 D2 日终一次性运维入口候选：** 镜像内固定六操作 CLI 与受监督映射 worker 已实现；
截止只读坐标预检及原三连接捕获/恢复接入，独立 Compose `ops` 服务仅在显式一次性运行时
通过只读挂载接收 owner/source/ledger 文件凭据，默认试点 `prepare/start` 不要求这些文件。
扩大日终与试点部署专项 **561 项通过、15 子测试通过 / 1 条第三方
弃用警告**；最新全新自有 PG16.15 空库升到
`20261113_0134`，API/两采集角色权限核验后，实际映射预览→提交→回读和截止预览→捕获→
回读通过；CLI 外层的映射精确回读、截止精确恢复也已在该原生 PG 上通过。预览期间决定/截止、
审计与库存交易计数不变，最终 1 条映射决定、1 条映射审计、3 个合成截止；最新证据
`artifacts/local-daily-ops-pg16/checks/run-dz84p8uf/`，实例已停止。当前机器没有
Docker CLI，隔离 Linux 的 Compose 5.5.1 已完成当前文件的合成配置解析。后续同一准确
API 镜像已通过隔离容器内 FD 3 / 隐藏 TTY、精确回读、两项拒绝及 212 表 / 788 行的
第二新库恢复；真实目标机与六操作完整负向矩阵仍缺。本地 PG 不是 GitHub 门禁。详情见
[日终入口候选与余项](DAILY_RECONCILIATION_OPS_ENTRY_20260923.md)和
[当前镜像运维实跑](CURRENT_CONTAINER_DAILY_CLI_20260923.md)。
另一全新 PG16.15 实例 `artifacts/local-daily-ops-pg16/checks/run-yvemf3xx/` 已验证独立
CLI 子进程/FD 正向精确回读及 4 个负向拒绝，决定、截止、审计、库存交易数量保持不变；
退出 0、实例停止。前一轮夹具环境失败 `run-myeq1cp1` 保留，未改正式源码。
同一冻结候选已在隔离 Linux VM 构建实际 API 镜像并核对 331 个 COPY 输入；准确镜像
配置摘要为 `sha256:ad19cde65430830f293d0de4e1450ccf0b8515e0068d6ff3a7d74325951f628b`。
以 UID 65532、无网络、只读根目录在镜像内执行日终 CLI `--help` 通过；VM 与包服务停止。
证据见 `artifacts/api-image-current-20260923-daily-ops/api-build-result.json` 和
`ops-image-checks.json`。目标机实际 Compose 配置仍待验证。
隔离 Linux Compose 5.5.1 的实际 `config --format json` 亦已通过：8 服务、日终运维
无默认凭据挂载/端口、专用内部数据库网络、只读根目录/UID 65532。证据
`artifacts/api-image-current-20260923-daily-ops/compose-checks.json`；仅使用合成非秘密变量，
尚非目标机实际配置或运维命令验收。
第一次三分片运行因发现默认挂载与试点部署输入冻结冲突，已受控停止，局部日志分别为
518/1,187/3 项通过、三片均退出 2；修正后的第四轮前两片已终端退出 0，分别为
2,078 和 2,398 项通过；第三片后续受控停止，详见 1.1 节。
仍不提交、推送或部署。

第二轮按 `artifacts/static-shards-20260923-daily-ops-attempt2/manifest-before.json`
冻结 1,091 个源码/门禁输入，摘要 `f69e30dee012585568232a7185e999b0cd057b4c1fa3c9b0ab60ad696d35cc99`。
第 1 片在 72% 已有两项失败，查明本地 shell 缺 Node 24 `PATH`；其准确测试补齐 Node
后 **2 项通过**。第二轮随后受控停止，三片局部为 1,555/1,276/15 项通过，退出码均 2。
第三轮使用已安装 Node 24.19.0 与本地 PG16 client 后，发现独立 CLI 在缺少应用配置时
`--help` 会因提前导入应用模块失败；该轮受控停止，局部为 515/1,138/2 项通过、三片退出 2。
现已改为解析命令后才导入应用模块，独立子进程 `--help` 与非法参数均已验证。
第四轮冻结 `artifacts/static-shards-20260923-daily-ops-attempt4/manifest-before.json` 的
1,091 项输入、摘要 `b2f07f16c03bde985b83727c37126f2a4aa6057d8b49a6c7c035bf7209368592`；
前两片已终端退出 0，分别为 2,078 passed / 1 skipped / 15 subtests passed 和
2,398 passed / 2 skipped；第三片后续受控停止，整组不能计通过。
同批客户端本地 `pnpm test` 为 80 文件 /
1,379 项通过，`tsc -b`、公开/私有构建及 `verify:pilot-release` 退出 0；私有构建有单块
>500 kB 提示。目录仍 `pending / 0`，pilot 检查只证明受邀 `/xx` 构件完整，不证明正式公开目录放行。
客户端日志同在 attempt2，GitHub Client 尚未执行。

**本轮接续核验：完整静态 6070 的两个 PID 已不存在，且无 `result.json`；`static.log` 最后停在 77%，可观察到 5,167 项通过、7 项失败、2 项跳过，不能认定全量通过。** 原日志保留于 `artifacts/full-static-release-20260922-binding-fix/`。7 项失败已准确复现：1 项旧工作流路径断言、6 项未跟上新增采集角色证据的测试夹具。修正后 7 项通过；相关工作流、边缘权限、采集角色 **58 项通过**，通知投递、PNVS、日终迁移聚焦 **133 项通过 / 1 条第三方弃用警告**。此前停止的 6070 不再等待或重标绿。

新增本地 PG16 补充门禁 `scripts/run_local_pg16_current_head_checks.py`：最新全新自有 PostgreSQL 16.15 实例从空库升至 `20261113_0134`，配置两名独立只读采集角色，实际 API 安全目录检查通过；无来源绑定时 `edge_inbox` 和投影角色准确拒绝 `revision_and_binding`。同一实例执行 9 个 PNVS 挑战/并发场景，7 个合成挑战、库存与截止事实未变，无真实短信。最新证据为 `artifacts/local-current-head-pg16/checks/run-vpqlza62/checks.json` 和 `cluster-state.json`，退出 0、实例停止；先前轮次独立保留。该脚本不执行 GitHub 专用破坏性 runtime 门禁，也不证明正向真实来源授权。

PNVS 新增专用 STS token 与到期时间配置，预检、提供者构造及发送/校验前拒绝缺失或临近过期凭据；SDK 日志 handler 在调用前后屏蔽。现阶段由部署更新三元组并重启 API，未实现自动刷新，也未真实发送短信。[专项说明](PNVS_STS_20260923.md)。最终联合聚焦 **276 项通过 / 1 条第三方弃用警告**，覆盖工作流、权限、通知、短信、日终迁移与适配器；前述 58、133、186 项与其有交集，不能相加。

**当前八角色容器联合恢复已在新目录通过。** 准确镜像的正常备份、真实数据库阻塞期限、
取消、父 worker 终止、损坏对象拒绝与第二新库恢复全部通过；五场景后会话和锁均清零，
源/恢复库均为 212 表 / 584 行，8 角色与 7 个新密码及旧密码拒绝通过，私有引擎/VM 已停止。
详细证据见[当前候选恢复](CURRENT_CONTAINER_RECOVERY_20260923.md)。后续另以
[非空日终样本](NONEMPTY_DAILY_CONTAINER_RECOVERY_20260923.md)完成当前镜像的 212 表 / 746 行
联合恢复，其中 1 条映射决议与 3 条截止快照非空，4 个对象字节核验通过。最新又以
[六张全非空样本](FULL_DAILY_CONTAINER_RECOVERY_20260923.md)完成 212 表 / 788 行、
5 个对象、六张日终表逐表一致的第二新库恢复；使用合成 OSS 传输，真实 OSS、
目标机及 RPO/RTO 仍缺。上一轮 `db-deadline` 未到
注入屏障的失败现场原样保留在[历史恢复交接](CURRENT_CONTAINER_RECOVERY_20260922.md)。
进一步的[当前镜像运维实跑](CURRENT_CONTAINER_DAILY_CLI_20260923.md)在同一 212 表 / 788 行
合成样本上完成私有 FD 和隐藏 TTY 的准确回读、过宽文件权限/错误摘要拒绝，随后完整备份
故障矩阵与第二新库恢复均通过。最终 `...-daily-cli-attempt4/result.json` 为 `passed`，
私有引擎、VM 和自有 PG 均停止；前三次夹具尝试证据保留，不与成功轮合并计数。

完整静态原单任务超过六小时仍未完成。CI 已改为 3 个独立矩阵分片，运行时从 `backend/tests`、`edge_sync` 自动发现；原候选为 313 个静态模块、111/99/103，新增 `test_daily_ops_cli.py` 后当前为 **314 个、111/99/104**，排除单独执行的破坏性 PG16 runtime 模块。稳定的 `postgresql16-release-gate` 总检查仍要求 PG16 runtime 和全部静态分片成功。拓扑、唯一覆盖及工作流解析历史 **30 项通过**；第四轮前两片终端退出 0（2,078/2,398 项），第三片已受控停止，远端也未执行，不能把结构检查当成全量绿灯。
第三片原 PID `71837` 的日志保留于 `artifacts/static-shards-20260923-daily-ops-attempt4/shard-2.log`，
受控停止证据为同目录 `shard-2-controlled-stop-20260923.json`。此轮没有完整通过结论。
本轮 `verify_repository_safety.sh` 已退出 0：1,629 个 Git 可见候选文件、30,466,364 字节，
受保护本地路径仍被忽略；`git diff --check` 亦通过。这两项不代替完整静态或远端门禁。

**8.21 完整静态会话 52984 已受控结束，退出 2；11 passed / 1 skipped / 1 warning，612.36 秒，1,470 个非 Markdown 输入无漂移。** 收集 6,645 项，尚未完整执行。因独立复现部署 prepare/start 候选绑定的三项缺陷而停止，两个 PID 90353 / 90332 已结束，不得继续等待。部署修复已通过 8.23 聚焦与复核；后续 6070 的结果见本节首段。原始日志、终端结果与停止理由保留，见[本轮门禁接续](STATIC_RELEASE_GATE_20260922.md)。

完整静态会话 **9496 已退出 1**，pytest PID 86848 / 监督 PID 86838 已结束。
210 文件，实际 **4960 passed / 1 failed / 3 skipped / 1 warning**，pytest 13,354.32 秒。
1,364 个非 Markdown 输入在整个运行期间保持摘要，原清单 SHA256
`e29e77acb196893fb44c264add619c4aa7988ecba7cfd836884f6cb2fcf40392`。
证据目录：`artifacts/static-release-candidate-20260921-target-guard-fix/`。

唯一失败为配置 CLI 的 REQUIRED_HEAD 固定 0128，而实际 Alembic HEAD 已为 0129。
7.98 只更新这一固定值；正式源码的 CLI 聚焦 **83939 退出 0：13 passed / 45 deselected**。
准确修正候选的实际 PG16 预检 **69302 退出 0：6 场景通过**，验证新 HEAD 接受、旧/多/空
HEAD、错误库名和 API 角色拒绝，206 张表前后不变，实例停止；候选与正式源码逐字节相同。
完整证据及原始失败见[7.98 CLI 修复](CONTROL_CLI_HEAD_20260921.md)。

上述 9496 及 8.19、8.20 本地 PG16 会话均已结束，自有数据库全部停止。6070 也不再运行；不能把历史完整运行和后续聚焦拼接成当前完整门禁通过。GitHub PG16/Client 仍需准确最终候选结果。

旧完整 53877 的提示断言失败及聚焦 66627 的 8 项通过已归档；64993、58287、26717、7406
也均已结束。生产通知函数的原生 PG16 专项 77205、398 项通知/权限/迁移聚焦及父迁移检查
原证据保持；本批没有改这些生产函数，未重复无关的客户端或通知 PG16 专项。

## 3. 已完成的近期开发及证据范围

8.22 HTTPS 夹具 **40 项边界测试通过**，随后完成 **9 个实际 HTTPS API 场景**：两次 PUT/HEAD、一次 GET，区域解释→总部退回→补证→独立批准，6 条事件、0 seal；212 张表中 203 张不变，库存/截止事实保持，1,109 输入无漂移，服务和 PG 停止。仅测试进程显式信任准确叶证书，系统信任未改变，浏览器联验仍待授权及执行。首轮 driver 字段错误保留，见[API 联验证据](DAILY_REVIEW_HTTPS_API_20260922.md)和[夹具步骤](DAILY_REVIEW_HTTPS_FIXTURE_20260922.md)。旧批仓库安全扫描通过（1,605 文件）；新增部署改动的后续复核见 8.23。

8.23 部署候选绑定已完成：原子 prepare 回执、准确源码/配置/镜像 ID/DB/HEAD 绑定、项目锁、唯一内部构建标签、静态挂载快照及固定本机数据库查询已实现。**155 项最终聚焦通过，7 输入零漂移**；最终 helper 的真实 Compose 5.5.1 整体快照再解析通过，CI 拓扑 2 项、仓库安全 1,613 文件扫描和 diff 检查通过。3 个原始错误放行反例及三轮中间证据保留，无新迁移或目标机执行。详见[部署绑定专项](PILOT_PREPARE_BINDING_20260922.md)、[配置再解析证明](PILOT_IMMUTABLE_COMPOSE_20260922.md)。

8.24 当前镜像及正常容器备份已有证据：330 构建输入/328 COPY 文件一致；0134 合成源 **212 表 / 584 行**，八角色及七个新密码/错误口令验证、API 实际角色、正常联合备份通过。第三轮容器会话 **47541 退出 1**，`db-deadline` 未到注入屏障，日志出现文件大小限制和超时；后续故障场景及第二新库恢复尚未执行，完整恢复未通过。所有自有 PG/容器/引擎/VM 已停止，原失败保留；新增六张日终表为空，不声称覆盖非空日终恢复。详见[本批恢复交接](CURRENT_CONTAINER_RECOVERY_20260922.md)。

下一批已明确为[日终运维 CLI 接入](DAILY_RECONCILIATION_OPS_CLI_DESIGN_20260922.md)：正式核心已有，当前只完成入口设计，尚未实现或运行；本轮源码冻结期间只做独立文档/验收准备，不混入新功能。

8.20 修复 PNVS 配置切换后旧验证码误用新方案，以及历史 `local_hash/legacy_unknown` 误走当前供应商的问题。发送/校验均绑定同一个 provider 配置副本与准确挑战摘要；不增加错误次数、不自动重发、不消费旧模式挑战。**158 项聚焦、9 个真实本地 PG16 场景通过**，第三轮 875 输入无漂移、集群停止。前两轮夹具失败完整保留，无新迁移或真实短信，详见[短信配置专项](SMS_PROFILE_BINDING_20260922.md)。

8.21 静态门禁从 233 文件白名单改为目录自动发现，只排除独立 GitHub PG16 runtime；关闭了 77 个认证、权限、KMS、附件等测试文件漏纳的问题。CI 路径覆盖 `cloud_oam/**`，静态期限改为 360 分钟，双成功聚合继续要求 static/runtime 都成功。第一次目录收集 6,563 项，不等于运行通过；之后新增短信/HTTPS夹具测试也由目录自动纳入。当前分支 push 本身不会触发两门禁，需面向 main 的 PR 或显式 dispatch 并核对准确 SHA。

8.19 已补逐档案可执行动作和当前身份/授权版本上下文，复用正式写授权、历史解释人回避及状态规则。前端据此收敛按钮和提交，部分解释只可定向退回、混合无效选择整次阻断，原请求恢复保留；无新迁移。后端 **209 passed**，前端完整 **1,379 passed / 80 文件**，类型、双构建和试点校验通过。首轮并发编辑导致的 3 个前端失败保留，冻结 224 输入后完整复跑通过。详见[动作提示专项](DAILY_REVIEW_ACTIONS_20260922.md)。

8.19 全新本地 PG16.15 空库同时通过 **12 场景 / 16 次实际动作读取 / 4 个非空降级拦截**；7 条审核事件、1 份请求封存、审核前后 7 张库存/截止事实表不变。876 个后端/迁移/夹具输入无漂移，第二轮 `status=passed`、`clusterStopped=true`。首轮因测试人员组织不满足总部角色约束而失败并已停止；只修正合成夹具，未放宽正式授权。当前 GitHub/完整静态/Client 门禁及真实环境验收仍缺。

8.18 部署入口已改为 `prepare` → KMS 登记 → 端口切换 → `start`，并补项目/卷/网络/镜像/数据库目标隔离、Docker 子网冲突和烟测域名绑定。失败立即停止并报告阶段，保留现场；旧端口绕过变量不再生效。新部署测试接入 PostgreSQL 16 的静态 CI 清单和脚本触发路径。证据及限制见[部署入口专项](PILOT_DEPLOY_ENTRY_20260922.md)。本批无新数据库迁移，无真实 Docker/目标机执行，不把脚本测试当作部署成功。

**B5 正式集成已落地：** 分阶段入口、DB/旧附件导出、worker 与 Compose 已进入正式目录；
API 和 worker 共用同一份 22 个纯附件校验定义。首轮 14 文件聚焦 **263 passed / 1 failed**，
唯一 Compose 层级错误已修正，受影响的 **20 项通过**。当前正式入口 **13 场景**、实际 PG16
**5 场景**和实际 Compose 5.5.1 配置解析全部通过。新包恢复 **206 表 / 572 行**一致，
3 个可用对象、1 个待上传意图、6 次合成 transport 的实际 SDK 调用已核验；两个新实例及
VM 均停止。完整原始失败、终端句柄及 41 文件摘要见[7.99 正式集成](BACKUP_FORMAL_INTEGRATION_20260921.md)。

前序[DB 镜像](OSS_BACKUP_RUNTIME_20260921.md)、[Docker 中断 14 场景](OSS_BACKUP_DOCKER_20260921.md)
和[Compose 中断 14 场景](OSS_BACKUP_COMPOSE_20260921.md)保留原证据。
**8.01 已补齐合成样本下的实际 DB/API/worker 容器全链路；真实 OSS 和生产恢复仍未验收**。主机 Python ≥3.11，
正式部署须使用已验证的 DB/API 工件，并明确容量、期限和独立 OSS 身份。当前 229 文件静态选择
尚未全量运行，合成传输及测试环境 API 不能代替真实云端、production 启动和生产验收结果。

8.01 已通过正式备份入口的实际 Docker/Compose 全链路：5 个场景通过，新包在第二个全新 PG16 实例恢复后 206 表 / 572 行一致，3 个可用对象和 1 个待上传意图核验。API 实际就绪且以 star_oam_api 连接；超时、取消、主进程终止、内容损坏后无会话/锁/临时容器残留，服务身份保持。4 次失败及数据目录保留，所有实例、引擎和 VM 已停止。API 为测试配置、OSS 为合成传输；真实云端验收和当前完整门禁仍缺，见[容器全链路与恢复交接](BACKUP_CONTAINER_E2E_20260921.md)。

8.15 已把可复用日终 PG16 夹具与审核门禁接入 `test_postgresql16_release_gate.py` 的正式 runtime 流程。全新本地 PG16 空库从 `upgrade head` 开始，捕获角色先验不存在后以随机密码创建；4 个非空 downgrade 保留拦截点和 12 个日终场景全部通过，集群已停止。静态工作流拓扑 2 项通过，源码清单 1,459 项无摘要漂移。详见[日终 PG16 runtime 证据](DAILY_REVIEW_CI_RUNTIME_20260921.md)。这仍是本地候选证据，GitHub runtime/完整静态/Client、真实来源与生产验收仍缺；未提交、推送或部署。

8.16 已由当前任务统一接管另一任务的发布候选改动。新增 `test_pilot_preflight.py` 并纳入 PostgreSQL 16 静态安全清单；预检契约和工作流拓扑共 4 项通过，启动安全/RBAC/数据库部署检查 77 项通过，前端 `/xx` 试点校验通过。公开知识目录仍是 `pending / 0`，严格生产门禁仍阻断；当前没有真实部署或生产写入。

8.17 当前候选复核已完成：公开站点与 `warehouse` 构建、试点发布预检通过；预检新增数据库 URL 密码长度拒绝用例，预检/工作流拓扑 **5 passed**，使用仓库 `.venv` 的启动安全/RBAC/数据库部署/正式访问聚焦集合 **81 passed**（约 61 秒），通知/库存通知/目标恢复专项 **178 passed / 1 warning**（832.38 秒），Web **80 文件 / 1,346 passed**、小程序 **1,070 passed**，开发模式公开入口校验与 `git diff --check`、仓库安全检查通过。带 `--release` 的严格公开门禁按设计因 `pending / 0` 拒绝；该批仍是本地候选证据，未推送、未触发远端 CI、未部署。

8.13 已完成当前网页、真实 API 与全新本地 PG16 的六项局部联验：正常开启、提示按档案隔离、提交后断线恢复、未送达请求保留、明确终结及外区隔离。数据库为 2 条开启事件、1 份终结证明，库存与截止不变；修复提示串档案问题。59 项聚焦、完整前端 1,346 项、类型和私有构建通过，两轮实例及服务器均停止。附件到总部审核的浏览器全流程、完整门禁和真实资料仍缺，见[浏览器联验证据](DAILY_REVIEW_BROWSER_20260921.md)。

8.12 已接入私有日终开启/逐项解释/专用证据/总部审核与退回页面，并补浏览器跨刷新恢复、对象锁和明确终结确认。110 项聚焦、80 文件共 1,345 项完整前端、TypeScript 和双构建通过；公开产物隔离及星星 `/xx` 路径保持。后端与 0134 逐字节保持 8.11，没有新增 PG 实例。真实浏览器/API 联合验收、当前完整门禁及真实资料仍缺，见[网页操作证据](DAILY_REVIEW_CLIENT_20260921.md)。

8.11 已完成日终原请求安全坐标恢复与服务端永久终结证明，数据库 HEAD 升为 0134。462 项后端聚焦、13 项 CLI、83 个本地 PG16 场景和六类期初完整流程通过，三个自有实例均停止。并发原提交/终结、迟到 SQL 拒绝、到期回滚、缺失/伪造/重复审计及丢失回执恢复已验证；库存和截止事实保持。前端本批未改，浏览器持久恢复与写操作页面仍缺，见[本批证据](DAILY_REVIEW_RECOVERY_20260921.md)及[页面接续](DAILY_REVIEW_UI_NEXT_SLICE.md)。

8.10 已接入私有日终查询页面与受同源保护的 Cookie 命令入口，数据库 HEAD 保持 0133。前端完整 1,293 项、后端聚焦 77 项、公开/私有双构建及 61 个本地 PG16 场景通过；两次自有实例均停止，原失败保留。查询与审核状态分开；跨刷新恢复及写操作页面仍缺，见[网页接入证据](DAILY_REVIEW_WEB_20260921.md)和[下一批依赖](DAILY_REVIEW_UI_NEXT_SLICE.md)。

8.09 已接入日终审核 HTTP/原请求恢复、专用附件和真实审核状态/历史查询，HEAD 升为 0133。518 项当前聚焦、13 项 CLI 检查、61 个本地 PG16 场景、六个期初完整流程及旧附件历史升级保护通过；5 个自有实例均停止。该批尚缺的只读页面已由 8.10 补齐；写操作页面、当前完整门禁和真实来源/OSS 验收仍缺，见[接口接入证据](DAILY_REVIEW_HTTP_20260921.md)及[页面接续](DAILY_REVIEW_UI_NEXT_SLICE.md)。

8.08 正式日终审核数据库与服务层已集成，Alembic HEAD 升为 0132。407 项聚焦、13 项 CLI 检查、53 个本地 PG16 场景及六个实际期初完整流程通过；权限种子、目录漂移、非空回退拒绝、12 条审计回执均核验，8 个自有实例均停止。该批尚缺的 HTTP、专用附件和查询状态已由 8.09 补齐，页面仍待实现，见[正式接入证据](DAILY_REVIEW_FORMAL_20260921.md)。

8.07 日终审核持久化候选已通过 47 个本地 PG16 场景（23 个新增专项）。4 份合成对账提交 12 条命令，完整审计前缀、独立审核、并发、到期回滚及非空回退拒绝已核验；库存与截止档案保持，六个实例均停止。该批尚未接入正式 0132；8.08 已完成数据库与服务层，8.09 已完成 HTTP/附件/查询，页面仍待完成，见[持久化候选证据](DAILY_REVIEW_PERSISTENCE_20260921.md)。

8.06 已补日终逐项解释、定向退回补证、独立审核及原请求回执恢复核心。132 项聚焦和 35 个本地 PG16 场景通过，其中 11 项为新增核心验证；两个自有实例均停止。正式写端、权限种子与 0132 迁移尚未接入，内存审核结果不代表数据库已审核；该批 HEAD 为 0131，见[审核核心与接入约束](DAILY_RECONCILIATION_REVIEW_20260921.md)。

8.05 已补总部/区域日终查询与分页明细，0131 只读视图不暴露私有档案。513 项聚焦、13 项 CLI 检查和 24 个原生 PG16 场景通过；207 张既有业务表在迁移往返后内容一致，4 个自有实例均停止。日终解释/审核、页面入口、当前容器工件及完整门禁仍缺，见[日终查询证据](DAILY_RECONCILIATION_QUERIES_20260921.md)。

8.04 已补新增采集账号的恢复前准备、阶段检查与新凭证启用；420 项聚焦和 87 个实际 PG16 场景通过，其中 16 项恢复场景。当前数据库备份在独立新库恢复后 208 表 / 934 行内容一致，七个登录账号的新密码通过、旧密码拒绝，API/边缘/投影/采集权限通过，四个实例停止。当前容器联合附件恢复与真实云端仍缺，见[新增账号恢复证据](DAILY_CAPTURE_RESTORE_20260921.md)。

8.03 两个只读角色的配置与权限共存证据保留，见[角色配置与验证](DAILY_CAPTURE_ROLES_20260921.md)。日终区域查询已在 8.05 落地，审核接口已在 8.09 落地；下一步页面及进程部署，并补当前容器工件与完整门禁。

8.02 日终后端/0130 迁移及 39 个原生场景的历史证据保留，见[正式集成证据](DAILY_FORMAL_INTEGRATION_20260921.md)。旧 API 镜像不包含当前日终后端、角色恢复及查询改动。

**B3 日终对账历史候选：** 已完成独立计算候选，**24 项测试通过**。按固定账本游标重建数量，
按仓库/SKU/成色左右比较；来源缺仓、截断账本、映射不全和日期不符拒绝，冲销保留原事实。
[计算候选与接入范围](DAILY_CONTROL_RECONCILIATION_20260921.md)保持。7.90 新增原始账本
只读采集，**13 个原生 PG16 场景通过**，修复锁前快照导致 3 个组织只读出 2 个的 RLS
并发缺行窗口；四个临时实例均停止。[最新读取专项](DAILY_LEDGER_CAPTURE_20260921.md)
保留反例和最终证据。7.91 [已发布来源读取](DAILY_CONTROL_SOURCE_20260921.md)新增 **16 个
实际 PG16 场景通过**：复核原来源/目录授权、采集回执、映射/物料和仓库来源，缺页拒绝，
30 张受检表前后摘要一致，四个临时实例停止。7.92 [实际发布专项](DAILY_CONTROL_PUBLICATION_CASES_20260921.md)
再通过 **7 个原生场景**：4 次真实合成发布、18 份读取，覆盖多仓拆分、增量来源、全空
目标仓、再出现与映射/来源撤权；撤权后新采集的未发布准备被拒绝，六张库存事实表不变，
两个实例停止。7.93 [两侧采集与计算适配](DAILY_CAPTURE_BRIDGE_20260921.md)通过 **29 项聚焦、
3 个实际 PG16 场景**：两个新采集对保留原时间，34 表读取前后不变，两个实例停止。
7.94 [映射授权候选](DAILY_MAPPING_GOVERNANCE_20260921.md)通过 **28 个实际 PG16 场景**：
当前总部会话批准、撤销/替换、幂等并发及不可变审计绑定通过，三个实例停止。
7.95 [不可变截止候选](DAILY_CUTOFF_PERSISTENCE_20260921.md)通过 **26 个实际 PG16 场景、
34 项聚焦**：三份截止绑定原采集/映射/审计，伪造报表与流水即使重算摘要仍被 SQL 拒绝，
库存六表不变，三个实例停止；同时修复有非目标区域仓库时的覆盖适配。
7.96 [截止事务期限候选](DAILY_CUTOFF_DEADLINE_20260921.md)通过 **33 个实际 PG16 场景**，
约 2 秒锁等待超时、提交前停顿回滚及提交结果未知精确恢复通过，四份截止/审计可核验，
库存六表不变、实例停止。7.97 [独立进程入口](DAILY_CUTOFF_PROCESS_20260921.md)新增 **12 个实际
PG16 场景通过**，覆盖建连停顿、工作进程暂停、半截回执和父进程丢失；原请求恢复准确，
三份截止/审计及库存六表核验，三个实例停止。真实网络、启动阶段父进程丢失及容器链路仍缺。
正式迁移/安全目录已在 8.02 落地，8.03 已补只读角色/API 共存，8.04 已验当前原生八角色恢复；本轮补当前容器联合恢复，真实 OSS、非空日终恢复、真实映射批准和三天验收仍缺。

**N0 本地修复及 PG16/聚焦验证完成：** 0129 仅授予事件 status 列 UPDATE，保护正文与状态；
补齐原目标渠道只在新绑定尚未生成投递时重排。扩展器取得事件锁后刷新缓存，收件人只读；
取消竞争的原始失败和隔离候选通过记录均保留。[当前专项证据](NOTIFICATION_EXPANSION_STATUS_20260921.md)。

| 交付 | 当前可复核证据 | 不能据此认定 |
| --- | --- | --- |
| 公开首页与 `/xx` | Web 1,241、小程序 1,070 项通过，类型/双构建/共享协议通过；511 个客户端输入与受检版本相同 | 真实目录已经导入、个人主体小程序可发布私有业务 |
| 期初发布/复盘/原请求终结 | 当前迁移 HEAD 0134；真实本地 PG16 的发布、启动/终结、撤权与提交竞争、复盘及只读恢复专项记录 | 当前候选完整 GitHub PG16 或真实多角色设备 UAT 已通过 |
| 备份正式集成及恢复 | 修正后 20 配置项、13 入口、5 实际 PG16 场景与实际 Compose 解析通过；恢复 206 表 / 572 行一致 | 当前完整门禁或正式 OSS 已验收 |
| 恢复演练 | 本地物理 PITR、非空 SN、审计链、损坏备份拒绝和幂等重放证据 | 生产 RPO≤5 分钟、RTO≤2 小时已达标 |
| CI 路径 | 备份脚本及辅助目录的单独改动会触发 PR / 已有 push 范围门禁，拓扑检查通过 | 本分支已推送、当前远端 CI 已运行 |

备份当前拒绝全部 RLS SELECT/ALL 限制性策略，避免后续成员关系激活过滤；导出完成后、
释放表锁前再复查角色/ACL。未来引入业务角色的限制性读取策略也会阻断备份，须重新审查。
没有增加 BYPASSRLS、角色所有权或写权限；失败不发布完成包，不删除旧备份。
7.75 原生反例/候选/恢复的三个集群均已核验停止；此前资源状态分别见各专项记录。

证据入口：

- [备份角色并发修复](BACKUP_ROLE_CONCURRENCY_20260921.md)及其 `verification.json`：
  `338712e109f7048e7a4ee07ec476dd5fb1a00fc06947fc817d2163e8fd011e0c`。
- [正式备份入口](BACKUP_PRODUCTION_ENTRY_20260921.md)、[SN/PITR 恢复](PITR_SERIAL_RESTORE_20260921.md)。
- [期初启动原请求终结](OPENING_START_SEALS_20260920.md)、[原表导入边界](PUBLIC_KNOWLEDGE_IMPORT_20260920.md)。
- `cloud_oam/artifacts/client-release-candidate-20260921/client-result.json` 为本地客户端结果。

## 4. 上线仍缺什么

2026-09-24 增量：期初盘点 Excel 格式预检已接入 `/xx` 盘点中心，支持模板、
格式错误展示及错误报告下载；没有任务/范围绑定、持久 `FileJob`、业务预校验或确认执行。
新增接口与页面聚焦 **67 passed**；Web 受控并发全量 **83 文件、1,387 passed**，
`build:warehouse` 与试点构件校验通过。首轮全量的 1 项 5 秒超时保留为原始失败，
独立运行 6 项通过后才做受控并发重跑。仓库安全扫描 **1,667 文件 PASS**。
当前候选完整后端静态/PG16、同架构 `x86_64` 镜像、目标 `prepare/start`、真实
PNVS/OSS/KMS/身份/期初及业务 UAT 尚无准确候选通过证明；旧 1,659 文件完整证据
不能外推。目标机已由用户确认为旧异机备份脚本中的杭州主机，SSH 只读核对见
`artifacts/target-readonly-20260924/result.json`，不要再次要求确认目标。
发布入口另补宿主 80/443 TCP 监听门禁，Docker 端口已释放但其他进程仍占用时
也会失败关闭；部署/回执绑定聚焦 **99 passed**。目标机仍未执行任何切换。
本轮还修正目标冒烟的旧站误通过：`/` 和 `/login` 必须是公开知识查询入口，
`/xx/` 必须是个人仓私有构件；假站正向/三项错误路由反例 **4 passed**。
当前仍只有本地静态入口证明，真实浏览器、备案终态、短信/OSS/KMS 与业务 UAT 未验。
当前 Git 可见候选为 **1,668 文件**，仓库安全扫描退出 0；前文 1,667 文件是
添加目标冒烟正反例之前的快照。
新冒烟对当前旧 `https://rscwz.cn` 的固定解析只读复核退出 1，代码为
`public_home_not_knowledge_entry`；旧站 HTTP 200 不再构成切换成功证据。
当前 1,668 文件输入清单保存在 `artifacts/current-candidate-20260924/input-manifest.json`，
PG16 检查前后逐文件比对为零漂移。新建自有 PostgreSQL **16.15** 空库升至
`20261118_0139`、角色/报表状态约束、九项短信配置夹具及合成期初报表完整流
退出 0，证据 `artifacts/local-current-head-pg16/checks/run-kpzgnsvw/checks.json`；
`cluster-state.json` 显示 `status=stopped`、`serverExitCode=0`、`ciReleaseGate=false`。
这是当前源码的本地 PG16 补充门禁，不是 GitHub 或生产验收。
导入下一切片需先扩展 `0136` 只允许导出的 `FileJob` 数据库守卫、增设错误报告
文件目的，再做持久预校验和授权确认；`0140` 已将期初盘点 XLSX 源文件作为
独立、8 MiB 封顶、`stocktake.count` 授权的私有用途，与报表结果分开。OSS 已增期初导入源文件的
8 MiB 上限、HEAD/GET 版本钉住与 SHA-256 复核的底层读取方法，相关聚焦
**77 passed**、仓库安全扫描 **1,669 文件 PASS**（前轮快照）。读取方法已由后述
当前上传人授权边界接入只读预览，但仍没有持久导入任务。本轮自有 PostgreSQL 16.15 空库至 `0139`、
运行权限/报表/短信合成检查再次退出 0，见
`artifacts/local-current-head-pg16/checks/run-nnsc3j0j/checks.json`；实例已停止，
`ciReleaseGate=false`、`syntheticStorageOnly=true`，且隔离 Edge/投影绑定仍显示
`revision_and_binding`，不构成生产门禁。当前格式
面板不能直接转换为库存写入口。
最新 `0140` 候选的 SQLite 升降级 **3 passed**、读取/格式等聚焦 **35 passed**；
全新 PostgreSQL **16.15** 空库至 `0140` 的当前候选门禁退出 0，真实 API 角色的
合法源文件允许、无权限拒绝、超 8 MiB 拒绝三项均通过，报表/短信/期初合成流
继续通过，证据 `artifacts/local-current-head-pg16/checks/run-iceityfz/checks.json`；
实例 `status=stopped`、`serverExitCode=0`。三次修复前的 PG16 失败报告保留在
`run-rd83071q`、`run-m9qydkwd`、`run-wzln9rl_`，分别对应旧 HEAD 常量、旧安全
目录哈希和测试负例只接受 23514。准确候选完整静态/客户端、GitHub CI、真实 OSS、
导入业务预校验与确认执行仍缺；本地 PG 报告明确 `ciReleaseGate=false`、
`syntheticStorageOnly=true`。

用户已确认旧异机备份脚本连接的 `118.31.37.87` 就是 RSC 目标机，无需再猜测
另一个主机。2026-09-24 再次只读核实：Ubuntu 24.04.2/x86_64、Docker 29.1.3，
根盘 49 GiB 中约 35 GiB 可用、内存约 2.1 GiB available；`star-oam-web-1`
仍占用宿主 80/443。现有 `rsc-preproduction-db-db-1` 为健康运行的 PostgreSQL
16.15，Compose project 为 `rsc-preproduction-db`，只接入独立且 internal=true 的
`rsc-preproduction-db-internal` 网络。服务器上已有 `rsc-pilot-api/web:f713999`
镜像，但这些是旧候选，不代表当前工作树已部署。上述只读检查没有改动主机；
后续隔离预检只新增镜像及构建缓存，原有容器、卷、网络、数据库或服务没有切换。
目标上线仍须按新候选镜像与隔离配置重新门禁。

只读期初导入新切片：`opening_count_import_source.py` 在正式当前身份下核验
`stocktake.count`、完成的本人私有文件、上传人版本和 OSS provider，之后才读取
HEAD/GET 钉住的源文件。`prevalidate_authorized_opening_count_import` 在第一段
事务关闭后以另一事务调用正式业务预校验，避免文件锁与盘点任务锁逆序。
初版源读取聚焦 **42 passed**、编译及 `git diff --check` 通过。随后新增
`POST /api/v1/stocktakes/opening/imports/opening-count/business-check` 受限只读
业务预检入口，只接受四个精确 UUID 和安全的请求头，由当前身份、已完成私有源与
正式盘点服务重验，返回任务/权限版本、摘要和受控错误行，禁缓存、不返回原单元格。
扩展后聚焦 **46 passed**、编译与差异检查通过。仍无持久
`FileJob`、业务错误报告文件、确认 API 或实际入账；该路由不是提交入口。下一切片
必须先扩展 `0136` 的导出专用数据库守卫，绑定导入任务的文件、任务/轮次/范围、
版本和原请求，做状态转换/反例、PG16 API 角色测试，再开放确认执行。
当前候选仓库安全扫描 **1,675 文件 PASS**。随后以全新自有 PostgreSQL 16.15
空库跑至 `20261119_0140` 并完成 API 权限、期初导入业务预检、报表完整流与
九项短信配置夹具，退出 0，证据
`artifacts/local-current-head-pg16/checks/run-hoih8oiy/checks.json`；
`cluster-state.json` 证实 `stopped`、`serverExitCode=0`。该脚本在新 HTTP 路由
加入前运行，且未调用私有源授权组合服务，故准确新候选 PG16 实际 API 角色链
仍是下一轮专项测试缺口；
`ciReleaseGate=false`，不能替代准确候选 GitHub CI 或真实 OSS。
随后补入真实 PG16 API 角色的合成私有文件完成与本人读取、无授权用户拒绝反例。
首次 `run-obid_p8i/failure.json` 因夹具使用 Mac 当前时钟作为完成时间，比
数据库事务时间略晚而被正式文件守卫拒绝；该次实例安全停止。夹具改用数据库
事务时间后，**当前代码候选**全新 PG16.15 空库至 `0140` 全部门禁退出 0，
`openingSourceRuntime` 五项均为 true，见
`artifacts/local-current-head-pg16/checks/run-jaueic67/checks.json`；
`cluster-state.json` 为 `stopped` / `serverExitCode=0`。该源读取使用合成
存储适配器，仅证明数据库权限和源文件调用绑定，不证明真实 OSS HEAD/GET。
当前后端候选三片完整静态随后分别退出 0：第一片 **2,122 passed / 1 skipped /
15 subtests passed**，第二片 **2,409 passed / 2 skipped**，第三片
**2,314 passed**，合计 **6,845 passed / 3 skipped / 15 subtests passed**。
日志 `artifacts/static-current-head-20260924/shard-{0,1,2}-v2.log`，每片只有
Starlette/AnyIO 一条弃用警告。首轮缺 Node PATH 和旧迁移来源哈希的失败日志
`shard-{0,1,2}.log` 保留，不计入通过。后端完整静态结束后仅修改公开首页的
备案页脚和对应前端测试/CSS；受影响的后端公开入口、知识源和来源配置测试
**59 passed**，见同目录 `public-after-filing-focused.log`。

准确最终前端候选独立重跑：Web **83 文件 / 1,387 项通过**，小程序
**1,070 项通过**，TypeScript、公开站与 `/xx` 构建、`verify:pilot-release`
退出 0；相关日志均在 `artifacts/static-current-head-20260924/client-*.log`。
公开构件含备案号、工信部链接、查询标题和后台入口；本地预览浏览器核对公开
查询页无登录框，页脚底部中央显示截图提供的 `豫ICP备2026043964号-1`，
下方链接 `https://beian.miit.gov.cn/`，位置与链接按
[工信部非经营性网站备案指南](https://gsca.miit.gov.cn/bsfw/bszn/art/2020/art_82d75f07581447f5a8cf74db402554f2.html)
核对。`verify_public_entry.mjs` 现强制
检查构件中的备案号与链接，入口脚本及配置受影响测试再跑 **15 passed**，
证据 `client-public-entry-verify.log`、`public-entry-gate-after-filing.log`。
仓库安全扫描 **1,675 文件 PASS**、
`git diff --check` 通过。私有构建仍有单块超过 500 kB 的性能提示。
当前 Git 可见 `cloud_oam` 输入的 SHA-256 清单及日志摘要索引保存在
`artifacts/static-current-head-20260924/input-manifest-final.json` 和
`result.json`；结果文件标明 `releaseReady=false`、`commitAllowed=false`。

**线上路由阻断：** 2026-09-24 只读浏览器复核发现 `https://rscwz.cn/`
和 `/xx/` 目前都显示旧“登录工作台”，公开根路径仍有手机号/密码输入框。
域名与 www 均解析至用户确认的 `118.31.37.87`，HTTPS 两路径返回 200，
但这只是旧镜像可达。目标机运行的 `star-oam-web:0.9.0` 内 Caddyfile
仍把所有路径回退到 `/srv/index.html`；本地新 Caddyfile 才分别使用
`/srv/public` 和 `/srv/warehouse`。必须以准确候选完成独立镜像、路由、
健康、权限和回滚门禁后切换；本轮未改服务器。GitHub CI、真实 OSS/短信/
KMS、真实身份/期初及业务 UAT 仍无放行证据，因此不提交、不推送、不部署。
已补强部署烟测，使正确标题但错误的公开 JS、缺备案号和两侧资源 HTML 回退
均失败关闭；本地双构建完整烟测退出 0，公网旧站仍以
`public_home_not_knowledge_entry` 退出 1。线上登录选项只读结果为密码开启、
短信/微信关闭，正式入口尚未具备。8 项烟测反例与 167 项部署协调器回归通过，
详见[双入口线上烟测专项](PILOT_LIVE_ROUTE_SMOKE_20260924.md)。
本次烟测脚本和测试变更发生在前述三片完整静态之后；旧
`static-current-head-20260924/input-manifest-final.json` 对应那次已通过候选，
不能直接标为本次最终源码的全量静态结果。新输入摘要、前后变动清单及
定点日志索引另见 `artifacts/pilot-live-route-preflight-20260924/result.json`。

| 范围 | 性质与下一步 |
| --- | --- |
| N0 通知扩展权限 | 0129、锁后刷新与原目标受控重排已通过当前源码 PG16 和 398 项聚焦；当前本地静态/客户端也已通过，GitHub 准确候选门禁和真实渠道仍待完成 |
| 当前候选本地全量门禁 | `0140` 后端三片完整退出 0，6,845 passed / 3 skipped / 15 subtests passed；备案页脚后公开入口后端 59 passed，Web 1,387 passed、小程序 1,070 passed、双构建和试点校验退出 0；PG16.15 当前合成角色链通过。GitHub PG16 runtime/Client、目标机新镜像与真实外部链路仍无放行证据；旧候选 6,784 项结果仅作历史 |
| B3 持续日终对账 | **实现/验收缺口**：纯计算 24 项、本地账本 13 个及已发布来源读取 16 个 PG16 场景通过；已增加只读日终接口，8.08 已接入正式审核服务/权限；8.09 已补 HTTP、专用附件与真实审核查询，8.10 已补查询页面；8.11 已补服务端安全坐标恢复和永久终结。8.12 已补浏览器持久恢复与写操作页面；8.13 已完成开启/断线恢复/终结等浏览器 API 联验子集，附件至总部审核的完整链路及设备验收仍缺。多仓/零仓/撤权实际发布专项 7 项已过。两侧计算适配 29 项聚焦和 3 个 PG 场景已过。映射治理候选 28 个 PG 场景已过。截止持久化候选 26 个 PG 场景及 34 聚焦已过。截止事务期限 33 个 PG 场景已过。独立进程候选 12 个 PG 场景已过。8.02 正式模块/0130/安全目录及 39 个 PG 场景已通过。本轮补六操作运维 CLI 与实际本地 PG16 正向验证；本轮已补镜像内入口/负向本地 PG/当前八角色合成容器联合恢复；下一步真实映射批准、实际网页/API 与设备联合验收及连续三天真实证据 |
| B5 正式 OSS 备份 | **验收/运维缺口**：正式入口、worker、共享校验和 Compose 已落地，当前 13 入口、5 PG 场景与实际配置检查通过。8.01 实际容器全链路 5 场景及独立新库恢复已通过，API 为测试配置、对象传输为合成；当前八角色合成容器联合恢复已通过，另有六张非空日终表在第二新库逐表一致；真实 OSS、异地保管、持续归档、密钥和生产恢复仍缺 |
| B1/B2 来源与历史迁移 | 采集/审核/物料和控制发布已有局部实现；真实语义、组织人员发布、完整原表、自动交接/密钥治理与历史迁移执行尚未验收 |
| N1—N5 通知 | 外部渠道 adapter、可信回调/验签/重放防护、双向机器人及专用并发/真实送达证据仍缺；已有 Outbox/原目标/运维恢复不能冒充实发 |
| B4 身份和 PNVS | 真实唯一人员映射、首管理员、微信/PNVS/KMS/私有附件链未验收；PNVS-01 配置绑定、历史挑战拒绝、专用 STS 注入及本地 PG16 验证已实现；无停机自动刷新、真实实发/回读和 RAM 最小权限仍缺 |
| B6/B8 业务 UAT | 关键收发/入账/工单/退回/盘点/SN 场景需要真实角色、设备和附件验收；不可将已实现模块重标成“未开发” |
| B7 与灾备/灰度 | 500 用户模型与压测、限流/告警、持续归档、异地保管、RPO/RTO、角色维护、实际容器故障及生产灰度仍待验证 |

详细问题在[正式基线缺口审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)和
[PNVS-01 专项计划](PNVS_01_AUTHENTICATION_PLAN_20260921.md)。通知成功不改变库存，对账批准也不生成库存。

## 5. 待答复与执行约束

- 浏览器 HTTPS 联验的临时用户级 SSL 证书信任请求尚未答复；证书仅已生成，系统信任未修改。实际 API 证明使用进程专用叶信任，不替代原 Edge 信任或真实浏览器验证。

- **公开 CI 候选提交例外仍未获答复**。本地状态保留原 pendingApproval；不能凭一般“继续”
  当作例外已批准。所有必要本地检查和准确改动应先准备齐全，当前不提交/推送/部署。
- 原 Edge 后续只读兼容性检查已通过，旧 `blocked` 记录保留为历史；当前不再等待那次恢复答复。知识源按用户要求暂停；再次涉及 NIO Chat 时使用已授权的官方托管只读路径，不自动登录、导出企业凭据或扩大业务写入。
- 不发送真实业务通知、不发送认证短信、不写生产库或业务系统，不开通付费服务。
- GitHub 专用破坏性 PG16 gate 不得伪装 CI 后在本机运行。局部实际 PG16 检查只用
  `backend/tests/local_pg16_cluster.py` 建立全新自有实例，不接受外部 DSN，也不重启旧数据目录。
- 工作中按需核验额度；到 10% 阈值时停止新的开发，准确处理自有运行资源，记录 terminal/
  running/unknown 状态并汇报。不能将额度不足写作“上线完成”。

## 6. 接手时的最短操作顺序

1. 核验指定工作树、分支、HEAD 和当前 diff；读基线及 AGENTS.md。
2. 读取 `artifacts/static-current-head-20260924/shard-{0,1,2}-v2.log` 的完整终端摘要，以及备案页脚修改后的 `public-after-filing-focused.log`；首轮失败日志保留，不与通过数相加。
3. 读取同目录 `client-web-test.log`、`client-mini-test.log`、两份构建日志和 `client-pilot-verify.log`；核对公开页备案号、工信部链接与无登录框。随后继续 B3/B5、目标机双入口切换准备和外部门禁。
   完整失败记录保留；未经原公开 CI 候选提交例外的明确答复与外部门禁，不提交或发布。
   部署入口先按 [8.18](PILOT_DEPLOY_ENTRY_20260922.md)核验当前候选的 Linux/Docker、独立网络/卷和镜像；真实 KMS/SMS/OSS、身份期初与业务 UAT 仍须单独验收。知识源暂缓，不反复探测。
4. B5 正式集成、镜像构建和[8.01 容器全链路](BACKUP_CONTAINER_E2E_20260921.md)已完成，真实 OSS/生产验收仍缺。
   8.02 已整合 7.95 非目标仓覆盖适配、7.96 事务剩余预算与 7.97 独立进程入口，
   新增 0130/安全目录；8.03 已补角色配置与 API 共存。8.04 已补新增角色恢复流程并完成原生验证。8.05 已补日终区域查询和权限。8.06 已补解释/审核核心，8.07 已验证持久化候选；8.08 已完成正式 0132、权限、安全目录与服务层并重跑期初完整流程。8.09 已接入有完整期限的 HTTP 命令/原回执恢复、日终专用附件范围和真实审核查询。8.10 已补查询页面、严格分页协议和同源 Cookie 接入。8.11 已补服务端原坐标恢复和永久终结证明，真实 PG 竞争与审计拒绝已验证。8.12 已补浏览器安全坐标持久化、跨标签锁及解释/审核/附件操作。8.13 已完成浏览器开启/断线恢复/终结子集并修复提示串档案。下一批补附件到总部审核完整联验，将原生 HTTP 用例纳入 CI，并做当前容器、联合恢复与完整门禁。
   真实网络/进程管理与连续三天验收仍缺；合成测试不等于真实验收。
5. 更新本页和规范状态，保留原始失败。额度到 10% 时停止新开发并如实交接。

规范本地状态：`cloud_oam/artifacts/notification-launch-candidate-20260919T152026Z/local-run-state.json`。
该文件是接续索引；实际源码、进程、终端结果和外部系统精确回读是最终依据。

维护本页时直接更新现行值，不反复追加“当前状态”标题；历史记录保留在归档和专项证据中。
