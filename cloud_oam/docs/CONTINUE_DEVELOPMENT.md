# RSC 个人仓开发交接

核验时间：2026-09-30T15:04:16+08:00（Asia/Shanghai）。路径除特别说明外相对 `cloud_oam/`。

**当前停在提交前：退回收货、独立入库与原请求恢复 H5 已实现，本地数量/SN PostgreSQL 16 两套均通过并正常停库；代码仍未提交、未部署。HEAD 为 ff25289。GitHub 父版本完整门禁仍未通过，真实服务及生产验收仍缺。**

本轮按用户“整理交接文档”收束状态，只调整交接记录和证据索引，不提交开发中的代码。下一位从第 6 节继续；不要轮询已经结束的旧工具会话，也不要重新开发已有收货后端。

## 1. 工作树与约束

- 工作树：`~/.codex/worktrees/06f6/oam`；分支 `codex/notification-delivery-worker`。默认 checkout 不是目标工作树。禁止 reset、revert、丢弃未提交改动。
- HEAD：`ff25289a6d497711dd4275260e05b5eab6454b52`，审批待办、详情和 H5 恢复版本。前序 `1f65dcbed655a0a1e7c4054ba8b6fe1f2d86d3d1` 为 0156 审批封存；`b4a964e5443439794429de6fee85281d9b7faa5f` 为审批恢复。已有发布记录见 `artifacts/loss-review-queue-next/push-result.json`；本轮未重读远端 ref。
- 修改或测试前完整阅读[正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)和 [AGENTS.md](../../AGENTS.md)。用户后续指令优先。
- 公开首页为“交流备件知识大全”，无登录；星星管理按钮跳 `https://rscwz.cn/xx`，`/xx` 是路径。公开小程序仅知识查询；飞书知识源已被用户延后。
- 审批、出库、发运、签收、验收、入库、OAM 收货、通知和对账分别保存事实。未知写结果只回查原请求，不自动重发或换 key。
- 用户确认旧备份脚本中的 `118.31.37.87` 为目标服务器；Ubuntu 24.04、star-oam 占用 80/443 属历史观察，部署前重查。本轮未连接服务器。
- 本地验证不等于真实业务授权或生产验收。OAM 凭据留在本地；飞书需求走用户指定 NIO Chat 托管 CLI；本轮未连接业务系统。

## 2. 完成范围和剩余缺口

| 范围 | 当前事实 | 待完成 |
| --- | --- | --- |
| 0155 收货/独立入库/恢复后端 | 已有正式服务、HTTP 和数量/SN 数据库证据 | 不重复实现；真实服务 UAT |
| 0156 审批封存、审批恢复 | 已提交并有发布记录 | 准确提交完整 CI、真实 UAT |
| 审批待办、详情和 H5 | ff25289 已提交；客户端 CI 成功 | PG16 完整 CI、真实附件/设备验收 |
| 退回收货 H5、独立入库、请求恢复 | 正式源码已接入，本地合同/页面/全量/双构建/浏览器合成/数量及 SN PG16 均有终态 | 最终差异审查后提交，发布后跟进新 SHA CI，真实 UAT |
| 报损发起 H5 | 当前后端合同草案与 24 项测试在 ignored artifacts 中 | 审核移植；正式恢复、adapter、页面和验收 |
| 报损退回发件侧 | 内部退回/出库/发运服务已有，普通工单正式 HTTP 已有 | 报损 origin 对应 HTTP、回查/封存和数据库证明 |
| 报废冲销、人员调拨、离职交接 | 需继续逐项基线审计 | 实现缺口及端到端验收，不能按其他模块测试推断完成 |

本批无新迁移、无权限种子调整；当前迁移 head 为 `20261205_0156`。本地通过不是完整 V1.0 上线证明。

## 3. 未提交实现清单

已修改 `frontend/src/App.tsx`、`styles.css`，新增以下文件与对应测试；另有交接/审计文档修改。精确清单用 `git status --short --untracked-files=all`，`git diff --stat` 不包含未跟踪文件。

| 文件 | 职责 |
| --- | --- |
| `frontend/src/formalReturnReceiving.ts` | 身份、互斥来源、分页、包裹/历史；bigint毫单位、微秒时间校验 |
| `frontend/src/formalReturnReceipt.ts` | 验收原命令、Python一致hash、预检、三码及原请求结果 |
| `frontend/src/formalReturnInbound.ts` | 独立入库计划、已接受量/SN、原命令及posted/交易证明 |
| `frontend/src/returnReceivingRecovery.ts` | 写前存储并回读、人员/包裹Web Locks、未知结果保留、回查及显式封存 |
| `frontend/src/returnReceivingAdapter.ts` | apiNoReplay、当前身份/权限前后校验、精确404 not_observed |
| `frontend/src/FormalReturnReceivingPage.tsx` | 目录、验收历史、逐笔入库状态、确认和待恢复请求 |
| `frontend/src/ReturnReceiptForm.tsx` | 数量/SN实物验收、异常说明及正式证据上传组件 |
| `frontend/src/App.returnReceiving.test.tsx` | 导航/直达权限及apiNoReplay；页面另有Page.test.tsx |
| `frontend/src/test-fixtures/return-receiving/` | 工单/报损×数量/SN四份合成合同，含验收和独立入库 |
| `backend/tests/test_return_receiving_h5_contract.py` | 当前后端schema、原命令hash、独立过账事实互验 |

