# Caddy 容器入口验证（7.73 补充证据）

2026-09-21 在原工作树 `06f6/oam`、原分支 `codex/notification-delivery-worker` 上，
使用 Dockerfile 指定的官方 `caddy:2.10-alpine` 镜像完成本地容器验证。实际版本为
**Caddy 2.10.2，Linux ARM64/v8**，16 项路由检查通过，临时虚拟机已停止。
没有提交、推送、部署或外部业务写入；完整静态回归仍使用原会话运行。

## 通过范围

- `/` 和 `/login` 返回公开“交流备件知识大全”；`/xx/` 和 `/xx/my-stock` 返回个人仓页面。
- `/xx` 返回 308，目标 `/xx/`；两端实际 JavaScript 资源均正常返回。
- 三个不存在的图片/脚本地址均返回 404，没有回退成 HTML。
- 页面缓存、安全响应头、两端 Service Worker 和个人仓 manifest scope 符合现有检查。
- 公开目录返回准确的本地构建文件，仍为 `pending/0`。
- 本地身份桩的 401、503 和 `Cache-Control: no-store` 被原样保留。

沿用 `scripts/preview_public_entry.py` 的实际 HTTP 检查，测试对象改为 Linux 容器。
配置只为本地检查替换域名/监听端口和 API 上游，关闭自动 HTTPS/管理端口，省略
`www` 的公网 HTTPS 重定向；生产 Caddyfile、Dockerfile、源码和迁移均未改动。
因此本结果不证明生产 TLS、公网域名、真实身份服务、其他 CPU 架构或正式产品镜像构建成功。
真实目录仍未就绪，正式发布检查继续拒绝。

## 镜像与环境证据

证据目录：`cloud_oam/artifacts/caddy-container-20260921/`（忽略目录，不提交安装包）。

- 镜像平台清单摘要：
  `sha256:8dbca3525dd8dce671e495665a0eb297a83ec87ee30f373947d0ea93115949db`。
- `caddy-image-verification.json` 保存官方原始索引、平台清单、配置及各层摘要；
  从官方 Docker Hub 匿名读取后逐项核对，导入 containerd 后再次核对平台清单。
- `container-result.json` 保存 16 个实际请求、Caddy 版本、通过状态和关闭状态；
  `container-check.log` 保存实际命令及输出，`vm-final-list.json` 确认状态为 `Stopped`。
- `staged-input-manifest.json` 保存 23 个测试文件摘要；检查前后保持一致。
- 临时 Lima 2.2.0 / Ubuntu 24.04 环境只分配 2 CPU、2 GiB 内存和 8 GiB 磁盘，
  只读挂载测试目录，没有挂载个人目录、转发 SSH 代理或读取业务登录态。
  测试端口只绑定本机，未配置自动启动。独立目录见 `owned-vm.json`。

官方安装包、Ubuntu 镜像及 nerdctl 包均核对发布摘要；安装方式参考
[Lima 官方说明](https://lima-vm.io/docs/installation/)。容器内直拉 Docker Hub 超时，
失败日志保留；随后通过本机已有系统代理读取同一官方镜像，生成并验证 OCI 归档，
再导入临时虚拟机，没有使用第三方镜像或调整业务浏览器/Windows 路由。
首次检查脚本读取了错误的运行时字段层级，未执行路由即失败；修正字段后重测通过，
首次失败结果与日志以 `-first` 后缀保留，两次运行结束均确认虚拟机停止。

## 接续

先检查[完整静态门禁接续](STATIC_RELEASE_GATE_20260920.md)中的原会话和日志，
不要因等待超时而重复启动，也不要在运行期间改动受检代码。该容器证据不替代
当前候选完整 GitHub PG16/客户端门禁、真实知识资料、角色/设备 UAT、生产迁移与恢复验收。
原待批的公开仓临时 CI 候选提交例外、Edge 单次恢复授权均保持原状。
