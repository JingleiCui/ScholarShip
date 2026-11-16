# ScholarShip · 学者之舟

> 学海无涯，AI作舟。

面向研究生科研阅读的 AI 工作空间：管理文献、追溯回答依据、逐块双语阅读，并将来源连接到图谱、草稿和可重跑图表。中文名化用“学海无涯苦作舟”，以 AI 辅助探索，保留人的判断与核验。

![实际本机界面](docs/assets/workspace.jpg)

这是实际运行界面，数据属于本地工作空间；仓库不附带论文、密钥或预生成科研数据库。首次克隆不会自动出现截图中的资料。设计概念图与实际页面分别保存，不能作为功能验收证据。

## 功能与当前边界

| 模块 | 已实现的行为 | 质量或覆盖限制 |
|---|---|---|
| 文献管理 | PDF 导入、哈希去重、候选人工决定、版本、本地逐页预览、结构修订 | 扫描副本语义去重与复杂结构需继续核验 |
| 证据问答 | 限定来源的混合检索、重排、冻结证据位置、确定性作者/表格计算、历史错答审阅 | 固定单篇五问已原文复核；官方跨论文题集尚未真实验收 |
| 阅读翻译 | PDF 定位、引用往返、逐块上下对照/双栏同步、字面量保护与片段缓存 | 已观察到语义误译，模型译文需人工校对 |
| 检索推荐 | arXiv/Crossref 适配、开放 PDF 入库、订阅调度、反馈与代码链接状态 | 真实外部检索旅程与长期推荐效果未完整验收 |
| 图谱与方向 | 局域有向图、可重算 PageRank、候选引用标记、后续元数据证据记录 | 候选身份需核验；没有检索到不能证明问题未解决 |
| 写作绘图 | 带来源的摘要/框架及版本、CSV 受控绘图、数据/脚本导出、Mermaid 拓扑 | 草稿未逐论断盲审；计划不代表已完成实验 |

2026-11-16：215 项后端测试、3 项前端测试通过；之后的界面改版已通过前端构建及 3 项前端测试。两次检查范围不同，不能把旧后端报告当作未来改动的结果。见[本批报告](docs/studio/report-2026-11-16.md)、[界面报告](docs/studio/topnav-redesign-2026-11-16.md)、[比赛逐项差距](docs/competition/audit-2026-11-16.md)。

唯一授权扫描件的 15 页真实索引、固定五问、翻译、摘要与框架批次已执行，使用 74/80 次后关闭；旧错误和 unknown 保留。此验收受语料/模型/期限限制，不能作为通用准确率。23 篇论文和 541 页教材有本地文本层解析记录，教材不等于 OCR 验收。**比赛完整验收、公网演示、视频与技术 PDF 尚未完成。**

## 快速开始

已验证的本地环境为 **macOS Apple Silicon、Python 3.12、Docker Desktop**。自动工具链脚本目前下载 macOS arm64 的 Node；不要宣称 Windows/Linux 一键安装已验证。Docker 应用镜像在本机集成测试通过，公网与 Linux amd64 待验证。

```bash
git clone https://github.com/JingleiCui/ScholarShip.git
cd ScholarShip
python3.12 bootstrap.py
.venv/bin/python dev.py
```

执行前启动 Docker Desktop，并检查 `bootstrap.py` 顶部 `BOOTSTRAP_PYTHON` 是否为自己的 Python 3.12 路径（默认 `/opt/homebrew/bin/python3.12`）。脚本仅安装项目专用工具链、基础容器、迁移和初始账号，不做全局升级。默认端口需要空闲。

- 页面：http://127.0.0.1:15173 ，接口文档：http://127.0.0.1:18080/docs 。
- 首次 `bootstrap.py` 自动生成私有 `.env` 和随机凭据；**不要先把空的 `.env.example` 复制成 `.env`**，已有 `.env` 不会被初始化覆盖。
- 账号为 `researcher` / `reviewer`，密码分别在 `.env` 的 `INITIAL_USER_PASSWORD` / `INITIAL_REVIEWER_PASSWORD`，请自行在编辑器查看。
- 停止项目：`.venv/bin/python stop.py`。保留基础容器与数据，不停止其他项目服务。
- 模型 Key 对本地文献管理、受控图表和合成契约演示不是必需的。模型配置/Key 本身不授权外部请求。

