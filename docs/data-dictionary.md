# PostgreSQL 数据字典

日期：2026-11-13。状态：用户已确认；未建库或运行迁移。共 41 张业务表。

机器可核对来源：[postgresql-catalog.json](data/postgresql-catalog.json)。运行语义、隔离、状态与事务规则见[关系库设计](data-design.md)。

所有表有 UUID 主键和 created_at；可变表另有 updated_at。外键默认 RESTRICT；append_only 及已发布内容禁止普通更新/删除。JSON、跨行规则与发布状态另由设计正文规定的受控服务/迁移保护。唯一键含主键；可空唯一键沿用 PostgreSQL 默认 NULL 不相等语义。

## users

个人用户与手工创建的评审账号

分组：账号；作用域：`global`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `username` | `text` | 否 | `—` |
| `password_hash` | `text` | 否 | `—` |
| `display_name` | `text` | 否 | `—` |
| `role` | `text` | 否 | `—` |
| `disabled_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`username`。

**CHECK 约束**：
- `role IN ('researcher', 'reviewer')`。

## auth_sessions

仅保存会话令牌摘要及有效期；不保存原始令牌

分组：账号；作用域：`account`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `token_hash` | `varchar(64)` | 否 | `—` |
| `csrf_hash` | `varchar(64)` | 否 | `—` |
| `expires_at` | `timestamptz` | 否 | `—` |
| `revoked_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, id`；`token_hash`。

**外键**：
- `owner_id` → `users(id)`。

**CHECK 约束**：
- `token_hash ~ '^[0-9a-f]{64}$'`。
- `csrf_hash ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, expires_at`。

## projects

个人科研空间；资料默认不跨项目共用

分组：账号；作用域：`account`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `name` | `text` | 否 | `—` |
| `description` | `text` | 否 | `—` |
| `preferences` | `jsonb` | 否 | `—` |
| `deleted_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |
| `active_profile_id` | `uuid` | 是 | `—` |

**唯一键**：`id`；`owner_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `active_profile_id` → `index_profiles(id)`。

**辅助索引**：
- `active_profile_id`。

## file_objects

同账号内按内容去重的不可变文件；存储键不包含用户提交的路径

分组：文件与文献；作用域：`account`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `sha256` | `varchar(64)` | 否 | `—` |
| `byte_size` | `bigint` | 否 | `—` |
| `mime_type` | `text` | 否 | `—` |
| `storage_key` | `text` | 否 | `—` |
| `state` | `text` | 否 | `—` |
| `purge_after` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, id`；`owner_id, sha256`。

**外键**：
- `owner_id` → `users(id)`。

**CHECK 约束**：
- `byte_size >= 0`。
- `state IN ('staged', 'ready', 'quarantined', 'purge_pending', 'purged')`。
- `sha256 ~ '^[0-9a-f]{64}$'`。

## source_records

外部查询返回的不可变来源记录；仅有元数据不等于已验证正文

分组：发现与推荐；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `provider` | `text` | 否 | `—` |
| `external_id` | `text` | 否 | `—` |
| `request_fingerprint` | `varchar(64)` | 否 | `—` |
| `retrieved_at` | `timestamptz` | 否 | `—` |
| `record_json` | `jsonb` | 否 | `—` |
| `content_sha256` | `varchar(64)` | 否 | `—` |
| `access_scope` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, provider, request_fingerprint, external_id, content_sha256`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。

**CHECK 约束**：
- `access_scope IN ('metadata', 'abstract', 'full_text')`。
- `request_fingerprint ~ '^[0-9a-f]{64}$'`。
- `content_sha256 ~ '^[0-9a-f]{64}$'`。

## uploads

上传或下载导入回执；显示名与文件内容身份分离

分组：文件与文献；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `file_id` | `uuid` | 否 | `—` |
| `source_record_id` | `uuid` | 是 | `—` |
| `display_name` | `text` | 否 | `—` |
| `origin` | `text` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `idempotency_key` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, idempotency_key`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, source_record_id` → `source_records(owner_id, project_id, id)`。

**CHECK 约束**：
- `origin IN ('local_upload', 'scholarly_download')`。
- `status IN ('received', 'checking', 'waiting_user', 'accepted', 'rejected', 'failed')`。
- `origin <> 'scholarly_download' OR source_record_id IS NOT NULL`。

**辅助索引**：
- `owner_id, file_id`。
- `owner_id, project_id, source_record_id`。

## papers

项目内学术作品身份；当前版本只是可变指针

分组：文件与文献；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `title` | `text` | 否 | `—` |
| `normalized_title` | `text` | 否 | `—` |
| `current_version_id` | `uuid` | 是 | `—` |
| `deleted_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |
| `display_metadata` | `jsonb` | 否 | `'{}'::jsonb` |

**唯一键**：`id`；`owner_id, project_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, id, current_version_id` → `paper_versions(owner_id, project_id, paper_id, id)`。

