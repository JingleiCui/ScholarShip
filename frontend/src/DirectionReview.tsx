import { useEffect, useState } from 'react';
import { api, errorMessage } from './researchApi';
type Block = { id: string; canonical_text: string };
type Span = { block_id: string; start: number; end: number; text: string };
function EvidencePicker({
  label,
  docs,
  change,
}: {
  label: string;
  docs: { id: string; title: string }[];
  change: (s: Span | null) => void;
}) {
  const [parse, setParse] = useState(''),
    [page, setPage] = useState(1),
    [blocks, setBlocks] = useState<Block[]>([]),
    [block, setBlock] = useState(''),
    [error, setError] = useState('');
  useEffect(() => {
    let stopped = false;
    setBlocks([]);
    setBlock('');
    change(null);
    if (parse)
      void api(`/parses/${parse}/pages/${page}`)
        .then((r) => {
          if (!stopped) setBlocks(r.blocks);
        })
        .catch((e) => {
          if (!stopped) setError(errorMessage(e));
        });
    return () => {
      stopped = true;
    };
  }, [parse, page]);
  return (
    <fieldset>
      <legend>{label}</legend>
      <select
        aria-label={`${label}文献`}
        value={parse}
        onChange={(e) => {
          setParse(e.target.value);
          setPage(1);
        }}
      >
        <option value="">选择已解析全文</option>
        {docs.map((d) => (
          <option key={d.id} value={d.id}>
            {d.title}
          </option>
        ))}
      </select>
      <label>
        PDF 页码
        <input
          type="number"
          min={1}
          value={page}
          onChange={(e) => setPage(Math.max(1, Number(e.target.value) || 1))}
        />
      </label>
      <select
        aria-label={`${label}段落`}
        value={block}
        onChange={(e) => {
          setBlock(e.target.value);
          const b = blocks.find((b) => b.id === e.target.value);
          const text = Array.from(b?.canonical_text ?? '')
            .slice(0, 4000)
            .join('');
          change(b ? { block_id: b.id, start: 0, end: Array.from(text).length, text } : null);
        }}
      >
        <option value="">选择证据段落（最多 4,000 字符）</option>
        {blocks
          .filter((b) => b.canonical_text.trim())
          .map((b) => (
            <option key={b.id} value={b.id}>
              {b.canonical_text.slice(0, 120)}
            </option>
          ))}
      </select>
      {block && (
        <blockquote>
          {Array.from(blocks.find((b) => b.id === block)?.canonical_text ?? '')
            .slice(0, 4000)
            .join('')}
        </blockquote>
      )}
      {error && <p role="alert">{error}</p>}
    </fieldset>
  );
}
export default function DirectionReview({
  docs,
  submit,
  disabled,
}: {
  docs: { id: string; title: string }[];
  submit: (args: object) => Promise<void>;
  disabled: boolean;
}) {
  const [original, setOriginal] = useState<Span | null>(null),
    [followup, setFollowup] = useState<Span | null>(null),
    [question, setQuestion] = useState(''),
    [reason, setReason] = useState(''),
    [assessment, setAssessment] = useState('insufficient');
  return (
    <details className="library-actions">
      <summary>后续论文全文证据对照</summary>
      <p>将后续论文导入并完成本地结构解析后，选择原研究与后续研究中的证据，保存人工核对结论。</p>
      <EvidencePicker label="原研究" docs={docs} change={setOriginal} />
      <EvidencePicker label="后续研究" docs={docs} change={setFollowup} />
      <label>
        要核对的主张
        <textarea value={question} maxLength={2000} onChange={(e) => setQuestion(e.target.value)} />
      </label>
      <label>
        对主张的判断
        <select value={assessment} onChange={(e) => setAssessment(e.target.value)}>
          <option value="insufficient">现有证据不足</option>
          <option value="supported">所选证据支持</option>
          <option value="partly_supported">部分支持／条件不同</option>
          <option value="contradicted">存在反例或冲突</option>
        </select>
      </label>
      <label>
        比较依据（方法、假设、实验与时间）
        <textarea value={reason} maxLength={4000} onChange={(e) => setReason(e.target.value)} />
      </label>
      <button
        disabled={
          disabled || !original || !followup || reason.trim().length < 3 || question.trim().length < 3
        }
        onClick={() =>
          void submit({
            original_evidence: original,
            followup_evidence: followup,
            question,
            reason,
            assessment,
          })
        }
      >
        保存可溯源对照产物
      </button>
    </details>
  );
}
