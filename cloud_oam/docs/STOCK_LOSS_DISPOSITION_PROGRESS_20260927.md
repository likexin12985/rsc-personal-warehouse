# 报损实际处置开发接续（2026-09-27）

本轮继续使用 06f6/oam，分支 `codex/notification-delivery-worker`。原有改动全部保留。
正式 V1.0 范围不缩减；本页是开发接续证据，不是上线或生产验收记录。

## 2026-09-28 最新终态（优先于下方过程记录）

SN v6 会话 97797 已 exit 0，`run-_za8iu7m`：三种 API 实际处置、32 组异常 COMMIT 完整回滚、共享 SN 保护、准确引用/串号替换拒绝、3 次改名通用反向拒绝、4 次 API 权限拒绝、实际时钟到期、同请求并发和历史保留全部通过。数量最终证据仍为 v5 `run-f2ejl41a`（30 组异常回滚），不将 v5 整体 exit 1 改写为通过。两者业务/迁移相同；v6 仅调整授权到期测试窗口。

通知保留 v4 会话 2650 已 exit 0，`run-7j59kj0t`：真实零期初及零渠道通知事实、旧错误复现、0126 当前链/0109 独立拒绝、完整 public 数据/函数/权限目录不变均通过。SN 和通知实例均 stopped/checks passed/serverExitCode 0，sourceDrift 为空。成功通知草案按原字节应用为 `scripts/run_local_pg16_notification_retention_checks.py`，SHA-256 为 `c7fac2a7060feb19e5d34c2c181db7a69c19e4e21bde80c040556002aa2dace4`；程序根目录解析和 CLI 通过。证据为 `notification-retention-verified-integration.json`。

现行源码清单 `release-candidate-source-manifest.json` 共 1,667 文件，无漂移；`source-evidence-resolution.json` 映射静态启动后 5 个 PG16 测试/运行器差异，业务/迁移及静态测试模块均未变。

静态三片终态全部 exit 0：98273 为 2,387 passed / 1 skipped / 15 subtests，69454 为 2,655 passed / 2 skipped，95028 为 2,496 passed；合计 **7,538 passed / 3 skipped / 15 subtests**。368 模块按 132/115/121 分片，互不重叠且覆盖完整，每个模块摘要已绑定原冻结清单。跳过项均为既有本机客户端缺省或 SN 适用范围。最终摘要 `static-final-summary.json`，各片详情 `static-{0,1,2}-terminal.json`。本地功能证据齐全，最终文档/仓库安全和准确索引核验后提交，远端 CI 必须对应实际新 SHA。后文“运行中/草案未应用”为过程记录，不覆盖本段。

隔离的下一批报损派生退回预览在 `artifacts/next-loss-derived-return/`：12 项服务聚焦通过（21308 exit 0，56.23 秒），按总部决定保留原成色和责任、拒绝错误来源/路线/权限，且查询无业务写入。该草案未集成，不能计为派生退回的正式过账、SQL 或履约验收。

## 已提交的上一批

`5c5625ab688562c0bdeb894073a68ffe9ac88242` 已恢复推送（会话 82272，exit 0）。
客户端 CI `36327607123` success。2026-09-28 最新回读：PG16 CI `36327607121`
completed/failure；quantity/serial 报损及三个 static_safety 分片均 success。
runtime 在通知历史降级断言失败：当前链被 `0126 opening authorization evidence`
保护拦截，旧断言只接受 0109。不能记为完整门禁通过。
准确回读和失败日志保存在 `artifacts/loss-disposition-0150-20260928/previous-commit-pg-ci-latest.json`
及 `previous-commit-pg-ci-failed.log`。正用全新自有 PG16 复现，并独立验证 0109 自身保护。
旧本地 PG16/迁移证据仍在 `artifacts/next-return-account-admission/`；不能替代远端终态。
最新读取和推送日志保存在 `artifacts/next-loss-disposition/`。

## 当前未提交的实际代码

- `StockLossDisposition` 原始处置模型，精确连接总部决定、原单/原行、库存交易和移动；
  每行只允许一个原始处置。后续纠正必须另建追加事实链，不能覆盖该记录。
