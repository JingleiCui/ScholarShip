import { useEffect, useState } from 'react';
import { api, errorMessage } from './researchApi';
type Part = {
  id: string;
  translated_text: string;
  canonical_text: string;
  text_sha256: string;
  page_no: number;
  quality_flags: string[];
  placeholders_json: { human_edited?: boolean };
};
export default function TranslationReview({
  translation,
  canWrite,
  onRevision,
}: {
  translation: string;
  canWrite: boolean;
  onRevision: (id: string) => void;
}) {
  const [parts, setParts] = useState<Part[]>([]),
    [index, setIndex] = useState(0),
    [text, setText] = useState(''),
    [reason, setReason] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    let active = true;
    setError('');
    setParts([]);
    void api(`/studio/translations/${translation}/review`)
      .then((p) => {
        if (active) {
          setParts(p);
          setIndex(0);
          setText(p[0]?.translated_text ?? '');
        }
      })
      .catch((e) => {
        if (active) setError(errorMessage(e));
      });
    return () => {
      active = false;
    };
  }, [translation]);
  const selected = parts[index];
  return (
    <details className="translation-review">
      <summary>译文核对与人工修订</summary>
      <p>修订保存为新版本，原模型回执与原译文保留。数字／否定提示是辅助检查，不代表完整语义评测。</p>
      <a href={`/api/v1/studio/translations/${translation}/export`}>下载当前双语 Markdown</a>
      {parts.length > 0 && (
        <>
          <label>
            核对片段
            <select
              value={index}
              onChange={(e) => {
                const i = Number(e.target.value);
                setIndex(i);
                setText(parts[i].translated_text);
                setReason('');
              }}
            >
              {parts.map((p, i) => (
                <option value={i} key={p.id}>
                  第 {p.page_no} 页 · 片段 {i + 1}
                  {p.quality_flags.length ? ' · 需核对' : ''}
                  {p.placeholders_json.human_edited ? ' · 人工修订' : ''}
                </option>
              ))}
            </select>
          </label>
          <blockquote>{selected?.canonical_text}</blockquote>
          {selected?.quality_flags.map((f) => (
            <p role="status" key={f}>
              {f}
            </p>
          ))}
          <label>
            译文
            <textarea value={text} onChange={(e) => setText(e.target.value)} disabled={!canWrite} rows={8} />
          </label>
          <label>
            修订说明
            <input
              value={reason}
              onChange={(e) => setReason(e.target.value)}
              maxLength={500}
              placeholder="例如：纠正否定范围；数字与原文一致"
            />
          </label>
          <button
            disabled={
              !canWrite || busy || !text.trim() || !reason.trim() || text === selected?.translated_text
            }
            onClick={() => {
              setBusy(true);
              setError('');
              void api(`/studio/translations/${translation}/revisions`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                  segment_id: selected.id,
                  expected_text_sha256: selected.text_sha256,
                  text,
                  reason,
                }),
              })
                .then((r) => {
                  onRevision(r.translation_id);
                  setReason('');
                })
                .catch((e) => setError(errorMessage(e)))
                .finally(() => setBusy(false));
            }}
          >
            {busy ? '保存中…' : '另存为人工修订版'}
          </button>
        </>
      )}
      {error && <p role="alert">{error}</p>}
    </details>
  );
}
