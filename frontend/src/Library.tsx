import LibraryActions from './LibraryActions';
import CancelTask from './CancelTask';
import { api as request, errorMessage } from './researchApi';
import { lazy, Suspense, useEffect, useRef, useState } from 'react';
import * as pdfjs from 'pdfjs-dist';
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url';
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
import type { Target } from './SourceReader';
const SourceReader = lazy(() => import('./SourceReader'));
type Paper = {
  id: string;
  title: string;
  version_id: string;
  metadata_json: { page_count: number };
};
type Parse = {
  parser_name: string;
  revision_no: number;
  mode: string;
  version_id: string;
  id: string;
  status: string;
  completed_pages: number;
  expected_pages: number;
  error_json?: unknown;
  result_json?: unknown;
  run_id?: string;
};
export default function Library({
  project,
  role,
  onResearch,
}: {
  project: string;
  role?: string;
  onResearch?: () => void;
}) {
  const canWrite = role === 'researcher';
  const [papers, setPapers] = useState<Paper[]>([]),
    [selected, setSelected] = useState<Paper | null>(null),
    [parses, setParses] = useState<Parse[]>([]),
    [page, setPage] = useState(1),
    [text, setText] = useState(''),
    [error, setError] = useState(''),
    [append, setAppend] = useState(false),
    [busy, setBusy] = useState(false),
    [reviews, setReviews] = useState<{ id: string; candidate_json: { papers: { title: string }[] } }[]>([]);
  const [queue, setQueue] = useState<{ file: File; key: string; state: string }[]>([]);
  const stopQueue = useRef(false);
  const [parseChoice, setParseChoice] = useState('');
  const [notice, setNotice] = useState('');
  const [readerTarget, setReaderTarget] = useState<Target | null>(null);
  const currentParse =
    parses.find((p) => p.id === parseChoice) ??
    parses.find(
      (p) => ['mineru-native', 'pdfplumber-structure'].includes(p.parser_name) && p.status === 'ready',
    ) ??
    parses[0];
  const rendering = useRef<pdfjs.RenderTask | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null),
    pdf = useRef<pdfjs.PDFDocumentProxy | null>(null);
  async function refresh() {
    if (project) {
      const fresh: Paper[] = await request(`/projects/${project}/papers`);
      setPapers(fresh);
      setSelected((old) => (old ? (fresh.find((p) => p.version_id === old.version_id) ?? null) : null));
      setReviews(await request(`/projects/${project}/duplicate-reviews`));
    }
  }
  useEffect(() => {
    let cancelled = false;
    setSelected(null);
    setPapers([]);
    setParses([]);
    setReviews([]);
    setError('');
    setNotice('');
    setParseChoice('');
    setReaderTarget(null);
    if (project)
      void Promise.all([
        request(`/projects/${project}/papers`),
        request(`/projects/${project}/duplicate-reviews`),
      ])
        .then(([p, r]) => {
          if (!cancelled) {
            setPapers(p);
            setReviews(r);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(errorMessage(e));
        });
    return () => {
      cancelled = true;
    };
  }, [project]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval>;
    async function poll() {
      try {
        const rows = await request(`/paper-versions/${selected!.version_id}/parses`);
        if (!cancelled) setParses(rows);
      } catch (e) {
        if (!cancelled) setError(errorMessage(e));
      }
    }
    void poll();
    timer = setInterval(() => void poll(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selected]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const loading = pdfjs.getDocument({
      url: `/api/v1/paper-versions/${selected.version_id}/file`,
      withCredentials: true,
    });
    void loading.promise
      .then((d) => {
        if (!cancelled) {
          pdf.current = d;
          setPage(1);
          void render(d, 1).catch((e) => {
            if (!cancelled) setError(errorMessage(e));
          });
        } else void loading.destroy();
      })
      .catch((e) => {
        if (!cancelled) setError(errorMessage(e));
      });
    return () => {
      cancelled = true;
      rendering.current?.cancel();
      pdf.current = null;
      void loading.destroy();
    };
  }, [selected]);
  async function render(doc: pdfjs.PDFDocumentProxy, n: number) {
    rendering.current?.cancel();
    if (rendering.current) await rendering.current.promise.catch(() => {});
    const pg = await doc.getPage(n);
    if (doc !== pdf.current) return;
    const base = pg.getViewport({ scale: 1 });
    const viewport = pg.getViewport({
      scale: Math.min(1.2, 1600 / base.width, 2200 / base.height),
    });
    const node = canvas.current;
    if (!node) return;
    node.width = viewport.width;
    node.height = viewport.height;
    const task = pg.render({ canvas: node, viewport });
    rendering.current = task;
    await task.promise.catch((e) => {
      if (e.name !== 'RenderingCancelledException') throw e;
    });
  }
  useEffect(() => {
    if (pdf.current) void render(pdf.current, page).catch((e) => setError(errorMessage(e)));
  }, [page]);
  useEffect(() => {
    let cancelled = false;
    const p = currentParse;
    setText('正在加载对应页的文本…');
    if (p && p.version_id === selected?.version_id)
      void request(`/parses/${p.id}/pages/${page}`)
        .then((r) => {
          if (!cancelled)
            setText(
              r.needs_ocr
                ? '此页没有可提取文本：可能为空白页或需要 OCR。'
                : r.blocks.map((b: { canonical_text: string }) => b.canonical_text).join('\n'),
            );
        })
        .catch(() => {
          if (!cancelled) setText('此页尚未提取。');
        });
    return () => {
      cancelled = true;
    };
  }, [page, currentParse?.id, currentParse?.status, currentParse?.completed_pages, selected?.version_id]);
  async function upload(file: File, key: string) {
    setError('');
    try {
      const data = new FormData();
      data.append('file', file);
      if (append && selected) data.append('paper_id', selected.id);
      const result = await request(`/projects/${project}/uploads`, {
        method: 'POST',
        headers: { 'Idempotency-Key': key },
        body: data,
      });
      await refresh();
      setNotice(
        result.reused
          ? '检测到内容完全相同的 PDF，已复用原版本；没有创建重复文献。'
          : 'PDF 已导入本地，正在逐页生成预览。新文件尚未深解析或建立问答索引。',
      );
      const importedPapers: Paper[] = await request(`/projects/${project}/papers`);
      const imported = importedPapers.find((p) => p.version_id === result.version_id);
      if (imported && imported.version_id !== selected?.version_id) {
        setSelected(imported);
        setPage(1);
        setParses([]);
        setParseChoice('');
      }
      if (result.review_id) setError('发现标题、标识或文本相似候选，已保留两个载体，等待去重核验。');
    } catch (e) {
      setError(errorMessage(e));
      throw e;
    }
  }
  async function uploadBatch(items: { file: File; key: string; state: string }[]) {
    setBusy(true);
    stopQueue.current = false;
    setQueue(items);
    for (let i = 0; i < items.length; i++) {
      if (stopQueue.current) break;
      items = items.map((v, j) => (j === i ? { ...v, state: '上传中' } : v));
      setQueue([...items]);
      try {
        await upload(items[i].file, items[i].key);
        items[i] = { ...items[i], state: '已导入' };
      } catch (e) {
        items[i] = { ...items[i], state: errorMessage(e) };
      }
      setQueue([...items]);
    }
    setBusy(false);
  }
  return (
    <section className="task-panel library" style={{ marginBottom: 24 }}>
      <h2>文献库 · 原文与解析版本</h2>
      {selected && canWrite && (
        <label>
          <input type="checkbox" checked={append} onChange={(e) => setAppend(e.target.checked)} />
          将下次导入明确作为当前作品的新文件版本
        </label>
      )}
      <p>
        原件按账号隔离。导入只生成本地预览；已完成的 OCR
        可在解析修订中选择。尚无深解析的文献不能直接用于证据问答。
      </p>
      {project ? (
        <label className="primary">
          {busy ? '正在导入…' : '导入 PDF'}
          <input
            aria-label="导入 PDF"
            type="file"
            multiple={!append}
            accept="application/pdf"
            disabled={busy || !canWrite}
            style={{ display: 'block', marginTop: 12 }}
            onChange={(e) => {
              const files = [...(e.target.files ?? [])];
              e.currentTarget.value = '';
              if (files.length > 50) {
                setError('单批最多 50 份 PDF，请分批导入。');
                return;
              }
              if (files.length)
                void uploadBatch(
                  files.map((file) => ({ file, key: crypto.randomUUID(), state: '等待上传' })),
                );
            }}
          />
        </label>
      ) : (
        <p>请先登录研究空间。</p>
      )}
      {queue.length > 0 && (
        <section className="upload-queue" aria-live="polite">
          <p>
            本批 {queue.filter((q) => q.state === '已导入').length}/{queue.length}{' '}
            份已导入；逐文件保留结果，后续解析独立执行。
          </p>
          {busy && (
            <button
              onClick={() => {
                stopQueue.current = true;
              }}
            >
              停止后续上传
            </button>
          )}
          <ul>
            {queue.map((q) => (
              <li key={q.key}>
                <span>{q.file.name}</span> — {q.state}
                {!busy && q.state !== '已导入' && (
                  <button onClick={() => void uploadBatch([{ ...q, state: '等待上传' }])}>重试此文件</button>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}
      <LibraryActions
        canWrite={canWrite}
        key={project}
        project={project}
        papers={papers}
        selected={selected}
        refresh={refresh}
      />
      {notice && <p role="status">{notice}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {reviews.map((r) => (
        <div key={r.id} className="notice">
          疑似重复：{r.candidate_json.papers.map((p) => p.title).join('；')}
          。请核验后选择：
          {(['keep_both', 'reject'] as const).map((d) => (
            <button
              key={d}
              disabled={!canWrite}
              onClick={() =>
                void request(`/duplicate-reviews/${r.id}/decision`, {
                  method: 'POST',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ decision: d }),
                })
                  .then(() => refresh())
                  .catch((e) => setError(errorMessage(e)))
              }
            >
              {d === 'keep_both' ? '保留两份独立记录' : '拒绝本次新增记录'}
            </button>
          ))}
        </div>
      ))}
      <div className="paper-grid">
        {papers.map((p) => (
          <button
            className="module-card paper-card"
            key={p.version_id}
            onClick={() => {
              setSelected(p);
              setParses([]);
              setParseChoice('');
              setReaderTarget(null);
              setError('');
              setNotice('');
            }}
          >
            <div className="paper-cover" aria-hidden="true">
              <span>PDF</span>
              <strong>{p.title}</strong>
              <div className="paper-lines" />
              <div className="paper-lines short" />
            </div>
            <strong className="paper-title">{p.title}</strong>
            <small style={{ display: 'block' }}>
              {p.metadata_json.page_count} 页 · {p.version_id.slice(0, 8)}
            </small>
          </button>
        ))}
      </div>
      {readerTarget && (
        <Suspense fallback={<p>正在加载结构化原文…</p>}>
          <SourceReader
            key={`${readerTarget.parse_id}:${readerTarget.page}`}
            target={readerTarget}
            close={() => setReaderTarget(null)}
          />
        </Suspense>
      )}
      {selected && (
        <>
          <h3>{selected.title}</h3>
          {parses.length > 0 && (
            <label>
              解析修订{' '}
              <select
                aria-label="解析修订"
                value={currentParse?.id ?? ''}
                onChange={(e) => setParseChoice(e.target.value)}
              >
                {parses.map((p) => (
                  <option key={p.id} value={p.id}>
                    修订 {p.revision_no} ·{' '}
                    {p.parser_name === 'mineru-native'
                      ? 'OCR 深解析'
                      : p.parser_name === 'pdfplumber-structure'
                        ? '本地文本结构'
                        : '本地预览'}{' '}
                    · {p.completed_pages}/{p.expected_pages} 页
                  </option>
                ))}
              </select>
            </label>
          )}
          {currentParse?.mode === 'deep' && (
            <button
              onClick={() =>
                setReaderTarget({ version_id: selected.version_id, parse_id: currentParse.id, page })
              }
            >
              阅读结构化原文
            </button>
          )}
          {onResearch && <button onClick={onResearch}>前往证据问答</button>}
          {currentParse?.run_id && (
            <CancelTask
              id={currentParse.run_id}
              status={
                currentParse.status === 'processing'
                  ? 'running'
                  : currentParse.status === 'pending'
                    ? 'queued'
                    : currentParse.status
              }
            />
          )}
          <p>
            {currentParse
              ? `${currentParse.status} · ${currentParse.completed_pages}/${currentParse.expected_pages} 页`
              : '等待解析'}{' '}
            · 阅读顺序未核验
          </p>
          {(currentParse?.status === 'failed' ||
            (currentParse?.status === 'partial' &&
              currentParse.completed_pages < currentParse.expected_pages)) && (
            <button
              onClick={() =>
                void request(`/parses/${currentParse.id}/resume`, {
                  method: 'POST',
                }).catch((e) => setError(errorMessage(e)))
              }
            >
              继续未完成页
            </button>
          )}
          <button
            onClick={() =>
              void request(`/paper-versions/${selected.version_id}/parses`, {
                method: 'POST',
              }).catch((e) => setError(errorMessage(e)))
            }
          >
            新增解析修订
          </button>
          <details>
            <summary>解析诊断</summary>
            <pre style={{ whiteSpace: 'pre-wrap' }}>
              {JSON.stringify(currentParse?.error_json ?? currentParse?.result_json, null, 2)}
            </pre>
          </details>
          <label>
            {' '}
            原文页码{' '}
            <input
              aria-label="原文页码"
              type="number"
              min={1}
              max={selected.metadata_json.page_count}
              value={page}
              onChange={(e) =>
                setPage(Math.max(1, Math.min(selected.metadata_json.page_count, Number(e.target.value) || 1)))
              }
            />
          </label>
          <div
            style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(auto-fit,minmax(280px,1fr))',
              gap: 20,
              marginTop: 20,
            }}
          >
            <canvas ref={canvas} style={{ maxWidth: '100%', height: 'auto', background: 'white' }} />
            <pre
              style={{
                whiteSpace: 'pre-wrap',
                overflowWrap: 'anywhere',
                fontSize: 12,
                lineHeight: 1.8,
                maxHeight: 800,
                overflow: 'auto',
              }}
            >
              {text}
            </pre>
          </div>
        </>
      )}
    </section>
  );
}
