# 小规模私有 OSS 部署与费用审查

2026-10-08，Asia/Shanghai。**试点 MVP，不等同完整 V1；本文是未执行的方案，不是云配置或上线验收回执。** 本轮只读取正式 V1 基线、当前交接、三项云配置准备单、附件/备份源码及阿里云官方公开文档；未登录业务浏览器，未创建 Bucket、授权身份、上传对象、购买服务、提交或推送代码。用户已确认私有 OSS 尚未配置。

## 1. 建议选型与基线关系

保留既有杭州轻量服务器；附件使用杭州 `cn-hangzhou` 的私有 OSS，标准存储、本地冗余 LRS、按量付费。使用 OSS 自有 HTTPS 域名、短时签名 URL 和 OSS 完全托管的 SSE-OSS/AES256。无需为这个方案增加 CDN、专属实例、传输加速、容量包或付费 KMS。这里只建议方案，不构成开通付费服务授权。

正式基线 4.1 指定文件使用“阿里云 OSS”；4.2 要求“OSS 私有 Bucket，使用短时签名 URL，不公开业务附件”。上述选型满足这一方向；LRS 只在一个可用区内冗余，不能当作跨可用区容灾或独立备份。基线 5 的 RPO ≤5 分钟、RTO ≤2 小时仍须备份和恢复实测，不能从 OSS 持久性或一份镜像推导通过。[存储类型及冗余说明](https://help.aliyun.com/zh/oss/user-guide/overview-53/)

SSE-OSS 的对象加解密功能官方标为免费，保护 OSS 静态对象；它不向应用提供字段加密 API，也不替代认证响应或收货联系人的应用数据密钥注册表、密钥版本、轮换和恢复。本文不采用 OSS 默认 KMS 密钥冒充自建应用 KMS。[服务端加密](https://help.aliyun.com/zh/oss/user-guide/server-side-encryption-8)

## 2. 公开价与明确测算

查询日的[OSS 官方价格页](https://www.aliyun.com/price/detail/oss)列出中国内地统一按量价，包含杭州。以下只保留本方案相关项：

| 项目 | 公开单价/规则 | 计费性质 |
| --- | --- | --- |
| 标准 LRS 存储 | 0.12 元/GB/月 | 随实际容量和时长变化 |
| 公网流出 | 00:00–08:00 为 0.25 元/GB；08:00–24:00 为 0.50 元/GB | 随下载量及时间变化 |
| 标准 PUT 类 | 每月每地域 500 万次以内公开免费额度；超出 0.01 元/万次 | 请求变量费 |
| 标准 GET 类 | 每月每地域 2000 万次以内公开免费额度；超出 0.01 元/万次 | 请求变量费 |
| 内/外网流入 | 流量费免费；上传请求、对象存储另计 | 无上传流量费 |
| 基础按量 Bucket | 无本方案新增的包月实例/容量包费用 | 固定采购额 0；并非使用免费 |

价格页同时列有中国内地标准 LRS **0.09 元/GB/月官网折扣价**。本测算不预先抵扣该折扣、新客额度、请求免费余量或已有资源包。官网正文在部分读取方式下仅返回导航，本轮通过官方价格页的搜索索引读取到了完整中国内地表；账号购买页、实际账单及优惠适用范围未核验。不得把网页公开价写成已获得的账号报价。

**基准用量假设：** 30 天/720 小时，平均存储 5 GB，公网下载合计 10 GB，PUT 类合计 1,000 次，GET 类合计 10,000 次。这里 GB 按 OSS 的二进制口径（2^30 字节）。GET 总数必须包含 HEAD 验证、下载、备份读取及适用的跨域 OPTIONS；PUT 总数必须包含实际上传及其他计费 PUT 类操作。默认把下载都计为忙时，并按免费请求余量已经耗尽来作保守预算。

| 基准费用组成 | 计算 | 30 天金额 |
| --- | --- | ---: |
| 附件存储 | 5 × 0.12 | 0.600 元 |
| 公网下载 | 10 × 0.50 | 5.000 元 |
| PUT 类请求 | 1,000 ÷ 10,000 × 0.01 | 0.001 元 |
| GET 类请求 | 10,000 ÷ 10,000 × 0.01 | 0.010 元 |
| **本组假设的小计** | 上述四项相加 | **5.611 元** |

如果全部 10 GB 下载均为闲时，按同一未抵扣口径小计为 3.111 元；若请求额度对本账号仍有余量，忙时场景相应为 5.600 元。若再适用 0.09 的存储折扣，该忙时且不抵扣请求的场景为 5.461 元。这些都是条件计算，不是报价承诺。OSS 按小时计量；31 天、平均 5 GB 的存储部分按 0.12÷720×744×5 约为 0.620 元，不能把 30 天估算当作每个自然月固定账单。小额费用会累加出账，0.001 元请求估算不是“无需付费”。[计费周期和小额累加规则](https://help.aliyun.com/zh/oss/billing-overview)、[请求类别](https://help.aliyun.com/zh/oss/api-operation-calling-fees)

**此小计不包括** 数据库备份、对象备份副本、恢复演练和日志的新增存储/下载；也不包括既有服务器续费、短信、数据库或其他云服务。它只能回答上述小量附件本身的 OSS 基础开销，不能称作整个项目的月费。

备份容量应另代入公式：`存储 GB×0.12×实际小时/720 + 闲时公网流出 GB×0.25 + 忙时公网流出 GB×0.50 + 超额度请求费`。例如额外保存平均 5 GB 的一份副本，单独增加约 0.60 元/30 天存储；但每日从公网重新全量读取 5 GB，30 天共 150 GB，忙时流出可达 75 元。现有备份读取器会对清单逐对象 HEAD/GET，不能误以为“业务用户少”意味着高频全量备份免费。备份频率、增量/WAL、留存份数及恢复路径尚未配置；不以每天一份示例冒充满足 5 分钟 RPO。当前正式适配器未启用内网 Endpoint，轻量服务器内网可达性未实测，不能套用“同地域 ECS 内网下载免费”来消掉这笔预算。

## 3. 当前代码对象前缀与真实操作

源码审查基于已提交 `14d0731387f8a24e8b25ed9222f89fec248ea563`。确定性 key 为 `formal-files/v1/{purpose}/{uuid 前两位}/{uuid hex}`；不是原文件名，没有姓名/电话进入 key。

| 场景 | 对象用途/前缀 | 当前 OSS 操作 | 权限 |
| --- | --- | --- | --- |
| 需求附件 | `request_attachment/` | 签名 PUT；完成 HEAD；授权后签名 GET | `oss:PutObject`、`oss:GetObject` |
| 外部审批登记证据 | `external_approval_evidence/` | 同上 | 同上 |
| 收货异常证据 | `receipt_exception_evidence/` | 同上 | 同上；用途仍受本人/区域授权核验 |
| 受控来源配置审查 | `source_configuration_evidence/` | 同上 | 仅在首发实际启用该管理流程后另行加入精确前缀 |
| 后置功能保留 | `stocktake_evidence/`、`daily_reconciliation_evidence/`、`stock_loss_evidence/`、`return_condition_evidence/` | 基础附件读写 | 不因为源码存在就向试点新增这些云前缀或功能 |
| 报表/期初导入保留 | `inventory_report_export/`、`opening_count_import/`、`opening_count_import_error/` | 服务器单次 PUT/HEAD、受控 GET | 后置，权限与功能开放另审 |
| 正式附件备份源读取 | 已审查 `files` 清单中的 key | HEAD；携带 If-Match 的 GET；观察到版本时指定 versionId | 独立身份 `oss:GetObject`；版本读取如实际使用再授予 `oss:GetObjectVersion` |

权限映射以官方 [PutObject](https://help.aliyun.com/zh/oss/developer-reference/putobject)、[HeadObject](https://help.aliyun.com/zh/oss/developer-reference/headobject)、[GetObject](https://help.aliyun.com/zh/oss/user-guide/simple-download-1) 为依据；**HEAD 使用 `oss:GetObject`，不要杜撰 `oss:HeadObject`。** 当前应用没有列举 Bucket/对象、删除、ACL 修改、标签或分片上传调用。文件最大值默认 120 MiB，不需要因 SDK 存在就授予全套 `oss:*` 或 `AliyunOSSFullAccess`。

本次检查的本地实现：

- `backend/formal_file_integrity.py`：用途和 key 白名单、尺寸/MIME/文件名及完成信息校验。
- `backend/app/formal_services/formal_files.py`：按用途和当前权限重新鉴权、幂等/审计、上传有效期 60–900 秒、下载 30–600 秒；部署默认上传 600、下载 300 秒。
- `backend/app/formal_services/file_storage.py`：V4 签名；绑定 Content-Type、文件 ID、SHA256 元数据、禁止覆盖；HEAD 校验；服务端生成文件 PUT 超时后只精确 HEAD 回读。
- `deployment/backup-worker/formal_object_backup.py`：独立只读源身份、清单/版本/内容摘要核验。

## 4. 可审查的最小权限模板

以下是**待替换非敏感坐标的 RAM 权限草案，未应用**。`<account-id>` 和 `<private-bucket>` 必须来自最终批准资源，不使用猜测名称或真实凭据。只为已确定的三个试点附件用途授权；若来源配置流程确需附件，先审查再加入该单一前缀。

```json
{
  "Version": "1",
  "Statement": [
    {
      "Effect": "Allow",
      "Action": ["oss:PutObject", "oss:GetObject"],
      "Resource": [
        "acs:oss:*:<account-id>:<private-bucket>/formal-files/v1/request_attachment/*",
        "acs:oss:*:<account-id>:<private-bucket>/formal-files/v1/external_approval_evidence/*",
        "acs:oss:*:<account-id>:<private-bucket>/formal-files/v1/receipt_exception_evidence/*"
      ]
    },
    {
      "Effect": "Deny",
      "Action": ["oss:*"],
      "Resource": [
        "acs:oss:*:<account-id>:<private-bucket>",
        "acs:oss:*:<account-id>:<private-bucket>/*"
      ],
      "Condition": {"Bool": {"acs:SecureTransport": ["false"]}}
    }
  ]
}
```

`Deny oss:*` 仅拒绝 HTTP，不授予所有操作。Bucket 级强制 HTTPS 策略由配置身份设置，运行身份没有管理权限。[RAM 的 HTTPS 拒绝条件](https://help.aliyun.com/zh/oss/tutorial-use-a-ram-policy-to-allow-a-user-to-access-oss-resources-only-over-https)、[授权语法](https://help.aliyun.com/zh/oss/user-guide/authorization-syntax-and-elements)

不要把 SourceIp 固定为服务器公网 IP 后直接套在这三个对象前缀：签名 PUT/GET 是浏览器/手机直接请求 OSS，实际来源是客户端网络。业务权限由 API 在签发前核验；签名链接在短有效期内属于持有者可使用的凭据，不能记录完整 URL 或用 CORS 代替鉴权。[阻止公共访问与签名 URL](https://help.aliyun.com/zh/oss/user-guide/block-public-access/)

身份分离：应用附件身份只能上述对象读写；备份源身份只读被备份前缀；备份目标写入身份只能独立目标 Bucket/备份前缀；恢复身份临时授权且脱离日常 API。配置者可以在受审查阶段管理指定 Bucket 的 ACL、加密、CORS、策略和费用告警，但不将这些权限留在运行容器。备份源与目标的密钥/凭据不得复用。当前备份 Worker 产出本地已验证联合包，本次未证明其已自动上传到独立 OSS 目标。

## 5. Bucket 配置与当前代码最小差异

1. 新建的附件 Bucket：杭州、Standard/LRS、private、Bucket 级 Block Public Access、SSE-OSS/AES256、拒绝 HTTP；使用 `https://<bucket>.oss-cn-hangzhou.aliyuncs.com`，不引入自定义域名证书/CDN。OSS 自有 Bucket 域名由阿里云管理证书。[OSS HTTPS](https://help.aliyun.com/en/oss/user-guide/access-oss-by-https-protocol)
2. **附件 Bucket 必须从未开启版本控制。** 官方 PutObject 文档明确：版本控制为 Enabled 或 Suspended 时，`x-oss-forbid-overwrite` 不生效。当前代码依赖这个头拒绝相同 key 覆盖，且完成凭据/下载以当前对象为准，所以不能“顺手打开版本控制”。若将来采用版本控制，须先完成版本绑定读写和恢复设计；本里程碑不扩展。独立备份仍必需。
3. CORS 限定实际 Web Origin `https://rscwz.cn`（Origin 不含 `/xx`），只允许实际所需 PUT/GET/HEAD；AllowedHeader 包含当前精确签名头 `content-type`、`x-oss-meta-sha256`、`x-oss-meta-file-id`、`x-oss-forbid-overwrite`。若适配器新增加密头，再显式加入；只暴露所需 ETag/请求 ID 等响应头。小程序真实上传/下载域名另在微信允许域名配置中验收，不能由 CORS 通过替代。[CORS 参数](https://help.aliyun.com/zh/oss/developer-reference/putbucketcors)
4. 当前正式附件和备份读取器均使用 `EnvironmentVariableCredentialsProvider`。不能声称 PNVS/KMS 默认凭据链自动供给 OSS。统一受控运行身份方案确定后，给两个适配器分别接入可刷新、用途隔离的凭据提供器；签名 TTL 不得长于剩余凭据寿命，续期失败应关闭新签发，禁止把静态临时 token 当长期方案。该项需针对性 SDK 验证，不能只改文档。
5. 当前 `StoredObjectHead` 及正式 HEAD 适配器不暴露 SSE 标记，也没有读取 Bucket 私有/BPA/版本控制/加密配置。新增单独的只读部署核验：绑定准确 Bucket/地域/账号，检查上述真实配置并保留脱敏回执；不要给日常应用附加 Bucket 管理权限。若只靠默认 Bucket 加密，必须实际上传并读取 SSE 结果；若改签名绑定 `AES256`，则同时改签名头/前端允许头/聚焦用例，不能假设新增头已生效。
6. 标准附件完成目前主要核对 HEAD 元数据、长度和 MIME；SHA256 元数据由上传者声明，不能在验收文案中把 HEAD 等同于对原始字节重新计算 SHA256。对真实测试对象及备份恢复按 GET 流式回读计算摘要；保留现有幂等、禁止覆盖、身份/授权版本和可恢复未知结果边界。

配置和读回应先于开启 `OAM_FILE_STORAGE_ENABLED`。`private_oss` 占位参数检查通过本身不足以代表这些云事实。不得取消现有检查来绕过尚未配置的状态。

## 6. 后续执行与账号侧待核验

资源创建前，明确最终账号、两个用途的 Bucket 名、备份留存和访问量，并在账号购买/配置页面核实：杭州 Standard/LRS 可选及实际单价、0.09 官网折扣是否生效、标准请求免费余量是否已被同账号其他 Bucket 用掉、有无资源包/抵扣/自动续费、默认未选任何增值收费项。基准计算不预订资源包；使用量稳定后再按真实账单比较，不因公开“免费额度”跳过余额和费用告警。

资源获准创建后，先完成身份用途隔离及自动续期验证，再按本方案配置 Bucket 和读取脱敏回执。用隔离测试 key 完成：匿名拒绝、HTTPS 成功/HTTP 拒绝、授权上传、精确 HEAD/GET 摘要一致、相同 key 第二次 PUT 被拒绝、删改权限拒绝、过期 URL 拒绝、撤权/续期失败关闭；三角色通过 API 的附件授权分别验收。测试操作使用审查过的实际角色与 prefix，不靠主账号完成后宣称运行身份通过。

最后执行数据库与附件一致清单备份、独立目标保管、隔离恢复和完整摘要回读，记录真实时长、恢复权限和不可变完成凭据兼容性。真实恢复若 ETag 改变，沿既有恢复契约处理，不能改原完成记录伪造一致。备份成本及 RPO/RTO 未配置/未测量时继续开放。上述实测均未在本次研究中执行，也不替代同 SHA 的 hosted PG16、真实 PNVS、UAT、部署或回滚门禁。

## 7. 独立只读 Bucket 预检候选

研究后新增 `scripts/oss_bucket_preflight.py`，尚未接入 Compose/既有预检，尚未真实访问 OSS。它只允许四种 Bucket GET，使用已安装并锁定的 `alibabacloud-oss-v2==1.3.2`：`GetBucketAcl`、`GetBucketVersioning`、`GetBucketEncryption`、`GetBucketPublicAccessBlock`。运行前核验了 SDK 方法、请求类、响应字段；对应官方说明为 [ACL](https://help.aliyun.com/zh/oss/developer-reference/getbucketacl)、[版本控制](https://help.aliyun.com/zh/oss/developer-reference/getbucketversioning)、[加密](https://help.aliyun.com/zh/oss/developer-reference/getbucketencryption)、[阻止公共访问](https://help.aliyun.com/zh/oss/developer-reference/getbucketpublicaccessblock)。

默认执行只打印帮助；只有显式 `--inspect` 才在 45 秒上限的子进程发起只读请求。Bucket/地域可用 `--bucket`/`--region` 或现有配置名，必须另提供 `--expected-owner`/`RSC_OSS_PREFLIGHT_OWNER_ID` 校验拥有者。使用独立审查身份的 SDK 环境凭据，工具不会自动登录、查找其他角色、发送短信或修改云配置；真实凭据仅由将来的受控运行端注入，不从聊天接收、不写源码。

输出只有固定 checks 状态和目标坐标摘要，不输出账号、Bucket 名、URL、签名、凭据或 SDK 错误正文。权限不足、网络异常、响应无法解析和超时均为 `unknown`，不是登录失效。只读权限为精确 Bucket 上的 `oss:GetBucketAcl`、`oss:GetBucketVersioning`、`oss:GetBucketEncryption`、`oss:GetBucketPublicAccessBlock`，不授予 Object 读写/删除；这组审查权限不必放进日常 API 身份。

`bucketConfigurationVerified` 仅表示此四项配置及拥有者被读取并满足约束；`releaseReady`、凭据续期、对象实际读写、HTTPS Bucket Policy、CORS、备份恢复全部保留 false。它不读取/证明费用、生命周期、Bucket Policy 完整内容、对象 ACL 或历史文件是否加密，不能单独放行应用。

新增测试使用真实 SDK 的序列化、V4 签名和 XML 解析，HTTP transport 完全在内存；socket 联网被禁止，身份全为合成值。两次探索失败保留：第一次 31 passed/1 failed，发现空 `<Status/>` 被 SDK 解析为 None；随后增加原始非空 XML 的受限只读校验，不能再把它当作未曾开启。第二次 34 passed/1 failed，发现锁定 SDK 拒绝官方示例的默认 XML namespace；最终将这个已确认的兼容性缺口明确断言为 `unknown`，没有修改第三方包或把解析异常当通过。**若实际云端返回这种命名空间，部署核验仍被阻断；届时只做受审查的解析兼容修复并新增真实回执，不能删除检查。**

最终仅运行新增文件：`PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_oss_bucket_preflight.py`，**40 passed，0.63 秒，exit 0**；两份新 Python 文件 AST 通过，真实 CLI `--help` 通过且未发请求。未重跑既有终态门禁；这些结果不是实际 Bucket 已配置证据。

补充审查：为覆盖默认 SDK 构造分支，新增合成环境身份/真实 SDK 配置的单项验证 `test_default_client_uses_bounded_verified_transport_with_synthetic_identity`，仅运行该新增项：**1 passed / 0.47s / exit 0**，日志 `/tmp/rsc-oss-default-client-focused.log`。此前 40 项不重跑；两次结果都未访问真实云资源。
