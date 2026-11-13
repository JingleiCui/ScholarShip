# M2 离线业务链路验收报告

2026-11-13。用户确认[本批范围](next-review.md)后，已完成合成深解析发布、真实本机 Qdrant 混合检索、LangGraph/PG 持久化问答及引用定位。**本批是离线业务验收，没有真实 MinerU 或模型请求，不代表官方科研问答效果通过。**

## 实现与使用

在[本机工作台](http://127.0.0.1:15173/)以研究账号登录，点击“准备合成文档与索引”，选择 Alpha/Beta 合成文献，运行离线问答。点击证据可查看原文高亮、页码及版面坐标；刷新页面会加载最近持久化任务。两份演示文献是本地 JSON 合成源，不生成伪 PDF，不混入 M1 文献库。评审账号只能查看本人空间，不能发起本批预算执行。

真实调用开关仍关闭，问答/向量/重排经过 `httpx.MockTransport`；代码不读取真实供应商密钥。演示没有真实语义模型，按固定词法重叠选取证据。每条结果都带 `synthetic`、校验范围和局限说明。

| 环节 | 本批行为 |
|---|---|
| 深解析 | 只接受显式 `scholarship.synthetic-layout/v1` 映射；未知云端格式拒绝。校验全页覆盖、块类型、文本、有限坐标及页面边界；原包以 SHA-256 保存，块记录原包文件 ID/页索引，事务发布 ready 修订。M1 预览不覆盖 |
| 分块/profile | `offline-hash-1024-v1`，固定 UTF-8 字节计数，600 目标/900 上限/80 重叠，Unicode 字符边界，原文不归一化；NFKC/英文词/中文 bigram 仅用于词法。长公式/表格/代码保留原块而不伪造逐字检索表示 |
| 向量 | 确定性 SHA-256 词袋合成向量，1,024 维 Cosine，query/document 同规则、无前缀；profile 指纹及集合与未来百炼真实向量隔离。字节计数不是供应商 tokenizer，真实模型的非对称输入配置与计数待联调定稿 |
| 构建 | PG 创建世代与 chunk/span/确定性 UUIDv5 point manifest；批量适配调用经过预算与幂等回执；重复 upsert 不增加 point。校验 PG 清单摘要、精确 point ID、payload、向量及点数后，事务切换 parse/index 配对指针 |
| 混合检索 | 每个固定世代在稠密与 BM25 召回前均限制 owner/project/profile/version/parse/generation；各取 40，按一基名次等权 RRF k=60；按文献轮转取最多 20 个重排候选、8 个证据。原生 BM25 在真实 Qdrant 运行 |
| 证据回查 | PG 二次核对世代状态、归属、点/内容哈希和原文 span；答案前和发布前再次核对删除状态。Qdrant payload 不授权访问，原文由 PG 提供 |
| 问答 | LangGraph retrieve → rerank → answer → publish；PG 检查点按 owner/run 分隔。结果保存 messages、claims、evidence_items、claim_evidence 与检索 trace；引文从 chunk 区间换算到原块 Unicode 半开区间 |
| 恢复 | PG outbox/Celery 投递，工作租约与心跳，最多 3 次尝试；已结算调用重放回执、未知调用暂停。旧索引保留；构建中断不切默认指针，晚到的旧世代不回退较新指针 |

数据库头为 `0005_offline_profile`；只新增合成 profile 与扩展 outbox 命令，没有新增业务表，原 41 张业务表和 2 张平台账务表保持。

## 验收证据

[机器摘要](../evidence/m2-business.json)：完整回归 **76 项通过，29.18 秒**，其中新增 15 项离线业务测试；既有 61 项全部保留。最后通过 `verify_m2_business.py` 独立入口复验新增 15 项（16.37 秒），包括固定 ZIP 元数据后的重放路径。frozen 安装、Ruff、TypeScript/生产构建、依赖健康及敏感值扫描通过。

新增验证覆盖：不可变解析及原包血缘、坏 schema/页码/坐标的原子拒绝、Unicode 分块、Qdrant 写入后中断与重放、旧索引保留、payload 篡改拒绝激活、跨账号作用域、删除后检查点恢复拒绝、双篇证据与无匹配拒答、任务幂等、异步 HTTP/worker/证据接口、历史任务读取、文件预检失败阻断。

问答检查点在一个 Python 进程写入后，由另一个 Python 进程恢复；验证消息与证据不会重复发布。本机服务恢复探针同时通过会话保留、排队任务跨重启和 3 条事件检查。浏览器实际完成合成双篇问答并点开原文高亮，[本机截图](../../var/reports/m2-business-evidence.png)供核对。

真实本地 `1607.06450v1.pdf`（14 页）作为只读测试样本：提取少量本地文本包装为明确标记的离线映射，检查引文所在 PDF 文件页及下载字节哈希；原文件前后哈希一致。该测试使用整页区域，**没有测量 MinerU 的解析、OCR、表格、公式或精细 bbox 准确率**。

## 文件存储与部署边界

API/worker/Beat Compose 文件目录改为与本机相同的 `var` 绑定路径，详见[存储方案](storage.md)。`deploy.py` 在 build/up 前调用 `storage_preflight.py`，按 PG 所有 ready 文件检查大小与 SHA-256，缺失或不符立即停止。当前业务目录 9 个文件全部通过；缺失目录的拒绝分支有集成测试。Compose 配置语法验证通过。

旧 `scholarship_file_data` 卷不删除，尚未在本批重建应用镜像或切换容器应用。Linux 权限、容器实际读写与公网部署未验收，不能从配置预检推断部署成功。

## 未完成及下一步

- 真实 MinerU schema 映射、OCR/表格/公式/坐标质量；真实向量、重排和文本模型效果。
- 官方单篇/多篇题目、数值和语义正确性、跨篇证据覆盖。逐字引文只证明引用位置，主张状态使用部分支撑，关联为 context，避免宣称语义核验通过。
- 真实 token/profile、实际价格/权益/回执、未知费用对账，以及公网/容器验收。项目仍无真实 HTTP 执行工厂。
- 翻译、文献发现推荐、图谱、研究方向核验和受控绘图等其余比赛能力仍需开发。

PyCharm 直接运行 `verify_m2_business.py` 可复验新增链路；完整回归运行 `validate.py`。测试自行建立唯一临时 PG 数据库、项目隔离 Qdrant collection 和独立队列，结束清理自己的资源；不向供应商发送资料。

下一阶段建议先完成[真实联调准入准备评审](live-readiness-review.md)，本报告不授权真实调用。

## 实现依据

原生全文 scoring 及稠密/BM25 查询依据 [Qdrant 官方全文文档](https://qdrant.tech/documentation/search/text-search/full-text-search/)和[混合检索文档](https://qdrant.tech/documentation/hybrid-queries/)；持久化采用 [LangGraph 官方 PostgreSQL saver](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-postgres/README.md)。业务状态、租户回查、幂等回执与文件校验仍由本项目实现并测试。
