"""Report exporters. Every format is built from the same flattened view, so headings, tables, charts, captions, citations, references, version, readiness and timestamp are carried the same way.
Each export library is optional: a missing one raises ExportUnavailable naming the package, and the other formats keep working."""
from __future__ import annotations

import csv
import io
import re
from typing import Any, Dict, Iterator, List, Tuple


class ExportUnavailable(Exception):
    pass


FORMATS = ("pdf", "docx", "pptx", "md", "csv", "xlsx", "png")
LABEL = {"fact": "Fact", "calculation": "Calculated", "prediction": "Prediction", "interpretation": "Interpretation", "suggestion": "Suggestion", "process": "Process", "opinion": "Opinion", "uncertain": "Uncertain", "note": "Note"}


def filename(title: str, version: int, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9]+", "_", title or "report").strip("_")[:60] or "report"
    return f"{base}_v{version}.{ext}"


def _need(module: str, package: str, fmt: str):
    try:
        return __import__(module, fromlist=["x"])
    except ImportError:
        raise ExportUnavailable(f"{fmt.upper()} export needs the '{package}' package, which is not installed. Install it with: pip install -r requirements-reports.txt") from None


def _meta(view: Dict[str, Any]) -> List[str]:
    r = view["readiness"]
    out = [f"Version {view['version']}", f"Readiness: {r['status'].replace('_', ' ')}", f"Version saved: {view['version_created_at']}", f"Citation style: {view['citation_style'].upper()}"]
    if view.get("author"):
        out.append(f"Author: {view['author']}")
    return out


def _flat(view: Dict[str, Any]) -> Iterator[Tuple[str, Any]]:
    """Yield ('h1'|'h2'|'p'|'table'|'figure'|'note'|'status', payload)."""
    def blocks(bs):
        for b in bs:
            if b["type"] == "paragraph":
                yield "p", [(c["text"], c.get("marker", ""), c.get("kind"), c.get("caveats", [])) for c in b["claims"]]
            elif b["type"] in ("table", "figure"):
                yield b["type"], b
            else:
                yield "note", b["text"]
    for s in view["sections"]:
        yield "h1", s["heading"]
        if s["status"] == "incomplete":
            yield "status", f"Incomplete: {s.get('why') or 'not enough supporting evidence'}"
        yield from blocks(s["blocks"])
        for x in s["subsections"]:
            yield "h2", x["heading"]
            if x["status"] == "incomplete":
                yield "status", f"Incomplete: {x.get('why') or 'not enough supporting evidence'}"
            yield from blocks(x["blocks"])


def _ptext(claims) -> str:
    claims = [(c["text"], c.get("marker", ""), c.get("kind"), c.get("caveats", [])) if isinstance(c, dict) else c for c in claims]
    return " ".join((t + (f" {m}" if m else "")) for t, m, _k, _c in claims)


def _caveats(claims) -> List[str]:
    return [c for _t, _m, _k, cav in claims for c in cav]


def _refs(view) -> List[str]:
    out = []
    for r in view["references"]:
        extra = []
        if r["missing"]:
            extra.append("missing: " + ", ".join(r["missing"]))
        if r["limitation"]:
            extra.append(r["limitation"])
        out.append(f"[{r['number']}] {r['text']}" + (f" ({'; '.join(extra)})" if extra else ""))
    return out


