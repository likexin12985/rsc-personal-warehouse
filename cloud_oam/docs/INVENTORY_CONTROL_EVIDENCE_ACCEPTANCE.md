# 省级控制库存：隔离证据一致性校验切片

2026-09-05。依据 V1.0 0.3、1.5、4.3 和根 AGENTS.md；不是生产接入或上线记录。

## 实现范围

`backend/app/inventory_control_evidence.py` 提供纯函数
`validate_inventory_control_evidence(expected_json, evidence_json, checked_at)`（关键字参数）。
输入均为独立传入的 JSON/校验时刻；实现不读取环境配置、数据库、文件或网络，不使用业务锁，
不生成 ID，不写 SyncRun/库存/审计/outbox，不返回启动凭据。模块不放在会预先加载数据库配置
的 `formal_services` 包内。没有路由、worker、CLI 或生产边缘采集器调用它。

成功仅为 `evidence_consistent`，永远同时返回：

- `source_authenticated=false`、`catalog_authenticated=false`；
- `projection_published=false`、`start_ready=false`。

这不是发布门禁或授权决策。hash/数量一致只证明传入材料内部一致，不能证明材料真实、目录
独立审批或已经覆盖现实中的所有省仓。当前仅用隔离假数据验收；后续必须有可信目录加载器、
认证暂存来源和不可变采集证据，禁止把请求方自填清单或 `complete=true` 当成这些证据。

## 来源、仓位与分页

独立 expectation 描述完整 feed 的 source system/instance、company/org、scope、目录版本、
目标 region，以及每个 warehouse 的 type/attribute 和显式 position→region 绑定。
目录不得从库存行反推，不通过名称猜省份，不把个人仓库存加到 OAM 控制库存上。

支持既有 `all` 与明确单仓 scope。全国 feed 先验证全部记录及所有映射，最后只返回目标区域的
仓数、仓位数和**库存记录行数**；这些不是库存数量或金额。非目标省份的未映射行同样拒绝。
单仓 scope 只证明独立目录声明的覆盖范围，不能自行声称它是省级全仓目录。

逐仓 query 必须与独立目录的 warehouseCode、warehouseType、warehouseAttribute 精确一致，
querySource 必须为 PC，snDisplayFlag 按仓库类型确定；附加物料、仓位、关键词过滤均拒绝。
逐页必须显式具备 success、page/size、source_total、record_keys 和记录摘要；页号连续、总数
稳定、记录互斥、页大小/总数/摘要和完整重建记录一致。每个预期仓库都必须出现。
零库存也必须有明确 total=0 的成功第一页及空集摘要；缺页、漏仓、未知总数不等于零。

现有 `get_paged` 会把缺失 model/amount/result 转为空，且未保存逐页总数，因此其 `(0, [])`
**不能**转换成新的显式零库存证明。当前 edge 并未生成本契约的采集证据；不得伪造适配。
仓位清单来自独立目录，空仓位通过完整、无仓位筛选的仓库采集证明其无库存行；不宣称已单独
验证源系统仓位目录的存在性、停用状态或目录变更。那些属于后续可信目录流程。

## 全量和增量保持分离

复用原 inventory manifest/batch 的字段、业务键排序、canonical JSON 和 SHA256；不修改已有
ingress schema。source_instance 是独立传输上下文，不是原 manifest 自带字段。
仅接受单 inventory entity 的证据包；现有 `--entity inventory` 的全国 scope 可兼容，混合实体
manifest 不能擅自删去其它实体后假称原始认证材料，未来 loader 需独立验证其提取关系。

本切片以完整证据状态为重放根，支持其后的连续多个 incremental，以及新的完整状态。
每次增量必须精确引用前驱 snapshot ID 和 final hash；重算 upsert/delete 后核对当前 final
count/hash，同时逐仓采集证据必须覆盖重建后的完整集合。删除不存在的键、重复键、漏批次、
跨来源/目录版本/目标区域、错前驱和时间倒序均拒绝。增量零变化仍须有本次全仓采集证据。
现有 edge 每次先完整重采再计算差量，不应凭空加上 OAM 原生 watermark 连续号要求。

这**不是 full-only 准入策略**。最多 64 个重放快照、单 JSON 16 MiB 等是离线验证器的资源边界，
不定义生产保存期或强制全量频率。后续可信封存基线/压缩检查点必须单独设计，不可用散列字符串
冒充已核验基线。历史 SyncRun、旧 `oam` 来源、盘点计数/回放/复核/过账均未修改。

## 时间、数量与信任边界

采集开始≤结束≤snapshot_at≤checked_at，连续证据不能倒序重用旧采集；时间须带时区。
新鲜度从目标区域最早的本次采集开始计算，并报告是否达到 V1.0 的 45 分钟目标。
超过目标仍可返回证据一致，但 `within_freshness_target=false`；不擅自启用硬 TTL。
目录新鲜度/撤销/审批身份尚未认证，不能仅凭这个结果准许建账。

本切片校验来源维度、结构、分页和传输摘要，不把 qtyStock/qtyLock 聚合成正式控制量，不裁定
物料状态、单位、负数或冻结/在途量的会计口径，不把来源 SN 当作正式唯一序列号。这些解析、
映射和数量语义仍需单独验证并封存；当前“记录一致”不能替代“数量可用于过账”。

## 测试与后继放行

新增 `backend/tests/test_inventory_control_evidence.py` 使用假数据覆盖全量、增量、全国筛选、
显式零库存、错误绑定、分页/摘要/前驱篡改、严格 JSON 和时间边界；加入 PG16 工作流静态集。
新增定向回归 195 项通过，包括从现有 edge 源码隔离提取的四个真实纯编码函数，验证 UTF8、
full/delta hash 和 prepare_entity 输出；不 import 生产采集器。另有无配置子进程验证导入及
执行均不加载 SQLAlchemy、app.database 或 app.config。

准确候选 `262b07a34cf508930c3835f2dad993a141920d97` 已验证：

- 本地新增测试与部署门禁契约合计 `209 passed`（2.13 秒）。
- 冻结后端/边缘全量 `2864 passed, 1 skipped`（947.35 秒），退出码 0；运行前后核对
  后端、边缘和 PG 工作流与上述 SHA 无差异，未配置本地真实 PG16，跳过项不计通过。
- [PG16 run 33953508516](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33953508516)
  全绿，job `101272530240` 于 `2026-09-05T08:03:12Z` 完成。静态
  `1931 passed, 1 skipped, 1 warning`（674.84 秒），动态 `1 passed, 1 warning`
  （210.13 秒）；包括容器清理的全部步骤成功。
- [客户端 run 33953508544](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33953508544)
  在同一 SHA 全绿，`2026-09-05T07:48:37Z` 完成；Web `745 passed / 34 files`，
  小程序 `631 passed, 0 failed`，冻结依赖安装、类型检查及构建均通过。

PG16 动态门禁证明原数据库链未被本切片破坏，不证明库存 publisher 已实现。数据库迁移和
ACL 均未修改，Alembic 唯一 head 保持 `20260905_0061`；客户端源码也未修改。
后继文档提交不挪用为新业务代码验收；接管时仍须核对实际 HEAD、远端和文件差异。

下一步仍是可信来源/目录/采集证据的前向数据库与最小权限方案、受控控制投影发布、批次选择、
启动计划和持久恢复。不得复用工单 projector ACL，也不得在未明确复用规则时给 control run
加全局唯一约束。个人备案和外部生产授权边界保持不变。
