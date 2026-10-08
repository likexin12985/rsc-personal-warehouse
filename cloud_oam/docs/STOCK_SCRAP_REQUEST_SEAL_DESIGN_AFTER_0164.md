# 报废与失而复得请求永久封存设计（0164 后候选）

本文件记录永久封存的实现要求；尚未进行正式表结构迁移、公开接口激活或生产授权。需求依据为正式 V1.0 的不可覆盖库存事实、完整请求恢复及权限约束，并沿用已实现旧逆向/纠正请求的永久封存语义。当前实现/证据以 `CONTINUE_DEVELOPMENT.md` 顶部及对应 source manifest 为准。

实施更新：私有封存结构已在新建本地PG16、完整0164迁移之后安装验证，18个外键/34个约束有效，API仍无访问权限；这不是正式迁移或生产安装。六类来源的SQLite结构测试11项通过。`recovery_authority.authorize_closure` 已提取同等身份/角色/范围/独立性的关闭请求授权分量，原 `authorize` 仍强制当前保管，8项聚焦通过。受控registrar、审计/COMMIT证明、lookup/seal调用链和双连接竞争均未完成；下方为完整目标，不因局部通过缩小。

## 1. 精确原请求与状态

封存对象是完整原请求：原操作者、请求号、真实 client key、原 canonical command/request hash 及已有的 expected plan hash；不能只给一个业务单号，也不能换 key 重试。输入继续使用现有 `ScrapRequestSeal` / `ScrapRecoveryRequestSeal`，在服务边界重新解析嵌套模型。

| 当前证据 | 封存调用的结果 | 新事实 |
| --- | --- | --- |
| 准确已执行 | 返回准确原始历史结果，retry=false | 不另造 seal，不重放通知 |
| 准确已封存 | 返回同一个原 seal，retry=false | 不改时间，不重复审计 |
| 完整校验后的未找到 | 在锁内持久关闭该原请求，retry=false | 追加唯一 seal 及审计 |
| 内容/key/操作者不匹配 | conflict | 不创建 seal |
| 孤立、损坏或读取变化 | unknown | 不推断未执行，不创建 seal |
| 当前权限不满足 | forbidden | 不创建 seal |

not_found 本身从不允许重发。只有 seal 真正提交并由完整原请求准确回查，才能确认该请求已经永久关闭；后续业务如仍需要执行，必须重新获取当前真实来源及批准，以独立新请求处理。

## 2. 真实来源锚点

| 原命令 | 必须存在的真实历史来源 | 不得伪造 |
| --- | --- | --- |
| 原始报废 | 原报损作业/行、真实总部终审决定及复核 hash | 尚未执行时不存在 root disposition；不得先造空处置根 |
| 纠正报废 | 原处置根、准确反向事实、独立纠正决定及各自 hash | 不用原始终审替代纠正决定 |
| 找回申请 | 准确报废行及其原始/纠正执行 hash | 不生成找回申请充当封存来源 |
| 区域找回复核 | 报废行、准确找回申请及 hash | 不生成已复核事实 |
| 总部找回复核 | 上述来源、准确独立区域复核及 hash | 不补造区域通过记录 |
| 实际恢复 | 上述来源、准确总部批准及 hash | 不用报废批准作为恢复批准 |

原始报废 seal 永久以真实终审决定为来源，即使后来另一独立请求完成了原始报废，也不能回填其 root。其余 seal 必须绑定真实已存在的 root。申请/复核没有库存 plan，不给它们合成 expected_plan_hash。

## 3. 候选持久化选择

新增一张 **数据库拥有的封存事实表**，由固定签名的受控函数写入，API 只读表，不开放直接 INSERT/UPDATE/DELETE/TRUNCATE。seal 行自身包含由真实 client key 推导的共同 key_token 和五个实际命名空间 hash；不把 seal 伪装成已执行事实，也不为原始 seal 放宽现有已执行绑定的非空 root 约束。

