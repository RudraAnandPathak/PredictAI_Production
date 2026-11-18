"""Split an extracted document into passages that can be cited. Chunks never cross a page, are built from whole sentences, and keep the exact character span they came from,
so `page_text[start:end] == chunk.text` always holds and any citation can be re-checked against the stored extraction."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from ..textkit import sentence_spans

CHUNKER_VERSION = "1"
MAX_CHUNK_CHARS = 2500


@dataclass
class Chunk:
    id: str
    ordinal: int
    page_start: int
    page_end: int
    section: Optional[str]
    start: int                      # character offsets inside the page text
    end: int
    text: str
    words: int

    def to_json(self) -> Dict[str, Any]:
        return {"id": self.id, "o": self.ordinal, "ps": self.page_start, "pe": self.page_end, "sec": self.section, "s": self.start, "e": self.end, "t": self.text, "w": self.words}

    @staticmethod
    def from_json(d: Dict[str, Any]) -> "Chunk":
        return Chunk(id=d["id"], ordinal=d["o"], page_start=d["ps"], page_end=d["pe"], section=d.get("sec"), start=d["s"], end=d["e"], text=d["t"], words=d["w"])


def _words(text: str) -> int:
    return len(text.split())


def _windows(text: str, target: int, overlap: int) -> List[Tuple[int, int]]:
    spans = sentence_spans(text)
    counts = [_words(text[a:b]) for a, b in spans]
    out: List[Tuple[int, int]] = []
    i = 0
    while i < len(spans):
        j, w = i, 0
        while j < len(spans):
            w += counts[j]
            j += 1
            if w >= target:
                break
        out.append((spans[i][0], spans[j - 1][1]))
        if j >= len(spans):
            break
        k, back = j, 0
        while k - 1 > i and back + counts[k - 1] <= overlap:
            back += counts[k - 1]
            k -= 1
        i = k
    return out


def _heading_events(doc: Dict[str, Any]) -> List[Tuple[int, int, int, str, bool]]:
    pages = {p["number"]: p.get("text") or "" for p in doc.get("pages", [])}
    events = []
    for h in doc.get("headings", []) or []:
        text, page = str(h.get("text", "")).strip(), h.get("page")
        if not text or page not in pages:
            continue
        pos = pages[page].find(text)
        events.append((page, max(pos, 0), int(h.get("level", 1) or 1), text[:120], bool(h.get("heuristic"))))
    events.sort(key=lambda e: (e[0], e[1]))
    return events


def chunk_document(doc: Dict[str, Any], *, target_words: int = 160, overlap_words: int = 30, max_chunks: int = 5000) -> Tuple[List[Chunk], List[str]]:
    warnings: List[str] = []
    events = _heading_events(doc)
    stack: List[Tuple[int, str]] = []
    ei = 0
    chunks: List[Chunk] = []
    empty_pages = 0
    for page in sorted(doc.get("pages", []), key=lambda p: p["number"]):
        text = page.get("text") or ""
        if not text.strip():
            empty_pages += 1
            continue
        for start, end in _windows(text, target_words, overlap_words):
            while ei < len(events) and (events[ei][0], events[ei][1]) <= (page["number"], start):
                _, _, level, title, _ = events[ei]
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, title))
                ei += 1
            body = text[start:end]
            if len(body) > MAX_CHUNK_CHARS:
                body, end = body[:MAX_CHUNK_CHARS], start + MAX_CHUNK_CHARS
            spill = page.get("spill") or {}
            page_end = int(spill["page"]) if spill and end > int(spill.get("start", end)) else page["number"]            # a sentence completed from the next page is cited as both pages
            chunks.append(Chunk(id=f"c{len(chunks) + 1:05d}", ordinal=len(chunks) + 1, page_start=page["number"], page_end=page_end, section=" > ".join(t for _, t in stack) or None,
                                start=start, end=end, text=body, words=_words(body)))
            if len(chunks) >= max_chunks:
                warnings.append(f"The document is very large: only the first {max_chunks} passages were indexed.")
                return chunks, warnings
    if empty_pages:
        warnings.append(f"{empty_pages} page(s) had no readable text and were not indexed.")
    if not chunks:
        warnings.append("No readable text was found, so nothing could be indexed.")
    return chunks, warnings
