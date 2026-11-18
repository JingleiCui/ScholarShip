"""Render five independent technical diagrams locally, without slide furniture.

Edit the geometry and labels below. PNG export uses local librsvg.
No model inference, application request or external asset is needed.
"""
from html import escape
from pathlib import Path
import shutil
import subprocess
import xml.etree.ElementTree as ET

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / 'docs/assets/architecture'
PNG_WIDTH = 2400
EXPORT_PNG = True
RSVG_CONVERT = shutil.which('rsvg-convert')
FONT = 'Noto Sans CJK SC, Microsoft YaHei, PingFang SC, sans-serif'
STROKE = '#4569A8'
GROUP_STROKE = '#719DD0'
FILL = '#E9F0FC'
BLUE = '#3763BA'
INK = '#202936'


class Diagram:
    def __init__(self, key, width, height, description):
        self.key = key
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title">',
            f'<title id="title">{escape(description)}</title>',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" '
            'markerWidth="6" markerHeight="6" orient="auto-start-reverse">'
            f'<path d="M 0 0 L 10 5 L 0 10 z" fill="{STROKE}"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="white"/>',
            f'<g font-family="{FONT}" fill="{INK}" font-size="18">',
        ]

    def rect(self, x, y, w, h, fill=FILL, stroke=GROUP_STROKE, dashed=False, radius=2):
        dash = ' stroke-dasharray="5 4"' if dashed else ''
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" '
                          f'fill="{fill}" stroke="{stroke}" stroke-width="1"{dash}/>')

    def text(self, x, y, lines, size=18, color=INK, center=True):
        if isinstance(lines, str):
            lines = [lines]
        anchor = 'middle' if center else 'start'
        gap = size * 1.45
        first = y - (len(lines) - 1) * gap / 2
        for i, line in enumerate(lines):
            self.parts.append(f'<text x="{x}" y="{first + i * gap}" text-anchor="{anchor}" '
                              f'dominant-baseline="central" font-size="{size}" '
                              f'fill="{color}">{escape(line)}</text>')

    def node(self, x, y, w, h, label, fill=FILL, size=18, radius=2):
        self.rect(x, y, w, h, fill=fill, radius=radius)
        self.text(x + w / 2, y + h / 2, label, size)

    def group(self, x, y, w, h, label):
        self.rect(x, y, w, h, fill='white', dashed=True, radius=8)
        self.text(x + w / 2, y + 25, label, size=19)

    def path(self, points, arrow=True, dashed=False):
        dash = ' stroke-dasharray="5 4"' if dashed else ''
        marker = ' marker-end="url(#arrow)"' if arrow else ''
        d = 'M ' + ' L '.join(f'{x} {y}' for x, y in points)
        self.parts.append(f'<path d="{d}" fill="none" stroke="{STROKE}" stroke-width="1.3" '
                          f'stroke-linejoin="round"{dash}{marker}/>')

    def edge_label(self, x, y, label, size=15):
        width = sum(size if ord(c) > 255 else size * .57 for c in label) + 10
        self.rect(x - width / 2, y - size / 2 - 3, width, size + 6, fill='white', stroke='none', radius=0)
        self.text(x, y, label, size, STROKE)

    def database(self, x, y, w, h, label):
        self.parts.append(f'<path d="M {x} {y + 12} A {w / 2} 12 0 0 1 {x+w} {y+12} '
                          f'L {x+w} {y+h-12} A {w/2} 12 0 0 1 {x} {y+h-12} Z" '
                          f'fill="{FILL}" stroke="{GROUP_STROKE}"/>')
        self.parts.append(f'<ellipse cx="{x+w/2}" cy="{y+12}" rx="{w/2}" ry="12" '
                          f'fill="{FILL}" stroke="{GROUP_STROKE}"/>')
        self.text(x + w / 2, y + h / 2 + 7, label, 18)

    def save(self):
        self.parts.extend(['</g>', '</svg>'])
        svg = '\n'.join(self.parts) + '\n'
        ET.fromstring(svg)
        target = OUTPUT / f'{self.key}.svg'
        target.write_text(svg, encoding='utf-8')
        if EXPORT_PNG:
            if not RSVG_CONVERT:
                raise RuntimeError('Configure RSVG_CONVERT or set EXPORT_PNG=False.')
            subprocess.run([RSVG_CONVERT, '--width', str(PNG_WIDTH), '--output',
                            str(target.with_suffix('.png')), str(target)], check=True)


