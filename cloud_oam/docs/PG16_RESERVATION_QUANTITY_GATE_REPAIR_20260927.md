# PG16 预留候选数量显示门禁修正

2026-09-27；父提交 `d9393928a363537668995fdd69bc86f10362c198`。

## 问题与修正

准确父提交的 PostgreSQL 16 run `36287869297`、runtime job `108532029466`
在 `pg16_reservation_gate.py:269` 失败。候选接口按当前物料的 quantity_scale
输出数量；门禁却无条件要求字符串 `2.000`。SN 物料 scale 为 0，真实响应为
`reservable_qty="2"`，已有候选服务测试也要求整数显示。

本次只改门禁：同时断言数量型 scale=3、数量 `2.000`；SN 型 scale=0、数量 `2`。
保持准确分配 ID、SN 集合和后续全部业务数量断言。不改变 API、数据库、精度策略、
库存数量、权限、并发保护或故障回滚行为。

## 独立验证

复用独立 `stocktake-quantity-validation/oam` 工作树，不复制报损候选。
逐文件 hash 和可执行位核对：父提交的其他 1,747 个文件完全相同，唯一代码增量是
`backend/tests/pg16_reservation_gate.py`。工作树原有四个盘点修复文件保留。

专用入口只创建新的 PG16.15 私有 Unix socket 实例，完整迁移到父提交的 0141。
真实 API 建立需求及审批/外部证据验证，数量型和 SN 库位分别完成期初，再由原子
入口生成合成入库。随后运行原 `assert_reservation_gate`；仅连接路由替换为这个
新实例，没有替换授权、库存服务、时间、HTTP 响应或断言。

| 检查 | 结果 | 证据 |
| --- | --- | --- |
| 原门禁复现 | SN 响应 scale=0、数量 `2`，原第 269 行失败；句柄 93302 退出 1 | `before-v2.log`；实例 `run-l3q8rgbz` |
| 修正后原门禁全段 | 数量/SN、越权与过量拒绝、注入审计失败后的完整回滚、原键回放、并发竞争、余额/SN/不可变事实核对均通过；句柄 3885 退出 0 | `after.log`；实例 `run-gc8ip9f1` |
| 候选/预留服务回归 | 64 passed；句柄 61008 退出 0 | `focused-v2.log` |
| 输入绑定 | 其他 1,747 文件与父提交完全相同；PG 前后 sourceDrift=[] | `selected-source.json` 和实例 `source-manifest.json` |

日志在独立工作树 `cloud_oam/artifacts/reservation-format-20260927/`，
实例在 `cloud_oam/artifacts/local-reservation-format-pg16/checks/`。
成功实例已核验 stopped / checks passed / serverExitCode 0。
首个本地连接适配缺少专用 admin URL、首个聚焦命令误写模块名的失败日志均保留，
不计为通过。

这证明当前修复解决准确复现的门禁问题，不代表完整远端 CI 或正式上线通过。
远端新提交必须等待其 runtime 与三片 static 的实际终态；报损、真实供应商、
实物期初及生产验收仍是独立开放项。没有生产部署或业务写入。
