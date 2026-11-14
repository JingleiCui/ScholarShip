# M2 官方接口、价格与额度核验

更新提示（2026-11-14）：下文为初次核查历史。当前模型组合和 MinerU 实测见[最新报告](../models/live-official-report.md)；当前 MinerU 单文件上限 200 页、每日 1,000 页最高优先级，不能继续按旧 600 页/2,000 页准入。

核查日：2026-11-13。范围：公开官方文档与本地凭据存在性。没有登录供应商账号、申请 Token、上传论文或发起模型请求。公开政策不等于本账号已获权益；[本地状态](../evidence/m2-credential-status.json)显示三个服务凭据及百炼业务空间 ID 均未配置，外部调用仍关闭。

## 已核验的候选

| 用途 | 保留选择 | 人民币公开价格与限制 | 来源 |
|---|---|---|---|
| 问答 | `deepseek-flash` | 每百万 Token：高峰未缓存输入 2 元、输出 8 元；空闲 1 / 4 元。预算按高峰、无缓存计算。官方当前说明服务版本为 V4.1-Flash-1017；旧 V4 Flash 别名不再代表旧模型 | [官方价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/) |
| 文本向量 | 北京 `text-embedding-v4`，1,024 维 | 0.0005 元/千输入 Token，即 0.5 元/百万；每批至多 10 条，每条 8,192 Token。公开列有 100 万 Token / 开通后 90 天政策，实际剩余未核实 | [向量接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api) |
| 重排 | 北京 `qwen3-rerank` | 0.5 元/百万输入 Token；公开列有 100 万 Token / 90 天政策。不能将输入量只按候选正文计算 | [价格](https://help.aliyun.com/zh/model-studio/model-pricing)、[接口](https://help.aliyun.com/zh/model-studio/text-rerank-api) |
| 深解析 | MinerU 精准 API，显式选择 `vlm` | 单文件 200 MB / 600 页；每日 2,000 页是最高优先级额度。所核查公开接口页没有足以确认本账号免费权益或人民币单价的信息 | [精准 API](https://mineru.net/doc/docs/index.html) |

DeepSeek 官方按北京时间划分高峰/空闲，当前正式调用名是 `deepseek-flash`。本项目不使用已退役别名，也不把官方“模型版本”文字当作一个可调用的快照 ID。请求模型、响应报告模型和核查日的官方版本说明分别保存。[价格与别名说明](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)

百炼还有更新模型，但本轮保留已确认的 v4 / qwen3-rerank；维度相同不代表向量空间相同。后续换模型必须新建 profile 与索引世代，并重新验证检索质量。免费额度按 **0 可用** 做预算，直到账户控制台核实。

## 接口边界与凭据

- DeepSeek：`https://api.deepseek.com/chat/completions`，Bearer `DEEPSEEK_API_KEY`。首批问答拟显式关闭 thinking，并设置输出上限；默认思考行为不能依赖猜测。[Chat Completions 参数](https://api-docs.deepseek.com/zh-cn/api/create-chat-completion/)
- 百炼：需地域一致的 `DASHSCOPE_API_KEY` 与 `DASHSCOPE_WORKSPACE_ID`，首选北京。向量的 OpenAI 兼容路径为 `https://{WorkspaceId}.cn-beijing.maas.aliyuncs.com/compatible-mode/v1/embeddings`；重排兼容路径使用 **`compatible-api/v1/reranks`**，不能拼成相同前缀。具体业务空间是否开通、网络是否可达仍未认证验证。[向量地址](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)、[重排地址](https://help.aliyun.com/zh/model-studio/text-rerank-api)
- 重排仍由专用 HTTP 适配器处理。该接口不是聊天补全；计费输入包括每份候选对应的 query 重复量。qwen3-rerank 单条 Query/Document 上限 4,000 Token，最多 500 文档；项目首批仅 20 候选、每块最多 900 估算 Token，超限报错而不静默截断。[重排契约](https://help.aliyun.com/zh/model-studio/text-rerank-api)
- MinerU：`MINERU_API_KEY`；本地 PDF 采用 `/api/v4/file-urls/batch` 申请签名上传地址，PUT 后自动提交，随后轮询 batch。签名链接及 Token 不进入公开事件或日志；签名 URL 上传不附带 MinerU Bearer。数据 ID 用于业务关联，不能据此假定供应商保证幂等。[上传流程](https://mineru.net/doc/docs/index.html)

轻量 MinerU 接口仅返回 Markdown，不能满足本项目结构坐标证据需求，因此不以其替代精准深解析。结果包需核对页号、内容 JSON 和版面数据，下载并校验后才成为本地新修订。[模式与结果格式](https://mineru.net/doc/docs/index.html)

## 账号与数据仍待确认

公开页不能证明当前余额、赠送额度是否到账/过期、模型权限、服务地域和账号限流。MinerU 的具体费用、文件/结果保留期限及删除机制，本轮未获得可据以作出承诺的账号级证据；未提交文件。若这些条件不能接受或价格不明确，该供应商保持关闭，M1 继续可用。

真实联调拟只上传两份明确选定的公开参赛样本到 MinerU；百炼接收解析后的文本块、问题及重排候选；DeepSeek 接收问题与选出的证据片段，不默认接收整本教材、原始文件、账号资料或数据库记录。启用前另列具体样本及费用范围，凭据在本机 `.env` 配置，不发送到聊天中。
