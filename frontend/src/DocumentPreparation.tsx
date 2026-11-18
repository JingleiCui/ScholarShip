import { useEffect, useState } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import TaskEvents from './TaskEvents';
export default function DocumentPreparation({
  project,
  version,
  role,
}: {
  project: string;
  version: string;
  role?: string;
}) {
  const [run, setRun] = useState<{
      id: string;
      status: string;
      error_json?: { code?: string; contract_code?: string };
    } | null>(null),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  useEffect(() => {
    if (!run || !['queued', 'running'].includes(run.status)) return;
    let stopped = false;
    const timer = setInterval(() => {
      void api(`/runs/${run.id}`)
        .then((r) => {
          if (!stopped) setRun(r);
        })
        .catch((e) => {
          if (!stopped) setError(errorMessage(e));
        });
    }, 2500);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [run?.id, run?.status]);
  return (
    <section className="preparation-notice">
      <div>
        <h2>先读原件，需要时再准备问答。</h2>
        <p>当前文献尚未提取文本结构。文本型 PDF 可本地处理，不会上传云端；扫描件仍需获准的 OCR。</p>
      </div>
      <button
        disabled={
          busy || role !== 'researcher' || (!!run && ['queued', 'running', 'succeeded'].includes(run.status))
        }
        onClick={() => {
          setBusy(true);
          setError('');
          void api(`/studio/projects/${project}/tasks`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
            body: JSON.stringify({ operation: 'local_parse', version_id: version }),
          })
            .then(setRun)
            .catch((e) => setError(errorMessage(e)))
            .finally(() => setBusy(false));
        }}
      >
        提取文本结构（本地）
      </button>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {run && (
        <details open>
          <summary>本地准备 · {runStatus(run.status)}</summary>
          {run.error_json && <p className="error">{runErrorMessage(run.error_json)}</p>}
          <TaskEvents run={run.id} />
        </details>
      )}
    </section>
  );
}