def overall():
    d = Diagram('a-overall', 1380, 760, 'ScholarShip 整体架构')
    for x, w, label in [(20, 210, '用户接入'), (260, 280, '应用接口与任务投递'),
                        (590, 400, '领域执行'), (1040, 320, '模型与外部服务')]:
        d.group(x, 20, w, 510, label)
    d.node(40, 90, 170, 58, '研究者', fill='white')
    d.node(40, 218, 170, 108, ['React 工作空间', '文献库 / 阅读', '问答 / 成果'])
    d.path([(125, 148), (125, 218)])
    d.node(285, 228, 230, 88, ['FastAPI', '身份、范围与输入校验'])
    d.path([(210, 272), (285, 272)])
    d.edge_label(247, 252, 'HTTP / SSE', 13)
    d.node(285, 352, 230, 66, ['任务 / 配置快照', 'Outbox（PostgreSQL）'])
    d.path([(400, 316), (400, 352)])
    d.node(285, 450, 106, 54, 'Beat', size=17)
    d.node(409, 450, 106, 54, ['Redis', 'Broker'], size=17)
    d.path([(338, 418), (338, 450)])
    d.path([(391, 477), (409, 477)])
    d.node(620, 80, 340, 76, ['Celery Worker', '租约 / 心跳 / 取消 / 恢复'])
    d.path([(515, 477), (565, 477), (565, 118), (620, 118)])
    d.node(620, 225, 340, 66, ['文献导入与解析', '文件版本 / 页面 / 原文块'])
    d.node(620, 326, 340, 66, ['LangGraph 证据问答', 'retrieve → rerank → answer → publish'], size=16)
    d.node(620, 427, 340, 66, ['Studio 操作分派', '翻译 / 发现 / 图谱 / 写作 / 绘图'], size=17)
    d.path([(790, 156), (790, 190), (605, 190), (605, 460)], arrow=False)
    for y in (258, 359, 460):
        d.path([(605, y), (620, y)])
    d.node(1065, 80, 270, 76, ['Model Gateway', '角色端口 / 模型与协议快照'])
    d.node(1065, 225, 270, 66, ['生产准入', '来源范围 / 开关 / 额度'])
    d.node(1065, 326, 270, 66, ['协议适配 / HTTP 传输', '生成 / 翻译 / 向量 / 重排 / OCR'], size=16)
    d.node(1065, 427, 270, 66, '配置的云端服务')
    d.path([(960, 359), (1015, 359), (1015, 118), (1065, 118)])
    d.path([(960, 460), (1015, 460), (1015, 359)], arrow=False)
    d.path([(1200, 156), (1200, 225)])
    d.path([(1200, 291), (1200, 326)])
    d.path([(1200, 392), (1200, 427)])
    d.group(260, 586, 1100, 154, '数据存储')
    d.database(285, 636, 310, 80, ['PostgreSQL', '业务状态 / 来源 / 回执 / 事件'])
    d.database(653, 636, 310, 80, ['Qdrant', 'Dense + BM25 / 索引世代'])
    d.node(1020, 636, 315, 80, ['持久化文件目录', '原件 / 解析 / 研究产物'])
    d.path([(790, 530), (790, 558)], arrow=False)
    d.path([(440, 558), (1177, 558)], arrow=False)
    for x in (440, 808, 1177):
        d.path([(x, 558), (x, 586)])
    d.path([(285, 385), (245, 385), (245, 676), (285, 676)], dashed=True)
    d.save()


def layer_row(d, y, label, cells, height=110):
    d.rect(20, y, 170, height, fill=BLUE, stroke=BLUE)
    d.text(105, y + height / 2, label, size=19, color='white')
    d.rect(210, y, 1130, height, fill='white', dashed=True, radius=6)
    cell_width = (1094 - (len(cells) - 1) * 18) / len(cells)
    for i, content in enumerate(cells):
        d.node(228 + i * (cell_width + 18), y + 18, cell_width, height - 36, content, size=18)


