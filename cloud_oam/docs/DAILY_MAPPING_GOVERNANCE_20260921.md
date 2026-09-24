# 7.94 日终映射批准、撤销与历史证明候选

2026-09-21。[两侧采集与计算适配](DAILY_CAPTURE_BRIDGE_20260921.md)之后，本批补充明确的
仓库、库位、区域和库存状态映射授权记录。**28 个实际 PG16 场景通过**；新增合成批准、
撤销、替换三条不可变记录，均绑定正式授权审计。六张库存事实表全程不变，三个实例已停止。
当前仍是忽略 artifacts 内的接入候选，真实业务映射尚未获批。

[固定证据](../artifacts/daily-mapping-governance-20260921/verification.json) SHA256：
`d85acb738422b38555882c661241d3e3d6d16581bb9c44bb8aeaa9ce77f40651`。
最终原生会话 **96369 退出 0**；先前 **67324 退出 0（27 项）**保留，最后一次补齐数据库
实际函数、触发器、ACL 目录与完整授权记录的留存和独立复核。

## 新增的候选行为

[领域服务](../artifacts/daily-mapping-governance-20260921/daily_mapping.py)与
[记录模型](../artifacts/daily-mapping-governance-20260921/daily_mapping_models.py)从现有 0115
规则授权机制派生，源码输入固定摘要。新增 `daily_comparison_mapping_decisions`，不把跨系统
库位映射塞入原“物料状态 → 成色”规则记录，也不改写期初或通用 reconciliation 表。
[生成入口](../artifacts/daily-mapping-governance-20260921/build_candidate.py)保留差异生成方式；
解冻后须整理为正式模块和前向迁移，不能在部署时运行生成器。

- 预览、执行及原请求恢复使用签名 Web JWT、当前会话/IP 证明、授权版本和全国总部管理员
  权限。当前候选明确复用 `inventory_control.authorize`；没有新增普通员工权限。
- 操作者、会话、绑定和批准附件锁定后生成审核摘要；请求键/请求号、映射版本、原目录摘要、
  完整请求及审核摘要共同绑定。凭据本身不写入授权记录。
- 映射包含每个库位的明确区域和目标仓库，以及纳入状态。全部库位和组织事实摘要必须与
  当前库中一致；目录目标仓覆盖、缺失库位、重复库位、错误区域或过时摘要均拒绝。
- 活跃版本的时间窗口不能重叠，版本号不可复用。撤销引用原批准的准确摘要；历史批准和
  审计保持可证明，但不能继续作为当前有效版本。替换必须显式建立新版本。
- 两个并发相同请求只生成一条批准记录；变更请求内容但复用键会拒绝，原请求可准确恢复。
- 映射审核期间对组织/库位表持 SHARE 锁，阻止并发修改或插入造成范围变化；不锁库存余额
  与流水。该范围覆盖全部参考目录，正式接入前仍须纳入完整锁顺序和规模评审。

## PostgreSQL 守卫与实际验证

[目录校验 SQL](../artifacts/daily-mapping-governance-20260921/validate_mapping.sql)和
[记录守卫 SQL](../artifacts/daily-mapping-governance-20260921/guard_mapping.sql)实际安装到
全新恢复的合成应用数据库中。校验同时由预览和 INSERT 守卫执行；撤销使用原批准的内容，
历史证明不因当前参考目录变化而被重解释。

数据库要求直接 schema owner、当前总部权限、会话时间及准确审计绑定。记录 UPDATE、
DELETE、TRUNCATE 都被始终启用的触发器拒绝。实际目录核对了两个函数的完整源码摘要、
SECURITY INVOKER、固定 search_path、所有者及仅所有者 EXECUTE ACL，两个触发器均为
ALWAYS。API、projector、Edge 角色没有新表读写权限；备份角色仅 SELECT。

28 个场景覆盖有效批准、只读预览、原请求恢复、伪造 token、错误授权版本、失效会话、
错误审核摘要、键冲突、范围缺失/重复/错误、目录变化、版本重叠、并发参考写阻断、记录
不可变、无权角色拒绝、撤销与历史保留、双请求并发和备份权限。实际执行变化仅限映射
记录、授权 audit_events 和 audit_chain_heads；另外专门在夹具中撤销测试会话验证拒绝。

[三条完整映射记录](../artifacts/daily-mapping-governance-20260921/decision-facts.json)与
[对应审计记录](../artifacts/daily-mapping-governance-20260921/audit-facts.json)已留存；数据库中的
既有审计证明函数逐条复核历史链，离线复核器再校对完整 payload、规则、请求、映射摘要和
审计事件字段。离线仅保存三条对应事件，不冒充完整授权审计链备份。

首次 **26235 退出 1**，原因是新 SQL 把映射对象的 13 个字段误写为 14；原始失败、代码和
实例保留在 [first-attempt-field-count](../artifacts/daily-mapping-governance-20260921/first-attempt-field-count)。
修正后在新实例完成 27 项，随后又在新实例补齐目录证据，最终 28 项。三个实例系统标识均不同，
服务器退出 0、停机日志、postmaster PID 文件及 socket 消失均核对；未关闭守卫、未重启旧目录。

## 仍未完成的上线工作

本批尚未进入正式 Alembic 链或应用安全目录；7.95 [不可变截止候选](DAILY_CUTOFF_PERSISTENCE_20260921.md)
已补截止事实持久化及验证，仍没有日终状态投影、操作 API、PC 审核页、
调度或真实三天验收。正式批准记录还需被日终截止绑定引用，且授权范围不能由上传 JSON
或布尔值代替。源当前可用性、操作者区域权限、证据附件生命周期、整体锁顺序及角色 DDL
并发仍须在最终日终写入口统一复查。本候选不是 GitHub PG16 发布门禁。

完整静态会话 `9496` 仍收集结果；已发现 `test_cli_head_pin_tracks_alembic_graph` 失败：
`configure_inventory_control.py` 固定 0128，而当前迁移 HEAD 为 0129。完整运行结束前不改
1,364 个冻结输入，结束后先精确修复该 CLI 固定值并完成必要聚焦与完整门禁，保留原始失败。
整体范围、额度和接续动作见[续开发交接](CONTINUE_DEVELOPMENT.md)。
