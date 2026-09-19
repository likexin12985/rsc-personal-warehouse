# 通知投递运维验证与正式基线缺口审计（2026-09-19）

## 审计边界

依据 `docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md`
全文复核，重点对应 1.13、3.10、阶段 5/6 和生产验收红线。
本次只操作既有 `codex/notification-delivery-worker` 工作树；未执行生产迁移、
真实供应商发送、OAM/RSC/飞书业务写入或部署。通知排队、发送、送达、已读、
收货和个人仓入账分别保存事实。

本批运行代码的本地提交为 `9b080ef`，远端等价提交为
`7266ae2b26fb0bfb89e62ea38e4004c8c4e65879`，两者内容树均为
`27116bd292ad1dfafe684bd2d2ab6612625f514b`。Git smart-HTTP 传输失败后，
远端通过 Git Data API 创建等价提交，提交 ID 不相同；不可把两者称为同一 SHA。

## 本批完成范围

- 恢复工作树隔离的 Python 3.12 后端环境，安装项目锁定运行依赖及
  `pytest==9.1.1`、`pglast==7.18`、`httpx==0.28.1`；环境目录不入库。
- 提供总部管理员通知投递查询、渠道/状态筛选及显式失败重试。
  查询不返回收件手机号/openid、provider 原始响应或原始幂等键。
- 重试要求权限、全国管理员范围、尝试序号、原因、幂等键和请求标识；
  只有有明确 429/5xx 失败证据且未达次数上限的投递可以重新排队。
  超时、断连或缺失响应的未知结果保持阻断，重试接口本身不发送消息。
- 重试与审计在同一事务中完成，同一命令回读原审计结果；前端使用
  `apiNoReplay`，避免认证恢复自动重放写请求。
- `20261018_0108` 为 `star_oam_api` 增加投递/尝试记录的读和追加权限，
  仅允许修改投递状态列，不授予尝试记录 UPDATE/DELETE；运维权限仅种给 admin。
  两个 Compose 通知 worker 使用该 API 运行角色，不需要新增高权限 worker 身份。
- 0108 同步推进 readiness 版本标记，固定 0107/0108 的真实函数体哈希，
  upgrade/downgrade 分别使用相应方向的预期哈希。降级撤销本批 API 授权，
  不恢复 PUBLIC 宽权限。

实现位置：`backend/app/formal_services/notification_delivery_operations.py`、
`backend/app/routers/formal_notifications.py`、`frontend/src/pages/FormalNotifications.tsx`、
`backend/alembic/versions/20261018_0108_notification_delivery_operations.py`。

## 验证记录

| 检查 | 已观察结果 | 证据范围 |
| --- | --- | --- |
| 后端最后一次通知链/运维/ACL/投影安全/PG16 诊断与拓扑聚焦回归 | 367 passed | 包含最后的 downgrade 哈希方向修复和新增运维权限/幂等回归；1 个上游弃用警告 |
| 通知运维专项 | 9 passed（包含在上述 367 项内） | 真实 FormalPrincipal 读/重试拒绝、跨对象 key/request 冲突、命令变化和旧 attempt 无副作用；已补入后续 static_safety CI 清单 |
| 迁移/数据库与部署权限/PG16 诊断与拓扑/通知运维联合回归 | 508 passed（6 分 26 秒） | 在最后修改后的工作树执行；与上述 367 项有交集，不累加 |
| Alembic 全量迁移回归 | 168 passed（包含在上述 508 项内） | 0108 模型、权限种子、升降级；真实 PG 函数体和并发由远端 PG16 另验 |
| 通知事件、扩展、dispatcher、delivery、inbox 回归 | 29 passed | 合成数据和 provider stub，不是实际发送 |
| 前端全量 | 64 files / 1077 passed | 页面/契约/权限交互 |
| TypeScript 和 Vite 构建 | 通过 | 存在既有大 bundle 提示 |
| Python 编译、diff 空白检查 | 通过 | 不替代运行级测试 |
| Client gate | [35432058320](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/35432058320) success | 远端 `7266ae2` |
| PG16 workflow 的 static_safety job | 3739 passed, 3 skipped, 1 warning；job success | [35432056789](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/35432056789)，远端 `7266ae2`；3 个跳过项已在本审计中解释 |
| PostgreSQL 16 runtime 与聚合 release gate | 1 passed, 1 warning in 9006.52s (2:30:06)；runtime、static、聚合全部 success | [35432056789](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/35432056789)，远端 `7266ae2` |