def layered():
    d = Diagram('b-layered', 1360, 800, 'ScholarShip 分层架构')
    layers = [
        (['交互呈现层', 'frontend/src'], [['研究空间与导航', 'React / TypeScript'], ['原文与双语阅读', 'PDF.js / KaTeX'], ['图谱与图形呈现', 'Cytoscape / Mermaid']]),
        (['应用接口层', 'api'], [['文献 / 问答 / Studio API', 'FastAPI / Pydantic'], ['会话 / CSRF / 项目范围', '输入校验 / 幂等键'], ['结果与文件交付', '状态查询 / SSE 事件']]),
        (['领域编排层', 'domains'], [['文献与解析', '版本 / 原文块 / 检索'], ['qa_graph', '四节点证据问答'], ['Studio workflows', '翻译 / 图谱 / 写作 / 绘图']]),
        (['模型接入层', 'models'], [['Catalog / Configuration', '能力目录 / 固定配置'], ['Gateway / Protocols', '角色端口 / 协议适配'], ['Production / Transports', '调用准入 / 额度 / HTTP']]),
        (['任务执行层', 'workers'], [['Celery Worker / Beat', '任务执行 / 调度'], ['Outbox / Lease', '持久投递 / 租约 / 心跳'], ['检查点 / 持久事件', '恢复 / 安全取消 / 回执']]),
        (['基础设施层', 'infrastructure'], [['PostgreSQL / SQLAlchemy Core', '租户事务 / RLS / 归属约束'], ['Qdrant / Redis', '派生索引 / 消息投递'], ['持久化文件 / Alembic', '文件清单 / 前向迁移']]),
    ]
    for i, (label, cells) in enumerate(layers):
        layer_row(d, 20 + i * 130, label, cells)
    d.save()


def langgraph():
    d = Diagram('c-langgraph', 1080, 730, 'ScholarShip LangGraph 证据问答工作流')
    d.group(270, 20, 450, 690, 'StateGraph / QAState')
    d.node(435, 66, 120, 36, 'START', fill='white', size=16, radius=18)
    graph_nodes = [
        (140, ['retrieve', '结构化证据 / Dense + BM25 / RRF']),
        (274, ['rerank', '候选校验 / 模型重排 / 证据选择']),
        (408, ['answer', '证据约束生成 / 结构答案 / 拒答']),
        (542, ['publish', '取消检查 / 引用校验 / 结果入库']),
    ]
    for y, label in graph_nodes:
        d.node(300, y, 390, 74, label, size=18)
    d.path([(495, 102), (495, 140)])
    for start, end in [(214, 274), (348, 408), (482, 542)]:
        d.path([(495, start), (495, end)])
    d.node(435, 654, 120, 36, 'END', fill='white', size=16, radius=18)
    d.path([(495, 616), (495, 654)])
    d.node(20, 140, 200, 74, ['固定任务输入', '问题 / 来源 / 模型快照'], fill='white', size=17)
    d.path([(220, 177), (300, 177)])
    d.node(20, 274, 200, 208, ['统一模型 Gateway', '', 'Query Embedding', 'Rerank', 'Chat'], size=18)
    d.path([(300, 195), (246, 195), (246, 311), (220, 311)])
    d.path([(300, 311), (220, 311)])
    d.path([(300, 445), (220, 445)])
    d.database(780, 115, 270, 100, ['Qdrant', '按固定来源范围检索'])
    d.path([(690, 177), (780, 177)])
    d.database(780, 315, 270, 100, ['PostgresSaver', '固定 thread_id / 同步持久化'])
    d.path([(748, 203), (748, 579)], arrow=False, dashed=True)
    for y in (203, 311, 445, 579):
        d.path([(690, y), (748, y)], arrow=False, dashed=True)
    d.path([(748, 365), (780, 365)], dashed=True)
    d.node(780, 630, 270, 74, ['PostgreSQL', '证据 / 回答 / 引用 / 轨迹'])
    d.path([(690, 590), (734, 590), (734, 667), (780, 667)])
    d.save()


