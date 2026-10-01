# RSC 个人仓开发交接

更新：2026-10-01 14:45（北京时间）。完整上线目标保持活动；本轮为 progress。主树1969源三组静态回归仍在运行，已发现2项离线迁移测试失败；修复在隔离候选验证，未应用主树、提交或部署。此前原生6组与H5验证证据保留如下。

## 工作位置与约束

- 工作树：`${RSC_REPO_ROOT}`；代码目录：`cloud_oam`。
- 分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。本批未提交、推送、部署。
- 继续前完整阅读根目录 [正式需求基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)，检查当前 diff。禁止 reset、revert 或丢弃未提交改动。
- 主树已应用正式0160多代集成包（58目标），另应用15目标：三个旧断言测试文件、审计批读及测试、六个处置HTTP接口及来源接口、新原生写HTTP辅助门禁。备份和回执：`artifacts/loss-multigeneration-main-integration-next/application.json`、`artifacts/loss-execution-command-http-next/main-application-v1/application.json`。
- 首页保持公开知识查询、无登录界面，星星管理入口到 `https://rscwz.cn/xx`。飞书知识源暂缓；后续按 NIO Chat CLI 路由。
- 本轮只操作本地代码和可销毁测试库，无生产/外部业务写入。不能用本地通过替代上线验收。

## 已完成及证据边界

| 范围 | 当前证据 | 限制 |
| --- | --- | --- |
| 报损退回收货、独立入库、请求恢复 | 历史数量/SN PG16回归通过，`artifacts/loss-formal-application-next/receipt-{quantity,serial}-verified-v2.json` | 旧1901源快照；不得冒充本轮全量发布 |
| 原处置经历一轮纠正后的恢复 | 主树已集成，34项聚焦及1918源数量/SN PG16通过，`artifacts/loss-recovery-main-integration-next/main-*-verified-v1.json` | 后续代码需按实际受影响范围重验 |
| 原处置/衍生退回只读HTTP | 主树已注册2个POST request-lookup；52项新用例+34项既有回归共86通过，`artifacts/loss-execution-recovery-http-next/verified-v1.json` | 当前读权限独立于写权限；返回原历史事实；无执行/封存回退。认证依赖为测试替代 |
| 后继历史HTTP | 同目录 `history-verified-v1.json`：6项数量/SN×三种纠正，84次路由请求通过；1306源核验 | 覆盖冲销/批准/纠正、证据损坏、游标变化、撤权；非原生HTTP/JWT验收 |
| 正式0160多代规范服务候选 | 43项恢复/版本头、6项多代/防伪/封存、81项路由/启动检查通过；早期25文件候选另210项通过 | 不把不同快照合并为同一次回归 |
| 正式0160原生generations | `artifacts/loss-multigeneration-release-next/native-{quantity,serial}-generations-verified-v2.json`：三轮、九请求、封存、双API并发单赢家、权限回滚、迁移历史保留均通过，测试库正常停止 | 1305外层/1304内层源；内层仅省略Alembic README。数量/SN的seal_retention也已终态通过；四场景证据齐全 |
| 审计批读优化 | `artifacts/audit-chain-batched-read-next/fixture-v2-verified.json`：34通过；`native-verified-v1.json`：15次原生新旧结果一致，SELECT 261→4，三类审计改写均拒绝 | 已应用主树；真实报损组合数量/SN各9条新旧回查一致且只读，测试库已停止，`business-{quantity,serial}-verified-v1.json`；当前组合回归运行中，非500用户验收 |

两个HTTP入口为 `/api/v1/stock-operations/loss-reports/dispositions/request-lookup` 和 `/api/v1/stock-operations/loss-reports/derived-returns/request-lookup`。都要求完整原请求，`retry_permitted=false`、`result_scope=original_command`，found/not_found/sealed相互区分，响应不泄露原始键或完整命令。

## 当前验证及在跑任务

上一批1937源主树：基础检查90通过、恢复HTTP92通过；原生退回数量/SN两组通过，均核验真实API角色、READ ONLY事务、200/409/403、迁移回环、历史保留及正常停库。证据：`artifacts/loss-multigeneration-main-integration-next/{runtime,http}-verified-v1.json`、`native-{quantity,serial}-return-verified-v1.json`。

上一批原生处置失败是旧测试仍匹配0150冻结约束文字，实际数据库正确返回0159约束且SQLSTATE=23514。两个失败库已正常停止，原始失败保留于同目录 `native-boundary-expectation-failure-v1.json`。三个测试文件已修正并应用，不改业务或SQL约束；本轮会重跑失败场景，尚不能声称处置原生已通过。

新接口候选证据：`artifacts/loss-execution-command-http-next/verified-v1.json` 44通过；`sources-verified-v1.json` 28通过。已应用主树：六个预览/执行/请求封存接口，以及 `GET /api/v1/stock-operations/loss-reports/execution-sources/{operation_id}`。来源接口返回准确总部决定引用、明确标注的原处置事实、真实且经当前保管责任核验的退回路线；未核验路线返回unavailable，不能猜UUID。

### 当前权威结果

