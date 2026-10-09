## 2026-10-09 最新接续：提供方运行入口已接通，部署未放行

**试点 MVP，不等同完整 V1；`not_ready`，仍未上线。** 本批已经把独立 provider claims/pins 和存量引用检查接入 Settings、认证/联系人请求工厂、API 启动、readiness 与 CLI。九项 OpenBao 非密配置由 API/gate 同源映射，默认仍关闭；请求只使用启动审核后的不可变快照，不跨请求缓存明文，不新增短信限流前的数据库事务。全部启动门禁通过后才发布 runtime；缺失/漂移/不可解仍失败关闭。

完整实现、逐阶段本地/本机 PG16 证据、失败保留、联系人预检补修与受控停机换钥限制见 [运行入口接线交接](PROVIDER_RUNTIME_WIRING_20261009.md)。本批不代表真实 Linux 身份/挂载、Transit/STS/OSS、0181 hosted PG16、SMS-only/UAT、恢复或部署已通过；未提交、推送或变更生产。后文“未接线”等旧记录是各时点历史，当前代码状态以本节和新交接为准。

# 2026-10-09 上线部署核查

**试点 MVP，不等同完整 V1；发布判定为 `not_ready`，未执行生产迁移或流量切换。** 用户本轮已明确要求上线部署，目标仍为已确认的杭州轻量服务器 `118.31.37.87`。阻塞来自当前候选和运行配置，不是缺少部署授权或需要重新登录。

> **历史观测边界（2026-10-09 接续）**：下方“本轮准确输入与现场证据”和“服务器与公开入口”记录的是 00:13–00:25 的历史只读观测；其中“开始核查时工作树干净”只描述当时输入。后续候选变更及证据以 [持续开发交接](CONTINUE_DEVELOPMENT.md) 的最新接续段为准。本文旧 PG16 快照须按观测时间读取，不保证运行仍在进行，也不覆盖未提交改动。C2 保持默认关闭、未接入生产工厂，发布结论仍为 `not_ready`。

## 本轮准确输入与现场证据

- 工作树 `06f6/oam`，分支 `codex/notification-delivery-worker`。开始核查时工作树干净，候选为 `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3`。后续本地修复不能冒充该 SHA 的 CI 结果。
- 2026-10-09 00:13（Asia/Shanghai）核对系统代理后，通过本机 HTTP/HTTPS 代理 `127.0.0.1:11304` 读取 GitHub；没有改用直连、重新登录或重跑 workflow。
- [客户端 run 37805075684](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37805075684) 对应上述 SHA，已 `completed/success`。
- [PG16 run 37805075682](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/37805075682) 对应同一 SHA，00:13 快照为 74 个作业：14 成功、2 失败、20 运行、38 排队；run 尚未终态。不能发布，也不能继续记为“缺 hosted DB”。
- 两项已读取的新失败为 quantity/review_seals 和 serial/submission，均停在 `pg16_stock_loss_seal_gate.py` 的 `generic_key_races` 等待；具体根因与修复另附后续证据。没有因看到 `TimeoutError` 就认定是网络、资源不足或登录失效。

00:19 再次回读 PG16：23 成功、2 失败、20 运行、29 排队，74 个作业均已列出；两个失败仍为同一组，整个 run 无终态。00:25 的最后回读为32成功、2失败、20运行、20排队，无新失败；对应 `ci-final-summary-20261008T162520Z.json`。这些快照只更新进度，不构成门禁通过。

本轮原始回读保存在 `artifacts/deployment-20261009/`：`ci-snapshot-20261008T161338Z.json`、两份 job 原始/clean 日志、`server-readonly-snapshot.json`、`target-config-and-https.json`。`deployment-observation.json` 绑定各回读文件摘要与 `not_ready` 判定。该目录为本地证据目录，不包含生产秘密，不作为已上线回执。

## 服务器与公开入口

