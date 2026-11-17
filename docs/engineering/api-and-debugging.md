# API、状态与排错地图

所有路径均以 `/api/v1` 为前缀。实际 schema 查看本地 `/docs` 或 `/openapi.json`；以下描述基线 `fd6a049`。UUID 示例中的大写名称是待替换的变量，不是可提交的真实值。

## 1. 认证和请求约定

浏览器登录 POST `/auth/login`，body 为 username/password；服务端校验 Origin 并设置 `ss_session`（HttpOnly）与 `ss_csrf` Cookie。写请求还需要 `X-CSRF-Token`，值与当前会话匹配。不是 Bearer JWT 认证。不要将 Cookie、密码或整份 `.env` 放进示例与 issue。

通过页面登录后，推荐在浏览器开发者工具 Network 查看请求结构，复制时删去凭据。Swagger 页面位于 API 端口，与 Web 的 Origin 可能不同；不能因为在 Swagger 点 Execute 就假定 Cookie／Origin／CSRF 已配置。需要 API 脚本时使用项目已有客户端模式，保持允许 Origin 并私下管理会话。

## 2. 核心接口目录

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/health/live`、`/health/ready` | 进程存活／依赖就绪 |
| GET | `/auth/me`、`/projects` | 当前身份／可见空间 |
| POST | `/projects/{project_id}/uploads` | multipart PDF 与可选 paper_id；返回 202 |
| GET | `/projects/{project_id}/papers` | 作品及版本列表 |
| GET | `/paper-versions/{version_id}/file` | 受授权的原 PDF |
| GET / POST | `/paper-versions/{version_id}/parses` | 解析历史／创建新预览修订 |
| GET | `/parses/{parse_id}/pages/{page_no}` | 固定解析页与原文块 |
| POST | `/parses/{parse_id}/resume` | 对支持的失败预览修订继续处理 |
| POST | `/duplicate-reviews/{review_id}/decision` | keep_both 或 reject |
| GET | `/research/projects/{project_id}/documents` | 问答可用来源、解析与索引就绪情况 |
| POST | `/research/projects/{project_id}/indexes` | 提交固定来源的建索引任务 |
| POST | `/research/projects/{project_id}/questions` | 提交问题／同来源追问 |
| GET | `/research/projects/{project_id}/runs` | 问答与索引历史 |
| GET | `/research/evidence/{evidence_id}` | 证据及 PDF 位置 |
| POST | `/studio/projects/{project}/tasks` | 翻译、摘要、草稿、本地解析、拓扑等 |
| POST | `/studio/projects/{project}/charts` | multipart CSV + config JSON |
| GET | `/studio/projects/{project}/runs` | 操作筛选后的任务列表 |
| GET | `/studio/artifacts/{artifact_version_id}` | 权限检查后的产物 ZIP |
| POST | `/studio/projects/{project}/search` | arXiv／Crossref 元数据查询 |
| POST | `/studio/projects/{project}/graph` | 当前选中文献局域图谱 |
| GET / PUT | `/projects/{project}/model-configuration` | 读取／切换后续任务配置 |

没有列出的接口以 OpenAPI 为准；不是上述每个操作都需要模型。`local_parse/chart/diagram/review_artifact` 可本地执行，`direction_check` 虽为 local operation 仍访问公开元数据服务。

## 3. 请求结构示例

研究问答使用 `Idempotency-Key`，同时提供正常会话与 CSRF：

```json
{
  "version_ids": ["替换为当前项目的版本 UUID"],
  "mode": "offline_acceptance",
  "question": "这篇论文如何设置优化器？",
  "parent_run_id": null
}
```

这是模拟流程请求，不会给出可用于科研的真实模型结果。真实 cloud 模式必须先具备有效策略和真实匹配索引。建索引用同一 schema 的 version_ids/mode，省略问题即可。

Studio 文本结构解析 body：

```json
{"operation":"local_parse","version_id":"替换为版本 UUID","mode":"offline_acceptance"}
```

后端会把该 operation 固定为 local 执行；前端 mode 不可覆盖这一规则。翻译 body 使用 `parse_id`，摘要／写作使用 `parse_ids`，不能互换：

```json
{"operation":"translate","mode":"offline_acceptance","parse_id":"替换为解析 UUID","language":"en"}
```

language 支持 zh-CN/en；页面当前固定默认中文。上传图表使用 FormData 的 file/config，config 例如：

```json
{"kind":"line","x":"name","y":["score"],"z":null,"units":{"score":"points"},"missing":"reject","title":"教学数据"}
```

202 表示任务已接收，返回对象中的 `id` 是后续定位依据；不等于计算完成。Studio 最多返回最近 30 条任务，需注意较旧产物的可发现性限制。正常响应字段以 `public_run` 与各业务 schema 为准，不保证所有接口返回同一个 envelope。

## 4. 故障定位顺序

1. 浏览器 Network：URL、方法、HTTP 状态、X-Request-ID、脱敏错误码。
2. API：身份、Origin/CSRF、参数验证、项目范围和模型准入是否通过。
3. PostgreSQL：run 是否存在、状态、固定 scope/config、outbox 是否已登记。
4. 后台：worker/Beat 是否运行、有效租约、attempt、依赖故障。
5. 模型账本：拒绝、已发送、成功、unknown；不要仅根据最终 task failed 推断远端未执行。
6. 检索：原文有无答案、切片是否包含、召回 trace、重排选择、引用支持情况。

使用 runtime 身份做普通排错，不为了看表方便改成管理员。不要输出完整连接串。状态和历史数据查看不应顺便执行有副作用的恢复工具。

| 状态／错误 | 解释 | 应对 |
|---|---|---|
| 401 | 缺少／过期会话 | 页面重新登录 |
| 403 | Origin、CSRF 或角色限制 | 核对请求来源与身份，不能只改前端隐藏按钮 |
| 404 | 资源不存在或当前租户不可见 | 核对空间／UUID；不泄露其他用户是否有该资源 |
| 409 | 幂等冲突、范围／索引不匹配、云端未启用等 | 阅读具体业务码，先恢复前提 |
| 413 / 422 | 上传过大或参数／格式不合法 | 修正输入，而非重试原请求 |
| queued / running | 已登记／已领取执行 | 看 worker、租约和依赖，不立即重复提交 |
| waiting_user | 外部结果或准入需要核验 | 保留调用记录，明确决定后才恢复 |
| failed | 已知契约或执行失败 | 区分原文、模型回执、预算与基础设施 |
| succeeded | 软件任务完成 | 仍要独立做事实与语义检查 |

## 5. 验证工具与范围

`doctor.py` 是依赖诊断；`validate.py` 管理唯一临时数据库、独立队列／collection，并运行锁定依赖、静态检查、构建和集成测试；`runtime_smoke.py` 涉及本项目启动器停止／重启，运行前保存工作。`storage_preflight.py` 核对已登记文件哈希，不读取管理员凭据。

历史云端验收入口默认只读 status，不能把“重新运行历史验收脚本”当成一般排错操作。性能排查先测分阶段延迟，再讨论缓存、批量、连接池和并行；在没有基准数据之前不写吞吐或提升百分比。

## 2026-11-17 新增接口

| 方法与路径（省略 /api/v1） | 用途 | 关键约束 |
|---|---|---|
| POST /projects | 新建研究空间 | 研究账号；同名活动空间拒绝；默认 mock |
| PATCH /papers/{id} | 标题、首选版本、归档恢复 | 首选版本必须属于作品；归档不删除原始文件 |
| GET /paper-versions/{a}/compare/{b} | 文本行差异 | 同项目、可用解析；有字符和差异数量上限 |
| GET /projects/{id}/content-candidates | 正文/OCR 身份候选 | 最近 100 个版本、前 3 页文字；标识与相似度分别展示 |
| POST /runs/{id}/cancel | 请求停止后续执行 | 排队即取消；运行中安全边界；unknown 保留 |
| POST /studio/translations/{id}/revisions | 人工译文修订 | ready 原译文、预期文本哈希、字面量保护；新 revision |
| GET /studio/translations/{id}/export | 双语 Markdown | 保留来源与模拟/人工标记 |
| PUT /studio/references/{id}/resolution | 参考身份核对 | 同空间作品、核对原因；追加审阅记录 |
| POST /studio/projects/{id}/tasks | direction_review / translate 等 | 全文对照必须来自不同文件版本且精确匹配原文；选词翻译验证字符跨度 |

研究索引新任务冻结 embedding_batch_size=32，旧配置缺字段时仍按 10 执行。前端多选建索引会逐篇创建任务；单篇超过任务上限时在创建前拒绝。不要通过反复点击或改历史快照绕过额度。错误码 `index_scope_exceeds_run_limit_split_documents` 和 `model_request_quota_exhausted` 分别表示提交前范围过大与执行次数已用完。
