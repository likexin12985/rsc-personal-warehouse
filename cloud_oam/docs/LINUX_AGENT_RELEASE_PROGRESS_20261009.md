# 2026-10-09 Linux Agent 与上线条件接续

**试点 MVP，不等同完整 V1；`not_ready`，未切换生产。** 工作树为 `06f6/oam`，分支 `codex/notification-delivery-worker`；本批起点 HEAD 为 `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3`。已有未提交改动保留。本文中的本地、真实 Linux 隔离、hosted CI 和正式部署是分别验收的对象。

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

## 用户恢复安排与现场依赖

用户本轮明确决定：**由本人保管全部恢复材料，保存到已连接的希捷移动硬盘**。因此不再等待第二名人员，也不得写成双人分持、独立复核或已有第二副本。单人、单盘事实应一直保留在恢复记录中。

已只读核验 `/Volumes/Seagate Backup Plus Drive` 为实际 USB/APFS 外盘、UUID `4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC`、可写；卷本身未加密。准备工具仅新建独立 AES-256 小型映像，不能修改整盘或在未挂载时创建本地替代目录。`hdiutil -agentpass` 不保证 GUI；本机非交互创建未成功，后续密码由用户在获准的系统界面亲自输入。

首次空容器创建 `7ab884150e2e` 返回失败；用户确认没有看到密码窗口。随后精确 `status` 回读 `imageExists=false`，未生成或写入任何正式恢复材料。源码在创建进程期间有末尾防拔盘补强，故该创建尝试不声明与最终源码摘要一致；后续用冻结 `status` 独立核验，不覆盖或重建同名对象。原生磁盘工具接管另被电脑操作工具拒绝（`Computer Use was not approved to use Disk Utility`）；已停止该路径，等待用户授权该应用或手工创建加密映像。终端完全访问不等于该应用的电脑操作授权。

阿里云 RAM 角色页在原 Mac Edge 的 9224 兼容入口实际返回 `ConsoleNeedLogin`。角色清单为未知，不能把错误页的空表当零角色。已请用户在原 Edge 刷新登录；没有导出凭据、创建云资源或发送短信。SSH 连接成功与 GitHub 代理可用分别核验，不要求为此重登 SSH/GitHub。

## 后续执行顺序

1. 0181 真实失败边界和部署 live bind/PNVS 独立 OIDC 的聚焦验证已完成；完成最终独立审查并冻结候选后提交，按该新 SHA 获取 hosted PG16/Client 终态。
2. 在加密恢复容器实际可用且解锁验证后初始化正式 Bao、登记恢复材料、正式两用途 wrapped registry/pins 和运行身份；完成封存、受控重启、离机恢复演练。
3. 恢复阿里云会话后核验或配置公开 issuer/JWKS、精确信任的 RAM OIDC、独立 PNVS/OSS 角色和私有 Bucket，实测身份回读、跨窗口刷新及拒绝边界。配置解析不是云身份通过。
4. 最后完成真实 SMS-only 登录、正式人员唯一映射、分角色/真机主链 UAT、附件私有访问、DB/密钥/附件恢复及发布回滚，证据绑定后执行 prepare/start 和域名切换。

范围保持申请/提交→审批→最小货源分配/占用→后台人工履约并记录发运→本人收货→个人仓入账。拣货和全部已延期后置区块保持隐藏，保留底层迁移/契约/依赖。真实短信登录为首发必要项；短信/微信/飞书业务通知及投递运维仍属后续迭代，不因本次部署扩大业务范围。
