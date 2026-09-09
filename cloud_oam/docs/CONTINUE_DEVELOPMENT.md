# 切换账号后的续开发入口

核验日期：2026-09-08。此文件是当前交接摘要；旧聊天和旧日期段落只作追溯。
产品规则仍以根目录 `AGENTS.md` 和 V1.0 正式需求全文为准。

## 1. 从哪里接续

| 项目 | 已核验值 |
|---|---|
| 本机主工作目录 | `~/Documents/Codex/2026-06-11/oam`（`~` 为本机用户主目录） |
| 开发分支 | `codex/production-readiness-gates` |
| 私有远端 | `https://github.com/likexin12985/rsc-personal-warehouse.git` |
| 已验收功能代码 | `4a6ec83b27f76f84d1d8fe2a9ded699c9b166349` |
| 本次交接准备前，本地与远端 HEAD | `0b426d088d4998fddb9c53e17c2998c93155ce46` |
| Alembic 唯一 head | `20260912_0072` |

本文件随上述 HEAD 的纯文档后继提交发布；接手时重新读取实际 HEAD 和远端，
不要把本文件中的交接前 SHA 当成回退目标，也不要从落后的 `main` 重新开发。
同一台电脑直接使用主工作目录。其他机器通过有权访问上述私有仓库的 GitHub 身份取得
该开发分支，再按 CI 的固定版本重建隔离开发依赖；不搬运原目录中的凭据或生产材料。

本次另查到两个干净的旧工作树，均无主开发分支之外的独立提交，无待合并工作：

- `~/.codex/worktrees/06f6/oam`：`codex/mini-post-recovery`，`6349d8d`。
- `~/.codex/worktrees/8403/oam`：detached `0bb7091`。

这些旧工作树保持原样。主目录的四个未跟踪目录为受保护本地材料：
`cloud_oam/deployment/offhost_backup/`、`operations/`、`preproduction/`、`recovery/`。
不清理、不暂存、不打包上传；其存在不表示有未提交的本批业务代码。

## 2. 哪些已经完成

- 核心库存账户、不可变流水、余额/SN 投影、权限、审计和事务 Outbox 已建立。
- 期初与日常盘点主要服务、页面、复核、过账和历史恢复已实现；正式控制库存接入及
  受控期初启动仍有缺口，不能把这些服务的测试通过当成真实业务建账已就绪。
- 需求提报、三级逐行审批、供给计划、Web 分配、占用、释放、拣货和实物出库主要路径已接入。
- 0072 按原拣货及原出库行建立独立出库事实，支持数量/SN 分批，库存从拣货中转入在途。
  原出库单/行仍是不可变拣货快照；当前出库数量必须从 `outbound_postings` 读取。
- 实物出库使用唯一预置且明确绑定的在途账户，保持原所有权、保管人、位置及物料维度；
  不自动创建账户或猜测交接责任。网页提交结果未知时保留原请求，只用 GET 核验，不重发 POST。

准确范围、接口和出库回归见[实物出库验收](OUTBOUND_POSTING_ACCEPTANCE.md)。
旧迁移 0071 及更早版本不可为新功能直接改写；下一批使用前向迁移并同步安全目录。
0072 首次空图降级失败已在 `4a6ec83` 修复并通过真库，不再当作当前阻塞。

## 3. 可复核的测试证据

| 工作流 | 对应提交 | 结果 |
|---|---|---|
| [PG16 34215778501](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/34215778501) | `4a6ec83` | success；真库迁移/并发 1 passed，后端/边缘门禁 2416 passed、2 skipped |
| [Client 34215778415](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/34215778415) | `4a6ec83` | success；Web 1009、小程序及门禁契约 724 项通过，类型/构建/安全检查通过 |
| [Client 34218828264](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/34218828264) | `0b426d0`，仅文档 | success；未改变上述后端/迁移基线 |

PG16 使用 GitHub Actions 的一次性隔离数据库，本机没有可用 Docker/PostgreSQL 16 真库。
本地环境跳过真库测试不计通过；不把门禁指向共享或生产数据库。
后续仅文档提交通常只触发 Client；任何新后端/迁移代码必须取得其准确 SHA 的完整 PG16 结果。
既有 Web 主包约 872 kB（gzip 223 kB）的体积提示仍在，未作为失败隐藏。

## 4. 下一批交付：发运与分包

