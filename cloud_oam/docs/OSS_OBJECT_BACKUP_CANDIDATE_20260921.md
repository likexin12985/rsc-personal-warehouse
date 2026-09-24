# 7.78 正式 OSS 附件备份读取候选

2026-09-21。本批在完整静态回归的源码冻结期间完成，仅写入忽略的 artifacts 和交接文档。
**候选未进入生产源码、备份定时任务或 CI；B5 联合备份缺口尚未关闭。**

后续 [7.79 同快照联合恢复](OSS_JOINT_BACKUP_SNAPSHOT_20260921.md)已有本地 PG16 和 68 项
聚焦证据；本页保留 7.78 的对象读取阶段，生产与真实云验收仍未完成。

## 实现与证据

候选位于 `cloud_oam/artifacts/oss-object-backup-candidate-20260921/`：

- `formal_object_backup.py`：只读 SDK 适配器、完成凭据校验、流式内容校验和原子对象包。
- `test_formal_object_backup.py`：真实安装的 OSS V2 1.3.2 参与序列化、V4 签名和响应解析；
  HTTP transport 为内存合成响应，socket 连接被测试夹具禁止，凭据全部为显式合成值。
- `focused-final.log` / `focused-final.xml`：会话 `86388` 终端退出 0，**48 passed / 0 skipped**。
- `verification.json`：固定候选、测试、SDK 和当前生产源码摘要；源文件检查仍为 1,364 个。

本批没有连接 OSS、调用业务 provider、启动 PostgreSQL 或 Docker，没有提交、推送或部署。
与此前[本地数据库和对象恢复](DATABASE_OBJECTS_RESTORE_20260921.md)相比，本批新增的是
真实 SDK 的请求边界和完整上传完成凭据核对，不将内存 HTTP 响应称为真实 OSS 验收。

## 对象读取规则

1. 在任何对象请求前，冻结并验证全部文件记录，包括完整 intent/completion 元数据、
   文件 ID、确定性 key、原文件名、上传人、状态、创建时间、SHA256、大小和 MIME。
   使用现有正式文件校验函数，不另造宽松的附件规则。重复 ID/key、错误 provider 或超出
   总字节预算时整包拒绝。
2. available 对象先读取 HEAD，核对原完成时的 `etag_sha256` 和 `head_manifest_sha256`。
   ETag 作为不透明标识处理，不假定它等于内容 MD5。
3. GET 使用 HEAD 的 ETag 作为 `If-Match`，返回了版本号时同时指定准确 `versionId`。
   无版本号与 `null` 版本分别处理；请求 `Accept-Encoding: identity`，拒绝范围、压缩、
   删除标记与非完整响应。GET 再次核对完成凭据、版本和本次 HEAD 的加密属性。
4. 按 64 KiB 流式累计长度和 SHA256，超过声明长度立即拒绝；内容、头信息或版本变化、
   中断、404/412/429/500 都不发布完成包。异常与完成均关闭流，不盲目重试，不输出 SDK
   错误正文、授权头或签名 URL。
5. pending 意图完整保留在清单中，明确 `object_archived=false`，不冒充已归档的附件。
   quarantined/deleted 当前拒绝整包，须先确定留存和恢复规则，不能静默跳过。
6. 对象暂存目录 0700、文件及最终 tar 0600。包内文件名仅用已校验 UUID，所有对象验证
   完成才发布。使用同目录硬链接独占创建最终名称；已有备份、符号链接、竞争生成的文件
   均保留。第二个对象失败时清理本次暂存内容，不遗留“完成包”。

[OSS GetObject 文档](https://www.alibabacloud.com/help/en/oss/developer-reference/getobject)
规定了 If-Match 条件及版本读取；[Python V2 下载文档](https://www.alibabacloud.com/help/en/oss/developer-reference/simple-download-using-oss-sdk-for-python-v2)
说明指定版本还需要对应版本读取权限。实际云账号权限仍未验证。

## 测试发现及修正

原始失败记录全部保留：

| 会话 | 结果 | 原因与处理 |
| --- | --- | --- |
| 76026 | exit 2，收集错误 | 缺少显式 test 环境，被生产配置正确拒绝；随后显式 test + 内存 SQLite，未打开生产库 |
| 37001 | exit 2，收集错误 | 测试导入路径写错，输入类型实际位于正式文件服务；已修正 |
| 67622 | exit 1，首项失败 | SDK 1.3.2 配置 additional_headers 后，普通请求的 SigningContext 仍未收到附加头 |
| 42995 | exit 1，10 passed | HEAD 类型对象未映射 Content-Range，单靠类型字段漏拒绝范围响应 |
| 70107 | exit 0，43 passed | 签名器接口和原始响应头验证修正后通过 |
| 86388 | exit 0，48 passed | 补充原 SDK 反例、只读签名器、环境工厂、冻结清单和空清单边界后全部通过 |

签名修正使用 SDK 提供的 signer 注入接口，仅给原 V4 算法补入 If-Match 和
Accept-Encoding，并拒绝 HEAD/GET 之外的方法。没有改装已安装 SDK、放松 TLS 或删掉
签名断言。HEAD 与 GET 均检查原始响应头，避免 SDK 类型模型遗漏字段。

## 未完成的联合备份和恢复接入

当前 manifest 明确 `database_snapshot_bound=false`。它只证明提供的对象清单和对象包，
不声称清单覆盖了整个数据库，也不能作为备份成功标记。

下一步按以下顺序推进：

1. 在忽略的候选目录复用现有 `deployment/backup` 锁、只读角色、RLS 和导出后复查，
   将完整 files 清单与 pg_dump 绑定到同一个导出快照。不能把另一次普通查询当成同快照。
2. 用全新自有 PG16 实例和合成附件证明：快照后新建/完成附件不会混进原快照；数据库
   恢复后的 files 与导出清单完全一致；库存、SN、对账和审计事实不因备份改变。
3. 将数据库 dump、清单和对象包纳入唯一原子完成标记及哈希验证；任一子步骤失败不得发布
   总包或触发旧备份清理。实际 Docker 数据库/客户端整体链仍需单独验证。
4. 明确隔离/删除态留存策略、专用只读 OSS 身份与 bucket/region 绑定、KMS 权限和恢复
   方法。manifest 记录观察到的 SSE/KMS 引用，只是清单，不证明能在灾备位置解密。
5. 原完成凭据绑定 ETag。真实恢复若产生不同 ETag，即使字节 SHA256 相同，也不能声称
   已满足现行完成凭据。须验证保留条件或设计独立可审计恢复证明；不得修改原完成记录
   来伪造一致。实际云恢复、异地保管和生产 RPO/RTO 仍开放。

生产源码冻结结束并完成候选集成验证后，才评估最小生产落地范围。本批不修改通知 0129
和既有门禁清单；当前唯一完整静态回归仍为会话 `53877`，准确运行状态见
[续开发入口](CONTINUE_DEVELOPMENT.md)。
