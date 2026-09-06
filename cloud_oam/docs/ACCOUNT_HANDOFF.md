# RSC 个人仓项目跨账号交接

- 交接日期：2026-09-01
- 最新状态核验：2026-09-06
- 交接方式：GitHub 私有仓库 + 新账号重新连接仓库
- 当前边界：GitHub 私有仓库已创建，`main` 已推送至
  `https://github.com/likexin12985/rsc-personal-warehouse`；后续功能必须使用独立分支开发、验证和评审

## 1. 接管前必须按顺序阅读

1. 仓库根目录 `AGENTS.md`。
2. `docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md` 全文。
3. `cloud_oam/README.md` 的当前开发状态、安全边界、本地验证和后续开发顺序。

V1.0 是产品、状态、权限、数据表、迁移和验收的唯一设计基线。任何历史聊天、README
摘要或测试结果都只能作为线索；继续开发前必须以当前源码和可重复检查重新确认。

## 2. 永久安全边界

- 禁止把 OAM Token、Cookie、验证码、密码、共享会话、RSC/Workflow/飞书登录态或生产数据
  上传到 GitHub、Codex 云端、日志、Issue、Pull Request 或聊天。
- OAM 仅允许由授权本地边缘端只读采集；云端不得配置 OAM 登录凭据，也不得自动调用登录或
  验证码接口。
- 新系统不得回写 OAM。OAM 省级库存只能作为外部控制总账参与对账，不能生成或叠加个人仓库存。
- 开发、测试、预生产和生产必须隔离；生产数据不得复制到开发环境。
- 审批、分配、占用、出库、发货、物流签收、OAM 收货、RSC/个人仓入库、通知送达和
  同步/对账始终是独立状态轴，任何中间状态都不能冒充履约完成。
- 测试通过不等于获得任何 OAM、RSC、Workflow、飞书、数据库或部署写权限。

## 3. 已确认的角色初始化策略

- 活跃内部人员默认配置本人范围的 `technician`（工程师）角色，但不得据此创建账号、启用身份
  或猜测人员映射。
- 蔚来总部管理员候选人为：李珂鑫、张鑫、张洋洋、张福利。仅当人员位于活跃总部组织、在职、
  姓名精确且唯一账号映射全部通过时，才允许按正式服务配置全国管理员角色。
- 省背包负责人初始保持为空，只允许既有正式全国管理员后续手工配置，不自动猜测或批量生成。
- 首名管理员仍需要稳定 `person_id` 的书面复核开通清单；姓名不能作为隐式自举凭据。

## 4. 当前源码状态

### 2026-09-06 当前门禁已验收（最新准确 SHA）

当前分支 `codex/production-readiness-gates` 的安全提交为
`27c445c030b887c935359f7458234eee6baf399f`，远端与本地一致。该 SHA 的
[Client release gate run 34009213016](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/34009213016)
和 [PostgreSQL 16 release gate run 34009213014](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/34009213014)
均为 success。PG16 静态阶段 `2133 passed, 1 skipped, 1 warning`，动态阶段
`1 passed, 1 warning`；warning 是既有 AnyIO 弃用提示。

本轮完成并验收：

- 0064 前向迁移 `rsc_lock_stocktake_finalizer_organization_0064(uuid)`，保留
  migrator owner、`SECURITY DEFINER`、固定 `search_path` 和 API/migrator 精确 EXECUTE ACL；
  0062/0064 readiness 双版本 SHA 已同步运行时安全清单。
- 小程序正式盘点恢复的 durable capability/no-replay、物理存储 key 完整性、任务级存储故障
  锁存、页面隐藏证据清理和 late-result 竞态保护；Client 门禁包含 Web、类型、构建和小程序契约。
- 0064 真库动态门禁使用 disposable 总部组织夹具，锁竞争、降级/再升级和安全目录均已回读；
  夹具已在门禁结束清理，不包含生产数据。

本轮不实现 0063 复核命令状态迁移；该计划仍冻结，且 Alembic 图需先决定 0064→0063 重接或
显式 merge，不能直接造成双 head。审批、分配、占用、出库、发货、物流签收、OAM 收货、
RSC/个人仓入库、通知送达和对账同步仍必须分别建模和验收，当前不能宣称一期或生产放行。

### 2026-09-06 最新续开发状态

当前分支 `codex/production-readiness-gates` 的安全提交为 `e5ea7fe`（远端已同步）。前向提交
`4c0f74a` 的 Client gate `33996107590` 与 PostgreSQL 16 gate `33996107518` 均通过，完成了
非期初串码/cutoff replay/多范围动态真库验收。`e5ea7fe` 的 Client gate `33997085112` 与
PostgreSQL 16 gate `33997085137` 均通过；其静态和动态阶段验证了过账尾部授权重读，库存批次、来源
审计与不可变过账完成事实仍在同一事务内成立。

`e5ea7fe` 没有新增迁移或改变 ACL。尾部 helper 只比较当前 principal 的人员、账号状态、授权版本、角色
范围、权限坐标及唯一总部管理员 assignment；漂移返回 `stocktake_posting_authorization_changed`（412）
并由调用方回滚。当前仍缺专门真实 PostgreSQL 竞争窗口对“尾部身份变化”和原 POST 锁序的证明，不能把
静态/单测视为该项已验收。

本轮已实现 Web 日常盘点过账的 post-only 持久恢复：哨兵只保存 task/version/actor/version/trace 等
最小非敏感坐标，状态查询、身份、权限和详情回读均使用 no-replay 只读请求；`not_observed`、身份/权限/版本/详情
漂移均保留阻塞，不自动重发 POST。前端本地 784 项测试、TypeScript 和生产构建已通过；远程 Client/PG 门禁
仍待本次提交后确认。小程序暂不接入；复核状态仍按 `REVIEW_COMMAND_STATUS_MIGRATION_PLAN.md` 保持
0063 前向迁移冻结。

### 2026-09-06 当前续开发状态

