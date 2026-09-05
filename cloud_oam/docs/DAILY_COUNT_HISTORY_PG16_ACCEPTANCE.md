# 日常盘点多轮历史查询：PostgreSQL 16 验收

日期：2026-09-05。遵循根 AGENTS.md、V1.0 和独立状态边界。
本切片从三轮隔离测试开始，真库暴露权限和封轮时序问题后，修复 17 处锁后回读及事务内封轮顺序。
不修改数据库迁移、ACL、客户端、边缘采集或部署，不合并业务状态。
没有访问 OAM、RSC、Workflow、飞书、阿里云或任何业务生产系统。

## 候选与证据

已验收修正提交：`2691ab3686c139e5b3ee35140ed39cfe45d4382b`。
`976cdba`、`ee0d0ed` 的真库运行均失败，不计全绿；本修复不代表所有历史恢复缺口已经解决。

- 历史查询、复盘执行及复核相关三文件：`103 passed`（64.32 秒）。本轮累计新增 13 项历史查询回归。
- 既有脱敏诊断回归：`13 passed`（0.16 秒）。
- 修正后本地后端/边缘完整回归：`2939 passed, 1 skipped`（909.10 秒），退出码 0。
  唯一跳过项是限定 GitHub 一次性环境的真库门禁；不能计作本地 PG 通过。
- 准确 SHA 的 PG16 [run 33965156443](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33965156443)：
  静态 `2006 passed, 1 skipped, 1 warning`（569.04 秒），动态 `1 passed, 1 warning`（186.61 秒）。
  包含容器清理在内全部步骤成功，job 于 `2026-09-05T12:21:41Z` 完成。
- 准确 SHA 的客户端 [run 33965156446](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33965156446)：
  Web `776 passed`、小程序 `647 pass / 0 fail`，类型检查、构建、安全扫描及清理全部成功。

完整回归期间后端、边缘及 PG workflow 与该 SHA 保持一致；迁移 head 仍为 `20260905_0061`。
后续文档提交不得改变上述已验收业务代码；任何新代码仍需重新验证准确 SHA。

## 失败证据与最小修复

`976cdba` 的 PG16 [run 33963127979](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33963127979)
静态 `2002 passed, 1 skipped, 1 warning`（754.85 秒），动态 `1 failed, 1 warning`（213.74 秒）。
失败在第二轮首次历史 GET：预期 `not_observed`，实际为证据不可用 503；容器日志确认
`permission denied for table stocktake_recount_cases`。该候选本地全量 2935 项通过、1 项跳过，
客户端 run `33963128025` 成功，都不能替代失败的真库验收。

API 对复盘案例、分配、范围、快照、计数/封轮/差异证据及附件绑定没有 UPDATE 权限。
PG 即使已由 0032 owner 函数持锁，后续直接 `FOR UPDATE` 仍检查调用角色权限；SQLite 会忽略该语句。
两复盘服务共 17 处改用既有 `_select_only_reference_statement`，在 PG owner 锁后普通回读，
保留 `populate_existing` 与全部业务校验。附件依靠 0057 task-owner 插入封印和不可变绑定保护；
task、round、freeze、RoleAssignment、FileObject 的真实锁均保留，没有扩大任何表权限。
location 的尾部 `lock_rows=False` 校验保持普通 SELECT。

新增实际服务查询的 PG 方言编译回归，并单独检查可变文件分支保留行锁。
真库 HTTP 服务调用增加同工作线程的既有脱敏诊断包装，只允许诊断字段，不输出 SQL、参数或原始异常。
普通服务/HTTP 错误契约不变。消除权限错误不等于完整 owner 图或历史终态兼容已经完成。

随后 `ee0d0ed` 的 PG16 [run 33964225622](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33964225622)
静态 `2004 passed, 1 skipped, 1 warning`（716.84 秒），动态 `1 failed, 1 warning`（223.26 秒）。
首次第二轮 GET 已越过原权限问题，但第二轮 count 写入触发 `P0001`：
`rsc_validate_stocktake_recount_round_0032` 第 39 行拒绝不合法父状态。
该候选本地全量 `2937 passed, 1 skipped`（814.27 秒）、客户端 `33964225621` 全绿，
仍不能替代失败的真库运行。

原服务同时修改 round/task 后一次 flush，ORM 可按依赖先把 task 更新为 submitted，
与 0032 即时 round guard 要求 parent counting 冲突；SQLite 历史 UPDATE guard 没有该检查。
`2691ab3` 在 task 仍 counting 时先 flush round，再更新并 flush task，保持同一业务时间和
同一事务，不在中间 commit。提交时仍由原延迟全图守卫校验完整结果；没有修改函数体或 SHA。
新增隔离 SQLite 的 PG 等价即时状态检查已真实复现旧实现失败；另验证 round SQL 已成功后
故意拒绝 task 更新，由调用方 rollback 后所有计数、轮次、任务、审计/Outbox 行恢复原状。

