import { useEffect, useState } from 'react';
import CancelTask from './CancelTask';
import { api, errorMessage } from './researchApi';
type Description = {
  figure_type: string;
  summary: string;
  visible_text: string[];
  uncertainties: string[];
  nodes: { id: string; text: string; bbox: number[] }[];
  edges: { source: string; target: string; label: string; direction: string }[];
};
type Result = {
  id: string;
  status: string;
  review?: { assessment: string; reason: string };
  result?: { description?: Description; artifact_version_id?: string };
};
export default function FigureReader({ blockId }: { blockId: string }) {
  const [rows, setRows] = useState<Result[]>([]);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    let alive = true;
    async function load() {
      try {
        const r = await api(`/studio/figures/${blockId}/results`);
        if (alive) setRows(r);
      } catch (e) {
        if (alive) setError(errorMessage(e));
      }
    }
    void load();
    const timer = setInterval(() => void load(), 10000);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, [blockId]);
  async function submit() {
    setBusy(true);
    setError('');
    try {
      await api(`/studio/figures/${blockId}/tasks`, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      });
      setRows(await api(`/studio/figures/${blockId}/results`));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  const pending = rows.some((r) => ['queued', 'running', 'waiting_user'].includes(r.status));
  return (
    <details>
      <summary>识别图内文字与结构</summary>
      <p>
        识别节点、连线和方向。调用会发送这张图片及图注到配置的云端视觉模型；需支持图片输入并具备调用权限。结果需对照原图核验。
      </p>
      <button disabled={busy || pending} onClick={() => void submit()}>
        {busy ? '正在提交' : pending ? '任务处理中或等待核对' : '提交图内识别'}
      </button>
      {error && <p role="alert">{error}</p>}
      {rows.map((r) => (
        <article key={r.id}>
          <p>
            状态：{r.status} · 内容核验：
            {r.review?.assessment === 'rejected'
              ? '未通过'
              : r.review?.assessment === 'accepted'
                ? '人工确认'
                : '待核验'}
          </p>
          {r.review && <p role="note">{r.review.reason}</p>}
          <CancelTask id={r.id} status={r.status} />
          {r.result?.artifact_version_id && <FigureReview id={r.result.artifact_version_id} />}
          {r.result?.description && (
            <>
              <NodeOverlay blockId={blockId} description={r.result.description} />
              {r.result.artifact_version_id && (
                <FigureEditor id={r.result.artifact_version_id} description={r.result.description} />
              )}
              <p>{r.result.description.summary}</p>
              <p>图内文字：{r.result.description.visible_text.join('；')}</p>
              <ul>
                {r.result.description.nodes.map((n) => (
                  <li key={n.id}>
                    {n.id}：{n.text}
                  </li>
                ))}
              </ul>
              <ul>
                {r.result.description.edges.map((e, i) => (
                  <li key={i}>
                    {e.source} {e.direction === 'directed' ? '→' : e.direction === 'undirected' ? '—' : '?'}{' '}
                    {e.target} {e.label}
                  </li>
                ))}
              </ul>
              {r.result.description.uncertainties.map((t, i) => (
                <p key={i}>待核对：{t}</p>
              ))}
              {r.result.artifact_version_id && (
                <a href={`/api/v1/studio/artifacts/${r.result.artifact_version_id}`}>下载识别结果与来源</a>
              )}
            </>
          )}
        </article>
      ))}
    </details>
  );
}

function FigureReview({ id }: { id: string }) {
  const [reason, setReason] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  async function review(assessment: string) {
    setBusy(true);
    try {
      await api(`/studio/figure-artifacts/${id}/reviews`, {
        method: 'POST',
        body: JSON.stringify({ assessment, reason }),
      });
      setNotice('核验记录已保存，列表将自动更新。');
    } catch (e) {
      setNotice(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>记录原图核验</summary>
      <label>
        核验依据
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
      </label>
      <button disabled={busy || reason.trim().length < 3} onClick={() => void review('accepted')}>
        确认与原图一致
      </button>
      <button disabled={busy || reason.trim().length < 3} onClick={() => void review('rejected')}>
        标记识别有误
      </button>
      {notice && <p role="status">{notice}</p>}
    </details>
  );
}

function NodeOverlay({ blockId, description }: { blockId: string; description: Description }) {
  const [selected, setSelected] = useState('');
  return (
    <details>
      <summary>对照节点位置</summary>
      <div style={{ position: 'relative', width: '100%', maxWidth: 640 }}>
        <img
          src={`/api/v1/research/blocks/${blockId}/asset`}
          alt="用于核对节点位置的原始图片"
          style={{ display: 'block', width: '100%' }}
        />
        {description.nodes
          .filter((n) => n.id === selected)
          .map((n) => (
            <div
              key={n.id}
              aria-hidden="true"
              style={{
                position: 'absolute',
                left: `${n.bbox[0] * 100}%`,
                top: `${n.bbox[1] * 100}%`,
                width: `${(n.bbox[2] - n.bbox[0]) * 100}%`,
                height: `${(n.bbox[3] - n.bbox[1]) * 100}%`,
                border: '3px solid #2563eb',
                background: 'rgba(37,99,235,.15)',
                pointerEvents: 'none',
              }}
            />
          ))}
      </div>
      <label>
        选择识别节点
        <select value={selected} onChange={(e) => setSelected(e.target.value)}>
          <option value="">隐藏位置框</option>
          {description.nodes.map((n) => (
            <option key={n.id} value={n.id}>
              {n.id} · {n.text}
            </option>
          ))}
        </select>
      </label>
      <p>位置框来自识别结果，需与原图对照核验。</p>
    </details>
  );
}

function FigureEditor({ id, description }: { id: string; description: Description }) {
  const [text, setText] = useState(() => JSON.stringify(description, null, 2));
  const [reason, setReason] = useState(''),
    [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  async function save() {
    setBusy(true);
    try {
      const value = JSON.parse(text);
      await api(`/studio/figure-artifacts/${id}/corrections`, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ description: value, reason }),
      });
      setNotice('修订任务已提交，新版本完成后会出现在列表中。');
    } catch (e) {
      setNotice(e instanceof SyntaxError ? 'JSON 格式不正确，请检查括号和引号。' : errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <details>
      <summary>修订图内识别结果</summary>
      <p>
        修改节点文字、0–1 范围的位置坐标和连线；连线必须引用已有节点
        ID。保存会创建新产物，原结果保留，无模型调用。
      </p>
      <label>
        结构化内容
        <textarea value={text} onChange={(e) => setText(e.target.value)} rows={18} maxLength={80000} />
      </label>
      <label>
        修改依据
        <textarea value={reason} onChange={(e) => setReason(e.target.value)} maxLength={2000} />
      </label>
      <button disabled={busy || reason.trim().length < 3} onClick={() => void save()}>
        保存为新版本
      </button>
      {notice && <p role="status">{notice}</p>}
    </details>
  );
}