私有路由 `/return-receiving` 和导航“退回收货与入库”已接入，需要 stock_operation.read 及 admin/provincial_manager，页面另核验当前责任和写权限。撤销写权限但保留读权限时仍可恢复。验收API使用 shipment_id；入库API使用 receipt_id。

页面已提供预检、明确确认、独立入库、异常上传、待核验列表和永久封存确认。SN接受项实际SKU/SN/二维码初始为空，支持手输或键盘扫码输入；不代表手机相机扫码已完成。正式上传组件已接入，但测试/预览使用合成适配器，真实OSS未验收。

确认页已展示接受、拒收、短少、接受中破损及本次入库的准确 SN，且只在明确确认后提交。新增4个测试覆盖异常SN展示和独立入库确认。macOS大小写冲突已通过 `FormalReturnReceivingPage.tsx` 命名修复，勿改回与 `formalReturnReceiving.ts` 同名。下一步转向当前候选门禁证据、真实服务验收及报损发起/发件侧缺口。

## 4. 已有本地证据

日志集中在 `artifacts/loss-return-h5-next/`。以下是已有测试终态的本轮回读，不是本轮重新执行全部测试；测试集合有重叠，不相加。

| 项目 | 终态 | 证据 |
| --- | --- | --- |
| 合同、恢复、HTTP adapter | 74 passed | contracts-recovery-v2.log |
| 页面/路由 | 16 passed，2 文件 | page-routes-v2.log |
| 前端全量 | 1789 passed，102 文件 | frontend-full-v2.log |
| 后端合同互验 | 4 passed | backend-parity-v3.log |
| 小程序回归 | 1079 passed | mini-full-v1.log |
| 公共/私有双构建及类型 | 退出 0；私有 bundle 仍有 >500 kB 提示 | build-public-v2.log、build-warehouse-v2.log |
| 公开入口隔离 | 通过；知识目录 pending/0，不是内容就绪 | public-entry-v1.log |
| 期初共享协议 | PASS | opening-protocol-v1.log |
| 迁移唯一 head | 1 passed，91.59 秒 | migration-head-v1.log |
| 依赖检查 | No broken requirements found | pip-check-v1.log |
| 仓库安全 | 本轮整理后 PASS，1966 文件 | handoff-safety-final.log（前序 repository-safety-v3.log） |
| 浏览器合成验收 | 数量验收、SN 独立入库，未知回执刷新后精确回查仍保留；390/1280 宽度通过 | browser-acceptance-v1.json 及同目录三张 PNG |
| 本批原生 PG16 | quantity、serial 均 passed，正常停库，源码无漂移 | native-terminal-v1.json |

前端全量覆盖最终页面与 SN 确认逻辑，随后局部 CSS 修复由最终构建和浏览器验证。浏览器 adapter 为合成场景，未连接真实 API/OSS，不证明相机扫码、真实身份、长列表或设备 UAT。

### PostgreSQL 16 精确断点

本地运行器会话 57217 已退出 0；不要继续轮询。PostgreSQL 16.15，新建临时库，未访问生产库：

| 模式 | 运行目录（artifacts/local-stock-loss-return-receipt-pg16/checks/ 下） | 终态 |
| --- | --- | --- |
| quantity | run-ouwc9w3y | passed=true；stopped；serverExitCode=0 |
| serial | run-hpbc7t_f | passed=true；stopped；serverExitCode=0 |

两模式均验证运行权限、空库迁移往返目录/ACL、有历史拒绝降级、API 禁止直接执行私有证明函数。1778 份源码清单无漂移；本轮重新核验原始 checks.json、cluster-state.json、source-manifest.json 的 SHA256 与全部清单文件。终态摘要及各 hash 见 native-terminal-v1.json。两模式共同 manifest SHA256 为 `4e70f693d66a1715572266124a9886803cb95a173e97d3f80c06b1c70963a93b`。

源码冻结已结束；这些证据仅覆盖该清单，后续改动应重新评估测试范围。单 head/依赖会话 9251 也已结束，无需轮询。临时浏览器与 4179 合成预览已在前序验收后关闭。

## 5. GitHub 状态与已知问题

本轮实时回读 PG16；下表均对应 **ff25289**，不覆盖未提交 H5：

| 门禁 | 最新已知状态 | 证据 |
| --- | --- | --- |
| [Client 36672988759](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36672988759) | 前序本日回读 completed/success | client-ci-current-v1.json |
| [PG16 36672988843](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36672988843) | 本轮 in_progress；19 成功、3 失败、2 运行中；整体 conclusion 为空 | handoff-pg16-latest.json |

static_safety (0)/(1)/(2) 均 failure；inventory、migrations 仍运行。0/1 已取日志显示 runner shutdown/operation canceled；2 的本次失败原因尚未取日志确认。不能把所有失败归因于网络，也不能把这次运行视为绿色门禁。接手先回读准确 run 的终态及失败日志；不要在原运行未结束时盲目重复启动。

