# 试点 MVP 低成本密钥托管审查

日期：2026-10-08，Asia/Shanghai。检查基线：`14d0731387f8a24e8b25ed9222f89fec248ea563`。本文为设计审查和实施清单，**未实现新 provider、未生成真实密钥、未配置服务器、未迁移数据、未放行上线**。试点 MVP，不等同完整 V1。

结论：正式 V1 没有指定必须购买付费阿里云 KMS；但当前代码确实只支持阿里云 KMS。小规模试点可以评估在现有服务器运行独立 OpenBao Transit 密钥托管进程，保留密文注册表、用途隔离、不可变 pin、历史版本和真实解密门禁。它需要一项有边界的 provider 适配和前向迁移，不能仅改环境变量。优先评估已有成熟组件，不为赶上线自行实现密码服务。

## 1. 基线要求与实现选择

已完整阅读正式 V1，并复核当前交接、云配置准备单和 `ALIYUN_KMS_ENVELOPE_KEY_RUNBOOK.md`。

| 来源 | 原文或当前事实 | 对低成本方案的约束 |
| --- | --- | --- |
| V1 §4.2 | “WAF、HTTPS、KMS/密钥托管、安全组和最小权限数据库账号。” | 明确要求密钥托管，但未限定为付费阿里云 KMS 软件实例；不能取消加密和最小权限 |
| V1 §3.2 | 人员字段包含 `mobile_encrypted`；验证码“不保存明文验证码” | 本文所审 registry 不是所有个人信息保护的总开关；其他敏感字段仍要逐项验收 |
| V1 §4.2 | “OSS 私有 Bucket，使用短时签名 URL，不公开业务附件。” | 自托管密钥不替代 OSS 私有访问控制、签名有效期或云运行身份 |
| V1 §5 正式上线门槛 | “RPO 目标不高于 5 分钟，RTO 目标不高于 2 小时。” | 同机服务必须通过离机备份、实际恢复和解封时限演练；不能自行改为第二天人工恢复 |
| V1 §6 | “生产数据写入、迁移和上线必须经过授权、演练和书面验收。” | 方案文件不构成上线证据 |
| 当前 KMS runbook | ciphertext-only registry、两用途两 Key、默认凭据链、Aliyun KeyVersionId 核对、双人 pin 复核 | Aliyun 产品细节属于当前受审实现；替代时需明确新 provider 契约及等价证据，不冒充仍在调用 Aliyun |

同机托管保留了“密钥与源码/镜像/数据库备份隔离”目标，但相对外部托管 KMS 改变了主机失陷边界和运维职责。需把这一差异及解封负责人写入部署决策。本文没有把“使用人数少”解释成接受 root 失陷、关闭恢复门禁或取消双人复核。

## 2. 现在两组密钥到底保护什么

| 用途 | 当前加密内容与生命周期 | 代码证据 |
| --- | --- | --- |
| `authentication_idempotency` | 正式登录、刷新、退出的幂等响应；成功会话响应含 access/refresh token，以 AES-256-GCM 保存，供短暂网络未知结果后的准确重放。重放有效期配置范围为 30–120 秒，过期并不授权改写历史 pin | `formal_services/authentication_idempotency.py`、`authentication_response_replay.py` |
| `material_request_contact` | 需求联系人姓名与手机号的规范 JSON；绑定需求 ID 和申请人员 ID 的 AAD；手机号和联系人摘要另用域隔离 HMAC。当前申请及不可变 revision 都保留精确密钥坐标，历史解密需求长期存在 | `formal_services/material_request_contact.py`、`production_adapters.py` |

注册表中保存的是上述 AES 数据密钥经 KMS 包装后的密文；KMS 解包后的 32 字节仅供请求内 cipher 使用。`kms_data_key_pins` 保存坐标和包装密文 SHA-256，API/备份身份只有读取权，迁移身份才能受控新增，禁止覆盖、更新、删除和截断。

