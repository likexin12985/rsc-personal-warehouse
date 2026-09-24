# 正式控制映射版本与撤销（7.51）

> 2026-09-20 / 7.59：非零控制整链原生 PG16、组合锁、实际角色及 API/Edge 启动校验通过，最终联合 753 项通过。
> 已修复快照原文/摘要不一致及 Edge 0119 触发器目录遗漏；head 0120，无新运行权限。
> 独立控制发布器、期初启动/恢复、完整当前候选门禁和真实验收仍缺。
> 见[本批真库证据与接续范围](INVENTORY_CONTROL_PG16_INTEGRATION_20260920.md)。以下保留历史记录。

> 2026-09-20 / 7.58：当前物料来源证明已接入，正规化输出升级 v2；有证据时逐 SKU 验证，缺失时整省候选阻断。
> 完整/增量/零库存 SQLite 整链及当前物料证明的原生 PG16 角色/锁专项通过，非零控制整链 PG16、publisher 和完整门禁仍待完成。
> 见[本批证据和接续边界](MATERIAL_CURRENT_SOURCE_PROOF_20260920.md)。以下保留对应历史版本行为。

7.50 已提供数量正规化审核，但调用方的规则 JSON 尚无批准事实。本批新增独立的、不可变的
映射决定，使总部管理员可以预览准确规则、批准、回读和显式撤销；正规化检查可直接按批准版本
取规则。映射批准不批准来源采集，也不证明正式物料投影的来源版本，不产生库存或发布事实。

## 审核与生效

- 新表 `inventory_control_mapping_decisions` 保存准确 binding、目录版本、规则全文与摘要、
  规则版本号、原审核文件、操作者及授权版本、当前网页登录会话、命令/审核摘要、有效区间与审计。
- 使用既有 `inventory_control:authorize` 的全国总部权限，不新增业务角色或权限种子。直接
  owner 连接与当前有效总部网页登录凭据必须同时满足；普通 API、edge 和 projector 不获得权限。
- 规则当前仅定义来源 `(materialStatus, materialStockType)` 到正式品相的准确映射，沿用
  7.50 类型及枚举校验。字符串/整数不混同，没有默认 new、单位别名或批次字段推测。
- 预览摘要绑定命令、准确目录、审核文件元数据、当前操作者授权版本及此前映射历史。执行前
  重新生成摘要；规则、文件、目录、历史或授权变化要求重新审核，不沿用旧预览。
- 生效区间为 `[valid_from, valid_to)`，不允许倒签；同一 binding/目录的批准区间不重叠。
  规则版本号在该目录内不得复用。自然接续或撤销后的替代版本都必须显式提供新版本号。
- 撤销追加独立决定，绑定原批准 ID 和原摘要；不改写或删除原规则。停用来源后仍可撤销映射，
  不能新增批准。撤销旧版后不会自动选用其他规则，也不会改变任何历史库存事实。
- 命令以当前操作者 + idempotency_key/request_id 分别唯一。相同审核命令重复执行或 status
  回读返回原决定；任一坐标复用但内容不同都冲突。审核文件后续失效不抹去历史执行结果，但会
  阻断该规则供新检查使用。
- 当前会话及权限在审计等待后再检查；决定与审计同一事务提交，失败一并回滚。会话 ID 使用
  RESTRICT 外键保留审核来源；注销通过现有撤销字段完成，不删除历史会话证据。

## 运维入口

已有 `scripts/configure_inventory_control.py` 新增独立 `--mapping-file`，默认 preview，支持
`--mode apply` 和 `--mode status`。文件、凭据与 owner 数据库配置沿用原受控运维方式：
JSON 不放凭据；交互隐藏输入或继承 `--access-token-fd`。命令参数不可与 command/handoff/
inspection 混用。当前 PC 自动签名交接尚未接入映射命令。

审核文件结构示例（全部值为合成示例，须替换为准确来源坐标和文件证据）：