表包含独立 seal UUID、六类原命令的明确 kind、完整 canonical command、request hash、原始请求号、操作者与授权版本、发生时间、准确来源字段和各自 typed FK。仅原始报废 seal 的 root 为空，并且必须拥有真实终审来源；其他类型禁止 root 为空。每一 kind 严格限定必填/必须为空的来源列，不能只存不可验证的 polymorphic ID。

原始附件标识保留在原 canonical intent 中，seal 不充当附件审核、审批通过或库存恢复凭证。其存在性和可用性不能被“封存成功”偷换为业务附件已验收。

数据库受控函数从真实 client key 计算所有 aliases/token，不接收调用方声称的 token/hash 作为登记依据。沿用旧 `cloud_oam.loss.correction.key.v1` token 语义和三个旧 aliases，追加现有 scrap/recovery 命名空间；不存储或记录明文 client key。API 不得利用普通表 INSERT 伪造别人的 key provenance。

## 4. 原子性与权限

写入顺序沿用真实库存写服务：先锁 inventory ledger head，再锁本次原操作者的权限图；精确重读历史来源、当前 read 与该动作的 write 权限，确认无旧/新事实、封存或孤立结果，再创建 seal 与准确审计，最后由 deferred COMMIT proof 复核。历史操作者不需要保留今天的写授权才能只读查询；创建新的 seal 必须有当前授权。

实现时须显式保留权限映射：区域命令 action 为 `review_scrap_recovery_region`，权限 action 为 `review_scrap_recovery_regional`，不能按命令字符串直接推导。现有 `recovery_authority.authorize` 末尾还会调用 `_custody`；封存授权应提取共用的身份/角色/范围/独立性部分，而不能直接调用完整物理执行授权，也不能给真实恢复执行添加可绕过 custody 的调用参数。对应数据库权限证明必须保持相同区分。

封存不执行库存动作，不要求原请求的库存计划今天仍可执行，不借当前库存数量猜测原请求是否执行。原始与纠正的 expected plan hash 必须按原请求完整保留；禁止为了让封存成功重新计算、替换原计划或来源 hash。权限/人员独立性及来源绑定继续按对应动作验证，不能借旧审批人的授权给当前调用方。

准确审计只描述关闭该请求，不增加业务通知、库存流水、审批、物流或入库事实。受控写函数创建的时间和原操作者必须与唯一审计完全一致；遗漏审计、错误聚合或孤立审计均不能成为有效 seal。

## 5. 双向关闭与回查

封存表、旧 loss key registry、新 scrap key registry、现有二十四类请求事实及库存交易/收发事实组成同一锁顺序下的交叉检查。所有相关插入均有不可由 API 绕过的 deferred fence。seal 先提交时，后到执行必须整笔回滚；执行先提交时，封存调用只返回原历史结果，不能同时留下 seal。

新报废/找回读接口必须同时识别准确已执行、准确已封存、无任何证据的未找到，以及孤立/冲突未知。旧逆向/纠正查询适配器必须把新 seal 的 token/aliases 冲突视为 unknown，不能返回干净 not_found。实际执行和审批服务应给出准确已封存错误；直接 SQL 绕过服务检查仍须在 COMMIT 被拒绝。

历史回查只依赖当前 read 权限与完整历史证据，不要求当前审批阶段、库存、写授权仍与当时一致。复用既有两遍观测及账本/审计游标检查，查询不得补 seal、登记或通知。

## 6. 必须取得的真实证据

- 六类命令分别完成永久关闭、精确回查及重复关闭不重复写；原始报废测试必须从没有 root 的状态开始。
- 原请求已执行时不造 seal；内容/key/request/operator 改动拒绝；找回申请/复核不合成计划字段。
- 漏 seal、漏审计、错误来源/FK、伪造 hash/token、API 直接写、owner 修改/删除/清空及孤立证据反例。
- 数量和 SN 的 seal 先赢/执行先赢，使用两个实际 API 连接并证明真实锁等待、准确唯一赢家和失败方完整回滚；跨新旧登记同 raw key 同样检查。
- 当前操作者晚撤权、权限自然到期与保管变更分别取证；历史审批人撤权不得误伤有权者的只读查询。
- 正式 revision/Base/ACL/readiness/目录进入同一 head；已有报废/找回/seal 时降级拒绝并保留全部事实；旧封存/旧 raw-key aliases 升级前后保留。
- HTTP、PC/H5 原请求保存与中断恢复、准确提交 SHA 的 CI 和生产验收另行验证。局部 SQL/SQLite 或私有服务通过不替代上线条件。

