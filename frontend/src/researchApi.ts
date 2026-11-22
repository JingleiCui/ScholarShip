const messages: Record<string, string> = {
  local_provider_required: '当前角色未选择本地模型，请在模型配置中切换本地预设。',
  local_admission_closed: '本地任务准入未通过，请核对任务配置和服务状态。',
  local_binding_changed: '模型配置已变化，请新建任务；原任务和回执保留。',

  cloud_vision_model_required: '请先在项目模型配置中选择支持图片输入的云端聊天模型。',
  cloud_vision_admission_required: '图内识别的云端权限尚未启用，请配置对应数据范围与额度。',
  ready_figure_image_required: '需要已完成深解析并保留图片原件的图块。',
  figure_image_size_limit: '图片超过 4 MB 限制，请先准备合适分辨率的图片解析修订。',

  index_scope_exceeds_run_limit_split_documents:
    '当前文献范围超过单任务请求上限。请逐篇建索引；超长文献需先调整项目的单任务限制并核对总额度。',
  model_request_quota_exhausted: '本任务请求次数已达到上限，已暂停后续执行；已有回执保留。',
  workspace_name_exists: '这个空间名称已存在，请换一个名称。',
  workspace_name_required: '请输入空间名称。',
  parse_required_for_comparison: '请等待两份文献完成文本解析后再比较。',
  comparison_requires_text_or_ocr: '缺少可比较文本，扫描件需要先完成 OCR。',
  protected_translation_literals_changed: '修订改变了公式、代码、链接或标签，请保留这些原文字面量。',
  translation_revision_conflict: '译文版本不匹配，请重新加载后修订。',
  ready_translation_required: '请先选择已完成的翻译版本。',
  embedding_profile_requires_rebuild: '当前向量模型与索引不匹配，请先重建所选文献索引。',
  version_not_indexed_in_project: '所选文献尚未建立索引，请先建立索引。',
  ready_native_parse_required: '该文献尚无已完成的深解析；导入 PDF 后的本地预览不能代替 OCR。',
  matching_ready_generation_required: '索引尚未完成校验，请等待索引任务完成。',
  mixed_embedding_profiles: '所选文献使用不同向量配置，请用同一配置重建后再一起提问。',
  followup_requires_same_source_scope: '续问必须保持相同的文献版本、索引和执行模式；切换文献请开始新问题。',
  followup_conversation_advanced: '该会话已有新回答，请选择最新回答继续追问。',
  followup_parent_unavailable: '上文任务尚未完成或已不可用，请开始新问题。',
  question_required: '请输入研究问题。',
};
export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : '请求未完成，请重试。';
}
export async function api(path: string, init?: RequestInit) {
  const csrf =
    document.cookie
      .split('; ')
      .find((x) => x.startsWith('ss_csrf='))
      ?.split('=')[1] ?? '';
  const r = await fetch('/api/v1' + path, { ...init, headers: { 'X-CSRF-Token': csrf, ...init?.headers } });
  if (!r.ok) {
    let detail: unknown;
    try {
      detail = (await r.json()).detail;
    } catch {
      /* Do not display raw proxy responses. */
    }
    const message =
      r.status === 401
        ? '会话已过期，请重新登录。'
        : r.status === 403
          ? '当前账号没有此操作权限，或会话校验已过期。'
          : typeof detail === 'string'
            ? (messages[detail] ?? (/^[\u3400-\u9fff]/.test(detail) ? detail : `请求未完成（${r.status}）。`))
            : `请求未完成（${r.status}）。`;
    throw new Error(message);
  }
  return r.json();
}

export function runStatus(status: string): string {
  return (
    (
      {
        queued: '排队中',
        running: '处理中',
        succeeded: '已完成',
        failed: '未完成',
        waiting_user: '已暂停，待处理',
        partially_succeeded: '部分完成',
        cancelled: '已取消',
      } as Record<string, string>
    )[status] ?? status
  );
}
export function runErrorMessage(error: { code?: string; contract_code?: string }): string {
  const code = error.contract_code ?? error.code ?? '';
  const reasons: Record<string, string> = {
    chart_font_unavailable: '缺少所需绘图字体，请安装 Noto Sans CJK SC 或选择已安装字体后重试。',
    incomplete_plaintext_translation_response: '接口遗漏翻译片段，合格部分已保存，本批已暂停。',
    invalid_plaintext_translation_response: '翻译结果格式或内容不合要求，本批已暂停。',
    translation_placeholder_integrity_failed: '翻译未保留原文标记，本批已暂停；已有合格片段保留。',
    production_admission_closed: '云端批次已关闭、到期或额度不足，请查看模型与额度状态。',
    rate_limited: '免费接口限流，已暂停；未自动重试。',
    invalid_csv_chart_input: 'CSV 的数据列、缺失值或图形参数有误，请检查输入。',
    followup_conversation_advanced: '会话已有新回答，请选择最新回答继续追问。',
    writing_scope_exceeds_run_budget_select_sections: '所选全文超出本次处理范围，请减少来源或分次整理。',
    no_text_layer_ocr_required: '文档没有可读取的文本层，需要先完成 OCR。',
    local_crop_rotation_requires_native_parser: '此 PDF 的裁切或旋转需要更完整的解析器。',
    summary_reduce_context_limit: '来源笔记仍过长，请减少来源或分次整理。',
    offline_contract_rejected: '接口结果未通过校验，请查看任务限制；已有结果保留。',
    offline_dependency_failure: '处理未完成，已保留失败记录；请检查文件或服务状态。',
  };
  return reasons[code] ?? '处理未完成，已保留任务记录与诊断。';
}
