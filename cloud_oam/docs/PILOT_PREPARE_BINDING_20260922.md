# 8.23 试点 prepare/start 候选绑定

日期：2026-09-22。范围为现有 `06f6/oam` 工作树，未提交、推送、部署、连接目标 Docker 或执行真实业务写入。
飞书公开知识源仍暂缓，正式公开发布检查保持严格。本文件补充 8.18 部署说明，不改写历史验证结果。

## 为什么必须修复

旧入口可在从未 prepare 时直接 start，也允许两阶段之间换有效配置或标签而继续启动。三条原始反例保存在
`artifacts/pilot-prepare-binding-counterexamples-20260922/`，使用合成 Docker，无目标环境访问。
主任务因此受控停止完整静态 52984：退出 2、11 passed / 1 skipped、1,470 输入无漂移。该轮不是通过证据。

## 绑定内容与执行顺序

`scripts/deploy_pilot.sh` 委派标准库协调器 `scripts/pilot_release.py`。同一项目只允许一个部署操作，
锁忙在任何 Docker 调用前拒绝。prepare 对候选文件、实际解析配置、原始 env/Compose 文件、静态 bind 内容及权限、
HTTPS/私有路径和可选 resolve host/IP 做整体摘要。敏感环境仅在内存与私有临时 Compose 文件，输出只有安全错误码。

构建仅支持现有 DB/API/Web 三个上下文与 Dockerfile，Web 固定 `RELEASE_PROFILE=pilot`；其他 backend build
也必须指向同一 backend Dockerfile。拒绝外部或远端 context、additional_contexts、inline Dockerfile、其他 build args，
以及未绑定的 Compose secrets/configs/env_file。候选中的 backend/app/.env、frontend/public/.test_*、运行命名目录内的
实际 COPY 输入仍纳入摘要，不能借运行工件排除规则漏过。

每次 prepare 用唯一内部标签构建三个镜像并读取其 ID，再将这三个 ID 显式发布到用户候选标签。
跨项目共用公共标签即使竞争，也不能把其他项目构建的 ID 当成本次构建结果。启动和迁移全部使用已捕获不可变 ID，
随后读取实际容器的 Image、归属与状态。成功回执同时绑定 DB 容器 ID、镜像 ID、数据卷名称/来源以及正式迁移 HEAD。
HEAD 只从已绑定 DB 容器内固定 Unix socket、只读 psql 查询，不接受外部 DSN；执行前清除
`PGHOSTADDR/PGSERVICE/PGSERVICEFILE`，数据库名只接受最长 63 字符的 ASCII 标识符，拒绝 conninfo/URI，并使用 `psql -X` 禁止启动文件改写连接或执行额外 SQL。

prepare 仅在迁移、容器身份、HEAD、镜像标签及全部输入再核验成功后原子创建回执；已有成功回执永不覆盖。
start 无回执、回执损坏/权限错误、源码/配置/镜像/挂载/数据库漂移均在 pin/API 变更前拒绝。
每阶段前后再核验输入，pin gate 后、API/Web 后核验 DB 身份；运行中公共标签再指向也仍执行已绑定 ID。
SIGTERM/超时会停止该次 CLI 子进程组、释放锁并不给成功回执；Docker daemon 可能已产生部分结果，须回读现场，
不能把 CLI 被中止解释为事务回滚。

Compose canonical JSON 本身已把 `$` 表示为 `$$`。协调器原样保存规范表示，执行前用同一 Compose 再次解析并要求整个
文档等值；只在语义校验和真实挂载路径上受控解一层。不会额外双写美元符号改变密码、命令或路径。

## state、回执及长期快照

默认 state 为部署用户的 `$HOME/.local/state/rsc-pilot`；覆盖值 `PILOT_STATE_DIR` 必须是工作树外的受控绝对路径，
不可经过符号链接，state/项目/回执目录属于部署用户且权限严格 0700，回执严格 0600。
两个阶段须由同一部署身份使用同一 state、项目、标签、文件和烟测目标执行。

成功回执位置为 `<state>/<project>/receipts/<tag>.json`。其中仅有聚合摘要、Docker 身份、迁移版本和快照路径，
不保存原始环境、凭据或 registry 内容。原始配置和 registry 仍须按既有安全部署规范保管。
`attempt-*` 下静态快照保持原文件与目录权限，Docker bind 只读挂载它们；上级私有目录不作为容器挂载内容。
DB 初始化脚本必须按既有 Docker/PG 用户权限可读，KMS registry 按正式镜像当前用户权限可读；本地合成测试不代替真实 Linux 验证。

**不可删除成功回执、attempt 快照或旧镜像来绕过拒绝。** 运行中的容器仍依赖快照文件，失败尝试也保留供排查。
缺回执先精确回读既有项目/容器/卷，再有据安排新的受控 prepare；输入改变需新审核候选标签，不复用旧成功标签。
脚本不会自动重 prepare、停止旧栈、删卷、清理现场或迁移降级。用户候选标签及唯一内部构建标签均不会被自动清理。

参数及 KMS 双人登记/端口切换顺序继续遵循 [试点执行单](PILOT_RELEASE_PLAN_20260921.md)。

## 本地验证与尚缺证据

最终聚焦原始日志和前后输入摘要保存在 `artifacts/pilot-release-binding-20260922/pytest.log` 与 `result.json`。
覆盖正常准备/启动、所有阶段失败、配置/源码/registry/镜像/DB/HEAD 漂移、回执损坏与权限、不可覆盖原子写、项目锁、
SIGTERM 子进程退出、Compose roundtrip 不等值、未绑定 build/config 来源、烟测目标及秘密不输出。
Docker 与烟测是有状态合成夹具；它们不启动容器，也不调用 KMS、PNVS、OSS 或目标服务器。

真实官方 Compose 5.5.1 的规范 JSON/不可变 ID/美元符号与挂载路径再解析验证由主任务单独保存最终源码摘要证据。
以上均不代替最终完整静态、GitHub PG16/Client、目标 Linux Docker 镜像/迁移/权限/挂载/HTTPS，及真实供应商、
人员映射、期初、备份恢复和业务 UAT。仍不能宣布已上线。

最终聚焦终端：**155 passed，138.24 秒**（pytest），协调器记录总耗时 138.69 秒；
7 个输入文件前后摘要一致，退出码 0。helper SHA256：
`ca6870ea9e1a7dfaeeea3869f48b82a0371bf6a89411c94bfa7f29a2a6bf9f1a`。
根目录 `artifacts/pilot-release-binding-20260922/result.json`/`pytest.log` 是此候选证据，
`attempt1`、`attempt2`、`attempt3` 保留先前 151/151/152 项结果与当时源码摘要，未覆盖成新候选。
