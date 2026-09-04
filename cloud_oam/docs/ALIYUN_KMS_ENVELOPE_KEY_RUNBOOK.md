# 阿里云 KMS envelope-key 部署清单

本清单只覆盖 `cloud_oam` 正式认证幂等响应和需求联系人快照的应用数据密钥。它不授权访问
OAM、RSC、Workflow、飞书或生产数据库，也不替代 PostgreSQL 16、身份、短信和 UAT 发布门禁。

## 1. 不可变安全边界

- 应用配置只保存 KMS endpoint、region、KMS Key ID 和应用密钥版本，不接受 AES 明文密钥。
- API 容器只读挂载 KMS `CiphertextBlob` 注册表。`Plaintext` 不得进入 Git、`.env`、聊天、
  工单、日志、剪贴板记录或容器镜像。
- `0040` 新增不可变 `kms_data_key_pins` 账本，只保存用途、KMS Key ID、应用密钥版本、
  KMS KeyVersionId 和 `CiphertextBlob` 原始 ASCII 的 SHA-256；不保存 `CiphertextBlob` 或明文。
  API 和备份身份只有 `SELECT`，只有迁移身份可执行受控首次 `INSERT`。
- 认证幂等与需求联系人必须使用两个不同的 KMS Key ID 和两个不同的 `CiphertextBlob`。
- 运行身份使用阿里云默认凭据链，优先 ECS RAM 角色；仓库和 Compose 不配置 KMS AccessKey。
- KMS 解密失败、返回 Key ID/KeyVersionId 不一致、密钥不是 32 字节、注册表漂移或缺项时，
  正式写入失败关闭。短信请求在创建挑战、审计或调用发送方之前先完成 KMS 解密预检。

## 2. 受控生成

由两名获授权运维人员在隔离终端中分别为以下用途执行 KMS `GenerateDataKey`，数据密钥长度为
256 bit，并原样提交对应的 EncryptionContext：

| 用途 | EncryptionContext |
| --- | --- |
| `authentication_idempotency` | `application=cloud_oam`、`purpose=authentication_idempotency`、`kms_key_id=<认证 Key ID>`、`application_key_version=1` |
| `material_request_contact` | `application=cloud_oam`、`purpose=material_request_contact`、`kms_key_id=<联系人 Key ID>`、`application_key_version=1` |

只把返回的 `CiphertextBlob`、KMS Key ID、KMS KeyVersionId 写入注册表；立即按组织密钥操作规程
销毁命令内存、临时文件和任何 `Plaintext` 输出。不要把真实返回值写入示例文件。

复制 `deployment/rsc-kms-data-keys.example.json` 到 Git 工作区之外的受控绝对路径，替换全部
占位值并设置为仅部署身份可读、不可被组或其他用户写入。真实文件名不得进入 Git。

## 3. RAM 最小权限与配置

运行角色仅授予两个精确 KMS Key 的 `Decrypt` 权限，并用资源和 EncryptionContext 条件进一步
约束；不得授予创建、删除、禁用或轮换主密钥的权限。部署环境至少配置：

```text
OAM_KMS_ENDPOINT=kms.<region>.aliyuncs.com
OAM_KMS_REGION=<region>
OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_FILE=<宿主机绝对路径>
OAM_AUTH_IDEMPOTENCY_KMS_KEY_ID=<认证 Key ID>
OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION=1
```

启用正式需求写入时再配置独立联系人 Key ID 和版本。容器内固定路径为
`/run/secrets/rsc-kms-data-keys.json`，Compose 只读挂载。

## 4. `0040`/`0041` 历史切片的正式发布顺序

正式环境必须严格执行以下顺序，任何一步失败都停止，不得跳过 gate 直接启动 API：

