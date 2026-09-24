# 物料采集认证接收与原回执恢复（7.53）

> 7.54 接续已补独立总部来源授权和持锁准入，见[来源授权说明](MATERIAL_SOURCE_AUTHORITY_20260920.md)。
> 当前部署脚本 head 要求为 0118；以下 7.53 功能及验证数字保留为历史记录。

在 [7.52 两轮采集](MATERIAL_MASTER_CAPTURE_20260920.md) 上新增专用签名接收和查询入口。
接收器核对真实 HMAC、准确来源/密钥登记、完整采集契约及时间窗，只追加不可变暂存回执；
回执不会创建正式物料、来源版本、库存流水、通知或发布资格。

## 传输登记与权限

新增 `20261026_0116` 前向迁移，两表分别保存传输登记和采集回执：

| 对象 | 写入与读取边界 | 历史保留 |
| --- | --- | --- |
| `oam_material_capture_bindings` | 直接 migrator 身份登记；backup 只读；edge 仅调用准确绑定检查函数 | 原元数据不可改，只允许追加一次 revoked_at；不可删除/截断 |
| `oam_material_capture_receipts` | edge 在当前有效登记的来源内 SELECT/INSERT；backup 只读；API/projector 无权限 | 正文、摘要、绑定、接收时间不可修改/删除/截断 |

两表均 FORCE RLS，4 个 ALWAYS 触发器、6 条策略及 4 个固定函数体摘要进入启动清单；
仅 2 个检查函数向 edge 开放 EXECUTE。同步更新 edge 部署授权/核验脚本及 head readiness。
修正运行时触发器清单中原 0114 凭据表的启用模式检查，使其与既有 ALWAYS 迁移一致；未修改历史迁移。
空表可以正常降级；只要有传输登记或回执，降级先阻断并保留证据。

登记准确关联一个现存 `SourceSystem.id`，要求 code=`oam`、mode=`read_only`、enabled=true。
采集包固定 wire source_system=`starcharge_oam`，接收器通过该显式登记关联两者，不按名称或区域
猜测同一来源。登记不含物料状态/单位语义，不构成总部对来源业务范围的批准。

密钥沿用隔离接收器已有 `RSC_EDGE_SYNC_SECRET`，物料用途用
`rsc.material-master-capture-key.v1` 域生成指纹，独立 key_id 和有效期登记。没有复制 OAM token，
没有新建浏览器资料；该共享传输密钥也不能证明某台机器独占凭据或实际 OAM 数据经过业务授权。
登记不能回溯生效；来源与密钥登记在接收事务内加锁，等待后重新校验时效和撤销状态。

## 显式部署准备

`deployment/provision_oam_material_capture.sql` 默认 PREVIEW，只接受直接迁移身份和准确 head 0116。
`registration_json` 必须包含 8 个非秘密字符串字段：`action`、`id`、`source_system_id`、
`source_instance`、`key_id`、`key_fingerprint`、`valid_from`、`valid_to`。action 为 register 或 revoke。
source_system_id 必须人工核对准确现存来源，key_fingerprint 使用
`app.material_capture_ingress.key_fingerprint(secret, source_instance)` 计算；禁止把密钥写进该 JSON。
新登记 valid_from 应设为计划启用的未来时间，采集开始也必须处于登记有效期内。

确认准确预览后，只有显式 `apply=REGISTER_TRANSPORT_ONLY` 才执行。脚本返回原请求和数据库中
准确登记；重复 ID 只允许元数据完全相同，已撤销登记不会重新激活。新版本须用新的 key_id/ID。
撤销须携带原 8 字段中相同的坐标和元数据；重复撤销只回读，不修改历史撤销时刻。
此脚本是基础设施登记入口，尚无总部批准/审核文件的业务授权审计链，不能用于正式物料发布。

接收器部署配置新增空的 `RSC_EDGE_MATERIAL_CAPTURE_KEY_ID`；空值默认关闭。准确值通过
`build_edge_runtime_env.py` 生成 `OAM_EDGE_MATERIAL_CAPTURE_ENABLED=true` 和对应 key_id。
缺白名单、密钥、版本或生产数据库身份时失败关闭；API 不接收这些凭据。
本批只验证了脚本语法和权限契约，未在真实 PostgreSQL 执行登记或部署。

## 上传与结果未知恢复

