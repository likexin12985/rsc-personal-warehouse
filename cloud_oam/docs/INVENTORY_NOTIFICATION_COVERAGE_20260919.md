# 库存事实通知覆盖与验收

本批遵循 V1.0 的 1.13、3.5、3.10：库存事实必须留下通知事件，排队、发送、送达与库存过账
分别保存。基于 `f713999` 后的未提交工作树开发，没有连接生产数据库或调用供应商。

## 新接线

`notification-expander` 每轮先以有界批次读取已过账库存 Outbox，核对交易、逐行移动及对应
库存审计的坐标，再生成 `inventory_transaction_changed`；之后才扩展为渠道投递记录。
查询不按原 Outbox 的 pending/published 判定是否已通知，而以独立通知去重键判定，因为
其他消费者的成功不能替代本消费者的完成。原 Outbox、库存、余额、SN 和审计不作修改。

| 原事实 | 当前通知覆盖 | 去重与例外 |
|---|---|---|
| `inventory.transaction.posted` | 期初、占用、释放、拣货、出库、转移、工单及其他统一过账事实 | 每个库存事务一个事件，从实际移动两端账户的保管人取得接收范围，同一人只解析一次 |
| `inventory.transaction.reversed` | 冲销形成的新库存事实 | 使用冲销事务 ID；不覆盖原交易或原通知 |
| `inventory.transaction.stocktake_difference_posted` | 非期初盘点的盘盈、盘亏、转移和状态调整 | 本批在原批次事务中新增逐笔 Outbox；与流水及审计同成同败 |
| 零差异/不调整的盘点完成 | 不制造库存变更通知 | 没有库存交易就不伪造移动或库存 Outbox；任务完成事实仍独立 |
| 个人仓入账、工单操作、退回提交/取消/出库/入账 | 原业务通知继续保留 | 仅当事件类型、业务类型、原库存事务坐标及已有收件人的人员绑定均匹配时，排除该人的重复渠道投递 |
| 发运、物流、验收等独立事实 | 保留原业务通知 | 不因库存事件存在而删除或代替这些通知 |

修正此前审计中的概括：非期初盘点批次原来没有逐笔库存 Outbox，并非仅缺少消费者。
这次同时补了源事件和消费路径。既有库存事件对应的失败或 unknown 投递继续由原投递记录处理，
不会利用新的库存通知绕过重试次数、未知结果隔离或取消状态。

接收范围来自不可变移动引用的 `stock_accounts.custodian_person_id`，不从组织、城市、名称或
当前库存位置猜测。无保管人时仍保留库存通知事件，但不编造区域负责人。共享通知内容只有交易
编号、类型、游标及冲销坐标，不包含双方账户明细、收件坐标、其他人员信息或跨 SKU 汇总数量。
用户按正式库存权限查看个人相关明细。

## 原子性、权限和并发

- 库存事务只写可靠 Outbox，不调用微信、短信或飞书。
- 消费事务使用 `inventory-change:{transaction_id}` 唯一去重键；一个消费者失败时回滚自己的
  通知写入，已提交库存保持不变。来源缺失、不唯一或内容冲突时回滚单条保存点，追加独立的
  来源隔离审计并继续其他通知；不自动修复原事实。隔离审计写入失败时仍整体回滚。
