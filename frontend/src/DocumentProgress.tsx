import { useEffect, useState } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import useExecution from './useExecution';

type Job = { id: string; status: string; error?: { code: string; contract_code?: string } };
export type Preparation = {
  version_id: string;
  preparation: Job | null;
  index: Job | null;
  parse_ready: boolean;
  index_ready: boolean;
  stage: string | null;
  detail: { message?: string; completed?: number; total?: number } | null;
  pages: number | null;
  chunks: number | null;
  extras: (Job & { operation: string })[];
  enrichment_error?: string;
};
const STEPS = ['PDF 已保存', '解析 / OCR', '切片', '向量化', '索引校验', '可以提问'];
export function preparationStep(p: Preparation): number {
  if (p.index_ready) return 5;
  if (p.stage === 'parse') return 1;
  if (p.stage === 'index_verification') return 4;
  if (p.stage === 'embedding') return 3;
  if (p.stage === 'chunking' || p.stage === 'index_queued' || p.parse_ready) return 2;
  return 1;
}
export default function DocumentProgress({
  project,
  versions,
  role,
  compatible = true,
}: {
  project: string;
  versions: string[];
  role?: string;
  compatible?: boolean;
}) {
  const [rows, setRows] = useState<Preparation[]>([]),
    [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const execution = useExecution(project);
  const scope = [...versions].sort().join(',');
  useEffect(() => {
    let stopped = false,
      fetching = false;
    setRows([]);
    setError('');
    async function load() {
      if (fetching) return;
      fetching = true;
      try {
        const next = await Promise.all(versions.map((id) => api(`/research/versions/${id}/preparation`)));
        if (!stopped) {
          setRows(next);
          setError('');
        }
      } catch (e) {
        if (!stopped) setError(errorMessage(e));
      } finally {
        fetching = false;
      }
    }
    void load();
    const timer = setInterval(() => void load(), 1800);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [project, scope]);
  async function resume(version: string) {
    setBusy(true);
    setError('');
    try {
      await api(`/research/versions/${version}/prepare`, {
        method: 'POST',
        headers: { 'Idempotency-Key': crypto.randomUUID() },
      });
      const next = await api(`/research/versions/${version}/preparation`);
      setRows((old) => old.map((p) => (p.version_id === version ? next : p)));
    } catch (e) {
      setError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  if (!versions.length) return null;
  return (
    <section className="preparation-progress" aria-label="论文自动处理进度">
      <div className="section-top">
        <h3>论文准备进度</h3>
        <small>后台自动处理，可离开此页</small>
      </div>
      {!rows.length && !error && <p role="status">正在读取实际处理进度…</p>}
      {rows.map((p, i) => {
        const step = preparationStep(p);
        const job = p.index && p.preparation?.status === 'succeeded' ? p.index : (p.preparation ?? p.index);
        const pending = job && ['queued', 'running'].includes(job.status);
        const failed = job && ['failed', 'cancelled', 'waiting_user'].includes(job.status);
        const waiting = job?.status === 'waiting_user';
        const indexed = p.index_ready && compatible;
        const ready = indexed && execution.localAvailable(['embedding', 'rerank', 'chat']);
        return (
          <div key={p.version_id} className="preparation-document">
            {rows.length > 1 && <strong>文献 {i + 1}</strong>}
            <ol className="preparation-steps" aria-label="处理步骤">
              {STEPS.map((label, n) => (
                <li
                  key={label}
                  className={n < step || ready ? 'done' : n === step ? 'current' : ''}
                  aria-current={n === step && !ready ? 'step' : undefined}
                >
                  <span aria-hidden="true">{n < step || ready ? '✓' : n + 1}</span>
                  {label}
                </li>
              ))}
            </ol>
            <p role="status">
              {ready
                ? `原文索引已校验${p.chunks != null ? ` · ${p.chunks} 个片段` : ''}，可以开始提问。`
                : indexed
                  ? '索引已准备，正在核对或等待本地模型服务恢复。'
                  : failed
                    ? `${waiting ? '处理已暂停，需核对后继续' : '本次处理未完成'}：${job.error ? runErrorMessage(job.error) : runStatus(job.status)}`
                    : pending
                      ? (p.detail?.message ?? (job.status === 'queued' ? '等待后台处理…' : '正在处理…'))
                      : p.index_ready
                        ? '已有索引与当前模型不匹配，需要重新准备。'
                        : p.parse_ready
                          ? '原文已解析，尚未建立问答索引。'
                          : '等待开始自动解析。'}
            </p>
            {pending && p.stage === 'embedding' && p.detail?.total && (
              <progress aria-label="片段向量化进度" max={p.detail.total} value={p.detail.completed ?? 0} />
            )}
            {!indexed && !pending && !waiting && role === 'researcher' && (
              <button disabled={busy} onClick={() => void resume(p.version_id)}>
                {failed ? '重新准备问答' : '继续自动准备'}
              </button>
            )}
            {!!p.extras.length && (
              <p className="preparation-extras">
                中文翻译：
                {p.extras.filter((t) => t.operation === 'translate' && t.status === 'succeeded').length}/
                {p.extras.filter((t) => t.operation === 'translate').length} 步完成
                {' · '}图内识别：
                {p.extras.filter((t) => t.operation === 'figure' && t.status === 'succeeded').length}/
                {p.extras.filter((t) => t.operation === 'figure').length} 步完成
                {p.extras.some((t) => ['failed', 'waiting_user', 'cancelled'].includes(t.status))
                  ? ' · 有步骤未完成，请在阅读与解析中查看'
                  : ''}
                。这些步骤不阻塞原文问答。
              </p>
            )}
            {p.enrichment_error && <p className="error">{p.enrichment_error}</p>}
          </div>
        );
      })}
      {execution.checked && !execution.localAvailable(['embedding', 'rerank', 'chat']) && (
        <p className="error">
          本地模型条件未满足：
          {!execution.configuration?.configured_flags?.allow_local ? '本地推理未启用；' : ''}
          {(['embedding', 'rerank', 'chat'] as const)
            .filter((r) => !execution.configuration?.local_ready?.[r])
            .map((r) => ({ embedding: '向量模型', rerank: '重排服务', chat: '回答模型' })[r])
            .join('、')}
          未就绪。请在“设置”核对本地模型配置及服务。
        </p>
      )}
      {error && (
        <p role="alert" className="error">
          进度读取或提交失败：{error}。刷新只读取进度，不会重复运行模型。
        </p>
      )}
    </section>
  );
}
