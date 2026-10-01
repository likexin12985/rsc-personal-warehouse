# RSC个人仓开发交接

更新：2026-10-01 17:19:44（北京时间）。目标仍是完整上线版本，本轮为 progress。未提交、推送、部署；本地测试不是生产验收。

## 工作位置与约束

- 工作树：`/Users/lizhiwang/.codex/worktrees/06f6/oam`，分支`codex/notification-delivery-worker`，HEAD `9dff36f7feca44626b82ceb6e40297b3732a22f0`。
- 首先完整阅读根目录正式生产版需求与架构设计V1.0；保留全部未提交改动，禁止reset/revert/丢弃。必须收齐既定证据后才提交。
- Python用`cloud_oam/.venv/bin/python`；从cloud目录运行pytest显式`-o pythonpath=backend`，先确认当前用例名称。系统Python3.9不适合。
- 本地PG仅使用工具新建、归本任务所有的Unix socket测试库。二进制`artifacts/pg16-native-20260920/install/bin`。不得借用生产库或外部业务写入。
- 首页为公开知识查询，星星后台按钮到`/xx`；小程序仍仅知识查询。飞书知识源按用户要求低优先级。微信/短信登录与真实生产渠道验收独立。

## 主树已整合与当前证据

统一证据根：`cloud_oam/artifacts/loss-correction-request-seals-next/`。

正式0161迁移、两类纠正请求封存、11条命令/预览/恢复POST和来源GET、H5纠正操作页已应用主树。57目标：28修改、29新增、无删除。原字节和逐项摘要在`main-application-v1/application.json`、`backups/`；当前1999源清单为`main-application-v1/source-v2.json`。

**主树本批验证已完成，主树不再被在跑测试固定。** 综合回执`main-application-v1/verified-v2.json`：

- 当前全前端2132项通过；最新日志`frontend-v2/frontend.log`。类型检查、后台/公开构建见`frontend-verified-v1.json`；随后只改两份后端测试和两份合成样本，产品运行时代码未变化。
- 首次后端组合147通过/1失败，失败为旧精确路由列表漏来源GET。随后65项接口及契约检查通过，`backend-contract-v4/contracts.log`，其中6项新增检查已保存样本与真实后端schema及请求摘要一致。不能把两次重叠计数相加为独立用例数。
- 契约生成测试原来无条件重写前端样本，造成两文件sourceDrift。现正常测试只写tmp_path；显式导出才使用`RSC_LOSS_CORRECTION_FIXTURE_EXPORT_DIR`绝对路径。两代样本均保存在`fixture-generation-fix/`，主树保留新样本，没有丢弃；65项复验确认源码不再变化。
- 主树真实PG16.15当前head门禁通过并正常停库，`native-head-verified-v1.json`：0161升级、空降至0140再升、完整启动/边缘权限、9个短信数据库场景、报表及期初约束。不是运营商发送验收。
- 正式0161、旧0160三轮历史升级与十份请求恢复、纠正HTTP/来源的数量/SN原生证据见根目录及`http-next`、`sources-next`的`*-verified-*.json`。主树后端运行时与这些验证候选相同；登录主体是合成身份，仍缺真实JWT/UAT。
- 390px页面无横向溢出，明确选择审批、预览、确认前禁用、勾选可用、取消正常。公开首页无登录表单，知识目录仍pending。`h5-next/visual/verified-v1.json`及图片保存证据；临时标签页关闭、视口恢复、预览服务85304已停止，未提交库存命令。

## 正在验证的发布门禁候选

`ci-next`尚未应用主树。为旧PG门禁更新实际0161版本/历史保留语义，并把纠正封存、来源HTTP两场景×数量/SN加入GitHub矩阵；保留全部旧矩阵分支。没有运行GitHub Actions。

- v1目录`ci-next/source`，清单`source-v1.json`，2005源。83项本地矩阵/隔离准入检查通过，`focused-verified-v1.json`。
- v1数量封存和HTTP来源均完整通过并停库：`native-seals-v1-quantity-verified.json`、`native-http_sources-v1-quantity-verified.json`。同worker正在各自SN步骤，不能把数量通过当整组通过。
- 旧多代门禁v1数量generations失败：空降重升合法重建0161函数后，旧断言比较原始pg_proc对象OID。失败库已正常停止，源码无漂移；原记录`native-multigeneration-v1/quantity-generations.log`。已停止其durable父worker90453，防止继续排队两个重复SN失败；已启动的quantity-seal_retention子进程92598仍必须按实际进程/PG状态收正常终态，不能据父状态failed认定它停止。
- 修正候选目录`ci-next/source-v2`，**当前清单`source-v3.json`**（仍2005源）。仅成功空库往返使用全部公开表行及规范化函数/触发器定义比较；拒绝降级仍保留原始目录严格不变检查。同时吸收主树4份测试/样本修复及空白规范化。144项本地配置/路由/样本检查在v2通过（`focused-verified-v2.json`）；v3再将诊断用函数签名JSON规范化，原生完整复验已启动。
- 当前原生：seals worker89917、http_sources worker89918使用v1；修正多代worker94283使用v3，依次执行quantity/serial×generations/seal_retention。实际子进程和进展以各state及ps为准。两套候选源都保持不变直到读者退出。

## 下一步

1. 收两个v1 SN原生终态，以及旧独立子进程92598的正常停库记录；保留原失败证据。
2. 收v3多代四步骤终态。逐文件核对v1/v3不同点：主树4份测试/样本、旧多代空库比较及CI入口空白；新封存/HTTP服务和共有运行时不变，可准确关联证据，不能说两版本完整源完全相同。
3. 全部候选门禁完成后，按v3 changes及当前主树摘要备份应用17个CI/测试/runner目标。保留当前主树已修复样本测试，不从旧候选覆盖它们。受影响验证通过后再继续正式基线审查。
4. 尚缺退回下游补偿、实际报废/纠正报废、失而复得及其他正式业务域验收。详见[报损纠正范围审计](LOSS_CORRECTION_SCOPE_AUDIT_20261001.md)。
5. 完整上线仍需真实短信/微信/通知/附件、正式角色与身份映射、迁移和期初、准确发布SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时、备份恢复/同步回退/应用回滚和真实业务冲销演练。不得缩减为本地绿色门禁。

## 历史与恢复

本次整理前的交接全文逐字保留在[历史交接](history/CONTINUE_DEVELOPMENT_20261001_171944.md)，SHA256 `f1335261b8e33945b0c5b07bdb1fccaa23889e5544e2a8e578ef1626b4f03948`。原失败命令、迁移与验证回执均保留。机器入口：`artifacts/loss-formal-application-next/continuation.json`和本批`status.json`。
