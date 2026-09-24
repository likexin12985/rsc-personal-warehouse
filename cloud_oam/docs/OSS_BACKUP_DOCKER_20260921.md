# 7.87 实际 Docker Engine 中断验证

后续 [7.88 实际 Compose 配置与中断验证](OSS_BACKUP_COMPOSE_20260921.md)已完成；本页保留 Docker 专项原始边界。

2026-09-21。**Docker Engine 29.8.1 的 run/exec 共 14 场景通过**，会话 `13285` 退出 0。
所有受测容器已删除，私有引擎和自有 VM 已停止。现有服务 PID、启动时间与存活保持，
本次导出进程清理、暂存为空、旧完成包保持。正式接入、Compose 与真实 OSS 尚未验收。

[固定证据](../artifacts/oss-backup-docker-20260921/verification.json) SHA256：
`75dee1391e4041af4455937d3e017ed52f06bdb561e53920d563403d7d857ce6`。
该记录核对了 guest 原始归档中的每份结果、远端回执、二进制输出、旧包、服务身份和引擎停止记录。

## 真实运行边界

- 使用 [Docker 官方静态包](https://download.docker.com/linux/static/stable/aarch64/docker-29.8.1.tgz)，
  仅在原自有 Linux ARM64 VM 内执行；未安装到 macOS，也未连接共享引擎。
- 本次引擎使用全新 `/tmp/rsc-docker-lease.wp8yOT/daemon` data/exec 目录及私有 Unix socket，
  关闭 bridge、iptables、ip6tables、IP 转发和端口代理，无 TCP 监听。
- 复用 7.85 已构建镜像，原 manifest 为
  `sha256:b0d210974e21e68b0b6c4b44acb7e4a005f07c416b75b7a6a900a2f41b54c311`；
  导出、加载至 Docker 后精确核对 image config digest 为
  `sha256:bbecd3313f6db88f3f5a7bcc35703d52793a9508a0a2365455cda63cc7e37b6b`。
- 容器 UID/GID 70、init、只读根、无网络、64 进程和 128 MiB 内存。运行合成导出及常驻服务，
  **未启动 PostgreSQL**，未挂载生产数据、读取真实 OSS 或调用业务接口。
- 监督器、发送器与 7.86 核心代码完全一致；只调整测试运输为实际 Docker。客户端消失时，
  从独立检查通道读取远端记录，不能单凭客户端非零退出认定任务已清理。

## 退出与清理

run、exec 各 7 个唯一场景，不与 containerd 场景累加成新的业务验收数量。

| 场景 | 远端退出 | Docker 客户端退出 |
| --- | --- | --- |
| 正常 | 0 | 0 |
| EOF | 125 | 125 |
| 心跳停顿 | 124 | 124 |
| CLI SIGKILL | 125 | -9 |
| CLI SIGSTOP | 124 | 124 |
| 发送器 SIGKILL | 125 | -9 |
| 总期限 | 124 | 124 |

这次实际 Docker exec 保留了远端 124/125；7.86 nerdctl exec 返回 1 的历史结果仍准确保留。
CLI 强制结束的 Docker 场景触发 EOF；两种运输的具体返回不互相代替。

已有服务容器的 7 场景均精确比较 base PID、启动时间及非僵尸存活；wrapper、supervisor、
keeper、child、grandchild 五个本次导出 PID 均消失。合成子孙进程忽略 TERM，强制清理路径已触发。
正常输出保留完整 256 字节，其他等待场景无输出。各私有暂存均空，合成旧完成包逐字节保持。

结束时先核对没有剩余受测容器，再按引擎 PID/启动时间/命令摘要验证身份并停止，
确认 API 不可用且本次 dockerd/containerd/runc 残留为 0，最后确认 VM 为 Stopped。

## 仍需完成

1. 实际 Compose `run --rm --no-deps -T` / `exec -T` 与合并配置验证；插件准备及测试夹具在
   `artifacts/oss-backup-compose-20260921/`，以其终端结果为准。
2. 解冻后将候选正式接入，验证真实 DB/API 容器、完整备份入口、实际 OSS/KMS 和一致恢复。
3. 当前完整本地静态、准确候选 SHA 的 GitHub PG16/Client、持续对账和真实业务验收仍开放。

本批 1,364 个非 Markdown 输入保持冻结。准确回归、额度与待答复事项见
[续开发交接](CONTINUE_DEVELOPMENT.md)。B5 整项仍未关闭，未提交、推送或部署。
