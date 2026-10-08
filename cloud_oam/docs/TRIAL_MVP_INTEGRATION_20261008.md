# 试点 MVP 修复候选与上线接续

记录时间：2026-10-08 17:20（Asia/Shanghai）。范围为**试点 MVP，不等同完整 V1**。

## 有限业务范围

申请/提交 → 审批 → 最小货源分配/占用 → 后台人工履约并记录发运 → 本人收货 → 个人仓入账。技术员仅显示申请、状态、本人收货和入账；区域/总部保留必要后台履约。拣货与全部后置区块按 capability/角色隐藏，保留底层状态、迁移、契约、出库依赖、不可变库存流水、幂等、审计、权限、安全、取消/关闭守卫和最小站内通知。

后续迭代：履约后供给容量/0178 分配后建计划、释放后重分配、复杂历史恢复、拒收/退回补偿、stock-return 全链、工单物料消耗/替换/冲销/旧坏件回收、报损报废/成色纠正、盘点/期初导入/冻结/复盘、人员调拨/离职交接、复杂报表/Excel/打印、短信/微信/飞书真实业务通知渠道及投递运维。真实短信身份验证仍属首发必需，不与业务通知后置混淆。

## 当前 CI 与修复

已提交/推送基线 `27ca13e10bf36165b19341a9aac9679df22e0e8c`：

- [Client gate](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37746456913)：success。
- [PG16 gate](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37746456944)：最近读取 48 success / 23 failure / 3 static_safety in_progress。这是旧候选结果，下面修复未经过该 run 验证。不得取消当前运行、降低失败断言或宣称新候选全绿。

| 失败族 | 修复与保留边界 | 尚待验证 |
| --- | --- | --- |
| 临时旧库升级 ACL 少 backup SELECT | 临时数据库建立与主库一致的 5 项默认权限，不改冻结 SQL/目录 | PG16 历史迁移 |
| 条件变更升级授权版本 | 独立计算 0167/0169 的增量策略，对每人每策略精确加一；保留原 allow/deny | 真实历史库升级 |
| 审计锁等待与身份围栏 | 区分 INSERT 即时锁与 deferred COMMIT；仍核对 SQLSTATE、准确函数、库存锁和回滚 | 控制/身份并发 |
| tuple 触发器坐标、过期迁移号、串码夹具 | 兼容当前目录坐标，绑定 HEAD，复用正式期初参数；不伪造库存 | inventory / execution / serial quality |
| 原始收货负例无申请根 | 先通过真实合成期初与履约链建立普通包裹，再在快照内做精确 key 负例并回滚 | submission / review seals |
| 合法类型化退回收货被普通申请守卫拒绝 | 追加 0179，只有已存在类型化发运的 INSERT 分支适用；普通发运混用拒绝，0105 完整收货证明仍在 COMMIT 强制 | return receipt / quality；不开放后置业务 |

0179 不新增表、不删除历史事实；锁表、直接非特权 migrator、PG16/read committed、准确前驱源与指纹、原 trigger 安装证明均为前置检查。SQLite 只验证结构迁移，不执行或替代 PG16 安全约束。迁移 catalog SHA256：`13c8d63deb452a759537b320e6f242f24e891adf035caf83e5a2998d1ed0fe02`；readiness SHA256：`bf1d05619f4920d9348a9614492b7d0657e4b0d5237e3e01efbf06500159213b`。

## 本地证据（不合并为远端通过）

- scratch UUID/default ACL：2 passed / 1 deselected；授权版本计算：2 passed。
- 0179 / supply migration / trigger coordinates：20 passed。
- database security / fixture permissions / SQLite Alembic：353 passed / 1 failed。该失败是把已知迁移号当未知的旧反例；纠正后权限文件 48 passed，不重跑其余已终态测试。
- `verify_current_head_sms_contract.py`：PASS / head 0179 / `releaseDecision=not_ready`。本地回执 `artifacts/formal-0165-integration/current-head-sms-contract-0179-20261008.json`，SHA256 `ca4b561c378e2fb1907fd727e42865212ff570bc78ba9de8a787eee7930d3243`。
- 小程序聚焦原 59 passed / 1 failed；修正新增测试初始数据后单项复测 1 passed。打包、来源绑定、篡改拒绝：8 passed。
- 公共入口校验 PASS；候选首页与新 AppID/路径在开发者工具实际回读。工具显示首页时 Errors 0 / Warnings 2；不是所有页面、手机或后端 UAT 通过。

