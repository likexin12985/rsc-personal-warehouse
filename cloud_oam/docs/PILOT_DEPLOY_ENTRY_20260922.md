# 8.18 个人仓试点部署入口修复

日期：2026-09-22。工作树仍为 `06f6/oam`，分支 `codex/notification-delivery-worker`，HEAD `f713999`；本批未提交、推送、部署或执行生产迁移。用户已将飞书知识源暂缓，公开目录保持 `pending / 0`，个人仓 `/xx` 开发继续。

## 修复内容

- 部署入口必须显式使用 `rsc-pilot-*` 项目及合法候选标签。解析后的卷/网络名称必须归属本项目，拒绝外部共享资源、固定容器名和数据库目录 bind；API/Web/DB、迁移和 KMS gate 的实际镜像必须匹配本次候选。
- API、迁移和 KMS gate 的 URL 分别绑定 `db:5432/POSTGRES_DB`、正确角色和对应数据库密码，拒绝远端库、错库、URL 参数和凭据错配；不输出秘密值。
- DB 镜像与 Edge DB 网络改为可配置且保持原默认值；试点入口导出独立名称，避免覆盖旧站镜像或加入旧站数据库网络。
- 配置预检后，只读列举并检查全部 Docker 网络；与任何其他网络的子网重叠都停止，既有同名网络必须由本项目管理且静态分配一致。检查失败或返回不完整时，在构建/迁移前停止。
- `prepare` 构建 DB/API/Web、等待数据库就绪并执行迁移，然后停下。KMS 注册表与持久化 pin 的双人登记按现有清单单独完成，脚本不自动插入或覆盖。
- `start` 检查端口和已准备的数据库，先执行只读 pin gate，再启动 API/Web；使用 `--no-deps` 避免隐式重复迁移。烟测绑定已解析 HTTPS 域名和 `/xx/`，最多三次只读检查。
- Docker 查询失败、80/443 映射到任意容器端口均停止；旧 `PILOT_ALLOW_PORT_CUTOVER=true` 不能绕过。错误输出标明阶段，保留现有容器/卷，不自动回退、重发或清理。
- 新脚本触发路径和部署测试已接入 PostgreSQL 16 工作流的静态检查部分；没有新增或改写业务迁移。

## 验证证据

最终本地聚焦 **148 passed，18.98 秒**：

```text
PYTHONPATH=backend .venv/bin/python -m pytest -q
  backend/tests/test_pilot_preflight.py
  backend/tests/test_pilot_deploy.py
  backend/tests/test_pg16_workflow_topology.py
  backend/tests/test_edge_runtime_env.py
  backend/tests/test_database_deployment_security.py
  backend/tests/test_backup_configuration.py
```

以上为同一条 pytest 命令的参数清单，工作目录 `cloud_oam`。日志保存在 `artifacts/pilot-deploy-entry-20260922/focused-final.log`。测试运行真实 shell/Python 入口，Docker 与烟测使用合成命令，验证失败后没有后续变更；不启动容器或联系供应商。独立检查覆盖错镜像/错库、IPv4/IPv6 端口、网络冲突/所有权/不完整读取、准备停点、gate 顺序和三次烟测上限。

初始开发版 shell 在变量说明中包含英文撇号，导致 macOS `sh` 解析失败，该轮为 13 failed / 24 passed；移除撇号后修复。中间集合 39 passed，随后扩展集合 89 passed；保存的中间日志为 `focused-attempt-02.log`。初始失败来自本任务终端记录，未补造原始日志。最终 `sh -n` 与 `git diff --check` 通过。

另外使用官方 Compose **5.5.1 Darwin arm64** 完成真实 `config` 解析，下载二进制与官方 release asset digest、`.sha256` 均匹配。只用合成环境/registry，`DOCKER_HOST` 指向不存在的 Unix socket，未连接引擎。解析 **5 个服务、4 个卷、3 个网络**，26 个 `checks_for` 检查全部通过，`version/config` 均退出 0 且 stderr 为空。脱敏结果为 `artifacts/pilot-compose-config-20260922/result.json`；`run --help` 额外验证了计划命令可用选项。此证据只证明真实 Compose 插值及静态预检兼容，不证明 Linux/容器启动。

仓库安全扫描通过（1,597 个候选文件，30,116,038 字节，运行工件保持忽略）。本批 13 个实现/配置/测试输入摘要及结果索引保存于 `artifacts/pilot-deploy-entry-20260922/verification.json`，`deploymentReady=false`。

## 尚未证明

- 合成 Docker 测试不证明 Linux 上实际镜像、迁移、pin、健康检查或切换成功。宿主机路由/非 Docker 监听也不在网络检查覆盖范围。
- 两阶段必须固定同一候选、配置、项目和标签；标签不得在两阶段之间重打。目标机仍需核验镜像摘要、已有卷归属、独立网络容量和最终候选 SHA。
- 真实 KMS/PNVS/私有 OSS、唯一人员映射、期初盘点、备份恢复和多角色 UAT 仍缺。完整候选静态/Client/GitHub PG16 门禁也未用本地聚焦结果替代。
- 用户暂缓知识源只改变开发优先级，不把未知来源当作空库存，也不解除严格公开发布要求。

操作顺序与参数见 [试点执行单](PILOT_RELEASE_PLAN_20260921.md)；统一状态见 [持续开发交接](CONTINUE_DEVELOPMENT.md)。

## 8.23 后续：prepare/start 候选回执绑定

上面的 8.18 数量与边界保留为历史证据。两阶段绑定已由后续 [8.23 协调器](PILOT_PREPARE_BINDING_20260922.md)
补齐代码与合成场景，当前使用条件、外置 state 权限、不可覆盖回执及不可删除静态快照以该文档为准。
该修复不把历史目标机镜像、配置预检或本地回归提升为真实部署通过。
