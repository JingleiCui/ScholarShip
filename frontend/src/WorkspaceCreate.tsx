import { useState } from 'react';
import { api, errorMessage } from './researchApi';
export default function WorkspaceCreate({
  onCreated,
  initialOpen = false,
}: {
  initialOpen?: boolean;
  onCreated: (project: { id: string; name: string; description: string }) => void;
}) {
  const [open, setOpen] = useState(initialOpen),
    [name, setName] = useState(''),
    [busy, setBusy] = useState(false),
    [error, setError] = useState('');
  return (
    <div className="workspace-create">
      <button onClick={() => setOpen(!open)} aria-expanded={open}>
        新建空间
      </button>
      {open && (
        <form
          className="inline-editor"
          onSubmit={(e) => {
            e.preventDefault();
            setBusy(true);
            setError('');
            void api('/projects', {
              method: 'POST',
              headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ name }),
            })
              .then((p) => {
                onCreated(p);
                setOpen(false);
                setName('');
              })
              .catch((e) => setError(errorMessage(e)))
              .finally(() => setBusy(false));
          }}
        >
          <label>
            空间名称
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={120}
              autoFocus
              required
            />
          </label>
          <small>独立文献与任务，默认使用不联网的模拟配置。</small>
          <button disabled={busy || !name.trim()}>{busy ? '正在创建…' : '创建并进入'}</button>
          {error && <p role="alert">{error}</p>}
        </form>
      )}
    </div>
  );
}