**辅助索引**：
- `owner_id, project_id, created_at, id`，部分索引 `deleted_at IS NULL`。
- `owner_id, project_id, id, current_version_id`。

## paper_identifiers

经消歧确认的作品级标识；arXiv 基础号去除版本后缀

分组：文件与文献；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `paper_id` | `uuid` | 否 | `—` |
| `scheme` | `text` | 否 | `—` |
| `normalized_value` | `text` | 否 | `—` |
| `source_record_id` | `uuid` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, scheme, normalized_value`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, paper_id` → `papers(owner_id, project_id, id)`。
- `owner_id, project_id, source_record_id` → `source_records(owner_id, project_id, id)`。

**CHECK 约束**：
- `scheme IN ('doi', 'arxiv', 'pmid', 'openalex')`。

**辅助索引**：
- `owner_id, project_id, paper_id`。
- `owner_id, project_id, source_record_id`。

## paper_versions

一个不可变文档载体版本；同一 arXiv 版号允许扫描/矢量等不同载体

分组：文件与文献；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `paper_id` | `uuid` | 否 | `—` |
| `file_id` | `uuid` | 否 | `—` |
| `version_label` | `text` | 是 | `—` |
| `published_on` | `date` | 是 | `—` |
| `metadata_json` | `jsonb` | 否 | `—` |
| `active_parse_id` | `uuid` | 是 | `—` |
| `active_index_id` | `uuid` | 是 | `—` |
| `deleted_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, paper_id, file_id`；`owner_id, project_id, paper_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, paper_id` → `papers(owner_id, project_id, id)`。
- `owner_id, file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, id, active_parse_id` → `document_parses(owner_id, project_id, version_id, id)`。
- `owner_id, project_id, id, active_parse_id, active_index_id` → `index_generations(owner_id, project_id, version_id, parse_id, id)`。

**CHECK 约束**：
- `(active_parse_id IS NULL) = (active_index_id IS NULL)`。

**辅助索引**：
- `owner_id, file_id`。
- `owner_id, project_id, id, active_parse_id`。
- `owner_id, project_id, id, active_parse_id, active_index_id`。

## duplicate_reviews

去重候选、正文相似证据及用户决定；候选列表经接口校验

分组：文件与文献；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `upload_id` | `uuid` | 否 | `—` |
| `candidate_json` | `jsonb` | 否 | `—` |
| `decision` | `text` | 是 | `—` |
| `selected_version_id` | `uuid` | 是 | `—` |
| `decided_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, upload_id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, upload_id` → `uploads(owner_id, project_id, id)`。
- `owner_id, project_id, selected_version_id` → `paper_versions(owner_id, project_id, id)`。

**CHECK 约束**：
- `decision IN ('keep_existing', 'activate_new', 'keep_both', 'reject')`。

**辅助索引**：
- `owner_id, project_id, selected_version_id`。

## document_parses

深解析或预览的一次独立修订；就绪后内容冻结

分组：文档与引用；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `version_id` | `uuid` | 否 | `—` |
| `parser_name` | `text` | 否 | `—` |
| `parser_version` | `text` | 否 | `—` |
| `config_sha256` | `varchar(64)` | 否 | `—` |
| `revision_no` | `integer` | 否 | `—` |
| `mode` | `text` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `expected_pages` | `integer` | 否 | `—` |
| `completed_pages` | `integer` | 否 | `—` |
| `manifest_sha256` | `varchar(64)` | 是 | `—` |
| `source_text_sha256` | `varchar(64)` | 是 | `—` |
| `published_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, version_id, revision_no`；`owner_id, project_id, version_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, version_id` → `paper_versions(owner_id, project_id, id)`。

**CHECK 约束**：
- `revision_no > 0`。
- `expected_pages > 0`。
- `completed_pages >= 0 AND completed_pages <= expected_pages`。
- `mode IN ('preview', 'deep')`。
- `status IN ('pending', 'processing', 'partial', 'ready', 'failed', 'cancelled')`。
- `config_sha256 ~ '^[0-9a-f]{64}$'`。
- `manifest_sha256 ~ '^[0-9a-f]{64}$'`。
- `source_text_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, project_id, version_id, config_sha256`。

## document_pages

页级几何、提取进度及独立重试单元

