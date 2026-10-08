# 隔离机器身份实验

本目录仅用于低成本试点 MVP 的隔离原型验证，不是完整 V1 验收，不启用应用 provider、Settings、SQL、部署或云资源。已完整阅读正式 V1 基线及 `docs/LOW_COST_RUNTIME_IDENTITY_20261008.md`。正式状态轴和生产授权边界保持独立。

## 文件与执行

- `identity_contract.py`：固定可信 issuer、单一 audience、实体 UUID；使用现有 PyJWT 2.10.1 / cryptography 50.0.1 验 RS256、kid、签名、必需 claims、整数时间、TTL 和剩余寿命。不发现凭据、不访问网络、不签名。
- `identity_tests.py`：合成 RSA/JWT、私有目录原子投影反例、显式 OIDC SDK 刷新时重读文件。直接执行只输出安全摘要，失败不输出 JWT、STS、密钥或原始异常。
- `identity_isolated_gate.py`：通过 `LocalBao` hook 使用临时真实 OpenBao，创建合成 AppRole/entity/alias/key/role。运行 JWT、SecretID 和 Bao token 仅留内存；没有把这些运行凭据写入投影文件。
- [Darwin/arm64 回执](../../artifacts/formal-0165-integration/openbao-prototype-20261008/identity_runtime_darwin_arm64_20261008.json)：2026-10-08 的本机真实隔离运行脱敏回执，退出码 0；存于 Git 忽略的 artifacts 中。

在 `cloud_oam/deployment/openbao-pilot` 执行新增聚焦测试：

```sh
../../.venv/bin/python identity_tests.py
```

最终结果：20 项通过、0 failures、0 errors，退出码 0。子用例覆盖缺失/错误 claim、字符串/布尔时间、未来签发、过期/超 TTL/余寿不足、错误签名、none/HS256、kid 未知/重复、无效/弱 RSA、公钥轮换、重复 JSON 字段，以及目录/目标权限、属主、软硬链接、替换失败清理和提交前再次检查有效期。文件句柄测试证明旧 inode 保留旧内容、重新按目录读取可见新 token。

真实 runtime 命令（不重复已经完成的 Transit 套件）：

```sh
../../.venv/bin/python isolated_openbao_harness.py \
  --binary /tmp/rsc-openbao-2.7.1-20261008/darwin_arm64/bao \
  --skip-transit --hook identity_isolated_gate:run_identity_contract
```

本次运行的源码 SHA-256：

| 文件 | SHA-256 |
| --- | --- |
| identity_contract.py | e735da761aa326f223fa4b21f296b4f6b9578ce7645470e8479669df95ced7cb |
| identity_tests.py | e0abcf4f8bcba9b3d027e4e39b8d1ce6ea7b7baf4b9340e5a54584ea2cc4d780 |
| identity_isolated_gate.py | 94f8efa6f650a8044cc231ce5196cc912644618d7aae457a19575ee4bc4d49d0 |
| isolated_openbao_harness.py | a8b59dc49b219ab032a8f353f27a4cd9ab5fe70050964d79a2734e113ca97658 |

该 harness 后续安全错误格式修订不在上述已执行源码 hash 内；不要把未执行版本的 hash 标为本次测试对象。二进制 hash、监听检查和清理结果见回执。

## 真实隔离证据

机器接口使用 `GET identity/oidc/token/<role>`。根权限只用于隔离自举；运行身份只有两个明确测试角色的签发读取权限，没有配置、轮换、策略管理或其他角色权限。由自举读取的实体 UUID 建立固定 subject，不从待验证 JWT 推导期望主体。

OIDC 配置字段 `issuer` 使用 origin；实际 discovery/JWT issuer 为该 origin 加 `/v1/identity/oidc`，并严格核对 discovery 的 JWKS URL。没有对 `.invalid` 测试域名发起请求。接口依据：[OpenBao 2.7 Identity Tokens API](https://openbao.org/docs/api/secret/identity/tokens/)、[Identity Tokens](https://openbao.org/docs/secrets/identity/identity-token/)。

回执确认 RS256 和 300 秒 TTL；两次**手动触发**真实密钥轮换产生 3 个不同 kid，轮换后旧、新 JWT 均可验证。三种错误期望 claim、签名篡改和 3 秒测试令牌的自然过期被拒绝；5 项运行身份越权及匿名签发被拒绝。撤销 SecretID 阻断新登录，再撤销已建立会话阻断签发。已经签出的 JWT 仍可在有效期内离线验证，不能宣称会话撤销会即时收回所有 JWT 或 STS。

首次隔离尝试在 `POST /v1/sys/init` 的原 8 秒读取期限超时，退出码 1，尚未执行身份 hook。原输出仅保留在工具记录中，没有原日志文件，未重建日志。没有重放未知初始化请求或复用该实例。运行器改为初始化专用 30 秒期限后，在全新临时实例得到上述通过结果。

实测网络边界是 UDS 业务 API（0600）及仅 loopback 的 cluster、metrics 各一个监听；API TCP 监听为 0，带 root token 的 5 条 API 请求在 metrics 端口被拒绝。metrics 辅助监听是该隔离 Raft 实验的运行器配置，不是生产部署方案或完全无 TCP 的证明。

## 证据范围与剩余条件

SDK 测试使用已安装的 `alibabacloud-credentials==1.0.12`，清空测试环境后构造**显式** `OIDCRoleArnCredentialsProvider`，拦截 `TeaCore.do_action`，同时禁止 socket connect。两次调用其同步刷新边界 `_refresh_credentials`，合成 STS 响应验证文件更新被重新读取；删除文件后阻断刷新。它不验证默认链选路、真实 RAM/STS、云端身份/权限或真实时间窗口下的自动续期。

合成投影目录为临时私有目录；没有声称本机目录是 tmpfs，也没有证明容器 UID/read-only mount 隔离。此原型尚缺生产守护/自举、tmpfs 目录投影部署、公开 HTTPS issuer/JWKS、证书校验、RAM 精确绑定及角色权限、真实 STS 兑换与跨窗口续期、时钟/发行者故障与运维恢复现场验收。未连接目标主机、未读取真实凭据、未创建云资源、未发短信；真实短信/UAT、附件、备份恢复/回滚及上线仍属于外部门槛。

上述“未连接目标主机”限于本份 Darwin/合成实验。主任务随后在目标服务器一次性断网、只读根、非 root 容器中完成同一 Identity hook 的独立 Linux 验证，真实运行凭据仍仅在内存；容器和上传目录清理后精确回读通过。该验证不包含真实 token 文件投影到生产消费者、RAM/STS 或公网 TLS，详见[组合验证回执](../../docs/OPENBAO_ISOLATED_EVIDENCE_20261008.md)。
