# 2026-10-09 试点身份目录与静态配置绑定

**试点 MVP，不等同完整 V1；`not_ready`，未部署。** 本批修复部署器的一个实际阻塞：旧 `pilot_release.py` 把所有 bind 当作静态配置复制并计算内容摘要，不适用于原子轮换的 OpenBao/OIDC token 或 Unix socket。本文件是配置和实现说明，不是目标 Linux、云身份或真实登录验收。

## 两类挂载

| 类型 | 准入及处理 |
| --- | --- |
| OpenBao token / Unix socket | 只认 API 与 `kms-pin-gate` 的九项 OpenBao 显式坐标及完全相同的来源。两个服务必须声明数值 `user: UID:GID`，API UID 与应用配置一致，并能读取声明的共享 GID。仅允许对应文件的父目录只读 bind，`create_host_path: false`、`rprivate`。 |
| OSS OIDC token | 只认 API 的 `OAM_FILE_STORAGE_CREDENTIAL_MODE=oidc_role_arn` 和现有完整 OIDC 坐标。发行身份、角色、token 路径及只读目录继续由现有预检校验。部署器另要求非秘密 `RSC_OSS_OIDC_PROJECTOR_UID`、`RSC_OSS_OIDC_SHARED_GID`，不得从任意现有文件 owner 推断可信身份。 |
| PNVS OIDC token | 只认 API 显式 PNVS 坐标与 SDK 官方环境变量完全相同、OSS/PNVS 的 role、subject、audience、token 目录独立且账号一致的声明。两角色可复用同一受控 issuer 的 RAM OIDC provider。复用上述 live 目录元数据合同，不读取/复制/摘要 token。完整条件见 `PNVS_OIDC_DEPLOYMENT_20261009.md`。 |
| OpenBao wrapped registry | 保持静态内容摘要和私有快照，只接受完整私有目录 bind。源和快照均须目录 `0700`、文件 `0600`，UID/GID 与 API 的显式数值身份一致。快照保留所有权，部署账号无权设置正确所有权则失败，不回退 root 或放宽应用 loader。 |
| 其余配置 | 保留原静态快照和摘要绑定。文件名含 `token` 或 `socket` 不构成动态豁免。额外服务、嵌套挂载及与动态目录重叠的静态来源均拒绝。 |

动态源必须位于真实 Linux `/run/` 下的 `tmpfs`，挂载属性包含 `nosuid,nodev`。部署器通过 dirfd、`O_NOFOLLOW`、稳定目录元数据和 `/proc/self/mountinfo` 核验归属；源目录为 `0750`，独立 projector/Bao UID 所有、共享组可读。令牌文件为 `0440`、单链接、有界普通文件；socket 为 `0660`、单链接 Unix socket。稳定目录仅含其约定的一个投影对象，避免把 AppRole SecretID 等其他材料随目录提供给 API。

官方 Agent 2.7.1 的原子 file sink 会暂建 `<精确文件名>.tmp.<8位小写十六进制>`，再 rename/remove。依据锁定上游 `a5db72cef75c24b920ade02065b18dd8eb666bac` 的 `file_sink.go`（本地 SHA-256 `b1748240e138b05c7c1d5812f07522274d3c0d2e2728babb2a4afb9299b2025a`），仅 OpenBao token 目录允许对此单个临时对象进行最多四次、间隔 25ms 的元数据复查；它须从创建时就是 Agent UID/shared GID、`0440`、单链接普通文件，大小 `0..4096`。临时项消失后才返回稳定检查；残留报 `runtime_projection_rotation_pending`，多个临时项、未知名字、软链接或错误权限直接拒绝。不会读取、删除或把临时对象纳入 fingerprint。

准备回执的 opaque 输入摘要绑定源目录/祖先 inode、UID/GID/mode 和实际 mount 属性。**不打开、复制、保存或计算 token 内容摘要，也不绑定 token 的 inode、mtime、大小**；每次检查仍验证当前文件类型、权限、所有权及尺寸边界。原子替换 token 可继续工作；替换源目录、改变挂载、软链接、权限退化均阻断。

