# 8.23 当前完整目录静态门禁接续

**当前完整静态会话 6070 正在运行：pytest PID 68895 / 监督 PID 68872，2026-09-22 23:03（北京时间）启动。自动收集 6,710 项 / 1 模块跳过，313 个候选测试文件，冻结 1,472 个非 Markdown 输入。** 证据目录 `artifacts/full-static-release-20260922-binding-fix/`；保持同一会话，等实际终端与源码稳定结果，不重复启动、不改冻结输入。当前尚无完整通过结论。

8.23 部署候选绑定 155 项聚焦、最终 helper 实际 Compose 快照解析、拓扑及仓库安全检查已通过。本轮按新候选重新开始，只排除独立 GitHub PG16 runtime。启动时周额度核验剩余 25%；8.24 交接核验剩余 9%，已停止新增开发；本会话继续后台运行。完成前不得宣告全量通过或提交/部署；运行步骤及判据仍见本文末。

## 8.21 原运行与受控停止历史

2026-09-22。本轮是当前整合候选的本地完整静态运行，**因已复现部署缺陷受控停止，未取得完整通过结果**。不能用收集成功、聚焦通过或历史门禁代替。

## 本轮终端结果

会话 52984 已于 `2026-09-22T14:36:59Z` 退出 2，pytest **11 passed / 1 skipped / 1 warning，612.36 秒**；1,470 个非 Markdown 输入无新增、删除或修改。监督器确认终端，两个 PID 均已结束。`fullStaticPassed=false`。

停止原因是独立合成反例证明部署入口未绑定 prepare/start 候选；三项分别为无 prepare、配置漂移、镜像 tag 漂移，均错误到达应用启动。证据在 `artifacts/pilot-prepare-binding-counterexamples-20260922/result.json`，本轮 `controlled-stop.json` 记录准确停止理由。不是测试自然失败或全量通过；待修复聚焦和复核后用新目录重跑。

## 原运行信息（历史）

- 工作树 `~/.codex/worktrees/06f6/oam`，分支 `codex/notification-delivery-worker`，HEAD `f713999d4693aedae11b5ebb379ef994aa50aeea`；全部未提交变更保留。
- 终端会话 **52984**，pytest PID **90353**，监督 PID **90332**；开始于 `2026-09-22T14:26:44Z`（北京时间 22:26）。
- 目录 `cloud_oam/artifacts/full-static-release-20260922/`：`process.json`、`command.json`、`source-manifest.json`、`static.log`，结束后由监督进程写 `result.json`。
- **6,645 项收集，1 模块跳过**；312 个候选测试文件，冻结 **1,470 个非 Markdown 仓库输入**。运行期间全部保持；当前该冻结已结束。不得复用本轮目录重复启动。
- Python 3.12.14、Node 24.19.0、PostgreSQL 工具 16.15；测试使用本地合成配置。运行器不继承 PG/OAM/RSC_PG16/ALIBABA_CLOUD/OSS/AWS 环境凭据。

从 `cloud_oam` 执行的实际测试命令：

```sh
PYTHONPATH=backend .venv/bin/python -m pytest -v -ra --tb=short --durations=25 \
  backend/tests edge_sync --ignore=backend/tests/test_postgresql16_release_gate.py
```

这里只排除有独立 GitHub PG16 服务与角色设置的 runtime 模块；没有按文件白名单缩小其余范围。最初自动收集 6,563 项，后续短信与 HTTPS 夹具新增测试已进入当前 6,645 项，两个数字对应不同候选。

## 门禁修正及验收

原 CI 233 文件白名单遗漏了 77 个可本地执行的测试文件，涉及认证、权限、KMS、附件等。CI 现使用同样的目录发现，路径触发覆盖 `cloud_oam/**`；静态任务上限为 360 分钟，最终聚合仍要求 static/runtime 双成功。拓扑检查已通过，不能据此认定流水线已通过。

完成时必须核验子进程实际退出、`terminalConfirmed`、`fullStaticPassed`、`sourceStableDuringRun`，新增/删除/改动输入均应为空，并保留完整日志和最慢项。若中途发现实际缺陷，先保留错误及准确候选，再受控停止本轮；不得运行中改源码后拼接通过结果。

旧完整会话 9496 的 4,960 passed / 1 failed / 3 skipped 及 CLI 修复证据保留在[历史接续](CONTINUE_DEVELOPMENT.md)，旧会话均已结束。当前分支普通 push 不自动触发远端门禁，需面向 main 的 PR 或显式 dispatch，并核验准确 SHA。当前没有提交、推送、部署或 GitHub 成功证据。
