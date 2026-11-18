export type Page =
  'home' | 'library' | 'read' | 'results' | 'tools' | 'discover' | 'graph' | 'write' | 'charts' | 'settings';
export type ReadingTab = 'document' | 'qa' | 'translation';
export type Route = { page: Page; versions: string[]; tab: ReadingTab; run?: string };
const pages: Page[] = [
  'home',
  'library',
  'read',
  'results',
  'tools',
  'discover',
  'graph',
  'write',
  'charts',
  'settings',
];
const uuid = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function validIds(ids: string[]): string[] {
  return [...new Set(ids.filter((x) => uuid.test(x)))].slice(0, 30);
}
export function parseRoute(hash: string): Route {
  const params = new URLSearchParams(hash.replace(/^#/, ''));
  const legacy = params.get('module');
  const oldPages: Page[] = ['library', 'read', 'discover', 'graph', 'write', 'charts'];
  const requested = params.get('page') as Page;
  const page = pages.includes(requested)
    ? requested
    : legacy !== null && /^[0-5]$/.test(legacy)
      ? oldPages[Number(legacy)]
      : 'home';
  const tab = params.get('tab');
  return {
    page,
    versions: validIds((params.get('versions') ?? '').split(',')),
    tab: tab === 'qa' || tab === 'translation' ? tab : legacy === '1' ? 'translation' : 'document',
    run: uuid.test(params.get('run') ?? '') ? params.get('run')! : undefined,
  };
}
export function routeHash(route: Route): string {
  const params = new URLSearchParams({ page: route.page });
  const ids = validIds(route.versions);
  if (ids.length) params.set('versions', ids.join(','));
  if (route.tab !== 'document') params.set('tab', route.tab);
  if (route.run && uuid.test(route.run)) params.set('run', route.run);
  return '#' + params;
}
export const pageNames: Record<Page, string> = {
  home: '首页',
  library: '文献库',
  read: '阅读与问答',
  results: '成果',
  tools: '研究工具',
  discover: '文献发现与追踪',
  graph: '引用图谱与研究方向',
  write: '学术写作',
  charts: '数据绘图与拓扑',
  settings: '模型与运行设置',
};
export const modules = [
  {
    code: 'F1',
    title: 'PDF 管理与细粒度解析',
    page: 'library' as Page,
    icon: 'file',
    description: '单篇与批量导入、正文候选核对、版本比较、文本结构与原文预览。',
    note: '扫描件使用已授权的 OCR 修订；原文件保留。',
  },
  {
    code: 'F2',
    title: '证据问答与多文对比',
    page: 'read' as Page,
    icon: 'source',
    description: '选择一篇或多篇文献，带着固定来源范围提问，点击引用回到原文。',
    note: '真实问答需匹配索引与执行准入；无证据明确说明。',
  },
  {
    code: 'F3',
    title: '版式阅读与双语翻译',
    page: 'read' as Page,
    icon: 'book',
    description: 'PDF 原件、标题导航、引用往返、选词选段与全文翻译、双语联动。',
    note: '历史译文可回看；公式与代码保留，译文可人工修订。',
  },
  {
    code: 'F4',
    title: '文献发现、追踪与推荐',
    page: 'discover' as Page,
    icon: 'search',
    description: '检索公开元数据与开放全文、arXiv 追踪、代码链接核验与推荐反馈。',
    note: '检索将发送查询词；可用全文与文献身份分别核对。',
  },
  {
    code: 'F5',
    title: '引用图谱、综述与研究方向',
    page: 'graph' as Page,
    icon: 'graph',
    description: '选择多篇文献构图，查看局域中心性，整理来源综述并对照后续证据。',
    note: '候选关系保留标记；未查到不能证明方向未被研究。',
  },
  {
    code: 'F6',
    title: '写作与可视化 Copilot',
    page: 'write' as Page,
    icon: 'pen',
    description: '从 Idea 到来源框架、中译英、CSV 图表与图注、可下载架构拓扑脚本。',
    note: '草稿需核对；图表同时保留数据、配置与脚本。',
  },
];
