# 日终映射与截止一次性运维入口：当前候选证据

2026-09-23。按[入口设计](DAILY_RECONCILIATION_OPS_CLI_DESIGN_20260922.md)新增镜像内
`app.daily_reconciliation.ops_cli`，固定六种操作：`mapping-preview`、`mapping-apply`、
`mapping-status`、`cutoff-preview`、`cutoff-capture`、`cutoff-recover`。这是本地候选，
不是生产上线或真实业务批准的证据。

入口只接收不含凭据的 64 KiB 内 JSON 命令文件；重复键、非有限数、未知字段、伪造身份、
无效版本与摘要在建连前拒绝。当前 Web access token 从隐藏终端输入或继承的独立 FD 读取。
三种数据库连接只从独立只读 conninfo 文件读取；CLI 不接受 DSN、SQL、角色、用户 ID、
快照数量或重试次数。映射使用直接 owner 连接；截止使用 owner、控制源、账本三连接。
子进程拥有连接和事务，父进程提供 1–60 秒总期限与最多 1 秒回收等待。写入回执丢失按
结果未知处理，必须拿同一命令、同一审核摘要（映射）精确回读，不自动补跑。

`cutoff-preview` 只核对真实身份、精确发布/映射坐标、当前来源权限、日期与 300 秒源时效、
PG16、迁移 HEAD 和采集角色目录；不生成账本游标、截止或“可开始”结论。映射及截止预览
结束时回滚读取与锁事务。截止写入继续经过原有三连接捕获、库存头游标、历史证明、审计和
COMMIT 守卫；回读 `recorded=false` 的退出码为 4，不能视作安全重试许可。镜像内 Compose
`daily-reconciliation-ops` 是 opt-in、无对外端口、只读根目录、`restart: "no"`、专用内部
数据库网络；默认命令只有 `--help`。部署时只对一次性 `docker compose run` 显式添加
三份 conninfo 和命令文件的只读 bind mount；Compose 默认服务不挂载这些文件，避免普通
`prepare/start` 依赖尚不存在的日终运维凭据。API、同步、通知服务不能访问这些挂载。

目标 Linux 的一次性操作必须使用已验镜像及 `docker compose run --rm --no-deps`：
`--no-deps` 阻止只读预检意外启动 `migrate` 依赖。先用 `docker compose config` 核对
`daily-reconciliation-ops` 仅能连接内部数据库网，再在目标机准备绝对路径的命令文件及
所需 conninfo 文件，全部只读挂载。连接文件应为 UID 65532 可读的私有普通文件，不能
让其他用户或容器组可读；命令文件不得包含令牌/DSN。交互终端只输入当前网页凭据，
不得放在命令行、环境变量、日志或持久卷。映射预览只需 owner 文件，截止预览/捕获/恢复
需要 owner、source、ledger 三份文件；每个操作仍需独立精确文件绑定。先预览并人工核对
摘要与授权版本，再按准确命令执行写入；写入退出 3 必须仅用原命令做 `mapping-status`
或 `cutoff-recover` 回读，不自动重放。退出 4 表示未记录，也不自动重试。此目标机流程
尚未实测，不能照此文字推定已具备生产执行许可。

本轮验证：

- `backend/tests/test_daily_ops_cli.py` 连同日终桥接、采集安全专项共 **78 passed**；
  扩大至全部 `test_daily_*.py` 后 **403 passed、15 subtests passed、1 条第三方弃用警告**；
  同时通过 `compileall`。覆盖命令/凭据隔离、重复键、错误角色/目标、超限、未知结果不重试、
  原坐标未记录返回等。修正默认运维挂载后，联同试点部署绑定/预检/部署专项
  **561 passed、15 subtests passed、1 条第三方弃用警告**。三组运行包含重复测试，不相加。
- 全新自有 PG **16.15** 空库从 Alembic 升至 `20261113_0134`，验证 API 安全目录并新建
  两名最小权限采集角色；由受监督入口实际完成映射预览、提交、回读及截止预览、捕获、回读。
  映射预览和截止预览前后决定/截止、审计、库存交易计数不变；结果为 **1 条映射决定、
  1 条映射审计、3 个合成截止**。证据：
  `artifacts/local-daily-ops-pg16/checks/run-dz84p8uf/checks.json`、
  `cluster-state.json`。实例退出 0 且已停止。早期候选 `run-rk_0p03z`、`run-drxkxkby`、
  `run-nw7jgb4m`、`run-cihp57f7` 保留；最新轮额外核对源/账本连接实际
  `current_user/session_user`，并通过 CLI 外层实际 PG 映射精确回读、截止精确恢复。
