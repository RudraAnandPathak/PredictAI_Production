"""Clean the text layer of a PDF so that what is stored, searched and quoted is the document's own prose.

What a raw PDF text layer gives you is one string per page: lines broken wherever the printed line ended, the running header and the page number on every page, headings glued to
the paragraph below them, and sentences cut in two by a page break. Quoting that produces fragments ("the executed Louis XVI, ruled from 1814 ..."). This module

  1. removes running headers, footers and bare page numbers (lines that repeat at the top or bottom of most pages, or look like a page number),
  2. joins wrapped lines into paragraphs (a blank line, or a short line that ends a sentence, ends a paragraph; a hyphen at a line end is joined back),
  3. puts each heading on its own paragraph so it is never quoted as part of a sentence,
  4. completes a sentence that a page break cut: the rest of the sentence is moved to the end of the page it started on, and the page records the spill, so a citation can say
     "pages 3-4". The text of each page remains the exact string that is cited, and citations still verify against the stored extraction.

Everything here is deterministic and conservative: when a line could be either a heading or a wrapped line, it is treated as a wrapped line."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, List, Tuple

from ...textkit import sentence_spans

PAGE_NUMBER = re.compile(r"^(?:page\s+)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$|^[-–—]\s*\d{1,4}\s*[-–—]$", re.I)
TERMINAL = re.compile(r"[.!?][\"'”’)\]]*$")
LINK_WORDS = frozenset("a an and as at by for from in into of on or the to with under over between during after before than that which who whom whose but nor yet so if its his her their".split())
NUMBERED = re.compile(r"^(\d+(\.\d+)*[.)]?|[IVX]+\.|[A-Z]\.)\s+\S")


def _norm(line: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", line.strip().lower()))


def _edge_lines(lines: List[str]) -> List[Tuple[int, str]]:
    idx = [i for i, ln in enumerate(lines) if ln.strip()]
    return [(i, lines[i]) for i in idx[:2] + idx[-2:]] if idx else []


def strip_running_lines(pages: List[Dict[str, Any]]) -> Dict[str, int]:
    """Remove repeated header/footer lines and bare page numbers from the top and bottom of every page. Returns counts for the extraction notes."""
    texts = [p for p in pages if p.get("method") == "text" and p.get("text")]
    counts: Counter = Counter()
    for p in texts:
        seen = {_norm(ln) for _, ln in _edge_lines(p["text"].split("\n")) if len(ln.strip()) >= 3}
        counts.update(seen)
    threshold = max(3, math.ceil(0.5 * len(texts)))
    repeated = {k for k, v in counts.items() if v >= threshold and not PAGE_NUMBER.match(k.replace("#", "1"))} if len(texts) >= 3 else set()
    removed_running = removed_numbers = 0
    for p in texts:
        lines = p["text"].split("\n")
        drop = set()
        for i, ln in _edge_lines(lines):
            s = ln.strip()
            if PAGE_NUMBER.match(s):
                drop.add(i)
                removed_numbers += 1
            elif _norm(s) in repeated:
                drop.add(i)
                removed_running += 1
        if drop:
            p["text"] = "\n".join(ln for i, ln in enumerate(lines) if i not in drop).strip("\n")
    return {"running_lines": removed_running, "page_numbers": removed_numbers}


def _is_heading(line: str, prev_ends: bool, next_line: str) -> bool:
    s = line.strip()
    words = s.split()
    if not (3 <= len(s) <= 90) or len(words) > 10 or TERMINAL.search(s) or s.endswith((",", ";", ":")) or "," in s:
        return False
    if not prev_ends or not (next_line.strip()[:1].isupper() or not next_line.strip()):
        return False
    letters = [c for c in s if c.isalpha()]
    if not letters:
        return False
    caps = sum(c.isupper() for c in letters) / len(letters) > 0.85 and len(letters) >= 4
    numbered = bool(NUMBERED.match(s)) and len(words) <= 8
    title_case = words[0][:1].isupper() and words[-1].lower() not in LINK_WORDS and sum(1 for w in words if w[:1].isupper() or w.lower() in LINK_WORDS or re.fullmatch(r"\d+[.)]?", w)) == len(words) and len(words) <= 8
    return caps or numbered or title_case


def reflow(text: str) -> str:
    """Join wrapped lines into paragraphs; headings stand alone. Paragraphs are separated by a blank line."""
    lines = [ln.rstrip() for ln in text.split("\n")]
    lengths = sorted(len(ln.strip()) for ln in lines if len(ln.strip()) > 20)
    typical = lengths[len(lengths) // 2] if lengths else 0
    paras: List[str] = []
    cur: List[str] = []

    def flush() -> None:
        if cur:
            paras.append(" ".join(cur))
            cur.clear()

    for i, raw in enumerate(lines):
        s = raw.strip()
        if not s:
            flush()
            continue
        nxt = next((x for x in lines[i + 1:] if x.strip()), "")
        prev_ends = not cur or bool(TERMINAL.search(cur[-1]))
        if _is_heading(s, prev_ends and (i == 0 or not lines[i - 1].strip() or bool(cur and TERMINAL.search(cur[-1]))), nxt):
            flush()
            paras.append(s)
            continue
        if cur and cur[-1].endswith("-") and s[:1].islower() and not cur[-1].endswith(" -"):
            cur[-1] = cur[-1][:-1] + s                                                       # "taxa-" + "tion" -> "taxation"
            continue
        if cur and TERMINAL.search(cur[-1]) and s[:1].isupper() and typical and len(cur[-1]) < 0.8 * typical:
            flush()                                                                          # a short last line of a paragraph
        cur.append(s)
    flush()
    return "\n\n".join(paras)


def _first_paragraph(text: str) -> Tuple[str, str]:
    m = re.search(r"\n\s*\n", text)
    return (text[: m.start()], text[m.end():]) if m else (text, "")


def complete_across_pages(pages: List[Dict[str, Any]]) -> int:
    """If a page ends in the middle of a sentence and the next page starts with its lowercase continuation, move the rest of that sentence onto the first page."""
    moved = 0
    live = [p for p in pages if p.get("text")]
    for a, b in zip(live, live[1:]):
        if b["number"] != a["number"] + 1:
            continue
        tail = a["text"].rstrip()
        last_para = tail.split("\n\n")[-1]
        if TERMINAL.search(last_para) or _is_heading(last_para, True, "X"):
            continue
        head, rest = _first_paragraph(b["text"].lstrip())
        if not head or not head[:1].islower():
            continue
        spans = sentence_spans(head)
        end = spans[0][1] if spans else len(head)
        fragment = head[:end].strip()
        if not fragment:
            continue
        left = tail + " " + fragment
        remainder = (head[end:].strip() + ("\n\n" + rest if rest else "")).strip() if head[end:].strip() else rest.strip()
        a["spill"] = {"page": b["number"], "start": len(tail) + 1, "end": len(left)}
        a["text"], b["text"] = left, remainder
        moved += 1
    return moved


def clean_pdf_pages(pages: List[Dict[str, Any]], *, reflow_text: bool = True) -> Dict[str, Any]:
    """`reflow_text=False` keeps the line structure (a caller that parses headings and reference lists line by line, such as the arXiv paper reader, needs it) and only removes running lines."""
    removed = strip_running_lines(pages)
    spills = 0
    if reflow_text:
        for p in pages:
            if p.get("method") in ("text", "ocr") and p.get("text"):
                p["text"] = reflow(p["text"])
        spills = complete_across_pages(pages)
    return {**removed, "sentences_completed_across_pages": spills}
