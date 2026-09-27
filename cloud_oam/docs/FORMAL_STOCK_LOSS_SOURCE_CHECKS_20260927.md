# 报损来源预检的 PostgreSQL 16 验证

范围为本人已开账库存查询和来源选择预检。报损提交、冻结、两级审核、处置、冲销及
客户端业务流程尚未完成；本验证不能证明完整报损功能或生产上线验收通过。

可重复入口：

```sh
.venv/bin/python scripts/run_local_pg16_stock_loss_sources_checks.py \
  --postgres-bin artifacts/pg16-native-20260920/install/bin
```

在 `cloud_oam` 目录运行。入口不接受服务器、DSN、已有数据库或数据目录；
`local_pg16_cluster.native_cluster` 分别为数量型、SN 型创建全新 PG16 私有 Unix
socket 实例，验证进程身份并仅停止自己的子进程。它不是 GitHub 的破坏性发布门禁，
不设置或伪造 GitHub runner 标识。

两轮均完整迁移至当前 0141，并使用实际部署脚本配置受限 edge 角色。合成来源经过
正式签名接收、物料发布及控制数发布；工程师实盘，独立区域负责人和总部管理员复核，
正式过账、解释控制差异并独立批准、关闭期初，才执行报损来源预检。不直接种余额。
SN 轮显式发布该 SN 物料的零控制数，不用数量型夹具的缺失行推定零库存。

`submit_loss` 仅在临时测试数据库建立合成授权，生产迁移没有新增此权限种子。
主体由真实数据库权限图加载，服务使用实际 `star_oam_api` 角色。检查包括：

- 未完成期初时拒绝；建立后仅返回本人精确库存。
- 数量型 `0.250` 与单件 SN 的预检成功，重复预检 hash 一致。
- SN 的错误 SKU、SN 编号、二维码或 SN 标识分别拒绝。
- 将真实角色授权改为 deny 后，旧主体不能继续预检。
- 监听 SQL 仅出现 SELECT；提交后逐表比较库存、期初、对账、审计及 outbox 事实不变。

现有期初证据验证需要账本行锁，不能在 PostgreSQL `READ ONLY` 事务或只读副本上
运行。数量轮的一次来源查询与两次预检共 3086 条 SELECT；SN 轮另含四种错误校验，
共 5829 条 SELECT。此结果没有证明 500 用户的性能或并发验收，后续需单独评估。

2026-09-27 仓库入口句柄 `32546` 退出 0，两轮通过、源文件清单无漂移、两个实例
正常停止且 server exit 0。证据位于：

- `artifacts/local-stock-loss-sources-pg16/checks/run-zt3n9dbe/`（数量型）
- `artifacts/local-stock-loss-sources-pg16/checks/run-u_ewpvdx/`（SN）
- `artifacts/loss-source-preview-20260927/permanent-pg16-v1.log`

每个实例保留 `source-manifest.json`、`checks.json`、`cluster-state.json`、迁移与
服务日志。早期忽略目录中的实验仍保留，接续以仓库入口及上述两个终态为准。
审批、处置及冲销必须继续增加其各自事实与 COMMIT/并发门禁，不能复用来源预检的
成功结果作为后续写入授权。
