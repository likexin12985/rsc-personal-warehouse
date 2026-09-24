# 日终审核实际 HTTPS API 联验

2026-09-22 使用未修改的 `daily_review_browser_fixture.py` 和全新自有 PostgreSQL 16，完成了实际 HTTPS API 的附件上传到独立总部批准链路。**这是 API 与真实本地 TLS/对象字节传输证据，不是浏览器、真实 OSS 或生产验收通过。**

## 通过范围

第二轮执行会话 `13762` 退出 0，9 个场景通过：

1. 精确测试叶证书的 SHA-256 与实际 TLS 对端证书一致；正确 `127.0.0.1` 名称通过，错误主机名证书校验被拒绝。
2. 区域、独立总部、外区三名合成身份通过原始 `/auth/me` 与 `/access/context` 读取；区域解释人和总部审核人 person ID 不同。
3. 区域开启真实截止档案，形成第 1 版。
4. 实际 PUT 67 字节初始测试文件；正式 `/complete` 经真实 HTTPS HEAD 回读后才为 available。
5. 只解释第一项差异后为第 2 版；总部只获 `request_changes`，没有 approve 提示。
6. 总部仅退回已解释的第一项，形成第 3 版。
7. 区域实际 PUT 98 字节补证，正式 `/complete` 再次执行真实 HEAD；替换第一项证据并解释剩余项，到第 5 版。
8. 总部从正式 download-intent 获得临时地址，以独立、无 Cookie 的对象连接真实 GET 补证，下载字节的 SHA-256 与上传一致。
9. 总部独立批准后为第 6 版；对账数值仍为 differences，动作提示为空；外区读档案 404、读附件下载意图 403。

数据库精确回读：6 条事件，依次为 `open → explain → request_changes → explain → explain → approve`，0 份原请求终结证明。对象实际传输为 **PUT 2 次、HEAD 2 次、GET 1 次**；没有 FakeStorage materialize 快捷路径。

212 张表的前后摘要中，203 张保持不变。仅 9 张审核、文件和审计表改变：`audit_chain_heads`、`audit_events`、`daily_review_bindings`、`daily_review_consumptions`、`daily_review_events`、`files`、`reconciliation_items`、`reconciliation_runs`、`state_transition_events`。库存及截止档案事实不变。

## TLS 和清理边界

- 测试证书为 `artifacts/daily-review-https-test-certificate-20260922/localhost-test.pem`，SHA-256：`4f1c899ba9aa5209787413655072a0244e71d98b0462a08833ad5e9ef165e724`。
- 只在本轮子进程设置 `SSL_CERT_FILE` 为这个准确叶证书、`SSL_CERT_DIR` 为新建空目录；API 客户端显式 `ssl.create_default_context(cafile=同一叶证书)`。保留 `CERT_REQUIRED` 与 hostname 校验，没有关闭验证、系统/Keychain 信任变更或浏览器证书忽略。
- 应用在随机 `127.0.0.1` 端口，对象使用同端口 `localhost` 主机；应用 Cookie 仅在内存，主机隔离。签名 URL、JWT、Cookie 值没有打印或写入结果/日志；安全扫描结果已入 verification。
- 回读完成后 STOP 自有服务。最终 fixture 退出 0，`serverStopped=true`、`clusterStopped=true`；PG 状态文件为 stopped，`serverExitCode=0`。
- 1,109 项本轮运行输入摘要在结束时逐项重查一致。工作树非 Markdown 源码未因本轮测试修改；独立 driver 和所有运行产物只在忽略目录。

`tlsVerifiedByPythonDefaultTrust=true` 在这里表示**本轮进程显式提供的测试叶信任**，不能解释成整台机器、原 Edge 或系统默认已信任。`browserTrustVerified`、`browserWorkflowVerified`、`realOss`、`productionStartup`、`githubReleaseGate` 仍为 false。

## 首轮保留记录

首轮 `9214` 的独立 driver 把 items.review 的实际字段 `version` 误写成 `item_version`。在真实 TLS、身份、区域开启和首次 PUT/HEAD 完成之后，driver 本地解析抛出 KeyError；没有发送解释请求。

首轮 fixture 自身退出 0，保留 1 条开启事件、1 次 PUT、1 次 HEAD；库存/截止不变，服务和 PG 均已停止，输入无漂移。该轮列为 **driver 失败、局部动作已验证**，不归为产品失败，也不冒充完整链通过。仅在新的忽略目录修复 driver 字段，第二轮使用全新实例，没有重用数据库或盲目重放旧业务命令。

## 证据与下一步

- 首轮：`artifacts/daily-review-https-api-20260922/`。
- 完整通过轮：`artifacts/daily-review-https-api-20260922-attempt2/`，含 `api-result.json`、`driver.log`、`fixture.log`、`verification.json`、独立 driver，以及 `run-01/` 下的源摘要、事件回读、全表摘要和停止证明。
- 夹具与40项边界测试：[夹具说明](DAILY_REVIEW_HTTPS_FIXTURE_20260922.md)。

下一步仍要使用原 Edge，在已获得明确测试证书信任授权并核验该浏览器信任后，实际操作上传、解释、退回、补证、下载和批准；还需覆盖跨标签、断线恢复、当前授权变化及宽窄屏。真实 OSS、短信/微信、KMS/生产启动、GitHub gate、真实来源/映射、连续三天对账和正式 UAT 保持独立待验。
