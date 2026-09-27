# 报损专用附件与整单预检候选

2026-09-27；基于 `d9393928a363537668995fdd69bc86f10362c198` 的未提交工作树。
依据正式 V1.0 §1.11、§3.9、§3.10；本文只记录附件及只读预检，未完成报损业务闭环。

## 本次行为

- `20261122_0143` 在原有文件规则上追加 `stock_loss_evidence` 用途。工程师本人、
  区域负责人有效区域授权、总部有效全国授权，都必须同时具备本角色的准确业务动作。
  用户、人员、组织、认证身份、权限版本、有效期和 deny 均重新校验。
- 数据库鉴权锁住当前权限图，并以实际时钟检查；新增 deferred/always 触发器在
  COMMIT 再检查。即便 INSERT 时有效，提交时到期也整事务拒绝。
  新函数不向 API/PUBLIC 授予执行权，没有新权限种子。
- 已存在任一专用附件（含 pending）时拒绝不兼容升降级；不改标签、不删文件。
  空库允许 0143 → 0142 → 0143，函数原文、启动清单和就绪检查随版本精确继承。
- `POST /api/v1/stock-operations/loss-reports/preview` 整合原因、来源账户/数量/SN、
  当前权限、账本游标与附件清单。附件必须是本人当前权限版本、正确用途、已完成上传、
  `aliyun_oss_v2` 元数据、摘要完整且时间有效。两次交错读取发现变更则拒绝整批预检。
- 响应只带业务所需附件摘要，不返回私有存储键、签名 URL 或扫码原文。
  前端通用上传组件允许该用途；预检不创建报损单、冻结流水或业务附件绑定。

## 验证与证据边界

| 检查 | 已观察结果 | 证据 |
| --- | --- | --- |
| 后端 14 模块聚焦回归 | 577 passed，句柄 96999 退出 0 | `artifacts/loss-evidence-20260927/final-regression-v2.log` / `.xml` |
| 首轮回归 | 575 passed / 2 failed，句柄 96280 退出 1；准确失败保留 | `final-regression.log` |
| 前端通用附件 | Vitest 2 文件 / 11 项通过，TypeScript 无错误，句柄 28301 退出 0 | `web-file.log`、`web-types.log`；Shell 终态属于 tsc，Vitest 成功另由日志确认 |
| PG16 专用附件 | 3 个真实角色上传/确认/重放/私有下载；原始 SQL deny/到期/身份无效拒绝；COMMIT 到期整事务回滚；保留文件拒绝降级；空库往返；前后运行权限通过 | `pg16-v1.log`，句柄 14849 退出 0；实例 `local-stock-loss-evidence-pg16/checks/run-3awijd2w` |
| PG16 数量整单预检 | 实际期初、已完成附件与数量预检通过；事实不变、真实撤权拒绝、sourceDrift 为空 | `pg16-command-v2.log`，句柄 26570 退出 0；实例 `local-stock-loss-sources-pg16/checks/run-0l33q9lv` |
| PG16 SN 整单预检 | 实际期初及 SN/二维码/SKU 校验通过，4 类错误证明拒绝；事实不变、真实撤权拒绝、sourceDrift 为空 | 同上；实例 `run-d7x8myai` |

以上所有日志相对 `artifacts/loss-evidence-20260927/`，PG 实例相对 `artifacts/`。
每个 PG 实例各自保存 `source-manifest.json`、`checks.json`、`cluster-state.json`，已核验
只启动新建私有 Unix socket 实例、最终 stopped/checks passed/serverExitCode 0。
文件门禁先于整单预检代码完成；各结果仅对应自己的源码清单。后来安全回归只补了
`test_database_security.py` 的继承链/新增触发器断言，没有修改已验证业务逻辑和迁移。
终态、日志 SHA256 汇总：`artifacts/loss-evidence-20260927/resume-evidence.json`。

首次测试夹具中的身份撤销字段、离职枚举和另一上传者 FK 曾不符合数据库约束；修正
夹具后复测，没有放宽生产约束。上述失败日志保留，不将首轮描述成全部通过。

PG 文件存储使用内存替身；权限、身份与期初均为本地合成数据。没有真实 OSS 上传、
生产权限更改、真实库存操作或通知投递。一次门禁累计 SELECT 达 9,598 / 12,706 条，
其中包括多次查询和预检；这不是单次请求指标，也不是性能/500 用户验收。

## 必须继续完成

1. 原报损单、review、反向分别绑定真实附件 FK，不以任意 JSON file_id 代替。
2. 持久请求封存/恢复，锁定后再次验证全部来源与附件，同事务写原单、冻结流水、
   SN、审计和 outbox，数据库 COMMIT 证明整批完整性。
3. 区域核实、总部终审及五种结果、SN 报废、派生退回履约、受控反向、客户端闭环。
4. 准确版本的完整 CI、实际身份/私有 OSS/渠道、UAT、性能与发布验收。

0142 的报损写入守卫仍拒绝全部新报损单；提交根路径仍无写接口。整单预检返回
`preview_only`，不能解释为已提交、已冻结、已审批或可上线。