当前工作分支 `codex/production-readiness-gates` 的本地与远端 HEAD 应在接管时以 `git rev-parse`
重新核验（HEAD 可能是仅文档更新的后继）；最新已验收功能代码基线为
`c7f98cb282a99456b923a4fcbfede943d5cc6482`，前一功能基线为 `8046c78f04e1b18b016013a59946593531da2149`，再前为 `f482b8eeff11a4479937be2e2287bc14a3e6a8a3`。其父提交 `18dfbd72b7d578aff5886029304a5afadb75b8f2`（再之前为
`d95a3fe61eaa656d0d144d307a4ab9348c048194`）
已通过 Client release gate `33979437995` 与 PostgreSQL 16 release gate `33979438009`；当前
`18dfbd7` 的 Client run `33981032052` 与 PostgreSQL 16 run `33981032060` 也均已通过。当前
`f482b8e` 的新过账恢复切片门禁已通过。该提交新增
盘点历史 cutoff replay、SN/逐件、多范围、尾部身份重验和 owner-lock 顺序的静态契约预检；
该提交没有新增迁移，
也没有把静态源码契约当作真库业务验收。

`8046c78` 修复过账命令状态查询在无审计结果分支遗漏的锁后身份/权限复核，并允许已过账后独立关闭的任务继续重证原过账历史；新增撤权竞态和 posted→closed 回归测试。该提交的 Client release gate `33985935542` 与 PostgreSQL 16 release gate `33985935553` 均已通过。

`c7f98cb` 将过账历史恢复回归接入正式 PostgreSQL 16 静态门禁，并补充真实 post/close 后的历史状态核验；同时为后续动态 SN 试验建立独立 replay 库位、账户和串码，先完成正式 opening establishment，再写入合法串码库存种子。Client release gate `33990582028` 与 PostgreSQL 16 release gate `33990581994` 均已通过。该证据不等同于非期初 SN、cutoff replay、多范围、尾部身份竞争或原 POST 锁序已验收。

后续测试提交 `03a66ea` 曾尝试直接给串码账户写入库存以扩展非期初 SN 真库样本，PG16 真库按正式规则拒绝并返回 `inventory_opening_not_established`；该测试扩展已由 `e227696` 删除，回退后的 Client gate `33988119885` 与 PostgreSQL 16 gate `33988119864` 均已通过。结论是：非期初 SN/cutoff/multiscope 动态样本必须先复用独立 opening establishment，不能绕过期初建账。

当前真库样本仍为单范围、hard freeze、非 SN。下一项必须先完成准确 SHA 的 SN + cutoff replay
多范围真库样本，再验收尾部身份竞争和原 POST 锁序；非 count 的 review/recount/disposition/
post/close 命令状态持久证据与两端跨重启恢复仍未完成。

复核命令状态查询目前只完成只读设计审查，未写入代码：`stocktake_reviews` 没有可重建的历史
`task_version`，不能把当前任务版本冒充命令结果。下一阶段须先做前向迁移并同步审计、触发器、
权限和数据库安全清单；在此之前保持未知结果阻塞。
迁移字段和验收边界已整理在 `cloud_oam/docs/REVIEW_COMMAND_STATUS_MIGRATION_PLAN.md`，接管后先按该计划做 0063 前向迁移设计评审。

`f482b8e` 的过账恢复切片已通过 PostgreSQL 16 release gate（run `33982740333`）；当前 HEAD
最近一次文档 Client release gate（run `33985348396`）已通过；此前
文档提交 `d98c6df` 的 Client release gate（run `33982782831`）也已通过。该切片的非 opening
盘点新增只读
`/{task_id}/post-differences-command-status`，使用精确 `X-Request-ID` 映射并重证审计、状态转换、
审批、过账完成及库存事实；9 项定向测试通过，未新增迁移。它只覆盖 post，不代表 review、
recount、disposition、close 或客户端跨重启恢复已经完成。

历史验收代码为 `b57f3c33be37cca025b17d6f7ea8b7f0db48452d`，已被上述后续功能基线取代；迁移 head 为0062。
它先由6个正式服务独立提交专用库位零期初闭环，再保持原+1日常盘盈；新增7项静态回归，
相关76项和既有期初服务2项通过；该夹具修正不修改38b35e6的业务、迁移、权限或工作流。
完整本地回归`3045 passed, 1 skipped`（814.07秒），退出码0，测试前后后端/边缘/工作流冻结。
准确 SHA PG16 run`33970055640`全绿：静态2112项（787.32秒）、动态复合门禁1项（256.95秒），
含容器清理，job于`2026-09-05T14:09:10Z`完成；完整skip/warning解释见最新验收文档。
准确 SHA 客户端 run`33970055644`全绿：Web776、小程序647、类型/构建/安全/清理。
完整证据和限制见`DAILY_COUNT_HISTORY_TERMINAL_ACCEPTANCE.md`；不代表一期、UAT或生产放行。

以下为第二轮测试修正 `1f7322c8504de7862bee00030e4c5e8453662421` 的历史结果。
它仅修正0058测试的版本感知caller与新增0062重建OID断言，保留原函数/触发器/权限/指纹校验；
新增21项回归、相关69项通过（3.23秒）。准确 SHA 客户端 run `33968901267` 全绿；
修正后本地完整回归`3038 passed, 1 skipped`（848.28秒），退出码0，测试前后相关源码冻结。
PG16 run `33968901288` 静态2105项通过，动态在新增+1盘盈被拒绝：专用夹具账户缺正式期初
建账。上述b57f3c3已补真实期初前置，不改守卫、不把差异改零；这个历史run仍不计全绿。
业务、迁移与ACL仍对应首次候选 `38b35e6309ff1ef2d5fe8d6a9657a818cc5d3250`：0062 历史 owner /
终态来源切片。该首次候选本地完整回归 `3017 passed, 1 skipped`（887.93 秒），退出码 0；测试前后
后端、边缘、工作流与该 SHA 一致。准确 SHA 客户端 run `33967807404` 全绿（Web 776、
小程序 647、类型/构建/安全/清理）。PG16 run `33967807410` 静态2084项通过，动态在旧0058
目录caller断言失败，容器已清理；随后已修正并重跑，该历史run不计全绿。
最新验证状态及未完成项以 `DAILY_COUNT_HISTORY_TERMINAL_ACCEPTANCE.md` 为准；
当前仅列明范围已获准确 SHA 门禁通过，不要当作生产已发布，也不要回退或重复开发。
下方 `2691ab3` 为前序已验收基线，不是选择旧提交继续开发的指示。

