# 0059–0061 原物料供给计划发布与验收清单

状态：2026-09-05 开发中；新增源码尚未取得准确 SHA 的 PostgreSQL 16 绿色证据。
之前 `0058` 的绿色门禁不能覆盖本次变更。本文件不授权访问或写入任何生产系统。

## 范围和独立状态

仅支持总部管理员对最终批准的当前需求修订建立原物料供给计划、登记参考单号/预计日期、跟进和取消。
跨区域调拨、总部补货、星星补货、外部采购参考均为计划分类，不生成外部单据。
数量创建后不可修改，取消不得同时修改参考单号或预计日期；终态不可重新打开。
活动计划的原物料等价数量之和不得超过最终批准数量扣除已取消数量。
活动替代决定存在时不能新建或继续跟进原物料计划，但可安全取消已有计划。

供给计划不改变审批、分配、占用、出库、发货、物流签收、OAM 收货、RSC/个人仓入库、
通知送达或对账同步。不会产生库存流水、通知事件或 outbox 发送任务。
取消供给计划不取消需求；全部计划结束后，需求取消仍须通过原有逐行取消服务。

## 接口与权限

- `POST /api/v1/material-requests/{request_id}/supply-tasks`：创建，201。
- `POST /api/v1/material-requests/{request_id}/supply-tasks/{task_id}`：跟进或取消，200。
- `GET /api/v1/material-request-supply-command-status?trace_request_id=...`：只读核验历史命令。

写入要求正式需求写开关和幂等 HMAC 配置、准确请求版本及任务版本、唯一当前总部全国管理员授权；
权限须由该管理员授权自身具备，不得拼接工程师/省级角色的权限。接口不解密联系方式，
也不以缺少联系方式解密密钥作为拒绝供给计划的理由。
每个命令推进需求版本，任务创建版本为 0，更新推进任务版本；原审批图锚点保持不变。
事务内命令、审计、状态事件、任务和需求投影必须一致，任一异常整体回滚。

客户端持久化仅含身份、授权版本和命令坐标等最小恢复字段，不含请求正文、参考号、备注、
PII、token 或幂等密钥。确认历史命令与当前详情分别展示，不把历史 `open` 当作当前状态。
账号/权限变化、页面代际变化、证据不足或 `not_observed` 均保留标记，不自动重发。
`not_observed` 不证明命令未执行；尚无安全人工解除协议时不得手工删除标记以重建请求。
该未决状态的受控解除能力仍是后续运营验收事项。

## 迁移与兼容边界

当前开发 head 为 `20260905_0061`；迁移链为 `0058 → 0059 → 0060 → 0061`。
不得修改任何历史迁移。
新增任务 owner-lock guard、延迟因果 validator/dispatcher 与精确列级更新权限；
原位替换 `0045` 终态审批和投影校验函数，允许有证据的供给计划后继版本及原有需求取消。
替换保留既有函数所有者、安全属性、ACL 和触发器绑定；数据库安全清单逐项核验函数体 SHA256。
`0060` 以前向修复补齐数据库即时权限拒绝、每任务历史版本唯一连续以及孤儿供给审计/状态事件
拒绝；历史授权证据与当前操作者授权分别校验，避免撤权污染已提交历史。
`0061` 只为供给审计键中的 JSON 文本提取补足括号，解决 PostgreSQL 在提交阶段将拼接结果
错误解析为 `text ->> unknown` 的问题。保留原函数身份、所有者、ACL、安全属性和触发器绑定；
仅替换该 validator 及 readiness 的函数源，不增加数据表或权限。新 validator SHA256 为
`f5803e9a3e0c931228692260c04c9bd4e544eb147aea45192277e57c7b969403`，readiness 为
`900dd22c6ff47e807dddd6dd6110449e433dde3bc410057ad03472e7216bc53c`。
readiness 精确推进到新 head，禁止新旧服务混跑。

`0058 → 0059` 升级要求供给事实图完全为空；`0059 → 0060` 对已有事实逐项预检，
发现不完整或孤儿证据即整体拒绝升级，不自动修补。`0060 → 0061` 在表锁内核验原目录，
替换后对既有任务及命令涉及的需求重证完整因果图，并拒绝孤儿审计/状态证据；失败整体回滚。
供给迁移降级均要求供给事实图为空。
授权环境中可用以下只读查询判断是否已有供给事实（不能代替迁移的完整因果预检）：

