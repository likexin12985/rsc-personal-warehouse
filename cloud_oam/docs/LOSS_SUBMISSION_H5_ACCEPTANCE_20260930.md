# 本人报损发起 H5 接入与验收

基线为正式 V1.0 的本人保管责任、报损冻结、两级审批、独立处置和不可变流水要求。本页只记录报损发起切片，不把提交视为审批通过、退回或报废完成。当前尚未提交、未部署。

## 当前实现

- `formalLossSubmission.ts`：当前本人可用账户、十进制数量、实物 SKU/SN/二维码证明、上传凭证、整体预检、原命令和精确回执校验。报损请求 hash 与当前 Python intent 一致。
- `lossSubmissionRecovery.ts`：按本人及个人仓位置保存完整命令和确认快照，保存后回读才能发送一次；跨标签 Web Locks；结果未知不重发、不换 key。读取失败、损坏、身份变化、授权撤销和页面离开均保留原记录。封存有单独确认，先回查，再按原坐标封存并再次回读。
- `lossSubmissionAdapter.ts`：全部业务请求绑定 `apiNoReplay`，当前人员、有效角色授权及权限指纹前后核验。报损提交/封存需要当前写权限；撤写后仍可按读权限回查，不依赖重新读取报损来源。
- 四份数量/SN × 已提交/已封存的合成服务响应，已接入正式前后端合同测试；不含真实业务数据，不作为公开知识目录。

`LossSubmissionForm.tsx`、`FormalLossSubmissionPage.tsx` 和私有 `/loss-reports/new` 导航已接入本人报损。页面提供本人库存选择、SN 只读查询和实际三码输入、正式凭证上传、原因、整单预检与明确确认，以及待核验请求回查/单独确认封存。上传中断、重复确认、损坏存储和只读恢复均有测试。相机扫码与真实 OSS 尚未验收，不能将手输/键盘扫码算相机能力。

`InventoryProjectionOut` 仅在 JSON 输出规范化 projected_at 为 UTC；内部 datetime 值保持不变。新增当前后端服务生成的 SN 查询 fixture，修复 SQLite 无时区测试值与报损来源时间戳不一致的问题；PG16 的 timestamptz 仍按同一时刻输出。无新迁移及权限种子变更。

## 正式接口

路径相对 `/api/v1/stock-operations/loss-reports`：

| 动作 | 路径 | 约束 |
| --- | --- | --- |
| 本人可用来源 | GET /sources | submit_loss，已建立可信期初和有效保管关系 |
| 实物选择预检 | POST /source-preview | 无库存写入，不代表后续提交授权 |
| 整单预检 | POST /preview | 重新核验全部来源、凭证、权限和库存方案 |
| 报损提交 | POST 空路径 | 原 request_id/key/expected_plan_hash；提交后才有冻结流水 |
| 原请求回查 | POST /request-lookup | read；not_found.retry_permitted=false，不自动重发 |
| 永久封存 | POST /request-seal | submit_loss；原请求坐标及 source_location_id |

SN 选择可使用现有只读接口 `GET /api/v1/inventory/personal/me/accounts/{stock_account_id}/serials`，权限为 inventory.read，支持 limit≤100、after_id、精确 serial_no。返回不含二维码；应保留实际扫码/手输证明，禁止预填实物标签内容或要求用户填写内部 UUID。

## 已核验及边界

证据位于 `artifacts/loss-submission-h5-next/`，该目录被 Git 忽略：

| 验证 | 证据 | 结果 |
| --- | --- | --- |
| 合同、原请求恢复、HTTP 适配 | contracts-recovery-adapter-v2.log | 49 passed |
| 当前后端 schema、intent/hash 和两种回执互验 | backend-parity-v1.log | 4 passed |
| TypeScript | typecheck-v3.log | exit 0 |
| 首次完整前端 | frontend-full-v1.log | 1832 passed / 6 failed；未通过 |
| 失败三个既有文件低并发复验 | legacy-focus-v1.log | 106 passed，未改断言/超时 |
| 完整前端低并发复验 | frontend-full-v2.log | 1838 passed，105 文件，132.54 秒，exit 0 |
| 仓库安全检查 | repository-safety-v1.log | PASS，exit 0 |

以上为页面加入前的历史证据。页面、SN 查询和时间戳修正后的当前验证：

| 验证 | 证据 | 结果 |
| --- | --- | --- |
| 页面/路由/合同/恢复/HTTP 适配 | page-focused-v4.log | 77 passed |
| 当前 schema、SN 来源及库存读取回归 | backend-inventory-parity-v1.log | 62 passed |
| 完整前端（新增 4 个异常页面用例前） | frontend-full-v3.log | 1862 passed，107 文件 |
| 最终完整前端 | frontend-full-v4.log | 1866 passed，107 文件，exit 0 |
| 小程序 | mini-full-v1.log | 1079 passed |
| 最终 TypeScript、公开/私有双构建 | build-public-v3.log、build-private-v3.log | exit 0；私有 bundle 体积提示仍在 |
| 公开入口隔离 | public-entry-v3.log | 通过；内容 catalog pending/0 |
| 期初共享协议 / 依赖 | opening-protocol-v1.log / pip-check-v1.log | PASS / clean |
| 浏览器合成验收 | browser-acceptance-v1.json、browser-*.png | 数量/SN、390/1280、回执丢失刷新恢复、撤写后只读回查通过 |
| 当前仓库安全 | repository-safety-v2.log | PASS，1984 文件，exit 0 |
| 本人报损 H5 数量/SN PG16 | native-pg16-v2.log、native-terminal-v2.json | 两种模式 passed，临时库均 stopped/serverExitCode 0，1794 文件零漂移；不覆盖发件读取候选 |

手机实测发现上传控件宽度使页面 390px 溢出为 430px；已通过本人报损页限定 CSS 修正，回测宽度为 390px。SN 实物字段保持空白，确认摘要展示实际选择的 SN。浏览器使用合成 adapter 和模拟上传，未连接真实业务服务。

新增上传测试第一次双构建因 readonly mock 赋值失败，已用 mockImplementation 修正并通过最终双构建。PG16 v1 为修正最终源码清单主动中止，自有临时库正常停止；不是通过证据。v2 冻结当前源码重新验证，最终状态见开发交接。

首次全量失败集中在发运、盘点和需求旧页面，多数为 5 秒超时；同源码降低并发后聚焦及完整复验均通过，未改断言、代码或超时设置，支持负载影响的判断。两次日志均保留。前一收货版本的 PG16 清单不能直接改名为本切片证据。

## 下一步验收

1. 最终完整前端、单 head、本地数量/SN PG16、停库与源码清单证据已收齐。按开发交接继续准确 SHA 远端 CI 与真实验收；新增发件读取另做整合及对应数据库门禁。
2. 最终差异审查与安全检查后，证据齐全才提交；前序版本 CI 不覆盖本候选。
3. 真实身份、OSS 上传、移动设备及相机/键盘扫码分别 UAT，长库存列表和 SN 分页性能需要真实规模验证。
4. 继续报损派生退回发件 HTTP/恢复、处置和反向冲销缺口；不把本次冻结提交算完整报损闭环。
