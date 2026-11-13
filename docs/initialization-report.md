# M0 环境与脚手架验收

日期：2026-11-13。用户已确认环境方案并授权直接初始化。本阶段交付独立、可运行、可重复验证的开发基础；六大业务模块尚未实现。

## 实际交付

- 项目专用 Python 3.12.16、Node 24.22.3、uv 0.13.8、pnpm 11.28.11；`uv.lock` / `pnpm-lock.yaml` 已生成并通过 frozen 安装。全局 Python 3.15.0、Node 24.10.0、pnpm 8.11.0 保持原版本。
- PostgreSQL 17.12、Qdrant 1.20.0、Redis 7.4.12 的独立 Compose 服务、持久卷和回环端口。完整 API/worker/Beat/Caddy 容器模式也已实际构建并验证；镜像摘要见[镜像锁定记录](environment/image-lock.json)。
- `scholar` schema 中 41 张业务表、494 列、147 个外键；初始 Alembic revision `0001_foundation`。初始迁移的数据目录及实现快照已冻结；后续结构变更必须新增迁移。官方 PostgreSQL checkpoint setup 在独立 `agent` schema 执行，重复初始化通过。
- 受限 runtime/auth/checkpoint 角色、owner RLS、复合外键、不可变证据/产物权限、发布解析保护、不可变文档载体、预算周期重叠保护。认证通过受限认证层和具名数据库函数完成，API/worker/Beat 不持有迁移管理员密码。
- FastAPI live/readiness/meta、登录/注销、项目作用域、事务 outbox、Celery/Beat、run 租约及完成 token 校验、LangGraph 持久化探针、可恢复游标的 SSE。外部模型服务默认关闭，未配置时仍可启动基础功能。
- React 工作台、六模块导航、真实依赖状态、登录与真实后台探针。尚未实现的入口明确显示“待开发”。桌面与窄屏已人工检查；页面登录、探针成功和三条事件已在浏览器观察到。
- PyCharm 独立 ScholarShip 窗口和项目解释器；Doctor 配置已在 IDE 实际运行，退出码 0。共享 All/API/Worker/Beat/Bootstrap/Doctor/Tests 配置随源码交付。
- 流程化操作已落为 `bootstrap.py`、`dev.py`、`stop.py`、`doctor.py`、`validate.py`、`deploy.py`、`runtime_smoke.py`。具体用法见[运行手册](runbook.md)。

## E01–E10 结果

| 编号 | 结果 | 证据与边界 |
|---|---|---|
| E01 工具链 | 通过 | doctor 实测项目路径、arm64 和精确版本；复查全局版本未变 |
| E02 依赖 | 通过 | uv/pnpm frozen 安装、核心 import、Ruff、TS typecheck 与 Vite 生产构建 |
| E03 基础服务 | 通过 | 独立 PG/Qdrant/Redis 实际可连接，业务进程使用受限连接；未操作其他项目服务。初始基础容器采样约 177 MiB，不代表满负载性能 |
| E04 迁移 | 通过 | 唯一临时空库执行 upgrade、重复 setup，查询实际 41/494/147，检查 agent 迁移表 |
| E05 隔离 | 通过 | 双账号轮换和连接池作用域复位、跨账号写入/外键负例、错版本解析/索引指针、发布后修改、不可变审计、费用账号关联和产物来源 XOR 负例；并发重叠预算周期仅一个成功 |
| E06 BM25 | 通过 | 真实 Qdrant scoring 文本索引、1,024 维稠密查询、BM25 与 RRF，召回前 owner 条件生效。使用合成数据及手工向量；未验证论文语义召回/重排质量 |
| E07 后台与事件 | 通过 | 实际 Celery worker/Beat/Redis 往返，幂等请求、重复执行无新增事件、过期租约重新投递、持久 checkpoint 复用、SSE 游标重连/越界/过期检查。另有 HTTP 重启恢复验证 |
| E08 配置与费用 | 通过（M0 范围） | 无外部凭据仍可运行；模板不含实际密钥，扫描未发现待提交文件包含本地密钥；预算配置已建。真实费用预留/结算尚未接入，未验证 100 元硬上限 |
| E09 UI 与 IDE | 通过 | 页面真实登录与探针完成；浏览器首次布局检查没有控制台 error；PyCharm Doctor 使用项目解释器且退出码 0 |
| E10 报告与恢复 | 通过（M0 范围） | 一键报告、JUnit、分项日志和截图；仅 API 排队后停止并以 All 重启，原登录会话及排队任务恢复；容器代理链路也通过。研究文件备份/恢复演练待真实导入前完成 |

自动集成测试共 **13 项通过**。最近一次执行的耗时、日志与机器结果位于 `var/reports/initialization.json`、`var/reports/pytest.xml`；可提交的脱敏摘要在[初始化证据](evidence/initialization.json)。本机/容器 HTTP 探针分别记录于 `var/reports/runtime-local.json`、`runtime-container.json`。

## 已处理的问题

初始化中修复了运行角色缺少迁移版本只读权限、Qdrant 客户端不支持 context manager、Compose 显式 project-directory 与构建相对路径不一致等问题。PyCharm 首次诊断曾报告依赖暂不可用，复核服务并重新执行后通过；未将首次失败记为成功。最终结果以实际检查报告为准。

启动器加入组件锁、端口占用检测和 SIGTERM 子进程回收；停止脚本核对本项目锁文件、进程命令及工作目录。容器与本机应用互斥运行，避免双 Beat。

## 尚待业务开发验证

本轮没有调用模型、向量/重排收费 API 或 MinerU，也没有购买服务器、公开发布或提交比赛。原始资料未修改、未批量导入、未提交 Git。项目历史从真实文档基线和初始化提交开始，没有伪造往期开发记录。

后续必须完成：真实 PDF 导入/解析与 541 页材料恢复、分块编码与业务索引 profile 启用、MinerU 额度与失败恢复、官方单篇/多篇问答及原文定位、翻译/引用图谱/推荐/方向核验、受控绘图与数据脚本血缘、费用准入结算、公网部署和比赛交付材料。当前探针只检查基础设施，不提供研究答案。

文档块树、引文子串上界、血缘无环、active 指针就绪条件等服务语义规则，会随对应业务接口实现并增加负例；不能以 147 个外键代替这些业务校验。长任务心跳、取消和外部调用未知结果对账也属于后续业务执行器范围。
