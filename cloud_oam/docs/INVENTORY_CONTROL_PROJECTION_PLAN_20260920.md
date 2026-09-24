# 7.60 控制发布前核查与逐行来源关系

2026-09-20，`codex/notification-delivery-worker`，`f713999` 后未提交候选。
本批新增当前总部会话下的发布前核查及 owner CLI，修复正规化有效期漏计映射到期的问题。
head 保持 **0120**，没有新增迁移、表、触发器或运行权限。7.59 证据保持。

## 当前行为

`inspect_inventory_control_projection_plan` 接受准确 preparation ID、其准备清单 SHA 和已批准
mapping decision ID。身份从当前正式网页 JWT/会话读取，校验当前总部权限和授权版本；
PostgreSQL 必须是直接 migrator、READ COMMITTED、干净 Session。普通 API、edge、
projector 和 backup 均不能调用该 owner 入口。

入口在同一读取事务内重建控制来源准入、完整/增量库存、批准映射和当前物料来源证明。
只有所有目标行都合格，才生成 `rsc.inventory_control_projection_plan.v1`；任一缺失 SKU、
未映射或数量/单位异常都会拒绝整个计划，不输出部分省级余额。

本批补齐了未来发布事实需要的准确关系：

| 计划信息 | 来自何处 | 用途 |
| --- | --- | --- |
| preparation、binding、catalog、capture chain | 0112 不可变准备事实 | 固定区域及覆盖范围 |
| 每段 capture snapshot、snapshot ref、manifest/staging SHA | 已认证暂存链和准备引用 | 分开保存传输原文、采集覆盖证据 |
| 每个原始业务键的 staging record ID | 重建后最后一次 upsert 的实际暂存行 | 支持逐行真实外键与原始来源回读 |
| 原始行 SHA、数量、锁定量、仓库/位置、来源时间 | 完整重建和正规化审核 | 防止错误汇总、跨区域混入和时间补造 |
| material line/version/publication/policy ID 与摘要 | 当前已审核物料发布证明 | 固定正规化所使用的主数据版本 |
| 七字段控制 payload、来源列表与各自 SHA | 已有期初控制合同 | 后续正式控制投影的输入 |

增量中未变行保留其原完整快照或更早增量的 upsert 指针；变化行使用新暂存行，删除行不再
参与后续计划。新的完整采集重置重建范围。可证明零库存保留完整覆盖链、返回零控制行和
零来源关系，物料来源状态为 not_required，不伪造 SKU 零行或声称完整物料目录已验证。
派生 `external_sync_current_records` 缓存不作为来源关系依据。

来源行只公开必要标识、摘要、数量和来源时间，不输出原始 data、SN 或私有文件 storage key。
汇总行仅当所有来源证明同一个已知更新时间时保留该时间；混合时间或含未知值时为 null，
不会用采集或核查时间代替。当前库存采集器本身仍提供未知来源时间。

## 审核摘要和有效期

新的 publication basis 使用独立 schema 保存去除观察时钟后的授权与正规化证据，保留所有
身份、内容摘要和有效截止时间。原始带时钟观察的 SHA 另行返回，不把修改后的内容冒充原
观察。相同来源关系和证据的重复核查得到相同 review SHA；新的物料版本、映射、授权或
来源关系会改变摘要。核查读取原始关系后再次重建，并在最终操作人验证后再检查截止时间。

本批发现正规化 `normalization_valid_until` 只取库存与物料证据期限，遗漏了已批准映射的
`valid_until`。现在取三者最早期限；映射先到期时，正规化响应和发布计划均按该期限失效。
现有配置核查在等待操作人验证后同样拒绝刚过期的映射，不把旧观察当作可继续使用的批准。

## 运维入口

既有 `scripts/configure_inventory_control.py` 新增 `--control-projection-plan-file`，文件形状：

```json
{
  "expected_authorization_version": 1,
  "command": {
    "preparation_id": "填写准确准备记录 UUID",
    "preparation_sha256": "填写该准备清单的 64 位 SHA256",
    "mapping_decision_id": "填写准确已批准映射 UUID"
  }
}
```

沿用既有 owner 数据库身份/PG16/head 预检、隐藏终端或继承描述符读取凭据。只允许默认
inspect 或显式 `--mode inspect`；apply、status、preview 均拒绝。成功返回前回滚读取事务，
释放锁，不提交任何事实。命令文件不得含 access token，响应也不返回凭据。

所有返回均明确 `recorded=false`、`write_authorized=false`、`projection_published=false`、
`start_ready=false`。这里完成的是未来发布器的当前输入核查；**独立发布事实、正式控制
SyncRun/Batch/Inbox/外部版本的原子写入尚未实现**，不可把本计划直接交给期初启动。

## 验证与接续

最终联合 **772 passed，1 warning，96.66 秒**。原生成功记录 `checks/run-_geb5v9p/`，
集群已正常停止，`serverExitCode=0`；本批所有测试会话均结束。

证据目录：`artifacts/control-projection-plan-20260920/`，最终结果见 `verification.json`。

- 首轮专项 17 项、随后组合 94 项通过；之后的最终联合结果与它们有交集，不累加。
- 覆盖真实服务的完整/增量/零库存、精确原行指针、稳定审核摘要、来源或内部关系变化拒绝、
  缺失物料整批拒绝、现有物料版本变化重审、当前权限和映射到期、缓存不能替代来源证据。
- CLI 使用当前凭据、禁止 commit、回滚后数据库事实不变；各写入模式在解析时拒绝。
- 新建原生 PostgreSQL **16.15** 合成库通过七场景：完整、增量保留原行、增量删除、缺失 SKU、
  目标零库存、完整零库存、新完整采集。完整链同时验证两套来源、映射、审核文件、当前物料
  版本、实际角色/锁、API/Edge 启动和 0120 迁移/降级保留；原库存和物料历史未因核查改变。
- 新测试纳入现有 static_safety，原生 helper 已接受保护的完整 PG16 门禁；本地只运行
  自建私有集群的独立检查器，没有启用 GitHub 专用破坏性入口或操作旧数据库。

下一步使用本批确切关系定义发布、控制行、原始行引用及版本关闭事实。首次发布、后续版本、
增量删除和零库存都须通过独立前向约束与完整图验证；同一事务内再次核查来源和当前会话，
再原子写正式控制批次，COMMIT 回执未知时只精确读取原结果。七字段历史 payload 不变，
新增关系不能借扩大旧工单 projector 权限实现。之后再接批次选择、期初启动和持久恢复。

B1 整体、B3 三天真实省级对账、B8 当前完整候选门禁/真机/角色 UAT、N1—N5 实际通知
provider 与回调、历史迁移、PITR/负载/灰度验收仍未关闭。公开知识来源依旧 pending/0，
“星星后台管理”链接 `/xx`。本批没有真实来源调用、上传、调度启用、提交、推送或部署。
