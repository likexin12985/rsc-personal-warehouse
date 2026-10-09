# OpenBao 2.7.1 试点常驻配置与首次封存控制器

**试点 MVP，不等同完整 V1。此目录是可审查的部署候选，尚未执行正式安装、初始化、解封、云身份配置或业务启用。** 旧隔离 Agent 的 19 项、template 的 14 项证明保持原来源；它们不证明本目录的常驻配置、周期 service token 或正式恢复已通过。

## 运行坐标

| 对象 | 固定边界 |
| --- | --- |
| 官方程序 | OpenBao 2.7.1；Linux amd64 二进制 SHA256 `535cf827b13753046757f5ec8b97ae0ef21f10a40ffe673f0d5e75616170f5ea` |
| Bao / Agent 容器底座 | 已有镜像 ID `sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575`；不拉新 tag |
| 公开元数据网关 | 已有 Caddy 镜像 ID `sha256:234958d6760cef50f79875faf17fd59c4b1d1d6b84eb77f3654c424efbcaa4b5`；UID/GID `23205:23205` + socket GID `23110`；同镜像程序普通字节副本 `/opt/rsc-openbao/caddy/caddy` → `/runtime/caddy`，0555 / 无 xattrs，SHA256 `4ef1f68c70219536b2711fd16547a79841a2dec2d6b4e56b1e3e5e9da76028e6` |
| Bao | `23101:23101` + `23110`，`network=none`，只读 rootfs，cap drop ALL，no-new-privileges，无发布端口 |
| Transit Agent | `23102:23110`，仅自己的 bootstrap 和 token 目录可写 |
| OSS / PNVS Agent | `23202:23212` / `23203:23213`，各自独立 bootstrap、实体、role 和 JWT 目录 |
| API / gate | 预留 `23204:23204`；正式启用前须核实数值 UID 并接已有 overlay。API 只读三个用途目录，gate 只读 Transit；均用**实际完整 Bao 容器 ID**绑定 PID namespace，不用容器名或 host PID |
| 配置 / 程序 / 状态 | `/etc/rsc-openbao` root:root `0755`、公开配置 `0444`；`/opt/rsc-openbao/2.7.1/bao` root `0555`；`/var/lib/rsc-openbao` Bao `0700` |
| 临时投影 | `/run/rsc-openbao` root `0755`，真实宿主 tmpfs、nosuid/nodev；`socket` Bao:shared `0750` / `0660`；`token-{transit,oss,pnvs}` 各 Agent:共享组 `0750` / `0440`；bootstrap 各 Agent 私有 `0700` |
| 资源 | Bao 384 MiB、3 Agent 各 128 MiB、网关 32 MiB，硬上限合计 **800 MiB**，MemorySwap=Memory，core=0；不与新的重型 PG16 实验并发 |

UID 是此候选的配置选择，只有实际 Linux 回读才能确认它们已建立及没有冲突。`runtime_install.py` 只允许所有固定目标均不存在的首次文件安装；不覆盖、删除或改属已有目录。中断留下的部分安装需精确审查，不能自动重跑安装器。安装器设置 public 配置 `0444`，解决本地交付包 `0600` 挂给非 root 后不可读的问题；systemd 启动前重新执行来源/权限/tmpfs/镜像/空间预检。

## 配置生成、安装与启动的分界

1. 用锁定源码执行 `runtime_bundle.py --instance-id <明确实例标识> --output <全新绝对目录>`。输出只有公开配置，含每个文件的 SHA256。它不启动服务、不产生身份和密钥。实例标识必须与后续数据库 pin/registry 选择一致，不能拿示例当实际实例身份。
2. 主部署任务审查生成包、`docker compose config` 与固定 Caddy 的 `validate` 结果，再决定是否运行 `runtime_install.py --bundle ... --binary ... --caddy-binary ... --instance-id ...`。Caddy 输入只能是已核验固定镜像提取的同字节普通副本，必须无扩展属性、符合固定SHA；安装只复制字节，不继承镜像的 file capability。安装器不调用 systemctl/docker up，不初始化。已有目标、权限/来源异常或剩余资源不足都阻断。
3. 安装后由主任务明确 `daemon-reload/start/enable`。unit 仅启动 Bao，保持 sealed；Agent 和 metadata 使用独立 Compose profiles。正式 API 不随此包启动。每次 Bao 容器重建或重启使旧 release 的 ID/StartedAt/PID 绑定失效，须重新准备与验收 API/gate，不沿用旧发布回执。
4. 官方 Agent 从其私有 tmpfs 读取短期单次 SecretID，读后删除。重启后不会从普通磁盘、环境变量或默认凭据链恢复 SecretID；需要受控运维重新引导。不得把已消费 SecretID 放回文件或无限自动尝试。

