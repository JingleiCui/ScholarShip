# Docker Compose 运行

应用可在本机进程或容器中运行，两种模式互斥。Compose 项目名为 `scholarship`，项目入口会显式传入目录与私有环境文件；不要用停止全机容器的方式切换。

当前镜像需要从源码构建，没有已发布的官方预构建镜像，也没有可以直接使用的公网演示地址。容器集成在 macOS Docker Desktop 上验证；Linux amd64 和公网环境仍需独立检查。

## 已初始化环境切换到容器

先完成[初始化](getting-started.md)，然后：

```bash
.venv/bin/python stop.py
.venv/bin/python deploy.py --action build
.venv/bin/python deploy.py --action up
```

页面入口：http://127.0.0.1:18081 。build/up 前按数据库登记核对共享文件的大小与哈希，缺失时先恢复文件，不跳过检查。容器与本机共用项目 `var/`，PostgreSQL/Qdrant/Redis 使用项目数据卷。

## 切回本机

```bash
.venv/bin/python deploy.py --action stop
.venv/bin/python dev.py
```

只停止本项目应用容器，保留基础服务。容器镜像包含构建时的前后端源代码；更新源代码后需要重新 build，不能把浏览器中的本机热更新误认为容器也已更新。

## 公网环境

当前默认只绑定回环端口。上线前需要独立配置 HTTPS、反向代理、账号交付和备份恢复；不要直接把开发端口公开。前后端数据持久化、云端模型权限与可用性应分别验收。详细生命周期和排查说明见[运行手册](../runbook.md)。
