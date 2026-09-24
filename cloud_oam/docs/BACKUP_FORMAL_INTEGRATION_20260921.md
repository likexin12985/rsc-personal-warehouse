# 7.99 备份链路正式集成

2026-09-21。已将验证过的备份入口、导出、独立 worker 和 Compose 配置接入正式目录。
本批 41 个源码/配置/测试文件已有摘要；没有数据库迁移变更，没有提交、推送或部署。
公开首页、星星 `/xx` 入口和仅公开知识查询的小程序边界保持。

[完整本地证据](../artifacts/backup-integration-20260921/verification.json) SHA256：
`b5ea8356f50af1c73551fec30eea3564fdbd0ee5de1fb2790490014ed0095a56`。
整项 B5 尚未验收，实际数据库/API 容器链路、真实 OSS 和生产恢复目标仍缺。

## 正式实现

- `backend/formal_file_integrity.py` 保存唯一的纯附件校验规则。API 服务、存储 adapter 和
  worker 使用同一份定义；22 个抽取定义与集成前 AST 一致，API 其余定义保持。
  API 继续使用 ORM FileObject，worker 使用纯数据快照；worker 不导入应用设置或数据库。
- `deployment/backup-worker/` 包含 OSS 读取、流式打包、同快照联合校验、独立 CLI、总期限
  监督器和存活信号发送器。未带入合成 transport、测试包装器或 VM 控制代码。
- `deployment/backup/` 整合数据库同快照导出、旧附件导出和容量限制。保留只读角色、
  RLS/角色并发复查及锁；PG16 客户端的连接丢失检测只作用于备份客户端。
- `scripts/backup.sh` 和 `backup_steps.sh` 贯穿同一剩余期限：预检、数据库、旧附件、worker、
  独立 SHA256 复核、独占硬链接发布。发布前失败不生成完成包；成功发布后才清理过期包，
  清理失败另报提示，完成备份保持。中断结果仍应精确检查最终文件和日志。
- Compose 的 db/api 启用 init 并只读挂载监督器。DB 镜像增加 Python；API 镜像复制共享规则。
  object-backup 仅在 ops profile 使用，固定 `PYTHONPATH=/app`，另有三个独立 OSS 凭据字段，
  不继承应用或数据库凭据；只读根、独立网络和原资源限制保持。
- PR / 已有 push 范围增加新入口与 worker 触发路径。静态选择由 210 增至 218 文件，新增
  8 个备份测试模块。本批没有运行整套静态或 GitHub 门禁。

## 准确验证结果

| 检查 | 终端结果和范围 |
| --- | --- |
| 首轮 14 文件聚焦 | `90485` 退出 1：263 passed / 1 failed，153.76 秒。唯一失败是 worker 配置误置于 networks 下；原 YAML 和日志保留 |
| 配置修正后的影响范围 | `7962` 退出 0：20 passed，12.10 秒；覆盖 worker 隔离、数据库部署权限、CI 拓扑及附件部署坐标 |
| 正式入口 | `22858` 退出 0：13 场景；运行正式 shell、监督器及 worker，替换 Docker/cloud transport，不接触业务服务 |
| 正式导出及恢复 | `97746` 退出 0：5 场景；实际 PG16 正常/租约 EOF/停顿，当前 worker 打包及第二个新库恢复 |
| 实际 Compose 解析 | `81648` 退出 0：5.5.1 直接解析正式配置，默认及 ops 分别检查；只用合成环境，没有启动引擎或正式服务 |

首轮已经通过附件上传、下载、审计、来源证据权限及迁移回归。修正只涉及 Compose 层级，
随后覆盖其受影响检查；不能将 263 与 20 简单相加，也不能称为一次 264 项全绿运行。
Starlette/anyio 的原弃用 warning 保留。

13 个入口场景包括正常、预检/DB/旧附件/worker/校验/发布失败、留存清理失败、两阶段
容量超限、DB 总期限、worker SIGTERM 和主机入口 SIGKILL。旧包和嵌套文件保持，暂存清理；
checksum 故障明确发生在实际 worker 打包完成之后。成功包独立复核为 3 个可用对象、
1 个待上传意图；待上传意图不冒充对象字节已经存在。

实际 PG16 使用两个全新自有 Unix socket 实例。导出时观察到只读会话和表锁；租约失效后
会话、锁及客户端消失，失败输出为空。当前 worker 对合成对象执行 6 次真实 SDK 条件请求，
响应全部关闭，未访问真实 OSS。最终新包恢复后 **206 表 / 572 行**逐表事实摘要一致，
两个实例均正常停止，PID 文件和 socket 消失。检查 VM 亦已停止。

## 部署前置与剩余工作

主机需 Python ≥3.11、Docker Compose 和 SHA256 工具。首次采用本批代码须构建新的 DB/API
镜像；旧 API 镜像不包含共享模块，旧 DB 镜像没有监督器所需 Python。容量由实际数据量
确定，显式配置 `RSC_BACKUP_MAXIMUM_BYTES`（1 MiB..1 TiB，按 KiB 对齐）和
`RSC_BACKUP_TIMEOUT_SECONDS`（1..86400 秒）；启动前检查至少 8 倍容量空闲空间，不代表已预留。
独立 OSS region、bucket 和只读身份按 `.env.example` 配置；空值不会阻断普通应用启动，
显式运行备份时必须通过预检。预检不证明真实云端授权。

下一批先使用正式构建工件核验 DB/API/worker 容器全链路、镜像内共享模块和中断清理。
随后完成真实 OSS/KMS、异地保管、持续归档、恢复操作和生产 RPO/RTO 验收。原生 PG16、
合成对象、Compose 配置及前序容器监督器证据分别保留，不相互替代。
完整静态与 GitHub PG16/Client 应在后续正式集成后的准确候选运行；待批公开 CI 提交例外仍未获答复。
其余上线范围见[续开发交接](CONTINUE_DEVELOPMENT.md)与[基线缺口审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。
