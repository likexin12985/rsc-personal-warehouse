# 退回实物出库封存门禁修复证据

2026-09-27，当前源码基于 `c69e500efc449e19e3067eaa2be5e762a5caba35`。
本文记录真实 PG16 草案预演，**修复已应用，本地实际源码测试门禁已通过；准确提交 CI 和上线验收另行证明**。
完整当前状态以 [开发交接](CONTINUE_DEVELOPMENT.md) 顶部为准。

## 失败与原因

准确 c69 的远端 PG16 run `36297461298`，runtime job `108558846481` 已失败；
同一 run 的三个 static 分片成功。失败位于 `pg16_stock_return_outbound_gate.py`
中的迟到实物出库反例：数据库返回 `23514 / 0110 return request namespace conflict`，
原测试只接受 `0101 sealed return request cannot execute`。

两道延迟约束均可能正确拒绝冲突，不能把某条错误文本必定先出现当作数据库契约。
也不能仅把断言改成接受任意错误；否则另一道保护失效会被先触发的约束掩盖。
已提交的 submit/cancel 同类修复不覆盖 outbound，此处仍需独立证明。

## 最小修复与复核范围

候选仅修改 PG16 出库测试，复用既有 `_reject_at_constraint`，分别指定 0101、
0110 及 ALL 验证。0101 函数在实物出库表上的准确触发器名后缀是 **0103**。
每个方向分别证明 SQLSTATE、准确错误文本以及保存点回滚后的完整业务快照。
既有实际出库、原请求重放、部分数量预算、只读回查和审计检查保留。
没有修改业务服务、迁移、数据库权限或放宽库存保护。

候选及原文件 SHA256 绑定保存在
`artifacts/loss-candidate-release-20260927/outbound-seal-test-repair.json`，
补丁和原始失败日志同目录。草案预演时原文件未变；现已按 hash 绑定应用，实际源码检查另行记录。

## 已完成的本地证据

独立新建 Unix socket PG16 实例 `run-k9yje93j`，迁移至 `20261127_0148`。
通过真实服务创建合成期初及库存；数量、SN 均复现原错误断言，且失败后全量回滚。
候选检查以下 12 项全部通过。会话 82613 退出 0；运行角色安全在前后均通过，
源码漂移为空；实例正常停止，`checks=passed / serverExitCode=0`。

| 追踪 | 先后方向 | 单独或整体约束 | 实测拒绝文本 |
| --- | --- | --- | --- |
| quantity | seal_first | `trg_stock_operation_outbounds_seal_0103` | `0101 sealed return request cannot execute` |
| quantity | seal_first | `trg_stock_operation_outbounds_request_0110` | `0110 return request namespace conflict` |
| quantity | seal_first | `ALL` | `0110 return request namespace conflict` |
| quantity | command_first | `trg_stock_operation_seals_proof_0101` | `0101 executed return request cannot be sealed` |
| quantity | command_first | `trg_stock_operation_command_seals_request_0110` | `0110 return request namespace conflict` |
| quantity | command_first | `ALL` | `0110 return request namespace conflict` |
| serial | seal_first | `trg_stock_operation_outbounds_seal_0103` | `0101 sealed return request cannot execute` |
| serial | seal_first | `trg_stock_operation_outbounds_request_0110` | `0110 return request namespace conflict` |
| serial | seal_first | `ALL` | `0110 return request namespace conflict` |
| serial | command_first | `trg_stock_operation_seals_proof_0101` | `0101 executed return request cannot be sealed` |
| serial | command_first | `trg_stock_operation_command_seals_request_0110` | `0110 return request namespace conflict` |
| serial | command_first | `ALL` | `0110 return request namespace conflict` |

详细 checks、原失败复现和正常停库记录位于
`artifacts/local-return-outbound-rehearsal-pg16/checks/run-k9yje93j/`。
`seal_first` 表示封存先发生，迟到出库必须失败；`command_first` 表示出库先成立，
迟到封存必须失败。两种情况都不能改变此前已成立的事实。

## 应用与尚待完成

测试修复已按原/目标 hash 应用；没有修改业务服务或迁移。
原失败和候选预演均保留。第一轮实际源码静态第 1、2 分片已通过；第 0 分片完整
结束后仅有一项历史 0103 摘要断言失败。该断言以及后续确认的 damaged 接收夹具、
独立入账反例已应用最终测试修复，聚焦实际源码 27 项通过。
完整实际源码退回链路 PG16 99797 已退出 0，实例 run-rqmzn3fr 十组检查通过、
源码无漂移、安全目录前后通过且正常停止。最终第 0 分片 22058 已退出 0，
2,325 passed / 1 skipped；三片合计 7,464 passed / 3 skipped / 15 subtests。
使用最终源码清单 `source-manifest-final.json` 和最终收集器核验，再完成提交前
准确工作文件安全扫描。首轮实际源码 PG16 32729 的失败记录保持原样，
不能因后续修复或预演通过将其改为成功。

草案出库—发运—验收预演 59285 已退出 0，`run-xrzqonn6` 八组检查通过，实例
正常停止、源码无漂移。该预演不覆盖入账、其他 runtime 模块或历史保留降级检查，
不能代替实际源码验证或完整 CI；新提交后的远端结果必须绑定准确新 SHA。

本地合成数据库结果不证明供应商、正式身份、真实库存、生产部署或 UAT 已完成。
