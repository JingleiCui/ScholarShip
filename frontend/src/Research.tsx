import TaskEvents from './TaskEvents';
import CancelTask from './CancelTask';
import { lazy, Suspense, useEffect, useState, useRef } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import useExecution from './useExecution';
import type { Target } from './SourceReader';
const SourceReader = lazy(() => import('./SourceReader'));

type Doc = {
  can_query_offline: boolean;
  can_query_cloud: boolean;
  native_index_stale?: boolean;
  id: string;
  title: string;
  native_parse_id: string | null;
  active_index_id: string | null;
  model_simulated: boolean | null;
};
type Citation = { id: string; quote: string; page: number; version_id: string; parse_id: string };
type Result = {
  answer_method?: string;
  answer?: string;
  model_simulated: boolean;
  source_synthetic: boolean;
  citations?: Citation[];
  limitations?: string;
};
type Run = {
  scientific_review?: { verdict: string; reason: string } | null;
  question?: string;
  model_preset?: string;
  id: string;
  kind: string;
  status: string;
  result_json?: Result;
  error_json?: { code: string; contract_code?: string };
};

export default function Research({
  project,
  role,
  versionIds,
  onScopeChange,
  onEvidence,
  focusRunId,
  onResults,
}: {
  project: string;
  role?: string;
  versionIds?: string[];
  onScopeChange?: (ids: string[]) => void;
  onEvidence?: (target: Target) => void;
  focusRunId?: string;
  onResults?: () => void;
}) {
  const execution = useExecution(project);
  const scopeKey = versionIds?.join(',');
  function selectSources(ids: string[]) {
    if (ids.length > 8 && ids.length >= selected.length) {
      setError('一次问答最多选择 8 个版本，请缩小范围。');
      return;
    }
    setSelected(ids);
    setParent('');
    onScopeChange?.(ids);
  }
  function showEvidence(value: Target) {
    if (onEvidence) {
      onEvidence(value);
      requestAnimationFrame(() =>
        document.querySelector('.reading-source')?.scrollIntoView({ block: 'start' }),
      );
    } else setTarget(value);
  }
  const focused = useRef('');
  const [docs, setDocs] = useState<Doc[]>([]),
    [selected, setSelected] = useState<string[]>([]);
  const [runs, setRuns] = useState<Run[]>([]),
    [question, setQuestion] = useState(''),
    [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [busy, setBusy] = useState(false),
    [target, setTarget] = useState<Target | null>(null);
  const [parent, setParent] = useState('');
  useEffect(() => setParent(''), [project, scopeKey]);
  const [mode, setMode] = useState<'offline_acceptance' | 'cloud'>('cloud');
  useEffect(() => {
    if (versionIds) {
      const next = versionIds.filter((id) => docs.some((d) => d.id === id));
      setSelected((old) => (old.join(',') === next.join(',') ? old : next));
    }
  }, [scopeKey, docs]);
  useEffect(() => {
    if (!focusRunId || !runs.some((r) => r.id === focusRunId) || focused.current === focusRunId) return;
    focused.current = focusRunId;
    requestAnimationFrame(() =>
      document.getElementById('research-run-' + focusRunId)?.scrollIntoView({ block: 'start' }),
    );
  }, [focusRunId, runs]);
  const cloudReady = execution.available(['embedding', 'rerank', 'chat']);
  const buildReady =
    mode === 'cloud'
      ? execution.available(['embedding'])
      : !docs.some((d) => selected.includes(d.id) && d.active_index_id && d.model_simulated === false);
  async function refresh() {
    const [d, r] = await Promise.all([
      api(`/research/projects/${project}/documents`),
      api(`/research/projects/${project}/runs`),
    ]);
    setDocs(d);
    setRuns(r);
  }
  useEffect(() => {
    let cancelled = false;
    if (!project) return;
    async function poll() {
      try {
        const [d, r] = await Promise.all([
          api(`/research/projects/${project}/documents`),
          api(`/research/projects/${project}/runs`),
        ]);
        if (!cancelled) {
          setDocs(d);
          setRuns(r);
          setPollError('');
        }
      } catch (e) {
        if (!cancelled) setPollError(errorMessage(e));
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [project]);
  async function submit(kind: 'indexes' | 'questions') {
    if ((kind === 'indexes' && !buildReady) || (kind === 'questions' && !queryReady)) {
      setError('当前来源与执行条件尚未就绪，请核对提示。');
      return;
    }
    setBusy(true);
    setError('');
    try {
      // Each document has its own durable index task; QA still freezes the full selected scope.
      const groups = kind === 'indexes' ? selected.map((id) => [id]) : [selected];
      for (const version_ids of groups) {
        await api(`/research/projects/${project}/${kind}`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
          body: JSON.stringify({
            version_ids,
            mode,
            ...(kind === 'questions' ? { question, parent_run_id: parent || null } : {}),
          }),
        });
      }
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const queryReady =
    selected.length > 0 &&
    selected.length <= 8 &&
    (mode !== 'cloud' || cloudReady) &&
    selected.every((id) =>
      docs.some(
        (d) => d.id === id && (mode === 'offline_acceptance' ? d.can_query_offline : d.can_query_cloud),
      ),
    );
  if (!project) return null;
  return (
    <section className="task-panel" style={{ marginBottom: 24 }}>
      <div className="section-top">
        <h2>向所选文献提问</h2>
        {onResults && (
          <button className="text-link" onClick={onResults}>
            历史成果
          </button>
        )}
      </div>
      <p>回答只依据当前选择的文献。单篇细节、多轮续问与跨文对比使用相同的来源核对方式。</p>
      <p className="inline-notice">{execution.notice}</p>
      <details className="execution-options">
        <summary>执行方式：{mode === 'cloud' ? '真实云端 / 历史回看' : '离线流程验收（模拟）'}</summary>
        <label>
          执行模式{' '}
          <select
            value={mode}
            onChange={(e) => {
              setMode(e.target.value as typeof mode);
              setError('');
            }}
          >
            <option value="offline_acceptance">离线验收 · 不发出外部请求</option>
            <option value="cloud">云端业务 · 需正式额度准入</option>
          </select>
        </label>
      </details>
      {(error || pollError) && (
        <p role="alert" className="error">
          {error || pollError}
        </p>
      )}
      {docs.length === 0 && <p>请先在文献库导入 PDF；已授权的 MinerU 本地产物通过项目导入脚本发布。</p>}
      <details className="source-scope" open={!selected.length}>
        <summary>问答来源 · 已选 {selected.length} 篇（最多 8 篇，可多文对比）</summary>
        <div className="action-row">
          <button disabled={!selected.length} onClick={() => selectSources(selected.slice(0, 1))}>
            只问当前这篇
          </button>
          <button onClick={() => selectSources([])}>清空来源</button>
        </div>
        {selected.length > 8 && (
          <p className="inline-notice">
            当前从其他工具带入 {selected.length} 篇。一次问答最多 8
            篇，请缩小来源范围；也可点击“只问当前这篇”。
          </p>
        )}
        {docs.map((d) => (
          <div key={d.id} style={{ margin: '12px 0' }}>
            <label>
              <input
                type="checkbox"
                disabled={!d.native_parse_id}
                checked={selected.includes(d.id)}
                onChange={(e) =>
                  selectSources(e.target.checked ? [...selected, d.id] : selected.filter((v) => v !== d.id))
                }
              />
              {d.title} ·{' '}
              {d.native_index_stale
                ? '新结构修订待重建索引，历史问答保留原修订'
                : d.native_parse_id
                  ? '原文已入库'
                  : '尚未提取文本结构'}{' '}
              · {d.active_index_id ? (d.model_simulated ? '模拟索引' : '真实索引') : '未建索引'}
            </label>{' '}
            {d.native_parse_id && (
              <button
                onClick={() => showEvidence({ version_id: d.id, parse_id: d.native_parse_id!, page: 1 })}
              >
                阅读结构化原文
              </button>
            )}
          </div>
        ))}
      </details>
      <details className="index-preparation">
        <summary>问答准备与索引状态</summary>
        <p className="helper">仅在需要时建立索引。模拟索引不会覆盖所选文献已有的真实活动索引。</p>
        <button
          disabled={busy || !buildReady || !selected.length || role !== 'researcher'}
          onClick={() => void submit('indexes')}
        >
          建立所选文献索引
        </button>
        {!buildReady && (
          <p className="helper">
            {mode === 'cloud'
              ? '新的真实向量化未开放；可回看现有索引与历史回答。'
              : '所选文献已有真实活动索引，请在独立练习空间执行模拟建索引。'}
          </p>
        )}
      </details>
      <label>
        会话上下文{' '}
        <select value={parent} onChange={(e) => setParent(e.target.value)}>
          <option value="">新问题（不继承上文）</option>
          {runs
            .filter((r) => r.kind === 'research_qa' && r.status === 'succeeded')
            .map((r) => (
              <option key={r.id} value={r.id}>
                接着问：{r.question?.slice(0, 70)}
              </option>
            ))}
        </select>
      </label>
      <label style={{ display: 'block', marginTop: 16 }}>
        研究问题{' '}
        <textarea
          value={question}
          maxLength={1000}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder="问题仅从所选文献证据回答"
        />
      </label>
      {selected.length > 0 && !queryReady && (
        <p role="status">
          {mode === 'cloud'
            ? '云端问答需要相匹配的真实索引和有效限额；请先核对配置与准入状态。'
            : '所选文献的向量配置与当前模型不一致，或索引尚未就绪。请先建立或重建索引。'}
        </p>
      )}
      <button
        disabled={busy || !question.trim() || !queryReady || role !== 'researcher'}
        onClick={() => void submit('questions')}
      >
        提交证据问答
      </button>
      <h3 className="history-heading">空间历史回答与任务</h3>
      {runs.map((r, index) => (
        <details
          open={focusRunId ? r.id === focusRunId : index === 0}
          id={'research-run-' + r.id}
          key={r.id}
          style={{ marginTop: 20, borderTop: '1px solid #ddd', paddingTop: 12 }}
        >
          <summary>
            <strong>
              {r.question ?? '文献索引'} ·{' '}
              {(
                {
                  queued: '排队中',
                  running: '执行中',
                  succeeded: '流程完成',
                  failed: '失败',
                  waiting_user: '需处理',
                } as Record<string, string>
              )[r.status] ?? r.status}
            </strong>
          </summary>
          {role === 'researcher' && <CancelTask id={r.id} status={r.status} />}
          <TaskEvents run={r.id} />
          <p>任务模型组合：{r.model_preset}</p>
          {r.error_json && <p role="alert">{runErrorMessage(r.error_json)}</p>}
          {r.result_json && (
            <>
              {r.scientific_review && (
                <p role={r.scientific_review.verdict === 'incorrect' ? 'alert' : 'status'}>
                  {r.scientific_review.verdict === 'incorrect'
                    ? '此历史回答已核对为错误，请勿采用。'
                    : '官方单篇样例已核对；不代表一般问答准确率。'}
                  {r.scientific_review.reason}
                </p>
              )}
              <p>
                {r.result_json.answer_method?.startsWith('deterministic_')
                  ? '原文结构提取／本地计算（非模型生成）'
                  : r.result_json.model_simulated
                    ? '模型响应：模拟'
                    : '模型响应：真实'}{' '}
                · {r.result_json.source_synthetic ? '来源：合成' : '来源：正式文献'}
              </p>
              <p style={{ whiteSpace: 'pre-wrap' }}>{r.result_json.answer}</p>
              <p>{r.result_json.limitations}</p>
              {r.result_json.citations?.map((c, i) => (
                <button
                  key={c.id}
                  onClick={() =>
                    void api(`/research/evidence/${c.id}`)
                      .then(showEvidence)
                      .catch((e) => setError(errorMessage(e)))
                  }
                >
                  引用 {i + 1} · 原件第 {c.page} 页
                </button>
              ))}
            </>
          )}
        </details>
      ))}
      {target && (
        <Suspense fallback={<p>正在加载原文阅读器…</p>}>
          <SourceReader
            key={`${target.version_id}:${target.parse_id}:${target.page}:${target.quote ?? ''}`}
            target={target}
            close={() => setTarget(null)}
          />
        </Suspense>
      )}
    </section>
  );
}
