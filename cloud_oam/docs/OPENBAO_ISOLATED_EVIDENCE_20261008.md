# OpenBao 隔离原型与兼容准备交接

2026-10-08，Asia/Shanghai。**试点 MVP，不等同完整 V1，仍未上线。** 本批基于本地候选 `8d0d1429c3a6f080f35f7332d0adbdc13e3ecbaa`；准确后续提交以 Git/操作回执为准。完成低成本方案 B 的有限隔离验证及 C 的适配器、兼容边界准备，未启用生产 provider、修改已有迁移或创建收费资源。

## 已取得的证据

| 层次 | 实际结果 | 不能据此推定 |
| --- | --- | --- |
| 发行来源 | 固定 OpenBao 2.7.1；官方 checksums 的真实 PGP 签名、已发布主/子钥 fingerprint、两个架构包及提取二进制摘要已核验 | 未验证外部撤销状态；不是后续发行安全复核 |
| Darwin Transit | 真实非 dev Raft；两个用途各自 v1/v2；手工封存/解封、8 类越权、撤销拒绝；新目录恢复 4 条新旧 DEK 等值 | 不是生产保管、离机灾备、HA 或 RPO/RTO |
| Darwin Identity | 真实 RS256、固定实体/audience/issuer、两次轮换及 3 个 kid；过期、篡改、越权、引导与会话撤销验证 | 无公网 TLS issuer、RAM/STS 或真实云权限证明 |
| 候选本地测试 | 新增 115 项通过：adapter 49、UDS transport 30、identity 20、harness 安全 7、发行篡改 4、验签输入守卫 5 | 合成或 mock 不能替代真实服务及数据库契约 |
| Linux 真实组合 | 同一固定 Linux 二进制、真实 Transit 恢复、Identity 与候选 adapter 组合运行全部通过 | 仅合成数据；非正式部署/业务迁移/长期容量验收 |

已终态且未修改的旧测试没有重跑。Darwin 的成功步骤没有为了更新文档重复执行；Linux 是首次受限容器组合验证。新增 transport 首轮 29 passed / 1 failed，修正提前拒绝响应未关闭 `HTTPResponse` 后，30 passed；首次日志/JUnit 均保留。

## Linux 隔离与权威回读

使用目标服务器已存在 API 候选镜像 `sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575`，`--pull=never`，没有构建/拉取/安装新服务。只挂载经过逐文件 SHA-256 核验的 10 文件原型包，不挂业务卷、Docker socket 或凭据。

容器 `rsc-openbao-linux-b5cef1ca0911` 实际通过 Docker inspect 和容器内核双重检查：UID/GID 65532、全部 capabilities 丢弃、no-new-privileges、seccomp、core dump 关闭、network none、只读 rootfs、只读原型目录、192 MiB `/tmp` tmpfs、512 MiB RAM、零 swap、0.5 CPU、256 PID、无映射端口、无落盘日志。此前故意省略只读 rootfs 的独立反例在启动 Bao 前被拒绝；两个测试容器均精确回读为不存在。

容器入口内命令为：

```sh
PYTHONPATH=/prototype/backend PYTHONDONTWRITEBYTECODE=1 /usr/local/bin/python \
  /prototype/deployment/openbao-pilot/linux_sandbox_entry.py \
  --binary /prototype/bin/bao \
  --hook identity_isolated_gate:run_identity_contract \
  --hook candidate_hook:verify_candidate_adapter
```

该命令仅说明已执行容器内步骤，不能脱离以上 Docker 边界在生产运行。组合运行 exit 0，入口耗时 **31.148 秒**；cgroup 实测内存峰值 **247,320,576 bytes（约 235.9 MiB）**，子进程最大 RSS 203,328 KiB。它不是常驻运行、并发压力或总体容量通过证据。

Transit 先真实轮换并恢复两用途的 v1/v2，各一条 DEK，共 4 条。随后候选 adapter 使用各用途的应用版本 1/2 生成另 4 条 wrapped DEK，均与独立解密参考值相同；这 4 条使用的是当时真实 **Transit v2**，不能写成 candidate 已真实覆盖 Transit v1/v2。错 context、错 AAD、跨用途、独立 pin 不匹配、token 撤销均拒绝；3 秒总预算的受限 UDS transport 用于实际解密。

