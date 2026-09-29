# RSC 个人仓开发交接

核对时间：2026-09-29 19:30（Asia/Shanghai）。本页为接续入口，覆盖历史文档中的旧 HEAD、候选和“当前状态”；每次接手仍须重新检查 Git、CI 和实际进程。本轮已同步 0155，新增报损提交/封存 HTTP 接口完成本地验证，尚未部署。

**当前结论：0155 已随交接提交 `8990ebd` 推送，客户端 CI 成功，14 个报损 PG16 分支成功；综合 PG16 和静态门禁未齐。新增报损提交/封存 HTTP 已通过数量/SN 原生验证与 86 项路由回归，其提交结果见本地证据。正式上线仍未放行。**

## 1. 工作位置与固定边界

- 工作树：`~/.codex/worktrees/06f6/oam`；分支：`codex/notification-delivery-worker`。下文路径默认相对 `cloud_oam/`。
- 先完整阅读[正式 V1.0 需求基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)及根目录 [AGENTS.md](../../AGENTS.md)。不得 reset、revert、丢弃或覆盖未提交改动。
- 公开首页为“交流备件知识大全”，无登录界面；星星后台按钮进入 `https://rscwz.cn/xx`。`/xx` 是路径，不是另一个二级域名。个人主体小程序当前仅公开知识查询，私有业务页面不进入公开包。
- 飞书知识源按用户要求后置；真实目录及公开性审核未完成，不宣称公开知识站已验收。若恢复飞书读取，遵循 AGENTS.md 的 NIO Chat 托管只读路径及凭据边界。
- 审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入账、通知、对账分别存证。结果未知先精确回查原请求，禁止盲目重发。
- 本地 PG16 只用全新自有临时实例；不传外部/生产 DSN，不重启已停止的历史库，不伪装 GitHub CI 在本机执行破坏性门禁。
- 不把开发授权扩成真实短信、外部业务写入、生产迁移或切换授权。凭据、生产数据和运行日志不进 Git。
- 历史交接记录的额度约定为剩余 10% 时停止新增开发并交接；接手核验当前额度，不继承历史百分比。

## 2. 版本与远端门禁

| 对象 | 本次确认的状态 | 证据/边界 |
| --- | --- | --- |
| 0155 功能提交 | `d76efd135bb7e0f476a187809e14fd5b41e3c235` | 报损收货、独立入库与恢复 |
| 已推送/远端分支头 | `8990ebddfe16d11265e7c6524fa7357d2771e26e` | 包含 0155 和交接整理；Git Data API 保留原 commit/tree，force=false，已精确回读 |
| 本轮提交/封存 HTTP | 基于 `8990ebd`，本地验证完成 | `artifacts/loss-http-submit/`；实际提交见 `git log` 及 `commit-evidence.json`，未推送到远端前不继承任何 CI |
| 迁移 head | `20261204_0155`，父节点 `20261203_0154` | 本轮 HTTP 接线不新增迁移或生产权限种子 |
| 8990ebd 客户端 CI | `36560246356`：completed / success | [准确运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36560246356) |
| 8990ebd PG16 CI | `36560246385`：整体仍运行；14 个报损分支成功 | [准确运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36560246385)；runtime/静态 2 仍运行，静态 0/1 已失败，原始日志确认 runner 收到 shutdown signal 后取消；不能视为通过 |
| 上一版 a03761e CI 终态 | `36521873050`：failure | [历史运行](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36521873050)；静态 0 runner 失联，runtime 超过 6 小时取消，12 个报损分支成功 |
| 生产状态 | 未部署本批，没有正式上线验收 | 本地、准确 SHA CI、真实业务 UAT、生产回读分别核验 |

