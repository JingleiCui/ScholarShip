# 本地链路验收（2026-11-22）

一键脚本已完成且退出成功，日常应用已恢复运行。验收使用独立临时数据库、专用队列和隔离向量集合；真实调用本机模型，未重置原账号密码，未替换历史研究项目的索引。新增前向迁移 0015_local_inference 已部署。

## 结果

- 默认 validate.py：250 项后端测试全部通过；前端测试/构建、依赖检查、Ruff 和密钥扫描通过。
- 显式真实本地验收：3 项通过，包含实际向量与 profile、聊天/翻译/视觉，以及真实 HTTP、队列、登录会话与重启恢复。
- 浏览器检查：问答与写作默认本地执行，桌面及 390px 移动端页面已检查，无水平溢出。
- 541 页教材：文件哈希与页数通过只读复核，4 页为空文本；未做 OCR/模型调用，pypdf 重复 Form XObject 限制不作为文本完整性或科学质量验收。
- 云端政策启用数 0，两个云端总闸保持关闭，当天新增 OpenRouter/ChatAnywhere 非模拟调用 0。MinerU 只复用已经下载的批准扫描件产物，本轮云端上传 0。

| 步骤 | 状态 | 应用任务秒数（含排队与轮询） |
|---|---|---:|
| fixture_preview | succeeded | 2.05 |
| local_parse | succeeded | 1.04 |
| fixture_index | succeeded | 3.11 |
| fixture_qa | succeeded | 5.14 |
| translate | succeeded | 3.09 |
| summary | succeeded | 5.17 |
| draft | succeeded | 15.4 |
| direction | succeeded | 19.53 |
| diagram | succeeded | 2.07 |
| chart_export | succeeded | 3.09 |
| scan_preview | partially_succeeded | 1.05 |
| scan_local_index | succeeded | 7.19 |
| scan_evidence_qa | succeeded | 17.44 |
| two_source_comparison | succeeded | 22.62 |
| figure | succeeded | 14.41 |

扫描预览的 partially_succeeded 为无文本层的预期状态，后续深解析使用哈希核验过的既有云端 OCR zip。它不表示本地执行了 OCR。

## 修复及清理

本地调用拥有独立 PostgreSQL 准入、幂等、未知结果保留和真实向量 profile 证明，不依赖云端预算。重排由固定 CPU 改为优先 Apple MPS、小批量推理；CPU 超时记录保留。视觉采用节点定位和固定 ID 连线两个独立请求，坐标网格在解码阶段约束；早期非法坐标/引用输出保留为失败，未用裁剪或补造节点冒充成功。视觉结果仍为 unreviewed，未获得通用图结构语义或科学正确性认证。

三个重复测试启动器合并为 scripts/check_backend.py，库存检查移入默认回归，旧的直连模型冒烟由实际 API 用户流程替代。已清理本项目取消的 MinerU 缓存和重复未完成权重缓存；共享缓存、历史验收记录、冻结迁移与原始 PDF 未删除。结构说明见 [repository-layout.md](../engineering/repository-layout.md)。

## 私有机器证据

var/reports/local-setup.json、local-journey.json、local-generation-real.json、local-embedding-real.json、local-ui.json、runtime-local.json、initialization.json、local-cloud-gates-audit.json。失败尝试存放于 local-attempts/，报告标记临时验收环境；该环境按既定隔离验证流程清理，不冒充留在生产空间的研究成果。

模型配置与一键使用见 [local-setup.md](local-setup.md)，条件式云端分工建议见 [cloud-alternatives.md](cloud-alternatives.md)。付费路线仍需单独适配、资料和预算授权，本轮未调用。当前本地模式按宿主机应用运行验收，容器跨宿主机的本地模型连接未验收。
