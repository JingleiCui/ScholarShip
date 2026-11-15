"""Narrow, auditable column maxima and Decimal differences from original HTML tables."""

from decimal import Decimal, InvalidOperation
from html.parser import HTMLParser
import re


class Table(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.cell = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.rows.append([])
        if tag in {"td", "th"} and self.rows:
            attrs = dict(attrs)
            self.cell = {
                "text": "",
                "rowspan": int(attrs.get("rowspan", "1")),
                "colspan": int(attrs.get("colspan", "1")),
            }
            if not 1 <= self.cell["rowspan"] <= 20 or not 1 <= self.cell["colspan"] <= 20:
                raise ValueError()

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"] += data

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None:
            self.rows[-1].append(self.cell)
            self.cell = None


def grid(text):
    parser = Table()
    parser.feed(text)
    if not 3 <= len(parser.rows) <= 200:
        raise ValueError()
    occupied = {}
    for r, row in enumerate(parser.rows):
        col = 0
        for cell in row:
            while (r, col) in occupied:
                col += 1
            if col + cell["colspan"] > 20:
                raise ValueError()
            for y in range(r, r + cell["rowspan"]):
                for x in range(col, col + cell["colspan"]):
                    if (y, x) in occupied:
                        raise ValueError()
                    occupied[y, x] = " ".join(cell["text"].split())
            col += cell["colspan"]
    width = max(c for _, c in occupied) + 1
    return [[occupied.get((r, c), "") for c in range(width)] for r in range(len(parser.rows))]


def simplify(text):
    return re.sub(r"[\W_]+", "", re.sub(r"\[\d+\]", "", text).casefold())


def compare_bleu(text, question):
    """Return None for ambiguous targets, headers, language direction or comparison scope."""
    if (
        not re.search("bleu", question, re.I)
        or not re.search(r"提升|提高|差值|差多少|improv|difference", question, re.I)
        or not re.search(r"最先进|最高|state.of.the.art|previous.best", question, re.I)
    ):
        return None
    language = (
        "EN-DE"
        if re.search(r"English.to.German|英语.{0,4}德|英德", question, re.I)
        else "EN-FR"
        if re.search(r"English.to.French|英语.{0,4}法|英法", question, re.I)
        else None
    )
    if not language:
        return None
    try:
        rows = grid(text)
        headers = [
            (r, c)
            for r, row in enumerate(rows[:4])
            for c, value in enumerate(row)
            if value.casefold() == language.casefold() and any("BLEU" == rows[prior][c].upper() for prior in range(r))
        ]
        if len(headers) != 1:
            return None
        hr, col = headers[0]
        numeric = []
        for i, row in enumerate(rows[hr + 1 :], hr + 1):
            if re.fullmatch(r"\d+(?:\.\d+)?", row[col]):
                numeric.append((i, row[0], Decimal(row[col])))
        targets = [row for row in numeric if simplify(row[1]) and simplify(row[1]) in simplify(question)]
        if len(targets) != 1:
            return None
        target = targets[0]
        family = re.match(r"[A-Za-z0-9]+", target[1])
        if not family:
            return None
        family = family[0].casefold()
        first = min(i for i, label, _ in numeric if label.casefold().startswith(family))
        baselines = [r for r in numeric if r[0] < first]
        if len(baselines) < 2:
            return None
        best = max(r[2] for r in baselines)
        return {
            "target": target[1],
            "target_score": str(target[2]),
            "baseline_labels": [r[1] for r in baselines if r[2] == best],
            "baseline_score": str(best),
            "difference": str(target[2] - best),
            "metric": "BLEU",
            "direction": language,
            "comparison_scope": "all prior rows before target model family, including ensembles",
            "algorithm": "html-spans-column-max-decimal-v1",
        }
    except (ValueError, TypeError, KeyError, IndexError, InvalidOperation):
        return None
