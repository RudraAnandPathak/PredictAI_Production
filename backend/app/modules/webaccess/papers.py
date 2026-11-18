"""Reading a research paper: PDF bytes (or arXiv's HTML) to the same structure as any other article, with the parts a reader looks for.

  Abstract, Introduction, Related work, Methodology, Experiments, Results, Discussion, Limitations, Conclusion, References, Appendix

  * `paper_from_pdf` uses the PDF reader already built for uploaded documents (workspace.parsing.documents: pypdf text layer, optional pdfplumber tables, optional OCR). Running
    headers and page numbers are removed, hyphenated line breaks are joined, headings are recognised by numbering and by the usual section names, formulas are kept as written
    and listed separately. A PDF has no maths markup: equations are the text layer's approximation and are labelled as such. Multi-column layouts can interleave lines.
  * `paper_from_html` reads arXiv's own HTML version (LaTeXML) through `structure.parse_article`: equations come out as their TeX, tables as rows.
  * Nothing is OCR-ed beyond a small page budget, a PDF with no readable text yields a clear 'no text' result instead of empty or invented content, and a paper with no
    recognisable headings becomes one 'Full text' section rather than a failure.

The result is untrusted text: it is only ever quoted, never obeyed."""
from __future__ import annotations

import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from ..textkit import sentence_spans
from .fetcher import FetchError
from .structure import parse_article, render_section, table_sentences

MAX_PDF_PAGES = 120
OCR_PAGE_BUDGET = 30
CANONICAL = (
    ("abstract", r"abstract"),
    ("introduction", r"introduction"),
    ("related work", r"related work|prior work|literature review|background(?: and related work)?"),
    ("methodology", r"methods?|methodology|approach|proposed (?:method|approach|model)|model(?: architecture)?|materials and methods|problem (?:setup|formulation|statement)|framework"),
    ("experiments", r"experiments?|experimental (?:setup|setting|design|evaluation|results)|evaluation|implementation details|datasets?"),
    ("results", r"results?(?: and discussion| and analysis)?|findings"),
    ("discussion", r"discussion|analysis"),
    ("limitations", r"limitations?|threats to validity"),
    ("conclusion", r"conclusions?(?: and future work)?|concluding remarks|summary and conclusions?|future work"),
    ("references", r"references|bibliography"),
    ("appendix", r"appendix|appendices|supplementary material"),
)
_CANON = [(name, re.compile(rf"^(?:{pat})$", re.I)) for name, pat in CANONICAL]
_NUM = re.compile(r"^(?P<num>(?:\d{1,2}(?:\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-Z]\.))\s+(?P<title>\S.*)$")
_CAPTION = re.compile(r"^(?:table|figure|fig\.|algorithm|listing)\s*\d", re.I)
_STAMP = re.compile(r"^arxiv:\d{4}\.\d{4,5}(v\d+)?\s*\[[^\]]+\]", re.I)
_PAGENUM = re.compile(r"^\s*(?:page\s+)?\d{1,4}(?:\s*(?:/|of)\s*\d{1,4})?\s*$", re.I)
_MATH_CHARS = set("=+−∑∏∫√≤≥≈≠∈∉⊂⊆∂∇∞±×÷·^_αβγδεζηθικλμνξπρστυφχψωΓΔΘΛΞΠΣΦΨΩ→←↔⇒∀∃∧∨¬ℝℕℤℚ")
_REF_ENTRY = re.compile(r"^\s*(?:\[\d{1,3}\]|\d{1,3}\.)\s+\S")


def _norm_head(line: str) -> Tuple[Optional[str], str, Optional[str]]:
    """(canonical name or None, title, number) when `line` looks like a section heading, else (None, '', None)."""
    s = re.sub(r"\s+", " ", line).strip()
    if not 3 <= len(s) <= 90 or s.endswith((".", ",", ";", ":")) and not _NUM.match(s) or _CAPTION.match(s):
        return None, "", None
    if len(s.split()) > 12 or "  " in line.strip() and len(line.split()) > 8:
        return None, "", None
    number, title = None, s
    m = _NUM.match(s)
    if m:
        number, title = m.group("num").rstrip("."), m.group("title").strip()
        if not title or not title[0].isalpha():
            return None, "", None
    title_c = title.rstrip(" :.")
    for name, rx in _CANON:
        if rx.match(title_c):
            return name, title_c.title() if title_c.isupper() else title_c, number
    if number and len(title_c.split()) <= 8 and (title_c[0].isupper()) and not re.search(r"[=<>]", title_c) and not re.search(r"\b(is|are|was|were|we|the)\b.*\b(is|are|was|were)\b", title_c):
        return "", title_c, number                                                              # a numbered title that is not one of the usual names
    if title_c.isupper() and 4 <= len(title_c) <= 60 and re.fullmatch(r"[A-Z0-9 ,:&\-–()/]+", title_c) and not number:
        return "", title_c.title(), None                                                        # an ALL-CAPS line
    return None, "", None


