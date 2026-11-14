# 真实联调记录与当前阻塞

2026-11-14。用户最初批准最多五次合成短文本请求，后明确增为六次。再增为七次未获批准；用户改选 Gemma，向量与重排保留 Nemotron。没有上传论文、调用 MinerU 云端或充值。

## 六个已占用名额

| 次序 | 用途 | 结果 |
|---|---|---|
| 1 | ChatAnywhere gpt-4o-mini 短聊天 | 成功，返回 SCHOLARSHIP_OK；报告型号 gpt-4o-mini-2024-07-18；输入 25、输出 4 Token |
| 2 | Nemotron 文档向量 | 未取得回执，unknown；原异常阶段未保留 |
| 3 | Nemotron 文档向量续测 | TLS/连接错误，unknown，后补诊断为 connect_error |
| 4 | Nemotron 文档向量，修正路由后 | 成功，两条合成文档，4096 维；报告型号为不带 :free 的同名基础型号；输入 26 Token，输出用量未提供 |
| 5 | Nemotron 问题向量 | read_timeout，unknown，已停止 |
| 6 | OpenRouter Gemma 合成证据问答 | HTTP 429，rate_limited，已停止；未取得有效回答 |

**累计占用 6/6：2 个成功回执、3 个 unknown、1 个明确拒绝。** 这不是六次成功推理或六次收费的证明。免费端点请求也保留计数；不伪造缺失 usage 为 0。重排、完整云端证据问答和科研质量尚未验证。

最初 ChatAnywhere 站内余额查询返回 403；它不是正式聊天 API，不能用来判断聊天权限。用户指出后已纠正，并用正式 `https://api.chatanywhere.tech/v1/chat/completions` 证实聊天可用。Host 原配置正确。

OpenRouter 只读 Key 查询成功并报告免费账户，公开目录中有选定向量型号。其后连接仍有波动；公开 GET 或不带 Key 的 POST 可达，不能保证随后的推理成功，也不能由 429 推断是账户日额度耗尽还是上游限流。

## 已采取的处理

- 普通 ProviderClient 仍只允许 MockTransport；真实执行走 manifest 和 PG 原子发送槽，不因选择配置组而开放。
- 同一审批链累计额度；续测不重置旧计数，不改写 unknown，不自动重试、重定向、换供应商或付费型号。
- 显式 OpenRouter 本地代理、TLS 校验及脱敏连接阶段诊断；后续回执保存 Retry-After（如供应商提供）。本轮 Gemma 的旧回执未保存该头，不能事后推定等待时长。
- 生成角色现选 free_gemma；ChatAnywhere 与 Ollama 继续可切换。向量原生维度实测为 4096，但没有把它冒充已发布业务 profile 或覆盖旧 1024 维索引。
- 一次性续测脚本合并为 validate_live_models.py 与 models/approvals.py。默认只读历史，当前 run 也不发请求；新建授权需下一次明确批准，不能靠重跑脚本扩额。

## 本次重构验证

见[可插拔设计](adapter-design.md)、[配置说明](configuration.md)及[重构验收](refactor-report.md)。真实 PG/Qdrant 上的合成端到端验证与云端可用性分开记录，不以本地契约通过宣称免费接口全流程已通。
