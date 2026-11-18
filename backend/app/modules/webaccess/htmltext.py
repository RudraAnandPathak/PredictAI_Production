"""HTML (and plain text) to readable text plus source metadata, using only the standard library.

Retrieved pages are untrusted. This module never executes anything: scripts, styles, frames, objects and templates are discarded with their content;
elements hidden from a reader (hidden attribute, display:none, visibility:hidden, zero font size, aria-hidden) are discarded too, because hidden text is the
usual hiding place for instructions aimed at AI systems; the count of such blocks is reported as a signal. Output is bounded."""
from __future__ import annotations

import html
import json
import re
import unicodedata
from html.parser import HTMLParser
from typing import Any, Dict, List, Optional, Tuple

MAX_TEXT_CHARS = 250_000
MAX_PARAGRAPHS = 4000
SKIP = {"script", "style", "noscript", "template", "svg", "canvas", "iframe", "object", "embed", "applet", "audio", "video", "map", "head", "select", "option", "button", "input", "textarea", "dialog"}
BOILER = {"nav", "footer", "aside", "header"}
BLOCK = {"p", "div", "section", "article", "main", "li", "ul", "ol", "dl", "dt", "dd", "blockquote", "pre", "table", "tr", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "figcaption", "br", "hr", "address", "details", "summary"}
FLUSH = BLOCK | BOILER
VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "param", "source", "track", "wbr"}
HIDDEN_STYLE = re.compile(r"(display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(px|pt|em|rem|%)?\s*(;|$)|opacity\s*:\s*0(\.0+)?\s*(;|$))", re.I)
_WS = re.compile(r"[ \t\r\f\v ]+")
DATE_KEYS = ("article:published_time", "og:article:published_time", "datepublished", "date", "dc.date", "dc.date.issued", "pubdate", "publishdate", "publish-date", "parsely-pub-date", "sailthru.date")


def clean_text(text: str) -> str:
    text = unicodedata.normalize("NFC", text)
    text = "".join(ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co", "Cn"))
    return _WS.sub(" ", text.replace("\n", " ")).strip()


class _Extractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: List[Tuple[str, bool, bool, bool]] = []     # (tag, skipped, boiler, main)
        self.title_parts: List[str] = []
        self.in_title = False
        self.meta: Dict[str, str] = {}
        self.jsonld: List[str] = []
        self._in_jsonld = False
        self.blocks: List[Dict[str, Any]] = []                  # {text, kind, main, boiler}
        self.cur: List[str] = []
        self.cur_kind = "p"
        self.hidden_removed = 0
        self.time_attr: Optional[str] = None
        self.canonical: Optional[str] = None
        self.lang: Optional[str] = None
        self.chars = 0

    def _skipping(self) -> bool:
        return any(s for _t, s, _b, _m in self.stack)

    def _boiler(self) -> bool:
        return any(b for _t, _s, b, _m in self.stack)

    def _main(self) -> bool:
        return any(m for _t, _s, _b, m in self.stack)

    def _flush(self) -> None:
        text = clean_text(" ".join(self.cur))
        self.cur = []
        if text and len(self.blocks) < MAX_PARAGRAPHS and self.chars < MAX_TEXT_CHARS:
            self.blocks.append({"text": text, "kind": self.cur_kind, "main": self._main(), "boiler": self._boiler()})
            self.chars += len(text)
        self.cur_kind = "p"

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html" and a.get("lang"):
            self.lang = a["lang"][:12]
        if tag == "meta":
            name = (a.get("property") or a.get("name") or a.get("itemprop") or "").lower()
            if name and a.get("content") and name not in self.meta:
                self.meta[name] = clean_text(a["content"])[:500]
            return
        if tag == "link":
            if "canonical" in a.get("rel", "").lower() and a.get("href"):
                self.canonical = a["href"][:2048]
            return
        if tag == "time" and a.get("datetime") and self.time_attr is None and not self._skipping():
            self.time_attr = a["datetime"][:40]
        if tag == "title" and not self._skipping_except_head():
            self.in_title = True
            return
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._in_jsonld = True
        if tag == "body":                                                # a <head> that was never closed must not swallow the page
            self.stack = [entry for entry in self.stack if entry[0] != "head"]
        if tag in VOID:
            if tag in BLOCK:
                self._flush()
            return
        hidden = ("hidden" in a) or a.get("aria-hidden", "").lower() == "true" or bool(HIDDEN_STYLE.search(a.get("style", "")))
        skipped = tag in SKIP or hidden
        if hidden and tag not in SKIP:
            self.hidden_removed += 1
        if tag in FLUSH:
            self._flush()
        if tag.startswith("h") and len(tag) == 2 and tag[1].isdigit():
            self.cur_kind = "h"
        elif tag == "li":
            self.cur_kind = "li"
        self.stack.append((tag, skipped, tag in BOILER, tag in ("article", "main")))

    def _skipping_except_head(self) -> bool:
        return any(s for t, s, _b, _m in self.stack if t != "head")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self.in_title = False
            return
        if tag == "script":
            self._in_jsonld = False
        for i in range(len(self.stack) - 1, -1, -1):
            if self.stack[i][0] == tag:
                if tag in FLUSH:
                    self._flush()
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)
            return
        if self._in_jsonld:
            if sum(len(x) for x in self.jsonld) < 100_000:
                self.jsonld.append(data)
            return
        if self._skipping():
            return
        self.cur.append(data)

    def close(self) -> None:
        super().close()
        self._flush()


