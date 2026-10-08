# OpenBao C1 追加绑定与候选读取证据（2026-10-08）

**试点 MVP，不等同完整 V1，仍未上线。** C1 追加绑定、候选读取和真实隔离 PG16 增量门禁已完成。本批实现已提交为 `c4851de57bfd53237634ba06d3dce4b112460a83`（前序 `5f59c3c`）；本节终态交接随后单独提交。精确候选、推送与 CI 状态见 `artifacts/formal-0165-integration/key-provider-c1-20261008/candidate-final-receipt.json`，不构成生产 provider 激活或发布批准。

已完整重读仓库正式 V1 基线，并保持用户缩减后的试点范围。本轮只推进密钥绑定兼容基础设施；不新增后置业务，不启用生产 OpenBao provider，不改写旧业务密文，也不购买云资源。此前隔离 OpenBao 原型的结果见 [隔离原型证据](OPENBAO_ISOLATED_EVIDENCE_20261008.md)；此前兼容设计见 [提供方兼容准备](OPENBAO_PROVIDER_COMPATIBILITY_20261008.md)。两份文件中“追加绑定尚未实施”的历史描述由本次 C1 进度补充，不能反过来把旧原型结果算作当前迁移门禁。

### 23:14 GitHub 代理恢复

用户明确 GitHub 经代理访问。读取当前系统代理后，为本仓库 GitHub HTTPS 配置 `http://127.0.0.1:11304`，经代理网页和 Git refs 读取均通过；旧 run 已终态，准确远端回读确认上次 push 未到达。既有源码/PG16/测试证据不变，后续一次正常推送并验收新 SHA；实际远端和 CI 回执见 `key-provider-c1-20261008/proxy-candidate-final-receipt.json`。下述直连失败是保留的历史，不再作为未解决代理配置问题。

### 22:58 Git 传输终态补充

实现 `c4851de` 与终态交接 `eeba5aa` 均已本地提交。一轮正常 push 超时后，已准确确认远端仍为 `14d0731`、新 workflow 数为0、无残留 Git 传输进程；未盲重推。`github.com` TCP/TLS 连接超时，而 GitHub API 可读，不能据此宣告登录失效。传输恢复后先回读再推送最新候选；完整新 SHA 门禁继续待验收。证据和接续步骤见 [当前交接](CONTINUE_DEVELOPMENT.md)，最终候选回执继续绑定精确源码和状态。

## 1. C1 当前实现范围

| 部分 | 当前代码与约束 | 当前不证明的事项 |
| --- | --- | --- |
| 0180 追加迁移 | `backend/alembic/versions/20261229_0180_openbao_data_key_pins.py` 从精确 `20261228_0179` 前序追加 `openbao_data_key_pins` 与 `application_key_version_claims`，对应模型位于 `backend/app/key_provider_models.py`。 | 没有生成生产密钥、激活新 provider、变更业务信封或迁移生产数据库。 |
| OpenBao pins | 保存 purpose、environment、provider_instance_id、key_path、应用版本、Transit 版本、原始 wrapped ciphertext/context/AAD 的 SHA-256，以及 created_at。无明文 DEK、token、密钥口令。 | 字段合法和 typed pin 不等于独立审查或双人复核来源已证明。 |
| 跨 provider 版本 | 共享 claims 主键为 `(purpose, application_key_version)`；同一用途的版本跨 Aliyun/OpenBao 全局唯一。环境、实例和 provider 不能另建版本命名空间；不同用途仍独立。迁移身份插入 pin 后由触发器插入 claim，冲突不能忽略。 | 仅数据库 claim 不会自动替现有 auth factory 做 provider 分流。 |
| 旧 0040 | 保留 `kms_data_key_pins` 的旧字段、唯一约束、不可变函数与守卫，精确回填旧 pin 的 provider/hash/created_at 镜像；旧 Aliyun registry、SDK 校验、信封及历史引用读取继续保留。 | 不把 Transit 整数版本冒充 Aliyun `KeyVersionId`，不重绑或覆盖旧 pin。 |
| 不可变性与权限 | 新 pin/claim 使用 UPDATE、DELETE、TRUNCATE 守卫与 ALWAYS triggers；API 只读，直接迁移身份受控追加；claims 必须有精确对应 pin。权限、函数身份和 catalog 由新的安全契约检查。 | SQLite 结构测试不证明 PostgreSQL ACL、会话身份、并发锁或触发器运行语义。 |
| 降级 | OpenBao pins 或非精确旧 claim 存在时拒绝降级；仅精确旧 Aliyun 镜像可移除共享 claims 后重新升级重建。 | 不是删除新提供方事实的恢复捷径；有新事实时仍需独立前向恢复方案。 |
| DB readiness | `key_provider_bindings_0180/functions.json` 与运行时副本以固定 SHA-256 绑定；只在精确前序基础上推进数据库 readiness revision，并检查函数元数据、ACL 和重载。 | 数据库 readiness revision 更新不等于 OpenBao 运行 provider/readiness 已注册。 |
| Scoped catalog probe | `backend/app/stock_scrap_security_probe.py` 的可选 tuple selectors 只筛选表名/函数名；无参数仍全量。指定对象的列、ACL、约束、索引、触发器、FK guards 和全部函数重载保留，参数使用绑定 `ANY`。 | snapshot 只是观察器。合法 selector 对应缺失对象会得到空结果；调用方必须与固定完整预期比较，不能只看查询成功。 |

