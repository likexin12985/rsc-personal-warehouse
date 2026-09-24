# 物料审核发布与不可变来源版本（7.56）

本批把已授权的 0116 物料采集回执接入正式目录：总部在当前有效网页登录会话中，按准确
回执和原始行 hash 审核每个 SKU 的状态、基础单位、追踪方式、数量精度和小数规则。
服务在同一事务创建不可变发布事实、来源版本、正式物料投影、初始追踪策略和审计。
版本与投影已实现，不代表真实来源语义已经获得业务确认，也不代表库存控制发布或期初启动完成。

## 输入与结果

- 只接受当前 0117 来源授权覆盖采集开始至接收完整区间、并且现在仍有效的回执。
  45 分钟新鲜度、传输登记、准确来源、撤销记录及审核文件均重新验证。
- 每个采集行必须恰好有一个按 SKU 排序的明确审核决定，绑定精确原始行 hash；单批 1–1000 行。
  不推断原始 materialStatus 或 isSnEnable 的含义；缺失、类型改变与 null 都保留在原始版本中。
- 名称、型号直接来自被审核的采集，来源更新时间保持真实未知 null。版本号
  `capture-v1:<capture_id>` 明确表示本次观察，不伪装为 OAM 原生更新时间或原生版本号。
- 首次发布新建物料及策略；更新保持物料/外部对象/策略 ID，追加版本并闭合旧版本有效期。
  A→B→A 保存三次观察，不重写第一版。可见采集中缺少某 SKU 不执行删除或停用。
- 旧或时间重叠的采集、来源实例变化、已有但无发布证据的 SKU/外部身份冲突整批拒绝。
  已建立物料的单位或追踪/精度策略改变必须另走迁移，当前入口不接受这种更新。
- 不创建库存账户、库存交易、余额、盘点、控制数、同步批次或通知 Outbox；
  `projection_published=true` 只描述本次物料发布，`full_catalog_verified` 和 `start_ready` 仍为 false。

## 数据库与访问边界

前向迁移 **20261029_0119** 新增 `material_projection_publications` 与 `material_projection_lines`，
旧 0118 及此前迁移文件保留。发布、行和审计以真实外键、hash、原命令与版本链相互绑定。
新增 15 个 ALWAYS 触发器，包含 7 个延迟约束触发器；提交时重验来源授权、会话、完整行集与
当前/历史版本。已管理的物料、单位、策略、版本内容及当前指针不能脱离已封存发布事实修改。
有发布事实或孤立发布审计时，降级先拒绝，不删除数据以满足旧结构。

复用总部全国范围 `material_source.authorize`：每次发布还必须有独立的逐行语义决定、审核文件
和预览 hash，已有来源授权本身不能自动发布。直接数据库连接必须是 `star_oam_migrator`、
READ COMMITTED、准确数据库名称及 PG16。API、edge、既有 OAM projector 无新表权限或新写权限；
backup 仅 SELECT。图校验触发器使用固定 search_path 的 owner SECURITY DEFINER，只读校验，
其函数执行权限撤销给 PUBLIC 和运行角色；来源/发布写入守卫为 SECURITY INVOKER。

生产需要先完成 7.55 客户端 1.0/2.0 兼容顺序，再迁移至 0119；当前候选尚未提交或部署。
SQLite 只验证领域行为、结构往返及发布事实只追加，不能替代 PostgreSQL 图约束/RLS/并发验证。

## 受控命令与结果恢复

入口为 `scripts/configure_inventory_control.py --material-publication-file <文件>`。
文件外层包含 `command` 和 `expected_authorization_version`；默认 preview，展示规范化内容、
原来源证明、目标当前版本和 `review_sha256`。apply/status 必须带回同一审核 hash 和原命令。
命令字段为 `receipt_id, capture_sha256, decisions, evidence_file_id, evidence_sha256, reason,
idempotency_key, request_id`。每行 decision 包含 `sku_code, raw_sha256, status, base_unit,
tracking_mode, quantity_scale, allow_fraction`，没有默认业务语义。

文件上限 1 MiB，拒绝重复 JSON 键、未知字段及凭据混入。网页登录凭据通过隐藏终端输入或
继承 FD 提供，不能放入命令参数、JSON、环境变量或日志。配置沿用受管 owner 连接环境，
CLI 检查数据库、PG16、0119 和直接 owner；不执行迁移、采集或上传。

所有 material 发布先锁当前会话和总部身份，再按排序 SKU 获取事务锁并锁定来源、文件和
当前对象/策略；审核 hash 变化即拒绝。写入与审计后再检当前来源/会话及完整版本图。
COMMIT 确认丢失只输出 `read_exact_status`，不自动重试；status 恢复原发布结果，
即使之后来源被撤销，历史事实仍可核验。恢复原结果不赋予再次发布或库存操作资格。

## 当前验证与剩余门槛

最终本地测试结果见本节后续记录及 `artifacts/material-publication-20260920/verification.json`。
保留首次失败记录：首次设置版本指针触发 ORM 自动更新时间，与封存时间不一致；已显式固定
该 UPDATE 的时间并覆盖初次/后续版本。测试角色最初未授予目录读权限，已修正合成测试权限。
新增迁移导致静态旧表数、函数/触发器数不匹配，已按新 manifest 更新。

真实 PG16 helper 已接入受保护的门禁，准备验证同命令并发仅发布一次、A/B/A 历史、API 目录读取、
绕过业务服务的修改拒绝、精确行锁、backup 回读及有事实时的降级保留；**尚未运行**。
仍需当前候选的真实 PG16、实际来源授权/语义文件、已有 SKU 迁移审核、PC 交接和持续同步。
公开知识首页的指定飞书源仍待真实导入；该数据与正式物料发布是不同来源、不同范围。

最终联合验证：**600 passed，1 warning，67.71 秒**。包括本批物料发布/CLI/迁移与保留、
0116/0117/0118 回归、目录 HTTP/小程序解析、当前配置/权限/启动边界、完整 SQLite head 往返
和 PG workflow 静态拓扑；不是全量后端或真实 PG16。依赖检查、语法编译、CLI help、
仓库安全检查及 diff 空白检查通过。双端代码本批未改，7.55 的 Web/小程序实测证据保留。

证据目录 `artifacts/material-publication-20260920/` 保留初次失败、最终日志、源码清单、
验证 JSON 与交接状态前后快照。发布候选仍为未提交工作树，不能把本地通过记为上线验收。
