# 公开知识目录原表导入交接（7.72）

本批补齐导入来源校验，真实数据仍未接入。网站首页保持“交流备件知识大全”，星星后台
管理链接仍跳转 `/xx`；小程序只注册公开知识页。没有提交、推送、部署或外部业务写入。

## 已关闭的代码缺口

原工具允许把任意整理后的 JSON 与一份无关导出文件一起提交，仅登记文件摘要。
新入口移除 `--rows`，要求原始 XLSX 和明确的审核计划：

- 原表 URL、原始字节 SHA256、完整工作表集合必须匹配，每页明确包括或排除。
- 包括页指定精确表头、字段列、首尾行、预期条数和排除行理由；隐藏页须明确允许。
- 只提取选定的编码、名称、分类、型号和说明，记录精确表名和行号。型号未知留空，
  不推断适配关系；数据文本仅裁掉前后空白，来源表名逐字保留。
- 公式、错误、数字及日期不能冒充公开文本；空编码、字段超长、额外字段和重复来源拒绝。
- 忽略不可靠的 XLSX 缓存尺寸，预先限制实际坐标、展开体积和扫描范围；拒绝宏、外部
  工作簿链接、重复 ZIP 条目、XML 实体和重复审核 JSON 键。
- 默认仅校验。`--write` 先暂存两份目录、检查期间改动，再替换并回读；第二份写入失败时
  恢复仍属于本次写入的第一份，保留检测到的并发外部编辑。临时文件会清理。

这不是跨文件事务；断电、强制结束或恢复时磁盘故障仍可能留下不一致，必须重新检查两份
目录及构建。双端产物门禁拒绝不一致，网页包装门禁拒绝过期构建，不能绕过后续检查。
原表身份、导出是否最新版、字段可公开性和 `verifiedAt` 仍需人工核验；文件摘要不证明这些事实。

## 输入与操作

原始工作簿及审核计划保存在 Git 之外，禁止把内部字段、排除理由或原文件提交。
先安装独立工具依赖，不把这些库加进 API 运行依赖：

```sh
cloud_oam/.venv/bin/python -m pip install -r cloud_oam/scripts/requirements-public-knowledge.txt
```

固定 openpyxl 3.1.5、et_xmlfile 2.0.0、defusedxml 0.7.1；拒绝没有 XML 保护的运行环境。
XML 保护依据 [openpyxl 官方安全说明](https://openpyxl.readthedocs.io/en/stable/)。

审核计划 schemaVersion 为 1，下面仅说明结构，字段和范围必须换成实际核对值，不能直接
当作真实表格映射使用。必须为全部工作表逐一填写 include/exclude：

```json
{
  "schemaVersion": 1,
  "sourceUrl": "https://wbenergy.feishu.cn/sheets/ZVnis9SUzhfiU9t9EK4csLvEnNg?sheet=1eOLBU",
  "sourceSha256": "填写原始XLSX文件的64位SHA256",
  "sheets": [
    {
      "name": "准确工作表名",
      "action": "include",
      "includeHidden": false,
      "firstRow": 2,
      "lastRow": 3,
      "expectedRecords": 2,
      "fields": {
        "code": {"column": "A", "headerCell": "A1", "headerText": "原始编码表头"},
        "name": {"column": "B", "headerCell": "B1", "headerText": "原始名称表头"},
        "category": {"sheetName": true},
        "model": null,
        "note": null
      },
      "excludedRows": []
    },
    {"name": "另一准确工作表名", "action": "exclude", "reason": "具体排除原因"}
  ]
}
```

`category` 可使用准确工作表名，也可像 code 一样绑定实际列。model/note 可绑定原表列或
明确设 null；不能写入自拟内容。排除行使用 `{"row": 4, "reason": "具体原因"}`，必须位于
选定范围内；范围之后还有内容时拒绝，不能悄悄漏掉尾部记录。空行不计记录数，只有私有
列内容的行也不能自动忽略，须明确排除。表头必须位于选定数据之前且在同一列。

```sh
cloud_oam/.venv/bin/python cloud_oam/scripts/import_public_knowledge.py \
  --review-plan /path/outside/repository/review.json \
  --source-export /path/outside/repository/source.xlsx \
  --source-url 'https://wbenergy.feishu.cn/sheets/ZVnis9SUzhfiU9t9EK4csLvEnNg?sheet=1eOLBU' \
  --verified-at '实际核对时间，含时区'
```

确认 dry run 的包括/排除页数与条数后，同一命令加 `--write`。输出仅包含计数、尺寸、
原文件/审核计划/目录摘要，不打印原单元格和排除理由。原文件不修改；成功更新网页和
小程序两份静态目录后，重跑目录测试、类型检查、双构建和以下正式门禁：

```sh
# cloud_oam/frontend 内
pnpm build
pnpm build:warehouse
pnpm verify:release
# 仓库根目录内
node cloud_oam/scripts/verify_public_entry.mjs --release
```

## 当前证据及边界

本地证据在 `artifacts/public-knowledge-import-20260920/`，仅使用合成 XLSX 测试夹具。
源码清单、终端退出码和日志摘要见该目录的 manifest、verification 与 test-commands 文件。

| 检查 | 当前结果 |
|---|---|
| 导入/来源/CI 拓扑/快照联合 | 70 passed；含 1 项既有 Starlette 依赖警告 |
| 网页知识查询与目录包装 | 2 文件 / 29 passed |
| 小程序知识查询与门禁合同 | 67 passed |
| 独立导入依赖 | 安装完成；pip check 通过 |
| TypeScript、公开及 `/xx` 构建 | 通过；保留个人仓约 998 kB bundle 提示 |
| 真正 XLSX 导入命令 | 合成文件 dry run 与双端写入、摘要回读通过；原文件不变 |
| 当前真实目录 | 两端均 pending / 0；正式发布门禁应拒绝 |
| 后端生产服务/迁移/部署/Edge | 与 7.71 的 452 个对应文件摘要一致；128 个迁移未变 |
| PostgreSQL 16 | 本批未重跑；7.71 本地 PG16.15 证据保留，不能称为当前完整远端 CI |

static_safety CI 已安装独立依赖并显式执行目录和来源测试；导入脚本/依赖变更加入触发范围。
远端 CI 尚未执行。本批不修改生产数据库角色或权限，不启用期初启动开关。

## 原表访问和下一步

当前环境没有 lark-cli 或可用飞书表格连接器，指定原表仍未取得完整最新版导出。原 Edge
共享连接状态为 blocked，autoRetry=false，lastErrorCode=AUTHORIZATION_OR_CONNECTION_FAILED。
这不是飞书登录失效证明。连接控制工具明确要求 `only a user-authorized maintenance step may use connect`；
已请求单次恢复授权，尚未收到答复，因此没有调用 connect、重试连接或更换浏览器资料。
也可使用用户提供的最新版本地导出路径；历史 2026-07-29 目录不能替代当前 15 页原表。

后续取得导出后逐工作表审核、实际导入并核对页面与原表，再跑正式公开门禁。整体上线仍缺
当前候选完整 GitHub PostgreSQL 16/客户端门禁、生产迁移与 PITR、负载/灰度、真实通知供应商
及回调、连续三天对账和多角色真机验收。不得用合成测试数量估算上线完成率。
原待批候选提交例外保持，证据未齐不提交、不推送、不部署；7.71 及更早证据不覆盖修改。
