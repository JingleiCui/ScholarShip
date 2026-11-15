import { lazy, Suspense, useEffect, useState } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import type { GraphData } from './CitationGraph';
import type { Segment } from './BilingualReader';
import type { Target } from './SourceReader';
const CitationGraph = lazy(() => import('./CitationGraph'));
const BilingualReader = lazy(() => import('./BilingualReader'));
const DiagramPreview = lazy(() => import('./DiagramPreview'));
const SourceReader = lazy(() => import('./SourceReader'));
type Doc = { id: string; title: string; native_parse_id: string | null };
type Paper = { id: string; version_id: string; title: string };
type Run = {
  editorial_review?: { reason: string; reviewed_artifact_version_id: string } | null;
  mode: string;
  id: string;
  operation: string;
  status: string;
  error_json?: { code: string; contract_code?: string };
  result_json?: {
    translation_id?: string;
    artifact_version_id?: string;
    caption?: string;
    mermaid?: string;
    model_simulated?: boolean;
    verification?: string;
    local_postprocessing_only?: boolean;
    source_artifact_version_id?: string;
    review_notice?: string;
    candidates?: { title: string; url: string; assessment: string }[];
    checked_at?: string;
    summary_spans?: {
      anchors: {
        block_spans?: {
          version_id: string;
          parse_id: string;
          page: number;
          pdf_bbox?: number[];
          quote: string;
          start: number;
          end: number;
        }[];
      }[];
    }[];
    sections?: { title: string; text: string }[];
    limitations_notice?: string;
  };
};
type RecordItem = {
  source_record_id: string;
  recommendation_id: string;
  external_id: string;
  title: string;
  abstract: string;
  url: string;
  provider: string;
  pdf_url: string | null;
};
type Recommendation = {
  id: string;
  record_json: RecordItem;
  vote?: number;
  reason_json: { query: string; feedback_samples?: number };
  score_json?: { feedback_influence?: number };
  code_state_json: {
    status: string;
    repositories?: { url: string; license_spdx?: string; archived: boolean }[];
  };
};
export default function Studio({
  project,
  role,
  module,
}: {
  project: string;
  role?: string;
  module: number;
}) {
  const [docs, setDocs] = useState<Doc[]>([]),
    [papers, setPapers] = useState<Paper[]>([]),
    [runs, setRuns] = useState<Run[]>([]);
  const [selected, setSelected] = useState<string[]>([]),
    [graphPapers, setGraphPapers] = useState<string[]>([]),
    [mode, setMode] = useState('offline_acceptance');
  const [error, setError] = useState(''),
    [note, setNote] = useState(''),
    [busy, setBusy] = useState(false),
    [idea, setIdea] = useState('');
  const [query, setQuery] = useState(''),
    [provider, setProvider] = useState('arxiv'),
    [records, setRecords] = useState<RecordItem[]>([]),
    [recs, setRecs] = useState<Recommendation[]>([]);
  const [subs, setSubs] = useState<
    { id: string; name: string; enabled: boolean; cursor_state: { status?: string } }[]
  >([]);
  const [graph, setGraph] = useState<GraphData | null>(null),
    [segments, setSegments] = useState<Segment[]>([]),
    [target, setTarget] = useState<Target | null>(null);
  const [chartKind, setChartKind] = useState('line'),
    [x, setX] = useState('name'),
    [ys, setYs] = useState('value'),
    [z, setZ] = useState(''),
    [units, setUnits] = useState('{}'),
    [csv, setCsv] = useState<File | null>(null);
  const [nodes, setNodes] = useState('输入\n解析\n检索\n生成\n证据核验'),
    [edges, setEdges] = useState('0,1\n1,2\n2,3\n3,4');
  useEffect(() => {
    let stop = false;
    if (!project) return;
    async function poll() {
      try {
        const needsSources = [1, 3, 4].includes(module);
        const operationFilters: Record<number, string> = {
          1: 'local_parse,translate,summary,review_artifact',
          3: 'direction,direction_check',
          4: 'draft,summary,review_artifact',
          5: 'chart,diagram',
        };
        const [d, p, r, re, s] = await Promise.all([
          needsSources ? api(`/research/projects/${project}/documents`) : Promise.resolve([]),
          module === 3 ? api(`/projects/${project}/papers`) : Promise.resolve([]),
          operationFilters[module]
            ? api(`/studio/projects/${project}/runs?operations=${operationFilters[module]}`)
            : Promise.resolve([]),
          module === 2 ? api(`/studio/projects/${project}/recommendations`) : Promise.resolve([]),
          module === 2 ? api(`/studio/projects/${project}/subscriptions`) : Promise.resolve([]),
        ]);
        if (!stop) {
          setDocs(d);
          setPapers(p);
          setRuns(r);
          setRecs(re);
          setSubs(s);
        }
      } catch (e) {
        if (!stop) setError(errorMessage(e));
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), 4000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [project, module]);
  async function action(fn: () => Promise<void>) {
    setError('');
    setNote('');
    setBusy(true);
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function task(operation: string, extra: object = {}) {
    await action(async () => {
      await api(`/studio/projects/${project}/tasks`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ operation, mode, parse_ids: selected, idea, ...extra }),
      });
      setNote('任务已保存，可在下方查看进度和下载产物。');
    });
  }
  const [sourcePage, setSourcePage] = useState(1),
    [pageBlocks, setPageBlocks] = useState<{ id: string; canonical_text: string }[]>([]),
    [blockIds, setBlockIds] = useState<string[]>([]);
  useEffect(() => {
    let stop = false;
    setPageBlocks([]);
    setBlockIds([]);
    if (selected.length === 1 && module === 1)
      void api(`/parses/${selected[0]}/pages/${sourcePage}`)
        .then((r) => {
          if (!stop) setPageBlocks(r.blocks);
        })
        .catch(() => {});
    return () => {
      stop = true;
    };
  }, [selected, sourcePage, module]);
  const [followupQuery, setFollowupQuery] = useState('');
  const [description, setDescription] = useState('输入 → 解析 → 检索 → 生成 → 证据核验');
  const operationNames: Record<string, string> = {
    review_artifact: '摘要与文献整理',
    local_parse: '本地结构解析',
    translate: '阅读翻译',
    summary: '来源摘要',
    draft: '写作框架',
    direction: '研究方向草稿',
    direction_check: '后续研究检索',
    chart: '数据图表',
    diagram: '架构图',
  };
  const canWrite = role === 'researcher';
  if (!project) return <p>请先登录研究空间。</p>;
  return (
    <section className="task-panel" style={{ marginBottom: 24 }}>
      <h2>
        {
          (
            {
              1: '翻译与阅读',
              2: '文献发现与追踪',
              3: '局域图谱与研究方向',
              4: '证据写作工作室',
              5: '受控数据分析与绘图',
            } as Record<number, string>
          )[module]
        }
      </h2>
      {(error || note) && <p role={error ? 'alert' : 'status'}>{error || note}</p>}
      {[1, 3, 4].includes(module) && (
        <details open={module !== 3}>
          <summary>
            {module === 3 ? '研究方向的来源与执行方式' : '研究来源与执行方式'}（已选 {selected.length} 篇）
          </summary>
          <label>
            模型执行方式{' '}
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              <option value="offline_acceptance">离线契约验收（模拟响应）</option>
              <option value="cloud">真实云端（需正式额度）</option>
            </select>
          </label>
          <fieldset className="source-picker">
            <legend>选择已解析文献（来源固定到解析修订）</legend>
            <div className="source-actions">
              <button
                onClick={() =>
                  setSelected(
                    docs
                      .filter((d) => d.native_parse_id)
                      .slice(0, 30)
                      .map((d) => d.native_parse_id!),
                  )
                }
              >
                选择全部来源（最多 30 篇）
              </button>
              <button onClick={() => setSelected([])}>清空来源</button>
              <span>已选 {selected.length} 篇</span>
            </div>
            {docs.map((d) => (
              <label key={d.id} style={{ display: 'block' }}>
                <input
                  type="checkbox"
                  disabled={!d.native_parse_id}
                  checked={!!d.native_parse_id && selected.includes(d.native_parse_id)}
                  onChange={(e) =>
                    setSelected(
                      e.target.checked
                        ? [...selected, d.native_parse_id!]
                        : selected.filter((id) => id !== d.native_parse_id),
                    )
                  }
                />
                {d.title}
                {!d.native_parse_id ? ' · 尚无深解析' : ''}
              </label>
            ))}
          </fieldset>
        </details>
      )}
      {module === 1 && (
        <div>
          <h3>本地结构解析</h3>
          <p>文本型 PDF 可本地解析；纯扫描件仍需已授权的 OCR。此步骤不发送文件到云端。</p>
          {docs
            .filter((d) => !d.native_parse_id)
            .map((d) => (
              <p key={d.id}>
                {d.title}{' '}
                <button
                  disabled={busy || !canWrite}
                  onClick={() => void task('local_parse', { version_id: d.id })}
                >
                  解析文本结构
                </button>
              </p>
            ))}
        </div>
      )}
      {module === 1 && (
        <>
          <p>整篇或选段翻译按原文块保存；公式、代码和引用标记受保护。模拟模式不会产生真正的中文翻译。</p>
          <button
            disabled={busy || !canWrite || selected.length !== 1}
            onClick={() => void task('translate', { parse_id: selected[0] })}
          >
            翻译所选整篇
          </button>
          <details>
            <summary>选择页面与段落翻译</summary>
            <label>
              PDF 页码{' '}
              <input
                type="number"
                min={1}
                value={sourcePage}
                onChange={(e) => setSourcePage(Math.max(1, Number(e.target.value) || 1))}
              />
            </label>
            {pageBlocks.map((b) => (
              <label key={b.id} style={{ display: 'block' }}>
                <input
                  type="checkbox"
                  checked={blockIds.includes(b.id)}
                  onChange={(e) =>
                    setBlockIds(e.target.checked ? [...blockIds, b.id] : blockIds.filter((id) => id !== b.id))
                  }
                />
                {b.canonical_text.slice(0, 180)}
              </label>
            ))}
            <button
              disabled={busy || !canWrite || selected.length !== 1 || !blockIds.length}
              onClick={() => void task('translate', { parse_id: selected[0], block_ids: blockIds })}
            >
              翻译勾选段落
            </button>
          </details>
          <button disabled={busy || !canWrite || !selected.length} onClick={() => void task('summary')}>
            生成有来源摘要
          </button>
          {segments.length > 0 && (
            <Suspense fallback={<p>加载双语阅读器…</p>}>
              <BilingualReader segments={segments} />
            </Suspense>
          )}
        </>
      )}
      {module === 2 && (
        <>
          <p>
            搜索会将查询词发送给所选公开元数据服务，不发送本地 PDF。Crossref
            返回的元数据不代表全文可用；代码尚未核验时不会声称已经复现。
          </p>
          <label>
            元数据源{' '}
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="arxiv">arXiv</option>
              <option value="crossref">Crossref</option>
            </select>
          </label>
          <label>
            论文标题或主题 <input value={query} onChange={(e) => setQuery(e.target.value)} maxLength={500} />
          </label>
          <button
            disabled={!query.trim() || busy || !canWrite}
            onClick={() =>
              void action(async () =>
                setRecords(
                  await api(`/studio/projects/${project}/search`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider, query, limit: 10 }),
                  }),
                ),
              )
            }
          >
            检索公开文献
          </button>
          <button
            disabled={!query.trim() || busy || !canWrite}
            onClick={() =>
              void action(async () => {
                await api(`/studio/projects/${project}/subscriptions`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ query, name: query.slice(0, 80), hours: 24 }),
                });
                setNote('已创建每日 arXiv 追踪。');
              })
            }
          >
            创建每日追踪
          </button>
          {records.map((r) => (
            <article key={r.source_record_id}>
              <h3>
                <a href={r.url} target="_blank" rel="noreferrer">
                  {r.title}
                </a>
              </h3>
              <p>{r.abstract}</p>
              <small>
                {r.provider} · {r.pdf_url ? '可请求公开 PDF' : '仅元数据，未提供可直接下载的全文'}
              </small>
              {r.pdf_url && (
                <button
                  disabled={busy || !canWrite}
                  onClick={() =>
                    void action(async () => {
                      await api(`/studio/sources/${r.source_record_id}/import`, {
                        method: 'POST',
                        headers: { 'Idempotency-Key': crypto.randomUUID() },
                      });
                      setNote('公开 PDF 已下载并导入本地；深解析与模型调用独立准入。');
                    })
                  }
                >
                  下载公开 PDF 到文献库
                </button>
              )}
            </article>
          ))}
          <h3>追踪记录</h3>
          {subs.map((s) => (
            <p key={s.id}>
              {s.name} · {s.enabled ? (s.cursor_state.status ?? '等待调度') : '已停止'}{' '}
              {s.enabled && (
                <button
                  disabled={!canWrite}
                  onClick={() =>
                    void action(async () => {
                      await api(`/studio/subscriptions/${s.id}`, { method: 'DELETE' });
                      setNote('追踪已停止。');
                    })
                  }
                >
                  停止追踪
                </button>
              )}
            </p>
          ))}
          <h3>推荐与反馈</h3>
          {recs.map((r) => (
            <article key={r.id}>
              <strong>{r.record_json.title}</strong>
              <p>
                理由：标题/摘要与“{r.reason_json.query}”词语匹配；兴趣反馈样本{' '}
                {r.reason_json.feedback_samples ?? 0} · 代码状态：{r.code_state_json.status}
              </p>
              <button
                disabled={!canWrite || busy}
                onClick={() =>
                  void action(async () => {
                    await api(`/studio/recommendations/${r.id}/verify-code`, { method: 'POST' });
                    setNote('已核验元数据内明确给出的 GitHub 链接；存在仓库不等于已复现实验。');
                  })
                }
              >
                核验明确代码链接
              </button>
              {r.code_state_json.repositories?.map((repo) => (
                <p key={repo.url}>
                  <a href={repo.url} target="_blank" rel="noreferrer">
                    {repo.url}
                  </a>{' '}
                  · 许可 {repo.license_spdx ?? '尚未确认'} · {repo.archived ? '已归档' : '未归档'} ·
                  未复现验证
                </p>
              ))}
              {([-1, 0, 1] as const).map((v) => (
                <button
                  key={v}
                  disabled={!canWrite || busy}
                  onClick={() =>
                    void action(async () => {
                      await api(`/studio/recommendations/${r.id}/feedback`, {
                        method: 'PUT',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ vote: v }),
                      });
                      setNote('反馈已保存。');
                    })
                  }
                >
                  {v === 1 ? '有用' : v === -1 ? '不相关' : '取消反馈'}
                  {r.vote === v ? ' ✓' : ''}
                </button>
              ))}
            </article>
          ))}
        </>
      )}
      {module === 3 && (
        <>
          <fieldset className="source-picker">
            <legend>图谱文献范围（正式验收至少 20 篇）</legend>
            <div className="source-actions">
              <button onClick={() => setGraphPapers([...new Set(papers.map((p) => p.id))])}>
                选择全部图谱文献
              </button>
              <button onClick={() => setGraphPapers([])}>清空图谱范围</button>
              <span>已选 {graphPapers.length} 篇</span>
            </div>
            {[...new Map(papers.map((p) => [p.id, p])).values()].map((p) => (
              <label key={p.id} style={{ display: 'block' }}>
                <input
                  type="checkbox"
                  checked={graphPapers.includes(p.id)}
                  onChange={(e) =>
                    setGraphPapers(
                      e.target.checked ? [...graphPapers, p.id] : graphPapers.filter((id) => id !== p.id),
                    )
                  }
                />
                {p.title}
              </label>
            ))}
          </fieldset>
          <button
            disabled={busy || !canWrite || !graphPapers.length}
            onClick={() =>
              void action(async () =>
                setGraph(
                  await api(`/studio/projects/${project}/graph`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ paper_ids: graphPapers }),
                  }),
                ),
              )
            }
          >
            构建引用图谱
          </button>
          {graph && (
            <Suspense fallback={<p>加载图谱…</p>}>
              <CitationGraph
                data={graph}
                onEvidence={(id) =>
                  void action(async () => setTarget(await api(`/studio/blocks/${id}/locate`)))
                }
              />
            </Suspense>
          )}
          <label>
            研究方向与核验问题 <textarea value={idea} onChange={(e) => setIdea(e.target.value)} />
          </label>
          <p>当前方向草稿基于所选文献；没有后续联网证据时必须显示覆盖不足，不能断言问题未解决。</p>
          <button
            disabled={busy || !canWrite || !selected.length || !idea.trim()}
            onClick={() => void task('direction')}
          >
            整理研究方向证据草稿
          </button>
          <label>
            后续研究检索词（仅这些词发送至 arXiv）
            <input value={followupQuery} maxLength={500} onChange={(e) => setFollowupQuery(e.target.value)} />
          </label>
          <button
            disabled={busy || !canWrite || !selected.length || followupQuery.trim().length < 3}
            onClick={() => void task('direction_check', { search_query: followupQuery })}
          >
            检索后续证据（公开元数据，不调用模型）
          </button>
          <p>保存检索时间、范围和原文 Future Work 候选。摘要匹配需后续全文核验，未找到不能当作问题未解决。</p>
        </>
      )}
      {module === 4 && (
        <>
          <label>
            研究 Idea / 写作目标{' '}
            <textarea value={idea} maxLength={2000} onChange={(e) => setIdea(e.target.value)} />
          </label>
          <p>
            生成 Abstract、Introduction、Related Work
            和计划实验框架；来源由服务端固定。草稿与文献身份均需人工核验，不生成虚构性能结果。
          </p>
          <button
            disabled={busy || !canWrite || !selected.length || !idea.trim()}
            onClick={() => void task('draft')}
          >
            生成带来源清单的框架
          </button>
          <button disabled={busy || !canWrite || !selected.length} onClick={() => void task('summary')}>
            整理来源摘要
          </button>
        </>
      )}
      {module === 5 && (
        <>
          <p>
            CSV、图、统计摘要和独立 Python 脚本一起打包；仅执行受控模板。三维雷达图必须指定 CSV
            中真实的第三维列。
          </p>
          <label>
            CSV 文件{' '}
            <input type="file" accept=".csv,text/csv" onChange={(e) => setCsv(e.target.files?.[0] ?? null)} />
          </label>
          <label>
            图形{' '}
            <select value={chartKind} onChange={(e) => setChartKind(e.target.value)}>
              {Object.entries({
                table: '表格',
                line: '折线图',
                bar: '柱状图',
                scatter: '散点图',
                radar: '二维雷达图',
                radar3d: '三维雷达图',
              }).map(([k, label]) => (
                <option key={k} value={k}>
                  {label}
                </option>
              ))}
            </select>
          </label>
          <label>
            X / 名称列 <input value={x} onChange={(e) => setX(e.target.value)} />
          </label>
          <label>
            数值列（逗号分隔） <input value={ys} onChange={(e) => setYs(e.target.value)} />
          </label>
          <label>
            第三维列（3D 必填） <input value={z} onChange={(e) => setZ(e.target.value)} />
          </label>
          <label>
            单位 JSON（不指定会明确标注） <input value={units} onChange={(e) => setUnits(e.target.value)} />
          </label>
          <button
            disabled={!csv || busy || !canWrite}
            onClick={() =>
              void action(async () => {
                const form = new FormData();
                form.append('file', csv!);
                form.append(
                  'config',
                  JSON.stringify({
                    kind: chartKind,
                    x,
                    y: ys.split(',').map((s) => s.trim()),
                    z: z || null,
                    units: JSON.parse(units),
                    missing: 'reject',
                    title: csv!.name,
                  }),
                );
                await api(`/studio/projects/${project}/charts`, {
                  method: 'POST',
                  headers: { 'Idempotency-Key': crypto.randomUUID() },
                  body: form,
                });
                setNote('图表任务已保存。');
              })
            }
          >
            生成可复现图表
          </button>
          <h3>架构拓扑</h3>
          <label>
            流程描述（用 → 或 -&gt; 连接步骤，每条流程一行）
            <textarea value={description} maxLength={2000} onChange={(e) => setDescription(e.target.value)} />
          </label>
          <button
            disabled={busy || !canWrite || !description.trim()}
            onClick={() => void task('diagram', { description })}
          >
            按描述生成架构图
          </button>
          <p>按明确步骤连线，不自动猜测未描述的节点。也可在下方精确编辑拓扑。</p>
          <label>
            节点名称（每行一个） <textarea value={nodes} onChange={(e) => setNodes(e.target.value)} />
          </label>
          <label>
            有向边（每行：起点序号,终点序号，从 0 开始）{' '}
            <textarea value={edges} onChange={(e) => setEdges(e.target.value)} />
          </label>
          <button
            disabled={busy || !canWrite}
            onClick={() =>
              void task('diagram', {
                nodes: nodes.split('\n').filter(Boolean),
                edges: edges
                  .split('\n')
                  .filter(Boolean)
                  .map((row) => row.split(',').map(Number)),
              })
            }
          >
            生成架构图与源码包
          </button>
        </>
      )}
      {module !== 2 && <h3>任务与产物</h3>}
      {module !== 2 && !runs.length && (
        <p>本模块还没有任务。选择来源并运行后，可在这里查看结果与下载产物。</p>
      )}
      {runs.map((r) => (
        <details key={r.id}>
          <summary>
            {operationNames[r.operation] ?? r.operation} · {runStatus(r.status)}
          </summary>
          {r.editorial_review && (
            <p role="status">
              {r.editorial_review.reason}{' '}
              <a href={`/api/v1/studio/artifacts/${r.editorial_review.reviewed_artifact_version_id}`}>
                下载整理版
              </a>
            </p>
          )}
          {r.error_json && <p role="alert">{runErrorMessage(r.error_json)}</p>}
          {r.result_json && (
            <>
              <p>
                {r.result_json.local_postprocessing_only
                  ? '本地整理已有模型产物，未新增调用'
                  : r.result_json.model_simulated
                    ? '模拟结果，仅用于契约验收'
                    : r.operation === 'direction_check'
                      ? '公开元数据检索，未调用模型'
                      : r.mode === 'local'
                        ? '本地计算产物'
                        : '模型结果待核验'}
              </p>
              <p>
                {r.result_json.review_notice ?? r.result_json.caption ?? r.result_json.limitations_notice}
              </p>
              {r.result_json.mermaid && (
                <Suspense fallback={<p>加载架构图…</p>}>
                  <DiagramPreview source={r.result_json.mermaid} />
                </Suspense>
              )}
              {r.result_json.checked_at && (
                <p>检索时间：{new Date(r.result_json.checked_at).toLocaleString()}</p>
              )}
              {r.result_json.candidates?.map((c, i) => (
                <p key={i}>
                  <a href={c.url} target="_blank" rel="noreferrer">
                    {c.title}
                  </a>{' '}
                  · 仅元数据，需全文核验
                </p>
              ))}
              {r.result_json.summary_spans?.some((w) => w.anchors.some((a) => a.block_spans?.length)) && (
                <details>
                  <summary>查看来源笔记原文（不等于逐条主张验证）</summary>
                  {r.result_json.summary_spans
                    .flatMap((w) => w.anchors.flatMap((a) => a.block_spans ?? []))
                    .slice(0, 30)
                    .map((span, i) => (
                      <button key={i} onClick={() => setTarget(span)}>
                        来源原文，第 {span.page} 页
                      </button>
                    ))}
                </details>
              )}
              {r.result_json.sections?.map((s, i) => (
                <article key={i}>
                  <h4>{s.title}</h4>
                  <p>{s.text}</p>
                </article>
              ))}
              {r.result_json.artifact_version_id && (
                <a href={`/api/v1/studio/artifacts/${r.result_json.artifact_version_id}`}>
                  下载产物与来源清单
                </a>
              )}
              {['summary', 'draft'].includes(r.operation) && r.result_json.artifact_version_id && (
                <button
                  disabled={busy || !canWrite}
                  onClick={() =>
                    void task('review_artifact', { artifact_version_id: r.result_json!.artifact_version_id })
                  }
                >
                  整理摘要与原件 References
                </button>
              )}
              {r.result_json.translation_id && (
                <button
                  onClick={() =>
                    void action(async () =>
                      setSegments(
                        await api(`/studio/translations/${r.result_json!.translation_id}/segments`),
                      ),
                    )
                  }
                >
                  打开中英逐块对照
                </button>
              )}
            </>
          )}
        </details>
      ))}
      {target && (
        <Suspense fallback={<p>加载证据原文…</p>}>
          <SourceReader
            key={target.parse_id + ':' + target.page}
            target={target}
            close={() => setTarget(null)}
          />
        </Suspense>
      )}
    </section>
  );
}
