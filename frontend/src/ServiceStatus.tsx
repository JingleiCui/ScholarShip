import { useEffect, useState } from 'react';
type Status = {
  mode: string;
  production_policies?: {
    role: string;
    enabled: boolean;
    request_limit: number;
    requests_used: number;
    unexpired: boolean;
  }[];
  shared_pool: {
    limit: string;
    spent: string;
    reserved: string;
    available: string;
    blocked: boolean;
  } | null;
  own_call_states: Record<string, number>;
};
export default function ServiceStatus({ userId }: { userId: string | undefined }) {
  const [status, setStatus] = useState<Status | null>(null),
    [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setError('');
    if (!userId) return;
    async function load() {
      try {
        const r = await fetch('/api/v1/system/external-services');
        if (!r.ok) throw new Error('预算状态暂不可用');
        const data = await r.json();
        if (!cancelled) {
          setStatus(data);
          setError('');
        }
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    void load();
    const t = setInterval(() => void load(), 15000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [userId]);
  return (
    <section className="task-panel" style={{ marginBottom: 24 }}>
      <div className="section-top">
        <h2>外部服务与统一预算</h2>
        <span className="outline-tag">
          {status?.mode === 'scoped_cloud_approval' ? '有限云端验收' : '模型调用已关闭'}
        </span>
      </div>
      <p>云端请求须按已批准的文献、模型、期限和次数准入。这里显示本地记账，不代表供应商余额或免费额度。</p>
      {error && <p role="status">{error}</p>}
      {status?.production_policies?.map((p, i) => (
        <p key={i}>
          云端 {p.role}：{p.enabled && p.unexpired ? '限额内可准入' : '暂停或到期'} · 已占用 {p.requests_used}
          /{p.request_limit}
        </p>
      ))}
      {!userId ? (
        <p>登录后可查看项目预算与自己的调用状态。</p>
      ) : status?.shared_pool ? (
        <>
          <div className="status-grid">
            {[
              ['项目共享额度', status.shared_pool.limit],
              ['已记账', status.shared_pool.spent],
              ['占用预留', status.shared_pool.reserved],
              ['可准入余额', status.shared_pool.available],
            ].map(([label, value]) => (
              <div className="service" key={label}>
                <div>
                  <strong>{label}</strong>
                  <small>¥ {value}</small>
                </div>
              </div>
            ))}
          </div>
          <p style={{ marginTop: 16 }}>
            待核对：{status.own_call_states.unknown ?? 0} · 已开始未完成：
            {status.own_call_states.sent ?? 0} ·{' '}
            {status.shared_pool.blocked ? '预算已阻止新调用' : '共享预算之外，还须通过本次云端批次准入'}
          </p>
        </>
      ) : (
        <p>正在读取预算状态…</p>
      )}
    </section>
  );
}
