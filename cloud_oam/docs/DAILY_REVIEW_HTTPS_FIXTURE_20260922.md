# 日终审核 HTTPS 浏览器测试夹具

本批提供可复用的本机合成测试入口 `backend/tests/daily_review_browser_fixture.py`，用于补齐当前浏览器上传、部分解释、总部退回、补证和独立批准的验收。**本夹具先完成40项边界测试；随后完成实际HTTPS API联验，见[独立联验记录](DAILY_REVIEW_HTTPS_API_20260922.md)。浏览器信任及完整浏览器链仍未通过。**

## 夹具约束

- 只创建新的自有 PostgreSQL 16 实例，使用 Unix socket，不接收外部 DSN，不恢复历史数据库；空库迁移到当前 HEAD，再复用正式 `pg16_daily_review_fixture.prepare` 创建三份有差异的合成截止档案。
- 正式角色、权限、会话、日终审核、文件 intent/complete/download 路由及数据库约束均保留。身份域只挂载原始 `GET /auth/me` 与 `GET /access/context`，不会把短信/微信、密码、refresh/logout 或权限变更路由带进测试入口。合成 Cookie 入口只设置随机生成的测试身份，不提供真实短信或微信登录证明。
- 应用使用 `https://127.0.0.1:<随机端口>`，对象使用 `https://localhost:<相同端口>`；Host 严格隔离。不同端口不能隔离 Cookie，因此必须使用这两个不同主机名。
- 测试对象真实接收 PUT 字节，校验长度、SHA-256、签名请求头并禁止覆盖；文件完成时通过保留证书/主机名验证的 HTTPS HEAD 精确回读，下载通过 GET 返回实际字节。没有 `FakeStorage.materialize` 直接制造存在事实。
- 对象端拒绝 Cookie、Authorization、Referer；CORS 仅接受精确应用来源。短期签名能力、合成 JWT、数据库密码和上传正文仅存内存，不写日志。文件状态、事件、hash 和对象计数可以作为安全证据保存。
- 单对象 8 MiB、总对象 32 MiB、最多 512 个有效签名；默认 900 秒、最多 1800 秒。请求到达时重新校验过期/方法。监听关闭后才回读事实并停止自有 PG；异常也执行关闭。
- 正式上传 HTTPS 约束没有放宽；不安装 CA、不修改系统信任、不启动新浏览器资料、不忽略证书错误。

## 当前证书缺口

只检查了仓库、`Documents/Codex`、`~/.config`、`~/.local/share` 和常规 Caddy 数据目录的证书文件名/目录元信息；没有发现可复用的本机 TLS 叶证书/私钥配对。系统 Keychain 对 `localhost`、`mkcert`、`Caddy Local Authority` 的精确证书名只读查询没有返回。这个结果不是对整台机器所有证书的穷举证明。

`mkcert`/`caddy` 不在当前 PATH。常规 Caddy 数据目录仅有 instance、last_clean 和 locks；没有为调查读取生产私钥。证书缺口保留，不能用自签证书警告页、HTTP 签名地址或浏览器忽略验证开关替代。

启动参数只能指向 `cloud_oam/artifacts/` 下明确用于测试的现成证书和私钥。公钥证书必须是 `CA=false` 叶证书，SAN 恰为 DNS `localhost` 与 IP `127.0.0.1`，且至少覆盖完整测试时长；私钥必须禁止组/其他用户访问。仅元数据通过不代表信任通过：夹具还要求 Python 默认 trust store 的 HTTPS 健康检查通过；实际原 Edge 的信任必须在浏览器联验时单独确认，证书可能同时需要被不同验证器信任。

调查后，主任务已生成仅供测试的叶证书 `artifacts/daily-review-https-test-certificate-20260922/localhost-test.pem` 与 `.key`。主任务未安装系统信任。随后仅在实际API联验的测试进程中显式信任准确叶证书，并由TLS服务加载测试私钥；私钥没有输出或复制。该联验不代表浏览器信任，见独立联验记录。

## 证书就绪后的执行方式

先冻结当前完整候选并完成公开/私有前端构建。使用一个不存在的新证据目录；禁止复用旧 `artifacts/daily-review-browser-*` 数据目录。

从 `cloud_oam` 执行（下列 TLS 路径只是参数示意，不代表文件已经存在）：

```sh
PYTHONPATH=backend .venv/bin/python backend/tests/daily_review_browser_fixture.py \
  --artifact-dir artifacts/daily-review-https-browser-<独立轮次> \
  --certificate artifacts/test-tls/localhost.crt \
  --private-key artifacts/test-tls/localhost.key \
  --lifetime-seconds 900
```

`ready.json` 只有在当前 API 角色验证、数据准备、TLS 健康检查通过后生成。它只含入口、合成对象 ID 和安全摘要，不能作为业务完成证明。合成身份入口为 `<origin>/__fixture/identity/region`、`hq` 和 `other`。

实际浏览器验收建议使用同一份截止档案：

1. 区域开启档案，在页面上传真实测试文件，确认 `available`；只解释第一项差异。
2. 独立总部身份只能退回已解释项，不能批准未完整解释的档案。退回第一项。
3. 区域上传补证、重新解释第一项，再解释剩余差异。
4. 独立总部读取私有证据并批准；当前状态为已审核，原始对账差异仍存在。预期该档案 6 条审核事件，版本 6，不能生成库存流水。
5. 外区身份不能读取档案或下载证据；实际对象请求不得有应用认证头、Cookie、Referrer。继续补两个标签页、断线恢复、权限变化及宽窄屏检查。

在本轮目录创建 `STOP` 文件或等待时限，夹具会关闭 listener 并回读所有表摘要、审核事件和对象摘要，验证库存/截止事实不变后停止 PG。必须读最终 `result.json` 的 `serverStopped`、`clusterStopped`、`inputHashesUnchanged` 及事实结果；`ready.json` 和手工 STOP 文件都不代表成功。

当前夹具不自动判断浏览器流程通过，`browserWorkflowVerified` 与 `browserTrustVerified` 始终为 false；实际观察另记独立证据。它也不提供真实 OSS、短信、生产 KMS/启动、GitHub gate、上线 UAT 或三天对账的完成证明。

## 已验证的范围

命令：`PYTHONPATH=backend .venv/bin/python -m pytest -q backend/tests/test_daily_review_browser_fixture.py`

结果：40 passed，1 条既有 Starlette/anyio deprecation warning，5.91 秒。覆盖签名/到期/方法、字节与摘要、覆盖拒绝、对象容量、CORS/凭据隔离、TLS 文件路径/SAN/有效期/权限/错配、错误脱敏、输入集合新增/修改/删除，原身份路由完整依赖与排除所有认证/权限写入口，以及正常/异常 listener 生命周期和 ready 前未能停止时不得误报清理完成。生命周期用测试替身，没有真实网络监听；HEAD 边界测试通过 TestClient，不代表实际 TLS 握手。

CLI `--help` 已退出 0；缺失证书预检已退出 1，未创建证据目录或启动服务。最初检查发现 formal_services 包的导入会提前加载默认生产配置，已延迟到明确选择本机测试设置之后。`git diff --check` 通过。

最终原始日志位于 `artifacts/daily-review-https-fixture-20260922/focused-final.log`；安全摘要为同目录 `verification.json`。第一次37项记录保留在摘要历史字段，最终40项以当前源码及日志hash绑定。
