# 0157 报损退回发件验收清单

本批实现及本地验收位于 `codex/notification-delivery-worker` 工作树；提交与远端 CI 以开发交接和 Git 状态为准，未部署。
需求依据为正式 V1.0 的 1.8、1.11、3.11 和第 6 节，以及首页公开查询、星星入口 `/xx` 的用户覆盖要求。
状态以 [开发交接](CONTINUE_DEVELOPMENT.md) 和下表终态证据为准。测试数据为本地合成数据，不能视为生产验收。

## 已验证与待验证的范围

| 要求 | 当前证据 | 结论 |
| --- | --- | --- |
| 工程师只能读取本人报损派生退回，保留总部派生人与本人执行人区别 | 正式导入聚焦 22 项；先前目录/隐私专项；数量/SN 原生 HTTP | 本地相关场景通过，真实身份 UAT 待验 |
| 出库与交运使用正式 API 和 API 数据库角色提交 | `formal-http-terminal-v1.json` 两种追踪模式 | 通过 |
| 新会话携带完整原请求回查，结果未知不授予重发许可 | 同上，`freshSessionOriginalCommandRecovery=true` | 通过 |
| 封存后晚到 HTTP 命令被拒绝；只读授权可回查，写授权撤销拒绝新操作 | 同上，`lateSealedExecutionDenied=true`、`readOnlyGrantRecovery=true` | 通过 |
| SN 的 SKU、SN、二维码和 serial_id 必须匹配 | serial 终态列明四类非法证明拒绝 | 通过 |
| 出库/交运/收货/入库相互独立 | HTTP 终态 `noReceiptOrInboundInference=true` | 当前发件场景通过，不能替代收货入库回归 |
| 0157 唯一 head、历史迁移兼容及实际注册 | 正式 22 项，后端副本 37 项；副本应用/迁移 555 个文件与正式相同 | 通过范围存在重叠，不累加为独立用例 |
| 空库 0157→0156→0157；有封存历史拒绝降级且事实/目录不变 | 两个 HTTP PG16 终态 | 通过 |
| 迁移前后正式安全目录、权限及源码不漂移 | 两个 HTTP PG16 终态，`runtimeSecurityBeforeAndAfter=true`、`sourceDrift=[]` | 通过 |
| 出库、交运两种操作的 SQL 双向封存互斥、真实并发、COMMIT 权限到期、完整审计 | 完整封存候选 v2 quantity、serial 全部通过，`formal-seals-terminal-v2.json` | 正式应用/迁移通过；测试驱动已按摘要接入 |
| 普通工单退料提交/取消封存与原请求恢复不受共享 SQL 改动影响 | `formal-ordinary-return-recovery-pg16-v1.log`：24 个数量/SN SQL 拒绝证明，exit 0，停库正常、源码无漂移 | 通过 |
| 普通出库/交运、0155 报损收货及独立入库的当前 0157 完整数据库回归 | 报损数量/SN、普通全流程及首账户兼容全部终态通过，分别见 `ordinary-fulfillment-terminal-v1.json`、`ordinary-return-account-terminal-v1.json` | 本地通过 |
| H5 完整原请求持久化、跨动作阻断、失去写权限后只读恢复 | 前端全量 1962 项 / 113 文件；277 个 src 文件与正式相同；先前合成浏览器 | 数量件与 SN 真实浏览器→正式 API→PG16 均通过，各 2 次物理 POST，响应丢失后刷新及只读恢复通过；SN 390px 无横向溢出 |
| 构建、公开/私有入口边界 | 类型检查、双构建及 `formal-public-entry-v1.log` | 通过，私有构建体积提示保留 |
| 完整公开知识目录发布 | `formal-entry-gate-v1.log`：`PUBLIC_CATALOG_NOT_READY` | 用户暂缓知识源，仍不可宣布完整发布通过 |
| 新 sender_http/sender_seals 的 CI 矩阵和安全分派 | 6 文件补丁已逐一核对原/结果摘要接入；正式工作流拓扑 31 passed / 1 deselected | 本地正式接入检查通过，准确 SHA 远端 CI 待验 |
| 仓库安全、准确提交 SHA 的完整远端 CI | 安全扫描 v3 通过 2030 个候选文件；旧 SHA PG16 静态分片 runner shutdown | 仍待完成；远端中断根因未知 |

证据均在 `cloud_oam/artifacts/loss-sender-read-next/`，该目录受 Git 忽略。
运行库是专用临时 PostgreSQL 16，数量 `run-8ot549t6`、SN `run-q0y9xh2n` 都已正常停止，HTTP 会话 exit 0。
不能只看日志中间的 PASS：须同时读取 checks.json、cluster-state.json 和进程终态。

## 本批失败的处理记录

- 完整封存候选 v1 在 exercise 中引用未定义 `execute`，报 NameError。候选 v2 改用已经导入的 `outbounds.execute_outbound`，全部候选 Python 全局引用检查通过，14 项 CI 边界检查通过。原失败日志、源文件摘要和正常停库记录保留；v2 quantity 和 serial 均完整通过。
- 前端第一次全量有既有收货确认框 1 秒等待超时；独立复验与保留业务断言的等待修正后，全量 1962 项通过。
- 不修改生产约束来消除测试错误，不把本地通过解释成已上线。

## 接入顺序

1. 收齐完整封存 quantity、serial 及普通退料兼容终态；仍有运行者时保持正式非 Markdown 源码冻结。
2. 审查并应用 `sender-ci-next-v2.patch`，验证原文件/结果摘要；不得重复应用旧版本补丁。
3. 补当前 head 兼容、真实浏览器联调、最终安全检查及必要复验，证据齐全才提交。
4. 对准确 SHA 核验完整远端 CI，再继续基线中处置恢复、反向冲销、报废、人员调拨、离职交接及真实服务和生产验收缺口。
