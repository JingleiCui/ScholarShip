# 模型与服务配置

主入口是根目录 `model_config.py`；凭据只保存在私有 `.env`，字段见 `.env.example`。界面中的模型选择用于新任务，不自动授予外部访问权限。

| 配置组 | 生成与翻译 | 向量/重排 | 使用前提 |
|---|---|---|---|
| mock | 合成响应 | 合成哈希向量与排序 | 用于验证流程，不产生科研结论 |
| free_api | ChatAnywhere | OpenRouter | 账号权限、模型可用性与生产准入 |
| free_gemma | OpenRouter Gemma | OpenRouter | 同上；存在历史限流记录，不保证持续可用 |

模型 ID、能力和地址以 `model_config.py` 实际定义为准；名称中的 free 不意味着无限额度。解析可接入 MinerU 云端，或使用另外安装的本地服务。

## Key 与执行条件

- ChatAnywhere：`CHATANYWHERE_API_KEY`。
- OpenRouter：`OPENROUTER_API_KEY`，向量与重排可共用。
- MinerU 云端：`MINERU_API_KEY`。

默认 `EXTERNAL_SERVICES_ENABLED=false`、`ALLOW_REAL_REQUESTS=False`。真实业务还需要匹配的生产策略，限制 owner/project/来源修订、角色、模型、有效期与用量。仅修改总开关不能替代配额策略；新环境也不会继承开发者的历史调用许可。当前没有面向任意文献的自助生产策略配置界面。

`approve_cloud_business.py` 是特定开发样本的维护入口，不是通用开通脚本。部署自己的真实业务前，应审查 `models/production.py` 和策略迁移，为自己的数据范围配置并验证准入，不能照搬历史授权。

## 更换服务

同协议平台通过目录和协议绑定扩展；不同协议实现独立插件，不在业务工作流里添加厂商分支。更换聊天/翻译模型影响新任务；更换 embedding 模型、维度或输入政策需要独立 profile 和索引世代，重建验证后切换，不截断向量混入旧索引。

移机前检查 `LIVE_PROXY_URLS` 和 `DOWNLOAD_DNS_OVERRIDES`：目前保留原开发机代理与下载解析配置；其他环境可能应清空。凭据不得放入地址或代理 URL。

实现说明见[协议适配设计](../models/adapter-design.md)和[工作室设计](../studio/design.md)。

## 图内识别

在原文阅读器展开已保存图片的 figure 图块，可提交图内识别任务。`FIGURE_MODEL` 单独指定图片模型，不影响项目问答型号；`VISION_MODEL_BINDINGS` 声明允许使用的视觉型号，接口不会在失败时自动更换模型。调用发送单张图片和图注，返回节点文字、归一化坐标、连线方向与不确定项；结果另存为带来源的 JSON 产物，不修改 OCR 原文，也不自动纳入可信检索。图片上限 4 MB，现有生产策略、额度与失败记录继续生效。账户实际图片权限需单独验证。