本机 PostgreSQL 18 不作为 PostgreSQL 16 验收依据。远端使用隔离的 PostgreSQL 16
临时数据库运行真实迁移往返、ACL、安全边界、并发和中断门禁。
该全局 PG16 门禁不能替代未来通知 provider 实发、callback 或通知重试自身的专用并发验收。

静态集的 SN 条件跳过另做了定向复核：履约准备的 serial_mismatch 和入账 SN 拒绝场景在
非 SN fixture 上跳过，同次运行的 SN fixture 正常通过（6 passed, 2 skipped）。
另一处是 `edge_sync/test_oam_read_client_security.py` 的模块级跳过：托管 Git 树不含本地
受保护的 `work/inventory_query_portal/oam_read_client.py`，这是源码边界，不是 PG16 动态测试跳过。

此前 PG16 试跑依次暴露 readiness 未推进、函数体哈希未同步、downgrade 哈希方向错误；本次真实
PostgreSQL 16 迁移往返与并发门禁已成功。
已保留失败/取消的运行证据，不以旧绿灯替代本次代码验证：
`35425375524`（失败）、`35430812070` 和 `35431830306`（runtime 失败后由后续运行取消）。

## 通知正式基线缺口

| 编号 | 当前事实与证据 | 缺口及最小后续交付 |
| --- | --- | --- |
| N1 | `notification_dispatcher.py` 仅加载显式注入的 adapter；没有默认外部调用，未配置时退出 | 实现并审核微信、短信、飞书真实 adapter，明确凭据边界、provider 错误语义和精确回读能力；再取得渠道测试环境的发送证据 |
| N2 | `formal_services/notification_delivery.py:record_notification_provider_status` 已实现 channel/message id 校验和 delivered/read 单调推进 | 尚无 provider HTTP 回调入口、验签、时间窗、provider event 去重和安全审计；需建立接收事实后再调用该内部状态函数 |
| N3 | `foundation_models.py:RobotInboundEvent` 有唯一 provider event id 模型 | 尚无飞书双向查询/跳转/命令处理链；机器人涉及业务命令时必须调用正式接口并重新核验权限，不能直接改库存 |
| N4 | 统一库存过账写 `inventory.transaction.*` Outbox；通知 expander 只消费 `notification_events` | 未建立通用库存 Outbox 到通知的覆盖映射。盘点过账等仅有库存 Outbox 的路径尚不能证明会通知受影响工程师；需按事件白名单、明确收件人和原事务 ID 幂等补齐，并避免与已接线业务通知重复 |
| N5 | 已有本地服务级重试测试和全局 PG16 迁移/权限门禁 | 仍需通知专用真 PG16 并发测试：两个操作者同键/异键、worker 与重试竞争、审计失败回滚；实际 provider 超时应保持 unknown，不能因为 HTTP 5xx 本身就假定供应商绝未受理 |

必须保留的已实现事实：

- `material_request_inbound.py` 分别记录 `personal_inbound_order_created` 与
  `personal_inbound_posted`，个人仓入账通知已经存在。
- `stock_return_inbound_commands.py` 已记录退回入账通知；退回提交、发出、发运和验收也有各自通知。
- `work_order_material.py` 的正式物料操作写通知；不能把“通用库存入口没有直接调用通知”
  扩大解释成全部工单/退回/个人仓入账都缺通知。