两组 registry 不负责 PNVS 云签名、OSS 云凭证、全部 HMAC secret、JWT 签名材料或 OSS 对象服务端加密。免费云产品默认加密密钥不是此应用可直接调用的解密服务。换掉应用 KMS 不会自动解决 PNVS/OSS 运行身份。

## 3. 已确认的硬绑定

| 位置 | 硬绑定 | 最小适配要求 |
| --- | --- | --- |
| `backend/app/config.py` | 两个 provider Literal 仅 `disabled/aliyun_kms`，生产检查要求 Aliyun，endpoint 必须 `.aliyuncs.com` | 新增显式、默认关闭的受控 provider 分支；保留 Aliyun 分支及原有失败关闭规则 |
| `backend/app/production_adapters.py` | Aliyun SDK/default chain；registry v1；包装密文格式、KeyVersionId 规则；SDK 返回 Key ID/KeyVersionId 与 registry 精确一致 | 抽离统一解析结果/坐标协议；新 provider 使用真实的版本证明，不把配置原样填回当供应商回执 |
| `formal_services/authentication_idempotency.py` | 生产 factory 只接受 `aliyun_kms`，拒绝静态内存/环境明文 key provider | 仅加入审查后的新实现类型；保留禁用任意测试 provider 和明文环境 key 的约束 |
| `formal_services/material_request_contact.py` | v1 envelope 的 `provider` 固定 `aliyun_kms` | 新写入使用明确的新 envelope 版本/provider；旧 v1 读取和验证不改变，不给新密文贴 Aliyun 标签 |
| 冻结迁移 `0029`、`0069` 及 `shipment_projection_0168/functions.json` | PG/SQLite 联系人 envelope 触发器检查 `provider = aliyun_kms`；当前函数目录带有该验证 | 新增前向迁移及新 head 目录证明；不得修改这些历史迁移/冻结正文/hash，不得删除其余联系人、状态或权限验证 |
| `foundation_models.KmsDataKeyPin` / `0040` | 旧账本无 provider 字段；KeyVersionId 长度、用途+应用版本唯一及不可变 ACL/触发器 | 显式设计 provider 坐标绑定的追加结构；不能复用已有用途+版本，也不能原地重绑旧 pin |
| `kms_pin_gate.py`、`kms_readiness.py`、启动 composition | plan/gate/历史引用验证目前均调用 Aliyun loader；readiness 实际解密并有单飞/预算 | gate 按真实 provider 分支验证；继续覆盖全部 active 和仍有引用的历史 key，不以仅配置存在取代解密 |
| `scripts/pilot_preflight.py`、Compose、部署脚本 | provider/endpoint/registry schema/mount 及 `migrate → pin-gate → api` 依赖固定 | 新方案有独立真实性检查；继续验证同一只读 registry、精确计划及数据库角色，不能删除原检查后全局放行 |

这不是“一处开关”的改动。首次无业务数据也必须经过合法新迁移，不能绕过当前 PG 触发器。

## 4. 备选方案比较

| 方案 | 新增付费产品 | 安全和恢复特性 | 判断 |
| --- | --- | --- | --- |
| 明文 root-only key 文件 | 无 | 只有文件权限，不提供文件本身加密；把文件挂到 API 后也不能称主密钥独立托管 | 不作为当前生产替代 |
| systemd 加密凭据 + 受控本机 keyring | 无新增密钥产品；仍消耗原服务器资源 | 成熟系统组件可保护静态凭据并按服务传递；要另做版本、context、pin、轮换、访问审计与恢复协议。若使用同盘 host key，整盘/宿主 root 同时获得密文和解密能力 | 可作简单 secret 投递基础设施；不是现有 registry 的零改动替换 |
| 自写独立进程/Unix socket key service | 无新增云产品 | 可做进程/UID/socket 隔离，但密钥协议、权限、审计、内存、轮换、备份和补丁责任都归项目 | 本里程碑不自行实现 |
| **现服务器独立 OpenBao Transit** | 不新购付费 KMS；开源软件不产生托管实例订阅，运维/存储/备份仍有成本 | 成熟 Transit 提供密钥版本、包装数据密钥、ACL、轮换；Shamir 解封允许把恢复材料保管在服务器之外；仍不防运行中的宿主 root | **优先验证的低成本候选**，待容量、解封和恢复演练通过后才可采用 |
| 自托管 Vault Transit | 不等于购买托管 Vault；具体发行版和许可需另审 | Transit 模型相似，但需要独立部署、补丁、认证和恢复运维 | 仅已有受管 Vault 时优先复用；本项目未发现既有实例，不额外引入两套产品 |
| 付费阿里云 KMS | 有独立产品费用 | 保留当前 SDK/registry 接口，外部托管边界更清晰；仍要解决轻量主机运行身份与网络接入 | 用户现阶段选择先不购买，保留适配器供以后使用 |

