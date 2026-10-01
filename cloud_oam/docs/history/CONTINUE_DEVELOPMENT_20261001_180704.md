# RSC个人仓开发交接

更新：2026-10-01 17:46:01（北京时间）。完整上线目标仍active，本轮progress；未提交、推送、部署。本地证据不等于生产验收。

## 工作位置与约束

- 工作树`${RSC_REPO_ROOT}`，分支`codex/notification-delivery-worker`，HEAD仍为`9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 根目录正式V1.0基线已完整阅读；接续工作仍须按该文件。禁止reset/revert/丢弃未提交改动；收齐既定证据后才提交。
- Python用`cloud_oam/.venv/bin/python`，从cloud目录pytest显式`-o pythonpath=backend`。原生PG16.15在`artifacts/pg16-native-20260920/install/bin`，只使用本任务创建的私有Unix socket测试库。
- 首页公开知识查询，星星按钮到`/xx`；小程序仅公开知识查询。飞书知识源低优先级；短信/微信真实渠道、JWT和生产验收独立。

## 当前主树与已收齐证据

统一证据根`artifacts/loss-correction-request-seals-next/`，下称P。

主树0161迁移、独立批准/执行原请求封存、纠正HTTP来源/命令/恢复及H5页面已整合。本轮再应用17项门禁/runner/CI修正，6新增、11修改、无删除，全部原字节保存。该批2005源逐字节等于CI v3，清单`P/ci-next/main-application-v1/source.json`，应用回执`application.json`。本轮新增只读退回事实图及测试3文件，**当前2008源**见`P/return-history-next/source-v1.json`；没有修改既有运行时文件。

- **主树144项聚焦测试通过**，15.20秒；`P/ci-next/main-application-v1/focused-verified-v1.json`。源摘要无漂移，worker97768/child97769均退出，主树未被在跑测试固定。
- 先前主树1999源的本批综合回执`P/main-application-v1/verified-v2.json`：当前合成样本对应全前端2132通过，接口/契约65通过；类型与后台/公开构建通过。该轮真实PG16.15当前head升级、空降0140再升、完整启动/边缘权限、9个短信数据库场景、期初报表检查通过且正常停库。
- 本轮17项只改变测试/runner/CI，产品运行时和前端与上一条相同；不要声称2005源重新运行了全部前端或完整后端。
- 旧接口测试漏来源GET、契约测试无条件重写前端样本已修复。正常生成只写tmp_path；显式导出需要绝对目录环境变量。两代样本与原失败147通过/1失败均保留，不改写为全绿、不相加重叠测试数。
- 页面390px检查无横向溢出、明确审批选择、预览/确认/取消正常；公开首页无登录表单、知识目录pending。`P/h5-next/visual/verified-v1.json`；浏览器临时页及预览服务已关闭，无业务写入。

## 新增原生门禁：四场景全部通过

`P/ci-next/native-seals-v1-verified.json`、`native-http_sources-v1-verified.json`均已核验真实终态、PG16.15、源码无漂移、正常停库和所有进程消失。

- seals×quantity/serial：批准/执行分别11类边界、各3类并发，真实提交/回查、封存后迟到写拒绝、显式新批准与执行不改变原封存结果。
- http_sources×quantity/serial：每个场景6次真实提交、3次提交后响应丢失、28次只读查询；来源GET只读、读写权限独立撤销、原请求恢复与永久封存。
- 各场景均覆盖正式0161迁移、空库往返、完整运行时权限、保留历史拒绝降级且全部公共行/函数/触发器不变。登录主体仍是合成身份，不是生产登录验收。
- 上述四场景源为v1的2005源，当前主树等于v3。两版本只有6处差异：2后端契约/路由测试、2合成样本、旧多代门禁比较及CI入口空白。所有产品运行时和6个新增辅助文件完全相同，见`v1-v3-source-comparison.json`；不能称两套完整源码相同。

## 正在运行：修正多代门禁

唯一durable worker **94283**，目录`P/ci-next/native-multigeneration-v2`，物理源`P/ci-next/source-v2`，当前清单**source-v3.json**。此副本保持不变直到读者退出。

数量generations已完整通过且正常停库，回执`P/ci-next/native-multigeneration-v2-quantity-generations-verified.json`：三轮、九历史请求、并发单一赢家、两类历史保留和3个合法重建函数。数量seal_retention也完整通过且正常停库，回执`native-multigeneration-v2-quantity-seal_retention-verified.json`。当前serial-generations，child4805；后续还有SN seal_retention。以state和ps为准，不等于整组通过。durable会继续下一步骤，观测超时不可另起重复作业。

旧v1两个数量步骤均在合法空库重升后，因原始pg_proc OID比较失败。已保留失败证据并确认child90456、92598和对应PG均正常停止，`native-multigeneration-v1/failure-reviewed-v2.json`。父90453的KeyboardInterrupt是为停止后续重复排队；旧state第二步残留running不代表进程仍在。

修正仅对成功空库往返比较数据和规范化函数/安全/权限/触发器；被拒绝的历史降级仍使用原始严格快照。0161要求第一代请求历史也保留，已核验该代降级拒绝及原请求恢复。完整四步尚待收齐，不提交。

## 新增退回事实图与确认的破损入库缺口

- `backend/app/formal_services/stock_loss_corrections/return_history.py`逐项验证原处置及出库/发运/验收/入库事实，检查跨行错误关联、完整快照、游标和当前总部读权限；`return_progress.py`给出互斥的履约份额及SN集合。只读内部组件，不提供补偿写授权，不把累计入库当当前可用库存。
- `backend/tests/test_loss_return_history.py` **29项通过，332.23秒**；query_only证明无写入，覆盖数量/SN、各阶段分离、部分验收、短少补收、损坏证据、读取期间撤权、原发件人停用。回执`P/return-history-next/focused-verified-v1.json`。原终端session65422已exit0、PID1131消失；该次无单独原始日志，回执明确引用工具终态。
- 独立2008源副本`P/return-history-next/source`保持不变。原生quantity worker3228/child3231、serial worker3229/child3230，状态见各`native-*-v1`。`native_runner.py`复用正式退回收货门禁，在真实API角色READ ONLY事务验证新图；不能据启动宣称通过。主树未被这些副本固定。
- **上线阻断：破损接受量错误入新件可用账户，数量/SN均已复现，尚未修复。** 验收接受1、其中破损1，当前服务仍入`new/available`余额1；真实合成服务测试2次，48.25秒。`P/return-history-next/damage-probe/verified-defect-v1.json`和两份观测是缺陷证据，2 passed仅表示复现成功，不是正确入账验收。
- [破损入库修复范围](RETURN_DAMAGED_INBOUND_GAP_20261001.md)明确需前向迁移、按验收结果拆分数量/SN及目标成色、原历史恢复、SQL提交约束和客户端合同一起修。不能仅阻断破损功能后称完成；禁止静默改旧余额或原流水。

## 下一批业务与正式上线

1. **优先修复已确认的破损入库缺口**，同时收剩余多代及新事实图原生终态。对各门禁检查checks/terminal/cluster-state/进程/源摘要；失败只修真实原因，不放宽库存或权限约束。主树17项已应用，无需再次应用。
2. [退回补偿设计](LOSS_RETURN_COMPENSATION_DESIGN_20261001.md)：普通整单取消不能用于报损派生单。只读事实图已有本地29项证明，原生尚待收齐；修好入库分类后实现未出库补偿及并发隔离，继而逐阶段逆向物流。仅未出库通过不代表下游补偿完成。
3. 报废原处置/纠正报废、SN生命周期和失而复得仍缺；保留当前明确不可用入口直到事实/迁移/权限/恢复完整。
4. 完整上线仍需真实短信/微信/通知/附件、正式身份角色、迁移和期初、准确SHA GitHub CI、多角色UAT、至少3天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚及真实业务冲销演练。
5. [发布说明](LOSS_CORRECTION_0159_RELEASE_RUNBOOK.md)已改为0159–0161当前边界，旧版逐字归档；[范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)按当前证据更新。

## 历史与机器入口

修订前交接完整保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_173018.md)，SHA256`477bc9ba18aa86c0dc3a6dd5706bd3f6491ed941329e221e52cc06dc43bc37de`。更早历史链保留。机器入口为`artifacts/loss-formal-application-next/continuation.json`及`P/status.json`，本轮旧JSON也按时间存档。

本轮更新前交接保留于[历史交接](history/CONTINUE_DEVELOPMENT_20261001_174601.md)，SHA256`3b1d428b4eb837b8f543ba568f3a153a9876df7f2972be98e13d2f86905ba731`。
