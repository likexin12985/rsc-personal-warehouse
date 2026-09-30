# 报损审批查询、照片授权与私有 H5 本地验收

> 2026-09-30 14:23：本文件只覆盖已发布 ff25289 的审批版本。新增退回收货/入库 H5 尚未提交，不能复用这里的浏览器、全量或 PG16 结论；最新状态见 [开发交接](CONTINUE_DEVELOPMENT.md)。

核验日期：2026-09-30。基础提交 `1f65dcb`，本批提交以 Git 历史及 ignored `artifacts/loss-review-queue-next/commit-evidence.json` 为准。本记录仅证明以下本地候选，不是生产放行。2026-09-30 13:24 交接回读：候选已提交并发布为 ff25289，准确 SHA CI 尚未完成，当前状态见开发交接。

## 问题与实现

区域/总部已有独立审批及原请求回查/封存，但缺少可用的待办、详情、审核照片权限和私有 H5 页面。本批接入按当前同一授权绑定限定范围的只读查询与照片下载，以及 `/loss-reviews` 页面。公开首页仍无登录，公开小程序只发布知识查询；私有仓库入口仍为 `/xx`。

- 待办/全部记录采用 UUID 游标，每页最多 20 行，空待办页仍可有下一页；申请人不自审，原单或审批证据损坏阻断该对象。
- 展示原明细、数量/SN、原提交摘要与精确区域审批坐标；不暴露恢复 request/key、库存账户、二维码或 storage key。
- 照片必须唯一绑定原报损，跨业务混合绑定拒绝。当前读权限可以独立于写权限存在；签发继续沿用文件服务锁、短时链接与审计。
- H5 区域核实/总部终审分别确认；总部逐行显式选择处置和理由。审批不改库存，总部终审后仍待处置。
- 发送前持有 Web Locks 并保存完整原命令；超时或回执不明保留记录，刷新重入后回查原请求，不自动重发。只有精确 found/sealed 和当前身份/权限前后稳定时才清理；永久封存需独立明确确认。
- 当前人员、授权版本和范围摘要均重查；存储损坏/不可读、跨标签竞争、切页或身份变化均拒绝误清理和新写。
- 390px 窄屏实测发现 grid/table 撑宽与复选框被全局样式拉宽，已修复局部 min-width、标题换行及 checkbox 尺寸。移除预览 CSS 草案后再验：文档宽度 390px、复选框 13px。

没有新数据库迁移、生产权限种子、真实短信、真实 OSS 或外部业务写入。当前 head 仍为 `20261205_0156`，历史迁移未修改。

## 本地证据

路径默认相对 `cloud_oam/artifacts/`；集合有重叠，不相加当覆盖总数。

| 验证 | 终态证据 |
| --- | --- |
| 正式查询/HTTP、分页/范围/照片审计 | loss-review-queue-next/integrated-v2.log：84 passed |
| 文件服务/既有报损附件兼容 | file-compatibility-v1.log：44 passed；loss-evidence-compatibility-v1.log：16 passed（同目录） |
| 区域权限夹具 | loss-review-queue-next/real-scope-fixture-v1.log：2 passed |
| 前端全量（含审批 42 项及路由 6 项） | loss-review-ui-next/frontend-full-v1.log：95 文件、1699 passed |
| 小程序全量 | loss-review-ui-next/mini-full-v1.log：1079 passed |
| 当前后端 schema/envelope/hash 与前端合成 fixture 一致 | loss-review-ui-next/backend-fixture-parity-v2.log：4 passed |
| TypeScript | loss-review-ui-next/integrated-typecheck-v4.log：exit 0 |
| 最终 CSS 后公开/私有构建 | loss-review-ui-next/build-public-v2.log、build-warehouse-v2.log：exit 0 |
| 双入口产物 | loss-review-ui-next/public-entry-v2.log：开发检查通过；知识目录 pending/0 条，不是知识上线 |
| 共享期初协议 | loss-review-ui-next/opening-protocol-v1.log：通过 |
| 仓库安全/依赖/diff | repository-safety-v3.log：1944 文件 PASS（loss-review-ui-next）；pip check 无损坏依赖；git diff --check 通过 |
| 浏览器合成操作 | loss-review-ui-next/browser-recovery-final.png：未知请求刷新重入后仍保留；browser-layout-final.json：正式 CSS 390px 无整页溢出 |

最后仅三行 CSS 修改发生在前端全量测试之后；该修改通过重新双构建及实际浏览器验证，native-v4 包含最终 CSS。未将合成 adapter 的页面验证称为真实后台/真实设备 UAT。

## PostgreSQL 16 完整终态

`loss-review-queue-next/native-terminal-v4.json`：runner 45500 exit 0，两库完整通过，停库正常。2026-09-30 12:52 重新生成当前 manifest，与两份 1759 文件清单完全一致。

| 模式 | 实例（local-stock-loss-review-seals-pg16/checks/ 下） | 完成时间（上海） |
| --- | --- | --- |
| quantity | run-18vij09f | 09:57:30 |
| serial | run-l35n10_y | 10:07:33 |

两模式均覆盖区域/总部 `requestRecovery.reviewQuery`：真实 API 角色只读 HTTP、范围分页、撤销写权限后读取、照片下载仅增加审计、撤销读权限后不签发。原提交/封存/审批 HTTP、实际 COMMIT、真实锁竞争、空库迁移往返、运行期权限目录及保留审批封存历史拒绝降级同时通过；两库 `sourceDrift=[]`、`stopped / checks=passed / serverExitCode=0`。

本轮 `retainedRegionalReviewBlocksDowngrade` 和 `retainedHeadquartersReviewBlocksDowngrade` flag=false 表示未重复旧 0147/0148 单独降级子流程，其既有独立门禁继续保留；不能把 false 写成该子流程本轮通过。文件使用 synthetic storage，不代表真实 OSS/KMS 验收。

失败历史保留：v1 区域夹具选错组织；v2 Session 外 ORM ID 访问；v3 因浏览器布局修复主动中断，均正常停库。类型检查 v2 缺 Node 测试类型已按既有 importActual/静态 JSON 方式修复；路由首轮断言遗漏 no-cache 参数，后由全量回归验证。失败轮次不改写为成功。

## 仍需完成

新准确 SHA 客户端/完整 PG16 CI；真实身份、短信 PNVS、OSS/KMS及设备 UAT；私有 H5 报损发起、退回收货/独立入库页面和发送侧 HTTP/恢复；反向冲销、人员调拨、离职交接及完整基线生产验收。父 `3e67e51` CI 三片静态均被中断/取消，不是通过，也不能替代本批准确 SHA。

后续见 [开发交接](CONTINUE_DEVELOPMENT.md)和[退回 H5 接入验收](LOSS_RETURN_H5_ACCEPTANCE_20260930.md)。
