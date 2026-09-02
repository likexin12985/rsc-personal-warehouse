# OAM 本地只读边缘同步器

本目录将 OAM 白名单字段以完整快照/增量批次推送到隔离的云端接收器。它只负责
OAM 只读采集和同步证据，不负责审批、分配、出库、收货、个人仓入账或 OAM 回写。

## 强制边界

- 同步器只通过项目统一的 `work/oam_shared_session.py` 读取现有会话；禁止调用登录、
  验证码、MFA 或企业 SSO 提交接口。会话无效时停止并按全局健康检查结果处理。
- 云端只接收仓库、库存、人员、物料申请和工单的白名单字段；OAM Token、Cookie、
  验证码和原始完整响应不得离开本地。
- 每个请求使用独立 HMAC 密钥、精确来源 ID、五分钟时间窗、唯一批次号和正文 hash。
  接收器凭据与主 API JWT、主数据库账号完全分离。
- 分页总数与实际记录数不一致、企业/组织/仓库绑定不一致、业务键重复、物料或仓库
  编码缺失、批次/最终数量或 hash 不一致时整次失败，不上传或发布部分结果。
- 来源时间早于或等于当前有效快照的包会被隔离，不能覆盖当前镜像。
- 失败或中断后，已有 outbox 会移动到 `outbox/quarantine`。`--resume-only` 已停用；
  下次必须重新运行健康检查并重新读取 OAM，禁止盲目重放旧包。
- 人员快照完成不会直接创建、启用、停用用户或撤销会话；人员投影须由后续云端受控
  任务重新校验后执行。镜像库存也绝不直接变成 RSC/个人仓库存。
- 工单列表上传前强制具备稳定来源 ID、已知原始状态、执行人来源 ID、精确目标企业和可解析的
  来源更新时间；正式工单发布只复制七字段最小白名单。30 天窗口中未再次出现只表示“本次未观察到”，
  绝不自动删除、停用或关闭正式工单。

## 本地配置

以下值必须由部署负责人提供，示例占位值不能用于真实上传：

```bash
export RSC_EDGE_API_BASE='https://your-isolated-edge-receiver.example/api'
export RSC_EDGE_SOURCE_ID='exact-allowlisted-source-id'
export RSC_EDGE_EXPECTED_COMPANY_ID='exact-source-company-id'
export RSC_EDGE_EXPECTED_ORG_CODE='exact-source-org-code'
export RSC_EDGE_TARGET_COMPANY_ID='exact-target-company-id'
export RSC_EDGE_TARGET_ORG_CODE='exact-target-org-code'
export RSC_EDGE_EMPLOYEE_COMPANY_INTERNAL_ID='exact-company-internal-id'
export RSC_EDGE_SYNC_SECRET='at-least-32-random-characters'
```

接收端使用 `deployment/build_edge_runtime_env.py` 从专用数据库账号、独立同步密钥和
来源白名单生成权限为 `0600` 的 `runtime.env`。不要把同步密钥写入主 API `.env`，也
不要让接收端数据库角色访问 `users` 或 `inventory_balances`。

## 只读检查与上传

首次必须限定单仓并先 dry-run：

```bash
.venv/bin/python edge_sync/oam_edge_sync.py \
  --entity all \
  --warehouse-code EXACT_WAREHOUSE_CODE \
  --dry-run
```

核对来源范围、记录数、delta 数量和 SHA-256 后，才可在另一次明确授权的运行中去掉
`--dry-run`。全国人员、申请或工单同步不能与 `--warehouse-code` 同时使用。工单同步只读取并
上传列表中的七字段最小白名单；详情、关系、流程节点、地址和联系人既不读取也不上传，云端接收器
也会在创建快照前拒绝 `work_order_detail`、`work_order_relation` 或任何额外工单字段。

