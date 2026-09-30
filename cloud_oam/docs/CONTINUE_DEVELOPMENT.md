# RSC 个人仓开发交接

更新时间：2026-09-30T23:06:45.126580+08:00。此文记录本批提交前核验，实际提交以 Git HEAD 为准；远端 CI 和生产验收不得由本地结果推断。上一版见 [历史交接](CONTINUE_DEVELOPMENT_HISTORY_20260930_2300.md)。

## 工作树、范围与当前结果

- 用户指定工作树 `/Users/replace-with-local-user/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`。本批起点及已核对远端 HEAD 为 `5e847d282cb1e6abdd1bc3a5af31e4c2c10301c4`。禁止 reset、revert 或丢弃改动。
- 遵循 [正式 V1.0 基线](../../docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)。目标仍是完整上线版本，尚未实现全部基线功能及生产验收。
- 本批：本人报损 H5、本人报损派生退回发件目录/出库/交运/原请求恢复与封存、0157 迁移和 CI 门禁、静态测试中断诊断。
- 公开首页为“交流备件知识大全”，无登录表单，星星按钮到 `https://rscwz.cn/xx`；公开小程序仅知识查询。知识源按用户指示暂缓，公开目录仍 pending/0。
- 审批、处置、出库、交运、签收、收货、入库、通知送达与对账各自独立。未知写入只回查完整原请求，不自动重发或换 key。
- 全部本地 PG16 会话已终态通过并正常停库，Vite/浏览器测试 API 已停止，源码冻结已解除。不要再轮询旧会话 91123、91787、47623、24963、97209、92124、61797、67928、25541。

## 本批终态证据

所有发件证据在受 Git 忽略的 `artifacts/loss-sender-read-next/`。下列 PG16 均检查源码清单与正常停库；全为合成数据，不代表生产验收。

| 范围 | 证据与结果 |
| --- | --- |
| 正式 sender HTTP 数量/SN | `formal-http-terminal-v1.json`：0157 实际注册、API 角色提交、完整原请求新会话回查、封存/迟到拒绝、撤写保读、空库往返和历史保留全部通过 |
| 完整 sender 封存数量/SN | `formal-seals-terminal-v2.json`：双操作双向 SQL 排斥、真实并发、COMMIT 到期、审计回滚、迁移/安全通过；原测试模块已按相同摘要正式接入 |
| 报损收货/独立入库数量/SN | `browser-quantity-and-receipt-terminal-v2.json` 及 `formal-loss-receipt-inbound-pg16-v1.log`：权限、异常回滚、并发唯一、恢复/封存、独立入库、迁移往返和历史保留通过 |
| 普通退料提交/取消恢复 | `formal-ordinary-return-recovery-pg16-v1.log`：24 个数量/SN SQL 双向互斥证明通过 |
| 普通出库/交运/收货/入库完整兼容 | `ordinary-fulfillment-terminal-v1.json`：12 阶段，含原 mini SDK、异常响应、实际 SQL 拒绝、封存/执行与重复提交并发、收货异常、独立入库及流水/SN 证明；会话 91787 exit 0，库 `run-isjyyt_c` stopped/passed/serverExitCode=0 |
| 普通首次入库账户兼容 | `ordinary-return-account-terminal-v1.json`：数量、SN、批次、多行合并、15 畸形图、权限撤销和并发，空库往返/有历史拒绝降级；91123 exit 0，库 `run-m2_fd41o` 正常停库 |
| 真实浏览器→正式 H5/API→PG16 | `browser-quantity-terminal.json`、`browser-serial-terminal.json`：每种模式仅 1 出库 + 1 交运 POST；交运 COMMIT 后模拟丢失响应、撤销实际写权限，刷新后只读恢复成功，未产生收货/入库；SN 390px 无横向溢出。身份为合成注入，真实短信/设备 UAT 仍缺 |
| 正式后端聚焦 | `formal-integration-backend-v1.log`：22 passed；独立副本 37 passed，应用/迁移 555 文件与正式相同；范围重叠不累计为 59 项 |
| 前端/构建 | `integration-frontend-full-v2.log`：1962 passed / 113 文件，277 src 与正式相同；类型、公开/私有构建及公开入口边界通过，私有 bundle 体积提示保留 |
| 正式 CI 矩阵接入 | `sender-ci-next-v2/applied.json`：6 文件原/结果摘要吻合；`formal-ci-integration-v2.log`：31 passed / 1 deselected，不等于远端 CI |
| 既有证据与后续源码差异 | `pre-ci-evidence-source-delta.json`：7 组旧终态的后续差异仅已审查 CI/门禁补丁，生产应用和迁移未变 |
| 本人报损发起 H5 | 见 [独立验收](LOSS_SUBMISSION_H5_ACCEPTANCE_20260930.md)：77 聚焦、62 后端、1079 小程序及数量/SN PG16；最终全量前端以后续 1962 项为准 |

