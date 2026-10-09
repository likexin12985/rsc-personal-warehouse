# PNVS 独立 OIDC 部署接线（2026-10-09）

**试点 MVP，不等同完整 V1；仍为 `not_ready`、未部署。** 这是 Linux Agent 后续的部署配置接线，不新增登录方式，不触发短信、RAM/OSS 写入、目标机配置或真实 `prepare/start`。

## 范围与明确身份

`deployment/pnvs-oidc.compose.yml.example` 将公开 `RSC_PNVS_OIDC_ROLE_ARN/PROVIDER_ARN/TOKEN_FILE/SESSION_NAME` 一一映射到锁定 `alibabacloud-credentials==1.0.12` 支持的 `ALIBABA_CLOUD_ROLE_ARN/OIDC_PROVIDER_ARN/OIDC_TOKEN_FILE/ROLE_SESSION_NAME`。默认链只供 PNVS；两项应用加密用途必须都选 OpenBao，防止 Aliyun KMS adapter 意外使用 PNVS 角色。`kms-pin-gate` 及其他服务不得获得这套 SDK OIDC 声明或 PNVS token 挂载。

三个 overlay 要求 **Compose ≥2.24.4**，用 `!override` 替换 capabilities 和 `security_opt`；真实目标 `2.40.3` 的纯配置合并已证明 API 共享组正确并存，不能把曾出现的重复安全项解析结果当作通过。失败与修复回执均保留；没有启动服务或重复成功 parser。

OSS 使用现有显式 provider，PNVS 使用 SDK 默认链；角色、token 目录、OpenBao entity UUID（`sub`）和 `aud` 必须分别配置。两者共享公开 `RSC_OIDC_ISSUER_URL` 和 `RSC_OIDC_EXPECTED_ACCOUNT_ID`；每个 role/provider ARN 都须属于该账号。**允许同一受控 issuer 复用同一 RAM OIDC provider ARN**，不要求虚构两个同 issuer 的提供商。

所有 issuer/sub/aud 声明复用现有 `deployment/openbao-pilot/identity_contract.py` 的纯合同校验；不解码或读取真实 JWT，不联系 JWKS。真实投影器、RAM trust 和 STS 必须另外回读核验，配置格式正确不证明它们存在或相符。

需要从已审核部署获取的非秘密信息：

- 受控 issuer HTTPS 地址、RAM 账号 ID、provider ARN；真实 issuer/JWKS/证书链验证安排。
- PNVS 专用 role ARN、独立 subject UUID、audience、session name；OSS 对应的独立 role/sub/aud。
- 正式 API 数字 UID/GID、PNVS projector UID/shared GID、已经存在的 `/run/...` tmpfs 投影目录；不得猜 UID 或从任意现有 token owner 反推信任。
- STS region 与 PNVS 角色仅允许 `SendSmsVerifyCode`/`CheckSmsVerifyCode` 的已审核策略。签名“恒创联众”、模板 `100001`、方案“RSC个人仓登录”沿用现有试点合同。

这些均为公开坐标，不需要用户在聊天里提供 token、AK、STS、验证码或私钥。

## 默认链排他性

锁定 SDK 的真实源顺序为环境 → OIDC → CLI profile → 旧 profile → metadata → URI；OIDC 初次失败会继续尝试后续来源。本模板和预检共同要求：

| 来源 | 限制 |
| --- | --- |
| 静态凭据 | `OAM_SMS_*`、`ALIBABA_CLOUD_*` AK/STS、`OSS_*` AK/STS、`OAM_FILE_STORAGE_*` AK/STS 四组相关字段全部为空；出现即阻断 |
| CLI profile | 官方 `ALIBABA_CLOUD_CLI_PROFILE_DISABLED=true` |
| 旧 profile | `ALIBABA_CLOUD_CREDENTIALS_FILE=/dev/null`；锁定 SDK 实测读取空配置后不能解析身份。API 禁止挂载替换 `/dev`、`/dev/null`、根目录，禁止设备映射/提权 |
| metadata | `ALIBABA_CLOUD_ECS_METADATA_DISABLED=true`，不设置角色名提示 |
| URI | `ALIBABA_CLOUD_CREDENTIALS_URI` 为空 |
| STS | 显式 region、公共 endpoint 模式；未自动设置代理或创建云资源 |

