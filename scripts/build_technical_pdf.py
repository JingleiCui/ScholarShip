"""Build the private technical handout; uses the document runtime's ReportLab."""

from pathlib import Path
from xml.sax.saxutils import escape
import re
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, PageBreak, Image
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont

# USER CONFIGURATION — run with the bundled document Python; no app dependency changes.
ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "private/competition/technical-document.md"
OUTPUT = ROOT / "private/output/pdf/ScholarShip-technical-document.pdf"
PAGE_SIZE = (595.28, 841.89)
FONT = "ScholarShipChinese"
FONT_PATH = "/System/Library/Fonts/Supplemental/Arial Unicode.ttf"
DATE = "2026-11-17"


def main():
    pdfmetrics.registerFont(TTFont(FONT, FONT_PATH))
    normal = ParagraphStyle(
        "body", fontName=FONT, fontSize=11, leading=19, textColor=HexColor("#22364b"), spaceAfter=13, wordWrap="CJK"
    )
    title = ParagraphStyle(
        "title", parent=normal, fontSize=24, leading=34, textColor=HexColor("#184b80"), spaceAfter=22
    )
    small = ParagraphStyle("small", parent=normal, fontSize=10, leading=15, spaceAfter=3, textColor=HexColor("#63788d"))
    source = SOURCE.read_text()
    parts = re.split(r"^## ", source, flags=re.M)
    story = [
        Spacer(1, 35),
        Paragraph("ScholarShip", ParagraphStyle("brand", parent=title, fontSize=38, leading=48)),
        Paragraph("学者之舟 · 技术说明", title),
        Paragraph("学海无涯，AI作舟", normal),
        Spacer(1, 30),
    ]
    story.append(Paragraph(escape(parts[0].split("\n\n", 1)[1]).replace("\n", "<br/>"), normal))
    story.append(Spacer(1, 10))
    story.append(Paragraph("阅读路线", title))
    for p in parts[1:]:
        story.append(Paragraph(escape(p.split("\n", 1)[0]), small))
    for part in parts[1:]:
        heading, body = part.split("\n", 1)
        story += [PageBreak(), Paragraph(escape(heading), title)]
        for text in body.strip().split("\n\n"):
            match = re.fullmatch(r"\[图:(\w+)\]", text.strip())
            if match:
                story.append(Image(str(ROOT / "docs/assets/diagrams" / f"{match[1]}.png"), width=490, height=283))
            else:
                story.append(Paragraph(escape(text).replace("\n", "<br/>"), normal))

    def page(c, doc):
        c.setStrokeColor(HexColor("#c9daeb"))
        c.line(48, 790, 547, 790)
        c.setFont(FONT, 9)
        c.setFillColor(HexColor("#63788d"))
        c.drawString(48, 805, "ScholarShip  /  学者之舟")
        c.drawRightString(547, 805, DATE)
        c.drawString(48, 33, "技术说明 · 真实、模拟与待验范围分别记录")
        c.drawRightString(547, 33, str(doc.page))

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    SimpleDocTemplate(
        str(OUTPUT),
        pagesize=PAGE_SIZE,
        leftMargin=50,
        rightMargin=50,
        topMargin=70,
        bottomMargin=57,
        title="ScholarShip 技术文档",
        author="ScholarShip",
    ).build(story, onFirstPage=page, onLaterPages=page)
    print(OUTPUT)


if __name__ == "__main__":
    main()
