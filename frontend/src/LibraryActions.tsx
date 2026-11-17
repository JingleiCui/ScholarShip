import { useEffect, useState } from 'react';
import { api, errorMessage } from './researchApi';
type Paper = { id: string; version_id: string; title: string };
type Comparison = {
  line_similarity: number;
  truncated: boolean;
  notice: string;
  changes: {
    kind: string;
    left: { text: string; page: number }[];
    right: { text: string; page: number }[];
  }[];
};
export default function LibraryActions({
  project,
  canWrite,
  selected,
  papers,
  refresh,
}: {
  project: string;
  canWrite: boolean;
  selected: Paper | null;
  papers: Paper[];
  refresh: () => Promise<void>;
}) {
  const [candidates, setCandidates] = useState<{
    notice: string;
    versions_checked: number;
    truncated: boolean;
    candidates: {
      left_version: string;
      right_version: string;
      left_title: string;
      right_title: string;
      similarity: number;
      advice: string;
    }[];
  } | null>(null);
  const [title, setTitle] = useState(''),
    [other, setOther] = useState(''),
    [archived, setArchived] = useState<{ id: string; title: string }[]>([]),
    [diff, setDiff] = useState<Comparison | null>(null),
    [error, setError] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  useEffect(() => {
    setTitle(selected?.title ?? '');
    setOther('');
    setDiff(null);
    setError('');
    setNotice('');
  }, [selected]);
  async function action(fn: () => Promise<void>) {
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
  async function edit(id: string, body: object) {
    await api(`/papers/${id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    await refresh();
    setNotice('已保存，原文件及历史证据保留。');
  }
  return (
    <details className="library-actions">
      <summary>文献整理、版本比较与归档</summary>
      {selected && (
        <>
          <label>
            显示标题
            <input value={title} onChange={(e) => setTitle(e.target.value)} maxLength={2000} />
          </label>
          <button
            disabled={busy || !canWrite || !title.trim()}
            onClick={() => void action(() => edit(selected.id, { title }))}
          >
            保存标题
          </button>
          <button
            disabled={busy || !canWrite}
            onClick={() => void action(() => edit(selected.id, { current_version_id: selected.version_id }))}
          >
            将此版本设为首选
          </button>
          <button
            disabled={busy || !canWrite}
            onClick={() => void action(() => edit(selected.id, { archived: true }))}
          >
            归档此作品（可恢复）
          </button>
          <label>
            与另一版本比较
            <select value={other} onChange={(e) => setOther(e.target.value)}>
              <option value="">选择文献版本</option>
              {papers
                .filter((p) => p.version_id !== selected.version_id)
                .map((p) => (
                  <option key={p.version_id} value={p.version_id}>
                    {p.title} · {p.version_id.slice(0, 8)}
                  </option>
                ))}
            </select>
          </label>
          <button
            disabled={busy || !other}
            onClick={() =>
              void action(async () =>
                setDiff(await api(`/paper-versions/${selected.version_id}/compare/${other}`)),
              )
            }
          >
            比较已解析文本
          </button>
        </>
      )}
      {diff && (
        <section>
          <p>
            {diff.notice} 文本行相似度 {(diff.line_similarity * 100).toFixed(1)}%。
            {diff.truncated ? '已达到比较上限，仅显示部分差异。' : ''}
          </p>
          {!diff.changes.length && <p>当前比较范围内没有文本行差异。</p>}
          {diff.changes.map((c, i) => (
            <details key={i}>
              <summary>
                差异 {i + 1} · {c.kind === 'insert' ? '新增' : c.kind === 'delete' ? '删除' : '修改'}
              </summary>
              <div className="comparison-grid">
                <div>
                  <h4>当前版本</h4>
                  {c.left.map((l, j) => (
                    <p key={j}>
                      第 {l.page} 页：{l.text}
                    </p>
                  ))}
                </div>
                <div>
                  <h4>比较版本</h4>
                  {c.right.map((l, j) => (
                    <p key={j}>
                      第 {l.page} 页：{l.text}
                    </p>
                  ))}
                </div>
              </div>
            </details>
          ))}
        </section>
      )}
      <button
        disabled={busy}
        onClick={() =>
          void action(async () => setCandidates(await api(`/projects/${project}/content-candidates`)))
        }
      >
        检查正文／OCR 相似候选
      </button>
      {candidates && (
        <section>
          <p>
            {candidates.notice} 已检查 {candidates.versions_checked} 个版本。
            {candidates.truncated ? '达到本次检查上限。' : ''}
          </p>
          {!candidates.candidates.length && (
            <p>当前有文字的解析中未发现达到阈值的候选；扫描件需先完成 OCR。</p>
          )}
          {candidates.candidates.map((c) => (
            <article key={`${c.left_version}:${c.right_version}`}>
              <p>
                {c.left_title} ↔ {c.right_title} · 词组相似度 {(c.similarity * 100).toFixed(1)}%
              </p>
              <p>{c.advice}</p>
            </article>
          ))}
        </section>
      )}
      <button
        disabled={busy}
        onClick={() =>
          void action(async () => setArchived(await api(`/projects/${project}/archived-papers`)))
        }
      >
        查看已归档作品
      </button>
      {archived.map((p) => (
        <p key={p.id}>
          {p.title}{' '}
          <button
            disabled={busy || !canWrite}
            onClick={() =>
              void action(async () => {
                await edit(p.id, { archived: false });
                setArchived(archived.filter((x) => x.id !== p.id));
              })
            }
          >
            恢复
          </button>
        </p>
      ))}
      {(error || notice) && <p role={error ? 'alert' : 'status'}>{error || notice}</p>}
    </details>
  );
}
