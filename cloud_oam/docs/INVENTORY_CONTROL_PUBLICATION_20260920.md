# 7.61 独立控制库存发布与原结果恢复

2026-09-20；既有 `codex/notification-delivery-worker` 工作树，`f713999` 后未提交候选。
本批从 7.60 的发布前核查推进到实际控制发布服务及 **0121** 向前迁移。
原产品基线、120 个历史迁移及已有未提交改动保留。全部数据和传输均为合成测试，未访问
真实 OAM/RSC/飞书、写生产数据库、发送通知、调整调度或部署。

## 已实现行为

`backend/app/inventory_control_projection.py` 提供 preview、execute 和 exact status。
发布要求直接 migrator、READ COMMITTED、干净 Session、当前总部管理员网页会话及准确
授权版本；重新读取来源/目录批准、映射、认证采集链、已审核物料来源和完成的证据附件。
使用已审核 preparation、其清单摘要、mapping decision、证据文件、原因及唯一请求坐标；
preview 的 review SHA 必须与执行事务内重建结果一致。公开查询首页不参与此流程。

同一事务写入四类不可变事实：

| 表 | 保存的事实 |
| --- | --- |
| control_projection_publications | 当前审核输入、来源/区域、前驱发布、采集/有效期、请求与审计 |
| control_projection_lines | 规范化控制行与对应 ExternalObjectVersion / SyncInboxEvent |
| control_projection_origins | 每一原始业务键的准确采集段、暂存行及审核物料行外键 |
| control_projection_closures | 旧版本由哪个发布关闭及 replaced / absent 原因 |

同时原子写入已完成 SyncRun、已应用 SyncBatch、Inbox 和外部控制版本。既有七字段控制
payload 保持，传输摘要、采集覆盖摘要和 opening 控制 manifest 仍是不同证据。
不产生 inventory_transactions、stock_balances、stocktake_tasks 或 outbox_events。

后续发布要求更晚的采集，关闭上一发布的控制版本并保留历史。增量未变行继续引用其原
upsert；删除行移出新集合。全省可证明零库存写入一份零行已应用批次，不伪造物料零行。
删除后重新出现会延续同一个外部对象的版本链。未纳入审核发布事实的旧控制对象阻断并
要求有据迁移，不静默收编或覆盖。

已发布的原命令通过相同请求与 review SHA 回读原结果；后续映射撤销或新版本发布不删除
旧结果。回读仍要求当前操作人和会话有效。提交回执丢失只要求 status；未找到原结果返回
`recorded=false, retry_allowed=false`，不自动重放写请求。

## 数据库边界

`20261031_0121_control_publications.py` 新建四张私有事实表、两项受保护函数及 24 个
ALWAYS 触发器。普通 API、edge、projector 不取得新表读写或新函数执行权限；backup 只读。
关联核心表的延迟约束检查发布、批次、事件、版本、原始行集合及关闭关系。事实禁止修改、
删除和截断；有发布或孤立发布审计时降级拒绝，保持 head 与历史。

总部身份、当前会话、来源/目录连续授权、映射及物料来源在 INSERT 和延迟提交边界核验。
即使服务执行时仍有效，只要 COMMIT 前过期，整笔事务也必须回滚。实际 PG16 反向测试
发现并修复当前版本指针被置 NULL 时比较条件漏检的问题。

原工单 projector 的权限与既有 RLS 未扩大；API/Edge 启动目录增加本迁移的准确函数摘要、
触发器集合和 readiness head。部署物料接收脚本只同步其 head 预检到 0121。

## 运维调用

既有 `scripts/configure_inventory_control.py` 增加 `--control-publication-file`：

```json
{
  "expected_authorization_version": 1,
  "command": {
    "preparation_id": "准确准备记录 UUID",
    "preparation_sha256": "该准备清单的 64 位 SHA256",
    "mapping_decision_id": "准确已批准映射 UUID",
    "evidence_file_id": "已完成的 source_configuration_evidence 文件 UUID",
    "evidence_sha256": "文件的 64 位 SHA256",
    "reason": "本次发布的审核说明",
    "idempotency_key": "唯一且固定的业务幂等键",
    "request_id": "唯一且固定的请求标识"
  },
  "review_sha256": "preview 返回的准确摘要；apply/status 必填"
}
```

默认 preview；显式 `--mode apply` 才提交，`--mode status` 只回读。凭据只从隐藏终端或
继承文件描述符读取，不能放命令行、请求 JSON 或日志。预检要求目标库名、直接 owner、
PG16 和准确 head。旧 `--control-projection-plan-file` 继续只 inspect，不被升级成写许可。

成功发布返回 `projection_published=true, start_ready=false`。已验证既有 opening 消费者
能够读取新发布以及历史版本；尚未提供正式控制批次选择、与启动计划绑定的用户流程，
不能将本批通过理解为已完成个人仓期初建账。

## 本批验证与证据

最终联合 **1183 passed，1 warning，214.92 秒**；此前专项 **326 passed** 与联合有交集，
不累加。全量迁移回归 **166 passed**，另两处过时 head 断言失败：运行函数加载路径和前驱
revision 仍指旧 head。修正后两项专项 **2 passed，5.66 秒**，并纳入上述 1183 项最终回归；
未通过项已逐项复验，保留首次失败日志，不把首次全迁移运行标记为全绿。

依赖检查、Python 编译、CLI 帮助、仓库安全扫描与 diff 空白检查通过。
证据目录 `artifacts/control-publication-20260920/` 包含源码清单、日志摘要、五个原生集群的
停止证明、前批证据保留核验及原状态的前后副本。原生最终结果为
`checks/run-dbxck6_a/checks.json`；完整 GitHub 门禁仍为 false。

已观察到原生 PostgreSQL **16.15** 实际运行：空库升到 0121、空库 0121 往返；签名库存
与物料整链；并发相同请求唯一发布；完整/增量/零行/重现四次发布；历史回读和 opening
消费者；SQL 图篡改拒绝、角色拒绝、backup 相同快照；过期 COMMIT 回滚；API/Edge
启动校验；有数据 0121 降级拒绝。集群由本批工厂新建并关闭，只监听私有 Unix socket。
失败数据与日志均保留，没有使用旧 55487/55492 数据库，也未解除 GitHub 专用全门禁保护。

本地 PG16 专项和完整 GitHub release gate 分开记账；完整候选门禁仍未完成，因此未提交、
推送或部署。原临时 CI 提交例外仍待原答复，本批不把“继续开发”视作批准。

## 正式基线缺口复核与下一步

B1 的独立控制发布事实、原子核心投影和原结果恢复已实现。本轮复核现有
`formal_services/opening_start_options.py` 与对应 router：只提供区域、资产归属、库位和
盘点人选项，尚无本批发布的受限候选批次读取与启动绑定。下一步先补这一连接点，并用
实际 API 角色验证当前发布读取、启动时重验、版本切换竞争和原任务恢复，保持历史时间
证据，不给旧盘点回放附加当前来源到期检查。

B1 的真实字段语义、完整目录与旧 SKU/控制对象有据迁移、30 分钟持续同步、PC 自动交接
和密钥治理仍待验收。B3 连续三天真实省级对账，B8 当前候选完整 PG16/正式角色与真机
UAT，N1—N5 真实通知发送/回调/送达，以及组织人员历史数据、PITR、负载和灰度验收均未关闭。

公开首页和小程序继续为“交流备件知识大全”，网页星星后台入口为 `/xx`；客户端本批未变。
指定飞书来源的真实公开知识导入仍 pending / 0，不以物料控制发布替代公开知识数据。
