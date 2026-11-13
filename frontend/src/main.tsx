import React, { useEffect, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
const Library = React.lazy(() => import("./Library"));

type User = {
  id: string;
  username: string;
  display_name: string;
  role: string;
};
type Project = { id: string; name: string; description: string };
type Health = {
  ready: boolean;
  services: Record<
    string,
    { ready: boolean; version?: string; error?: string }
  >;
};
type Run = { id: string; kind: string; status: string; result_json?: unknown };
type Event = { seq: number; type: string; message: string };
const modules = [
  {
    title: "文献管理",
    en: "LIBRARY",
    icon: "▤",
    desc: "统一收纳论文与教材，保留文件版本和原始来源。",
    tags: ["版本管理", "重复核验"],
  },
  {
    title: "论文阅读与翻译",
    en: "READ & UNDERSTAND",
    icon: "▥",
    desc: "双语阅读、公式与表格理解，让回答回到原文。",
    tags: ["原文定位", "证据问答"],
  },
  {
    title: "检索与推荐",
    en: "DISCOVER",
    icon: "⌕",
    desc: "围绕研究方向发现文献，追踪新进展与开源代码。",
    tags: ["混合检索", "方向核验"],
  },
  {
    title: "知识图谱",
    en: "CONNECT",
    icon: "⌘",
    desc: "沿着引用、主题与方法，连接你的研究线索。",
    tags: ["引用网络", "主题演化"],
  },
  {
    title: "写作与研究核验",
    en: "WRITE WITH EVIDENCE",
    icon: "✎",
    desc: "整理研究主张与支撑证据，追溯每一次修改。",
    tags: ["主张核验", "产物版本"],
  },
  {
    title: "数据分析与绘图",
    en: "ANALYZE & CREATE",
    icon: "▧",
    desc: "用受控分析模板生成图表，关联数据与生成脚本。",
    tags: ["可复现分析", "来源追踪"],
  },
];
async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const csrf =
    document.cookie
      .split("; ")
      .find((x) => x.startsWith("ss_csrf="))
      ?.split("=")[1] ?? "";
  const response = await fetch("/api/v1" + path, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      ...init?.headers,
    },
  });
  if (!response.ok)
    throw new Error(
      response.status === 401
        ? "请登录后继续。"
        : response.status === 403
          ? "会话校验失败，请重新登录。"
          : `请求未完成（${response.status}）。`,
    );
  return response.json() as Promise<T>;
}
function Sail({ small = false }: { small?: boolean }) {
  return (
    <svg
      width={small ? 32 : 56}
      height={small ? 32 : 56}
      viewBox="0 0 56 56"
      fill="none"
      aria-hidden="true"
    >
      <path d="M27 6v31H8L27 6Z" fill="currentColor" />
      <path d="M31 13v24h16L31 13Z" fill="currentColor" opacity=".48" />
      <path d="M6 41h44l-9 9H16L6 41Z" fill="currentColor" />
    </svg>
  );
}
function App() {
  const [active, setActive] = useState(-1),
    [health, setHealth] = useState<Health | null>(null),
    [user, setUser] = useState<User | null>(null),
    [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState("");
  const [loginOpen, setLoginOpen] = useState(false),
    [username, setUsername] = useState("researcher"),
    [password, setPassword] = useState(""),
    [error, setError] = useState(""),
    [busy, setBusy] = useState(false),
    [run, setRun] = useState<Run | null>(null),
    [events, setEvents] = useState<Event[]>([]),
    [streamState, setStreamState] = useState("");
  const stream = useRef<EventSource | null>(null);
  async function refresh() {
    try {
      setHealth(await api<Health>("/health/ready"));
    } catch {
      setHealth({ ready: false, services: {} });
    }
  }
  async function loadProjects() {
    const rows = await api<Project[]>("/projects");
    setProjects(rows);
    setProject(rows[0]?.id ?? "");
  }
  useEffect(() => {
    void refresh();
    void api<User>("/auth/me")
      .then((u) => {
        setUser(u);
        void loadProjects();
      })
      .catch(() => {});
    const timer = setInterval(() => void refresh(), 30000);
    return () => {
      clearInterval(timer);
      stream.current?.close();
    };
  }, []);
  async function login(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setBusy(true);
    try {
      const u = await api<User>("/auth/login", {
        method: "POST",
        body: JSON.stringify({ username, password }),
      });
      setUser(u);
      setPassword("");
      setLoginOpen(false);
      await loadProjects();
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    try {
      await api("/auth/logout", { method: "POST" });
      stream.current?.close();
      setUser(null);
      setProjects([]);
      setProject("");
      setRun(null);
      setEvents([]);
    } catch (e) {
      setError((e as Error).message);
    }
  }
  async function probe() {
    setError("");
    setBusy(true);
    try {
      const r = await api<Run>("/runs", {
        method: "POST",
        headers: { "Idempotency-Key": crypto.randomUUID() },
        body: JSON.stringify({ project_id: project }),
      });
      setRun(r);
      setEvents([]);
      setStreamState("正在连接事件流");
      stream.current?.close();
      const es = new EventSource(`/api/v1/runs/${r.id}/events`);
      stream.current = es;
      for (const type of ["queued", "running", "succeeded", "failed"])
        es.addEventListener(type, (e) => {
          const event = e as MessageEvent;
          const data = JSON.parse(event.data) as { message: string };
          setEvents((old) =>
            old.some((x) => x.seq === Number(event.lastEventId))
              ? old
              : [
                  ...old,
                  {
                    seq: Number(event.lastEventId),
                    type,
                    message: data.message,
                  },
                ],
          );
          setRun((old) => (old ? { ...old, status: type } : old));
          setStreamState("实时事件已连接");
          if (type === "succeeded" || type === "failed") {
            es.close();
            setStreamState("事件已完整接收");
            setBusy(false);
          }
        });
      es.onerror = () => {
        setStreamState("连接中断，正在按游标重连");
        void api<Run>(`/runs/${r.id}`)
          .then((state) => {
            setRun(state);
            if (["succeeded", "failed", "cancelled"].includes(state.status)) {
              es.close();
              setBusy(false);
              setStreamState("任务状态已核对；事件流中断");
            }
          })
          .catch(() => {
            es.close();
            setBusy(false);
            setError("会话或网络不可用，请刷新重试。");
          });
      };
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }
  const serviceNames: Record<string, string> = {
    postgresql: "PostgreSQL",
    qdrant: "Qdrant",
    redis: "Redis",
  };
  return (
    <div className="shell">
      <aside className="sidebar">
        <a className="brand" href="#" onClick={() => setActive(-1)}>
          <Sail small />
          <div>
            ScholarShip<small>学 者 之 船</small>
          </div>
        </a>
        <div className="workspace-label">RESEARCH WORKSPACE</div>
        <nav>
          <button
            className={active === -1 ? "selected" : ""}
            onClick={() => setActive(-1)}
          >
            <span>⌂</span>工作台
          </button>
          {modules.map((m, i) => (
            <button
              key={m.en}
              className={active === i ? "selected" : ""}
              onClick={() => setActive(i)}
            >
              <span>{m.icon}</span>
              {m.title}
            </button>
          ))}
        </nav>
        <div className="sidebar-bottom">
          <div className="compass">◉</div>
          <p>学海无涯，AI 作舟。</p>
          <small>每一条结论，都有来处。</small>
          <div className="stage">
            <span />
            M1 · 文献导入
          </div>
        </div>
      </aside>
      <main>
        <header>
          <div className="breadcrumb">
            我的工作空间 <span>/</span>{" "}
            {active < 0 ? "工作台" : modules[active].title}
          </div>
          <div className="header-actions">
            <span className="local-tag">本地开发</span>
            <button
              className="account"
              onClick={() => (user ? void logout() : setLoginOpen(true))}
            >
              <span className="avatar">{user ? "研" : "访"}</span>
              {user ? `${user.display_name} · 退出` : "登录研究空间"}
            </button>
          </div>
        </header>
        <div className="content">
          {(active === 0 || active === 1) && (
            <React.Suspense fallback={<p>正在加载文献阅读器…</p>}>
              <Library project={project} />
            </React.Suspense>
          )}
          <section className="hero">
            <div>
              <div className="eyebrow">
                <span />
                EVIDENCE FIRST. EXPLORE FURTHER.
              </div>
              <h1>
                让每一次探索，
                <br />
                都有据可循<span>。</span>
              </h1>
              <p>
                从文献到洞察，从想法到成果。
                <br />
                在一个可追溯的工作空间里，推进你的研究。
              </p>
              <div className="hero-foot">
                <span className="step-number">01</span>
                <span>基础环境已就位，研究旅程即将开始</span>
                <span className="long-arrow">⟶</span>
              </div>
            </div>
            <div className="hero-art">
              <div className="orbit orbit-one" />
              <div className="orbit orbit-two" />
              <div className="star s1">＋</div>
              <div className="star s2">＋</div>
              <div className="boat">
                <Sail />
              </div>
              <div className="wave w1" />
              <div className="wave w2" />
              <span className="art-caption">A VOYAGE INTO KNOWLEDGE</span>
            </div>
          </section>
          <section className="environment">
            <div className="section-top">
              <h2>
                环境状态 <span>LIVE</span>
              </h2>
              <button className="text-button" onClick={() => void refresh()}>
                刷新状态 ↻
              </button>
            </div>
            <div className="status-grid">
              <div className="service">
                <span className={`status-dot ${health?.ready ? "ok" : ""}`} />
                <div>
                  <strong>研究服务</strong>
                  <small>
                    {health?.ready ? "依赖与迁移就绪" : "正在检查或依赖未就绪"}
                  </small>
                </div>
                <span className="service-mark">API</span>
              </div>
              {Object.entries(serviceNames).map(([key, name]) => (
                <div className="service" key={key}>
                  <span
                    className={`status-dot ${health?.services[key]?.ready ? "ok" : ""}`}
                  />
                  <div>
                    <strong>{name}</strong>
                    <small>
                      {health?.services[key]?.ready
                        ? health.services[key].version?.split(" ")[0]
                        : "未就绪"}
                    </small>
                  </div>
                  <span className="service-mark">
                    {key === "postgresql"
                      ? "DB"
                      : key === "qdrant"
                        ? "VEC"
                        : "MQ"}
                  </span>
                </div>
              ))}
            </div>
          </section>
          <section className="research">
            <div className="section-top">
              <div>
                <h2>{active < 0 ? "你的研究工具箱" : modules[active].title}</h2>
                <p>六个能力模块，一个连贯的研究过程。</p>
              </div>
              <span className="outline-tag">M1 · 文献导入</span>
            </div>
            <div className="module-grid">
              {modules.map((m, i) => (
                <button
                  onClick={() => setActive(i)}
                  className={`module-card ${active === i ? "focused" : ""}`}
                  key={m.en}
                >
                  <div className="card-top">
                    <span className="module-icon">{m.icon}</span>
                    <span className="planned">
                      {i < 2 ? "本地预览可用" : "待开发"}
                    </span>
                  </div>
                  <small className="module-en">{m.en}</small>
                  <h3>
                    {m.title}
                    <span>↗</span>
                  </h3>
                  <p>{m.desc}</p>
                  <div className="tags">
                    {m.tags.map((t) => (
                      <span key={t}>{t}</span>
                    ))}
                  </div>
                </button>
              ))}
            </div>
            {active >= 2 && (
              <div className="notice" role="status">
                {modules[active].title}
                尚未实现。当前页面用于确认工作台结构，后续开发会在此提供真实数据与操作。
              </div>
            )}
          </section>
          <section className="bottom-grid">
            <div className="task-panel">
              <div className="section-top">
                <h2>
                  任务中心 <span>LOCAL PROBE</span>
                </h2>
                <span className="outline-tag">不产生模型费用</span>
              </div>
              <p>检查任务投递、LangGraph 持久化与实时事件回传。</p>
              <div className="probe-controls">
                {user ? (
                  <select
                    aria-label="研究项目"
                    value={project}
                    onChange={(e) => setProject(e.target.value)}
                  >
                    {projects.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                ) : (
                  <span className="signin-hint">登录后可运行基础设施探针</span>
                )}
                <button
                  className="primary"
                  disabled={busy || !health?.ready || (!!user && !project)}
                  onClick={() => (user ? void probe() : setLoginOpen(true))}
                >
                  {busy ? "正在执行…" : user ? "运行探针 ↗" : "登录并体验 ↗"}
                </button>
              </div>
              {run ? (
                <div className="run-detail">
                  <div className="run-heading">
                    <strong>
                      {run.status === "succeeded"
                        ? "✓ 探针已完成"
                        : run.status === "failed"
                          ? "探针失败"
                          : "◌ 任务处理中"}
                    </strong>
                    <code>{run.id.slice(0, 8)}</code>
                  </div>
                  <ol>
                    {events.map((e) => (
                      <li key={e.seq}>
                        <span>{String(e.seq).padStart(2, "0")}</span>
                        {e.message}
                      </li>
                    ))}
                  </ol>
                  <small>{streamState}</small>
                </div>
              ) : (
                <div className="empty-task">
                  <span>⌁</span>
                  <div>
                    还没有运行记录
                    <small>开启第一次环境验证，确认研究工作流的起点。</small>
                  </div>
                </div>
              )}
              {error && !loginOpen && (
                <div role="alert" className="error">
                  {error}
                </div>
              )}
            </div>
            <div className="principles">
              <span className="eyebrow">BUILT FOR TRUST</span>
              <h2>从起点，就认真对待证据。</h2>
              <div>
                <span>01</span>
                <p>
                  <strong>来源可查</strong>
                  <small>原文、页码与证据锚点，构成每条结论的依据。</small>
                </p>
              </div>
              <div>
                <span>02</span>
                <p>
                  <strong>过程可追</strong>
                  <small>任务、数据与产物版本，保留研究演进的轨迹。</small>
                </p>
              </div>
              <div>
                <span>03</span>
                <p>
                  <strong>成本可控</strong>
                  <small>外部服务默认关闭，首期 AI 预算目标 ¥100。</small>
                </p>
              </div>
            </div>
          </section>
          <footer>
            <span>ScholarShip · 学者之船</span>
            <span>
              2026 研究生组参赛项目 <i>·</i> M1 文献与本地预览
            </span>
          </footer>
        </div>
      </main>
      {loginOpen && (
        <div
          className="modal-backdrop"
          onClick={(e) => {
            if (e.target === e.currentTarget) setLoginOpen(false);
          }}
        >
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-labelledby="login-title"
          >
            <button
              className="close"
              aria-label="关闭"
              onClick={() => setLoginOpen(false)}
            >
              ×
            </button>
            <Sail small />
            <h2 id="login-title">进入你的研究空间</h2>
            <p>研究账号与评审账号拥有独立的数据空间。</p>
            <form onSubmit={(e) => void login(e)}>
              <label>
                账号
                <input
                  autoFocus
                  autoComplete="username"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                />
              </label>
              <label>
                密码
                <input
                  type="password"
                  autoComplete="current-password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                />
              </label>
              {error && (
                <div className="error" role="alert">
                  {error}
                </div>
              )}
              <button className="primary" disabled={busy}>
                {busy ? "正在登录…" : "登录"}
              </button>
            </form>
            <small>
              初始账号凭据由本地初始化生成，请从项目私有配置中查看。
            </small>
          </section>
        </div>
      )}
    </div>
  );
}
createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
