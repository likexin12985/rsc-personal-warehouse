# 期初盘点 Excel 导入：格式预校验切片

日期：2026-09-24。依据正式 V1.0 基线 1.10、1.12：导入先预校验并生成错误报告，
有权限人员随后确认执行；OAM 主数据不能由 Excel 覆盖，库存只能由正式盘点与不可变流水入账。

当前只实现**格式层**。已授权 `stocktake.count` 的账号可从
`GET /api/v1/stocktakes/opening/imports/opening-count/template` 下载单表 XLSX 模板，
以原始 XLSX 请求体和正确 MIME 调用 `POST .../format-check`，得到文件 SHA-256、
行数、格式有效标志、错误位置与代码；`POST .../error-report` 对有错误的同一文件
返回不回显原始单元格值的 XLSX 错误报告。接口不读取任务/范围，不解析现有物料归属，
不创建 `FileJob`、审计、通知或库存事实，也没有“确认执行”入口。文件上限 8 MiB、
解压总量上限 64 MiB、盘点行上限 10,000、错误报告最多保留 999 项加截断提示；公式、宏、
外部链接、路径异常和非模板表头拒绝；不信任 XLSX 声明的工作表尺寸，会按实际行列
检查上限，防止缩短尺寸声明藏匿数据。数量被规范为最多三位小数的正数文本，
物料/SN 标识须为文本。`count_method=import` 只在内存中形成候选行。

`/xx` 盘点中心现已接入“期初盘点 Excel 格式预检”区：按 `stocktake.count`
权限显示，支持下载模板、选择 XLSX、查看行级格式错误及下载同一文件的错误报告。
页面明确提示格式通过并不代表业务预校验或导入，不提供“确认导入”入口；切换文件会
清除旧结果。客户端经现有 Cookie/会话请求层收取二进制文件，保留显式 XLSX MIME，
拒绝将 200 HTML 错误页当作模板下载。

格式检查通过**不是业务预校验通过**，更不是盘点提交。后续正式导入任务须：

1. 将源文件按专用用途存于私有 OSS，绑定上传者、文件 SHA-256、准确任务/轮次/范围、
   当前人员权限版本和模板版本；持久化 `FileJob` 的预校验状态与错误文件。
2. 在同一受控业务范围内只读核对 OAM SKU 投影、物料追踪政策、SN/批次、冻结截止、
   实盘人/复盘人及完整范围；生成逐行错误报告，任何错误都不得执行部分行。
3. 由有 `stocktake.count` 且获该范围分配的人员显式确认；确认时重验源文件/hash、
   人员/授权、任务版本、轮次、截止和所有引用，用原幂等坐标调用正式
   `submit_opening_stocktake_scope_count`。未知提交结果只读原命令回执，不自动重放。
4. 执行后精确回读任务、范围完成、差异、审计和库存事实；此导入不能写 OAM 主数据、
   直接修改余额或跳过区域/总部复核与期初发布。

`0140` 已在文件完整性规则和数据库文件守卫中增设独立的 `opening_count_import`
用途：仅接受 XLSX、单文件上限 8 MiB，创建/更新须有当前 `stocktake.count`
权限；与后台专用的 `inventory_report_export` 目的分开。OSS 适配器已有期初盘点
源文件的底层受控读取方法，但**任务/轮次/范围授权与 FileJob 尚未接入**，
目前没有可执行导入的正式接口。`0136` 的 `file_jobs` 运行角色守卫仍只接受
`export` 任务，现有列权限和状态转换不能承载 `import`。
因此下一次迁移需独立定义错误文件目的、任务与文件/范围/授权版本的精确
绑定、预校验→待确认→执行的受控状态转换及运行角色权限，并用真实 PG16
验证伪造、越权、重放和源文件漂移拒绝；不能通过回滚一次正式计数命令来冒充
无副作用的业务预校验。

本轮聚焦：`test_opening_count_import_workbook.py`、
`test_opening_count_import_format_api.py` 加原开账读写路由测试合计 **86 passed**；
仓库安全扫描 **1,663 文件 PASS**，`git diff --check` 通过。当前候选较上轮
1,659 文件冻结快照增加四文件（含本文档）、改动两处源码；旧完整静态/PG16/镜像证据只属于旧快照，
新候选完整门禁与新镜像尚未重跑。另在全新本地 PG16.15 空库完成现有迁移至
`0139`、运行角色/报表任务权限及九项短信配置夹具复核，见
`artifacts/local-current-head-pg16/checks/run-5reyib22/checks.json`；实例已停止、
`ciReleaseGate=false`。本切片没有新增迁移、真实 OSS 或外部业务写入。

