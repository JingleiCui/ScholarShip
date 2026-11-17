# ScholarShip 本地运行手册

本机项目：`/Users/cuijinglei/PycharmProjects/ScholarShip`。Python 解释器：根目录 `.venv/bin/python`。常用选项在入口脚本顶部修改，命令行覆盖仅为可选方式。

## 当前版本（2026-11-17）

最新功能及检查见[复核记录](qa/completion-2026-11-17.md)。完整操作见[使用手册](guides/user-manual.md)。批量导入、版本比较、翻译修订、参考文献审阅、任务取消和 SSE 步骤均已接入；下方旧阶段叙述仅保留历史。

以下旧阶段段落保留历史背景；当前状态以[本批报告](studio/report-2026-11-16.md)为准。迁移已到 0013，41 张业务表未变。云端批次 74/80 完成，所有准入已关闭，未使用的六次不能直接续用。模型选择与 Key 配置本身不授权请求。

六模块在顶部导航中操作，研究空间在页顶切换。默认空间含扫描件真实问答、翻译及带原文来源的写作产物；“比赛本地语料验收”含 23 篇本地解析论文，图谱边需身份复核。历史错误回答有警告；全文翻译、摘要/框架均可回看来源。已有产物阅读不消耗模型额度。

`verify_studio_local.py` 验证受控图表、拓扑图及本地编辑派生，不请求模型；`run_cloud_business.py` 默认只读 status，已完成批次不可再执行。`prepare_competition_corpus.py`、`verify_textbook_structure.py`、`review_official_answers.py`、`recover_translation_receipts.py` 均默认只读状态，配置在顶部。`format_frontend.py` 使用锁定的项目格式器。

部署预检通过 runtime 专用的只读文件清单函数核对所有已登记 ready 文件，不能使用空租户查询假装全部通过，也不再读管理员配置。管理员仅用于显式迁移和已授权的策略维护。

## 本地模型检查

在 PyCharm 运行 `local_models_doctor.py`，无需填写 Key。它核对 local 配置对应的 Ollama 模型清单，报告明确区分模型缺失与尚未完成的业务接线。当前不是本地推理启动器，不自动安装、拉取模型或调用云端。结果在 `var/reports/local-models.json`；详细状态见[复核记录](qa/completion-2026-11-17.md)。

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

原始比赛资料目录只读；本机和容器现在共用宿主 `var/`（容器 `/app/var`）。`deploy.py` 在 build/up 前按 PG 文件清单校验大小和哈希；旧 `scholarship_file_data` 卷保留，缺失文件先迁移并复核，不能跳过预检。见[存储方案](m2/storage.md)。本批未重建应用容器。PG/Qdrant/Redis 各有独立项目数据卷。

外部模型、向量、重排和 MinerU 接口尚未启用。共享预算池已补齐，100 CNY / 30 天与账号预算同时准入；离线执行器、未知占额及幂等结算已验证。真实供应商调度尚未启用，不能以当前配置宣称供应商账单硬上限。详见 [M2 账务设计](m2/billing-design.md)。

不可变记录禁止普通角色任意改删；不要通过清库解决迁移问题。业务数据备份、文件 manifest 和恢复演练在真实资料导入前补齐；此阶段只完成空库迁移与任务/会话恢复验证。

## M1 使用

登录后进入“文献管理”，导入 PDF 并选择文献。可按 PDF 文件页码查看原件和对应预览文本。扫描页显示无可提取文本；部分完成不等同于 OCR 成功。追加扫描/矢量等不同载体时先选定作品并明确勾选新版本选项。疑似重复记录需人工保留或拒绝。新修订与继续失败页是两个不同操作。真实样本与恢复检查见 `evaluate_m1.py` / `recover_m1.py`；后者会停止并重启本项目进程，请先保存当前调试工作。

## M2 离线复验

Run `verify_m2.py` 执行四类适配契约和独立测试库账务检查，不发送真实网络请求；完整回归仍用 `validate.py`。工作台显示共享本地预算及服务关闭状态，不代表供应商余额。真实供应商上传与研究问答未启用，详见 [M2 报告](m2/offline-report.md)。

## M2 离线业务演示

研究账号在工作台点击“准备合成文档与索引”，选择文献后运行离线问答，点击引用查看原文。刷新会恢复最近任务；所有回答明确标记合成数据。Run `verify_m2_business.py` 验证新增业务链路，Run `storage_preflight.py` 检查共享文件。真实模型、云端解析和科研效果尚未验收，见[本批报告](m2/business-report.md)。

## 可插拔模型配置与流程验收

根目录 `model_config.py` 集中配置提供商、命名模型、能力与 mock/local/free_api/free_gemma 配置组；Key 只放私有 `.env`。当前项目选 free_gemma（Gemma 生成 + Nemotron 向量/重排），ChatAnywhere 保留。配置只影响后续任务，不打开推理。更换 embedding 协议、输入策略或维度需要新 profile 和已验证世代；当前活动索引保留。

