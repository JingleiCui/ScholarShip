export type Policy = {
  role: string;
  provider?: string;
  model?: string;
  enabled: boolean;
  request_limit: number;
  requests_used: number;
  ends_at: string;
};
export type ExecutionConfiguration = {
  roles: Record<string, { provider: string; model: string }>;
  production_policies?: Policy[];
  local_ready?: Record<string, boolean>;
  configured_flags?: { allow_local: boolean };
};
export type ExternalState = {
  requested_external_flag: boolean;
  shared_pool?: { live_enabled: boolean; blocked: boolean; available: string; period_end?: string } | null;
};
// This only explains the current UI state. Server-side source, expiry and quota checks remain authoritative.
export function cloudAvailable(
  configuration: ExecutionConfiguration | null,
  external: ExternalState | null,
  roles: string[],
  now = Date.now(),
): boolean {
  const pool = external?.shared_pool;
  if (
    !configuration ||
    !external?.requested_external_flag ||
    !pool?.live_enabled ||
    pool.blocked ||
    !(Number(pool.available) > 0) ||
    (pool.period_end && Date.parse(pool.period_end) <= now)
  )
    return false;
  return roles.every((role) => {
    const model = configuration.roles[role];
    return (
      !!model &&
      model.provider !== 'mock' &&
      (configuration.production_policies ?? []).some(
        (p) =>
          p.role === role &&
          p.enabled &&
          Date.parse(p.ends_at) > now &&
          p.requests_used < p.request_limit &&
          p.provider === model.provider &&
          p.model === model.model,
      )
    );
  });
}

export function localAvailable(configuration: ExecutionConfiguration | null, roles: string[]): boolean {
  return (
    !!configuration?.configured_flags?.allow_local &&
    roles.every((role) => configuration?.local_ready?.[role] === true)
  );
}
