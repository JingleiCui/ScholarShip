import { useRef, useMemo, useState } from 'react';
import ScientificText from './ScientificText';
export type Segment = {
  id: string;
  block_id: string;
  canonical_text: string;
  translated_text: string;
  page_no: number;
  placeholders_json: { model_simulated?: boolean; human_edited?: boolean };
};
export default function BilingualReader({ segments }: { segments: Segment[] }) {
  const [parallel, setParallel] = useState(false);
  const blocks = useMemo(() => {
    const result: Segment[] = [];
    for (const s of segments) {
      const last = result[result.length - 1];
      if (last?.block_id === s.block_id) {
        last.canonical_text += s.canonical_text;
        last.translated_text += s.translated_text;
      } else result.push({ ...s, id: s.block_id });
    }
    return result;
  }, [segments]);
  const left = useRef<HTMLDivElement>(null),
    right = useRef<HTMLDivElement>(null);
  const driven = useRef<{ side: 'left' | 'right'; top: number } | null>(null);
  function sync(side: 'left' | 'right') {
    const source = side === 'left' ? left.current : right.current;
    const target = side === 'left' ? right.current : left.current;
    if (!source || !target) return;
    if (driven.current?.side === side && Math.abs(source.scrollTop - driven.current.top) < 3) {
      driven.current = null;
      return;
    }
    const blocks = [...source.querySelectorAll<HTMLElement>('[data-block]')];
    const anchor =
      [...blocks].reverse().find((n) => n.offsetTop - source.offsetTop <= source.scrollTop + 5) ?? blocks[0];
    if (!anchor) return;
    const other = [...target.querySelectorAll<HTMLElement>('[data-block]')].find(
      (n) => n.dataset.block === anchor.dataset.block,
    );
    if (!other) return;
    const ratio = Math.max(
      0,
      Math.min(
        1,
        (source.scrollTop - (anchor.offsetTop - source.offsetTop)) / Math.max(1, anchor.offsetHeight),
      ),
    );
    const top = other.offsetTop - target.offsetTop + ratio * other.offsetHeight;
    driven.current = {
      side: side === 'left' ? 'right' : 'left',
      top: Math.max(0, Math.min(top, target.scrollHeight - target.clientHeight)),
    };
    target.scrollTop = driven.current.top;
  }
  if (!parallel)
    return (
      <section className="bilingual-flow">
        <div className="section-top">
          <h3>原文与译文 · 逐块阅读</h3>
          <button onClick={() => setParallel(true)}>切换双栏同步</button>
        </div>
        <p>
          {segments.some((s) => s.placeholders_json.model_simulated)
            ? '模拟译文，仅验证对应关系。'
            : '模型译文待人工校对。'}
          {segments.some((s) => s.placeholders_json.human_edited) ? '包含人工修订，原始版本保留。' : ''}
          公式和代码保留原文。
        </p>
        {blocks.map((s) => (
          <article key={s.id} className="bilingual-block">
            <small>PDF 第 {s.page_no} 页</small>
            <div className="source-block">
              <span>原文</span>
              <ScientificText text={s.canonical_text} />
            </div>
            <div className="translation-block">
              <span>译文</span>
              <ScientificText text={s.translated_text} />
            </div>
          </article>
        ))}
      </section>
    );
  return (
    <section>
      <div className="section-top">
        <h3>原文与译文 · 按内容块双向同步</h3>
        <button onClick={() => setParallel(false)}>切换逐块阅读</button>
      </div>
      <p>
        {segments.some((s) => s.placeholders_json.model_simulated)
          ? '当前为模拟译文，仅验证对应关系和公式保护。'
          : '模型译文待人工校对。'}
        公式和代码保留原文；两侧高度不同仍按相同内容块对齐。
      </p>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(2,minmax(0,1fr))', gap: 16 }}>
        {(['left', 'right'] as const).map((side) => (
          <div
            key={side}
            ref={side === 'left' ? left : right}
            onScroll={() => sync(side)}
            style={{
              height: 600,
              overflow: 'auto',
              position: 'relative',
              border: '1px solid #e4ebf5',
              padding: 12,
            }}
            aria-label={side === 'left' ? '原文' : '译文'}
          >
            {blocks.map((s) => (
              <article
                key={s.id}
                data-block={s.id}
                style={{ paddingBottom: 24, whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}
              >
                <small>PDF 第 {s.page_no} 页</small>
                <div>
                  <ScientificText text={side === 'left' ? s.canonical_text : s.translated_text} />
                </div>
              </article>
            ))}
          </div>
        ))}
      </div>
    </section>
  );
}
