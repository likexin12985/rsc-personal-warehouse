# 旧 CI 静态失败的增量修复

范围仍为**试点 MVP，不等同完整 V1**。本批修复历史测试、夹具与 CI 运行条件，不开放任何后置功能，不修改冻结 SQL、生产角色、权限或业务守卫。

## 失败来源

旧提交 `27ca13e10bf36165b19341a9aac9679df22e0e8c` 的 PG16 run `37746456944` 已终态失败。三组静态测试共报告 45 个失败节点、12 个夹具错误；分别为 30 failed/3796 passed/5 skipped、6 failed/4051 passed/4 skipped/12 errors、9 failed/3619 passed/7 skipped。此前的运行中状态已过期。完整失败节点与日志摘要在 `artifacts/formal-0165-integration/static-failures-37746456944.json`，不能把旧成功项计为新候选通过。

`8ad7216` 已修复已知迁移头反例；`094f01766563b7d6ef1a866d2e499599af3d7e72` 完成 PNVS 脱敏预检、候选镜像基础摘要固定和部署材料。下面改动在这两个提交之后。

## 修复边界

- 历史 0037 ACL 的精确对比补入 0168 合法新增的 shipment_status；历史 ACL 本身不增加该列，也不授权其他列。
- 历史函数指纹、readiness 与完整目录从冻结版本沿真实后继迁移逐步验证。新增测试工具只接受完整 before 对象吻合后的 after；正文/ACL 不符直接失败，不从当前 DATA 反推预期。原始目录、前驱、键集合和完整对象相等断言保留。
- 合成迁移链单测隔离真实目录搜索，防止测试用的短版本号误匹配仓库真实迁移后缀。
- `AddConstraint` 会给传入约束安装 `_create_rule`。四份历史迁移测试现在编译浅拷贝，避免修改 Base 共享约束、污染后续 schema 对比；后续完整 metadata 对比继续执行。
- 附件/权限读取单测原本刻意使用部分纠正事实，现在给这些夹具独立表名；真实身份、权限、来源、保管和文件完成状态查询保留。正式纠正表、必填列、外键和 PG16 写守卫不改。这些测试不能作为完整纠正业务或提交围栏证据。
- 0106/0110 的独立历史数据库夹具使用明确的成色纠正之前的结构视图，避免 0167 外键引用尚未安装的组合唯一键。0165 schema/行包对比也使用相应历史视图；不删除当前模型或底层依赖。
- 原生 decision-seal 安装测试保留二进制版本、私有 Unix socket、新库/角色、目录/PID 身份和退出回执校验。CI 安装 PostgreSQL 16 服务端并显式传递 `/usr/lib/postgresql/16/bin`，不再依赖某台 Mac 的 artifacts 绝对目录；没有 skip 或 continue-on-error。该原生测试仍需准确新 SHA 的 CI 实跑。

## 验证与接续

旧失败的本地定位先得到 8 failed / 2 passed，保留 `/tmp/rsc-static-incremental-current.log`；未将后续修复混算进原结果。修复后已完成：

- 附件和权限读取夹具：53 passed / 1 warning / 181.06s / exit 0，`/tmp/rsc-reader-fixture-repair.log`。
- 冻结目录推进的正文/ACL 漂移与不可变输入：3 passed / 1.28s / exit 0。
- 目录/历史指纹/共享 metadata 连续对比/CI 拓扑：81 passed / 1 warning / 344.78s / exit 0，`/tmp/rsc-static-repaired-catalog-v2.log`。
- 历史入库/保留守卫/报废行包/0165 结构迁移：10 passed / 1 warning / 398.37s / exit 0，`/tmp/rsc-historical-fixture-repair.log`。
- 曾有两次命令路径/测试节点名错误，未运行测试；纠正后才启动验证，不视为业务测试失败或通过。没有重跑已终态的完整套件。
- 本批 147 项聚焦验证通过，不能替代新 SHA 的完整静态安全、原生安装和托管 PG16 事务门禁。

验证通过后集中提交，一次正常推送当前分支获取新 SHA 的 Client/PG16 结果。生产切换仍受真实运行身份、KMS/OSS、数据库/权限、PNVS、人员/UAT、TLS/API readiness、备份恢复与回滚门禁约束。候选镜像仅完成构建和离线核验，详见 `PILOT_CANDIDATE_BUILD_20261008.md`。
