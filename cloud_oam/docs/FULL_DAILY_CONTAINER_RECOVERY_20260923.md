# 六张非空日终表的当前容器联合恢复

2026-09-23。在冻结的 1,091 项源码/门禁输入及准确镜像
`rsc-formal-api-current:20260923-daily-ops-65d43a2e4251` 上，使用全新自有 PostgreSQL
16.15 源库运行真实日终映射、截止与审核 HTTP/worker/COMMIT 夹具。该夹具包含部分解释、
指定项退回、独立总部审核、授权撤销拒绝、原请求永久封存及提交回执丢失后的精确恢复。
[源库终态](../artifacts/current-container-source-20260923-full-daily-attempt1/result.json)
为 `passed`，迁移 HEAD `20261113_0134`、212 表 / 788 行，六张日终表全部非空：

| 表 | 源库和恢复库行数 |
| --- | ---: |
| `daily_comparison_mapping_decisions` | 1 |
| `daily_reconciliation_cutoffs` | 3 |
| `daily_review_bindings` | 2 |
| `daily_review_consumptions` | 7 |
| `daily_review_events` | 7 |
| `daily_review_request_seals` | 1 |

5 个可用合成附件的字节数和 SHA-256 均与快照目录一致，另有 1 个待上传意图。
新造的两份合成证据文件使用真实字节的 SHA-256；只在隔离测试夹具中替换占位摘要，
未修改冻结的业务源码、迁移、正式镜像或附件完整性规则。源库已停止。

[当前容器联合恢复结果](../artifacts/current-container-recovery-20260923-full-daily-attempt2/result.json)
为 `passed`。真实 Compose、私有 Docker、准确 API 镜像及两个独立 PostgreSQL 16
实例参与；正常备份退出 0，实际数据库阻塞期限退出 124，取消退出 143，父 worker
终止退出 -9，损坏对象退出 1 并拒绝。每轮完成后会话与关系锁均为 0，临时输出和 worker
被清除，服务容器身份不变。联合包验证 5 个可用对象、1 个待上传意图后进入第二新库；
源库、恢复库和快照预期的 212 表 / 788 行逐表内容 SHA-256 完全一致，包含上表所有
非空日终事实。源/恢复实例标识分别为 `7688618953624494094` / `7688619779340566541`。
八角色、七个 LOGIN 的新密码、错误/源库旧密码拒绝及 API 运行身份核验通过。
所有 Compose 容器、私有引擎、数据库与 VM 已停止，清理错误为空。逐场景记录及原始
数据库核对见同目录 `guest-evidence.tar`。

第一次尝试因 Lima VM 启动时 `systemd` 健康检查降级，在进入数据库和备份前停止；
失败结果保留于 `current-container-recovery-20260923-full-daily-attempt1/`，其 VM 停止状态
已核验。第二次在相同源样本和镜像上取得完整终态通过。

这是**本地合成数据、合成 OSS 传输、测试环境 API** 的恢复证明，不能充当正式上线证据。
真实私有 OSS/KMS、目标 Linux、真实身份与 OAM 来源、期初开账、实际设备 UAT、
连续三天对账、生产 RPO≤5 分钟/RTO≤2 小时、远端 GitHub PG16 runtime/Client
及当前完整静态第三片仍须分别取证。本批未提交、推送或部署。