- 严格预览/执行输入只接受原决定和原审批/计划摘要，不允许客户端替换数量、SN 或目标。
- `stock_loss_disposition_plan.py` 只读预览，重查当前 dispose_loss 权限、有效期初、当前责任、
  物料策略、投影和逐行剩余冻结；按完整维度复用或推导缺失目标账户。
- `stock_loss_disposition_commands.py` 在账本和期初/当前权限图锁内重新核验计划，
  同一事务完成目标账户创建、统一库存过账、处置事实、审计、状态、Outbox 和独立通知。
- `stock_loss_disposition_facts.py` 以历史游标重建冻结余额、原 SN 和各原行释放依据，
  核验审批、流水、审计、状态和通知。历史释放图用迭代及单次读取缓存，避免递归栈限制。
- 恢复可用、转旧、转坏接入上述路径；退回与报废仍拒绝通过此入口。
- 通用冲销检查原库存交易的来源类型，反向请求改名也不能绕过专用报损纠正要求。

**0150 迁移和运行安全目录已实现，基础数量/SN PG16 已通过，追加反例和整批回归尚未收齐。**
迁移使用准确 CAS 扩展冻结释放、交易检查及受控账户准入；新增表仅 SELECT/INSERT，
函数保持私有，触发器 ALWAYS；历史处置阻断降级。未添加正式路由、
生产权限种子、客户端入口。当前批不能提交、部署或宣称完成全部报损闭环。

## 测试解释及原始日志

日志均在 `artifacts/next-loss-disposition/`，各轮存在重叠，不累计成总通过数。

- `focused-v1.log`：测试误读不存在的 `approval_stage` 字段；真实处置和证据核验已执行，
  断言应核验原提交 `status=submitted`，已修正。
- `focused-v2.log`：12 passed，数量/SN 三种处置、原请求重放、退回/报废拒绝、通知失败整笔回滚。
- `focused-v3.log`：31 passed / 1 failed；故意写不存在的原行 ID 时 SQLite 外键在 flush
  提前拒绝，原用例却预期后续 read proof 拒绝；这是测试阶段预期错误。
- `focused-followup-v1.log`：8 passed / 44 deselected，通用反向拦截、前任申请/审批人停用后
  独立执行、后续报损不污染旧证据、客户端不能指定处置数量或目标。
- `holds-iterative-v1.log`：4 passed / 48 deselected，迭代历史核验后的共享冻结和后续报损。
- `reversal-regression-v1.log`：18 passed / 101 deselected，既有通用/工单/退回冲销边界回归。
- `focused-v4.log`：33 passed / 1 failed；测试又将 DEFERRABLE posting_movement_id 外键
  误当作 flush 时拒绝，实际应先由读证明拒绝，并在 COMMIT 拒绝。已分别修正三种检验阶段。
- `focused-final-services-v1.log`：已定位相同测试预期问题后，准确中断 PID 60463，
  会话 62726 exit 2（13 passed / KeyboardInterrupt）；不计作通过，日志保留。
- `reference-boundaries-v1.log`：修正后的即时外键、提交时外键、篡改摘要和借用其他真实移动
  的聚焦复测：会话 19172 exit 0，16 passed / 38 deselected，91.45 秒。

`source-manifest-final-services-v1.json` 是修正延迟外键测试之前的候选快照，已经有已知
测试文件变化，不得作为当前全部源码通过的证明。下一轮必须重新冻结当前源码并完成
整组测试，再继续 SQL/PG16。

## 2026-09-28 接续证据

本轮日志目录：`artifacts/loss-disposition-0150-20260928/`。