1. 先关闭全部旧版短信写入口并等待未过期挑战自然到期，再以 `star_oam_migrator` 执行
   Alembic `upgrade head`，确认数据库到达仓库发布清单与 README 标明的当前唯一 head
   （本文修订时为 `20260904_0054`）。`0040`/`0041` 只是 KMS 与短信账本的历史最低迁移
   边界，不得把数据库停留在 `20260902_0041`。`0040` 创建空的不可变 pin 账本及最小
   ACL；随后 `0041` 对旧短信证据执行失败关闭预检并创建单 owner dispatch 账本。
   未过期歧义、已验证但无发送引用、重复引用或旧接受审计不完整时必须停止，禁止猜测回填。
2. 使用准备挂载的同一份密文注册表，通过隔离的 `kms-pin-plan` 运维服务生成计划。该服务不接入
   容器网络、不接收数据库 secret，只输出非敏感坐标、密文 SHA-256、确定性
   `manifest_sha256`，不得输出密文本身。
3. 先由 `star_oam_api` 通过批准的只读审阅查询导出既有 pins，再由两名获授权人员逐项复核
   注册表来源、用途、Key ID、应用版本、KMS KeyVersionId、SHA-256、计划总数和本次新增差集，
   并在受控变更单中共同确认同一个 `manifest_sha256`。仍被认证重放或联系人历史 revision 引用的
   旧条目必须继续保留在 registry 中；既有 pin 可以多于当前 registry，但不能被修改或删除。
4. 仅由 `star_oam_migrator` 根据已双签差集执行普通 `INSERT`。首次部署只有在只读查询证明 pin
   表为空时，才全量插入 plan 的全部 entries；轮换时按 `(purpose, application_key_version)`
   逐项比对 plan 与既有 pins 的交集，并同时要求 Key ID、KMS KeyVersionId 和 SHA-256 完全一致，
   只插入既有 pins 中不存在、但 plan 新增的 purpose+version。匹配旧行不得重插；既有 pins 中
   不在当前 plan 的已退役历史行保持不动。任一重合坐标、hash、KMS version 或双签差集不一致
   立即 `ROLLBACK` 并停止。始终禁止 `UPSERT`、`ON CONFLICT`、`UPDATE`、`DELETE` 或
   `TRUNCATE`，不能覆盖、忽略或重绑既有应用版本。
5. 使用 `star_oam_api` 运行不带 `--plan` 的 `python -m app.kms_pin_gate`。该 gate 只读核验
   注册表、不可变 pin、当前 active 坐标和全部仍需解密的持久引用；输出只能是固定的
   `ready/not ready`，失败信息不得泄露 Key ID、路径、数据库地址或 SDK 详情。
6. 只在只读 gate 成功后启动 API。启动后第一次 `/api/health/ready` 必须完成真实 KMS
   `Decrypt` 探针并返回 `200`，ALB 才能把实例加入业务流量。

在 Compose 骨架中，步骤 2 必须显式跳过依赖，避免生成计划时误启动数据库或迁移：

```bash
docker compose --profile ops run --rm --no-deps kms-pin-plan
```

将标准输出保存到 Git 工作区之外的受控变更附件，并由两人核对其 `manifest_sha256`。不得把
输出重定向到仓库，也不得把真实注册表或部署环境文件作为附件。先以 `star_oam_api` 的只读事务
查询既有 pins，按 `purpose, application_key_version, kms_key_id` 排序；两名复核人必须在变更单
中标注“首次空表全量”或“轮换仅新增”，并列出精确新增差集。步骤 4 再在组织批准的数据库客户端
中，以 `star_oam_migrator` 身份显式开启事务、复核当前身份并锁定 pin 表以串行化受控插入：

