# RSC个人仓开发交接

更新：2026-10-01 19:24:00（北京时间）。完整上线目标仍 active；本轮有代码与验证进展。未提交、推送或部署；本地通过不等于 GitHub 或生产通过。

## 接续入口与边界

- 工作树：`${RSC_REPO_ROOT}`；分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 先完整阅读根目录 `docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md`。本轮已读；所有后续开发遵循其正式范围。
- 禁止 reset、revert 或丢弃未提交改动。既定证据全部收齐后才提交。不要将候选验证通过表述为主树、GitHub 或生产已通过。
- Python：`cloud_oam/.venv/bin/python`，不得 resolve 解释器软链接；pytest 从 cloud 目录用 `-o pythonpath=backend`。Node：`${HOME}/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`。
- PG 16.15：`cloud_oam/artifacts/pg16-native-20260920/install/bin`。仅用任务创建、TCP 关闭的私有 Unix socket 测试库；不连接生产数据库或执行外部业务写入。
- 公开首页知识查询，星星按钮到 `/xx`。小程序保持公开知识查询；飞书知识源优先级低。短信、微信、真实登录和生产验收分别核验。

以下路径均相对 `cloud_oam`：P=`artifacts/loss-correction-request-seals-next`；R=`P/return-history-next`；D=`artifacts/damaged-return-inbound-next`。

## 当前代码位置

| 位置 | 当前内容 | 是否已进入主树 |
| --- | --- | --- |
| 主工作树 | 2008 源文件，0161；报损纠正批准、执行、独立原请求恢复/封存、H5，以及只读退回事实图 | 是，仍有未提交改动 |
| `D/source` | 2024 源文件，`migration-source-v6.json`；0162 破损分账、客户端及正式 CI 新场景 | 否 |
| `D/history-quality-work-v1` | 旧入库成色异常的只读投影；不更改旧流水或余额 | 否，也未应用 D/source |
| `D/history-http-work-v2` | 总部只读退回历史接口、类型化结果、真实 HTTP 测试及父路由注册副本 | 否，也未应用 D/source |

**主树确认缺陷尚未修复**：旧服务会把接受的破损物料入 `new/available`。数量和 SN 复现见 `R/damage-probe/verified-defect-v1.json`。候选已实现正确分账，但尚未应用主树，更没有上线。

`D/main-application-preflight-v6.json` 已逐文件核对候选的 57 项新增/修改：当前主树均保持原基线字节、无冲突，候选源无漂移。此检查不是应用或发布授权；应用前仍须再次核对。

## 已完成的候选修复

- 入库预览 2.0 按原验收接受量拆为原成色与坏件份额；破损是接受量子集，不重复相加；SN 逐件归类。原本坏件仍只入一份坏件。
- 原子多账户过账保留组织、责任人、位置、SKU 和批次，验收、入库、通知各自独立。历史 1.0 请求保留原始结果，响应丢失后只读回查。
- 0162 前向迁移冻结函数目录、约束及最小权限；禁止新增 1.0 入库，直接 SQL 错分账被拒绝；有新历史时拒绝降级。旧历史不会被静默改成新分类。
- H5 和小程序验证原验收、份额、成色及 SN；小程序先读取原验收再核对预览。
- 正式 CI 矩阵已在候选注册 `return_quality_whole` / `return_quality_mixed` × quantity / serial，共用本地和 CI 业务 helper。**尚未在 GitHub 执行**。

## 已收齐的验证证据

| 范围 | 当前证据 | 能证明什么 |
| --- | --- | --- |
| 四组正式 helper 原生门禁 | `D/formal-quality-verified-v6.json`，各目录 `verified.json` | **全部通过**：数量/SN × 整批/混合破损；真实 PG16.15 API 角色提交、并发单一赢家、原请求恢复、SQL 伪造拒绝、0162 迁移/权限/空库往返/历史保留；终态与 checks 一致、源无漂移、正常停库、进程退出 |
| CI 路由/参数/多包裹服务 | `D/ci-quality-focused-verified-v6.json` | 67 项通过；不是 GitHub 运行 |
| 只读历史分类异常服务 | `D/history-quality-service-verified-v1.json` | 6 项通过；数量整批/部分和 SN、旧分类异常与正确新版分开；原事实不变 |
| 原历史查询回归叠加新投影 | `D/history-quality-regression-verified-v2.json` | **29 项通过**，617.96 秒；原权限、完整证据、快照、游标和阶段分离保留；35297/35300 退出 |
| 当前 head/ORM/往返/扩展业务 | `D/migration-verified-v5.json` | 4 项通过；全迁移链、全部表与 ORM、混合 SN、分批验收 |
| 前端及小程序 | `D/client-verified-v5.json`、`D/mini-next/verified-v2.json` | 前端 2134 项、类型和两种构建通过；小程序全套首次 1082 通过/1 个 Git 边界失败，独立 Git 夹具 65 项通过覆盖该失败；另 37 项入库聚焦通过。重叠数量不可相加 |
| 多包裹服务 | `D/multiparcel-service-verified-v3.json` | 数量/SN × 两种入库顺序共 4 项通过；后来入账后原请求仍准确恢复 |
| 真旧代码升级历史兼容 | `D/native-legacy-verified-v1.json` | 0161 旧应用实际写旧历史，0162 升级保持全部业务行、原回查/重试；旧历史可降旧版恢复再升级。此版本未含新异常投影 |
| 较早独立原生场景 | `D/native-normal-quantity-verified-v3.json`、`D/native-quality-verified-v1.json`、`D/native-mixed-sn-verified-v2.json` | 正常入库、破损数量/整批 SN、混合 SN，分别固定对应较早源清单；不混称最新完整源码 |

