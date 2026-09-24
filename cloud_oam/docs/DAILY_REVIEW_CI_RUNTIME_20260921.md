# 日终对账 PG16 runtime 接线与空库证据（2026-09-21）

本批把可复用日终审核门禁接入 `cloud_oam/backend/tests/test_postgresql16_release_gate.py` 的正式 runtime 流程。接线位置在既有迁移、权限、保留边界和 opening seal 检查之后、API/投影/边缘引擎释放之前；新增检查不会跳过已有检查，也不会改变生产数据库或外部来源。

## 本地空库证据

使用 `backend/tests/local_pg16_cluster.py` 新建、未恢复归档的 PostgreSQL 16.0.15 集群，先从空库执行 Alembic `upgrade head`，再配置边缘 staging 和合成工单来源，最后调用与 CI 共用的 `pg16_daily_review_runtime.run_with_capture_roles`。捕获角色由管理员连接先验证不存在，再以随机密码创建；测试使用两个只读角色连接，不写入真实来源、飞书、OSS 或生产库。

可复核工件：`artifacts/daily-review-ci-runtime-20260921/`。

- `result.json`：`status=passed`、`emptyDatabase=true`、`restoredArchive=false`、`githubReleaseGate=false`、`clusterStopped=true`。
- PostgreSQL 16 身份：`serverVersionNum=160015`；本次集群目录为 `native-checks/run-5nrxyj4a`，状态为 `stopped`。
- 迁移顺序：`upgrade head` 成功；对 `20261108_0129`、`20261110_0131`、`20261111_0132`、`20261112_0133` 的四次非空 downgrade 均按预期失败，失败后事实摘要保持不变。
- 12 个场景全部通过：来源发布/总部映射/三份真实截止、0130/0132/0133/0134 分层回退拦截、当前 JWT 与幂等恢复、附件意图与未绑定下载隔离、部分解释与独立审核、永久终结、提交结果丢失后的精确恢复、撤权拒绝，以及 7 条事件/1 份终结证明和库存/截止事实不变。
- 日终 fixture 只通过正式来源发布、映射批准和 `deadline_entry.execute_ready` 生成截止事实；没有直接插入 cutoff、review 或 seal 事实，也没有使用固定业务 UUID。

## 其他校验与边界

`test_pg16_workflow_topology.py`：2 passed；新增模块与正式门禁通过 `py_compile`，`git diff --check` 通过。空库 runner 的源码清单共 1,459 个输入，逐项摘要无不匹配。

这份本地证据证明共享 runtime 模块和空库迁移路径可执行，不能替代 GitHub `pg16_runtime` 的实际成功结果、完整静态门禁、Client 门禁、真实 OSS/来源、生产身份和灰度验收。正式上线仍保持阻塞，未提交、未推送、未部署。