旧 Git push 曾出现 443/HTTP2/TLS 问题，后经 Git Data API 核验对象 SHA 并非强推更新成功。后续先读当前远端再决定发布，不执行带旧固定 SHA 的脚本、不强推。客户端前次 TLS 超时已在随后成功回读中恢复，不能据此要求重新登录。

## 6. 接手步骤

1. 完整读基线，确认工作树、分支、HEAD 和全部 diff（含未跟踪文件）。保护现有改动。
2. 读本文件、当前验收文档及本批 continuation.json。两套本地 PG16 已结束，不重复启动旧门禁。复核 source manifest 与拟提交改动；没有新代码修改无需机械重跑相同测试。
3. 回读 ff25289 的 PG16 CI 终态，取得 static (2) 等失败日志，分清运行器故障与断言失败；必要时修复并做针对性验证。
4. 对本批 H5 做最后代码/差异审查，核对所有必要门禁后精确暂存、提交。不要使用覆盖整个工作树的盲目 git add，不纳入下一批 ignored 草案。发布后只以新准确 SHA 的 CI 取证；提交/推送不是部署。
5. 接报损发起 H5：`artifacts/loss-submission-h5-next/` 有四份真实后端服务生成的合成 fixture（数量/SN × found/sealed），export-v1.log 4 passed；formalLossSubmission.ts/test 草案 contracts-v1.log 24 passed、typecheck-v1.log 退出 0。尚未接正式源码、页面、持久恢复与 adapter，不能计已完成。审核相对 imports、字段限制、原请求全量校验后再移植。
6. 按[发件 HTTP/恢复审计](LOSS_SENDER_HTTP_RECOVERY_AUDIT_20260930.md)补报损来源路径：普通工单路由已存在，不能伪造 work_order_id。当前封存约束仅允许报损 receive_return；发件封存需新增迁移、来源证明、双向并发互斥、权限与恢复验收，不能只挂路由。报损派生提交涉及处置和退回两项事实，不能复用普通单一结果假设。
7. 继续正式基线缺口审计及真实 UAT/上线取证，按第 7 节逐项关闭。

只读恢复命令：

```sh
cd ~/.codex/worktrees/06f6/oam
git branch --show-current
git rev-parse HEAD
git status --short --untracked-files=all
git diff --check
cd cloud_oam
cat artifacts/loss-return-h5-next/native-terminal-v1.json
cat artifacts/loss-return-h5-next/continuation.json
~/.local/bin/gh run view 36672988843 --repo likexin12985/rsc-personal-warehouse --json headSha,status,conclusion,jobs,url
```

后端 `.venv/bin/python`，从 cloud_oam 设置 `PYTHONPATH=backend`。Node 目录 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin`，pnpm 为 `~/.cache/codex-runtimes/codex-primary-runtime/dependencies/bin/fallback/pnpm`；Node 加 PATH，从 frontend 运行命令。原生 PG16 bin 为 `artifacts/pg16-native-20260920/install/bin`。完整门禁参数以仓库根目录 `.github/workflows/` 为准，不猜生产 DSN。

**artifacts 被 Git 忽略，不随 clone/push 转移。** 换机或换工作树必须保留经检查的测试日志、终态 JSON、源码清单和合成 fixture，或按对应脚本重建；不要复制真实数据库、令牌或会话。合同草案也在 ignored artifacts，切换 checkout 不会自动带走。

## 7. 上线仍需独立取得的证据

- 真实短信 PNVS、唯一人员映射及实发回读；真实 OSS/KMS。
- 公开知识内容和来源授权（用户已延后）；期初盘点、OAM 只读批次和历史数据校验。
- 真实设备/身份 UAT，三级部分审批、多仓分配、分批发货/收货、取消释放、盘点及工单消耗。
- 连续至少 3 天对账差异解释，500 用户规模压测，RPO≤5 分钟/RTO≤2 小时备份恢复。
- 应用回滚、同步批次回退、业务冲销及 HTTPS 双入口生产验收。备案审核截图不等于部署完成。

受邀 HTTPS H5 试点与完整 V1.0 分别验收；不提供无完整证据支持的上线日期或完成百分比。

相关文档：[退回 H5 验收](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)、[基线缺口审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md)、[UAT/上线矩阵](FORMAL_V1_UAT_AND_LAUNCH_EVIDENCE_20260925.md)、[部署入口](PILOT_DEPLOY_ENTRY_20260922.md)、[双入口烟测](PILOT_LIVE_ROUTE_SMOKE_20260924.md)、[短信认证](PNVS_01_AUTHENTICATION_PLAN_20260921.md)、[通知运维](NOTIFICATION_OPERATIONS_BASELINE_AUDIT_20260919.md)。

整理前原文保留在[本次历史快照](CONTINUE_DEVELOPMENT_HISTORY_20260930_1501.md)，更早历史另见其链接。历史“正在运行”“待实现”等语句只代表原时点，不能覆盖本文件当前结论。