源码生成时不把 `.env` 或宿主凭据透传进容器，Compose 使用 `--env-file /dev/null`。容器普通日志 driver=none，避免 init/Agent 的原始诊断落盘；如需诊断，使用单独审查的有界内存采集器，只落白名单分类和计数。**持久 audit 是另一类必要业务证据**：`log_raw=false`、0600、Bao 私有存储；本包没有伪装成已完成审计轮转/归档方案，持续磁盘空间告警、受控 reopen/轮转和恢复仍要验收。不得对正在写的 Raft 目录做复制便称一致性备份。

## 周期凭据及权限事实

`approle-contract.json` 为 20 分钟 periodic **service** token、`token_max_ttl=0`、`token_explicit_max_ttl=0`、`token_num_uses=0`；SecretID 600 秒 / 一次使用；禁止 default policy。续期由官方 Agent LifetimeWatcher 执行，失败或 token 丢失后不能使用已消耗的 SecretID 重新认证，须人工受控 bootstrap。这里只定义契约，自然续期新专项的真实证据另记，未沿用旧 batch token 证明。

每个用途仅有自己的业务路径，以及精确的 `auth/token/lookup-self:read`、`auth/token/renew-self:update`。Transit 业务路径恰为两个现有 decrypt；OSS/PNVS 仅各自 `identity/oidc/token/rsc-<purpose>:read`。没有 token create、SecretID create、role、policy、key 配置、通配或 root 权限。旧 `api-decrypt-policy.json.example` 和旧终态测试完全保留；本目录是明确的新周期策略。

**Transit file sink 投影的是 Agent 自己的 auto-auth token，因此 API 也能够 lookup/renew 同一 token。** 只读文件挂载不能消除这项能力，不声称续期权限只属于 Agent。OSS/PNVS 的 Bao token 只在 Agent 内存中，不投给 API，API 只读各自短 JWT。三个角色有效 policy 合集及额外 group policy 仍须正式只读回查，不能只看本地 JSON。

OSS/PNVS 的官方 template 使用 120 秒静态刷新间隔，部署时 OIDC role 的 JWT TTL 应为 600 秒，并核对不同 subject、audience、role、token 目录；可共用受控 issuer/provider。环境声明须分别填 `RSC_OSS_OIDC_WRITER=openbao_agent_template_v1` 和 `RSC_PNVS_OIDC_WRITER=openbao_agent_template_v1`。已有 reader 新守卫负责有界等待临时文件；之前 Linux 证明只实测最终 0440 和原子 rename，**瞬时 0600 未采到**，不把源码契约混写为现场观察。

## 两个公开 GET

拟定 issuer origin 为 `https://rscwz.cn`，实际 issuer 为 `https://rscwz.cn/v1/identity/oidc`。最终须由现有 HTTPS/TLS 配置与 RAM 公钥读取验收确认。

独立网关只处理：

- `GET /v1/identity/oidc/.well-known/openid-configuration`
- `GET /v1/identity/oidc/.well-known/keys`

query 非空拒绝；匹配后重写为固定路径；客户端请求头全部删除，只设置内部 Host。其他路径、方法返回 404，内部管理/签发接口不公开。网关的 UDS 父目录只读，网络是 `rsc-openbao-metadata` internal，8080 仅容器网络，无 host port；主任务须将既有 web 精确接入该内部网络，并仅将这两个 GET 反代到网关。现有 web 的 root 首页、`/xx` 与业务 API 保持各自职责；本候选没有修改现有入口。

这份 Caddyfile 仍需固定镜像的真实 `validate` 与两正向/方法/query/管理路径拒绝检查；文字审查与本地字符串测试不替代 Caddy 的解析及 HTTPS 回读。metadata 公网可用也不代表 RAM/STS 兑换或附件/短信权限通过。

## 正式 init 与封存草案