Run `verify_models.py` 做脱敏配置诊断；Run `verify_model_pipeline.py` 完成隔离库中的多组合端到端与断点恢复，不发送真实模型请求。完整回归为 `validate.py`。本机已迁移到 0008_adapter_profiles，业务表仍为 41 张。详情见[配置说明](models/configuration.md)、[设计](models/adapter-design.md)和[验收](models/refactor-report.md)。

`local_models.py` 默认只检查隔离服务，未安装的本地 OCR/重排服务不会因选择 local 而自动安装或启动。

## 真实联调状态

统一入口 `validate_live_models.py` 默认 status。当前最终模型验证 completed，累计 12/12；issue 开关已关闭，run 只读完成状态。本轮用标准论文两段短片段跑通真实向量、检索、重排与 ChatAnywhere 引文回答，不代表全文题集、扫描入库问答或比赛六模块已通过。Gemma 429 与早前失败记录保留。

可选 action：`mineru-status` 查看唯一授权扫描件；`mineru-run` 复用其已完成结果。扫描件 15 页 OCR 与样例参数核对已通过，只上传一次；申请/上传授权开关也已关闭。原始 ZIP/Markdown 保存在忽略的 var/mineru-validation；结果 URL 不公开。

本机结果 CDN 的 DNS/连接问题使用 model_config.py 中 DOWNLOAD_DNS_OVERRIDES 修复：地址来自当日公开 DNS，仍验证原域名的 HTTPS 证书；不改系统 hosts。其他主机可清空该字典，回到原生 httpx；保留覆盖时需本机 curl 支持。签名 URL 通过 stdin 传递，不在进程参数或输出中。此路径已在本机测试，新应用容器尚未重建验收。

MinerU 当前单文件 200 页，541 页教材需三部分及原始页码映射；分区计划已测，未上传教材。供应商未返回账号剩余优先级页数，不能当成已验证数值。详情见[真实报告](models/live-official-report.md)、[比赛审计](competition/audit-2026-11-14.md)及[下一增量](m2/live-business-review.md)。

## M2 正式业务闭环（本批更新）

本节更新上文“真实产物未入库”的历史状态。当前迁移为 `0010_profile_input_policy`。唯一已授权扫描件的 MinerU 产物已正式入库；新问答面板位于工作台及“检索与推荐”页。勾选 OCR 文献，默认“离线验收”，可建模拟索引、提交问题、查看任务和点击引用，进入固定修订 PDF 阅读器。目录、页码、文字块和图片可定位；模拟回答不代表科学正确性。

运行 `verify_m2_live_business.py`：顶部 `ACTION='status'` 查看；`import` 复用已下载 ZIP 并校验原授权来源后本地入库；`acceptance` 幂等建立正式扫描来源的模拟索引并逐项跑官方五问和拒答；`validate` 在隔离测试库跑新增测试。正常开发直接 Run `dev.py`；不需要在页面填写新的 Key。

当前 `free_gemma` 保留；新任务读取项目保存的配置组和单项覆盖，旧任务固定快照。mock/local/free_api/free_gemma 的离线验收全部用模拟 transport。`local` 的真实推理、真实标准双栏/教材深解析、跨文及多轮科研质量仍待验收。

云端业务入口已接线但拒绝执行：外部总开关仍关，生产策略未创建/启用。旧 12/12 名额和唯一扫描件生命周期不变。不要为尝试新页面而修改旧授权或开上传开关。正式启用需要独立的明确免费/限额策略、共享 live budget 和配置准入；本次不配置这些权限。

详见[设计](m2/live-business-design.md)、[验收](m2/live-business-report.md)和[官方题集](competition/qa-cases.json)。

## 用户路径一键复验（2026-11-15）

服务已通过本项目 `dev.py` 启动后，Run `verify_user_journey.py`。脚本使用本地研究账号和已发布扫描来源，执行 20 项本地 API 用户路径检查；只创建明确标记的模拟任务、做一次本地同文件复用检查，不调用云端。地址、测试文件和问题在脚本顶部修改。报告保存在忽略的 `var/reports/user-journey-api.json`。

页面现在支持导入结果反馈、解析修订选择、结构化阅读入口、原文缩放、模型切换后的索引就绪提示，以及刷新保留模块。详见[用户路径报告](qa/user-journey-2026-11-15.md)。

若 `stop.py` 显示停止 0 个启动器但端口仍占用，不能当作新版本已启动，也不要用模糊进程名停止服务；先检查占用进程的命令、项目 cwd、父子关系及本项目锁。受管理启动器之外的进程需单独核实归属后恢复。本轮遗留进程的精确恢复记录保留在私有报告中。