```sql
SELECT
  EXISTS (SELECT 1 FROM public.supply_tasks) AS has_tasks,
  EXISTS (SELECT 1 FROM public.material_request_commands
          WHERE operation IN ('create_supply_task','update_supply_task','cancel_supply_task')) AS has_commands,
  EXISTS (SELECT 1 FROM public.audit_events
          WHERE action IN ('material_request.supply_task.create',
                           'material_request.supply_task.update',
                           'material_request.supply_task.cancel')) AS has_audit,
  EXISTS (SELECT 1 FROM public.state_transition_events
          WHERE aggregate_type = 'supply_task') AS has_state_events;
```

在升级到 `0059` 或任何降级前，任一为真即停止：不得自动删除、补造证据或把原型事实视为正式命令。
需单独审计历史数据并制定前向兼容迁移；本次不提供通用回填。
已产生正式供给事实后禁止从 `0061` 降级到 `0060` 或更低版本，只能前向修复或按获批事故流程
恢复完整备份，不能回退到较弱数据库权限边界。

正式迁移顺序：冻结所有受影响需求写入口、排空旧事务、备份恢复验证、只读预检、迁移、
一次性替换所有后端、readiness/ACL/函数体核验、隔离 UAT，再发布客户端及恢复写入。
当前只允许一次性隔离测试数据库，未授权预生产或生产操作。

## 验收矩阵

- 服务：总部唯一权限/工程师拒绝、最终审批图、精确小数容量、活动替代、状态转移、
  幂等重放与冲突、取消元数据不可变、终态禁止恢复。
- 恢复：原 actor/trace、请求/任务连续版本、审计哈希与相邻链边、历史命令、
  需求最终取消后的历史核验、已见损坏证据返回 503、不把未知当拒绝。
- Web/小程序：创建/跟进/取消、严密回读、脱敏显示、恢复哨兵、刷新/卸载/身份变化、
  并发点击与未决结果阻断、取消权限不能绕过元数据更新。
- PostgreSQL 16：空库升级及空图往返、所有者/ACL/安全属性/函数体目录、真实 API 序列化
  与提交、回滚无残留、直接 SQL 绕过拒绝、双会话超量竞争、库存/通知/outbox 不变、
  原有盘点/审批门禁不退化、有供给事实时降级硬阻断。

准确提交、运行链接及计数应在相应门禁完成后登记；本地 SQLite/静态测试或 PostgreSQL 18
辅助执行均不能替代 PostgreSQL 16 发布证据。

## 已观察结果（非放行证据）

候选 `d8866496d198eb0d15a6e73cc075e97eefcdac3b` 的
[GitHub run 33918927666](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33918927666)
静态发布清单 `1222 passed, 1 skipped, 1 warning`；动态门禁 `1 failed`，停在旧测试仍以
`0058` readiness hash 校验 `0059` 的断言。代码中已修正按准确 head 选择摘要，尚待新候选重跑。
除此之外，独立安全审查发现的数据库权限/版本唯一性/孤儿证据三项缺口由 `0060` 处理。
`0059` 不单独放行。

后续候选 `6e15548cd467c4c8d1dedf633c3ed4b7710a538b` 的本地完整后端及边缘回归为
`2258 passed, 1 skipped`（824.70 秒）；Web 425 项、小程序 216 项通过。
[GitHub run 33920379101](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33920379101)
静态 `1226 passed, 1 skipped, 1 warning`（505.95 秒）；动态 `1 failed`（133.61 秒），停在首次运行时安全清单检查：
真实 `audit_events` 集合新增了 `trg_audit_events_supply_causality_0059`，但
`EXPECTED_AUDIT_TRIGGERS` 漏登记。此问题通过准确新增绑定和集合差集诊断修复，
不删除真实触发器、不放宽集合校验，也不修改历史 `0059/0060` 迁移；修复后的相关数据库安全、
部署安全和边缘数据库安全测试 `305 passed`（3.56 秒），Web 类型检查和构建通过。
仍须新候选真库重跑，不能把这些定向结果当作发布证据。