主树先前证据仍有效于其固定版本：`P/main-application-v1/verified-v2.json`（1999源前端2132及后端65等）、`P/ci-next/main-application-v1/focused-verified-v1.json`（2005源144项）、`R/focused-verified-v1.json`和`R/native-verified-v1.json`（2008源只读图服务29及原生数量/SN）。`P/ci-next/native-seals-v1-verified.json`、`native-http_sources-v1-verified.json`、`native-multigeneration-v2-verified.json`分别记录封存/HTTP/多代门禁；不要把不同版本或重叠用例相加为全量上线验收。

## 仍在运行：先回读，禁止重复启动

以下已用实时 `ps` 核对，状态仅表示本次观测。每个目录均有 `job.json`、`state.json`、`checks.log`、`worker.log`。

| D 下任务目录 | 状态 | worker / child PID | 子进程存活 |
| --- | --- | --- | --- |
| `native-ordinary-quality-v2` | running | 32487 / 32494 | 是 |
| `native-legacy-quality-quantity-v2` | running | 36318 / 36337 | 是 |
| `native-legacy-quality-serial-v2` | running | 36319 / 36338 | 是 |

- 原坏件/批次门禁已打印普通 quantity 和 serial 账户准入、原子提交及并发通过，仍继续批次/账户复用等场景，不能当完整通过。
- 两组真旧数据异常查询已打印首次升级后原请求回查/重试及全部业务行保持不变；仍须完成降级、旧代码恢复、再升级后的同样核验及正常停库。
- 新 HTTP 入口：`GET /api/v1/stock-operations/loss-reports/corrections/return-history/{root_disposition_id}`。只读总部权限，六个互斥履约阶段、短少观测与破损子集独立，旧错误入库展示准确份额/SN；明确 `current_stock_verified=false`、`write_authorization_provided=false`，不返回原始命令或幂等键。尚未应用正式源码或接入 H5 展示。
- `D/source` 仍被上述原生及 HTTP 任务固定，全部相关读者退出前不可修改。两组旧历史任务另固定 `R/source` 和 `D/legacy-quality-native-v2`，也不可修改。

## 最近失败及处理

- 新 HTTP v1 测试的身份切换夹具仍保留上一总部主体，先返回 `actor_principal_stale` 412；测试原本只预期权限 403。v2 保留准确 412 断言，再切换夹具当前主体，独立要求总部越权 403；应用权限未放宽。原日志与 `D/history-http-check-v1/failure-reviewed.json` 保留，39906/39909 已退出。
- 普通退回 v1 预期两份入库，但正式工单拆回的来源已是坏件，正确结果只有一份；v2 验证原坏件、分批总量、批次/SN和账户复用。旧失败正常停库回执保留。
- 更早失败包括解释器 resolve 丢 venv、SQL CASE 括号、迁移前驱/封存表清单遗漏、测试凭证复用和候选 Git 边界。均保留原始失败及修正证据，不重写为通过。详细历史见下方归档。

## 接下来按顺序执行

1. 收齐仍在跑的 4 个任务。原生必须核对 checks 与终端、PG16 身份/TCP关闭、全部源摘要、正常停库和 worker/child/PG PID 退出；单有 state 或中间 PASS 不够。
2. 通过后将只读异常投影及 HTTP 代码应用到候选：原字节备份，生成新固定源清单，再跑实际注册路由/原历史回归。测试叠加覆盖通过不等于已应用源码。
3. 在所有既定0162迁移、权限、业务/客户端证据齐全后，重新核对主树差异，保留原字节，集成破损修复。验证实际主树；当前不存在可宣称已发布的提交。
4. 将总部历史查询接入 H5；明确累计履约与当前可回收库存不同。历史异常识别不是自动纠正旧库存。
5. 按 [退回补偿设计](LOSS_RETURN_COMPENSATION_DESIGN_20261001.md)实现未出库补偿和正常履约并发隔离，随后分阶段逆向物流；不能调用普通整单取消替代报损派生退回。部分出库、已发运、拒收/短少、已入库需各自实物证据和逆向库存事实。
6. 报废原处置/纠正报废、SN 生命周期和失而复得仍缺，保持明确不可用直到事实/迁移/权限/恢复完整。
7. 全部正式基线缺口继续审计。真实短信/微信/通知/附件、身份角色、全量迁移和期初、准确 SHA 的 GitHub CI、多角色 UAT、连续至少3天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚及真实业务冲销演练均是独立上线条件，不能用局部合成门禁替代。

## 历史与机器入口

本次精简前全文保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_192400.md)，SHA256 `f3935512439197f64308e2b63dacbf0d82038a50d037d64c664907fca174e7f2`；其中包含更早归档链和各批修正经过。

当前机器入口：`D/status.json`、`P/status.json`、`artifacts/loss-formal-application-next/continuation.json`。引用旧版 source 清单时必须保留其版本边界，不将历史 PID 当当前进程。
