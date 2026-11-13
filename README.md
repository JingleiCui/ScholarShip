# ScholarShip · 学者之船

> 学海无涯，AI 作舟。

面向科研文献导入、证据问答、双语阅读、检索推荐、引用图谱与写作绘图的科研助手。

## 当前状态

2026-11-13：需求范围、技术选型、项目架构、数据库与向量库设计已由用户确认；进入环境与脚手架方案评审。

当前目录是新项目的设计文档目录，尚未初始化 Git、Python/Node 运行环境、数据库、脚手架或业务代码。文档描述的是待实施设计，不能视为已实现功能。

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 需求梳理 | 基线已确认 | [需求基线](docs/requirements.md) |
| 技术选型 | 已确认 | [技术决策](docs/technology.md) |
| 项目架构 | 已确认 | [架构设计](docs/architecture.md)、[评审结论](docs/architecture-review.md) |
| 数据库与向量库设计 | 已确认 | [关系库设计](docs/data-design.md)、[向量库设计](docs/vector-design.md)、[数据字典](docs/data-dictionary.md)、[评审结论](docs/data-design-review.md) |
| 环境与脚手架 | 方案待评审 | [环境与脚手架方案](docs/environment-plan.md)、[初始化评审单](docs/environment-review.md) |
| 开发、测试与提交 | 待前序阶段确认 | [验收矩阵](docs/acceptance.md)作为后续开发依据 |

每个阶段先提供可评审产物，经用户确认后推进。架构评审通过不代表数据设计、初始化或购买云服务已确认。

## 已确认技术

- React、TypeScript、Vite；PDF.js、KaTeX、Cytoscape.js。
- Python 3.12、FastAPI、LangGraph。
- PostgreSQL、SQLAlchemy、Alembic；Qdrant。
- Celery、Redis、Celery Beat。
- 本地轻量 PDF 预览及 MinerU 云端深解析。
- DeepSeek Flash 为文本模型默认候选，百炼文本向量与重排 API 为候选。
- NetworkX、Pandas、Matplotlib、Mermaid；持久化文件目录、Docker Compose。

## 参赛约束

按官网所列 2026-11-18 提交目标推进。必须兼顾完整前后端、公网演示、源码与真实提交历史、技术文档和演示视频。官网时间表和本地资料存在差异，见[需求基线](docs/requirements.md)。短周期全模块交付风险较高，进度必须按实际验收记录呈现。

模型调用以 100 元内为优化目标；全文翻译按需执行并缓存，不默认批量翻译整个文献库；Agent 循环设置上限。MinerU 实际额度、服务器价格与学生优惠均需在开通/使用前核实，当前没有购买或调用付费服务。

## 本地材料

原始资料位于 `/Users/cuijinglei/Documents/homework/比赛/科研小助手`，保持原文件不变。测试集盘点见 [dataset-inventory.json](docs/evidence/dataset-inventory.json)，该清单仅记录文件与文本层检查，不代表通过解析、OCR 或问答验收。
