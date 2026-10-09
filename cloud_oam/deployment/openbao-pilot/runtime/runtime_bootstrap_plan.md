# 正式初始化之后的分阶段引导

试点 MVP，不等同完整 V1。以下阶段必须分别取得精确现场回执；当前只交付阶段 A 的可审查工具，B/C/D 仍在准备，不宣称已配置或上线。既有 init freeze-02 不因本文件而变更。

1. **A：封存读取与解封**。`runtime_unseal.py` 复用 init 的实际 RW 加密映像、公开绑定、同 run/container/fingerprint、PGP 私钥及已封存密文；只在内存验证并取出份额。固定 public helper 源通过 `python3 -c` 执行，份额只进入 SSH/docker 私有 stdin 管道。先只读 seal-status，精确 Shamir/2.7.1/1-of-1/initialized；已解封时只回读，部分进度或原 sealed 尝试 marker 阻断。首次写之前独占/fsync 公开 marker，单次 PUT unseal 后重新 GET。超时未知不重试、不删除 marker，回到 init status 只读核对。没有配置/root-token传递、Agent 启动或备份恢复完成声明。
2. **B：逐对象配置与验收**。计划按 `auth`、`transit-keys`、`oidc` 分命令。每个对象先 GET，只允许 absent→create 或完整相等→verify；不匹配阻断，不覆盖默认或陌生现状。公开逐对象 journal 先记 pending，未知后只读精确对象；对象存在且契约完整相等才允许和解。三用途 ACL/AppRole/entity/alias 独立；OIDC key/role/config 必须按固定版本实际字段归一后精确比较，issuer/audience 属公开输入但不能使用示例冒充云配置。root token 从已封存 PGP 在内存解密，经私管道传递，不进入 argv、环境或普通日志。正在核对固定上游 entity/alias/OIDC 路由来源，尚未生成执行器。
3. **C：Transit wrapped DEK、registry 与独立 DB pin**。沿用现有 `OpenBaoKeyCoordinate`、`context_b64`、`associated_data_b64`、`OpenBaoWrappedKey` 和 registry schema，不另造序列化协议。两个 key 固定 derived AES256-GCM96，禁止导出/明文备份/删除/收敛加密。仅请求 wrapped datakey，不请求 plaintext。任何非幂等生成未知立即停止；收到的包装密文先封存，不因丢响应重复生成。DB pin 需独立受控写与原库真实读回、历史引用核验；不能把 registry 中自己计算的值当独立 pin 证明。静态运行 registry 拟独立 `/etc/rsc-openbao-registry`，API UID `23204:23204` 目录 0700 / 文件0600，避免污染 `/etc/rsc-openbao` 的公开 bundle 精确集合。此阶段尚未实现或运行。
4. **D：一次性 bootstrap 与官方 Agent**。每个用途独立 UID/私有 tmpfs；先验证目录身份、空状态与正式 role/entity。单次 SecretID TTL600/uses1，收到后只在私管道内转交对应 UID，0600/O_EXCL 写 role-id 与 secret-id；不向 Bao/其他用途/API 授予该目录权限。创建或写入未知不重新发 SecretID，先精确原 accessor/文件状态回读。Agent 启动由主任务按 frozen compose 单独执行；自然续期须有新20分钟专项证据，首次实际登录及 policy合集、secret消费、最终投影/JWKS/subject/audience/只读挂载仍须现场核验。

阶段 A 新增7个 mock检查见 `artifacts/openbao-runtime-20261009/unseal-local-01/`（0.391秒），没有真实解封、服务器调用、加密映像访问或旧套件重跑。阶段A不把“已解封”与“正式恢复演练通过”混为一谈。正式备份/隔离恢复、hosted PG16、云STS/OSS、短信及MVP主链UAT仍独立验收。