按 V1.0 第 1.8、2.3、3.7 节及[出库验收的后续范围](OUTBOUND_POSTING_ACCEPTANCE.md#后续开发)继续：

1. 建立正式 `shipments`、`shipment_lines`、`shipment_serials`，逐笔绑定原出库事实和
   出库行，支持同一出库事实分为多个包裹；不能按旧快照的 `outbound_qty=0` 计算剩余。
2. 冻结来源、目标位置、人员和需求修订关系；人工填写承运商、运单、交运时间。
   首期不接承运平台、不自动下单，地址文字或同名人员不得作为身份绑定。
3. 实现只读候选、独立幂等交运命令、原请求状态查询和 Web 页面。
   累计交运不超过原出库数量，SN 归属唯一且不可重复发运；一包完成不代表整单完成。
4. 发运事实、需求版本/状态事件、审计及 Outbox 同事务提交，登记交运不得再次移动或扣库存。
   结果未知时只读恢复原命令，后续状态变化后仍能核验原出库和拣货命令。
5. 补数量/SN 守恒、越权、竞争、幂等、整笔回滚、历史恢复及发运前后库存完全不变的测试；
   新迁移核验精确函数/触发器、空图降级再升级及非空事实降级阻断，再通过 Client/PG16 门禁。

优先参考这些代码：

- `backend/app/formal_services/material_request_outbound.py`、`material_request_outbound_options.py`。
- `backend/app/inventory_models.py`、`demand_models.py`、`database_security.py`。
- `backend/app/routers/formal_material_requests.py`、`backend/app/material_request_outbound_schemas.py`。
- `backend/alembic/versions/20260912_0072_outbound_postings.py`。
- `backend/tests/test_material_request_outbound.py`、`test_outbound_postings_migration.py`、`pg16_outbound_gate.py`。
- `frontend/src/FormalMaterialRequestOutboundPanel.tsx`、`materialRequestOutbound.ts`、`materialRequestOutboundRecovery.ts`。

上述路径相对 `cloud_oam/`。发运完成后继续人工物流/签收、工程师分批及异常验收、
独立个人仓入库流水；通知、OAM 收货证据及对账不能代替任何一步。

## 5.1 本次继续开发进度：0073 发运与分包

已在主工作目录实现 0073 前向迁移、`shipments`/`shipment_lines`/`shipment_serials`、人工承运商/运单登记 API、只读发运列表、Web 发运面板，以及数量/SN 守恒和“登记发运不再次扣库存”的回归测试。当前实现仍把物流签收、分批验收、OAM 收货证据和个人仓入账作为独立后续事实；不能把发运登记当作收货或入账完成。

本地专项结果：后端 0072 出库与 0073 发运相关 46 项通过；前端发运契约 2 项通过；TypeScript 类型检查通过。新迁移和后端代码仍需取得准确提交 SHA 的完整 PostgreSQL 16 门禁后，才能进入下一批收货/个人仓入账。

## 5. 完整 V1.0 和上线仍缺什么

- 上述发运收货链；审批代理、替代料以及已分配/拣货后的完整取消补偿。
- 正式工单投料、消耗、旧坏件回收；人员调拨、退回、报损报废和离职交接。
  被 production 隔离的旧原型页面/路由不能计入正式完成。
- 小程序履约、统一扫码等入口；正式报表、批量导入导出、打印和通知送达链。
- OAM 可信控制库存发布、组织/人员正式只读发布及日终对账、受控建账启动准备。
- 首管理员与真实身份/短信/微信/附件联调、预生产迁移及备份恢复、压测、真机/UAT、灰度和发布放行。

开发与备案并行推进。当前未部署生产、未执行外部业务写入，也未因本交接新增此类授权。
此前“6–10 周”已经明确更正为缺乏工时测量的粗估，不作为交付承诺。
下一批记录实际开发、CI 等待、返工和可获取的额度变化；账号共享用量不能直接归因于本项目。
不在交接文档保存账号标识、余额、验证码、令牌或登录材料。

## 6. 0074/0075 收货与个人仓入账进度

当前分支已完成从发运到个人仓入账的后端主链：0074 新增物流事件、收货、收货异常、待入账事实；0075 新增不可变 `inbound_postings` 事务绑定；0076 新增独立 `material_request.fulfill` 写权限。收货接口执行数量/SN累计校验，入账确认调用统一 `post_inventory_transaction`，不直接更新库存表。详情页已提供创建待入账单和确认过账入口。

最近提交：`4ade78e`（fulfillment 权限回归）。本地验证：前端正式 Web 测试 53 个文件/1013 个用例通过，TypeScript 与 Vite production build 通过；迁移与 ORM 对照通过，数据库安全/PG16 相关测试 303 passed、1 skipped；仓库安全脚本通过。Vite 仅提示主 chunk 体积偏大，未影响构建。GitHub 门禁仍需以账号额度恢复后的真实运行结果为准。

## 7. 接手续检及运行环境

同机先在主目录执行以下只读检查，确认后按第 4 节直接开发：

```sh
cd "$HOME/Documents/Codex/2026-06-11/oam"
git status --short
git branch --show-current
git rev-parse HEAD origin/codex/production-readiness-gates
git worktree list
git diff --name-only 4a6ec83b27f76f84d1d8fe2a9ded699c9b166349 HEAD
```

读取实际远端 ref 与工作流结果；缓存的 `origin/` 引用不能代替一次实时远端读取。
每次 commit 和 push 前，从仓库根运行 `bash cloud_oam/scripts/verify_repository_safety.sh`。
只暂存本批明确文件；不使用 `git add .`、`git clean`、强制推送或重置旧工作树。

本机后端解释器为 `cloud_oam/.venv/bin/python`；Node 为
`~/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node`。
接手先验证路径仍存在；新机器以 CI 配置的 Python/Node/锁文件为准，不依赖本机绝对运行时路径。
相关专项完成后跑所需门禁；文档更改不需要重新跑本地全套业务测试。

本会话建议的开发配置是 GPT-5.6 Sol / High / Standard（Fast 关闭）；这是建议，
没有代替用户更改选择器。轻量页面可用 Medium，疑难事务可专项复核；不常驻 Max/Ultra，
不以切换模型证明服务容量、代码状态或业务验收已恢复。

## 7.1 物流事件 Web 接入（当前增量）

最新实际 HEAD：`c512ecd`（`test: cover logistics timeline ordering`）。
0074 已建的 `logistics_events` 现在具备正式写入 API、按发运单读取 API、Web 适配器严格响应/输入校验，
以及发运面板中的事件历史展示和 `pickup / transit / signed / exception` 登记入口；
收货验收面板已接入收货人、发运明细、合格/拒收数量、验收条件和 SN 输入；
异常事实已写入 `receipt_exceptions` 并在收货响应、历史页面展示。
异常收货明细可绑定证据文件 ID，并纳入收货幂等请求指纹；前端合法证据 ID 契约已覆盖。
个人仓入账历史根据不可变 `inbound_postings` 绑定派生 `posted`，不改写原始入账单事实。
物流事件登记只追加签收/运输事实，不改变收货验收状态、库存或个人仓入账状态。

待入账单创建现在在同一事务写入幂等 `personal_inbound_order_created` Outbox 事实，供待办与通知处理；库存过账仍由统一库存过账服务产生独立库存事件。

物流事件写入与历史读取均复核发运事实的来源库存账户范围；本批履约后端定向回归 17 项、Web 发运/收货/入账契约 10 项通过。
随后扩展履约与数据库安全回归至 317 项，全部通过。
收货与物流时间线现均拒绝早于交运时间的事实；修复后物料需求全套仍为 513 passed、1 skipped。
物流时间线专门契约回归新增 2 项，履约相关定向回归合计 18 passed。

本批本地验证：正式 Web 测试 53 个文件/1019 个用例通过，TypeScript 检查和 Vite production build 通过，后端完整测试 3353 passed、2 skipped，仓库安全检查通过；
已推送 `origin/codex/production-readiness-gates`。下一步仍需补正式收货验收录入页面、异常证据处理、
收货/入账历史读取和端到端 PostgreSQL 16 门禁；云端门禁结果仍受账号额度与网络可用性影响。

## 7.3 收货异常与个人仓入账边界修正

提交 `3e7a786` 修正了收货异常与个人仓入账之间的数量边界：混合收货中的拒收/破损行继续只保留为收货异常事实，个人仓过账只读取合格数量；整张收货单没有任何合格数量时明确阻断，不生成空库存交易。新增两项服务回归，收货/入账仍保持独立状态轴，未改变既有库存过账入口或迁移。

本次定向验证：`tests/test_material_request_inbound_contract.py` 为 6 passed；提交前后仓库安全检查均通过，准确 SHA 已推送到既有开发分支。该修正尚未取得新的 PostgreSQL 16 真库门禁，不替代端到端收货、SN 和并发验收。

## 7.2 远端门禁现状

截至本次核验，最新 Client 工作流 `34276684676` 对应 HEAD `4a23049`，job 在启动约 3 秒后失败，
`steps: []`，没有执行任何测试步骤；更早的 PostgreSQL 16 工作流 `34275459633` 也呈现同样的快速失败形态。
这属于 GitHub 账号额度/运行环境阻断，不能作为代码失败证据，也不重复触发无效重跑。

## 8. 新账号可直接粘贴的提示词

> 在 ~/Documents/Codex/2026-06-11/oam 主仓库继续 RSC 个人仓开发。
> 先完整阅读 AGENTS.md、docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md，
> 再读 cloud_oam/docs/CONTINUE_DEVELOPMENT.md 和 cloud_oam/docs/OUTBOUND_POSTING_ACCEPTANCE.md。
> 核对 codex/production-readiness-gates 的实际 HEAD、远端、工作树及准确 SHA 的门禁。
> 当前主链已到 0072 实物出库；直接实施下一批“发运与分包”的后端、Web、断线恢复、迁移和测试，
> 发运必须绑定原出库事实，累计数量/SN 不超量，不能再次扣库存。完成后继续收货与个人仓入账。
> 维持 V1.0 独立状态、正式权限及 PG16 门禁，安全检查通过后提交推送到既有私有开发分支。
> 保护本地部署目录，开发与备案并行，不操作外部生产系统，不重新开发已验收功能。
> 每批报告实际开发耗时、测试等待、完成内容和可归因的额度变化，不再给未经实测的总工期承诺。

## 7.4 收货与入账页面事实绑定修正

最新实际 HEAD：`d8dd7c3`。收货验收页改为加载当前需求的已登记发运事实，并用真实
`shipment_line_id` 下拉选择；同时修复前端发运响应校验丢失明细主键的问题。个人仓入账页改为
从当前需求的收货历史选择收货单，避免手工输入任意 UUID。两项改动均只改变入口校验和展示，
不改变后端事实、库存过账或状态轴。

本地前端全量回归：53 个测试文件、1020 个用例通过；TypeScript 构建通过。提交前后仓库安全
检查均通过，已推送 `origin/codex/production-readiness-gates`。后续仍需继续补正式工单履约、
通知送达、OAM 可信控制库存和 PG16 真库门禁等 V1.0 缺口。

## 7.5 个人仓入账真实服务契约与重试修复

本批修正了此前替代过账服务的测试未发现的实际阻断：个人仓入账从原在途账户转入本账
个人仓，应使用 `transfer`；`inbound` 仅用于外部进入受管账。已验收合格数量现在解析到
预置 `available` 账户。重试不再绕过所属需求、发运目标和统一库存过账鉴权；错误通过
正式 HTTP 状态返回并回滚。新增数量/SN 场景，收货/入账专项合计 24 passed；
扩大物料需求与库存过账回归为 617 passed、1 skipped（130.45 秒）。

详见 [个人仓入账服务验收记录](PERSONAL_INBOUND_ACCEPTANCE.md)。此前“后端主链完成”的描述
应理解为已有接口和模型，不能作为真实首笔过账或 PG16 联调完成的证据；本批无生产写入。

## 7.6 个人仓入账页面恢复

基于实际 HEAD `93cd4e4` 的本批改动：个人仓入账页现在能从服务端历史继续处理待入账单；
写请求结果未知时暂停重提交，只有只读 GET 核验按钮可解除状态。前端新增 2 项恢复契约，
全量回归为 54 个文件、1023 个用例，TypeScript 构建通过。该页面恢复不改变后端事实，
也不把 GET 回读当成库存过账完成。

## 7.7 正式工单物料事实表基础

基于 `7f21ed8` 新增迁移 `20260917_0077`：`work_order_material_operations`、
`work_order_material_lines`、`work_order_material_serials` 和
`work_order_replacement_pairs`。四张表均为只追加事实，分别保存 OAM 工单绑定、操作类型、
个人仓库存账户、数量/成色、SN 的 SKU/二维码校验，以及以换代修新旧件配对；没有复用旧
`work_order_materials` 原型表，也没有自动猜测 OAM 工单或人员。

本批只建立正式数据边界和迁移/ACL 基础，尚未宣称投料、消耗、释放、回收服务完成；下一批
 将实现逐工单权限预检、批量整批校验和统一库存过账命令。0077 的 PostgreSQL 16 真库门禁
仍未取得。

## 7.8 正式工单物料只读预检

新增 `formal_services/work_order_material.py` 作为正式工单物料操作的只读预检边界：只接受
活动状态的 OAM 工单、精确匹配的工单工程师和操作人、启用中的正式物料，以及属于该操作人的
`available` 库存账户；物料、成色和库存账户必须逐项一致。整批明细会先校验数量、状态、重复
物料和重复 SN，全部通过后才允许后续命令进入写入阶段。本批没有创建操作事实，也没有库存过账；
对应后续的占用/消耗命令必须继续调用统一库存过账服务。

预检模块同时提供操作请求指纹生成器，固定工单、操作人、操作类型和整批明细的规范化哈希，
供后续幂等写入使用；不接受未定义的操作类型。

本批继续增加 `record_posted_operation`：它要求调用方先取得状态为 `posted` 的统一库存事务，
再追加工单操作、明细和 SN 校验事实；重复幂等键且请求指纹一致时只返回原事实，指纹或库存事务
不一致时阻断。该函数本身不改库存余额。

新增正式路由 `POST /api/v1/work-orders/{work_order_id}/material-preflight`，只提供当前登录人员
的只读预检结果；路由要求 `work_order_material:operate` 权限，并拒绝伪造操作人。它不会创建操作
事实或改变库存，后续写入命令仍须在统一库存过账成功后调用事实写入服务。

本批新增 `POST /api/v1/work-orders/{work_order_id}/material-operations`，接收操作类型、幂等键、
整批明细和统一库存事务 ID，只接受已经 `posted` 的库存事务，再追加正式工单操作事实；同一幂等
键可安全重放，绑定不同请求或不同库存事务时返回冲突。生产 App 导入仍由配置层要求真实
PostgreSQL URL，未用占位数据库绕过门禁。

操作 DTO 现支持声明新旧 SN 配对并纳入请求指纹；实际登记命令暂拒绝只有 UUID 的配对，必须由
新件消耗和拆回件入账的关联命令生成 `work_order_replacement_pairs`。

预检现在还会逐 SN 读取正式库存序列、生命周期和当前位置，要求 SN 的物料与明细一致、状态为
`active`，且当前位置正是提交的库存账户；只提交 SN UUID 但无法证明库存坐标时会阻断。

带 SN 的明细现在要求 SN 数量与物料数量逐一相等；数量为 2 却只提交 1 个 SN 等情况在预检阶段
直接拒绝，避免库存数量与序列号事实分叉。

新增只读历史接口 `GET /api/v1/work-orders/{work_order_id}/material-operations`，按正式工单返回
不可变操作事实，供履约页面刷新和断线恢复；该接口不推断库存状态，也不修改任何事实。
历史接口现在先核验正式工单存在，不会把不存在的工单伪装成空历史，缺失对象明确返回 404。

事实写入还会校验操作类型与库存事务类型严格对应：占用/释放/消耗/回收/冲销分别只能绑定
`reserve`/`release`/`consume`/`inbound`/`reversal` 事务，跨类型绑定直接冲突（回收类型修正见 7.15）。

操作写入 DTO 现在在 HTTP 边界使用枚举约束，只允许 `occupy`、`release`、`consume`、`recover`、
`reverse` 五类正式操作，未知类型在进入服务前即被拒绝。

## 7.9 工单操作证据修正与真实剩余范围

本节修正 7.8 的完成口径。此前的 9 项测试主要覆盖纯函数/DTO，没有验证数据库写入、
HTTP 数据范围或原库存事务绑定。`record_posted_operation` 仍是已有库存流水的证据登记器，
不能代替“批量占用、消耗、回收及操作事实同事务提交”的正式业务命令。

本批修正：

- 登记必须重新校验当前权限版本、工单工程师和对象范围；写入通过现有 owner-lock 函数锁定
  OAM 工单，避免对 API 只读投影直接申请 UPDATE 权限。
- 只接受 `source_document_type=work_order_material` 且来源 ID、过账用户精确匹配的流水；
  行数、个人库位、账户、物料、成色、数量和原流水 SN 集合逐项一致。
- 不再按 SN 的当前可变位置验证已发生的消耗事实；历史回读使用不可变流水，工单关闭后
  仍可按原键读取既有事实，换键不能重复登记同一事务。
- 每个受控 SN 必须提供并验证实际 SKU、SN 和二维码值，禁止收到 UUID 就自动写
  `sku_verified=true` / `qr_verified=true`。错误或缺失三码证据不产生操作事实。
- 任意新旧 SN UUID 配对不构成以换代修证据，当前明确阻断；后续必须由新件消耗与拆回件
  入账的关联命令建立配对。此前“已实现配对”仅是 DTO/表结构，不能计为正式回收完成。
- 历史 HTTP 查询验证具体工单范围并禁用缓存。预检返回 `coordinates_validated`，不再使用
  容易被误读为库存可过账保证的 `ready_for_posting`。数量限制为有限的 numeric(18,3)，输入
  拒绝未定义字段；同值 `1` / `1.000` 使用相同请求指纹。

新增隔离 SQLite 数据库及 HTTP 契约覆盖跨工单、跨人员、数量不符、伪造扫码、缺失扫码、
历史重放、重复绑定和数据范围。测试模拟历史流水和当前身份，不等于真实扫码验收、
完整生产鉴权种子、统一库存命令联调或 PostgreSQL 16 并发验收。

本批新增 `execute_consume_operation` 的事务组合：调用统一 `post_inventory_transaction` 后在同一
未提交会话追加工单事实；路由提交或回滚整个会话。当前专项测试验证了调用顺序和失败边界，
但尚未取得真实 PostgreSQL 16 的余额投影/并发结果。下一步仍须实现占用、释放、回收命令、
对应权限迁移、业务审计及 Outbox、以换代修双向流水配对、完整回滚/并发测试和准确 SHA 的
PG16 门禁。保留上述目标，
不要把当前证据登记接口当作已完成的工单物料功能上线。

本批验证：扩展库存/锁约定/数据库安全回归 422 passed（28.77 秒）；随后补充 HTTP 成功扫码重放、
数据库晚期异常回滚及权限版本失效，工单专项合计 34 passed（2.63 秒）。
旧 HEAD `abd1df1` 的 PG16 run `34289777177` 在 2 秒内终止，`steps: []`，未执行测试。
本批仍没有真实 PostgreSQL 16 并发通过证据。

## 7.10 原子消耗命令

新增 `POST /api/v1/work-orders/{work_order_id}/material-operations/consume`。它只生成 `consume`
库存流水：每条明细从本人个人仓账户扣减，统一过账返回成功后才追加工单操作事实；路由最后才提交
数据库事务，异常时回滚两类事实。相同幂等键由库存过账和工单事实同时回读，工单后来关闭也不
会把已完成的幂等重放误判成新操作。

本批服务专项 35 passed，覆盖 evidence/DTO/HTTP 路径和原子调用编排；由于本机无 PostgreSQL 16，
尚未把真实余额投影、并发锁和期初证据图当作通过。占用、释放、回收仍需各自的账户方向、
权限、审计和 PG16 门禁。

## 7.11 释放占用命令

新增 `POST /api/v1/work-orders/{work_order_id}/material-operations/release`。释放必须同时提供
占用来源账户和本人可用个人仓目标账户；服务逐项核验物料、人员、个人库位和目标状态，调用统一
`release` 库存流水后再登记工单事实。目标账户纳入幂等指纹，不能用同一幂等键改投另一目标。
真实余额投影和 PG16 并发仍待门禁验证。

## 7.12 工单占用顺序约束

新增 `POST /api/v1/work-orders/{work_order_id}/material-operations/occupy`。占用只允许从本人
`available` 账户移动到本人 `reserved` 账户；消耗和释放的来源账户现在必须是 `reserved`，从而
把“先占用、再消耗或释放”的 V1.0 顺序写入服务边界。三类命令共用统一库存过账和幂等键，
当前专项回归 36 passed；实际余额投影、SN 位置并发和 PG16 门禁仍待验证。

## 7.13 操作事实审计与待办事件

工单物料操作事实现在与 `material_request` 审计链、Outbox 待办事件在同一数据库事务中追加。
缺少审计链头时写入会 fail-closed；同一操作重放只返回既有事实，不重复产生审计事件或 Outbox
事件。专项回归新增审计链和 Outbox 幂等断言，当前工单物料证据测试为 21 passed。

这只证明 SQLite 事务边界和事件幂等契约；正式 PostgreSQL 16 门禁、事件消费者、权限迁移、
余额投影并发和收货/个人仓入账链仍需继续实现与验证。

## 7.14 工单物料权限迁移

新增 Alembic `20260918_0078`，为正式工单物料历史读取和占用/释放/消耗/回收操作建立独立的
`work_order_material/read` 与 `work_order_material/operate` 权限，并向 admin、区域经理和
技师角色写入最小授权。迁移快照、权限集合和回滚检查已通过；真实 PostgreSQL 16 门禁当前
仍因环境未提供数据库而跳过，不能视为 PG16 通过。

迁移全量回归随后通过 `168 passed`（约 149 秒），覆盖升级到 `20260918_0078`、ORM 快照、
权限集合、角色授权和可回滚性；该结果仍不替代真实 PostgreSQL 16 并发门禁。

个人仓入账成功后，入账单状态与需求单 `personal_inbound_status` 现在在同一事务中更新为
`posted`；重复回放只复用既有库存事务和绑定，不重复增加库存事实。该状态是需求侧读模型，
库存事务仍是入账事实来源。收货审计/通知事件和真实 PostgreSQL 16 余额并发验证仍待完成。

## 7.15 回收请求与实际验证范围修正（2026-09-09）

复核 `519de8d` 发现，所谓独立 recover DTO 实际误绑到了 release 接口，recover 仍引用 release
DTO；仅检查路径存在的测试没有覆盖该错误。本批修正两个接口的 OpenAPI 类型，并通过真实 HTTP
请求验证调用的命令、返回结果和异常回滚。四个执行接口均拒绝请求操作人与当前登录人员不一致。

按 V1.0 第 1.9 节，回收明细现在只接受明确的 `used` 或 `damaged` 成色和一个
`target_stock_account_id`，不再接收无用途的来源账户 `stock_account_id`。目标账户必须属于当前
人员、位于有效个人仓、处于可用状态且成色一致。通用事实登记入口也不能绕过旧/坏件条件。
调用方须使用新的目标账户字段契约；释放接口继续要求来源和目标两个账户。

真实库存校验另发现旧 recover 命令把外部拆回件写成 `return`，而该类型只允许受管账户间移动。
本批改用外部入库 `inbound`，业务事实仍为 `recover`；未放宽统一库存服务对内部退回的限制。

四个命令原来每次重新生成 `effective_at`，与库存幂等指纹冲突。现在持有工单锁时，只复用同一
业务过账键下的原始时间，仍把当前操作者和当前输入交给统一库存服务重新计算指纹、核验权限。
没有原交易且工单已关闭时，在调用库存服务前拒绝新操作。四类命令的真实库存服务回放测试覆盖
原时间恢复、关闭工单后的原请求、修改数量冲突和撤销库存权限；首笔交易使用隔离预置证据。

另有旧件/坏件两条测试从完整已复核的期初图开始，使用真实 SQLite 库存服务验证余额 0→1、
重复调用仍为 1，以及库存过账后业务审计异常时余额、流水、工单事实和 Outbox 整笔回滚。
HTTP 输入、OpenAPI、目标账户、事实/审计/Outbox 与上述余额测试分别提供证据，均不等于
PostgreSQL 16 锁竞争、真机扫码、以换代修配对或生产放行通过。

本批验证：工单 HTTP/证据/预检与统一库存过账回归合计 `165 passed`（31.71 秒）。下一步仍须
验证迁移后真实技师角色能否完成所需库存过账授权，以及准确 SHA 的 PostgreSQL 16 门禁。
## 7.16 工单过账的最小权限边界

工单物料的四个原子命令现在向统一库存过账服务传递明确的权限资源
`work_order_material`/`operate`。库存账户范围仍由统一过账服务锁定并逐账户核验，但不再把
技师提升为可直接调用任意库存过账能力的 `inventory_transaction/post` 角色。真实回放测试
移除广泛库存过账权限后仍能重放原工单事实；移除工单操作权限则在进入库存服务前拒绝。

这只证明当前正式权限模型和隔离测试一致；迁移后的真实技师身份、RLS/数据库 ACL 与
PostgreSQL 16 并发门禁仍需在准确 SHA 上验证。

## 7.17 工单详情物料履约回读入口（2026-09-09）

Web 工单详情新增“物料履约”页签，使用正式工单物料历史接口读取占用、消耗、释放和收回事实。
页面只读展示操作号、操作类型、库存过账事务和状态；接口响应执行严格 UUID、字段集合和版本校验，
不接受旧 `/work-order-materials` 原型数据，也不会把读取失败当作写入成功。该入口为后续正式操作面板、
收货和个人仓入账联动提供事实回读位置。

本批前端全量回归为 `55 files / 1028 tests passed`，生产构建通过；结果仍不替代真实 PostgreSQL 16
门禁、RLS/ACL 和收货/入账端到端验证。

## 7.18 履约历史操作类型契约收紧（2026-09-09）

工单物料原子操作历史的前后端响应契约现在使用 Literal 集合校验：`occupy`、`release`、`consume`、
`recover` 和通用事实登记入口保留的 `reverse` 均为合法操作类型，终态为 `posted`。未知操作、未知状态
或额外字段会失败关闭；Web 页签把 `posted` 显示为“已过账”，把 `reverse` 显示为“冲销”。这保持了
原子命令边界，同时不丢失通用冲销历史。

本批前端页面回归为 `55 files / 1029 tests passed`，后端工单路由与预检回归为 `31 passed`，生产构建和
仓库安全检查通过；仍不替代 PostgreSQL 16 真库门禁。

## 7.19 PG16 readiness head 修复（2026-09-09）

GitHub 付款方式更新后，Client gate 已在真实 runner 通过；PG16 gate 首次实际执行暴露出
`rsc_oam_runtime_binding_ready_0044()` 仍固定检查 `20260912_0072`，而 Alembic head 已到
`20260918_0078`，导致 `edge_inbox` 与 `star_oam_projector` 的绑定就绪检查错误失败。
新增 `20260919_0079` 只推进该 readiness marker，保留函数 OID、owner、ACL、SECURITY DEFINER、
search_path 和精确 prosrc SHA 校验；降级直接恢复稳定的 0072 marker，以保证继续回退到历史
0072 migration 时仍可验证。运行时安全 manifest 和 PG16/Alembic head 常量已同步到 0079。

本批本地验证：readiness 专项 `3 passed、1 skipped`；迁移与 OAM 投影安全回归 `182 passed`。
真实 PostgreSQL 16 并发 gate 已具备重跑条件；首次真实执行继续暴露出 `inbound_postings`
缺少 `star_oam_api` 的 `SELECT/INSERT` ACL，已由后续 0080 迁移修复；0080 同时把 readiness
marker 原子推进到当前 head，尚未把云端重跑结果写成通过证据。

## 7.20 分批收货与个人仓入账状态聚合（2026-09-09）

个人仓状态现在按当前修订的每条最终批准明细分别聚合：`pending_acceptance` 表示已经发运但还没有
合格验收，`partially_accepted` 表示仅有部分合格数量，`accepted` 表示全部合格数量已收货但仍有
数量未过账，`posted` 只有在每条正净批准明细都已由独立 `inbound_postings` 绑定已过账库存事务时
才成立。拒收数量不计入个人仓，取消数量从应入账量扣除；不同物料不会互相抵消，已取消的空需求不会
因真空聚合被误标为已过账。

该读模型在发运、收货和个人仓过账事实提交后刷新；状态变化同步推进需求版本，避免旧出库事实在当前
版本仍被误判为同版本历史。相同收货幂等键在版本变化后先回读既有事实再返回，分批写入不会重复增加
版本或审计/Outbox 事实。个人仓入账单的 `posting_transaction_id` 与 `inbound_postings` 绑定同一
库存事务，后续审计或 Outbox 失败时随事务回滚。

本批本地定向回归：收货、发运、入账与状态聚合 `48 passed`；此前已取得准确 SHA 的最新提交
`3047344` 的 Client gate 与 PostgreSQL 16 release gate 均通过。状态聚合新增代码仍需随本批准确
提交 SHA 重新通过两条云端门禁后，才能作为发布证据。
