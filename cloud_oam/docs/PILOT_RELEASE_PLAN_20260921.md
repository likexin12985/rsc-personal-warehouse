# RSC 个人仓三天试点上线执行单

更新时间：2026-09-22。目标是把现有 `/xx` H5 作为单区域、受邀用户试点上线；这份执行单不把试点标记为完整 V1.0 正式生产。

当前执行状态：本地候选已有前端构建、后端聚焦门禁和安全检查证据；2026-09-24 已对用户确认的目标机完成只读预检，但本批部署入口尚未在目标 Linux 主机执行，也没有写入真实生产数据库或发送真实短信。目标机是 Ubuntu 24.04 / `x86_64`，现有旧 `star-oam-web` 在 `rscwz.cn` 独占 80/443；当前本地镜像为 `arm64`，旧试点镜像与源码均不是当前候选。部署前仍需补齐同候选 `x86_64` 镜像、生产凭据、真实身份/期初数据和试点验收证据。用户已将飞书知识源降为低优先级，暂缓读取；它不阻断 `/xx` 试点开发，严格公开发布门禁仍保留。

## 试点范围

- 1 个区域，5–10 名已核验人员，约 20 个 SKU。
- 只开放 HTTPS H5 `/xx`；公开首页可以继续展示资料待更新状态。
- 只使用微信或手机验证码登录，禁止密码登录和临时测试账号进入生产。
- 试点必须使用已核验的组织、人员、SKU、库位和期初盘点数据。
- 试点流程为：登录与身份映射、库存/个人仓查询、需求申请、内部审批、人工发运、收货验收、个人仓入账。
- OAM 保持只读；审批、出库、发运、签收、OAM 收货、个人仓入账、通知和对账分别记录。

## 三天投入估算

在目标 Linux/Docker 主机、域名、生产凭据、试点人员和期初数据已经准备好的前提下，按每天 6–8 小时计算，Codex 的有效开发与验收投入约为 16–22 小时：

- 第一天 6–8 小时：冻结候选和数据清单 1 小时，构建与自动化门禁 2 小时，生产配置预检 2 小时，目标机部署和冒烟 1–3 小时。
- 第二天 6–8 小时：真实登录与角色验收 2 小时，需求到入账的试点链路 2–3 小时，重复提交/异常收货/恢复演练 2 小时，缺陷修复 1 小时。
- 第三天 4–6 小时：冻结 SHA 和重跑门禁 1 小时，导入已审批试点数据 1 小时，单区域灰度 1–2 小时，首日监控和回滚记录 1–2 小时。

域名、短信或微信资质、KMS 双人 pin、真实身份/期初数据或可恢复备份任一项未就绪时，Codex 可以继续完成代码和发布包，但不能把剩余时间计算成已上线；目标机准备通常还会增加 1–2 个工作日。

## 目标机一次性输入

真实部署只需要把以下内容放到目标机或安全凭据系统，不要把密钥粘贴到聊天中：

1. Linux 主机上的 Docker Compose v2、项目候选 SHA 和可写的 Compose 工作目录。
2. 已核验的 `APP_DOMAIN`、HTTPS 证书/反向代理，以及 Caddy/ALB 的直接对端 IP，填入 `OAM_TRUSTED_PROXY_IPS`。
3. 生产 `.env` 和绝对路径、权限为 `0600` 的 KMS registry；短信 PNVS、私有 OSS、数据库角色密码和独立 HMAC 均使用生产值。
4. 已核验试点人员、角色、SKU、库位、期初盘点和回滚/恢复记录。
5. `SMOKE_BASE_URL=https://<APP_DOMAIN>`。
6. 固定且专属的 `PILOT_COMPOSE_PROJECT=rsc-pilot-<已核验标识>` 和不可复用的候选 `PILOT_IMAGE_TAG`；两阶段必须使用相同值、相同配置和候选文件。
7. 明确的 `OAM_BACKEND_SUBNET`、子网内 `OAM_WEB_IPV4_ADDRESS`，及与该 IP 相同的 `OAM_TRUSTED_PROXY_IPS`。子网不能与旧栈或其他 Docker 网络重叠；停止旧容器不会释放旧网络。

输入齐全并完成该目标的执行审批后，在 `cloud_oam` 工作目录运行以下两阶段入口。下面变量须替换成已审核值，不要把真实密钥粘贴到聊天或命令输出：

```sh
export PILOT_COMPOSE_PROJECT='rsc-pilot-<已核验标识>'
export PILOT_IMAGE_TAG='<已审核候选标识>'
# 必须为工作树外的受控绝对路径；目录 0700，prepare/start 使用同一部署身份。
export PILOT_STATE_DIR='<工作树外的受控绝对目录>'
export SMOKE_BASE_URL='https://<已核验域名>'
sh scripts/deploy_pilot.sh prepare
```

