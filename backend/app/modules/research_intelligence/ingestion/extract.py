"""Content extraction for research sources: PDF, DOCX, TXT, Markdown, CSV, XLSX and fetched HTML.

Import-light on purpose: this module runs inside a separate, resource-limited process (isolated.py) so a hostile file can crash or exhaust only that process. It touches no database and
no network. Output is plain data: Blocks (heading / paragraph / table / rows) with page numbers or locators, plus whatever metadata the file itself states. Nothing is invented: a field the
file does not state stays None/empty, and what cannot be extracted (tables inside PDFs, page numbers in DOCX) is reported in `warnings`."""
from __future__ import annotations

import csv
import io
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

MAX_TABLE_ROWS = 20000
ROWS_PER_BLOCK = 25
HEADING_WORDS = 14


class ExtractError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Block:
    kind: str                      # heading | paragraph | table | rows
    text: str
    page: Optional[int] = None
    level: int = 0                 # heading level (1 = top)
    locator: str = ""
    section: Optional[str] = None  # filled by the structure pass


@dataclass
class Extracted:
    format: str
    blocks: List[Block] = field(default_factory=list)
    title: Optional[str] = None
    authors: List[str] = field(default_factory=list)
    published: Optional[str] = None
    abstract: Optional[str] = None
    keywords: List[str] = field(default_factory=list)
    refs: List[str] = field(default_factory=list)
    page_count: Optional[int] = None
    table_count: int = 0
    warnings: List[str] = field(default_factory=list)

    def to_json(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------------------- shared text helpers
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")
_WS = re.compile(r"[ \t   ]+")


def clean_text(text: str) -> str:
    text = _CTRL.sub(" ", text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)                       # undo hyphenation at line ends
    return _WS.sub(" ", text).strip()


_NUMBERED = re.compile(r"^(\d{1,2}(\.\d{1,2}){0,3}\.?|[IVX]{1,5}\.|[A-Z]\.)\s+\S")
_SECTION_NAMES = {"abstract", "introduction", "background", "related work", "literature review", "methods", "method", "methodology", "materials and methods", "data", "results", "findings",
                  "discussion", "conclusion", "conclusions", "limitations", "references", "bibliography", "acknowledgements", "acknowledgments", "appendix", "keywords", "summary", "future work"}


def _plain(text: str) -> str:
    return re.sub(r"^[\dIVXA-Z.]+\s+", "", text).strip(" :").lower()


def looks_like_heading(line: str) -> Optional[int]:
    """Return a heading level (1..3) or None. Conservative: a wrong heading only affects section labels, never content."""
    s = line.strip()
    if not s or len(s) > 120 or s.endswith((".", ",", ";", ":")) and not _NUMBERED.match(s):
        return None
    words = s.split()
    if len(words) > HEADING_WORDS:
        return None
    low = re.sub(r"^[\dIVXA-Z.]+\s+", "", s).strip(" :").lower()
    if low in _SECTION_NAMES:
        return 1
    m = _NUMBERED.match(s)
    if m and len(words) <= 10 and not re.search(r"[.!?]$", s) and s[m.end() - 1:m.end()].isalpha():
        return min(3, m.group(1).count(".") + (0 if m.group(1).endswith(".") else 1))
    if len(words) <= 8 and s.isupper() and sum(c.isalpha() for c in s) >= 4:
        return 1
    return None


def paragraphs_from_lines(text: str) -> List[str]:
    """Join wrapped lines into paragraphs: a blank line, or a line that ends a sentence before a capitalised line, starts a new one."""
    out: List[str] = []
    buf: List[str] = []
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            if buf:
                out.append(" ".join(buf)); buf = []
            continue
        if buf and re.search(r"[.!?:]$", buf[-1]) and len(buf[-1]) < 60 and line[:1].isupper():
            out.append(" ".join(buf)); buf = []
        buf.append(line)
    if buf:
        out.append(" ".join(buf))
    return [clean_text(p) for p in out if clean_text(p)]


def _split_blocks_from_text(text: str, page: Optional[int], locator_prefix: str, start_index: int = 0) -> List[Block]:
    blocks: List[Block] = []
    idx = start_index
    for line_group in re.split(r"\n\s*\n", text):
        lines = [l for l in line_group.split("\n") if l.strip()]
        if not lines:
            continue
        # a heading is a single short line standing alone (or leading its group)
        first_level = looks_like_heading(lines[0])
        if first_level and (len(lines) == 1 or len(lines[0].split()) <= HEADING_WORDS):
            idx += 1
            blocks.append(Block("heading", clean_text(lines[0]), page, first_level, f"{locator_prefix}"))
            rest = "\n".join(lines[1:])
        else:
            rest = "\n".join(lines)
        for para in paragraphs_from_lines(rest):
            idx += 1
            blocks.append(Block("paragraph", para, page, 0, f"{locator_prefix}"))
    return blocks


# --------------------------------------------------------------------------------------- PDF
def extract_pdf(data: bytes, max_pages: int) -> Extracted:
    try:
        from pypdf import PdfReader
        from pypdf.errors import PyPdfError
    except ImportError:
        raise ExtractError("PARSER_UNAVAILABLE", "PDF support needs the pypdf package (pip install -r backend/requirements-research.txt).") from None
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise ExtractError("PDF_ENCRYPTED", "This PDF is password-protected. Remove the protection and upload it again.")
        n = len(reader.pages)
    except ExtractError:
        raise
    except (PyPdfError, ValueError, OSError, KeyError, TypeError, RecursionError):
        raise ExtractError("FILE_CORRUPTED", "This PDF is damaged and cannot be read.") from None
    out = Extracted("pdf", page_count=n)
    if n == 0:
        raise ExtractError("EMPTY_DOCUMENT", "This PDF has no pages.")
    if n > max_pages:
        out.warnings.append(f"Only the first {max_pages} of {n} pages were read.")
    meta = {}
    try:
        meta = reader.metadata or {}
        t = clean_text(str(meta.get("/Title") or "")) if meta else ""
        out.title = t if 3 <= len(t) <= 300 and not re.fullmatch(r"[\w .-]*\.(pdf|docx?|tex)", t, re.I) and not t.lower().startswith("microsoft word") else None
        a = clean_text(str(meta.get("/Author") or "")) if meta else ""
        out.authors = [x.strip() for x in re.split(r";|,| and ", a) if x.strip()][:40] if a else []
        d = str(meta.get("/CreationDate") or "") if meta else ""
        m = re.match(r"D:(\d{4})(\d{2})?(\d{2})?", d)
        out.published = "-".join(x for x in m.groups() if x) if m else None
    except Exception:                                                     # noqa: BLE001 - damaged metadata is not a reason to fail the import
        out.warnings.append("The PDF's own metadata could not be read.")
    chars = 0
    for i in range(min(n, max_pages)):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:                                                 # noqa: BLE001
            out.warnings.append(f"Page {i + 1} could not be read.")
            continue
        text = _CTRL.sub(" ", text).replace("\r", "\n")
        chars += len(text.strip())
        out.blocks.extend(_split_blocks_from_text(text, i + 1, f"p.{i + 1}"))
    if chars < 40:
        raise ExtractError("NO_TEXT_LAYER", "No readable text was found. This looks like a scanned PDF; run it through OCR first (OCR is not part of this release).")
    out.warnings.append("Tables inside PDFs are read as ordinary text; structured table extraction from PDF is not supported.")
    _mine_front_matter(out)
    return out


# --------------------------------------------------------------------------------------- DOCX
def extract_docx(data: bytes) -> Extracted:
    try:
        import docx
        from docx.table import Table
        from docx.text.paragraph import Paragraph
    except ImportError:
        raise ExtractError("PARSER_UNAVAILABLE", "DOCX support needs the python-docx package (pip install -r backend/requirements-research.txt).") from None
    try:
        document = docx.Document(io.BytesIO(data))
    except Exception:                                                     # noqa: BLE001 - python-docx raises many types for bad input
        raise ExtractError("FILE_CORRUPTED", "This Word document is damaged and cannot be read.") from None
    out = Extracted("docx")
    body = document.element.body
    n = 0
    for child in body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, document)
            text = clean_text(para.text)
            if not text:
                continue
            n += 1
            style = (para.style.name if para.style is not None and para.style.name else "") or ""
            m = re.match(r"Heading (\d)", style)
            if m or style == "Title":
                out.blocks.append(Block("heading", text, None, int(m.group(1)) if m else 1, f"¶{n}"))
            else:
                out.blocks.append(Block("paragraph", text, None, 0, f"¶{n}"))
        elif tag == "tbl":
            table = Table(child, document)
            rows = []
            for row in table.rows[:MAX_TABLE_ROWS]:
                seen, cells = set(), []
                for cell in row.cells:
                    if id(cell._tc) in seen:
                        continue
                    seen.add(id(cell._tc))
                    cells.append(clean_text(cell.text))
                rows.append(" | ".join(cells))
            if rows:
                n += 1
                out.table_count += 1
                out.blocks.append(Block("table", "\n".join(rows), None, 0, f"table {out.table_count}"))
    if not out.blocks:
        raise ExtractError("EMPTY_DOCUMENT", "This document contains no text.")
    try:
        cp = document.core_properties
        t = clean_text(cp.title or "")
        out.title = t or None
        out.authors = [a.strip() for a in re.split(r";|,", cp.author or "") if a.strip()][:40]
        out.published = cp.created.date().isoformat() if cp.created else None
        out.keywords = [k.strip() for k in re.split(r"[;,]", cp.keywords or "") if k.strip()][:30]
    except Exception:                                                     # noqa: BLE001
        pass
    out.warnings.append("Word documents have no fixed pages; passages are located by paragraph number.")
    if not out.title:
        first = next((b for b in out.blocks if b.kind == "heading" and _plain(b.text) not in _SECTION_NAMES), None)
        out.title = first.text if first else None
    _mine_front_matter(out)
    return out