# --------------------------------------------------------------------------- markdown / csv
def to_md(view: Dict[str, Any]) -> bytes:
    L = [f"# {view['title']}", ""]
    if view.get("subtitle"):
        L += [f"_{view['subtitle']}_", ""]
    L += ["> " + " · ".join(_meta(view)), ""]
    if view["readiness"]["reasons"]:
        L += ["> **Review before relying on this report:** " + "; ".join(view["readiness"]["reasons"]), ""]
    for kind, p in _flat(view):
        if kind == "h1":
            L += [f"## {p}", ""]
        elif kind == "h2":
            L += [f"### {p}", ""]
        elif kind == "status":
            L += [f"_{p}_", ""]
        elif kind == "p":
            L += [_ptext(p), ""]
            L += [f"_Caveat: {c}_" for c in _caveats(p)] + ([""] if _caveats(p) else [])
        elif kind == "table":
            L += [f"**{p['caption']}**" if p.get("caption") else "", "| " + " | ".join(map(str, p["columns"])) + " |", "|" + "---|" * len(p["columns"])]
            L += ["| " + " | ".join(str(x).replace("|", "\\|") for x in r) + " |" for r in p["rows"]] + ([f"_{p['marker']}_"] if p.get("marker") else []) + [""]
        elif kind == "figure":
            c = p["chart"]
            L += [f"**{p.get('caption') or c.get('title') or 'Figure'}** ({c['kind']} chart)", "", "| Label | Value |", "|---|---|"] + [f"| {a} | {b} |" for a, b in zip(c["labels"], c["values"])] + [""]
        else:
            L += [f"> Note: {p}", ""]
    L += ["## References", ""] + (_refs(view) or ["_No sources are cited._"])
    return ("\n".join(L) + "\n").encode("utf-8")


def to_csv(view: Dict[str, Any]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["# " + view["title"], *_meta(view)])
    w.writerow([])
    for s in view["sections"]:
        for b in s["blocks"] + [y for x in s["subsections"] for y in x["blocks"]]:
            if b["type"] == "table":
                w.writerow([f"# {s['heading']} - {b.get('caption') or 'Table'}"])
                w.writerow(b["columns"])
                w.writerows([[("'" + x if isinstance(x, str) and x[:1] in "=+-@" else x) for x in r] for r in b["rows"]])
                w.writerow([])
    w.writerow(["# References"])
    for r in _refs(view):
        w.writerow([r])
    return buf.getvalue().encode("utf-8-sig")


# --------------------------------------------------------------------------- charts
def _chart_png(chart: Dict[str, Any], width=6.4, height=3.4) -> bytes:
    mpl = _need("matplotlib", "matplotlib", "chart")
    mpl.use("Agg")
    from matplotlib import pyplot as plt
    fig, ax = plt.subplots(figsize=(width, height), dpi=150)
    if chart["kind"] == "line":
        ax.plot(range(len(chart["values"])), chart["values"], marker="o", color="#2b5fab")
        ax.set_xticks(range(len(chart["labels"])))
    else:
        ax.bar(range(len(chart["values"])), chart["values"], color="#2b5fab")
        ax.set_xticks(range(len(chart["labels"])))
    ax.set_xticklabels([l[:18] for l in chart["labels"]], rotation=30 if len(chart["labels"]) > 4 else 0, ha="right" if len(chart["labels"]) > 4 else "center", fontsize=7)
    ax.set_title(chart.get("title") or "", fontsize=9)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()


def to_png(view: Dict[str, Any]) -> bytes:
    figs = [b for s in view["sections"] for b in s["blocks"] + [y for x in s["subsections"] for y in x["blocks"]] if b["type"] == "figure"]
    if not figs:
        raise ExportUnavailable("This report has no charts to export as PNG.")
    _need("matplotlib", "matplotlib", "png")
    from matplotlib import pyplot as plt
    mpl = __import__("matplotlib")
    mpl.use("Agg")
    fig, axes = plt.subplots(len(figs), 1, figsize=(6.4, 3.4 * len(figs)), dpi=150, squeeze=False)
    for ax, b in zip(axes[:, 0], figs):
        c = b["chart"]
        (ax.plot if c["kind"] == "line" else ax.bar)(range(len(c["values"])), c["values"], **({"marker": "o"} if c["kind"] == "line" else {}), color="#2b5fab")
        ax.set_xticks(range(len(c["labels"])))
        ax.set_xticklabels([l[:18] for l in c["labels"]], rotation=30 if len(c["labels"]) > 4 else 0, fontsize=7)
        ax.set_title((b.get("caption") or c.get("title") or ""), fontsize=9)
    fig.text(0.01, 0.005, f"{view['title']} - version {view['version']} - {view['readiness']['status']} - {view['version_created_at']}", fontsize=6, color="#555")
    fig.tight_layout(rect=(0, 0.02, 1, 1))
    out = io.BytesIO()
    fig.savefig(out, format="png")
    plt.close(fig)
    return out.getvalue()