`frozen_document` 为这几项保留原 live bind，静态 registry 则继续指向受控快照。每次部署阶段前后重新检查来源；启动/门禁容器回读还核对真实 `Config.User`、`HostConfig.GroupAdd`、`Mounts.Type/Source/Destination/RW/Propagation`。这些声明与只读回读不替代应用实际 UID、Unix peercred、令牌策略、有效期和解密验收。

所有 admitted live 服务还必须声明并回读 `cap_drop: [ALL]`、无 `cap_add`、非 privileged、唯一 `no-new-privileges` 安全项。OpenBao API/gate 须相同 `RSC_OPENBAO_CONTAINER_ID`（完整 64 位 ID）、`RSC_OPENBAO_IMAGE_ID`（完整 sha256 ID）及 `pid: container:<完整ID>`，禁止名字和 host PID。准备及每次 `stable` 只读 inspect 此外部 Bao，白名单绑定真实 ID/Image、Running/StartedAt/host PID、User/GroupAdd、无权限提升、只读 rootfs、独立 PID/IPC/userns、无设备/发布端口及 `network=none`、mount 元数据；同名重建、重启或挂载漂移均失效。全部原始 `Config.Env` 不进入摘要或回执。API/gate 实际 `PidMode` 必须匹配声明。这是当前 transport 的正数 `SO_PEERCRED` PID 合同所需，不是采用宿主 PID 或放宽 transport 的替代办法。

`deployment/openbao-runtime.compose.yml.example` 仍是 draft；必须先核验真实外部 Bao/Agent 与 host 目录，再将模板合并到准确候选。模板不创建用户、进程、密钥或云资源，不能把隔离实验的临时 ID/UID 直接填写成正式配置。

三份 overlay 的 `cap_drop`、`cap_add`、`security_opt` 使用 **Compose ≥2.24.4** 的 `!override` 明确替换，避免多 overlay 追加出重复安全项。真实目标 Compose `2.40.3+ds1-0ubuntu1~24.04.1` 只做 `config` 解析，已看到 API 三个共享 GID 正确合并、单一 `ALL`/no-new-privileges 和 gate 不携带 PNVS 身份；没有启动服务。初轮重复安全项结果与后续修复证据均保留，成功 parser 未重跑。详见 `artifacts/linux-agent-runtime-20261009/compose-merge-02/stdout.json`、`compose-defaults-03/receipt.json` 及主任务裁定回执。

同一真实 Compose 的最小矩阵确认：long syntax 明确 `create_host_path:false` 会序列化成 **`bind:{}`**，`privileged:false` 会省略；再读 canonical JSON 做 `config` roundtrip 保持一致。`create_host_path:true` 和 short syntax 都保留 `true`；long syntax 未声明 bind 时完全没有 bind 键。共享 `bind_disables_host_path_creation` 因而只接纳 `type=bind` 且 **bind 键实际存在且是对象** 的明确 false 或空对象形式；缺 bind/null/数组、显式 true/null/字符串/数字均拒绝。没有把所有缺失值默认成 false，也没有改变原 JSON 的严格 roundtrip 比较。路径存在、非软链接、真实 tmpfs、read-only、传播属性及 owner/inode 检查全部保留；真实 Docker inspect 的 `Privileged` 仍必须明确为 false。

OSS 示例见 `deployment/oss-oidc.compose.yml.example`。所有 UID/GID/路径都必须来自真实已核验部署，示例不会创建用户、目录、身份发行者或云权限。

## 两项相邻接线修复

`pilot_preflight.py` 已按 provider 识别 OpenBao 与 Aliyun。OpenBao 配置复用纯 `OpenBaoProductionSettings` 和正式 registry parser，读取的仅是 wrapped 注册表及公开坐标；不读 token、不连接 socket、不查数据库、不构造在线取钥器。缺失/非法注册表、活跃版本缺失和 API/gate 配置或只读挂载不一致均阻断。历史 Aliyun 注册表声明仍独立检查；精确历史集合与不可变 DB pins/claims 仍由运行门禁核验，不从配置猜测。

