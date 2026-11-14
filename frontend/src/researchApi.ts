export async function api(path: string, init?: RequestInit) {
  const csrf = document.cookie.split('; ').find(x => x.startsWith('ss_csrf='))?.split('=')[1] ?? '';
  const r = await fetch('/api/v1' + path, { ...init, headers: { 'X-CSRF-Token': csrf, ...init?.headers } });
  if (!r.ok) throw new Error(`请求失败 ${r.status}：${(await r.text()).slice(0, 160)}`);
  return r.json();
}
