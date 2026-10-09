# GitHub 开源复用选择与试点落点（2026-10-09）

**试点 MVP，不等同完整 V1；发布仍为 `not_ready`。** 本文落实用户“去 GitHub 找通用开源进行 copy，节省时间”的要求。已完整阅读正式 V1 基线，按用户缩减后的试点范围评估实际缺口；沿用当前 FastAPI / SQLAlchemy / React 工作树，保留所有既有改动。

后续实现顺序是：已有依赖能做的直接使用；独立、维护中的开源组件能做的优先引入；仅项目特有的库存、权限和审计约束继续本地实现。复制代码时登记固定版本、完整提交、源文件、许可证、改动和必要验证。下载源码、读懂示例、完成隔离实验、正式接入和上线验收分别记录。

## 筛选结果

本轮使用系统实际 HTTP/HTTPS 代理 `127.0.0.1:7890` 访问公开 GitHub 仓库。该端口只记录本轮观察，后续先读系统配置。匿名 API 之后出现 rate limit，固定提交的公开 raw 文件仍可读；这不是账号登录失败，没有提取凭据或重新登录。

| 来源 | 本轮核验 | 采用方式与当前决定 |
| --- | --- | --- |
| [OpenBao](https://github.com/openbao/openbao/releases/tag/v2.7.1) | v2.7.1，MPL-2.0，完整提交 `a5db72cef75c24b920ade02065b18dd8eb666bac` | **本批第一项实际复用**：使用官方 Agent 的 AppRole 自动认证和 file sink，省去自写令牌认证、生命周期调度和原子文件替换程序。新增候选配置与隔离证明，尚未启用生产服务。 |
| [InvenTree](https://github.com/inventree/InvenTree/releases/tag/1.5.6) | 1.5.6，MIT，`284a670413cc544b899f6a60713b9676beccb8ea`；Django/DRF、React/Mantine | 零件、库位、SN 和库存边界最接近。优先参考/摘取依赖小的通用片段；现有并发和回滚已有对应，未重复复制其 Django 库存模型或重跑旧测试。 |
| [FastAPI full-stack template](https://github.com/fastapi/full-stack-fastapi-template/tree/7257e606cb94eada1dca40173e2b7f99dd549bc6) | MIT，`7257e606cb94eada1dca40173e2b7f99dd549bc6` | 后续出现测试报告/本地服务编排缺口时，可摘取 `frontend/playwright.config.ts`。当前模板使用 Python >=3.14、SQLModel、密码认证和另一套路由/UI，不整体迁移。 |
| [TanStack Table](https://github.com/TanStack/table/tree/d8ae30bcfe8665ef43d7557f4a492e0caa6f9c40) | MIT，`d8ae30bcfe8665ef43d7557f4a492e0caa6f9c40`，该提交 React 包 9.2.8 | 后续新增复杂只读列表时优先采用无头分页/排序组件，保留现有外观。本批不安装；不混用 v8/v9 示例。 |
| [Ant Design ProComponents](https://github.com/ant-design/pro-components/tree/1c070b9b38a95da923262e609fc9e40fb5e732e3) | MIT，`1c070b9b38a95da923262e609fc9e40fb5e732e3`，该提交为 3.1.15-6 预发布，要求 antd 6 | 留作未来独立后台模块的备选。目前实际前端没有 Ant Design，引入会增加第二套 UI 体系；首发收益不足。 |
| [ERPNext](https://github.com/frappe/erpnext/releases/tag/v15.122.0) / [Snipe-IT](https://github.com/grokability/snipe-it/releases/tag/v8.8.0) | GPL-3.0 / AGPL-3.0-or-later；Frappe / PHP-Laravel | 参考占用、部分交付、失效人员/库位等场景。技术栈和许可义务均需另评估，本轮没有把它们的代码复制进项目。ERPNext 同时维护 v16，API 的 latest v15 标记不代表最高主版本。 |

上述项目均通过官方仓库核验为未归档且近期维护；维护状态不构成安全或适配保证。表中除 OpenBao 外均为筛选/参考，不能写成已安装或已接入。

## 本批具体复用：官方 Agent

- 候选配置：[agent-autoauth.json.example](../deployment/openbao-pilot/agent-autoauth.json.example)。
- 新增隔离证明：[agent-autoauth-proof.py](../deployment/openbao-pilot/agent-autoauth-proof.py)。
- 原版许可、来源和摘要：[第三方说明](../third_party/openbao/NOTICE.md)、[source.json](../third_party/openbao/source.json)。
- 上游接口依据：[AppRole 示例](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/website/content/docs/agent-and-proxy/autoauth/methods/approle.mdx)、[file sink](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/internal/command/agentproxyshared/sink/file/file_sink.go)、[生命周期处理](https://github.com/openbao/openbao/blob/a5db72cef75c24b920ade02065b18dd8eb666bac/api/lifetime_watcher.go)。

使用已固定签名/摘要的官方 2.7.1 二进制，不复制或维护它的 Go 实现，也不安装第二个密钥产品。配置只开启 AppRole auto-auth + file sink；不开放 Agent HTTP listener、API proxy、缓存或模板渲染。API 继续从既有受限 Unix socket 调用解密，现有 `openbao_runtime_transport.py`、两条 decrypt policy 和默认关闭行为均保持。

短期 batch token 不需要 API 持有 `renew-self` 权限；Agent 在生命周期临界时使用其持有的 AppRole SecretID 重新认证，并通过 file sink 原子替换令牌文件。`token_no_default_policy=true`，实际策略只能含两个精确 Transit decrypt 路径；角色配置必须独立回读，Agent 配置本身不证明服务端角色权限正确。

正式环境还须满足以下接入条件，它们不是本机单用户实验的结论：

1. Bao、Agent/projector、API 分别使用三个非 root UID。projector 主组设为共享 GID，sink `uid=-1/gid=-1` 继承进程身份；令牌目录 `0750`，文件 `0440`（JSON 中十进制 `288`），明确 umask 并在真实 Linux 上回读。
2. API 只读挂载整个令牌目录，不能只 bind 一个文件 inode；官方原子替换会生成新 inode。API 不能读取 bootstrap SecretID，也不能写入令牌目录。每次请求按现有 transport 重新读令牌。
3. bootstrap 由受控流程投递到私有临时目录；Agent 读取后删除 SecretID 文件并只在内存缓存。SecretID 必须有有限有效期和受控补发/撤销流程；Agent 重启和 SecretID 到期都需要恢复安排。示例没有写入真实凭据，也不是可无人照管的正式部署配置。
4. batch token **不能按单个 token 立即撤销**。撤销 SecretID 只阻止后续认证，已经发出的 token 仍可使用到短 TTL 结束。紧急止用需禁用该专用策略并精确验证拒绝；影响范围是使用此策略的所有令牌，恢复策略可能让未过期令牌再次有效。生产响应时不能误把 SecretID 撤销当作全部旧 token 即时失效。
5. 令牌过期、Bao 封存、文件/身份不符继续由现有 API 失败关闭。官方 Agent 的认证重试仅针对凭据生命周期，不是重试库存/发运/收货业务写入；未知业务写结果仍须先精确回读。

本机证明只使用新建合成 AppRole、两用途合成密文、临时目录和官方进程，回执只记布尔结果、摘要和计数。原始 token、SecretID、解封份额和明文密钥不进入 Git 或证据。过程与结果见 `artifacts/open-source-reuse-20261009/agent-autoauth-proof-attempt-*.json`；首次失败保留，最终有效结果须看该目录的本批完成回执。

### 本批新增验证终态

最终 `agent-autoauth-proof-attempt-06.json`：**exit 0，21 项实际行为检查通过**，`cleanupCompleted=true`、`sourceInputsStable=true`。使用官方 OpenBao 2.7.1 的本机 Darwin ARM64 二进制及真实临时服务，不是 mock；12 秒 token TTL 仅为隔离实验参数。

- 实测 UDS AppRole 认证、两条精确解密权限、`0440` 文件、读取后删除 SecretID、自然到期前重新认证、原子替换 inode、旧文件描述符仍指向旧 token。
- 7 条越权路径全部 403，包括 `renew-self`、创建 token、读密钥/生成 DEK/轮换等；随后同一个 token 成功解密，排除“过期导致假权限拒绝”。
- 删除隔离专用 policy 后，两项解密均拒绝；root 只读核验该 token 尚未过期，恢复相同 policy 后同 token 再次成功，确认真实策略撤权边界。
- 撤销 SecretID 后不能再登录或投递新 token，已发 token 仍可短暂使用，随后真实自然过期拒绝。没有把它写成单 token 即时撤销。
- 源码、配置和原 decrypt policy 在运行前后摘要一致。proof SHA-256 `67f324e2419f9e49305fdd0763d807748943d19fb0b093b9ed1f9e6e1fa45e63`；配置 SHA-256 `d92c124a4242f9cf5eefa6bd8e3b70df68d1dc16c5be2d5b8cd5749ac1842791`。

attempt 01–03 的 Agent 配置解析失败及 attempt 04 的 Mac 临时目录组继承失败均保留；只修新配置的 HCL JSON 布局及受控夹具目录组，没有放宽正式 transport 权限。attempt 05 是修正后的首次 19 项通过；独立复核补充同 token 正向对照及运行前后摘要校验后，才执行 attempt 06。未重新运行既有 Transit、库存、PG16 或前端终态套件。

这 21 项属于一次新隔离实验，不是 21 个 pytest 用例，不累计或冒充旧套件。Linux 三 UID、只读目录挂载、tmpfs、正式 bootstrap 生命周期仍明确未验证；本机没有证明硬性内存上限，也没有执行生产部署。

## 库存来源对照：避免重复实现

实际读取 InvenTree 固定提交的 [stock/tests.py](https://github.com/inventree/InvenTree/blob/284a670413cc544b899f6a60713b9676beccb8ea/src/backend/InvenTree/stock/tests.py)，SHA-256 为 `049d63d2a6f299ac8152db75807888a6a37f367bdca1d0b0bf00d74f540a6e65`。对照结果：

- `test_concurrent_take_stock_does_not_phantom_remove` 的“并发耗尽恰好一成功”对应本项目 `pg16_reservation_gate.py::assert_reservation_gate` 的数量竞争 winner/loser 和 SN 的 `[201,409]`，已有真实 PG16 历史证据。
- `test_entries_discarded_on_rollback` 对应 `test_inventory_posting.py::test_service_flushes_without_commit_and_caller_rollback_removes_everything`；本地还检查余额、流水游标、审计和 Outbox 一并回滚。没有为本次复用研究重写或重跑这些终态测试。
- `test_adjustment_stale_quantity` 的旧 ORM 对象场景可保留作后续查漏。现有入账流程锁住余额并由不可变流水证明投影，漂移会失败关闭；本次未找到完全同构的“双 Session 预加载再交错扣减”专门证据，不能将一般投影守卫冒充该精确场景已经通过。

历史通过见 [PG16 占用门禁修复记录](PG16_RESERVATION_QUANTITY_GATE_REPAIR_20260927.md)，绑定当时的提交/0141；不是当前候选/0181/hosted PG16 通过证据。

## 后续开发和发布边界

本批收益是用已有官方实现替代尚未完成的自定义令牌投递进程，并建立可追踪的开源来源清单。没有可信依据给出“省了几天”或上线百分比。剩余上线工作按真实依赖继续：受控 Linux 身份/挂载和凭据补发、真实 Transit/STS/私有 OSS、最新候选 hosted PG16/迁移权限、短信登录、多角色/真机 UAT、备份恢复和发布回滚。

试点链条保持申请/提交→审批→最小分配/占用→后台人工履约并发运→本人收货→个人仓入账。拣货与后置区块继续按 capability/角色隐藏。不可变流水、幂等、审计、权限、安全、取消/关闭守卫、最小站内通知继续保留。不得通过引入开源项目恢复已明确后置的业务范围：

- 履约后供给容量/0178 分配后建计划、释放后重分配和复杂历史恢复。
- 拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收。
- 报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接。
- 复杂报表/Excel/打印、短信/微信/飞书真实业务通知渠道及投递运维。首发必需的短信登录仍单独待验。

本轮不修改业务代码或现有数据库迁移，不提交、推送或部署。新配置与隔离结果不替代任何当前发布门禁。