客户端增量复核：API/格式客户端/预检面板/盘点页面 **67 passed**；全量前端首轮
1 项并行负载下触发 5 秒超时，其单独复核 **6 passed**，受控并发全量重跑
**83 文件、1,387 passed**。`pnpm build:warehouse` 通过；`verify:pilot-release` 退出 0，
但知识目录仍 `pending / 0`，只允许标记试点构件。仓库安全扫描为 **1,667 文件 PASS**，
`git diff --check` 通过。以上客户端证据不替代新增候选的完整后端/PG16/目标机门禁。

后续增量（2026-09-24）：`FileStorageAdapter.read_opening_count_source` 只接受
`formal-files/v1/opening_count_import/<UUID 前两位>/<UUID>` 的确定性路径和
8 MiB 以内声明大小；先验证 HEAD 的长度、MIME、文件 ID、SHA-256 元数据和 ETag，
GET 以 `If-Match` 锁定相同对象版本，流式计量后复核实际大小及内容 SHA-256，
读取结束关闭响应流。伪路径、元数据漂移、GET 版本变化、超长流、短流和错误摘要
均在单元测试拒绝。该方法**不自行判定任务/范围/人员权限**，调用前仍须完成正式
文件与盘点范围授权；本轮不创建数据库目的、`FileJob` 或确认执行入口。
聚焦 `test_opening_count_private_source_read.py` 加报表对象存储、格式和正式文件服务
**77 passed**；仓库安全扫描 **1,669 文件 PASS**、`git diff --check` 通过。
新代码尚无准确候选完整静态/镜像或真实 OSS 链路证据，不能据此提交或发布。
其后在全新自有 PostgreSQL **16.15** 实例上复核当前迁移至 `0139`，角色、
报表任务约束、九项短信配置夹具与合成期初报表流退出 0；原始报告为
`artifacts/local-current-head-pg16/checks/run-nnsc3j0j/checks.json`，
`cluster-state.json` 证实实例已停止。报告同时标记 `ciReleaseGate=false`、
`syntheticStorageOnly=true`，未覆盖本切片的真实 OSS GET 或完整 CI。

本轮 `0140` 证据：新文件目的/8 MiB/权限聚焦、SQLite 空库升降级 **3 passed**，
读取与格式等聚焦 **35 passed**。实际 PostgreSQL **16.15** 空库迁移至
`20261119_0140`、API 运行角色允许合法盘点源，拒绝无授权用户和超限文件；
原报表任务、九项短信夹具及合成期初报表全链路也退出 0，见
`artifacts/local-current-head-pg16/checks/run-iceityfz/checks.json`，
`cluster-state.json` 显示实例已停止且 `serverExitCode=0`。前两次 PG16 原始失败
`run-rd83071q`（采集角色 HEAD 未更新）、`run-m9qydkwd`（安全目录哈希未更新）
和第三次 `run-wzln9rl_`（超限负例先由旧守卫以 P0001 拒绝，测试断言过窄）
均保留；最终通过不擦除失败记录。`ciReleaseGate=false`、`syntheticStorageOnly=true`，
真实 OSS、完整 CI 和导入确认链仍缺。

后续业务预校验增量：新增内部 `prevalidate_opening_stocktake_scope_count`，复用
正式实盘提交在写事实之前的任务/轮次/范围、授权、冻结、OAM 物料投影、追踪策略、
SN/批次和现场维度校验；返回任务与权限版本、请求摘要、待核实输入项序号，
不写完成、差异、审计或库存事实，也不占用提交幂等键。调用会拒绝已有待写
Session；返回值只代表当次事务的快照，后续确认必须重新读取源文件与权限并
再走正式提交命令。工作簿解析同时保留每项真实 Excel 行号，即使中间有空行
也不将内部规范化序号误作源行号。**该内部能力尚未与私有源文件、持久
`FileJob`、业务错误文件或确认 API 连接，不可称为完整导入。**