`prepare` 现在要求候选标签没有成功回执，检查解析配置和 Docker 子网，再顺序构建 DB/API/Web、启动独立数据库并迁移，然后明确停下；此时应用未启动。目标机可用内存只读核验约 2.1 GiB，部署入口固定 `COMPOSE_PARALLEL_LIMIT=1`，避免三个服务同时构建争用内存；这仍不能代替实际资源/构建验收。按 [KMS 登记清单](ALIYUN_KMS_ENVELOPE_KEY_RUNBOOK.md)完成同一 registry 的双人计划复核和持久化 pin 登记。该步骤没有自动插入或覆盖 pin。

生成 KMS 计划时也要显式绑定这个项目和镜像。部署脚本内导出的变量不会传回调用者 shell，不能直接使用清单中的无项目通用命令：

```sh
RSC_API_IMAGE="rsc-pilot-api:$PILOT_IMAGE_TAG" \
RSC_DB_IMAGE="rsc-pilot-db:$PILOT_IMAGE_TAG" \
RSC_WEB_IMAGE="rsc-pilot-web:$PILOT_IMAGE_TAG" \
OAM_EDGE_DB_NETWORK="${PILOT_COMPOSE_PROJECT}_edge_db" \
RSC_RELEASE_PROFILE=pilot \
docker compose --env-file "${PILOT_ENV_FILE:-.env}" \
  -f "${PILOT_COMPOSE_FILE:-docker-compose.yml}" \
  --project-name "$PILOT_COMPOSE_PROJECT" --profile ops \
  run --rm --no-deps --pull never kms-pin-plan
```

将计划输出保存在工作树外的受控变更附件；其后的只读核对和获批 INSERT 必须连接同一试点数据库。Compose `run` 没有 `--no-build` 选项，此处不传 `--build`，并禁止拉取缺失镜像；镜像缺失应停止检查准备记录。

受控切换释放 80/443 后，在相同环境和候选下执行：

```sh
sh scripts/deploy_pilot.sh start
```

`start` 先核验该标签的私有 prepare 回执，绑定候选/解析配置/静态挂载、实际镜像 ID、DB 容器/卷和正式 HEAD，再运行只读 pin gate 和 API/Web，最后最多三次只读冒烟。所有容器按回执中的不可变镜像 ID 运行。它不重新迁移、不构建/拉取新镜像、不自动停旧栈或清理失败资源；任何失败按阶段记录并保留现场，检查后再决定恢复动作。

目标机当前的旧 Web 正在服务同一 `rscwz.cn` 域名，不能把新站当成另一个独立域名并行挂载。切换窗口必须先固定旧 Web 容器/镜像、Caddy 卷和 Compose 配置身份，完成旧站可恢复备份；新项目 `prepare` 和 KMS/DB 验证结束后才受控停止**仅旧 Web**、确认端口释放并执行新 `start`。旧 API、旧数据库和 Edge Receiver 不因网页切换而停止。若新站冒烟失败，先精确回读新项目状态，再停止新 Web、恢复旧 Web 的原项目/镜像与 80/443 绑定并核验旧站 HTTPS；不得用旧数据卷或旧数据库冒充新个人仓期初。

## 构建规则

默认构建仍是严格生产档：

```sh
pnpm run build
pnpm run build:warehouse
pnpm run verify:release
```

当公开知识目录还没有完成真实导入和审核时，只能使用明确标记的试点档：

```sh
RSC_RELEASE_PROFILE=pilot docker compose build web
```

试点档只放宽公开目录的 `pending` 状态，仍要求 `/xx` 私有产物、后端启动边界、认证、数据库迁移和运行时安全门禁通过。它不允许绕过后端生产配置，也不证明正式生产就绪。

## 三天执行顺序

### 第一天：冻结和预生产

1. 核对工作树、最终 diff、迁移 HEAD 和试点数据清单。
2. 配置 HTTPS、数据库、唯一身份映射、短信或微信登录、私有文件存储和回滚点。
3. 在目标主机先运行只读预检：`RSC_RELEASE_PROFILE=pilot python3 scripts/pilot_preflight.py --env-file .env`；失败时不启动 Compose。
4. 用 `RSC_RELEASE_PROFILE=pilot` 构建 `/xx`，运行前端完整测试、后端聚焦测试和迁移预检。
5. 预生产冒烟：登录、角色上下文、库存查询、需求查询、收货候选和个人仓入账查询。

预检输出中的 `status=pass` 只表示已解析的 Compose 配置满足静态边界；它固定输出 `deploymentReady=false`，仍必须继续完成真实镜像、HTTPS、短信、KMS、OSS、迁移、身份/期初数据、恢复和业务验收。