00:13–00:14 通过现有 SSH 配置只读回读：原六个容器的 ID/名称与前次证据一致，均 running，带健康检查的五个均 healthy；旧 Web 仍占用 80/443。主机可用内存约 2.0 GiB、空闲磁盘约 32.3 GiB。容量快照不证明新服务负载或恢复能力。

`https://rscwz.cn/api/auth/login-options` 实际返回 `sms_enabled=false`、`password_enabled=true`；`/api/health/live` 和 `/api/health/ready` 均为 404。域名当前仍是旧 API，不符合新试点的 SMS-only 与 readiness 要求。`/`、`/xx/` 返回 HTML 200，仅证明路由可达；未执行浏览器页面验收，不据 HTML 字符串缺失判定页面最终内容。

旧 API 未设置本次检查的 OIDC role/provider/token-file 与 credential URI。首轮 KMS/OSS 检查键名与现行源码不一致，因此该部分不作为证据；00:18 按现行 Compose 重新检查，`OAM_KMS_ENCRYPTED_DATA_KEY_REGISTRY_PATH/FILE`、`OAM_FILE_STORAGE_BUCKET/REGION` 及映射后的 `OSS_ACCESS_KEY_ID/SECRET/SECURITY_TOKEN` 均未设置，`OAM_SMS_PROVIDER=disabled`。补充回执为 `current-config-presence.json`；只记录有无和允许公开的 provider 枚举，没有输出秘密，也没有据此推断所有可能的配置文件均不存在。服务器上仍存在旧候选草稿 `/opt/rsc-build-20261008-8ad7216/pilot.settings.draft.env`（0600），它不是 041cb69 的可发布配置。旧候选镜像也不能改标签后当作本次源码的发布产物。

## 仍需完成的上线条件

1. **同一候选的完整 PG16 门禁。** 保留已失败日志，只修复确认的问题；不取消正在运行的 CI、不重跑未修改且已终态的大套件。任何新修复都须绑定新的候选与独立验证结果。
2. **低成本密钥方案实际接入。** C1 已完成不可变 provider pins/claims、严格 registry 和候选读取；C2 已新增联系人新信封/旧信封双读、认证 replay 分流及默认关闭的 OpenBao 组合，并已通过本地聚焦证据。当前组合仍未接入 Settings/main/生产工厂，真实运行 attestation、数据库身份/ACL、投影 token、Transit/STS、私有 OSS 和 hosted PG16 尚未验证；当前 Aliyun 路径仍是唯一启用的生产路径。不能把 OpenBao 组合测试或 0180 建表成功当作应用已经接入或可上线。
3. **真实运行配置与首发登录。** 补受控运行身份及续期、私有 OSS 与动态凭据适配、正式人员唯一映射和真实短信登录证据。SMS-only 登录不在延期的业务通知范围内；不启用密码或静态凭据兜底。
4. **发布与恢复验收。** 精确候选镜像、独立配置与数据库身份、密钥备份/离机恢复材料、数据库/附件恢复、HTTPS/API 和角色/真机主链路 UAT 均需实际证据。仅通过配置预检不能宣告上线。

