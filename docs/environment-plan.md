# 环境搭建与脚手架初始化方案

日期：2026-11-13。状态：**待用户确认**。需求、技术、架构及数据设计已确认；本轮完成本机检查、公开版本核验和依赖解析预检，尚未安装项目依赖、启动 ScholarShip 容器、建库或初始化代码。

配套文件：[初始化评审单](environment-review.md)、[精确候选版本](environment/dependency-candidates.json)、[本机与预检记录](evidence/environment-audit.json)、[镜像来源核验](evidence/image-candidates.json)。版本清单是待落地候选，不等同于已生成的 uv.lock/pnpm-lock.yaml 或运行测试结果。

## 1. 已检查的本机条件

| 项目 | 实测 |
|---|---|
| Mac | Apple Silicon / arm64，36 GiB 内存 |
| 默认 Python | `/opt/homebrew/bin/python3`，3.15.0 |
| 可用 Python 3.12 | `/opt/homebrew/bin/python3.12`，3.12.8，可作工具链引导 |
| 当前 Node / npm | Node 24.10.0、npm 11.6.0 |
| 当前 pnpm / Corepack | pnpm 8.11.0、Corepack 0.34.0 |
| uv | 当前 PATH 未找到 |
| Docker | Engine 24.0.7、Compose 2.23.3，daemon 可用，aarch64 |
| Docker VM | 14 CPU、约 3.84 GiB 内存；已有 9 个其他容器运行，采样内存合计约 2.44 GiB（不含 VM/系统开销） |
| PyCharm | `/Applications/PyCharm.app`，2026.1.2，正在运行 |
| Git | 2.50.1；已配置提交身份，仅检查存在性，不在文档中保存姓名/邮箱 |

不修改现有 MySQL、Redis、Nginx 等服务，不复用其数据卷或凭据。所有 ScholarShip 资源使用固定项目前缀。现有 Docker 内存是共享资源，初始化先测实际余量；不自动停止其他容器、重启 Docker 或修改全局资源设置。

## 2. 独立工具链与精确版本

| 组件 | 本项目候选 | 落地方式 |
|---|---|---|
| Python | 3.12.16 | uv 管理的项目专用解释器；启动前核验可下载的 arm64 构建及版本 |
| uv | 0.13.8 | 用已有 Python 3.12 在项目 `.tools/bootstrap/` 建立引导环境，只在该环境安装 uv |
| Node | 24.22.3 LTS | 下载官方 arm64 发行包到 `.tools/node/`，验证官方校验和；不改全局 PATH |
| pnpm | 11.28.11 | 使用项目工具链独立安装/调用，`packageManager` 固定版本；不升级全局 pnpm 8 |
| PostgreSQL | `postgres:17.12-bookworm` | 本项目独立容器和卷 |
| Qdrant | `qdrant/qdrant:v1.20.0` | 与 qdrant-client 1.20.0 对齐；原生全文 BM25 在 1.20 引入 |
| Redis | `redis:7.4.12-alpine` | 独立 broker，启用 AOF；PG 任务记录作为恢复依据 |
| Python 部署镜像 | `python:3.12.16-slim-bookworm` | 与项目 Python patch 版本对齐 |
| 前端构建镜像 | `node:24.22.3-bookworm-slim` | 初始化核验镜像标签和 arm64/amd64 manifest，摘要写入锁定记录 |
| 部署代理 | `caddy:2.11.9-alpine` | 完整容器 profile 使用；初始本机开发使用 Vite 代理 |

后端主要候选为 FastAPI 0.162.5、SQLAlchemy 2.1.8、LangGraph 1.2.21、官方 PostgreSQL checkpoint 3.2.0、Celery 5.7.0；前端为 React 19.3.1、TypeScript 7.0.2、Vite 8.3.8。完整直接依赖及来源见 JSON 清单，避免正文重复列出全部版本。

