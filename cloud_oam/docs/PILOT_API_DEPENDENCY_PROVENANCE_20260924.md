# 目标机 API 依赖来源与哈希预检

日期：2026-09-24。工作树 `06f6/oam`、分支 `codex/notification-delivery-worker`。
本轮只构建目标机隔离预检镜像，没有提交、切换服务或运行生产迁移。

## 问题与修复

旧 `backend/Dockerfile` 仅使用 15 个顶层版本号，目标机从官方 PyPI 下载
`setuptools` 两次超时，转用阿里云镜像站虽可构建，但传递依赖和文件来源没有
独立锁定。官方 PyPI 当前解析的 `Mako 1.4.3` 在目标镜像站不可用，说明
两处索引的可见版本确实不同；失败原始日志保留为
`artifacts/pilot-live-route-preflight-20260924/target-mirror-hash-download.log`。

新增 `backend/requirements-linux-amd64.constraints`，固定与已验证预检镜像
一致的 `Mako 1.4.1`、`watchfiles 1.2.0`；用 `uv 0.12.5`、官方 PyPI、
无缓存解析 Python 3.12 / Linux x86_64，生成
`backend/requirements-linux-amd64.lock`。其中 15 项顶层依赖、72 项总依赖
与原预检镜像版本逐项一致。两次独立官方解析的有效包/哈希正文摘要均为
`eaf22fa6ab853cf10ab1d3b3038c5560f389912f2438424afa90455656177e7f`（重新生成到仓库路径后，
只有自动注释中的输出路径变化）。

在目标机一次性、无业务配置的容器中，使用镜像站对全部 72 项执行
`pip download --require-hashes --no-deps`，退出 0、下载 72 个文件。
其中 7 个源码包还从官方 PyPI 单独下载，原始 SHA-256 均存在于锁文件。
这 7 个包均没有声明 `pyproject.toml` 构建系统。为避免 pip 的临时隔离环境
再下载不受控的构建工具，另以官方 PyPI 哈希固定 `setuptools 80.9.0`、
`wheel 0.45.1`，目标镜像站的两份发行包也通过哈希下载。
`backend/Dockerfile` 先安装这两项，再以 `--no-build-isolation --require-hashes`
安装 72 项运行依赖，最后卸载构建工具。
`backend/tests/test_api_dependency_lock.py` 防止顶层依赖与锁文件偏离，
并检查两份锁与 Dockerfile 的哈希/构建隔离约束。
PostgreSQL 16 发布工作流的数据库和静态作业现同样先按这两份哈希锁安装
运行依赖，再安装固定版本的测试工具并执行 `pip check`；CI 拓扑与依赖锁
聚焦 **7 passed**，工作流 YAML 可解析。测试工具自身的传递依赖尚未独立
哈希锁定，不能将该结果称为完全可复现的 CI 环境。

## 目标机结果

- 最终仓库 Dockerfile 构建上下文：345 个输入，tar SHA-256
  `37c52deb43351baa4f53f76186341f7d5c089cbf18245a8c89d60c80a15ff349`。
- 预检镜像：`rsc-pilot-api:preflight-locked-37c52deb-20260924`，
  `linux/amd64`，ID
  `sha256:0ce887ba8d1ad369c3dc49d9c4f6ee7dd1991418de3dbb6cfab8d578e712bc7f`；
  镜像标签中的运行锁/构建锁 SHA-256 分别为
  `efb52bbbe911dedfe26b95b703c557bdd950e282003903bb146f6d2b0fcb06f4`、
  `df2bcce1e436683baacd697fa6f3fbb55c88567c2d6eef65c3767daa18340c7f`。
- 镜像内 72 项版本与锁文件逐项相同；非特权、只读、无网络一次性容器
  `python -m pip check` 输出 `No broken requirements found.`。原有 OAM、
  Edge Receiver 与 PG16 容器仍运行，未启动 RSC 试点服务。
- 当前依赖锁/烟测/部署安全/绑定/PG16 拓扑聚焦 **95 passed**；
  本轮仓库安全检查 **1,682 个候选文件 PASS**，`git diff --check` 通过。

## 未解除的门禁

已锁定构建工具与运行依赖，但 Python/Caddy 基础镜像目前仍以可移动标签引用；
发布时须冻结基础镜像摘要与最终镜像摘要。该镜像未接入真实 KMS/PNVS/私有 OSS、
生产数据库角色或试点配置，
没有 `prepare/start` 回执、真实用户验收或应用回滚证明，不能提升为发布镜像。
三片完整静态测试启动于本次锁文件改动前，哪怕最终通过也不是新候选的
完整静态门禁。该轮系统 `PATH` 缺少 Node，已出现的 7 项失败在加入桌面
自带 Node 后聚焦 **7 passed**；静态脚本现在启动前检查 Node。仍须等待原轮
完整失败汇总，再对冻结后的准确候选重跑。镜像站与官方 PyPI 的版本差异
必须继续保留为发布风险证据。

原始日志及输入摘要位于
`artifacts/pilot-live-route-preflight-20260924/`：
`uv-compile-fresh.log`、`target-mirror-hash-download-preflight.log`、
`target-api-build-locked-final.log`、`backend-buildtools-locked-context.json`、
`api-final-locked-verify-raw.log` 和 `focused-fully-locked-current.log`。
哈希模式的保证与源码构建隔离范围依据
[pip 官方 Secure installs](https://pip.pypa.io/en/stable/topics/secure-installs/)
和 [Build System Interface](https://pip.pypa.io/en/stable/reference/build-system/)；
[阿里云镜像站说明](https://developer.aliyun.com/mirror/pypi)明确提示内容可能与 PyPI 不同。
