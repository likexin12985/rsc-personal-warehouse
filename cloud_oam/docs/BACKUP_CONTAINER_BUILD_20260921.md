# 8.00 正式 API 镜像与 PG16 容器检查准备

2026-09-21。正式 API Dockerfile 已实际构建，70 个依赖包及 Linux ARM64 依赖条件已核验。
镜像内依赖一致性、独立 worker CLI、API 与 worker 共享校验定义检查通过。现有 PG16
实例管理工具也已在实际 DB 镜像内通过新库创建、只读角色和正常停库检查。
本批正式源码保持 7.99，尚未运行 DB/API/worker 完整备份链路，没有提交、推送或部署。

[固定证据](../artifacts/backup-container-chain-20260921/verification.json) SHA256：
`73318ba20e461d9d56a5a14bd813c2a4f2587486be59f59b8d318e0e8ff03d7e`。

## 准确工件与验证范围

| 工件/检查 | 已核验结果 |
| --- | --- |
| API 构建 | 最终会话 `54758` 退出 0；正式 Dockerfile、requirements、共享模块及 293 个 app 文件共 297 个输入保持摘要 |
| API 镜像 | `rsc-formal-api-backup-check:20260921-800`，Python 3.12.14、OSS SDK 1.3.2，`pip check` 通过 |
| API/worker 导入 | 镜像内共享模块摘要正确，worker CLI 可独立导入；纯模块不加载应用、SQLAlchemy 或 psycopg；API 的共享类对象身份一致 |
| 依赖工件 | 70 个 wheel；64 个与 PyPI 发布摘要一致，6 个由校验后的 PyPI 源码包构建为纯 Python wheel；原 requirements 未修改 |
| PG16 容器检查 | `53069` 退出 0；原实例管理工具不变，实际 PG16.15 / Python 3.14.7，空库和只读角色验证，TCP 关闭，实例正常停止 |
| 资源状态 | 检查容器已退出，包源服务器及 VM 停止；首次失败和最终检查的两个新 PG16 实例均停止 |

API 镜像 manifest：
`sha256:edb83439b8b5283afea99aa653466d92b7aa22b3dcc04b474ceae97d6025e460`。
config：`sha256:c1991033a5a6163d635267b225f22f0e97ca5ab20c7f70335d35b60b9a00de73`。
本地 `artifacts/backup-container-chain-20260921/formal-api-image.tar` SHA256：
`d414347bf43bc40345a14f90d9d8d4e6237a00ceb5d61fd17fde1e738249000d`。
工件没有发布到镜像仓库。

DB 镜像沿用 7.85 已构建且与正式 Dockerfile 相同的工件：
`sha256:b0d210974e21e68b0b6c4b44acb7e4a005f07c416b75b7a6a900a2f41b54c311`。
检查只挂载纯 Python 测试依赖和元数据，没有在正式镜像中安装测试库。实例由原
`backend/tests/local_pg16_cluster.py` 创建并绑定准确子进程、目录、系统 ID 和私有 Unix socket。
此次为空库检查，不是 206 表/572 行的容器恢复验收。

## 构建恢复与保留的失败

- `54746`：首次从 PyPI 下载超时，构建失败，VM 已停止；日志保留于
  `first-attempt-package-timeout/`。没有因此修改业务依赖版本。
- 官方 Python 基础镜像经现有公共开发代理下载并逐层校验。构建用相同摘要的本地 OCI
  布局和仅监听 VM 回环地址的临时包源。Dockerfile 的原 `PIP_INDEX_URL` 参数指向该包源，
  正式源文件保持。[Docker 构建上下文](https://docs.docker.com/build/concepts/context/)
  与[nerdctl 构建说明](https://github.com/containerd/nerdctl/blob/main/docs/build.md)作为工具参考。
- `12103`：当前 nerdctl 将 OCI 路径后的 `@digest` 当成目录的一部分；改为已经完整校验、
  只有目标 manifest 的本地布局目录。此处不宣称 Docker Buildx 的所有参数格式均兼容 nerdctl。
- `7098`：Mac 跨平台下载遗漏 Linux ARM64 条件下的 greenlet，实际构建拒绝。随后按镜像
  Python 3.12.14、Linux/aarch64、原 extras 和版本约束校验完整依赖树，补入官方 wheel。
  下载器再次按主机条件跳过依赖的中间日志也保留；最终目标环境依赖闭包通过。
- `62767`：PG 检查夹具最初只复制 `.py`，遗漏包版本元数据，SQLAlchemy 因 psycopg 版本
  无法识别而拒绝。补齐原安装包的 METADATA/WHEEL，未伪造版本或修改实例管理工具。
  首次实例已正常停止；最终在全新实例重新验证。

所有原失败目录和准确终端结果保留。没有启动真实 OAM、通知、短信、KMS 或 OSS 操作。
导入成功不代表 API 服务、生产启动检查、备份恢复或完整发布门禁已经通过。

## 下一批直接执行

1. 核对上述源码和镜像摘要，复用已保存的 API 工件及既有 DB 工件；源码不变时无需重复下载
   或重建。沿用自有 VM，在全新的私有 Docker 引擎目录/socket 中加载，保持共享运行环境边界。
2. 用本批已验证的原实例管理工具在 DB 容器内创建新实例。保留测试依赖的版本元数据；
   镜像默认 PG 环境仅在实例管理夹具入口清理，正式备份客户端仍按正式规则设置自身参数。
   只恢复明确的合成备份，不接外部 DSN，不复启已停止的旧数据目录。
3. 运行真实 API 进程与正式 worker/入口，用显式测试身份及合成对象 transport 隔离外部服务。
   核验数据库导出、旧附件导出、worker 打包、独占发布、二次新库恢复及中断后的会话/锁清理。
   保留服务 PID/启动时间、对象字节和数据库逐表摘要；合成 OSS 要继续明确标注。
4. 之后完成真实 OSS/KMS、生产启动边界、持续归档/异地保管和 RPO/RTO 验收；B3 正式迁移、
   权限接口、真实目录和当前候选完整门禁仍待推进。

全局进度、额度停止条件和待答复事项见[续开发交接](CONTINUE_DEVELOPMENT.md)。
