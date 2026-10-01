# 报损纠正与处置上线缺口核查

核查日期：2026-10-01。主树 `codex/notification-delivery-worker`、HEAD `9dff36f7feca44626b82ceb6e40297b3732a22f0`；本批尚未提交、推送或部署。以正式需求基线1.11、不可变库存流水与第6节验收红线为依据。任务进程的最新终态见 [开发接续](CONTINUE_DEVELOPMENT.md)，不从旧日志里的PASS推断整组完成。

## 当前实现与证据

| 要求 | 当前证据 | 尚未完成 |
| --- | --- | --- |
| 区域核实、总部终审与处置分离 | 原报损审批契约仍保持stock_effect=none；处置为独立业务事实与库存流水；本轮202项组合通过 | 正式权限配置与真实角色UAT |
| 原处置恢复可用、转旧、转坏和派生退回 | 主树注册预览、执行、请求封存和准确来源接口；候选44+28项；当前202项组合回归覆盖 | 准确发布SHA CI和生产验收 |
| 写入结果未知的恢复 | 四组当前1948源PG16.15 HTTP通过：数量/SN×处置/退回，真实COMMIT后模拟断联503仍能按完整原请求查回；not_found不允许自动重发 | 真实登录链路与终端/网络验收 |
| 永久关闭原处置/原派生退回请求 | 四组原生HTTP均证明封存后迟到写409、无新增库存事实、准确回查sealed；当前读写授权分别验证 | 正式角色权限与发布SHA CI |
| 三种正常处置的原生完整回归 | 当前数量/SN均已输出三类实际提交、SN/数量、共享冻结保护、通知去重与只读恢复PASS | 数量/SN两组已退出0，完整checks、1948源一致和PG16.15正常停库已验证；发布SHA/真实角色验收仍独立 |
| 多代冲销与纠正 | 正式0160已经集成；数量/SN×generations/seal_retention四组规范候选原生证据齐全，三轮/九请求、并发单赢家、保留旧历史及封存 | 当前准确发布SHA完整CI、纠正HTTP及客户端、真实业务权限 |
| 原处置的历史恢复 | `stock_loss_corrections.original_recovery`验证后继历史而返回同一原事实；后继历史6场景/84次HTTP及当前组合回归通过 | 不得把原posted当当前库存；真实登录/生产验收仍缺 |
| 独立纠正批准与执行 | `correction_approval.approve`和`correction_execution.execute`已具独立权限、原因、父记录、审计/键绑定与恢复服务 | 主树尚未集成独立封存；隔离候选新增两类事实，24项服务及另8项恢复边界通过，原生覆盖层测试中。正式0161迁移/目录/权限、纠正API和操作页仍缺 |
| 退回补偿 | `return_dependencies`证明后续出库/发运/收货/入库并阻止直接冲销 | 各阶段独立补偿事实、依赖顺序、数量/SN守恒和并发；不能改原单状态或删除旧流水替代 |
| 报废与失而复得 | 总部批准支持scrap，H5候选明确显示报废执行未开放；数据库不接受缺少专门证据的报废历史 | 原报废/纠正报废的完整过账、SN生命周期、专用反向冲销与SQL历史证明 |
| H5总部处置 | 22目标候选完成：严格接口契约、已批准来源/真实路线、预览/显式确认、持久原请求和跨标签锁、只回查、单独封存；2046项全前端、118项聚焦、最终页面10项、后端契约8项通过；公开/私有构建与桌面/390px界面检查完成 | 已按before摘要备份应用主树；1969源主树前端2046/后端契约8及双构建/公开隔离验证均通过，443模块静态发布回归仍在运行 |
| 公开入口与小程序 | 公开首页查询，登录入口在/xx；当前miniprogram/app.json只注册knowledge，README明确原业务源码不进入公开上传包 | 保留用户的公开入口安排；不把总部业务页重新注册到公开小程序。历史小程序工程师业务能力仍不得当作当前已发布能力 |

## 证据定位

0161集成进展：隔离覆盖层数量/SN已全部通过并正常停库，见 `artifacts/loss-correction-request-seals-next/native-{quantity,serial}-verified-v1.json`。独立正式候选 `formal-source` 注册了真实0161迁移、精确结构及运行时权限目录；`formal-native-quantity-v1` 已实际完成升级和完整启动权限检查，整组仍需以终态回执为准。主树仍是0160，不得将候选进度记成部署完成。

0161新增历史的降级策略：只允许新封存表和请求绑定表全部为空时恢复旧结构；有不可变请求历史则拒绝结构降级，应用回滚应保留升级后的数据库。还需验证既有0160业务历史升级后精确保留，以及后续发布门禁在0161下的保留历史预期；不能修改或删去旧事实来让降级通过。

- 202项主树组合：`artifacts/loss-execution-command-http-next/main-focused-verified-v1.json`（1947源）。
- 四组当前原生HTTP及59项CI配置/分派：`artifacts/loss-execution-command-http-next/main-application-v2/*-verified.json`（1948源）。CI证明仅本地矩阵/分派，GitHub Actions尚未运行。
- 原生失败与修正：同目录`preceding-failures.json`、`application.json`。旧0150断言替换为实际0159错误文字；预览允许必要行锁但仍仅SELECT、禁止提交与业务变化；GET与请求恢复保持数据库READ ONLY。生产服务和约束未因此改变。
- 正式0160原生四场景：`artifacts/loss-multigeneration-release-next/scenario-evidence-status.json`及四份`native-*-verified-v2.json`。外层1305/内层1304仅README差异；不能称为当前全仓1948源结果。
- H5：`artifacts/loss-execution-h5-next/main-application-v1/verified-v1.json`为主树终态；`verified-candidate-v1.json`、`application-review.json`为此前候选。原生后端与本地模拟页面是独立证据，不能组合成生产端到端验收。
- 已有报损退回收货/独立入库：`artifacts/loss-formal-application-next/receipt-{quantity,serial}-verified-v2.json`（历史1901源），后续受影响发布需按实际源码重验。

## 剩余实施顺序

1. 原生处置迁移和H5主树集成已完成；收443模块三组静态发布回归终态，按真实失败修复，保留全部既有改动。
2. 为纠正批准和纠正执行各增加独立不可变封存事实、完整原请求回查与数据库双向迟到写栅栏，保持审批不动库存和权限独立。随后接入正式纠正API/后台交互。
3. 实现退回下游补偿、原报废与纠正报废、失而复得专用反向流水；通过新迁移扩展，不能修改历史已冻结SQL以省略门禁。
4. 完整上线仍须真实短信/微信/通知/附件及角色配置、正式迁移和期初、准确提交SHA CI、多角色UAT、至少三天可解释对账、500用户性能、RPO≤5分钟/RTO≤2小时及备份恢复/回滚演练。公共知识源按用户要求暂缓，不虚构目录内容或绕过发布要求。

上一份审计的全部历史快照已逐字保存到 [历史审计](history/LOSS_CORRECTION_SCOPE_AUDIT_20261001_before_current_audit.md)。历史里的“当前”“运行中”“未集成”仅表示当时状态，不再作为本轮状态。
