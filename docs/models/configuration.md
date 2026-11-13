# ScholarShip 统一模型配置

2026-11-14。本批实现配置、协议适配与合成契约验证；没有真实模型调用、论文上传或充值。

## 日常修改位置

主入口：[model_config.py](../../model_config.py)。可直接编辑顶部配置，之后重启本项目服务。项目工作台可以保存 `mock`、`local`、`free_api` 配置组；项目选择优先于文件中的默认组，角色覆盖按“组 → 文件 ROLE_OVERRIDES → 项目覆盖”合并。端点和能力只从服务端已命名配置读取，浏览器不能提交任意 URL。

| 配置组 | 聊天/问答/摘要/翻译 | 向量 | 重排 | PDF/OCR |
|---|---|---|---|---|
| mock | 合成响应 | 合成哈希向量 | 合成词法排序 | 合成解析映射 |
| local | Ollama qwen3.5:9b | Ollama qwen3-embedding:0.6b | 本地 Qwen3-Reranker-0.6B | 本地 MinerU 4.1 basic |
| free_api（默认选择） | ChatAnywhere gpt-4o-mini（权限待验证） | OpenRouter nvidia/nemotron-3.5-embed-8b:free | OpenRouter nvidia/nemotron-3.5-rerank-8b:free | 本地 MinerU |

各角色可单独替换，例如文件顶部 `ROLE_OVERRIDES = {"parser": "mineru_cloud"}`。云端解析保留 MinerU `vlm` 选项；更改配置不上传文件，不自动启动解析。OpenCode 免费模型不作为应用后端配置。

凭据只放私有 `.env`：

```dotenv
CHATANYWHERE_API_KEY=你的ChatAnywhere密钥
OPENROUTER_API_KEY=你的OpenRouter密钥
MINERU_API_KEY=你的MinerU密钥
EXTERNAL_SERVICES_ENABLED=false
```

以上三个 Key 本机均检测为已填写，只核验存在性，没有鉴权验证。已有值保持不变，不需重复粘贴，不发送到聊天或提交 Git。

## 请求和切换规则

- ChatAnywhere 使用 `https://api.chatanywhere.tech/v1/chat/completions`，不发送 DeepSeek 专属 thinking 参数；按 JSON 能力开关选择 response_format，所有引文仍在本地严格核对。
- OpenRouter 使用 `/api/v1/embeddings` 与 `/api/v1/rerank` 两个不同接口；只允许已确认的两个 `:free` 型号，无付费型号或供应商自动替换列表。
- 向量请求不强制指定原生维度、不截取或补零。`input_type` 按文档/查询分别设置 `search_document` / `search_query`；首个有效响应确认维度，后续不一致拒绝。
- 更换向量模型、维度或输入规则创建新 profile/collection/世代。校验点清单、payload 和向量后再原子切指针；失败保留旧活动索引。模拟回执生成的 profile 带 simulated，不能作为真实向量 profile 使用。
- 聊天和重排配置仅影响后续任务，历史任务保留配置快照和指纹；解析器切换要求新解析修订。现有 `/offline` 演示始终固定 mock，不受免费模型选择影响。
- 缺少 usage 保留 null，不伪造为 0；请求型号与报告型号分开记录。401/403/404/429、截断、工具调用和坏 JSON 明确拒绝，超时与不确定响应保持 unknown，不盲目重发。
- 新的 `model_runtime.calls` 单独记录模拟调用和配额计数，不向原 DeepSeek 人民币预算套价。每 run 请求上限可配置；成功回执幂等复用，未知占据计数，普通角色不能直接修改账目。

## 公开接口

- `GET /api/v1/system/models`：当前文件配置、凭据存在性、验证状态及本人模拟调用计数。
- `GET /api/v1/projects/{id}/model-configuration`：项目选择和索引影响。
- `PUT /api/v1/projects/{id}/model-configuration`：研究账号保存命名配置组及角色覆盖，携带 expected_fingerprint；陈旧编辑返回 409，跨账号返回 404，任意地址或未知模型配置返回 422。

## 当前边界与本地服务准备

**配置已实现不等于真实服务已开通。** 普通 ProviderClient 只接受 MockTransport；新增的 LiveExecutor 需要独立审批清单与数据库发送槽，见[联调报告](live-report.md)。仅更改外部开关不能启用任意真实调用。本次审批因额度查询 403 已停止，真实推理 0/5。当前所有非 mock 服务均标记未验证，免费额度未知。

根目录 `verify_models.py` 默认只生成脱敏配置报告，不请求任何模型；将顶部 RUN_TESTS 改为 True 可运行独立数据库契约测试。

`local_models.py` 默认 ACTION='check'，报告独立环境、锁文件和权重准备状态。可显式开启 ALLOW_DOWNLOADS 并选择 prepare，在 `.tools/model-services` 安装隔离依赖、生成带哈希依赖锁并下载公开重排权重。parser/rerank 模式分别提供本机 18082/18083 服务，受本地推理开关约束；Ollama 复用已有 11434 服务，不修改全局配置。

本次没有执行 prepare，没有下载新权重、安装 MinerU/重排环境或运行 OCR。准备脚本也不自动下载 Ollama embedding 和 MinerU 模型包；它们需要在本地模型验证阶段准备和记录版本。保留这些未完成状态，不能把服务声明当作本地闭环已验收。当前只有先前检测到的 qwen3.5:9b 已下载，仍未进行本批推理验证。

## 接口依据

[ChatAnywhere 项目说明](https://github.com/chatanywhere/gpt_api_free)、[OpenRouter RAG/重排接口](https://openrouter.ai/docs/cookbook/evaluate-and-optimize/rag)、[OpenRouter 向量接口](https://openrouter.ai/docs/api/api-reference/embeddings/create-embeddings)、[NVIDIA 向量的 query/passage 说明](https://build.nvidia.com/nvidia/nemotron-3_5-embed-8b)、[Ollama 本地 API](https://docs.ollama.com/api/introduction)、[MinerU 本地 SDK](https://opendatalab.github.io/MinerU/usage/sdk_api/)。本批仅按公开契约与合成响应验证，账户及真实质量待实测。