def _is_formula(line: str) -> bool:
    s = line.strip()
    if len(s) < 3 or len(s) > 160:
        return False
    marks = sum(1 for ch in s if ch in _MATH_CHARS)
    words = len(re.findall(r"[A-Za-z]{4,}", s))
    return marks >= 3 and marks / max(1, len(s)) >= 0.08 and words <= 5 or bool(re.search(r"\(\d{1,3}\)\s*$", s) and marks >= 2 and words <= 4)


def clean_pages(pages: List[str]) -> List[List[str]]:
    """Per page, the lines worth keeping: no arXiv stamp, no page numbers, no header or footer line that repeats across pages; hyphenated words are rejoined."""
    split = [[ln.rstrip() for ln in (p or "").replace("\x00", "").split("\n")] for p in pages]
    counts: Counter = Counter()
    for lines in split:
        for ln in {re.sub(r"\d+", "#", x.strip()).lower() for x in lines[:3] + lines[-3:] if x.strip()}:
            counts[ln] += 1
    threshold = max(3, int(len(pages) * 0.4))
    repeated = {k for k, c in counts.items() if c >= threshold and len(k) < 110}
    out: List[List[str]] = []
    for lines in split:
        kept = []
        for x in lines:
            t = x.strip()
            if not t or _STAMP.match(t) or _PAGENUM.match(t) or re.sub(r"\d+", "#", t).lower() in repeated and (x in lines[:3] or x in lines[-3:]):
                if not t:
                    kept.append("")
                continue
            kept.append(t)
        joined: List[str] = []
        for t in kept:
            if joined and joined[-1].endswith("-") and t[:1].islower() and not joined[-1].endswith(" -") and len(joined[-1]) > 2 and joined[-1][-2].isalpha():
                joined[-1] = joined[-1][:-1] + t
            else:
                joined.append(t)
        out.append(joined)
    return out