# --------------------------------------------------------------------------------------- TXT / Markdown
def extract_text(text: str, fmt: str) -> Extracted:
    out = Extracted(fmt)
    text = _CTRL.sub(" ", text).replace("\r\n", "\n").replace("\r", "\n")
    if fmt == "markdown" and text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end > 0:
            for line in text[4:end].split("\n"):
                k, _, v = line.partition(":")
                k, v = k.strip().lower(), v.strip().strip("\"'")
                if k == "title" and v:
                    out.title = v[:300]
                elif k in ("author", "authors") and v:
                    out.authors = [a.strip() for a in re.split(r";|,", v.strip("[]")) if a.strip()][:40]
                elif k == "date" and v:
                    out.published = v[:20]
                elif k in ("keywords", "tags") and v:
                    out.keywords = [a.strip() for a in re.split(r",", v.strip("[]")) if a.strip()][:30]
            text = text[end + 4:]
    n = 0
    para_lines: List[str] = []

    def flush() -> None:
        nonlocal n
        if para_lines:
            for para in paragraphs_from_lines("\n".join(para_lines)) if fmt == "txt" else [clean_text(" ".join(para_lines))]:
                if para:
                    n += 1
                    out.blocks.append(Block("paragraph", para, None, 0, f"¶{n}"))
            para_lines.clear()

    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*\S)\s*#*$", line) if fmt == "markdown" else None
        if m:
            flush()
            n += 1
            out.blocks.append(Block("heading", clean_text(m.group(2)), None, len(m.group(1)), f"¶{n}"))
        elif not line.strip():
            flush()
        else:
            if fmt == "txt" and not para_lines and (lv := looks_like_heading(line)) and len(line.split()) <= HEADING_WORDS:
                n += 1
                out.blocks.append(Block("heading", clean_text(line), None, lv, f"¶{n}"))
            else:
                para_lines.append(line)
    flush()
    if not out.blocks:
        raise ExtractError("EMPTY_DOCUMENT", "This file contains no text.")
    if not out.title:
        h = next((b for b in out.blocks if b.kind == "heading" and b.level == 1 and _plain(b.text) not in _SECTION_NAMES), None)
        out.title = h.text if h else None
    out.warnings.append("Plain text and Markdown have no pages; passages are located by paragraph number.")
    _mine_front_matter(out)
    return out


