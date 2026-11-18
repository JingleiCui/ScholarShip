import { lazy, Suspense, useEffect, useState } from 'react';
import Research from './Research';
import DocumentPreparation from './DocumentPreparation';
import { api, errorMessage } from './researchApi';
import { Icon } from './Brand';
import type { ReadingTab } from './navigation';
import type { Target, SourceSelection } from './SourceReader';
const Library = lazy(() => import('./Library'));
const Studio = lazy(() => import('./Studio'));
const SourceReader = lazy(() => import('./SourceReader'));
type Doc = { id: string; title: string; native_parse_id: string | null };
export default function Reader({
  project,
  role,
  versions,
  tab,
  run,
  onScope,
  onTab,
  onLibrary,
  onResults,
}: {
  project: string;
  role?: string;
  versions: string[];
  tab: ReadingTab;
  run?: string;
  onScope: (ids: string[]) => void;
  onTab: (tab: ReadingTab) => void;
  onLibrary: () => void;
  onResults: () => void;
}) {
  const [docs, setDocs] = useState<Doc[]>([]),
    [error, setError] = useState(''),
    [target, setTarget] = useState<Target | null>(null);
  const [selection, setSelection] = useState<SourceSelection | null>(null);
  const scopeKey = versions.join(',');
  useEffect(() => {
    let stop = false;
    setDocs([]);
    setError('');
    async function load() {
      try {
        const rows = await api(`/research/projects/${project}/documents`);
        if (!stop) {
          setDocs(rows);
          setError('');
        }
      } catch (e) {
        if (!stop) setError(errorMessage(e));
      }
    }
    void load();
    const timer = setInterval(() => void load(), 4000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [project]);
  useEffect(() => {
    setTarget(null);
    setSelection(null);
  }, [project, scopeKey]);
  useEffect(() => {
    let stopped = false;
    if (!run || tab !== 'document') return;
    void api(`/studio/projects/${project}/runs`)
      .then((rows) => {
        const record = rows.find((r: { id: string }) => r.id === run);
        const source = record?.result_json?.source;
        if (source?.version_id && source?.parse_id && source?.page && !stopped)
          setTarget({ version_id: source.version_id, parse_id: source.parse_id, page: source.page });
      })
      .catch((e) => {
        if (!stopped) setError(errorMessage(e));
      });
    return () => {
      stopped = true;
    };
  }, [project, run, tab]);
  const current = docs.find((d) => d.id === versions[0]);
  return (
    <>
      <div className="reading-heading">
        <button className="text-link" onClick={onLibrary}>
          文献库
        </button>
        <span>/ 阅读与问答</span>
        <h1>{current?.title ?? '选择文献，开始阅读。'}</h1>
        <p>原件、译文与证据在同一个工作空间。问答来源固定到文献与解析修订。</p>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      <div className="reading-context">
        <label>
          当前阅读文献{' '}
          <select value={current?.id ?? ''} onChange={(e) => onScope(e.target.value ? [e.target.value] : [])}>
            <option value="">选择一篇文献</option>
            {docs.map((d) => (
              <option key={d.id} value={d.id}>
                {d.title}
              </option>
            ))}
          </select>
        </label>
        <span className="status-pill">
          已选 {versions.filter((id) => docs.some((d) => d.id === id)).length} 篇来源
        </span>
        <button className="text-link" onClick={onResults}>
          查看已有成果
        </button>
      </div>
      {current && !current.native_parse_id && (
        <DocumentPreparation key={current.id} project={project} version={current.id} role={role} />
      )}
      <div className="reading-tabs" role="tablist" aria-label="阅读任务">
        {(['document', 'qa', 'translation'] as ReadingTab[]).map((id, i) => (
          <button
            key={id}
            id={'reading-tab-' + id}
            role="tab"
            aria-selected={tab === id}
            aria-controls="reading-workspace"
            tabIndex={tab === id ? 0 : -1}
            onClick={() => onTab(id)}
            onKeyDown={(e) => {
              const keys = ['ArrowLeft', 'ArrowRight', 'Home', 'End'];
              if (!keys.includes(e.key)) return;
              e.preventDefault();
              const tabs: ReadingTab[] = ['document', 'qa', 'translation'];
              const next =
                e.key === 'Home' ? 0 : e.key === 'End' ? 2 : (i + (e.key === 'ArrowRight' ? 1 : 2)) % 3;
              onTab(tabs[next]);
              requestAnimationFrame(() => document.getElementById('reading-tab-' + tabs[next])?.focus());
            }}
          >
            {['阅读原文', '证据问答 / 多文对比', '选词与双语翻译'][i]}
          </button>
        ))}
      </div>
      {!current ? (
        <section className="empty-state">
          <Icon name="book" size={36} />
          <h2>先选定一篇文献</h2>
          <p>从上方选择已导入文献，或回到文献库导入。没有默认的虚构论文与回答。</p>
          <button className="primary" onClick={onLibrary}>
            打开文献库
          </button>
        </section>
      ) : (
        <div
          id="reading-workspace"
          role="tabpanel"
          aria-labelledby={'reading-tab-' + tab}
          className={tab === 'document' ? 'document-workspace' : 'reading-workspace'}
        >
          <section className="reading-source">
            <div className="source-mode">
              <h2>
                {target
                  ? `引用原文 · ${docs.find((d) => d.id === target.version_id)?.title ?? '固定文献版本'}`
                  : '文献原文'}
              </h2>
              <div className="action-row">
                <button onClick={() => setTarget(null)} aria-pressed={!target}>
                  PDF 原件
                </button>
                {current.native_parse_id && (
                  <button
                    onClick={() =>
                      setTarget({ version_id: current.id, parse_id: current.native_parse_id!, page: 1 })
                    }
                    aria-pressed={!!target}
                  >
                    结构原文 / 图表公式
                  </button>
                )}
              </div>
            </div>
            <p className="mobile-reading-link">
              <a href="#reading-assistant">跳至{tab === 'translation' ? '翻译' : '问答'}</a>
            </p>
            <Suspense fallback={<p>正在打开原文…</p>}>
              {target ? (
                <SourceReader
                  key={
                    target.parse_id +
                    ':' +
                    target.page +
                    ':' +
                    (target.quote ?? '') +
                    ':' +
                    target.pdf_bbox?.join(',')
                  }
                  target={target}
                  onSelectText={(value) => {
                    setSelection(value);
                    onTab('translation');
                  }}
                  close={() => setTarget(null)}
                />
              ) : (
                <Library project={project} role={role} layout="reader" initialVersionId={current.id} />
              )}
            </Suspense>
          </section>
          {tab !== 'document' && (
            <section className="reading-assistant" id="reading-assistant">
              <Suspense fallback={<p>正在加载研究工具…</p>}>
                {tab === 'qa' ? (
                  <Research
                    project={project}
                    role={role}
                    versionIds={versions}
                    onScopeChange={onScope}
                    onEvidence={setTarget}
                    focusRunId={run}
                    onResults={onResults}
                  />
                ) : (
                  <Studio
                    project={project}
                    role={role}
                    module={1}
                    sourceSelection={selection}
                    versionIds={versions}
                    onScopeChange={onScope}
                    focusRunId={run}
                    onResults={onResults}
                  />
                )}
              </Suspense>
            </section>
          )}
          {tab === 'document' && (
            <div className="next-action">
              <div>
                <h2>读到这里，有个问题？</h2>
                <p>围绕这篇文献提问，并点开回答的出处核对。</p>
              </div>
              <button className="primary" onClick={() => onTab('qa')}>
                向文献提问 <Icon name="arrow" size={18} />
              </button>
              <button onClick={() => onTab('translation')}>阅读翻译</button>
            </div>
          )}
        </div>
      )}
    </>
  );
}