- 供应商 delivered/read 与站内消息的本人已读是不同事实；新的 callback 不得把本人查看消息
  伪装成供应商回执，也不能用送达结果推进库存或 OAM 收货。

## 后续顺序与放行标准

1. 先补明确的库存事件覆盖矩阵与盘点等缺口，按原库存事务坐标去重；已有业务通知保持独立事件语义。
2. 建立 provider 回调事实、验签/鉴权/重放防护和专用 PG16 并发测试，再接审核过的真实 adapter。
3. 为每个渠道取得发送、未知结果隔离、受控重试、重复回调、送达/已读顺序的测试环境证据。
4. 继续逐项核对正式身份、可信 OAM 发布、日终对账、迁移/恢复/回滚、500 用户压测和业务 UAT。
   Client/PG16 通过只说明对应代码门禁通过，不能关闭这些真实环境验收项。

## 非通知正式基线复核

| 编号 | 性质 | 当前实现和待补证据 |
| --- | --- | --- |
| B1 | 实现缺口 | 新控制证据校验器 `backend/app/inventory_control_evidence.py` 和准备目录 `opening_start_option_schemas.py` 固定 `start_ready=false`；内部一致性检查没有来源认证、目录认证或正式控制发布能力。现有 `opening_stocktake.py` 启动和历史回放服务已经存在，不应误报为完全没有启动能力。按 `OPENING_CONTROL_PROJECTION_NEXT_SLICE.md` 补新可信控制 publisher、版本/闭包和启动恢复事实，保留历史来源，不能直接把布尔值改成 true |
| B2 | 实现及验收缺口 | 已有工单和收货 publisher；组织/人员独立正式发布、可信控制库存发布及历史迁移执行链仍未闭合。`MigrationBatch/MigrationError` 模型不等于完成迁移，需补数量、关键字段、附件以及多轮演练证据 |
| B3 | 实现及验收缺口 | `formal_services/opening_control_reconciliation.py` 的批次 scope 为 `opening:{region}:{task}`，正式路由接的是期初对账。仍需按日、控制快照和本地流水游标建立持续对账，取得连续至少 3 天差异解释 |
| B4 | 环境验收缺口 | 唯一身份解析及失效关闭已实现；本工作树未提供真实人员映射、首管理员、微信、短信/KMS、私有附件链的完整验收记录。现有短信认证 adapter 使用 PNVS，不能拿其他短信产品套餐替代接口验收 |
| B5 | 环境验收缺口 | 备份脚本已校验权限、哈希并原子发布，仓库 cron 是每日备份；需独立 PITR/WAL 或等效能力证据满足 RPO≤5 分钟，并实测 RTO≤2 小时及恢复后的库存/SN/审计核对。主目录可能另有部署材料，本审计未读取，不能断言外部演练从未发生 |
| B6 | 业务验收缺口 | 发运、分批验收、独立入账、工单回收、退回及扫码已有实现；仍缺按真实角色、设备、附件执行关键 V1.0 场景的 UAT 和真机证据，不沿用旧文档把这些已实现模块标成“未开发” |
| B7 | 环境验收缺口 | 未在本工作树发现可复核的 500 用户负载模型及报告；需记录部署规格、延迟、错误率、热点锁等待和后台队列积压，并验证生产入口限流和监控告警 |

非通知下一批优先处理 B1 的前向事实模型和默认不准入边界；它阻塞真实建账，且是 B3 的上游依赖。
环境验收项必须用对应环境报告关闭，不能以单元测试数量替代。

未取得的正式上线证据包括：连续至少 3 天省级 OAM 差异解释、正式迁移数量/关键字段/附件核验、
零负库存/重复 SN/重复入账/越权的完整业务验收、RPO ≤ 5 分钟与 RTO ≤ 2 小时的恢复演练、
应用回滚/同步批次回退/真实业务冲销演练，以及总部试用和区域灰度。