- 在另一全新自有 PG16.15 空库升至同一 HEAD 后，独立 `python -m app.daily_reconciliation.ops_cli`
  子进程通过继承的私有 FD 完成映射状态与截止恢复；错误审核摘要、错误授权版本、
  错误采集角色、秘密文件权限过宽四项均拒绝，决定、截止、审计与库存交易计数不变。
  证据为 `artifacts/local-daily-ops-pg16/checks/run-yvemf3xx/checks.json`、
  `cluster-state.json`，退出 0 且实例停止。首轮 `run-myeq1cp1` 因验证夹具错误继承
  测试启动配置而失败并停止；只修正隔离产物夹具，正式源码与原失败证据均保留。
- Compose YAML 解析和服务/网络形态本地检查通过。当前本机 shell 没有 `docker` 命令；
  隔离 Linux VM 的 Compose **5.5.1** 已用合成非秘密变量对当前 Compose 文件执行
  `config --format json`，8 服务解析通过。日终运维服务无默认秘密文件挂载和对外端口，
  只连接专用内部数据库网，以 UID 65532、只读根目录运行，数据库服务也在该网。
  证据 `artifacts/api-image-current-20260923-daily-ops/compose-checks.json`；前两次
  合成输入路径错误归档为 `compose-checks-attempt1/2.json`。真实目标机配置尚未验收。
- 已在受控隔离 Linux VM 用当前冻结源码实际构建 API 镜像
  `rsc-formal-api-current:20260923-daily-ops-65d43a2e4251`；331 个镜像内 COPY 输入的
  SHA/权限/大小与源一致，`pip check` 通过，镜像配置摘要
  `sha256:ad19cde65430830f293d0de4e1450ccf0b8515e0068d6ff3a7d74325951f628b`。
  对该准确镜像执行 `nerdctl run` 无网络、只读根目录、UID 65532 的 CLI `--help`，
  六个固定操作可加载；镜像构建结果与独立检查分别见
  `artifacts/api-image-current-20260923-daily-ops/api-build-result.json`、
  `ops-image-checks.json`。包服务与 VM 已停止。后续实际数据库/FD/TTY 验收见下项。
- [当前镜像日终运维容器实跑](CURRENT_CONTAINER_DAILY_CLI_20260923.md)：同一准确镜像
  在隔离 Linux Docker 中以 UID 65532、无网络、只读根目录，使用私有 FD 3 和隐藏 TTY
  精确读取映射状态，并以 FD 精确恢复截止；错误审核摘要和过宽 conninfo 权限均拒绝。
  合成 PG16 源及第二新库各 212 表 / 788 行，六张日终表均非空且逐表 hash 一致；
  完整故障矩阵、无残留会话/锁、引擎与 VM 清理通过。`...-attempt4/result.json`
  终端 `status=passed`；前几轮夹具失败保留。目标 Linux 与真实来源仍未验收。
- 首次三个完整静态分片在约 2 分钟时因发现默认秘密文件挂载与试点 `prepare` 输入冻结冲突
  而受控停止，分别为 518/1,187/3 项已通过的**局部结果**，退出码均为 2；日志在
  `artifacts/static-shards-20260923-daily-ops/`。删除默认挂载后须从新候选重跑，不能拼接局部结果。
- 第二轮三片在同一 `f69e30de…` 源码下发现本地 `PATH` 未含 Node，两个小程序契约测试
  因环境失败；补齐 CI 固定 Node 24.19.0 后精确 2 项通过。第二轮受控停止，局部通过
  1,555/1,276/15 项、三片退出 2，日志在 `...-attempt2/`。源摘要没有改变；
  `...-attempt3/` 使用 Node/PG16 client 后发现 CLI `--help` 提前导入应用配置，受控停止，
  局部 515/1,138/2 项通过、三片退出 2。修复为延迟导入后，独立子进程测试通过。
- 第四轮已冻结 1,091 输入，摘要 `b2f07f16c03bde985b83727c37126f2a4aa6057d8b49a6c7c035bf7209368592`；
  `...-attempt4/` 前两片终端退出 0，分别通过 2,078 和 2,398 项；第三片仍在执行。
- [当前八角色合成容器联合恢复](CURRENT_CONTAINER_RECOVERY_20260923.md)已通过；
  [非空日终容器恢复](NONEMPTY_DAILY_CONTAINER_RECOVERY_20260923.md)另验证 1 条映射
  决议和 3 条截止快照在第二个新库中保留，212 表 / 746 行逐表一致。六张日终表另外
  四张仍为空；后续[六张全非空恢复](FULL_DAILY_CONTAINER_RECOVERY_20260923.md)
  又验证 212 表 / 788 行、5 个对象及六张日终表在第二新库逐表一致。真实 OSS/目标机尚未验收。

仍需在冻结候选后补齐：CLI 外层六操作完整矩阵、并发及提交失联后的原坐标恢复矩阵；
容器中六操作完整矩阵、信号及提交失联恢复矩阵；
三个完整静态分片、GitHub PG16 runtime/客户端门禁。真实 OAM 来源、附件、身份、
SMS/OSS 及连续三天真实对账另行验收。未提交、推送或部署。
