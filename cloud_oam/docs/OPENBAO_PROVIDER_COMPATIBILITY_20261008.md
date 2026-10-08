# OpenBao Transit 追加式提供方契约准备（2026-10-08）

范围：试点 MVP，不等同完整 V1。已完整重读正式 V1 基线；本文件只记录密钥托管替代方案的本地准备，不改变产品范围、安全要求、生产配置或发布门禁。

**当前不是可切换的生产提供方。** 新模块没有被 Settings、factory、readiness、迁移或数据库安全目录注册；没有凭据发现、文件读取、默认网络客户端或 readiness 成功回执。当前 Aliyun 生产读取路径和历史 SQL 保留原状。没有访问真实云资源、生产密钥或生产数据库，也没有购买资源、提交或推送。

## 1. 当前兼容边界（现有代码只读核验）

| 边界 | 当前事实 | 对追加迁移的影响 |
| --- | --- | --- |
| `backend/app/production_adapters.py` | registry schema 为 `rsc.kms.encrypted-data-key-registry.v1`；entry 精确包含 purpose、kms_key_id、application_key_version、kms_key_version_id、ciphertext_blob、encryption_context。Aliyun decrypt 回执必须匹配真实 KeyId 和 KeyVersionId。 | OpenBao 不能伪装成该 registry，也不能从配置填造 Aliyun 回执字段。保留旧 loader；新增明确提供方分流。 |
| `backend/app/formal_services/authentication_idempotency.py` | 生产 cipher factory 只接受精确 `KmsAuthenticationKeyProvider`；configured factory 只接受 `aliyun_kms`。认证重放行保存 encryption_key_version，未保存提供方和 key id。 | 应用版本必须在同一 purpose 的所有提供方中全局唯一；不能只在新 registry 内去重。跨提供方切换需冻结认证写入并证明未过期的旧 replay 引用为 0，或另做显式、可验证的兼容映射方案。 |
| `backend/app/formal_services/material_request_contact.py` | v1 信封精确九字段，provider 固定 `aliyun_kms`；历史读取使用保留的 kms_key_id/key_version。应用 AAD 绑定 request_id/requester_person_id，不包含 provider。 | 新提供方应追加新信封契约，保留旧 v1 双读。不能只替换 provider 字符串，也不能悄悄更改旧 AAD。当前投影和不可变 revision 的所有旧引用都仍需 Aliyun registry/权限可读。 |
| `backend/alembic/versions/20260901_0040_kms_data_key_pins.py` 与 `foundation_models.KmsDataKeyPin` | immutable pins 保存 Aliyun 字段；唯一 purpose/application version、ciphertext 指纹；UPDATE/DELETE/TRUNCATE 受守卫；API SELECT only。 | 新提供方需要追加独立绑定或经审查的统一绑定表；保留旧行，不将 Transit 整数版本塞入 Aliyun kms_key_version_id。新老表之间的 purpose/version 全局唯一也必须由数据库证明。 |
| `backend/app/database_security.py`、`kms_pin_gate.py`、`kms_readiness.py` | database security/pin gate 对字段、约束、权限、pin 匹配及引用实施验证；通用 readiness gate 本身不识别 Aliyun，缓存的是调用方提供的三元坐标集合与解密可用性。 | 新表、触发器、权限、provider 路由和具备完整提供方区分的 readiness 坐标需配套前向变更；只有 adapter 单测不能满足发布条件。 |
| 冻结 `20260831_0029_material_request_approval_domain.py` | PostgreSQL/SQLite 联系人信封校验限定 provider=`aliyun_kms`。 | 不修改历史文件；在新的迁移中按精确前序函数/触发器源码替换必要片段，保留所有其他守卫。 |
| 冻结 `20260909_0069_stock_reservations.py` | PostgreSQL 使用函数源码 hash 与明确 replacement；SQLite 重建的申请触发器也保留 Aliyun 信封约束。 | 不放宽整个触发器，不破坏已存在的分配、占用、状态因果规则。 |
| 冻结 `20261217_0168_shipment_projection.py` 与 `shipment_projection_0168/functions.json` | 0168 在精确前序/PG16 migrator/锁约束下替换守卫；冻结 JSON 中 `rsc_guard_material_request_identity_0029()` 的 before 和 after 均保留 `aliyun_kms`。 | 新迁移必须以届时实际最新 head 的完整源码为前序，不能改 0168 JSON 或其 hash 来绕过漂移检查。 |