前序已验收修正提交 `2691ab3686c139e5b3ee35140ed39cfe45d4382b` 已推送：三轮真库测试暴露
复盘只读表重复 FOR UPDATE 权限错误与封轮时序问题，已修复 17 处锁后回读，并在 task
仍 counting 时先 flush round、再提交 task，保持同一事务，不新增 commit。
保留 owner 锁、可变行锁及所有校验，不改迁移/ACL、两端、边缘或部署。相关 103 项回归
（64.32 秒）及 13 项脱敏诊断通过；本轮累计新增 13 项历史查询回归。
修正后本地完整回归 `2939 passed, 1 skipped`（909.10 秒），退出码 0。
准确 SHA PG16 run `33965156443` 全绿：静态 `2006 passed, 1 skipped, 1 warning`（569.04 秒），
动态 `1 passed, 1 warning`（186.61 秒），含容器清理，job 于 `2026-09-05T12:21:41Z` 完成。
准确 SHA 客户端 run `33965156446` 全绿：Web 776、小程序 647、类型/构建/安全/清理通过。
测试期间后端/边缘/PG workflow 与上述 SHA 一致，迁移 head 仍为 `20260905_0061`。
测试初候选 `976cdba` 的 run `33963127979` 静态 2002 项通过，动态失败；
第二轮首次 GET 返回 503，容器日志确认 stocktake_recount_cases 权限不足，不计全绿。
权限修复 `ee0d0ed` 的 run `33964225622` 静态 2004 项通过，动态在第二轮 count 封轮失败，
0032 即时 round guard 报 P0001；其本地 2937 项通过、1 项跳过也不能计真库成功。
该历史 SHA 的新增范围、当时修复方案见 `DAILY_COUNT_HISTORY_PG16_ACCEPTANCE.md`：彼时停在第三轮
submitted，不覆盖复盘过账/关闭后恢复、附件/SN/截止回放并发。
彼时确认来源重证中的 active-freeze/live-assignee 检查会阻断部分合法历史，完整 owner
集合及附件锁序也有缺口；上述修复已由0062实施并获受限验收，按最新终态验收完成剩余矩阵后再接客户端。

此前已验收服务端功能提交 `8cabfdecb0e8734403fb8863e90d06951383f164` 已推送：新增日常初盘/复盘
scope count 历史核验 GET，绑定当前 actor/person/授权版本、operation、task/round/scope、
随机 trace，证明不可变 completion/manifest/audit，不返回原计数或重放许可。
该功能 SHA 的 62 项新增定向回归通过；后继三轮审查发现的限制以上方最新记录为准。
首次功能 `812debc` 的 PG16 run `33957264704` 静态通过、动态失败，不能算全绿；
已定位新查询把库存截止与冻结启动时间错误等同，按既有 0047 时序修正并补推进时钟回归。
当前修正 SHA 的本地后端/边缘全量 `2926 passed, 1 skipped`（539.43 秒），退出码 0；
测试前后后端/边缘/PG workflow 与该 SHA 无差异。跳过项为 GitHub 一次性 PG16 真库门禁，
不能计作本地真库通过。准确 SHA 的 PG16
[`33958198821`](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33958198821)
已全绿：静态 `1993 passed, 1 skipped, 1 warning`（737.46 秒），动态 `1 passed, 1 warning`
（223.17 秒），包含容器清理在内的全部步骤成功，job 于 `2026-09-05T09:48:21Z` 完成。
准确 SHA 客户端 run
[`33958198902`](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33958198902)
已全绿（Web 776、小程序 647、类型/构建/安全/清理）。未修改迁移、ACL、客户端、边缘或部署。
契约和限制见 `DAILY_COUNT_COMMAND_STATUS_ACCEPTANCE.md`：该 SHA 的 PG 仅覆盖初盘及其
关闭后历史，后继三轮测试见上方；深层来源历史变化可能保守阻塞；两端未接入持久哨兵。
彼时计划先补历史来源和owner锁图；当前已由0062实现，剩余矩阵以上方最新结果为准。
不得因 `not_observed` 换键补写，
不得把本接口扩称为创建、下发、差异、复核、过账、关闭或整个盘点恢复完成。

客户端自身最近功能提交为 `a78d5adae54024aed9f5f8cb5724b94e4ec32de1`：日常盘点分页、SKU/二维码/SN
标识录入、逐件 SN 数量预检，以及页面切换后旧列表/写回调隔离。Web 全套 776 项、小程序
647 项、TypeScript、构建及仓库安全检查本地通过。精确 SHA 的客户端 run
[`33956352741`](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33956352741)
已回读全绿，包括冻结安装、测试、类型、构建及清理；HEAD 可以是文档后继。
该客户端候选不改后端、迁移、ACL、边缘、部署或 PG 工作流；其后端对应下方历史基线，
当前后端以上方最新已验收代码为准。客户端验收见 `DAILY_STOCKTAKE_CLIENT_ACCEPTANCE.md`。
日常历史 GET 已由上方服务端切片交付，后续按最新历史修复前置推进跨重启恢复；不能借刷新
或 `not_observed` 清除未知请求、换键重发，不能把现有 opening-only 查询用于日常盘点。
OSS/CSP、审批转派、通知兼容、可信控制投影、部署及恢复演练仍未完成，生产访问仍禁止。

此前已验收后端功能提交为 `262b07a34cf508930c3835f2dad993a141920d97`，以下为其历史基线。
新增控制库存离线一致性校验：195 项新增定向通过；冻结后端/边缘全量 `2864 passed, 1 skipped`
（947.35 秒）。准确 SHA 的 PG16 run `33953508516` 已全绿，job 于
`2026-09-05T08:03:12Z` 完成：静态 `1931 passed, 1 skipped, 1 warning`（674.84 秒），
动态 `1 passed, 1 warning`（210.13 秒），含清理全部步骤成功。相同 SHA 的客户端 run
`33953508544` 全绿（Web 745、小程序 631、类型/构建），`2026-09-05T07:48:37Z` 完成。
它不连接生产、不写数据库、不认证来源/目录、不开放启动，也不改变历史 full/incremental
规则、迁移或 ACL；Alembic head 仍为 `20260905_0061`。
证据及未验边界见 `INVENTORY_CONTROL_EVIDENCE_ACCEPTANCE.md`；下一步补可信来源/目录、
不可变采集证据及独立控制发布链，不要重做已通过的纯校验器和准备目录。

