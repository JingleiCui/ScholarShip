import { useEffect, useRef } from 'react';
import cytoscape from 'cytoscape';
export type GraphData = {
  nodes: { id: string; title: string; pagerank: number; in_degree: number }[];
  edges: {
    source: string;
    target: string;
    reference_id: string;
    identity_confidence?: string;
    block_id: string | null;
  }[];
  minimum_20_met: boolean;
  limitations: string;
  papers_with_active_parse: number;
};
export default function CitationGraph({
  data,
  onEvidence,
}: {
  data: GraphData;
  onEvidence: (id: string) => void;
}) {
  const element = useRef<HTMLDivElement>(null);
  const evidence = useRef(onEvidence);
  evidence.current = onEvidence;
  useEffect(() => {
    if (!element.current) return;
    const cy = cytoscape({
      container: element.current,
      elements: [
        ...data.nodes.map((n) => ({
          data: { ...n, label: n.title.length > 30 ? n.title.slice(0, 30) + '…' : n.title },
        })),
        ...data.edges.map((e, i) => ({ data: { ...e, id: 'edge-' + i } })),
      ],
      style: [
        {
          selector: 'node',
          style: {
            label: 'data(label)',
            'background-color': '#286254',
            color: '#193d34',
            'font-size': 11,
            width: 28,
            height: 28,
          },
        },
        {
          selector: 'edge',
          style: {
            'target-arrow-shape': 'triangle',
            'curve-style': 'bezier',
            'line-color': '#799b8e',
            'target-arrow-color': '#799b8e',
            width: 2,
          },
        },
        {
          selector: 'edge[identity_confidence = "local_identifier_candidate_needs_review"]',
          style: { 'line-style': 'dashed', 'line-color': '#af8a45', 'target-arrow-color': '#af8a45' },
        },
      ],
      layout: { name: 'circle' },
      wheelSensitivity: 0.2,
    });
    cy.on('tap', 'edge', (event) => {
      const id = event.target.data('block_id');
      if (id) evidence.current(id);
    });
    return () => cy.destroy();
  }, [data]);
  return (
    <>
      <p>
        {data.nodes.length} 篇 · {data.edges.length} 条带原文的候选或已核验关系 ·{' '}
        {data.minimum_20_met ? '已达 20 篇输入规模' : '少于 20 篇，尚不满足官方图谱规模验收'}
      </p>
      <p>{data.limitations}</p>
      <div
        ref={element}
        aria-label="局域文献引用图"
        style={{ height: 450, border: '1px solid #bdcfc5', borderRadius: 12 }}
      />
      <p>
        点击引用边查看原文。虚线表示本地标识候选，文献身份仍需核验；排名包含这些候选边，仅在当前所选范围内计算。
      </p>
      <ol>
        {[...data.nodes]
          .sort((a, b) => b.pagerank - a.pagerank)
          .map((n) => (
            <li key={n.id}>
              {n.title} · PageRank {n.pagerank.toFixed(4)} · 局域入度 {n.in_degree}
            </li>
          ))}
      </ol>
    </>
  );
}