```sql
BEGIN;
DO $$
BEGIN
    IF current_user <> 'star_oam_migrator' THEN
        RAISE EXCEPTION 'unexpected provisioning role';
    END IF;
END
$$;

LOCK TABLE public.kms_data_key_pins IN SHARE ROW EXCLUSIVE MODE;

SELECT purpose, kms_key_id, application_key_version,
       kms_key_version_id, ciphertext_sha256
  FROM public.kms_data_key_pins
 ORDER BY purpose, application_key_version, kms_key_id;
-- 首次部署：结果必须为空，随后全量 INSERT plan。
-- 轮换：结果必须与双签插入前快照完全一致，随后仅 INSERT 双签新增差集。

INSERT INTO public.kms_data_key_pins (
    purpose,
    kms_key_id,
    application_key_version,
    kms_key_version_id,
    ciphertext_sha256,
    created_at
) VALUES
    ('<purpose>', '<kms_key_id>', <application_key_version>,
     '<kms_key_version_id>', '<ciphertext_sha256>', statement_timestamp())
RETURNING purpose, kms_key_id, application_key_version,
          kms_key_version_id, ciphertext_sha256;

SELECT purpose, kms_key_id, application_key_version,
       kms_key_version_id, ciphertext_sha256
  FROM public.kms_data_key_pins
 ORDER BY purpose, application_key_version, kms_key_id;
-- 到此停止；不要把 COMMIT 与上述语句整段自动执行。
```

首次空表时，`VALUES` 必须覆盖 plan 全部 entries；轮换时，`VALUES` 只能包含已双签新增差集，
既有 pins 中已存在且匹配的计划旧行必须省略，账本独有历史行不得触碰。提交前由第二人把插入前
快照、`RETURNING` 新增行和插入后完整快照重新与同一份计划逐项比对，并确认所有仍被引用旧
registry 条目都有匹配 pin，且“插入后行数 = 插入前行数 + 双签新增差集行数”；purpose+version、
Key ID、KMS version、hash、差集或该计数公式任一不一致均执行 `ROLLBACK`，全部一致才单独执行
`COMMIT`。不要在命令行参数中放数据库密码。落库后用 API 身份执行：

```bash
docker compose run --rm kms-pin-gate
```

Compose 已把 `migrate -> kms-pin-gate -> api` 配置为硬依赖链；手工跳过依赖或直接启动 API
不属于受支持发布路径。

pin 一经写入即为不可变事实。`0040` 在存在任一 pin、认证密文或需求/修订引用时拒绝降级；
任何更正都必须新增应用密钥版本或走另一个受审计迁移，不能修改历史行。

## 5. 轮换与历史保留

- 应用密钥轮换必须先新增注册表项和新的不可变 pin，再切 active version，滚动重启并重新通过
  只读 gate/readiness；禁止在同一坐标原地替换密文。同一用途的应用密钥版本在所有 KMS Key ID
  之间全局唯一：更换 Key ID 时也必须使用该用途从未使用过的新应用版本，注册表和 gate 会拒绝
  “新 Key ID 复用旧应用版本”的歧义映射。
- `auth_idempotency_operations` 当前只持久化应用密钥版本，没有持久化 KMS Key ID。因此认证
  Key ID 切换前必须停止相关写入并证明所有幂等重放窗口已经清零，并切换到从未使用过的新应用
  版本；否则必须先完成显式迁移和重加密。仅等待一个估算时长或只看当前 active version 都不能
  作为清零证明。
- 需求联系人 envelope 会在当前需求和不可变历史 revision 中保留精确 Key ID 与版本。旧的
  联系人 registry entry 和 pin 必须长期保留，直到独立审计迁移完成当前记录及全部历史 revision
  的重加密、逐条可解密校验和回滚演练。不得因 active Key ID 已切换或经过固定天数就清理。
- 认证旧 registry entry 也只能在重放引用精确归零并完成滚动发布复核后移除；既有 pin 不删除。
- 认证幂等与需求联系人必须使用两组完全不相交的 KMS Key ID。该规则覆盖 active 和所有历史
  registry/pin 条目，轮换后也不得把另一用途的旧 Key ID 复用到当前用途。

## 6. 健康检查与外层防护

- `/api/health/live` 只证明进程可响应，不访问数据库或 KMS；不得因 KMS/RDS 暂时故障而触发
  无意义的容器重启风暴。