0180 的冻结迁移、运行时权限契约、当前 head 夹具与审计工具应保持一致。旧 0029/0069/0168 等历史 SQL 不得就地改写来绕过前序 hash 或既有库存/审批守卫。

## 2. Strict registry 与只读 pin reader

`backend/app/openbao_registry_candidate.py` 只读取显式提供的物理绝对路径，schema 固定为 `rsc.openbao.wrapped-data-key-registry.v1`。严格拒绝重复 JSON key、额外字段、错误类型/版本/格式和超限输入；最多 1 MiB、64 条目。逐层目录 fd 与 `O_NOFOLLOW` 阻断符号链接；最终父目录必须为当前 euid 的 0700，文件必须为当前 euid 的普通 0600 单硬链接文件。读取前后及路径绑定重新核对，读取量有界，不自动 resolve 不可信输入路径。POSIX owner/mode 检查本身不宣称已经证明 ACL、挂载或生产部署安全。

registry 不保存或自行生成 reviewed pins。调用者必须独立提供 pins，以及期望 environment/provider_instance_id；文件后续变更不自动热加载。现有 `OpenBaoTransitCandidate` 再统一验证原始 ciphertext、真实 `vault:vN` 前缀、Transit 版本、canonical derivation context、独立 wrap AAD 及三个 hash。历史版本缺失直接拒绝，不回落到 active/latest。

`backend/app/openbao_pin_reader_candidate.py` 提供：

```python
read_openbao_reviewed_pins_candidate(
    db, *, environment, provider_instance_id, expected_coordinates
) -> tuple[OpenBaoReviewedPin, ...]
```

边界如下：

- `db` 是外部显式提供且已经验证的连接；模块不发现 DSN/凭据、不创建连接、不提交/回滚、不重试。数据库身份、只读权限、有限连接/语句超时和日志控制仍是调用者前置条件。
- 独立指定 1–64 个精确历史坐标；在 SQL 前验证用途、环境、实例、版本和去重。结果按指定坐标顺序返回，不选 latest。
- 单条参数化 VALUES CTE + 三个 `LEFT JOIN` 同时观察 pins、claims 和旧 Aliyun pins；`LIMIT` 与 `fetchmany` 均限制为预期条目数加一，缺失/重复/额外返回整体拒绝，不通过 join/filter 隐藏矛盾。
- 明确比较数据库原始 `key_path`、环境、实例、用途、版本；claim 的 provider、hash、aware created_at 必须与 pin 完全一致；同用途/版本存在旧 Aliyun pin 时拒绝。
- 数据库三个 hash 原样作为期望 pins，不从 registry 或远端解密响应回填。typed pin 只说明形状与一致性，**不证明登记来自独立审查/双人复核**。随后仍必须通过现有 registry/candidate 的完整匹配。
- 读取、权限、网络或解析异常统一保持 unavailable/unknown，固定错误不携带原始异常链；不自动宣布登录失效，不启用 fallback。

