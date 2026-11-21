# 模型接入审计与热切换（2026-11-21）

当前不是“全部本地模型”。根目录 `model_config.py` 默认 `ACTIVE_PRESET='free_gemma'`：聊天/翻译是 OpenRouter Gemma，向量/重排是 OpenRouter Nemotron，解析目录是 MinerU 云端。`FIGURE_MODEL` 独立选择 OpenRouter Nemotron VL。ChatAnywhere gpt-4o-mini 由 `free_api` 预设选择。历史云端回执与禁用的授权记录保留；配置登记、历史成功和现在可执行是三个不同状态。

## 本地目录及入口

| 配置名 / 模型 | 用途 | 协议与调用入口 | 当前证据 |
|---|---|---|---|
| `ollama_chat` / `qwen3.5:9b` | 聊天、证据问答、翻译、摘要/草稿生成 | `ProviderClient.generate/answer` → `OllamaChat` → `POST http://127.0.0.1:11434/api/chat` | `/api/tags` 只读检查不可达；未证明权重安装或推理可用 |
| `ollama_embedding` / `qwen3-embedding:0.6b` | 文档和查询向量，配置为 1024 维 | `ProviderClient.embed` → `OllamaEmbedding` → `POST http://127.0.0.1:11434/api/embed` | 同上，未发起推理 |
| `local_rerank` / `Qwen/Qwen3-Reranker-0.6B` | 重排 | `ProviderClient.rank` → `Rerank` → `POST http://127.0.0.1:18083/rerank`；服务入口 `local_services/rerank_server.py` / `local_models.py` 的 `rerank` 动作 | 独立服务环境、锁和权重准备记录均不存在；当前服务响应也未提供可结算 token usage |
| `mineru_local` / `mineru-4.1-basic` | 本地深解析目录 | 专用 `mineru_local` 生命周期；`local_models.py` 的 `parser` 动作预留 18082 端口 | 未验证安装；尚无业务上传/解析/发布适配器，不能作为可用深解析入口 |

`mock_*` 是合成协议/哈希算法，不是本地大模型。Studio 的 `local_parse` 是 PDF 文本层启发式解析，也不是本地 OCR 模型。本轮未安装、下载或启动模型服务，没有新推理或上传。

## 层次与本次修改

复用既有 `ModelGateway.invoke(intent, role, operation, arguments)`，保留配置→目录→协议→HTTP→带准入执行器→业务流程的边界。

| 文件 | 职责 / 本次变化 |
|---|---|
| `model_config.py` | 新增 `INFERENCE_MODE`；提供商显式声明 `deployment`；所有现有预设和默认禁用开关保持兼容 |
| `models/catalog.py` | 按配置实际字节缓存，避免同大小/同时间戳修改未生效；显式部署分类及兼容推导；本地端点限定 loopback HTTP，云端限定 HTTPS |
| 新增 `models/routing.py` | 统一按实际使用角色检查 local/cloud/auto 模式、当前文件开关和全局开关；合成模型不能进入真实通道 |
| `models/adapters.py` | 构建客户端时使用同一份目录决定能力、协议、参数绑定和回执身份，避免一次调用混用不同配置版本 |
| `models/production.py` | 同一网关支持本地/云端/混合角色；本地无 Key 时不强制 Bearer；发出请求前再次检查权限与绑定；保留 DB 原子准入、幂等、unknown 不重发、连接池关闭和真实回执 |
| `domains/research.py`、`domains/studio/workflow.py` | 预检只检查本次实际使用的角色；业务不增加供应商分支 |
| `models/configuration.py`、`api/model_configuration.py` | 状态返回全部可选模型、部署类型、热更新边界；重建判断包含 embedding 端点/协议/输入策略变化 |
| `local_models_doctor.py` | 更新真实执行入口说明，仍不把服务配置视为就绪 |
| `backend/tests/test_model_hotplug.py` 等 | 配置热切换、同元数据修改、本地兼容协议、混合/受限模式、权限撤销、本地 DB 准入及 profile 回归 |

这里的 `models/` 位于 `backend/src/scholarship/models/`。新模型使用既有协议时，只需增加目录配置；全新线格式才新增/注册 `protocols.py` 插件。MinerU 是文件任务生命周期，不应伪装成聊天接口。

## 配置字段与使用

```python
# 文件默认选择；已有项目保存的 preset 优先于 ACTIVE_PRESET。
ACTIVE_PRESET = "local"  # mock / local / free_api / free_gemma / 自定义预设
INFERENCE_MODE = "auto"  # auto 允许逐角色混合；local/cloud 限制真实请求的部署类型
ROLE_OVERRIDES = {"chat": "chatanywhere_chat", "translation": "chatanywhere_chat"}
# 以上示例是本地 embedding/rerank + 云端生成；INFERENCE_MODE 应为 auto。
```

`PROVIDER_DEFINITIONS[name]` 的字段：`deployment`（`local/cloud/simulated`）、`roles`（角色→协议）、`base_urls`（受信地址白名单）、可选 `key_env`（仅环境变量名称）、可选协议参数。旧目录未写 `deployment` 时保持原推导：mock→simulated，有 key_env→cloud，其余→local。显式部署分类允许本地 OpenAI 兼容服务使用相同插件，也允许本地服务可选鉴权。当前本地网络范围只支持 `http://127.0.0.1:<port>`；不自动开放远程 HTTP、任意代理或重定向。