完整安装、容器切换、恢复与故障排查见[运行手册](docs/runbook.md)。若没有原比赛资料，只验证自己提供的文档；依赖特定私有样本/历史回执的验收脚本不能直接当作新环境通用检查。

## 模型配置

根目录 [`model_config.py`](model_config.py) 集中定义模型、提供商、协议、超时及配置组；Key 只填写私有 `.env`，模板为 [`.env.example`](.env.example)。

| 配置组 | 生成/翻译 | 向量与重排 | 状态 |
|---|---|---|---|
| mock | 合成响应 | 合成向量/排序 | 用于契约流程验证，不是科研结论 |
| local | Ollama | Ollama + 本地 rerank | 配置保留，本机服务未完整安装验收 |
| free_api | ChatAnywhere gpt-4o-mini | OpenRouter Nemotron 免费型号 | 已批准样例批次曾调用成功，当前准入关闭 |
| free_gemma | OpenRouter Gemma | OpenRouter Nemotron 免费型号 | 配置保留，已有 429 记录，不保证当前可用 |

MinerU 云端解析为可切换选项；本地 MinerU 不会因选中配置就自动安装。免费型号可限流或下线，不静默转付费路线。移机前检查 `LIVE_PROXY_URLS` 和 `DOWNLOAD_DNS_OVERRIDES`，它们包含原开发机的路由配置，其他主机可能应清空。新任务冻结模型配置，更换向量模型/维度需独立 profile、重建与验证后切换。详见[统一模型设计](docs/models/adapter-design.md)。

`EXTERNAL_SERVICES_ENABLED=false` 与 `ALLOW_REAL_REQUESTS=False` 默认关闭。历史验收批次不是新部署的授权，新克隆不会生成生产配额；正常云端使用需要另行配置明确的服务权限、来源与额度策略。

## 技术与目录

React / TypeScript / Vite · FastAPI / Python 3.12 · LangGraph · PostgreSQL / SQLAlchemy / Alembic · Qdrant · Celery / Redis / Beat · PDF.js / KaTeX / Cytoscape.js · Pandas / Matplotlib / NetworkX / Mermaid。

```text
frontend/       页面与组件
backend/        API、领域工作流、模型适配、任务、迁移与测试
model_config.py 统一模型与协议绑定配置
local_services/ 可选的本地服务
deploy/         Dockerfile、Compose、反向代理
docs/           需求、设计、分阶段验收与参赛交付清单
*.py            可直接在 IDE 运行的项目专用入口，参数位于顶部
```

## 验证

```bash
.venv/bin/python doctor.py
.venv/bin/python validate.py
```

`validate.py` 创建独立临时数据库/队列/向量集合，检查依赖、Ruff、前端测试/构建、后端集成和当前凭据误提交；需要本项目基础容器运行。报告在忽略的 `var/reports/`。重启恢复检查见运行手册；本机模式与容器模式互斥，不能同时启动。

原件、账号、模型 Key、日志、缓存与运行报告不进 Git。不要上传 `.env`、数据库备份或原比赛论文。安全说明见 [SECURITY.md](SECURITY.md)。

## 比赛与来源说明

- [比赛官方页面](https://www.boxuegu.com/matchTrack/detail/?id=10040)，赛题 id=10040；本项目按用户确认的 2026-11-18 目标推进。官网/本地规则的差异与时间问题保留在[需求基线](docs/requirements.md)。
- [参赛交付检查单](docs/submission/README.md)：源码上传、技术 PDF、视频和公网评审分别核验。
- [文档导航](docs/README.md) · [AI 辅助开发披露](docs/submission/ai-contribution.md) · [第三方来源说明](THIRD_PARTY_NOTICES.md)。
- 仓库维护者：[JingleiCui](https://github.com/JingleiCui)。真实参赛者姓名、学校、团队和参赛编号尚未填写，不从账号推断。
- 本仓库尚未指定整体代码开源许可证。公开可访问与授予复用许可是两回事；第三方依赖依各自许可证使用。正式提交前由作者确定授权方式，不能把当前仓库宣称为已采用 MIT。