目标机受控执行使用上面的 `prepare` → KMS 登记 → 端口切换 → `start` 顺序。单独静态预检只检查配置；部署入口额外绑定项目、镜像、数据库和烟测域名。Compose 卷与网络必须归属同一专属项目，DB/API/Web 使用 `rsc-pilot-*:<PILOT_IMAGE_TAG>`，迁移和 pin gate 使用同一 API 镜像；不得用自定义 Compose 指向旧卷、远端业务库或旧镜像标签。

`PILOT_ALLOW_PORT_CUTOVER=true` 不再绕过端口占用。入口检查 Docker 查询失败、80/443 映射到其他容器端口、既有网络子网重叠时均停止；宿主机路由、非 Docker 监听和真实 HTTPS 仍需目标机验收。它不会替代第二天的真实人员、发运、收货、入账和恢复演练。

部署前要让 `OAM_WEB_IPV4_ADDRESS`、`OAM_BACKEND_SUBNET` 和 `OAM_TRUSTED_PROXY_IPS` 三者一致；当前 Compose 将 Caddy 固定在私有网络地址，`pilot_preflight.py` 会拒绝不一致或 loopback 值。`FORWARDED_ALLOW_IPS` 只控制 ASGI 代理处理，不能单独改变应用限流使用的客户端 IP。

### 第二天：真实试点验收

1. 用真实试点人员和设备完成登录、权限边界、需求、收货和入账验收。
2. 验证附件上传、异常收货、重复提交、会话刷新和精确结果未知恢复。
3. 做一次全新数据库恢复和应用回滚演练；失败则停在预生产。
4. 启动第一天的真实 OAM 控制数与本地账对账记录。

### 第三天：灰度上线

1. 冻结候选 SHA，重新运行准确候选的必要门禁并保留原始日志。
2. 只导入已审批的试点数据，禁止把目录缺失、未知库存或未映射人员当作零值。
3. 单区域灰度，观察登录失败、越权、库存流水、任务积压、错误率和备份状态。
4. 形成上线记录、回滚记录和首日差异解释；未满足任一放行项则回到预生产。

## 试点放行条件

- 生产无密码登录已启用并取得真实发送/校验成功证据。
- 每位试点人员只有一个准确业务身份和有效角色范围。
- 所有库存变化都有单据、操作者、时间、幂等键、前后账户和审计记录。
- 负库存、重复 SN、重复入账和未授权越权均为零。
- 真实备份恢复和应用回滚演练通过。
- 试点入口、数据库、附件和监控均使用 HTTPS、私有存储和独立生产凭据。

回滚按两层执行：应用层保留上线前后两组固定 digest 的 API/web 镜像、解析后的 compose 配置和环境版本，异常时切回上一组镜像并重新做登录、库存查询和写入只读核验；数据库不得把含业务事实的 Alembic 迁移直接 downgrade，必须保留切换前联合备份包，按受控恢复 runbook 恢复或以前向修复为准。没有完成一次全新库恢复和应用切回演练，不得把灰度扩大。

## 不属于三天承诺

完整 V1.0 仍需真实 OAM 来源/迁移、连续至少三天省级差异解释、真实通知供应商和回调、500 用户压测、RPO/RTO 演练、跨设备 UAT、总部试用和正式灰度放量。公开知识目录仍需真实导入和公开性审核后，才能使用严格生产档构建。

## 8.23 回执和快照保管补充

`PILOT_STATE_DIR` 默认是部署用户的 `$HOME/.local/state/rsc-pilot`，也可指定工作树外的受控绝对目录，
目录须属于该用户且权限 0700；成功回执位于 `<state>/<project>/receipts/<tag>.json`、权限 0600。
两个阶段使用同一 state、用户、项目、候选标签、配置文件与烟测目标；使用 `SMOKE_RESOLVE_HOST/IP`
时必须成对且在 prepare 前固定。不得复制不明回执，不得复用标签覆盖旧成功回执。

`attempt-*` 目录保存长期只读挂载快照，运行中的 DB/API 仍依赖这些路径；**不得删除回执或静态快照
来解锁重试**。同样保留失败尝试供排查。缺回执说明没有可核验的准备证据，先按精确项目/容器/卷
回读现场，再有据安排新的受控 prepare；脚本不会自动重建、迁移、清理或停止旧栈。
源文件、配置、标签或 DB 身份发生变化时，旧回执不能继续用；保留其历史并安排新的审核候选标签。
固定支持的构建范围及失败语义见 [8.23 完整说明](PILOT_PREPARE_BINDING_20260922.md)。
