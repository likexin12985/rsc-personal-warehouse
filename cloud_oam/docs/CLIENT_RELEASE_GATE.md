# Web 与微信小程序独立发布门禁

## 已验证证据

源码提交：`ae263fb10a149a434c04ea5cb46c2a3bd3a906b5`。
GitHub [Client release gate 33949308200](https://github.com/likexin12985/rsc-personal-warehouse/actions/runs/33949308200)
已成功，任务于 `2026-09-05T06:16:26Z` 完成；以下记录仅证明该准确提交，不自动覆盖后续修改。

- Linux runner 实际版本：Node `24.19.0`、pnpm `11.19.0`。
- 仓库安全检查通过；冻结依赖安装成功，禁用安装生命周期脚本。
- Web：32 个测试文件、691 项通过；TypeScript 检查和 Vite 构建通过。
- 微信小程序：578 项通过，无失败；其中新增 62 项门禁配置/安全边界回归。
- 没有执行部署、发布、生产访问、短信发送或外部业务操作。

## 执行和权限边界

工作流为 `.github/workflows/client-release-gate.yml`，与 PostgreSQL 16 门禁独立。
支持手动执行、向 main 的 PR，以及 main/当前开发分支的 push。故意不设路径过滤：
避免工作流作为 required check 时因路径跳过而永久等待；文档提交也会触发该门禁。
GitHub 分支保护是否把门禁设为必需，是独立仓库治理条件，不由本文件推定。

runner 固定为 `ubuntu-24.04`，超时 15 分钟；仓库权限仅 `contents: read`，checkout
不持久化凭据。工作流不配置生产 secrets、部署、缓存、构建产物上传或外部系统步骤。
checkout 与 setup-node 均固定准确提交，而非可变 tag：

- checkout：`3d3c42e5aac5ba805825da76410c181273ba90b1`。
- setup-node：`2028fbc5c25fe9cf00d9f06a71cc4710d4507903`，已核对官方 v6.0.0。

Node 和 pnpm 实际版本必须与固定值相同；安装采用 `--frozen-lockfile --ignore-scripts`。
Web 测试、类型检查、构建和小程序测试分别执行，不能用其中一项冒充其余项。

`.yml` 内容采用合法的 JSON 语法（YAML 的子集），使小程序 Node 原生测试能结构化解析，
无需增加解析器依赖。合同测试也检查重复键（包括转义形式）、权限扩大、额外步骤、跳过测试、
缓存/产物上传及不固定的工具版本，并在隔离临时仓库中证明安全扫描仍拒绝其他工作流。
`.gitignore` 和仓库安全扫描只放行这一个新文件名，不放行整个 workflows 目录。

## 仍未完成的独立验收

本门禁不证明 `frontend/Dockerfile` 的 Node `22-alpine` / Caddy `2.10-alpine` 浮动镜像
可重复性、摘要固定、跨架构兼容或供应链安全；这些仍须单独验证，不能编造镜像 digest。
它也不证明微信真机生命周期、原生存储/并发、个人主体审核或真实短信接口可用。
PostgreSQL 16 真库、数据库权限、迁移及恢复门禁继续独立执行。

所有运行只使用源码、公开依赖及隔离夹具；不得为使门禁通过而上传生产数据、登录态或密钥。
