# 代码阅读路线：沿一次操作理解 ScholarShip

基线：2026-11-17 工作区；包含尚未提交的功能补齐改动。本文中的路径相对项目根目录，使用 PyCharm 的文件搜索和符号搜索定位。不要从迁移文件逐行读起；先观察输入输出，再追踪核心状态。

## 30 分钟：建立整体模型

| 时间 | 阅读入口 | 阅读后应能回答 |
|---|---|---|
| 0–5 分钟 | [main.tsx](../../frontend/src/main.tsx)、[Research.tsx](../../frontend/src/Research.tsx) | 页面入口在哪，前端发出什么请求？ |
| 5–10 分钟 | [api/research.py](../../backend/src/scholarship/api/research.py) | 身份、请求类型、幂等键怎样进入领域层？ |
| 10–15 分钟 | [domains/research.py](../../backend/src/scholarship/domains/research.py) | 来源和模型在何时固定？ |
| 15–23 分钟 | [offline_workflow.py](../../backend/src/scholarship/domains/offline_workflow.py) 的 qa_graph | 四个节点分别读写哪些状态？ |
| 23–30 分钟 | [indexing.py](../../backend/src/scholarship/domains/indexing.py)、[gateway.py](../../backend/src/scholarship/models/gateway.py) | 检索边界在哪里，业务怎样调用模型？ |

完成后在纸上画出 UI → API → PG/outbox → worker → graph → gateway/Qdrant → evidence → UI。能标清网络和数据库交界，才进入细节。

## 2 小时：跟踪真实主链路

### 单元 1：HTTP 到任务（20 分钟）

从 `frontend/src/researchApi.ts::api` 看公共请求封装、CSRF 与错误翻译，再看 `Research.tsx` 的索引／问答提交。请求的 UUID 应来自服务端文献列表；不能把 paper_id 当成 version_id。

读 `api/research.py::ResearchRequest/submit`：mode 默认 offline_acceptance；版本数 1–8；问题长度上限 1000；云端关闭转 409。随后读 `domains/research.py::enqueue_research`：解析就绪、模型配置、索引 snapshot、parent_run_id 的范围比较。

关键状态：`runs.input_json` 保存输入，`config_json` 保存固定执行配置，`input_scope_json` 保存索引世代，`task_outbox` 保存投递意图。练习：解释“用户切换模型后，队列里昨天的任务使用哪个配置”。答案必须指向 `restore_snapshot`，而非猜测读取当前配置。

测试入口：`test_live_business.py::test_project_preset_is_used_and_task_snapshot_is_pinned`；`test_studio.py::test_followup_preserves_conversation_and_rejects_changed_scope`。

### 单元 2：领取和恢复（20 分钟）

读 `workers/tasks.py::dispatch` 与 `workers/offline.py::execute_offline`。标出锁行、有效租约判断、attempt 计数、心跳、最终 token 比较、waiting_user 与 failed 的分支。

思考：消息发出后投递器宕机，为什么不丢任务但可能重复投递？过期旧 worker 恢复后为什么不能覆盖新结果？仅有数据库写回 token 校验，为什么仍不能保证外部 HTTP 只调用一次？最后一题必须结合模型账本回答。

测试入口：`test_foundation.py::test_expired_run_lease_recovers_via_outbox`、`test_live_business.py::test_business_api_outbox_executes_on_project_worker`。

### 单元 3：图状态与模型执行（25 分钟）

读 `offline_workflow.py::QAState/qa_graph`。retrieve 产生 retrieval 或 structured；rerank 产生 selected；answer 产生 answer；publish 产生 result。结构工具命中后仍沿固定节点执行，但部分节点直接复用确定性答案，不再请求模型。

看 `PostgresSaver` 的 thread_id、`get_state`、`previous.next`、`invoke(None...)` 与同步持久化。checkpoint 恢复节点状态，不是外部事务协调器。

继续读 `models/gateway.py::ModelGateway/SimulatedGateway` 与 `models/production.py::ProductionGateway.invoke`。找出新请求发送前记录、已成功缓存路径、unknown 处理、费用结算、异常停策略。练习：画出“请求已发送→超时→等待核验”的分支，不允许把它画回自动重试。