初始化时先安装目标解释器，再用 uv 生成跨平台锁文件并 `uv sync --locked`，前端使用固定 pnpm 生成锁文件并执行 frozen-lockfile 安装。镜像保留精确 tag，并记录拉取到的 multi-platform digest。禁止以浮动 `latest` 作为交付版本。

当前 Python dry-run 在现有 3.12.8/macOS arm64 上成功，不等同于目标 3.12.16 或 Linux 容器验证。Node 引擎约束、peer 解析、实际前端构建也必须分别检查。若发行包/镜像不可用或能力不匹配，给出具体失败并调整候选，不悄悄替换已确认的检索能力或数据库。

## 3. 开发与部署运行模式

### 本机开发（默认）

PostgreSQL、Qdrant、Redis 使用 Docker；API、Celery worker、Beat、Vite 在本机项目工具链运行，便于 PyCharm 断点调试。macOS worker 使用 `solo`、并发 1，避免桌面开发环境的多进程 fork 差异。API 单进程开发热重载；Beat 单实例。

| 服务 | 监听 | 初始资源上限/说明 |
|---|---|---|
| 前端 | `127.0.0.1:15173` | Vite；代理 `/api`，浏览器保持同源会话 |
| API | `127.0.0.1:18080` | Uvicorn；原文下载与 SSE 走同一代理 |
| PostgreSQL | `127.0.0.1:15432` → 5432 | 512 MiB；连接池初值 5、额外连接 5 |
| Qdrant HTTP | `127.0.0.1:16333` → 6333 | 768 MiB；初版使用 HTTP，不额外暴露 gRPC |
| Redis | `127.0.0.1:16379` → 6379 | 128 MiB；broker 不采用淘汰队列消息的策略 |
| 本机容器集成入口 | `127.0.0.1:18081` | 完整容器 profile 的 Caddy 入口 |

端口在本轮 LISTEN 盘点中未占用，启动前再次检查；冲突时在代码配置区集中修改，不终止占用端口的其他进程。容器上限是限制而非预留或性能保证；当前 VM 不能据此承诺全负载。若余量不足则记录资源瓶颈并讨论 Docker 资源调整，不替用户关闭现有服务。

### 容器集成与后续公网部署

使用同一 Compose 项目下的独立 profile 启动 API/worker/Beat/Caddy。服务间使用 Compose DNS 和内部端口，不能把 host 的 15432/16333 等地址写进容器连接串。对外只发布代理入口，数据库、向量库、Redis 保持内部可达。公网机器、域名、HTTPS、学生优惠和服务保持周期另行核验，此阶段不购买。

开发与容器模式互斥运行 API、worker、Beat，避免重复调度；共用的仅是明确指定的本项目基础服务和数据卷。测试使用独立数据库/卷及 `ss_probe_`/`ss_test_` collection，清理只允许精确前缀和资源清单。

## 4. 目录与 PyCharm 入口

```text
ScholarShip/
  pyproject.toml              # 后端、测试与工具依赖；包位置 backend/src
  uv.lock
  .python-version
  .node-version
  .env.example               # 只有键名和无敏感示例
  bootstrap.py               # 一次性初始化入口；配置在文件顶部
  dev.py                     # 日常启动入口；默认 MODE=all
  doctor.py                  # 版本、端口、服务、迁移和可选凭据状态
  .run/                      # 可共享 PyCharm Run Configurations
  backend/src/scholarship/
    main.py
    config.py
    api/ domains/ adapters/ infrastructure/ workers/
  backend/migrations/
  backend/tests/
  frontend/
    package.json
    pnpm-lock.yaml
    src/app/ features/ components/ styles/
  deploy/
    compose.yaml Dockerfile.api Dockerfile.web Caddyfile
  docs/                      # 保留已确认需求和设计
  var/                       # 原件、暂存、日志、报告等；不提交
  .tools/ .cache/ .venv/      # 项目专用；不提交
```