物料入口分别为 POST `/api/integrations/oam/edge/material-master/captures` 和其 `/status`。
两者都要求来源白名单、HMAC、准确用途/schema/key_id 和 5 分钟签名窗口。请求正文严格规范化，
拒绝重复 JSON 字段、NaN、错摘要/类型、旧包和伪造元数据。通用验签改为有界流读取，超限即拒绝。
接收最大值受现有接收器配置约束：默认 8 MiB；采集契约最大 16 MiB，请求信封还需额外空间。
超限保留本地文件并报告失败，不自动放大配置或重传。

以下命令为部署模板，本批没有执行真实调用。`RSC_EDGE_SYNC_SECRET` 只从已有私有环境读取，
不通过命令参数传递；api-base 必须是准确 HTTPS 隔离接收地址（含 `/api`）。

```bash
.venv/bin/python edge_sync/oam_material_master_capture.py \
  --capture --upload --source-instance EXACT_ALLOWLISTED_SOURCE_ID \
  --key-id EXACT_REGISTERED_KEY_ID --api-base https://receiver.example/api \
  --archive-dir /private/local-material-captures

.venv/bin/python edge_sync/oam_material_master_capture.py \
  --status-file /private/local-material-captures/material-master-EXACT_CAPTURE_ID.json \
  --source-instance EXACT_ALLOWLISTED_SOURCE_ID \
  --key-id EXACT_CURRENT_REGISTERED_KEY_ID --api-base https://receiver.example/api
```

上传仅允许与本次新采集一起执行，先 O_EXCL/0600 归档并 fsync，再通过共享 Edge HTTP 发一次请求。
两轮来源读取和接收上传均不重试。超时、断连、错误或不匹配回执保留原文件，不覆盖或推进状态。
`--inspect-file` 保持纯离线；`--status-file` 先重验历史文件，再核验原 Edge 归属，只查询准确
capture_id，既不重新读取 OAM，也不重新上传。not_found 返回 retry_allowed=false。

status 要求当前有效登记/凭据；显式轮换后允许查询旧版本的原回执，返回原 key_id/receipt_id。
历史回执不会因超过 45 分钟而丢失，回执另行报告当前新鲜度。精确重复接收复用原事实；同一
capture_id 的不同正文、密钥或绑定冲突拒绝。COMMIT 已成功但响应丢失时，status 找回同一回执。

`channel_attested=true` 仅代表配置传输渠道认证；`source_authorized`、`full_catalog_verified`、
`master_source_evidence_verified`、`projection_published`、`start_ready` 始终 false。
即使两轮明确为空也只记录可见目录的空观察，不删除任何正式物料。

## 验证与接续

本地测试实际运行 HMAC、ASGI HTTP、CLI、SQLite 0116 迁移及 commit，OAM 来源和共享 Edge 传输
由合成替身提供。覆盖首次/重复接收、冲突、窗口/撤销、篡改、流大小限制、回执丢失与查询恢复、
轮换后历史查询、不可变事实、空表往返和有证据保留。最终结果与准确命令记在同目录交接及
`cloud_oam/artifacts/material-capture-ingress-20260920/verification.json`，首轮失败日志也保留。

扩大采集/库存控制/迁移/权限/部署联合 **1189 passed，1 warning，71.60 秒**，包含完整 SQLite
head 往返。随后补齐生产路由隔离、缺配置启动拒绝及部署 SQL 整段解析，最终边界联合
**116 passed，1 warning，13.44 秒**；两轮有交集，不累加，不代表单次全量后端通过。
追加用例首轮 111 passed/5 failed：生产夹具未关闭旧批次开关、psql 解析预处理遗漏 gset/gexec
终止符；均只修正夹具/解析适配后通过。最初接收专项的 3 个断言错误及修复结果也完整保留。

实际 PG16 helper 已接入既有保留检查之后，准备核验双连接并发、撤销锁、角色/RLS、backup、
历史查询、不可变及 0116 降级阻断；本批没有执行。SQL/PLpgSQL 可解析和 SQLite 通过不代表真库通过。
未新增公开 CI 候选提交，原未答复例外仍保持待定；没有提交、推送、部署、通知或调度变更。

下一步仍须来源业务授权、不可变正式来源版本与物料投影，并协调 `materials.source_updated_at`
目前非空与真实时间未知的前向迁移/查询契约；不得用采集时间填充来源更新时间。还须明确状态、
基本单位、SN/批次语义并在发布事务内重验登记、授权、原文与主数据并发范围。B1 控制 publisher、
B3 持续对账、B8 当前候选 PG16/真机、公开表格导入与通知渠道/UAT/恢复/压测均未关闭。
