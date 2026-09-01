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
`--dry-run`。全国人员、申请或工单同步不能与 `--warehouse-code` 同时使用。工单详情
任一子接口不完整会使整次同步失败；“待接单且 OAM 尚无流程节点”仅记录为来源警告。

计划任务入口为 `edge_sync/run_scheduled_sync.sh`。它会先运行精确组件健康检查；只有
实时结果明确可用时才继续。不要因为 DNS、超时、隧道或页面错误而判断登录失效，也
不要在失败后手工调用 `--resume-only`。

生产 Mac mini 使用 `launchd` 直接调度 `edge_sync/run_launchd_sync.sh`，每 1800 秒
执行一次，不依赖 Codex 心跳。任务定义保存在 `edge_sync/cn.rsc.oam-edge-sync.plist`，
安装位置为 `~/Library/LaunchAgents/cn.rsc.oam-edge-sync.plist`。包装器只记录调度状态
并调用上述受控入口，不改变健康检查、会话、校验、outbox 或云端暂存规则。

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

生产主 API 只暴露管理员只读的 `/api/integrations/oam/edge/status`，用于查看已持久化
批次、记录数和最近有效快照；它不持有接收器密钥，因此不会声称接收器凭据是否已
配置。生产边缘服务只暴露签名批次/完成接口和健康检查，不暴露用户、业务或管理 API。

同步完成只代表“镜像已暂存并校验”，不代表 OAM 收货、RSC/个人仓入库、通知送达或
对账完成。所有这些状态必须由各自事实和证据单独推进。
