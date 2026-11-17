import { useEffect, useState } from 'react';
import { api, errorMessage } from './researchApi';
type Reference = {
  id: string;
  marker: string;
  raw_reference: string;
  resolution_state?: string;
  resolved_paper_id?: string;
};
type RecordItem = {
  source_record_id: string;
  title: string;
  url: string;
  pdf_url?: string;
  authors?: string[];
  published?: string;
};
export default function ReferenceWorkbench({
  parse,
  references,
}: {
  parse: string;
  references: Reference[];
}) {
  const [context, setContext] = useState<{
    project_id: string;
    papers: { id: string; title: string }[];
    can_write: boolean;
  } | null>(null);
  const [ref, setRef] = useState(''),
    [paper, setPaper] = useState(''),
    [reason, setReason] = useState('');
  const [query, setQuery] = useState(''),
    [provider, setProvider] = useState('crossref');
  const [records, setRecords] = useState<RecordItem[]>([]),
    [notice, setNotice] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let stopped = false;
    void api(`/studio/parses/${parse}/reference-context`)
      .then((r) => {
        if (!stopped) setContext(r);
      })
      .catch((e) => {
        if (!stopped) setError(errorMessage(e));
      });
    return () => {
      stopped = true;
    };
  }, [parse]);
  async function act(fn: () => Promise<void>) {
    setBusy(true);
    setError('');
    setNotice('');
    try {
      await fn();
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const current = references.find((r) => r.id === ref);
  if (!references.length) return null;
  return (
    <details className="library-actions">
      <summary>参考文献检索与身份核对（{references.length} 条）</summary>
      <label>
        参考条目
        <select
          value={ref}
          onChange={(e) => {
            const r = references.find((x) => x.id === e.target.value);
            setRef(e.target.value);
            setPaper(r?.resolved_paper_id ?? '');
            setQuery((r?.raw_reference ?? '').slice(0, 500));
            setRecords([]);
            setReason('');
          }}
        >
          <option value="">选择原文参考条目</option>
          {references.map((r) => (
            <option key={r.id} value={r.id}>
              [{r.marker}] {r.raw_reference.slice(0, 100)}
            </option>
          ))}
        </select>
      </label>
      {current && (
        <>
          <blockquote>{current.raw_reference}</blockquote>
          <p>先按标题、作者、年份核对；同名或主题相近不代表同一作品。</p>
          <label>
            公开检索词
            <input value={query} maxLength={500} onChange={(e) => setQuery(e.target.value)} />
          </label>
          <label>
            检索来源
            <select value={provider} onChange={(e) => setProvider(e.target.value)}>
              <option value="crossref">Crossref</option>
              <option value="arxiv">arXiv</option>
            </select>
          </label>
          <button
            disabled={busy || !context?.can_write || !query.trim()}
            onClick={() =>
              void act(async () => {
                setRecords(
                  await api(`/studio/projects/${context!.project_id}/search`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ provider, query, limit: 8 }),
                  }),
                );
              })
            }
          >
            用此检索词查找公开元数据
          </button>
          <p>只发送上方检索词；PDF 导入仅支持已有开放下载地址。</p>
          {records.map((r) => (
            <article key={r.source_record_id}>
              <a href={r.url} target="_blank" rel="noreferrer">
                {r.title}
              </a>
              <p>
                {r.authors?.join(', ')} · {r.published}
              </p>
              <button
                disabled={busy || !r.pdf_url || !context?.can_write}
                onClick={() =>
                  void act(async () => {
                    await api(`/studio/sources/${r.source_record_id}/import`, {
                      method: 'POST',
                      headers: { 'Idempotency-Key': crypto.randomUUID() },
                    });
                    setNotice('已导入并排队预览。重新打开文献库可查看，身份关系仍需人工确认。');
                    setContext(await api(`/studio/parses/${parse}/reference-context`));
                  })
                }
              >
                导入开放 PDF
              </button>
            </article>
          ))}
          <label>
            对应的本地作品
            <select value={paper} onChange={(e) => setPaper(e.target.value)}>
              <option value="">尚未消歧／撤销已有确认</option>
              {context?.papers.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.title}
                </option>
              ))}
            </select>
          </label>
          <label>
            核对依据
            <textarea
              value={reason}
              maxLength={1000}
              onChange={(e) => setReason(e.target.value)}
              placeholder="填写已核对的作者、年份、标识或原文依据"
            />
          </label>
          <button
            disabled={busy || !context?.can_write || reason.trim().length < 3}
            onClick={() =>
              void act(async () => {
                await api(`/studio/references/${ref}/resolution`, {
                  method: 'PUT',
                  headers: { 'Content-Type': 'application/json' },
                  body: JSON.stringify({ paper_id: paper || null, reason }),
                });
                setNotice('身份核对已保存；重新生成图谱可采用此结果。');
              })
            }
          >
            保存身份核对
          </button>
        </>
      )}
      {notice && <p role="status">{notice}</p>}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
