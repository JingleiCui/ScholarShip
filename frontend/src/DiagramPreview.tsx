import { useEffect, useState } from 'react';
let nextId = 0;
export default function DiagramPreview({ source }: { source: string }) {
  const [svg, setSvg] = useState(''),
    [error, setError] = useState('');
  useEffect(() => {
    let cancelled = false;
    setSvg('');
    setError('');
    void import('mermaid')
      .then(async ({ default: mermaid }) => {
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: 'strict',
          maxTextSize: 30000,
          flowchart: { htmlLabels: false },
        });
        const result = await mermaid.render(`scholarship-diagram-${++nextId}`, source);
        if (!cancelled) setSvg(`data:image/svg+xml;charset=utf-8,${encodeURIComponent(result.svg)}`);
      })
      .catch(() => {
        if (!cancelled) setError('图示渲染失败，请检查节点名称和连线。');
      });
    return () => {
      cancelled = true;
    };
  }, [source]);
  return error ? (
    <p role="alert">{error}</p>
  ) : svg ? (
    <img src={svg} alt="架构拓扑预览" style={{ maxWidth: '100%', maxHeight: 700 }} />
  ) : (
    <p>正在生成图示…</p>
  );
}