此前已验收功能提交为 `762150c2d4e6ca1337b00af5b7f01409528f30d9`，以下是该历史快照。
本轮已完成期初四层只读准备目录及 PC/小程序入口，不开放启动按钮，不改迁移或 ACL；head
仍为 `20260905_0061`。精确证据分别为：

- 后端 `b192ccfe7186e625d7080271c3a76e2b838914ab` 的 PG16 run `33949950302` 全绿，
  `2026-09-05T06:46:57Z` 完成；静态 `1736 passed, 1 skipped, 1 warning`（771.99 秒），
  动态 `1 passed, 1 warning`（175.60 秒），清理等全部步骤通过。
- 相同冻结后端/边缘全量 `2669 passed, 1 skipped`（867.52 秒），退出码 0；
  运行前后确认相关源码和 PG 工作流与该 SHA 无差异。187 项新回归含真实时间窗修复。
- 客户端 `762150c` 的 run `33950490567` 全绿，`2026-09-05T06:42:18Z` 完成；Web 745、
  小程序 631、冻结安装、类型及构建通过。PC 独立提交为 `0bbc43894d17b8d7258fdeba3b7a1c423575920f`。
  后继客户端没有修改已验收后端/边缘或 PG 工作流。

详细契约、测试及未验边界见 `OPENING_START_PREPARATION_ACCEPTANCE.md`；工具链见
`CLIENT_RELEASE_GATE.md`。完整省级控制集、来源绑定/发布权限、启动计划及持久恢复仍未完成，
目录成功不等于控制库存就绪；新状态始终为 `start_ready=false`。
下一切片先读 `OPENING_CONTROL_PROJECTION_NEXT_SLICE.md`，一期是否仅接受完整省级快照
尚待用户确认，不能把旧 README 的 full-only 建议冒充 V1.0 硬要求。仍禁止访问生产。

以下保留为此前验收记录，不替代上面的最新代码证据：

此前完整 PostgreSQL 16 验收代码为 `75b7e42a988f1178145e91a747b43bba467dac65`，
run `33932385447` 全绿，job 于 `2026-09-05T00:31:28Z` 完成：静态
`1549 passed, 1 skipped, 1 warning`（666.56 秒），动态 `1 passed, 1 warning`（204.55 秒），
包括容器清理的全部步骤成功。依赖声明收口 `2e18820` 已离线重装验证；目录权限修复
`addb823` 及安全分页 `be3f615` 均已包含在本次准确 SHA 的验收中。
新候选相关本地回归 203 项通过（58.09 秒）；后续代码变更仍需独立验证，文档提交不冒充新代码验收。
该历史候选本地后端/边缘全量 `2482 passed, 1 skipped`（903.41 秒），退出码 0；
运行前后已确认对应源码及工作流与上述 SHA 无差异。客户端源码保持已验收快照：
Web 691 项及类型/构建通过，小程序 516 项通过；不能用它们替代真机 UAT 或容器验收。
接管时先实时核对 status、HEAD、远端及相应 run，不从旧聊天复制提交判断。

后续独立客户端门禁提交 `ae263fb10a149a434c04ea5cb46c2a3bd3a906b5` 已通过准确 SHA 的
GitHub run `33949308200`（`2026-09-05T06:16:26Z` 完成）：冻结安装、Web 691 项、
类型检查/构建、小程序 578 项全绿。该提交未修改后端业务或迁移；新增 62 项门禁配置回归。
证据及未验收的 Docker/真机边界见 `CLIENT_RELEASE_GATE.md`。后继准备目录/页面使用上方
各自准确提交的独立验收，不挪用这里的历史结果。

新候选不改迁移或 ACL：目录服务同时保留完整 principal 的 deny 和单个 manager grant 的 allow；
人员游标只用本页最后已授权返回的人员，内部扫描超过 1000 整页拒绝。候选及发起人、组织/
父位置绑定在结束前重新读取，不用 ORM 缓存冒充复验。PG16 新增真实 API 登录 READ ONLY
正向分页，以及隔离事务临时权限变化后的有效 API 角色拒绝/精确 manager 允许；临时变化全部
回滚并核对原目录。这些新增真库断言已在上述准确候选的 run 中执行通过。
游标字段不变，但旧版暂存 lookahead 游标不能用于新版连续分页；升级后从第一页重载目录。

- Alembic 唯一 head 为 `20260905_0061`。准确提交
  `2677546f5ef6041ecb92acee75d1af868c9d80ff` 已通过 GitHub PostgreSQL 16 run `33924289472`：
  静态 `1234 passed, 1 skipped, 1 warning`，动态 `1 passed, 1 warning`。
  `0061` 前向修复历史 `0059` 审计键的 JSON 提取括号，保持函数身份、权限和触发器；
  同时修复运行时安全清单遗漏、夹具派生 ID 与应用多余行锁，未改写任何历史迁移。
  接管仍先核对 status、HEAD、远端及准确门禁 SHA，不把后续变更自动算作已验收。
  操作、兼容和历史排障见 `docs/SUPPLY_TASK_0059_RELEASE_RUNBOOK.md`；不要从头重建或扩大生产访问。
- `0052` 已为期初盘点范围完成事实增加不可变 `request_jsonb` 原始请求和
  `request_resolution_jsonb` 解析证据，数据库会独立校验请求、策略快照、主数据解析、观测、
  盘点行、序列号别名、复核、过账、对账和关闭之间的因果关系。同轮序列号跨盘点行/观测互斥、
  `READ COMMITTED` 并发门禁、保守历史回填和有证据降级阻断已纳入迁移；该变更禁止新旧应用与
  数据库混合运行。`0053` 以前向迁移修复 `0052` 的十五表共享延迟触发器在非
  `stocktake_rounds` 记录上解析 `NEW.status` 的问题，保持原函数身份、所有者、ACL 和触发器绑定，
  并把 readiness 精确推进到 `0053`。`0054` 再以前向迁移只修复有效复盘建立后对已封印来源初盘轮
  的历史复证：当前提交证明仍拒绝提前存在的复盘范围分配，仅历史证明允许读取复盘图独立封印的
  后继分配；它原位替换既有 helper 和 readiness 的函数源，保持函数 OID、签名、所有者、ACL、
  六个调用方和全部触发器绑定，不新增或改写表、业务行及权限。`0055` 以前向迁移修复非期初盘点
  启动 guard/validator 误用不存在的 `audit_events.sequence_no`，改为按
  `audit.stream_version, audit.id` 重证审计顺序；`0056` 原位修复共享轮次 assignment
  helper 仅接受 `opening` 的错误限制，使非期初初盘事实继续受同一数据库授权图约束；`0057` 增加
  非期初差异回放的 ledger-head-first owner-lock 图、已封印盘点附件不可追加门禁和非期初控制
  快照拒绝门禁；`0058` 仅修复最终批准复核图在合法 `posted`、对账及 `closed` 终态下的持续成立。
  `0052` 至 `0058` 均为不可改写的迁移历史。上一验收版本 `0058` 对应准确 Git SHA 的 GitHub
  disposable PostgreSQL 16 门禁已经通过；正式顺序见
  `docs/OPENING_STOCKTAKE_0052_RELEASE_RUNBOOK.md`。
