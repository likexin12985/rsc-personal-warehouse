# 7.86 实际容器存活信号与中断清理

后续 [7.87 实际 Docker Engine](OSS_BACKUP_DOCKER_20260921.md)已完成；本页保留 containerd 专项原始边界。

2026-09-21。**14 个真实 containerd 场景通过，全部受测容器已删除，自有 VM 已停止。**
新建 worker 容器和已有服务容器中的独立导出命令，均覆盖正常、EOF、信号停顿、CLI
SIGKILL、CLI SIGSTOP、发送器 SIGKILL 和总期限。尚未验证实际 Docker Engine/Compose
或真实数据库容器；未正式集成，B5 仍开放。

[固定证据](../artifacts/oss-backup-container-lease-20260921/verification.json) SHA256：
`f4417bbbd37c0995ffc6de94527751b89cc4ccd626ca5701610c3cf8d0f16e4d`。
最终会话 **28214 exit 0**。前序[镜像构建](OSS_BACKUP_RUNTIME_20260921.md)及
[整项入口候选](OSS_BACKUP_JOB_DEADLINE_20260921.md)分别保留。

## 检查了什么

- 复用已验证的 ARM64 镜像摘要
  `sha256:b0d210974e21e68b0b6c4b44acb7e4a005f07c416b75b7a6a900a2f41b54c311`，未重建镜像。
- PostgreSQL 镜像中的 Python 3.14.7 运行未改动的监督器和存活发送器，**没有启动 PostgreSQL**。
  每个场景使用新建私有 job 目录，保留一个合成旧完成包；不挂载真实数据库/业务目录。
- 容器按 `postgres` UID/GID 70、只读根、关闭网络、64 进程和 128 MiB 内存边界运行，
  使用 init。合成导出和其子进程都忽略 TERM，迫使监督器执行进程组清理。
- CLI 被停止/强制结束后，从另一条独立检查通道读取容器状态与私有证据，不能把客户端
  退出本身算作容器工作已停止。现有服务容器还精确核对原服务 PID、启动时间和非僵尸状态，5 个本次导出相关
  PID 均不存在，**不是只把僵尸进程记为“结束”**。
- 全部场景都验证私有暂存为空、旧完成包保持；正常场景的 256 字节二进制输出完整，
  其余合成等待场景无输出。容器结束后精确删除本场景容器，不按模糊名称批量清理。

本次增加的测试采证包装器只把**未改动的监督器 CLI** stderr 和退出码保存在私有 job，
并保留 stdin/stdout 原通路。它用于在客户端已经消失时读取远端结果，不是生产 worker，
不属于正式运行时代码或对外回执。

## 准确退出证据

两种执行方式各 7 场景，共 14 个唯一场景；不和前序检查或失败尝试累加。

| 场景 | 远端监督器退出 | 新建容器客户端退出 | 现有容器 exec 客户端退出 |
| --- | --- | --- | --- |
| 正常 | 0 | 0 | 0 |
| EOF | 125 | 125 | 1 |
| 信号停顿 | 124 | 124 | 1 |
| CLI SIGKILL | 124 | -9 | -9 |
| CLI SIGSTOP | 124 | 124 | 1 |
| 发送器 SIGKILL | 125 | -9 | -9 |
| 总期限 | 124 | 124 | 1 |

这里的 exec 客户端 1 是本机已安装 nerdctl 的行为；远端 124/125、相应原因和清理结果
均从独立私有证据核对。没有把任意非零退出算成成功，也没有声称 Docker 有相同行为。
现有服务容器中，每次都额外确认 base 服务保持运行，直到测试结束后的显式清理。

## 新的部署约束候选

`compose.backup.yml` 只相对 7.84 为 **db 和 api 增加 `init: true`**；object-backup 原有
init 保持。备份命令及后代应有 PID 1 回收器，官方
[Compose init 定义](https://docs.docker.com/reference/compose-file/services/#init)及
[nerdctl init 选项](https://github.com/containerd/nerdctl/blob/main/docs/command-reference.md)
说明其信号转发与子进程回收职责。

配置仍在忽略 artifacts，尚未修改正式 Compose。真实数据库/API 在该配置下的启动、
健康检查和关停仍须验证；本次用合成常驻服务测试导出隔离，没有替代实际服务验收。

## 保留的原始失败

- **12374 exit 1**：夹具使用了 nerdctl 不接受的 `--mount ...,rw` 字段，尚未启动远端
  命令；去掉该字段，采用读写默认值。`first-attempt-mount-option/` 保留原始材料。
- **54976 exit 1**：8 个场景先通过，exec EOF 的清理也已成功，但旧断言要求 CLI 返回
  125，实际为 1。`second-attempt-exec-exit-code/` 保留退出和进程消失证据。随后增加
  独立远端采证，分开检查远端与运输退出，再完整运行 14 场景。
- **13110 exit 0** 曾完成首轮 14 场景，但服务保留证据主要是容器 Running 状态；
  后续补充原主进程 PID/启动时间/命令与存活回读，并再次完整验证。该原始通过记录存于
  `third-attempt-container-state-only/`，不与最终 14 个场景重复累加。
- 最终 `guest-evidence.tar` 内逐场景 receipt/result/旧包/输出与汇总逐一核对；不存在
  仅凭外层命令 exit 0 或日志关键字宣布全部完成的情况。

## 下一步

1. 继续真实 Docker Engine 的 run/exec 与中断验收，再核对 Compose 参数。用于测试的
   官方 Linux ARM64 **Docker 29.8.1** 二进制已下载并检查 tar 路径/成员，未安装或执行。
   路径 `artifacts/oss-backup-docker-20260921/`；压缩包 SHA256：
   `667395fbffab52901b80181dfbb39ea76da2fbd7642c4fbddd24e42146b07b48`。
   来源为 [Docker 官方静态发行目录](https://download.docker.com/linux/static/stable/aarch64/)，
   HTTPS 来源已核对，本地摘要用于后续传输一致性，不冒充发布方签名证明。
2. 后续 Docker 必须在现有自有 VM 内使用全新私有 data/exec/socket 目录，禁止接共享或
   生产 daemon，禁止暴露 TCP、改原业务浏览器或启动数据库发布门禁。先确认 VM 无其他
   容器任务，再启动本次自有引擎；结束须核对引擎/容器和 VM 已停止。
3. 正式备份目录集成、当前完整 GitHub PG16/Client、真实 OSS/KMS 与恢复演练仍缺。
   B3 日终对账、真实资料/人员、N1—N5 通知、设备 UAT 和生产灰度继续开放。

完整静态会话、源码冻结、额度与待答复事项统一见[续开发交接](CONTINUE_DEVELOPMENT.md)。
本批保持 1,364 个受检非 Markdown 输入不变；未提交、推送、部署或发送业务消息。
