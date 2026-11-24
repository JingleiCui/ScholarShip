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
        <p>当前文献尚未解析。文本页读取文字，扫描页使用本地 OCR，不上传云端；完成后到阅读页核对结构和中英对应。</p>
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
        本地解析 / OCR
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
