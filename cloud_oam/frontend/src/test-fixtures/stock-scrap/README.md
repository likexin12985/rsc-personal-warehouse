# 报废与找回结果样本

`committed-quantity-results.json` 来自本地自建 PostgreSQL 16 测试库中两代真实服务提交产生的10份合成业务事件：首次/纠正报废各一份，找回申请、区域复核、总部复核和恢复过账各两份。读取使用只读、可重复读事务；没有连接生产系统或发送通知。

证据目录：`cloud_oam/artifacts/formal-0165-integration/`；对应数量业务运行 `local-scrap-business-pg16/run-4goc02ia`。样本验证公开结果契约；不能替代完整原生门禁、HTTP事务、序列号覆盖、真实身份或生产验收。

样本文件 SHA256：`001fe377279c2d848f3d6f1d5e1c79b89707c2b7eeb4b88a176c6cce9b78f07a`。

`command-hashes.json` 是12份独立合成请求，来自实际后端Pydantic六类命令和现有摘要函数，覆盖中文、emoji、换行/制表及附件排序。包含仅供测试的幂等键，不是生产凭据。它验证Python/TypeScript命令规范及摘要一致性，不是已发生的库存事实。恢复编排测试会将原生公开结果样本的字段替换为这些合成请求坐标；该组合只证明客户端保存/互斥/回查逻辑，不能冒充对应请求已通过原生HTTP事务。
