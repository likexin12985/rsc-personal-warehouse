# 轻量服务器低成本运行身份方案（研究与待实施设计）

2026-10-08，Asia/Shanghai。源码核对基准：`14d0731387f8a24e8b25ed9222f89fec248ea563`。**试点 MVP，不等同完整 V1；本文件不构成身份、云权限、短信、部署或上线通过回执。**

已完整阅读正式 V1 基线，并核对当前交接、`CLOUD_CONFIGURATION_PLAN_20261008.md`、PNVS/KMS/OSS 实现和锁定版 Credentials SDK 源码。本次仅研究与落本文档，没有读取凭据、连接目标机、申请云角色、创建身份提供商、发短信、修改网络或购买资源。

## 结论与选择

组件收敛补充：若采用同机 OpenBao 托管数据密钥，优先验证其现成 Identity Token 功能同时提供机器 OIDC issuer，避免另造签发服务。正确接口、固定 aud/sub、受限公钥发布、SecretID/entity 自举和实际 STS 验收见 [密钥方案第 10 节](LOW_COST_KEY_CUSTODY_REVIEW_20261008.md#10-同一-openbao-能否复用为阿里云机器-oidc-issuer)。这仍是待实施兼容性方案，不代表已建立身份。

目标继续使用已确认的杭州轻量服务器 `118.31.37.87`。**可以实施不依赖 ECS 实例角色的 OIDC 联邦身份；当前尚未配置、尚未实测。** 优先使用已有且适合机器身份的受控 OIDC 提供商；现有材料没有证明用户已有该服务。若没有，低现金成本候选是同机隔离的工作负载令牌签发/投影组件、公开的 HTTPS 发行者元数据与公钥，以及 RAM 中严格绑定的 OIDC 身份提供商和角色。

这条路径不要求新增 ECS、付费 KMS、ACK 或 IDaaS 实例，但需要真实的签名信任根、身份配置、轮换、恢复及运行维护。它是待实施设计，不是轻量服务器已经内置 OIDC 或默认链自动可用的事实。阿里云正式文档明确支持企业自建或第三方 OIDC IdP；用令牌换取 STS 不需要阿里云长期 AccessKey。由此推断它可以用于普通轻量主机上的程序，最终仍须目标机真实兑换、角色回读及续期验收。[OIDC 角色 SSO](https://help.aliyun.com/zh/ram/overview-of-oidc-based-sso)、[非阿里云应用身份场景](https://help.aliyun.com/zh/ram/product-overview/ram-use-cases)、[AssumeRoleWithOIDC](https://help.aliyun.com/zh/ram/developer-reference/api-sts-2015-04-01-assumerolewithoidc)

## 已知事实与不能代替的证据

| 对象 | 本次核实 | 结论边界 |
| --- | --- | --- |
| 目标轻量实例 | 交接记录了 IMDS 加固探测 404、旧 API 未见相关默认链配置 | 本轮没有重做探测；404 不证明所有轻量产品永远不支持，也不能作为角色可用证据 |
| `AliyunServiceRoleForSwas` | 官方说明其用于轻量服务访问 VPC 等资源 | 是云服务的角色，不是进程通过元数据领取应用凭据的角色 |
| 轻量 RAM 用户/角色文档 | 描述谁可以管理轻量云资源 | 不证明主机里的应用自动获得身份；未找到本产品受支持的应用实例角色绑定操作 |
| OIDC→STS | 官方支持外部 IdP，锁定 Python SDK 有 OIDC provider | 仍缺 IdP、私钥保护、RAM 信任、持续投影、角色权限与目标机运行证明 |
| `credentials_uri` | 官方 SDK 可以 GET 一个 URI 获得带到期时间的 STS | URI 是传递机制，不能创造可信身份，也不负责保护或续期上游的信任根 |
| 当前应用 | PNVS/KMS 使用默认链，OSS 使用环境凭据提供器 | 三者并未自动形成同一套可续期运行身份 |

服务关联角色和轻量访问控制依据：[轻量服务关联角色](https://help.aliyun.com/zh/simple-application-server/service-association-role)、[轻量身份管理](https://www.alibabacloud.com/help/zh/simple-application-server/identity-management)。未获得官方产品能力或目标实例绑定证据前，不能拿 ECS 指南代替轻量验收。

## 安全目标与当前产品选择

正式 V1 §4.2 原文是“WAF、HTTPS、KMS/密钥托管、安全组和最小权限数据库账号。”；§1.4 要求正式环境无密码登录；§6 要求生产写入、迁移和上线经过授权、演练及书面验收。基线没有指定必须用 ECS 实例 RAM 角色，也没有要求应用运行身份必须购买某项专属实例。

本设计保留源码/镜像中无秘密、运行角色最小权限、临时云凭据、鉴权审计、到期失败关闭和可恢复密钥管理目标。`default_chain`、固定动态 provider 允许列表及当前 KMS 产品绑定是已有实现选择；变化应使用明确适配并保留安全验证，不能把静态 AK 包在 URI 里便宣称已经消除了长期信任根。本文件不决定应用数据加密方案，也不把 OIDC 签名密钥当作数据加密密钥。

## 候选对比

| 候选 | 信任根与刷新责任 | 适用性 / 当前缺口 | 决策 |
| --- | --- | --- | --- |
| 轻量主机直接取 ECS RAM 角色 | 阿里云实例绑定和元数据服务 | 当前没有产品及实例证据 | 不采用假定路径，不继续猜元数据端点 |
| 已有受控机器 OIDC | IdP 的签名私钥、主机向 IdP 的认证；IdP 持续签发、投影器更新 token，SDK 换 STS | 阿里云官方通用支持；没有已存在 IdP 证据 | 若确有现成能力，优先复用 |
| 同机隔离 OIDC 工作负载身份 | 独立受控签名私钥 + 公钥/TLS 发行者 + 精确 RAM 信任；本机投影器自动更新短 token | 不需要阿里云长期 AK，不新增付费实例；需要建设、复核和恢复演练 | 推荐低现金成本实施候选；不得在设计阶段标通过 |
| 本机 STS 经纪 + `credential_uri` | 经纪必须有上游实例角色、OIDC 或受限 RAM 用户 AK；经纪刷新 STS，SDK 刷新消费 | 若上游是长期 AK，长期秘密仍然存在；裸 URI 无进程身份保护 | 有成熟经纪时可用；当前不能凭空设置 URI |
| RAM 用户 AK 直接传应用 / 手贴 STS | 长期 AK 或人工刷新 | 与现有生产动态身份约束冲突；手贴 STS 会到期 | 不作为本轮快捷放行方案 |
| GitHub Actions OIDC 充当常驻身份 | 依赖每次 CI job 的令牌 | 短生命周期 CI 身份不是常驻服务的发行者 | 不做定时 Actions 向生产灌凭据 |

RAM 本身官方标为免费产品；这不等于 PNVS、OSS、额外 IdP 产品、日志或网络免费。同机候选没有新增云实例购买项，计算占用来自现有主机，容量和运维成本仍须评估。[RAM 计费](https://help.aliyun.com/zh/ram/product-overview/billing-methods/)

## 同机 OIDC 候选的可执行构成

以下为拟定配置契约，尚未创建资源。每个坐标确定后再生成最终配置；占位值必须被预检拒绝。

1. **固定身份，不提供任意签名接口。** 独立 OS 服务身份运行签发/投影组件，只允许预先审查的 `iss`、`aud`、`sub` 和最大有效期；应用不能提交任意 claims。拟定 API、附件和备份各自的主体，不让业务容器取得备份/恢复主体。
2. **签名私钥隔离。** 新生成专用签名密钥；不复用 SSH/TLS/KMS/聊天暴露秘密。只由签发服务读取，权限与应用 UID、Web UID、数据库身份分离；不挂到 API、不放源码、镜像、日志或普通 DB 备份。私钥轮换与离线加密备份另行受控；主机 root 被攻陷仍可能破坏同机信任根，不能宣传为硬件隔离或托管 HSM 等级。
3. **公开内容仅限元数据与公钥。** 选择已控制且 HTTPS 可达的 issuer URL，发布符合受选成熟 OIDC 实现的 discovery/JWKS。签发私钥、token 输出和内部管理接口均不公开。发行者域名、证书链指纹、issuer/JWKS 一致性必须现场核验；不在公开查询首页增加登录页面。
4. **只读 token 目录投影。** 投影器把短寿命 OIDC token 原子替换到仓库外的 tmpfs 目录，拟定 token 10 分钟、每 5 分钟更新，时钟偏差预算不超过 60 秒。应用仅挂该用途目录为只读；不要单文件 bind mount，避免原子替换后容器仍读旧 inode。备份身份使用另一个隔离目录，业务容器不可读。
5. **RAM 身份提供商及角色。** 管理员配置 issuer、真实 TLS CA 验证指纹、精确 audience。角色信任同时精确绑定提供商 ARN、issuer、audience、subject；应用无创建/修改 RAM 角色或策略权限。STS 会话拟定 3600 秒，未审查前不延长。
6. **SDK 配置。** API 传入非秘密坐标 `ALIBABA_CLOUD_ROLE_ARN`、`ALIBABA_CLOUD_OIDC_PROVIDER_ARN`、`ALIBABA_CLOUD_OIDC_TOKEN_FILE`；使用专用 token 目录。关闭无证据的 ECS 元数据兜底；不得挂载宿主机 `~/.aliyun`、静态 AK 或人工 STS。
7. **运行验收。** 先做只读 `GetCallerIdentity` 核对预期账号和角色，再验证真实自动轮换和有效期。只有经过授权的真实短信及私有附件业务试验能验证对应业务权限；“动态 provider 已解析”不是 UAT。

OIDC 提供商配置和证书轮换条件依据：[管理 OIDC 身份提供商](https://help.aliyun.com/zh/ram/manage-an-oidc-idp)。上面的本机 UID、投影和有效期参数是本项目设计建议，不是官方产品已经替我们配置的事实。应采用维护中的标准组件/密码库，不把自制无鉴权 token 服务器投入生产。

### RAM 角色信任模板（不可直接部署）

此模板所有占位符需要替换为核实值。信任策略 Action 按阿里云文档为 `sts:AssumeRole`；调用接口仍为 `AssumeRoleWithOIDC`。不能把 API 名误填为信任 Action。[OIDC 角色信任规则](https://help.aliyun.com/zh/ram/user-guide/create-a-ram-role-for-a-trusted-idp)

```json
{
  "Version": "1",
  "Statement": [{
    "Effect": "Allow",
    "Principal": {"Federated": "acs:ram::<ACCOUNT_ID>:oidc-provider/<PROVIDER_NAME>"},
    "Action": "sts:AssumeRole",
    "Condition": {"StringEquals": {
      "oidc:iss": "<REVIEWED_HTTPS_ISSUER>",
      "oidc:aud": "<REVIEWED_AUDIENCE>",
      "oidc:sub": "<EXACT_API_SUBJECT>"
    }}
  }]
}
```

PNVS 专用角色的业务权限模板：

```json
{
  "Version": "1",
  "Statement": [{
    "Effect": "Allow",
    "Action": ["dypns:SendSmsVerifyCode", "dypns:CheckSmsVerifyCode"],
    "Resource": "*"
  }]
}
```

这是服务级授权，官方未给这两个动作定义模板级资源或产品条件键；不能编造模板 ARN。签名、模板、方案、单号、限流、禁止未知结果重放仍由应用约束。该角色不附加短信管理、RAM 管理、备份删除或业务数据管理权限。[PNVS 权限表](https://help.aliyun.com/zh/pnvs/developer-reference/api-dypnsapi-2017-05-25-ram)

OSS 身份按正式 Bucket/对象前缀限制对象上传与读取，备份及恢复另设身份。**不能把服务器 IP 限制直接套在浏览器/小程序预签名直传身份上**，因为 OSS 看见的是终端请求来源；精确策略及操作映射由附件方案核验。若统一 API 进程既需 PNVS 又需 OSS，则必须明确批准该进程所持权限并集，或使用两个显式 provider；不能宣称两种 RAM 角色已自动隔离。

## 相对当前代码的最小变更清单

| 位置 | 当前状况 | 必要改动 / 验收 |
| --- | --- | --- |
| `docker-compose.yml` API / kms-pin-gate | 仅透传 URI、metadata-disabled；未投影 OIDC token | 增加经审查的 OIDC 三坐标和目录只读挂载；不同服务只接必要身份；静态变量继续不得启用 |
| `scripts/pnvs_runtime_preflight.py` | 允许动态 provider 名称，检查三段凭据非空；明确把权限/续期/发布保持 false | 加入选定身份模式与配置排他性检查、预期角色回读、真实续期证据；不得只把 `credentialRefreshVerified` 改 true |
| `backend/app/sms.py` | PNVS 默认链，禁发送 SDK 自动重试 | 保持；检查实际选中 provider 与审查来源相符，日志/异常持续脱敏 |
| `backend/app/production_adapters.py` | KMS 使用相同 SDK 默认链 | 数据密钥方案由单独 ADR 决定；若保留云 KMS，解密权限单独受控，不给签发器 |
| `backend/app/formal_services/file_storage.py` | OSS V2 固定 `EnvironmentVariableCredentialsProvider` | 使用受控可刷新 provider 接口，保留临时令牌和到期时间；签名有效期不得越过凭据安全剩余时间；验证真实续期及旧 URL 失效 |
| 新的发行者/投影运行单元 | 不存在 | 受控 UID、固定 claims、私钥权限、tmpfs 投影、原子更新、健康/时钟检查、签名轮换、恢复文档；不需引入通用用户登录平台 |
| 预检 / readiness / 演练 | 无此身份运行回执 | token 缺失/过期、错 issuer/audience/subject、RAM 拒绝、IdP 停止、过期无法续期均失败关闭；不启静态兜底 |

当前锁定 `alibabacloud-credentials==1.0.12`、`alibabacloud-oss-v2==1.3.2`。检查的是本地已安装第三方源码，没有读取用户凭据。具体发现：

- `provider/default.py` 顺序为环境凭据 → 已配置的 OIDC → CLI profile → 旧 profile → 未禁用的 ECS → 已配置的 URI；不能以设置一个 URI 保证其被选中。启动时必须核实来源并清除未审查的配置回退路径。
- `provider/oidc.py` 每次刷新 STS 会重新读取 token 文件。SDK 不负责给该文件写新的 OIDC token；发行者/投影器停了，长期运行就无法续期。
- `provider/uri.py` 读取 HTTP JSON 的 `Code`、`AccessKeyId`、`AccessKeySecret`、`SecurityToken`、`Expiration`，缓存刷新点是到期前 15 分钟。其当前实现没有可配置的每请求身份认证头，不能把裸 URI 当安全的多用户凭据服务，也不要把秘密放 URL/query。
- `provider/default.py` 返回包装后的 `Credentials` 时没有传递 `expiration`。当前 PNVS 预检也不检查到期时间，所以需要在实际采用的 provider 边界保留到期信息并明确验证。已有默认链检查并不是凭据寿命证明；这些源码发现不等于 SDK 永远会继续发过期请求。

官方凭据能力依据：[Python SDK 访问凭据](https://help.aliyun.com/zh/sdk/developer-reference/v2-manage-python-access-credentials)。

## 受控 STS 经纪的备选边界

若 OIDC 工程维护成本不可接受，可以另行审查“仅经纪保存、仅能 AssumeRole 到精确角色的专用 RAM 用户 AK”的方案；API 仍只接收短 STS。经纪 AK 必须是新生成、独立受控秘密，应用不可读；角色信任、源网络约束、自动续期、撤销和轮换必须现场验证。经纪不得具备 PNVS/OSS/KMS 直接业务权限、RAM 管理权限或任意角色扮演权限。

但它把信任根换成了经纪持有的长期 AK，**没有消除长期密钥**。这涉及当前“生产不得注入静态云凭据”约束的具体例外设计，须明确批准后才能实施；本轮不自动选择。控制台登录不能直接授权服务持续取凭据，已登录页面中的 Cookie/STS 也不能导出做信任根。`credentials_uri` 仍需私有隔离通道；不要新增公网凭据端口。

`AssumeRole` 需要已被授权的 RAM 用户/角色及相应信任，主账号 AK 不能用于该调用；限定 `sts:AssumeRole` 到精确目标角色，不使用通配角色。[STS 角色常见问题](https://help.aliyun.com/zh/ram/support/faq-about-ram-roles-and-sts-tokens)

## 本里程碑接续与验收

1. 先生成可审查的选定身份配置、角色信任/权限模板和本机部署单元；离线验证错误配置、令牌轮换、跨主体拒绝及日志脱敏。无需购买服务或调用真实短信。
2. 确定采用已有 OIDC 还是本机隔离签发；核实发行者控制权、签名根保护和恢复责任，再创建 RAM 信任并部署真实组件。本文不自行完成这些外部授权。
3. 记录期望账号/角色、实际 provider、只读身份回读、两次跨轮换窗口成功以及到期失败关闭；回执不保存 AK、OIDC token、STS 或原始 SDK 错误。撤销新令牌与现有 STS 的剩余有效窗口分别记录，不能声称删除 IdP 会使所有已签发 STS 立即作废。
4. 使用准确候选 SHA 的既有 CI 终态；只补上述新增身份/附件适配的聚焦验证，不重复整套终态测试。本研究未另查 CI，状态以主任务精确 run 回执为准。
5. 之后仍须真实短信及多角色/真机 UAT、私有附件访问、HTTPS/API readiness、备份恢复与回滚；身份配置完成不等于发布完成。
