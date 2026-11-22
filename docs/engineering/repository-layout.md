# 项目结构与维护入口

当前按应用、模型服务、验收和历史证据分工。目录调整保持既有 Python 包和业务 API 路径，避免为了移动文件破坏任务恢复。

```text
backend/
  src/scholarship/
    api/                 身份校验、项目接口、请求与状态
    domains/             文献、索引、证据问答与 Studio 业务
    models/              配置目录、协议、云端/本地/模拟执行器
    infrastructure/      PostgreSQL、文件、健康和初始化
    workers/             持久任务、投递和进程执行
  migrations/            冻结历史迁移 + 后续前向迁移
  tests/                 默认回归；全部使用隔离数据库
local_services/          权重下载器和独立本地重排服务
frontend/src/            页面、执行条件与阅读交互
scripts/                维护工具与合并后的回归入口
  check_backend.py       contracts / business / pipeline / local / all
model_config.py          可编辑的模型目录和角色选择
setup_local_models.py    本地下载、依赖、服务和验收协调器
verify_local_journey.py  真实本地 API 用户流程；MinerU 复用云端产物
validate.py              默认全量回归、构建、依赖与密钥检查
runtime_smoke.py         真实 HTTP/队列与重启恢复
.tools/、var/、private/   私有依赖、权重、运行文件和报告，不提交
```

## 测试职责

- `backend/tests/test_*.py`：默认回归使用模拟供应商响应；数据库、队列和向量库真实且隔离。不消耗模型推理，不证明模型质量。
- `backend/tests/local_model_acceptance.py`：必须显式运行的真实本地模型验收，默认测试发现不会运行；包括实际向量、聊天、翻译、视觉和隔离环境 HTTP/队列用户流程。
- `scripts/check_backend.py` 合并原 `verify_m2.py`、`verify_m2_business.py`、`verify_model_pipeline.py` 的重复启动代码。原测试主体保留；可编辑顶部 GROUP，命令行 `--group` 仅作覆盖。
- 原 `scripts/test_local_models_doctor.py` 的库存、重定向、响应上限和远程端点拒绝测试已合并到默认回归的 `test_local_inference.py`。旧的“本地调用走云端预算”测试被独立本地准入、幂等、隔离与 profile 证明测试替代。
- 原始低层 HTTP 冒烟脚本不再作为用户流程验收入口，避免把没有业务回执的直连响应算作全流程通过。

## 保留边界

历史验收报告与授权记录、已部署迁移、原始 PDF、独立测试的负例和恢复测试不因年代较早而删除。已有云端验证/恢复工具保留只读或显式授权边界，不合并进本地下载脚本。`.env` 的密码不会自动同步到数据库账号，也不会在正常数据面工具中加载迁移管理员凭据。