`pilot_preflight` 在任何 PNVS/OIDC 字段出现时要求完整匹配，残缺配置失败关闭；旧未选择 OIDC 的通用配置仍按原配置合同检查，不被解释为已具备运行身份。`pnvs_runtime_preflight --resolve-default-chain` 的选定 OIDC 模式只接受精确 `provider_name=default/oidc_role_arn`，不接受 profile/OIDC、ECS 或 URI 作为成功替代。子进程沿用严格超时、SDK 日志隔离、固定状态输出，绝不输出原 SDK 诊断。

## 只读投影与仍未证明的事项

PNVS 目录适用 `PILOT_LIVE_IDENTITY_MOUNTS_20261009.md` 的全部 live bind 合同：精确 Settings/部署坐标、目录 `0750`、文件 `0440`、API 数字身份、独立受控 projector 所有权、共享只读组、真实 Linux tmpfs、来源目录 inode/mount 绑定、`create_host_path=false`、能力清空和禁止提权、实际容器只读回读。该动态 token 不进入静态复制/内容 hash。这个模板不提供发行者或自动投影进程。

真实 Compose 的 canonical JSON 会将明确 false 输出为 `bind:{}`；部署 helper 仅接受存在的 bind 对象中明确 false 或该空对象形式。缺 bind、null/数组/字符串、true，以及 short syntax 对应的自动建目录 true 均拒绝。此最小兼容处理在 `_readonly_source` 和 `live_bind_specs` 共用，不改 `decoded_config` 或严格 roundtrip，也不放宽真实目录/权限证据。

锁定 SDK 默认链/`Client` 会丢弃 credential expiration，因此本预检不能据 provider 名称或三个字段非空宣布续期/有效期验收。输出的 `expectedRoleVerified`、`issuerClaimsVerified`、`credentialRefreshVerified`、`projectionVerified`、`providerPermissionsVerified`、`smsSent`、`pnvsRoundTripVerified`、`releaseReady` 保持 false。尚需真实账号/角色回读、固定 claims/JWKS 校验、跨窗口续期、缺失/过期/错 issuer/aud/sub/停止投影失败关闭以及撤销窗口证据。

随后才进入已指定测试号码的受控真实短信登录、唯一人员映射和角色/真机主链 UAT；申请→审批→最小分配/占用→后台人工发运→本人收货→本人入账各状态独立核验。真实短信验证码仅在产品 UI 输入，不能进入日志或聊天；权限/本人操作、幂等、不可变流水和最小站内通知继续保留。

## 本地证据

新增 `backend/tests/test_pnvs_oidc_deployment.py` 与 `test_pilot_peer_runtime.py` 已落在既有 CI 的 `backend/tests` 收集范围，不新增 workflow。锁定 SDK 离线测试用合成令牌和 STS 响应验证实际 role/provider/token 路径、精确 provider 名称、OIDC 失败后 profile 空解析/CLI/metadata/URI 不可兜底；没有真实云请求。帮助冷启动不加载身份 SDK/contract、不读取运行文件。阶段与失败修复回执见 `artifacts/oss-pilot-preflight-20261009/runtime-boundary-04..06/`，具体结果见 live mount 交接；不将这批离线证据计作真实短信或发布完成。

后续 canonical 专项只跑新文件 `test_pilot_compose_canonical_mounts.py`，14 passed；回执为 `artifacts/oss-pilot-preflight-20261009/compose-canonical-08/receipt.json`。它承接真实 `compose-defaults-03/receipt.json` 的 false/true/omitted/short 与 roundtrip 矩阵，旧终态测试未重跑。