PyCharm 以独立新窗口打开此目录，项目解释器设为根目录 `.venv/bin/python`。共享运行配置至少包含 Bootstrap、All、API、Worker、Beat、Doctor 和 Tests；前端可以由 All 入口管理，不依赖特定版本 PyCharm 的 npm 插件功能。

`dev.py` 顶部提供 `MODE`、端口、是否启动前端、worker 数量与超时等可编辑配置。支持 `all/api/frontend/worker/beat`；日常无需记长命令。All 只启动本项目进程，Ctrl+C/停止按钮回收其子进程，不按端口批量杀进程。基础服务未就绪则先给出 doctor 结果，不偷偷切到 SQLite 或内存状态。

## 5. 配置、密钥与默认费用控制

延续 `user-code-config-preference`：常用运行参数在代码配置区集中编辑，CLI 仅作可选覆盖。优先级明确为：可选 CLI > 显式进程环境 > 本地 `.env` > 代码默认值；启动诊断显示配置来源并隐藏敏感值。

| 配置组 | 主要内容 |
|---|---|
| 运行 | 项目/数据/测试资料路径、运行模式、端口、超时、worker 并发 |
| 数据 | runtime/auth/checkpoint/migration 连接用途、Qdrant 地址、Redis broker |
| 检索 | 已确认 profile、1,024 维、600/900/80 分块、RRF 和重排数量 |
| 任务 | 自动重试 2 次、规划 8 轮、租约/心跳、事件留存与批量写入 |
| 外部服务 | DeepSeek、百炼和 MinerU 的 endpoint/模型/凭据；初始化保留空凭据 |
| 预算 | 建议首期 30 天内，文本模型/向量/重排合计上限 100 CNY；不自动续期、充值；未知调用保留预留 |

初始化阶段不发起真实模型、OCR 或付费搜索调用；基础能力探针使用明确标注的本地合成数据。实际接口集成阶段核验额度和价格后启用，未配置服务显示“未配置/不可用”，不返回伪造结果。

`.env` 第一次从模板生成，数据库/Redis 密码、Qdrant key、会话密钥采用独立随机值，已有文件不覆盖。API/worker 仅注入各自需要的键；迁移管理员连接只供初始化/迁移进程，不能通过整份 env_file 将超级用户凭据传给业务进程。前端只接受白名单 `VITE_` 公共配置，不注入后端密钥。

`.gitignore` 使用根锚定 `/.tools/`、`/.cache/`、`/.venv/`、`/var/`、`/frontend/node_modules/`、构建产物与本地环境文件，保留 `.env.example`；不得误忽略 `docs/data/` 中的已确认设计。禁用非必要的模型 tracing/遥测，日志与错误报告脱敏。

原始比赛文件目录继续只读使用，不把 125.7 MiB 的论文集提交 Git；运行时导入/解析结果进入 var。真实论文测试和合成基础设施探针分别记录，不能混为功能验收。

## 6. 初始化执行顺序

1. 复核目录指令、版本/端口/架构/磁盘/内存；确认当前路径是 ScholarShip。
2. 初始化独立 Git 仓库，使用已有提交身份，工作分支 `codex/bootstrap`；先提交现有已确认文档，再记录实际脚手架变更，保持真实时间线。
3. 建立项目工具链；核验下载校验和、版本与可执行架构，不修改全局解释器、全局 pnpm 或 shell 配置。
4. 创建根 pyproject、前端 package、锁文件、配置入口、密钥模板、忽略规则、目录与 PyCharm 运行配置。
5. 创建项目专用 Compose 网络/卷和基础服务；管理员角色仅用于引导，建立受限 runtime/auth/checkpoint 访问及 scholar/agent schema。
6. 实现已确认 41 张业务表的 SQLAlchemy 元数据和 Alembic 初始迁移，补循环复合外键、索引、不可变更新保护、RLS/权限；官方 checkpoint 独立执行其 setup 迁移并记录版本。服务启动不自动并发迁移。
7. 加入 API live/readiness/meta、结构化错误/日志、Celery 与 Beat 入口、事务 outbox 基础设施、SSE 事件契约、前端六入口导航骨架与任务状态组件；尚未实现的功能明确标为待开发。
8. 运行基础验证、生成实际报告，并在 PyCharm 选择新项目解释器及运行入口；对失败先修复再报告结果。