## 新增真实链路

在既有隔离 PG16 集群中增加专用库位、保管关系和零余额账户，不复用尚有冻结的并发测试库位。
正式服务使用 `star_oam_api` 角色和数据库当前时间，每一步独立提交：

1. 创建抽盘任务、硬冻结启动；截止快照为零。
2. 第一轮实盘 1 件，产生真实差异；区域复核要求复盘，开启第二轮。
3. 第二轮再次实盘 1 件，经复盘差异与区域复核，开启第三轮。
4. 第三轮计数提交，保持独立的第三轮 completion/submission。

每轮提交前的原 trace 返回 `not_observed`；每轮提交及开启后继轮后，逐一回查所有历史轮。
初盘、第二轮、第三轮回执必须对应各自 scope、completion、轮次、时间和封轮关系，不能
因任务已进入第三轮而把前两轮当作失效或把同一个 scope 的不同轮次混为一笔。

GET 经过真实 FastAPI router、API-role Session、数据库身份加载、权限与业务证据检查。
身份传输依赖被注入，不代表认证协议、手机验证码或微信登录已经联调。
错 operation、错人员、管理员冒用原执行人哨兵必须失败，成功和业务错误都验证隐私头。

查询前后比较完整持久化行，而不是只比较条数：库存/ledger、审计头与审计、Outbox、
状态事件、范围/冻结/快照、计数/提交、差异、复核和复盘案例/分配。
包含复合主键表，按全部主键列稳定排序；没有 `id` 列的逐 SN 证据也能正确读取。
GET 不结束调用方事务；结束及回滚由测试请求依赖负责。

## 合成回归

新增 13 项覆盖三轮已打开/已提交/已评估、各轮回执独立、跨轮 trace 错配、祖先轮证据损坏、
只发 SELECT 且不 flush/commit/rollback，以及 PG 全行快照工具的复合主键兼容。
还包括 PG 只读表语句编译、可变文件锁保留、即时封轮顺序及失败原子回滚检查；
编译和 SQLite 等价触发器检查不替代真库 owner/ACL 验证。
损坏来源时原 trace 与未见 trace 均失败关闭，不能把证据错误降级为“未观察到”。

```sh
PYTHONPATH=cloud_oam/backend python -m pytest -q cloud_oam/backend/tests/test_stocktake_count_command_status.py
PYTHONPATH=cloud_oam/backend python -m pytest -q cloud_oam/backend/tests cloud_oam/edge_sync
bash cloud_oam/scripts/verify_repository_safety.sh
git diff --check
```

本地不运行 PG16 破坏性集群测试，不伪造 GitHub runner 标记，不连接本地或共享数据库。
真库唯一入口是现有私有仓库一次性 PostgreSQL 16 release gate。

## 仍未验收及客户端接入前置

1. 本次真库链仅单范围、硬冻结、无附件、非 SN，停在第三轮 submitted。
   不证明非封轮多范围、文件并发、截止回放、换执行人或复盘过账/关闭后恢复。
   专用冻结随一次性测试集群销毁；不得为清理直接改历史冻结。
2. 源码确认：`stocktake_review._recompute_initial_count_and_difference_graph` 和
   `stocktake_recount_difference._load_evaluation_inputs` 复用当前 scope 资格与活动冻结检查。
   `stocktake_posting` 正式过账会释放冻结。因此复盘历史在过账/关闭后会受到该检查阻断；
   不相关旧范围执行人停用/保管变化也可能牵连当前合法查询。需贯穿递归来源的专用历史模式，
   不能放宽写侧前置、伪装当前时间、跳过来源证明或改历史数据。
3. 源码集合核对发现尚未闭合的 owner 锁范围：0032 只覆盖任务内图及部分引用，未覆盖
   所有回放端点、截止后范围账户、缺失/回放 SN 和相关维度。递归来源可在最终全任务
   文件集合锁前先读取祖先附件并锁定其文件；不能把当前代码注释当作一次完整排序 union 的证明。
   这是待处理的锁图风险，不是已复现死锁。本次无附件/无回放测试不能替代并发验收。
   现有复盘计数/差异写入口仍在 0032 后才补 principal 锁；本次权限兼容修复未调整该锁序，
   下一完整 owner 切片应一并验证，不能把少发无权 FOR UPDATE 描述成顺序问题全部修复。
