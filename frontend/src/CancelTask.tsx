import { useState } from 'react';
import { api, errorMessage } from './researchApi';
export default function CancelTask({ id, status }: { id: string; status: string }) {
  const [notice, setNotice] = useState(''),
    [busy, setBusy] = useState(false);
  if (!['queued', 'running', 'waiting_user'].includes(status)) return null;
  return (
    <div>
      <button
        disabled={busy || !!notice}
        onClick={() => {
          setBusy(true);
          void api(`/runs/${id}/cancel`, { method: 'POST' })
            .then((r) =>
              setNotice(r.status === 'cancelled' ? '已取消。' : '已请求停止后续步骤；当前调用回执将保留。'),
            )
            .catch((e) => setNotice(errorMessage(e)))
            .finally(() => setBusy(false));
        }}
      >
        取消后续执行
      </button>
      {notice && <p role="status">{notice}</p>}
    </div>
  );
}
