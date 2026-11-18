import { BRAND, Icon } from './Brand';
import { modules, type Page } from './navigation';
export default function Home({
  signedIn,
  project,
  onNavigate,
  onStart,
}: {
  signedIn: boolean;
  project: string;
  onNavigate: (page: Page) => void;
  onStart: () => void;
}) {
  return (
    <>
      <section className="home-hero">
        <div className="hero-copy">
          <p className="intro-kicker">{BRAND.slogan}</p>
          <h1>
            从一篇文献，
            <br />
            开始你的研究。
          </h1>
          <p className="hero-lead">
            读原文、提问题、核对出处，再整理成有来源的研究材料。ScholarShip 陪你把每一步串起来。
          </p>
          <div className="action-row">
            <button className="primary" onClick={onStart}>
              <Icon name="book" />
              {project ? '打开我的文献库' : signedIn ? '创建研究空间' : '开始阅读'}
            </button>
            <a className="quiet-button" href="#workflow">
              先了解使用流程
            </a>
          </div>
          <p className="helper">第一次使用？先走通一篇，再探索更多研究工具。</p>
        </div>
        <div
          className="hero-art"
          role="img"
          aria-label="工作方式示意：从文献提问，回答通过引用回到原文；不代表真实研究结果"
        >
          <div className="art-label">
            <Icon name="source" size={16} />
            原文始终是理解的起点
          </div>
          <div className="art-paper">
            <small>工作方式示意</small>
            <h3>
              一篇文献，
              <br />
              一个明确的问题。
            </h3>
            <div className="text-line" />
            <div className="text-line short" />
            <div className="text-line" />
            <div className="art-source">
              选定文献范围
              <br />
              保留来源与版本
            </div>
          </div>
          <div className="art-answer">
            <span>有来源的理解</span>
            <p>这个结论，原文是怎么说的？</p>
            <small>
              <Icon name="source" size={16} />
              点开引用，对照原文
            </small>
          </div>
        </div>
      </section>
      <section className="journey" id="workflow" aria-labelledby="journey-title">
        <div className="section-heading">
          <h2 id="journey-title">一条清晰的研究路径</h2>
          <p>从原件到成果，始终保留来源。</p>
        </div>
        <ol>
          {[
            ['导入文献', '选择空间，导入有权使用的 PDF。'],
            ['阅读与提问', '打开原文，选择单篇或多篇来源。'],
            ['核对证据', '点击回答引用，确认原文是否支持。'],
            ['整理成果', '回看摘要、译文、框架与图表。'],
          ].map(([title, desc], i) => (
            <li key={title}>
              <span className="step-number">{i + 1}</span>
              <div>
                <h3>{title}</h3>
                <p>{desc}</p>
              </div>
            </li>
          ))}
        </ol>
      </section>
      <section className="home-tasks">
        <div className="section-heading">
          <div>
            <h2>从你现在的任务出发</h2>
            <p>一个明确目标，就是开始的方向。</p>
          </div>
          <button className="text-link" onClick={() => onNavigate('tools')}>
            全部研究工具 <Icon name="arrow" size={18} />
          </button>
        </div>
        <div className="scenario-grid">
          <article>
            <Icon name="book" />
            <div>
              <h3>读懂一篇论文</h3>
              <p>原件阅读、双语对照与证据问答在同一处，逐步理清方法、结论和适用边界。</p>
              <button className="text-link" onClick={onStart}>
                选择一篇文献 <Icon name="arrow" size={18} />
              </button>
            </div>
          </article>
          <article>
            <Icon name="pen" />
            <div>
              <h3>整理一份研究材料</h3>
              <p>从选定来源组织摘要和写作框架，保留原文位置与版本，方便回看与修改。</p>
              <button className="text-link" onClick={() => onNavigate('results')}>
                查看已有成果 <Icon name="arrow" size={18} />
              </button>
            </div>
          </article>
        </div>
      </section>
      <div className="trust-strip">
        <span>
          <Icon name="source" size={19} />
          引用固定到原文位置
        </span>
        <span>
          <Icon name="layers" size={19} />
          修改保留历史版本
        </span>
        <span>
          <Icon name="shield" size={19} />
          本地处理与云端发送分别说明
        </span>
      </div>
    </>
  );
}
export function Tools({ onNavigate }: { onNavigate: (page: Page, tab?: 'qa' | 'translation') => void }) {
  return (
    <>
      <div className="page-title">
        <h1>需要时，打开一件研究工具。</h1>
        <p>六个功能模块保留完整入口；每次操作都有明确的来源、结果与下一步。</p>
      </div>
      <div className="tool-grid">
        {modules.map((m) => (
          <article className="tool-card" key={m.code}>
            <div className="tool-icon">
              <Icon name={m.icon} />
              <span>{m.code}</span>
            </div>
            <h2>{m.title}</h2>
            <p>{m.description}</p>
            <small>{m.note}</small>
            <div className="action-row">
              <button
                onClick={() =>
                  onNavigate(m.page, m.code === 'F2' ? 'qa' : m.code === 'F3' ? 'translation' : undefined)
                }
              >
                打开
                {
                  {
                    F1: '文献库',
                    F2: '证据问答',
                    F3: '阅读翻译',
                    F4: '文献发现',
                    F5: '引用图谱',
                    F6: '学术写作',
                  }[m.code]
                }{' '}
                <Icon name="arrow" size={18} />
              </button>
              {m.code === 'F6' && (
                <button className="text-link" onClick={() => onNavigate('charts')}>
                  数据绘图
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
      <aside className="inline-notice">
        <Icon name="shield" />
        <div>
          本地文献、图表与已保存成果可以独立使用。新的云端生成需有效执行准入；历史样本与模拟结果始终分别标注。
        </div>
      </aside>
    </>
  );
}
