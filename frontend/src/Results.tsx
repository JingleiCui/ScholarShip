import { useEffect, useState } from 'react';
import { api, errorMessage, runErrorMessage, runStatus } from './researchApi';
import { Icon } from './Brand';
import TaskEvents from './TaskEvents';
import CancelTask from './CancelTask';
import type { Page, ReadingTab } from './navigation';
export type SavedRun = {
  id: string;
  source_version_ids?: string[];
  kind?: string;
  operation?: string;
  mode?: string;
  status: string;
  question?: string;
  created_at?: string;
  content_review?: { assessment: string; reason: string } | null;
  scientific_review?: { verdict: string; reason: string } | null;
  editorial_review?: { reason: string; reviewed_artifact_version_id: string } | null;
  error_json?: { code?: string; contract_code?: string };
  result_json?: {
    source?: { parse_id: string; version_id: string; block_id: string; page: number };
    answer?: string;
    model_simulated?: boolean;
    source_synthetic?: boolean;
    answer_method?: string;
    caption?: string;
    artifact_version_id?: string;
    translation_id?: string;
    limitations?: string;
    review_notice?: string;
    limitations_notice?: string;
    local_postprocessing_only?: boolean;
    sections?: { title: string; text: string }[];
    citations?: { id: string; page: number }[];
  };
};
export const operationNames: Record<string, string> = {
  figure: '图内文字与结构',
  figure_correct: '图内结构人工修订',
  research_qa: '证据问答',
  research_build: '文献索引',
  local_parse: '文本结构解析',
  prepare_document: '本地解析、识图与翻译准备',
  translate: '阅读翻译',
  summary: '来源摘要 / 综述',
  draft: '写作框架',
  review_artifact: '摘要与文献整理',
  direction: '研究方向草稿',
  direction_check: '后续研究检索',
  direction_review: '后续全文证据对照',
  chart: '数据图表',
  diagram: '架构拓扑',
};
export function resultOrigin(r: SavedRun): string {
  if (r.operation === 'prepare_document') return '本地解析完成后排队识图 / 翻译；各子任务独立核对';
  return r.result_json?.source_synthetic
    ? '合成来源'
    : r.result_json?.model_simulated
      ? '模拟结果 · 不用于科研'
      : r.result_json?.local_postprocessing_only
        ? '本地整理已有产物'
        : r.mode === 'local' ||
            r.result_json?.answer_method?.startsWith('deterministic_') ||
            ['direction_review', 'figure_correct'].includes(r.operation ?? '')
          ? '本地处理 / 人工对照'
          : r.operation === 'direction_check'
            ? '公开元数据'
            : r.status === 'succeeded'
              ? '历史模型结果 · 需核对'
              : '尚无完成结果';
}
export default function Results({
  project,
  role,
  onNavigate,
}: {
  project: string;
  role?: string;
  onNavigate: (page: Page, run?: string, tab?: ReadingTab, versions?: string[]) => void;
}) {
  const [rows, setRows] = useState<SavedRun[]>([]),
    [error, setError] = useState(''),
    [loading, setLoading] = useState(true),
    [filter, setFilter] = useState('all');
  useEffect(() => {
    let stop = false;
    setRows([]);
    setLoading(true);
    setError('');
    if (!project) {
      setLoading(false);
      return;
    }
    async function load() {
      try {
        const [s, r] = await Promise.all([
          api(`/studio/projects/${project}/runs`),
          api(`/research/projects/${project}/runs`),
        ]);
        if (!stop) {
          setRows([...s, ...r].sort((a, b) => (b.created_at ?? '').localeCompare(a.created_at ?? '')));
          setError('');
        }
      } catch (e) {
        if (!stop) setError(errorMessage(e));
      } finally {
        if (!stop) setLoading(false);
      }
    }
    void load();
    const timer = setInterval(() => void load(), 5000);
    return () => {
      stop = true;
      clearInterval(timer);
    };
  }, [project]);
  const types: Record<string, string[]> = {
    qa: ['research_qa'],
    writing: ['summary', 'draft', 'review_artifact', 'direction', 'direction_review'],
    translation: ['translate'],
    figures: ['figure', 'figure_correct'],
    charts: ['chart', 'diagram'],
    tasks: ['research_build', 'local_parse', 'prepare_document', 'direction_check'],
  };
  const visible = rows.filter(
    (r) => filter === 'all' || types[filter]?.includes(r.operation ?? r.kind ?? ''),
  );
  function open(r: SavedRun) {
    const op = r.operation ?? r.kind ?? '';
    onNavigate(
      ['research_qa', 'research_build', 'local_parse', 'prepare_document', 'translate', 'figure', 'figure_correct'].includes(op)
        ? 'read'
        : ['chart', 'diagram'].includes(op)
          ? 'charts'
          : ['direction', 'direction_check', 'direction_review'].includes(op)
            ? 'graph'
            : 'write',
      r.id,
      op === 'translate'
        ? 'translation'
        : ['research_build', 'local_parse', 'prepare_document', 'figure', 'figure_correct'].includes(op)
          ? 'document'
          : 'qa',
      r.source_version_ids,
    );
  }
  return (
    <>
      <div className="page-title">
        <h1>每一份成果，都留下出处。</h1>
        <p>集中回看当前空间的问答、译文、摘要、框架和图表；结果来自实际任务记录。</p>
      </div>
      <div className="filter-row" aria-label="成果类型">
        {[
          ['all', '全部'],
          ['qa', '问答'],
          ['translation', '译文'],
          ['figures', '图内结构'],
          ['writing', '摘要与写作'],
          ['charts', '图表'],
          ['tasks', '准备与检索任务'],
        ].map(([id, title]) => (
          <button key={id} aria-pressed={filter === id} onClick={() => setFilter(id)}>
            {title}
          </button>
        ))}
      </div>
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {loading && <p role="status">正在读取当前空间的任务与产物…</p>}
      {!loading && !error && !visible.length && (
        <section className="empty-state">
          <Icon name="layers" size={34} />
          <h2>{rows.length ? '这一类还没有记录' : '还没有研究成果'}</h2>
          <p>从文献库打开一篇文献，或用自己的 CSV 生成图表。完成后的记录会保存在这里。</p>
          <button className="primary" onClick={() => onNavigate('library')}>
            打开文献库
          </button>
        </section>
      )}
      <div className="result-list">
        {visible.map((r) => (
          <article className="result-card" key={r.id}>
            <div className="result-top">
              <span className="status-pill">{operationNames[r.operation ?? r.kind ?? ''] ?? '研究任务'}</span>
              <span className={`status-pill ${r.status === 'failed' ? 'warning' : ''}`}>
                {runStatus(r.status)}
              </span>
              <small>{r.created_at ? new Date(r.created_at).toLocaleString() : r.id.slice(0, 8)}</small>
            </div>
            <h2>{r.question ?? operationNames[r.operation ?? r.kind ?? ''] ?? '研究任务'}</h2>
            <p className="provenance">{resultOrigin(r)}</p>
            {r.scientific_review && (
              <p className={r.scientific_review.verdict === 'incorrect' ? 'error' : 'inline-notice'}>
                {r.scientific_review.verdict === 'incorrect'
                  ? '此历史回答已核对为错误，请勿采用。'
                  : '固定样本审阅记录：'}
                {r.scientific_review.reason}
              </p>
            )}
            {r.editorial_review && <p className="inline-notice">{r.editorial_review.reason}</p>}
            {['figure', 'figure_correct'].includes(r.operation ?? '') && (
              <p className={r.content_review?.assessment === 'rejected' ? 'error' : 'inline-notice'}>
                {r.content_review?.assessment === 'rejected'
                  ? '图内内容核验未通过，请勿作为正确结构采用。'
                  : r.content_review?.assessment === 'accepted'
                    ? '此图内结构有人工核对记录；不代表一般识别准确率。'
                    : '图内结构尚待对照原图核验。'}{' '}
                {r.content_review?.reason}
              </p>
            )}
            {r.error_json && <p className="error">{runErrorMessage(r.error_json)}</p>}
            {r.result_json && (
              <>
                <p className="result-excerpt">
                  {r.result_json.answer ??
                    r.result_json.sections?.map((s) => s.text).join('\n') ??
                    r.result_json.caption ??
                    r.result_json.review_notice ??
                    '产物已保存，可打开详细任务查看。'}
                </p>
                {(r.result_json.limitations ?? r.result_json.limitations_notice) && (
                  <p className="helper">{r.result_json.limitations ?? r.result_json.limitations_notice}</p>
                )}
              </>
            )}
            <div className="action-row">
              <button onClick={() => open(r)}>
                打开结果与来源 <Icon name="arrow" size={18} />
              </button>
              {r.result_json?.artifact_version_id && (
                <a className="button" href={`/api/v1/studio/artifacts/${r.result_json.artifact_version_id}`}>
                  下载产物与来源
                </a>
              )}
              {r.editorial_review && (
                <a
                  className="button"
                  href={`/api/v1/studio/artifacts/${r.editorial_review.reviewed_artifact_version_id}`}
                >
                  下载整理版
                </a>
              )}
            </div>
            <details className="task-details">
              <summary>任务步骤与记录</summary>
              {role === 'researcher' && <CancelTask id={r.id} status={r.status} />}
              <TaskEvents run={r.id} />
              <p className="helper">任务 {r.id}。流程完成与科学内容核对分别记录。</p>
            </details>
          </article>
        ))}
      </div>
      {rows.length > 0 && (
        <p className="helper">当前显示最近 30 条工作室任务与 20 条问答/索引记录，未删除更早的历史。</p>
      )}
    </>
  );
}