- 本地源码已包含正式需求提报/三级逐行审批/安全撤回取消，以及期初和非期初盘点的实盘、差异、
  复核、复盘、过账、独立对账和关闭能力。撤回/取消已增加仅以 `X-Request-ID` 查询的服务端只读
  command-status，PC 硬刷新和小程序进程终止后可使用不含幂等键、正文、PII、token 或 hash 的
  最小哨兵恢复；`not_observed` 仍表示未知，不是拒绝或未发生的证明。
- 需求获批目前仍只表示需求成立。分配/占用、拣货/出库、发运、物流签收、OAM 收货证据、
  RSC/个人仓履约入库、通知送达及跨系统对账闭环尚未完成，禁止用需求或盘点状态替代。
- 本轮已接入原物料供给计划创建/跟进/取消、总部权限、数量与终态约束及 PC/小程序界面。
  持久化恢复仅保存最小非敏感坐标，历史命令与当前状态分开核验；未知结果不得盲重试。
  该模块已通过上述准确提交的 PostgreSQL 16 动态验收，但 `not_observed` 的受控解除仍为运营待办，
  不能据此宣布一期或生产可用。
- 后续客户端脱敏已移除期初盘点/对账错误公开属性及小程序 Page.data/WXML 中的原始幂等键；
  已独立提交为 `ca9fac76761d6fc400ef75421c2123d2440927d1`；Web 425 项、小程序 216 项通过，
  原请求内存重放保持不变。后续修复提交 `e2043bbfcaa9fedbf7320adb4f7a773bfa4be0c6` 已完成
  Web 首次直接拒绝白名单和全调用防重、小程序历史成功响应与同轮当前投影的独立核验。
  Web 476 项及 TypeScript/构建、小程序 220 项、后端路由定向 95 项通过；仅客户端及测试变化，
  未改后端业务/迁移/ACL，其精确 SHA 官方 run `33926859550` 已成功：静态
  `1234 passed, 1 skipped, 1 warning`，动态 `1 passed, 1 warning`。
  后续 `ff9611e97ce53adb98c6e05a9af15b59176b6c8a` 已修复小程序通用 4xx 清标和确认弹窗竞态，
  全套小程序 229 项通过。两端身份/硬刷新持久化、盘点命令核验和未知结果受控解除仍属 P0，
  不可误认为已完成整个恢复协议。
- 复盘人员选择候选 `31b1ae52df57bc6fcdd1ca8d8b894685622c7d21` 已推送；新目录严格绑定
  task/round/scope/version/current actor，重用写侧资格校验。PC 不再手填负责人 UUID；分页只返回
  已授权人员的游标，超 1000 原始身份扫描整体失败关闭。Web 509 项、TypeScript/构建及后端
  定向 90 项通过；准确 SHA run `33928469889` 已成功：静态 `1351 passed, 1 skipped, 1 warning`
  （590.95 秒），动态 `1 passed, 1 warning`（193.17 秒）。独立 worktree 的该 SHA 本地后端/边缘
  全量 `2326 passed, 2 skipped`（927.66 秒）；不包含后续未提交客户端恢复模块。
- 后续 `a3f0483298108e4411f9d1b1167617364038ff7b` 已推送历史计数 command-status API，
  新增 50 项定向测试通过；精确 SHA run `33929437568` 静态 1401 项通过、动态失败，原因已定位为
  来源初盘存在已处置记录时，复盘历史核验传入当前轮空解析集合而非同任务已预锁全集。修复已提交为
  `ca1a45cf1bafabd6372c1e29f7fdb8a20703b317`，后端历史状态/正式读取定向 99 项通过，
  普通当前详情的真库非变更断言同步加入。该中间 run `33930872181` 已被最终客户端候选取代
  并取消，不计通过；最终 `e54765a` 的 run `33931135022` 已成功：静态
  `1412 passed, 1 skipped, 1 warning`（551.04 秒），动态 `1 passed, 1 warning`（186.29 秒）。
  包括清理的全部步骤成功；后续目录授权修复不能直接借用这一结果。
  不放宽证据规则，不改历史迁移或 ACL。查询不写业务数据，但需要规范
  数据库锁，不能在 SQL READ ONLY 事务运行。历史确认不推进当前复盘轮；`not_observed` 绝不清标。
  PC 基础 `8df5447` 与页面接线 `d553ef5a75d085cd8254584ce2e234bc6a4df590` 已提交并推送：
  676 项 Web、TypeScript/构建通过。count 先持久化后单次 POST，401 不自动重发；历史与当前
  投影独立核验后才能精确清标。其他期初及对账写入口全部经过同任务 count 屏障；这是本浏览器
  同源标签协调，不是跨设备全局锁。小程序持久 count 页面提交
  `d6f166c5d2e95b281ca4e548dbfa66fdc30994dd` 已完成独立复核，完整 516 项测试通过。
  小程序使用服务上下文互斥，不冒充跨设备或原生进程锁。PC 输入边界修复
  `e54765a10b4d465c6073df69e3fa97296ef90f93` 已补合法小数、数值上限、SN 单件与重复维度
  前置校验；最终 Web 全套 691 项、TypeScript/构建通过。恢复协议和真机待验边界见
  `docs/OPENING_COUNT_RECOVERY_ACCEPTANCE.md`，不得通过删存储绕过未知结果。