systemd 的服务凭据可由磁盘/Unix socket 等来源获取，并可使用 TPM2、宿主密钥或两者进行加密；解密后以受限服务文件投递。这里只把它用作 secret 投递设计依据，不能假设目标轻量服务器已有 TPM2。[systemd 官方凭据说明](https://github.com/systemd/systemd/blob/main/docs/CREDENTIALS.md)

OpenBao 为开源项目；Transit 文档描述了数据密钥、版本和按 key 路径限制应用权限的能力。Vault 也有相同类别的 Transit 服务，但不代表当前已经部署任一产品。[OpenBao 项目](https://openbao.org/)、[OpenBao Transit](https://openbao.org/docs/secrets/transit/)、[Vault Transit](https://developer.hashicorp.com/vault/docs/secrets/transit)

## 5. 建议验证的同机 OpenBao 部署形态

以下为拟实施方案，不是已部署事实。

1. 在现轻量服务器使用独立 OS 身份和独立数据目录运行固定版本/摘要的 OpenBao；先核对实际 CPU、内存、磁盘余量及 systemd 能力，限制资源，不触碰现有六个业务容器。禁止 dev 模式、root token 作为 API 身份、公开管理端口及 Docker socket 挂载。
2. 使用非公开 Unix socket，目录仅服务身份和授权 API 访问组可达；API 容器仅获得该 socket 目录和自己的受控凭据，不获得 OpenBao 存储、解封份额或宿主密钥目录。UDS 权限与 OpenBao token/ACL 两层都需校验，不把“同机可连接”当鉴权。
3. 使用两个独立 Transit key：认证响应、需求联系人。禁用 key 导出、明文 key backup、删除和收敛加密；业务 API 只获得精确两个 `transit/decrypt/<name>` 路径的 `update` 权限，不给 key 创建、配置、rotate、rewrap、datakey 或列表管理权限。生成与轮换由独立运维身份执行。
4. 为每个应用版本受控生成 `datakey/wrapped`，只接收包装密文。使用规范化 context/AAD 绑定应用、环境、provider 实例身份、用途、key 路径、应用版本；32 字节、随机 nonce、AEAD 和用途隔离要求不变。具体请求字段按固定版本的真实 API 验证；不自行传确定性 nonce。
5. 拟用 AppRole/Agent 或等价受控方式给 API 提供短期最小权限 token，凭据只写到受限内存目录；设置过期、撤销和续期失败关闭。RoleID 不是 secret，但 SecretID/token 是 secret；不能放进 `.env`、镜像或聊天。启动凭据的生成、交接和重新认证要形成实际步骤，不能只画一个 Agent 就宣称接入完成。
6. 首次初始化和每次重启后的解封由两名指定保管人完成，份额离机保存、互相独立；不在服务器保存满足阈值的份额，不写自动输入份额脚本。若暂时只有一名保管人，该“双人”条件仍未满足，不用两个文件/两个角色冒充两人。
7. 重启后在未解封、token 未续期、恢复材料缺失或实际解密失败时，ready 必须拒绝，登录/联系人写入在副作用之前拒绝。`live` 仍只证明进程存活。恢复后按 pin 与历史引用重新解密核验，再恢复流量。

UDS 由官方 listener 支持，权限应在实际使用版本验证；本方案不开放另一个公网入口。[OpenBao Unix listener](https://openbao.org/docs/configuration/listener/unix/) AppRole 可控制机器认证，但并不免除 SecretID/token 的受控投递和轮换工作。[OpenBao AppRole](https://openbao.org/docs/auth/approle/)

默认 Shamir 解封在服务重启后需要重新完成；离机份额与及时可达的保管人直接影响恢复时间。外部 KMS auto-unseal 会重新引入外部依赖，不属于本次默认低成本方案。[OpenBao Seal/Unseal](https://openbao.org/docs/concepts/seal/)

OpenBao 的官方安全模型不承诺防御已控制宿主 OS/进程的攻击者；独立 UID/容器仍需宿主补丁和最小权限。单机方案不能声称有外部 KMS 的隔离或高可用能力。[OpenBao 安全模型](https://openbao.org/docs/internals/security/)

## 6. 版本证明与迁移不能伪造

OpenBao 包装密文使用包含版本的 `vault:vN:...` 格式；解密 API 示例仅返回 plaintext，**没有 Aliyun Key ID/KeyVersionId 回执**。因此新适配器不得构造假的 `KmsDecryptResult`，也不能把配置中的 key/version 回填并通过既有 Aliyun 相等检查。应使用明确的 OpenBao 证明契约：受控端点/实例与路径、不可变包装密文 pin、其真实版本前缀、context/AAD 验证及实际成功解密。任何语义差异写入测试和审计；无法建立证明就不放行。[OpenBao Transit API](https://openbao.org/docs/api/secret/transit/)

最小设计可以保留现有 `kms_data_key_pins` 历史行，并通过新的追加式 provider 绑定记录为新版本声明 provider、实例、key 路径和真实版本格式；也可采用经过评审的等价前向 schema。最终 schema 必须在实现前固定，不能把新 provider 偷塞进旧 Aliyun 字段后隐瞒语义变化。新旧坐标唯一性、两用途永久隔离、API 只读和 migrator 受控 INSERT 不变。

迁移分两种情形：

- **新试点库**：先用只读证据证明既无旧 Aliyun pins，也无认证/联系人引用；执行受审新 head 后，仅为新 provider 生成和登记新版本。空库不是跳过 pin 或双人复核的理由。
- **存在旧数据**：Aliyun 包装密文不能直接交给 OpenBao 解密。保留旧 provider、registry 和访问能力完成历史读取；认证 provider/key 切换先停止相关写入并精确证明有效重放窗口引用归零。联系人当前记录与不可变 revisions 如需转移，必须另做受审重加密迁移、逐条解密/等值核对和恢复演练，不能原地覆盖 revision 或旧 pins。无法访问旧密钥时登记不可恢复阻塞，不能重新生成同名 key 冒充。

日常轮换为“生成新包装数据密钥 → 新应用版本 → 新 pin/provider 绑定 → 双人核对 → active 切换”；不得使用 rewrap 的新密文覆盖旧坐标。旧联系人引用所需 key 版本持续保留。认证已持久化的应用版本缺少 provider/key 信息，故跨 provider 切换不能依靠推测等待时间。

## 7. 最小代码、迁移和验证批次

| 批次 | 交付 | 通过证据 |
| --- | --- | --- |
| A：契约与离线配置 | provider 协议、registry/envelope 新版本定义、独立 sample、最小 ACL、systemd/socket/Agent 配置模板；真实 secret 一律不入库 | 占位/未知 provider/明文 key 输入被拒；只读路径/权限检查；不会启服务或生成真实 key 的 dry-run |
| B：适配 | 新 OpenBao loader，与 Aliyun loader 并存；auth/contact factories、历史 dispatch、pin plan/gate/readiness 分支更新；新增 provider 命名而非假 Aliyun 回执 | 使用实际本机隔离 OpenBao 实例验证 32 字节数据密钥、用途/context/版本匹配、限时解密、sealed/revoked/timeout 失败；不发短信、不用生产数据 |
| C：前向迁移 | 新 provider/envelope 的 DB 证明、不可变 provider 绑定、当前 runtime catalog/hash、迁移角色 ACL；保留所有历史文件及普通申请守卫 | PG16 新建库与旧 head 升级，旧 v1 正常读、新格式允许、未知格式拒绝、原 pin 不可变、越权与降级阻塞；只能跑新增和受影响门禁 |
| D：部署与恢复 | provider-aware preflight、migrate/plan/gate/api 依赖、受控 secret/socket 挂载、备份清单扩展、恢复 runbook | 真实解封与续期、旧/新应用版本可解密、API 无管理权限、离机恢复、恢复后权限及 pin 回读、ready 证明 |
| E：正式放行 | 同 SHA 代码门禁、人员和 SMS-only 登录、角色 MVP UAT、私有附件、HTTPS、备份回滚 | 原有正式门禁保持；局部新适配测试通过不表示已上线 |

测试至少覆盖：错误用途、环境、key 路径、版本前缀、AAD、密文/pin/hash 漂移、短密钥、重复应用版本、历史 key 缺失、符号链接/权限松散文件、服务封印、token 撤销/过期、并发探针只执行一轮、超时返回脱敏错误且没有业务副作用。日志不能输出 SecretID/token、数据密钥、联系人或完整 provider 响应。

修改部署代码后的镜像必须重新按实际输入证明绑定；现 `14d0731` CI 与既有候选镜像证据保留其原范围，不能冒充验证了此处尚未实现的 provider。不要为了本文重跑已終态测试或取消现有 CI。

## 8. 可恢复备份的交付物

只备份 PostgreSQL 和 registry 无法恢复 OpenBao 密钥。需要同时保存并互相绑定：数据库备份/WAL、全部有效及历史包装密文 registry、不可变 pin/provider binding 计划摘要、OpenBao 加密存储的一致性快照、部署版本/配置摘要、离机解封材料，以及独立受控的其他应用 secrets 恢复材料。

不能用“把全机目录压缩到同一服务器”代替备份。密钥服务快照和数据库备份离机保存；解封份额独立保管，业务 API 身份不能读取快照或全部恢复材料。选定固定版本和存储后，使用其受支持的一致性 snapshot/restore 操作，不能直接复制运行中存储文件并称已验证。

恢复演练必须在隔离目录/实例中恢复到指定版本：重新解封、建立最小身份、核对 registry/pin、解开两个用途的新旧测试 envelope、验证恢复后的业务 DB 权限和引用，再记录实测 RPO/RTO。密钥新增/轮换时应先有可恢复离机快照再启用该应用版本；不能等夜间备份后才发现当天的新 key 丢失。

## 9. 尚待决定和已有授权内可继续的工作

待决定：接受同机托管而非独立云 KMS 的安全边界；指定真实的两名解封/复核人员和离机保管方式；确定固定 OpenBao 版本、容量上限、升级窗口、恢复时间承诺与备份位置。若要求在任何时间无人值守重启后立即恢复，需要另审受控自动解封机制，不能把全部份额放回本机。

已有开发授权范围内可先完成 A 批以及隔离测试用的 B 批原型，随后评审 C 批；均不要求购买 KMS、迁移服务器或读取旧密钥。真正启用前必须完成 D/E，不能靠移除 `authentication_kms_coordinates` 或 `kms_registry_structure_and_active_keys` 检查把缺项改为通过。

本审查仅覆盖密钥托管适配；轻量服务器 PNVS/OSS 运行身份及私有 OSS 用量费用分别由总部署方案核算，本文不宣称它们已解决。

## 10. 同一 OpenBao 能否复用为阿里云机器 OIDC issuer

**可以作为优先验证的组件复用方案，无需再自制 JWT 签发服务；目前不是可直接采用的已验证身份。** OpenBao 的 Identity Token 功能可以给已认证的实体签发 OIDC 格式 ID token，并发布 discovery/JWKS；阿里云 RAM 可信任外部 issuer 并用其 token 换取 STS。二者文档足以支持兼容性验证计划，但本轮没有 OpenBao→阿里云实际交换结果，机器身份引导、公开公钥路径和持续 token 投递也尚未实现。[OpenBao Identity Token](https://openbao.org/docs/secrets/identity/identity-token/)、[阿里云 OIDC 角色 SSO](https://help.aliyun.com/zh/ram/overview-of-oidc-based-sso)

### 正确的功能与端点

| 功能 | 本方案是否使用 | 官方端点/边界 |
| --- | --- | --- |
| 机器取得 OpenBao 自己签发的 ID token | 是，拟复用 | 已认证本机投递身份调用 `GET /v1/identity/oidc/token/<固定 role>` |
| 发布验签发现文档与公钥 | 是，拟复用 | `GET /v1/identity/oidc/.well-known/openid-configuration` 与 `GET /v1/identity/oidc/.well-known/keys`；不需要发布签发或管理端点 |
| 使用外部 IdP 登录 OpenBao | 不是本方案的签发机制 | `auth/oidc` / `auth/jwt` 是验证外部凭据的认证方法，不会因为配置了它就成为阿里云可信 issuer |
| 完整的浏览器授权码 OIDC provider | 本小试点不需要 | 不把 `identity/oidc/provider/...` 的浏览器授权流程与上述机器签发接口混用 |

具体签发/discovery/JWKS 接口见 [Identity Token API](https://openbao.org/docs/api/secret/identity/tokens/)；外部登录方向见 [OpenBao JWT/OIDC auth](https://openbao.org/docs/auth/jwt/)。Identity Token discovery 示例中的浏览器授权/token endpoint 为空，因此不能根据浏览器 OIDC 客户端是否成功登录来判断机器签发兼容；真正判据是阿里云 `AssumeRoleWithOIDC` 的完整验证结果。

### 最小复用设计（未配置）

1. **issuer 与公钥路径**：为同一 OpenBao 配置明确 HTTPS issuer，其路径指向该实例的 `/v1/identity/oidc`；issuer、discovery 返回值和 token `iss` 必须逐字一致。拟复用现有 HTTPS 入口，仅以精确 GET allowlist 代理上表两个公开元数据路径到本地 socket；禁止将整个 `/v1`、`/identity` 或签发/管理 API 代理到公网。这不需要新造签发服务或新增公网端口，但仍需单独审查现有入口配置，当前没有改动。
2. **独立签名 key**：使用 OpenBao Identity 的专用签名 key，与两把 Transit 数据密钥分离。选择双方支持的算法；`RS256` 为候选，待真实 STS 交换核验。`allowed_client_ids` 使用已审两个用途的精确 Client ID，不用 `*`。轮换保留旧公钥至少覆盖所有未过期 token、时钟容差和验签缓存窗口。
3. **固定 audience/subject**：Identity role 的 `client_id` 决定 `aud`；`sub` 是请求者的真实 OpenBao entity ID，不能在模板中伪造或覆盖。为 PNVS、OSS 分别固定 role/Client ID，必要时分离实体及投递身份；将实际 entity ID 登记为 RAM 精确 subject。重建 entity 不是无感轮换，必须走信任变更。该设计利用既有身份规则，不从 hostname 或任意请求参数造 `sub`。[Identity Token claims](https://openbao.org/docs/secrets/identity/identity-token/)
4. **RAM 信任与权限**：受信 principal 只指向该账号的精确 OIDC provider；`oidc:iss`、`oidc:aud` 和 `oidc:sub` 使用精确 `StringEquals`，不省略 subject、不用全实体通配。身份信任不等于云权限；PNVS/OSS 各自角色只附所需动作/资源。阿里云文档允许 subject 条件，但本试点选择将其作为必需边界。[RAM OIDC 信任条件](https://help.aliyun.com/zh/ram/implement-oidc-based-sso-from-okta)
5. **TLS 与可达性**：阿里云必须能读取实际 discovery/JWKS；不能把 `localhost`、Unix socket 或仅私网可达 URL 填成公网 issuer。域名、TLS 证书链、RAM CA 指纹及轮换要实核，JWKS 公钥轮换与 TLS CA 轮换是两件事。证书换链先登记新指纹并验证过渡期，不跳过 TLS 验证。[阿里云 OIDC provider 配置](https://help.aliyun.com/zh/ram/user-guide/manage-an-oidc-idp)、[官方指纹核验方法](https://help.aliyun.com/zh/ram/user-guide/obtain-oidc-idp-fingerprints-through-openssl)
6. **短期 token 投递**：由已经认证的本机最小权限投递身份，仅对精确 `identity/oidc/token/<role>` 有读取能力；它与本目录现有两路径 decrypt ACL 分开，不给应用签名 key 管理权限。拟将 ID token TTL 定为数分钟并在到期前更新，实际值需与 RAM 最早签发限制和 SDK 刷新节奏联验。token 仅通过受限内存目录中原子替换文件投递给相应 SDK；不进入 `.env`、Git、stdout、日志或镜像。需证明确实重新读取新文件并刷新 STS，不能拿一次签发或静态文件宣称自动续期。
7. **不要掩盖引导根**：Identity 签发接口要求一个已认证且绑定 entity 的 OpenBao 客户端。候选是同机 AppRole/Agent，经独立管理员交付受限 SecretID，映射固定 entity；续期/重新认证和到期撤销需实证。首次角色/实体初始化、SecretID 交接以及人工 Shamir 解封依然由人负责。不得把所有恢复份额或 root token 放回本机，也不能先依赖阿里云 STS 登录 OpenBao、再依赖 OpenBao token 换同一 STS 形成启动循环。[OpenBao AppRole](https://openbao.org/docs/auth/approle/)

以上投递步骤是部署设计，并非当前代码/SDK 已实现事实。ID token 与交换得到的 STS 凭据生命周期不同；停止签发不必然立即撤销已发 STS。真实验收必须覆盖错误 issuer/audience/subject、过期 token、未知/轮换 `kid`、公钥不可达、停止投递、OpenBao sealed、STS 过期、错误 role 和最小权限拒绝；回执仅保存非敏感配置、状态和相关时间，绝不保存 token 或 AccessKey 内容。[AssumeRoleWithOIDC](https://help.aliyun.com/zh/ram/developer-reference/api-sts-2015-04-01-assumerolewithoidc)

推荐执行顺序为：先确认 OpenBao 作为密钥托管候选的固定版本和引导/恢复方案 → 为同一组件准备 Identity role/issuer 配置 → 用隔离身份验证 discovery/JWKS 与实际 STS 交换 → 验证持续投递和 PNVS/OSS 各自 SDK 刷新。若机器认证引导或公钥发布不能满足约束，继续记录该具体缺口；不另造签发服务器、不把配置填好当作接入成功。此次仅补文档，没有扩大模板权限、打开监听或执行任何 token 签发。

### 离线模板验证留存

之前已完成且未重跑的命令（工作目录 `cloud_oam/`）：

```bash
.venv/bin/python deployment/openbao-pilot/verify_templates.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s deployment/openbao-pilot -p test_template_contract.py -v
```

首条 exit 0，模板报告保持 `deploymentReady=false`、`effectivePolicyVerified=false`、`actualListenersVerified=false`。第二条 exit 0，`Ran 7 tests in 0.005s` / `OK`。结果存在当时工具输出中，**没有另存日志文件，因此没有可提供的日志路径**。这些结果只覆盖原离线模板，不证明本节 OIDC 集成已实现或验收。