# --------------------------------------------------------------------------------------- CSV / XLSX
def _profile(headers: List[str], rows: List[List[str]]) -> str:
    lines = [f"Columns ({len(headers)}): " + ", ".join(headers[:60]) + f". Data rows read: {len(rows)}."]
    for ci, name in enumerate(headers[:40]):
        vals = [r[ci].strip() for r in rows if ci < len(r) and r[ci].strip() != ""]
        nums = []
        for v in vals:
            try:
                nums.append(float(v.replace(",", "")))
            except ValueError:
                break
        if vals and len(nums) == len(vals):
            lines.append(f"{name}: numeric, {len(vals)} values, min {min(nums):g}, max {max(nums):g}, mean {sum(nums) / len(nums):.4g}, {len(rows) - len(vals)} blank.")
        elif vals:
            distinct = len(set(vals))
            lines.append(f"{name}: text, {len(vals)} values, {distinct} distinct, {len(rows) - len(vals)} blank.")
    return "\n".join(lines)


def _table_blocks(out: Extracted, sheet: Optional[str], headers: List[str], rows: List[List[str]], truncated: bool) -> None:
    label = f"sheet {sheet}" if sheet else "table"
    out.table_count += 1
    out.blocks.append(Block("heading", sheet or "Data", None, 1, label))
    out.blocks.append(Block("table", _profile(headers, rows), None, 0, f"{label} profile"))
    head = " | ".join(headers)
    for start in range(0, len(rows), ROWS_PER_BLOCK):
        group = rows[start:start + ROWS_PER_BLOCK]
        text = head + "\n" + "\n".join(" | ".join(c.strip() for c in r) for r in group)
        out.blocks.append(Block("rows", text, None, 0, f"{label} rows {start + 1}-{start + len(group)}"))
    if truncated:
        out.warnings.append(f"{label}: only the first {MAX_TABLE_ROWS} rows were read.")


