# 当前候选八角色容器备份恢复方案

2026-09-22；方案与源码只读审计，不代表容器或生产恢复已经通过。已复核完整正式 V1.0 基线。
审计时主任务完整静态会话 6070 活跃；1,472 个非 Markdown 输入冻结，不修改这些输入。
候选清单：[source-manifest.json](../artifacts/full-static-release-20260922-binding-fix/source-manifest.json)，
SHA256 `f4b1aea94c9afece55ef14ff0718f1c5a5b2296fd292c5f8ce9c8c1cccf8b65f`。

## 已有证据与必须重验的部分

| 证据 | 可复用结论 | 不能据此宣称 |
| --- | --- | --- |
| [8.00 构建](BACKUP_CONTAINER_BUILD_20260921.md) | 正式 Dockerfile、离线依赖闭包与 Linux ARM64 构建方法已有证据 | 当前 API 镜像已构建 |
| [8.01 容器链](BACKUP_CONTAINER_E2E_20260921.md) | 实际 Docker/Compose、五个成功/失败场景、206 表/572 行、三个对象/一个意图；源目录实际HEAD为0129 | 当前 schema、八角色恢复、真实 OSS 或 production 启动通过 |
| [8.04 原生恢复](DAILY_CAPTURE_RESTORE_20260921.md) | 0130 的八角色新凭据恢复、208 表/934 行、禁止旧凭据、角色/ACL/RLS 核验 | 当前0134的完整容器对象链已通过 |
| [当前角色实现](../backend/app/daily_reconciliation/capture_provisioning.py)7 行 | 当前固定 HEAD 是 `20261113_0134` | 可以直接运行旧0130断言的 runner |

本次只读摘要比较：8.00 的297个构建输入已有10项变化，另有33个新增 app 模块未被旧清单覆盖；
8.01 的334个输入已有14项变化。变更包含 `database_security.py`、认证/SMS、文件服务及
`formal_file_integrity.py`。**当前 API 必须重建，不能通过新挂载替换旧镜像内部业务代码。**

## 可靠复用边界

| 工件/入口 | 精确位置 | 复用条件 |
| --- | --- | --- |
| 当前API构建方法 | [build_api_image.py](../artifacts/backup-container-chain-20260921/build_api_image.py) | 派生到新目录，更新候选清单和唯一标签；不覆盖旧证据或复用旧source.tar |
| 离线Python与依赖 | `artifacts/backup-container-chain-20260921/python-arm64.oci.tar`、`verified-packages.tar`、`wheels-verification.json` | 本次70个wheel摘要全部一致，requirements仍匹配；执行前重验base/平台/包摘要。仅适用已验证CPython3.12/Linux ARM64，不代替目标x86镜像 |
| DB镜像 | `rsc-backup-interpreter-check:20260921` | 当前DB Dockerfile与8.01绑定摘要相同；运行时复核 manifest `sha256:b0d210974e21e68b0b6c4b44acb7e4a005f07c416b75b7a6a900a2f41b54c311`、config `sha256:bbecd3313f6db88f3f5a7bcc35703d52793a9508a0a2365455cda63cc7e37b6b`，不凭标签 |
| DB监督器 | [local_pg16_cluster.py](../backend/tests/local_pg16_cluster.py) | 原实现建立全新目录/socket/子进程/system ID；测试依赖连版本元数据打包，不在正式镜像装测试库 |
| 私有Docker引擎 | [run_chain.py](../artifacts/backup-container-e2e-20260921/run_chain.py)、[guest_engine.py](../artifacts/oss-backup-docker-20260921/guest_engine.py) | 只复用方法；每轮新daemon目录/socket，独占自有VM，不调用默认Docker上下文 |
| 容器故障与恢复夹具 | [guest_chain.py](../artifacts/backup-container-e2e-20260921/guest_chain.py)、[db_owner.py](../artifacts/backup-container-e2e-20260921/db_owner.py) | 派生新版；移除旧镜像、旧SQL及206/572硬编码，补八角色分阶段恢复 |
| 八角色恢复用例 | [native_restore_cases.py](../artifacts/daily-capture-restore-20260921/native_restore_cases.py) | 复用阶段/新密码/权限检查规则；旧0130输入和预期结果不能直接运行 |

VM登记在 `artifacts/caddy-container-20260921/owned-vm.json`，历史记录为
`LIMA_HOME=/private/tmp/rsc-caddy-vm-25_4tngo`、`caddy-check`、32GiB磁盘、Stopped。
这是文件记录，审计未调用VM状态命令，不能宣称当前实际停止。后续执行者必须核对独占归属、
只读stage挂载、无SSH agent转发和无既有containerd任务；任何不一致先停止该验证。
VM由主任务指定的镜像构建代理独占，本文不启动、停止或改配置。

旧 runner 会写旧目录结果和 VM metadata，不能原地执行。全部新夹具/日志使用
`artifacts/current-container-recovery-20260922/`，不修改8.00/8.01/8.04失败或通过证据。
六个正式backup worker文件当前与8.01一致，但它们导入的共享模块已变化，仍需随新API镜像重验。

## 最小源样本路线

主任务可在全新自有原生PG16中构造当前样本；不加载任何真实业务数据库或秘密：

1. 验证8.01引用的旧合成SQL、206表/572行清单和对象字节来源，不能从正在运行的旧库导出。
   文件指针为 `artifacts/backup-integration-20260921/verified-output/database.sql`、
   `facts-restored.json`，以及 `artifacts/oss-joint-backup-20260921/source-checks/run-pnfptk44/`
   下的 `uploads.tar.gz` 和 `local-object-fixture/`。旧归档成员摘要仍需执行前精确复核。
