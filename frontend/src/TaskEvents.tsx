import { useEffect, useState } from 'react';
export default function TaskEvents({ run }: { run: string }) {
  const [open, setOpen] = useState(false),
    [lines, setLines] = useState<{ id: string; text: string }[]>([]),
    [state, setState] = useState('');
  useEffect(() => {
    if (!open) return;
    const source = new EventSource(`/api/v1/runs/${run}/events`);
    setState('正在连接任务事件…');
    const terminal = new Set(['succeeded', 'failed', 'cancelled', 'partially_succeeded']);
    const receive = (event: Event) => {
      const e = event as MessageEvent;
      try {
        const p = JSON.parse(e.data);
        setLines((old) =>
          old.some((x) => x.id === e.lastEventId)
            ? old
            : [...old, { id: e.lastEventId, text: p.message ?? e.type }].slice(-100),
        );
        setState('已连接');
        if (terminal.has(e.type) || e.type === 'waiting_user') {
          source.close();
          setState(e.type === 'waiting_user' ? '任务等待人工处理' : '任务已结束');
        }
      } catch {
        setState('收到无法解析的事件');
      }
    };
    for (const name of [
      'queued',
      'running',
      'progress',
      'succeeded',
      'failed',
      'cancelled',
      'cancel_requested',
      'partially_succeeded',
      'waiting_user',
    ])
      source.addEventListener(name, receive);
    source.onerror = () => setState('连接中断，正在按事件序号恢复…');
    return () => source.close();
  }, [open, run]);
  return (
    <details onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>实时任务步骤</summary>
      <p role="status">{state}</p>
      <ol>
        {lines.map((l) => (
          <li key={l.id}>{l.text}</li>
        ))}
      </ol>
    </details>
  );
}
