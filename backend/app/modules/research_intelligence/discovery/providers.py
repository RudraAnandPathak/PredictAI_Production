"""Academic discovery providers: Crossref, OpenAlex, arXiv, Semantic Scholar and (web, via the existing Phase 6 provider) SearXNG.

Each provider turns a `Query` into normalised `Record`s using the service's documented public API, or reports honestly why it could not:
`ok`, `skipped` (the provider cannot answer this kind of query), `rate_limited`, `needs_key`/`key_rejected`, `unavailable`, `unreachable`, `bad_answer`.
Nothing here downloads a paper. `oa_url` is only an address the provider reported as open access.

Verified against: the providers' public documentation (Crossref REST API, OpenAlex developer docs, arXiv API user manual, Semantic Scholar Academic Graph API) and tests with injected
responses. NOT verified against the live services from the build sandbox (no network): `scripts/verify_phase10_live.py` does that on a machine with internet access.
"""
from __future__ import annotations

import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from ..security.urlguard import UrlRejected
from .records import Record, finish, normalise_arxiv, normalise_doi, normalise_type, strip_markup, terms

JSON_T = ("application/json",)
ATOM_T = ("application/atom+xml", "application/xml", "text/xml")


@dataclass
class Query:
    q: str = ""
    title: str = ""
    author: str = ""
    doi: str = ""
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    field: str = ""
    pub_type: str = ""
    open_access: bool = False
    sort: str = "relevance"
    limit: int = 25

    def text(self) -> str:
        return " ".join(x for x in (self.q, self.title) if x).strip()

    def terms(self) -> List[str]:
        return terms(self.text())


@dataclass
class ProviderResult:
    provider: str
    label: str
    status: str                                  # ok | skipped | rate_limited | needs_key | key_rejected | unavailable | unreachable | bad_answer
    records: List[Record] = field(default_factory=list)
    message: str = ""
    fix: str = ""
    total: Optional[int] = None
    retry_after: Optional[float] = None
    notes: List[str] = field(default_factory=list)

    def public(self) -> Dict[str, Any]:
        return {"provider": self.provider, "label": self.label, "status": self.status, "count": len(self.records), "total": self.total, "message": self.message or None,
                "fix": self.fix or None, "retry_after_s": self.retry_after, "notes": self.notes}