- `services-v1.log`：54 passed，321.89 秒，exit 0；服务源清单 `service-source-v1.json` 已核验无漂移。
- `migration-focused-v4.log`：13 passed，58.20 秒，exit 0；0150 及 0145/0149 继承迁移、私有函数/CAS、安全目录、SQLite 关闭写入和历史保留。
- `database-security-v2.log`：305 passed，32.90 秒，exit 0；新增表权限、9 个新增触发器、函数体摘要和现有完整目录检查。
- 迁移聚焦 v1/v2/v3 失败分别为工具任务未等目录刷新、SQLite 夹具 DDL 被测试 rollback 回滚、命令未设置 `PYTHONPATH=backend`；原日志保留，不计通过。
- 权限目录 v1：303 passed / 2 failed，旧测试夹具未计入新增表和 9 个触发器。只更新明确清单断言，未放宽生产验证。
- PG16 v1：空库升级/降级/再升级、运行角色目录及真实个人期初已执行；处置预览的测试事务误设数据库 READ ONLY，既有期初完整性校验需 SELECT FOR UPDATE 而失败，未处置提交。未改生产锁或弱化证据，改为逐条 SELECT 审计和完整业务数据快照比较。
- PG16 v2：恢复可用正向及 8 组拒绝通过；新目标数量篡改先被账户准入 guard 拒绝，测试误限定为 0150 报错而失败。按准确分支修正，仍要求 SQLSTATE 23514 和全部数据完整回滚。
- `pg16-v3.log`：会话 67484 exit 0；数量 `run-0yru_uhv`、SN `run-tlrxnfxt` 均 passed，集群 stopped/checks passed/serverExitCode 0，源码均无漂移。每模式 3 种真实 API 处置、24 组异常提交回滚、3 次原请求重放、2 个新账户、提交时权限到期、双连接同请求单次过账、后续处置后的历史证明、迁移往返及历史拒绝降级，前后运行安全目录均通过。
- v3 后补充全库结构测试中的新表清单和约束文本空格对齐；同时加入独立 CI flow 矩阵（submission/disposition × quantity/serial），所有腿均使用独立临时 PG16。运行时函数体和权限没有放宽。
- `pg16-v4.log`：会话 46771 exit 0；数量 `run-dloaw_hs`、SN `run-ly9rksbr` 均 passed/正常停库/源码无漂移。每模式另证明 3 次改名通用反向的独立 SQL 拒绝、4 次 API UPDATE/DELETE/TRUNCATE/私有函数调用拒绝；数量模式另有处置后共享冻结份额及借用拒绝。不能把此数量共享场景外推成 SN 共享释放已证明。
- `previous-loss-regression-v1.log`：发现 shell 未包含已有 Node runtime 后，仅中断准确自有进程 20733，退出 1 / KeyboardInterrupt，临时集群停止，不计通过。后续使用已有 bundled Node PATH 重跑。
- `alembic-v1.log`：166 passed / 2 failed；旧父版本断言、新表结构清单未更新。修正后连同 ORM 精确匹配/完整降级和 CI 拓扑，`final-integration-focused-v1.log` 为 12 passed / 75.06 秒 / 会话 44468 exit 0。
- `previous-loss-regression-v2.log`：数量完整通过（`run-dppzlbe9`）；SN 在 12 秒授权到期夹具的预览阶段提前到期，会话 61297 exit 1，不计 SN 通过。测试窗口改为 45 秒，仍断言正常命令返回时未到期、真实数据库时钟越界后 COMMIT 拒绝和全回滚；生产权限规则未改。v3 会话 65695 已 exit 0：数量 `run-fwu73u8k`、SN `run-rlyii_fg` 完整通过，含提交/回查/永久封存/共享冻结/区域核实/总部终审；均无源码漂移且正常停库。
- v4 后仅两个 PG16 专用辅助测试文件继续变化：`pg16_stock_loss_disposition_gate.py` 增加原行/真实移动交叉引用、同池另一 SN 冒用及共享 SN 释放场景，`pg16_stock_loss_submission_boundaries.py` 修正上述时间窗口。业务源码及静态测试模块无变化。静态原清单和两文件差异分别保存在 `final-source-manifest.json`、`static-to-pg16-test-only-delta.json`；补充 PG16 必须按 `supplemental-pg16-source-manifest.json` 取得新终态，不宣称整树始终无漂移。
- `current-head-pg16-v1.log` 会话 47930 已 exit 0，`run-9ljv79u5` 正常停库。0150 当前安全目录、采集角色、9 项合成短信配置、期初、报表实际 HTTP/worker、带数据导入及历史拒绝降级通过；短信和对象存储仍为合成验证，不是真实供应商验收。
- v5 数量 `run-f2ejl41a` 已 passed/正常停库/源码无漂移：3 种实际过账、30 组异常 COMMIT 完整回滚、准确原行/真实移动交叉引用、共享冻结释放保护均通过；SN 仍在运行，不外推 SN 结果。
- `pg16-v5.log` 会话 17961 最终 exit 1：SN `run-sepsfzs0` 的新增原行/流水/SN 反例已通过，但完整命令在原 20 秒授权窗口内未跑完，被应用正常拒绝，尚未进入预期的 COMMIT 到期边界。仅将该测试窗口改为 60 秒，并显式断言真实数据库时钟越界；服务/权限规则不变。`pg16-serial-v6.log` / 会话 97797 正按准确 SN 模式重跑。数量通过结果保持按 v5 版本记录，不宣称 v5 整体退出 0。
- 通知历史隔离补验草案 v1 / 会话 53118 exit 1，`run-gnmszpqn` 正常停库：期初本身没有创建通知目标，夹具前置断言拒绝。新的 `scripts/run_local_pg16_notification_retention_checks.py` 通过 API 服务为合成人员创建零渠道通知事实，无外部发送；随后应复现原 0126 拦截，并独立验证 0109 和完整 public 数据/目录不变。v2 / 会话 16039 exit 1，`run-ru9jodym` 正常停库：降级子进程在 180 秒超时，尚未得到预期保护错误，不计通过。
- `notification-retention-v3.log` / 会话 43277 / `run-2slg49i9` exit 1，实例正常停止。600 秒本地等待获得真实拒绝，但复用报损来源额外建立了 0143 文件，当前链先被 `0143 loss evidence requires retention and explicit migration` 拒绝；预期的 0126/0109 场景尚未验证。无保护放宽或历史删除。
- v4 / 会话 2650 / `notification-retention-v4.log` 正在执行忽略目录 `notification-retention-isolated-candidate.py`：签名来源发布后，实际 API 完成零期初的启动/盘点/审核/过账/关闭，再创建零渠道通知。前置断言明确不存在报损凭证。保留 600 秒本地等待、原错误复现、0126 当前链/0109 独立保护和 public 数据/目录快照检查。草案按自身 SHA-256 留证，成功后才按准确内容应用到运行器，再核验来源差异。当前活动 SN 清单不变。
- SN v6 最新已通过恢复可用的异常引用/SN 回滚、实际 COMMIT、原请求重放和真实数据库时钟越界后的权限拒绝/整笔回滚；转旧的异常引用回滚、实际 COMMIT、库存/SN 和重放也通过。仍待转坏、共享份额和正常停库终态，不能计为整组通过。通知 v4 的真实零期初闭环已通过，当前正检查降级边界，尚无整组终态。
- CI 原 0109 单一错误文本断言已改用已有 `_assert_retention_downgrade` 双重验证。运行中的静态模块和业务/迁移源码未改；后续仅 5 个 PG16 测试/运行器文件不同，准确清单为 `supplemental-final-source-manifest.json` 和 `static-to-final-test-only-delta.json`。不将原冻结清单误记为当前全树完全相同。
- 通知/封存聚焦 v1 命令用了不存在的测试文件名，exit 4 / no tests ran；实际模块确认后，`retention-focused-v2.log` / 会话 32862 为 15 passed / 540.25 秒 / exit 0。失败日志保留。
- 368 个静态测试模块的三片分别为 132/115/121 文件，会话 98273/69454/95028，日志 `static-0-v1.log` 至 `static-2-v1.log`，终态尚未齐备。依赖 `pip-check.log` exit 0，No broken requirements found。
- 预览的“只读”指无业务写入；保留既有期初证据行锁，不宣称数据库 READ ONLY 事务兼容。

## 下一步必须完成

1. 已收齐 0150 数量 v5 / SN v6 原生终态及通知历史 v4 双重保护证据，保留全部失败记录。
2. 上述并发、COMMIT 权限到期、共享冻结、改名通用反向、裸账户及原行/SN 交叉替换已通过本地真实数据库验证；远端门禁仍须绑定后续提交。
3. 整体本地静态、迁移/权限与 PG16 功能门禁已齐备；完成最终文档安全及索引核验后提交，再验证准确新 SHA 的远端门禁。上一提交 CI 不代表候选通过。
4. 继续无虚假工单的派生退回、受控报废三层 SN 证明、专用冲销及可继续纠正、请求回查/永久封存、PC/小程序；这些独立未完成，不能由前三种处置的通过替代。

未写生产业务、未发送真实通知、未部署。完整上线目标仍未完成。
