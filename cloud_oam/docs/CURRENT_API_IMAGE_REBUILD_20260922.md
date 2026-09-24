# 8.24 当前正式 API 镜像重建证据

日期：2026-09-22。工作树 `06f6/oam`，分支 `codex/notification-delivery-worker`。
先重新完整复核 V1.0/AGENTS。本批仅写新的忽略工件目录和本 Markdown，未改源码、测试、配置，未提交/推送/部署。
完整静态 `6070` 的 1,472 个非 Markdown 输入保持冻结；本批构建仅为后续当前八角色容器恢复提供真实当前 API 镜像，
不把镜像构建通过解释为完整恢复、上线或外部业务验收。

## 当前结果

真实本地 Linux ARM64 containerd 构建通过，2026-09-22 15:14:04–15:14:55 UTC（中国时间 23:14），约 50.47 秒。
正式 `backend/Dockerfile` 原文未修改；只将其已有 `PIP_INDEX_URL` 参数指向自有 VM 的临时 loopback wheel 索引，
基础 `python:3.12-slim` 通过 BuildKit 映射到已校验的官方 OCI archive，构建没有下载新的外部依赖。

- 唯一新标签：`rsc-formal-api-current:20260922-ca6870-e6c7bea1e262`。
- 镜像 manifest：`sha256:f1cd472afef931f2d5b2ae04d018171cd7120203a51397b9fb2384e5f7f88db1`。
- 镜像 config：`sha256:58967d56d7292bdd51e546dd6f7b23024493a0f43cc46e50c047011ce50b0d81`。
- 镜像归档 SHA256：`73bbe9f391195efa3f9aff9fc5ba136d64a7ceb4abb87313179239592b1106ce`。
- 70 个 wheels 与当前 requirements 完全匹配并逐文件重新 hash；官方 OCI archive 及内部全部 blobs 再校验。
- 330 个构建输入（326 个 app 文件及 Dockerfile/.dockerignore/requirements/共享模块）与 6 个 worker 输入前后无变化。
- `COPY app` 按实际目录树扫描全部普通文件，只排除当前 Docker ignore 明确的 `__pycache__`、`*.pyc`、`tests`、`.pytest_cache`；
  拒绝链接，不再只选择 `.py`。本候选扫描结果暂时恰好全部为 Python 文件，不能据此恢复旧的仅 `.py` 选择规则。
- 镜像内 328 个实际 COPY 文件与当前候选的路径、内容 hash、权限 mode、字节大小全部一致。
- `pip check` 输出 `No broken requirements found.`；Python 3.12.14、OSS SDK 1.3.2；共享模块纯导入未引入 app/SQLAlchemy/psycopg，
  API/共享定义 identity 一致，当前 worker 在无网络只读容器内执行 `--help` 成功。

## 隔离与收尾

复用明确归属的 `/private/tmp/rsc-caddy-vm-25_4tngo` / `caddy-check`，启动前确认为 Stopped、2 CPU/2 GiB、
唯一 host mount 为既有 stage 且只读，SSH agent 不转发。启动后确认无活跃容器才开始构建。

所有验证容器使用 `--network none`、只读根文件系统和资源限制；公共 wheel 包源仅绑定 guest `127.0.0.1`，
无 OAM、RSC、飞书、PNVS、KMS、OSS 或生产数据库调用。未读或导出业务凭据。
最后确认无活跃容器，按精确 PID/starttime/command 身份关闭自有包源，再停止自有 VM并重新读回 Stopped。
旧镜像、旧构建和历史失败证据均保留；新镜像归档已取回并再次核对 hash。VM 独占已交还主任务，不能并发启动。

## 证据与后续边界

全部工件位于 `artifacts/api-image-current-20260922-ca6870/`：

- `api-build-result.json`、`api-build-commands.log`：实际构建/导入/导出、每步命令、最终 VM 和包源停止状态。
- `source-manifest-before.json` / `source-manifest-after.json`：完整构建与 worker 输入清单。
- `image-source-manifest.json`：镜像内实际 COPY 内容。
- `dependency-reuse.json`、`api-installed-dependencies.txt`：已校验的依赖复用和镜像内安装版本。
- `formal-api-image.tar`：本候选新镜像归档；无需复用旧 API 镜像代替当前代码。
- `build_api_image.py` / `runner-verification.json`：可核对执行器与 runner SHA256。重跑必须使用新目录和唯一标签，
  不覆盖现有成功结果。

当前 requirements SHA256：`b039ed170b427802a5b4d229304497dc0338073cf9f128a6559760b49c5b4371`；复用官方基础 manifest：`sha256:950206c37262dd86c55659797f6ee418fee30535072f65a82ed470d985f5cda5`。
本证据为 Linux ARM64 本地镜像，不证明目标服务器架构、生产镜像发布或目标环境恢复成功。
后续八角色正式容器备份/恢复必须精确使用上述新镜像 digest，并保存自身当前源码/worker/数据/权限证据。
完整静态、GitHub PG16/Client、真实供应商、身份、期初和 UAT 放行要求继续保留。