## 小程序候选与平台

公开小程序源 `miniprogram/app.json` 不变，后置源码和契约保留。`scripts/build_trial_miniprogram.py` 构建独立试点项目：查询首页无登录表单，个人仓入口进入短信登录流程；星星保留 `rscwz.cn/xx`。页面白名单与真实 JS 依赖闭包排除后置页、测试、私有配置和凭证。

原目录 `artifacts/miniprogram-trial/20261008-candidate` 保留。新回执绑定目录 `20261008-bound-candidate` 的包内容与原目录一致：833343 bytes，SHA256 `984c6d9ec3f9c9b22093bc463e347f1dcb96dd20e42b2065d0f53b9a9e542b36`。AppID 为 `wx15b6d1a74dd92eb8`。回执包含 sourceHead、每个源输入/构建脚本 hash、包文件 hash 和独立状态，不将脏工作树包假称为纯提交产物。工具已重新核验 `login=true`、服务端口 55909。未开启获取登录票据、默认信任项目或多端端口。

```sh
.venv/bin/python scripts/build_trial_miniprogram.py --output artifacts/miniprogram-trial/20261008-bound-candidate --verify
```

当前 `apiTargetConfigured=false`；添加 `--require-api-target` 会拒绝，不能上传为可用体验版。新版后端完成 HTTPS/认证预检后，用核验的 `--api-base-url https://rscwz.cn/api --pc-origin https://rscwz.cn` 生成新目录，再校验来源、包 hash 和平台合法域名。这里是预期配置格式，不表示该域名现在运行新版 API。

用户截图已表明微信认证完成、小程序未备案；两者独立。公众平台页面访问被电脑操作工具 URL 限制阻断，未读取备案驳回原文，不绕过限制、不猜补正内容。上传开发版、提交审核、备案、正式发布必须各自取得平台回执；目前全部未完成。

## PNVS 与目标服务器只读结果

目标为用户确认的杭州轻量服务器，现有 `star-oam-api-1` 仍服务旧应用。正式路由只读回读：

- `/api/auth/login-options`：200，`sms_enabled=false`、`password_enabled=true`、`wechat_enabled=false`。
- `/api/health/live`、`/api/health/ready`：404。
- 根路径 `/health/live`、`/health/ready` 返回 HTML 200，是网页回退，不能作为 API 就绪证明。
- 旧 API 容器未配置所查 Alibaba AccessKey/STS、role ARN、OIDC token file、credentials URI、ECS metadata 提示变量；只读取有无，未输出任何值。未据此推断所有可能的凭证文件均不存在。
- 实例元数据加固探测返回 404，没有取得可用角色证据；不能仅凭 DMI 的 ECS 字样假设轻量服务器支持并已绑定 RAM 实例角色。
- PNVS 控制台此前可见套餐余量 1000、预置签名“恒创联众”、模板 100001。这些仅证明可见服务配置，不证明运行身份权限、实际发送、验证码校验或用户会话。

阿里云文档说明[默认凭证链](https://help.aliyun.com/zh/sdk/developer-reference/v2-manage-python-access-credentials)可使用不同来源；[ECS 实例角色](https://help.aliyun.com/zh/ecs/user-guide/attach-an-instance-ram-role-to-an-ecs-instance)须实际绑定，不能把服务关联角色当成应用身份。目标机尚未验证可用的受控凭证来源；没有读取旧 AccessKey、降级为硬编码密钥、发送真实短信或切换生产。

下一步核验所选运行身份与容器可达性，完成 KMS pin、PNVS 最小权限与签名模板的隔离配置。真实发送前另需准确授权号码/次数及本人验证码；不要向用户索取密码或密钥，不用测试夹具号码发送。真实发送、送达、验证、正式身份映射与会话各自留证。

## 接续规则

保持原工作树和分支，不 reset/revert/丢弃。完成当前修复审查及安全检查后集中提交，当前 CI 终态后一次正常推送取得新 SHA 结果；不以旧 SHA 通过项替代。原生 PG16 尚待复核。目标机切换、备份/恢复、真实人员与来源事实、多角色/真机 UAT 和回滚仍是独立放行条件。登录/端口已解决，不再重复要求用户操作；备案原文、运行身份和指定短信联调条件分别记录。
