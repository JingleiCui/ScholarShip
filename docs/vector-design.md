# Qdrant 与检索数据设计评审稿

日期：2026-11-13。状态：**用户已确认**，见[评审结论](data-design-review.md)。当前没有创建 collection、计算向量或调用模型。配套结构：[关系库设计](data-design.md)、[数据字典](data-dictionary.md)、[索引配置样例](data/qdrant-profile.json)。

## 1. 初始 profile 与集合边界

| 配置 | 初始选择 | 原因/限制 |
|---|---|---|
| 稠密模型 | 百炼 `text-embedding-v4`，显式 1,024 维 | 官方接口支持中英文；账户地域、模型和 SDK 在初始化时实测 |
| 距离 | Cosine | 入库和查询使用相同模型/维度；校验有限值、维数及非零向量 |
| 词法检索 | Qdrant 原生全文 BM25，`lexical_text` scoring 索引 | 不为 BM25 再调用模型；需锁定支持该能力的 server/client 版本 |
| 融合 | 等权 RRF，k=60 | 不直接相加语义/BM25 原始分数；有独立评测集后再调参 |
| 重排 | 百炼 `qwen3-rerank` 默认候选 | 单独记录调用与费用；服务故障不得冒充已重排 |
| 集合 | 每项目、每 profile 一个 collection | 首版个人空间数量小，避免跨用户共用全文词频统计；迁移期并存新旧 profile |
| 命名 | `ss_p_<project_uuid_without_hyphens>_<profile_fingerprint_prefix>` | 由服务端生成，用户和模型不直接传 collection 名；映射保存 PostgreSQL |
| 运行 | 单节点、单 shard、单副本，持久卷 | 不宣称高可用；备份和实际恢复测试必须覆盖 |
| 存储 | 稠密向量落盘、payload 落盘，默认不量化 | 小规模先保持精度；内存/索引参数以实测调优 |

profile 指纹覆盖 provider、模型标识、维度、距离、分块、词法、输入前缀和文本规范化。模型供应商若后台更换同名模型而不提供修订号，记录返回模型信息、配置与实测日期，并保留该限制；不能声称跨时期位级可复现。

原生 BM25 是本次检索实现决策，仍属于已确认的 Qdrant 技术范围。环境阶段对真实 server/client 验证 scoring 全文索引与混合查询；若缺少能力，先返回技术评审调整，不静默退化成单向量检索。

## 2. 分块与原文映射

- 初值目标 600 token、硬上限 900 token、相邻上下文重叠 80 token；使用固定版本的 tokenizer，并记录计数。供应商请求仍以实际 Token 上限验证。
- 优先按章节/段落合并，不跨论文版本；保留标题路径。表格保留表头、图保留图注、公式作为独立语义单元。超长表按行组拆分并关联同一原表，超长段按安全文本边界拆分。
- 公式或不可安全拆分单元超过限制时保留原文块和资源，生成明确带来源的检索描述，标明不是逐字原文；不能无声截断并称作完整公式。
- 代码、表格、公式的检索表示和原始文字分别保存；引用只回到真实 `chunk_spans`/结构块，不能引用模型生成的描述作为原论文事实。
- 每个 chunk 保存正文、词法文本、摘要、Token 数、页区间、类型、章节路径；`chunk_spans` 有序记录原文块与 chunk 的字符半开区间。添加的章节上下文前缀不冒充原文引用。
- chunk 编号在一个 generation 内固定；point ID 为 `UUIDv5(固定命名空间, project_id + generation_id + chunk_no)`。重复 upsert 同 generation 得到相同 point，不重复增加有效内容。
- 普通正文、表格、公式、图注和参考条目可索引；页眉页脚等噪声保留在解析结果但默认不进检索。引用信息问题优先查结构化参考条目，再补检索。

词法文本按固定规则处理 Unicode、大小写和空白，同时保留数值、GPU/模型代号及关键符号的检索别名，例如 β₁/β_1/beta1。中文使用稳定的字符 bigram 预分词补充，英文使用 word tokenizer；文档和查询遵循同一词法版本。原文 canonical_text 不因词法处理而变化，不能用 lexical_text 生成引文。

## 3. point payload 与索引

每个 point 的 payload 至少包含：

| 字段 | 含义/索引 |
|---|---|
| `owner_id`、`project_id` | 账号/项目；keyword 索引，服务端强制过滤 |
| `paper_id`、`paper_version_id` | 作品与不可变载体版本；keyword 索引 |
| `parse_revision_id`、`index_generation_id` | 解析与构建世代；keyword 索引 |
| `profile_id`、`chunk_id`、`content_sha256` | PostgreSQL 对应身份/校验；keyword 索引 |
| `block_kind` | 正文/表格/公式/参考等；keyword 索引 |
| `page_start`、`page_end` | 页区间；integer 索引 |
| `section_path` | 标题路径，用于上下文呈现；默认不额外建索引 |
| `lexical_text` | 词法检索文本；全文 scoring 索引，word tokenizer、lowercase、最短词长 1，禁用会破坏专名的自动 stemming |

原文正文和定位以 PostgreSQL 为准；Qdrant 不保存密钥、用户会话、私有服务器绝对路径和公共文件 URL。返回 chunk ID 后回 PostgreSQL 取原文与位置，并再次校验作用域、当次固定世代和当前删除/权限状态。已经固定范围的运行可继续使用尚未回收的旧世代，不因“当前版本”切换而改引用。Qdrant payload 中的状态不能授权访问。

