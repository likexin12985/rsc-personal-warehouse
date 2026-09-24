# 物料来源业务授权与采集准入（7.54）

> 7.55 已完成未知来源时间的可空迁移与目录/双端契约，当前 head 为 0118；
> 来源版本/正式物料投影仍待实现，见[来源时间接续](MATERIAL_SOURCE_TIME_20260920.md)。以下保留 7.54 原证据。

在 [7.53 认证接收](MATERIAL_CAPTURE_INGRESS_20260920.md) 上增加总部对准确物料来源的批准与撤销。
传输登记、来源业务授权、正式物料版本、库存发布和启动仍是独立事实。本批完成前两者的组合检查，
不生成正式物料或库存，不把采集回执改成发布许可。

## 准确授权与恢复

新增 `20261027_0117` 和 `material_source_authority_decisions`，只追加决定和授权审计。
新增 `material_source.authorize`，仅种给内部总部 admin；执行要求当前有效 RSC 网页会话、
正式身份、全国授权、预期 authorization_version 和审核文件，不接受自由填写操作者。
普通 API、edge、projector 没有新表权限；直接 migrator 执行，backup 只读。

批准准确绑定 0116 的传输登记 ID、source_system_id、wire 来源/范围/端点、key_id/指纹和
登记有效期。时间以整数微秒参与来源摘要，避免 Python/PostgreSQL 时间字符串格式差异。
本地来源必须为启用且 read_only 的 `oam`，wire 仍为 `starcharge_oam`，不按名称猜同一来源。
授权要求明确截止时间，不得回溯生效、越过传输登记窗口或与未撤销的有效区间重叠。

预览绑定原命令、当前身份版本、来源、审核文件元数据、传输撤销状态和既有决定历史。执行重读
相同预览摘要；原 actor/request_id/idempotency_key 只能恢复完全相同的命令与预览。
撤销指定原 grant ID 和 payload 摘要，即使传输或来源已停用也能撤销并恢复历史决定。
撤销不会修改旧事实，重新授权不能让撤销前的旧采集重新获得准入。

决定正文保存原会话坐标、审核文件摘要和不可变审计关系，不保存 JWT。预览不返回文件 storage_key。
COMMIT 成功但确认丢失返回结果未知，只允许精确 status 回查；not_found 仍为 retry_allowed=false。

## 运维入口

`scripts/configure_inventory_control.py` 新增 `--material-source-file`（preview/apply/status）和
`--material-inspection-file`（inspect）。现有控制授权、映射与签名交接模式保留；物料新模式尚未
接入 PC 页面或自动交接。生产预检要求直接 migrator、准确数据库名、真实 PG16 和 head 0117。
命令默认预览，不自动迁移、不自动重试；凭据只通过隐藏终端输入或独立继承 fd 提供。

来源命令文件信封包含 `command`、`expected_authorization_version`，执行/查询再附原 `review_sha256`。
command 包含 action、binding_id、expected_subject_sha256、evidence_file_id、evidence_sha256、reason、
valid_from、valid_to、revoked_grant_id、idempotency_key、request_id。批准的 expected_subject_sha256 为
准确传输 subject 摘要；撤销时改用原决定 payload_sha256，并令 valid_from/valid_to 为 null。
新授权 valid_from 可空，表示执行时立即生效；valid_to 必须明确且处于传输登记窗口内。

下列为部署后的操作模板，本批未执行真实批准或数据库命令：

```bash
.venv/bin/python scripts/configure_inventory_control.py --material-source-file /private/material-source-command.json
.venv/bin/python scripts/configure_inventory_control.py --material-source-file /private/material-source-command.json --mode apply
.venv/bin/python scripts/configure_inventory_control.py --material-source-file /private/material-source-command.json --mode status
.venv/bin/python scripts/configure_inventory_control.py --material-inspection-file /private/material-receipt-inspection.json
```

检查文件只含准确 receipt_id 和 expected_authorization_version。必须先完成授权，再做新采集和
认证接收；对已经采完的内容补一个新 grant 不构成当时的授权。

## 采集准入范围

同一事务内锁住准确 binding、来源和所用审核文件，重验不可变决定及审计、原始采集契约、密钥
和有效期。授权需覆盖采集开始至接收时刻的完整闭区间及当前时点；连续自然续期可以组合，
一微秒空隙、到期、显式撤销、文件隔离/变化或来源停用均拒绝。等待锁后再次检查时效，采集
不得超过 45 分钟。准入不要求接收完成至当前时点之间每一时刻均有授权。

成功返回 source_authorized=true，并列出准确授权片段、当前决定和 valid_until；它只在持锁
事务内有效，CLI 随后回滚。full_catalog_verified、master_source_evidence_verified、
projection_published、start_ready 保持 false。未来 publisher 必须在自己的发布事务内重做证明，
不能缓存本次检查 JSON 当凭据。明确空观察也不删除正式物料。

数据库守卫为 owner-only SECURITY INVOKER，2 个 ALWAYS 触发器和函数摘要纳入运行清单。
空表且种子权限完整才能降级；已有决定、孤立授权审计或权限目录发生变化时先阻断，保留证据。
历史 0116 及更早迁移不修改。传输登记脚本的当前 head 预检同步到 0117，登记本身仍不代替批准。

## 验证与接续

最终大范围联合 **1244 passed，1 warning，67.63 秒**，覆盖来源授权、HMAC 接收、原控制授权/
映射/准入链、安全清单、部署契约及完整 SQLite head 往返。使用真实 JWT/CLI/SQLite 提交和
合成 OAM/Edge 输入；结果未知测试先实际 commit 再抛出确认丢失，status 找回同一条事实。
首轮身份夹具遗漏 revoked 状态、迁移权限清单和安全清单固定数量遗漏均已修正，失败日志保留。
收尾另补空表降级时的权限目录漂移保护，最终专项 **68 passed，1 warning，19.33 秒**，
含完整 SQLite head 往返，不与大范围结果累加。该专项首轮 67 passed/1 failed，新增角色
夹具使用了基线不允许的角色 code；改为现有 provincial_manager 后通过，原日志保留。

真实 PG16 helper 已接入历史保留检查之后，准备验证双连接幂等、binding/文件/来源/撤销锁、
角色 ACL、backup、不可变和降级保留；本批未运行。SQL/PLpgSQL 可解析、SQLite 通过不能替代真库。
未提交、推送、部署、真实来源读取、批准、上传或通知；原 CI 候选提交例外仍待答复。

正式物料模型和查询接口仍要求非空 source_updated_at；下一步须前向迁移为真实未知时间可空，
并实现不可变来源版本与正式投影。状态、基本单位、SN/批次语义尚无真实确认，不用采集时刻
或公开知识库补造。B1 独立控制 publisher、自动交接/审核文件验收/密钥治理、B3 持续对账、
B8 当前候选真库与正式角色/真机，以及通知渠道、迁移/恢复/压测验收继续保留。

完整命令、退出码、源码/日志摘要位于 `cloud_oam/artifacts/material-source-authority-20260920/`。
7.53 及此前证据保持为各自历史快照，不把前批结果扩展为本批生产证据。
