import { useEffect, useState } from 'react';
import { api, errorMessage, runStatus } from './researchApi';
import useExecution from './useExecution';
import ScientificText from './ScientificText';
import TaskEvents from './TaskEvents';
import type { Target } from './SourceReader';

type Part = { source: string; translation: string | null; state: string; model_simulated?: boolean; human_edited?: boolean };
type Block = { id: string; canonical_text: string; kind: string; page_no: number; bbox: number[];
  parts: Part[]; translation_state: string; has_image: boolean;
  metadata: { method?: string; semantic_review?: string; confidence?: number };
  source_locator: { pdf_bbox?: number[] } };
type Task = { id: string; status: string; operation: string; error?: string; artifact_version_id?: string };
type Alignment = { parse_id: string | null; pages: number; parser?: string; blocks: Block[];
  total_blocks: number; translation_blocks: number; tasks: Task[]; ocr_pages?: number[]; limitations: string };

export default function DocumentWorkbench({ project, version, role, onSource, onTranslation, onResults }: {
  project: string; version: string; role?: string; onSource: (target: Target) => void;
  onTranslation: () => void; onResults: () => void;
}) {
  const [data, setData] = useState<Alignment | null>(null), [page, setPage] = useState(1);
  const [error, setError] = useState(''), [busy, setBusy] = useState(false), [run, setRun] = useState('');
  const [parentState, setParentState] = useState('');
  const [figures, setFigures] = useState(true), [translate, setTranslate] = useState(true);
  const execution = useExecution(project);
  const modelReady = execution.localAvailable([...(figures ? ['chat'] : []), ...(translate ? ['translation'] : [])]);
  useEffect(() => {
    if (!run) return;
    let stopped = false;
    async function load() {
      try { const row = await api(`/runs/${run}`); if (!stopped) setParentState(row.status); }
      catch (e) { if (!stopped) setError(errorMessage(e)); }
    }
    void load(); const timer = setInterval(() => void load(), 2500);
    return () => { stopped = true; clearInterval(timer); };
  }, [run]);
  useEffect(() => {
    let stopped = false;
    async function load() {
      try {
        const next = await api(`/studio/versions/${version}/alignment?page=${page}`);
        if (!stopped) { setData(next); setError(''); }
      } catch (e) { if (!stopped) setError(errorMessage(e)); }
    }
    void load();
    const timer = setInterval(() => void load(), 3000);
    return () => { stopped = true; clearInterval(timer); };
  }, [version, page]);
  async function start(operation: string) {
    setBusy(true); setError('');
    try {
      const next = await api(`/studio/projects/${project}/tasks`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
        body: JSON.stringify({ operation, version_id: version, mode: 'local_inference',
          recognize_figures: figures, translate_document: translate, language: 'zh-CN' }),
      });
      setRun(next.id); setParentState(next.status);
    } catch (e) { setError(errorMessage(e)); } finally { setBusy(false); }
  }
  const pending = data?.tasks.filter(t => ['queued', 'running'].includes(t.status)).length ?? 0;
  const active = pending > 0 || ['queued', 'running'].includes(parentState);
  return <section className="document-preparation" aria-label="本地解析与中英对应">
    <div className="section-top"><h2>本地解析与中英对应</h2><span className="status-pill">{data?.parse_id ? data.total_blocks ? '结构已保存' : '没有可核对文本' : '待解析'}</span></div>
    <p>页面分类 → 文本 / OCR → 区域排序与图表提取 → 本地图内识别与逐块翻译。解析不等于翻译或问答索引就绪。</p>
    <div className="action-row">
      <button disabled={busy || active || role !== 'researcher'} onClick={() => void start('local_parse')}>本地解析 / OCR</button>
      <label><input type="checkbox" checked={figures} onChange={e => setFigures(e.target.checked)} /> 识别图内节点与连线</label>
      <label><input type="checkbox" checked={translate} onChange={e => setTranslate(e.target.checked)} /> 逐页生成中文译文</label>
      <button className="primary" disabled={busy || active || role !== 'researcher' || !modelReady}
        onClick={() => void start('prepare_document')}>本地解析并执行所选步骤</button>
    </div>
    {!modelReady && <p>本地模型未就绪时仍可解析 / OCR；识图和翻译需本机服务可用且通过本地准入。</p>}
    {error && <p role="alert" className="error">{error}</p>}
    {run && <><p aria-live="polite">解析与排队步骤：{runStatus(parentState)}</p><TaskEvents run={run} /></>}
    <p aria-live="polite">{data?.parse_id ? `共 ${data.pages} 页 · 完整译文覆盖 ${data.translation_blocks}/${data.total_blocks} 个非空原文块 · ${pending} 个任务等待或执行中` : '解析后可在这里逐页核对原文；未翻译内容会明确标记。'}</p>
    {!!data?.ocr_pages?.length && <p>实际使用 OCR 的页：{data.ocr_pages.join('、')}。OCR 置信度不代表语义正确。</p>}
    {data?.parse_id && <>
      <div className="action-row">
        <label>核对页码 <input aria-label="解析核对页码" type="number" min={1} max={data.pages} value={page}
          onChange={e => setPage(Math.max(1, Math.min(data.pages, Number(e.target.value) || 1)))} /></label>
        <button onClick={() => onSource({ version_id: version, parse_id: data.parse_id!, page })}>对照 PDF 与结构</button>
        <button onClick={onTranslation}>翻译版本与人工修订</button><button onClick={onResults}>查看图内识别与任务成果</button>
      </div>
      <details open><summary>原文与中文对应 · 第 {page} 页 · {data.blocks.length} 个块</summary>
        {!data.blocks.length && <p>该页没有可显示的内容块，请核对原件和 OCR 状态。</p>}
        {data.blocks.map(b => <article className="alignment-block" key={b.id}>
          <div className="action-row"><small>{b.kind} · {b.metadata.method ?? data.parser} · {b.translation_state === 'ready' ? '译文待人工核对' : b.translation_state === 'partial' ? '部分已翻译' : '尚未翻译'}</small>
            <button onClick={() => onSource({ version_id: version, parse_id: data.parse_id!, page,
              pdf_bbox: b.source_locator.pdf_bbox, text: b.canonical_text })}>定位原件</button></div>
          <div className="alignment-pair">
            <div><h3>原文（解析结果）</h3>{b.canonical_text ? <ScientificText text={b.canonical_text} /> : <p>图片资产，请打开结构阅读器核对原图；图结构另存审阅。</p>}</div>
            <div><h3>中文译文</h3>{b.parts.map((p, i) => <div key={i}>
              {p.translation !== null ? <><small>{p.model_simulated ? '模拟译文' : p.human_edited ? '人工修订' : '本地/历史译文，需核对'}</small><ScientificText text={p.translation} /></> : <p>此原文范围尚未翻译。</p>}
            </div>)}{!b.parts.length && <p>图像未自动生成可信中文说明。</p>}</div>
          </div>
        </article>)}
      </details>
    </>}
    {!!data?.tasks.length && <details><summary>独立步骤状态（解析完成不代表全部完成）</summary>
      {data.tasks.map(t => <p key={t.id}>{t.operation} · {runStatus(t.status)}{t.error ? ` · ${t.error}` : ''}</p>)}
    </details>}
    <p className="muted">{data?.limitations ?? '图像和表格区域检测需核对；不自动将未经审阅的连线推断作为问答证据。'} 新解析不会改写旧译文或旧索引。</p>
  </section>;
}