- `main-focused-verified-v1.json`：主树1947源的202项组合终态通过，源码复核无漂移。
- 原v1三个worker均结束。四个HTTP步骤的失败原因都是preview被辅助门禁误设为READ ONLY；两个disposition-recovery步骤在之后发现另一条0150旧断言。实际数据库拒绝通用冲销并返回 `0159 exact dedicated disposition inverse required`、SQLSTATE23514。六次失败的源码、日志及正常停库记录保留在 `artifacts/loss-execution-command-http-next/main-application-v2/preceding-failures.json`。
- 已应用6个测试/CI目标：preview测试保留SELECT-only/无COMMIT/无ORM写入/事实不变，GET与恢复仍数据库READ ONLY；专用冲销旧断言改为精确0159文字；4个CI草案应用。未修改生产约束或服务。原字节备份与逐项应用回执在同目录 `application.json`。
- 同目录 `quantity-http-disposition-verified.json`、`quantity-http-return-verified.json`、`serial-http-disposition-verified.json`、`serial-http-return-verified.json`：**四组全通过**；1948内外源清单一致；原生PG16.15、真实star_oam_api、启动权限前后检查、期初锁下SELECT预览、真实COMMIT后模拟断联503→原请求找回、永久封存拒绝迟到写、撤销write仍可read、撤销read拒绝，全部正常停库。模拟登录身份，不能当生产JWT验收。
- `ci-topology-dispatch-verified.json`：59项本地CI矩阵/分派通过，包含数量/SN×处置/退回两个新增矩阵。**未运行GitHub Actions**。

两组完整处置恢复也已终态通过：同目录 `quantity-disposition-recovery-verified.json`、`serial-disposition-recovery-verified.json`。每组覆盖三种处置、3次只读预览、3次幂等重放、通用冲销拒绝、数量/SN与共享冻结保护、并发同请求单笔记账、过期权限/保管关系整笔回滚、通知去重、当前write撤销后的原请求read；数量30次/SN32次畸形提交全回滚。迁移空往返、保留处置/保管历史时拒绝降级、前后启动ACL通过，PG16.15正常停库，1948内外源一致。worker18941/18942/18943全部结束；不能再把它们当在跑任务。

**当前运行的是全量静态发布回归**：`artifacts/loss-formal-application-next/static-release-current.json`，主源固定1969文件，三组覆盖443个测试模块：

| shard | worker / child | 目录 |
| --- | --- | --- |
| 0（159模块） | 26868 / 26873 | `artifacts/durable-development-gates/static-release-1969-0-v1` |
| 1（135模块） | 26869 / 26875 | `artifacts/durable-development-gates/static-release-1969-1-v1` |
| 2（149模块） | 26870 / 26874 | `artifacts/durable-development-gates/static-release-1969-2-v1` |

每组使用既有 `scripts/run_static_shard.py --index N --count 3`，覆盖后端/边缘同步，原生PG运行模块由独立已述门禁负责。三组均已实际启动并进入测试，尚无终态；不要改1969固定源。准确child以后以state和ps为准；`artifacts/static-safety/shard-*-*/progress.jsonl`提供当前用例进展，不将点号或单项PASS当整组通过。

本轮已发现并单独复现一项失败：`test_0041_postgresql_offline_sql_covers_preflight_truncate_acl_and_pg_guards` 的历史测试范围使用了当前 HEAD，离线生成到0159时调用需要真实查询结果的权限/版本预检，触发 `AttributeError: 'NoneType' object has no attribute 'one'`。复现日志 `artifacts/loss-formal-application-next/diagnostic-0041-offline-v2.log`，1 failed/113.88秒；不是生产短信发送结果。初次直接pytest缺少标准runner的PYTHONPATH，另有一条仅诊断命令的ModuleNotFoundError，已按runner路径修正后得到上述真实失败。

修复候选在 `artifacts/migration-offline-boundary-next/`：从1969源逐文件核验复制，修改0159/0160入口使离线升降级在发出未验证SQL前明确拒绝，旧短信SQL测试固定到0041，并新增真实Alembic离线/SQLite在线往返测试。候选4目标尚未应用主树；保留全部权限和历史保护检查，当前主树仍受三个静态回归固定。准确状态见该目录 `status.json`，不能把准备好候选当作修复已合入或门禁已通过。

候选首轮11项通过，1970份源复核无漂移，证据 `focused-v1-verified.json`。随后静态组2发现第二个同类失败：`test_postgresql_offline_sql_preserves_type_boundary`。v2候选保留至0158的完整历史SQL检查，新增版本仍由独立拒绝测试及真实PG16负责。v2聚焦为17通过/1失败（175.37秒、无源漂移）；剩余失败是旧测试以 `CREATE FUNCTION public.{function_name}(` 精确查找函数时找不到文本，正在提取具体函数名与生成SQL，不得删掉函数摘要断言。`validation-v2` 的worker32263已进入真实PG16在线检查，child35623、数据库进程35638，库目录在候选的 `artifacts/local-current-head-pg16/checks/run-jjrztq1m`；请以state和ps重新核对。只使用全新本机Unix socket测试库，无生产连接。