- `ca1a45c` / `e54765a` 相同的后端、边缘、测试及工作流源码在本地完整回归为
  `2389 passed, 1 skipped`（938.69 秒）；测试期间上述源码保持冻结，并以 Git 差异复核。
  后续候选不得借用此计数。依赖声明收口 `2e188208d892e87cd46467bf6e1a4de455eb673a`
  只固定既有 12 项版本和 pnpm 11.19.0，不改变锁定包图；独立离线全新安装后 Web 691 项、
  TypeScript/构建再次通过。Docker 浮动镜像/工具链与客户端独立 CI 仍待补齐，见一期清单。
- 期初启动仍缺专用资产 owner/物理库位/人员组合目录、正式库存控制投影与批次选择接口，
  不能只加按钮或把边缘快照 ID 当正式 SyncRun。同步新鲜度与分阶段批次复用边界需独立验收；
  启动命令在 task_id 生成前需独立恢复协议。详细依赖见一期剩余清单。
- 需求提报的 PC/小程序工单字段已改为本人范围的正式只读选择器，不再接受手填内部 UUID，也不
  回退 v0.9。选项必须具备唯一当前 `starcharge_oam` 来源版本、规范投影哈希和 45 分钟内同步证据；
  `0042` 只向 API 开放迁移所有者持有的精确工单 `FOR SHARE` 行锁，创建、修订、提交均锁后重读。
  旧草稿的不可选引用只能明确清除或正式重选；写后工单/修订锚点回读不一致继续保留原幂等坐标。
  当前源码已有 `formal_services/oam_work_order_projection.py` 正式投影器及独立角色/来源范围
  校验；尚未授权生产采集或启用。在没有正式投影时选择器会安全为空，不得接入旧工单接口
  或浏览器直连 OAM 作为替代。
- 最新 `0061` 上述准确 Git SHA 的 GitHub disposable PostgreSQL 16 真实迁移、双会话
  并发与锁等待门禁已通过。预生产迁移、真实身份/附件 UAT、备份恢复、发布回滚及
  正式变更审批仍是发布门禁；当前源码不得直接投产。
- 本地源码已实现 KMS envelope-key 注册表加载、`0040` 不可变 `kms_data_key_pins` 账本、
  确定性 `--plan`、只读 pin gate、请求前置解密和 live/ready 单飞 TTL 健康边界；仍未生成或
  接入真实 KMS 数据密钥/ECS RAM 角色，也未在预生产或生产 PostgreSQL 16 执行迁移、pin 双签落库、
  WAF/ALB 限流或真实 UAT，因此不能据此放行生产。
- `0041` 已在本地新增短信 dispatch 单 owner 账本、租约/迟到调用门禁、
  `prepared/sending/accepted/uncertain/expired` 独立状态、最小列级 ACL 和生产启动目录/函数体
  校验。发送使用独立无 overflow 数据库池，发送/校验共用有界 provider 容量门；未决发送和重复
  频控拒绝不会造成持久化写放大。对应准确 Git SHA 的 disposable PostgreSQL 16 迁移和进程中断
  门禁已经通过，但 backup/edge 间接角色继承的生产部署复核、PNVS 回执恢复、用户 1000 条套餐
  与 PNVS 接口兼容性及隔离号码联调仍未完成，因此生产短信继续关闭。

最新细节必须以 `cloud_oam/README.md` 和当前测试结果为准。

## 5. 短信服务外部条件（尚未完成生产验收）

- 2026-09-01 用户反馈已在阿里云开通 1000 条短信套餐。该信息只表示一项采购前置已推进，
  尚未通过控制台账单、产品类型、有效期或可调用接口的核验，不得据此开启生产短信。
- 当前登录验证码代码接入的是阿里云号码认证服务 `dypnsapi` 的
  `SendSmsVerifyCode` / `CheckSmsVerifyCode`，不是标准 `dysmsapi` 短信发送接口；必须由用户
  提供套餐所属产品名称及适用接口的非敏感截图或文字信息后再决定是否兼容。1000 条标准短信
  套餐不当然适用于 PNVS，禁止用“已购买套餐”替代产品/API 兼容性验收。不要提供账号密码。
- 正式联调前仍需确认已审核签名、验证码模板 Code、模板变量 `code` / `min`、方案名称、
  套餐有效期、预生产测试手机号和授权测试人员，并选定 RAM 角色/最小权限凭据托管方案。
  AccessKey Secret 不得进入聊天、GitHub、普通 `.env` 或测试日志。
- 该 1000 条套餐默认只登记为“登录验证码用途待确认”。需求、审批、发货、收货等业务短信仍属
  独立通知送达能力；在事件展开、接收人快照、delivery/attempt、回执验签、幂等回调、重试和
  额度告警完成前，不得复用登录验证码模板，也不得把 provider 接受发送当作通知已送达。

## 6. 当前验收基线

最新后端及客户端准确 SHA / run 见第 4 节与 `OPENING_START_PREPARATION_ACCEPTANCE.md`；
下方为 `0061` 首次验收及更早历史记录，不是重新选择旧提交继续开发的指示。

`0061` 首次数据库验收基线为 `2677546f5ef6041ecb92acee75d1af868c9d80ff`：
[run 33924289472](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33924289472)
状态已回读确认为 success，GitHub 完成更新时间 `2026-09-04T22:23:25Z`。
静态 `1234 passed, 1 skipped, 1 warning`（560.26 秒），
动态 `1 passed, 1 warning`（184.68 秒）。本地相同后端/测试源码的完整后端及边缘复跑为
`2266 passed, 1 skipped`（826.43 秒）；客户端独立脱敏提交未改后端/边缘或工作流。
下方 `0058` 及更早记录仅为历史证据；最新迁移兼容要求以供给发布清单为准。

2026-09-05 上一验收版本 `0058` 源码在不访问任何外部生产系统的前提下，已取得准确 Git SHA
`20c29f724d46112d06fd1734091ecee90500d636` 的 GitHub disposable PostgreSQL 16 成功证据：

- GitHub Actions run：
  [`33907501757`](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33907501757)，
  attempt `1`，触发方式 `push`，job
  `postgresql16-release-gate`；运行时间为 `2026-09-04T18:44:25Z` 至
  `2026-09-04T18:55:55Z`。
- PostgreSQL 16 镜像 digest：
  `sha256:cf78e76683b9ca8c5733cbbdce6c9262b45b6767934dd0a95e671f9a0fc20685`；Python
  版本为 `3.12.13`。
