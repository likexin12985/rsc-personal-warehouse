# RSC 个人仓 PC 客户端

本目录遵循《RSC个人仓与物资运营扩展系统——正式生产版需求与架构设计 V1.0》。当前正式
客户端已经开放本地验收通过的身份、会话、权限上下文、省负责人配置、正式库存状态、一期需求
提报/三级审批/撤回/安全取消，以及期初和日常盘点页面。v0.9 历史页面不能替代审批、分配、占用、
出库、发货、物流签收、OAM 收货、个人仓入库、通知送达或对账同步；现有页面也不表示已经通过
生产 KMS/OSS、PostgreSQL 16、真实身份和 UAT 发布门禁。

正式首页在 `/auth/me` 与 `/access/context` 的人员、授权版本、账号/在职状态、运营角色及
`inventory/read` 权限完全一致后，才请求 `/v1/inventory/summary`。响应必须符合 V1 契约；
期初账未可信建立时仍只展示账本游标和“期初未建立”，不读取 v0.9 余额、不显示假零值，也不跨
SKU/计量单位汇总。正式需求页的撤回/安全取消按钮同时依赖最新权限快照和详情
`allowed_actions`；写入使用固定幂等坐标，并精确回读同一需求版本、修订、审批实例、逐行取消事实
及十条独立状态轴。所有 v0.9 业务 GET/写路径以及 fragment、编码分隔符、dot-segment、绝对 URL
等非规范 API 路径仍在发起网络请求前失败关闭。
正式需求业务意图注册表仍只存在于当前标签页内存。撤回/取消在 POST 前另行写入一个
`sessionStorage` 恢复哨兵，字段严格限定为版本、固定业务类型、`X-Request-ID` 和创建时间；不保存
`Idempotency-Key`、需求 ID、动作、正文、原因、联系人、地址、身份、令牌或 hash。硬刷新后先新鲜
读取 `/auth/me` 和 `/access/context`，再以原 `X-Request-ID` 查询
`/v1/material-request-lifecycle-command-status`。只有服务端返回 `confirmed`，且同一需求的版本、修订、
审批实例/尝试、当前步骤及十条独立状态轴精确回读一致，才清理哨兵并显示“已恢复”。
`not_observed`、传输不确定、身份/授权变化、哨兵损坏或存储不可用都永久保留阻断语义，不使用 TTL
自动放行，也不制造新的撤回/取消坐标；状态查询只做有限退避并允许用户再次核验原坐标。明确 4xx
拒绝可以在本地清理原哨兵，但清理本身也必须成功回读。

## 正式认证请求坐标

- 每次短信发送、短信登录、会话刷新、退出登录和管理员强制下线，在首次逻辑调用前均生成
  独立的 `X-Request-ID` 与至少 144 bit CSPRNG 熵的 `Idempotency-Key`。
- 认证传输层会为所有 `/auth` 非 GET/HEAD/OPTIONS 请求兜底生成并严格校验幂等键；固定前缀
  后只允许 36 位随机十六进制，不拼接手机号、验证码、人员、令牌、设备或会话标识。
- 同一逻辑请求在凭据刷新后的受控重放复用原请求坐标。Web 端使用固定、非敏感的站点级
  Web Locks 锁，并在 origin localStorage 保存非敏感协调哨兵：只含版本、状态、创建时间和独立
  opaque attempt id，不含令牌、手机号、用户、会话或幂等键。API 原请求前捕获 success revision；
  锁内发现 revision 已变化即复用结果，只有同 revision 才建立 pending 并生成一组请求坐标。
- BroadcastChannel 只负责快速通知，不承担互斥正确性；即使消息延迟或丢失，共享哨兵仍保证
  等待标签不生成第二个幂等键。pending、未知格式及 logged_out 均永久失败关闭，不使用 TTL
  自动放行；只有确定刷新成功才写 success revision，显式重新认证成功才以新 success revision
  替换阻断状态。成功结果
  不会永久锁定，下一次真实会话到期仍可产生一次新的受控刷新。
- 浏览器缺少 Web Locks、BroadcastChannel 或可读写 localStorage 时自动刷新失败关闭，不退化为可能误用旧
  HttpOnly Cookie 的单标签路径。刷新令牌、访问令牌和幂等键均不写入 localStorage，也不在
  BroadcastChannel 中传递。
- 退出与刷新使用同一个 Web Lock；退出先写 pending 阻断刷新，收到确定清除凭据的响应后写
  logged_out terminal 并广播，避免在途刷新晚到的 Set-Cookie 覆盖退出。断网、请求未送达或
  代理错误时保留当前登录界面并允许显式重试。非 2xx 响应
  仅在服务端返回 `X-Auth-Credentials-Cleared: true`，证明双认证 Cookie 已清除后才清空 UI；
  2xx 成功响应可正常退出。
- Web 短信登录也使用同一把锁；登录成功在锁内建立新 authenticated baseline 并广播认证终态，确定
  被拒绝时恢复原协调记录，响应不确定时保留 pending。这样 refresh 或 logout 的晚到
  Set-Cookie 不会覆盖一次更新的显式登录。其他标签收到认证终态后先清空旧用户、权限与页面
  数据，再重新读取 `/auth/me`；广播丢失时由 authenticated storage event 执行同样的隔离与重载。
- 不因超时、断网或结果不确定而盲目自动重放认证写请求。普通业务写不会被传输层误加认证
  幂等键；业务接口按各自正式状态机显式提供幂等坐标。
- 浏览器没有安全随机数能力时认证写请求失败关闭，不降级使用时间戳或 `Math.random()`。

## 本地验证

使用 Codex 工作区随附的 Node.js 运行：

```bash
node node_modules/vitest/vitest.mjs run
node node_modules/typescript/bin/tsc -b
node node_modules/vite/bin/vite.js build
```

试用或正式发布仍须完成 HTTPS、短信服务、微信身份映射、弱网、会话轮换、强制下线、越权
和服务端幂等结果回放验收。客户端的请求坐标不能单独替代服务端事务与审计证据。
