# 第三方来源与授权边界

本文件为项目来源索引，不替代依赖包的 LICENSE/NOTICE。直接与传递依赖由 uv.lock 和 frontend/pnpm-lock.yaml 固定；分发应用/容器时应保留各软件要求的许可证与通知。这里不对全部依赖统一授予许可。

主要组件包括 FastAPI、SQLAlchemy、Alembic、psycopg、LangGraph/checkpoint-postgres、Qdrant client、Celery、Redis client、HTTPX、OpenAI Python SDK、pypdf、pdfplumber、Pandas、NumPy、Matplotlib、NetworkX、tiktoken；前端 React、Vite、PDF.js、KaTeX、Cytoscape.js、Mermaid。其上游来源与版本可通过锁文件及已安装发行元数据追溯。容器的 PostgreSQL、Qdrant、Redis、Caddy、Python、Node 和字体为独立组件，镜像与包许可分别适用。

[OpenAI4S](https://github.com/PKU-YuanGroup/OpenAI4S) 仅用于计算执行、产物版本与来源追踪的设计参考；当前没有复制其源码。核查提交与设计参考见 docs/technology.md。未来代码复用须另行标注文件、提交、修改及许可证。

论文和赛题由各自权利人持有，不在此仓库重新分发。数据清单不意味着取得再分发许可。用户自行提供有权处理的文档。模型名称/服务接口不意味着附带模型权重、商业使用许可或免费额度；各服务条款分别适用。

设计图由 GPT Image 生成，属于视觉探索；实际运行截图在 docs/assets/workspace.jpg。AI 开发披露见 docs/submission/ai-contribution.md。项目整体许可证尚待作者选择，本文件不是 MIT/Apache 授权。