内部组合入口另用预期源 SHA 将 XLSX 格式、正式盘点预检和源行号相连；
SHA 不一致先拒绝，待核实项形成不回显单元格值的逐行错误。它只接受已授权
调用方提供的文件字节，**不负责私有 OSS 文件/范围授权**，也不生成可执行命令。
组合入口的本地测试覆盖空行后的真实错误行、源 SHA 漂移及无数据库 DML。

后续只读源文件边界已接通：`opening_count_import_source.py` 在独立事务中重读
当前上传人及 `stocktake.count` 权限，要求源文件是该人完成核验的
`opening_count_import` 用途，上传时人员/权限版本和 OSS provider 均与当前绑定
一致，才调用已有的私有对象 HEAD/GET/SHA-256 读取。外层
`prevalidate_authorized_opening_count_import` 关闭该事务后再用新事务进入正式
期初计数业务预校验，避免将文件锁带入任务优先的盘点锁顺序。源文件预览仍不
创建 `FileJob` 或计数事实；确认时必须重新校验私有源、任务范围和业务事实。
本次聚焦 42 项通过，涵盖非上传人、未完成、用途伪造、权限版本和 provider 漂移
在对象读取前拒绝，以及两段事务的先后顺序。真实 OSS 和 PG16 运行角色对
该新增服务的集成尚未验收。

只读业务入口现已接入 `POST /api/v1/stocktakes/opening/imports/opening-count/business-check`：
请求只包含源文件、任务、轮次、范围 UUID，且必须携带安全的幂等键和请求标识；
服务端从当前身份和已完成的私有文件取源内容，返回源/载荷/业务请求摘要、
任务/权限版本及不回显源单元格的错误行。响应强制 `no-store`；私有 OSS 未配置、
文件不属于本人、权限失效或范围业务校验失败均关闭入口。HTTP 路由没有
`FileJob` 创建、确认或库存写调用，用户看到 `ready=true` 也只能视作当时的
短时预览。源文件与业务组合、HTTP/错误边界聚焦 **46 passed**；完整静态、
真实 OSS 及浏览器联验仍需后续门禁。

新增 PG16 专项已在全新自有 16.15 实例中验证：真实 API 角色将合成私有源
完成为 available 后可按当前身份读取，无授权身份在对象读取前拒绝；原有
来源大小/权限、期初计数业务预校验与报表完整流均通过。准确结果为
`artifacts/local-current-head-pg16/checks/run-jaueic67/checks.json`，实例
`stopped` 且 `serverExitCode=0`。前一轮 `run-obid_p8i/failure.json`
保留：夹具用 Mac 当前时钟生成的完成时间晚于 PostgreSQL 事务时间，守卫正确
拒绝；改为数据库事务时间后重跑通过。仍无真实 OSS 对象读取、持久导入任务、
完整 CI 或设备确认。

2026-09-24 后续门禁：后端三片完整静态合计 **6,845 passed / 3 skipped /
15 subtests passed**；新增备案页脚后相关后端公开入口 **59 passed**、
Web **1,387 passed**、小程序 **1,070 passed**，双构建和试点构件校验退出 0。
详见[开发交接](CONTINUE_DEVELOPMENT.md)。导入仍只有只读业务预检，
没有持久 `FileJob`、错误报告和确认执行，线上也还在使用旧登录首页镜像。

最新聚焦：盘点初盘/复盘/命令状态 **194 passed**，导入格式/HTTP/私有源读取
**32 passed**，包括业务待核实错误最多 999 项加截断提示；仓库安全扫描
**1,673 文件 PASS**。新建自有 PostgreSQL 16.15
空库迁移至 `20261119_0140` 后，API 角色在合成正式期初流程里验证
`count_method=import` 业务预检无事实变化、同一幂等键可正式提交；迁移、
权限、九项短信夹具及报表全链路退出 0，证据
`artifacts/local-current-head-pg16/checks/run-pylub4f5/checks.json`，实例
`cluster-state.json` 为 `stopped` / `serverExitCode=0`。这仍是本地合成
运行，`ciReleaseGate=false`、`syntheticStorageOnly=true`，不替代真实
OSS、当前候选完整静态/Client 或目标机验收。
