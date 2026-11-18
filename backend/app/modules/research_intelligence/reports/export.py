"""Export a report to Markdown, DOCX, PDF and PPTX.

All four renderers read the same structure and keep the same things: the notice, the section order and headings, quotations marked as quotations with their citation labels, an origin tag on every
block (source quote / tool analysis / author), tables, and the numbered reference list. Files are built in memory from stored text only; nothing is fetched. The PDF uses the Bitstream Vera font that
ships with reportlab; characters it lacks are replaced with '?' in the PDF only (the other formats keep full Unicode)."""
from __future__ import annotations

import io
import re
from typing import Any, Dict, List, Tuple

ORIGIN = {"source": "Source quote", "analysis": "Tool analysis", "author": "Author"}
FORMATS = {"md": ("text/markdown; charset=utf-8", "md"), "docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx"),
           "pdf": ("application/pdf", "pdf"), "pptx": ("application/vnd.openxmlformats-officedocument.presentationml.presentation", "pptx")}


def safe_filename(title: str, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._ -]+", "", title).strip(". -").replace(" ", "_")[:80] or "report"
    return f"{base}.{ext}"


def _cite(b: Dict[str, Any]) -> str:
    return " " + "".join(f"[{c}]" for c in b.get("cite", [])) if b.get("cite") else ""


def _quote_loc(b: Dict[str, Any]) -> str:
    ref = b.get("ref") or {}
    return f" ({ref['locator']})" if ref.get("locator") else ""


def _para_text(b: Dict[str, Any]) -> str:
    return (b.get("text") or "").strip()


# ---------------------------------------------------------------------------------------------- markdown
def to_markdown(r: Dict[str, Any]) -> bytes:
    out = [f"# {r['title']}", "", f"> {r['notice']}", ""]
    for s in r["sections"]:
        out += [f"## {s['heading']}", ""]
        for b in s["blocks"]:
            tag = f"*{ORIGIN.get(b.get('origin'), 'Tool analysis')}*"
            if b["type"] == "text":
                if _para_text(b):
                    out += [f"{_para_text(b)}{_cite(b)}  ", f"{tag}", ""]
            elif b["type"] == "quote":
                out += [f"> “{_para_text(b)}”{_quote_loc(b)}{_cite(b)}", f"> {tag}", ""]
            elif b["type"] == "bullets":
                out += [f"- {i}" for i in b["items"]] + ["", tag, ""]
            elif b["type"] == "table":
                out += ["| " + " | ".join(b["columns"]) + " |", "|" + "---|" * len(b["columns"])]
                out += ["| " + " | ".join(str(x).replace("|", "\\|") for x in row) + " |" for row in b["rows"]] + ["", tag, ""]
    return ("\n".join(out).rstrip() + "\n").encode("utf-8")


# ---------------------------------------------------------------------------------------------- docx
def to_docx(r: Dict[str, Any]) -> bytes:
    from docx import Document
    from docx.shared import Pt, RGBColor
    d = Document()
    d.core_properties.title = r["title"]
    d.add_heading(r["title"], 0)
    n = d.add_paragraph()
    run = n.add_run(r["notice"])
    run.italic = True
    run.font.size = Pt(9)
    for s in r["sections"]:
        d.add_heading(s["heading"], 1)
        for b in s["blocks"]:
            label = ORIGIN.get(b.get("origin"), "Tool analysis")
            if b["type"] == "text" and _para_text(b):
                p = d.add_paragraph(_para_text(b) + _cite(b))
                t = p.add_run(f"  [{label}]")
                t.font.size, t.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
            elif b["type"] == "quote":
                p = d.add_paragraph(style="Intense Quote" if "Intense Quote" in [st.name for st in d.styles] else None)
                p.add_run(f"“{_para_text(b)}”{_quote_loc(b)}{_cite(b)}").italic = True
                t = p.add_run(f"  [{label}]")
                t.font.size, t.font.color.rgb = Pt(8), RGBColor(0x66, 0x66, 0x66)
            elif b["type"] == "bullets":
                for i in b["items"]:
                    d.add_paragraph(i, style="List Bullet")
            elif b["type"] == "table":
                tb = d.add_table(rows=1, cols=len(b["columns"]))
                tb.style = "Light Grid Accent 1" if "Light Grid Accent 1" in [st.name for st in d.styles] else "Table Grid"
                for i, c in enumerate(b["columns"]):
                    tb.rows[0].cells[i].text = str(c)
                for row in b["rows"]:
                    cells = tb.add_row().cells
                    for i, v in enumerate(row):
                        cells[i].text = str(v)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- pdf
_FONT: Dict[str, Any] = {}


def _fonts() -> Tuple[str, str, str]:
    if "ok" not in _FONT:
        import os
        import reportlab
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.ttfonts import TTFont
        d = os.path.join(os.path.dirname(reportlab.__file__), "fonts")
        try:
            for name, f in (("Vera", "Vera.ttf"), ("Vera-B", "VeraBd.ttf"), ("Vera-I", "VeraIt.ttf")):
                pdfmetrics.registerFont(TTFont(name, os.path.join(d, f)))
            pdfmetrics.registerFontFamily("Vera", normal="Vera", bold="Vera-B", italic="Vera-I", boldItalic="Vera-B")
            _FONT["names"] = ("Vera", "Vera-B", "Vera-I")
            _FONT["face"] = pdfmetrics.getFont("Vera").face
        except Exception:                                                              # noqa: BLE001 - fall back to the built-in Latin fonts
            _FONT["names"] = ("Helvetica", "Helvetica-Bold", "Helvetica-Oblique")
            _FONT["face"] = None
        _FONT["ok"] = True
    return _FONT["names"]


