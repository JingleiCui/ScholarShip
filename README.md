# ScholarShip · 学者之船

> 学海无涯，AI 作舟。

面向科研文献导入、证据问答、双语阅读、检索推荐、引用图谱与写作绘图的科研助手。

## 当前状态

2026-11-16：六模块已有可操作的本地工作室。已完成唯一授权 15 页扫描件的真实 158 点 / 4,096 维混合索引、五个固定官方单篇问题的原文复核、全文翻译、摘要与论文框架。云端批次使用 74/80 次后已关闭，剩余六次封存；当前不会因选择模型或填写 Key 自动请求。历史错误、失败及 unknown 均保留。

23 篇实际论文已本地结构解析，用于候选引用图谱；541 页教材完成本地文本层解析。CSV 可导出图像、原始数据、配置和可重跑脚本；拓扑文本可生成 Mermaid 和源码。检索追踪、反馈推荐、后续研究核验已有接口及合成契约验证，但真实外部检索与跨论文科研质量仍待验收。**尚未达到比赛全部验收要求**，也未完成公网演示、视频和技术 PDF。详见[本批验收](docs/studio/report-2026-11-16.md)及[比赛差距清单](docs/competition/audit-2026-11-16.md)。

统一配置位于 `model_config.py`；能力目录、协议、传输、执行准入与业务工作流分离，保留 mock/local/free_api/free_gemma。新任务冻结配置，向量模型或维度变化必须新建 profile、重建后切换；旧索引和历史任务保留。见[模型设计](docs/models/adapter-design.md)。

PyCharm 已绑定本项目 `.venv/bin/python`（3.12.16）。日常选择 **ScholarShip All** 或直接运行 `dev.py`；页面为 http://127.0.0.1:15173 ，API 文档为 http://127.0.0.1:18080/docs 。数据库等基础容器需已启动。

首次运行 `bootstrap.py`，健康诊断运行 `doctor.py`，一键验收运行 `validate.py`。配置集中在各入口文件顶部；密钥在不入 Git 的 `.env` 中。账号 `researcher` / `reviewer` 的初始密码分别读取 `INITIAL_USER_PASSWORD` / `INITIAL_REVIEWER_PASSWORD`；已有账号不会被初始化脚本重置。详见[运行手册](docs/runbook.md)。

| 阶段 | 状态 | 交付物 |
|---|---|---|
| 需求梳理 | 基线已确认 | [需求基线](docs/requirements.md) |
| 技术选型 | 已确认 | [技术决策](docs/technology.md) |
| 项目架构 | 已确认 | [架构设计](docs/architecture.md)、[评审结论](docs/architecture-review.md) |
| 数据库与向量库设计 | 已确认 | [关系库设计](docs/data-design.md)、[向量库设计](docs/vector-design.md)、[数据字典](docs/data-dictionary.md)、[评审结论](docs/data-design-review.md) |
| 环境与脚手架 | 已初始化、已验收 | [环境与脚手架方案](docs/environment-plan.md)、[初始化评审单](docs/environment-review.md) |
| 业务开发与比赛提交 | 六模块开发与分项验收，提交物待完成 | [验收矩阵](docs/acceptance.md)作为后续开发依据 |

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
