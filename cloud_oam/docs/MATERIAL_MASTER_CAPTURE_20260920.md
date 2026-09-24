# OAM 备件主数据采集（7.52）

> 7.53 已接物料专用认证接收、显式上传、传输登记与原回执恢复，见
> [最新接收验证](MATERIAL_CAPTURE_INGRESS_20260920.md)。以下保留 7.52 的实现和验证边界，
> 不再代表当前没有上传入口；来源业务授权和正式物料投影仍待完成。

正式物料 `materials` 已有查询模型，但边缘同步尚无物料主数据实体或正式物料投影器。不能把库存
行、飞书公开查询目录或手工种子当作完整 OAM 主数据，再为它们补写“来源已验证”。本批先增加
实际采集入口和可以独立重验的观察契约；认证接收、授权、版本入库和正式投影仍待接续。

## 采集范围与字段

`edge_sync/oam_material_master_capture.py` 只读取 `/material_type/spare_list`，查询参数仅为
`page`、`size`。范围固定为 `material:spares-visible`，准确绑定配置的 source_instance，表示
当前共享会话可见的备件列表。该接口没有经过验证的企业过滤或全 ERP 覆盖语义，因此证据不包含
猜测的 company/org 绑定，也不宣称是完整 ERP 物料库。缺失物料后续必须阻断对应正式投影。

- 必填准确字段为 `materialCode`、`materialName`、`unitCode`、`unitName`、`materialStatus`。
  编码须为规范大写编码，不能自动去空格或变更大小写；不从 platformMaterialCode、name、status、
  unit 等别名补猜。缺字段、空字段、超长、异常类型或重复编码整次失败。
- 只额外保留已知的 `regularModel`、`isSnEnable`；缺失、null、空规格和原始布尔/整数/字符串
  保持差别。来源状态的整数 `0` 与字符串 `"0"` 不混同，不自行解释 active/inactive。
- 现有本地导出脚本之间的状态解释不一致，因此本批不沿用其状态映射、重复行择优、库存/飞书补齐
  或名称挑选规则。正式状态、基本单位及跟踪策略需要准确字典和独立批准。
- 来源更新时间尚无可靠字段契约，`source_updated_at` 固定为 null，不把采集时间写成来源更新时间。
  每条记录的 `mm-v1:` 版本来自已接纳字段的规范 JSON 摘要，属于观察内容版本。
- 未列入白名单的字段不归档、不打印；页面证据摘要只代表已接纳字段，不声称是完整 HTTP 响应摘要。

## 一致性与归档

完整读取全部分页后再读取第二轮，逐页验证成功布尔值、严格整数总量、实际条数和唯一编码，并
比较两轮完整记录集合及字段类型。分页顺序可变化，成员、名称、单位、状态类型或可选字段变化
则失败；两次明确成功的空列表只记录 empty_observation，不删除正式物料。

契约保存两轮起止时间、页序、页数/总量、页内编码与字段摘要、按编码排序的记录及最终摘要。
后端纯验证模块 `app/material_master_capture_evidence.py` 独立重建每页和最终集合，校验准确
来源绑定、严格结构、类型、版本与时间。采集时钟不得倒退，未来时间及超过 45 分钟的采集被拒绝。
单包上限 16 MiB，最多 100000 条/10000 页，每页最多 1000 条。

两轮一致性不能证明 OAM 事务快照，也不能证明变化后又恢复的记录未被修改。文件摘要不提供身份
认证。校验报告始终返回 source_authenticated/full_catalog_verified/master_source_evidence_verified/
projection_published/start_ready=false；不能把观察报告或手工构造文件作为可信主数据凭证。

归档只允许本人所有、权限准确为 0700 的已有私有目录，拒绝已观察到的符号链接路径；不自动改动
其他目录权限。新文件以 O_EXCL/0600 写入并同步文件和目录，名字绑定 capture_id；同名文件永不
覆盖。写入或 fsync 失败保留已有文件，报告 archive_unconfirmed，不能把失败视作没有产生文件。
检查目录与打开目录之间并非完整的祖先目录事务锁，运行目录应由本机用户独占管理。

## 显式本地入口

从已配置原本机共享业务客户端的仓库运行；托管仓库不附带 `work/` 中的会话和客户端，缺失时报
local_read_client_unavailable，不复制凭据或使用另一网络入口。采集之前运行本机
`ensure_local_edge.py --compatibility`，核验原 Edge 资料与 9224 兼容服务归属；失败时不调用
登录探测、业务接口或其他网络入口。随后调用全局健康检查，仅
要求 oam 组件。只有该组件明确 auth_required 才报告登录失效；超时、缺页、未知或其他组件失败
均不替代该证据。业务读取复用原 `oam_read_client.post_json(..., retries=0)` 和共享 Edge 适配器。

以下为部署模板，路径与来源 ID 必须由本机配置确定；本次开发未执行真实采集：

```bash
.venv/bin/python edge_sync/oam_material_master_capture.py \
  --capture --source-instance EXACT_ALLOWLISTED_SOURCE_ID \
  --archive-dir /private/local-material-captures

.venv/bin/python edge_sync/oam_material_master_capture.py \
  --inspect-file /private/local-material-captures/material-master-EXACT_CAPTURE_ID.json \
  --source-instance EXACT_ALLOWLISTED_SOURCE_ID
```

inspect-file 只重验本地文件，不联网、不运行登录健康检查；stdout 仅输出摘要/条数/观察范围与文件名，
不显示物料正文。help 不依赖未托管业务客户端或数据库。没有自动上传、调度安装或入库模式，原同步器
默认实体、接收 schema、数据库 RLS 和权限保持；物料文件不会混入既有库存 HMAC 凭据。

## 验证与后续

本地合成来源覆盖完整两轮、空列表、全部必填字段、类型/重复/缺页/变更/时钟/大小限制、篡改重验、
私有归档与结果未知、CLI 采集后离线回读、共享客户端调用及精确健康检查。来源响应在测试中替换，
不代表实际 Edge 传输、真实 OAM 字段验收或全 ERP 覆盖。静态 CI 门禁已加入该专项。

首轮采集与 CI 拓扑 **101 passed，0.45 秒**；与原库存采集、正规化、正式映射及边缘安全
联合曾通过 315 项，补齐原 Edge 归属前置检查后最终 **323 passed，17.33 秒**。
依赖检查、编译、CLI help 通过；本批未重跑后端全量，
未改变迁移或权限，因此保留 7.51 的 SQLite head 往返证据，不新增真实 PG16 通过声明。

新证据目录为 `cloud_oam/artifacts/material-master-capture-20260920/`，精确源码、命令、日志及
结果见 source-manifest.json、verification.json。7.51 原证据保留，迁移 head 仍为 20261025_0115。

下一步必须接物料专用认证接收、精确来源身份/范围授权、不可变采集证明、正式来源版本和物料投影，
并复核本地 `oam` 与线缆 `starcharge_oam` 的准确 SourceSystem 绑定，不按名称默认为同一 ID。
未知来源更新时间需要前向迁移及查询契约协调。实际状态/单位/SN/批次语义、密钥生命周期、控制
publisher/RLS、发布事务内重验、启动恢复和持续对账仍未完成。真实 PG16、公开飞书数据导入、
通知渠道与 UAT/恢复/压测验收继续保留。本批不形成上线或提交许可。