def _pdf_text(s: str) -> str:
    face = _FONT.get("face")
    if face is not None:
        s = "".join(ch if ch in "\n\t" or ord(ch) in face.charToGlyph else "?" for ch in s)
    else:
        s = s.encode("latin-1", "replace").decode("latin-1")
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def to_pdf(r: Dict[str, Any]) -> bytes:
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.units import mm
    from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    reg, bold, ital = _fonts()
    base = ParagraphStyle("b", fontName=reg, fontSize=10, leading=14, spaceAfter=4)
    h0, h1 = ParagraphStyle("h0", parent=base, fontName=bold, fontSize=20, leading=24, spaceAfter=8), ParagraphStyle("h1", parent=base, fontName=bold, fontSize=14, leading=18, spaceBefore=10, spaceAfter=4)
    note = ParagraphStyle("n", parent=base, fontName=ital, fontSize=8.5, leading=11, textColor=colors.HexColor("#555555"))
    quote = ParagraphStyle("q", parent=base, fontName=ital, leftIndent=14, borderPadding=(2, 2, 2, 6))
    tag = ParagraphStyle("t", parent=base, fontSize=7.5, leading=9, textColor=colors.HexColor("#777777"), spaceAfter=6)
    cell = ParagraphStyle("c", parent=base, fontSize=8.5, leading=11, spaceAfter=0)
    buf = io.BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, leftMargin=20 * mm, rightMargin=20 * mm, topMargin=18 * mm, bottomMargin=18 * mm, title=r["title"], author="PredictAI")
    P = lambda t, st: Paragraph(_pdf_text(t), st)                                   # noqa: E731
    flow: List[Any] = [P(r["title"], h0), P(r["notice"], note), Spacer(1, 6)]
    for s in r["sections"]:
        flow.append(P(s["heading"], h1))
        for b in s["blocks"]:
            label = ORIGIN.get(b.get("origin"), "Tool analysis")
            if b["type"] == "text" and _para_text(b):
                flow += [P(_para_text(b) + _cite(b), base), P(label, tag)]
            elif b["type"] == "quote":
                flow += [P(f"“{_para_text(b)}”{_quote_loc(b)}{_cite(b)}", quote), P(label, tag)]
            elif b["type"] == "bullets":
                flow += [P("• " + i, base) for i in b["items"]] + [P(label, tag)]
            elif b["type"] == "table":
                data = [[P(str(c), ParagraphStyle("th", parent=cell, fontName=bold)) for c in b["columns"]]] + [[P(str(v), cell) for v in row] for row in b["rows"]]
                t = Table(data, repeatRows=1)
                t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#bbbbbb")), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
                flow += [t, P(label, tag)]
    doc.build(flow)
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- pptx
def to_pptx(r: Dict[str, Any]) -> bytes:
    from pptx import Presentation
    from pptx.util import Pt
    prs = Presentation()
    t = prs.slides.add_slide(prs.slide_layouts[0])
    t.shapes.title.text = r["title"]
    t.placeholders[1].text = "Automatically generated from selected sources. Not peer reviewed."
    n = prs.slides.add_slide(prs.slide_layouts[1])
    n.shapes.title.text = "About this report"
    n.placeholders[1].text = r["notice"]
    for p in n.placeholders[1].text_frame.paragraphs:
        for run in p.runs:
            run.font.size = Pt(16)

    def slide(title: str, lines: List[Tuple[str, int]]) -> None:
        s = prs.slides.add_slide(prs.slide_layouts[1])
        s.shapes.title.text = title
        tf = s.placeholders[1].text_frame
        tf.clear()
        for i, (text, size) in enumerate(lines):
            p = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            p.text = text
            for run in p.runs:
                run.font.size = Pt(size)

    for s in r["sections"]:
        lines: List[Tuple[str, int]] = []
        part = 1

        def flush() -> None:
            nonlocal lines, part
            if lines:
                slide(s["heading"] + (f" ({part})" if part > 1 or len(s["blocks"]) > 8 else ""), lines)
                part += 1
                lines = []
        for b in s["blocks"]:
            label = ORIGIN.get(b.get("origin"), "Tool analysis")
            if b["type"] == "text" and _para_text(b):
                lines.append((f"{_para_text(b)[:420]}{_cite(b)} · {label}", 16))
            elif b["type"] == "quote":
                lines.append((f"“{_para_text(b)[:420]}”{_cite(b)} · {label}", 15))
            elif b["type"] == "bullets":
                lines += [(i[:300], 15) for i in b["items"]]
            elif b["type"] == "table":
                lines += [(" | ".join(str(x) for x in row)[:300], 14) for row in b["rows"]]
            if sum(len(x[0]) for x in lines) > 900 or len(lines) >= 6:
                flush()
        flush()
    prs.core_properties.title = r["title"]
    buf = io.BytesIO()
    prs.save(buf)
    return buf.getvalue()


def export(report: Dict[str, Any], fmt: str) -> Tuple[bytes, str, str]:
    if fmt not in FORMATS:
        raise ValueError("unsupported format")
    fn = {"md": to_markdown, "docx": to_docx, "pdf": to_pdf, "pptx": to_pptx}[fmt]
    ctype, ext = FORMATS[fmt]
    return fn(report), ctype, safe_filename(report["title"], ext)
