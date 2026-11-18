import { useEffect, useState } from 'react';
import { api, errorMessage, runStatus } from './researchApi';
import TaskEvents from './TaskEvents';
export default function SystemProbe({ project, role }: { project: string; role?: string }) {
  const [run, setRun] = useState<{ id: string; status: string } | null>(null),
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
    }, 2000);
    return () => {
      stopped = true;
      clearInterval(timer);
    };
  }, [run?.id, run?.status]);
  return (
    <section className="task-panel">
      <h2>基础设施探针</h2>
      <p>只检查数据库、队列和持久任务，不回答科研问题、不调用模型。</p>
      <button
        disabled={
          busy || !project || role !== 'researcher' || (!!run && ['queued', 'running'].includes(run.status))
        }
        onClick={() => {
          setBusy(true);
          setError('');
          void api('/runs', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
            body: JSON.stringify({ project_id: project }),
          })
            .then(setRun)
            .catch((e) => setError(errorMessage(e)))
            .finally(() => setBusy(false));
        }}
      >
        运行本地探针
      </button>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {run && (
        <>
          <p>{runStatus(run.status)}</p>
          <TaskEvents run={run.id} />
        </>
      )}
    </section>
  );
}
