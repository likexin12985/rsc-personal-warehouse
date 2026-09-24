# 本地 PostgreSQL 16 时间点恢复演练（2026-09-21）

后续增强演练已补本地非空 SN 流转、余额重建、审计哈希链、损坏备份拒绝和恢复后幂等
重试，见[SN 恢复增强证据](PITR_SERIAL_RESTORE_20260921.md)。下文保留首次样本的准确范围。

当前候选已取得一次真实 PostgreSQL **16.15** 的本地合成数据恢复证据：物理备份校验、
WAL 回放、指定恢复点暂停、数据核对、恢复为可写实例及权限检查均通过。
这补充 B5 的本地验证，**不关闭生产 RPO≤5 分钟、RTO≤2 小时的验收缺口**。
本次没有修改生产服务、迁移、部署、权限定义或客户端，没有提交、推送或部署。

## 演练边界与过程

1. 复核静态长回归冻结的 1,352 个非 Markdown 文件，集合及 SHA256 全部一致。
2. 使用现有 `backend/tests/local_pg16_cluster.py` 工厂创建全新数据目录和权限为 0700
   的私有 Unix socket，验证子进程 PID、数据目录、PG16 版本、系统标识和初始空库。
   关闭 TCP 监听，不接受现有 DSN、主机、端口或数据目录，不读取生产 `.env`。
3. 以独立迁移角色升级到 `20261107_0128`，执行现有 Edge 列权限配置。
4. 启动 `pg_receivewal --synchronous --no-loop`；在业务数据生成前执行
   `pg_basebackup --checkpoint=fast --wal-method=stream --manifest-checksums=SHA256`，
   并以 `pg_verifybackup` 核对备份清单和内容，退出 0。
5. 运行现有 `pg16_opening_fixture_gate.run`：实际签名发布、期初盘点、两级审核、
   正数量入账、幂等重放、独立对账与关单，另验证 SN 重复和权限拒绝。
   全部是本地合成材料、人员和业务事实，没有读取或写入外部业务系统。
6. 通过只读备份角色记录所有 public 表完整行内容的排序摘要；建立命名恢复点。
   在专用 `local_pitr_probe` schema 中另写入恢复点后的标记，确认已提交且 WAL 落盘。
7. 保留原备份，新建另一份恢复目录和私有 socket。只从本次 WAL 目录回放，到命名
   恢复点暂停；核对同一系统标识、全部表摘要和标记，再恢复为可写实例并检查权限。
8. 只停止本次创建的两个子进程，确认退出 0，保留数据库、备份、WAL 和全部日志。
   结束后再次核对 1,352 个受检文件不变。

恢复方法依据 PostgreSQL 16 官方的[物理备份](https://www.postgresql.org/docs/16/app-pgbasebackup.html)、
[WAL 流接收](https://www.postgresql.org/docs/16/app-pgreceivewal.html)及
[恢复点参数](https://www.postgresql.org/docs/16/runtime-config-wal.html#RUNTIME-CONFIG-WAL-RECOVERY-TARGET)。
本次未把既有每日 `pg_dump` 逻辑备份当作物理恢复底座，也没有修改每日备份脚本或 cron。

## 实际结果

| 核对项 | 本次结果 |
| --- | --- |
| 实际数据库 | PostgreSQL 16.15，当前 128 个迁移 |
| 物理备份完整性 | `pg_verifybackup` 通过 |
| WAL 文件 | 3 个完整 16 MiB 段；逐文件保存 SHA256 |
| 恢复边界 | 到指定点暂停；恢复点后的已提交标记被排除 |
| public 表完整内容 | 206 张表、544 行，恢复前后全部摘要一致，包括空表 |
| 库存事实 | 1 条事务、1 条移动、1 条余额，全部一致 |
| SN 事实 | 4 条主档一致；移动关联和当前位置均为 0 条 |
| 审计事实 | 57 条审计事件、4 条链头，完整行摘要一致 |
| 盘点与对账 | 6 个盘点任务，1 次独立对账和 1 个对账项，全部一致 |
| 序列 | public schema 中无序列，不能声称验证了非空序列恢复 |
| 恢复后的权限 | API 生产数据库边界与 Edge 数据库边界检查通过 |
| 临时资源 | 源库和恢复库均已停止，退出 0；对应 PID 已消失 |

本机小样本“复制备份→启动恢复→核对→恢复为可写实例→权限检查”耗时 **2.04 秒**。
WAL 切段后观测到目标段完整且 flush 位置覆盖后续提交的等待为 **0.121 秒**。
这些是本次实验计时，不是生产 RTO/RPO 承诺，未计入故障发现、远端下载、DNS/服务切换、
密钥恢复、附件恢复或真实负载。日志中探测不存在的 timeline history 文件属于恢复搜索；
数据库随后明确到达指定点、完成恢复，最终退出码均为 0。

## 证据与复现

目录：`cloud_oam/artifacts/pitr-local-20260921/`。

- `run_pitr_drill.py`：一次性验证入口，只接受当前固定二进制目录，不接受外部目标。
- `drill.log`、`result.json`：本次进程输出及最终结果索引。
- `checks/run-c8n5ahz2/`：本次全新集群、原物理备份、WAL、恢复目录及迁移/恢复日志。
- `pitr-result.json`：最终结果、源码冻结检查和两个实例的关闭确认。
- `base-facts.json`、`target-facts.json`、`restored-facts.json`：逐表计数与完整行 SHA256。
- `wal-evidence.json`、`cluster-state.json`、`restore-state.json`：WAL 和进程归属证据。

当前验证脚本保存在被忽略的本地 artifacts 下；它不是已发布运维工具，不会自动进入
Git 候选。后续如纳入仓库，应在原长回归终止后补正式工厂和失败场景评审。

在已有指定本机 PG16 二进制的环境，从仓库根目录运行以下命令会创建另一套全新演练
目录，不会复用或覆盖本次数据目录：

```sh
cloud_oam/.venv/bin/python cloud_oam/artifacts/pitr-local-20260921/run_pitr_drill.py
```

当前证据 SHA256：

- 验证脚本：`646cfb03f98aecb2b29df3195d8b5de2a18246e4f7fbe4dbe2c7a9deab648b5e`
- 最终 `pitr-result.json`：`8b2f5909e29ed7f3b8db50d8c3026ca2e26f9314cd92c1bd0a6ffc5844fd511b`
- 目标和恢复表摘要均为：`b604c46630f3c041920e11c4630d21d1d5039db6125a052c87e6e462c8b24f21`

## 仍需完成

B5 保持开放：生产部署实际采用的持续 WAL/PITR、异地保管与保留周期、监控和告警，
生产规模 RPO/RTO、非空 SN 流转与当前位置恢复、附件/密钥一致恢复，以及业务重建核对
尚无本次证据。本次使用工厂内部管理角色做物理复制；生产不能直接沿用其本地 trust
配置或高权限复制账号，需要单独最小权限设计。

当前候选完整 GitHub PG16/客户端门禁、真实公开目录、真实角色/设备、通知供应商与回调、
三天持续对账及灰度仍待完成。公开目录继续 pending/0，原候选提交例外和 Edge 恢复的
待答事项保持不变。原后端静态长回归继续等待同一会话，不能凭本次演练提交或上线。
