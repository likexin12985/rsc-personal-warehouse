# OpenBao 2.7.1 隔离原型记录

2026-10-08。**试点 MVP，不等同完整 V1；没有启用生产 provider 或正式部署。** 原有 `README.md` / `review-status.json` 仍只描述未启用的离线模板。本文件记录新增真实二进制原型，不把 mock、配置意图和运行证据合并。

## 固定发行与验证

官方 GitHub Release API 当日返回当前稳定版 **v2.7.1**，发布日期 `2026-10-01T14:03:19Z`，`draft=false`、`prerelease=false`。存在 `darwin_arm64` 和 `linux_amd64` 的官方 tar.gz。固定包及包内 `bao` 摘要见 `official_release_2.7.1.json`，不使用 `latest`。[官方发行](https://github.com/openbao/openbao/releases/tag/v2.7.1)

本机没有 GPG/cosign；在 `/tmp/rsc-openbao-verifier-20261008` 的独立 venv 安装 PyPI `PGPy==0.6.0`，未改全局或应用依赖。`verify_official_release.py` 核对官方发布的主公钥和实际签名子钥完整 fingerprint，真实验证 `checksums.txt.gpgsig`，再将两个包的 SHA-256 与已签 checksum 及 Release API digest 交叉核对，最后仅提取唯一 regular-file `bao`。没有自实现 PGP 验证算法。[官方校验与 fingerprint](https://openbao.org/docs/install/)

验签边界：这是 PGpy 的密码学验签和固定官方 fingerprint，不是假称调用了 GPG CLI。PGpy 不自动完成全部 key revocation 评估；未查询外部撤销清单，锁文件明确保留该项 `false`。本地 API 返回信息和当次验签不取代未来发布时的安全公告/撤销复核。

GitHub 网页下载端口曾超时；最终通过官方 Release assets API 的 `Accept: application/octet-stream` 下载同一发行资产。没有使用第三方镜像、企业代理、业务浏览器凭据或放宽 TLS。

验签命令（已经成功，不为更新文档重跑）：

```sh
/tmp/rsc-openbao-verifier-20261008/bin/python deployment/openbao-pilot/verify_official_release.py --downloads /tmp/rsc-openbao-2.7.1-20261008 --manifest deployment/openbao-pilot/official_release_2.7.1.json
```

## 真实发现及已修正的隔离形态

1. 只写 Unix `socket_mode` 不足以应用权限。2.7.1 只有 mode/user/group 均非空时才构造 socket 权限配置；首次运行因实际 mode 不等于 `0600` 失败退出。新增 harness 补齐当前进程 uid/gid，原离线模板已有三字段，不修改。[固定版本 Unix listener 源码](https://github.com/openbao/openbao/blob/v2.7.1/internal/command/server/listener_unix.go)
2. **纯 UDS + Raft 尚不能作为可运行方案。** 真实初始化后解封状态失败；固定版本 `InitListeners` 仅从 TCP listener 合成集群监听地址，UDS 上写 `cluster_address` 不产生该监听。没有把空 TCP 监听集视为 Raft/HA 成功。[固定版本 server 源码](https://github.com/openbao/openbao/blob/v2.7.1/internal/command/server.go)
3. 经明确同步边界后，原型使用 UDS API `0600`，以及 `127.0.0.1` 临时 metrics-only TCP listener + 单独 loopback Raft cluster listener。指标 listener 配置 `metrics_only=true`、`unauthenticated_metrics_access=false`，仅本地合成原型使用 `tls_disable=true`；不能复制为正式 TLS 配置。实际进程监听必须恰好两条 loopback TCP。携带 root 和运行 token 的 sys/init、health、mounts、mount-create、transit-decrypt 均实际收到 403/404。[metrics-only 的官方边界](https://openbao.org/docs/configuration/listener/tcp/)
4. 后续两次运行发生超时，第一次只记录 `TimeoutError`，具体请求阶段未知；第二次精确定位为 `POST /v1/sys/init` 超过原 8 秒期限。没有重放未知请求：每次均销毁该隔离实例。初始化独立期限调整为 30 秒后的全新实例通过；不由此归因登录、网络或 HOME。普通请求仍为 8 秒；应用候选 transport 的独立 3 秒总预算由其自己的测试证明。

`isolated_openbao_harness.py` 为非 dev、单节点 Raft、3 份/阈值 2 的手动 Shamir 初始化。新 `/tmp/rsc-bao-*` 目录 `0700`；root/runtime token、解封份额、明文 DEK 与 snapshot 响应仅在内存处理，不进入 argv、环境、stdout 或文件。加密 Raft 状态只在该临时目录，进程退出后清理。不设置或继承 HOME；不继承云凭据/BAO token；不启用正式服务或现有配置。

Raft 并非本项目已确定的唯一正式存储选择。官方当前存储建议与运维取舍仍须评估；本次按已限定范围验证单节点 Raft，没有扩展存储后端，也没有证明高可用。[官方存储说明](https://openbao.org/docs/configuration/storage/)

## Darwin 实测证据

成功命令（已终态，不重跑）：

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python deployment/openbao-pilot/isolated_openbao_harness.py --binary /tmp/rsc-openbao-2.7.1-20261008/darwin_arm64/bao
```

exit 0；两个用途 `rsc-authentication-idempotency` / `rsc-material-request-contact` 均通过：

- `derived=true` 的独立 context 与 `associated_data` 实际参与验证；错值、缺失和跨用途解密均拒绝。
- `datakey/wrapped` 只返回包装密文，解密为 32 字节。真实密文前缀从 `vault:v1:` 轮换到 `vault:v2:`，旧版本继续能读；没有伪造 Aliyun KeyVersionId。
- 精确 decrypt-only token 不含 default policy；encrypt、datakey、key read、rotate、export、snapshot、token-create、其他 key 路径共 8 类越权请求均 403。
- seal 后 decrypt 503；手动阈值 2 解封后等值解密；token 撤销后 403。
- 读取实际 Raft snapshot，在另一全新目录/cluster 普通 restore 被拒，再显式执行一次该可销毁测试 cluster 的 snapshot-force；要求原份额重新解封。两个用途各自 v1/v2，共 **4 条** DEK 全部等值回读，恢复后实际监听再次校验。
- 最后所有原型进程退出、临时状态清理，`cleanupCompleted=true`。不证明真实保管人可达、离机备份或正式 RPO/RTO。

脱敏回执保存于 Git 忽略目录：`artifacts/formal-0165-integration/openbao-prototype-20261008/runtime_darwin_arm64_20261008.json`。该文件明确 `capturedFrom=successful tool stdout, not a rerun`：它由实际工具 stdout 重建保存，不冒充当次 shell 重定向日志。执行时 harness SHA-256 为 `a8b59dc49b219ab032a8f353f27a4cd9ab5fe70050964d79a2734e113ca97658`；之后只增加 Identity 固定安全错误码白名单，未重跑 Transit。锁文件/ACL 的当时摘要同时在回执中。

## 新增安全反例

仅运行本次新增测试，未重跑既有 7 项离线模板测试或成功 Transit：

```sh
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m unittest discover -s deployment/openbao-pilot -p test_isolated_safety.py -v
RSC_OPENBAO_PUBLIC_FIXTURES=/tmp/rsc-openbao-2.7.1-20261008 PYTHONDONTWRITEBYTECODE=1 /tmp/rsc-openbao-verifier-20261008/bin/python -m unittest discover -s deployment/openbao-pilot -p test_release_tampering.py -v
```

分别 **7 passed / 0.016s**、**4 passed / 0.055s**。第一组验证错误 binary/架构、额外/公网/缺失监听、指标口错误接受管理员请求、不受限超时、非法 API path、错误内容泄漏均失败关闭；此组 mock 不冒充真实运行。第二组用已下载的官方公钥/签名材料，验证篡改 checksums、错误信任 fingerprint、错误签名子钥及篡改发行包不能提取/写成功 manifest。

真实重定向日志：同一忽略 artifact 目录中的 `isolated_safety_tests_20261008.log`、`release_tampering_tests_20261008.log`。

独立审查后又补充验证器前置：通过 `importlib.metadata.version('PGPy')` 拒绝非已审阅 `0.6.0`；拒绝 downloads、manifest、提取目录/二进制及输入文件的符号链接。只运行新增 `test_release_guardrails.py` 的 **5 项反例（0.013s）**，没有重跑上述已终态测试。命令为 `PYTHONDONTWRITEBYTECODE=1 /tmp/rsc-openbao-verifier-20261008/bin/python -m unittest discover -s deployment/openbao-pilot -p test_release_guardrails.py -v`；真实重定向日志为同目录 `release_guardrail_tests_20261008.log`。这些新增守卫不代表重新取得了另一份发行验签回执。

## 尚未由本证据证明

- 本文上述命令只证明 Darwin 合成原型。Linux 随后已完成独立受限容器组合实跑，报告和清理回读见[Linux 组合证据](../../docs/OPENBAO_ISOLATED_EVIDENCE_20261008.md)；不能将包验签或本文的 Mac 回执当作 Linux 证据。
- 同机独立 UID/group、UDS 容器挂载、TLS/公网 OIDC discovery/JWKS、机器引导身份、RAM STS 信任、真实云渠道、正式 provider/factory/registry/pins/迁移/readiness 仍不能由本实验推定完成。
- API root token 只用于本次临时 fixture；正式 API 不可使用它。手动解封的真实份额管理、离机备份及恢复值守、RPO ≤ 5 分钟/RTO ≤ 2 小时、性能、PG16 与真实 UAT 门禁继续保留。旧 Aliyun 密文不能改标签交 OpenBao 解密。
- `official_release_2.7.1.json` 的包锁与后续 prototype receipts 不改变原离线 `review-status.json`，也不授权对现有六个服务、80/443、数据库、业务卷或收费资源作任何变更。
