# 可插拔模型配置

2026-11-14。主入口为根目录 [model_config.py](../../model_config.py)；密钥只保存在 `.env`。更换配置不会自动开启云端推理。

## 当前组合

| 配置组 | 生成与翻译 | 向量 | 重排 | 解析 |
|---|---|---|---|---|
| free_gemma（当前默认） | OpenRouter `google/gemma-4-31b-it:free` | OpenRouter Nemotron 3.5 Embed 8B free | OpenRouter Nemotron 3.5 Rerank 8B free | 本地 MinerU |
| free_api | ChatAnywhere `gpt-4o-mini` | 同上 | 同上 | 本地 MinerU |
| local | Ollama `qwen3.5:9b` | Ollama `qwen3-embedding:0.6b` | 本地 Qwen3-Reranker-0.6B | 本地 MinerU |
| mock | 合成响应 | 合成哈希向量 | 合成排序 | 合成解析 |

当前研究项目已保存 free_gemma。ChatAnywhere 与 Ollama 选项保留；项目选择优先于 ACTIVE_PRESET，单角色优先级为配置组 → ROLE_OVERRIDES → 项目覆盖。

Gemma 用于聊天、证据问答、摘要和翻译，不能替代 embedding 或 rerank。最新已用 Nemotron 4096 维向量/重排和临时 ChatAnywhere 组合完成标准论文两段短片段的真实联调；Gemma 仍 429。唯一授权扫描件的 15 页 OCR 样例也通过。详见[最新报告](live-official-report.md)。尚未发布真实业务深解析/索引，不能当作全文科研问答或比赛六模块通过。

## 如何切换

- **已有组合**：工作台选择配置组并保存，或修改 ACTIVE_PRESET 后重启服务。文件默认不会覆盖已保存的项目选择。
- **同接口的新模型**：在 MODELS 新增命名条目，在 MODEL_CAPABILITIES 声明用途，再让 PRESETS 或 ROLE_OVERRIDES 指向它。模型 ID、角色、端点均在调用前校验；不要把聊天模型填入向量角色。
- **同协议的新平台**：在 PROVIDER_DEFINITIONS 增加批准的 base_urls、Key 环境变量名及协议绑定，再增加 MODELS 条目。现有协议不改业务代码。同平台具有不同参数策略时可以定义多个提供商别名，共用同一 Key 环境变量。
- **不同协议**：在 `backend/src/scholarship/models/protocols.py` 实现并注册独立协议，增加请求/响应契约测试。不会把任何相似接口假定为完全兼容。

提供商配置示例（示例域名不会被自动调用）：

```python
PROVIDER_DEFINITIONS["example_cloud"] = {
    "roles": {"chat": "openai_chat", "translation": "openai_chat"},
    "base_urls": ["https://api.example.com/v1"],
    "key_env": "EXAMPLE_API_KEY",
}
MODELS["example_chat"] = {
    "provider": "example_cloud", "model": "厂商实际模型ID",
    "base_url": "https://api.example.com/v1", "json_mode": True,
}
MODEL_CAPABILITIES["example_chat"] = ["chat", "translation"]
ROLE_OVERRIDES = {"chat": "example_chat", "translation": "example_chat"}
```

密钥配置：

```dotenv
OPENROUTER_API_KEY=你的完整Key
CHATANYWHERE_API_KEY=已有值保持不变
MINERU_API_KEY=已有值保持不变
EXTERNAL_SERVICES_ENABLED=false
```

当前三个 Key 均已填写，不需要再次发送给助手。已有 Key 不被重写。新增接口的 Key 也只放 `.env`；Key 只在发送时读取，不进入配置缓存、任务快照或报告。

## 协议与网络

已提供 `openai_chat`、`ollama_chat`、`openai_embedding`、`ollama_embedding`、`rerank` 五种文本/检索协议。MinerU 使用独立 prepare/upload/poll/download 生命周期；云端解析包的原文页码、几何及资产映射仍需专门验收，不能直接当成聊天接口。

OpenRouter 使用 `https://openrouter.ai/api/v1`，聊天、向量、重排分别使用 `/chat/completions`、`/embeddings`、`/rerank`。Nemotron 文档/问题输入分别带 `search_document` / `search_query`。Gemma 关闭 reasoning，JSON 输出仍由本地进行严格结构和逐字引文检查。

本机显式代理位于 LIVE_PROXY_URLS：`{"openrouter": "http://127.0.0.1:7897"}`。能直连的机器设为 `{}`。不隐式继承环境代理、不关闭 TLS 证书校验、不自动换平台或付费型号。显式代理不能保证免费接口无网络波动或限流。

Run `diagnose_model_network.py` 只用无 Key、无模型输入的 HTTP 请求诊断配置路由；可达不代表模型权限或推理已通过。

## 快照、索引与恢复

新任务使用 models-v2 快照，固定角色、模型、端点、协议版本与相关参数策略；历史 models-v1 原样恢复。参数发生漂移时拒绝继续旧任务，不悄悄套用新参数。聊天参数变更不会改变 embedding 的 profile。

向量维度使用有效回执确认，不截断或补零。profile 包含协议绑定和文档/查询输入策略；切换后先构建、验证新世代，再切活动指针。失败保留旧索引。模拟与真实向量严格分开；本次扩展仅开放带绑定的**合成 profile**，未启用真实资料索引或真实科研问答。

同一任务成功步骤复用回执；unknown 保留占额并停止，不自动重发。429 保存 Retry-After（如有），不自动退避推理。客户端在工作流内复用并统一关闭；维度探针的首块向量直接复用，避免重复向量请求。配置按文件版本缓存，编辑后自动失效，调用者拿到独立副本。

## 验证入口

- Run `verify_models.py`：脱敏配置诊断；顶部 RUN_TESTS 可开启协议测试。
- Run `verify_model_pipeline.py`：自动创建隔离测试库/向量 collection，完成多组合与配置新增平台的端到端测试；模型响应全部合成，不调用云端。
- Run `validate.py`：全部回归、迁移、构建、依赖和密钥扫描。
- Run `validate_live_models.py`：统一真实联调入口，默认 status。此前多个续测脚本已合并；旧授权和 unknown 保留在 PG。当前累计 12/12 已结束，模型与扫描件签发开关均关闭；run 不发送推理，mineru-run 复用已完成产物。需要下一阶段明确批准后才增加云端用量。

本地工具保留 `local_models.py`，默认仅检查。当前只有 Ollama qwen3.5:9b 权重已下载；未把合成 Ollama/MinerU 契约测试冒充本地推理、OCR 安装或 541 页教材解析验收。

下载 CDN 的本机 DNS 覆盖配置见 DOWNLOAD_DNS_OVERRIDES；不影响模型协议或 embedding profile。其他机器清空或重新核验地址，仍保留原域名 HTTPS 证书校验。
