# 7.57 原生 PostgreSQL 16 与来源配置附件

2026-09-20，在原 `codex/notification-delivery-worker` 工作树继续；head 为 `f713999`，
本批未提交、推送或部署。现有未提交改动、旧数据库及所有失败日志保留。

## 本批落地

- 正式文件 API 新增 `source_configuration_evidence`。当前有效总部全国 admin 且具有
  `inventory_control.authorize` 或 `material_source.authorize` 才能上传；使用原上传意图、
  对象 HEAD 核验、完成凭据及 authorization 审计。没有新增角色权限。
- 来源批准、目录批准、映射批准和物料发布必须引用该用途的完整上传凭据。单独
  `status=available`、旧文件、错误 provider、缺失完成凭据及未来完成时间均拒绝。
- 下载限原上传者的当前来源审核权限；文件 API 不读取 owner 私有配置表，也不把配置
  权限扩展成他人文件的下载权限。跨审核人共享下载和配置页面上传控件仍未实现。
- **0120** 为前向迁移：扩展原文件守卫，增加四类审核事实的用途/完成凭据约束；
  新触发器 ALWAYS 启用，函数不得由 API、edge、projector、backup 或 PUBLIC 直接执行。
  已有不符合规则的审核事实阻断升级，存在此用途文件或审核事实时阻断降级。
  不改写历史附件、不修改 0001–0119 历史迁移。
- 修复生产 API 启动校验器漏列 0116 两个 material capture 接收函数的问题：只认可准确
  `edge_inbox` 执行权限，仍拒绝 API/projector/其他角色、授权转授及函数体变化；无新增数据库授权。
- 控制及物料 PG16 测试改为调用真实文件服务。库存快照按整行排序，兼容无 `id` 列的余额表；
  历史降级检查同时验证最先命中的保留守卫和原指定迁移的独立拒绝，原事务最终回滚。

## 原生 PG16 的执行边界

从 [PostgreSQL 官方 16.15 源码](https://www.postgresql.org/ftp/source/v16.15/)编译本地 ARM64 运行时。
归档 SHA-256 为 `c1575341fa7bd40f5274ea465b34390f4dc64cdd0770af327005caaeb9f6b7ed`，校验记录为
`artifacts/pg16-native-20260920/source-verification.json`。
运行时与数据库均在忽略的 artifacts 内；编译配置无 ICU/readline/SSL，不能据此认定生产部署配置等价。

`scripts/run_local_pg16_material_checks.py` 仅接受 `--postgres-bin`，不接受 DSN、已有 data 目录、
主机、端口或库名。工厂创建新的私有目录/Unix socket，禁止 TCP，拒绝 libpq `PG*` 环境覆盖，
核验进程子 PID、data 路径、启动时间、系统标识、16.x 版本以及初始库/角色集合，之后才创建
独立 migrator/API/edge/projector/backup 角色。结束只停止本次子进程，数据和日志保留。

```sh
cloud_oam/.venv/bin/python cloud_oam/scripts/run_local_pg16_material_checks.py \
  --postgres-bin cloud_oam/artifacts/pg16-native-20260920/install/bin
```

现有 GitHub-hosted PG16 门禁的启用条件、一次性服务检查和用户待确认事项保持；未伪造任何
GitHub 环境标记，也没有在本机现存数据库运行其破坏性入口。本地专项结果不能代替完整
GitHub release gate、生产数据库验收、真实来源或通知供应商验收。

## 已取得的真库证据

最终真库记录：`artifacts/pg16-native-20260920/checks/run-_fwwzet4/`，PostgreSQL **160015**。
`checks.json` 为 passed；`cluster-state.json` 记录子进程正常退出、checks passed、status stopped。

- migrator 从空库迁移到 **0120**；0120→0119→0120 空库往返通过。
- 受限 API 角色完成来源附件上传、完成核验和本人下载，使用合成存储适配器。
- 当前总部会话批准/撤销、真实 HMAC edge 采集和物料审核发布通过。
- 两连接同命令并发只发布一次；三次 A→B→A 观察保留不同不可变版本、稳定物料身份和准确关闭时间。
- API 目录保留未知来源时间 null；直接 SQL 破坏图关系、越权读取/写入及锁竞争均按预期拒绝。
- backup 读取与 owner 发布快照一致；生产 API 完整启动数据库权限校验通过。
- 已有审核文件和发布记录时，降级到 0119 明确拒绝；原 head 和发布快照保持。

先前失败均保留：直接 available 文件被正式守卫拒绝；本地初始化误用审计主键；库存余额
快照错误假定 `id` 列；完整启动权限校验漏列接收函数。修复后的结果只以最终记录为准。
第 4 次运行仅物料专项通过，第 7 次加入启动校验通过，第 8 次补齐有数据降级保留通过。

本轮联合测试终态和源码/日志/状态摘要见同目录 `verification.json`、`source-manifest.json`。
历史全量迁移回归、专项失败与修复记录保留，不将有交集的测试数累加。

## 正式基线缺口复核与下一步

1. B1：物料发布的本地真库证据已有；来源文件实际上传、真实状态/单位/SN/批次语义、
   原有 SKU 的有据迁移和完整目录覆盖仍待验收。`inventory_control_normalization` 的
   `master_source_evidence_verified` 仍为 false；下一步接入事务内的当前物料来源版本证明。
2. B1：独立控制库存 publisher、期初启动/结果恢复、PC 自动交接与定时发布、密钥治理仍缺。
3. B3：连续至少三天真实省级对账；B8：当前全部候选代码的完整 PG16 gate、正式角色/真机 UAT 未完成。
4. N1–N5：实际通知 provider、签名回调/重放、目标身份、发送/送达证据及并发验收仍需补齐。
5. 历史组织/人员/SKU/单据/附件迁移、PITR/RPO/RTO、500 用户负载和灰度回滚验收仍缺。
6. 公开首页继续“交流备件知识大全”，星星后台管理链接 `/xx`。指定飞书表格的真实导入仍
   pending/0；没有真实来源读取、业务批准、上传、发送或调度变更。

继续保留全部未提交改动；只有完整所需证据齐全后才提交。当前结果不是上线完成声明。

本批最终联合 **712 passed，1 warning，106.82 秒**；包括完整 SQLite head 往返。