`validate_persisted_kms_key_references()` 当前对 auth 只检查未过期 completed/failed replay；对 contact 检查当前申请与所有 revision 的永久引用。未来新提供方不得把 missing historical key、网络失败或 token 撤销降级为可写状态。

## 2. 本轮只新增的文件

- `backend/app/openbao_transit_candidate.py`：独立 typed metadata、canonical context/AAD、精确 pin 校验和 decrypt adapter。
- `backend/tests/test_openbao_transit_candidate.py`：仅新模块的离线聚焦测试。
- `deployment/openbao-pilot/candidate_hook.py` 与 `backend/tests/test_openbao_candidate_hook.py`：仅隔离实例使用的 UDS transport、真实服务 hook 及新增反例。
- 本文档。

OpenBao 真实隔离原型、凭据/OIDC 原型有独立的运行证据，不能与本模块的离线测试混为一谈。没有修改现有 Settings/factory/readiness/migrations/SQL、主交接、Compose、Aliyun SDK 或依赖版本。

## 3. 候选契约

`OpenBaoKeyCoordinate` 显式保存 purpose、environment、provider_instance_id、application_key_version；application 固定 `cloud_oam`，provider 固定 `openbao_transit_v1`。两种 purpose 分别映射到 `transit/keys/rsc-authentication-idempotency` 和 `transit/keys/rsc-material-request-contact`，请求只能去对应 `/v1/transit/decrypt/<name>`。

`context_b64(coordinate)` 与 `associated_data_b64(coordinate)` 使用相同坐标字段，加不同 schema：`rsc.openbao.derivation-context.v1` 和 `rsc.openbao.wrap-aad.v1`。序列化固定 `sort_keys=True, separators=(',', ':'), ensure_ascii=True`，ASCII 字节后做 canonical base64。环境、实例、用途、完整 key path 和应用版本都参与派生与 wrap AAD。这里的 AAD 保护 wrapped DEK，不能替代现有业务信封 AAD。

`OpenBaoWrappedKey` 保存原始 `vault:vN:<canonical-base64>` 与独立声明的 transit_key_version，二者必须相同。候选限定 aes256-gcm96 包装 32 字节 DEK，因此 payload 必须正好 12 字节 nonce + 32 字节密文 + 16 字节 tag。不同算法、prefix 或包装结构 fail closed，不能由解析器猜测转换。

`OpenBaoReviewedPin` 必须来自外部独立审查的不可变清单，包含完整坐标、真实 Transit 版本、原始 wrapped ciphertext ASCII 的 SHA-256，以及 base64 解码后的 context/AAD 字节 SHA-256。loader 没有“根据解密响应补 pin”入口。typed pin 构造本身不证明外部审批、数据库不可变性或备份齐全；这些仍待前向迁移。

`OpenBaoTransitCandidate` 构造必须另行提供期望的 environment/provider_instance_id；只接受 1–64 个条目及一一匹配的 pins，拒绝重复 purpose/application version、重复 ciphertext 指纹、错误用途或部署坐标。历史版本缺失直接失败，不回落到 active/latest。此候选的去重仅覆盖传入的新条目；跨旧 Aliyun registry 的全局去重尚须数据库契约补齐。

transport 必须显式注入，收到 exact path、ciphertext、context、associated_data 和 3 秒超时预算。模块没有自身 HTTP/凭据行为。只接受 HTTP 200、无非空 errors/warnings、data 精确为 plaintext 的单项回执；canonical base64 解码必须为 32 字节。不会接受 batch、重定向、Aliyun KeyVersionId 等额外回填字段。没有重试，没有长期 plaintext cache，错误输出固定文案且不保留原 transport 异常链。Python bytes 不承诺物理内存清零。

**Transport 的生产义务仍未完成：** 真实 socket/TLS 归属、token 最小权限与刷新、connect/read 总预算、响应大小和重复 JSON key 校验、关闭重定向/重试、日志脱敏需由后续生产实现及集成门禁证明。fake transport 通过不能证明这些属性。候选也不处理文件模式/symlink/原子加载；未来文件 registry 加载器应单独验证，不能把 typed metadata 测试当文件安全通过。

本轮另外提供 `PrototypeUnixDecryptTransport`，在独立 0700 目录/0600 socket、属主和 inode 核验下限定两条 decrypt POST 路径，使用 3 秒 connect/send/recv 总预算、32 KiB wire / 16 KiB body 上限、严格 JSON 和显式连接关闭。它在一次性 Linux 容器完成真实解密，但没有生产 token 续期或受控服务部署能力；不自动注册到应用。

