# PostgreSQL 16 恢复后的 SN、余额与审计验证（2026-09-21）

在[首次本地时间点恢复演练](PITR_LOCAL_DRILL_20260921.md)基础上，本次用另一套全新
PostgreSQL **16.15** 增加非空 SN 流转、恢复后的业务重建、幂等重放和损坏备份拒绝。
全部通过，两个临时数据库均退出 0 并已停止；受检代码和生产配置未变。

## 覆盖范围

沿用全新私有 Unix socket 工厂、实际 0128 迁移、物理备份、同步落盘 WAL 和命名恢复点。
保留首次演练及其数据，未重启或复用旧集群，没有打开受保护的完整 GitHub runtime gate。
本次调用正式库存内核生成合成入账/预留/释放流水，**不代表真实申领、审批、发运或收货
业务链验收**；没有伪造这些独立业务状态或进行任何外部写入。

| 场景 | 实际验证 |
| --- | --- |
| 损坏备份 | 复制物理备份后仅在副本翻转一个数据字节；`pg_verifybackup` 以 checksum mismatch、退出 1 拒绝；原文件摘要保持，损坏副本从未启动 |
| 恢复点前 SN | 通过库存内核把 1 件 SN 入账到 available，再转入 reserved；两笔均真实提交 |
| 恢复点后 SN | 再真实提交 release：源库回到 available，库存移动从 3 条变为 4 条，审计事件从 59 条变为 60 条 |
| 指定点恢复 | 恢复库保持 reserved=1、available=0，恢复点后的释放及专用标记均不存在 |
| 完整表比对 | 206 张 public 表、560 行的排序内容摘要与恢复点前完全一致，包括空表；没有 public 序列 |
| 余额重建 | 从全部不可变 posted 移动重新计算，3 条余额与计算值一致，无负数或缺失投影 |
| SN 重建 | 对全部 4 个 SN 主档从不可变流水重建；1 条非空当前位置、最后移动与生命周期一致 |
| 审计哈希链 | 用正式只读校验器从每个非空链头逐项回溯到起点；authorization 10 条、inventory 49 条全部通过，合计等于 59 条审计事件，无孤立事件 |
| 恢复后的原请求重试 | 使用原预留命令和原幂等键，返回同一事务且 replayed=true；全部 public 表摘要再次保持不变 |
| 权限与隔离 | 恢复后 API/Edge 数据库边界检查通过，TCP 关闭、无外部连接；源库与恢复库均已停止 |

首次样本仅证明 SN 主档保留；本次已补 **本地非空 SN 流转与当前位置恢复**，并验证
事实与派生状态的关系。不能再将这项本地验证写作“只有 SN 主档”；也不能据此声称生产
SN 全生命周期、真实业务链或持续对账已经验收。

本机小样本从复制备份到恢复、数据/权限/重建核对及幂等重试耗时 **1.91 秒**，仅记录
本次实验。生产 RPO/RTO 仍未证明，附件与密钥恢复也未覆盖。

## 证据

目录：`cloud_oam/artifacts/pitr-serial-local-20260921/`。
本次运行：`checks/run-r5_5t2qo/`；原工具会话 `30486` 已终止，退出 0。

- `run_pitr_serial_drill.py` 和 `serial_restore_checks.py`：工厂编排和语义核对入口。
- `drill.log`、`result.json`、`pitr-result.json`：终端及最终证据。
- `corrupted-backup-result.json`、`corrupted-backup-verify.log`：损坏检测和原件保持。
- `target-facts.json`、`after-target-source-facts.json`、`restored-facts.json`：指定点前、
  后续源库及恢复库的完整表摘要。
- `before-target-semantics.json`、`after-target-source-semantics.json`、`restored-semantics.json`：
  SN/余额重建和逐条审计链结果。
- `restored-replay.json`：恢复后原预留请求重试的相同事务证明。
- `cluster-state.json`、`restore-state.json`：自有进程、系统标识、停止及退出码证明。

根目录以外的逐项结果位于上述 run 目录。脚本仍属于被忽略的本地验证 artifacts，未加入
候选源码；正式运维工具的评审与纳入须等当前长回归结束，不能更改正在受检的文件。

SHA256：

- 主脚本：`9a239018abad989b12d427ad062694dfe7ffc5c3a9133e6d4563bb93d8d7f40a`
- 语义核对脚本：`4bf3d03f7e1792b5ff84740f0c021570a209cf36f48da1d3923303f47a548615`
- 最终结果：`51cd4d9873fd8474c3149f7b4280e8a045e633d107d556c9335a66eb1881aec7`
- 指定点与恢复后表摘要均为：`2f1517be26effddad5189ff35e7beb0c0f7acd0b7eea35612cb38ec5911b0886`

## 仍开放的上线条件

B5 继续需要生产持续 WAL/PITR 与异地保管、恢复监控、生产规模 RPO≤5 分钟/RTO≤2 小时、
真实业务与附件/密钥一致恢复。其余远端完整门禁、真实知识目录、真实角色/设备、通知、
连续三天对账和灰度验收不因本次通过而关闭。

当前静态长回归继续沿用原会话 `26717` / PID `85216`，先取得明确终端结果再处理受检
源码；禁止因观察超时重跑。公开目录仍 pending/0，待批候选提交例外和 Edge 恢复答复
保持不变。本批未提交、推送或部署。