def business():
    d = Diagram('d-business', 1360, 650, 'ScholarShip 业务架构')
    d.group(20, 20, 1320, 108, '研究工作空间')
    for i, label in enumerate(['文献库', '阅读与问答', '研究工具', '成果']):
        d.node(40 + i * 325, 64, 305, 44, label)
    modules = [
        ('文献管理与解析', ['PDF 导入', '重复候选与版本', '文本层 / OCR 结果', '页面与原文块']),
        ('检索与证据问答', ['固定文献范围', 'Dense + BM25', '重排与证据回答', '引用定位原文']),
        ('双语阅读', ['PDF / 大纲跳转', '选段 / 全文翻译', '双语联动阅读', '人工修订与导出']),
        ('文献发现与跟踪', ['arXiv / Crossref', '订阅与反馈评分', '允许来源下载', '文献身份核对']),
        ('图谱与研究方向', ['局域引用图', '候选边 / PageRank', '图谱来源摘要', '方向与证据核查']),
        ('写作与绘图', ['来源摘要与框架', 'CSV 受控图表', '配置与 Python 脚本', 'Mermaid 图形脚本']),
    ]
    for i, (title, items) in enumerate(modules):
        x = 20 + i * 224
        d.group(x, 158, 200, 298, title)
        for j, label in enumerate(items):
            d.node(x + 12, 210 + j * 58, 176, 42, label, size=17)
    d.group(20, 486, 1320, 144, '共享业务基础')
    for i, label in enumerate([['来源与版本', '文献 → 版本 → 解析 → 原文块'], ['任务与执行记录', '状态 / 配置 / 回执 / 事件'], ['产物与审阅', '来源清单 / 修订 / 导出']]):
        d.node(40 + i * 435, 538, 415, 70, label)
    d.save()


def technology():
    d = Diagram('e-technology', 1360, 820, 'ScholarShip 技术与部署架构')
    d.node(540, 20, 280, 64, ['Web Browser', 'React / TypeScript / PDF.js / KaTeX'], size=16, fill='white')
    d.group(20, 120, 230, 190, '本地开发入口（二选一）')
    d.node(40, 178, 190, 100, ['Vite Dev Server', '项目 Node / pnpm', '127.0.0.1:15173'], size=17)
    d.group(280, 120, 1060, 560, '')
    d.text(1050, 145, 'Docker Compose / scholarship', size=19)
    d.node(540, 175, 280, 64, ['Caddy', 'SPA 静态文件 / API 反向代理'])
    d.path([(680, 84), (680, 175)])
    d.node(540, 294, 280, 84, ['api', 'FastAPI / Uvicorn / Pydantic', 'SQLAlchemy Core / Psycopg'], size=17)
    d.path([(680, 239), (680, 294)])
    d.path([(540, 52), (135, 52), (135, 120)], dashed=True)
    d.path([(230, 228), (265, 228), (265, 266), (600, 266), (600, 294)], dashed=True)
    d.node(315, 294, 185, 84, ['beat', 'Celery Beat', 'Outbox 调度'], size=17)
    d.node(860, 294, 440, 84, ['worker / Python 3.12', 'Celery / LangGraph / pdfplumber / PyPDF', 'NetworkX / NumPy / pandas / Matplotlib'], size=17)
    for x in (407, 680, 1080):
        d.path([(x, 378), (x, 418)], arrow=False)
    d.path([(407, 418), (1190, 418)], arrow=False)
    resources = [(315, 'PostgreSQL 17', '业务 / RLS / 检查点'), (565, 'Redis 7.4', '消息 Broker'),
                 (815, 'Qdrant 1.20', 'Dense + BM25'), (1065, '共享文件目录', 'PDF / 解析 / 产物')]
    for x, title, detail in resources:
        d.path([(x + 117, 418), (x + 117, 470)])
        d.database(x, 470, 235, 86, [title, detail])
    d.node(315, 594, 985, 56, 'model_config.py → Catalog / Gateway → Protocols / Adapters → HTTPX → 云端服务', size=18)
    d.path([(1300, 336), (1320, 336), (1320, 622), (1300, 622)])
    d.group(20, 710, 1320, 90, '工程交付')
    for i, label in enumerate(['项目 .venv / uv.lock', 'Node / pnpm-lock', 'Alembic 前向迁移', 'pytest / Vitest / validate.py']):
        d.node(40 + i * 325, 750, 305, 32, label, size=16)
    d.save()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for draw in (overall, layered, langgraph, business, technology):
        draw()
    print('Rendered five independent SVG/PNG diagrams.')


if __name__ == '__main__':
    main()