该查找失败现已精确定位为 `rsc_register_loss_request_binding_0159`：它是0159新增函数，当前运行时清单包含它，而截至0158的历史SQL正确地不包含它。`legacy-offline-failure.json`、`legacy-offline-generated.sql`保留145.67秒单项复现结果。v3使用独立 `source-v3`，仅相对v2修改这一个测试文件：该特定函数改由摘要固定的0159安装目录核验声明、参数和完整正文摘要，同时仍检查它不在旧SQL中；其他运行时函数照常校验旧SQL及已有版本替换。支持正式冻结定义的 `CREATE OR REPLACE` 语法，没有放宽任何数据库权限。v3聚焦worker37227/child37228见 `validation-v3/state.json`；v2原生仍在原目录运行，两者运行时与迁移文件逐一相同，差异回执 `v2-v3-runtime-equality.json`。不能在任一测试使用源码时直接覆盖它。

### H5处置（已应用并完成主树验证）

目录 `artifacts/loss-execution-h5-next/`；22个目标的候选before/after摘要见 `application-review.json`，327份候选前端源码摘要见 `frontend-source-manifest.json`。实现总部只读可达的“报损处置”导航与路由、准确已批准明细与真实路线、预览/单独确认、完整原请求先保存再单次发送、Web Locks跨标签协调、失联只回查、not_found禁止重发、永久封存二次确认、授权变化保留原请求。写权限专用dispose_loss，不能用finalize_loss代替。原处置明确标注历史事实，退回生成与发货/收货/入库分开；报废仍明确待实现。

主树逐项应用备份：`main-application-v1/application.json`，22目标、仅覆盖匹配before摘要的App.tsx并新增其余文件。主树终态：`main-application-v1/verified-v1.json`，worker26280六步骤全通过，1969源复核无漂移：**前端118文件2046项、后端契约8项、TypeScript、仓库构建、公开构建、公开入口开发模式验证**。公开bundle无认证客户端，/xx资源与worker隔离，小程序目录一致；知识目录pending/0条，不冒充release模式通过。候选临时HTML/TSX展示入口没有复制到主树。

之前候选验证回执 `verified-candidate-v1.json`：
- 真实注册HTTP生成数量/SN×四类共8份合成契约（`export-v2.log`），当前后端契约回读8通过（`backend-contract-v1.log`）。初次外置导出未加载conftest导致收集失败，已仅补显式本地测试设置并成功，不是生产依赖故障。
- 候选全前端118文件2046项通过（`all-frontend-v1.log`，随后仅加页面样式和小屏表格展示）；聚焦118通过（`focused-v3.log`，含公开目录/Service Worker测试）；最终移动端展示调整后页面/入口10通过（`ui-final-v1.log`）。
- TypeScript、/xx仓库构建、公开首页构建成功（`warehouse-build-v3.log`、`public-build-v2.log`）。公开bundle不含处置页，私有bundle不含合成测试数据。仓库包仍有既有大块体积警告，不能算500用户/弱网性能验收。
- 浏览器已检查桌面和390px手机布局，确认前执行按钮禁用；本地模拟执行后明确仍待发货/收货/入库。截图 `desktop-preview.png`、`mobile-preview.png`、`desktop-result.png`；仅本地合成数据和模拟transport。临时tab已关、视口已恢复、5187测试server已停。
- 本轮一次错误使用node --test启动Vitest文件，属于命令错误；保留`build-checks-v1.log`，已用正确Vitest入口全部通过，不修改断言。

## 下一步顺序

1. 跟随26868/26869/26870三个当前静态回归worker，收终态/失败用例、源码1969一致及退出码。原生6组和H5主树6步骤已通过，不无故重复。
2. 三组静态worker确实结束后解除主源固定，按准确失败证据修复并重验实际受影响范围。通过后审查整批diff及必要安全检查，满足既定本地门禁才提交；GitHub准确SHA CI、生产部署与业务验收仍独立，不能凭本地通过宣称上线。
3. H5主树已集成，继续纠正批准/执行独立封存、退回补偿、报废/失而复得。已核验小程序当前仅发布公开知识查询；保持app.json只注册knowledge，不把本次总部处置加入公开小程序包。`loss-history-proof-reuse-next`仍未集成，不与已验证审计批读混淆。
4. 持续按正式基线审计：真实角色范围、微信/短信/通知/附件、正式迁移与期初、准确发布SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO/RTO及恢复回滚演练。飞书知识源继续按用户要求低优先级。当前不是上线验收完成。

## 历史与故障记录

- 本次整理前的交接内容已逐字保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_132445.md)；保留摘要在 `artifacts/loss-formal-application-next/handoff-archive-20261001_132445.json`。
- 功能缺口：[报损纠正范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)、[正式基线审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)。其他审计中的旧段落只代表当时状态。
- 已修复的API读取迁移表权限错误、审计fixture非法流、启动前manifest路径错误和README清单范围差异都有原始回执；未扩权、未删除约束、未修改历史库存记录。
- 机器可读状态：`artifacts/loss-formal-application-next/continuation.json`。有新状态时优先更新本入口及对应准确回执，避免继续叠加相互矛盾的“当前状态”段落。
