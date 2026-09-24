# 库存通知来源异常复核

本节接续 7.34 的按对象隔离，属于未提交的 7.35 切片。依据 V1.0 的 1.13、3.5、3.10，
把通知来源异常的查看、显式复核和结果审计接到 `/xx/` 个人仓消息中心。公开首页仍为
“交流备件知识大全”，页首“星星后台管理”进入 `/xx`。

## 管理操作

总部通知运维管理员可以在消息中心查看“库存通知来源异常”。页面显示来源编号、首次隔离、
最近复核、异常类别以及通知是否已生成。记录中没有原来源 payload、手机、openid、渠道密钥
或原幂等键。普通用户不请求这个接口；只有读取权限的管理员可以查看，但没有复核按钮。

复核必须填写本次核查情况，并提交页面刚读到的原对象、最新审计 ID 和原始内容摘要。
成功时只生成独立通知事件及接收项，由后续 expander 生成投递队列，再由 dispatcher 处理。
页面明确区分生成、排队、发送和送达；接收项为零时照实显示，不宣称已通知工程师。

来源不完整、通知内容冲突或原来源内容变化时，继续保持隔离，并追加本次复核结果。
接口不修复源库存、原 Outbox 或历史审计，也不提供 force/删除隔离/更换来源的参数。
结果未知时浏览器不会自动重放 POST；必须先成功刷新来源记录，再决定下一次显式操作。

## 接口和权限

| 接口 | 权限与作用 |
|---|---|
| `GET /api/v1/notifications/inventory-sources?limit=50&after_id=...` | 既有 `notification_delivery/read` 加正式总部 admin 全国范围；按首次隔离审计版本倒序分页，复核不会改变分页顺序 |
| `POST /api/v1/notifications/inventory-sources/{outbox_id}/recheck` | 既有 `notification_delivery/retry` 加正式总部 admin 全国范围；只重检该来源的通知生成事实 |

复核请求体为 `expected_audit_id`、`expected_source_sha256`、`reason`，还必须带
`Idempotency-Key` 和 `X-Request-ID`。缺失或无效为 400/422；无权限为 403；来源不存在为
404；对象忙或幂等冲突为 409；旧审计版本/摘要为 412。响应与错误继承通知接口的
`private, no-store` 缓存边界。

复用既有通知运维能力只授权同一管理员处理通知生成异常；不会把原投递重试的 429/5xx、
次数上限或 unknown 边界放宽。本切片没有新增角色、表、ACL 授权或迁移，head 保持
`20261018_0108`。`access_context/read` 和区域范围 admin 都不足以访问来源异常。

写服务在取得正式身份图锁及来源对象锁后，按数据库当前时间重新加载有效人员、登录身份、
账号和角色权限。旧请求中的管理员身份已撤销时拒绝执行。服务拒绝只带 `allows()` 的临时对象。

## 不可变结果与并发边界

原 `inventory_notification.source_blocked` 审计永久保留。每次新的显式复核在既有
`material_request` 链追加 `inventory_notification.source_rechecked`，记录操作者、原因、
原失败审计、前一次审计、原始和本次观察的内容 SHA256、错误类别或新通知 ID/接收项数。
源内容不会复制进复核审计。

幂等键作用域为“操作者 + 原 Outbox 对象”，仅存单向摘要；同一 key 或 request_id 对应不同
复核内容时拒绝。同一命令回读当时已提交的结果，即使后面又有新复核也不改写历史回复。
新命令必须匹配最新审计版本。来源对象 advisory lock 保护上述检查和追加；SQLite 测试
不提供多进程并发保证，生产并发证据必须来自 PostgreSQL 16。

单条投影在保存点中执行，已知来源/通知冲突回滚临时通知后再追加失败结果。结果审计和成功
投影由调用者一起提交；审计不可写或其他基础设施错误让整个请求回滚。来源锁忙时返回 409，
不产生虚假的“已复核”。原隔离仍阻止自动消费重试，成功投影另有通知去重键保证不会重复创建。

## 验证与剩余缺口

- 后端联合聚焦集：66 项通过；包含正式权限拒绝、撤销后重载、只读权限、原内容变化、版本
  过期、幂等原结果、保存点冲突、调用者/审计失败回滚、分页及真实 FastAPI 私有响应检查。
- Web 全量：68 文件、1123 项通过；通知聚焦 16 项包含在内。初轮新增测试缺少组件清理，
  修正测试生命周期后全量通过，没有修改产品逻辑来绕过失败。
- TypeScript/个人仓构建通过，保留既有 bundle 体积提示；公开产物开发检查通过，真实目录仍
  为 pending/0 条，不能据此放行公开发布。
- PG16 专项已接入当前正式 gate：要求真实 API role、有效全国管理员、身份图锁、来源锁、
  消费回滚、原隔离保留、结果审计和重复复核。尚未运行，不计动态通过。

复核本节后端的命令（在 `cloud_oam/backend`）：

```sh
../.venv/bin/python -m pytest -q tests/test_inventory_notification_operations.py \
  tests/test_inventory_notifications.py tests/test_notification_expander.py \
  tests/test_notification_delivery_operations.py tests/test_notification_inbox.py \
  tests/test_pg16_workflow_topology.py
```

未映射身份的补建恢复、历史缺失 Outbox 的受控处理、大历史量 EXPLAIN/压测、区域/全国异常
汇总和真实 provider 仍未闭合。7.33 的后端全量首轮为 4769 通过/3 跳过/1 失败，唯一历史 ACL
测试已修正并复测；那次全量不覆盖 7.34/7.35，不能称为本批全量通过。

用户“全部门禁证据齐全后再提交”的约束仍有效。目标 GitHub 仓库已核实为 PUBLIC，临时候选
CI 提交的知情确认尚未收到，故本节没有提交、推送或部署，也没有调用真实通知渠道。

后续 7.36 已补独立原目标留存与 0109 迁移，详见
[通知原始目标留存](NOTIFICATION_TARGET_RETENTION_20260920.md)。本节 7.35 的“无新增迁移”
属于当时范围；待解析目标列表及身份补齐后的显式恢复仍未实现。