`oss_bucket_preflight.py --inspect` 改用正式 `OssOidcCredentialsProvider`。应给该受控审查进程提供单独的 Bucket 四 GET 审查角色，不给日常附件身份附加管理权限；拒绝环境 AK/手贴 STS 和默认链兜底。预期 Bucket owner 必须匹配明确角色的账号。命令的默认帮助保持冷启动，不导入身份 SDK、不读 token 或访问网络。

安全只读入口（在已经准备好的受控运行配置内执行；不粘贴 token）：

```sh
python scripts/oss_bucket_preflight.py --help
python scripts/oss_bucket_preflight.py --inspect
python scripts/pnvs_runtime_preflight.py
python scripts/pnvs_runtime_preflight.py --resolve-default-chain
```

OSS 检查从非秘密 `OAM_FILE_STORAGE_REGION`、`OAM_FILE_STORAGE_BUCKET` 和 `RSC_OSS_PREFLIGHT_OWNER_ID` 取目标；其显式 OIDC 身份变量由部署环境提供。`--inspect` 才允许只读 GET；PNVS `--resolve-default-chain` 只尝试解析动态身份，不发送/校验短信。配置解析、Bucket 四 GET 与身份解析均不代表续期、权限全集、附件读写、真实 PNVS 或上线通过。

## 验证与剩余依赖

- 两个新预检测试文件及一个直接受影响的静态凭据拒绝节点：47 passed；原 Aliyun/OSS 精确兼容节点：10 passed。各自源摘要、日志、XML 和退出码在 `artifacts/oss-pilot-preflight-20261009/attempt-01/` 与 `compatibility-02/`；不重跑这两组已终态结果。
- 动态目录/静态快照新增专项及直接受影响的部署协调器测试，结果以同目录 `live-mounts-*` 回执为准。Mac 的真实文件/Unix socket 元数据与合成 Linux mountinfo、Docker 回读必须分开表述，不能当成真实目标 Linux 验收。
- `live-mounts-03` 为前一阶段 51 passed。此次新增外部 peer/隔离/临时项与 PNVS 部署专项在 `runtime-boundary-04` 因缺后端 `PYTHONPATH` 统一 setup 失败，未进入测试；`runtime-boundary-05` 修正入口后 102 passed、1 failed（测试直接调用原始 SDK 的预期日志边界不符）；`runtime-boundary-06` 仅重跑改接真实预检日志隔离边界的两个 SDK 节点，2 passed。新节点共 103 个，不能把重复节点相加作总数。各回执保存前后源码摘要；102 个其他节点和旧 51/47/10 终态集未重跑。
- `compose-canonical-08` 仅运行新 `test_pilot_compose_canonical_mounts.py`：**14 passed in 0.27s**，源码前后稳定。覆盖真实 canonical 形状的两个消费入口、缺失/错误类型拒绝、冻结输出保持原形状、其余 RO/传播/来源限制不退化；未重跑此前 103/51/47/10 节点，也没有重复真实成功 parser。
- 本批未访问目标服务器或云控制台，未读取正式凭据、发送短信或运行真实 `prepare/start`，未提交、推送、部署。旧配置路径和云状态只能引用注明时间的历史证据。

后续仍需正式 Linux 实例配置与恢复责任、STS/OIDC/私有 OSS、当前候选 PG16/Client、正式人员唯一映射、SMS-only 登录、角色/真机主链 UAT、密钥/数据库/附件恢复和回滚。真实隔离 Linux 证明的最新状态以 `deployment/openbao-pilot/linux-agent-README.md` 和其回执为准；本批没有重跑该证明。PNVS 独立 OIDC 坐标及只读投影现在有 draft 模板与失败关闭检查，但真实 issuer/RAM/投影/STS 尚未完成；不能把 OSS 显式身份或云控制台登录当成 PNVS 运行身份。
