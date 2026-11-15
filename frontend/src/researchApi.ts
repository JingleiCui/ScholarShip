const messages: Record<string, string> = {
  embedding_profile_requires_rebuild: '当前向量模型与索引不匹配，请先重建所选文献索引。',
  version_not_indexed_in_project: '所选文献尚未建立索引，请先建立索引。',
  ready_native_parse_required: '该文献尚无已完成的深解析；导入 PDF 后的本地预览不能代替 OCR。',
  matching_ready_generation_required: '索引尚未完成校验，请等待索引任务完成。',
  mixed_embedding_profiles: '所选文献使用不同向量配置，请用同一配置重建后再一起提问。',
  question_required: '请输入研究问题。',
};
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求未完成，请重试。';
}
export async function api(path: string, init?: RequestInit) {
  const csrf = document.cookie.split('; ').find(x => x.startsWith('ss_csrf='))?.split('=')[1] ?? '';
  const r = await fetch('/api/v1' + path, { ...init, headers: { 'X-CSRF-Token': csrf, ...init?.headers } });
  if (!r.ok) {
    let detail: unknown;
    try { detail = (await r.json()).detail; } catch { /* Do not display raw proxy responses. */ }
    const message = r.status === 401 ? '会话已过期，请重新登录。' : r.status === 403 ? '当前账号没有此操作权限，或会话校验已过期。' : typeof detail === 'string' ? messages[detail] ?? (/^[\u3400-\u9fff]/.test(detail) ? detail : `请求未完成（${r.status}）。`) : `请求未完成（${r.status}）。`;
    throw new Error(message);
  }
  return r.json();
}