# --------------------------------------------------------------------------- xlsx
def to_xlsx(view: Dict[str, Any]) -> bytes:
    ox = _need("openpyxl", "openpyxl", "xlsx")
    wb = ox.Workbook()
    ws = wb.active
    ws.title = "Summary"
    ws.append([view["title"]])
    for m in _meta(view):
        ws.append([m])
    for r in view["readiness"]["reasons"]:
        ws.append(["Review: " + r])
    ws.append([])
    ws.append(["Section", "Statement", "Citation", "Kind"])
    n = 0
    names = {"Summary"}
    for s in view["sections"]:
        for b in s["blocks"] + [y for x in s["subsections"] for y in x["blocks"]]:
            if b["type"] == "paragraph":
                for c in b["claims"]:
                    ws.append([s["heading"], c["text"], c["marker"], LABEL.get(c["kind"], c["kind"])])
            elif b["type"] == "table":
                n += 1
                nm = re.sub(r"[\[\]:*?/\\]", "", f"T{n} {b.get('caption') or s['heading']}")[:31]
                while nm in names:
                    nm = nm[:28] + f"_{n}"
                names.add(nm)
                t = wb.create_sheet(nm)
                t.append(list(b["columns"]))
                for r in b["rows"]:
                    t.append([("'" + x if isinstance(x, str) and x[:1] in "=+-@" else x) for x in r])
            elif b["type"] == "figure":
                n += 1
                nm = f"F{n} data"[:31]
                t = wb.create_sheet(nm)
                t.append(["Label", "Value"])
                for a, v in zip(b["chart"]["labels"], b["chart"]["values"]):
                    t.append([a, v])
    rs = wb.create_sheet("References")
    rs.append(["No.", "Citation", "Missing details", "Limitation"])
    for r in view["references"]:
        rs.append([r["number"], r["text"], ", ".join(r["missing"]), r["limitation"] or ""])
    for row in ws.iter_rows():
        for c in row:
            if isinstance(c.value, str) and c.value[:1] in "=+@" and c.row > 1:
                c.value = "'" + c.value
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------- docx
def to_docx(view: Dict[str, Any]) -> bytes:
    docx = _need("docx", "python-docx", "docx")
    from docx.shared import Inches
    d = docx.Document()
    d.add_heading(view["title"], 0)
    if view.get("subtitle"):
        d.add_paragraph(view["subtitle"])
    d.add_paragraph(" · ".join(_meta(view)))
    if view["readiness"]["reasons"]:
        p = d.add_paragraph()
        p.add_run("Review before relying on this report: ").bold = True
        p.add_run("; ".join(view["readiness"]["reasons"]))
    for kind, p in _flat(view):
        if kind == "h1":
            d.add_heading(p, 1)
        elif kind == "h2":
            d.add_heading(p, 2)
        elif kind == "status":
            d.add_paragraph().add_run(p).italic = True
        elif kind == "p":
            d.add_paragraph(_ptext(p))
            for c in _caveats(p):
                d.add_paragraph().add_run("Caveat: " + c).italic = True
        elif kind == "table":
            if p.get("caption"):
                d.add_paragraph().add_run(p["caption"]).bold = True
            t = d.add_table(rows=1, cols=len(p["columns"]))
            t.style = "Table Grid"
            for i, c in enumerate(p["columns"]):
                t.rows[0].cells[i].text = str(c)
            for r in p["rows"]:
                cells = t.add_row().cells
                for i, x in enumerate(r):
                    cells[i].text = str(x)
            if p.get("marker"):
                d.add_paragraph(p["marker"])
        elif kind == "figure":
            d.add_picture(io.BytesIO(_chart_png(p["chart"])), width=Inches(5.8))
            d.add_paragraph().add_run(p.get("caption") or p["chart"].get("title") or "Figure").italic = True
        else:
            d.add_paragraph("Note: " + p)
    d.add_heading("References", 1)
    for r in _refs(view) or ["No sources are cited."]:
        d.add_paragraph(r)
    out = io.BytesIO()
    d.save(out)
    return out.getvalue()


