# 环境与脚手架初始化评审单

日期：2026-11-13。状态：**待用户确认后执行初始化**。

用户已确认数据库与向量库设计。本轮提供可执行的初始化方案，未创建项目运行环境或业务代码。

## 需要确认的实施边界

| 项目 | 提案 |
|---|---|
| 项目目录 | `/Users/cuijinglei/PycharmProjects/ScholarShip`，独立 Git/PyCharm 项目 |
| 工具链 | 项目 Python 3.12.16、Node 24.22.3、uv 0.13.8、pnpm 11.28.11；现有全局环境不改 |
| 开发运行 | Mac 本机 API/worker/Beat/Vite；Docker 只运行本项目 PG/Qdrant/Redis；PyCharm 直接运行入口 |
| 服务版本 | PostgreSQL 17.12、Qdrant/server+client 1.20.0、Redis 7.4.12；精确候选及镜像见方案 |
| Docker 边界 | 使用现有 daemon；不停止其他容器、不复用其他项目数据、不自动调整 VM/重启 Docker |
| 数据初始化 | 实现已确认 41 张表及迁移、作用域/外键/RLS，官方 checkpoint 独立 schema；不自动批量上传论文 |
| 脚手架范围 | 可启动 API/前端导航、后台任务与事件基础设施、健康诊断、配置入口及测试；六模块业务另按批次开发 |
| 配置与费用 | 代码集中配置 + 私有 env 密钥；建议模型/向量/重排首期 30 天合计 100 元预算、不自动续期；初始化不调用付费 API |
| 验证 | 环境方案 E01–E10；记录真实成功、失败和未测试项，不能只以依赖解析代替运行验收 |

## 已发现的本机条件

默认 Python 是 3.15.0，但独立 Python 3.12.8 可作引导。Docker VM 约 3.84 GiB、已有 9 个容器；当前采样约 2.44 GiB，必须实测新增服务占用。规划端口与现有 LISTEN 清单不冲突，启动前会再查。

已核验 Qdrant 1.20 新增原生 BM25，客户端 1.20 含对应模型接口。Python 候选直接依赖的 dry-run 与临时目录的前端 package-lock-only 元数据解析均已成功；完整锁定安装、前端构建、镜像启动、迁移、RLS 和任务验证仍待初始化。

## 文档与证据

- [详细执行方案](environment-plan.md)
- [精确依赖候选](environment/dependency-candidates.json)
- [本机与预检记录](evidence/environment-audit.json)
- [方案一致性检查结果](evidence/environment-plan-check.json)：文档链接、精确版本、端口、Qdrant 版本配对和依赖预检均通过；运行验证尚未执行。

确认本方案后，下一步直接执行项目工具链、环境和脚手架初始化，并跑通 E01–E10。初始化过程中遇到可逆的常规兼容问题会修复并记录；涉及停止现有服务、扩大外部访问或购买资源的变化会单独说明。
