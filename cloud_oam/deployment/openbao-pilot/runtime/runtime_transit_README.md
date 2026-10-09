# C1/C2：首次 Transit 配置与包装响应封存

这是本地已验证的小工具，尚未创建正式 Transit key 或 DEK，尚未选择生产数据库中的应用版本。A/B/D 已冻结输入不改。后续 C3 的完整 registry 组装/安装、独立 DB pin 与历史引用核验仍由正式协调流程完成，不以本工具的提案自行启用。

## 生产契约与既有运行时

`runtime_transit_contract.py` 直接导入产品 `OpenBaoKeyCoordinate`、`context_b64`、`associated_data_b64`、`OpenBaoWrappedKey` 与 registry `_parse_entries`，不修改或另造它们的序列化。固定 provider `openbao_transit_v1`、environment `production`，purpose 只能为 `authentication_idempotency` 或 `material_request_contact`。**每次必须显式给出 application_key_version**；工具不查询/选择/启用版本，不允许缺值、bool、0、负数或字符串冒充整数。

保管控制器仍使用现有 `/tmp/rsc-openbao-verifier-20261008/bin/python`（3.9.6，已安装固定 PGPy）。产品 dataclass(slots=True) 在 3.9 不支持，因此公开契约 bridge 使用现有 `cloud_oam/.venv/bin/python`（3.12.14）；无需安装新依赖。两个本机进程之间只有公开坐标、context/AAD 和 wrapped ciphertext，绝不传 root token、share 或明文 DEK。Bridge 只允许私有 stdin/stdout pipe，拒绝把材料输出终端。

## 独立阶段

`runtime_transit.py keys` 默认仅回读；只有 `--apply` 允许在对象确实不存在时创建 `transit/` mount 及两个固定 key。每次创建前先 O_EXCL/fsync 公共 intent，随后 GET 精确核验；已存在且准确就只读通过，偏移拒绝，不更新旧 key。固定 derived AES256-GCM96；禁止 convergent、exportable、plaintext backup、deletion、import/restore、auto rotation。要求实际 GET 的 latest_version=1、keys 只有 `1`、min_decryption_version=1/min_encryption_version=0/min_available_version=0、HKDF-SHA256 及相应能力。静态源码不是已实际创建版本 1 的证据，正式 GET 不符就阻断。

`generate --apply` 只接受已经通过以上只读 key 审查的实例，固定 `POST /v1/transit/datakey/wrapped/<exact-key>`，bits=256、**key_version=1**、生产 context/AAD。`0/latest` 不可用。只接受严格 `{ciphertext,key_version}` 响应，拒绝 plaintext/其他字段、版本偏移、非规范 base64 或不符 12-byte nonce + 32-byte DEK + 16-byte tag 的包装格式。

生成前同时建立映像内的公开 generation intent 与 Bao 私有 state 内的 O_EXCL attempt marker；每个 purpose/application_version 只能生成一次。远端原始 wrapped 响应先写 `/state/transit-<purpose>-v<version>-wrapped.json` 0600 并 fsync/读回，才经私有 pipe 返回。本地在原已验证 AES256 映像内按顺序保存：

1. `transit-<purpose>-v<version>/generation-intent.json`：完整 Bao ID、原 init runId、本次 attemptId、生产坐标。
2. `original-wrapped-response.json`：原始包装结果及同一次请求绑定，O_EXCL/fsync/读回。
3. `pin-proposal.json`：**直接基于原始响应、先于 registry-entry** 形成的待审核提案；显式 `independentDatabasePinVerified=false`。
4. `registry-entry.json`：符合已有 registry schema 的一个 entry。

没有生成单独明文 DEK、没有 decrypt/export/backup/rotate/delete/token/login API 白名单，没有把 wrapped material 放进公开结果、日志或聊天。主控制器只输出阶段状态、用途、版本、来源 hash 和明确的未完成字段。

## 中断处理

`status` 仅查精确 marker/保存结果，不能改变远端状态。`recover` 仅读取同一 attempt、同一 purpose/version、同一 instance/contract 的已保存包装结果；没有结果时保持 unknown，绝不重新 generate。远端生成成功而响应保存之前中断，可能留下未被登记的孤立 DEK，必须人工判定；不得为了补回执再次生成同一应用版本。

恢复封存时允许验证已有相同字节，绝不覆盖不同内容。生成模式遇到本地同名 custody 目录就拒绝，即使目录为空。SSH/回读失败后不要调用 `generate` 重试；应先 read-only status，再明确 recover 原响应。恢复不要求 Bao 解封，因为只回读该实例私有 state 的 wrapped ciphertext；根 token仍只在本机已验证映像/内存/private pipe，未写普通文件。

参数包括 `--contract-python`、`--purpose`、`--application-key-version`、新 `--attempt-id`、原 `--run-id`、完整 `--container-id`、`--instance-id`、`--ssh-config/--ssh-host`、必要时 `--ssh-sudo`、`--vault-mount/--vault-binding`。没有任何默认应用版本，示例也不代替实际数据库选定。

## 仍须独立完成的 C3/上线证据

核对实际候选 PostgreSQL16 中跨 provider 的版本占用、存量密文、未过期认证重放和所有历史引用；显式选择两用途版本。取得原响应提案后，需要独立审核、受控 DB pin/claim 写入并在原库精确回读，不可由 registry 或可变 decrypt response 反推 pin。随后才可按既有 schema 组装正式 registry，安装到独立 `/etc/rsc-openbao-registry`（API23204:23204，目录0700/registry.json0600），并通过 API/gate 的只读绑定与 pin/provider 门禁。

当前工具只保存两个用途各自的 registry-entry，**不宣称完整 registry 已组装、已安装、DB pin 已生效或生产已就绪**。这也不能替代公开 issuer/JWKS、OSS/短信 STS、UAT、正式备份及恢复演练。

## 本次新增检查与固定原始来源

`artifacts/openbao-runtime-20261009/transit-local-01`：12 个新增 unittest 首轮全通过，0.612 秒 wrapper/0.401 秒 tests；源码前后 hash 一致。包括真实本机 3.9→3.12 公共参数桥接、实际 O_EXCL/0600/读回、产品 coordinate/parser 复用、unknown 不重发、回查不发网络、权限与路径反例、原响应→pin→entry 保存顺序。无远端调用/正式 key/DEK；未重跑任何已终态旧套件。测试为 unittest 文本回执，不声称 pytest XML 或 hosted PG16。

Bao 源固定 commit `a5db72cef75c24b920ade02065b18dd8eb666bac`，两个文件均与已有完整 Git tree blob 匹配，见 `artifacts/openbao-runtime-20261009/transit-upstream/source-receipt.json`：`path_keys.go` 的创建/GET策略与 `path_datakey.go` 的 wrapped 路径/显式版本/响应字段。后者只有 plaintext 分支返回明文；本工具的白名单永远不进入它。初始版本/KDF/min-version 的真实状态仍以正式 GET 为准，不用 mock 代替。

提交仅精确添加三个 `runtime_transit*.py`、`test_runtime_transit.py` 和本 README。不要把当前 `runtime/` ignore 例外扩大到 custody、生成 registry、证据目录或秘密。
