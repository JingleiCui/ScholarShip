import React, { lazy, Suspense, useEffect, useRef, useState } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import './styles.css';
import './workspace.css';
import Brand, { BRAND, BrandMark, Icon } from './Brand';
import Home, { Tools } from './Home';
import WorkspaceCreate from './WorkspaceCreate';
import { api, errorMessage } from './researchApi';
import { pageNames, parseRoute, routeHash, type Page, type ReadingTab, type Route } from './navigation';
const Reader = lazy(() => import('./Reader'));
const Library = lazy(() => import('./Library'));
const Results = lazy(() => import('./Results'));
const Studio = lazy(() => import('./Studio'));
const ModelConfiguration = lazy(() => import('./ModelConfiguration'));
const ServiceStatus = lazy(() => import('./ServiceStatus'));
const OfflineResearch = lazy(() => import('./OfflineResearch'));
const SystemProbe = lazy(() => import('./SystemProbe'));
type User = { id: string; username: string; display_name: string; role: string };
type Project = { id: string; name: string; description: string };
function App() {
  const [route, setRoute] = useState<Route>(() => parseRoute(location.hash));
  const [user, setUser] = useState<User | null>(null),
    [projects, setProjects] = useState<Project[]>([]),
    [project, setProject] = useState('');
  const [authLoading, setAuthLoading] = useState(true),
    [projectLoading, setProjectLoading] = useState(false),
    [projectError, setProjectError] = useState('');
  const [loginOpen, setLoginOpen] = useState(false),
    [username, setUsername] = useState('researcher'),
    [password, setPassword] = useState(''),
    [loginError, setLoginError] = useState(''),
    [busy, setBusy] = useState(false);
  const dialog = useRef<HTMLDialogElement>(null),
    main = useRef<HTMLElement>(null),
    returnFocus = useRef<HTMLElement | null>(null);
  function navigate(page: Page, options: Partial<Route> = {}) {
    const next = { ...route, page, run: undefined, ...options };
    const hash = routeHash(next);
    if (location.hash !== hash) location.hash = hash;
    else setRoute(next);
  }
  useEffect(() => {
    const sync = () => {
      if (['#main-content', '#reading-assistant', '#workflow'].includes(location.hash)) return;
      setRoute(parseRoute(location.hash));
    };
    window.addEventListener('hashchange', sync);
    return () => window.removeEventListener('hashchange', sync);
  }, []);
  useEffect(() => {
    document.title = `${BRAND.name} · ${BRAND.chinese} | ${pageNames[route.page]}`;
    main.current?.focus({ preventScroll: true });
    if (!route.run) window.scrollTo({ top: 0, behavior: 'instant' });
  }, [route.page]);
  useEffect(() => {
    if (!loginOpen) return;
    returnFocus.current = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    dialog.current?.showModal();
    dialog.current?.querySelector('input')?.focus();
    return () => {
      dialog.current?.close();
      returnFocus.current?.focus();
    };
  }, [loginOpen]);
  async function loadProjects(current: User) {
    setProjectLoading(true);
    setProjectError('');
    try {
      const rows: Project[] = await api('/projects');
      setProjects(rows);
      let previous = '';
      try {
        previous = sessionStorage.getItem('scholarship:project:' + current.id) ?? '';
      } catch {}
      setProject(rows.find((p) => p.id === previous)?.id ?? rows[0]?.id ?? '');
    } catch (e) {
      setProjects([]);
      setProject('');
      setProjectError(errorMessage(e));
    } finally {
      setProjectLoading(false);
    }
  }
  useEffect(() => {
    let stopped = false;
    void api('/auth/me')
      .then((current: User) => {
        if (!stopped) {
          setUser(current);
          void loadProjects(current);
        }
      })
      .catch(() => {})
      .finally(() => {
        if (!stopped) setAuthLoading(false);
      });
    return () => {
      stopped = true;
    };
  }, []);
  function changeProject(id: string) {
    setProject(id);
    if (user)
      try {
        sessionStorage.setItem('scholarship:project:' + user.id, id);
      } catch {}
    navigate(route.page, { versions: [], run: undefined });
  }
  function created(p: Project) {
    setProjects((old) => [...old, p]);
    setProject(p.id);
    if (user)
      try {
        sessionStorage.setItem('scholarship:project:' + user.id, p.id);
      } catch {}
    navigate('library', { versions: [] });
  }
  async function login(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setLoginError('');
    try {
      const current: User = await api('/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });
      setUser(current);
      setPassword('');
      setLoginOpen(false);
      await loadProjects(current);
    } catch (e) {
      setLoginError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  async function logout() {
    setBusy(true);
    try {
      await api('/auth/logout', { method: 'POST' });
      setUser(null);
      setProjects([]);
      setProject('');
      navigate('home', { versions: [] });
    } catch (e) {
      setProjectError(errorMessage(e));
    } finally {
      setBusy(false);
    }
  }
  function start() {
    navigate('library');
    if (!user && !authLoading) setLoginOpen(true);
  }
  function scope(ids: string[]) {
    navigate(route.page, { versions: ids, tab: route.tab });
  }
  function read(version: string, tab: ReadingTab = 'document') {
    navigate('read', { versions: [version], tab });
  }
  const moduleId: Partial<Record<Page, number>> = { discover: 2, graph: 3, write: 4, charts: 5 };
  const protectedPage = !['home', 'tools'].includes(route.page);
  const activeNav = ['discover', 'graph', 'write', 'charts', 'settings', 'tools'].includes(route.page)
    ? 'tools'
    : route.page;
  function content() {
    if (route.page === 'home')
      return <Home signedIn={!!user} project={project} onNavigate={navigate} onStart={start} />;
    if (route.page === 'tools')
      return <Tools onNavigate={(page, tab) => navigate(page, { tab: tab ?? route.tab })} />;
    if (authLoading || projectLoading)
      return (
        <section className="empty-state" role="status">
          <h1>正在打开研究空间…</h1>
          <p>正在核对账号与当前空间，已有资料不会改变。</p>
        </section>
      );
    if (!user)
      return (
        <section className="empty-state">
          <BrandMark size={64} />
          <h1>进入你的研究空间</h1>
          <p>登录后导入文献、阅读原文，找回自己的任务与成果。</p>
          <button className="primary" onClick={() => setLoginOpen(true)}>
            登录并继续
          </button>
          <button className="text-link" onClick={() => navigate('home')}>
            返回首页了解流程
          </button>
        </section>
      );
    if (!project)
      return (
        <section className="empty-state">
          <Icon name="book" size={36} />
          <h1>{projectError ? '研究空间暂时未能读取' : '先为研究建一个空间。'}</h1>
          <p>
            {projectError || '一个课题一个空间，文献、问题与成果集中保存。首次创建默认不会发送云端请求。'}
          </p>
          {projectError ? (
            <button onClick={() => void loadProjects(user)}>重新读取空间</button>
          ) : user.role === 'researcher' ? (
            <WorkspaceCreate onCreated={created} initialOpen />
          ) : (
            <p>当前只读账号尚无研究空间，请使用已分配的账号查看资料。</p>
          )}
        </section>
      );
    const key = `${user.id}:${project}`;
    if (route.page === 'library')
      return (
        <>
          <div className="page-title">
            <h1>你的文献，从这里出发。</h1>
            <p>先检查原件与版本，再打开阅读。文献准备、问答索引与内容核对分别展示。</p>
          </div>
          <Library
            key={key}
            project={project}
            role={user.role}
            onRead={(id) => read(id)}
            onResearch={(id) => read(id, 'qa')}
          />
        </>
      );
    if (route.page === 'read')
      return (
        <Reader
          key={key}
          project={project}
          role={user.role}
          versions={route.versions}
          tab={route.tab}
          run={route.run}
          onScope={scope}
          onTab={(tab) => navigate('read', { tab })}
          onLibrary={() => navigate('library')}
          onResults={() => navigate('results')}
        />
      );
    if (route.page === 'results')
      return (
        <Results
          key={key}
          project={project}
          role={user.role}
          onNavigate={(page, run, tab, versions) =>
            navigate(page, { run, tab: tab ?? route.tab, versions: versions ?? route.versions })
          }
        />
      );
    if (route.page === 'settings')
      return (
        <>
          <div className="page-title">
            <h1>模型与运行设置</h1>
            <p>模型配置、执行准入与实际内容质量分别记录。修改配置不会自动启用推理。</p>
          </div>
          <ServiceStatus key={key} userId={user.id} />
          <ModelConfiguration key={'models:' + key} project={project} role={user.role} />
          <details className="developer-tools">
            <summary>开发与离线流程检查</summary>
            <p>在独立练习空间使用模拟模型，只验证软件流程。不要在真实文献空间覆盖活动索引。</p>
            <SystemProbe key={'probe:' + key} project={project} role={user.role} />
            <OfflineResearch key={'offline:' + key} project={project} userId={user.id} role={user.role} />
          </details>
        </>
      );
    return (
      <>
        <div className="page-title">
          <button className="text-link" onClick={() => navigate('tools')}>
            研究工具
          </button>
          <h1>{pageNames[route.page]}</h1>
          <p>
            {route.page === 'discover'
              ? '搜索公开元数据、跟踪新文献；向自己的文献提问请进入阅读与问答。'
              : route.page === 'graph'
                ? '从选定文献的真实引用出发，连接局域图谱、来源综述与后续证据。'
                : route.page === 'charts'
                  ? '使用自己的 CSV 和明确的节点连线，保留数据、配置、图片与脚本。'
                  : '从明确的 Idea 与文献来源开始，整理仍需核对的摘要和论文框架。'}
          </p>
          {route.page === 'write' && (
            <div className="action-row">
              <button onClick={() => navigate('charts')}>数据图表与架构拓扑</button>
              <button onClick={() => navigate('read', { tab: 'translation' })}>中译英与阅读翻译</button>
            </div>
          )}
        </div>
        <Studio
          key={key + ':' + moduleId[route.page]}
          project={project}
          role={user.role}
          module={moduleId[route.page]!}
          versionIds={route.versions}
          onScopeChange={scope}
          focusRunId={route.run}
          onResults={() => navigate('results')}
        />
      </>
    );
  }
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        跳至主要内容
      </a>
      <header className="app-header">
        <a
          className="app-brand"
          href={routeHash({ ...route, page: 'home', run: undefined })}
          aria-label="ScholarShip 学者之舟，回到首页"
        >
          <Brand />
        </a>
        <nav className="app-nav" aria-label="主导航">
          {(['home', 'library', 'read', 'results'] as Page[]).map((page) => (
            <a
              key={page}
              href={routeHash({ ...route, page, run: undefined })}
              aria-current={activeNav === page ? 'page' : undefined}
            >
              {pageNames[page]}
            </a>
          ))}
        </nav>
        <div className="app-header-end">
          <a
            href={routeHash({ ...route, page: 'tools', run: undefined })}
            aria-current={activeNav === 'tools' ? 'page' : undefined}
          >
            研究工具
          </a>
          <button
            className="account-button"
            onClick={() => (user ? navigate('settings') : setLoginOpen(true))}
          >
            {user ? user.display_name : '登录研究空间'}
          </button>
        </div>
      </header>
      <div className="app-context">
        <span className="context-tag">{protectedPage ? pageNames[route.page] : BRAND.slogan}</span>
        {user ? (
          <>
            <label>
              研究空间{' '}
              <select aria-label="研究空间" value={project} onChange={(e) => changeProject(e.target.value)}>
                {!projects.length && <option value="">{projectLoading ? '读取中…' : '尚无空间'}</option>}
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
            {user.role === 'researcher' && !!projects.length && <WorkspaceCreate onCreated={created} />}
            <button className="text-link context-settings" onClick={() => navigate('settings')}>
              设置
            </button>
            <button className="text-link" disabled={busy} onClick={() => void logout()}>
              退出
            </button>
          </>
        ) : (
          <span className="context-hint">以文献为起点，让理解有据可循。</span>
        )}
      </div>
      {projectError && project && (
        <p role="alert" className="error">
          {projectError}
        </p>
      )}
      <main
        ref={main}
        className={'app-main ' + (route.page === 'read' ? 'app-main-wide' : '')}
        id="main-content"
        tabIndex={-1}
      >
        <Suspense
          fallback={
            <p role="status" className="loading-state">
              正在加载研究工具…
            </p>
          }
        >
          {content()}
        </Suspense>
      </main>
      <footer className="app-footer">
        <span>
          {BRAND.name} · {BRAND.chinese}
        </span>
        <span>{BRAND.slogan}</span>
        <a href={routeHash({ ...route, page: 'tools', run: undefined })}>六大研究模块</a>
        <a href={routeHash({ ...route, page: 'settings', run: undefined })}>模型与运行状态</a>
      </footer>
      {loginOpen && (
        <dialog
          className="login-dialog"
          ref={dialog}
          onCancel={() => {
            setPassword('');
            setLoginOpen(false);
          }}
          onClick={(e) => {
            if (e.target === e.currentTarget) {
              const box = e.currentTarget.getBoundingClientRect();
              if (
                e.clientX < box.left ||
                e.clientX > box.right ||
                e.clientY < box.top ||
                e.clientY > box.bottom
              ) {
                setPassword('');
                setLoginOpen(false);
              }
            }
          }}
        >
          <button
            className="dialog-close"
            aria-label="关闭登录"
            onClick={() => {
              setPassword('');
              setLoginOpen(false);
            }}
          >
            ×
          </button>
          <BrandMark size={56} />
          <h2>进入你的研究空间</h2>
          <p>文献、任务与成果按账号隔离。</p>
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
            {loginError && (
              <p className="error" role="alert">
                {loginError}
              </p>
            )}
            <button className="primary" disabled={busy}>
              {busy ? '正在登录…' : '登录并继续'}
            </button>
          </form>
        </dialog>
      )}
    </div>
  );
}
class RenderBoundary extends React.Component<{ children: React.ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() {
    return { failed: true };
  }
  render() {
    return this.state.failed ? (
      <section className="empty-state">
        <h1>页面暂时未能显示</h1>
        <p>已保存的资料不受影响。请重新加载页面；不会自动重发任务。</p>
        <button onClick={() => location.reload()}>重新加载页面</button>
      </section>
    ) : (
      this.props.children
    );
  }
}
// Vite may reevaluate this entry during development. Retain the root so updates
// cannot create competing React trees on the same container.
const root: Root = import.meta.hot?.data.root ?? createRoot(document.getElementById('root')!);
if (import.meta.hot) import.meta.hot.data.root = root;
root.render(
  <RenderBoundary>
    <App />
  </RenderBoundary>,
);
