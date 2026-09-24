# 库存控制采集凭据与隔离接收（7.48）

接续 7.49 已增加采集区间与当前时点的有效来源/目录授权组合，以及当前总部网页会话认证的
只读检查入口；发布、启动和密钥生命周期仍未闭合。见[最新授权核验](INVENTORY_CONTROL_ADMISSION_20260920.md)。
以下保留 7.48 当时的结果与边界。

继续正式 V1.0 基线的 B1 控制库存链。已有严格采集器现在可以向隔离接收端提交带 HMAC 的
摘要声明，后端追加不可变接收凭据，再由准备记录观察服务核对整条采集链。本批使用合成来源和
SQLite，未访问真实 OAM、签发真实授权、启用调度、提交、推送或部署。

## 接收与证明边界

- 新增 `POST /api/integrations/oam/edge/inventory-control/captures`，仅属于原隔离 ingress。
  复用现有独立 Edge HMAC、来源白名单和签名时效，不向主 API 开放入口或凭据。
- 声明仅含 18 个字符串字段：契约版本、部署密钥版本、准确来源/企业/组织/范围、快照及时间、
  目录版本/区域、绑定/目录/采集链摘要和采集区间。请求不重复携带库存原始行。
- 验签后要求规范 JSON 字节、摘要、签名头、来源、密钥版本和请求 ID 一致；锁定准确的已完成
  inventory 快照，核对时间和绑定摘要。签名验证不超过 5 分钟，采集开始距接收不超过 45 分钟。
- 凭据保存密钥版本及按来源分隔的 HMAC 指纹，不保存原密钥、签名、OAM token、Cookie 或 JWT。
  同快照相同声明只返回原凭据；不同内容或密钥指纹冲突不覆盖原记录。密钥轮换必须更换版本名。
- 接收回执中的 `capture_attested` 表示配置渠道已经认证该声明。独立观察函数
  `observe_inventory_control_attestation` 才逐段重证准备链、每个前缀摘要、目录、准确暂存关联、
  采集区间和最新时效；缺任一凭据、内容不符或过期即失败。
- 观察结果的 `source_authenticated/capture_attested=true` 只证明上述配置渠道及一致性。
  原准备/授权接口不改变布尔结果；`projection_published/start_ready` 始终为 false。
  这不证明 OAM 原始数据正确、来源提供了事务快照，或该目录已获得有效审批。

## 迁移与权限

新增前向迁移 `20261024_0114`，原 0113 及更早迁移不改写。新表
`inventory_control_capture_attestations` 唯一关联快照与来源请求，使用 4 条 FORCE RLS 策略及
2 个 ALWAYS 触发器。`edge_inbox` 仅能在当前准确 inventory binding 内 SELECT/INSERT；
backup 只读，主 API 和 projector 没有该表权限。owner 读取保留，但直接新增凭据也要求实际
`session_user=edge_inbox`，owner 不能替代接收端身份伪造接收事实。

UPDATE/DELETE/TRUNCATE 被拒绝；有凭据时阻止降级并保留历史。新增私有 SECURITY DEFINER
守卫固定 owner、`search_path=pg_catalog` 和源码摘要，不向运行角色开放 EXECUTE。
启动检查同步核对表、策略、触发器、函数、ACL 及 head。部署授权脚本重新执行后也会保留新表的
最小授权，核验脚本同步覆盖；没有给主 API、projector 增加业务写权限。

新增的是 OAM scope 家族的 1 个守卫及 2 个触发器；原 material-request 检查目录的
92 个函数/309 个触发器不是整个数据库总量，不能将其与这批新增数量混为一谈。

## 配置与本地恢复

默认关闭采集凭据。部署到 0114 并审核准确来源 binding 后，在受保护的接收端配置中指定
`RSC_EDGE_CONTROL_CAPTURE_KEY_ID`，配置生成器才输出 `OAM_EDGE_CONTROL_CAPTURE_ENABLED=true`
和相同版本名。该值标识现有 `RSC_EDGE_SYNC_SECRET` 的部署版本，不是另一份密钥。
配置文件从首次写入即为 0600，目录 0700，替换后同步目录；不输出凭据值。

本地控制采集同时需要 `--control-attestation-key-id`，也可由
`RSC_EDGE_CONTROL_CAPTURE_KEY_ID` 提供。缺少它会在非 dry-run 采集前拒绝启动。
原 `--control-catalog-file` 与 inventory-only 限制继续生效，参见
[采集命令与证据格式](INVENTORY_CONTROL_CAPTURE_20260920.md)。

上传先完成批次及快照，再提交摘要声明；只有准确凭据 ID、来源、快照、密钥版本、摘要和阶段
回执全部匹配，才推进本地索引。超时、缺失或错误回执保留原 outbox 与私有证据；下一周期先
隔离未知旧包并重新查询来源，不自动重放。现有默认 all 模式和调度状态未改变。

## 验证与后续

本批日志和源码摘要保存在忽略目录 `cloud_oam/artifacts/inventory-control-attestation-20260920/`，
最终准确命令、结果、会话退出码和限制以 `verification.json` 为准。7.47 证据原样保留。

最终采集/凭据/准备/授权/配置/交接/权限/迁移/部署联合 **902 passed，1 warning，67.27 秒**。
警告为上游 TestClient 弃用提示。PostgreSQL 16 保护入口及未托管的本地读客户端模块共
**2 skipped，2.06 秒**，不计作真库通过。Python 依赖、编译和安装脚本语法通过。
前序夹具遗漏、旧 head 前驱断言及 SQL 解析测试参数替换遗漏已修复，失败日志保留；各轮测试
有重叠，不累加通过数。没有重跑完整后端、Web 和小程序全套，双端源码与 7.47 比较未变。

已覆盖真实 HMAC 验签、签名内容/密钥轮换冲突、失效时间、缺失/过期凭据、全量/增量/显式零库存、
调用方回滚、提交回执丢失后的原凭据恢复、SQLite 不可变保护和 head 升降级。只替换最终共享
Edge 传输为进程内接收器，不构成真实来源或网络验证。

PG16 门禁已接入实际 edge 身份、首次并发接收、精确 RLS、API/projector 拒绝、backup 只读、
owner 修改/删除/清空拒绝、范围撤销及有事实禁止降级。其动态结果仍未取得；SQL/PLpgSQL 解析、
源码摘要和 SQLite 通过不能替代 PostgreSQL 16。保护入口的跳过不计作通过。

当前候选未经提交。用户要求证据齐全才提交，先行公开 CI 候选提交的例外仍未获答复；未伪造 CI
或 disposable 环境声明，也未将此缺口绕开。B1 下一步是部署身份及密钥生命周期、采集时/发布时
有效来源和目录授权组合、审核原文件受控验收、正规化与独立 publisher/RLS，随后原子发布和启动
恢复。自动配置交接、B3 持续对账、B8 真库/真机及通知渠道、恢复和压测验收继续保留。

公开目录仍 pending/0 条，网站和小程序保持“交流备件知识大全”；网页星星管理入口 `/xx` 保持。