分组：文档与引用；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `page_no` | `integer` | 否 | `—` |
| `width` | `double precision` | 否 | `—` |
| `height` | `double precision` | 否 | `—` |
| `rotation` | `integer` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `text_layer_chars` | `integer` | 否 | `—` |
| `page_sha256` | `varchar(64)` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, parse_id, page_no`；`owner_id, project_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id` → `document_parses(owner_id, project_id, id)`。

**CHECK 约束**：
- `page_no > 0`。
- `width > 0 AND height > 0`。
- `rotation IN (0,90,180,270)`。
- `text_layer_chars >= 0`。
- `status IN ('pending', 'processing', 'ready', 'failed')`。
- `page_sha256 ~ '^[0-9a-f]{64}$'`。

## document_blocks

结构块；raw_text 保留原提取文本，canonical_text 的变换可追查

分组：文档与引用；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `page_id` | `uuid` | 否 | `—` |
| `parent_block_id` | `uuid` | 是 | `—` |
| `ordinal` | `integer` | 否 | `—` |
| `kind` | `text` | 否 | `—` |
| `raw_text` | `text` | 否 | `—` |
| `canonical_text` | `text` | 否 | `—` |
| `content_sha256` | `varchar(64)` | 否 | `—` |
| `locator_precision` | `text` | 否 | `—` |
| `bbox` | `jsonb` | 是 | `—` |
| `source_locator` | `jsonb` | 否 | `—` |
| `structured_json` | `jsonb` | 否 | `—` |
| `asset_file_id` | `uuid` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, parse_id, page_id, ordinal`；`owner_id, project_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, asset_file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, parse_id, page_id` → `document_pages(owner_id, project_id, parse_id, id)`。
- `owner_id, project_id, parse_id, parent_block_id` → `document_blocks(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `ordinal >= 0`。
- `(locator_precision = 'page' AND bbox IS NULL) OR (locator_precision = 'bbox' AND bbox IS NOT NULL)`。
- `kind IN ('title', 'author', 'abstract', 'heading', 'paragraph', 'table', 'figure', 'formula', 'reference', 'appendix', 'code', 'other')`。
- `locator_precision IN ('page', 'bbox')`。
- `content_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, asset_file_id`。
- `owner_id, project_id, parse_id, parent_block_id`。

## bibliographic_refs

解析版本中的参考条目、元数据及明确消歧结果

分组：文档与引用；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `entry_block_id` | `uuid` | 是 | `—` |
| `marker` | `text` | 否 | `—` |
| `raw_reference` | `text` | 否 | `—` |
| `metadata_json` | `jsonb` | 否 | `—` |
| `resolved_paper_id` | `uuid` | 是 | `—` |
| `source_record_id` | `uuid` | 是 | `—` |
| `resolution_state` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, parse_id, marker`；`owner_id, project_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id` → `document_parses(owner_id, project_id, id)`。
- `owner_id, project_id, resolved_paper_id` → `papers(owner_id, project_id, id)`。
- `owner_id, project_id, source_record_id` → `source_records(owner_id, project_id, id)`。
- `owner_id, project_id, parse_id, entry_block_id` → `document_blocks(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `resolution_state IN ('unresolved', 'ambiguous', 'verified')`。

**辅助索引**：
- `owner_id, project_id, resolved_paper_id`。
- `owner_id, project_id, source_record_id`。
- `owner_id, project_id, parse_id, entry_block_id`。

## citation_mentions

正文引用出现位置；一条参考文献可以有多个出现位置

分组：文档与引用；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `source_block_id` | `uuid` | 否 | `—` |
| `reference_id` | `uuid` | 否 | `—` |
| `char_start` | `integer` | 否 | `—` |
| `char_end` | `integer` | 否 | `—` |
| `context_text` | `text` | 否 | `—` |
| `verification` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, source_block_id, reference_id, char_start, char_end`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id, source_block_id` → `document_blocks(owner_id, project_id, parse_id, id)`。
- `owner_id, project_id, parse_id, reference_id` → `bibliographic_refs(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `char_start >= 0 AND char_end > char_start`。
- `verification IN ('extracted', 'confirmed', 'rejected')`。

**辅助索引**：
- `owner_id, project_id, parse_id, source_block_id`。
- `owner_id, project_id, parse_id, reference_id`。

## index_profiles

只含非敏感模型/分块/词法配置；配置变化新增 profile

分组：检索与翻译；作用域：`global`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `profile_key` | `text` | 否 | `—` |
| `provider` | `text` | 否 | `—` |
| `model_name` | `text` | 否 | `—` |
| `dimensions` | `integer` | 否 | `—` |
| `metric` | `text` | 否 | `—` |
| `chunker_revision` | `text` | 否 | `—` |
| `lexical_revision` | `text` | 否 | `—` |
| `config_json` | `jsonb` | 否 | `—` |
| `fingerprint` | `varchar(64)` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`profile_key`；`fingerprint`。

**CHECK 约束**：
- `dimensions > 0`。
- `fingerprint ~ '^[0-9a-f]{64}$'`。

## index_generations

某个解析版本在一个 profile 下的独立构建世代

分组：检索与翻译；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `version_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `profile_id` | `uuid` | 否 | `—` |
| `generation_no` | `integer` | 否 | `—` |
| `collection_name` | `text` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `expected_points` | `integer` | 否 | `—` |
| `verified_points` | `integer` | 否 | `—` |
| `manifest_sha256` | `varchar(64)` | 是 | `—` |
| `manifest_file_id` | `uuid` | 是 | `—` |
| `verified_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, parse_id, profile_id, generation_no`；`owner_id, project_id, parse_id, id`；`owner_id, project_id, version_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `profile_id` → `index_profiles(id)`。
- `owner_id, manifest_file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, version_id, parse_id` → `document_parses(owner_id, project_id, version_id, id)`。

**CHECK 约束**：
- `generation_no > 0`。
- `expected_points >= 0 AND verified_points >= 0 AND verified_points <= expected_points`。
- `status IN ('building', 'verifying', 'ready', 'retired', 'delete_pending', 'deleted', 'failed')`。
- `manifest_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `profile_id`。
- `owner_id, manifest_file_id`。

## retrieval_chunks

保存检索块正文及确定性 point ID，使向量库可重建

分组：检索与翻译；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `generation_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `chunk_no` | `integer` | 否 | `—` |
| `point_id` | `uuid` | 否 | `—` |
| `canonical_text` | `text` | 否 | `—` |
| `lexical_text` | `text` | 否 | `—` |
| `content_sha256` | `varchar(64)` | 否 | `—` |
| `token_count` | `integer` | 否 | `—` |
| `section_path` | `text[]` | 否 | `—` |
| `block_kind` | `text` | 否 | `—` |
| `page_start` | `integer` | 否 | `—` |
| `page_end` | `integer` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `representation` | `text` | 否 | `—` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, generation_id, chunk_no`；`point_id`；`owner_id, project_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id, generation_id` → `index_generations(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `chunk_no >= 0`。
- `token_count > 0`。
- `page_start > 0 AND page_end >= page_start`。
- `representation IN ('verbatim','structured','generated_description')`。
- `content_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, project_id, parse_id, generation_id`。

## chunk_spans

检索块与原文块的有序多对多映射；偏移为 Unicode 码点半开区间

分组：检索与翻译；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `chunk_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `block_id` | `uuid` | 否 | `—` |
| `span_no` | `integer` | 否 | `—` |
| `block_start` | `integer` | 否 | `—` |
| `block_end` | `integer` | 否 | `—` |
| `chunk_start` | `integer` | 否 | `—` |
| `chunk_end` | `integer` | 否 | `—` |
| `representation` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, chunk_id, span_no`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id, chunk_id` → `retrieval_chunks(owner_id, project_id, parse_id, id)`。
- `owner_id, project_id, parse_id, block_id` → `document_blocks(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `span_no >= 0`。
- `block_start >= 0 AND block_end > block_start`。
- `chunk_start >= 0 AND chunk_end > chunk_start`。

**辅助索引**：
- `owner_id, project_id, parse_id, chunk_id`。
- `owner_id, project_id, parse_id, block_id`。

## translation_revisions

绑定解析、语言、模型、Prompt 及术语表摘要的一次翻译修订

分组：检索与翻译；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `target_language` | `text` | 否 | `—` |
| `model_name` | `text` | 否 | `—` |
| `prompt_revision` | `text` | 否 | `—` |
| `glossary_sha256` | `varchar(64)` | 否 | `—` |
| `config_sha256` | `varchar(64)` | 否 | `—` |
| `revision_no` | `integer` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, parse_id, target_language, config_sha256, revision_no`；`owner_id, project_id, parse_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id` → `document_parses(owner_id, project_id, id)`。

**CHECK 约束**：
- `revision_no > 0`。
- `status IN ('pending', 'processing', 'partial', 'ready', 'failed', 'cancelled')`。
- `glossary_sha256 ~ '^[0-9a-f]{64}$'`。
- `config_sha256 ~ '^[0-9a-f]{64}$'`。

## translation_segments

按原文块对齐的译文；检查公式/代码占位符后才 ready

分组：检索与翻译；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `translation_id` | `uuid` | 否 | `—` |
| `parse_id` | `uuid` | 否 | `—` |
| `block_id` | `uuid` | 否 | `—` |
| `segment_no` | `integer` | 否 | `—` |
| `translated_text` | `text` | 否 | `—` |
| `placeholders_json` | `jsonb` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, translation_id, block_id, segment_no`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, parse_id, translation_id` → `translation_revisions(owner_id, project_id, parse_id, id)`。
- `owner_id, project_id, parse_id, block_id` → `document_blocks(owner_id, project_id, parse_id, id)`。

**CHECK 约束**：
- `segment_no >= 0`。
- `status IN ('pending', 'processing', 'ready', 'failed')`。

**辅助索引**：
- `owner_id, project_id, parse_id, translation_id`。
- `owner_id, project_id, parse_id, block_id`。

## conversations

对话的 checkpoint 身份、文献范围及串行写入锁版本

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `title` | `text` | 否 | `—` |
| `checkpoint_thread_id` | `text` | 否 | `—` |
| `context_json` | `jsonb` | 否 | `—` |
| `lock_version` | `bigint` | 否 | `—` |
| `archived_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`checkpoint_thread_id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。

**CHECK 约束**：
- `lock_version >= 0`。

## runs

每次任务的权威状态；幂等键必须与请求摘要配对

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `conversation_id` | `uuid` | 是 | `—` |
| `parent_run_id` | `uuid` | 是 | `—` |
| `kind` | `text` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `idempotency_key` | `text` | 否 | `—` |
| `request_sha256` | `varchar(64)` | 否 | `—` |
| `input_json` | `jsonb` | 否 | `—` |
| `config_json` | `jsonb` | 否 | `—` |
| `result_json` | `jsonb` | 是 | `—` |
| `input_scope_json` | `jsonb` | 否 | `—` |
| `next_event_seq` | `bigint` | 否 | `—` |
| `event_floor` | `bigint` | 否 | `—` |
| `lease_token` | `uuid` | 是 | `—` |
| `lease_until` | `timestamptz` | 是 | `—` |
| `heartbeat_at` | `timestamptz` | 是 | `—` |
| `cancel_requested_at` | `timestamptz` | 是 | `—` |
| `error_json` | `jsonb` | 是 | `—` |
| `finished_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, idempotency_key`；`owner_id, project_id, conversation_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, conversation_id` → `conversations(owner_id, project_id, id)`。
- `owner_id, project_id, parent_run_id` → `runs(owner_id, project_id, id)`。

**CHECK 约束**：
- `next_event_seq > 0 AND event_floor > 0 AND event_floor <= next_event_seq`。
- `status IN ('queued', 'running', 'waiting_user', 'succeeded', 'partially_succeeded', 'failed', 'cancelled')`。
- `request_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `status, lease_until`。
- `owner_id, project_id, created_at, id`。
- `owner_id, project_id, parent_run_id`。

## messages

消息与流式草稿；终态消息冻结，修正追加新消息

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `conversation_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 是 | `—` |
| `ordinal` | `bigint` | 否 | `—` |
| `role` | `text` | 否 | `—` |
| `content_text` | `text` | 否 | `—` |
| `verification_state` | `text` | 否 | `—` |
| `final_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, conversation_id, ordinal`；`owner_id, project_id, run_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, conversation_id` → `conversations(owner_id, project_id, id)`。
- `owner_id, project_id, conversation_id, run_id` → `runs(owner_id, project_id, conversation_id, id)`。

**CHECK 约束**：
- `ordinal >= 0`。
- `role IN ('user', 'assistant', 'system_notice')`。
- `verification_state IN ('not_applicable', 'provisional', 'supported', 'partially_supported', 'insufficient')`。

**辅助索引**：
- `owner_id, project_id, conversation_id, run_id`。

## run_steps

单步执行尝试；未知外部结果先核对回执，不直接发起新尝试

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 否 | `—` |
| `step_key` | `text` | 否 | `—` |
| `attempt_no` | `integer` | 否 | `—` |
| `tool_name` | `text` | 否 | `—` |
| `status` | `text` | 否 | `—` |
| `lease_token` | `uuid` | 是 | `—` |
| `lease_until` | `timestamptz` | 是 | `—` |
| `input_sha256` | `varchar(64)` | 否 | `—` |
| `output_json` | `jsonb` | 是 | `—` |
| `error_json` | `jsonb` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, run_id, step_key, attempt_no`；`owner_id, project_id, run_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。

**CHECK 约束**：
- `attempt_no BETWEEN 1 AND 3`。
- `status IN ('pending', 'running', 'unknown', 'succeeded', 'failed', 'cancelled')`。
- `input_sha256 ~ '^[0-9a-f]{64}$'`。

## external_calls

外部请求意图与回执；完整响应可保存为受控文件

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `step_id` | `uuid` | 否 | `—` |
| `provider` | `text` | 否 | `—` |
| `operation` | `text` | 否 | `—` |
| `intent_key` | `text` | 否 | `—` |
| `request_sha256` | `varchar(64)` | 否 | `—` |
| `remote_id` | `text` | 是 | `—` |
| `status` | `text` | 否 | `—` |
| `response_file_id` | `uuid` | 是 | `—` |
| `receipt_json` | `jsonb` | 是 | `—` |
| `started_at` | `timestamptz` | 是 | `—` |
| `finished_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, provider, intent_key`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, step_id` → `run_steps(owner_id, project_id, id)`。
- `owner_id, response_file_id` → `file_objects(owner_id, id)`。

**CHECK 约束**：
- `status IN ('prepared', 'sent', 'unknown', 'succeeded', 'failed', 'cancelled')`。
- `request_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, project_id, step_id`。
- `owner_id, response_file_id`。

## run_events

提交后才发送的公开事件；不保存隐藏思维链或密钥

分组：会话与运行；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 否 | `—` |
| `seq` | `bigint` | 否 | `—` |
| `event_type` | `text` | 否 | `—` |
| `public_payload` | `jsonb` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, run_id, seq`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。

**CHECK 约束**：
- `seq > 0`。

## task_outbox

事务内记录待投递命令；dispatcher 持有限时租约

分组：会话与运行；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 否 | `—` |
| `event_key` | `text` | 否 | `—` |
| `command` | `text` | 否 | `—` |
| `payload_json` | `jsonb` | 否 | `—` |
| `available_at` | `timestamptz` | 否 | `—` |
| `dispatched_at` | `timestamptz` | 是 | `—` |
| `lease_token` | `uuid` | 是 | `—` |
| `lease_until` | `timestamptz` | 是 | `—` |
| `delivery_attempts` | `integer` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, event_key`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。

**CHECK 约束**：
- `delivery_attempts >= 0`。

**辅助索引**：
- `available_at, id`，部分索引 `dispatched_at IS NULL`。
- `owner_id, project_id, run_id`。

## evidence_items

原文或外部来源证据；固定版本、原文范围和检索回执

分组：证据与产物；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 否 | `—` |
| `block_id` | `uuid` | 是 | `—` |
| `source_record_id` | `uuid` | 是 | `—` |
| `evidence_kind` | `text` | 否 | `—` |
| `quote_text` | `text` | 是 | `—` |
| `char_start` | `integer` | 是 | `—` |
| `char_end` | `integer` | 是 | `—` |
| `locator_json` | `jsonb` | 否 | `—` |
| `retrieval_json` | `jsonb` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, run_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。
- `owner_id, project_id, block_id` → `document_blocks(owner_id, project_id, id)`。
- `owner_id, project_id, source_record_id` → `source_records(owner_id, project_id, id)`。

**CHECK 约束**：
- `(evidence_kind = 'block' AND block_id IS NOT NULL AND source_record_id IS NULL AND quote_text IS NOT NULL AND char_start IS NOT NULL AND char_end IS NOT NULL AND char_start >= 0 AND char_end > char_start) OR (evidence_kind = 'external_record' AND source_record_id IS NOT NULL AND block_id IS NULL)`。
- `evidence_kind IN ('block', 'external_record')`。

**辅助索引**：
- `owner_id, project_id, run_id`。
- `owner_id, project_id, block_id`。
- `owner_id, project_id, source_record_id`。

## artifacts

图表/脚本/草稿/图谱等逻辑产物，当前版本指针可改变

分组：证据与产物；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `kind` | `text` | 否 | `—` |
| `name` | `text` | 否 | `—` |
| `current_version_id` | `uuid` | 是 | `—` |
| `deleted_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, id, current_version_id` → `artifact_versions(owner_id, project_id, artifact_id, id)`。

**辅助索引**：
- `owner_id, project_id, id, current_version_id`。

## artifact_versions

产物版本绑定文件和执行来源；元数据必须带类型 schema 版本

分组：证据与产物；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `artifact_id` | `uuid` | 否 | `—` |
| `file_id` | `uuid` | 否 | `—` |
| `producing_run_id` | `uuid` | 否 | `—` |
| `producing_step_id` | `uuid` | 是 | `—` |
| `revision_no` | `integer` | 否 | `—` |
| `metadata_json` | `jsonb` | 否 | `—` |
| `config_sha256` | `varchar(64)` | 否 | `—` |
| `published_at` | `timestamptz` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, artifact_id, revision_no`；`owner_id, project_id, artifact_id, id`；`owner_id, project_id, producing_run_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, artifact_id` → `artifacts(owner_id, project_id, id)`。
- `owner_id, file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, producing_run_id` → `runs(owner_id, project_id, id)`。
- `owner_id, project_id, producing_run_id, producing_step_id` → `run_steps(owner_id, project_id, run_id, id)`。

**CHECK 约束**：
- `revision_no > 0`。
- `config_sha256 ~ '^[0-9a-f]{64}$'`。

**辅助索引**：
- `owner_id, file_id`。
- `owner_id, project_id, producing_run_id`。
- `owner_id, project_id, producing_run_id, producing_step_id`。

## artifact_inputs

产物版本的输入血缘；四类来源必须且只能选择一种

分组：证据与产物；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `output_version_id` | `uuid` | 否 | `—` |
| `source_file_id` | `uuid` | 是 | `—` |
| `source_parse_id` | `uuid` | 是 | `—` |
| `source_artifact_version_id` | `uuid` | 是 | `—` |
| `source_message_id` | `uuid` | 是 | `—` |
| `input_role` | `text` | 否 | `—` |
| `ordinal` | `integer` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, output_version_id, ordinal`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, output_version_id` → `artifact_versions(owner_id, project_id, id)`。
- `owner_id, source_file_id` → `file_objects(owner_id, id)`。
- `owner_id, project_id, source_parse_id` → `document_parses(owner_id, project_id, id)`。
- `owner_id, project_id, source_artifact_version_id` → `artifact_versions(owner_id, project_id, id)`。
- `owner_id, project_id, source_message_id` → `messages(owner_id, project_id, id)`。

**CHECK 约束**：
- `ordinal >= 0`。
- `num_nonnulls(source_file_id,source_parse_id,source_artifact_version_id,source_message_id) = 1`。
- `source_artifact_version_id IS DISTINCT FROM output_version_id`。

**辅助索引**：
- `owner_id, source_file_id`。
- `owner_id, project_id, source_parse_id`。
- `owner_id, project_id, source_artifact_version_id`。
- `owner_id, project_id, source_message_id`。

## claims

消息或产物中的可核查论断；计算过程与原始操作数证据分开保存

分组：证据与产物；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 否 | `—` |
| `message_id` | `uuid` | 是 | `—` |
| `artifact_version_id` | `uuid` | 是 | `—` |
| `ordinal` | `integer` | 否 | `—` |
| `claim_text` | `text` | 否 | `—` |
| `claim_kind` | `text` | 否 | `—` |
| `verification_state` | `text` | 否 | `—` |
| `calculation_json` | `jsonb` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, run_id, id`；`owner_id, project_id, message_id, ordinal`；`owner_id, project_id, artifact_version_id, ordinal`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。
- `owner_id, project_id, message_id` → `messages(owner_id, project_id, id)`。
- `owner_id, project_id, artifact_version_id` → `artifact_versions(owner_id, project_id, id)`。
- `owner_id, project_id, run_id, message_id` → `messages(owner_id, project_id, run_id, id)`。
- `owner_id, project_id, run_id, artifact_version_id` → `artifact_versions(owner_id, project_id, producing_run_id, id)`。

**CHECK 约束**：
- `ordinal >= 0`。
- `num_nonnulls(message_id,artifact_version_id) = 1`。
- `claim_kind IN ('factual', 'derived_numeric', 'hypothesis', 'planned_work')`。
- `verification_state IN ('supported', 'partially_supported', 'insufficient', 'not_applicable')`。

**辅助索引**：
- `owner_id, project_id, run_id`。
- `owner_id, project_id, message_id`。
- `owner_id, project_id, artifact_version_id`。
- `owner_id, project_id, run_id, message_id`。
- `owner_id, project_id, run_id, artifact_version_id`。

## claim_evidence

论断与证据多对多关系；引用反对证据同样允许

分组：证据与产物；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `claim_id` | `uuid` | 否 | `—` |
| `evidence_id` | `uuid` | 否 | `—` |
| `relation` | `text` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `run_id` | `uuid` | 否 | `—` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, claim_id, evidence_id, relation`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, claim_id` → `claims(owner_id, project_id, id)`。
- `owner_id, project_id, evidence_id` → `evidence_items(owner_id, project_id, id)`。
- `owner_id, project_id, run_id, claim_id` → `claims(owner_id, project_id, run_id, id)`。
- `owner_id, project_id, run_id, evidence_id` → `evidence_items(owner_id, project_id, run_id, id)`。

**CHECK 约束**：
- `relation IN ('supports', 'contrasts', 'context', 'operand')`。

**辅助索引**：
- `owner_id, project_id, evidence_id`。
- `owner_id, project_id, run_id, claim_id`。
- `owner_id, project_id, run_id, evidence_id`。

## subscriptions

定时增量追踪配置；每来源游标与时间窗口独立

分组：发现与推荐；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `name` | `text` | 否 | `—` |
| `sources_json` | `jsonb` | 否 | `—` |
| `query_json` | `jsonb` | 否 | `—` |
| `schedule_json` | `jsonb` | 否 | `—` |
| `timezone` | `text` | 否 | `—` |
| `cursor_state` | `jsonb` | 否 | `—` |
| `next_due_at` | `timestamptz` | 否 | `—` |
| `enabled` | `boolean` | 否 | `—` |
| `lease_token` | `uuid` | 是 | `—` |
| `lease_until` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。

**辅助索引**：
- `next_due_at, id`，部分索引 `enabled = true`。

## recommendations

推荐某条已保存来源记录；分数、理由及代码可用性可解释

分组：发现与推荐；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `source_record_id` | `uuid` | 否 | `—` |
| `subscription_id` | `uuid` | 是 | `—` |
| `canonical_work_key` | `text` | 否 | `—` |
| `score_json` | `jsonb` | 否 | `—` |
| `reason_json` | `jsonb` | 否 | `—` |
| `code_state_json` | `jsonb` | 否 | `—` |
| `algorithm_revision` | `text` | 否 | `—` |
| `seen_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, canonical_work_key, algorithm_revision`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, source_record_id` → `source_records(owner_id, project_id, id)`。
- `owner_id, project_id, subscription_id` → `subscriptions(owner_id, project_id, id)`。

**辅助索引**：
- `owner_id, project_id, source_record_id`。
- `owner_id, project_id, subscription_id`。

## recommendation_feedback

当前账号对推荐的显式反馈，修改保留审计记录

分组：发现与推荐；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `recommendation_id` | `uuid` | 否 | `—` |
| `vote` | `smallint` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, recommendation_id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, recommendation_id` → `recommendations(owner_id, project_id, id)`。

**CHECK 约束**：
- `vote IN (-1,0,1)`。

## budget_accounts

账号某周期/类别的预算；行锁保护并发准入

分组：成本与审计；作用域：`account`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `period_start` | `timestamptz` | 否 | `—` |
| `period_end` | `timestamptz` | 否 | `—` |
| `category` | `text` | 否 | `—` |
| `currency` | `text` | 否 | `—` |
| `limit_amount` | `numeric(18,6)` | 否 | `—` |
| `spent_amount` | `numeric(18,6)` | 否 | `—` |
| `reserved_amount` | `numeric(18,6)` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, id`；`owner_id, period_start, period_end, category`。

**外键**：
- `owner_id` → `users(id)`。

**CHECK 约束**：
- `period_end > period_start`。
- `currency = 'CNY'`。
- `limit_amount >= 0 AND spent_amount >= 0 AND reserved_amount >= 0`。

**跨行约束**：
- No overlapping tstzrange(period_start, period_end, [)) for same owner/category；All budget configuration writes acquire an owner/category advisory transaction lock and validate overlap before insert/update; protected write service only. Test concurrent configuration.

## budget_reservations

调用前预留；未知回执保留预留，不代表实际已扣款

分组：成本与审计；作用域：`project`；写入方式：`controlled_mutation`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `account_id` | `uuid` | 否 | `—` |
| `call_id` | `uuid` | 否 | `—` |
| `reserved_amount` | `numeric(18,6)` | 否 | `—` |
| `state` | `text` | 否 | `—` |
| `settled_at` | `timestamptz` | 是 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |
| `updated_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, project_id, call_id`；`owner_id, project_id, account_id, call_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, account_id` → `budget_accounts(owner_id, id)`。
- `owner_id, project_id, call_id` → `external_calls(owner_id, project_id, id)`。

**CHECK 约束**：
- `reserved_amount >= 0`。
- `state IN ('held', 'unknown', 'settled', 'released')`。

**辅助索引**：
- `owner_id, account_id`。

## usage_ledger

费用追加账本；估价和供应商账单核对分别标记，调整不改旧行

分组：成本与审计；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `account_id` | `uuid` | 否 | `—` |
| `call_id` | `uuid` | 否 | `—` |
| `reservation_id` | `uuid` | 否 | `—` |
| `entry_key` | `text` | 否 | `—` |
| `delta_amount` | `numeric(18,6)` | 否 | `—` |
| `basis` | `text` | 否 | `—` |
| `model_name` | `text` | 是 | `—` |
| `price_snapshot` | `jsonb` | 否 | `—` |
| `usage_json` | `jsonb` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`；`owner_id, entry_key`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, account_id` → `budget_accounts(owner_id, id)`。
- `owner_id, project_id, call_id` → `external_calls(owner_id, project_id, id)`。
- `owner_id, project_id, reservation_id` → `budget_reservations(owner_id, project_id, id)`。
- `owner_id, project_id, account_id, call_id, reservation_id` → `budget_reservations(owner_id, project_id, account_id, call_id, id)`。

**CHECK 约束**：
- `basis IN ('usage_estimate', 'billing_reconciliation', 'refund')`。

**辅助索引**：
- `owner_id, account_id`。
- `owner_id, project_id, call_id`。
- `owner_id, project_id, reservation_id`。
- `owner_id, project_id, account_id, call_id, reservation_id`。

## audit_events

不含密钥的关键业务操作与设计版本记录

分组：成本与审计；作用域：`project`；写入方式：`append_only`。

| 字段 | PostgreSQL 类型 | 可空 | 默认值 |
|---|---|---|---|
| `id` | `uuid` | 否 | `gen_random_uuid()` |
| `owner_id` | `uuid` | 否 | `—` |
| `project_id` | `uuid` | 否 | `—` |
| `run_id` | `uuid` | 是 | `—` |
| `action` | `text` | 否 | `—` |
| `subject_json` | `jsonb` | 否 | `—` |
| `safe_payload` | `jsonb` | 否 | `—` |
| `created_at` | `timestamptz` | 否 | `now()` |

**唯一键**：`id`；`owner_id, project_id, id`。

**外键**：
- `owner_id` → `users(id)`。
- `owner_id, project_id` → `projects(owner_id, id)`。
- `owner_id, project_id, run_id` → `runs(owner_id, project_id, id)`。

**辅助索引**：
- `owner_id, project_id, created_at, id`。
- `owner_id, project_id, run_id`。