## 4. 查询流程与失败处理

1. 验证当前账号/项目，解析选定论文或默认当前版本；从 PostgreSQL 捕获 profile、文献/parse/generation 的一致范围快照，保存到 run。
2. 同一查询的稠密与 BM25 预取都包含 owner、project、profile 和明确版本/世代过滤。默认每路取 40，RRF 合并 40，重排候选最多 20，最终证据最多 8；均为待评测初值。
3. 中文问题可做有界英文检索改写，并保留原问题、改写与来源；改写不能更换论文范围或数值条件。按论文分组补齐跨文比较的证据，不让一篇论文占满所有候选。
4. PostgreSQL 二次授权并验证 generation/chunk/摘要；删除或已失效候选被过滤，再做有限补召回。记录各路候选、分数、RRF、重排结果和排除原因。
5. 引文从原文块与 span 抽取，答案生成与证据检查完成后输出。业务服务决定能否作答，不用固定语义分数阈值假装置信概率。

过滤必须在召回前完成，召回后仍二次校验。显式历史版本检索可使用保留或重建的历史 generation，但页面和答案清楚标注历史版本；默认只查当次范围快照中的就绪版本。

稠密或词法一侧失败时，首版返回明确的检索依赖错误和可重试状态，不悄悄改变“混合检索”语义。重排故障允许用户明确选择“使用未重排结果继续”，否则暂停；结果记录实际使用的检索模式。模型故障、没有相关证据和库里根本没有该论文分别处理。

BM25 统计来自 collection 中全部点；同项目暂存/历史世代在清理前可能影响 IDF。该影响不跨项目，但意味着相同问题的排序可能随索引维护变化。run 保存实际召回响应/证据及 profile/generation 清单；需要严格排序复现实验时使用冻结的 collection 快照，不声称一般在线检索位级可重放。

## 5. 构建、激活、删除与模型升级

```mermaid
flowchart LR
    A[PG 创建 building generation] --> B[保存 chunks / spans / point manifest]
    B --> C[批量嵌入并幂等 upsert]
    C --> D[核验 point ID / 摘要 / 数量 / 范围]
    D --> E[PG 标记 ready 并原子切活动指针]
    E --> F[旧世代等待在途运行后清理]
```

构建用 outbox 驱动，批次回执持久化；任务重启按 manifest 对账。不能仅凭 point 数量相等判定成功，应按确定 ID 清单及关键 payload/摘要完整核对，先核对供应商回执再决定是否重新计费嵌入。嵌入缓存（启用时）按 owner、模型/profile、维度和文本摘要分隔，不跨账号复用私有内容。

完成前不设置活动状态；旧索引继续服务。Qdrant 写入成功而 PG 提交失败时，以 PG 状态恢复，未激活点不应被默认查询命中。PG 指针提交后通知失败可重放；不会再次创建论文版本。

软删除先在 PG 生效，后续检索与文件读取立即拒绝；Qdrant 清理只筛选精确 owner/project/generation，不使用无作用域的 delete all。恢复旧版本应复用或重建该版本的索引，不能把它的来源换成新文件。

模型/profile 升级创建新 collection，按 PostgreSQL 原文重建。用同一人工问题集比较召回与引文，旧 profile 仍可回退。所有文献准备完成后才切项目与版本指针。迁移中失败不删除旧索引；回收旧 profile 前检查运行范围引用和备份。

## 6. 容量、备份与验证

实际输入是 39 份 PDF、1,986 页，分块数尚未测量。仅以 float32 原始稠密向量估算：

| 假设 chunk 数 | 1,024 维原始向量体积 |
|---|---|
| 20,000 | 约 78.1 MiB |
| 100,000 | 约 390.6 MiB |

上述不包含全文/BM25 索引、HNSW、payload、进程、页缓存或新旧世代并存，不能据此承诺整机内存。暂不部署量化/分布式服务，测试真实索引后的磁盘、RSS、查询延迟和构建峰值，再选学生服务器规格。

可保存 collection 快照及 profile/版本清单；权威恢复仍依赖 PostgreSQL、原件、原文块和 manifest。无嵌入缓存的重建可能再次产生 API 费用，恢复流程先预估额度。备份时点和跨库协调见[关系库设计](data-design.md)。

验证至少覆盖：向量维度/模型错误拒绝、相似内容跨账号不召回、旧版本/未激活点过滤、原文映射正确、重复 upsert、部分批次失败、活动指针提交前后宕机、删除任务精确范围、RRF 与重排实用效果、教材首中尾查询、模型升级回退和真实恢复。离线静态配置检查不算这些测试通过。

## 7. 官方依据

- [百炼文本向量 API：维度、批量和输入上限](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api)
- [Qdrant 原生全文 BM25](https://qdrant.tech/documentation/search/text-search/full-text-search/)
- [Qdrant 混合查询与 RRF](https://qdrant.tech/documentation/search/hybrid-queries/)
- [Qdrant payload 索引](https://qdrant.tech/documentation/manage-data/indexing/)
- [Qdrant 内存与容量规划](https://qdrant.tech/documentation/operations/capacity-planning/)
