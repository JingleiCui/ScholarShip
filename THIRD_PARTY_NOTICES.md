# 第三方来源与授权边界

本文件为项目来源索引，不替代依赖包的 LICENSE/NOTICE。直接与传递依赖由 uv.lock 和 frontend/pnpm-lock.yaml 固定；分发应用/容器时应保留各软件要求的许可证与通知。这里不对全部依赖统一授予许可。

主要组件包括 FastAPI、SQLAlchemy、Alembic、psycopg、LangGraph/checkpoint-postgres、Qdrant client、Celery、Redis client、HTTPX、OpenAI Python SDK、pypdf、pdfplumber、Pandas、NumPy、Matplotlib、NetworkX、tiktoken；前端 React、Vite、PDF.js、KaTeX、Cytoscape.js、Mermaid。其上游来源与版本可通过锁文件及已安装发行元数据追溯。容器的 PostgreSQL、Qdrant、Redis、Caddy、Python、Node 和字体为独立组件，镜像与包许可分别适用。

论文和外部资料由各自权利人持有，不在此仓库重新分发。数据清单不意味着取得再分发许可。用户自行提供有权处理的文档。模型名称/服务接口不意味着附带模型权重、商业使用许可或免费额度；各服务条款分别适用。