本轮用户确认方案后即可进入上述初始化，不再增加一次相同阶段的重复确认。付费购买、公开发布和比赛最终提交仍不包含在此初始化动作中。

## 7. 初始化交付验收

| 编号 | 验收 | 通过依据 |
|---|---|---|
| E01 | 工具链隔离 | doctor 显示目标版本、arm64、项目解释器/Node/pnpm 路径；全局版本不变 |
| E02 | 依赖可复现 | uv/pnpm 实际锁定安装成功；核心 import、TS typecheck 和生产构建成功；锁文件可 frozen 重装 |
| E03 | 基础服务 | 本项目 PG/Qdrant/Redis 健康，数据卷/端口正确，其他项目容器未被操作 |
| E04 | 数据迁移 | 41 张业务表及设计约束存在，agent schema 独立；空库 upgrade、重复执行和当前 revision 检查通过 |
| E05 | 隔离与约束 | 两个测试账号的 RLS、复合外键、版本指针、费用关联负例；业务角色无管理员/BYPASSRLS 权限 |
| E06 | BM25 能力 | Qdrant 1.20 scoring 全文索引、稠密/BM25/RRF 请求真实执行；合成探针不冒充论文问答效果 |
| E07 | 后台与事件 | 实际 worker 队列往返、outbox 幂等与租约、事件游标/SSE 重连测试；不只 mock Celery |
| E08 | 配置与预算 | 空凭据时可启动基础服务，外部调用禁用且显示未配置；模板无密钥，预算参数与脱敏有检查 |
| E09 | UI 与 IDE | PyCharm 新窗口与项目解释器可运行；浏览器打开导航骨架/任务状态，无控制台阻断错误 |
| E10 | 报告与恢复 | 记录执行命令、版本、迁移、测试、截图和失败/未测项；停止项目进程后能按文档再次启动 |

API `/api/v1/health/live` 只说明进程存活；`/api/v1/health/ready` 检查基础依赖和预期迁移版本，缺失则 503；`/api/v1/meta` 报告非敏感版本和功能配置状态。可选外部服务未配置应单列，不伪装成完整六模块可用。

本阶段通过代表项目基础设施可运行，不代表 A01–A25 比赛功能全部完成。后续按导入/解析/证据问答 → 阅读翻译 → 检索推荐/图谱 → 写作绘图的业务增量开发，每批提供实际验收。

## 8. 预检边界与来源

Python 依赖解析 dry-run 已成功：27 项直接候选、105 项解析后发行包，目标为既有 3.12.8，下载到缓存但没有安装包。前端在临时目录执行 npm package-lock-only/ignore-scripts 解析成功：17 项直接候选、236 个 lock package 记录，没有执行安装脚本；项目固定 pnpm 的锁定安装和构建仍未进行。镜像来源和客户端接口检查的实际状态见证据文件。运行、镜像拉取、迁移与功能测试尚未发生。Docker Hub 公共标签接口返回过 429，镜像候选改由 Docker Official Images 清单核验，拉取摘要和运行能力仍待实际验证。

- [uv 安装和管理 Python](https://docs.astral.sh/uv/guides/install-python/)
- [pnpm 安装与版本固定](https://pnpm.io/installation)
- [Node 官方发行索引](https://nodejs.org/dist/index.json)
- [Qdrant 1.20 原生 BM25 发布说明](https://github.com/qdrant/qdrant/releases/tag/v1.20.0)
- [Docker Official Images 清单](https://github.com/docker-library/official-images/tree/master/library)
- [Python 包原始元数据](https://pypi.org/pypi/langgraph/json)
- [前端包原始元数据](https://registry.npmjs.org/vite)