Git 推送超时后先回读远端仍为 a03761e，才使用既有 Git Data API 精确提交并回读 8990ebd；没有 force push。证据：`artifacts/continue-0155-release/push-result.json`。上一版 runtime 原始日志和阶段耗时分析同目录；222 个阶段完成，但后续检查未执行，不能按通过验收。退回出库单阶段约 2250 秒，首次账户边界约 1430 秒，累计触及 6 小时上限；需要在保留全部场景和迁移顺序依赖的前提下拆分综合门禁，不能仅跳过慢检查。

## 3. 最新一批完成了什么

本轮新增正式 `POST /api/v1/stock-operations/loss-reports` 与 `/request-seal`：当前本人/submit_loss 权限重检，请求头与原 request/key 一致性，COMMIT 完成才返回成功；数据库/审计故障返回结果未确认，保留原请求回查，不自动重发。封存若发现原单已提交，返回原单而不另造封存。区域核实/总部审批、客户端报损页面及发件接口仍待补齐。

新增 `test_stock_loss_write_routes.py`、`pg16_stock_loss_write_http_gate.py` 和 `run_local_pg16_stock_loss_http_checks.py`。后端路由/来源/回查/封存 **86 passed**，CI 拓扑 **13 passed**。数量 `run-fn3k7kzl`、SN `run-xjv3qzx8` 实际 PG16 API 角色 HTTP 通过：真实提交回执丢失后找回、提交前故障回滚、封存回执丢失后找回、迟到提交拒绝、精确重试不重复冻结、撤销写权限后仍能按读权限回查。两库 stopped / serverExitCode=0，1722 份非文档源码零漂移；首次探测因测试配置源码更新主动中止并正常停库，不计通过。首次 pytest 因工作目录错误无法导入 app，改在 backend 执行后通过，原日志保留。证据见 `artifacts/loss-http-submit/evidence-index.json` 与 `native-terminal.json`。CI 增加独立数量/SN `submission_http`，当前候选矩阵为 **16 分支**；已上传版本仍为 14 分支。

下面是已推送 0155 的完成范围：

1. 报损包裹接收列表/详情及收货预检、提交、回查、封存支持真实五字段来源：`origin_kind`、`loss_operation_id`、`loss_line_id`、`headquarters_decision_id`、`disposition_id`。保留 new/used/damaged 成色，不补假工单，不与普通工单合同混用。
2. 收货只保存实物验收，不变更库存；独立入库经统一流水过账，覆盖数量件和 SN、新件区域账户首次创建。
3. 客户端恢复记录按报损处置与包裹隔离，保留原 request/hash；网络结果未知、切页及再次进入先查询。收货和入库分别封存，迟到命令拒绝。
4. 0155 迁移补齐 PostgreSQL 当前授权与历史事实证明、精确通知接收人和来源互斥；内部证明函数不授 API 直接执行权。SQLite 对不能等价证明的报损写入失败关闭。未修改旧迁移。
5. CI 新增数量/SN `return_receipt` 分支；当前源码报损矩阵为 14 个分支。远端 a03761e 的 12 个分支不包含本批增量。

| 代码入口 | 用途 |
| --- | --- |
| [0155 迁移](../backend/alembic/versions/20261204_0155_stock_loss_return_receipts.py) | SQL 证明、封存、账户准入与升降级 |
| [来源解析](../backend/app/formal_services/stock_return_receipt_origin.py)、[接收投影](../backend/app/formal_services/stock_return_receiving.py) | 报损来源与普通工单隔离 |
| [收货合同](../backend/app/stock_return_receipt_schemas.py)、[入库合同](../backend/app/stock_return_inbound_schemas.py) | HTTP 输入输出边界 |
| [客户端来源工具](../miniprogram/utils/loss-return-origin.js) | 来源比较及恢复坐标 |
| [服务回归](../backend/tests/test_stock_loss_return_receipt.py)、[迁移回归](../backend/tests/test_stock_loss_return_receipt_migration.py) | 数量/SN、事务与合同验证 |
| [自有 PG16 运行器](../scripts/run_local_pg16_stock_loss_return_receipt_checks.py)、[原生检查](../backend/tests/pg16_stock_loss_return_receipt_gate.py) | 两类独立新库、权限、并发、迁移及历史证明 |