不得用只读 not_found、模拟内存去重、遗漏 raw-key 交叉检查或关闭旧守卫代替上述持久封存。

## 7. 实施接续注意点

候选 seal 的建议 kind 固定为 original/correction/apply/regional/headquarters/execute，不能用任意 table name 作为调用参数。每行应派生并保存真实报损 operation/line；original 绑定 `stock_loss_headquarters_decisions.id`，该旧表没有 operation_id 列，须经真实 decision.line_id / review_id 查到报损来源，不能臆造复合外键。

可直接复用的真实复合键包括：scrap line 的 `(id,root_disposition_id)`；找回申请的 `(id,scrap_line_id)`；区域和总部找回复核的 `(id,recovery_request_id,scrap_line_id,decision)`；旧纠正决定的 `(id,root_disposition_id,reversal_id,disposition)`。HQ/execute 来源只能匹配 verified 区域；execute 还必须匹配 approve 总部。原始 seal 的 root 永久为空；其他五种必须为真实 root。expected_plan_hash 仅 original/correction/execute 有值，其他三种必须为空。

检查实际 canonical 来源，不能统一套模板：报废 canonical 为 `schema_version/action/intent/request_id/expected_plan_hash`；三段找回及实际恢复 canonical 使用 `recovery_facts.intent` 的完整命令结构并排除真实 client key，附件 UUID 排序。数据库必须构造同一份规范 JSON，再用现有 `rsc_canonical_reconciliation_json_0026` 算法检查 hash；不能用另一个 JSON 序列化规则近似。

现已新增 `seal_request.sql` 的私有规范输入/键派生函数，原生证据见交接顶节。它没有来源存在性或写入授权语义，未来registrar必须在同一事务中进一步验证真实来源。原始无root的上游历史可分别使用实际已有的 `rsc_check_loss_submission_history_0159(operation_id)`、`rsc_check_loss_regional_history_0159(regional_review_id)`、`rsc_check_loss_headquarters_history_0159(review_id)`，并验证真实decision.line_id/review_id与报损operation一致；不要调用必须有root的 `rsc_check_loss_upstream_history_0159` 来迫使生成虚假root。纠正/找回则必须使用真实root的完整历史及各级准确hash；既有 `rsc_scrap_recovery_source_0165(scrap_line_id)` 核验的是历史执行来源，不能替代当前关闭授权或完整请求缺失证明。

性能注意：当前候选 `scrap_bindings.sql` 使用数组重叠扫描 registry，以及对多类请求表使用 `to_jsonb(f)` 构造碰撞谓词。最终规模门禁前应改为按真实列构造的 token/alias/actor-request 查询，利用已有唯一索引，并核验查询计划与完整拒绝语义；不能将局部合成数据库的通过当作500用户性能证明。不要在正在运行的原生门禁期间修改这些源。

### 7.1 受控写入接续：提交时复核不得依赖明文 key

六类数据库当前授权已新增为私有 `seal_authority.sql`，原生数量/SN均通过，每组51个当前授权拒绝反例；正常停库及源码摘要证据见交接顶节。它从source函数重新推导范围，要求同角色范围的当前read与对应write，并独立检查区域/总部自审；不检查当前保管、不发放库存permit。源准备函数的历史证明不含当前权限，不能单独作为封存批准。

接入deferred COMMIT proof前，须将canonical/source/current-grant的无明文key校验部分与真实client key派生包装分开。registrar入口接收真实key，验证并计算五个aliases/token，但不持久保存key、不写GUC、不依赖调用方传入的token。COMMIT按已受控写入的canonical及真实来源重新验证当前权限/来源/审计/碰撞；不得使用固定假key调用现有包装器冒充provenance证明。封存历史只读校验不调用当前write授权；创建时和提交时的当前权限校验必须与保留历史校验分离。