## 失败记录与处理

- 封存驱动 v1 未定义 `execute`，v2 使用已有 `outbounds.execute_outbound` 后数量/SN 全通过；未削弱生产约束。
- 浏览器 v1 的 ASGI2/ASGI3 识别错误已修；数量 v2 出库后复查曾遇 fixture 临时认证替换，原请求只读恢复成功。v3 用专用锁隔离测试身份切换，SN 全程通过。失败日志和旧驱动保留。
- 前端首次全量有既有确认框等待超时；等待按钮可用并保留原断言后完整复验通过。
- 仓库安全早期三份文档含个人目录，已改占位符。提交前最终扫描 `formal-repository-safety-final.log` 已终态 exit 0：2031 文件 PASS；`git diff --check` 通过。普通完整兼容门禁源码清单已再次逐文件校验，无漂移。
- GitHub 只读 API 已确认远端 HEAD 为 5e847d2；另一个耗时的 `git ls-remote` 被主动停止（signal 15），不是推送失败。未执行远端业务写入。

## 提交、CI 与下一步

1. 本地业务证据已收齐，做最终安全与差异核对后提交本批；`formal-integration-v3.patch` 与 `sender-ci-next-v2.patch` 都已应用，严禁重复应用。
2. 提交后为准确新 SHA 取得 Client/PG16 终态。当前远端仅 5e847d2：Client 36690470446 success；PG16 36690470476 failure，旧静态分片 runner shutdown 根因仍未知。新增诊断记录阶段/资源信息，不自动等于问题解决；不要盲目重跑旧任务。
3. 继续 [基线审计](FORMAL_V1_BASELINE_GAP_AUDIT_20260923.md) 和 [处置实施合同](STOCK_LOSS_DISPOSITION_IMPLEMENTATION_PLAN_20260927.md)：总部处置/派生退回独立请求恢复及永久封存、专用反向与纠正接续、受控报废、人员调拨和离职交接仍未完成。
4. 真实 PNVS/微信身份、OSS/KMS、授权历史/附件迁移、实物期初、多角色设备 UAT、至少三天差异解释、500 用户压测、RPO≤5 分钟/RTO≤2 小时及回滚/冲销演练仍缺。受邀 H5 试点不能替代完整上线目标。
5. 目标仍是用户确认的旧备份服务器；历史定位为 118.31.37.87，旧 star-oam 占 80/443。本轮未 SSH、迁移生产数据库或发送真实短信/通知。目标机配置与当前候选 prepare/start 回执须重新只读核对；不能引用旧镜像宣称当前已部署。

## 运行约定

从 `cloud_oam` 使用 `.venv/bin/python`、`PYTHONPATH=backend`。Node 使用已安装 runtime。PG16 二进制为 `artifacts/pg16-native-20260920/install/bin`。原始日志、临时数据库和合成驱动在被忽略的 artifacts，不会随 Git push 上传；不可将运行库或凭据加入仓库。
