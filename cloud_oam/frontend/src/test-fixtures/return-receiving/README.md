# 退回接收协议样本

四份 `*-receiving-{quantity|serial}.json` 是合成数据经实际后端服务生成的当前协议样本；入库预览为 2.0，验收、入库结果及状态协议仍为各自的 1.0。不得只改版本字符串或手工重算 plan hash 冒充新服务输出。

在 `cloud_oam` 执行 `.venv/bin/python scripts/export_return_receiving_fixtures.py`，生成器在新的 ignored artifacts 目录创建四份文件，使用独立内存 SQLite，不访问现有数据库或外部服务。审核后复制到本目录，运行后端 `test_return_receiving_h5_contract.py`、前端退回接收/入库合同及页面测试。该生成器不构成 PostgreSQL 业务提交证明。

`loss-receiving-serial-v1.json` 保留更新前的合成历史样本，用于验证旧原请求摘要及已过账结果回查仍可读取；它不是当前后端的入库预览响应，不得通过放宽当前响应 schema 使其冒充 2.0。