登记函数先锁inventory ledger，再锁当前principal；不存在证明必须覆盖两登记表、全部既有请求事实、库存交易/收发事实和孤立审计/状态/通知。新seal必须自带全部五个真实aliases，而旧registry非空root和旧alias含义保留。使用既有审计链writer追加唯一准确audit，受控SQL登记与审计在同一外层事务，遗漏任一项由deferred双向证明拒绝。此节是下一步约束，不是已实现的业务入口。

### 7.2 已落地的数据库候选与剩余接入

canonical/source/current-authority现已提供无明文key的私有核心，保留原真实key包装器。`seal_persistence.sql` 包含受控registrar、历史来源/唯一审计证明、请求缺失检查、只追加保护及跨请求/事件的deferred fence；不是正式revision。v1数量/SN均完成六类实际API角色封存事务、原始无root、唯一审计/重复无新增、迟到真实原始报废拒绝、晚身份版本拒绝与全库回滚。审计UTC格式及孤立/重复审计、三时区一致性v2结果以交接顶节为准。

候选 `request_seals` 已组合准确已执行返回原结果、准确已封存回查和新封存受控登记；`seal_reads` 只用 SELECT 读取及校验历史/唯一审计，不调用含 FOR SHARE 的数据库来源函数。原始/纠正及四阶段找回查询均识别 seal，旧lookup遇新seal返回unknown。69项聚焦已通过，组合服务与撤写权后 READ ONLY 的数量/SN原生证据已全部通过并正常停库；每组13份seal准确回查、39项改动拒绝、13次重复关闭无新增和10份已执行原结果返回。完整回执 `scrap-seal-readback-v1-receipt.json`，以交接文档首节为准。低层重复登记仍要求当前write权限，组合服务对已执行/已封存先准确回查、仅需当前read，不能借低层入口冒充只读。没有HTTP路由或正式迁移。实际写服务准确sealed错误及六类顺序式迟到写已取得证据（见7.3）；六阶段两API连接的seal先赢/execute先赢已取得真实锁等待及完整终态证据（见7.4）；自然权限到期已取得完整证据（见7.5）；旧审批/新原始关闭的同key竞争及权限目录并发撤权已取得限定范围证据（见7.6、7.7）；其他动作覆盖仍需审计。


### 7.3 实际写入口关闭检查候选

`request_guard.prepare` 已接入六类 `bound_commands` 入口。它重新校验准确原命令，按账本→当前权限图锁顺序核对当前身份及read，然后调用完整原请求回查。只有已证明的sealed返回明确关闭409；内容/键不一致返回冲突，孤立证据返回unknown，已执行请求要求只读恢复。not_found只允许同一受锁事务继续既有当前write、实物保管、审批阶段和库存计划校验，不产生公开重试许可。

数据库fence没有移除或绕过。原生反例在六类真实可执行阶段先创建封存，再验证服务明确拒绝，并使用不含此服务前检的真实库存/审批writer与真实client-key registrar尝试提交；六类均已在数量/SN两个原生库的COMMIT由封存冲突拒绝，全库快照不变。18份seal撤write后只读回查、18次再次执行关闭拒绝及54项写入冲突也通过。该顺序式迟到写证明不等于两个连接同时竞争。聚焦20项通过，完整回执 `scrap-seal-write-admission-v1-receipt.json`；两库正常停库、源码无漂移，详情以交接文档首节为准。


### 7.4 两个真实API连接的关闭/执行竞争

`pg16_scrap_seal_races.py` 已接六类实时业务阶段，分别控制seal先提交和execute先提交。先完成赢家事务内的真实服务调用但暂不COMMIT，启动第二个实际API连接；核对不同backend PID、`pg_blocking_pids`指向赢家及等待方的账本表RowShareLock，确认真实锁等待后才提交赢家。每个参与方只调用一次，不自动重试。

