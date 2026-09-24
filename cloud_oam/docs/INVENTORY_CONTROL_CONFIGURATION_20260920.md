# 控制来源配置的认证运维入口与验证（7.45）

接续至 7.49：已增加当前总部网页会话认证的 `--inspection-file` 只读检查，组合逐段采集与当前
有效授权，head 0114。准确入口及边界见[采集授权核验](INVENTORY_CONTROL_ADMISSION_20260920.md)。
以下为 7.45 当时记录；PC 签名交接后续实现另见 7.46 文档。

2026-09-20，接续 [7.44 版本授权](INVENTORY_CONTROL_AUTHORITY_20260920.md)。
工作树 `06f6/oam`、分支 `codex/notification-delivery-worker`，HEAD 保持 `f713999`。
本批没有提交、推送、上传、部署或实际批准生产来源范围。

## 本批交付

新增 `inventory_control_configuration.py` 和 `scripts/configure_inventory_control.py`，将已有
授权领域服务接到**受控运维命令入口**。操作者由有效 RSC 网页登录凭据解析，命令不接受管理员
ID。仍须直接 owner 连接；日常 API、edge 和 projector 不获得新表权限，也不持有此入口的 owner
数据库凭据。无新增表、角色、权限或迁移，head 保持 **0113**。

PC 系统管理页面及从页面到 owner 端的受限签名命令传递仍未实现，不能将此运维入口称为已交付
完整 PC 配置流程。后续须接页面中的真实来源/目录选择和审核证据，不能要求普通业务管理员掌握
数据库 owner 凭据，也不能在普通 API 进程中添加 owner 连接来绕过数据库隔离。

## 操作及事务边界

1. `preview` 为默认模式：重验当前网页登录会话、总部全国权限、授权版本及已验证身份；返回准确
   来源绑定、目录、审核文件摘要、原命令和授权历史的预览及 `review_sha256`，不保存授权。
2. `apply`：必须带回同一原命令、授权版本及预览摘要。文件、来源状态、历史决定或命令内容变化
   均使摘要失配。来源、区域和文件证据在执行事务内锁定；领域服务继续检查有效期、权限和父授权。
3. 决策及其审计、会话执行审计共用一个回滚边界。执行审计绑定准确决策/审计 ID、请求摘要、
   预览摘要、原会话 ID、Token 签发/到期时刻和权限版本；不保存 Token 或签名密钥。
4. 完成审计后再次核验实际时间、登录和角色有效期；等待过程中到期时整组回滚。调用方仍拥有最终
   commit，成功服务调用后执行 rollback 也不残留事实。
5. `status` 按同一原命令/幂等坐标查已记录决策及完整审计链，不盲重放。缺少执行审计的旧领域
   决策不能被补写成“曾经过登录入口执行”。重复或不匹配的执行审计会阻断。
6. 原会话后来失效时，可用同一人员的新有效网页登录回读原结果，原会话审计不改写。原授权已撤销
   时重放原请求也只返回原决定，不重新授权。

凭据校验固定 HS256 与 RSC access audience，要求完整且严格类型的 sub/sid/iat/exp；拒绝伪造、
过期、未来签发、其他用途、过长有效期和缺失声明。会话必须对应本人、网页端、有效设备及当前
版本的受保护来源证据。数据库锁后重新读取当前时间，不接受用户传入的历史时钟。

以上均不认证 OAM 采集，也不发布控制库存；响应始终保留 `projection_published=false`、
`start_ready=false`。`recorded=true` 只说明准确配置决策及执行证据已保存。

## 受控命令使用

运行主机须通过既有受控配置提供 RSC JWT 验证配置，以及两项专用参数：

- `OAM_CONTROL_CONFIGURATION_DATABASE_URL`：直接 `star_oam_migrator` 的 PostgreSQL 连接。
- `OAM_CONTROL_CONFIGURATION_DATABASE_NAME`：准确目标逻辑库名，必须与 DSN 及实际连接一致。

不回退到日常 API DSN。连接后核验 current_user/session_user、准确库名、PostgreSQL 16 及
单一迁移 head 0113，并设置有界连接、锁等待、语句和空闲事务超时。脚本不运行迁移。

原命令、预览与回执保存到已忽略的 `runtime/control-configuration/`，不进入源码仓库。
从 `cloud_oam` 目录运行：

