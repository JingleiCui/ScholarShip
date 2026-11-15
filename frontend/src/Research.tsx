import { lazy, Suspense, useEffect, useState } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
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

export default function Research({ project, role }: { project: string; role?: string }) {
  const [docs, setDocs] = useState<Doc[]>([]),
    [selected, setSelected] = useState<string[]>([]);
  const [runs, setRuns] = useState<Run[]>([]),
    [question, setQuestion] = useState(''),
    [error, setError] = useState('');
  const [pollError, setPollError] = useState('');
  const [busy, setBusy] = useState(false),
    [target, setTarget] = useState<Target | null>(null);
  const [parent, setParent] = useState('');
  useEffect(() => setParent(''), [project]);
  const [mode, setMode] = useState<'offline_acceptance' | 'cloud'>('offline_acceptance');
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
    setBusy(true);
    setError('');
    try {
      await api(`/research/projects/${project}/${kind}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({
          version_ids: selected,
          mode,
          ...(kind === 'questions' ? { question, parent_run_id: parent || null } : {}),
        }),
      });
      await refresh();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const queryReady =
    selected.length > 0 &&
    selected.every((id) =>
      docs.some(
        (d) => d.id === id && (mode === 'offline_acceptance' ? d.can_query_offline : d.can_query_cloud),
      ),
    );
  if (!project) return null;
  return (
    <section className="task-panel" style={{ marginBottom: 24 }}>
      <h2>文献证据问答 · M2</h2>
      <p>
        使用正式文献版本与结构化原文（OCR
        或本地文本层）。当前可做离线验收：向量、重排和回答为模拟响应，不代表科研正确性。云端任务须同时满足模型、文献范围及限额准入。
      </p>
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
      {(error || pollError) && (
        <p role="alert" className="error">
          {error || pollError}
        </p>
      )}
      {docs.length === 0 && <p>请先在文献库导入 PDF；已授权的 MinerU 本地产物通过项目导入脚本发布。</p>}
      {docs.map((d) => (
        <div key={d.id} style={{ margin: '12px 0' }}>
          <label>
            <input
              type="checkbox"
              disabled={!d.native_parse_id}
              checked={selected.includes(d.id)}
              onChange={(e) =>
                setSelected(e.target.checked ? [...selected, d.id] : selected.filter((v) => v !== d.id))
              }
            />
            {d.title} ·{' '}
            {d.native_index_stale
              ? '新 OCR 待重建索引，问答使用原活动修订'
              : d.native_parse_id
                ? '原文已入库'
                : '尚无深解析'}{' '}
            · {d.active_index_id ? (d.model_simulated ? '模拟索引' : '真实索引') : '未建索引'}
          </label>{' '}
          {d.native_parse_id && (
            <button onClick={() => setTarget({ version_id: d.id, parse_id: d.native_parse_id!, page: 1 })}>
              阅读结构化原文
            </button>
          )}
        </div>
      ))}
      <button
        disabled={busy || !selected.length || role !== 'researcher'}
        onClick={() => void submit('indexes')}
      >
        建立所选文献索引
      </button>
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
      {runs.map((r, index) => (
        <details
          open={index === 0}
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
                      .then(setTarget)
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