赢家提交后、对手结束事务前，通过既有owner连接读取全库已提交快照，再允许对手完成。对手最终不能新增任何事实；seal先赢时仅封存/审计/审计链三表变化，execute先赢时关闭调用必须返回found及准确原结果，seal表不变。之后用真正READ ONLY回查。未给API增加测试专用读权限。计数中的一个业务结果表示一个原请求的有效执行结果，不是声称该事务只写一行表记录。

两种顺序在数量/SN各六类阶段合计24个并发场景已通过完整门禁，正常停库且源码完整清单一致；回执为 `scrap-seal-races-v1-receipt.json`，详见交接文档首节。该证明尚不包括跨旧/新命令命名空间复用同raw key、自然权限到期或外部管理员撤权竞争，不能扩大验收范围。


### 7.5 真实服务的自然权限到期与COMMIT回滚

`pg16_scrap_seal_expiry.py` 已在数量/SN各六类真实历史来源上通过自然到期检查，完整回执 `scrap-seal-natural-expiry-v1-receipt.json`。owner仅在业务事务前配置测试角色任期的截止时间；业务以实际API角色调用一次组合服务，记录数据库时钟并确认服务在任期有效时返回、seal与唯一审计仍未提交。随后等待数据库时钟自然越过截止时间，实际COMMIT由当前关闭权限守卫拒绝23514。失败后全库与配置基线相同；恢复原测试任期后全库与原始快照相同，再做真正READ ONLY回查，not_found且retry_allowed=false。

这12个场景不是末尾修改身份版本或私有授权探针。服务返回、授权截止和提交尝试的时间顺序逐项保存及验证；历史/两代业务/原并发矩阵全部回归通过，两库正常停止且1550文件完整源码清单一致。并发撤权是另一个事务竞争，仍待取证；本轮不扩大为跨新旧key竞争、正式迁移、HTTP或上线验收通过。


### 7.6 旧纠正审批与新原始关闭共用真实key

`pg16_scrap_cross_registry_races.py` 在第一代真实找回后，使用合法旧CorrectionApprove和准确新原始关闭请求，同raw key而request_id不同。两个真实API连接在账本锁处竞争，每个原命令只调用一次。新seal先提交时，旧审批和真实key registrar能执行，但实际COMMIT因已有封存的跨registry冲突23514拒绝，完整回滚；旧审批先提交时，新关闭返回unknown 503，不能伪造为自己请求的found或写入seal。双方均再以READ ONLY按各自完整原命令核验，失败者不新增任何事实。

旧审批的成功结果直接用于后续第二代纠正报废；新seal的一份成功结果纳入后续三时区、撤write回查及重复关闭，持久seal总数因此为每组19。数量/SN各两种顺序共4个场景完整通过，回执 `scrap-seal-cross-revoke-v1-receipt.json` 中的 `crossRegistrySealConcurrency` 保存PID、锁等待、错误阶段和精确快照证明。当前覆盖是旧审批↔新封存；其他旧逆向/新登记动作尚不能据此声称已经真实并发验证。

### 7.7 六类关闭与独立权限目录撤权事务

`pg16_scrap_seal_revocation.py` 每类关闭分别测试撤权先提交和关闭先提交，一个真实API事务与一个独立fixture-owner事务竞争。owner只将该动作的现存write grant从allow改为deny，保留read；实际观察权限行阻塞、对应表锁和不同backend PID。撤权先赢时关闭服务因当前权限23514拒绝，无seal/审计残留；关闭先赢时撤权等待关闭COMMIT后生效，之后仍能在write deny时准确READ ONLY回查。恢复原grant后全库与预期赢家快照完全相等。

数量/SN六类两种顺序合计24个场景通过，回执字段 `sealRevocationConcurrency`。这不是管理员HTTP端点验收，也不声称覆盖所有身份冻结/角色撤销方式；没有新增API身份表权限，没有用末尾同事务版本注入代替竞争。两组完整门禁及源码1552文件一致性、正常停库均已验证；正式迁移和上线验收仍独立待完成。