```sh
.venv/bin/python scripts/configure_inventory_control.py --command-file runtime/control-configuration/command.json
.venv/bin/python scripts/configure_inventory_control.py --mode apply --command-file runtime/control-configuration/command.json
.venv/bin/python scripts/configure_inventory_control.py --mode status --command-file runtime/control-configuration/command.json
```

JSON 顶层只接受 `command`、`expected_authorization_version` 和 `review_sha256`。command 复用
`AuthorityCommand` 的准确 ID、版本摘要、证据文件、理由、有效期及幂等键/request_id；不得猜测。
首次 preview 可以不带 review_sha256；审核预览后，将原摘要补入原文件再 apply。预览不保证
当前业务条件一定可执行，领域层最终检查仍可能拒绝。

RSC 网页凭据用终端隐藏输入；受控进程可用 `--access-token-fd` 指定独立的已继承文件描述符，
内容以 EOF 结束。凭据不放在命令参数、JSON、环境变量或日志中，不读取 OAM/Edge 凭据。
脚本只输出预览或结果 JSON；参数错误与数据库异常不输出原始输入、DSN 或凭据。

退出码 0 表示本次操作已明确完成；2 表示失败；3 表示 commit 或结果输出的确认不完整，结果未知。
遇到 3 保存原命令、原幂等键和摘要，先用 status 精确回读。无记录也不是盲重试许可；需确认
原执行已终止并排除迟到提交，再决定是否执行。脚本本身从不自动重试。

## 当前证据

证据目录：`cloud_oam/artifacts/inventory-control-configuration-20260920/`。

| 范围 | 结果 | 实际覆盖 |
| --- | --- | --- |
| 配置入口、版本授权、准备、迁移、数据库安全和完整 head | **635 passed，42.50 秒** | SQLite 真实迁移与领域服务、真实 JWT 签名的合成会话、失败回滚/原结果恢复、head 升降级和权限对齐 |
| 部署与 PG16 工作流合同 | **31 passed，1.09 秒** | 受保护部署/门禁的静态合同 |
| PG16 本地保护入口 | **1 skipped，0.42 秒** | 动态门禁未运行，不能计作通过 |
| 脚本帮助与编译 | 通过 | `--help` 不连数据库；新增服务/脚本/测试可导入和编译 |

第一次 pytest 启动因日志目录不存在未执行，创建目录后首轮 **42 passed / 1 failed**；失败为测试
将字符串直接传入 UUID 主键回读，修正后联合 **631 passed**。随后增加会话更新恢复、角色到期
和原请求冲突检查，最终为上表 635 项。初始及中间日志均保留，不能充作最终证据。

CLI 测试实际执行 preview/apply/status 与 SQLite 服务，替换了数据库连接配置和 PG16 预检。
“提交成功后抛出回执丢失”通过真实 commit 后制造异常，随后查询到原决策，未再次追加审计。
真实 PostgreSQL 的 SQL、权限、锁竞争与超时语义仍须对应候选动态门禁验证。

新增 PG16 helper 已接在现有 0113 场景之后、保留性降级检查之前，计划验证真实 owner 连接下
的网页登录、双连接同请求收敛、唯一执行审计和会话撤销拒绝；当前只有本地导入/接线证据。
0113 既有保留守卫按 aggregate_type 覆盖执行审计，新增孤立执行审计的真实 SQLite 降级拒绝测试。

客户端和旧迁移源码保持不变，本轮不重跑客户端或全量后端，不提供真机/生产验收结论。

## 下一步与缺口

B1 继续 PC 配置页面及受限命令传递、真实范围和审核文件验收、可信采集 attestation、正规化、
独立 publisher/RLS 和原子发布/启动恢复。B3 持续对账依赖其结果；B8 真库/真机以及 N1—N5、
B2—B7 的上线缺口继续保留。

公开首页仍是“交流备件知识大全”，星星后台管理跳转 `/xx`；准确飞书原表尚未取得完整数据，
目录 pending/0 条。当前候选仍缺真实 PG16 证据；临时公开 CI 候选提交例外尚未获得答复，
继续遵守“全部证据齐全后再提交”，不使用旧 SHA 的绿色结果代替当前候选验证。