## 4. 本地证据索引与测试口径

日志根目录：`artifacts/loss-receipt-inbound-0155/`。这些是 **ignored 本地证据，不随 Git 克隆**；交接到另一台机器须取得受控证据或重跑，不能将缺失文件视为通过。

| 证据文件 | 结果及范围 |
| --- | --- |
| `service-v6.log` | 11 passed，含 2 个收货 HTTP 合同测试；`http-v5.log` 与其重叠，不加总 |
| `ordinary-regression-v1.log` | 80 passed，普通工单收货、入库、封存、权限和 HTTP 兼容 |
| `mini-all-v2.log` / `mini-focused-v3.log` | 小程序全量 1079 passed；后续来源比较改动聚焦 33 passed，属于复验，不加总 |
| `migration-v3.log` / `orm-head-v2.log` | 20 项迁移回归、3 项 ORM/迁移图通过 |
| `security-focused-v2.log` / `security-current-account-v3.log` | 首轮 342 passed / 2 failed；旧测试账户函数哈希修正后两项单独通过。不得写成同一进程 344 全绿 |
| `loss-inbound-http-v2.log` | 额外数量/SN 入库 HTTP 与 Node 合同 2 passed；辅助 `test_loss_inbound_http.py` 位于 ignored 证据目录 |
| `native-v4-terminal.json` | 数量/SN 原生 PG16 均 exit 0，checks=passed、sourceDrift=[]，两实例 stopped / serverExitCode=0 |
| `final-source-v4.json` | 1719 份非文档源码摘要；文档整理后另核对无漂移 |
| `repository-safety-final.log` / `pip-check.log` | 功能提交前仓库安全 1896 文件 PASS；依赖无冲突。文档整理后的安全检查单独记录 |
| `completed-checks.json` / `verification-log-sha256.json` | 各次终态、重叠范围与原始日志摘要索引 |

原生实例目录：`artifacts/local-stock-loss-return-receipt-pg16/checks/run-zam7t1r3`（数量）和 `run-w437n0sp`（SN）。只读检查结果，不复用旧库。

原生已证明：空库双向迁移目录/ACL 复原、有历史拒绝降级、API 不能执行私有证明、实时授权过期拒绝、4 类锁、并发超量单赢家、同键精确重试、收货库存中立、独立入库、原请求恢复及封存阻止迟到写入。数量/SN 各有 5/6 类畸形收货及 2 类畸形入库在 COMMIT 回滚；入库后历史可回读。

早期失败日志保留。v3 原生检查因执行中源码变化最终失败，不能算完整通过；提交依据为 v4 零漂移终态。测试总数不跨批、跨 SHA、跨重叠用例相加。

## 5. 接下来按什么顺序做

| 顺序 | 工作 | 完成标准 |
| --- | --- | --- |
| 1 | 解决综合门禁累计超过 6 小时及静态 runner 取消 | 上一版终态已定位；当前 CI 继续取证。按依赖拆分并证明所有原场景仍执行，保留聚合失败关闭；不能放宽或删减门禁 |
| 2 | 完成本轮 HTTP 增量提交及准确版本远端门禁 | 本地证据已齐；准确提交回读后验证客户端、完整 runtime/静态及 16 个报损分支；当前正在运行的旧 SHA 不被提前取消 |
| 3 | 补报损发起/审批正式接口及请求恢复 | 提交及永久封存 HTTP 本轮已补；[报损路由](../backend/app/routers/formal_stock_losses.py) 的区域/总部审批及相应请求恢复仍缺，客户端完整报损流仍缺。沿用角色/范围、同键原子性及旧权限拒绝 |
| 4 | 补报损发件侧出库、发运接口/客户端恢复 | [退回路由](../backend/app/routers/formal_stock_returns.py) 发件坐标仍要求工单；使用精确报损来源，分别回查/封存，数量/SN 与未知结果端到端验收 |
| 5 | 补报废反向、人员间调拨及离职交接 | 报废审批/处置/反向分别留事实；独立人员调拨闭环；交接案、唯一未结、双方确认、未结事项清理、个人仓归零及双层关闭 |
| 6 | 继续基线逐项审计及真实试点准备 | 报表范围/筛选/订阅、打印、真实来源/身份/期初、短信及其他通知渠道、OSS/KMS、设备 UAT、备份回滚、性能和连续三天对账逐项补证 |

