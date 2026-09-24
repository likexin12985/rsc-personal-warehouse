# 部署配置快照的真实 Compose 解析检查

2026-09-22。用于支持 prepare/start 候选绑定修复；本项仅验证配置解析，没有连接 Docker daemon、构建镜像、启动容器或部署。

使用已核验的官方 Docker Compose 5.5.1 macOS 二进制，SHA-256 为 `998735c9b6fe68a4f05895e6ea73d71ad06f9fc7046383ad89e47346781b6af5`。输入为当前正式 `docker-compose.yml`、全合成环境和 KMS registry，客户端指向不存在的私有 socket。所有解析配置只留在权限受控忽略工件内，不包含真实凭据。

实际将三个镜像替换为 `sha256:<64位摘要>`，保留原依赖和构建配置；环境值覆盖 `$VARIABLE`、`${VARIABLE:-default}`、`$$`，命令也包含字面美元符号。正式 Compose 的 canonical JSON 直接保存后再次解析，**整个文档相等、无警告、三个 image ID 保持**。额外再转义一次的反例会改变值，检查正确识别了该差异。

原因可核对 [Compose 5.5.1 的 config 实现](https://github.com/docker/compose/blob/v5.5.1/cmd/compose/config.go#L180-L182)：正常 config 输出已经将美元符号双写，不能对这个规范输出再盲目重复转义。部署协调器仍须以同一 Compose 实际再次解析相等作为边界，不能只凭手写 JSON 或合成 Docker 的返回值断言。

第二轮证据：`artifacts/pilot-immutable-compose-20260922-attempt2/{verify.py,result.json}`。首轮错误地把未经 config 规范化的手工字面值当作 canonical，整体相等断言失败；该假设与原文件保留在 `artifacts/pilot-immutable-compose-20260922/first-attempt-failure.json`。随后修正验证输入，在新目录执行，没有修改正式源码来迁就断言。

这不证明镜像实际存在或 target Linux 可启动；部署协调器、容器身份、回执、异常和真实目标验收继续单独验证。

正式协调器最终复核位于 `artifacts/pilot-coordinator-compose-20260922-final3/result.json`：直接调用当前 `validate_builds` 与 `frozen_document`，6 个 bind 来源改成含字面美元符号的合成绝对路径，整个规范文档由真实 Compose 再解析仍相等。最终 helper SHA256：`ca6870ea9e1a7dfaeeea3869f48b82a0371bf6a89411c94bfa7f29a2a6bf9f1a`，与 155 项最终聚焦的受检文件一致。其他带 final/final2 的目录是按中间代码保留的证据，以本条准确摘要为准。