def extract_csv(text: str) -> Extracted:
    out = Extracted("csv")
    text = _CTRL.sub(" ", text)
    try:
        dialect = csv.Sniffer().sniff(text[:8192], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)
    try:
        headers = [h.strip() or f"column_{i + 1}" for i, h in enumerate(next(reader))]
    except (StopIteration, csv.Error):
        raise ExtractError("EMPTY_DOCUMENT", "This CSV file has no rows.") from None
    rows: List[List[str]] = []
    truncated = False
    try:
        for r in reader:
            if len(rows) >= MAX_TABLE_ROWS:
                truncated = True
                break
            if any(c.strip() for c in r):
                rows.append(r)
    except csv.Error:
        raise ExtractError("FILE_CORRUPTED", "This CSV file could not be read.") from None
    if not rows:
        raise ExtractError("EMPTY_DOCUMENT", "This CSV file has a header but no data rows.")
    _table_blocks(out, None, headers, rows, truncated)
    out.title = None
    out.warnings.append("A data table has no pages; passages are located by row range. Cell values are quoted as written; formulas and charts do not exist in CSV.")
    return out


def extract_xlsx(data: bytes) -> Extracted:
    try:
        import openpyxl
    except ImportError:
        raise ExtractError("PARSER_UNAVAILABLE", "XLSX support needs the openpyxl package (pip install -r backend/requirements-research.txt).") from None
    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception:                                                     # noqa: BLE001
        raise ExtractError("FILE_CORRUPTED", "This Excel workbook is damaged and cannot be read.") from None
    out = Extracted("xlsx")
    for ws in wb.worksheets[:30]:
        rows: List[List[str]] = []
        headers: List[str] = []
        truncated = False
        for i, row in enumerate(ws.iter_rows(values_only=True)):
            cells = ["" if v is None else str(v) for v in row]
            if i == 0:
                headers = [c.strip() or f"column_{j + 1}" for j, c in enumerate(cells)]
                continue
            if len(rows) >= MAX_TABLE_ROWS:
                truncated = True
                break
            if any(c.strip() for c in cells):
                rows.append(cells)
        if headers and rows:
            _table_blocks(out, ws.title, headers, rows, truncated)
    wb.close()
    if not out.blocks:
        raise ExtractError("EMPTY_DOCUMENT", "This workbook has no data rows.")
    out.warnings.append("Formulas are read as the values last saved by Excel; charts, macros and images are ignored.")
    return out


# --------------------------------------------------------------------------------------- HTML (fetched pages)
def extract_html(html: str, base_url: str = "") -> Extracted:
    try:
        from bs4 import BeautifulSoup
    except ImportError:
        raise ExtractError("PARSER_UNAVAILABLE", "Web page import needs beautifulsoup4 (pip install -r backend/requirements-research.txt).") from None
    soup = BeautifulSoup(html[:5_000_000], "html.parser")
    out = Extracted("html")

    def meta(*names: str) -> List[str]:
        vals = []
        for n in names:
            for tag in soup.find_all("meta", attrs={"name": n}) + soup.find_all("meta", attrs={"property": n}):
                c = clean_text(tag.get("content") or "")
                if c:
                    vals.append(c)
        return vals

    title = (meta("citation_title", "og:title", "dc.title") or [clean_text(soup.title.get_text()) if soup.title else ""])[0]
    out.title = title[:300] or None
    out.authors = [a for a in (meta("citation_author", "dc.creator") or meta("author"))][:40]
    out.published = ((meta("citation_publication_date", "citation_date", "article:published_time", "dc.date", "date") or [None])[0] or "")[:25] or None
    ab = meta("citation_abstract", "dc.description", "og:description", "description")
    out.abstract = ab[0][:3000] if ab else None
    out.keywords = [k.strip() for k in (meta("keywords", "citation_keywords") or [""])[0].split(",") if k.strip()][:30]
    for tag in soup(["script", "style", "noscript", "nav", "footer", "aside", "form", "header", "iframe", "svg", "button"]):
        tag.decompose()
    root = soup.find("article") or soup.find("main") or soup.body or soup
    n = 0
    for el in root.find_all(["h1", "h2", "h3", "h4", "p", "li", "table", "blockquote", "pre"]):
        if el.name == "table":
            rows = [" | ".join(clean_text(c.get_text(" ")) for c in tr.find_all(["th", "td"])) for tr in el.find_all("tr")[:200]]
            rows = [r for r in rows if r.strip(" |")]
            if rows:
                n += 1
                out.table_count += 1
                out.blocks.append(Block("table", "\n".join(rows), None, 0, f"table {out.table_count}"))
            continue
        if el.find_parent("table") is not None:
            continue
        text = clean_text(el.get_text(" "))
        if len(text) < (2 if el.name.startswith("h") else 25):
            continue
        n += 1
        if el.name.startswith("h"):
            out.blocks.append(Block("heading", text[:200], None, int(el.name[1]), f"¶{n}"))
        else:
            out.blocks.append(Block("paragraph", text, None, 0, f"¶{n}"))
    if not out.blocks:
        raise ExtractError("EMPTY_DOCUMENT", "No readable article text was found on that page (it may need JavaScript or a login).")
    if not out.title:
        h = next((b for b in out.blocks if b.kind == "heading"), None)
        out.title = h.text if h else None
    out.warnings.append("Web pages have no pages; passages are located by paragraph number. Only the text of the page was read, not scripts or other pages.")
    _mine_front_matter(out)
    return out


