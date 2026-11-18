"""RSS 2.0, RSS 1.0 (RDF) and Atom 1.0 feeds, read with the standard library only.

A feed is untrusted input. A document type declaration or entity definition is refused outright (they are how "billion laughs" and external-entity files work), only http(s) links without
credentials are kept, text is stripped of markup and control characters, and the number of items, their size and dates in the future are all bounded. Nothing here fetches anything."""
from __future__ import annotations

import hashlib
import html
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..research.sources import parse_date
from ..webaccess.htmltext import clean_text, strip_tags

MAX_ITEMS = 100
MAX_TITLE, MAX_SUMMARY, MAX_URL = 300, 2000, 2000
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$|cmpid$|ocid$)", re.I)


class FeedError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


def _local(tag: Any) -> str:
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def normalise_url(url: str) -> Optional[str]:
    """http(s) only, no credentials, tracking parameters and fragments removed. Returns None for anything else."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname or parts.username or parts.password:
        return None
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", query, ""))


def _text(node: Optional[ET.Element], limit: int) -> str:
    if node is None:
        return ""
    raw = "".join(node.itertext()) if len(node) else (node.text or "")
    return clean_text(strip_tags(html.unescape(raw)))[:limit]


def _first(entry: ET.Element, *names: str) -> Optional[ET.Element]:
    wanted = set(names)
    for child in entry:
        if _local(child.tag) in wanted and ("".join(child.itertext()).strip() or child.attrib):
            return child
    return None


def _link(entry: ET.Element) -> str:
    for child in entry:
        if _local(child.tag) != "link":
            continue
        if child.get("href"):                                                                  # Atom: prefer rel=alternate (or no rel)
            if child.get("rel", "alternate") == "alternate":
                return child.get("href", "")
        elif (child.text or "").strip():
            return child.text.strip()
    for child in entry:
        if _local(child.tag) == "link" and child.get("href"):
            return child.get("href", "")
    return ""


def item_ids(guid: str, url: str, title: str, published: Optional[str]) -> str:
    basis = guid or url or f"{title}|{published or ''}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:40]


def content_hash(title: str, summary: str) -> str:
    norm = re.sub(r"\W+", " ", f"{title} {summary}".lower()).strip()
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


def parse_feed(body: bytes, *, now: Optional[datetime] = None, max_items: int = MAX_ITEMS) -> Dict[str, Any]:
    """Returns {format, title, language, items, skipped, future_dated}. Raises FeedError for anything that is not a readable feed."""
    now = now or datetime.now(timezone.utc)
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body[:200_000], re.I):
        raise FeedError("FEED_REFUSED", "This feed uses a construct that is refused for safety (a document type or entity definition).")
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FeedError("FEED_UNREADABLE", "This is not a readable RSS or Atom feed.") from None
    kind = _local(root.tag)
    if kind == "rss":
        channel = next((c for c in root if _local(c.tag) == "channel"), None)
        if channel is None:
            raise FeedError("FEED_UNREADABLE", "This RSS feed has no channel.")
        container, entries, fmt = channel, [c for c in channel if _local(c.tag) == "item"], "rss"
    elif kind == "rdf":
        container, entries, fmt = root, [c for c in root if _local(c.tag) == "item"], "rdf"
    elif kind == "feed":
        container, entries, fmt = root, [c for c in root if _local(c.tag) == "entry"], "atom"
    else:
        raise FeedError("FEED_UNREADABLE", "This is not an RSS or Atom feed.")
    title = _text(_first(container, "title"), 200)
    language = (_text(_first(container, "language"), 20) or root.get("{http://www.w3.org/XML/1998/namespace}lang") or "").lower()[:12] or None
    items: List[Dict[str, Any]] = []
    skipped = future = 0
    for entry in entries:
        if len(items) >= max_items:
            skipped += 1
            continue
        url = normalise_url(_link(entry) or _text(_first(entry, "guid", "id"), MAX_URL))
        name = _text(_first(entry, "title"), MAX_TITLE)
        if not url or not name:
            skipped += 1
            continue
        summary = _text(_first(entry, "encoded", "description", "summary", "content"), MAX_SUMMARY)
        when = parse_date(_text(_first(entry, "pubdate", "published", "updated", "date", "issued"), 60))
        if when is not None and when > now + timedelta(days=2):
            when, future = None, future + 1                                                      # a date far in the future is wrong, not news
        author = _text(_first(entry, "creator", "author", "name"), 120) or None
        guid = _text(_first(entry, "guid", "id"), MAX_URL)
        published = when.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if when is not None else None
        items.append({"title": name, "url": url, "guid": guid, "published": published, "summary": summary if summary.lower() != name.lower() else "", "author": author})
    if not items and not entries:
        raise FeedError("FEED_EMPTY", "The feed has no items.")
    return {"format": fmt, "title": title or None, "language": language, "items": items, "skipped": skipped, "future_dated": future}