计划任务入口为 `edge_sync/run_scheduled_sync.sh`。它会先运行精确组件健康检查；只有
实时结果明确可用时才继续。每 30 分钟执行增量，并在当天首个完整成功周期执行一次全量快照；
基础范围与工单范围必须都成功后才记录当日全量完成，任一失败会在下一周期重新查询来源。
不要因为 DNS、超时、隧道或页面错误而判断登录失效，也不要在失败后手工调用 `--resume-only`。

生产 Mac mini 使用 `launchd` 直接调度 `edge_sync/run_launchd_sync.sh`，每 1800 秒
执行一次，不依赖 Codex 心跳。任务定义保存在 `edge_sync/cn.rsc.oam-edge-sync.plist`，
安装位置为 `~/Library/LaunchAgents/cn.rsc.oam-edge-sync.plist`。包装器只记录调度状态
并调用上述受控入口，不改变健康检查、会话、校验、outbox 或云端暂存规则。
`StartInterval` 只提供尽力调度，不等于独立 watchdog；进程未启动或主机离线仍必须由部署侧
监控。当前仓库中的同步状态查询可用于非生产管理员诊断，但生产主 API 尚未挂载该管理路由。

安装命令：

```bash
./cloud_oam/edge_sync/install_launchd_agent.sh
```

安装器只复制同步所需的只读代码到
`~/Library/Application Support/RSC/oam-edge-sync/runtime`，避免 macOS 后台进程读取
“文稿”目录时被隐私隔离阻断。共享 OAM 会话保存在
`~/.config/rsc-edge-sync/oam_session.json`；项目内 `work/oam_session.json` 是指向同一文件
的符号链接，不创建第二份会话。

- 本次状态：`~/.config/rsc-edge-sync/launchd-status.env`
- 同步日志：`~/Library/Logs/RSC/oam-edge-sync.log`
- `launchd` 标准输出/错误：`~/Library/Logs/RSC/oam-edge-sync-launchd.*.log`
- 新失败原因只通知一次；相同失败持续时不重复通知，恢复后通知一次。

## 云端核对

非生产管理路由提供管理员只读的 `/api/integrations/oam/edge/status`，按
`source_instance + scope_key` 分别展示最近完成快照、45 分钟新鲜度、正式工单投影 run 与未解决
冲突；任何范围缺失/过期、当前失败/冲突或未解决冲突都会失败关闭。该管理路由目前刻意不挂载到
生产主 API，待 V1.0 正式管理权限与监控出口单独评审后再开放。生产边缘服务只暴露签名批次/完成
接口和数据库边界健康检查，不暴露用户、业务或管理 API。

同步完成只代表“镜像已暂存并校验”，不代表 OAM 收货、RSC/个人仓入库、通知送达或
对账完成。云端正式工单投影器使用独立数据库角色，只在完成清单、最终数量/hash、来源时间、
企业范围及唯一 OAM employee → Person 映射全部成立时追加正式来源版本并刷新工单镜像；缺映射
进入冲突且保留旧投影。启用前还必须由迁移角色按人工复核坐标执行
`deployment/provision_oam_work_order_source.sql`，Compose 的 `sync` profile 默认关闭。
边缘接收登录身份须先由 bootstrap 管理员运行 `deployment/provision_edge_receiver_role.sql` 独立
建立，再由迁移角色运行 `deployment/create_oam_edge_staging.sql` 授予暂存最小 ACL；两步都不能由
应用启动代替。历史环境若曾暂存工单详情/关系，只能先审核
`deployment/redact_legacy_oam_work_order_details.sql` 的 dry-run 数量与哈希，再在书面批准的维护窗口
用精确确认坐标执行；v2 审计会绑定替代快照实际七字段、最终/增量哈希和批次正文。仓库中没有执行
过该不可逆清理，正式维护前仍需在 disposable PostgreSQL 16 完成 dry-run/执行/回滚演练。
审批、分配、占用、出库、发货、物流签收、OAM 收货、RSC/个人仓入库、通知送达和对账同步
仍是独立状态，工单镜像发布不会推进其中任何一项。