随后候选 `11f8300a24272d49ff9cccdd28d01342b236f516` 的 run `33921598276` 在静态步骤期间
主动取消：独立只读审查提前发现运行时函数选择器仅覆盖 `_0029/_0030/_0045/_0046`，
会遗漏已经登记在精确清单内的四个 `_0059/_0060` 函数。取消不是通过或真实执行失败。
选择器已补齐两个函数族；同族未知函数仍被选入并由精确集合检查拒绝，未改为仅筛选已知函数。
新增三项回归后，相关安全测试为 `308 passed`（4.18 秒）；等待下一候选的准确真库证据。

候选 `06fcf5a11a50b30cacd23b2807a496e115edcfe3` 的 run `33921958684` 也在动态步骤前
主动取消：独立一次性 PostgreSQL 18 辅助执行已通过迁移及旧盘点链，提前发现供给测试夹具
使用随机新需求 ID，被正式服务的 `material_request_create_id_invalid` 前置校验拒绝。
夹具改为调用正式 `derive_material_request_create_id`，复用同一申请人 principal、幂等键和
HMAC 配置；不放宽创建服务，不把辅助 PG18 结果当作 PG16 发布通过。

下一次全新辅助 PG18 执行进入首次供给创建时，以 SQLSTATE `42501` 拒绝
`substitution_decisions SELECT ... FOR UPDATE`：API 对该表只有 SELECT 权限。
修复只去掉这处多余行锁，保留已先取得的需求父行锁；`0037` 的 ALWAYS 触发器使替代决策
INSERT/UPDATE 同样锁该父需求，API 仍无替代决策 UPDATE/DELETE 权限。
新增 SQL 编译回归证明父锁先于替代决策查询且查询没有 FOR UPDATE；供给服务测试 `7 passed`
（4.04 秒）。这是应用查询修复，不改历史迁移或数据库授权，仍须真库后续执行确认。

再一次全新辅助 PG18 执行到首次供给创建的 COMMIT，报 SQLSTATE `42883`：
`operator does not exist: text ->> unknown`。错误位于历史 `0059` validator 中的
`command_row.idempotency_key_hash || ':' || command_row.result_jsonb->>'task_version'`。
新增前向 `0061` 将末项改为 `(command_row.result_jsonb->>'task_version')`，不修改 `0059/0060`。
已扩展 PG16 真库测试，要求空图升降级前后函数 OID、所有者、ACL、参数、安全属性及触发器
绑定恒等，仅两个目标函数体变化；有供给事实时拒绝降级。新候选尚待真库执行，不作为放行证据。

`0061` 冻结后的定向检查：完整迁移测试 `167 passed`（226.68 秒），数据库安全及投影安全
`298 passed`（2.39 秒），供给/历史状态/需求查询/API 服务回归 `147 passed`（51.81 秒）；
repository-safety 检查 `550` 个候选文件通过。升级预检经过独立字段复核：供给任务经
`request_line_id → material_request_lines.request_id` 关联需求，三种供给审计 action 指向的
真实需求也纳入重证集合，避免仅枚举任务/命令时遗漏未消费审计。完整静态与真库结果仍待登记。

同一冻结候选在全新一次性 UTC PostgreSQL 18 辅助环境中执行完整动态流程，结果
`1 passed`（191.19 秒）：覆盖 `0061` 空图往返、旧盘点链、供给首次提交、真实 API、直接 SQL
权限拒绝、历史操作者撤权后的合法跟进、状态核验、并发容量竞争、回滚及有事实降级阻断。
辅助 runner 仅调整两处 PostgreSQL 主版本断言和 PG18 新增 NOT NULL catalog 项的查询差异，
不修改业务逻辑；自建数据库已停止。此结果不能替代官方 PostgreSQL 16 门禁；本地完整静态
仍在运行，后续需绑定准确候选 SHA 和 GitHub run。

当前正向连续历史及精确 SQL 静态断言已覆盖版本连续约束；完整恶意历史 `0,1,1,3` 图在
`0059 → 0060` 升级时拒绝的专项真库夹具仍需补充，不能声称该反例已经过真实执行。