class ProviderError(Exception):
    def __init__(self, status: str, message: str, fix: str = "", retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.status, self.message, self.fix, self.retry_after = status, message, fix, retry_after


class ProviderGate:
    """Polite pacing per provider (minimum gap between requests) plus a cool-down after a 429/503. Process-wide; thread-safe. `clock` and `sleep` are injectable for tests."""

    def __init__(self, clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep, max_wait_s: float = 4.0) -> None:
        self.clock, self.sleep, self.max_wait_s = clock, sleep, max_wait_s
        self._last: Dict[str, float] = {}
        self._until: Dict[str, float] = {}
        self._lock = threading.Lock()

    def acquire(self, key: str, min_interval_s: float) -> Optional[float]:
        """None when the call may go ahead (after waiting at most `max_wait_s`); otherwise the seconds until the provider may be used again."""
        with self._lock:
            now = self.clock()
            until = self._until.get(key, 0.0)
            if until > now:
                return round(until - now, 1)
            wait = max(0.0, self._last.get(key, -1e9) + min_interval_s - now)
            if wait > self.max_wait_s:
                return round(wait, 1)
            self._last[key] = now + wait
        if wait > 0:
            self.sleep(wait)
        return None

    def cool_down(self, key: str, seconds: float) -> None:
        with self._lock:
            self._until[key] = self.clock() + max(1.0, min(seconds, 900.0))


GATE = ProviderGate()
PROVIDER_ORDER = ("crossref", "openalex", "arxiv", "semanticscholar", "searxng")


class Provider:
    key = ""
    label = ""
    host = ""
    min_interval_s = 1.0
    description = ""

    def available(self, settings: Any) -> Tuple[bool, str]:
        return True, ""

    def describe(self, settings: Any) -> Dict[str, Any]:
        ok, why = self.available(settings)
        return {"key": self.key, "label": self.label, "host": self.host, "description": self.description, "available": ok, "reason": why or None}

    def _fetch(self, fetcher: Any, url: str, accept=JSON_T, headers: Optional[Dict[str, str]] = None, tries: int = 2) -> Any:
        last: Optional[ProviderError] = None
        for attempt in range(tries):
            try:
                return fetcher.fetch(url, accept=accept, extra_headers=headers or None)
            except UrlRejected as e:
                last = self._explain(e)
                if last.status in ("rate_limited", "needs_key", "key_rejected") or attempt + 1 >= tries or last.status not in ("unavailable", "unreachable"):
                    raise last from None
                time.sleep(0.4)
        raise last or ProviderError("unavailable", f"{self.label} could not be used.")

    def _explain(self, e: UrlRejected) -> ProviderError:
        s = e.status
        if s == 429:
            return ProviderError("rate_limited", f"{self.label} is limiting requests right now.", "Wait a little; results are kept in your search history so you do not need to repeat searches.", e.retry_after or 30)
        if s in (401, 403):
            return ProviderError("key_rejected", f"{self.label} refused the request ({s}).", self._key_fix())
        if s is not None and s >= 500:
            return ProviderError("unavailable", f"{self.label} answered with a server error ({s}).", "Try again later.", e.retry_after)
        if s == 404:
            return ProviderError("bad_answer", f"{self.label} has no matching record or the request was not understood (404).")
        if s is not None and s >= 400:
            return ProviderError("bad_answer", f"{self.label} rejected the request ({s}).", "Try simpler search words or fewer filters.")
        if e.code in ("URL_FETCH_FAILED", "URL_TIMEOUT"):
            return ProviderError("unreachable", f"{self.label} could not be reached from this server.", "Check the server's internet connection, proxy or firewall.")
        return ProviderError("unavailable", f"{self.label} could not be used ({e.code}).")

    def _key_fix(self) -> str:
        return "Check the provider's access rules."

    @staticmethod
    def _json(res: Any, label: str) -> Any:
        try:
            return json.loads(res.body.decode("utf-8", "replace"))
        except ValueError:
            raise ProviderError("bad_answer", f"{label} answered in a form that could not be read.", "The service may have changed its format or returned an error page; try again later.") from None

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:                    # pragma: no cover - interface
        raise NotImplementedError

    def run(self, fetcher: Any, q: Query, settings: Any, gate: ProviderGate) -> ProviderResult:
        ok, why = self.available(settings)
        if not ok:
            return ProviderResult(self.key, self.label, "skipped", message=why)
        wait = gate.acquire(self.key, self.min_interval_s)
        if wait is not None:
            return ProviderResult(self.key, self.label, "rate_limited", message=f"{self.label} was used a moment ago; its polite request interval has not passed.", retry_after=wait,
                                  fix="Searching again in a few seconds will include it.")
        try:
            res = self.search(fetcher, q, settings)
        except ProviderError as e:
            if e.status == "rate_limited":
                gate.cool_down(self.key, e.retry_after or 30)
            elif e.status == "unavailable":
                gate.cool_down(self.key, e.retry_after or 10)
            return ProviderResult(self.key, self.label, e.status, message=e.message, fix=e.fix, retry_after=e.retry_after)
        except (KeyError, TypeError, ValueError, AttributeError, ET.ParseError):                 # an unexpected answer shape must not take the whole search down
            return ProviderResult(self.key, self.label, "bad_answer", message=f"{self.label} answered in a form that could not be read.", fix="The service may have changed its format; try again later.")
        return res


# ---------------------------------------------------------------------------------------------------------------------------- Crossref
class Crossref(Provider):
    key, label, host = "crossref", "Crossref", "api.crossref.org"
    min_interval_s = 0.3
    description = "DOI registry: journal articles, conference papers, books. Metadata and sometimes abstracts; open full text only when a Creative Commons licence and a PDF link are given."
    TYPES = {"journal-article": "journal-article", "preprint": "posted-content", "conference-paper": "proceedings-article", "book": "book", "book-chapter": "book-chapter", "thesis": "dissertation", "dataset": "dataset", "report": "report"}

    def _mail(self, settings: Any) -> str:
        return (getattr(settings, "ri_crossref_mailto", "") or "").strip()

    def _url(self, path: str, params: Dict[str, Any], settings: Any) -> str:
        if self._mail(settings):
            params["mailto"] = self._mail(settings)
        return f"https://api.crossref.org/{path}?{urlencode(params)}"

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:
        if q.doi:
            doi = normalise_doi(q.doi) or ""
            res = self._fetch(fetcher, f"https://api.crossref.org/works/{quote(doi, safe='/')}" + (f"?mailto={quote(self._mail(settings))}" if self._mail(settings) else ""))
            msg = (self._json(res, self.label) or {}).get("message") or {}
            rec = self._record(msg, 1) if msg else None
            return ProviderResult(self.key, self.label, "ok", [rec] if rec else [], total=1 if rec else 0)
        params: Dict[str, Any] = {"rows": max(1, min(q.limit, 100)), "select": "DOI,title,author,issued,published,container-title,abstract,type,URL,is-referenced-by-count,references-count,subject,license,link"}
        if q.q:
            params["query.bibliographic" if len(q.q.split()) > 1 else "query"] = q.q
        if q.title:
            params["query.title"] = q.title
        if q.author:
            params["query.author"] = q.author
        filters: List[str] = []
        if q.year_from:
            filters.append(f"from-pub-date:{q.year_from}-01-01")
        if q.year_to:
            filters.append(f"until-pub-date:{q.year_to}-12-31")
        if q.pub_type in self.TYPES:
            filters.append(f"type:{self.TYPES[q.pub_type]}")
        if filters:
            params["filter"] = ",".join(filters)
        if q.sort == "citations":
            params.update(sort="is-referenced-by-count", order="desc")
        elif q.sort == "date":
            params.update(sort="published", order="desc")
        data = (self._json(self._fetch(fetcher, self._url("works", params, settings)), self.label) or {}).get("message") or {}
        records = [r for r in (self._record(it, i + 1) for i, it in enumerate(data.get("items") or [])) if r]
        return ProviderResult(self.key, self.label, "ok", records, total=data.get("total-results") if isinstance(data.get("total-results"), int) else None)

    def _record(self, m: Dict[str, Any], rank: int) -> Optional[Record]:
        authors = [" ".join(x for x in (a.get("given"), a.get("family")) if x) or a.get("name", "") for a in (m.get("author") or []) if isinstance(a, dict)]
        parts = ((m.get("issued") or m.get("published") or {}).get("date-parts") or [[None]])[0] or [None]
        date = "-".join(f"{int(x):02d}" if i else str(x) for i, x in enumerate(parts) if x)
        abstract = re.sub(r"^\s*abstract\s*[:.\-]?\s*", "", strip_markup(m.get("abstract") or ""), flags=re.I) or None
        licenses = [l.get("URL", "") for l in (m.get("license") or []) if isinstance(l, dict)]
        cc = next((u for u in licenses if "creativecommons.org" in u), None)
        pdf = next((l.get("URL") for l in (m.get("link") or []) if isinstance(l, dict) and "pdf" in str(l.get("content-type", "")).lower()), None) if cc else None
        doi = m.get("DOI")
        rec = Record(title=(m.get("title") or [""])[0] if isinstance(m.get("title"), list) else str(m.get("title") or ""), authors=authors, abstract=abstract, published=date or None,
                     venue=(m.get("container-title") or [""])[0] if isinstance(m.get("container-title"), list) else None, pub_type=normalise_type(m.get("type")), doi=doi,
                     url=m.get("URL") or (f"https://doi.org/{doi}" if doi else None), oa_url=pdf, license=cc, fields=list(m.get("subject") or []),
                     citation_count=m.get("is-referenced-by-count"), citation_source="crossref", reference_count=m.get("references-count"), providers={self.key: {"id": doi}})
        return finish(rec)


# ---------------------------------------------------------------------------------------------------------------------------- OpenAlex
class OpenAlex(Provider):
    key, label, host = "openalex", "OpenAlex", "api.openalex.org"
    min_interval_s = 0.2
    description = "Open catalogue of scholarly works with citation counts, fields and open-access locations. Since 13 Feb 2026 a free API key is needed for normal use (PREDICTAI_RI_OPENALEX_API_KEY)."
    TYPES = {"journal-article": "article", "preprint": "preprint", "book": "book", "book-chapter": "book-chapter", "thesis": "dissertation", "dataset": "dataset", "review": "review", "report": "report"}

    @staticmethod
    def _api_key() -> str:
        return (os.environ.get("PREDICTAI_RI_OPENALEX_API_KEY") or "").strip()

    def _key_fix(self) -> str:
        return "Create a free key at https://openalex.org/settings/api and set PREDICTAI_RI_OPENALEX_API_KEY on the server. Without a key OpenAlex allows only a very small daily test allowance."

    def _url(self, path: str, params: Dict[str, Any], settings: Any) -> str:
        if self._api_key():
            params["api_key"] = self._api_key()
        mail = (getattr(settings, "ri_crossref_mailto", "") or "").strip()
        if mail:
            params["mailto"] = mail
        return f"https://api.openalex.org/{path}?{urlencode(params)}"

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:
        notes: List[str] = []
        if not self._api_key():
            notes.append("No OpenAlex API key is configured; OpenAlex only allows a very small daily allowance without one.")
        if q.doi:
            doi = normalise_doi(q.doi) or ""
            data = self._json(self._fetch(fetcher, self._url("works", {"filter": f"doi:https://doi.org/{doi}", "per_page": 1}, settings)), self.label) or {}
            recs = [r for r in (self._record(w, 1) for w in data.get("results") or []) if r]
            return ProviderResult(self.key, self.label, "ok", recs, total=len(recs), notes=notes)
        filters: List[str] = []
        if q.title:
            filters.append("title.search:" + re.sub(r"[,|:]", " ", q.title))
        if q.year_from:
            filters.append(f"from_publication_date:{q.year_from}-01-01")
        if q.year_to:
            filters.append(f"to_publication_date:{q.year_to}-12-31")
        if q.pub_type in self.TYPES:
            filters.append(f"type:{self.TYPES[q.pub_type]}")
        if q.open_access:
            filters.append("open_access.is_oa:true")
        if q.author:
            ids = self._author_ids(fetcher, q.author, settings)
            if not ids:
                return ProviderResult(self.key, self.label, "ok", [], total=0, notes=notes + [f"No OpenAlex author profile matched “{q.author}”."])
            filters.append("authorships.author.id:" + "|".join(ids))
            notes.append("Author search matches OpenAlex author profiles by name; different people can share a name.")
        params: Dict[str, Any] = {"per_page": max(1, min(q.limit, 100))}
        if q.q:
            params["search"] = q.q
        if filters:
            params["filter"] = ",".join(filters)
        if q.sort == "citations":
            params["sort"] = "cited_by_count:desc"
        elif q.sort == "date":
            params["sort"] = "publication_date:desc"
        data = self._json(self._fetch(fetcher, self._url("works", params, settings)), self.label) or {}
        recs = [r for r in (self._record(w, i + 1) for i, w in enumerate(data.get("results") or [])) if r]
        total = (data.get("meta") or {}).get("count")
        return ProviderResult(self.key, self.label, "ok", recs, total=total if isinstance(total, int) else None, notes=notes)

    def _author_ids(self, fetcher: Any, name: str, settings: Any) -> List[str]:
        data = self._json(self._fetch(fetcher, self._url("authors", {"search": name, "per_page": 3}, settings)), self.label) or {}
        ids = []
        for a in data.get("results") or []:
            i = str(a.get("id") or "").rsplit("/", 1)[-1]
            if re.fullmatch(r"A\d+", i):
                ids.append(i)
        return ids

    @staticmethod
    def _abstract(inv: Any) -> Optional[str]:
        if not isinstance(inv, dict) or not inv:
            return None
        slots: Dict[int, str] = {}
        for word, positions in inv.items():
            if isinstance(positions, list):
                for p in positions:
                    if isinstance(p, int) and 0 <= p < 20000:
                        slots[p] = str(word)
        return " ".join(slots[i] for i in sorted(slots)) or None

    def _record(self, w: Dict[str, Any], rank: int) -> Optional[Record]:
        loc = w.get("primary_location") or {}
        best = w.get("best_oa_location") or {}
        oa = w.get("open_access") or {}
        topic = w.get("primary_topic") or {}
        pdf = best.get("pdf_url") or (oa.get("oa_url") if str(oa.get("oa_url") or "").lower().endswith(".pdf") else None)
        doi = normalise_doi(w.get("doi") or (w.get("ids") or {}).get("doi"))
        rec = Record(title=w.get("display_name") or w.get("title") or "", authors=[(a.get("author") or {}).get("display_name") or a.get("raw_author_name") or "" for a in w.get("authorships") or [] if isinstance(a, dict)],
                     abstract=self._abstract(w.get("abstract_inverted_index")), published=w.get("publication_date") or w.get("publication_year"), venue=((loc.get("source") or {}).get("display_name")),
                     pub_type=normalise_type(w.get("type")), doi=doi, url=loc.get("landing_page_url") or (f"https://doi.org/{doi}" if doi else None), oa_url=pdf, license=best.get("license"),
                     fields=[x for x in ((topic.get("field") or {}).get("display_name"), (topic.get("subfield") or {}).get("display_name")) if x], keywords=[k.get("display_name") for k in (w.get("keywords") or []) if isinstance(k, dict) and k.get("display_name")][:10],
                     citation_count=w.get("cited_by_count"), citation_source="openalex", reference_count=w.get("referenced_works_count"),
                     providers={self.key: {"id": str(w.get("id") or "").rsplit("/", 1)[-1]}})
        return finish(rec)


# ---------------------------------------------------------------------------------------------------------------------------- arXiv
class Arxiv(Provider):
    key, label, host = "arxiv", "arXiv", "export.arxiv.org"
    min_interval_s = 3.0                                                                         # the arXiv API user manual asks for no more than one request every three seconds
    description = "Preprints in physics, mathematics, computer science and more. Always open full text (not peer-reviewed)."
    NS = {"a": "http://www.w3.org/2005/Atom", "x": "http://arxiv.org/schemas/atom", "o": "http://a9.com/-/spec/opensearch/1.1/"}

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:
        if q.pub_type and q.pub_type != "preprint":
            return ProviderResult(self.key, self.label, "skipped", message="arXiv only holds preprints.")
        params: Dict[str, Any] = {"start": 0, "max_results": max(1, min(q.limit, 100))}
        if q.doi:
            m = re.match(r"^10\.48550/arxiv\.(.+)$", normalise_doi(q.doi) or "", re.I)
            if not m:
                return ProviderResult(self.key, self.label, "skipped", message="Only arXiv DOIs (10.48550/arXiv.…) can be looked up on arXiv.")
            params = {"id_list": normalise_arxiv(m.group(1)) or m.group(1), "max_results": 1}
        else:
            parts: List[str] = []
            toks = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-']*", q.q)][:10]
            parts += [f"all:{t}" for t in toks]
            if q.title:
                parts.append('ti:"' + re.sub(r'["\\]', " ", q.title).strip() + '"')
            if q.author:
                parts.append('au:"' + re.sub(r'["\\]', " ", q.author).strip() + '"')
            if q.field and re.fullmatch(r"[A-Za-z\-]+(\.[A-Za-z\-]+)?", q.field.strip()):
                parts.append(f"cat:{q.field.strip()}")
            if q.year_from or q.year_to:
                lo, hi = q.year_from or 1991, q.year_to or 2200
                parts.append(f"submittedDate:[{lo}01010000 TO {hi}12312359]")
            if not parts:
                return ProviderResult(self.key, self.label, "skipped", message="Nothing to search for.")
            params["search_query"] = " AND ".join(parts)
            params["sortBy"] = {"date": "submittedDate", "citations": "relevance"}.get(q.sort, "relevance")
            params["sortOrder"] = "descending"
        res = self._fetch(fetcher, "https://export.arxiv.org/api/query?" + urlencode(params), accept=ATOM_T)
        raw = res.body.decode("utf-8", "replace")
        if re.search(r"<!DOCTYPE|<!ENTITY", raw, re.I):
            raise ProviderError("bad_answer", "arXiv answered in an unexpected format.")
        root = ET.fromstring(raw)
        total = root.findtext("o:totalResults", None, self.NS)
        recs: List[Record] = []
        for i, e in enumerate(root.findall("a:entry", self.NS)):
            r = self._record(e, i + 1)
            if r:
                recs.append(r)
        notes = ["arXiv papers are preprints and may not be peer-reviewed."] if recs else []
        if q.sort == "citations":
            notes.append("arXiv has no citation counts; results are in relevance order.")
        return ProviderResult(self.key, self.label, "ok", recs, total=int(total) if total and total.isdigit() else None, notes=notes)

    def _record(self, e: ET.Element, rank: int) -> Optional[Record]:
        ns = self.NS
        title = strip_markup(e.findtext("a:title", "", ns))
        if not title or title.lower() == "error":
            return None
        raw_id = e.findtext("a:id", "", ns)
        aid = normalise_arxiv(raw_id)
        cats = [c.get("term") for c in e.findall("a:category", ns) if c.get("term")]
        doi = e.findtext("x:doi", None, ns)
        rec = Record(title=title, authors=[strip_markup(a.findtext("a:name", "", ns)) for a in e.findall("a:author", ns)], abstract=e.findtext("a:summary", None, ns),
                     published=(e.findtext("a:published", "", ns) or "")[:10] or None, venue=(e.findtext("x:journal_ref", None, ns) or "arXiv"), pub_type="preprint",
                     doi=doi, arxiv_id=aid, url=f"https://arxiv.org/abs/{aid}" if aid else None, oa_url=f"https://arxiv.org/pdf/{aid}" if aid else None, fields=cats[:6],
                     providers={self.key: {"id": aid}})
        return finish(rec)


# ---------------------------------------------------------------------------------------------------------------------------- Semantic Scholar
class SemanticScholar(Provider):
    key, label, host = "semanticscholar", "Semantic Scholar", "api.semanticscholar.org"
    min_interval_s = 1.1
    description = "Academic graph with abstracts, citation counts and open-access PDF links. Works without a key at a shared, lower limit; a free key (PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY) gives a dedicated limit."
    FIELDS = "title,abstract,year,venue,publicationDate,externalIds,authors,citationCount,referenceCount,openAccessPdf,fieldsOfStudy,publicationTypes,url"
    TYPES = {"journal-article": "JournalArticle", "conference-paper": "Conference", "review": "Review", "book": "Book", "book-chapter": "BookSection", "dataset": "Dataset"}

    @staticmethod
    def _headers() -> Dict[str, str]:
        k = (os.environ.get("PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY") or "").strip()
        return {"x-api-key": k} if k else {}

    def _key_fix(self) -> str:
        return "Request a free key at https://www.semanticscholar.org/product/api and set PREDICTAI_RI_SEMANTIC_SCHOLAR_API_KEY, or check that the existing key is valid."

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:
        base = "https://api.semanticscholar.org/graph/v1"
        if q.doi:
            doi = normalise_doi(q.doi) or ""
            res = self._fetch(fetcher, f"{base}/paper/DOI:{quote(doi, safe='/')}?{urlencode({'fields': self.FIELDS})}", headers=self._headers())
            rec = self._record(self._json(res, self.label) or {}, 1)
            return ProviderResult(self.key, self.label, "ok", [rec] if rec else [], total=1 if rec else 0)
        text = q.text()
        if not text:
            return ProviderResult(self.key, self.label, "skipped", message="Semantic Scholar paper search needs search words or a title; it cannot search by author alone.")
        params: Dict[str, Any] = {"query": text, "limit": max(1, min(q.limit, 100)), "fields": self.FIELDS}
        if q.year_from and q.year_to:
            params["year"] = str(q.year_from) if q.year_from == q.year_to else f"{q.year_from}-{q.year_to}"
        elif q.year_from:
            params["year"] = f"{q.year_from}-"
        elif q.year_to:
            params["year"] = f"-{q.year_to}"
        if q.pub_type in self.TYPES:
            params["publicationTypes"] = self.TYPES[q.pub_type]
        if q.field:
            params["fieldsOfStudy"] = q.field
        url = f"{base}/paper/search?{urlencode(params)}" + ("&openAccessPdf" if q.open_access else "")
        data = self._json(self._fetch(fetcher, url, headers=self._headers()), self.label) or {}
        recs = [r for r in (self._record(p, i + 1) for i, p in enumerate(data.get("data") or [])) if r]
        notes = ["Semantic Scholar paper search cannot filter by author; the author filter is applied to its results afterwards."] if q.author else []
        return ProviderResult(self.key, self.label, "ok", recs, total=data.get("total") if isinstance(data.get("total"), int) else None, notes=notes)

    def _record(self, p: Dict[str, Any], rank: int) -> Optional[Record]:
        ext = p.get("externalIds") or {}
        oa = p.get("openAccessPdf") or {}
        types = p.get("publicationTypes") or []
        ptype = next((normalise_type(t) for t in types if normalise_type(t) != "other"), "other")
        rec = Record(title=p.get("title") or "", authors=[a.get("name", "") for a in p.get("authors") or [] if isinstance(a, dict)], abstract=p.get("abstract"), published=p.get("publicationDate") or p.get("year"),
                     year=p.get("year"), venue=p.get("venue") or None, pub_type=ptype, doi=ext.get("DOI"), arxiv_id=ext.get("ArXiv"), url=p.get("url"), oa_url=oa.get("url"), license=oa.get("license"),
                     fields=list(p.get("fieldsOfStudy") or []), citation_count=p.get("citationCount"), citation_source="semanticscholar", reference_count=p.get("referenceCount"),
                     providers={self.key: {"id": p.get("paperId")}})
        return finish(rec)


# ---------------------------------------------------------------------------------------------------------------------------- SearXNG (web)
class Searxng(Provider):
    """General web results through the SearXNG instance the platform owner runs (the existing Phase 6 provider is reused unchanged). Results are web pages, not papers."""
    key, label, host = "searxng", "Web (SearXNG)", "your SearXNG instance"
    min_interval_s = 0.5
    description = "General web search through your own SearXNG instance. Web pages, not peer-reviewed papers. Needs PREDICTAI_SEARXNG_URL."

    def __init__(self, fetcher_factory: Optional[Callable[[Any], Any]] = None) -> None:
        self._factory = fetcher_factory

    def available(self, settings: Any) -> Tuple[bool, str]:
        if not getattr(settings, "searxng_url", ""):
            return False, "The platform owner has not set PREDICTAI_SEARXNG_URL, so web search is off. docker/searxng starts a ready-made instance."
        return True, ""

    def search(self, fetcher: Any, q: Query, settings: Any) -> ProviderResult:
        from ..modules_bridge import phase6_searxng                                              # lazy: Phase 6 stays untouched and optional
        text = " ".join(x for x in (q.q, q.title, q.author) if x).strip()
        if not text:
            return ProviderResult(self.key, self.label, "skipped", message="Web search needs search words.")
        provider, web_fetcher = phase6_searxng(settings, self._factory)
        try:
            hits = provider.search(web_fetcher, settings, text, q.limit)
        except Exception as e:                                                                    # noqa: BLE001 - Phase 6 raises its own FetchError with an exact, already-explained message
            code = getattr(e, "code", "")
            msg = getattr(e, "message", None) or "The SearXNG instance could not be used."
            status = {"RATE_LIMITED": "rate_limited", "PROVIDER_LIMITER": "rate_limited", "CONNECT_FAILED": "unreachable", "TIMEOUT": "unreachable"}.get(code, "unavailable")
            raise ProviderError(status, str(msg)) from None
        recs: List[Record] = []
        for h in hits:
            rec = finish(Record(title=h.title, abstract=h.snippet or None, published=h.published, url=h.url, kind="web", pub_type="web-page", venue=None, providers={self.key: {"id": h.url}}))
            if rec:
                doi = normalise_doi(h.url)
                rec.doi = doi or None
                recs.append(rec)
        return ProviderResult(self.key, self.label, "ok", recs, total=len(recs), notes=["Web results are not peer-reviewed papers; check each source."] if recs else [])


def default_providers(searx_fetcher_factory: Optional[Callable[[Any], Any]] = None) -> Dict[str, Provider]:
    ps: List[Provider] = [Crossref(), OpenAlex(), Arxiv(), SemanticScholar(), Searxng(searx_fetcher_factory)]
    return {p.key: p for p in ps}