def _jsonld_meta(chunks: List[str]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        data = json.loads("".join(chunks))
    except (ValueError, RecursionError):
        return out
    stack = [data]
    steps = 0
    while stack and steps < 200:
        steps += 1
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item[:20])
        elif isinstance(item, dict):
            for key in ("datePublished", "dateCreated", "dateModified", "headline", "name"):
                if isinstance(item.get(key), str) and key not in out:
                    out[key] = item[key][:300]
            author = item.get("author")
            if isinstance(author, dict) and isinstance(author.get("name"), str):
                out.setdefault("author", author["name"][:200])
            elif isinstance(author, list) and author and isinstance(author[0], dict) and isinstance(author[0].get("name"), str):
                out.setdefault("author", author[0]["name"][:200])
            elif isinstance(author, str):
                out.setdefault("author", author[:200])
            publisher = item.get("publisher")
            if isinstance(publisher, dict) and isinstance(publisher.get("name"), str):
                out.setdefault("publisher", publisher["name"][:200])
            if isinstance(item.get("@graph"), list):
                stack.extend(item["@graph"][:30])
    return out


def extract_html(markup: str) -> Dict[str, Any]:
    parser = _Extractor()
    try:
        parser.feed(markup[:3_000_000])
        parser.close()
    except (AssertionError, RecursionError, ValueError):                 # malformed beyond repair: keep what was read so far
        parser._flush()
    ld = _jsonld_meta(parser.jsonld)
    meta = parser.meta
    main_blocks = [b for b in parser.blocks if b["main"] and not b["boiler"]]
    use_main = sum(len(b["text"]) for b in main_blocks) >= 200
    chosen = main_blocks if use_main else ([b for b in parser.blocks if not b["boiler"]] or parser.blocks)
    paragraphs: List[Dict[str, str]] = []
    seen = set()
    for b in chosen:
        if len(b["text"]) < 25 and b["kind"] != "h":
            continue
        key = b["text"].lower()
        if key in seen:
            continue
        seen.add(key)
        paragraphs.append({"kind": b["kind"], "text": b["text"]})
    published = next((meta[k] for k in DATE_KEYS if meta.get(k)), None) or ld.get("datePublished") or ld.get("dateCreated") or parser.time_attr
    title = clean_text(" ".join(parser.title_parts))[:300] or meta.get("og:title") or ld.get("headline") or ""
    return {
        "title": title,
        "text": "\n\n".join(p["text"] for p in paragraphs)[:MAX_TEXT_CHARS],
        "paragraphs": paragraphs,
        "meta": {"site_name": meta.get("og:site_name") or ld.get("publisher") or meta.get("application-name"), "author": meta.get("author") or ld.get("author"),
                 "published": published, "modified": meta.get("article:modified_time") or ld.get("dateModified"), "description": meta.get("description") or meta.get("og:description"),
                 "canonical": parser.canonical, "language": parser.lang or meta.get("og:locale")},
        "hidden_blocks_removed": parser.hidden_removed,
        "used_main_content": use_main,
    }


def extract_plain(text: str) -> Dict[str, Any]:
    paragraphs = [{"kind": "p", "text": clean_text(p)} for p in re.split(r"\n\s*\n", text) if len(clean_text(p)) >= 25][:MAX_PARAGRAPHS]
    return {"title": "", "text": "\n\n".join(p["text"] for p in paragraphs)[:MAX_TEXT_CHARS], "paragraphs": paragraphs,
            "meta": {"site_name": None, "author": None, "published": None, "modified": None, "description": None, "canonical": None, "language": None}, "hidden_blocks_removed": 0, "used_main_content": False}


def decode_body(body: bytes, content_type: str) -> str:
    charset = None
    m = re.search(r"charset=([\w\-]+)", content_type or "", re.I)
    if m:
        charset = m.group(1)
    if charset is None:
        m2 = re.search(rb"<meta[^>]+charset=[\"']?([\w\-]+)", body[:4096], re.I)
        if m2:
            charset = m2.group(1).decode("ascii", "ignore")
    for enc in (charset, "utf-8"):
        if not enc:
            continue
        try:
            return body.decode(enc, errors="strict" if enc == "utf-8" else "replace")
        except (LookupError, UnicodeDecodeError):
            continue
    return body.decode("utf-8", errors="replace")


def strip_tags(snippet: str) -> str:
    """For short API snippets that contain simple markup such as <span class="searchmatch">."""
    return clean_text(html.unescape(re.sub(r"<[^>]{0,200}>", "", snippet or "")))
