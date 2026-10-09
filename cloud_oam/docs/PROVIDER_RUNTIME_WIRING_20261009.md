# 2026-10-09 提供方运行入口接线交接

**试点 MVP，不等同完整 V1；当前未提交、未上线，发布判定 `not_ready`。** 本文件覆盖此前“OpenBao 尚未接入 Settings / API factory / startup / readiness”的代码状态，不改变历史测试或部署记录。基线提交仍为 `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3`；准确工作树来源与本批证据见 `artifacts/provider-runtime-wiring-20261009/milestone-receipt.json`。没有操作生产服务器、真实云渠道、密钥材料或业务数据库。

## 已接通的代码链

1. Settings 明确支持 `aliyun_kms` 与 `openbao_transit_v1`，默认仍 `disabled`。OpenBao 使用九项非密坐标：实例、registry/socket/token 文件路径和五项 UID/GID。配置校验在独立轻量模块中执行；冷导入不初始化全局数据库，不读取 token、文件或网络。不存在“配置 attestation=true 就可发布”的开关。
2. API 启动先校验实际数据库身份、ACL 与不可变目录，再 SELECT 扫描仍需读取的历史/current 引用，以及显式 active 对应的独立 claims/pins。先核验共用 OpenBao registry 的完整集合，再按用途投影；两用途不能因分开构造而绕过整份 registry 的核对。
3. `ProviderKeyRuntime` 保存固定配置、不可变完整 pin 与 wrapped entry 元组。它不保存 Session、认证 reader 的数据库闭包、请求 cipher 或明文 DEK。所有启动门禁通过后才发布到 app.state，失败不发布，关闭时清空；密钥登记、注册表、active 配置改变需受控重启。
4. 认证和联系人请求从当前 app.state 获取 runtime，构建请求专属 cipher；生产环境缺失或类型/配置不匹配直接失败关闭。认证预检不新增数据库事务，因此不破坏独立短信限流事务。同一请求的认证幂等可复用预检 cipher，但不跨请求缓存明文。
5. 认证历史版本按不可变 claim 分流，支持不同历史 Aliyun CMK，也支持 active Aliyun + 历史 OpenBao。联系人支持 OpenBao 当前 v2 写及历史 v2/v1 读，v1 历史按其实际 CMK/version 路由，不套用当前密钥。未登记、缺失、歧义、绑定漂移、错误摘要均阻断；OpenBao 失败不回退到 Aliyun。
6. Readiness 以用途、完整 provider binding 的不透明摘要和应用版本构成受控查表标识；覆盖所有 active 和仍需历史版本。保留四线程、四秒总预算、TTL 和 single-flight，调用实际 provider probe，不读取请求 DEK 缓存。配置变化即使在成功 TTL 内也返回 503。
7. CLI 的默认 pin gate 先验数据库角色/目录再构建相同 runtime；只报告结构可用，不报告真实 decrypt 或发布通过。旧 Aliyun `--plan` 保留原 schema，出现任何 OpenBao 声明即拒绝，不冒充混合提供方审核计划。Compose API 与 gate 共用九项坐标映射，未设置默认秘密、虚构身份或不存在文件的挂载。

## 当前明确限制

- 联系人 **active Aliyun + 存量 OpenBao v2** 的逆向切换未实现，启动明确拒绝。不能仅因两种密钥都可 probe，就宣称 Aliyun 联系人 reader 可读取 v2。回滚不得删除 v2 事实或旧密钥登记来绕过这一守卫。
- OpenBao 当前运行 registry 必须精确匹配 active 与仍需存量 key 集合，多余条目也会阻断。未引用或已过期的旧 wrapped 材料可保留在受控历史归档；不能删除独立 DB pin、历史事实或恢复材料来迁就配置。
- 提供方或应用密钥版本变更须先停止并排空旧实例，再以稳定数据库状态构建新 runtime。**没有宣称支持滚动密钥切换**：旧实例未完成的认证操作可能在扫描后才产生旧版本 replay 引用。后续若需要滚动切换，须独立设计经审核的保留版本集合。
- 没有新增可直接部署的 OpenBao overlay。真实 API/门禁 UID、只读 registry/token/socket 目录、token 投影器、Unix peer 身份、权限和生命周期仍须在受控部署 override 中设置并实测；环境变量映射通过不等于这些资源已经准备好。

## 聚焦验证与失败保留

本批仅测试新增或实际受影响的路径，没有重跑已终态的完整业务套件。首次失败不覆盖、不累计成成功证据。

