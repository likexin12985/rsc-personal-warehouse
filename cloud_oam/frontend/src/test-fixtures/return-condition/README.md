# 成色纠正客户端契约样本

`command-hashes.json` 的 12 类命令是明确的合成输入。摘要由服务候选 v7 中实际
`return_condition_request_inputs.canonical` 和
`return_condition_decision_seal_admission.canonical` 生成，文件内保存参与规范化的源码 SHA256。
它验证数量字符串、证据与 SN 排序、带域的幂等键摘要及原请求摘要的跨语言一致性。

这些输入没有执行库存业务，不是权限、实际 COMMIT、PG HTTP、渠道或 UAT 证据。
客户端测试中的业务结果明确为传输故障模拟。原输入摘要不能与已入账事件摘要互换。