上述两个候选模块没有接入 Settings、factory 或生产 provider readiness。其单测和 PG 组合里的 mock 解密不能冒充真实生产身份续期或真实 Transit 解密证据。

## 3. 已终态的本地聚焦测试

下表区分原始日志/JUnit 与工具 stdout。只读归档已有结果，没有为写本文档重跑终态测试。各组覆盖不同新增边界，不合并宣称“整个后端/PG16 全部通过”。

| 组 | 范围 | 真实结果 | 原始证据与限制 |
| --- | --- | --- | --- |
| 0180 迁移结构 | `backend/tests/test_key_provider_binding_migration.py` | 35 passed / 1.47s | 证据源为当时工具 stdout，无原始 JUnit XML；命令和工具 chunk `c9fdb1` 已记入 `key-provider-c1-20261008/migration-focused-tool-receipt.json`，不后造 XML。 |
| readiness 迁移守卫 | `backend/tests/test_key_provider_binding_readiness_guard.py` | 18 passed / 0.20s | 同为工具 stdout，无原始 JUnit XML；命令和工具 chunk `591000` 记入同一 tool receipt。 |
| Strict registry | `backend/tests/test_openbao_registry_candidate.py` | 82 passed / 0.55s / exit 0 | 原始 `/tmp/rsc-openbao-registry-c1-20261008.log` 与同名 `.xml` 已存在。 |
| Pin reader | `backend/tests/test_openbao_pin_reader_candidate.py` | 83 passed / 0.37s / exit 0 | 原始 `/tmp/rsc-openbao-pin-reader-c1-20261008.log` 与同名 `.xml` 已存在；包含真实执行 SELECT 的合成 SQLite join 语义和 mock candidate 组合，不是 PG16 权限证明。 |
| Scoped probe | `backend/tests/test_key_provider_scoped_probe.py` | 45 passed / 0.25s / exit 0 | 原始 `/tmp/rsc-key-provider-scoped-probe-20261008.log` 与同名 `.xml` 已存在；记录式连接验证真实 snapshot SQL/绑定/细节保留，未连 PG16。 |
| readiness overlay | `backend/tests/test_key_provider_readiness_security.py` | 12 passed / 0.18s | `artifacts/formal-0165-integration/c1-readiness-20261008.log` 与同名 `.xml`。 |
| 历史夹具准入 | `backend/tests/test_pg16_loss_fixture_permissions.py -k historical_fixture_admission` | 21 passed / 28 deselected / 0.56s | `artifacts/formal-0165-integration/c1-head-fixture-admission-20261008.log` 与同名 `.xml`；未选择项没有重新执行。 |
| 当前 head 链 | `test_alembic_migrations.py` 的两个指定节点 | 2 passed / 100.42s | `artifacts/formal-0165-integration/c1-head-chain-20261008.log` 与同名 `.xml`。 |
| binding security | `backend/tests/test_key_provider_binding_security.py` | 累计 58 个不同节点已通过，分三次运行 | 首次 56 passed / 1 failed / 4.38s；仅修测试中误写的属性名后单点 1 passed / 3.42s；另新增 hash 原子性单点 1 passed / 0.21s。不是一次 58 项全绿。 |

另有 3 个受当前 head 变化影响的静态目录节点已单独修复并通过（3 passed / 4.79s）：`test_supply_allocation_migration.py`、`test_supply_capacity_migration.py` 的 `test_forward_catalog_matches_runtime_and_exact_predecessor`，以及 `test_return_receipt_routing_migration.py::test_exact_function_and_readiness_chain`。逐段核对 0177→0178→0179→0180 的冻结目录、函数和 readiness hash，保留原业务守卫断言；没有修改历史生产迁移。证据为 `artifacts/formal-0165-integration/ci-14d0731/static1-readiness-chain-repair-20261008.{log,xml,json}`。以上合计 359 个不同聚焦节点；下述诊断文本修复 5 节点是既有覆盖的复验，不另加计。

binding security 原始证据分别为 `artifacts/formal-0165-integration/c1-binding-security-focused-20261008.log` / `.xml`、`c1-binding-security-integration-repair-20261008.log` / `.xml`、`c1-binding-hash-atomicity-20261008.log` / `.xml`。首次失败保留，原因是测试使用不存在的 `RUNTIME_FUNCTIONS`，修为 `RUNTIME_EXECUTE_FUNCTIONS`；旧 57 项没有再整组重跑。