```json
{
  "expected_authorization_version": 1,
  "command": {
    "action": "grant",
    "binding_id": "11111111-1111-4111-8111-111111111111",
    "catalog_id": "22222222-2222-4222-8222-222222222222",
    "rules": {
      "revision": "synthetic-reviewed-v1",
      "conditions": [
        {"material_status": "synthetic-usable", "material_stock_type": "synthetic-stock", "condition_code": "new"}
      ]
    },
    "expected_subject_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
    "evidence_file_id": "33333333-3333-4333-8333-333333333333",
    "evidence_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
    "reason": "合成示例：复核准确来源品相字典",
    "idempotency_key": "synthetic-mapping-review-001",
    "request_id": "synthetic-request-001"
  }
}
```

首次 preview 后把返回 `review_sha256` 添加到文件顶层，再执行 apply。发生提交结果未知时，
保留原文件和原摘要，通过 status 精确回读，不盲目重放。撤销命令使用 action=revoke、rules=null、
revoked_grant_id=原决定 ID、expected_subject_sha256=原决定摘要；仍须独立审核文件及原因。

正规化 inspection 文件可使用 `mapping_decision_id`，替代原 `normalization_rules`；二者不可
同时提供。检查先重证原始采集的来源/目录授权和 HMAC，再取当前有效批准规则，完成时再次核对
撤销、审核文件和有效期。错误目录、错误 ID、未生效、过期、撤销或文件失效均阻断。

通过时 `normalization_rules_authorized=true` 并返回准确决定 ID、摘要、审计 ID 和有效期。
独立传入规则的旧审核入口仍为 false。`master_source_evidence_verified`、密钥生命周期、
`projection_published` 和 `start_ready` 仍为 false；不能把当前审核 JSON 当成发布许可。

## 数据库与门禁

新增前向迁移 `20261025_0115`，不改写之前 114 个迁移。新表只给 backup SELECT，清除其他
非 owner 的表/函数默认授权。PG 使用 SECURITY INVOKER、固定 search_path 和 ALWAYS
触发器，数据库复核直接 owner、总部权限/身份、当前会话、准确目录、规则/文件/命令摘要、
时间区间、撤销关系与审计绑定；阻断 UPDATE、DELETE 和 TRUNCATE。

准入、批准、撤销均锁准确 binding；正规化使用该锁及审核文件共享锁，使检查结束前撤销和文件
变更等待。新版本创建不锁其他 binding；审计追加仍遵守既有授权流序列。存在决定或孤立相关审计
时 downgrade 拒绝，不销毁历史。空库支持完整 head 往返；运行时函数体哈希和触发器目录同步钉住。

readiness 新摘要 `7dad591c17ca90b278337f7b012bd7e9d826e5fc7753dcb41b05e7baf518de97`。
当前材料申请安全目录为 93 个函数、311 个触发器（不是整个数据库总数）；新增函数属于只读
运行角色不可执行的 owner guard。edge 捕获表的 RLS/权限及部署 SQL 清单保持。

本批证据目录为 `cloud_oam/artifacts/inventory-control-mappings-20260920/`，准确命令、结果、
源码和日志摘要见 `verification.json`。本地使用实际 JWT、正式身份、SQL 迁移、HMAC 接收、
授权和审计链，来源、审核文件及物料均为合成数据。PG16 helper 已接真实并发重复提交、准确
binding/文件锁、撤销、私有 ACL、backup 回读及迁移保留，但尚未在实际 PG16 执行。

最终联合 **1029 passed，1 warning，82.70 秒**，包含完整 SQLite head 升降级。此前专项
38 项通过；组合阶段 494 项通过、两个迁移清单断言失败，补齐 head 前驱和新表登记后最终通过。
保护 PG16 与未托管本地客户端共 **2 skipped，0.39 秒**，不算真实 PostgreSQL 验收。

下一步补正式主数据来源/版本证明、真实字段语义及密钥生命周期，接独立 publisher/RLS、发布
事务内复核、批次选择与启动恢复。真实 PG16、公开目录真实导入、通知渠道、迁移/恢复演练、
UAT、持续对账和压测继续保留；本批没有真实批准、业务发送、提交、推送、部署或启用调度。
