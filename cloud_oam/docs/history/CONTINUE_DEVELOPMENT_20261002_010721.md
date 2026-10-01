# RSC个人仓开发交接

更新：2026-10-02 01:01:12（北京时间）。完整上线目标仍 active；本轮已将0162修复及只读历史界面应用主树。未提交、推送或部署；本地通过不等于 GitHub 或生产通过。

## 接续入口与边界

- 工作树：`/Users/lizhiwang/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`；HEAD：`9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 先完整阅读根目录 `docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md`。本轮已读；所有后续开发遵循其正式范围。
- 禁止 reset、revert 或丢弃未提交改动。既定证据全部收齐后才提交。不要将候选验证通过表述为主树、GitHub 或生产已通过。
- Python：`cloud_oam/.venv/bin/python`，不得 resolve 解释器软链接；pytest 从 cloud 目录用 `-o pythonpath=backend`。Node：`/Users/lizhiwang/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`。
- PG 16.15：`cloud_oam/artifacts/pg16-native-20260920/install/bin`。仅用任务创建、TCP 关闭的私有 Unix socket 测试库；不连接生产数据库或执行外部业务写入。
- 公开首页知识查询，星星按钮到 `/xx`。小程序保持公开知识查询；飞书知识源优先级低。短信、微信、真实登录和生产验收分别核验。

以下路径均相对 `cloud_oam`：P=`artifacts/loss-correction-request-seals-next`；R=`P/return-history-next`；D=`artifacts/damaged-return-inbound-next`。

## 当前代码位置

| 位置 | 当前内容 | 是否已进入主树 |
| --- | --- | --- |
| 主工作树 | **2034源，0162**；破损按成色入库、旧请求保留、总部历史异常查询及H5展示 | **是，未提交** |
| `D/source` | 同一2034源，`migration-source-v7.json`；与主树逐字节一致 | 已应用 |
| `D/history-quality-work-v1` / `D/history-http-work-v2` | 只读投影和HTTP的开发及验证来源，保留旧证据 | 已应用候选和主树 |
| `D/history-ui-work-v1` / `D/history-ui-source-v1` | H5开发与独立验证副本，当前UI清单为`history-ui-source-v2.json` | 已应用候选和主树 |

**前向破损入库缺陷已在主树修复，尚未发布。** 0161错误入`new/available`的复现保留在`R/damage-probe/verified-defect-v1.json`。旧库存分类没有被迁移静默改写；异常可只读识别，授权逆向纠正仍未实现。

`D/main-application-v1/application.json`记录73项应用（26新增、47修改、无删除）；全部原字节在其`preserved/`目录。`source.json`证明主树2034源等于已验证的整合候选。应用前原2008源完全无漂移；没有覆盖其他任务改动。当前主树被复验任务固定，修改前必须确认这些读者退出。

## 已应用的修复

- 入库预览 2.0 按原验收接受量拆为原成色与坏件份额；破损是接受量子集，不重复相加；SN 逐件归类。原本坏件仍只入一份坏件。
- 原子多账户过账保留组织、责任人、位置、SKU 和批次，验收、入库、通知各自独立。历史 1.0 请求保留原始结果，响应丢失后只读回查。
- 0162 前向迁移冻结函数目录、约束及最小权限；禁止新增 1.0 入库，直接 SQL 错分账被拒绝；有新历史时拒绝降级。旧历史不会被静默改成新分类。
- H5 和小程序验证原验收、份额、成色及 SN；小程序先读取原验收再核对预览。
- 正式 CI 矩阵已在主树注册 `return_quality_whole` / `return_quality_mixed` × quantity / serial，共用本地和 CI 业务 helper。**尚未在 GitHub 执行**。

## 已收齐的验证证据

| 范围 | 当前证据 | 能证明什么 |
| --- | --- | --- |
| 整合候选全量前端及相关后端 | `D/integrated-verified-v7.json` | **前端2161项、后端55项通过**；当前主树与该2034源逐字节一致。UI类型及双构建通过，后台JS包较大，正式性能验收仍未完成 |
| 主树小程序 | `D/main-application-v1/mini-verified.json` | **1083项全过、0失败**，已在真实Git工作树验证，旧候选Git边界错误消除 |
| 四组正式 helper 原生门禁 | `D/formal-quality-verified-v6.json`，各目录 `verified.json` | **全部通过**：数量/SN × 整批/混合破损；真实 PG16.15 API 角色提交、并发单一赢家、原请求恢复、SQL 伪造拒绝、0162 迁移/权限/空库往返/历史保留；终态与 checks 一致、源无漂移、正常停库、进程退出 |
| CI 路由/参数/多包裹服务 | `D/ci-quality-focused-verified-v6.json` | 67 项通过；不是 GitHub 运行 |
| 只读历史分类异常服务 | `D/history-quality-service-verified-v1.json` | 6 项通过；数量整批/部分和 SN、旧分类异常与正确新版分开；原事实不变 |
| 总部历史 HTTP 接口 | `D/history-http-verified-v2.json` | **6 项通过**，128.65 秒；真实数量/SN旧版和新版业务、部分破损、原事实不变、准确旧异常、私有无缓存、主体过期412与总部越权403分开；404、无原始请求泄漏；40443/40447退出。现已应用主树，只读API不是补偿写入口 |
| 原历史查询回归叠加新投影 | `D/history-quality-regression-verified-v2.json` | **29 项通过**，617.96 秒；原权限、完整证据、快照、游标和阶段分离保留；35297/35300 退出 |
| 当前 head/ORM/往返/扩展业务 | `D/migration-verified-v5.json` | 4 项通过；全迁移链、全部表与 ORM、混合 SN、分批验收 |
| 前端及小程序 | `D/client-verified-v5.json`、`D/mini-next/verified-v2.json` | 前端 2134 项、类型和两种构建通过；小程序全套首次 1082 通过/1 个 Git 边界失败，独立 Git 夹具 65 项通过覆盖该失败；另 37 项入库聚焦通过。重叠数量不可相加 |
| 多包裹服务 | `D/multiparcel-service-verified-v3.json` | 数量/SN × 两种入库顺序共 4 项通过；后来入账后原请求仍准确恢复 |
| 真旧代码升级历史兼容 | `D/native-legacy-verified-v1.json` | 0161 旧应用实际写旧历史，0162 升级保持全部业务行、原回查/重试；旧历史可降旧版恢复再升级。此版本未含新异常投影 |
| 较早独立原生场景 | `D/native-normal-quantity-verified-v3.json`、`D/native-quality-verified-v1.json`、`D/native-mixed-sn-verified-v2.json` | 正常入库、破损数量/整批 SN、混合 SN，分别固定对应较早源清单；不混称最新完整源码 |

主树先前证据仍有效于其固定版本：`P/main-application-v1/verified-v2.json`（1999源前端2132及后端65等）、`P/ci-next/main-application-v1/focused-verified-v1.json`（2005源144项）、`R/focused-verified-v1.json`和`R/native-verified-v1.json`（2008源只读图服务29及原生数量/SN）。`P/ci-next/native-seals-v1-verified.json`、`native-http_sources-v1-verified.json`、`native-multigeneration-v2-verified.json`分别记录封存/HTTP/多代门禁；不要把不同版本或重叠用例相加为全量上线验收。

## 当前主树复验与已结束任务

| D 下目录 | 状态 | worker / child PID | 子进程存活 |
| --- | --- | --- | --- |
| `main-application-v1/backend` | running | 32468 / 32473 | 是 |
| `main-application-v1/mini` | passed | 32469 / 32474 | 否 |
| `main-application-v1/native-head` | running | 32470 / 32475 | 是 |

上述状态已用实时ps核对。当前运行目标是主树2034源；不要修改被固定的主树源码或重复启动。`state.json`、`checks.log`、`worker.log`和`job.json`保留各目录；观察超时不代表执行终止。

此前三组原生已全部结束并核验：

- `D/native-ordinary-quality-verified-v2.json`：数量、SN、批次、批次+SN原坏件入库；分批复用精确账户且不重复转坏；多行同账户、重算流水；4种当前权限拒绝、15种畸形关联拒绝、不同收货并发及显式新预览；迁移、权限、历史拒降、正常停库与进程退出。
- `D/native-legacy-quality-verified-v2.json`：数量/SN真0161旧代码生成历史，0162升级后的总部API角色READ ONLY异常识别、准确数量/SN、接收人总部权限拒绝；全部业务行不变。降级后旧代码恢复、再升级再验证，正常停库、摘要及进程退出均齐全。旧`R/source`和开发harness已不被活进程固定。
- H5聚焦111项、父页接入9项（重叠不得相加）和类型/双构建通过，见`history-ui-verified-v1.json`、`history-ui-parent-verified-v2.json`、`history-ui-build-verified-v2.json`；之后整合前端全套2161通过。

总部正式只读入口：`GET /api/v1/stock-operations/loss-reports/corrections/return-history/{root_disposition_id}`。H5“报损纠正”中的原退回处置提供显式“查询退回历史”；六个互斥履约阶段、短少观测、破损子集和旧分类异常分别展示。身份变更后丢弃迟到响应，刷新失败不保留旧结果，不自动重试或写库存。

## 最近失败及处理

- 新 HTTP v1 测试的身份切换夹具仍保留上一总部主体，先返回 `actor_principal_stale` 412；测试原本只预期权限 403。v2 保留准确 412 断言，再切换夹具当前主体，独立要求总部越权 403；应用权限未放宽。原日志与 `D/history-http-check-v1/failure-reviewed.json` 保留，39906/39909 已退出。
- 普通退回 v1 预期两份入库，但正式工单拆回的来源已是坏件，正确结果只有一份；v2 验证原坏件、分批总量、批次/SN和账户复用。旧失败正常停库回执保留。
- 更早失败包括解释器 resolve 丢 venv、SQL CASE 括号、迁移前驱/封存表清单遗漏、测试凭证复用和候选 Git 边界。均保留原始失败及修正证据，不重写为通过。详细历史见下方归档。

## 接下来按顺序执行

1. 收齐主树HTTP/head和原生当前head复验：终态、checks、源摘要、PG身份及正常停库、进程退出。小程序1083项已经收齐，不必无理由重复。
2. 0162前向入库修复、只读异常接口和H5已应用主树，无需再次应用73项文件。既定证据全部齐全后再处理提交；GitHub精确SHA门禁和生产验收仍分别待办。
3. 按 [退回补偿设计](LOSS_RETURN_COMPENSATION_DESIGN_20261001.md)实现未出库补偿及正常履约并发隔离，继而逐阶段逆向物流；不能调用普通整单取消替代报损派生退回。部分出库、已发运、拒收/短少、已入库需各自实物证据和逆向库存事实。
4. 旧成色异常已经可查询，授权历史分类纠正仍缺；不能把当前余额或同SKU其他SN当成原物资仍可回收的证明。
5. 报废原处置/纠正报废、SN生命周期和失而复得仍缺，保留明确不可用入口直到事实/迁移/权限/恢复完整。
6. 完整正式上线还需真实短信/微信/通知/附件、身份角色、全量迁移及期初、准确SHA GitHub CI、多角色UAT、连续至少3天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚及真实业务冲销演练。局部合成门禁不能替代这些条件。

## 历史与机器入口

本次精简前全文保留在 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_192400.md)，SHA256 `f3935512439197f64308e2b63dacbf0d82038a50d037d64c664907fca174e7f2`；其中包含更早归档链和各批修正经过。

当前机器入口：`D/status.json`、`P/status.json`、`artifacts/loss-formal-application-next/continuation.json`。引用旧版 source 清单时必须保留其版本边界，不将历史 PID 当当前进程。

HTTP终态更新前交接保留于 [历史交接](history/CONTINUE_DEVELOPMENT_20261001_192435.md)，SHA256 `29f43853ed7f1fe8a03e444ae7f5f9f5d1655f06453bbfe5f3229d85d7be4e45`。

本次主树整合更新前全文：[历史交接](history/CONTINUE_DEVELOPMENT_20261002_010112.md)，SHA256 `c4064cc405f47a59d9150916dd79d892a6b0b70adf6c5f29a722b1509afc1870`。
