# 已确认技术决策

状态：2026-11-13 用户确认。组件具体版本、适配兼容性及账号可用性，在初始化前核验并锁定。

| 领域 | 选择 | 针对本项目的依据 |
|---|---|---|
| 前端 | React + TypeScript + Vite | 阅读、证据、图谱等交互集中在浏览器；可构建静态资源，便于低价部署 |
| 文档展示 | PDF.js + KaTeX | 保留原始 PDF，结构化翻译单独呈现；通过位置映射实现交互 |
| 图谱展示 | Cytoscape.js | 承载局域引用关系、筛选、高亮与节点导航 |
| API | Python 3.12 + FastAPI | 与文档、检索和分析生态一致，提供类型约束及 OpenAPI |
| Agent | LangGraph | 显式任务状态、工具控制、暂停/恢复及流式事件 |
| 关系数据 | PostgreSQL + SQLAlchemy + Alembic | 业务事务与 Agent 持久化；官方 PostgreSQL checkpoint 适配降低集成工作 |
| 向量检索 | Qdrant | 稠密与关键词混合检索、元数据过滤及独立索引生命周期 |
| 后台任务 | Celery + Redis + Beat | 解析、索引、翻译等长任务与 API 分离；定时追踪独立调度 |
| 解析 | 本地轻量提取 + MinerU 云端深解析 | 扫描、复杂版式和公式使用深解析，降低公网服务器计算压力 |
| 模型候选 | DeepSeek Flash + 百炼文本向量/重排 | 以成本、中文问题到英文证据检索、工具调用和效果实测为依据 |
| 分析与绘图 | NetworkX + Pandas + Matplotlib + Mermaid | 图论计算、CSV 处理及可下载脚本 |
| 部署 | Docker Compose + 持久化文件目录 | 同机自托管，降低服务数量与托管订阅费用；最终机器规格由实测决定 |

## 关系库与向量库取舍

MySQL 也能满足业务存储；本机运行的服务程序为 8.0.36。当前选择 PostgreSQL 的主要原因是 LangGraph 官方持久化集成，而非否定 MySQL 的存储能力。现有 MySQL 实例不纳入新项目变更范围。

Milvus 能承担混合检索。Standalone 官方要求至少 8 GB 内存、建议 16 GB；Lite 的并发与进程限制不同，需要独立评估。当前按小规模起步和低成本部署优先选 Qdrant，不据此声称其检索质量一定更高。具体资源与检索效果以相同数据、相同模型和问题集实测为准。

## OpenAI4S 参考边界

静态核查参考提交：`1aa313157fd9b572a59d4c0ce41088f4c5bdf461`，核查日 2026-11-13。

参考它的结构化工具/科研计算分工、操作记录、证据来源、产物版本、按需能力加载和执行预算。架构提案采用一个 LangGraph 主编排器；首版不嵌入第二套完整 Agent 主循环，不声称已实现持久 Python/R 内核或任意实验平台。

当前仅借鉴设计，没有复制 OpenAI4S 源码。后续若复用具体代码，须记录文件、提交、修改内容和许可证，保留所需版权声明；附属模型、数据、第三方 Skills 的许可分别核查。MIT 主项目许可不代表所有外部服务和数据免费。

## 配置与费用原则

- 后续开发入口支持从 PyCharm 直接运行；路径、并发、超时、算法与输出位置集中在可编辑配置区，CLI 参数为可选覆盖。这延续用户的代码配置偏好。
- API 密钥、数据库密码等敏感值采用环境变量或未提交的本地 `.env`；代码配置区不保存密钥。
- 文本模型、向量化、重排、OCR/深解析、搜索、服务器与流量分别核算。
- 100 元是模型调用优化目标，不是已充值金额或总项目成本承诺。运行前预估、运行后记账，并保留供应商账单差异。
- MinerU 文档列出的精准 API 单文件上限为 200 MB/600 页，每账号每日 2,000 页最高优先级额度；这不等同于已确认的免费额度。须在账号中核实当前计费、权限及排队情况。
- 不依赖本机长期在线维持评审服务；学生优惠的地域、期限、续费、磁盘与流量条件须逐项核验。

## 核查来源

- [LangGraph checkpointers](https://docs.langchain.com/oss/python/langgraph/checkpointers)
- [Qdrant hybrid queries](https://qdrant.tech/documentation/hybrid-queries/)
- [Qdrant 部署要求](https://qdrant.tech/documentation/installation/)
- [Milvus Standalone 要求](https://milvus.io/docs/prerequisite-docker.md)
- [Milvus Lite 能力与限制](https://milvus.io/docs/milvus_lite.md)
- [MinerU 精准解析 API](https://mineru.net/doc/docs/index.html)
- [DeepSeek 价格](https://api-docs.deepseek.com/zh-cn/quick_start/pricing/)
- [百炼文本向量接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
- [OpenAI4S 架构（核查提交）](https://github.com/PKU-YuanGroup/OpenAI4S/blob/1aa313157fd9b572a59d4c0ce41088f4c5bdf461/docs/architecture.md)
- [OpenAI4S 存储设计（核查提交）](https://github.com/PKU-YuanGroup/OpenAI4S/blob/1aa313157fd9b572a59d4c0ce41088f4c5bdf461/openai4s/storage/README.md)
- [OpenAI4S 许可证（核查提交）](https://github.com/PKU-YuanGroup/OpenAI4S/blob/1aa313157fd9b572a59d4c0ce41088f4c5bdf461/LICENSE)
