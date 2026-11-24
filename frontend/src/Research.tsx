import TaskEvents from './TaskEvents';
import CancelTask from './CancelTask';
import DocumentProgress from './DocumentProgress';
import { lazy, Suspense, useEffect, useState, useRef } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import useExecution from './useExecution';
import { BrandMark, Icon } from './Brand';
import { conversationGroups, sourceKey, type ConversationRun } from './researchConversation';
import type { Target } from './SourceReader';
const SourceReader = lazy(() => import('./SourceReader'));
const PROMPTS = [
  '请概括研究目标、方法与主要结论，并给出原文依据。',
  '实验设置和评价指标是什么？',
  '作者提到了哪些局限或适用条件？',
];
type Mode = 'offline_acceptance' | 'cloud' | 'local_inference';
type Doc = {
  can_query_offline: boolean;
  can_query_cloud: boolean;
  can_query_local: boolean;
  native_index_stale?: boolean;
  id: string;
  title: string;
  native_parse_id: string | null;
  active_index_id: string | null;
  model_simulated: boolean | null;
};
type Citation = { id: string; quote: string; page: number; version_id: string; parse_id: string };
type Result = {
  scope?: { generations?: { id: string; version_id: string }[] };
  answer_method?: string;
  answer?: string;
  model_simulated: boolean;
  source_synthetic: boolean;
  citations?: Citation[];
  limitations?: string;
};
type Run = ConversationRun & {
  scientific_review?: { verdict: string; reason: string } | null;
  model_preset?: string;
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
  onConversationChange,
}: {
  project: string;
  role?: string;
  versionIds?: string[];
  onScopeChange?: (ids: string[]) => void;
  onEvidence?: (target: Target) => void;
  focusRunId?: string;
  onResults?: () => void;
  onConversationChange?: (run?: string) => void;
}) {
  const execution = useExecution(project);
  const [docs, setDocs] = useState<Doc[]>([]),
    [selected, setSelected] = useState<string[]>(versionIds ?? []);
  const [runs, setRuns] = useState<Run[]>([]),
    [question, setQuestion] = useState('');
  const [error, setError] = useState(''),
    [pollError, setPollError] = useState('');
  const [busy, setBusy] = useState(false),
    [mode, setMode] = useState<Mode>('local_inference');
  const [activeRun, setActiveRun] = useState<string | null>(focusRunId ?? null);
  const [target, setTarget] = useState<Target | null>(null),
    [awaitingIndex, setAwaitingIndex] = useState(false);
  const composer = useRef<HTMLTextAreaElement>(null),
    end = useRef<HTMLDivElement>(null);
  const transcript = useRef<HTMLDivElement>(null),
    followTail = useRef(true);
  const locked = useRef(false),
    focused = useRef('');
  const mounted = useRef(true),
    view = useRef('');
  view.current = project + ':' + mode + ':' + sourceKey(selected);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  const scopeKey = versionIds?.join(',');
  useEffect(() => {
    if (versionIds) setSelected((old) => (sourceKey(old) === sourceKey(versionIds) ? old : versionIds));
  }, [scopeKey]);
  useEffect(() => {
    setActiveRun(focusRunId ?? null);
    setQuestion('');
    setError('');
  }, [project, scopeKey]);
  useEffect(() => {
    if (!focusRunId || focused.current === focusRunId) return;
    const row = runs.find((r) => r.id === focusRunId && r.kind === 'research_qa');
    if (row) {
      focused.current = focusRunId;
      setActiveRun(row.id);
      if (row.mode) setMode(row.mode as Mode);
    }
  }, [focusRunId, runs]);
  async function refresh() {
    const [d, r] = await Promise.all([
      api('/research/projects/' + project + '/documents'),
      api('/research/projects/' + project + '/runs'),
    ]);
    setDocs(d);
    setRuns(r);
  }
  useEffect(() => {
    let stopped = false;
    setDocs([]);
    setRuns([]);
    setPollError('');
    async function poll() {
      try {
        const [d, r] = await Promise.all([
          api('/research/projects/' + project + '/documents'),
          api('/research/projects/' + project + '/runs'),
        ]);
        if (!stopped) {
          setDocs(d);
          setRuns(r);
          setPollError('');
        }
      } catch (e) {
        if (!stopped) setPollError(errorMessage(e));
      }
    }
    void poll();
    const timer = setInterval(() => void poll(), 2500);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [project]);
  const groups = conversationGroups(runs, selected, mode);
  const conversation = groups.find((g) => g.turns.some((r) => r.id === activeRun));
  const turns = conversation?.turns ?? [];
  const last = turns.at(-1);
  const outdatedConversation = turns.some((r) =>
    r.result_json?.scope?.generations?.some((g) =>
      docs.some((d) => d.id === g.version_id && d.active_index_id && d.active_index_id !== g.id),
    ),
  );
  const pendingTurn = turns.some((r) => ['queued', 'running', 'waiting_user'].includes(r.status));
  const parent =
    last?.status === 'succeeded' ? last.id : [...turns].reverse().find((r) => r.status === 'succeeded')?.id;
  const buildReady =
    selected.length > 0 &&
    selected.length <= 8 &&
    selected.every((id) => docs.some((d) => d.id === id && d.native_parse_id)) &&
    (mode === 'local_inference'
      ? execution.localAvailable(['embedding'])
      : mode === 'cloud'
        ? execution.available(['embedding'])
        : !docs.some((d) => selected.includes(d.id) && d.active_index_id && d.model_simulated === false));
  const queryReady =
    selected.length > 0 &&
    selected.length <= 8 &&
    (mode === 'local_inference'
      ? execution.localAvailable(['embedding', 'rerank', 'chat'])
      : mode !== 'cloud' || execution.available(['embedding', 'rerank', 'chat'])) &&
    selected.every((id) =>
      docs.some(
        (d) =>
          d.id === id &&
          !d.native_index_stale &&
          (mode === 'offline_acceptance'
            ? d.can_query_offline
            : mode === 'local_inference'
              ? d.can_query_local
              : d.can_query_cloud),
      ),
    );
  const indexTasks = runs.filter(
    (r) => r.kind === 'research_build' && r.source_version_ids?.some((id) => selected.includes(id)),
  );
  const indexing = indexTasks.some((r) => ['queued', 'running'].includes(r.status));
  useEffect(() => {
    if (awaitingIndex && queryReady && !indexing) {
      setAwaitingIndex(false);
      composer.current?.focus();
    }
  }, [awaitingIndex, queryReady, indexing]);
  useEffect(() => {
    if (activeRun && followTail.current && transcript.current) {
      transcript.current.scrollTo({ top: transcript.current.scrollHeight, behavior: 'smooth' });
    }
  }, [turns.length, last?.status, activeRun]);
  function newConversation() {
    setActiveRun(null);
    setQuestion('');
    setError('');
    followTail.current = true;
    onConversationChange?.();
    composer.current?.focus();
  }
  function selectSources(ids: string[]) {
    if (ids.length > 8) {
      setError('一次对话最多选择 8 篇文献。');
      return;
    }
    setSelected(ids);
    setActiveRun(null);
    setError('');
    onScopeChange?.(ids);
  }
  function showEvidence(value: Target) {
    if (onEvidence) onEvidence(value);
    else setTarget(value);
    requestAnimationFrame(() =>
      document.querySelector('.reading-source')?.scrollIntoView({ block: 'start', behavior: 'smooth' }),
    );
  }
  async function submit(kind: 'indexes' | 'questions') {
    if (locked.current) return;
    if (
      (kind === 'indexes' && (!buildReady || indexing)) ||
      (kind === 'questions' && (!queryReady || pendingTurn || outdatedConversation || !question.trim()))
    ) {
      setError('请先核对来源、索引与模型状态，等待当前步骤完成。');
      return;
    }
    locked.current = true;
    setBusy(true);
    setError('');
    const requestView = view.current;
    try {
      const batches = kind === 'indexes' ? selected.map((id) => [id]) : [selected];
      for (const version_ids of batches) {
        const row = await api('/research/projects/' + project + '/' + kind, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
          body: JSON.stringify({
            version_ids,
            mode,
            ...(kind === 'questions' ? { question: question.trim(), parent_run_id: parent ?? null } : {}),
          }),
        });
        if (!mounted.current || requestView !== view.current) return;
        if (kind === 'questions') {
          followTail.current = true;
          setRuns((old) => [
            {
              ...row,
              question: question.trim(),
              mode,
              source_version_ids: [...selected],
              parent_run_id: parent ?? null,
              conversation_id: last?.conversation_id ?? null,
            },
            ...old.filter((r) => r.id !== row.id),
          ]);
          setActiveRun(row.id);
          setQuestion('');
          onConversationChange?.(row.id);
        } else setAwaitingIndex(true);
      }
      try {
        await refresh();
      } catch {
        setPollError('任务已提交，正在重新获取进度；请勿重复提交。');
      }
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      locked.current = false;
      if (mounted.current) setBusy(false);
    }
  }
  const hasUnparsed = selected.some((id) => !docs.find((d) => d.id === id)?.native_parse_id);
  const statusText = !selected.length
    ? '先选择论文'
    : hasUnparsed
      ? mode === 'local_inference'
        ? '正在自动解析论文'
        : '先完成论文解析'
      : indexing
        ? '正在建立索引'
        : queryReady
          ? '可以开始对话'
          : '正在准备论文 · 查看下方进度';
  if (!project) return null;
  return (
    <section className="research-chat" aria-label="论文 AI 对话">
      <div className="chat-heading">
        <div>
          <h2>与 AI 讨论论文</h2>
          <p>围绕所选原文提问，点击回答中的引用核对出处。</p>
        </div>
        <div className="action-row">
          <span className={'chat-readiness ' + (queryReady ? 'ready' : '')} role="status">
            {statusText}
          </span>
          <button type="button" onClick={newConversation}>
            新建对话
          </button>
          {onResults && (
            <button type="button" className="text-link" onClick={onResults}>
              全部成果
            </button>
          )}
        </div>
      </div>
      <div className="chat-source-strip">
        <Icon name="file" size={18} />
        <span>
          {selected.length
            ? selected.map((id) => docs.find((d) => d.id === id)?.title ?? '正在读取文献').join(' / ')
            : '尚未选择文献'}
        </span>
        <small>{selected.length} 篇来源</small>
      </div>
      {mode === 'local_inference' && (
        <DocumentProgress
          project={project}
          versions={selected}
          role={role}
          compatible={selected.every((id) =>
            docs.some((d) => d.id === id && d.can_query_local && !d.native_index_stale),
          )}
        />
      )}
      {(error || pollError) && (
        <p role="alert" className="error">
          {error || pollError}
        </p>
      )}
      {outdatedConversation && (
        <p className="inline-notice" role="status">
          当前是基于旧索引的历史对话，原回答与引用保留。论文索引已更新，请开启新对话使用最新正文。
          <button type="button" onClick={newConversation}>
            使用最新资料开始对话
          </button>
        </p>
      )}
      {!queryReady && mode !== 'local_inference' && (
        <div className="chat-setup" aria-label="对话准备">
          <p>
            {hasUnparsed
              ? '论文尚未解析。切换“阅读与解析”完成本地解析，再回来建立索引。'
              : indexing
                ? '索引在后台建立，完成后输入框会准备就绪。你可以先写下问题。'
                : mode === 'cloud'
                  ? '云端对话需要有效准入和相匹配索引；历史回答仍可查看。'
                  : '模拟模式仅演示流程，回答不用于研究。'}{' '}
          </p>
          <button
            type="button"
            className="primary"
            disabled={busy || indexing || !buildReady || role !== 'researcher'}
            onClick={() => void submit('indexes')}
          >
            {indexing ? '索引建立中…' : '建立所选文献索引'}
          </button>
          {indexTasks
            .filter((r) => ['queued', 'running', 'failed', 'waiting_user'].includes(r.status))
            .slice(0, 3)
            .map((r) => (
              <div key={r.id}>
                <p>
                  索引 · {runStatus(r.status)}
                  {r.error_json ? ' · ' + runErrorMessage(r.error_json) : ''}
                </p>
                <details>
                  <summary>查看索引步骤</summary>
                  <TaskEvents run={r.id} />
                </details>
              </div>
            ))}
        </div>
      )}
      <div
        ref={transcript}
        className="chat-transcript"
        role="log"
        aria-label="当前对话记录"
        aria-relevant="additions"
        onScroll={(e) => {
          const log = e.currentTarget;
          followTail.current = log.scrollHeight - log.scrollTop - log.clientHeight < 100;
        }}
      >
        {!turns.length && (
          <div className="chat-welcome">
            <BrandMark size={38} />
            <h3>从一个具体问题开始</h3>
            <p>可以询问研究方法、实验设置和局限。回答附原文引用；缺少依据时会说明。</p>
            <div className="chat-suggestions">
              {PROMPTS.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => {
                    setQuestion(prompt);
                    composer.current?.focus();
                  }}
                >
                  {prompt}
                </button>
              ))}
            </div>
          </div>
        )}
        {turns.map((r) => (
          <div className="chat-turn" id={'research-run-' + r.id} key={r.id}>
            <article className="chat-message user">
              <span className="chat-speaker">你</span>
              <p>{r.question}</p>
            </article>
            <article className="chat-message assistant">
              <div className="chat-speaker">
                <BrandMark size={24} /> ScholarShip
              </div>
              {r.scientific_review && (
                <p className={r.scientific_review.verdict === 'incorrect' ? 'error' : 'inline-notice'}>
                  {r.scientific_review.verdict === 'incorrect'
                    ? '此历史回答已核对为错误，请勿采用。'
                    : '固定样例已有源文审阅，不代表一般准确率。'}
                  {r.scientific_review.reason}
                </p>
              )}
              {['queued', 'running'].includes(r.status) && (
                <>
                  <p className="chat-progress" role="status">
                    {r.status === 'queued' ? '问题已收到，等待处理…' : '正在检索原文、重排证据并生成回答…'}
                  </p>
                  <TaskEvents run={r.id} initiallyOpen />
                </>
              )}
              {r.error_json && (
                <p role="alert" className="error">
                  {runErrorMessage(r.error_json)}
                </p>
              )}
              {r.status === 'cancelled' && <p>本次问答已取消，已有记录保留。</p>}
              {r.result_json?.answer && (
                <>
                  <p className="chat-answer">{r.result_json.answer}</p>
                  <div className="chat-citations" aria-label="回答引用">
                    {r.result_json.citations?.map((c, i) => (
                      <button
                        key={c.id}
                        type="button"
                        title={c.quote}
                        onClick={() =>
                          void api('/research/evidence/' + c.id)
                            .then(showEvidence)
                            .catch((e) => setError(errorMessage(e)))
                        }
                      >
                        <Icon name="source" size={16} /> 引用 {i + 1} · 原件第 {c.page} 页
                      </button>
                    ))}
                  </div>
                  <p className="chat-origin">
                    {r.result_json.answer_method?.startsWith('deterministic_')
                      ? '原文结构提取 / 本地计算'
                      : r.result_json.model_simulated
                        ? '模拟回答，不用于科研'
                        : r.mode === 'local_inference'
                          ? '本地模型回答，需核对原文'
                          : '历史云端模型回答，需核对原文'}
                    {r.result_json.source_synthetic ? ' · 合成测试资料' : ''}
                  </p>
                </>
              )}
              <details className="chat-turn-details">
                <summary>处理状态与核查说明 · {runStatus(r.status)}</summary>
                {role === 'researcher' && <CancelTask id={r.id} status={r.status} />}
                <TaskEvents run={r.id} />
                {r.result_json?.limitations && <p>{r.result_json.limitations}</p>}
                <p>模型组合：{r.model_preset ?? mode}</p>
              </details>
            </article>
          </div>
        ))}
        <div ref={end} />
      </div>
      <form
        className="chat-composer"
        aria-label="发送论文问题"
        onSubmit={(e) => {
          e.preventDefault();
          void submit('questions');
        }}
      >
        <label htmlFor="research-question">{parent ? '继续追问' : '研究问题'}</label>
        <textarea
          id="research-question"
          ref={composer}
          value={question}
          maxLength={1000}
          rows={3}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={
            parent ? '继续围绕这组文献提问…' : '输入你的问题，例如：作者用了什么方法，原文依据在哪里？'
          }
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
              e.preventDefault();
              void submit('questions');
            }
          }}
        />
        <div className="chat-composer-footer">
          <small>
            {pendingTurn
              ? last?.status === 'waiting_user'
                ? '当前任务需处理，请先核查；不会自动重复请求。'
                : '本轮回答完成后可以继续追问。'
              : parent
                ? '续问保持相同来源与索引。换文献请新建对话。'
                : !queryReady
                  ? '可先写下问题；论文和模型准备完成后即可发送。'
                  : 'Enter 发送，Shift + Enter 换行。'}{' '}
            {question.length}/1000
          </small>
          <button
            className="primary"
            type="submit"
            disabled={
              busy ||
              pendingTurn ||
              outdatedConversation ||
              !queryReady ||
              !question.trim() ||
              role !== 'researcher'
            }
          >
            <Icon name="arrow" size={18} />
            {busy ? '正在提交…' : '发送问题'}
          </button>
        </div>
      </form>
      <div className="chat-secondary">
        <details className="source-scope">
          <summary>问答来源与索引 · 已选 {selected.length} 篇</summary>
          <p>最多 8 篇；切换来源会开始新的对话上下文。</p>
          <div className="action-row">
            <button type="button" onClick={() => selectSources(selected.slice(0, 1))}>
              只问当前这篇
            </button>
            <button type="button" onClick={() => selectSources([])}>
              清空来源
            </button>
          </div>
          {docs.map((d) => (
            <label className="chat-source-option" key={d.id}>
              <input
                type="checkbox"
                disabled={!d.native_parse_id}
                checked={selected.includes(d.id)}
                onChange={(e) =>
                  selectSources(e.target.checked ? [...selected, d.id] : selected.filter((id) => id !== d.id))
                }
              />
              {d.title}
              <small>
                {!d.native_parse_id
                  ? '待解析'
                  : d.native_index_stale
                    ? '新解析待重建索引，历史索引保留'
                    : !d.active_index_id
                      ? '待建索引'
                      : d.model_simulated
                        ? '模拟索引'
                        : '真实索引'}
              </small>
            </label>
          ))}
          <button
            type="button"
            disabled={busy || indexing || !buildReady || role !== 'researcher'}
            onClick={() => void submit('indexes')}
          >
            建立 / 重建所选索引
          </button>
        </details>
        <details className="execution-options">
          <summary>
            执行方式 ·{' '}
            {mode === 'local_inference' ? '本地模型' : mode === 'cloud' ? '云端 / 历史回看' : '模拟流程'}
          </summary>
          <label>
            执行模式{' '}
            <select
              value={mode}
              onChange={(e) => {
                setMode(e.target.value as Mode);
                setActiveRun(null);
                setError('');
                onConversationChange?.();
              }}
            >
              <option value="local_inference">本地模型</option>
              <option value="cloud">云端业务 · 需准入</option>
              <option value="offline_acceptance">离线验收 · 模拟</option>
            </select>
          </label>
          <p>{execution.notice}</p>
        </details>
        <details className="chat-history">
          <summary>当前来源的会话历史 · {groups.length} 个</summary>
          {!groups.length && <p>还没有这组文献在当前模式下的历史对话。</p>}
          {groups.map((g) => (
            <button
              type="button"
              key={g.id}
              className="chat-history-item"
              onClick={() => {
                const id = g.turns.at(-1)!.id;
                setActiveRun(id);
                setQuestion('');
                onConversationChange?.(id);
              }}
            >
              <span>{g.turns[0].question}</span>
              <small>
                {g.turns.length} 轮 · {runStatus(g.turns.at(-1)!.status)}
              </small>
            </button>
          ))}
          <p>展示最近 100 项问答与索引记录中的会话；历史成果也可从成果页打开。</p>
        </details>
      </div>
      {target && (
        <Suspense fallback={<p>正在打开原文…</p>}>
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
