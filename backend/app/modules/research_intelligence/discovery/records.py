"""Discovery records: one normalised shape for every provider, duplicate detection and merging, and a transparent ranking.

Rules:
* Nothing is invented. A field a provider did not report stays empty; the citation text is built only from fields that are present.
* A record says what is *accessible*: `access` is `open_full_text` only when a provider reported an open-access PDF address; it is never claimed that a file was downloaded.
* The ranking score is a heuristic for ordering (term coverage, agreement between providers, completeness). It is not a probability and not a quality judgement.
"""
from __future__ import annotations

import hashlib
import math
import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlsplit

PUB_TYPES = ("journal-article", "preprint", "conference-paper", "book", "book-chapter", "review", "thesis", "dataset", "report", "web-page", "other")
STOP = frozenset("a an the of and or for in on to with by from at as is are be this that these those into using use based via its their our we it".split())
DOI_RE = re.compile(r"10\.\d{4,9}/[^\s\"<>]+", re.I)
ARXIV_DOI = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)


def normalise_doi(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    v = re.sub(r"^(https?://)?(dx\.)?doi\.org/", "", v, flags=re.I)
    v = re.sub(r"^doi:\s*", "", v, flags=re.I)
    m = DOI_RE.match(v)
    if not m:
        return None
    return m.group(0).rstrip(".,;)").lower()


def normalise_arxiv(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    m = re.search(r"(\d{4}\.\d{4,5})(v\d+)?\b", v) or re.search(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b", v)
    return m.group(1).lower() if m else None


def strip_markup(text: Any) -> str:
    if not isinstance(text, str):
        return ""
    t = re.sub(r"<[^>]{0,200}>", " ", text)
    t = (t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&#39;", "'").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", t).strip()


def fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def title_key(title: str) -> str:
    t = re.sub(r"[^a-z0-9 ]+", " ", fold(title or "").lower())
    return re.sub(r"\s+", " ", t).strip()


def terms(text: str) -> List[str]:
    return [w for w in re.findall(r"[a-z0-9]{2,}", fold(text or "").lower()) if w not in STOP]


def surname(author: str) -> str:
    a = fold(author or "").strip().lower()
    if "," in a:
        return re.sub(r"[^a-z]", "", a.split(",")[0])
    parts = [p for p in re.split(r"\s+", a) if p]
    return re.sub(r"[^a-z]", "", parts[-1]) if parts else ""


def clean_authors(authors: Iterable[Any], limit: int = 60) -> List[str]:
    out: List[str] = []
    for a in authors or []:
        name = strip_markup(a) if isinstance(a, str) else ""
        if name and name not in out:
            out.append(name[:120])
        if len(out) >= limit:
            break
    return out


def parse_date(value: Any) -> Tuple[Optional[str], Optional[int]]:
    """(`YYYY`, `YYYY-MM` or `YYYY-MM-DD`; year). Anything else is dropped rather than guessed."""
    if isinstance(value, int) and 1000 <= value <= 2200:
        return str(value), value
    if not isinstance(value, str):
        return None, None
    m = re.match(r"^(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", value.strip())
    if not m:
        return None, None
    y = int(m.group(1))
    if not 1000 <= y <= 2200:
        return None, None
    out = m.group(1)
    if m.group(2) and 1 <= int(m.group(2)) <= 12:
        out += f"-{int(m.group(2)):02d}"
        if m.group(3) and 1 <= int(m.group(3)) <= 31:
            out += f"-{int(m.group(3)):02d}"
    return out, y


def safe_http_url(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    v = value.strip()
    sp = urlsplit(v)
    if sp.scheme not in ("http", "https") or not sp.hostname or sp.username or len(v) > 600:
        return None
    return v


TYPE_MAP = {
    "journal-article": "journal-article", "article": "journal-article", "journalarticle": "journal-article", "journal article": "journal-article",
    "posted-content": "preprint", "preprint": "preprint", "proceedings-article": "conference-paper", "conference": "conference-paper", "conferencepaper": "conference-paper", "conference-paper": "conference-paper",
    "book": "book", "monograph": "book", "edited-book": "book", "book-chapter": "book-chapter", "chapter": "book-chapter", "bookchapter": "book-chapter", "review": "review", "review-article": "review",
    "dissertation": "thesis", "thesis": "thesis", "dataset": "dataset", "report": "report", "web": "web-page",
}


def normalise_type(value: Any) -> str:
    return TYPE_MAP.get(str(value or "").strip().lower(), "other")


@dataclass
class Record:
    title: str
    authors: List[str] = field(default_factory=list)
    abstract: Optional[str] = None
    published: Optional[str] = None
    year: Optional[int] = None
    venue: Optional[str] = None
    pub_type: str = "other"
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    oa_url: Optional[str] = None             # an open-access PDF or full-text address reported by a provider (not downloaded)
    license: Optional[str] = None
    fields: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    citation_count: Optional[int] = None
    citation_source: Optional[str] = None    # which provider reported citation_count (counts differ between providers)
    reference_count: Optional[int] = None
    providers: Dict[str, Dict[str, Any]] = field(default_factory=dict)   # provider -> {"rank": n, "id": provider's own id}
    kind: str = "paper"                      # paper | web

    # ------------------------------------------------------------------ derived
    @property
    def access(self) -> str:
        if self.oa_url:
            return "open_full_text"
        if self.abstract:
            return "abstract"
        return "metadata"

    def dedupe_keys(self) -> List[str]:
        keys: List[str] = []
        if self.doi:
            keys.append(f"doi:{self.doi}")
            m = ARXIV_DOI.match(self.doi)
            if m:
                keys.append(f"arxiv:{normalise_arxiv(m.group(1)) or m.group(1).lower()}")
        if self.arxiv_id:
            keys.append(f"arxiv:{self.arxiv_id}")
        tk = title_key(self.title)
        if len(tk) >= 12:
            first = surname(self.authors[0]) if self.authors else ""
            keys.append(f"title:{tk}|{first}")
        if self.kind == "web" and self.url:
            keys.append("url:" + re.sub(r"^https?://(www\.)?", "", self.url.lower()).rstrip("/"))
        return keys

    @property
    def key(self) -> str:
        return self.dedupe_keys()[0] if self.dedupe_keys() else f"raw:{hashlib.sha256(self.title.encode()).hexdigest()[:16]}"

    @property
    def rid(self) -> str:
        return hashlib.sha256(self.key.encode()).hexdigest()[:16]

    def to_dict(self) -> Dict[str, Any]:
        from .citation import apa, bibtex
        return {"rid": self.rid, "kind": self.kind, "title": self.title, "authors": self.authors, "abstract": self.abstract, "published": self.published, "year": self.year, "venue": self.venue,
                "type": self.pub_type, "doi": self.doi, "arxiv_id": self.arxiv_id, "url": self.url, "oa_url": self.oa_url, "license": self.license, "fields": self.fields, "keywords": self.keywords,
                "citation_count": self.citation_count, "citation_source": self.citation_source, "reference_count": self.reference_count, "access": self.access,
                "providers": self.providers, "citation": {"apa": apa(self), "bibtex": bibtex(self)}}

    @staticmethod
    def from_dict(d: Dict[str, Any]) -> "Record":
        return Record(title=d.get("title") or "Untitled", authors=list(d.get("authors") or []), abstract=d.get("abstract"), published=d.get("published"), year=d.get("year"), venue=d.get("venue"),
                      pub_type=d.get("type") or "other", doi=d.get("doi"), arxiv_id=d.get("arxiv_id"), url=d.get("url"), oa_url=d.get("oa_url"), license=d.get("license"), fields=list(d.get("fields") or []),
                      keywords=list(d.get("keywords") or []), citation_count=d.get("citation_count"), citation_source=d.get("citation_source"), reference_count=d.get("reference_count"),
                      providers=dict(d.get("providers") or {}), kind=d.get("kind") or "paper")


def finish(r: Record) -> Optional[Record]:
    """Validate and normalise a record built by a provider. Returns None for an unusable one (no title)."""
    r.title = strip_markup(r.title)[:400]
    if not r.title:
        return None
    r.authors = clean_authors(r.authors)
    r.abstract = (strip_markup(r.abstract)[:6000] or None) if r.abstract else None
    r.published, y = parse_date(r.published)
    r.year = r.year if isinstance(r.year, int) and 1000 <= r.year <= 2200 else y
    r.doi = normalise_doi(r.doi)
    r.arxiv_id = normalise_arxiv(r.arxiv_id) if r.arxiv_id else None
    if r.doi and not r.arxiv_id:
        m = ARXIV_DOI.match(r.doi)
        if m:
            r.arxiv_id = normalise_arxiv(m.group(1))
    r.url, r.oa_url = safe_http_url(r.url), safe_http_url(r.oa_url)
    r.venue = strip_markup(r.venue)[:300] or None if r.venue else None
    r.fields = [strip_markup(f)[:80] for f in r.fields if isinstance(f, str) and strip_markup(f)][:12]
    r.keywords = [strip_markup(k)[:60] for k in r.keywords if isinstance(k, str) and strip_markup(k)][:20]
    for attr in ("citation_count", "reference_count"):
        v = getattr(r, attr)
        setattr(r, attr, int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v >= 0 else None)
    if r.citation_count is None:
        r.citation_source = None
    return r


# ---------------------------------------------------------------------- duplicate detection and merging
def _same(a: Record, b: Record) -> bool:
    ka, kb = set(a.dedupe_keys()), set(b.dedupe_keys())
    if any(k.startswith(("doi:", "arxiv:", "url:")) and k in kb for k in ka):
        return True
    if any(k.startswith("title:") and k in kb for k in ka):
        # same normalised title and first author: accept unless the years are far apart (a reprint, not the same work)
        return a.year is None or b.year is None or abs(a.year - b.year) <= 1
    return False


def _merge_into(base: Record, other: Record) -> None:
    """Fill gaps in `base` from `other`; keep the richer value when both exist. Never overwrite a DOI with a different one."""
    if len(other.title) > len(base.title) and title_key(other.title) == title_key(base.title):
        base.title = other.title
    if not base.authors or (len(other.authors) > len(base.authors) and surname(other.authors[0]) == surname(base.authors[0])):
        base.authors = other.authors
    if other.abstract and (not base.abstract or len(other.abstract) > len(base.abstract)):
        base.abstract = other.abstract
    for attr in ("venue", "license"):
        if getattr(other, attr) and not getattr(base, attr):
            setattr(base, attr, getattr(other, attr))
    if other.published and (not base.published or len(other.published) > len(base.published)):
        base.published, base.year = other.published, other.year
    if base.pub_type == "other" and other.pub_type != "other":
        base.pub_type = other.pub_type
    elif base.pub_type == "preprint" and other.pub_type in ("journal-article", "conference-paper", "book-chapter", "book"):
        base.pub_type = other.pub_type            # the same work also exists in a published version
    if not base.doi and other.doi:
        base.doi = other.doi
    if not base.arxiv_id and other.arxiv_id:
        base.arxiv_id = other.arxiv_id
    base.url = base.url or other.url
    base.oa_url = base.oa_url or other.oa_url
    base.fields = list(dict.fromkeys(base.fields + other.fields))[:12]
    base.keywords = list(dict.fromkeys(base.keywords + other.keywords))[:20]
    if other.citation_count is not None and (base.citation_count is None or other.citation_count > base.citation_count):
        base.citation_count, base.citation_source = other.citation_count, other.citation_source
    if other.reference_count is not None and base.reference_count is None:
        base.reference_count = other.reference_count
    base.providers.update(other.providers)


def merge_records(batches: List[Tuple[str, List[Record]]]) -> List[Record]:
    """Duplicates (same DOI, arXiv id, or same title and first author within a year) become one record that remembers every provider that returned it."""
    merged: List[Record] = []
    for provider, records in batches:
        for rank, rec in enumerate(records, start=1):
            rec.providers.setdefault(provider, {})["rank"] = rank
            target = next((m for m in merged if _same(m, rec)), None)
            if target is None:
                merged.append(rec)
            else:
                _merge_into(target, rec)
    return merged


# ---------------------------------------------------------------------- ranking
def rank_records(records: List[Record], query_terms: List[str], *, sort: str = "relevance") -> List[Tuple[Record, float, List[str]]]:
    """Order by `sort`. For relevance: reciprocal-rank agreement between providers + how many query terms the title/abstract contain + completeness. Returns (record, score, reasons)."""
    qs = list(dict.fromkeys(query_terms))
    out: List[Tuple[Record, float, List[str]]] = []
    for r in records:
        reasons: List[str] = []
        score = sum(1.0 / (60 + v.get("rank", 60)) for v in r.providers.values()) * 60            # reciprocal-rank fusion; 1.0 means rank 1 at one provider
        if len(r.providers) > 1:
            reasons.append(f"returned by {len(r.providers)} providers")
        if qs:
            ttl, abs_ = set(terms(r.title)), set(terms(r.abstract or ""))
            in_t = sum(1 for q in qs if q in ttl) / len(qs)
            in_a = sum(1 for q in qs if q in abs_ or q in ttl) / len(qs)
            score += 2.0 * in_t + 1.0 * in_a
            if in_t >= 0.99:
                reasons.append("all search terms in the title")
            elif in_a >= 0.99:
                reasons.append("all search terms in the title or abstract")
            elif in_a > 0:
                reasons.append(f"{round(in_a * 100)}% of search terms found")
        if r.abstract:
            score += 0.3
        if r.oa_url:
            score += 0.2
            reasons.append("open full text reported")
        if r.citation_count:
            score += min(0.6, math.log10(1 + r.citation_count) / 8)
        out.append((r, round(score, 3), reasons))
    if sort == "date":
        out.sort(key=lambda t: (t[0].published or "", t[1]), reverse=True)
    elif sort == "citations":
        out.sort(key=lambda t: (t[0].citation_count or -1, t[1]), reverse=True)
    else:
        out.sort(key=lambda t: (-t[1], t[0].title.lower()))
    return out


def author_matches(r: Record, needle: str) -> bool:
    n = surname(needle) if needle.strip() else ""
    toks = [fold(t).lower() for t in re.findall(r"[A-Za-zÀ-ɏ\-']{2,}", needle)]
    if not toks:
        return True
    hay = fold(" ".join(r.authors)).lower()
    return all(t in hay for t in toks) if len(toks) > 1 else (n in hay if n else True)