## 4. 追加迁移的准入条件（本轮未实施）

1. 给新 provider 提供 append-only 非秘密绑定，保持 API 只读、迁移身份受控写入、禁止 UPDATE/DELETE/TRUNCATE；跨提供方 purpose/application version 全局唯一，单 key path 不跨用途。
2. 生产配置与工厂显式支持新 provider；旧 Aliyun 的真实 SDK 校验、registry、pins、历史引用和权限保留。禁止把通用 callable 包装为旧 KMS 类后绕过 provider admission。
3. 联系人新信封/旧 v1 双读、历史引用扫描、两类提供方 readiness 全链一致。对现有加密业务事实需要另立经过审计的重加密方案，不能覆盖 immutable revision 或重绑旧 pin；新空库则需先证明 pins/replay/contact 引用均为空。
4. 新 head 迁移更新准确 SQL/security catalog，保持原来的状态、不可变性、权限与因果守卫；migration downgrade 不能在新提供方事实存在时静默丢弃支持。
5. 精确 DB roles + 托管 PostgreSQL 16 门禁 + 双提供方历史读/越权/并发/失败场景通过。现有托管 PG16 已实际执行，当前待完成的是准确新候选和新契约的门禁证据，不能笼统说没有 hosted DB；本地单测不代表通过。
6. 真实 OpenBao 固定版本/来源校验、独立 token/撤销/封存、加密持久化、独立离机 unseal 份额、备份恢复和轮换可读证明齐全，才可讨论启用。新数据密钥先恢复演练再设为 active；禁止 rewrap 覆盖旧坐标/pin。

## 5. 本轮验证回执

仅执行新聚焦测试，未重跑已终态测试：

```sh
# cwd: cloud_oam/
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_openbao_transit_candidate.py --junitxml=/tmp/rsc-openbao-candidate-20261008.xml > /tmp/rsc-openbao-candidate-20261008.log 2>&1
```

结果：**49 passed in 0.37s，退出码 0**。原始文本回执 `/tmp/rsc-openbao-candidate-20261008.log`，JUnit `/tmp/rsc-openbao-candidate-20261008.xml`；这是本机临时文件，不是持久交付库或云门禁。

覆盖两目的/精确历史版本、canonical context/AAD、错误环境/实例/目的、非法版本、真实 prefix 声明不一致、错误 pin/ciphertext 指纹、重复应用版本、历史缺失、403/503/302、异常响应/短密钥/非 canonical base64/batch、超时/权限异常无自动重试和无原始异常链，以及生产组合根未注册该模块。socket 在单测中被阻断。

真实 OpenBao 2.7.1 原型复用上述 canonical helpers 和 transport seam；本单测报告不宣称真实 OpenBao 已成功。后续真实原型结果以其独立证据为准。

后续新增 transport 测试命令为 `PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_openbao_candidate_hook.py --junitxml=/tmp/rsc-openbao-candidate-hook-20261008.xml`。首次 29 passed / 1 failed 揭示提前拒绝响应未关闭 `HTTPResponse`，修复后终态 **30 passed in 1.89s，exit 0**，首轮证据保留；旧 49 项没有重跑。两组日志/JUnit 已逐字复制到忽略的 `artifacts/formal-0165-integration/openbao-prototype-20261008/`。

真实 Linux hook 随 Transit/Identity 组合首次运行通过：两用途各应用版本 1/2，共 4 条 DEK 均与独立参考值匹配；它们当时均为实际 Transit v2。错 context/AAD/用途、pin 不匹配与 token 撤销均拒绝。该结果和生产/迁移欠缺分别记录于[隔离组合证据](OPENBAO_ISOLATED_EVIDENCE_20261008.md)，不能把应用版本 1/2 等同 Transit v1/v2。

## 6. 官方依据

OpenBao 官方 Transit API 定义 named-key decrypt、base64 derivation context、AEAD associated_data、wrapped datakey 和整数版本 ciphertext prefix；decrypt 的 plaintext 回执不提供 Aliyun KeyVersionId。采用精确 pin/请求绑定是本项目适配设计，不是官方提供的额外身份回执。[OpenBao Transit API](https://openbao.org/docs/api/secret/transit/)

只读检查当前仓库契约后得出以上兼容结论；没有对生产现状做验证或发布判断。