- PostgreSQL 使用按原事务 ID 派生的 `pg_try_advisory_xact_lock`；同对象已有处理者时延后，
  不同对象可以继续。锁的非阻塞与事务释放语义见
  [PostgreSQL 16 官方说明](https://www.postgresql.org/docs/16/functions-admin.html#FUNCTIONS-ADVISORY-LOCKS)。
- 继续使用 `star_oam_api`，没有新增角色、表、UPDATE Outbox 授权或 schema migration；head
  仍为 `20261018_0108`。真实受限角色能否完整执行新链路，必须由新 PG16 运行验证。
- 原业务通知去重现在拒绝同 key 对应不同业务内容；重复读取收件人数不再提前 flush 调用方
  尚未完成的 Outbox。缺失身份不生成猜测收件人，空渠道不会虚增收件人数。
- 7.35 已增加总部来源异常查看与显式复核，原隔离审计永久保留，来源内容变化继续阻断。
  复核只生成通知事实并记录结果，具体权限、幂等与 API 见
  [库存通知来源异常复核](INVENTORY_NOTIFICATION_OPERATIONS_20260920.md)。

## 验证状态

| 检查 | 本次证据 | 范围 |
|---|---|---|
| 7.33 后端全量首轮 | 4769 passed、3 skipped、1 failed，4172.24 秒，退出码 1 | 唯一失败为下述已修复的历史 ACL 测试；跳过为 2 个无 SN 数量夹具及本机 PG16；不覆盖随后 7.34 新增功能 |
| 通知、投影、盘点、0037/入账状态与 PG16 workflow 拓扑聚焦集 | 最新 85 项通过 | SQLite/服务级：双方保管人、已有通知去重、异常来源、失败回滚、零差异盘点和历史 ACL 修正；含下列 8 项故障注入与 12 项修复复测 |
| 库存过账、盘点、工单、入账及部署安全联合集 | 201 项通过 | 与上行有交集；核对原统一库存入口与调用方行为 |
| 盘点源事务故障注入复测 | 8 项通过 | SQLite 真实触发器拒绝新增 Outbox INSERT 后，余额、游标、流水、审计、冻结与任务状态一起回滚；移除夹具故障后同一请求可成功 |
| 来源隔离接续批次 7.34 | 联合 79 项及单独 worker 集成 1 项通过 | 异常前后正常通知均保留；重启跳过隔离对象；保存点回滚及外层回滚；已有发运通知仍可进入 queued；与上方历史集合有交集 |
| 总部来源复核 7.35 | 后端联合 66 项、Web 全量 68 文件/1123 项通过 | 正式权限和撤销重载、版本/内容摘要、原结果幂等、保存点/审计失败回滚及未知结果先刷新；与上方集合有交集 |
| 通知前端 | 7 项通过，TypeScript/个人仓构建通过 | 明确显示库存事实，投递状态仍为独立字段；保留既有大 bundle 提示 |
| 依赖 | `pip check` 通过 | 当前隔离 Python 3.12 环境 |
| PostgreSQL 16 专项 | 已加入正式 gate，尚未实跑 | `pg16_inventory_notification_gate.py` 使用真实 API role 和既有过账/冲销事实，检查同对象竞争、不同对象继续、消费者回滚和投递幂等 |
| 非期初盘点差异 PG16 | 已接入非零场景，尚缺新运行证据 | 复用既有三轮复盘的真实盘盈 1 件，强制要求对应 Outbox；flush 后回滚消费者、重新投影及重复扩展均纳入专项，余额、源事件、审计和盘点状态须不变 |

可复用本地命令，在 `cloud_oam/backend` 内执行：

```sh
../.venv/bin/python -m pytest -q tests/test_inventory_notifications.py \
  tests/test_notification_events.py tests/test_notification_expander.py \
  tests/test_notification_expansion.py tests/test_notification_delivery.py \
  tests/test_stocktake_safe_posting_service.py
```

PG16 专项仅从原 `test_postgresql16_release_gate.py` 的受保护入口执行；不能在本机伪造
GitHub runner 标记或将 PostgreSQL 18 的运行写成 PostgreSQL 16 通过。
2026-09-19 用 `gh repo view --json nameWithOwner,visibility,isArchived` 实时核对发现目标
`likexin12985/rsc-personal-warehouse` 为 **PUBLIC**，未归档。旧说明中的“私有仓库”不可沿用。
已请求用户确认一次仅用于验证的临时候选提交；尚未获得答复，没有创建或上传候选提交。
候选分支会公开源码，必须在知晓当前可见性的前提下确认；不自动修改仓库可见性，也不因此
放宽原 GitHub runner、一次性数据库、loopback 和新集群校验。
复核更正：全局门禁除零差异链外，已有 `_assert_nonopening_multiround_count_status` 的
非零盘盈链。因此本批复用其真实过账事实，未再创建一套合成盘点终态；历史绿色运行尚未包含
新 Outbox 和通知专项，不能沿用为本批通过证据。
该盘盈夹具属于无个人保管人维度的区域仓账户，因此专项要求保留零收件人事件，不把盘点
任务执行人自动当作收件人；个人过账/冲销夹具则必须实际生成投递行，避免零行“通过”绕过 INSERT 权限检查。

全量回归发现的 0037 历史 ACL 比较缺口已定位：0084 后新增的 `personal_inbound_status`
列权限原来未从当前清单中按版本区分。已修正测试，明确这四个后加状态列不得出现在 0037
历史 ACL 中，其余列继续精确比较；未修改历史迁移或运行权限。0037 与个人仓入账状态联合
回归 12 项及后续联合 85 项通过。全量首轮已结束，保留了修正前的这项失败；必须区分首轮与修复复测。

## 未关闭的放行项

1. 准确候选代码的 PG16 全量运行，以及真实非零盘点差异的 Outbox/通知/回滚证据。
2. 部署前检查历史交易是否缺少库存 Outbox；本次只改变后续写入，不静默回填历史或制造历史
   通知。以下只读查询可定位前 100 个待核对对象，实际修复需要独立的来源核验和迁移方案：

   ```sql
   SELECT tx.id, tx.ledger_cursor, tx.source_document_type
   FROM inventory_transactions AS tx
   WHERE NOT EXISTS (
     SELECT 1 FROM outbox_events AS event
     WHERE event.aggregate_type = 'inventory_transaction'
       AND event.aggregate_id = tx.id::text
       AND event.event_type IN ('inventory.transaction.posted', 'inventory.transaction.reversed',
                               'inventory.transaction.stocktake_difference_posted')
   )
   ORDER BY tx.ledger_cursor
   LIMIT 100;
   ```

3. 没有有效身份/渠道时不发送；后续身份补齐后的受控补建收件人尚未实现，不能把零收件人
   的 expanded 状态视为已通知人员。区域异常汇总与全国重大异常也需另行定义明确事件和接收范围。
4. 7.34 已把已知来源异常隔离为独立审计事实，7.35 已补总部查看与显式复核，均通过本地聚焦；
   仍缺新 PG16 运行证据。隔离事实是持久检查点，不能删除审计或把原 Outbox
   改为 dead_letter 来恢复。大历史量下的查询耗时仍须用 PG16 EXPLAIN/压测证明，`limit`
   只限制返回行数，不能被写成已经限制了数据库扫描成本。
5. N1/N2/N3 的真实 provider、回调验签与双向机器人；本批不提供实际发送或送达证据。
6. 公开知识目录、目标容器、线上 HTTPS/登录、小程序真机及其他 B1–B7 正式基线缺口仍独立推进。

因此本批推进了 N4，尚不能将 N4 或完整上线目标标为关闭。