CI 协作任务的摘要为 `artifacts/formal-0165-integration/c1-head-readiness-current-20261008.json`、`c1-binding-security-focused-receipt-20261008.json`、`c1-binding-hash-atomicity-20261008.json`。摘要不能替代对应原始 log/XML。三组临时目录原始 log/XML 已复制到 `artifacts/formal-0165-integration/key-provider-c1-20261008/focused/`，摘要见 `focused-copy-index.json`。工具 stdout 的两组使用明确来源的摘要回执，没有补造原始日志/XML。

以下是已执行命令的记录，工作目录均为 `cloud_oam/`；仅用于归档，不是重跑指令：

```sh
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_openbao_registry_candidate.py --junitxml=/tmp/rsc-openbao-registry-c1-20261008.xml > /tmp/rsc-openbao-registry-c1-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_openbao_pin_reader_candidate.py --junitxml=/tmp/rsc-openbao-pin-reader-c1-20261008.xml > /tmp/rsc-openbao-pin-reader-c1-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_key_provider_scoped_probe.py --junitxml=/tmp/rsc-key-provider-scoped-probe-20261008.xml > /tmp/rsc-key-provider-scoped-probe-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_key_provider_readiness_security.py --junitxml=artifacts/formal-0165-integration/c1-readiness-20261008.xml > artifacts/formal-0165-integration/c1-readiness-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_pg16_loss_fixture_permissions.py -k historical_fixture_admission --junitxml=artifacts/formal-0165-integration/c1-head-fixture-admission-20261008.xml > artifacts/formal-0165-integration/c1-head-fixture-admission-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_alembic_migrations.py::test_pg16_gate_head_matches_alembic_graph backend/tests/test_alembic_migrations.py::test_revision_history_has_single_current_head --junitxml=artifacts/formal-0165-integration/c1-head-chain-20261008.xml > artifacts/formal-0165-integration/c1-head-chain-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_key_provider_binding_security.py --junitxml=artifacts/formal-0165-integration/c1-binding-security-focused-20261008.xml > artifacts/formal-0165-integration/c1-binding-security-focused-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_key_provider_binding_security.py::test_production_registration_keeps_bindings_read_only_and_functions_internal --junitxml=artifacts/formal-0165-integration/c1-binding-security-integration-repair-20261008.xml > artifacts/formal-0165-integration/c1-binding-security-integration-repair-20261008.log 2>&1
PYTHONPATH=backend .venv/bin/pytest -q backend/tests/test_key_provider_binding_security.py::test_register_nonstring_source_fails_hash_precomputation_without_partial_updates --junitxml=artifacts/formal-0165-integration/c1-binding-hash-atomicity-20261008.xml > artifacts/formal-0165-integration/c1-binding-hash-atomicity-20261008.log 2>&1
```

首次安全扫描发现 pin-reader 测试的合成诊断文本符合凭据格式。已替换为不含凭据语法的标记，并仅复跑受影响 5 节点：5 passed / 0.20s；原 83 项日志原样保留，未放宽扫描规则。修复日志/XML 已归档至同一 `focused/`，命令和新摘要见 `pin-reader-diagnostic-repair-receipt.json`。下表测试源摘要对应这次修复，其余源未变。

已验证的源码摘要（后续改动必须另绑证据，不沿用该摘要）为：

| 源码 | SHA-256 |
| --- | --- |
| `backend/app/openbao_registry_candidate.py` | `4a9786c8bd460d9c951907ae2ba4da899af5e7073a31e32280b691207611ee51` |
| `backend/tests/test_openbao_registry_candidate.py` | `7c713d78348a35c0db8bd5e100ff1b3c88b9b68b36b8a255660bc66e0301d673` |
| `backend/app/openbao_pin_reader_candidate.py` | `2a9ba7864ab93a26baa157ce162e7422ed39e5c2c12d7c927256c04263cbbd69` |
| `backend/tests/test_openbao_pin_reader_candidate.py` | `2b8265338d1829e16cb05762ca01829c7f84b7f46a4a22ab17a6f8bee9e206ab` |
| `backend/tests/test_key_provider_scoped_probe.py` | `ad65c4a11e3dff57ec8f2946c88aa8c0feffdf0b34753c19b5819915a8899a01` |

