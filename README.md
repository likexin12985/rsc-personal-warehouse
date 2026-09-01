# RSC 个人仓与物资运营扩展系统

本私有仓库承载 `cloud_oam` 的正式生产版开发准备。应用源码、数据库迁移、前端、微信小程序、
边缘同步、部署模板和测试位于 [`cloud_oam/`](cloud_oam/)。

开始任何设计、修改、测试、评审、迁移或部署工作前，必须依次完整阅读：

1. [`AGENTS.md`](AGENTS.md)
2. [`RSC个人仓与物资运营扩展系统——正式生产版需求与架构设计 V1.0`](docs/RSC个人仓与物资运营扩展系统_正式生产版需求与架构设计_V1.0.md)
3. [`cloud_oam/README.md`](cloud_oam/README.md)
4. [`跨账号私有仓库交接说明`](cloud_oam/docs/ACCOUNT_HANDOFF.md)

提交或推送前必须从仓库根目录运行：

```bash
./cloud_oam/scripts/verify_repository_safety.sh
```

本仓库不包含、也不得接收 OAM/RSC/Workflow/飞书凭据、生产会话、生产数据库、业务导出或
未经脱敏的运营证据。仓库内容不是任何外部生产系统写入、迁移或部署授权。
