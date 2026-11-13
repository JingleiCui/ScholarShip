# ScholarShip 本地运行手册

本机项目：`/Users/cuijinglei/PycharmProjects/ScholarShip`。Python 解释器：根目录 `.venv/bin/python`。常用选项在入口脚本顶部修改，命令行覆盖仅为可选方式。

## 第一次初始化

在 PyCharm 独立打开项目，使用已有 Python 3.12 运行 `bootstrap.py`。它依次准备项目工具链、锁定依赖、私有配置、三个基础容器、业务迁移、官方 checkpoint 迁移和初始账号。已存在的 `.env`、账号密码和项目数据不会重置。首次下载需要网络；日常启动不重新下载依赖。

该引导入口当前针对本机 macOS arm64；Linux 使用下述 Docker 构建方案。安装与测试不会调用 DeepSeek、百炼或 MinerU。

## 日常开发

1. 启动 Docker Desktop。基础容器已运行时可直接继续；否则运行 `bootstrap.py`，顶部 `PHASE='infra'`。
2. 在 PyCharm 选择 **ScholarShip All**，或直接 Run `dev.py`。默认同时启动 API、worker、Beat 和前端。单独调试可选择 API、Worker、Beat 配置。
3. 打开 http://127.0.0.1:15173 。API 文档：http://127.0.0.1:18080/docs 。运行 `doctor.py` 检查真实版本、迁移与依赖。
4. 登录 `researcher` 或 `reviewer`；密码在私有 `.env` 的 `INITIAL_USER_PASSWORD` / `INITIAL_REVIEWER_PASSWORD` 中。请在编辑器中自行查看，不要提交、截图或发送整份 `.env`。数据库只保存 Argon2 哈希，会话原始令牌不入库。
5. 点击“运行探针”，检查 queued → running → succeeded 和三条实时事件。该探针检查本地基础设施，没有研究内容，也不消耗模型额度。
6. 停止 PyCharm 的 All 运行项，或 Run `stop.py`。启动器会回收自己创建的子进程；基础容器和数据卷保留。不要重复启动 Beat。

`dev.py` 使用组件文件锁及端口检查；发现占用会退出，不会杀死占用者。`stop.py` 通过本项目锁文件、进程命令和工作目录核对身份，只停止本项目启动器。开发模式与容器应用模式互斥；切换前先停止当前模式。

## 一键验收与恢复

Run `validate.py`：执行 frozen 安装、核心依赖 import、Ruff、TypeScript/生产构建、真实数据库/队列/向量检索测试和敏感值扫描。报告在 `var/reports/initialization.json`，分项日志与 JUnit 文件在同一目录。

测试自动创建唯一 `ss_test_<uuid>` PostgreSQL 数据库，使用独立 `scholarship_test` Celery 队列，创建后清理精确命名的 `ss_probe_<uuid>` Qdrant collection。不会清空业务库、Redis 或其他 collection。测试只用于本机开发，不得指向他人的生产基础服务。

Run `runtime_smoke.py`：默认验证本机 HTTP 和重启恢复。先停止已有本机 All；脚本先仅启动 API，保存排队任务，再停止并以 All 重启，验证原会话、排队任务和事件回传，最后停止自己创建的进程。

恢复原则：PG 中的任务和 outbox 是依据；Redis 只负责投递。重复消息由 run 租约及 token 比较阻止重复完成；过期租约由 dispatcher 再次投递，最多 3 次尝试。M0 探针为短时、无外部副作用任务；长任务心跳、取消、外部调用 unknown 对账和人工恢复将在相应业务增量中实现，不能由本探针验收代替。

## 完整容器集成

先 Run `stop.py`。运行 `deploy.py`，顶部 `ACTION='build'` 构建，然后 `ACTION='up'` 启动应用。浏览器入口 http://127.0.0.1:18081 。容器通过内部 DNS 访问基础服务，管理员密码没有注入 API/worker/Beat。服务镜像摘要见 `docs/environment/image-lock.json`。

容器已启动后，将 `runtime_smoke.py` 的 `TARGET='container'` 可验证代理、登录、队列和 SSE。切回本机时运行 `deploy.py`，`ACTION='stop'`；只停止 web/API/worker/Beat，保留基础服务，再 Run `dev.py`。

当前是本机回环地址集成配置，未配置公网域名/HTTPS，也未验证 Linux amd64。服务器与学生优惠另行核验，不在当前脚本中购买或发布。API readiness 检查依赖与迁移，worker/Beat 的实际处理能力由探针验证；容器 Running 状态本身不是后台处理成功的依据。

## 数据、密钥和费用

原始比赛资料目录只读；运行文件进入 `var/`（本机）或 `scholarship_file_data`（容器）。两个模式目前分别存储文件，真实文件导入前需要选择主存储或显式迁移，不能假设二者自动同步。PG/Qdrant/Redis 各有独立项目数据卷。

外部模型、向量、重排和 MinerU 接口尚未启用。预算表已建立、初始配置为每账号 100 CNY / 30 天，尚未接入真实调用费用准入和扣费。实际接入时需核验价格、额度、预留与结算，不以当前配置值宣称已经实现费用硬上限。

不可变记录禁止普通角色任意改删；不要通过清库解决迁移问题。业务数据备份、文件 manifest 和恢复演练在真实资料导入前补齐；此阶段只完成空库迁移与任务/会话恢复验证。

## M1 使用

登录后进入“文献管理”，导入 PDF 并选择文献。可按 PDF 文件页码查看原件和对应预览文本。扫描页显示无可提取文本；部分完成不等同于 OCR 成功。追加扫描/矢量等不同载体时先选定作品并明确勾选新版本选项。疑似重复记录需人工保留或拒绝。新修订与继续失败页是两个不同操作。真实样本与恢复检查见 `evaluate_m1.py` / `recover_m1.py`；后者会停止并重启本项目进程，请先保存当前调试工作。