21:14:33 的最终回读确认：两个临时容器不存在、上传的 256,324,912 bytes 临时目录已删除、原六个运行容器 ID/名称集合与启动前一致。`MemAvailable=2,086,756 KiB`、swap 全部空闲、`/opt` 空闲 34,685,796,352 bytes。没有接管 80/443、修改业务容器、访问生产数据库或发送短信；容器集合不变不等同应用端到端健康验收。

脱敏证据均在 Git 忽略目录 `artifacts/formal-0165-integration/openbao-prototype-20261008/`：

- `linux-b5cef1ca0911-manifest.json`：真实执行源码、镜像外固定二进制及上传包摘要。
- `linux-b5cef1ca0911-receipt.json`：直接捕获的 SSH stdout，包含运行与前后权威回读。
- `linux-b5cef1ca0911-cleanup.json`：容器/目录清理、原服务集合和资源回读。
- `linux-boundary-negative.json`：拒绝缺少 rootfs 只读条件的真实容器反例。
- `runtime_darwin_arm64_20261008.json` / `identity_runtime_darwin_arm64_20261008.json`：Mac 独立回执；前者标明由真实成功 stdout 重建，不冒充原始重定向日志。

运行包中的 `isolated_openbao_harness.py` SHA-256 为 `5d9e622fac1ec4eef104353133c85e3554ac8ca2762480d8c498996682039983`，Linux 入口为 `cc2bcd237de55e615290c5e59a431b0bce13e28ea25c93289d8865fee86d6ba0`，candidate hook 为 `8de860dda681d0bd7fa8c95b462e9bdcf7a3f9d21efa64c363e8a7fa9a69e31e`。身份与 adapter 摘要见同一 manifest；后续改动不能沿用本次实跑结论而不说明差异。

## 已明确的实现边界

- OpenBao 2.7.1 的纯 UDS + Raft 原型未能解封。当前可运行实验形态为 UDS 业务 API，加两个仅 loopback 的 metrics/cluster 监听；带 root 和运行 token 的 API 请求不能经 metrics 端口执行。不能把原离线 UDS 配置片段当成可部署的完整服务。
- Identity 的 SecretID 撤销阻止新登录，会话撤销阻止继续签发；已签出 JWT 在有效期内仍可离线通过。未证明即时撤回 JWT/STS。
- 新 `openbao_transit_candidate.py` 独立于 Settings/factory/readiness。它使用明确 provider/实例/用途/版本坐标与独立 pins，不读取凭据，不伪造 Aliyun KeyVersionId，也不重解释旧密文。
- 新 UDS transport 仅供隔离实例使用；生产 token 引导/续期、服务边界、文件 registry 加载、持续可用性仍未完成。Identity 文件投影与 SDK 重读实验为合成边界，未通过真实 RAM/STS。

## 下一有限批次

1. **C：追加兼容迁移。** 当前已存在 head `20261228_0179`，须从届时实际 head 追加；保留 0029/0040/0069/0168 等冻结字节与旧 Aliyun 读路径。补齐跨 provider 的 purpose/application-version 数据库唯一性、不可变 bindings、contact 新信封/旧 v1 双读、auth 未过期引用保护、完整 provider 坐标 readiness，以及实际 source-hash/角色 ACL 门禁。不得只改 provider 字符串或跳过 pin gate。[兼容准备](OPENBAO_PROVIDER_COMPATIBILITY_20261008.md)
2. **生产运行身份和附件。** 固定非秘密 issuer/audience/subject/RAM 角色及私有 OSS 坐标，再准备可审阅的配置；真实 TLS/JWKS、STS 续期与最小权限、OSS 防覆盖和签名 URL 都需实测。云资源尚未配置，不要求在聊天提供密钥。
3. **当前候选 CI。** `14d0731` 的 Client 已成功；PG16 `37772507579` 最新 21:06 快照 64 成功、7 失败、3 static_safety 运行中。`8d0d142` 已本地修复相关夹具并聚焦验证，但没有新 SHA 的远端通过。旧 run 保持运行，等终态后一次正常推送集中候选，避免 cancel-in-progress；仍需读取全部失败，不把本原型算作 PG16。
4. **试点放行。** 当前版本迁移/ACL、真实短信登录、人员/角色及真机 UAT、API/HTTPS、备份恢复与回滚分别通过后再发布。整体 `releaseDecision=not_ready`。

本批不扩大 MVP。拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实业务通知渠道及投递运维继续列为后续迭代。底层状态、迁移、契约、不可变库存流水、幂等、审计、权限、安全与取消/关闭守卫保留；真实短信登录仍为首发要求。
