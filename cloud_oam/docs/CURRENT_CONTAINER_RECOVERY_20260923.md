# 当前候选八角色容器联合恢复证据

2026-09-23。在现有 `06f6/oam` 工作树、`codex/notification-delivery-worker` 分支上，
用第四轮静态清单的 1,091 个源码/门禁输入冻结当前候选。准确 API 镜像为
`rsc-formal-api-current:20260923-daily-ops-65d43a2e4251`，配置摘要
`sha256:ad19cde65430830f293d0de4e1450ccf0b8515e0068d6ff3a7d74325951f628b`；
[镜像构建回执](../artifacts/api-image-current-20260923-daily-ops/api-build-result.json)
确认 331 个镜像内 COPY 输入一致、`pip check` 通过、包服务及隔离 VM 停止。

新目录 [联合恢复结果](../artifacts/current-container-recovery-20260923-daily-ops-attempt2/result.json)
终端状态 `passed`，实际 Compose、私有 Docker、当前 API 镜像及 PostgreSQL 16 容器运行。
源库按正式 0134 迁移及八角色配置启动：7 个 LOGIN 密码正确登录，错误密码拒绝，
`star_oam_edge` 保持 NOLOGIN；API 实际以 `star_oam_api` 登录。正常联合备份通过，
其对象包经独立校验后恢复到**另一个新数据库实例**，恢复库拒绝源库旧密码，
恢复后再次核对角色、表和数据。源库与恢复库均为 212 表、584 行；实例标识分别为
`7688599879088672782` 和 `7688600767659880462`。

五个实际容器场景均有独立终态：正常备份退出 0；数据库真实阻塞后的期限退出 124；
取消退出 143；杀死父 worker 退出 -9；损坏对象退出 1 并拒绝。期限场景在退出前有
[注入屏障](../artifacts/current-container-recovery-20260923-daily-ops-attempt2/guest-evidence.tar)
和实际数据库观察：3 个备份会话、269 个关系锁；场景完成后五轮均为 0 遗留会话、
0 遗留关系锁，临时输出与 worker 清除，正常业务容器身份保持。最终所有 Compose 容器、
私有 Docker 引擎、源/恢复 PG 与隔离 VM 停止，清理错误列表为空。原始逐场景文件位于
同目录 `guest-evidence.tar`；外层与包内摘要由 `result.json` 绑定。

这是**本地合成联合恢复通过**，不代表真实 OSS 或生产 RPO/RTO 验收。源样本只有 3 个
available 附件对象和 1 个 pending 意图；六张日终表为空，尚未验证非空映射、截止、
解释、审核和请求封存的恢复。目标 Linux、真实私有 OSS、KMS、实际业务数据和灾备演练
仍需分别取证。首轮包装在 VM 启动前因静态清单不含 `.dockerignore`、`Dockerfile` 和
`formal_file_integrity.py` 停止；这些文件由镜像构建回执另行逐字节核验，失败目录
`current-container-recovery-20260923-daily-ops/` 保留，未覆盖或抹除原证据。

后续已补[非空日终事实的当前容器联合恢复](NONEMPTY_DAILY_CONTAINER_RECOVERY_20260923.md)：
同一准确镜像在另一个新源库/恢复库上保留了 1 条映射决议和 3 条截止快照，
212 表 / 746 行及 4 个可用对象逐项一致。此段原有 584 行空日终样本仍保留为独立历史证据。
最新[六张非空日终表恢复](FULL_DAILY_CONTAINER_RECOVERY_20260923.md)又将完整审核事件、
消费、绑定和请求封存纳入 212 表 / 788 行的第二新库逐表核验。

完整静态三分片、GitHub PG16 runtime/Client、真实 OAM 来源与身份/期初、业务 UAT、
连续三天对账及正式上线仍未放行；本批未提交、推送或部署。
