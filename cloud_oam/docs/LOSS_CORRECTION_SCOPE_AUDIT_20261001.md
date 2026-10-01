# 报损纠正与处置上线缺口核查

核查日期：2026-10-01。工作树`codex/notification-delivery-worker`、HEAD `9dff36f7feca44626b82ceb6e40297b3732a22f0`。本批未提交、推送或部署。遵循正式基线1.11、库存流水和第6节验收红线。最新进程与主树复验见[开发接续](CONTINUE_DEVELOPMENT.md)。

## 当前实现、证据和缺口

| 要求 | 当前实现与证据 | 仍需完成 |
| --- | --- | --- |
| 报损冻结、区域核实、总部批准、处置分开 | 原审批不改变库存，处置与流水独立。原处置恢复可用/转旧/转坏及派生退回已经主树集成，既有数量/SN原生记录保留 | 0161当前版本完整发布矩阵、真实角色UAT |
| 退回发件、收货、独立入库 | 已有数量/SN原生收货/入库证据；物流签收/OAM收货/个人账保持独立 | 当前版本受影响门禁与真实终端验收；下游逆向补偿另列 |
| 破损验收入库分类 | **已确认缺陷，未修复**：数量/SN合成服务均接受1、破损1，却入new/available余额1。证据`return-history-next/damage-probe/verified-defect-v1.json` | [按验收结果拆分数量/SN和目标成色](RETURN_DAMAGED_INBOUND_GAP_20261001.md)，前向迁移、历史恢复、SQL约束及前端合同一起修；正常收货门禁不能覆盖此缺口 |
| 多代冲销和独立纠正审批/执行 | 0160多代历史、0161独立批准/执行封存、正式HTTP及经完整历史证明的来源GET已应用主树。数量/SN原生候选均passed，主树运行时代码逐字节一致 | 主树本批复验已完成（verified-v2），仍缺准确发布SHA与正式角色/UAT |
| 结果未知后原请求恢复 | H5保存完整请求后只发送一次，跨标签按原处置锁定；中断只查原请求，not_found不重发；显式永久封存另行确认。原生HTTP分别证明当前读写授权、三次真实提交后响应丢失回查 | 真实登录/JWT和终端网络验收；不会把历史posted当当前库存 |
| 0160旧数据升级0161 | 数量/SN各三轮旧历史、十份完整原请求恢复保持一致，旧公共表行精确保留；新封存表空、新列空；有请求历史拒绝降级并保持数据和目录 | 正式环境迁移演练和应用回滚验收；不得删除绑定历史以允许降级 |
| H5纠正页面与角色路由 | 原处置详情到准确root；不自动选择审批决定；独立权限、预览/确认、存储损坏阻止新写。主树1999源全前端2132、类型、双构建证据已收齐，运行时代码与当前2005源一致 | 真实角色UAT仍缺 |
| 手机布局和公开隔离 | 390px无横向溢出，确认前禁用，勾选可用，取消正常。公开首页无登录表单，星星按钮到/xx；知识目录pending；小程序仍仅公开知识页 | 飞书知识源按用户指示低优先级；不能把空目录称为已验收资料库 |
| 退回下游补偿 | 新增完整历史核验和互斥履约份额内部读组件，本地29项通过，数量/SN原生仍在跑。已有依赖检查继续阻止直接冲销；只读图不提供补偿写授权 | 先修破损入库分类，再完成各阶段逆向事实、顺序与数量/SN守恒、并发、恢复；不得改状态或删流水替代 |
| 报废与失而复得 | 总部可批准报废；实际执行界面明确尚未开放，不伪装完成 | 原报废和纠正报废过账、SN生命周期、失而复得专用反向流水及数据库历史证明 |
| 发布门禁 | 17项修正已备份并应用主树：更新0161，增加seals/http_sources×quantity/serial且保留所有原矩阵。主树144项聚焦通过；新增四个原生场景全部通过且正常停库 | 修正多代四步骤仍在跑；准确提交SHA GitHub Actions未完成，尚未提交 |

主树补充：旧契约生成测试会改写前端样本，已改为默认仅写临时目录，显式导出需绝对目录参数。两代样本都保留。修复后65项接口/契约及2132项前端通过，源码无漂移；新增6项验证已保存样本与后端schema和完整请求摘要一致。该1999源当前head PG16.15升级/空往返/权限已正常通过并停库。当前2005源再加入17项测试/runner/CI变化，产品运行时未改；与`ci-next/source-v3.json`相同。隔离`source-v2`仍由多代worker使用，主树聚焦复验已结束。

## 本轮证据定位

统一根目录：`cloud_oam/artifacts/loss-correction-request-seals-next/`。

- `main-application-v1/application.json`、`backups/`、`source.json`：57目标，28修改/29新增，无删除，主树1999源等于候选。主树worker终态需查各`state.json`和实际进程。
- `formal-native-quantity-verified-v2.json`、`formal-native-serial-verified-v1.json`：真实0161迁移、空库往返、完整运行时目录/最小权限、两类封存并发/迟到写/不可变事实、有历史拒绝降级、正常停库。
- `populated-upgrade-{quantity,serial}-verified-v1.json`：旧0160历史升级与只读恢复精确保留。
- `http-next/native-{quantity,serial}-verified-v1.json`：三种命令及恢复/封存真实数据库事务。`sources-next/native-{quantity,serial}-verified-v1.json`再覆盖只读来源、独立撤权和准确引用用于后续命令。
- `h5-next/page-verified-v1.json`：2132项来自v4，v5仅两份测试导入修正后的13项和完整类型通过；避免混称同一版本全量。`visual/verified-v1.json`和图片为合成数据真实前端检查；没有提交库存命令。
- 主树最新前端`main-application-v1/frontend-v2`已passed；后端v1/v2收集失败、v3的147通过/1失败均保留，修复后v4的65项接口/契约通过。`main-application-v1/verified-v2.json`为该批完整回执。
- `ci-next/main-application-v1/application.json`：17目标原字节备份、应用结果及当前2005源清单；`focused-verified-v1.json`证明主树144项通过、无漂移、worker结束。
- `ci-next/native-seals-v1-verified.json`及`native-http_sources-v1-verified.json`：新增四个原生场景通过，原始源为v1；`v1-v3-source-comparison.json`准确列出6处差异和未变运行时/新辅助文件。不能冒充准确SHA的GitHub执行通过。
- 旧多代两次相同失败及正常停库见`ci-next/native-multigeneration-v1/failure-reviewed-v2.json`；修正版四步骤以`native-multigeneration-v2/state.json`及真实进程为准，不用单步PASS代替整组终态。

## 下一步

1. 主树原业务批次以`main-application-v1/verified-v2.json`为证；当前源码以`ci-next/main-application-v1/source.json`为准。不能把旧147通过/1失败改写成全绿。
2. 门禁修正已整合、主树144项及新增四个原生场景通过；继续收齐修正多代四步骤终态。保留历史失败证据与所有已有改动。
3. 当前在2005源基础上新增3文件，2008源见`return-history-next/source-v1.json`。新增只读历史核验29项通过；原生候选独立固定，不能把它的启动当通过。优先修复已确认的破损入库缺陷，再补退回下游补偿、报废及失而复得。
4. 全部正式基线仍需真实短信/微信/通知/附件和角色配置、迁移及期初、准确发布SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时及备份恢复/应用回滚演练。本地测试不等于生产上线。

前一份审计逐字保留：[历史审计](history/LOSS_CORRECTION_SCOPE_AUDIT_20261001_before_0161_main.md)。