`runtime_init.py` 只提供 `prepare-vault`、`status`、`initialize`、`recover`，**没有 unseal/configure 接口**。后续必须以正式封存成功、独立复核和数据库/密钥选择为前提实施解封/配置。它不读取密码、自动 mount image、调用 Keychain、安装依赖或把份额/root token放到命令行。

已选单人、单个希捷移动硬盘；Shamir **1/1** 如实表达这一安排，不声称双人或第二份容灾。固定外盘 UUID `4FBD5E6D-037F-4DE4-BE21-75A186DB7CDC`，image 为 `/Volumes/Seagate Backup Plus Drive/RSC-recovery-7ab884150e2e/RSC-recovery.dmg`，内部卷 UUID `67A771D2-90A4-3F23-B837-F2EB70BFC7F8`。

1. image **关闭时**运行 `prepare-vault --vault-binding <新的非密回执文件>`，以原生 hdiutil 核验 encrypted header，并记录 exact file/device/inode/birthtime/size、closed hash 和外盘身份。这个动作只读 image，不生成 key。历史实测 mounted image 上 `hdiutil isencrypted` 可报 resource temporarily unavailable，因此不能在挂载后把该接口当准入条件。
2. 用户通过原生工具将同一 image **RW** 挂载。控制器核对 hdiutil 的 exact image→mount 关系、两卷 UUID、USB 外置可写、实际 mount/ST_RDONLY、inode/device 与先前绑定。没有 `/Volumes` 本地替代目录、自动 attach 或全盘操作。
3. 用现存 `/tmp/rsc-openbao-verifier-20261008/bin/python`（PGPy 0.6.0）执行 initialize，提供已核验 SSH alias/config、精确 64 位 Bao 容器 ID、`--instance-id`、显式 12 位 run ID、RW mount 和 pre-attachment binding。程序先只读检查固定 image/binary/helper/server-config SHA、五个精确 bind 来源与读写性、命令、无凭据环境、进程隔离及启动身份，再查 `sys/init`。初始化不允许已经存在 attempt marker 或 initialized=true。
4. 仅在上述加密卷的新 `initialization-<run>` 子目录生成一次性 RSA3072 PGP 私钥，并用 `O_EXCL`、0600、fsync、同文件读回确认；公开 fingerprint 绑定 run 与 container。**该私钥不加第二层口令，依赖已核验的 AES256 image，仅存在其内部；不得移到普通盘。** macOS 无秘密探测显示 mlockall 为 ENOSYS，故只实施 core=0，明确 Python 堆未全锁页，不宣称安全内存保证。
5. 原生 OpenBao `sys/init` 同时指定 `pgp_keys=[公开key]` 和 `root_token_pgp_key=同一公开key`，1/1。Bao 将份额 hex 编码后 PGP 加密，root token 同样 PGP 加密；本地不自造 Bao 密钥。远端 helper 先独占写无秘密 attempt marker，再发送一次 init；成功后只把 **PGP 加密响应**写入私有 `/state/initialization-ciphertext.json` 并 fsync，随后经私有 SSH subprocess 管道返回。
6. 本地将该密文 O_EXCL/fsync/readback 封存到同一 image，并在内存中用刚刚回读的 PGP 私钥解密验证份额/root 形状；不输出它们或其 hash。再次查询正式 initialized=true/sealed=true，回执只保留公开 fingerprint、状态及布尔验证结果。此时也不能写成备份恢复/UAT/生产就绪通过。
7. SSH 超时/中断后先 status。初始化写请求**不重放**；若远端已封存密文，可用同 run/container/fingerprint 的 recover 只读取回已有结果。如果 init 已完成但密文响应因服务/宿主崩溃未被保存，保持 unknown/阻断，不能删除状态、自动重新初始化或假称恢复成功。

原生字段及编码直接核对固定上游：[sys_init.go](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/internal/http/sys_init.go)、[init.go](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/internal/vault/init.go)、[barrier/aes_gcm.go](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/internal/vault/barrier/aes_gcm.go)。三源已存 `artifacts/openbao-runtime-20261009/upstream/`，Git blob 均与固定 commit 的树匹配。`sys_init.go:119–125` 的 recovery 两字段是 `omitempty`，只有实际 recovery shares 非空才赋值，当前 Shamir 的响应闭集因此是 `keys/keys_base64/root_token`。`init.go:406–413` 的 `root_token` 在 PGP 模式中是加密消息 base64，不能把字段名字当作明文依据。`aes_gcm.go:338–344` 生成 `2*aes.BlockSize`（32）字节；`init.go:120–143` 的 shares=1 直接取该 key，再 hex 后加密，因此解密是 64 字符 hex。真实正式 init 与封存/关闭/重开/解封恢复仍各自待验。