# --------------------------------------------------------------------------- pdf
def to_pdf(view: Dict[str, Any]) -> bytes:
    _need("reportlab", "reportlab", "pdf")
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    from xml.sax.saxutils import escape
    st = view["settings"]
    base = {"Helvetica": "Helvetica", "Times": "Times-Roman", "Courier": "Courier"}.get((st.get("font") or {}).get("family", "Helvetica"), "Helvetica")
    ss = getSampleStyleSheet()
    body = ParagraphStyle("b", parent=ss["BodyText"], fontName=base, fontSize=(st.get("font") or {}).get("size", 11) - 1, leading=(st.get("font") or {}).get("size", 11) + 3)
    h1 = ParagraphStyle("h1", parent=ss["Heading1"], fontName=base + ("-Bold" if base != "Times-Roman" else "-Bold"), fontSize=15)
    h2 = ParagraphStyle("h2", parent=ss["Heading2"], fontName=h1.fontName, fontSize=12)
    small = ParagraphStyle("s", parent=body, fontSize=8, textColor=colors.HexColor("#555555"))
    page = A4 if (st.get("page") or {}).get("size", "A4") == "A4" else letter
    out = io.BytesIO()
    foot = f"{view['title'][:60]} · version {view['version']} · {view['readiness']['status'].replace('_', ' ')} · {view['version_created_at']}"

    def deco(canvas, doc):
        canvas.saveState()
        canvas.setFont(base, 7)
        canvas.setFillColor(colors.HexColor("#555555"))
        canvas.drawString(0.7 * inch, 0.45 * inch, foot)
        canvas.drawRightString(page[0] - 0.7 * inch, 0.45 * inch, f"Page {doc.page}")
        canvas.restoreState()
    doc = SimpleDocTemplate(out, pagesize=page, leftMargin=0.8 * inch, rightMargin=0.8 * inch, topMargin=0.8 * inch, bottomMargin=0.8 * inch, title=view["title"], author=view.get("author") or "PredictAI")
    E = lambda s: escape(str(s))  # noqa: E731
    F = [Paragraph(E(view["title"]), ParagraphStyle("t", parent=ss["Title"], fontName=h1.fontName))]
    if view.get("subtitle"):
        F.append(Paragraph(E(view["subtitle"]), body))
    F.append(Paragraph(E(" · ".join(_meta(view))), small))
    if view["readiness"]["reasons"]:
        F.append(Spacer(1, 6))
        F.append(Paragraph("<b>Review before relying on this report:</b> " + E("; ".join(view["readiness"]["reasons"])), body))
    F.append(Spacer(1, 10))
    for kind, p in _flat(view):
        if kind == "h1":
            F.append(Paragraph(E(p), h1))
        elif kind == "h2":
            F.append(Paragraph(E(p), h2))
        elif kind == "status":
            F.append(Paragraph("<i>" + E(p) + "</i>", small))
        elif kind == "p":
            F.append(Paragraph(E(_ptext(p)), body))
            for c in _caveats(p):
                F.append(Paragraph("<i>Caveat: " + E(c) + "</i>", small))
            F.append(Spacer(1, 4))
        elif kind == "table":
            if p.get("caption"):
                F.append(Paragraph("<b>" + E(p["caption"]) + "</b>", body))
            cell = ParagraphStyle("c", parent=body, fontSize=8, leading=10)
            data = [[Paragraph(E(c), cell) for c in p["columns"]]] + [[Paragraph(E(x), cell) for x in r] for r in p["rows"]]
            t = Table(data, repeatRows=1)
            t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.4, colors.grey), ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef7")), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
            F += [t, Spacer(1, 8)]
        elif kind == "figure":
            F.append(Image(io.BytesIO(_chart_png(p["chart"])), width=5.6 * inch, height=3.0 * inch))
            F.append(Paragraph("<i>" + E(p.get("caption") or p["chart"].get("title") or "Figure") + "</i>", small))
            F.append(Spacer(1, 8))
        else:
            F.append(Paragraph("<i>Note: " + E(p) + "</i>", small))
    F.append(Paragraph("References", h1))
    for r in _refs(view) or ["No sources are cited."]:
        F.append(Paragraph(E(r), small))
    doc.build(F, onFirstPage=deco, onLaterPages=deco)
    return out.getvalue()