- GitHub 静态发布清单为 `1044 passed, 1 skipped, 1 warning`；隔离 PostgreSQL 16 动态迁移、
  ACL、触发器、API 真链、降级阻断及并发锁门禁为 `1 passed`，耗时 `142.36s`。
- 同一代码提交的本地精确静态发布清单为 `1046 passed`；本地 disposable PostgreSQL 18 辅助门禁为
  `1 passed`，耗时 `130.34s`。

上述结果证明该 SHA 在一次性 disposable PostgreSQL 16 环境中的源码、迁移及并发门禁通过，不能
替代预生产迁移、备份恢复、真实身份/附件 UAT、PNVS/套餐兼容性验收、生产变更审批或生产发布。
当前仍不得宣称已通过预生产或生产放行。

以下 2026-09-04 `0054` 结果现为历史快照，不能作为 `0058` 的当前放行证据：静态发布清单
`972 passed`（其中 Alembic 回归 `145 passed`）、FastAPI 后端全量
`2065 passed, 1 skipped`、仓库安全检查 `527 candidate files, 17,856,178 bytes`，以及本地
disposable PostgreSQL 18 的 `0053 -> 0054 -> 0053 -> 0054` 空图函数原位替换、OID/所有者/ACL/
函数 hash 和 26 个触发器验证。

以下计数是加入 `0054` 前的 `0053` 本地历史快照，同样不能作为 `0058` 的当前放行证据：

- 期初盘点、入账和审计链回归 `254 passed`。
- Alembic 迁移、数据库权限及部署安全回归 `434 passed`。
- 与 PostgreSQL 16 GitHub 工作流一致的静态发布测试清单 `969 passed`。
- FastAPI 后端全量 `2062 passed, 1 skipped`；边缘同步全量 `36 passed`。
- Python 编译、`git diff --check`、单一 Alembic head 和仓库安全检查通过。
- 本机没有受控 PostgreSQL 16。该历史快照中的迁移、共享触发器、历史来源轮复证、双会话并发、
  直接 SQL 篡改、数值溢出、角色/ACL 和函数体哈希结论，现已由上方准确 Git SHA 的 GitHub
  disposable PostgreSQL 16 run 取代；它们不得继续冒充当前证据。

以下 2026-09-02 内容保留为历史交接快照，不能替代上面的当前复核：

2026-09-02 在不访问任何外部生产系统的前提下完成以下只读/本地验证：

- 仓库安全检查通过：允许进入私有仓库的候选文件为 497 个，合计 14,745,188 字节（约 14.1 MiB）；
  未发现越界路径、
  Git 可见符号链接或子模块、高置信凭据、真实用户绝对路径、已忽略但仍被跟踪的文件。
- Alembic 仅有一个 head：`20260902_0042`。
- FastAPI 后端全量 `1790 passed, 1 skipped`；边缘同步单独 `21 passed`。
- 前端 `23` 个测试文件、`365` 个测试通过，TypeScript 类型检查与生产构建通过；构建仅保留单个
  612.96 kB JavaScript
  chunk 的优化提示，不影响本次私有仓库安全准备。
- 微信小程序 `195` 个测试通过，全部 JavaScript 文件语法检查和 JSON 解析通过。

上述计数包含 `0040`、`0041`、`0042`、正式工单来源/新鲜度/精确行锁门禁、KMS/pin/readiness 门禁、
SDK 日志封闭、跨用途 Key 隔离和微信 provider 前置幂等 owner 的当前工作树。这些结果不代表
PostgreSQL 16 发布门禁、预生产 UAT、真实
身份验证、外部系统联调或生产发布已经完成。新账号接管后应重新执行验证。
撤回/取消的内存意图注册表仍不跨进程保留，但 PC 硬刷新和小程序进程终止后可用最小
`X-Request-ID` 哨兵按“新鲜身份→当前权限→服务端命令事实→同一需求新鲜详情”恢复。只有审计、
命令、动作、状态转换、版本、修订、审批锚点与十个独立状态轴全部一致时才清理哨兵；查无记录、
身份/权限变化、存储损坏、传输不确定或事实不一致都继续失败关闭，不生成替代坐标。
正式 KMS 发布顺序必须是：`migrate -> 隔离 kms-pin-plan 服务 -> API 身份只读查询既有 pins -> 双人复核新增差集
-> star_oam_migrator 普通 INSERT -> star_oam_api 只读 gate -> API`。首次空表才全量插入；轮换
只插入既有 pins 中不存在、但 plan 新增的 purpose+version，匹配旧行不重插，旧坐标/KMS
version/hash 任一不符立即停止，
仍禁止 upsert/update/delete。认证 Key ID 切换前必须证明
所有幂等重放窗口清零并使用该用途从未用过的新应用版本，或先做迁移重加密；同一用途禁止跨
Key ID 复用应用版本。联系人当前记录和全部历史 revision 的旧 registry+pin
必须保留至独立审计迁移完成。API `/api/health/live` 只证明进程存活，`/api/health/ready` 与兼容
`/api/health` 才是数据库+KMS readiness；ALB 使用 ready、容器使用 live。KMS 等待/探针预算
均强制不超过 4 秒，生产拒绝 `DEBUG=sdk` 并禁用阿里云 SDK 自带流式日志。WAF/ALB 在 KMS 前的
认证接口限流和 readiness 来源限制属于外部发布硬门禁，未配置不得投产。
微信正式登录已改为先提交唯一 `pending` 幂等 owner、再兑换一次性 code；provider 失败审计与
加密失败终态同事务提交。同键并发已由上方准确 SHA 的 disposable PostgreSQL 16 gate 验证，
provider 返回后崩溃的不确定结果恢复仍是发布门禁。短信挑战已在本地实现 `0041` 单 owner
dispatch，发送/校验均禁用 SDK 自动
重试并严格绑定 `OutId`；provider 结果不确定时不自动重发，网络调用前以 challenge+dispatch
双行锁阻断迟到 owner 与过期替换竞态。`OutId` 不是 provider 幂等保证；回执对账恢复、真实
PostgreSQL 16 并发/kill 已由上方准确 SHA 的 disposable gate 验证，但 PNVS 回执对账恢复、PNVS
与用户 1000 条套餐的兼容性及隔离号码联调仍未确认，生产短信继续关闭。
客户端发布顺序必须是：先完成全部后端实例的 `0042`（包含 `0039`、`0040`、`0041`）迁移、pin gate 与新版切换，
再发布启用恢复哨兵的 PC/小程序。