# --------------------------------------------------------------------------------------- structure pass
def _mine_front_matter(out: Extracted) -> None:
    """Assign each block its section, and pick up abstract, keywords and references from the text when the file's own metadata lacks them."""
    section: Optional[str] = None
    in_refs = False
    for b in out.blocks:
        if b.kind == "heading":
            section = b.text
            in_refs = bool(re.sub(r"^[\dIVXA-Z.]+\s+", "", b.text).strip(" :").lower() in ("references", "bibliography", "works cited", "literature cited"))
        b.section = section
        if in_refs and b.kind == "paragraph":
            out.refs.append(b.text)
    if not out.abstract:
        for i, b in enumerate(out.blocks):
            if b.kind == "heading" and re.sub(r"^[\dIVXA-Z.]+\s+", "", b.text).strip(" :").lower() == "abstract":
                paras = []
                for nb in out.blocks[i + 1:i + 6]:
                    if nb.kind == "heading":
                        break
                    paras.append(nb.text)
                if paras:
                    out.abstract = " ".join(paras)[:3000]
                break
            if b.kind == "paragraph" and re.match(r"^abstract[\s:.—-]+", b.text, re.I) and len(b.text) > 80:
                out.abstract = re.sub(r"^abstract[\s:.—-]+", "", b.text, flags=re.I)[:3000]
                break
    if not out.keywords:
        for b in out.blocks[:60]:
            m = re.match(r"^(keywords?|key words|index terms)\s*[:—-]\s*(.{3,400})$", b.text, re.I)
            if m:
                out.keywords = [k.strip(" .") for k in re.split(r"[;,·•]", m.group(2)) if k.strip(" .")][:30]
                break
    if not out.title:
        first = next((b for b in out.blocks if b.kind in ("heading", "paragraph") and 8 <= len(b.text) <= 200 and _plain(b.text) not in _SECTION_NAMES), None)
        out.title = first.text if first and first.kind == "heading" else None
    # references: split a single long reference paragraph into entries when the parser merged them
    if len(out.refs) == 1 and len(out.refs[0]) > 400:
        parts = [p.strip() for p in re.split(r"\s(?=\[\d{1,3}\]\s|\d{1,3}\.\s(?=[A-Z]))", out.refs[0]) if len(p.strip()) > 20]
        if len(parts) > 1:
            out.refs = parts
    out.refs = out.refs[:500]


def extract(fmt: str, data: bytes, *, max_pages: int = 400, base_url: str = "") -> Extracted:
    from_text = None
    if fmt in ("txt", "markdown", "csv", "html"):
        from_text = _decode(data)
    if fmt == "pdf":
        return extract_pdf(data, max_pages)
    if fmt == "docx":
        return extract_docx(data)
    if fmt == "xlsx":
        return extract_xlsx(data)
    if fmt == "csv":
        return extract_csv(from_text or "")
    if fmt == "html":
        return extract_html(from_text or "", base_url)
    if fmt in ("txt", "markdown"):
        return extract_text(from_text or "", fmt)
    raise ExtractError("UNSUPPORTED_FILE", "That kind of file is not supported.")


def _decode(data: bytes) -> str:
    if data[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")