## 4. 真实隔离 PG16 终态

运行 `c1-db78c45e0e35` 使用实际 **PostgreSQL 16.15（160015）**。同机一次性 PG/API 容器均为 UID/GID 70、network none、只读 rootfs、全部 capability 删除、no-new-privileges、0.5 CPU、零 swap；PG 512 MiB/API 768 MiB。仅挂载本次新建的 source（只读）、私有 UNIX socket 和 evidence，数据库位于容器 tmpfs，不使用业务库、业务 volume、主机端口或生产配置。先核验镜像、容器归属、空数据库/角色和源码摘要，再执行迁移；API/备份/投影/接收身份直接登录，`star_oam_edge` 保持 NOLOGIN。

| 检查 | 终态证据 |
| --- | --- |
| 完整迁移和旧兼容 | 全链升级至 0179，合成旧 Aliyun pin 后升级 0180；provider/hash/created_at 精确回填。仅旧事实 downgrade 至 0179 保留 0040 pin，再升级 0180 精确重建，四次迁移日志及 prepare receipt 已保存。 |
| 完整数据库启动权限校验 | 真实 `star_oam_api` 调用未绕过的 `validate_production_database_security` 通过。模型导入使用显式 test Settings 和隔离 API socket URL，仅为导入前提；不代表生产 Settings、HTTP API readiness 或正式 provider 准入。 |
| 13 类目录/对应关系漂移 | API INSERT、backup UPDATE、PUBLIC SELECT、column UPDATE、禁用/缺失 trigger、额外函数重载、函数 EXECUTE、列 default、缺 check/unique、缺 claim、时间不匹配全部拒绝；每例仅事务内破坏，回滚后精确验证完整目录和对应关系。 |
| 旧镜像四类降级拒绝 | 无 OpenBao pin 时，missing/hash/time/orphan claim 分别调用真实 0180 downgrade；全部得到精确 blocker。使用真正在线 Alembic context，无 guard monkeypatch，每例回滚后完整事实、head、readiness 和 catalog 恢复。 |
| 权限和不可变性 | 12 非法坐标、2 孤儿 claim、2 顺序跨 provider 冲突、9 owner 更新/删除/清空全部拒绝；API/backup/projector/receiver 共 48 写拒绝、12 函数执行拒绝，读权限 6 正向/6 拒绝。角色升级和 replication 绕过拒绝，7 个 ALWAYS guards 保留。 |
| 真实并发 | 两个 provider 双向竞争，每方向覆盖先写方 COMMIT 和 ROLLBACK；4 组均通过 `pg_blocking_pids` 加共享 claims 表锁观测真实等待。8 次插入各只尝试一次，失败/回滚方无 pin 残留，最终精确 7 claims。 |
| DB pin reader + registry | 一次写入 auth `1_801_001` / contact `1_801_002`，两 pin/claim 精确回读；API 读两 pin 后通过 strict registry 与 mock transport 组合。错实例、缺版本、旧 Aliyun v1 和无读权限均拒绝；最终 9 claims。真实 PG 权限成立，解密明确是 mock，不证明真实 Transit 或双人审查来源。 |
| OpenBao 事实后的降级拒绝 | 真正 Alembic downgrade 失败且包含精确 blocker；head、全部事实和 catalog 原样保持。 |
| 最终只读回读 | REPEATABLE READ + READ ONLY 下 catalog 与 claims 一一对应；Aliyun pins=3、OpenBao pins=6、claims=9。 |
| 清理 | 10 个本次测试容器和上传目录已精确确认移除，原六个运行容器 ID/名称集合不变。下述停止异常不隐去。 |

原始证据目录：`artifacts/formal-0165-integration/key-provider-c1-20261008/`。`c1-db78c45e0e35-prepare-receipt.json`、各 phase 的 `*-controller.json`、`*-source-manifest.json`、`*-evidence/` 和 `c1-db78c45e0e35-cleanup-receipt.json` 分别保存执行边界、结果、源码、原始日志及清理回读。最终 source manifest 再次逐项核验；不是把工作树 HEAD 冒充纯净提交产物。PG 镜像固定为 `sha256:a2fd756045c06ff91dd78c5df4f55cdbfa3fc93f4cb0332d7330d656cee1966f`；依赖运行镜像为 `sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575`。