def structure_from_pages(pages: List[str], *, tables_by_page: Optional[Dict[int, List[List[List[str]]]]] = None, abstract: str = "", title: str = "") -> Dict[str, Any]:
    """Page texts to the standard article structure. Pure and deterministic, so odd layouts can be tested without a PDF."""
    pages_lines = clean_pages(pages)
    warnings: List[str] = []
    cells = {n: {re.sub(r"\s+", " ", c).strip() for rows in tabs for r in rows for c in r if c and len(c.strip()) < 60} for n, tabs in (tables_by_page or {}).items()}
    sections: List[Dict[str, Any]] = []
    cur: Dict[str, Any] = {"heading": None, "canon": None, "number": None, "level": 0, "lines": [], "page_start": 1, "page_end": 1, "equations": []}
    in_refs = False
    references: List[Dict[str, Any]] = []
    ref_buf: List[str] = []

    def close() -> None:
        nonlocal cur
        if cur["lines"] or cur["heading"]:
            sections.append(cur)

    for pno, lines in enumerate(pages_lines, start=1):
        for ln in lines:
            if not ln:
                if not in_refs and cur["lines"] and cur["lines"][-1] != "":
                    cur["lines"].append("")
                continue
            if re.sub(r"\s+", " ", ln).strip() in cells.get(pno, ()):
                continue                                                                       # a table cell: its text is kept as table rows, and is never a heading
            canon, head, num = _norm_head(ln)
            if head and (canon or num or ln.strip().isupper()):
                close()
                level = 1 + (num.count(".") if num else 0)
                cur = {"heading": head, "canon": canon or None, "number": num, "level": min(level, 4), "lines": [], "page_start": pno, "page_end": pno, "equations": []}
                in_refs = canon == "references"
                continue
            if in_refs:
                if _REF_ENTRY.match(ln) and ref_buf:
                    references.append({"text": " ".join(ref_buf)[:400]})
                    ref_buf = []
                ref_buf.append(ln)
                continue
            cur["page_end"] = pno
            if _is_formula(ln):
                if len(cur["equations"]) < 40:
                    cur["equations"].append({"tex": None, "text": ln.strip()[:200], "page": pno, "approximate": True})
            cur["lines"].append(ln)
    if ref_buf:
        references.append({"text": " ".join(ref_buf)[:400]})
    close()
    out_sections: List[Dict[str, Any]] = []
    heading_path: List[Tuple[int, str]] = []
    front: List[str] = []
    for raw in sections:
        text = _paragraphs(raw["lines"])
        if not text and not raw["heading"]:
            continue
        if raw["heading"] is None and not out_sections and len(sections) > 1:
            front = text[:2]                                                                   # title and authors before the first heading: the lead, not a section
            continue
        if raw["heading"]:
            while heading_path and heading_path[-1][0] >= raw["level"]:
                heading_path.pop()
            heading_path.append((raw["level"], raw["heading"]))
        if raw["canon"] == "references":
            continue
        out_sections.append({"id": "", "heading": raw["heading"], "level": raw["level"], "path": [h for _, h in heading_path] if raw["heading"] else [], "anchor": None, "number": raw["number"],
                             "canonical": raw["canon"], "paragraphs": text, "lists": [], "tables": [], "equations": raw["equations"], "text": "", "page_start": raw["page_start"], "page_end": raw["page_end"]})
    if not out_sections or all(s["heading"] is None for s in out_sections) and len(out_sections) == 1:
        # no recognisable headings: keep the text, say so, and never pretend there was structure
        only = out_sections[0] if out_sections else {"id": "", "heading": None, "level": 0, "path": [], "anchor": None, "number": None, "paragraphs": [], "lists": [], "tables": [], "equations": [],
                                                      "text": "", "page_start": 1, "page_end": max(1, len(pages))}
        only["heading"], only["path"], only["level"], only["canonical"] = "Full text", ["Full text"], 1, None
        out_sections = [only]
        warnings.append("NO_SECTION_HEADINGS")
    if not any(s.get("canonical") == "abstract" for s in out_sections) and abstract:
        out_sections.insert(0, {"id": "", "heading": "Abstract", "level": 1, "path": ["Abstract"], "anchor": None, "number": None, "canonical": "abstract", "paragraphs": [abstract], "lists": [],
                                "tables": [], "equations": [], "text": "", "page_start": 1, "page_end": 1, "from_metadata": True})
    # tables found by the optional table reader, attached to the section that was open on their page
    for pno, tables in (tables_by_page or {}).items():
        home = next((s for s in out_sections if s.get("page_start", 1) <= pno <= s.get("page_end", 1) and s["heading"]), out_sections[-1] if out_sections else None)
        if home is None:
            continue
        for rows in tables[:4]:
            clean = [[c[:160] for c in r[:12]] for r in rows[:60]]
            if len(clean) >= 2:
                header_rows = 1
                home["tables"].append({"caption": f"Table on page {pno}", "kind": "table", "header_rows": header_rows, "rows": clean, "page": pno,
                                       "sentences": table_sentences(f"Table on page {pno}", "table", header_rows, clean)})
    lead = {"id": "", "heading": None, "level": 0, "path": [], "anchor": None, "number": None, "canonical": None, "paragraphs": [title] if title else front[:1], "lists": [], "tables": [], "equations": [],
            "text": "", "page_start": 1, "page_end": 1}
    final = [lead] + out_sections
    for i, s in enumerate(final):
        s["id"] = f"s{i}"
        s["text"] = render_section(s)
    chars = sum(len(s["text"]) for s in final)
    if chars < 400 * max(1, min(len(pages), 3)) and len(pages) >= 1 and chars < 1200:
        warnings.append("LITTLE_TEXT")
    if any(s["equations"] for s in final):
        warnings.append("EQUATIONS_APPROXIMATE")
    return {"title": title, "sections": [s for s in final if s["text"] or s["heading"] is None], "references": references[:80], "warnings": warnings, "dropped": 0}


def _paragraphs(lines: List[str]) -> List[str]:
    """Join wrapped lines into paragraphs (blank lines and sentence ends followed by a capital start a new one), each at most about 1500 characters."""
    paras: List[str] = []
    buf: List[str] = []
    for ln in lines:
        if not ln:
            if buf:
                paras.append(" ".join(buf))
                buf = []
            continue
        buf.append(ln)
    if buf:
        paras.append(" ".join(buf))
    out: List[str] = []
    for p in paras:
        p = re.sub(r"\s+", " ", p).strip()
        while len(p) > 1500:
            spans = sentence_spans(p, 700)
            cut = 0
            for a, b in spans:
                if b > 1200:
                    break
                cut = b
            cut = cut or 1500
            out.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            out.append(p)
    return out


