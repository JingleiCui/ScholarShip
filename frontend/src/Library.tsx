import { useEffect, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import workerUrl from "pdfjs-dist/build/pdf.worker.min.mjs?url";
pdfjs.GlobalWorkerOptions.workerSrc = workerUrl;
type Paper = {
  id: string;
  title: string;
  version_id: string;
  metadata_json: { page_count: number };
};
type Parse = {
  version_id: string;
  id: string;
  status: string;
  completed_pages: number;
  expected_pages: number;
  error_json?: unknown;
  result_json?: unknown;
};
async function request(path: string, init?: RequestInit) {
  const csrf =
    document.cookie
      .split("; ")
      .find((x) => x.startsWith("ss_csrf="))
      ?.split("=")[1] ?? "";
  const r = await fetch("/api/v1" + path, {
    ...init,
    headers: { "X-CSRF-Token": csrf, ...init?.headers },
  });
  if (!r.ok)
    throw new Error(`请求失败 ${r.status}：${(await r.text()).slice(0, 180)}`);
  return r.json();
}
export default function Library({ project }: { project: string }) {
  const [papers, setPapers] = useState<Paper[]>([]),
    [selected, setSelected] = useState<Paper | null>(null),
    [parses, setParses] = useState<Parse[]>([]),
    [page, setPage] = useState(1),
    [text, setText] = useState(""),
    [error, setError] = useState(""),
    [append, setAppend] = useState(false),
    [busy, setBusy] = useState(false),
    [reviews, setReviews] = useState<
      { id: string; candidate_json: { papers: { title: string }[] } }[]
    >([]);
  const rendering = useRef<pdfjs.RenderTask | null>(null);
  const canvas = useRef<HTMLCanvasElement>(null),
    pdf = useRef<pdfjs.PDFDocumentProxy | null>(null);
  async function refresh() {
    if (project) {
      setPapers(await request(`/projects/${project}/papers`));
      setReviews(await request(`/projects/${project}/duplicate-reviews`));
    }
  }
  useEffect(() => {
    let cancelled = false;
    setSelected(null);
    setPapers([]);
    setParses([]);
    setReviews([]);
    setError("");
    if (project)
      void Promise.all([
        request(`/projects/${project}/papers`),
        request(`/projects/${project}/duplicate-reviews`),
      ])
        .then(([p, r]) => {
          if (!cancelled) {
            setPapers(p);
            setReviews(r);
          }
        })
        .catch((e) => {
          if (!cancelled) setError(String(e));
        });
    return () => {
      cancelled = true;
    };
  }, [project]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    let timer: ReturnType<typeof setInterval>;
    async function poll() {
      try {
        const rows = await request(
          `/paper-versions/${selected!.version_id}/parses`,
        );
        if (!cancelled) setParses(rows);
      } catch (e) {
        if (!cancelled) setError(String(e));
      }
    }
    void poll();
    timer = setInterval(() => void poll(), 2000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [selected]);
  useEffect(() => {
    if (!selected) return;
    let cancelled = false;
    const loading = pdfjs.getDocument({
      url: `/api/v1/paper-versions/${selected.version_id}/file`,
      withCredentials: true,
    });
    void loading.promise
      .then((d) => {
        if (!cancelled) {
          pdf.current = d;
          setPage(1);
          void render(d, 1).catch((e) => {
            if (!cancelled) setError(String(e));
          });
        } else void loading.destroy();
      })
      .catch((e) => {
        if (!cancelled) setError(String(e));
      });
    return () => {
      cancelled = true;
      rendering.current?.cancel();
      pdf.current = null;
      void loading.destroy();
    };
  }, [selected]);
  async function render(doc: pdfjs.PDFDocumentProxy, n: number) {
    rendering.current?.cancel();
    if (rendering.current) await rendering.current.promise.catch(() => {});
    const pg = await doc.getPage(n);
    if (doc !== pdf.current) return;
    const base = pg.getViewport({ scale: 1 });
    const viewport = pg.getViewport({
      scale: Math.min(1.2, 1600 / base.width, 2200 / base.height),
    });
    const node = canvas.current;
    if (!node) return;
    node.width = viewport.width;
    node.height = viewport.height;
    const task = pg.render({ canvas: node, viewport });
    rendering.current = task;
    await task.promise.catch((e) => {
      if (e.name !== "RenderingCancelledException") throw e;
    });
  }
  useEffect(() => {
    if (pdf.current)
      void render(pdf.current, page).catch((e) => setError(String(e)));
  }, [page]);
  useEffect(() => {
    let cancelled = false;
    const p = parses[0];
    setText("正在加载对应页的文本…");
    if (p && p.version_id === selected?.version_id)
      void request(`/parses/${p.id}/pages/${page}`)
        .then((r) => {
          if (!cancelled)
            setText(
              r.needs_ocr
                ? "此页没有可提取文本：可能为空白页或需要 OCR。"
                : r.blocks
                    .map((b: { canonical_text: string }) => b.canonical_text)
                    .join("\n"),
            );
        })
        .catch(() => {
          if (!cancelled) setText("此页尚未提取。");
        });
    return () => {
      cancelled = true;
    };
  }, [page, parses, selected]);
  async function upload(file: File) {
    setBusy(true);
    setError("");
    try {
      const data = new FormData();
      data.append("file", file);
      if (append && selected) data.append("paper_id", selected.id);
      const result = await request(`/projects/${project}/uploads`, {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: data,
      });
      await refresh();
      if (result.review_id)
        setError("发现同标题候选，已保留两个载体，等待去重核验。");
    } catch (e) {
      setError(String(e));
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="task-panel library" style={{ marginBottom: 24 }}>
      <h2>文献库 · 本地预览</h2>
      {selected && (
        <label>
          <input
            type="checkbox"
            checked={append}
            onChange={(e) => setAppend(e.target.checked)}
          />
          将下次导入明确作为当前作品的新文件版本
        </label>
      )}
      <p>原件按账号隔离；当前为页级文本预览，尚未深解析或建立问答索引。</p>
      {project ? (
        <label className="primary">
          {busy ? "正在导入…" : "导入 PDF"}
          <input
            aria-label="导入 PDF"
            type="file"
            accept="application/pdf"
            disabled={busy}
            style={{ display: "block", marginTop: 12 }}
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) void upload(f);
            }}
          />
        </label>
      ) : (
        <p>请先登录研究空间。</p>
      )}
      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}
      {reviews.map((r) => (
        <div key={r.id} className="notice">
          疑似重复：{r.candidate_json.papers.map((p) => p.title).join("；")}
          。请核验后选择：
          {(["keep_both", "reject"] as const).map((d) => (
            <button
              key={d}
              onClick={() =>
                void request(`/duplicate-reviews/${r.id}/decision`, {
                  method: "POST",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ decision: d }),
                })
                  .then(() => refresh())
                  .catch((e) => setError(String(e)))
              }
            >
              {d === "keep_both" ? "保留两份独立记录" : "拒绝本次新增记录"}
            </button>
          ))}
        </div>
      ))}
      <div
        style={{ display: "flex", gap: 12, flexWrap: "wrap", marginTop: 20 }}
      >
        {papers.map((p) => (
          <button
            className="module-card"
            key={p.version_id}
            onClick={() => setSelected(p)}
          >
            {p.title}
            <small style={{ display: "block" }}>
              {p.metadata_json.page_count} 页 · {p.version_id.slice(0, 8)}
            </small>
          </button>
        ))}
      </div>
      {selected && (
        <>
          <h3>{selected.title}</h3>
          <p>
            {parses[0]
              ? `${parses[0].status} · ${parses[0].completed_pages}/${parses[0].expected_pages} 页`
              : "等待解析"}{" "}
            · 阅读顺序未核验
          </p>
          {(parses[0]?.status === "failed" ||
            (parses[0]?.status === "partial" &&
              parses[0].completed_pages < parses[0].expected_pages)) && (
            <button
              onClick={() =>
                void request(`/parses/${parses[0].id}/resume`, {
                  method: "POST",
                }).catch((e) => setError(String(e)))
              }
            >
              继续未完成页
            </button>
          )}
          <button
            onClick={() =>
              void request(`/paper-versions/${selected.version_id}/parses`, {
                method: "POST",
              }).catch((e) => setError(String(e)))
            }
          >
            新增解析修订
          </button>
          <details>
            <summary>解析诊断</summary>
            <pre style={{ whiteSpace: "pre-wrap" }}>
              {JSON.stringify(
                parses[0]?.error_json ?? parses[0]?.result_json,
                null,
                2,
              )}
            </pre>
          </details>
          <label>
            {" "}
            原文页码{" "}
            <input
              aria-label="原文页码"
              type="number"
              min={1}
              max={selected.metadata_json.page_count}
              value={page}
              onChange={(e) =>
                setPage(
                  Math.max(
                    1,
                    Math.min(
                      selected.metadata_json.page_count,
                      Number(e.target.value) || 1,
                    ),
                  ),
                )
              }
            />
          </label>
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit,minmax(280px,1fr))",
              gap: 20,
              marginTop: 20,
            }}
          >
            <canvas
              ref={canvas}
              style={{ maxWidth: "100%", height: "auto", background: "white" }}
            />
            <pre
              style={{
                whiteSpace: "pre-wrap",
                overflowWrap: "anywhere",
                fontSize: 12,
                lineHeight: 1.8,
                maxHeight: 800,
                overflow: "auto",
              }}
            >
              {text}
            </pre>
          </div>
        </>
      )}
    </section>
  );
}