测试入口：`test_model_pipeline.py::test_pipeline_unknown_does_not_resend_or_publish`、`test_live_business.py::test_production_uncertain_never_resends_or_refunds`。

### 单元 4：索引与证据（35 分钟）

读 `retrieval_profile.py::chunks/lexical`；注意长度按 UTF-8 bytes，中文双字词与希腊字符别名。再读 `indexing.py::build_index`：首条回执探测维度、profile 登记、每批 10 个切片、写入与完整回读核验、活动指针切换。

读 `hybrid` 的过滤字段与 RRF；它每来源分别召回，再轮转合并，不能概括成“全库一次 topK”。复核候选的内容哈希和原文 slice 是避免索引漂移的关键。

最后看 `offline_workflow.py::validate_selected/persist_answer` 和 `api/research.py::locate`，追踪 chunk → block → quote → evidence → PDF。思考：为什么“引用 ID 存在”“引用文字相等”和“论断正确”是三个判断？

测试入口：`test_live_business.py::test_new_parse_keeps_old_active_index_and_pins_qa_to_it`、`test_switch_back_embedding_builds_new_generation_and_old_resume_cannot_rollback`；`test_studio.py::test_quote_menu_preserves_latex_unicode_and_rejects_invalid_ids`。

### 单元 5：前端结果与错误（20 分钟）

读 `SourceReader.tsx` 的目标定位、PDF viewport 与高亮，`Library.tsx` 的解析修订选择，以及 `Research.tsx` 的模式／索引就绪检查与历史审阅标签。

对照 `main.py` 的任务状态与 SSE，`Studio.tsx` 的 4 秒轮询。解释为何本版本不能声称全文逐 token 流式输出。找出源文不可用、索引不匹配、模型未启用、unknown 对应的提示，确认用户不会仅看到通用“失败”。

## 深入模块：每次只读一条行为

| 模块 | 主入口 | 最重要的问题 | 对应测试 |
|---|---|---|---|
| 身份与隔离 | api/auth.py；infrastructure/database.py | Cookie／CSRF／RLS 各防什么？ | test_foundation.py：auth、rls、composite_fk |
| 上传与版本 | api/library.py；workers/documents.py | 相同字节、同作品版本、不同解析怎样分开？ | test_library.py |
| 原生 OCR | domains/mineru_native.py | 来源哈希、页面映射、旋转裁剪怎么校验？ | test_live_business.py：native、rotated_crop |
| 模型协议 | models/catalog.py、protocols.py、adapters.py | 能力目录、wire 转换与输出契约为何分层？ | test_adapter_plugins.py、test_provider_contracts.py |
| 预算 | models/production.py；domains/billing.py；migration SQL | 预留／结算／未知如何防并发超额？ | test_shared_billing.py、test_live_business.py |
| 翻译 | domains/studio/translation.py、translation_parts.py | 字面量保护、分片缺失、部分恢复 | test_studio.py：translation、plaintext、partial |
| 写作 | domains/studio/workflow.py::write；anchors.py；editorial_review.py | 来源窗口缓存、字符跨度与编辑派生 | test_studio.py：long_source_notes、unicode_windows |
| 发现与订阅 | domains/studio/discovery.py；workers/tasks.py | 网络失败、去重、租约、反馈是什么算法？ | test_studio.py：metadata、feedback、subscription |
| 图谱 | domains/studio/graph.py | 候选身份、边方向、PageRank 迭代 | test_studio.py：graph_20_unrelated |
| 图表 | domains/studio/chart_renderer.py、charting.py | 同一配置怎样既渲染又导出脚本？ | test_studio.py：chart、exported_script |
| 生命周期 | dev.py、stop.py、deploy.py | 只停止本项目、模式互斥、文件一致性 | runtime_smoke.py；storage_preflight.py |

上述测试名称缩写可用 `rg 'def test_' backend/tests/对应文件` 找到完整符号。运行测试应优先使用项目 `validate.py` 管理独立数据库／队列／collection，不手工把集成测试指向真实业务库。直接使用 pytest 之前先理解 `conftest.py` 和环境准备。