下一批开发先闭合一个可验收切片，不同时打开上述全部写入口。接收侧 0155 本地已完成，不能重写成“仍无报损收货”；也不能据此宣称整个报损闭环或正式上线完成。

## 6. 上线环境与仍缺的现场证据

- 用户已确认旧备份脚本连接的 `118.31.37.87` 是目标机，不再重复询问。**主机状态仅有 2026-09-24 历史只读快照**：Ubuntu 24.04/x86_64，旧 star-oam 占用 80/443；本次未连接或修改服务器。部署前须重新核验容器、端口、网络、数据卷、镜像和回滚点。
- 2026-09-24 公网曾仍显示旧登录页，准确新版本尚无双入口公网验收。不能把当时 HTTP 200 或旧镜像可达当作新版本部署成功。
- 备案截图显示申请审核通过；最终备案记录、实际域名/网站名称、公开内容及 HTTPS 需分别核对。截图不代替部署或上线验收。
- 短信认证已有配置和合成验证；真实 PNVS/身份映射/最小权限、实发及回读尚无验收证据，不回答“短信已搞定”。通知 outbox 和恢复也不等于真实渠道送达。
- 真实 OSS、KMS、期初盘点/历史迁移、OAM 批次语义、设备 UAT、500 用户压测、RPO≤5 分钟/RTO≤2 小时与演练、连续至少三天差异解释仍分别待验。
- 受邀 `/xx` 小范围试点与全量正式 V1.0 是不同放行范围，均需准确候选和书面验收；不提供未经依据的完成百分比或上线时间承诺。

## 7. 接手命令与文档导航

在指定工作树执行只读检查：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git status --short
git log -5 --oneline
git diff --stat
git diff --check
```

阅读基线后，先查本页证据目录及 GitHub 链接。若需要重跑本地报损 PG16，在 `cloud_oam/` 使用已安装的 PG16 二进制；例：

```sh
.venv/bin/python scripts/run_local_pg16_stock_loss_return_receipt_checks.py --postgres-bin artifacts/pg16-native-20260920/install/bin
```

运行前确认该路径存在、测试配置及 Node 可用，冻结非文档源码；脚本自动创建并停止两类自有实例，保存完整终态。不要为了文档改动重复运行整批业务门禁。

- [正式基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)：当前业务缺口与详细历史审计。
- [V1.0 UAT 与上线证据矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)：现场验收清单；旧日期的候选状态不覆盖本页。
- [部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[公网双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)：目标准备、健康、路由和切换门禁。
- [短信认证专项](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维审计](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)：供应商/渠道/恢复的独立缺口。
- [截至本次整理前的完整交接原文](CONTINUE_DEVELOPMENT_HISTORY_20260929.md)：3048 行逐字保留，含过期 HEAD、阶段性失败和当时待确认记录；仅作历史证据，不执行其中旧“下一步”。
- [更早交接归档](CONTINUE_DEVELOPMENT_HISTORY_20260921.md)：早期实现与验证过程。

维护方式：更新本页现行值及核验时间，详细日志留在受控证据目录；不再往入口重复追加“最新/当前”时间块。历史状态不可覆盖实际源码、准确 SHA 的 CI 或生产回读。
