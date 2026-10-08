# OpenBao 试点离线审查模板

**未启用、不可直接部署、不表示新 provider 已实现。** 这些文件不启动进程、不创建用户、不生成 key/token、不写数据库、不改防火墙。主方案见 `../../docs/LOW_COST_KEY_CUSTODY_REVIEW_20261008.md`。

## 产物与边界

| 文件 | 内容 |
| --- | --- |
| `listener.fragment.json.example` | 只有一个受限 Unix API socket 的配置片段；刻意不提供未验证的存储/集群组合 |
| `api-decrypt-policy.json.example` | 恰好两个用途 key 的 decrypt/update 权限，没有管理权限或通配 |
| `review-status.json` | 版本和发行包摘要尚未选择；运行验证和部署状态全部为 false |
| `verify_templates.py` | 只读检查精确 ACL 集合、socket 片段及非部署状态；不会调用 OpenBao |
| `test_template_contract.py` | 实际反例验证通配、额外能力、额外/TCP 监听、权限放宽及假就绪被拒 |

API ACL 是完整的**业务解密权限集合**，不是包含凭据续期的最终机器认证策略。实际 role/token 不得再合并管理员或其他宽权限 policy，也不能默认附带未经审查的 `default` policy。续期/重新认证由独立受控投递方案审查；此模板未授予管理、生成密钥、轮换、导出或续期能力。

配置和 policy 使用官方支持的 JSON 表达式；`.example` 文件避免被配置目录自动当成完整配置加载。不要将此整个目录传给 `bao server -config`：其中包括审查元数据，且缺少必需的 storage。最终采用单独、受控的完整配置，检查所有合并配置和环境覆盖，再通过实际端口/socket 回读确认。[配置格式与合并规则](https://openbao.org/docs/configuration/)、[Policy JSON 与默认拒绝](https://openbao.org/docs/concepts/policies/)

## 精确 Transit 请求契约（待固定版本实证）

- 两个独立 key 名为 `rsc-authentication-idempotency`、`rsc-material-request-contact`；必须先由独立运维身份创建，不让 API upsert。
- 拟用 `aes256-gcm96`、`derived=true`、`convergent_encryption=false`、`exportable=false`、`allow_plaintext_backup=false`。另核验删除默认关闭。此处是审查参数，不是自动创建脚本。
- `context` 是 **base64 编码的密钥派生上下文**，在 `derived=true` 时必需；`associated_data` 是 **base64 编码的 AEAD 附加认证数据**。二者语义不同，不得把 Aliyun EncryptionContext 字典不经编码直接当成请求字段，也不能提供其中一个后省掉另一个。
- 适配器将定义固定规范 JSON/context schema，至少绑定 application、environment、provider instance、purpose、key path、application key version。规范化后的 context 与 AAD 字节必须在 registry/实际解密中一致；确切序列化 schema 要先定版，不能临时拼字符串。
- 生成路径仅为 `POST /v1/transit/datakey/wrapped/<精确 key 名>`，请求 `bits=256`，连同上述 context/AAD；不使用 `plaintext` 路径，不自行指定 nonce。该生成权限不在 API policy 内。
- 生成结果只保存真实包装密文；实际 decrypt 结果必须规范 base64 且解码为 32 字节。版本来自真实包装密文前缀，并连同受控 key 路径、context/AAD、pin 和成功解密形成证明。OpenBao 响应没有 Aliyun KeyVersionId，不得把配置回填为假回执。
- 禁止 API 调用 rewrap 后覆盖既有 pin；轮换始终生成新应用版本，旧联系人和仍有效认证引用需要保留原 key。

上述字段与路径依据当前官方 Transit API，必须由**经审核的固定发行版本**再次验证才可执行；版本清单不写 `latest`，不从今日文档推断服务器二进制能力。[Transit API](https://openbao.org/docs/api/secret/transit/)

## 尚缺的具体部署条件

1. 核实固定 OpenBao 版本、官方下载来源、校验/签名和部署包 SHA-256；此处 null 是未决定，不能换成虚构摘要。
2. 完成存储与集群配置：官方 Raft 要求 `cluster_addr` 且禁止 `disable_clustering=true`。UDS-only API 与 Raft 集群监听的组合尚未实测，本片段没有承诺它能运行，也不能为了消除端口问题关闭 Raft 约束。确定完整组合后验证所有实际监听仅在批准的本地边界。[Raft 约束](https://openbao.org/docs/configuration/storage/raft/)、[配置参数](https://openbao.org/docs/configuration/)
3. 建立独立服务用户/访问组、受控父目录、容器 UID/GID/socket 挂载、内存和日志限制。socket 的 `0660` 只约束该文件，不能代替父目录、token 和 ACL；API 不能得到存储目录、root token 或解封份额。[Unix listener](https://openbao.org/docs/configuration/listener/unix/)
4. 审查运行身份投递、token TTL/续期/撤销、跨用途 context 拒绝、双人手动解封及离机份额；没有真实 token 或初始化步骤在仓库中。
5. 配置审计日志并验证不记录明文 key、token、联系人或完整响应；审计不可写/磁盘满的失败行为也需验证。
6. 完成真实一致性备份与隔离恢复；key 轮换后的快照必须先可恢复再启用；满足既定 RPO/RTO。现机资源读数只是容量候选证据，不是负载/恢复测试通过。
7. 按主方案完成 provider/envelope/前向迁移、PG16、pin、readiness 和真实 UAT。此模板测试不替代任何门禁。

## 仅离线检查

从 `cloud_oam/` 运行（无网络、无服务启动、无真实密钥）：

```bash
.venv/bin/python deployment/openbao-pilot/verify_templates.py
.venv/bin/python -m unittest discover -s deployment/openbao-pilot -p test_template_contract.py -v
```

通过仅表示这些模板保持限定的权限和监听**配置意图**；报告继续返回 `deploymentReady=false`、`effectivePolicyVerified=false`、`actualListenersVerified=false`。它不解析最终 OpenBao 合并配置、不核验真实角色的 policy 合集、不证明机器已经安全监听，不能用于开启流量。