# --------------------------------------------------------------------------- pptx
def to_pptx(view: Dict[str, Any]) -> bytes:
    pp = _need("pptx", "python-pptx", "pptx")
    from pptx.util import Inches, Pt
    prs = pp.Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)

    def slide(title, lines=(), size=16):
        s = prs.slides.add_slide(prs.slide_layouts[5])
        s.shapes.title.text = title[:120]
        tb = s.shapes.add_textbox(Inches(0.6), Inches(1.5), Inches(12), Inches(5.2))
        tf = tb.text_frame
        tf.word_wrap = True
        for i, ln in enumerate(lines):
            para = tf.paragraphs[0] if i == 0 else tf.add_paragraph()
            para.text = ln[:600]
            para.font.size = Pt(size)
        fb = s.shapes.add_textbox(Inches(0.6), Inches(7.0), Inches(12), Inches(0.35))
        fb.text_frame.text = f"{view['title'][:70]} · version {view['version']} · {view['readiness']['status'].replace('_', ' ')} · {view['version_created_at']}"
        fb.text_frame.paragraphs[0].font.size = Pt(9)
        return s
    slide(view["title"], [view.get("subtitle") or ""] + _meta(view) + (["Review: " + "; ".join(view["readiness"]["reasons"])] if view["readiness"]["reasons"] else []), 16)
    for s in view["sections"]:
        items = [s["status"] == "incomplete" and f"Incomplete: {s.get('why') or 'not enough supporting evidence'}" or None]
        figs, tabs = [], []
        for b in s["blocks"] + [y for x in s["subsections"] for y in x["blocks"]]:
            if b["type"] == "paragraph":
                items += [_ptext(b["claims"])]
            elif b["type"] == "figure":
                figs.append(b)
            elif b["type"] == "table":
                tabs.append(b)
            else:
                items.append("Note: " + b["text"])
        items = [i for i in items if i]
        chunk, size = [], 0
        for it in items or ["(no content)"]:
            if size + len(it) > 1500 and chunk:
                slide(s["heading"], chunk, 14)
                chunk, size = [], 0
            chunk.append(it)
            size += len(it)
        slide(s["heading"], chunk, 14)
        for f in figs:
            sl = prs.slides.add_slide(prs.slide_layouts[5])
            sl.shapes.title.text = (f.get("caption") or f["chart"].get("title") or s["heading"])[:120]
            sl.shapes.add_picture(io.BytesIO(_chart_png(f["chart"], 8, 4.2)), Inches(2.2), Inches(1.5), width=Inches(9))
        for t in tabs:
            sl = prs.slides.add_slide(prs.slide_layouts[5])
            sl.shapes.title.text = (t.get("caption") or s["heading"])[:120]
            rows = t["rows"][:15]
            shape = sl.shapes.add_table(len(rows) + 1, len(t["columns"]), Inches(0.6), Inches(1.5), Inches(12), Inches(0.4) * (len(rows) + 1))
            for i, c in enumerate(t["columns"]):
                shape.table.cell(0, i).text = str(c)[:60]
            for ri, r in enumerate(rows, 1):
                for ci, x in enumerate(r):
                    shape.table.cell(ri, ci).text = str(x)[:80]
    slide("References", _refs(view)[:14] or ["No sources are cited."], 11)
    out = io.BytesIO()
    prs.save(out)
    return out.getvalue()


def build(fmt: str, view: Dict[str, Any], content: Dict[str, Any]) -> Tuple[bytes, str]:
    fn = {"md": to_md, "csv": to_csv, "xlsx": to_xlsx, "docx": to_docx, "pdf": to_pdf, "pptx": to_pptx, "png": to_png}[fmt]
    return fn(view), fmt
