# 运行与故障排查

使用项目根目录 `.venv/bin/python`，正常运行配置可在入口脚本顶部修改。本地应用模式与容器应用模式互斥，不要同时启动两套 worker 或 Beat。

## 本地启动

1. 启动 Docker Desktop。首次安装按[安装说明](guides/getting-started.md)运行 `bootstrap.py`。
2. 在项目根目录运行 `.venv/bin/python dev.py`。
3. 打开 `http://127.0.0.1:15173`。API 文档为 `http://127.0.0.1:18080/docs`。
4. 使用 `researcher` 登录，密码在私有 `.env` 的 `INITIAL_USER_PASSWORD`。不要展示或提交该文件。

启动器使用项目锁和端口检查，发现占用后会退出，不会终止其他项目。运行 `.venv/bin/python stop.py` 只停止已核实的本项目进程，保留数据库、队列与文件。

## 容器运行

先停止本地应用。运行 `deploy.py`，顶部 `ACTION='build'` 构建，再设置 `ACTION='up'` 启动。入口为 `http://127.0.0.1:18081`。切回本地前先用 `ACTION='stop'` 停止容器应用；基础服务与数据卷保留。完整说明见[部署指南](guides/deployment.md)。

部署会核对 PostgreSQL 登记的 ready 文件与本机文件大小、哈希，不能跳过缺失文件。不要删除旧数据卷来解决文件或迁移问题。

## 诊断与检查

```bash
.venv/bin/python doctor.py
.venv/bin/python validate.py
```

`doctor.py` 检查运行依赖。`validate.py` 检查依赖、代码、前端构建和集成行为，使用唯一临时数据库、独立队列及向量集合，不清理业务数据。报告写入 `var/reports/`。

进程生命周期变更另运行 `runtime_smoke.py`，运行前停止现有本项目启动器。该检查验证会话、排队任务和事件恢复，不代表模型回答正确。

## 任务处理

PostgreSQL 的任务与 outbox 是持久依据，Redis 负责消息投递。重复投递由租约和执行 token 防重。取消是协作式的：排队任务可立即终止，运行任务在安全边界退出。已经发出的供应商请求不能假设被撤回。

外部请求结果未知时保留调用记录和占额，先对账，再决定是否恢复。不要通过清库、重置计数或自动换供应商解决失败。SSE 展示任务步骤，不是逐 Token 生成。

## 云端服务

模型与协议在 `model_config.py`，Key 在 `.env`。服务配置、项目选择与数据/额度准入是独立条件。参见[模型配置](guides/models.md)。401/403 检查账户与模型权限；429 检查限额；连接失败检查项目代理与 TLS。不要打印完整请求头、连接字符串或签名下载地址。

## 文件与数据

原始 PDF、解析图片和产物存放在持久化目录，业务元数据在 PostgreSQL。备份时须同时保存数据库与文件，并核对文件清单。重建 Qdrant 索引不能替代原文备份。恢复步骤应在隔离环境演练，不能覆盖正在使用的库。

使用流程见[完整手册](guides/user-manual.md)，接口定位见[接口与排错](engineering/api-and-debugging.md)。
