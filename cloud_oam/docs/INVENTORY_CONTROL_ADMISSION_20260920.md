# 控制采集的有效授权核验（7.49）

> 后续 7.50 已增加[数量正规化审核](INVENTORY_CONTROL_NORMALIZATION_20260920.md)及检查文件可选
> `normalization_rules`。以下保留 7.49 原始验证范围，审核候选仍不等于发布许可。

依据 V1.0 继续 B1 控制投影。7.48 已证明配置渠道的 HMAC 声明和准备链一致性；本批将这些
凭据与不可变来源/目录决定组合，核验每段库存页采集区间及检查当时的有效授权。

本批没有新表、迁移或数据库权限，head 保持 `20261024_0114`。不访问真实 OAM，不批准真实
来源/目录，不发业务通知，不启用调度，也未提交、推送或部署。

## 核验规则

- `inventory_control_admission.inspect_inventory_control_admission` 重新验证准备图、准确 binding、
  全部授权决定及审计链、每段 HMAC 凭据和当前新鲜度，不采用此前保存的布尔结果。
- 逐段核对声明中的库存采集区间，使用准确目录版本的授权及其来源父授权。事后新增授权不能追认
  早于生效时间的采集；已有准备记录可以供审核，但批准后必须重新采集才可能通过检查。
- 授权区间为 `[valid_from, valid_to)`，采集结束端点也必须有效。连续自然续期允许跨越一个或
  多个授权版本，逐段保留实际决定 ID 与摘要；空档、重叠歧义及到期端点均失败。
- 历史采集与当前时点可以使用不同的自然续期授权。显式撤销的来源或目录决定不能再支持新发布
  核验；新增替代授权不会追认其旧采集链。该规则不删除已发生的采集、授权或历史发布事实。
- 当前来源必须仍启用且为只读 OAM，区域必须仍有效；使用到的审核文件须 available，内容摘要、
  大小、类型及存储绑定与原决定一致。无关历史决定的失效文件不冒充本次使用的证据。
- 当前数据库时间在等待锁和完成核验后再次检查。返回摘要、准确授权分段、当前决定及最迟失效
  时间，不返回库存原始行、审核文件私有路径、OAM 凭据或网页登录凭据。

## 并发与权限边界

检查需要直接 owner 身份和 READ COMMITTED 事务，先锁准确 binding，再以共享锁保护当前
source、region 和实际使用到的文件。授权/撤销写入使用同一 binding 锁，调用方事务结束后才
释放。拒绝重复读和可串行化事务中的历史快照，防止它们在等待锁后仍看不到刚提交的撤销。
不获取全局授权审计写锁，也不修改授权、审计、准备、SyncRun 或库存事实。

新增 `inventory_control_configuration.inspect_inventory_control_capture` 使用当前 RSC 网页
会话核验总部全国配置权限、已验证身份和授权版本，完成时再验证会话。API、edge、projector
和 backup 不获得该 owner 入口权限，普通 API 进程仍不持有 owner DSN。

输出中的 `capture_authorized/source_authorized/catalog_authorized` 表示这些准确证据在本次
检查通过；`source_authenticated/capture_attested` 沿用配置渠道的证明边界。
`trusted_key_lifecycle_verified/projection_published/start_ready` 仍为 false。当前部署密钥
注册、轮换和撤销生命周期尚未闭合，这一结果也不证明真实来源事务快照或原审核文件的在线验收。

此结果是有时效的观察，不是可携带到其他事务重复使用的发布许可。未来 publisher 必须在自己的
事务内重取准确锁、重证授权/凭据与时效，并由数据库边界保证原子发布；不能复用这次输出直接改
发布或启动布尔值。来源更新语义、正规化和独立 publisher/RLS 仍未完成。

## 运维检查入口

已有配置命令新增 `--inspection-file`，该文件仅接受以下字段（示例 ID 必须替换为准确准备 ID）：

```json
{
  "preparation_id": "11111111-1111-4111-8111-111111111111",
  "expected_authorization_version": 1
}
```

在已经配置专用 owner 数据库目标的受控运维端执行：

```bash
.venv/bin/python scripts/configure_inventory_control.py \
  --inspection-file /absolute/private/inspection.json
```

交互终端隐藏输入当前网页登录凭据；自动受控运行继续使用原 `--access-token-fd` 机制。
凭据不放参数、JSON 或环境变量。此模式完成后 rollback 释放检查锁，永不 commit；不能混用
`--mode apply`，也不能用普通 command/handoff 文件冒充检查。失败只输出脱敏错误，不自动重试。
现有 preview/apply/status 和双向签名交接保持；PC 到此检查的自动受限交接仍待接入。

## 验证与后续

证据目录：`cloud_oam/artifacts/inventory-control-admission-20260920/`。准确命令、最终数量、
退出码、源码/日志摘要及未运行范围记录于 `verification.json`，7.48 原证据保持。

最终采集/授权/交接/权限/完整 SQLite head 迁移/部署合同联合 **936 passed，1 warning，69.45 秒**。
此前专项 28 项、组合 199 项均通过，不累加重叠次数。首轮检查全表事实时产生的测试夹具表排序
警告已修正，最终仅保留上游 TestClient 弃用提示。保护 PG16 及未托管本地读客户端模块共
**2 skipped，0.34 秒**，不算真库通过。Web/小程序和 114 个既有迁移文件未改，没有重跑双端构建。

本地测试串联真实 HMAC、实际接收/commit、SQL 创建的 0112/0113/0114 事实、正式权限与 JWT
会话、授权观察和 CLI 回滚；来源及文件全部为合成数据。覆盖全量/增量/显式零库存、追认拒绝、
撤销、自然续期、边界空档/重叠、文件失效、检查中过期及所有数据库事实前后不变。

PG16 门禁已加入真实独立连接：检查持锁时，撤销和文件/来源变更应锁等待超时；检查结束后显式
撤销成功，下一次检查应拒绝；另核验历史快照隔离级别与非 owner 身份拒绝。上述动态门禁尚未
运行，保护跳过不算通过，也未绕过当前候选提交所需的用户例外确认。

下一步继续密钥生命周期、受控审核原文件验收、SKU/品相/单位/数量正规化、独立 publisher/RLS
和实际发布事务内复核；随后接批次选择、启动恢复与持续对账。真实 PG16、通知渠道、迁移、恢复、
UAT 和压测证据仍保留。公开知识目录 pending/0 条，首页查询和星星管理按钮 `/xx` 保持。
