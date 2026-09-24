# 控制库存发布准备事实与验证（7.43）

2026-09-20，接续 7.42。工作树仍为 `06f6/oam`，分支
`codex/notification-delivery-worker`，Git HEAD 为 `f713999`。本批未提交、推送、上传或部署。
正式需求依据 V1.0；本批推进 B1 的准备证据持久化，不能据此放行正式建账或关闭 B1。

## 本批行为

新增 `inventory_control_models.py`、`inventory_control_preparation.py` 和前向迁移
`20261022_0112`，前驱为 `20261021_0111`。保存五类不可变事实：明确的正式来源/区域与
暂存源绑定、独立目录版本、采集链、准确 inbox 快照链接、准备 manifest。目录包含明确空仓位，
不从有库存的记录反推目录；全量、增量链及显式零库存沿用原校验语义。

服务要求传入准确的正式 source/region ID。历史来源代码按既有期初规则识别 `OAM/oam`，
不重命名，也不将暂存的 `starcharge_oam` 直接替换为正式源；来源必须启用且 read_only，
区域必须 active/region_company。准确 inbox 记录须已 complete，并逐一匹配来源、范围、时间、
模式、manifest、批次和明细。此验证证明保存内容的一致性，不代表来源或目录获批。

原始 HTTP 字节摘要与规范化 JSON 摘要分别保存。接收器允许的空白、时间字符串格式不能导致
把原字节摘要错误当作规范化摘要。另保存暂存 envelope 摘要，回读再次验证接收时间、完成时间、
原 manifest/batch 摘要和明细，不把完整接收等同于真实采集证明。

相同内容重放复用原事实；同目录版本改内容会拒绝，不能覆盖。写入使用事务内保存点，最终重证
失败时整组回滚，即使调用方继续 commit 也不残留半组。保存前拒绝待写会话；回读拒绝待改证据，
保留调用方 ORM 修改，不用刷新悄悄丢弃。无关待写 Outbox 不会被回读提前 flush。

## 数据库边界

五张表仅 schema owner 写、backup 读，API、edge、现有 work-order projector 不增加权限。
新守卫为 SECURITY INVOKER、固定 search_path，清除继承的表/函数授权；10 个 ENABLE ALWAYS
触发器校验摘要、父子关系、准确快照、完整链接，并拒绝 UPDATE/DELETE/TRUNCATE。
运行时目录现为 91 个函数、307 个触发器；新增私有表 ACL 检查会拒绝任何意外运行时或其他角色
授权及列权限。有准备事实即拒绝降级；历史 111 个迁移文件未编辑。

readiness 函数仅前向更新 schema head 的精确哈希，没有新增启动权。没有生产路由、worker 或
collector 调用准备服务，没有库存行、SyncRun、盘点任务、审计或 Outbox 发布。
结果中的 `source_authenticated`、`catalog_authenticated`、`capture_attested`、
`projection_published`、`start_ready` 均为 false。

## 本地验证

证据目录：`cloud_oam/artifacts/inventory-control-preparation-20260920/`，其中
`source-manifest.json` 固定本批源码，`verification.json` 固定命令、日志哈希和验证范围。

| 范围 | 最新结果 | 实际边界 |
| --- | --- | --- |
| 准备服务、原纯校验器、0112、数据库安全、0111 历史 pin 与 Alembic head | 551 passed，70.45 秒 | 含实际接收代码、SQLite 0112 建表后的保存/回读、全 head 升降级和 ORM 对齐；1 个上游弃用警告 |
| 部署安全、PG16 诊断及工作流合同 | 31 passed，1.27 秒 | 不能代替真实数据库执行 |
| 保护 PG16 本地入口 | 1 skipped，0.37 秒 | 真 PostgreSQL 16 未运行；没有伪造 CI/一次性库标记 |
| 客户端与历史迁移 | Web 197、小程序 299、既有迁移 111 文件哈希与 7.42 一致 | 本批未重跑客户端、真机或全量后端；新迁移另验 |

初始测试证据保留：`security-first.log` 为 525 passed/5 failed，
`security-final.log` 为 543 passed/1 failed。失败源于新增目录后测试固定数量、后继版本与
容器类型断言未同步；修正后上述完整范围通过，不将失败运行计作绿色门禁。

新增 PG16 helper 已接在 0111 保留证明之后：实际 edge role 接收全量/增量/零库存，owner 保存
和只读事务回读，API/edge/projector 拒绝访问，backup 只读，SQL 篡改及追加拒绝，临时授权漂移
被启动检查拒绝，最后验证 0112 有数据拒绝降级并保留图。该 helper 仅本地导入/结构验证通过，
动态结果待受保护的一次性 PG16 环境。鉴权使用明确的合成 VerifiedEdgeRequest 依赖，不能
把该 helper 说成真实来源认证或 HMAC 验证已通过。

## 下一步与上线缺口

B1 仍缺绑定准确版本的来源/目录授权与撤销事实、可信采集 attestation、SKU/品相/单位/数量
正规化、独立受限 publisher/RLS、原子幂等 SyncRun、批次选择和启动恢复。后续审批必须指向
本批保存的准确 ID/摘要，不能把准备结果的布尔值直接改为 true。继续保留已实现的期初启动与
历史回放，不把整个期初模块误报为未开发。B3 持续日终对账仍依赖可信控制发布。

B8 的服务、独立入账事实、按验收单状态回读和小程序恢复已取得本地证据，仍待准确候选 PG16
和真实角色/设备验收。N1—N5 通知真实渠道/回调/并发及 B2—B7 迁移、身份、恢复、UAT、压测
缺口不因本批保存准备事实而关闭。

公开首页继续为“交流备件知识大全”，网站“星星后台管理”跳转 `/xx`，小程序公开包仍只注册
知识页。原飞书表完整导出未取得，目录保持 pending/0 条。临时公开 CI 候选提交的既有确认尚无
答复；继续遵守用户“证据齐全后再提交”，不提交或上传当前候选。
