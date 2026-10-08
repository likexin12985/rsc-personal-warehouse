# 试点候选镜像与 PNVS 配置预检

记录时间：2026-10-08 19:27（Asia/Shanghai）。**试点 MVP，不等同完整 V1；发布状态 not_ready。**

## 来源和构建

源码基点 `8ad721686fd1f5516d0531c724005af6076147b4`，归档时另含三个 Dockerfile 的基础镜像摘要固定。不是纯净 SHA 镜像，也不包含之后的 PNVS 预检脚本及 Compose 修改。3071 个来源文件逐一核验；源归档 SHA256 `de7edab01ca30515096e02cda6b27ffdad295386aa95a9bd33a90fcecf178519`，来源清单摘要 `25c1b4ee90df849d47e0651d286b8865d4fe8856c0de308ff900b780855a983b`。

目标目录 `/opt/rsc-build-20261008-8ad7216`。没有 compose up、应用启动、数据库迁移、DNS/端口切换或真实短信。

- attempt1：旧 Docker builder 不支持 `--progress`，exit 125，保留原日志。
- attempt2：官方 PyPI 下载超过 2400 秒，TimeoutExpired，无候选镜像。超时瞬间存在临时构建容器，故该回执记录 `originalContainersUnchanged=false`；后续精确回读确认临时容器已退出，原六个业务容器 ID 不变。不能抹掉原失败回执。
- attempt3：确认终态和镜像不存在后，使用已有 `PIP_INDEX_URL` 构建参数切换至阿里云 PyPI 镜像。保留所有版本、`--require-hashes`、构建工具锁和 `--no-build-isolation`，未降级依赖校验。串行构建 DB/API/Web，限制单核、1200 MB 内存/1600 MB 含交换空间，三个构建均 exit 0，原六个业务容器 ID 不变。

镜像共同标签后缀 `build-20261008-8ad7216-mirror3`，架构 linux/amd64：

| 镜像 | 实际 image ID |
| --- | --- |
| rsc-pilot-api | `sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575` |
| rsc-pilot-web | `sha256:8a00fda73e368416a8d82d1a0ee83df47239ceb6707c96391c7499b45d3fd71e` |
| rsc-pilot-db | `sha256:a2fd756045c06ff91dd78c5df4f55cdbfa3fc93f4cb0332d7330d656cee1966f` |

这是目标机本地 image ID，未上传镜像仓库，不冒充 registry digest。Python/Node/Caddy/Postgres 基础镜像均固定到目标机实际已存在并回读的摘要。

## 镜像验证

- API：无网络、只读、非特权一次性容器中读取 666 个应用/依赖文件，全部与来源清单相等；运行锁版本逐项相等，Python 3.12.14 / x86_64。`python -m pip check` exit 0，No broken requirements found。第一次检查命令误缺 python，exit 127；单独纠正后通过，两份回执都保留。
- Web：`caddy adapt` exit 0；镜像内 Caddyfile 摘要与来源一致，公开首页为“交流备件知识大全”，私有 HTML 引用 `/xx/assets/`。实际解析配置将 `/api/*` 保持原路径代理至 `api:8000`，`/xx` 308 到 `/xx/`，公开与私有根目录分别为 `/srv/public`、`/srv/warehouse`。首次 cap-drop ALL 导致镜像内 Caddy 文件能力执行被拒；仅补 NET_BIND_SERVICE 后完成离线解析，没有开放端口/网络。格式提示不影响解析。
- 未连接真实候选 API、未取得域名 TLS/readiness 结果、未完成 prepare/start 或回滚演练。旧站 `/api/health/live`/ready 404、短信关闭的证据仍有效；禁止给小程序配置旧 API 冒充已接通。

## PNVS 预检改动

`scripts/pnvs_runtime_preflight.py` 只读取配置；显式 `--resolve-default-chain` 才在有 12 秒上限的子进程中解析 SDK 默认凭证链。输出固定状态码，SDK 输出/异常不外泄，不接受手机号，不调用发送或验证码检查。

配置先要求审核过的签名/模板/方案、SMS-only/default_chain，并拒绝 OAM_SMS 与 ALIBABA_CLOUD 环境变量直接注入 AccessKey/Secret/STS。只有 SDK 报告 ECS RAM、OIDC 或 credential URI 动态来源且返回完整临时凭证，才记录 identity resolved；仍不证明身份归属、续期、供应商权限或任何真实短信闭环。providerPermissionsVerified、credentialRefreshVerified、smsSent、pnvsRoundTripVerified、releaseReady 始终为 false。

Compose 为 API 和 kms-pin-gate 传递相同的可选 credential URI/metadata 配置提示；没有创建凭证管理器、填入 URI 或假定轻量服务器已有 ECS RAM 角色。默认凭证链的部署配置预检也拒绝 SDK 静态环境变量，避免仅清空 OAM_SMS 字段形成旁路。

聚焦命令（不重复已终态测试）：

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pilot_preflight.py backend/tests/test_pilot_deploy.py backend/tests/test_pnvs_runtime_preflight.py
```

结果：122 passed / 54.77s / exit 0。旧服务脱敏配置预检为 configuration_blocked，未执行身份解析。独立候选草稿在目标机 Compose 2.40.3 解析成功；来源路径、镜像标签、资源隔离、HTTPS 域名、SMS-only 和代理配置通过，但以下十项失败准确阻止部署：api_database_role、migration_database_binding、kms_pin_database_binding、sms_login_rate_limits、required_secrets、secret_separation、authentication_kms_coordinates、material_request_writes、private_oss、kms_registry_structure_and_active_keys。草稿不含已填入的真实密钥，不可直接部署。

## 门禁与回滚接续

旧 SHA `27ca13e...` 的 PG16 run 37746456944 已终态：48 success / 27 failure（其中 3 组静态安全和汇总门禁失败）。静态组分别为 30 failed/3796 passed/5 skipped、6 failed/4051 passed/4 skipped/12 errors、9 failed/3619 passed/7 skipped；这些是旧版结果，部分已由 8ad7216 修复，其余逐项处理。禁止关闭断言或将旧成功项升级为当前 SHA 放行。

下一步先修复新发现的历史迁移预期/隔离夹具问题并聚焦验证，集中提交后一次正常推送取得准确新 SHA 的 Client/PG16 证据。生产切换仍需受控运行身份、KMS/OSS、正式数据库/权限、真实 PNVS/人员/UAT、备份恢复与回滚。沿用 `pilot_release.py prepare/start` 及其来源回执验证；本次仅构建，不生成伪 prepare 回执，不用独立构建绕过脚本门禁。回滚前必须保存旧容器/镜像、配置摘要、备份及迁移版本；数据回滚须按已有保留事实守卫和恢复演练执行，不能直接降级/删除业务事实。

本地证据：`artifacts/build-candidate-20261008-8ad7216/remote-evidence/`，13 个文件及 evidence-index.json；索引 SHA256 `b01fb50a32f301a1fa961b27c80f6b2eb1ef1d3eecf8460a10f81cd9571e23dd`。这些操作产物保持 Git 忽略，不提交日志、env 或凭据。