## 本地验证及待补证据

`artifacts/openbao-runtime-20261009/local-01/` 记录 **25 个新增聚焦检查通过（2.012 秒）**，含一次仅内存的合成 PGPy RSA3072 公钥指纹/加解密验证。其余是纯配置和 mock：来源绑定、独立目录、权限集合、已初始化/已尝试拒绝、PGP share/root 同公钥、未知结果不重试、密文与纯文本拒绝、恢复只读、文件独占及错误输出形状。未运行旧 14/19 或任何业务套件，未 remote、未生成正式 key、未 attach image。新增后续测试单独记录，不合并成旧测试重跑。

`local-02/` 另记录 **7 个新增协议检查通过（wrapper 0.522 秒，测试 0.140 秒）**，含挂载后不再调用 isencrypted、UUID/挂载关系/写保护/原 image 变化拒绝、安装公开文件 0444 与独占写、root UID/GID/模式/hash、init peer 的精确挂载/命令/资源/来源，以及错误反例。本轮仅执行 `test_runtime_native_protocol.py`，没有重跑前 25 个；两次回执各自保存当时的源 hash。最后加强的 peer 与 public-file 检查属于第二批测试来源，不将第一批来源冒充最终版本的全套复验。原生 diskutil/hdiutil/docker 在此批均为协议 mock，实际解析/挂载/服务器状态仍由部署任务另验。

首次真实 `prepare-vault` 在原生外盘/加密 header 验证之后失败：固定 PGPy 解释器是 Python 3.9.6，没有 3.11 才加入的 `hashlib.file_digest`；未生成 binding、正式密钥或执行挂载/远端写。最小修复只把 SHA256 读取改为有界分块，`local-03/` 单独新增 1 个测试在该固定 Python 3.9 上通过（wrapper 0.358 秒，明确断言无 file_digest，使用公开合成文件；native mock）。旧 freeze-01 和对应修改前源码保留，freeze-02 绑定修复，旧 25/7 不重跑。真实 closed binding 后续须重新执行并另记结果，不能把这个 mock 当真实盘验收。

首次固定镜像 Caddy validate 因 `/usr/bin/caddy` 的 `cap_net_bind_service=ep` 与非root/cap-drop ALL 组合在 exec 时 EPERM，临时容器已由主任务清理、没有正式启动。修复保留同镜像/UID/cap-drop ALL，只绑定固定SHA普通字节副本，并对源与安装文件的 xattrs 做空集合守卫；不增加 NET_BIND_SERVICE。`caddy-fix-01/02` 的3个新增节点最终通过：首次1节点通过、另外2个因Mac Python不含Linux专用os.listxattr而测试fixture失败，修正为显式Linux协议mock后仅重跑该2节点通过（0.176秒）；旧测试不重跑，真实Caddy启动仍须独立回执。原 freeze-02 对应4份修改前源码已归档。

首次正式文件安装在任何固定目标创建前被 `source_bundle_entries` 阻断：Python 默认导入在 stage bundle 写入 `__pycache__`，触发精确条目集合守卫。保留原失败和 freeze-03 源；安装器/preflight 在导入本地模块前设置 `sys.dont_write_bytecode=True`，systemd preflight 加 `-B`。`bytecode-fix-01/` 新1节点使用固定Python3.9真实子进程分别执行两个入口 `--help`，刻意不带 `-B` 或环境兜底，验证目录仍只有原3个公开模块（0.678秒）；不运行实际安装/daemon，也不重跑旧节点。失败后须精确读回所有固定目标，确认未创建后才可由主任务决定使用新冻结继续。

启用仍需：本候选的真实 Compose/Caddy 解析、host 安装与 namespace/UID/挂载回读、20 分钟 periodic 专项、正式 init 封存和恢复验证、精确有效 ACL/实体/发行者/keys/DB registry/pin、RAM/STS/私有 OSS、当前候选 hosted PG16、真实 SMS-only 登录和 MVP 主链 UAT。不要用本目录的配置/预检替代这些证据。
