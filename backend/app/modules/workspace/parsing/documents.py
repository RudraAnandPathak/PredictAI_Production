"""Document readers. Each returns a dict:
  pages: [{number, text, method('text'|'ocr'|'none'), ocr_confidence, headings, tables, ref}],
  headings: [{level, text, page, heuristic}], tables: [{page, rows}], page_count, page_count_estimated, meta, warnings.
Page numbers are real for PDF; formats without pages (DOCX, RTF, TXT, MD) are split into virtual pages of ~500 words and flagged."""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from ....core.errors import AppError
from ..errors import corrupted
from . import ocr as ocr_mod
from . import pdfclean
from . import xmlsafe
from .datasets import _decode_choice, unavailable

WORDS_PER_PAGE = 500
MAX_TEXT_CHARS = 20_000_000
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _warn(out: List[Dict[str, str]], code: str, message: str) -> None:
    if len(out) < 50:
        out.append({"code": code, "message": message})


# ------------------------------------------------------------------ paging of un-paginated text
def _virtual_pages(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """blocks: [{text, heading_level?, table?}] -> pages of ~500 words, keeping blocks whole."""
    pages: List[Dict[str, Any]] = []
    current: Dict[str, Any] = {"text": [], "words": 0, "headings": [], "tables": [], "first": None, "last": None}

    def flush() -> None:
        if current["words"] or current["tables"] or current["headings"]:
            pages.append({"number": len(pages) + 1, "text": "\n\n".join(current["text"]), "method": "text", "ocr_confidence": None,
                          "headings": current["headings"], "tables": current["tables"],
                          "ref": {"type": "blocks", "from": current["first"], "to": current["last"]}, "virtual": True})
        current.update(text=[], words=0, headings=[], tables=[], first=None, last=None)

    for index, block in enumerate(blocks, start=1):
        words = len(block["text"].split())
        if current["words"] and current["words"] + words > WORDS_PER_PAGE:
            flush()
        if current["first"] is None:
            current["first"] = index
        current["last"] = index
        if block["text"]:
            current["text"].append(block["text"])
        current["words"] += words
        if block.get("heading_level"):
            current["headings"].append({"level": block["heading_level"], "text": block["text"][:300], "block": index})
        if block.get("table"):
            current["tables"].append({"rows": block["table"], "block": index})
    flush()
    return pages


def _split_big(blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """A single block longer than a page (e.g. a text file with no blank lines) is cut at line, then word, boundaries."""
    out: List[Dict[str, Any]] = []
    for block in blocks:
        if len(block["text"].split()) <= WORDS_PER_PAGE or block.get("table") or block.get("heading_level"):
            out.append(block)
            continue
        current: List[str] = []
        count = 0
        for token in re.findall(r"\S+\s*", block["text"]):
            current.append(token)
            count += 1
            if count >= WORDS_PER_PAGE:
                out.append({"text": "".join(current).strip()})
                current, count = [], 0
        if current:
            out.append({"text": "".join(current).strip()})
    return out


def _finish(blocks: List[Dict[str, Any]], meta: Dict[str, Any], warnings: List[Dict[str, str]], fmt: str) -> Dict[str, Any]:
    pages = _virtual_pages(_split_big(blocks))
    if not pages:
        raise corrupted("No readable text was found in this document.")
    headings, tables = [], []
    for page in pages:
        for h in page["headings"]:
            headings.append({**h, "page": page["number"], "heuristic": h.get("heuristic", False)})
        for t in page["tables"]:
            tables.append({"page": page["number"], "rows": t["rows"], "block": t["block"]})
    return {"format": fmt, "pages": pages, "headings": headings, "tables": tables, "page_count": len(pages), "page_count_estimated": True,
            "meta": meta, "warnings": warnings}


# ------------------------------------------------------------------ TXT / Markdown
_MD_HEAD = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_MD_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$")


def read_text(path: Path, fmt: str) -> Dict[str, Any]:
    raw = path.read_bytes()
    if b"\x00" in raw[:65536] and not raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise corrupted("This file contains binary data, so it cannot be read as text.")
    encoding, note = _decode_choice(raw[:262144])
    text = raw.decode(encoding, errors="replace").replace("\r\n", "\n").replace("\r", "\n")
    warnings: List[Dict[str, str]] = []
    if note:
        _warn(warnings, "ENCODING_GUESS", note)
    if not text.strip():
        raise corrupted("The file is empty.")
    blocks: List[Dict[str, Any]] = []
    lines = text.split("\n")
    i = 0
    paragraph: List[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            blocks.append({"text": "\n".join(paragraph).strip()})
            paragraph.clear()

    while i < len(lines):
        line = lines[i]
        if fmt == "md":
            heading = _MD_HEAD.match(line)
            if heading:
                flush_paragraph()
                blocks.append({"text": heading.group(2), "heading_level": len(heading.group(1))})
                i += 1
                continue
            if "|" in line and i + 1 < len(lines) and _MD_TABLE_SEP.match(lines[i + 1]):
                flush_paragraph()
                rows = [[c.strip() for c in line.strip().strip("|").split("|")]]
                i += 2
                while i < len(lines) and "|" in lines[i] and lines[i].strip():
                    rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                    i += 1
                blocks.append({"text": "\n".join(" | ".join(r) for r in rows), "table": rows})
                continue
        if not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line)
        i += 1
    flush_paragraph()
    return _finish(blocks, {"encoding": encoding, "characters": len(text)}, warnings, fmt)


# ------------------------------------------------------------------ DOCX
def read_docx(path: Path) -> Dict[str, Any]:
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                raise corrupted("This is not a valid Word document (word/document.xml is missing).")
            body = xmlsafe.parse_bytes(zf.read("word/document.xml"))
            styles: Dict[str, str] = {}
            if "word/styles.xml" in zf.namelist():
                for st in xmlsafe.parse_bytes(zf.read("word/styles.xml")).iter(W + "style"):
                    name = st.find(W + "name")
                    if name is not None:
                        styles[st.get(W + "styleId") or ""] = (name.get(W + "val") or "").lower()
    except zipfile.BadZipFile as error:
        raise corrupted("This file is damaged (it is not a readable Word document).") from error
    except KeyError as error:
        raise corrupted("This Word document is damaged.") from error
    warnings: List[Dict[str, str]] = []

    def run_text(par) -> str:
        parts = []
        for el in par.iter():
            if el.tag == W + "t":
                parts.append(el.text or "")
            elif el.tag == W + "tab":
                parts.append("\t")
            elif el.tag in (W + "br", W + "cr"):
                parts.append("\n")
        return "".join(parts).strip()

    def heading_level(par) -> int:
        ppr = par.find(W + "pPr")
        if ppr is None:
            return 0
        ps = ppr.find(W + "pStyle")
        name = styles.get(ps.get(W + "val") if ps is not None else "", "")
        if name == "title":
            return 1
        m = re.match(r"heading\s*(\d)", name)
        if m:
            return min(int(m.group(1)), 6)
        ol = ppr.find(W + "outlineLvl")
        if ol is not None and (ol.get(W + "val") or "").isdigit():
            return min(int(ol.get(W + "val")) + 1, 6)
        return 0

    blocks: List[Dict[str, Any]] = []
    container = body.find(W + "body")
    for child in list(container) if container is not None else []:
        if child.tag == W + "p":
            text = run_text(child)
            if text:
                level = heading_level(child)
                blocks.append({"text": text, **({"heading_level": level} if level else {})})
        elif child.tag == W + "tbl":
            rows = []
            for tr in child.iter(W + "tr"):
                rows.append([" ".join(run_text(p) for p in tc.iter(W + "p")).strip() for tc in tr.findall(W + "tc")])
            if rows:
                blocks.append({"text": "\n".join(" | ".join(r) for r in rows), "table": rows})
    if any(True for _ in body.iter(W + "ins")) or any(True for _ in body.iter(W + "del")):
        _warn(warnings, "TRACKED_CHANGES", "The document contains tracked changes; the text shown reflects the current content including insertions.")
    return _finish(blocks, {"headings_from": "document styles"}, warnings, "docx")


def docx_page_estimate(path: Path) -> Optional[int]:
    """Estimated pages (~500 words each) from the document body; None when it cannot be read cheaply."""
    try:
        with zipfile.ZipFile(path) as zf:
            info = zf.getinfo("word/document.xml")
            if info.file_size > 80 * 1024 * 1024:
                return None
            data = zf.read("word/document.xml")
        words = len(re.sub(rb"<[^>]+>", b" ", data).split())
        return max(1, -(-words // WORDS_PER_PAGE))
    except Exception:
        return None


# ------------------------------------------------------------------ RTF
_SKIP_DEST = {"fonttbl", "colortbl", "stylesheet", "info", "pict", "header", "footer", "footnote", "themedata", "colorschememapping", "datastore",
              "latentstyles", "listtable", "listoverridetable", "rsidtbl", "generator", "xmlnstbl", "mmathPr", "fldinst", "object"}
_RTF_TOKEN = re.compile(r"\\([a-zA-Z]+)(-?\d+)? ?|\\'([0-9a-fA-F]{2})|\\([^a-zA-Z])|([{}])|([^\\{}]+)", re.S)


def read_rtf(path: Path) -> Dict[str, Any]:
    raw = path.read_bytes()
    if not raw.lstrip().startswith(b"{\\rtf"):
        raise corrupted("This file does not look like a valid RTF document.")
    text = raw.decode("latin-1")
    out: List[str] = []
    stack: List[bool] = []
    skip = False
    uc_skip = 0
    depth = 0
    for m in _RTF_TOKEN.finditer(text):
        word, num, hexa, sym, brace, lit = m.groups()
        if brace == "{":
            stack.append(skip)
            depth += 1
            if depth > 500:
                raise corrupted("This RTF document is nested too deeply to read.")
        elif brace == "}":
            skip = stack.pop() if stack else False
            depth = max(0, depth - 1)
        elif sym is not None:
            if sym == "*":
                skip = True
            elif sym in "\\{}" and not skip:
                out.append(sym)
            elif sym == "~" and not skip:
                out.append(" ")
        elif word is not None:
            if word in _SKIP_DEST:
                skip = True
            elif skip:
                continue
            elif word in ("par", "line", "sect", "page"):
                out.append("\n")
            elif word == "tab":
                out.append("\t")
            elif word == "u" and num is not None:
                code = int(num)
                out.append(chr(code + 65536 if code < 0 else code))
                uc_skip = 1
            elif word == "bullet":
                out.append("•")
            elif word in ("emdash", "endash"):
                out.append("—" if word == "emdash" else "–")
        elif hexa is not None:
            if not skip:
                out.append(bytes([int(hexa, 16)]).decode("cp1252", errors="replace"))
        elif lit is not None and not skip:
            chunk = lit.replace("\r", "").replace("\n", "")
            if uc_skip and chunk:
                chunk, uc_skip = chunk[1:], 0
            out.append(chunk)
    plain = "".join(out)
    blocks = [{"text": p.strip()} for p in re.split(r"\n\s*\n|\n", plain) if p.strip()]
    if not blocks:
        raise corrupted("No readable text was found in this RTF document.")
    return _finish(blocks, {"formatting": "ignored"}, [], "rtf")


# ------------------------------------------------------------------ PDF
def _pypdf():
    try:
        import pypdf  # type: ignore
        return pypdf
    except ImportError:
        raise unavailable("PDF", "Server administrators can enable PDF reading by installing the optional 'pypdf' package (see requirements-data.txt).")


def open_pdf(path: Path):
    pypdf = _pypdf()
    try:
        reader = pypdf.PdfReader(io.BytesIO(path.read_bytes()), strict=False)
        if reader.is_encrypted:
            try:
                if not reader.decrypt(""):
                    raise corrupted("This PDF is password-protected. Remove the password and upload it again.")
            except AppError:
                raise
            except Exception as error:
                raise corrupted("This PDF is password-protected. Remove the password and upload it again.") from error
        count = len(reader.pages)
    except AppError:
        raise
    except Exception as error:
        raise corrupted("This PDF is damaged or not a valid PDF, so it could not be read.") from error
    if count == 0:
        raise corrupted("This PDF has no pages.")
    return reader, count


def pdf_page_count(path: Path) -> int:
    return open_pdf(path)[1]


_NUMBERED = re.compile(r"^(\d+(\.\d+)*[.)]?|[IVX]+\.|[A-Z]\.)\s+\S")


def _guess_headings(text: str) -> List[Dict[str, Any]]:
    """Heuristic only: short lines that are numbered, ALL CAPS, or title-like and followed by a blank line or body text."""
    found = []
    lines = text.split("\n")
    for i, line in enumerate(lines):
        s = line.strip()
        if not (3 <= len(s) <= 80) or s.endswith((".", ",", ";", ":")) or len(s.split()) > 10:
            continue
        nxt = lines[i + 1].strip() if i + 1 < len(lines) else ""
        letters = [c for c in s if c.isalpha()]
        if not letters:
            continue
        caps = sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(letters) >= 4
        numbered = bool(_NUMBERED.match(s)) and len(s.split()) <= 8
        alone = (i == 0 or not lines[i - 1].strip()) and (not nxt or bool(nxt))
        if (caps or numbered) and (i == 0 or not lines[i - 1].strip() or numbered):
            found.append({"level": 1 if caps else 2, "text": s, "heuristic": True})
        elif alone and pdfclean._is_heading(s, True, nxt):
            found.append({"level": 2, "text": s, "heuristic": True})                       # a short Title Case line standing alone between paragraphs
    return found[:20]


def _pdf_tables(path: Path, numbers: List[int], warnings: List[Dict[str, str]]) -> Dict[int, List[List[List[str]]]]:
    try:
        import pdfplumber  # type: ignore
    except ImportError:
        return {}
    result: Dict[int, List[List[List[str]]]] = {}
    try:
        with pdfplumber.open(str(path)) as pdf:
            for number in numbers:
                try:
                    found = pdf.pages[number - 1].extract_tables()
                except Exception:
                    continue
                kept = [[[(c or "").strip() for c in row] for row in table] for table in found if table and len(table) >= 2]
                if kept:
                    result[number] = kept[:10]
    except Exception:
        _warn(warnings, "TABLES_UNAVAILABLE", "Tables could not be detected in this PDF.")
    return result


def read_pdf(path: Path, *, ocr_enabled: bool, ocr_languages: str, ocr_max_pages: int, min_chars: int = 25, reflow_text: bool = True) -> Dict[str, Any]:
    reader, count = open_pdf(path)
    warnings: List[Dict[str, str]] = []
    pages: List[Dict[str, Any]] = []
    ocr_pages = ocr_done = ocr_failed = 0
    can_ocr = ocr_enabled and ocr_mod.available()
    bad_pages = 0
    for index in range(count):
        number = index + 1
        try:
            text = (reader.pages[index].extract_text() or "").replace("\x00", "").strip()
        except Exception:
            text, bad_pages = "", bad_pages + 1
        method, confidence, page_warn = "text", None, None
        if len(text) < min_chars:
            ocr_pages += 1
            if can_ocr and ocr_done < ocr_max_pages:
                try:
                    res = ocr_mod.ocr_pdf_page(path, number, ocr_languages)
                    recognised = str(res["text"]).strip()
                    if recognised:
                        text, method, confidence = recognised, "ocr", res["confidence"]
                        ocr_done += 1
                        if confidence is not None and confidence < 60:
                            page_warn = "Low OCR confidence: check this page against the original."
                    else:
                        method = "none"
                except Exception:
                    ocr_failed += 1
                    method = "none"
                    page_warn = "OCR failed on this page."
            else:
                method = "none" if not text else "text"
                if not text:
                    page_warn = ("OCR is switched off on this server." if not ocr_enabled else
                                 "OCR is not available on this server." if not ocr_mod.available() else "OCR page limit reached.")
        pages.append({"number": number, "text": text, "method": method, "ocr_confidence": confidence, "headings": [], "tables": [],
                      "ref": {"type": "page", "page": number}, **({"warning": page_warn} if page_warn else {})})
    cleaning = pdfclean.clean_pdf_pages(pages, reflow_text=reflow_text)                                              # running headers/footers, page numbers, wrapped lines, sentences cut by a page break
    for page in pages:
        page["headings"] = _guess_headings(page["text"]) if page["method"] == "text" else []
    # tables (only text-layer pages; pdfplumber is optional)
    tables_by_page = _pdf_tables(path, [p["number"] for p in pages if p["method"] == "text" and p["text"]][:200], warnings) if count <= 300 else {}
    all_tables = []
    for page in pages:
        for t in tables_by_page.get(page["number"], []):
            page["tables"].append({"rows": t})
            all_tables.append({"page": page["number"], "rows": t})
    headings = [{**h, "page": p["number"]} for p in pages for h in p["headings"]]
    empty = [p["number"] for p in pages if p["method"] == "none"]
    if ocr_pages and not can_ocr:
        _warn(warnings, "OCR_UNAVAILABLE", f"{ocr_pages} page(s) have no text layer (probably scanned). " +
              ("OCR is switched off on this server." if not ocr_enabled else "OCR is not installed on this server, so their text could not be read."))
    if ocr_done:
        _warn(warnings, "OCR_USED", f"{ocr_done} page(s) were read with OCR. Recognised text can contain mistakes; per-page confidence is shown.")
    if ocr_failed:
        _warn(warnings, "OCR_FAILED", f"OCR failed on {ocr_failed} page(s).")
    if ocr_done >= ocr_max_pages and ocr_pages > ocr_done:
        _warn(warnings, "OCR_LIMIT", f"OCR was limited to {ocr_max_pages} pages; the remaining scanned pages have no text.")
    if bad_pages:
        _warn(warnings, "PAGE_READ_ERRORS", f"{bad_pages} page(s) could not be read.")
    if empty and len(empty) == count:
        raise corrupted("No text could be read from this PDF" + ("" if can_ocr else " (it looks scanned and OCR is not available)") + ".")
    meta = {"ocr_engine": ocr_mod.engine_version() if ocr_done else None, "ocr_languages": ocr_languages if ocr_done else None,
            "pages_without_text": empty, "cleaning": cleaning, "tables_detected": len(all_tables), "headings_are": "heuristic (line shape), not document structure"}
    return {"format": "pdf", "pages": pages, "headings": headings, "tables": all_tables, "page_count": count, "page_count_estimated": False,
            "meta": meta, "warnings": warnings, "partial": bool(empty or ocr_failed or bad_pages)}


def estimate_pages(path: Path, fmt: str) -> Optional[int]:
    """Cheap page count used for the Standard-tier quota check before anything is stored."""
    if fmt == "pdf":
        return pdf_page_count(path)
    if fmt == "docx":
        return docx_page_estimate(path)
    if fmt in ("txt", "md", "rtf"):
        size = path.stat().st_size
        return max(1, -(-size // 3000)) if fmt != "rtf" else max(1, -(-size // 6000))
    return None


def read_document(path: Path, fmt: str, **ocr_options: Any) -> Dict[str, Any]:
    if fmt == "pdf":
        return read_pdf(path, **ocr_options)
    if fmt == "docx":
        return read_docx(path)
    if fmt == "rtf":
        return read_rtf(path)
    if fmt in ("txt", "md"):
        return read_text(path, fmt)
    raise corrupted("Unsupported document format.")
