# Linux Agent、独立身份和 API 只读挂载证明

**试点 MVP，不等同完整 V1；该证明不是生产部署，也不改变 `not_ready`。**

这批新增文件只用于把已经完成的官方 OpenBao Agent 2.7.1 本机行为证明推进到真实 Linux 内核、容器身份和只读目录挂载。无真实业务/数据库/云账号配置，不运行现有终态套件，不更新生产 ACL，不初始化正式密钥，不操作现有服务。所有对象都在本次随机名称的 Docker 容器与 tmpfs 卷内创建，结束时删除；失败结果保留，不自动重试整个实验。

## 输入与运行

使用服务器上已经存在且明确核验的完整镜像 ID，`--pull=never`；镜像需 Linux AMD64、包含 `python`，没有声明隐式 volume 或云凭据环境变量。OpenBao 二进制必须与仓库已签名核验的官方 2.7.1 Linux AMD64 SHA-256 完全一致：

`535cf827b13753046757f5ec8b97ae0ef21f10a40ffe673f0d5e75616170f5ea`

上传包保留以下相对路径即可，不上传整仓库、`.env` 或其他凭据：

```text
cloud_oam/deployment/openbao-pilot/linux-agent-proof.py
cloud_oam/deployment/openbao-pilot/linux-agent-worker.py
cloud_oam/deployment/openbao-pilot/linux-agent-server.json
cloud_oam/deployment/openbao-pilot/agent-autoauth.json.example
cloud_oam/deployment/openbao-pilot/api-decrypt-policy.json.example
cloud_oam/deployment/openbao-pilot/official_release_2.7.1.json
cloud_oam/backend/app/openbao_runtime_transport.py
cloud_oam/backend/app/openbao_transit_candidate.py
```

在服务器执行一次；输出只包含布尔检查、内核证据、源码/二进制摘要、固定错误码。不要记录 Docker exec 的内部 stdin/stdout，它包含本次合成实例的临时管理材料。

```sh
python3 /absolute/upload-root/cloud_oam/deployment/openbao-pilot/linux-agent-proof.py \
  --source-root /absolute/upload-root \
  --binary /absolute/reviewed-openbao-2.7.1/bao \
  --run-id NEW_12_LOWERCASE_HEX \
  --receipt-journal /absolute/evidence/attempt-01-journal.json \
  --image sha256:EXACT_ALREADY_VERIFIED_LOCAL_IMAGE_ID
```

原始 stdout 应保存到新的 attempt 回执，不覆盖之前的失败。每次使用新的明确 run-id 和 journal 路径；journal 已存在则拒绝启动。非密 journal 保存计划名称、实际容器 ID、卷名称、阶段和源码摘要，便于 SSH 中断后精确回读，禁止盲目重放。单次总运行预算 160 秒，正常约一分钟；控制器收到中断或失败后仍执行精确容器/卷清理。清理校验 run-id label 和对象 ID，`docker ps -a` / `docker volume ls` 命令成功且确切对象为空才算清理完成，不把任意 inspect 失败当作不存在。不会执行 `docker system prune` 或处理别的名称。Docker 端点固定本机 `/var/run/docker.sock`，容器内不挂载 Docker socket。

## 真实身份和挂载

| 进程 | UID / 主 GID | socket 目录 | token 目录 | bootstrap 目录 | Raft 数据 |
| --- | --- | --- | --- | --- | --- |
| Bao | 23101 / 23101，附加 23110 | RW，owner 23101:23110 / 0750 | 不挂载 | 不挂载 | 私有 tmpfs / 0700 |
| 官方 Agent | 23102 / 23110 | RO | RW，owner 23102:23110 / 0750 | RW，owner 23102:23110 / 0700 | 不挂载 |
| API 真实 transport | 23103 / 23103，附加 23110 | RO | **整个目录 RO** | **不挂载** | **不挂载** |

socket 为 `0660`，令牌为 `0440`。实际所有权、模式、API `fstatvfs(ST_RDONLY)`、mountinfo 的 tmpfs/RO、文件写入拒绝、服务主进程 `/proc/1/status` 和 `SO_PEERCRED` 均由程序读取内核证据。没有把声明的环境变量 UID 当作已验证身份。

API 使用 `--pid=container:<本次 Bao 容器>`，以满足当前严格 transport 的 `SO_PEERCRED` peer PID > 0 约束。两个 sibling PID namespace 会使 Linux 返回 PID 0，不能为方便容器隔离而修改/放宽 transport。这里只共享 PID namespace；API 的网络、挂载、cgroup、UID 仍独立，capabilities 为空且禁止提权；真实尝试打开 peer 进程的 `/proc/<pid>/environ` 必须被拒绝，不读取或输出其内容。正式部署应保持同一或可见的受控 PID namespace，或者单独审核 transport 契约变更；不能仅抄一份 Compose 环境变量就宣布成功。

