# ScholarShip 文档

## 使用与部署

| 主题 | 说明 |
|---|---|
| [安装与首次运行](guides/getting-started.md) | 工具链、账号、导入自己的第一份文献 |
| [模型与服务](guides/models.md) | 配置组、Key、执行条件、模型切换 |
| [容器部署](guides/deployment.md) | 构建、模式切换、共享存储与公网边界 |
| [能力与验证范围](guides/capabilities.md) | 模型输出、结构解析和各模块质量的实际限制 |
| [运行手册](runbook.md) | 进程生命周期、检查与故障恢复 |

## 架构与开发

| 主题 | 说明 |
|---|---|
| [项目架构](architecture.md) | 领域职责、文件与证据流转 |
| [关系数据](data-design.md) / [向量检索](vector-design.md) | 数据模型、版本与索引世代 |
| [模型协议](models/adapter-design.md) | 能力目录、协议、传输与工作流边界 |
| [研究工作流](studio/design.md) | 翻译、摘要、产物和受控工具 |
| [贡献指南](../CONTRIBUTING.md) | 本地检查与提交要求 |
| [AI 辅助开发](development/ai-assisted-development.md) | 工具使用、贡献和审查责任 |
| [安全](../SECURITY.md) / [第三方说明](../THIRD_PARTY_NOTICES.md) | 配置保护和来源授权 |

仓库保留带日期的设计、决策和验证记录，用于追溯实现历史。历史计划不代表当前能力，最终行为以源码与实际检查为准。`docs/design/` 包含已放弃的视觉方案，运行界面见根目录 README。