## 读数据库的顺序

先查 `infrastructure/schema_catalog.json` 与[数据字典](../data-dictionary.md)，画核心关系，再看 `schema.py` 的构造，最后读对应迁移的约束、触发器与窄权限函数。账务／调用账本在额外 schema，不要用“41 张表”推断数据库只有 41 张。

重点找四种约束：跨租户复合外键、唯一幂等键、已发布记录保护、活动指针可引用状态。应用层 if 和数据库约束解决不同竞争窗口，解释各自必要性。

## 六个自测实验（离线）

1. 阅读 existing 索引切换测试：把“旧任务恢复”断言删掉会漏掉哪类倒退？不必真的修改项目，先口述。
2. 在独立测试 fixture 中给向量返回错误维度，预期拒绝且旧指针不变。
3. 给引用返回不存在的 quote_id，预期不发布答案，而不是选最接近的句子。
4. 模拟 provider timeout，确认调用记录为 unknown 且第二次不发送 HTTP。
5. 构造完全没有引用关系的 20 篇文献，图谱节点仍存在，边不能凭相似主题生成。
6. 将练习 CSV 的 latency 改成空值，确认受控 renderer 拒绝；恢复原始输入后再复现图片。

这六题都已有相关测试保护。先预测结果，再阅读断言。能说明预测错误的原因比背下类名更重要。

## 不应照着旧设计背诵的内容

当前没有完备的通用多 Agent 自主规划、任意代码执行沙箱、全面 token 流、跨平台一键初始化或生产级备份恢复验收。Ollama 与本地 MinerU 保留配置，不代表本机全部安装并测通。讲架构时主动区分当前实现和演进方案。

## 本批新增功能怎么读

从 `api/library_tools.py` 看新空间、归档和文本比较，再读 `domains/document_identity.py` 的词组指纹；注意候选不是自动合并。对应真实操作复验为根目录 `verify_completion.py`，配置在顶部，使用独立空间。

翻译先看 `api/studio.py::TextSelection`，跟到 `translation_parts.py::prepare` 对块与字符范围的验证，再读 `api/reading_tools.py::revise`。这里应能解释：选词的 UTF-16 浏览器偏移如何换成字符计数，为什么修订时要传旧文本哈希，为什么不改原模型回执。

参考身份核对读 `api/reference_tools.py` 与 `domains/studio/reference_reviews.py`。解析内容不可变，审阅放在追加式 audit_events；`graph.py` 和参考条目读取叠加同一套审阅逻辑。后续研究人工对照在 `direction_review.py`，校验两份原文的精确 slice 并发布带来源的 JSON。

取消读 `api/task_control.py`、`domains/cancellation.py` 与 worker 的最终写回，理解 waiting_user 不等于 cancelled。`TaskEvents.tsx` 消费已持久事件，不伪造逐 Token 输出。新增回归集中在 `backend/tests/test_completion.py`。


## 当前前端路径

`navigation.ts` 负责主页面、旧模块链接兼容与版本范围的 hash 编码；`main.tsx` 负责会话、空间、受保护页面与统一顶部导航；`Brand.tsx` 和 `public/brand/scholarship-mark.svg` 统一品牌。`Home.tsx` 提供首次指引与六模块工具入口。

`Reader.tsx` 将文献原件、证据问答和翻译绑定到相同版本范围；`Research.tsx` 接收并保留该范围，引用在相邻原文工作面定位。`Results.tsx` 聚合既有只读任务历史，重开时携带来源版本和任务 ID。`api/studio.py` 只增加受同项目/RLS 约束的来源 ID 与内容审阅覆盖层，不暴露完整任务输入。

`execution.ts` 只用于解释界面是否可执行；服务端仍执行完整来源范围、模型、期限和额度准入。前端不可用时默认关闭，不能把配置存在当作执行权限。`readingSelection.ts` 校验显示文字与 canonical text 完全匹配，再把 DOM UTF-16 选择转换为 Unicode 码点偏移。