保留的失败和限制：

- 首次容器准备 `c1-e80ff434e1dc` 因镜像声明的额外匿名 VOLUME 被边界检查阻断，未启动、未迁移；准确移除该容器、其唯一匿名 volume 和临时目录后，才使用覆盖 tmpfs 的新实例。
- 首次 `startup` 因未给模型导入提供测试 Settings，出现 ValidationError；独立复核后只重跑该失败项 `startup-import-fixed`，原始失败保留，迁移未重跑。
- 停止 PG 时，镜像默认 SIGINT 到达 Python wrapper，容器退出码为 **130**。没有干净 PostgreSQL shutdown 日志，因此 `postgresCleanShutdownProven=false`；这不作备份/恢复证据。先准确回读十个容器均已停止、无 OOM 和原业务容器未变，再移除自建对象，**没有重放 stop**。今后该测试 wrapper 必须显式使用 SIGTERM 或处理 SIGINT。
- 上述停机异常随后用**全新空 PG16 容器**单独验证修复：显式 `--stop-signal SIGTERM` 使 wrapper 转发数据库 fast shutdown，观察到真实 `database system is shut down`，wrapper exit 0；自建容器和目录移除、原六个运行容器保持。证据为 `shutdown-signal-probe-output.jsonl` 和 `shutdown-signal-probe-receipt.json`（22:34:53）。未重跑迁移或业务门禁，**不回填原实例的干净关闭状态，也不替代备份恢复证明**。
- `prepare` 增加的 `-O` 拒绝在最初执行包生成后完成，实际运行是非优化解释器；新增本地优化反例在数据库动作前拒绝。对应源差异在两阶段 manifest 中保留。
- 某些本地测试首轮只有工具 stdout；明确标识证据来源，不补造 XML。合成测试不证明正式 key 的保管、离机恢复、RAM/STS、私有 OSS 或短信可用。