def paper_from_pdf(data: bytes, *, ocr_enabled: bool = False, ocr_languages: str = "eng", abstract: str = "", title: str = "") -> Dict[str, Any]:
    """PDF bytes to the standard structure. Raises FetchError('PDF_UNREADABLE') for a damaged or password-protected file and FetchError('PDF_NO_TEXT') when no text could be read."""
    from ..workspace.parsing import documents          # imported here: the PDF reader is optional (pypdf), and its absence must read as 'unavailable', not break import
    from ...core.errors import AppError
    with tempfile.TemporaryDirectory(prefix="predictai-paper-") as tmp:
        path = Path(tmp) / "paper.pdf"
        path.write_bytes(data)
        try:
            read = documents.read_pdf(path, ocr_enabled=ocr_enabled, ocr_languages=ocr_languages, ocr_max_pages=OCR_PAGE_BUDGET, reflow_text=False)
        except AppError as e:
            msg = str(getattr(e, "message", "") or "")
            if "pypdf" in msg:
                raise FetchError("PDF_UNAVAILABLE", "PDF reading is not installed on this server (pypdf), so the paper's full text cannot be read.") from None
            if "No text could be read" in msg:
                raise FetchError("PDF_NO_TEXT", "The paper's PDF has no readable text (it looks scanned and OCR is not available here).") from None
            if "password" in msg:
                raise FetchError("PDF_PROTECTED", "The paper's PDF is password-protected.") from None
            raise FetchError("PDF_UNREADABLE", "The paper's PDF is damaged or not a valid PDF, so it could not be read.") from None
    pages = read.get("pages") or []
    if len(pages) > MAX_PDF_PAGES:
        pages = pages[:MAX_PDF_PAGES]
        truncated = True
    else:
        truncated = False
    texts = [p.get("text", "") for p in pages]
    if not any(t.strip() for t in texts):
        raise FetchError("PDF_NO_TEXT", "The paper's PDF has no readable text (it may be scanned images and OCR is not available).")
    tables = {p["number"]: [t["rows"] for t in p.get("tables", [])] for p in pages if p.get("tables")}
    paper = structure_from_pages(texts, tables_by_page=tables, abstract=abstract, title=title)
    ocr_pages = [p["number"] for p in pages if p.get("method") == "ocr"]
    bad_pages = [p["number"] for p in pages if p.get("method") == "none"]
    paper["pdf"] = {"pages": len(read.get("pages") or []), "pages_read": len(pages), "ocr_pages": ocr_pages[:50], "unreadable_pages": bad_pages[:50], "truncated": truncated}
    if ocr_pages:
        paper["warnings"].append("OCR_USED")
    if bad_pages:
        paper["warnings"].append("SOME_PAGES_UNREADABLE")
    if truncated:
        paper["warnings"].append("PAPER_TRUNCATED")
    paper["warnings"].append("MULTI_COLUMN_ORDER_NOT_GUARANTEED")
    return paper


def paper_from_html(markup: str, *, abstract: str = "") -> Dict[str, Any]:
    """arXiv's HTML version (LaTeXML). Equations come out as TeX; a page with no usable text is an error, never an empty paper."""
    art = parse_article(markup, "latexml")
    secs = [s for s in art["sections"] if s["text"]]
    if sum(len(s["text"]) for s in secs) < 800:
        raise FetchError("HTML_NO_TEXT", "The paper's HTML version has no readable text.")
    for s in art["sections"]:
        s["canonical"] = next((name for name, rx in _CANON if s["heading"] and rx.match(s["heading"].strip(" :."))), None)
    if abstract and not any(s.get("canonical") == "abstract" for s in art["sections"]):
        art["sections"].insert(1 if art["sections"] else 0, {"id": "", "heading": "Abstract", "level": 1, "path": ["Abstract"], "anchor": None, "number": None, "canonical": "abstract",
                                                          "paragraphs": [abstract], "lists": [], "tables": [], "equations": [], "text": abstract, "from_metadata": True})
        for i, s in enumerate(art["sections"]):
            s["id"] = f"s{i}"
    art["warnings"] = list(art.get("warnings") or [])
    return art