2. `native_cluster`只建立空目标与六个固定基础角色；用单事务加载旧SQL并复核原内容摘要。
3. 正式Alembic升到0134，再以正式`provision_capture_roles`配置两个独立只读账号。
   调用当前API、edge_inbox、projector、capture权限验证，不关闭RLS/触发器或放宽守卫。
4. 用当前正式 [backup_database.sh](../deployment/backup/backup_database.sh) 导出
   `database.sql/files.ndjson/snapshot.json`，重新记录当前全表/序列、迁移HEAD、文件状态与对象摘要。
5. 三个available对象仍使用原真实合成字节；一个pending intent保持pending，不补造对象。
   对象目录完整核验后作为只读夹具；当前 `formal_file_integrity.py` 是额外最终判据，失败即阻断。

新源最终通过目录：`artifacts/current-container-source-20260922-attempt3/`，212表/579行。
前两轮失败结果与停止证据保留；第三轮退出0且原生集群已停止，不能把前轮失败改写成通过。
最终数量从该源回执读取，不能仍写206/572或旧208/934。
这个最小样本的新增日终事实表为空；结论仅覆盖**当前schema、八角色、旧业务事实和对象链**，
不声称恢复了非空日终审核、附件或请求封存。后续可用当前日终PG16夹具建立独立非空补充场景。

## 新容器的执行顺序

源容器A和最终恢复容器B都必须按同一八角色流程，不在DB镜像临时安装正式应用：

1. DB容器用原helper创建全新实例及六个基础角色，发布受控socket就绪barrier；此时不加载SQL。
2. 当前API镜像通过只读挂载的正式 `scripts/configure_daily_capture_roles.py` 执行
   `prepare-restore`，输入本次精确备份SHA和新的restore UUID；两个capture账号保持NOLOGIN/无密码。
3. 预备成功后才写允许加载barrier；DB owner用`psql -X -w --set=ON_ERROR_STOP=1
   --single-transaction`导入。仅在SQL确实含`CREATE SCHEMA public`时才在该新目标内对应处理schema。
4. 当前API镜像执行`check-restore`，必须为`restored_disabled`；精确复核全部表/序列/对象。
5. 七个登录角色使用本次独立随机合成密码，恢复库与源库不同；正式CLI完成capture账号的
   `activate-restore`。旧密码必须失败，新密码必须成功，`star_oam_edge`一直NOLOGIN。
6. 当前API镜像以实际各角色凭据重新验证完整权限和`current_user/session_user`；不能只查角色名。
7. A启动原Uvicorn命令及ready检查，运行当前逐字节正式`backup.sh/backup_steps.sh`、数据库导出、
   旧附件导出与OSS worker；仅测试hook替换OSS transport和故障停顿，正常dump仍为真实pg_dump。
8. 核验新完整联合包并恢复至B，重新走上述阶段，核对数据库、对象、库存/SN/审计与新凭据。
   B从新联合包恢复，不从原fixture直接加载。完成后只清理准确本轮资源并保留失败证据。

DB镜像的纯Python测试依赖只够实例helper；正式角色CLI/权限代码在**当前API镜像**运行，
不把新业务源挂载进旧API。测试配置明确为`OAM_ENVIRONMENT=test`，不把ready=200称作
production KMS/身份启动通过。所有服务无网络、无发布端口，只用私有Unix socket与受控挂载。
临时合成密码不进入argv、日志、结果JSON、归档；不查看或复制真实凭据。

## 当前候选验收表

下列是本轮必须取得的证据，文档写成时尚未执行完成：

| 检查 | 通过标准 | 当前状态 |
| --- | --- | --- |
| 候选绑定 | 全部冻结输入前后不变；API build输入包含新增模块；actual image ID与manifest匹配 | 新API由独立代理构建，等待终端回执 |
| 原生当前源 | 旧样本先精确验证、正式升0134、八角色权限通过、当前正式导出 | attempt3已通过并停库；源未验证七个密码的SCRAM登录 |
| A/B空目标恢复 | prepared→单事务加载→restored_disabled→事实验证→active；不同system ID | 待容器验收 |
| 八角色凭据 | 七组新密码通过、旧密码拒绝、edge NOLOGIN、重复activate不改密 | 待容器验收 |
| 数据与对象 | 当前动态表/行/序列清单完全一致；3对象字节/元数据一致；1意图不变 | 待容器验收 |
| 正常完整备份 | 正式入口退出0；独占发布一个包；SQL/目录同一快照；源事实不变 | 待容器验收 |
| 4类容器故障 | DB超时、主动取消、父进程SIGKILL、对象字节损坏均不发布新完整包 | 待容器验收 |
| 清理与服务隔离 | 备份会话/关系锁/临时worker归零；原API/DB ID及启动时间不变；准确本轮实例停止 | 待容器验收 |
| 失败恢复 | 错SHA/restore UUID、不完整SQL、角色/RLS漂移拒绝；损坏SQL无部分schema/数据 | 至少复测最小负向集 |
| 新增日终非空数据 | 独立当前日终夹具的截止/解释/审核/封存恢复与原证明 | 本最小路线不覆盖，明确保留 |
| 真实生产恢复 | OSS专用只读身份、KMS/对象一致性、production启动、异地/PITR、RPO/RTO | 未验证，不能由合成容器结果替代 |

最终回执应绑定：候选清单及摘要、三个数据库的system ID/启停证据、实际API/DB镜像摘要、
当前源/联合包/SQL/对象摘要、全部角色验证、故障退出码与清理结果。
失败原样留存；不通过更改预期摘要、丢对象、禁用守卫或复启旧数据目录消除失败。
正式上线仍需基线规定的授权、演练及书面验收；本地小样本耗时不是生产RPO/RTO。