短暂 keeper 进程只负责创建本次 tmpfs 目录的 owner/mode，并维持 tmpfs 生命周期。它仅有 `CHOWN/SETUID/SETGID`，设置完成后立即降为 65534；其他三个服务均 `cap-drop=ALL`、`no-new-privileges`、默认 seccomp、禁止 core。初始化材料通过 stdin 投递给 Agent UID，SecretID 被官方 Agent 消费后删除。容器日志关闭，root token/解封份额只保留控制器内存；敏感值不会进入回执或宿主持久文件。

## 资源与真实验证

- 全部容器 `network=none`、无发布端口、rootfs 只读。Bao 内部 Raft 所需的 loopback 监听不对主机发布。
- 硬内存上限合计 **608 MiB**：Bao 384、Agent 128、API 64、keeper 32；swap 全为 0。另有每容器 CPU/PID 上限，回读 cgroup；没有将 `GOMEMLIMIT` 当作硬限制。
- socket/token/bootstrap 各为 1 MiB 的 Docker tmpfs 卷，Bao Raft 私有 tmpfs 128 MiB，每容器临时目录 8 MiB。实际内存归各自 cgroup；没有真实存量数据。
- 官方 Agent 通过原 AppRole/file-sink 配置自动投递 30 秒的非续租 batch token。API 不得到 renew-self/default/root 权限；角色配置独立精确回读。
- 同一个真实 `OpenBaoUnixDecryptTransport` 实例完成两用途真实解密，看到 Agent 自然更新令牌和 inode 后再次解密，证明目录挂载支持原子替换，代码没有缓存旧 token。
- 实际 seal 后两用途返回 503；手动两份解封恢复后同实例再次成功；停止 Agent 并等自然到期后两用途 403。失败状态均来自真实服务，无 mock UID、伪装只读标志或替代 transport。
- 源码、官方配置和二进制运行前后摘要相同；停止/删除精确四个容器与三个 tmpfs 卷并回读不存在，才算清理完成。

此证明仍不等于正式服务已经长期配置。正式 bootstrap 补发/到期、真实密钥登记和恢复保管、OSS/STS、当前候选 PG16、短信与 UAT 各有独立门禁，不能用这个合成 Linux 证明替代。不得把短时实验 UID/TTL 自动写入正式环境。

## 2026-10-09 真实 Linux 终态

在已确认的 Linux x86_64 目标服务器上仅执行一次 `attempt-01`，run-id `4beaeb8d75fb`：**exit 0、19 项实际行为检查通过、耗时 90.052 秒、`cleanupCompleted=true`、`sourceInputsStable=true`**。没有因成功后继续重跑；这 19 项是同一次新隔离证明中的检查点，不是 19 个 pytest 用例，也不与旧套件累加。

证据：[proof.json](../../artifacts/linux-agent-runtime-20261009/attempt-01/proof.json)、[精确回读与原服务对照](../../artifacts/linux-agent-runtime-20261009/attempt-01/readback.json)。

实际结果包含三独立非 root UID、服务主进程身份、只读 token/socket 目录、bootstrap/Raft 不挂载、API 不能打开 peer environ，以及同一个真实 transport 的两用途状态顺序：初次 `200/200` → Agent 自然换 inode 后 `200/200` → 实际 seal `503/503` → 手动解封正向 `200/200` → Agent 停止并自然过期 `403/403`。四个容器均未 OOM；回执记录所有硬资源限制、mount 与容器 ID；准确清理四个本次容器和三个 tmpfs 卷。原有六个运行服务的 ID、running 和 health 在前后回读中一致。

源码绑定：`linux-agent-proof.py` SHA-256 `a6f4f936301332d3f407b7cfb0eae1ac859ce2dd577e95632ddb8d1c4b6721d0`；`linux-agent-worker.py` `e7527ba01f7a83103daee632bc451be8946d608a0d6f3f7a513a781ea9069dfc`。使用已存在固定镜像 `sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575` 与上述官方 Bao 二进制，未拉取镜像或改旧源码。

这解决的是 **真实 Linux 身份、目录挂载和官方 Agent 与现有 transport 的兼容证据**。全部合成凭据已随隔离环境清理；`productionConfigured=false`、`productionReady=false`，没有将本次临时 Agent 宣称为已正式启用。