共享即时唯一索引对并发未提交冲突的等待规则见 [PostgreSQL 16 唯一性检查](https://www.postgresql.org/docs/16/index-unique-checks.html)；本次另有上述实际竞争回执，未只凭文档推断通过。

精确新提交的 hosted PG16/Client 仍独立待验收。旧 SHA `14d0731` 的 run `37772507579` 已于 22:45:36 结束，22:49:20（Asia/Shanghai）准确回读：**64 个成功 job、10 个失败测试分片及 1 个失败汇总门禁**，无运行中分片。static0 为 3791 passed / 5 skipped / 37 setup errors；static1 为 4058 passed / 4 skipped / 29 setup errors / 1 failed；static2 为 3621 passed / 7 skipped / 18 setup errors / 1 failed。合计 84 个 setup errors 均缺 `reader_tables`；另两个失败是旧目录/迁移前序断言，均已有对应修复和精确本地节点通过证据。原始通过、跳过和失败保持各自旧 SHA 归属，未重跑旧成功套件。static2 分类及文件摘要见 `ci-14d0731/static2-classification-20261008T1450.json`。

旧 run 终态后才正常推送新候选，避免取消原运行。新 SHA 的 hosted PG16 和 Client 结果必须重新独立验收；不能以本地或同机隔离 PG16 证据代替。已有 hosted PG16 运行事实保持，不能再笼统写成“没有 hosted DB”。

21 个直接消费夹具的测试模块显式补齐依赖，分别执行 setup-plan，覆盖 84 个唯一节点并包含上述全部 84 个错误；这是收集/依赖证明，**没有执行测试体，不能计作业务通过**。首次只选数量型、序列号型两个业务代表时，2 failed / 351.27s 暴露了 `authority._tables` 的隔离 reader 绑定泄漏到完整业务流程。修复仅在 `regional_source` 夹具中恢复收集时保存的真实解析函数，保留权限与断言；只重新运行这两项失败节点，**2 passed / 1350.49s / exit 0**；数量型 576.922s，序列号型 763.526s。原始 `decisions-quantity-serial-table-binding-repair.log` / `.xml`、`repair-receipt.json` 与 `final-source-manifest.json` 位于 `artifacts/formal-0165-integration/static0-reader-fixture-20261008/`；21 文件最终摘要全部回读一致。首次 2 failed 的原始日志/XML 保留。两项为业务代表复验，不声称 84 个节点或全部 hosted 分片已通过。

仓库安全扫描首次因合成诊断文本失败，修复后完整扫描 **PASS（3141 文件，76631641 bytes）**；后续候选文件和最终文档增量按同一规则验证，日志及精确文件摘要见 `repository-safety-delta.log` / `repository-safety-delta-receipt.json` 与 `repository-safety-final-docs.log` / `repository-safety-final-docs-receipt.json`。扫描证据位于 `key-provider-c1-20261008/repository-safety-repair.log`。旧 CI 终态后的三份交接更新另经同一安全规则验证，见 `repository-safety-terminal-handoff.log` 与同名 receipt。当前 head 短信源码契约通过且明确 `releaseDecision=not_ready`；这不是发送或验证真实短信。

## 5. C2 的具体待办

1. **联系人新信封和双读。** 新增明确版本/provider 的 contact envelope；旧 Aliyun v1 九字段、旧 AAD、当前申请与不可变 revision 的历史读取全部保留。新业务 AAD 与 wrap AAD 分开，provider/环境/实例/应用版本的绑定不能靠替换字符串完成。严格解析拒绝混合/额外字段；未知 provider、错误历史版本与不可用旧 key 均失败关闭。通过新的精确前序迁移扩展相关 SQL 守卫，禁止改写冻结 0029/0069/0168 或覆盖历史 revision。
2. **认证重放的 provider 分流。** 现有 auth replay 只有 `encryption_key_version`，须依据同用途全局唯一 claims 显式选择 Aliyun/OpenBao；旧未过期 replay 保持可读。禁止新 provider 复用旧应用版本、latest fallback 或伪造 Aliyun 回执。切换前分别证明旧引用扫描、存量读、轮换/并发/未知 provider/失败恢复与回滚边界；不得由 C1 表存在直接推断兼容完成。
3. **Settings/factory/readiness 显式注册。** 增加受控 provider 配置与工厂 admission；保留旧路径。完整坐标必须区分 provider、purpose、environment、provider instance、key path、application version，并匹配真实 Transit version 和不可变 ciphertext pin。独立验证两 provider 历史引用/readiness，禁止共用不完整缓存键、duck-typing 伪装为旧 KMS 或读取失败后的默认降级。
4. **pins 审查、登记与恢复。** 定义独立审查清单、受控迁移身份登记、只追加审计及可验证的双人复核来源；不能由 registry 自证自己的 expected pin。正式密钥先完成独立备份/恢复、旧版本可读和撤销/封存验证，再讨论 active。新事实存在时用前向恢复，不靠降级丢弃 pins/claims。
5. **真实运行身份与 OpenBao 服务。** 将已有隔离原型转为经审查的部署方案，补服务身份最小权限、token 续期/到期/撤销、TLS/Unix socket 归属、总超时、严格解码、无重定向/重试、日志脱敏；补公网 TLS/JWKS、受控签名身份、RAM/STS 的真实联邦验收。生产持久化、离机解封份额、灾备和轮换与恢复的证据独立保存；不复用原型 root token。
6. **私有 OSS。** 在已授权实际 Bucket 上取得私有 ACL、BPA、SSE-OSS、版本控制状态及最小前缀权限的真实只读证据，处理锁定 SDK 的 XML 兼容边界；随后按明确范围验收附件上传、读取、删除/防覆盖契约。纸面方案和合成 SDK 测试不能代替实际资源验收。
7. **首发短信、部署及 UAT。** 真实 PNVS/SMS 无密码登录属于试点首发必需；补正式人员映射、会话/幂等/限流/审计、真实登录与恢复验收。新版 HTTPS/API readiness、正确小程序 API 绑定、角色/真机主链路 UAT、备份恢复与回滚分别验收。业务短信/微信/飞书通知与投递运维仍为后续迭代。
8. **准确候选门禁。** C1 终态归档和审查后集中提交；新 SHA 的 hosted PG16/Client 结果按原始终态登记，避免连续推送取消同版运行。不得重跑未修改且已终态的大套件来凑通过数量，不关闭失败断言，不用局部静态结果替代正式门禁。

### C2 第一片已核实的最小落点（尚未实施）

- `backend/app/formal_services/material_request_contact.py`：`protect_material_request_contact`、`reveal_material_request_contact`、`validate_material_request_contact_envelope`、`_contact_aad`。以明确 `(schema, provider)` 分流并保留旧 v1 字节契约；第一片使用显式注入的候选 cipher，不放开生产开关。
- `backend/app/formal_services/material_request_draft.py`：`_validate_draft` 和 `_validate_draft_references` 各有一处 v1 AAD 硬编码，必须同步采用版本感知的绑定验证。`material_request_edit.py` 的本人权限、脱敏核对及解密后 revision 再核对必须保留。
- `backend/app/production_adapters.py`：`_MaterialRequestContactCipherRing` 与 `validate_persisted_kms_key_references` 是后续实际接入前的依赖；当前扫描只认旧 `kms_key_id/key_version`，不能跳过当前申请或历史 revision，也不能直接放开 Settings/factory。
- 新前向迁移必须接精确 `20261229_0180`。当前 request identity guard 的有效源来自 **0168 after**，SHA-256 `7de21a51296307dbf2fce2b155bf05c5477d54d500375639d58ca318a686c0c8`；revision guard 仍为 0029，SHA-256 `ea718216fbf47320670b171a4aca22019fdbf4540533742aac6ac7573b31ab83`。readiness 为0180，SHA-256 `312196ece062b180451184dae65c85d8de1695a8060ef8322e0bb5d81ad8c0a1`。实施前须重新核对，不回用旧0029模板覆盖累积守卫；SQLite 四个 insert/update 守卫同样保留0069/0168附加逻辑。
- 旧 v1 精确九字段与历史 key 读取不变。v2 需绑定 provider、purpose、environment、provider instance、key path、application version 以及 request/person，明确区分业务 AAD 与 wrap AAD；Transit version 不冒充应用版本。v2 的 contact pin/claim 必须精确匹配，任一当前申请或历史 revision 出现 v2 后禁止降级。
- 只新增相应聚焦组：v1 固定向量；v2 合成往返与坐标篡改；严格形状/编码；v2 当前申请和 v1 revision 混合历史；本人读取/脱敏/revision 复核；精确前序、漂移、SQL 接纳/拒绝与 v2 降级守卫。未修改的已终态套件不重跑。

以上来自本轮只读代码梳理；不算 C2 实现或验收通过。C1 的独立静态审查未发现阻断缺陷，原始范围与源码绑定见 `key-provider-c1-20261008/independent-review-receipt.json`；它不替代真实 provider、审查来源或发布门禁。

## 6. 试点业务范围保持冻结

主链路仅为：**申请/提交 → 审批 → 最小货源分配/占用 → 后台人工履约并记录发运 → 本人收货 → 个人仓入账**。技术员仅保留申请、状态查看、本人收货和本人入账；区域/总部保留必要后台人工履约。拣货及后置区块继续按 capability/角色整体隐藏，底层状态、迁移、契约、出库依赖保留。

不可变库存流水、幂等、审计、权限、安全约束、取消/关闭守卫和最小站内通知不能因缩减范围而放宽。审批、分配、占用、出库、发运、物流签收、OAM 收货、个人仓入账、通知和对账继续独立记录。

后续迭代清单明确保留：

- 履约后供给容量、0178 分配后建计划、释放后重分配和复杂历史恢复。
- 拒收/退回补偿及 stock-return 全链。
- 工单物料消耗、替换、冲销及旧坏件回收。
- 报损报废、成色纠正。
- 盘点、期初导入、冻结、复盘；首发实际库存仍必须有可核验的来源/期初事实。
- 人员调拨、离职交接。
- 复杂报表、Excel、打印。
- 业务短信/微信/飞书真实通知渠道及投递运维。

本文件不扩大业务写入授权，不改变公开查询首页及后台入口约定；主交接与正式验收审计同步链接本文件，候选提交与正式上线分别验收。文档与回执不得包含个人 home 路径、SSH 配置或原始凭据。
