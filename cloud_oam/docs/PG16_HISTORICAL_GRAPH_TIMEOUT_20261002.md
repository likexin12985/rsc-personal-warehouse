# PG16 历史夹具加载与完整迁移进程预算修复

源提交 `cb0210da25881d2198a6e31b2195a894e4e74409` 的 GitHub PG16 门禁出现两项失败：

- [migrations 任务](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36926005735/job/110583577964)：异常历史夹具准备时，`upgrade 20260903_0051` 超过 180 秒。
- [inventory 任务](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/36926005735/job/110583577982)：验证库存历史保留时，`downgrade 20260901_0040` 超过 180 秒。

二者 stderr 均停在 Alembic 数据库上下文初始化后，尚未出现第一条 `Running upgrade/downgrade`。前次本地剖析已证明：完整版本图会触发约十万次历史 runpy 加载。0164 没有改变库存约束来绕过失败；这两项也不是 0164 认证审计锁错误重现的证据。当前修复仍须在新的准确 SHA CI 复验。

## 历史夹具的真正优化

新增 `backend/pg16_legacy_migration_graph.py`。仅在建立一次性历史测试库时，使用 AST 读取明确的 revision/down_revision/depends_on/branch_labels 字面量，沿前驱和显式依赖闭包复制准确原始源码，构建临时 Alembic 配置。0051 夹具仍执行全部 51 个历史迁移；不会执行与它无关的后期版本导入。

重复版本、缺失前驱、循环依赖、动态元数据和源文件漂移都拒绝。原始源码与执行副本逐字节核对，临时目录只在本次上下文有效，不修改生产迁移文件，不共享 runpy 的可变模块全局。后续历史数据升级到当前 head 使用原完整图，保持所有升级、历史保留及失败原子性断言。

CI `_seed_0051_observation_only_completion` 和本地 `run_local_pg16_legacy_opening_checks.py` 共用此实现。临时数据库自身的私有 UUID 引导和最小权限规则不变。

两个独立新建 PG16.15 库的实际对比：完整图建立 0051 耗时 69.769 秒，准确历史图为 2.907 秒。140 个函数、270 个触发器、1217 个约束、727 个索引及全部关系/列/权限目录严格相等，摘要均为 `694e887d53f42ce3d955708e59a07b5ff3b82e2e13caa6d8a04a2beab08f272e`。两个库正常关闭，源码摘要无漂移。耗时是在本机有其他验证负载的观察值，不作为生产 SLA。

## 完整图的有界执行预算

完整 head 升级和保留检查仍需完整版本图。其 Python 子进程预算由 180 秒调整为 600 秒，与现有本地当前 head 入口一致；不是“所有迁移已加速到 180 秒以内”。

该预算包括解释器启动、版本图加载及 SQL 执行；具体 SQL statement_timeout、业务事务/锁检查和全部正确性断言不变。锁观察仍要求查到准确进程、准确对象及阻塞者；子进程提前结束或没有锁证据必须失败。新的聚焦用例覆盖超过原 180 秒的启动、明确子进程失败立即传播、600 秒内始终没有锁则失败，以及不把观察超时当作子进程终态。

## 当前验证及接续

本轮证据均在 `artifacts/formal-baseline-audit-20261002-0164/`：

- `historical-graph-focused-v2.log`：历史图、字节码隔离和锁观察 30 项通过。
- `historical-graph-bridge.log`：CI 临时库 UUID 引导/清理及准确历史图接入 3 项通过。
- `historical-graph-routing.log`：原 UUID 夹具及当前 0164 readiness/account admission 来源回归 4 项通过。随后仅在原夹具测试文件追加历史图接入用例，由上一条单独复验；不是新增一次业务范围。
- `historical-graph-equivalence.json`：上面的原生目录相等、源码及正常停库终态。
- `historical-graph-native.log`：旧历史回填、直接升 head、规则漂移原子拒绝三场景；是否已终态及源代码清单见 `continuation-current.json`，不能凭中间 PASS 行宣布整套通过。

不提交未收齐证据的改动；不重启仍在运行的 CI 或私有测试库，不删除旧失败。完整正式目标仍包括报废/失而复得等业务缺口，不能以本次测试基础设施修复替代。