| 证据 | 已验证范围 | 结果与限制 |
|---|---|---|
| Settings 与 Compose 新测试 | 默认关闭、两 provider、输入类型/路径/UID、冷导入、API/gate 一致映射 | 111 passed；首轮部署测试 13 failed / 11 passed 揭示真实冷导入缺陷，拆出轻量模块后修复 |
| API 依赖及既有认证聚焦 | 7 类认证路由、联系人 HTTP、生产缺 runtime、成功登录、同键/refresh replay | 23 个新增及 7 个既有节点通过；两次新增 fixture 绑定错误已精确修正 |
| Runtime 合成集成 | 真实 SELECT、registry parser、AES-GCM、跨 CMK/provider、完整 binding、请求缓存、异常链 | 首轮两个 fixture 格式错误导致 10 failed / 6 passed；修正后 16 passed；后续新增 7 passed；异常净化后新增 1 项及 5 个受影响节点通过（24 个唯一节点，跨阶段记录） |
| Startup / readiness | DB 门禁先行、全部成功后发布、失败无状态、shutdown 清空、真实 probe 与配置漂移 | 18 个唯一节点通过；初始 3 passed / 15 failed 捕获发布时序缺口，修复后仅复验 15 个失败节点通过 |
| CLI | Aliyun plan 与 OpenBao 声明拒绝，结构检查与发布状态分开 | 17 passed |
| 受影响兼容测试 | 原 health 7 项、两个过时静态断言、轻量 dataclass 默认契约 | 9 passed + 1 passed；未扩大为旧全套回归 |
| 本机 PostgreSQL 16.15 | 六张最小元数据表、独立 API SELECT-only/READ ONLY、完整 claims/pins/runtime 与真实 AES | 首轮 14/14；修复后仅补 contact-preflight 4/4，均退出 0，73 源文件稳定，两个集群正常停止；仅 fake provider transport，不是完整 0181 迁移、正式 ACL 或 hosted 证据 |

联系人预检新增 9 项先在修复前全部失败，修复后 9 passed，包含提交/审批/外部证据三条真实 HTTP 路由在服务失效或 DEK 无效时零业务写入。真实本机 PG16 原始回执为 `local-pg16-runtime-ijo_kt0w/receipt.json`；修复聚焦回执为 `local-pg16-runtime-3reuo4k8/receipt.json`，均位于本批 artifact 目录。native helper 不提供硬内存上限参数，本轮不声称施加了容器的 512 MiB 限制。

最后审查发现 OpenBao 联系人 `active_key_version()` 只返回版本，不能作为原写接口的实时取钥预检。新增独立 `preflight_active_key()` 并在请求写入口调用，保持启动构造无网络；对不重新加密的提交/审批类操作也保留密钥不可用时阻断。与原 14 项 PG16 证据分开记录此后续修复的聚焦验证，不把旧 source hash 替换成新代码。最终数量、源码、日志/收据摘要和安全检查以聚合回执为准。

## 下一步与上线阻塞

1. 当前整棵未提交树尚未形成可发布候选。完成审查、安全检查和来源绑定后，按现有候选流程取得新 SHA 的准确 hosted PG16 / Client 终态。旧 run 的历史快照不证明本批代码通过；本轮没有刷新 GitHub 状态。
2. 0181 的真实迁移、完整权限、并发及回滚继续待验。本机最小表测试不能替代这些门禁，也不能将此前 hosted 失败改写为通过。
3. 实测受控 Linux 身份、OpenBao token 续期/撤销/过期/sealed 拒绝、真实 Transit、STS/OIDC 与私有 OSS 归属/权限/附件；恢复保管人和离机介质仍需落实。
4. 真实 SMS-only 登录、正式人员映射、角色与小程序真机 MVP UAT、HTTPS/API、密钥/数据库/附件恢复及部署回滚分别验收后再切换。

有限业务链保持 **申请/提交→审批→最小货源分配/占用→后台人工履约并记录发运→本人收货→个人仓入账**。技术员只保留申请、状态查看、本人收货和本人入账；区域/总部保留必要人工履约。拣货和后置区块按 capability/角色隐藏，不删除底层状态、迁移、契约和出库依赖。不可变库存流水、幂等、审计、权限、安全约束、取消/关闭守卫和最小站内通知保持。

后续迭代仍明确包括：履约后供给容量/0178 分配后建计划、释放后重分配和复杂历史恢复；拒收/退回补偿、stock-return 全链；工单物料消耗/替换/冲销/旧坏件回收；报损报废/成色纠正；盘点/期初导入/冻结/复盘；人员调拨/离职交接；复杂报表/Excel/打印；短信/微信/飞书真实业务通知渠道及投递运维。延期的是业务通知渠道，首发短信认证不因此取消。