- `/api/health/ready` 检查数据库，并以单飞 TTL 缓存的真实 `Decrypt` 探针验证全部 active 与
  持久引用坐标。成功缓存默认 60 秒、失败缓存 10 秒、并发等待与探针预算默认各 4 秒；缓存只
  保存状态和坐标指纹，不保存明文密钥或 SDK 响应。等待和探针预算的配置上限均为 4 秒；连同
  最长 2.5 秒的独立数据库检查，必须落在 Compose/ALB 的 8 秒 ready 超时内。
- 生产环境禁止 `DEBUG=sdk`。API 启动会拒绝该值，并在 SDK 导入前后及每次 Decrypt 前禁用
  `credentials` 与 `alibabacloud-tea` 自带 logger；不得为了排障打开可能输出 Authorization、
  `x-acs-security-token` 或完整响应头的 wire debug。
- `/api/health` 是兼容旧监控的 readiness 别名。所有响应 `no-store`；失败统一为脱敏 `503`。
- Docker/Compose 和 ALB 目标组使用 `/api/health/ready`，容器/ECS liveness 使用
  `/api/health/live`。readiness 端点只应允许可信的 ALB/监控来源访问，不得公开给任意互联网
  调用者反复触发 KMS 探针。
- WAF/ALB 必须在请求进入 API 和 KMS 预检前，对短信请求及其他认证写接口实施独立的按路径、
  来源和速率限制。应用内 PostgreSQL 限流不能替代该外层门禁；WAF/ALB 规则、告警和压测未验收
  前禁止正式发布。

readiness 的缓存成功不授权任何业务写入。短信、认证、联系人写入仍必须执行各自请求级 KMS
预检，审批、分配、占用、出库、发运、签收、OAM 收货、个人仓入库、通知与对账状态不受健康
检查代替或推进。

## 7. 预生产验收

1. 使用预生产 RAM 角色启动 API；占位、缺项、重复密文、错误 context、符号链接或不可读文件
   必须令启动失败，健康检查不得通过。
2. 临时撤销 `Decrypt` 权限后发起一条隔离短信验证码请求；必须返回脱敏 `503`，且挑战、审计、
   状态事件和短信 provider 调用均为零。
3. 恢复权限后验证同一逻辑请求只解密一次，认证与联系人分别命中自己的 Key ID/context。
4. 篡改 KeyVersionId、返回明文长度或注册表坐标，必须在业务写入前失败关闭且不泄露 SDK 错误。
5. 验证 live 与 ready 分离、旧 `/api/health` 等价于 ready、TTL 内不重复探针、坐标变化不复用
   旧缓存、20 个并发检查只有一轮 Decrypt，且等待超时只返回脱敏 `503`。
6. 验证 `migrate -> 隔离 kms-pin-plan 服务 -> 只读既有 pin 查询 -> 双人复核差集 ->
   migrator INSERT -> 只读 gate -> API` 顺序；分别演练首次空表全量插入和轮换仅新增，匹配旧行不得重插。尝试
   upsert/update/delete/truncate、缺 pin、错 hash、错 KMS version 或错历史坐标必须失败关闭。
7. 在 WAF/ALB 预生产规则下验证突发认证请求先被外层限流，不能形成高频 KMS 调用或短信费用。

## 8. 短信产品边界

当前验证码适配器调用阿里云号码认证服务 `dypnsapi` 的 `SendSmsVerifyCode` /
`CheckSmsVerifyCode`，不是标准 `dysmsapi` 短信发送接口。用户购买的 1000 条标准短信套餐不当然
适用于 PNVS；必须先以非敏感产品/账单证据确认套餐所属产品、接口、签名、模板、有效期及计费
口径，再决定沿用 PNVS 或另行开发标准短信适配器。任何套餐购买信息都不代表已经完成生产联调。

完成上述验收仍不代表允许投产；须与 PostgreSQL 16 真实迁移/并发、正式身份、短信产品接口、
真实 KMS/RAM、WAF/ALB、私有 OSS、备份恢复、监控告警、UAT 和回滚演练一并签字放行。
