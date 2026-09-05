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
新增定向合成回归 `62 passed`（15.82 秒），含两范围、初盘/复盘、真实服务过账/关闭后历史查询、
保管移交/尾部授权变化、时间差兼容、推进时钟及逆序负例、无业务写和框架级隐私错误。
冻结修正源码 `8cabfdecb0e8734403fb8863e90d06951383f164` 的本地后端/边缘全量
`2926 passed, 1 skipped`（539.43 秒），退出码 0；运行前后后端/边缘/PG workflow 与该 SHA 无差异。
跳过项仅允许 GitHub 一次性 PG16 数据库执行，不能把它算作本地真库通过。
动态门禁在真实 API role 下验证初盘提交后及关闭后原 trace / 未见 trace，并检查
查询前后库存、审计、Outbox、状态事件和计数/提交事实不变，事务仍由调用方持有。
准确 SHA 的 GitHub PG16
[run 33958198821](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33958198821)
已全绿：静态 `1993 passed, 1 skipped, 1 warning`（737.46 秒），动态 `1 passed, 1 warning`
（223.17 秒），job 于 `2026-09-05T09:48:21Z` 完成，包含测试容器清理在内全部步骤成功。
同 SHA 的客户端
[run 33958198902](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33958198902)
全绿：Web 776、小程序 647、类型检查、构建及仓库安全检查通过。后继文档提交不改变
已验收后端/边缘/迁移/客户端/PG workflow；上述结果只覆盖本切片，不等于生产放行。

### 首次 PG16 失败与修正依据

初始功能 SHA `812debc65fa4a85d0a14718d63d4ac4723208bab` 的 run `33957264704`：
静态 `1989 passed, 1 skipped, 1 warning`（726.76 秒），动态 `1 failed`（203.71 秒）。
该 SHA 本地全量 `2922 passed, 1 skipped`（702.54 秒）不能替代真库验收。
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

### 下一切片实施顺序（只读定位，尚未实施）

先在 `backend/tests/test_postgresql16_release_gate.py` 增加独立 helper，保留
`_complete_0051_nonopening_stocktake_service_chain` 原闭环。隔离夹具新增专用有效区域库位、
保管绑定及零余额账户，不能复用仍持有冻结的 `difference_peer_location`。
用真实 API role、真实时钟依次创建/启动、初盘计数、生成差异、区域复核要求复盘、
开复盘、复盘计数，再经复盘差异/复核进入第三轮。每步独立提交，用正式服务生成事实，
不手填 round/case/audit，不为清理直接解冻。

逐轮原 trace 应分别确认各自 completion；新轮计数前 `not_observed` 不影响旧轮证明。
每次 GET 对比库存/ledger cursor、审计、Outbox、状态事件、计数和提交事实不变，
并确认事务仍由调用方结束。另增实际 PG principal + API-role Session 的路由测试，
不能把直接服务调用称作 HTTP 验收。单范围多轮不替代多范围非封轮、附件竞争或换执行人覆盖。

随后仅将两端 `submit_initial_count` / `submit_recount_count` 分流到独立持久恢复模块：

- Web 入口为 `frontend/src/pages/FormalStocktakes.tsx` 的 `submitCountWithEvidence`；
  小程序为 `miniprogram/pages/formal-operational-stocktake-detail/index.js` 的 `submitCount`。
  保留附件 claims、数量与扫码校验，不把所有命令的内存 registry 整体持久化。
- 借鉴 opening store 的写后回读、精确清理和任务级互斥，但使用独立 storage/lock namespace、
  kind 和验证器。仅增加版本、kind、轮次编号等非敏感结构字段，不保存原写意图。
- 当前日常 adapter 使用普通传输，401 可经认证刷新再发原请求；count 必须改走
  `apiNoReplay` / `postNoReplay`，恢复所需 GET 也不触发认证刷新 POST。
- 日常详情使用 `rounds[]` / `visible_scope_completions[]`，不得套用 opening 当前轮投影。
  核验历史轮、范围、completion、操作者、时间和封轮关系；不要求旧 count action 仍可用。
- 现有 `run` 的泛用非 uncertain 清理及 `retryPending` 原 POST 通道不拥有持久哨兵清理权。
  count 未知结果只读核验；不得因当前版本/allowed_actions、刷新或普通 4xx 清除屏障。
- 验收覆盖响应丢失后重启（最多一次 POST）、坐标/操作串证、后续轮及终态、身份变化、
  页面失效与迟到响应、坏存储/清理失败、同任务竞争及严格落盘字段白名单。
  Web 原生 Web Locks 与小程序同服务上下文租约不等同于跨设备锁；真机 UAT 仍须独立进行。