新增本地 OpenAI 兼容服务的配置示例（只登记，不安装或推理）：

```python
PROVIDER_DEFINITIONS["desktop_api"] = {
    "deployment": "local",
    "roles": {"chat": "openai_chat", "translation": "openai_chat"},
    "base_urls": ["http://127.0.0.1:1234/v1"],
}
MODELS["desktop_chat"] = {
    "provider": "desktop_api", "model": "your-installed-model",
    "base_url": "http://127.0.0.1:1234/v1", "json_mode": True,
}
MODEL_CAPABILITIES["desktop_chat"] = ["chat", "translation"]
# ROLE_OVERRIDES = {"chat": "desktop_chat", "translation": "desktop_chat"}
```

新增云端兼容 API 同样添加目录，将 `deployment` 改为 `cloud`，登记 HTTPS 端点和 `key_env='YOUR_PROVIDER_API_KEY'`；Key 值只放私有 `.env`。向量接口需声明实际模型与维度/输入策略，不能因为线格式兼容就声称模型能力兼容。现有 OpenRouter 保留 `free_only`、型号白名单与禁止回退参数。付费路径的价格和结算仍需另行设计/批准，本次没有开放付费路线。

真实请求另需 `ALLOW_LOCAL_INFERENCE` / `ALLOW_REAL_REQUESTS`、`EXTERNAL_SERVICES_ENABLED`、项目/角色/型号的有效 DB 策略、数据范围与共享预算全部满足。保留现有生产总闸，所以本地请求也须通过 `EXTERNAL_SERVICES_ENABLED` 和共享 DB 准入，不能只改预设就绕开历史授权。所有生产策略当前继续关闭；没有创建或重启任何授权。无 usage 的服务不会虚构 token：现有本地 rerank 服务需未来补可审计用量契约才能成功结算。

兼容性原因，API 的 `mode='cloud'` 仍是历史“真实执行”通道名称；实际 local/cloud/mixed 由角色配置决定，`offline_acceptance` 始终只用模拟传输。Studio 的 `mode='local'` 继续表示确定性本地工具，不等同本地 LLM。UI 的“云端”历史标签尚未改名；本轮通过模型配置 API / 文件切换，未增加新的前端工作流。

## 生效时点与迁移步骤

1. 备份当前模型配置和项目选择。部署本轮代码；数据库无需升级，无新迁移、无旧迁移修改、无存量数据重写。首次部署新代码需要更新 API/worker 进程；以后常规目录/预设修改不需重启。
2. 添加提供商、命名模型、能力和预设，使用 `load_selection()` 做无网络校验；采用原子替换文件避免写入半个 Python 文件。无效配置直接失败，不静默保留旧配置或切换厂商。
3. 有项目选择时，通过 `GET/PUT /api/v1/projects/{project}/model-configuration` 切换 `preset`、`overrides`；PUT 携带 GET 返回的 `fingerprint` 作为 `expected_fingerprint`，防止覆盖并发编辑。`GET /api/v1/system/models` 提供 `available_models`、`deployment` 和 `reload_policy`。项目角色覆盖 > 文件角色覆盖 > 预设；项目保存的 preset > 文件 ACTIVE_PRESET。
4. 新任务读取当前配置并固定 models-v2 快照。队列中、运行中、恢复的任务保留原型号/端点/协议/参数；旧目录绑定变化时停止并提示重新建任务，不把旧任务悄悄路由到新模型。保留旧提供商和命名模型登记以便历史快照恢复；models-v1 兼容逻辑不变。
5. 聊天/翻译/重排切换影响新任务；embedding 的模型、端点、维度、协议或输入策略变化必须建立独立 profile 并重建验证候选索引，再切 active 指针，旧索引和历史回执保留。解析器变化需新的不可变解析 revision；本地 MinerU 业务生命周期仍未启用。
6. 文件中的权限开关每次真实请求前重读，可阻止后续请求，无法撤回已经发出的网络请求。环境变量、全局 Settings 和内建凭据仍是进程缓存，更新后重启 API/worker；连接池/代理在现有 gateway 内复用，下一个 gateway 使用新设置。
7. 容器中的配置是部署副本，编辑宿主文件不保证容器可见；需更新容器文件/只读挂载或重建部署。本地服务的权重、GPU/进程、rerank 服务内固定 MODEL 和新协议插件注册需独立启动/重启，不属于配置热切换。
8. 验证通过后才能讨论启用数据和额度；本轮仅完成代码与隔离离线验收，没有启用真实发送。

## 验证要点

覆盖本地/云端同协议模型、混合角色、模式限制、配置热生效和旧快照不变、请求前撤销开关、无 Key 本地鉴权、未授权不发送、错能力/端点拒绝、unknown 不重试、真实/模拟 profile 隔离，以及端点/输入策略变化后的重建要求。全量 `validate.py` 使用独立临时库/队列/集合；MockTransport 测试中的 `simulated=false` 是真实执行器路径的契约测试，绝不是本地模型质量或安装验收。本轮 `validate.py` 全部通过：后端 238 项（0 失败、0 跳过），前端测试与生产构建、Ruff、依赖健康和密钥扫描全部通过。完整机器报告位于私有运行目录 `var/reports/initialization.json`。
