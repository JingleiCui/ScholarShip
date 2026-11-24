import { useEffect, useState } from 'react';
type ModelRole = {
  provider: string;
  model: string;
  deployment?: string;
  credential_required: boolean;
  credential_present: boolean;
  verification_state: string;
  dimensions: number | null;
  dimensions_state: string;
};
const MODE_PRESETS = { local: 'local', cloud: 'free_gemma' } as const;
type Mode = keyof typeof MODE_PRESETS;
const MODE_LABELS: Record<Mode | 'custom', string> = {
  local: '本地模型模式',
  cloud: '云端模型模式',
  custom: '自定义组合',
};
function activeMode(state: Configuration | null): Mode | 'custom' {
  if (!state) return 'custom';
  if (Object.keys(state.active_overrides ?? {}).length) return 'custom';
  if (state.selected_preset === MODE_PRESETS.local) return 'local';
  if (['free_gemma', 'free_api'].includes(state.selected_preset)) return 'cloud';
  return 'custom';
}
type Configuration = {
  production_policies?: {
    role: string;
    model: string;
    enabled: boolean;
    request_limit: number;
    requests_used: number;
    ends_at: string;
  }[];
  selected_preset: string;
  active_overrides?: Record<string, string>;
  available_presets: string[];
  roles: Record<string, ModelRole>;
  fingerprint: string;
  requires_reindex: boolean;
  bounded_validations?: {
    state: string;
    max_requests: number;
    used_requests: number;
    stop_code: string | null;
  }[];
};
async function read(path: string, init?: RequestInit) {
  const r = await fetch('/api/v1' + path, init);
  if (!r.ok) throw new Error(`模型配置请求失败 ${r.status}`);
  return r.json();
}
export default function ModelConfiguration({ project, role }: { project: string; role?: string }) {
  const [state, setState] = useState<Configuration | null>(null),
    [preset, setPreset] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false),
    [note, setNote] = useState('');
  useEffect(() => {
    let stop = false;
    setState(null);
    setError('');
    setNote('');
    if (project)
      void read(`/projects/${project}/model-configuration`)
        .then((s: Configuration) => {
          if (!stop) {
            setState(s);
            setPreset(s.selected_preset);
          }
        })
        .catch((e) => {
          if (!stop) setError(String(e));
        });
    return () => {
      stop = true;
    };
  }, [project]);
  function csrfToken() {
    return (
      document.cookie
        .split('; ')
        .find((x) => x.startsWith('ss_csrf='))
        ?.split('=')[1] ?? ''
    );
  }
  async function applySwitch(target: string, overrides: Record<string, string>, notePrefix: string) {
    setBusy(true);
    setError('');
    setNote('');
    try {
      // Read the current fingerprint immediately before writing so a concurrent
      // edit cannot be silently overwritten.
      const fresh: Configuration = await read(`/projects/${project}/model-configuration`);
      const result = await read(`/projects/${project}/model-configuration`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrfToken() },
        body: JSON.stringify({ preset: target, overrides, expected_fingerprint: fresh.fingerprint }),
      });
      const next: Configuration = await read(`/projects/${project}/model-configuration`);
      setState(next);
      setPreset(next.selected_preset);
      const parseNote = result.requires_new_parse ? '解析模型变化只影响新解析任务，旧解析修订保留。' : '';
      setNote(
        (result.requires_reindex
          ? `${notePrefix}向量索引需重建，当前活动索引保留；`
          : `${notePrefix}仅影响后续任务；`) + `${parseNote}未启用推理。`,
      );
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  async function switchMode(mode: Mode) {
    if (!state) return;
    // A clean mode switch clears role overrides, otherwise an override could
    // keep one role on the other deployment after the preset changes.
    await applySwitch(MODE_PRESETS[mode], {}, `已切换到${MODE_LABELS[mode]}，`);
  }
  async function save() {
    if (!state) return;
    await applySwitch(preset, state.active_overrides ?? {}, '配置已保存，');
  }
  const names: Record<string, string> = {
    chat: '聊天 / 证据问答 / 摘要',
    translation: '翻译',
    embedding: '向量化',
    rerank: '重排',
    parser: 'PDF / OCR',
  };
  const stopReasons: Record<string, string> = {
    chatanywhere_preflight_403: '历史额度查询返回 403；不代表聊天接口不可用。',
    provider_transport_outcome_unknown: '连接或回执异常，结果未知；已停止，未自动重发。',
    rate_limited: '免费接口返回 429 限流；已停止，未自动重试。',
  };
  return (
    <section className="task-panel" style={{ marginBottom: 24 }}>
      <div className="section-top">
        <h2>统一模型配置</h2>
        <span className="outline-tag">配置切换与调用准入分开</span>
      </div>
      <p>配置与实际可用性分开显示。填写 Key 不代表权限或免费额度已验证；合成演示固定使用 mock。</p>
      {!project ? (
        <p>登录并选择项目后查看。</p>
      ) : state ? (
        <>
          <div
            className="mode-switch"
            role="group"
            aria-label="模型运行模式快速切换"
            style={{
              display: 'flex',
              alignItems: 'center',
              flexWrap: 'wrap',
              gap: '4px 10px',
              margin: '6px 0 12px',
            }}
          >
            <span
              className="mode-switch-label"
              style={{ fontSize: 13, fontWeight: 600, color: '#19345c', marginRight: 6 }}
            >
              运行模式
            </span>
            <button
              type="button"
              className={activeMode(state) === 'local' ? 'primary' : ''}
              aria-pressed={activeMode(state) === 'local'}
              title="本地模型组：Ollama 文本/向量/重排，解析仍走 MinerU 云端"
              disabled={busy || role !== 'researcher'}
              onClick={() => void switchMode('local')}
            >
              本地模型
            </button>
            <button
              type="button"
              className={activeMode(state) === 'cloud' ? 'primary' : ''}
              aria-pressed={activeMode(state) === 'cloud'}
              title="云端模型组：OpenRouter Gemma 与 Nemotron 向量/重排，MinerU 云解析"
              disabled={busy || role !== 'researcher'}
              onClick={() => void switchMode('cloud')}
            >
              云端模型
            </button>
            <span className="outline-tag">{MODE_LABELS[activeMode(state)]}</span>
          </div>
          <p style={{ margin: '0 0 16px', fontSize: 12 }}>
            一键切换即时生效于新建任务，无需重启服务；运行中/排队中的任务保留原配置快照。切换仅保存配置，不启用推理或额度。
            {activeMode(state) === 'custom' ? ' 当前为自定义组合；点击任一模式将清除角色级覆盖。' : ''}
          </p>
          <div className="probe-controls">
            <select
              aria-label="模型配置组"
              value={preset}
              disabled={busy}
              onChange={(e) => setPreset(e.target.value)}
            >
              {state.available_presets.map((p) => (
                <option key={p} value={p}>
                  {p}
                </option>
              ))}
            </select>
            <button disabled={busy || role !== 'researcher'} onClick={() => void save()}>
              {busy ? '保存中…' : '应用配置组（高级）'}
            </button>
          </div>
          {Object.entries(state.roles).map(([role, m]) => (
            <div className="service" key={role} style={{ marginTop: 10, overflowWrap: 'anywhere' }}>
              <div>
                <strong>{names[role] ?? role}</strong>
                <small>
                  {m.deployment === 'local'
                    ? '本地'
                    : m.deployment === 'cloud'
                      ? '云端'
                      : m.deployment === 'simulated'
                        ? '模拟'
                        : m.provider}{' '}
                  · {m.provider} · {m.model}
                </small>
                <small>
                  {m.credential_required
                    ? m.credential_present
                      ? 'Key 已填写'
                      : 'Key 未填写'
                    : '无需云端 Key'}{' '}
                  ·{' '}
                  {m.verification_state === 'synthetic_only'
                    ? '仅合成验证'
                    : m.verification_state === 'received_valid_response'
                      ? '历史有效回执；当前额度仍需准入'
                      : m.verification_state === 'last_attempt_not_successful'
                        ? '最近一次请求未成功；当前可用性未确认'
                        : '未验证服务、模型权限或安装状态'}
                  {role === 'embedding' ? ` · 维度：${m.dimensions ?? '待首个有效响应确认'}` : ''}
                </small>
              </div>
            </div>
          ))}
          {state.production_policies?.map((p, i) => (
            <p key={'prod' + i}>
              正式验收 · {names[p.role]} · {p.model} ·{' '}
              {p.enabled && new Date(p.ends_at) > new Date() ? '限额准入' : '暂停或到期'} · {p.requests_used}/
              {p.request_limit}
            </p>
          ))}
          {state.bounded_validations?.map((v, i) => (
            <p role="status" key={i}>
              受控接口授权批次：
              {{ active: '待执行', stopped: '已停止', completed: '已完成', expired: '已过期' }[v.state] ??
                v.state}{' '}
              · 已占用名额 {v.used_requests}/{v.max_requests} ·{' '}
              {v.stop_code ? (stopReasons[v.stop_code] ?? v.stop_code) : '只允许已授权的合成文本范围。'}
            </p>
          ))}
          {state.requires_reindex && <p>更换向量模型后需重建索引，完成前保留原索引。</p>}
        </>
      ) : (
        <p>正在读取配置…</p>
      )}
      {note && <p role="status">{note}</p>}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
    </section>
  );
}