4. 下一步先设计并验证历史专用完整 owner 入口及显式历史证据模式；需要改数据库函数时
   新增前向迁移、最小权限与函数体 SHA 清单，禁止改写 0032/0057 等历史迁移。
   通过释放冻结、历史人员变更、附件逆序竞争、SN/截止回放及尾部撤权回归后，再接客户端。
5. 两端持久恢复、人工未执行封存及创建/启动/差异/复核/过账/关闭的独立恢复仍未交付。
   `not_observed` 不清标、不换键、不自动重放；库存、通知和外部同步状态保持独立。

准确 SHA 门禁通过只证明上述受限切片，不等于一期完成、UAT 通过或生产放行。

## 下一个修复切片的技术边界（方案，未实施）

### A. 独立历史 owner 入口

可借鉴 0057 的完整回放账户/引用集合，但不能调用它冒充历史接口：该入口限定当前第一轮
submitted。0032 可在完整引用集合预锁后复用其任务内历史锁图，不能改写既有函数。
若下一可用 revision 为 0062，应新增专用 `SECURITY DEFINER RETURNS void` 入口，
只接受 task/round/actor 坐标，由库内派生集合，不接受 SQL、表名或调用方自报 owner 清单。

推荐顺序：ledger → task → 全 principal → round → 完整账户/引用 → 0032 任务图 →
有界回放流水 → 全附件绑定 → 文件 UUID 全集 → 服务重证 → audit 最后。
完整 principal 含请求人、初盘执行人、所有 completion/submission 操作者、复核人、
复盘 opener/assignee；引用包含回放两端账户、范围新增账户、组织/位置祖先、保管历史、
物料/策略/批次，以及快照、计数、观察和回放流水的全部 SN。集合去重并失败关闭，不能静默截断。
不得先递归锁来源文件子集再补全任务全集；文件等待后重新读元数据并复验权限。

保持 migrator 所有者、固定 `search_path=pg_catalog, public`，只给 migrator/API EXECUTE，
不给 PUBLIC/worker/edge/projector，不增加业务表 UPDATE 或 grant option。
安装前验证 0061 依赖函数签名、参数、语言、安全属性、owner/ACL 和函数体 hash；
同步 `database_security.py` 函数清单、SHA 和 readiness 链，catalog 漂移必须拒绝启动。
降级必须校验应用/readiness 兼容，不能让需要新入口的应用接受旧能力；具体策略在迁移设计中确定。

### B. 显式历史证据上下文

内部不可变上下文绑定 Session/transaction、task、目标 operation/round/scope、已验证的
冻结计划、完整祖先轮和预锁 owner 集合；不得来自 HTTP 参数，不使用 `skip_guard`。
普通写服务默认 `history=None`，保留当前权限、活动冻结、任务状态和幂等检查。

通过 keyword-only 参数贯穿：

- `stocktake_recount_count._load_and_validate_recount_assignment_graph`；
- `stocktake_review._load_and_validate_sealed_difference_evidence` 与
  `_recompute_initial_count_and_difference_graph`；
- `stocktake_recount_difference._load_and_validate_sealed_recount_difference_evidence` 与
  `_load_evaluation_inputs`，包括再次递归到 recount graph 的路径。

历史计划重证创建事件、scope key/hash/manifest 和不可变快照，不要求无关旧执行人今天仍在职。
当前 caller、授权版本、目标执行人及保管绑定仍使用当前时间重验，不伪装旧时间。
来源轮严格逐一递减，跨任务/跳轮/重复来源拒绝；不能在包装来源对象时丢掉祖先审计引用。
全部来源及释放证据经最终 audit proof 统一核验后，才能返回 confirmed 或 not_observed。

历史冻结接受完整 active 事实，或有唯一合法过账来源证明的 released 事实。
后者必须绑定 posting completion、task/effective scope-round、全冻结集合、释放时间/操作者/
固定原因及唯一 approved→posted 事件和审计；每笔历史计数均发生在释放前。
不能只看 task.status，也不能直接套用要求当前恰为 posted 且未关闭的 posting replay validator。
该释放证明仅服务于历史 count 的合法时间区间，不对外冒充完整库存过账确认。

### C. 下一修复切片门槛

除现有三轮用例，必须补 approved/posted/closed、多范围子集、无关旧人员变化与当前目标撤权、
伪造/缺失释放事实、源审计损坏、附件逆序争用、快照-only SN、截止回放端点及尾部身份变化。
证明不传历史上下文的原写入口继续拒绝无活动冻结或失效人员，查询无业务写，
并独立跑前向迁移、catalog 漂移、API-role 和并发门禁。完成这些后再接两端持久恢复。
