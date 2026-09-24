# 7.83 备份超时监督器与 PostgreSQL 断连清理

后续整项入口接线已完成隔离候选验证，见[7.84 当前专项](OSS_BACKUP_JOB_DEADLINE_20260921.md)。
以下保留 7.83 当时的组件范围与缺口。

2026-09-21。新增独立 POSIX 进程组监督器，并为备份数据库连接启用断连检查；**16 项
聚焦、4 个实际 PG16 场景通过**。本批是可复核的组件候选，**尚未接入整项备份入口，
没有完成真实 Docker exec 断连验收**，不能据此关闭 B5 的整体超时/取消缺口。

候选及原始证据：`cloud_oam/artifacts/oss-backup-deadline-20260921/`。

## 实现

- `deadline_runner.py` 使用单调时钟监督一个受控备份命令；超时退出 124，TERM/INT/HUP
  分别保留取消退出码，正常命令返回原退出码，二进制 stdout 不混入监督日志。
- 专用 keeper 成为本次命令的会话/进程组 leader，导出命令及其子进程加入同一组。
  keeper 在最终终止前一直保留 PID，父进程只对这个尚未回收的组发送信号；不按名称
  查杀，也不在回收 PID 后再向可能复用的组号发送信号。
- 取消时先给本组 TERM，再在宽限期后 KILL。leader 与父进程间有独占管道；父进程被
  SIGKILL 时，keeper 通过 EOF 自行清理并结束该组。普通退出遗留的后台子进程也在
  本组清理范围；独立的无关进程在检查中始终保持运行。
- 监督器创建并只清理本次私有临时目录，向受控命令传递 TMPDIR；正式文件包/旧备份
  不作为任意清理参数。命令参数和继承环境不写入监督日志。仅支持不自行脱离进程组
  的受控命令；不存在对任意 daemon、任意第三方程序的退出保证。
- `candidate-ops/backup_database.sh` 仍先清除调用方 PGOPTIONS，再仅为备份连接设置
  `-c client_connection_check_interval=1000`。没有更改全库配置、其他角色、读写权限、
  连接地址或实际业务状态。

## 发现并修复的实际问题

首次监督器检查为 **7 passed / 8 failed**：keeper 提前结束整个组，父进程再次终止时
本机返回 EPERM。现已让 keeper 在父进程最终信号前保持存活，并由父进程在 KILL 后
完成常规暂存清理。没有把 EPERM 当作可忽略成功，也没有请求或扩大系统权限。

首次真实 PG16 专项正常导出通过，超时场景因数据库会话未在观察窗口清理而拒绝；
临时目录当时已经为空。PostgreSQL 默认关闭运行查询期间的客户端断连检查，因此仅
杀死客户端并不保证长查询立即结束；官方说明见
[PG16 客户端连接检查](https://www.postgresql.org/docs/16/runtime-config-connection.html#GUC-CLIENT-CONNECTION-CHECK-INTERVAL)。
本次仅对备份客户端启用 1 秒检查，随后重新创建实例验证。

也核对了本机安装的 PG16.15 `pg_dump.c`：pg_dump 会自行清零 statement_timeout 及
idle_in_transaction_session_timeout，因此没有把简单注入这两个参数当成整项截止时间。
SQL 语句超时本身也是逐语句计算，参考
[PG16 超时说明](https://www.postgresql.org/docs/16/runtime-config-client.html#GUC-STATEMENT-TIMEOUT)。

## 精确证据

| 范围 | 终端结果 | 证据 |
| --- | --- | --- |
| 监督器及配置最终检查 | `63214` exit 0，16 passed | `deadline-options-final.xml` / `.log` |
| 原始组件反例 | `46937` exit 1，7 passed / 8 failed | `deadline-initial.xml` / `.log` |
| 第一次真实数据库专项 | `17986` exit 1 | `deadline-native.log` / `deadline-native-failure.json` |
| 修复后的实际 PG16 | `36156` exit 0，4 场景 | `deadline-native-retry.log` / `deadline-native-result.json` |

16 项包含无效期限拒绝、正常退出码/二进制输出、超时、三种取消信号、父进程被强制
结束、遗留后台子进程，以及清除外部 PGOPTIONS 后只设置备份专属连接检查的验证。
中间 15 项重跑与最终 16 项有交集，不累加。

实际数据库使用全新私有 Unix socket PG16，先重新校验并恢复 7.79 合成 v1 包。
在两个持锁只读事务仍存在时，注入真实备份角色的 `SELECT pg_sleep(60)` 活跃长查询，
分别触发期限、取消和监督父进程消失。每次精确回读均为 **0 个备份会话、0 个相关表锁**，
客户端停止、暂存目录清空，原备份和 **206 表 / 572 行**完整事实保持。

| 场景 | 退出码 | 输出字节 | 整个检查耗时 |
| --- | --- | --- | --- |
| 正常导出 | 0 | 5,898,240 | 1.161 秒 |
| 3 秒期限 | 124 | 0 | 3.454 秒 |
| TERM 取消 | 143 | 0 | 1.434 秒 |
| 监督父进程 SIGKILL | -9 | 0 | 1.398 秒 |

失败和成功两个实例均已确认停止。原始日志、失败和自有数据目录保留，未重启旧实例。
这是本机 Unix socket 与合成数据证据，不是网络故障、生产负载或容器的 RTO 承诺。

## 下一步必须衔接

1. **整项入口未接线。** 当前 `backup-entry.sh` 仍未调用监督器。下一步需把预检、数据库、
   旧附件、worker 和发布串在同一截止时间内，并统一暂存目录所有权；不能给每一步重新
   分配完整时长，也不能把仅清理监督器 TMPDIR 误认为已清理任意外部 worker job 目录。
2. 必须明确并验证主机及数据库容器的 Python 运行时。当前 DB 镜像尚未加入监督器所需
   的解释器/安装路径；不能直接把本机虚拟环境路径写进生产 Compose。
3. Docker 客户端消失与容器内父进程消失是不同事件；需要显式的连接租约或可验证的
   取消传播，并实际检查远端进程、长查询/锁、容器停止及旧备份。当前没有 remote lease。
4. [7.82 流式与规模](OSS_BACKUP_STREAMING_20260921.md)代码未变，其 157 项、10 万目录、
   120 MiB 附件和旧格式/PG16 恢复证据保持。本批相对前序仅新增三个监督器/检查文件和
   备份脚本的连接检查，不能冒充重新验收了完整 worker 或正式 OSS。
5. 冻结结束后整体接入与门禁；真实 OSS/KMS、留存/ETag、异地备份、并发/RPO/RTO、
   B3 持续对账及 N1—N5 通知仍未关闭。

`verification.json` 固定 34 个候选文件与本批证据，受检正式 1,364 个非 Markdown 输入
保持不变。未提交、推送、部署或发送业务消息。当前完整回归、额度与所有待答复事项以
[续开发交接](CONTINUE_DEVELOPMENT.md)为准。
