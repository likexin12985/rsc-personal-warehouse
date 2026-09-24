# 试点双入口线上烟测补强

日期：2026-09-24。使用 `06f6/oam`、`codex/notification-delivery-worker`，
保留既有未提交改动；本批未提交、推送、部署、迁移或切换目标服务器现有服务。

## 问题与改动

`rscwz.cn` 的旧 Web 镜像把 `/` 与 `/xx/` 都回退到旧密码登录页。
HTTP 200、甚至正确的 HTML 标题都不足以证明公开 JS 是查询站。
`scripts/smoke_test.sh` 在原有 `/api/health`、公开 `/` 和 `/login`、
私有 `/xx/`、无密码登录选项及未认证守卫检查后，从已核验的 HTML
中提取同源单段 JS 路径，限量读取实际公开与私有构件：

- 公开 JS 不超过 1 MiB，必须含资料待更新、后台入口、截图中的
  `豫ICP备2026043964号-1` 与工信部备案系统链接，且不能含已知认证客户端路径。
- 私有 JS 不超过 2 MiB，不能是站点把缺失资源回退为 HTML 的结果。
- 路径只接受 `/assets/<单段>.js` 与 `/xx/assets/<单段>.js`；不跳转到页面给出的
  外部 URL。任何一项失败即退出非零，成功标记只在全部检查通过后输出。

此检查不登录、不发送验证码、不调用业务写接口，也不能证明真实身份、库存或
供应商链路；目标机 `start` 仍须按 `PILOT_RELEASE_PLAN_20260921.md` 的
prepare、KMS、切换与回滚顺序。

## 证据

- `sh -n scripts/smoke_test.sh` 退出 0。
- `backend/tests/test_pilot_smoke_public_entry.py` **8 passed**：正确双入口、旧首页、
  错误 `/login` 或 `/xx/`、公开脚本混入登录、缺备案号、公开/私有资源 HTML
  回退均有反例。
- 部署协调器、绑定、预检及 PG16 工作流拓扑聚焦
  `artifacts/pilot-live-route-preflight-20260924/deployment-focused.log`：
  **167 passed**。该批在私有 JS 补强前运行，协调器测试使用合成烟测入口；最终
  实际烟测脚本由其后 8 项及下述双构件联验覆盖。
- `local-built-smoke.log`：本地**真实** `frontend/dist` 与
  `dist-warehouse` 文件配合只读模拟 API，完整烟测退出 0，含
  `public_bundle_ok=true`、`private_bundle_ok=true`。
- `old-live-smoke.log` 和 `old-live-smoke-v2.log`：公网目标只读烟测均退出 1，
  准确错误为 `public_home_not_knowledge_entry`。另只读核验公网
  `/api/auth/login-options` 为 `password_enabled=true`、`sms_enabled=false`、
  `wechat_enabled=false`；旧站不符合正式无密码登录边界。
- 目标机只读核对：Docker Compose `2.40.3+ds1-0ubuntu1~24.04.1`；已有旧
  `rsc-pilot-web:f713999` 为 `amd64` 镜像，但不是当前工作树候选。
  `/opt` 三层范围只见旧 `star-oam` 和 Edge Receiver 的 `.env`（权限 0600），
  未在该范围发现独立试点配置；未读取任何凭据值。
- 目标机隔离构建 Web 预检镜像 `rsc-pilot-web:preflight-ec4a915b-20260924`：
  `amd64`、镜像 ID `sha256:9546edd80bbfeb9a520a7d6f1f1a05ebd16ad59912d2b484b05fc0f6d7c49035`，
  成功日志 `target-web-build-legacy.log`。该镜像绑定的是 Dockerfile 改动前的
  源码清单 `ec4a915b…`，仅供架构预检，不是当前最终候选。
- 目标机缺少 Buildx 组件，原 BuildKit 命令在安装依赖前失败；传统 Docker
  构建器可用。API 镜像从官方 PyPI 连续两次在 `files.pythonhosted.org` 的
  `setuptools` 下载上超时；保留 `target-api-build-legacy.log` 与
  `target-api-build-v2.log`。`backend/Dockerfile` 的 pip 超时/重试已提高，
  仍不足以使官方源在该主机完成。阿里云镜像源构建仅作为网络/架构预检，
  其依赖来源不同，不能直接提升为正式发布镜像。
- 使用阿里云镜像源的 API 隔离预检构建退出 0，镜像
  `rsc-pilot-api:preflight-73891cc9-mirror-20260924` 为 `amd64`、ID
  `sha256:01b9f3e455888e04e9432e9ac08d352a4841abaeaf3cf92cafd85763901e36f4`；
  绑定准确构建上下文 SHA-256 `73891cc9e5dd9b66d77e3017ad3522cd5e271a450e70dc85588c56fddb5d0cf2`，
  只读、无网络、非特权一次性容器 `python -m pip check` 输出
  `No broken requirements found.`。日志为 `target-api-build-mirror.log`；
  宿主原有 OAM、Edge Receiver 和 PG16 容器在构建后仍运行，未启动试点服务。
- 本次最终聚焦重跑：烟测、数据库部署安全、发布绑定与 PG16 工作流拓扑
  **92 passed**。镜像构建和依赖检查仍不等于 API 实际启动、真实数据库迁移、
  外部渠道联通或发布回滚验收。
- 本地重新建立 PostgreSQL **16.15** 空库，升至 `20261119_0140`，执行
  运行角色/报表权限、短信配置反例、期初业务预校验和合成报表 HTTP/worker 完整流；
  `artifacts/local-current-head-pg16/checks/run-bs81fmwf/checks.json` 为
  `status=passed`，`cluster-state.json` 为 `status=stopped`、`serverExitCode=0`。
  报告明示 `ciReleaseGate=false`、`syntheticStorageOnly=true`，未验证真实外部凭据。
- 目标 Web 预检镜像的 Caddy 配置在目标 Linux 中校验通过，公开和私有首页文件
  均存在；一次性检查容器已退出。公网再测仍以
  `public_home_not_knowledge_entry` 拒绝旧首页。目标机的两个旧试点源码目录
  顶层只有 `.env.example`，未见 RSC 专用运行配置；未读取凭据内容。
- API 现增加官方 PyPI 哈希锁定清单，目标镜像站的 72 项发行包均通过哈希下载，
  两项构建工具也已单独哈希锁定；最终仓库 Dockerfile 的 `amd64` 隔离镜像
  内 72 项运行版本逐项一致，当前聚焦 **95 passed**。
  见[依赖来源证据](PILOT_API_DEPENDENCY_PROVENANCE_20260924.md)。该镜像仍非
  `prepare/start` 发布回执。

## 后续门禁

准确候选仍需目标 `amd64` 镜像、独立生产配置、KMS/PNVS/私有 OSS、
真实唯一人员映射与已复核期初、备份恢复、HTTPS 双入口切换与回滚演练。
切换前当前公网公开首页有登录框，试点入口也仍是密码模式；不能把本地双构建
通过或线上旧站 HTTP 200 记作上线通过。公开目录 `pending / 0` 是用户暂缓
飞书知识源后的状态，不能冒充已提供真实查询资料。
