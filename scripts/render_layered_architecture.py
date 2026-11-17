"""Editable layer layouts for the implemented system; no external generation service."""

from pathlib import Path
from matplotlib.figure import Figure
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from render_architecture import FONT

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "docs/assets/diagrams"
DPI = 180
LAYOUTS = {
    "system-layered": (
        "ScholarShip · 技术架构",
        [
            (
                "交互层",
                [
                    "文献与版本\n批量导入 / 候选核对",
                    "阅读与证据\nPDF.js / KaTeX",
                    "研究工作室\n图谱 / 写作 / 绘图",
                    "任务视图\n状态 / SSE / 取消",
                ],
            ),
            (
                "应用接口层",
                [
                    "FastAPI 路由\n请求校验 / 幂等键",
                    "会话与项目边界\n身份 / 角色 / owner",
                    "产物交付\n原件 / 导出 / 修订",
                ],
            ),
            (
                "领域编排层",
                [
                    "文献解析\n文本层 / MinerU 产物",
                    "LangGraph 问答\n检索 → 生成 → 引用校验",
                    "阅读与研究\n翻译 / 图谱 / 来源摘要",
                    "分析与绘图\nCSV / 受控 Python",
                ],
            ),
            (
                "模型接入层",
                [
                    "统一 Gateway\n任务配置快照",
                    "协议适配\n生成 / 向量 / 重排",
                    "生产准入\n范围 / 额度 / unknown",
                    "HTTP 传输\n超时 / TLS / 无隐式重试",
                ],
            ),
            (
                "异步执行层",
                [
                    "事务 Outbox\n持久投递意图",
                    "Celery + Redis\n队列 / Worker / Beat",
                    "恢复与事件\n租约 / 心跳 / 安全取消",
                ],
            ),
            (
                "数据资源层",
                [
                    "PostgreSQL\n权威状态 / RLS / 来源",
                    "Qdrant\n向量 + BM25 / 索引世代",
                    "持久化文件目录\nPDF / 图像 / 脚本",
                    "云端服务\n聊天 / 向量 / 重排 / OCR",
                ],
            ),
        ],
    ),
    "business-layered": (
        "ScholarShip · 业务架构",
        [
            ("用户目标", ["收纳与比较文献", "阅读并核对结论", "发现关联与研究方向", "整理可复用研究产物"]),
            (
                "工作入口",
                [
                    "研究空间\n资料范围与隔离",
                    "文献库\n文件版本与解析修订",
                    "阅读工作台\n原文 / 引用 / 译文",
                    "研究工作室\n图谱 / 写作 / 绘图",
                ],
            ),
            (
                "研究主流程",
                [
                    "导入资料\n去重候选 / 版本核对",
                    "解析与建立索引\n页面块 / 向量 / 重排",
                    "提问与阅读\n回答 / 引用 / PDF 定位",
                    "核验并整理\n修订 / 草稿 / 导出",
                ],
            ),
            (
                "支撑能力",
                [
                    "发现文献\n公开元数据 / 开放 PDF",
                    "引用网络\n身份核对 / 局域中心性",
                    "研究方向复核\n双来源全文证据",
                    "数据图表\n输入 / 配置 / 生成脚本",
                ],
            ),
            (
                "可信机制",
                [
                    "来源固定\n文件哈希 / 解析版本",
                    "结果可核对\n原文位置 / 证据跨度",
                    "历史可追溯\n不可变产物 / 人工审阅",
                    "执行可控制\n取消 / 配额 / 失败恢复",
                ],
            ),
            ("交付产物", ["文献与阅读记录", "带引用的回答与译文", "来源摘要与论文框架", "图谱 / 图表 / 数据与脚本"]),
        ],
    ),
}


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for name, (title, layers) in LAYOUTS.items():
        fig = Figure(figsize=(16, 12), facecolor="white")
        ax = fig.subplots()
        ax.set(xlim=(0, 16), ylim=(0, 12))
        ax.axis("off")
        ax.text(0.35, 11.5, title, fontfamily=FONT, fontsize=25, weight="bold", color="#163653")
        ax.text(
            0.35,
            11.05,
            "分层说明职责与依赖；纵向箭头表示层间协作，不代表每次请求遍历全部模块。",
            fontfamily=FONT,
            fontsize=11,
            color="#52667a",
        )
        for i, (label, cells) in enumerate(layers):
            y = 9.35 - i * 1.65
            ax.add_patch(
                FancyBboxPatch(
                    (0.3, y),
                    15.4,
                    1.35,
                    boxstyle="round,pad=0.02,rounding_size=0.12",
                    facecolor="#f1f6fd",
                    edgecolor="#c6d7ed",
                )
            )
            ax.text(0.55, y + 0.7, label, fontfamily=FONT, fontsize=13, weight="bold", color="#225491", va="center")
            width = 12.6 / len(cells)
            for j, t in enumerate(cells):
                x = 2.65 + j * width
                ax.add_patch(
                    FancyBboxPatch(
                        (x, y + 0.18),
                        width - 0.18,
                        0.98,
                        boxstyle="round,pad=0.02,rounding_size=0.08",
                        facecolor="white",
                        edgecolor="#b7cce7",
                    )
                )
                ax.text(
                    x + (width - 0.18) / 2,
                    y + 0.67,
                    t,
                    fontfamily=FONT,
                    fontsize=12,
                    ha="center",
                    va="center",
                    linespacing=1.65,
                    color="#193953",
                )
            if i < 5:
                ax.add_patch(
                    FancyArrowPatch(
                        (8, y - 0.03),
                        (8, y - 0.27),
                        arrowstyle="-|>",
                        mutation_scale=14,
                        color="#729acb",
                        linewidth=1.6,
                    )
                )
        ax.text(
            0.35, 0.25, "ScholarShip · 学者之舟    |    学海无涯，AI作舟", fontfamily=FONT, fontsize=11, color="#52667a"
        )
        for extension in ["svg", "png"]:
            fig.savefig(OUTPUT / f"{name}.{extension}", dpi=DPI, bbox_inches="tight")


if __name__ == "__main__":
    main()
