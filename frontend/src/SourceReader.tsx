import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
import { api } from "./researchApi";
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;

export type Target = { version_id: string; parse_id: string; page: number; pdf_bbox?: number[]; quote?: string; text?: string; start?: number; end?: number };
type Block = { id: string; canonical_text: string; kind: string; asset_file_id: string | null; source_locator: { pdf_bbox?: number[] } };
type Heading = { id: string; canonical_text: string; page_no: number; structured_json: { heading_level?: number } };

export default function SourceReader({ target, close }: { target: Target; close: () => void }) {
  const [page, setPage] = useState(target.page), [error, setError] = useState('');
  const [blocks, setBlocks] = useState<Block[]>([]), [outline, setOutline] = useState<Heading[]>([]);
  const [box, setBox] = useState<number[] | undefined>(target.pdf_bbox);
  const reader = useRef<HTMLElement>(null);
  useEffect(() => { reader.current?.scrollIntoView({block:"start",behavior:"smooth"}); },[]);
  const canvas = useRef<HTMLCanvasElement>(null);
  const doc = useRef<pdfjs.PDFDocumentProxy | null>(null);
  const rendering = useRef<pdfjs.RenderTask | null>(null);
  const [ready, setReady] = useState(0);
  useEffect(() => {
    let cancelled = false;
    setError('');
    const task = pdfjs.getDocument({ url: `/api/v1/paper-versions/${target.version_id}/file`, withCredentials: true });
    void task.promise.then(d => { if (!cancelled) { doc.current = d; setReady(x => x + 1); } }).catch(e => { if (!cancelled) setError(String(e)); });
    void api(`/research/parses/${target.parse_id}/outline`).then(r => { if (!cancelled) setOutline(r); }).catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; rendering.current?.cancel(); doc.current = null; void task.destroy(); };
  }, [target.version_id, target.parse_id]);
  useEffect(() => {
    let cancelled = false;
    setBlocks([]);
    void api(`/parses/${target.parse_id}/pages/${page}`).then(r => { if (!cancelled) setBlocks(r.blocks); }).catch(e => { if (!cancelled) setError(String(e)); });
    return () => { cancelled = true; };
  }, [page, target.parse_id]);
  useEffect(() => {
    let cancelled = false;
    async function render() {
      rendering.current?.cancel();
      await rendering.current?.promise.catch(() => {});
      const d = doc.current;
      if (!d || !canvas.current || page > d.numPages) return;
      const pg = await d.getPage(page);
      if (cancelled) return;
      const base = pg.getViewport({ scale: 1 });
      const viewport = pg.getViewport({ scale: Math.min(1.15, 1200 / base.width) });
      const node = canvas.current;
      if (!node) return;
      node.width = viewport.width; node.height = viewport.height;
      const task = pg.render({ canvas: node, viewport });
      rendering.current = task;
      await task.promise;
      if (cancelled) return;
      if (box) {
        const [x1,y1] = viewport.convertToViewportPoint(box[0],box[1]);
        const [x2,y2] = viewport.convertToViewportPoint(box[2],box[3]);
        const ctx = node.getContext('2d')!;
        ctx.fillStyle = 'rgba(246,185,43,.23)'; ctx.strokeStyle = '#b35b00'; ctx.lineWidth = 2;
        ctx.fillRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));
        ctx.strokeRect(Math.min(x1,x2),Math.min(y1,y2),Math.abs(x2-x1),Math.abs(y2-y1));
      }
    }
    void render().catch(e => { if (!cancelled && e.name !== 'RenderingCancelledException') setError(String(e)); });
    return () => { cancelled = true; rendering.current?.cancel(); };
  }, [ready,page,box]);
  return <section ref={reader} className="task-panel" aria-label="原文证据阅读器">
    <button onClick={close}>关闭原文</button><h3>原文与证据 · 固定修订 {target.parse_id.slice(0,8)}</h3>
    <p>页码是原始 PDF 文件页序。黄色框定位 OCR 原文块；逐字引文核对不等于事实核验。</p>
    {error && <p role="alert">{error}</p>}
    {target.quote && <blockquote><p>引文来自原件第 {target.page} 页；当前浏览第 {page} 页。</p>{target.text && target.start !== undefined && target.end !== undefined ? <>{target.text.slice(0,target.start)}<mark>{target.text.slice(target.start,target.end)}</mark>{target.text.slice(target.end)}</> : target.quote}</blockquote>}
    <label>原文页码 <input aria-label="证据原文页码" type="number" min={1} max={doc.current?.numPages ?? 1} value={page} onChange={e => { setPage(Math.max(1,Math.min(doc.current?.numPages ?? 1,Number(e.target.value)||1))); setBox(undefined); }} /></label>
    <details><summary>文档标题目录</summary>{outline.map(h => <button key={h.id} style={{ display:'block', marginLeft:12*((h.structured_json.heading_level ?? 1)-1) }} onClick={() => { setPage(h.page_no); setBox(undefined); }}>{h.page_no} · {h.canonical_text}</button>)}</details>
    <div style={{ display:'grid',gridTemplateColumns:'repeat(auto-fit,minmax(300px,1fr))',gap:20, marginTop:16 }}>
      <div style={{ overflow:'auto' }}><canvas ref={canvas} style={{ maxWidth:'100%',height:'auto' }} /></div>
      <div style={{ maxHeight:850,overflow:'auto' }}>{blocks.map(b => <article key={b.id} style={{ marginBottom:16 }}>
        <button onClick={() => setBox(b.source_locator.pdf_bbox)}>定位 {b.kind}</button>
        <p style={{ whiteSpace:'pre-wrap',overflowWrap:'anywhere' }}>{b.canonical_text}</p>
        {b.asset_file_id && <img src={`/api/v1/research/blocks/${b.id}/asset`} alt={`${b.kind} 原始解析图像`} style={{ maxWidth:'100%' }} />}
      </article>)}</div>
    </div>
  </section>;
}

