# 7.88 实际 Compose 配置与中断验证

2026-09-21。**实际 Compose 5.5.1 合并检查及 run/exec 14 个场景通过**，最终会话 `2150`
退出 0。所有测试容器已删除，私有 Docker 引擎和自有 VM 已停止。当时生产源码冻结；
本次运行合成服务，没有启动 PostgreSQL、API、正式 worker 或访问真实 OSS。

[固定证据](../artifacts/oss-backup-compose-20260921/verification.json) SHA256：
`6c7c1e5cbd120c46087fabda56cd6010b2c104cfb3bd6e06d6f541ddce379807`。
前序 [7.87 实际 Docker](OSS_BACKUP_DOCKER_20260921.md)的 14 场景独立保留，不重复计为生产业务验收。

## 配置与真实运行证据

- Compose 来自 [Docker 官方 v5.5.1 发行](https://github.com/docker/compose/releases/tag/v5.5.1)，
  Linux ARM64 二进制 SHA256
  `732e3a84c1a0f67256ce80bc2598a24546b10ca05f9faa97efceb1171ece2ef7`，同时匹配发布方摘要文件
  和 GitHub asset digest。未验证签名，未在 macOS 执行或安装全局插件。
- 只在新的私有 Docker Engine 29.8.1 数据目录和 Unix socket 上运行。复用已核对摘要的镜像，
  未修改原 VM 挂载、共享 containerd、网络或业务浏览器。
- 用当前 `docker-compose.yml` 的原样副本与 7.86 `compose.backup.yml` 合并，输入仅为
  合成环境值。默认配置不包含 `object-backup`；显式启用 `ops` 后，db/api/worker 的 init、
  原数据挂载、健康检查及 API 对 KMS gate 的依赖保持。
- worker 只有三个 OSS 环境字段，未配置时均为空；保留 ops profile、只读根、cap drop、
  no-new-privileges、64 进程、512 MiB、只读代码挂载与独立备份网络，未继承数据库/API 凭据。
  配置检查没有启动这些正式服务。
- 运行检查用独立 Compose 项目的合成 worker 与常驻服务，实际调用
  `run --rm --no-deps -T --user … --volume …` 和 `exec -T`。测试容器 UID/GID 70、init、只读根、
  无网络、64 进程、128 MiB；逐项核对 Docker inspect 的实际约束及 Compose 项目/服务标签。
  合成测试的网络和内存边界不替代正式 OSS 服务的出网及容量验收。

## 中断与原服务保持

两种调用方式各覆盖正常、EOF、存活信号停顿、CLI SIGKILL、CLI SIGSTOP、发送器 SIGKILL
和总期限，共 14 个唯一场景。

| 场景 | 远端退出 | Compose 客户端退出 |
| --- | --- | --- |
| 正常 | 0 | 0 |
| EOF | 125 | 125 |
| 信号停顿 | 124 | 124 |
| CLI SIGKILL | 125 | -9 |
| CLI SIGSTOP | 124 | 124 |
| 发送器 SIGKILL | 125 | -9 |
| 总期限 | 124 | 124 |

run 场景实际触发自动删除，逐个核对准确容器不存在；exec 场景确认原服务 PID、启动时间和
非僵尸存活保持，五个导出相关进程均消失。暂存为空、旧完成包逐字节保持。正常输出完整
256 字节，其他合成等待场景无输出。客户端已结束时仍通过独立通道取得远端回执。

归档逐项核对 result、receipt、inspect、base PID、输出、旧包及空暂存；最终确认私有
引擎 API 不可用、本次 runtime 残留为 0，VM 为 Stopped。监督器和发送器核心代码未改。

## 保留的失败

- 下载会话 `30506` 直连超时，改用既有 macOS 公共开发代理后 `96222` 退出 0；原日志保留。
- `22495`：默认未启用 ops 时 worker 被正确排除，夹具误认为存在；改为分别检查默认与 ops。
- `11583`：Compose 的内存字节数字段为字符串，夹具按整数比较；核对原值 `536870912`
  后按准确十进制值检查。没有放宽容量限制。
- `75239`：容器已自动删除，Docker 返回小写 `no such object`，旧夹具只接受大写提示；
  改为忽略大小写并仍绑定准确容器名。
- 三个执行失败都在下一次重试前核对私有引擎和 VM 已停止，分别保留 source、log、result
  与 guest 归档。最终 14 项完整通过，不将失败尝试中的局部通过累加。

## 正式集成后的状态

7.99 已把本页清单接入正式源码：独立 worker、同一份附件校验、分阶段入口、数据库及
旧附件导出、Compose init/挂载和 ops profile 均落地；当前配置直接通过实际 Compose 5.5.1
解析。worker 现在有固定 `PYTHONPATH=/app` 和三个 OSS 字段。首轮配置错误、修正后 20 项、
正式入口 13 场景、PG16 5 场景及源码摘要见[正式集成专项](BACKUP_FORMAL_INTEGRATION_20260921.md)。
本页上方 14 个容器场景是 7.88 原候选的历史证据，不能称为新 API/DB 镜像全链路已通过。

下一步需要正式构建工件的 DB/API/worker 容器全链路、真实 OSS/KMS、异地保管和生产恢复。
完整 9496 已在 7.98 结束，CLI 版本修复已验证，当前源码冻结结束；整合后的完整门禁仍缺。
其余范围、额度和待答复事项见[续开发交接](CONTINUE_DEVELOPMENT.md)。未提交、推送或部署。
