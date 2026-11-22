# 本地模型准备与验收

配置目标：Ollama `qwen3.5:9b` 负责文本与图内识别，`qwen3-embedding:0.6b` 负责 1024 维向量，Qwen3-Reranker-0.6B 独立服务负责重排。**MinerU 使用云端，取消本地安装、下载和解析任务**；历史本地 MinerU 目录别名保留用于旧配置恢复，不作为当前运行目标。

入口为项目 `.venv` 下的 `setup_local_models.py`，或 `local_models.py --action all`。主要参数在脚本顶部。所有下载、日志和依赖位于项目 `.tools/`、`var/`。只读检查使用 `local_models.py` 或 `local_models_doctor.py`，不会推理。

## 下载与服务

官方 Ollama manifest 固定每层 SHA-256。完整共享缓存先只读校验再复制，避免重复下载；不修改用户全局缓存。缺失文件支持续传，大文件按范围下载，完整校验后才发布 manifest 并清理本项目临时分块。使用显式本地代理时仍验证 HTTPS 证书。

重排权重来自官方 Qwen ModelScope 仓库的固定 revision，并按发布的 SHA-256 校验每个文件。服务从项目缓存加载，禁止运行时自动联网下载。MinerU 云端产物仅复用已有授权下载结果；选择云端解析不等于新上传或新额度授权。

协调器拥有唯一任务锁、阶段状态及退出码；各准备阶段独立记录失败。缺少依赖时不启动推理，未完成不会标记通过。仅复用工作目录属于本项目的服务，不停止其他项目进程。本地模型单并发，向量上下文设为 2048，避免小模型不必要的上下文内存开销。

## 执行边界

应用的 `local_inference` 与确定性本地工具 `local`、模拟 `offline_acceptance`、历史云端 `cloud` 分开。新增本地准入及真实回执独立于云端总闸和预算；云端策略、限额与历史调用记录保持原样。成功本地 embedding 通过本地准入证明登记独立真实 profile，禁止混用模拟或云端旧索引。

默认预设为 local，解析角色为 MinerU 云端。新建项目继承当前预设；已有项目的明确选择继续保留，可通过项目模型配置接口切换。任务仍固定配置快照，切换不重写旧任务。

## 验收与报告

- `validate.py`：默认隔离回归、前端构建、依赖与密钥检查，不调用真实模型。
- `.venv/bin/python -m pytest -q backend/tests/local_model_acceptance.py`：显式真实本地验收。临时数据库验证实际向量、回执、profile、证据引用、翻译、视觉，以及真实 HTTP/队列/重启和完整用户流程。
- `verify_local_journey.py`：在指定本地 API 的独立验收空间中执行实际流程，避免替换历史项目的已批准索引。合成 PDF 验证已知事实；扫描论文使用既有云端 OCR zip，不重新上传。视觉结果保留未审阅状态，不将接口成功冒充科研或图结构语义验收。
- 生命周期测试和用户流程读取环境变量或私有 `.env` 的 `LOCAL_ACCEPTANCE_PASSWORD`，否则使用初始化密码。密码仅用于登录，不自动更新数据库账号。

机器报告位于 `var/reports/local-embedding-real.json`、`local-generation-real.json`、`local-journey.json`、`local-setup.json` 和 `runtime-local.json`。报告明确标记隔离验收环境；`passed=true` 仅代表该报告列出的范围。

目录与历史入口迁移说明见[项目结构](../engineering/repository-layout.md)。
