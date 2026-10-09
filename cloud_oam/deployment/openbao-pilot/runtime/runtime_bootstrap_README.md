# D：核心三个 Agent 的一次性首次凭据交付

状态：本地产物和新增检查，不是正式凭据签发、Agent 启动或上线通过。仅 `transit`、`oss`、`pnvs`，不复用这些身份执行 backup writer/reader/recovery。

前置条件：固定 Bao 容器的来源、挂载、UID、PID、资源边界通过 `runtime_init.Remote.preflight`；A 已首次解封，B auth/OIDC 精确回读已完成；原加密映像已由用户挂载 RW，init runId、PGP fingerprint 和 Bao 完整 ID 仍对应同一次初始化。根任务统一协调，不在此文档存真实 root token、SecretID 或恢复口令。

## 明确分步执行

1. 为每个 purpose 选择一个新的公开 12 位十六进制 `attempt-id`，创建本地真实规范绝对路径的 0700 journal 目录。`--run-id` 是原初始化 ID，不能与操作 attempt 混淆。只读核验该用途 bootstrap 目录为空、UID/GID/0700、实际 tmpfs、nosuid/nodev。
2. `runtime_bootstrap.py prepare` 先 O_EXCL 写公开 prepare intent，再 `docker create` 固定镜像的 stopped writer；立即 O_EXCL 记录 Docker 返回的完整 ID，然后 exact inspect。该容器只有本用途 bootstrap RW bind；无 Bao socket、state、网络或日志，rootfs RO、cap-drop ALL、NNP、独立 UID/GID、32 MiB/no swap、32 PID、0.25 CPU、core 0。没有创建 SecretID。
3. 独立检查本地 `prepared.json` 后才调用 `issue`。它要求同一 source、同一完整 writer ID、仍是 created/未运行、同一精确目录仍空；从已验证的加密映像在内存取 root。远端只允许本用途精确策略/角色/实体/alias 回读和该用途 SecretID 路由；不允许 login、renew、token-create、Transit 或其他用途路径。
4. 远端先 O_EXCL/fsync `/state/bootstrap-<purpose>-attempt.json`，才调用一次 SecretID create（600 秒、1 次使用、固定 purpose/instance/attempt metadata）。根 token 与 SecretID 仅在私有 stdin/stdout pipe/内存；不会写本地 journal、argv、env 或普通磁盘。非认证凭据的 accessor 仅保存于 Bao 私有 state 0600 文件，用于精确只读回查，不输出到公开回执。
5. 同一 accessor 回读通过后，把 roleId/SecretID 私有 pipe 交给已检查的 writer。writer 内部再次验证实际 Linux UID/GID、cap/NNP/seccomp、rootfs RO、32 MiB/no swap、tmpfs 和目录元数据，然后 O_EXCL 创建两个 0600 单链接文件，逐文件 fsync、同 FD 读回、目录 fsync。局部写入不覆盖、不清空、不补写。
6. 只在 writer exit 0、完整 ID/隔离再次检查、Bao 和加密映像身份仍一致时，写公开 `delivered.json`。该回执明确 `agentStarted=false`、`productionReady=false`。协调任务随后显式启动对应官方 Agent，并在 SecretID 的 600 秒有效期内完成真实验证。

命令共同参数：`--ssh-config`、`--ssh-host`、必要时 `--ssh-sudo`、`--container-id`（完整 Bao ID）、`--instance-id`、`--run-id`、`--vault-mount`、`--vault-binding`、`--purpose`、`--attempt-id`、`--journal`。不接收命令行口令/Token/SecretID。

## 中断与回查

`prepare` 的 create 响应丢失时，先按公开 prepare intent 中的唯一 name 精确 inspect；不能重新 create。已保存完整 ID 时只读该 ID。任何缺失/不一致保持 unknown，禁止拿任意同名容器代替原绑定。当前 helper 不自动收养未知结果、删除容器或清理凭据。

`issue` 本地 intent 或远端 marker 已存在即禁止再次签发。`runtime_bootstrap.py status` 只回查既有 marker/accessor，输出 `not_issued`、`unknown`、`present` 或 `absent`；404 仅证明 exact accessor 不再存在，**不能单独区分已经消费与 TTL 到期**，也不证明 Agent 登录成功。SSH 超时、JSON 解析错误、writer 局部写入均保持 unknown，不自动重放。

正式 Agent 的通过证据需要另行核对：真实容器 ID/Image/StartedAt/PID、独立 UID、cap/NNP、RO source 挂载；SecretID 文件已消费；相同 entity 的成功 AppRole login 审计；token/投影真实文件元数据；有效期与自然续期；API 的真实来源/PID namespace/SO_PEERCRED；OIDC issuer/JWKS、云 STS 和具体最小权限。不能用 `systemctl is-active` 代替这些检查，现 unit 是 oneshot/RemainAfterExit，容器 restart=no。

首次操作后，运行 token 丢失、机器重启、重新 seal 或消费后的 SecretID 不支持自动再认证。后续恢复必须设计独立操作 ID、已有 marker 的精确状态回读、明确撤销/过期证明和新的受控 bootstrap；**不删除本轮 marker、不重复原 init/unseal runId、不启动无限重试登录**。

## 本轮验证边界

`bootstrap-writer-local-01` 的 5 个新 unittest 检查真实本地 O_EXCL/0600/单链接/不覆盖和仅元数据状态；不是 Linux UID 实测。`bootstrap-local-01` 新 controller/remote 10 节点首轮 9 通过、1 个测试临时目录因 macOS `/var` symlink 被正确拒绝；原失败保留。测试用 `.resolve()` 修正，`bootstrap-local-02` 仅复验该 1 节点和新增的容器身份/隔离 1 节点，2 通过。总计 16 个不同新增节点最新均通过，17 次执行；未重跑已终态的旧 14/19/periodic 或 hosted PG16 套件。

尚未在 Linux 执行 D；尚未生成正式 SecretID；本机 Python 堆未全量锁页，不宣称 secure-memory 证明。现存所有 frozen runtime/configure/unseal 输入保持不变。本目录可能受 `runtime/` ignore 影响，提交时仅精确加入本 README、三个 `runtime_bootstrap*.py` 与两个对应新测试；不加入 journal、生成 bundle、普通 artifacts 或任何真实秘密。