具体兼容落点见 [C1/C2 交接](OPENBAO_C1_BINDING_EVIDENCE_20261008.md#5-c2-的具体待办)，低成本部署边界见 [部署方案](LOW_COST_PILOT_DEPLOYMENT_20261008.md)。没有替用户选择未确认的收费资源、实际密钥保管人或离机恢复位置。

## 后续执行顺序与防误操作

先补代码及真实配置依赖，再执行 `pilot_release.py` 的 prepare/start 流程，保留来源、镜像、迁移、pin gate 和准备回执的绑定。不能为了接管端口绕过守卫，也不能用手写回执代替 prepare。

备份前必须明确试点 Compose project、Compose file、env file 和独立备份目录。当前旧备份脚本中有裸 `docker compose` 调用及默认 `/opt/star-oam/backups`，还带留存清理；**不得直接沿用默认值在试点上运行**。先核验确切目标和恢复方法，再操作，避免影响旧服务备份。数据回滚不能删除已发生的库存或密钥事实。

有限业务链保持申请/提交→审批→最小分配/占用→后台人工履约并记录发运→本人收货→个人仓入账。拣货及全部已冻结后置区块保持隐藏；底层状态、迁移、契约与出库依赖保留。后续迭代清单和正式 V1 差距继续沿用正式审计，不借部署扩大业务范围。

## 本轮 PG16 失败调查与待验改动

两项原始失败日志只记录 `future.result(timeout=10)`，没有 `40P01`、数据库锁超时或具体等待 PID，不能凭错误名称判定网络失败或 PostgreSQL 死锁。随后独立源码审查确认测试意图与当前迁移冲突：0167 安装的 `rsc_condition_request_key_lock()` 对 `shipments`、`receipts` 的 BEFORE INSERT 无条件锁定 `inventory_ledger_heads`，旧夹具却先由 leader 持有该锁，再要求整个 probe INSERT 在锁释放前完成。

冻结依据为 `backend/alembic/return_condition_0167/catalog.json` 的两表触发器及函数 `after.prosrc`；可读 SQL 见 `backend/alembic/return_condition_candidate/request_keys.sql`。这些生产定义和库存锁不作修改。

第一版“先 flush probe，再让 leader 持库存锁”的夹具候选被独立审查拒绝：probe 已持有同一库存锁，会造成反向等待。该候选和局部协调测试结果保留为 rejected 证据；局部测试通过未证明真实 PostgreSQL 锁图正确，不能计作门禁通过。

待验更正把两种证明分开：完整 seal-first 竞态检查真实库存锁等待及 seal 提交后的精确拒绝；0146 独立键锁检查由 leader 只持精确 advisory key，验证同键等待、异键可执行，临时 probe 全部回滚。新增阶段/PID/阻塞关系诊断不包含业务 SQL 参数。任何局部通过都不能代替更正后的真实 PG16 证据；本轮不据此提交发布候选或切换生产。

当前更正已落本地，新增分支聚焦 **4 passed / 8 deselected / 3.18s**，AST 与 diff 检查通过；真实 PG16 未运行，未提交或推送。测试与冻结 SQL 摘要见 `artifacts/deployment-20261009/loss-key-probe-phase-b-receipt.json`，失败分类见同目录 `ci-041cb6974-loss-key-classification.json`。原8项协调结果属于被拒绝候选，单独留存，不与本轮4项合并成门禁通过。

旧候选 `37805075682` 的迁移失败已完成只读分类：失败位置是 `_assert_0061_empty_event_key_downgrade_and_reupgrade` 的固定 hash 变化集合，索引 3 对应的 `rsc_guard_material_request_supply_task_0059()` 在 0177/0178 中有合法链式变化（`913d60 → 7f6258 → 32010b`），而测试只允许索引 0、1、4。日志中的 0092/0093 drift/permission 错误来自门禁的负向注入检查；四个 loss 分片仍是旧夹具等待超时。当前仅把测试允许集合扩为 `(0, 1, 3, 4)`，并完成收集/语法检查；没有改生产迁移或重跑旧 run，修复须随新候选取得 hosted PG16 证据后才能重新判断。分类回执为 `artifacts/deployment-20261009/pg16-historical-hash-classification.json`。

## 2026-10-09 hosted PG16 run 最新只读回读

对同一 run `37805075682` 通过 GitHub 代理再次执行只读 `gh run view`，保存回执 `artifacts/deployment-20261009/pg16-run-37805075682-live-20261008T184722Z.json`。该 run 仍绑定 HEAD `041cb6974d20d352e1a5149701fa2e9ab4d4b3e3`，状态仍为 `in_progress`，阶段计数为 **67 completed/success、6 completed/failure、1 in_progress**；唯一未完成作业为 `static_safety (0)`，失败项仍为 inventory、migrations 和四个 loss 分片。未取消、未重跑、未推送、未部署；该历史 run 只验证其 checkout 的已提交 HEAD，不覆盖当前工作树未提交改动；不能替代工作树稳定后的新候选终态门禁。