## 7. 私有仓库边界

Git 仓库根目录设置在当前 `oam` 目录，但根 `.gitignore` 默认拒绝所有内容，只允许：

- `.gitignore`
- `README.md`
- `AGENTS.md`
- V1.0 正式设计基线
- `cloud_oam/` 中未被其第二层 `.gitignore` 排除的源码、迁移、测试和部署模板

以下内容必须永久留在本地并保持 Git 不可见：`work/`、`output/`、`outputs/`、根 `scripts/`、
`tmp/`、环境文件、运行数据库、上传物、业务导出、会话、Cookie、证书、私钥和构建缓存。

每次提交及推送前执行：

```bash
./cloud_oam/scripts/verify_repository_safety.sh
```

安全检查只报告路径和规则，不输出匹配到的秘密内容。任何失败都必须先处置，禁止用跳过脚本、
强制添加被忽略文件或关闭扫描的方式绕过。

## 8. 新账号接管步骤

1. 使用新账号连接同一个 GitHub 私有仓库，并仅授予所需仓库权限。
2. 从仓库根目录开始新任务，完整阅读第 1 节列出的三份资料。
3. 运行仓库安全检查和本地验证；不得直接沿用旧聊天中的“已通过”结论。
4. 仅配置开发/测试所需的占位或隔离环境。生产密钥必须由部署方通过正式密钥管理系统另行配置，
   不进入仓库。
5. 从 `cloud_oam/README.md` 的“后续开发顺序”继续，保留所有既有发布门禁。

建议新账号的第一条提示词：

> 请接管当前私有仓库中的 RSC 个人仓项目。先完整阅读仓库根目录 AGENTS.md、
> docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md 和
> cloud_oam/README.md。后续所有代码、文档、迁移、状态和验收必须遵循这些基线。
> 当前分支 codex/production-readiness-gates，接管时先以 git status、git rev-parse HEAD 和远端分支重新确认；最新已验收功能代码为
> c7f98cb，迁移 head 为 20260905_0062；c7f98cb 的 PostgreSQL 16 run 为 33990581994、Client run 为 33990582028，最近文档 Client run 为 33992298015。
> 先读 cloud_oam/docs/DAILY_COUNT_HISTORY_TERMINAL_ACCEPTANCE.md 中最新准确 SHA 验证状态。
> 已实施专用有界 owner、released 冻结历史证明、递归祖先审计及一次完整附件锁；不放宽写侧守卫。
> 历史 b57f3c3 已补齐专用库位真实零期初6步独立提交，再保留原日常+1盘盈；新增7项静态回归、
> 相关76项及既有期初2项通过。准确 SHA 客户端run33970055644全绿（Web776、小程序647）；
> 本地完整回归3045项通过、1项按环境跳过（814.07秒），准确 SHA PG16 run33970055640
> 全绿（静态2112、动态复合门禁1项），含容器清理，job完成于2026-09-05T14:09:10Z；
> 按最新终态验收文件核对详细范围。
> 前两轮失败分别是旧0058目录caller断言未纳入0062、专用夹具缺正式期初；均已修正测试，
> 没有改写历史迁移、放宽权限或造建账资格。详细失败证据保留在最新终态验收文档，勿重复开发。
> 真库新增样本为单范围、硬冻结、非 SN、跨轮附件、三轮计数到实际盘盈过账/对账/关闭；
> 该样本已通过真库门禁，但不代表SN、截止回放、多范围或全部终态/并发恢复已完成。
> 既有失败与修复历史保留在本交接第4节及 DAILY_COUNT_HISTORY_PG16_ACCEPTANCE.md，勿重复开发。
> 本地 HEAD 可以是该已验收代码的文档后继，不得强制回退。先只读检查 git status、HEAD、远端分支
> 及准确候选的门禁，阅读 cloud_oam/docs/PHASE1_REMAINING_ACCEPTANCE_20260905.md。
> 运行 cloud_oam/scripts/verify_repository_safety.sh，并重新检查 Alembic head 和相关测试；
> 不得访问或写入 OAM、RSC、Workflow、飞书及任何外部生产系统。继续一期盘点、
> 需求提报和处理的剩余发布门禁，不得把需求获批当成分配、占用、出库、发货、签收或个人仓入库。
> 不重做已经通过的目录权限、分页和计数恢复修复；不得删除恢复哨兵绕过 not_observed。
> 不重复新增三轮测试；0062 已实现递归来源历史模式和专用 owner，保留写侧守卫与 SHA 清单。
> 按最新终态验收文件补 SN/截止回放、多范围、尾部身份竞争和原 POST 锁序验收；
> 当前真库样本仍非 SN、单范围硬冻结，不能用静态引用覆盖冒充 SN/回放已通过。
> 完成这些前置后再接两端日常 count 持久哨兵和重启核验。
> 不把这个scope count GET推广成创建、下发、差异、复核、过账、关闭或人工未执行封存已完成。
> 两端只读期初准备入口和隔离控制证据一致性校验已完成，但没有可信控制库存发布，不能启动。
> 请读 cloud_oam/docs/INVENTORY_CONTROL_EVIDENCE_ACCEPTANCE.md 和
> cloud_oam/docs/OPENING_CONTROL_PROJECTION_NEXT_SLICE.md；可信目录、不可变采集证据与
> 独立控制发布链仍是后续待办。full-only、45分钟硬TTL和分阶段建账复用策略未被批准，
> 不擅自收紧设计基线，也不接入外部生产系统。
> 继续保留额度剩余 2% 时暂停新开发、保存并报告交接的约定。

## 9. 远端仓库状态与后续规则

- 远端 `origin` 固定为 `https://github.com/likexin12985/rsc-personal-warehouse.git`，仓库可见性必须保持
  `Private`；切换账号只需取得该私有仓库权限，不需要重新创建项目或复制生产凭据。
- 默认分支使用 `main`；应启用分支保护、Pull Request 评审和必要检查。
- 新功能使用独立分支；迁移文件一旦进入共享历史不得改写 revision 身份。
- 不在 GitHub Issue、PR 描述或 Actions 日志中粘贴生产数据、真实会话或未经脱敏的附件。
- 每次推送前重新确认 `origin`、当前分支和仓库安全检查；不得向其他远端或公开仓库推送。
