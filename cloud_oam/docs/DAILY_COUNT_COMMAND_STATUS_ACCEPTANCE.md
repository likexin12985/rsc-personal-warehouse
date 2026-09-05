# 日常盘点原计数命令核验：服务端验收

日期：2026-09-05。遵循根 AGENTS.md、V1.0 及独立业务状态规则。
本切片只操作本地合成测试和私有 GitHub 仓库；没有访问任何业务生产系统。

## 契约

`GET /api/v1/stocktakes/{task_id}/rounds/{round_id}/scopes/{scope_id}/count-command-status`

仅接受四个单值查询参数：`operation`（`initial_count` / `recount_count`）、
`actor_person_id`、`actor_authorization_version`、`trace_request_id`。
拒绝额外/重复参数、请求体及 `Idempotency-Key`；不需要写密钥或原请求正文。

- 只核验非期初初盘/复盘的一个历史 scope count。期初接口不变。
- 当前登录人员、授权版本、任务可见范围及目标执行人权限必须成立；管理员能读任务
  不等于可以查询别人的原计数命令。原人员/版本改变仍保持恢复阻塞。
- 以冻结 scope/snapshot、不可变 completion、授权/计数/SN/附件 manifest、轮次 submission
  及正式 audit 链证明历史事实。初盘、复盘使用各自 trace namespace，并拒绝同 actor、
  同原 trace 在这两个 namespace 或多个 scope 中的歧义；不宣称全系统所有业务 namespace 唯一。
- `confirmed` 只返回 completion ID、原轮次、完成时间、scope 已完成和该笔是否导致封轮。
  “本笔导致封轮”由 `sealing_completion_id`、相邻审计伴随事件及提交事实证明，
  不从当前 task/round 状态推断。后续已过账/已关闭不代表这次查询推进这些状态。
- `not_observed` 仅表示未观察到匹配 trace，不能证明未执行、清除哨兵、允许重试或换键补写。
  证据损坏、歧义或权限变化均失败关闭，不能降级成 `not_observed`。
- 响应不包含盘点数量、标签/SN 原文、备注、附件链接、请求正文、哈希或幂等键；
  正常和框架级错误都设置 private/no-store、no-cache、no-referrer、nosniff。

## 数据与锁边界

无需新增表或迁移，Alembic head 仍为 `20260905_0061`，函数体/ACL/历史迁移不变。
旧非期初 completion 的 opening 专属 request JSON 为 null 是合法历史，不猜测回填。
旧 count audit 的 `created_at` 使用模型默认时间；核验以被审计哈希绑定的
`occurred_at == completed_at` 为依据，不能要求两者与模型创建时间全部相等。

服务不 flush、commit、rollback，不发 POST，不修改任何业务事实。但会取得 PG 行锁，
**不是 PostgreSQL `READ ONLY` 事务**。调用方负责及时结束事务。
锁顺序为 ledger → task → 全任务相关 principal → round/0032 owner graph →
来源证据及文件 UUID 有序全集 → audit head。audit 后只做已有证明核验及只读权限重验。
0032 已覆盖任务历史图；0057 限制附件绑定插入并取得 task owner，旧绑定不可修改。

审批、分配、占用、出库、发货、物流签收、OAM 收货、RSC/个人仓入库、通知送达、对账同步
保持独立；查询不推进任何一个状态，也不开放期初启动或供给履约。

## 可复现测试

在仓库根目录、已安装冻结依赖的隔离测试环境执行：

```sh
PYTHONPATH=cloud_oam/backend python -m pytest -q cloud_oam/backend/tests/test_stocktake_count_command_status.py
PYTHONPATH=cloud_oam/backend python -m pytest -q cloud_oam/backend/tests cloud_oam/edge_sync
bash cloud_oam/scripts/verify_repository_safety.sh
git diff --check
```

新增测试明确加入 PostgreSQL 16 workflow 的静态文件清单。
新增定向合成回归 `58 passed`，含两范围、初盘/复盘、真实服务过账/关闭后历史查询、
保管移交/尾部授权变化、时间差兼容、无业务写及框架级隐私错误。
冻结功能源码 `812debc65fa4a85d0a14718d63d4ac4723208bab` 的本地后端/边缘全量
`2922 passed, 1 skipped`（702.54 秒）；运行前后相关源码与该 SHA 无差异。
跳过项仅允许 GitHub 一次性 PG16 数据库执行，不能把它算作本地真库通过。
动态门禁在真实 API role 下验证初盘提交后及关闭后原 trace / 未见 trace，并检查
查询前后库存、审计、Outbox、状态事件和计数/提交事实不变，事务仍由调用方持有。
候选全量及准确 SHA 的 CI 结果见交接文档；在回读成功前不计入已验收发布。

### 首次 PG16 失败与修正依据

初始功能 SHA `812debc65fa4a85d0a14718d63d4ac4723208bab` 的 run `33957264704`：
静态 `1989 passed, 1 skipped, 1 warning`（726.76 秒），动态 `1 failed`（203.71 秒）。
失败位于新增 GET 的冻结时间核验，容器已清理；不能将本次 run 计作通过。

非期初启动先捕获 `cutoff_at`，等待审计锁后再次取时，写入
`task.frozen_at = freeze.valid_from = initial_round.started_at`；0047 的既有正式数据库约束
允许 `cutoff_at <= started_at`。新查询误加的 `freeze.valid_from == cutoff_at` 与之冲突。
后继修正采用上述正式时序，同时要求 completion 不早于其所属轮次开始；不改历史迁移，
不补写历史时间，不放宽审计/manifest/权限校验。新增真实推进时钟及错配负例，
不能继续依赖所有取时都固定为同一 NOW 的测试覆盖此边界。

## 尚未完成的边界与下一切片

1. 两端仍未接入日常计数跨进程持久哨兵；本 API 不代表重启恢复完成。
2. 复盘目前为合成 SQLite 服务/API 回归；本次 PG16 新增验证的是初盘及其终态历史，
   **没有新增非期初复盘的真实 PG 服务链**。下一切片先补隔离 PG 复盘 fixture/多轮回归，
   再接客户端持久恢复，不借初盘门禁声称复盘真库验收完成。
3. 初盘历史从冻结 scope 重算，不要求其他 scope 原执行人今天仍在职。
   复盘的深层来源证明仍复用现有更严格的 live scope 校验；来源原执行人停用、
   库位保管变化可能返回 503 并继续阻塞。完整历史恢复需显式历史证据模式及独立回归，
   不能靠改当前授权或跳过来源证明来放行。
4. 后续哨兵仅存操作/人员/版本/任务/轮次/范围/随机 trace；存储失败不发写请求，
   禁存原正文、数量、备注、扫描原文、签名 URL、token 和幂等键。重启先查询，不自动重放。
5. 创建、下发、差异、复核、过账、关闭、人工未执行封存仍须独立命令恢复证明。
   OSS/CSP、审批转派、通知、可信控制投影、最终产物与恢复演练仍为独立待办。
