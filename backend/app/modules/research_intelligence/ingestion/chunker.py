"""Heading-aware chunking. A chunk never crosses a page boundary for PDFs (so a citation names one page) and never splits a table row group. Overlap is carried between
neighbouring paragraph chunks inside one section. Every chunk keeps its section, page range and locator."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .extract import Block


@dataclass
class Chunk:
    ordinal: int
    text: str
    section: Optional[str]
    page_start: Optional[int]
    page_end: Optional[int]
    locator: str
    words: int
    sha256: str


def _sha(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text).strip().lower().encode("utf-8")).hexdigest()


def _sentences(text: str) -> List[str]:
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(\[])", text)
    return [p for p in parts if p.strip()]


def _split_long(text: str, limit: int) -> List[str]:
    """Split one very long paragraph on sentence boundaries (falling back to word windows)."""
    out, buf, n = [], [], 0
    for s in _sentences(text):
        w = len(s.split())
        if w > limit:
            if buf:
                out.append(" ".join(buf)); buf, n = [], 0
            words = s.split()
            out.extend(" ".join(words[i:i + limit]) for i in range(0, len(words), limit))
            continue
        if n + w > limit and buf:
            out.append(" ".join(buf)); buf, n = [], 0
        buf.append(s); n += w
    if buf:
        out.append(" ".join(buf))
    return out


def chunk_blocks(blocks: Sequence[Block], *, target_words: int = 220, overlap_words: int = 40, max_chunks: int = 3000) -> List[Chunk]:
    chunks: List[Chunk] = []
    cur: List[str] = []
    cur_words = 0
    cur_page: Optional[int] = None
    first_page: Optional[int] = None
    cur_loc, cur_section = "", None
    tail = ""

    def flush(force_tail: bool = True) -> None:
        nonlocal cur, cur_words, first_page, cur_loc, tail
        if not cur:
            return
        text = "\n".join(cur).strip()
        if text:
            chunks.append(Chunk(len(chunks), text, cur_section, first_page, cur_page, cur_loc, len(text.split()), _sha(text)))
        words = text.split()
        tail = " ".join(words[-overlap_words:]) if overlap_words and force_tail and len(words) > overlap_words * 2 else ""
        cur, cur_words, first_page, cur_loc = [], 0, None, ""

    for b in blocks:
        if len(chunks) >= max_chunks:
            break
        if b.kind == "heading":
            flush(False)
            tail = ""
            cur_section = b.text
            continue
        if b.kind in ("rows", "table"):
            flush(False)
            tail = ""
            cur, cur_words, cur_page, first_page, cur_loc, cur_section = [b.text], len(b.text.split()), b.page, b.page, b.locator, b.section or cur_section
            flush(False)
            continue
        pieces = _split_long(b.text, target_words) if len(b.text.split()) > target_words else [b.text]
        for piece in pieces:
            w = len(piece.split())
            page_change = cur and b.page is not None and cur_page is not None and b.page != cur_page
            if cur and (cur_words + w > target_words or page_change):
                flush(not page_change)
            if not cur:
                cur_section = b.section or cur_section
                first_page, cur_loc = b.page, b.locator
                if tail:
                    cur.append(tail); cur_words += len(tail.split()); tail = ""
            cur.append(piece); cur_words += w; cur_page = b.page if b.page is not None else cur_page
            if b.page is not None and first_page is None:
                first_page = b.page
            if cur_words >= target_words:
                flush()
    flush(False)
    # merge a tiny trailing chunk into its predecessor when they share a page
    if len(chunks) > 1 and chunks[-1].words < 25 and not re.search(r"rows|profile|table|sheet", chunks[-1].locator + chunks[-2].locator) and chunks[-1].page_start == chunks[-2].page_end and chunks[-1].section == chunks[-2].section:
        last = chunks.pop()
        prev = chunks[-1]
        text = prev.text + "\n" + last.text
        chunks[-1] = Chunk(prev.ordinal, text, prev.section, prev.page_start, last.page_end, prev.locator, len(text.split()), _sha(text))
    # locator: make it unique and human-readable
    for c in chunks:
        if c.page_start is not None and c.page_end is not None and c.page_end != c.page_start:
            c.locator = f"pp.{c.page_start}-{c.page_end}"
        elif c.page_start is not None:
            c.locator = f"p.{c.page_start}"
        elif not c.locator:
            c.locator = f"chunk {c.ordinal + 1}"
    seen: dict = {}
    for c in chunks:
        n = seen.get(c.locator, 0)
        seen[c.locator] = n + 1
        if n:
            c.locator = f"{c.locator} (part {n + 1})"
    return chunks
