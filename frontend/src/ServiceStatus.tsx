import { useEffect, useState } from "react";
type Status = {
  mode: string;
  shared_pool: {
    limit: string;
    spent: string;
    reserved: string;
    available: string;
    blocked: boolean;
  } | null;
  own_call_states: Record<string, number>;
};
export default function ServiceStatus({
  userId,
}: {
  userId: string | undefined;
}) {
  const [status, setStatus] = useState<Status | null>(null),
    [error, setError] = useState("");
  useEffect(() => {
    let cancelled = false;
    setStatus(null);
    setError("");
    if (!userId) return;
    async function load() {
      try {
        const r = await fetch("/api/v1/system/external-services");
        if (!r.ok) throw new Error("预算状态暂不可用");
        const data = await r.json();
        if (!cancelled) {
          setStatus(data);
          setError("");
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
        <span className="outline-tag">离线契约 · 未联调</span>
      </div>
      <p>
        外部模型服务尚未启用，不会上传文件或发起研究问答请求。这里显示本地预算，不代表供应商余额或免费额度。
      </p>
      {error && <p role="status">{error}</p>}
      {!userId ? (
        <p>登录后可查看项目预算与自己的调用状态。</p>
      ) : status?.shared_pool ? (
        <>
          <div className="status-grid">
            {[
              ["项目共享额度", status.shared_pool.limit],
              ["已记账", status.shared_pool.spent],
              ["占用预留", status.shared_pool.reserved],
              ["可准入余额", status.shared_pool.available],
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
            {status.own_call_states.sent ?? 0} ·{" "}
            {status.shared_pool.blocked
              ? "预算已阻止新调用"
              : "预算准入机制已建立，真实调用仍关闭"}
          </p>
        </>
      ) : (
        <p>正在读取预算状态…</p>
      )}
    </section>
  );
}
