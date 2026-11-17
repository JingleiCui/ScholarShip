"""Render the reviewed architecture diagrams with the project's plotting stack."""

from pathlib import Path
from matplotlib.figure import Figure
from matplotlib import font_manager
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch

# USER CONFIGURATION
ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/assets/diagrams"
DPI = 180
FONT = next(
    (
        n
        for n in ["Noto Sans CJK SC", "Noto Sans CJK JP", "PingFang SC"]
        if n in {f.name for f in font_manager.fontManager.ttflist}
    ),
    "DejaVu Sans",
)
DIAGRAMS = {
    "architecture": (
        "ScholarShip 系统架构",
        [
            ("React 工作空间\nPDF.js · KaTeX", 1, 5),
            ("FastAPI\n身份 · 范围 · 输入校验", 4, 5),
            ("PostgreSQL\n任务 · 来源 · 版本 · 事件", 7, 5),
            ("Celery Worker\n执行与心跳", 4, 3),
            ("Outbox → Redis\nBeat 投递与恢复", 7, 3),
            ("LangGraph / 领域流程\n检索 · 翻译 · 写作", 4, 1),
            ("模型 Gateway\n协议 · 传输 · 准入", 1, 1),
            ("Qdrant / 文件目录\n索引与不可变产物", 7, 1),
        ],
        [(0, 1), (1, 2), (2, 4), (4, 3), (3, 5), (5, 6), (5, 7)],
    ),
    "evidence": (
        "证据流转与版本边界",
        [
            ("论文作品\npaper", 1, 5),
            ("文件版本\nversion + SHA-256", 4, 5),
            ("解析修订\nparse + 页面/块", 7, 5),
            ("回答与引用\nclaim + 原文范围", 1, 3),
            ("检索证据\n冻结来源与跨度", 4, 3),
            ("索引世代\nprofile + generation", 7, 3),
            ("原始 PDF 定位\n页码 · bbox · 字符范围", 1, 1),
            ("人工审阅\n追加记录，保留原结果", 4, 1),
            ("新模型 / 新解析\n候选验证后才切指针", 7, 1),
        ],
        [(0, 1), (1, 2), (2, 5), (5, 4), (4, 3), (3, 6), (3, 7), (8, 5)],
    ),
    "workflow": (
        "用户操作流程",
        [
            ("1 新建空间\n导入 PDF / 核对版本", 1, 5),
            ("2 解析并阅读\n扫描件需要 OCR", 4, 5),
            ("3 分篇建索引\n再选择来源提问", 7, 5),
            ("6 写作与导出\n摘要 · 框架 · 来源清单", 1, 3),
            ("5 检索与图谱\n参考身份 / 后续全文核对", 4, 3),
            ("4 原文核验\n引用 · 翻译 · 人工修订", 7, 3),
            ("CSV → 受控图表\n输入 + 配置 + Python", 1, 1),
            ("检查任务与事件\n失败分类 · 安全取消", 4, 1),
            ("区分执行模式\n本地 / 模拟 / 真实云端", 7, 1),
        ],
        [(0, 1), (1, 2), (2, 5), (5, 4), (4, 3), (3, 6), (4, 7), (5, 8)],
    ),
}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, (title, nodes, edges) in DIAGRAMS.items():
        fig = Figure(figsize=(12, 7), facecolor="#f6faff")
        ax = fig.subplots()
        ax.set(xlim=(-0.5, 8.5), ylim=(-0.1, 6.4))
        ax.axis("off")
        ax.text(-0.2, 6.1, title, fontfamily=FONT, fontsize=21, color="#153b67", weight="bold")
        for a, b in edges:
            _, x, y = nodes[a]
            _, xx, yy = nodes[b]
            dx, dy = xx - x, yy - y
            start = (x + (1.13 if dx > 0 else -1.13 if dx < 0 else 0), y + (0.47 if dy > 0 else -0.47 if dy < 0 else 0))
            end = (xx - (1.13 if dx > 0 else -1.13 if dx < 0 else 0), yy - (0.47 if dy > 0 else -0.47 if dy < 0 else 0))
            ax.add_patch(FancyArrowPatch(start, end, arrowstyle="-|>", mutation_scale=16, color="#6d92b9", lw=1.5))
        for label, x, y in nodes:
            ax.add_patch(
                FancyBboxPatch(
                    (x - 1.13, y - 0.47),
                    2.26,
                    0.94,
                    boxstyle="round,pad=.06",
                    facecolor="white",
                    edgecolor="#aac8e9",
                    lw=1.3,
                )
            )
            ax.text(
                x, y, label, ha="center", va="center", fontfamily=FONT, fontsize=11.5, color="#173e6c", linespacing=1.65
            )
        ax.text(-0.2, 0, "学者之舟  |  当前实现示意，非性能或准确率结果", fontfamily=FONT, fontsize=9, color="#58718a")
        for suffix in ["svg", "png"]:
            fig.savefig(OUTPUT / f"{name}.{suffix}", dpi=DPI, bbox_inches="tight")


if __name__ == "__main__":
    main()
