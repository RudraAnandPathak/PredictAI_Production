"""arXiv through its official API (https://export.arxiv.org/api/query, Atom 1.0) plus the paper's own HTML or PDF for the full text.

  search    field-aware queries: `ti:` `au:` `abs:` `cat:` `all:` with AND / OR / ANDNOT, quoted phrases, or an arXiv identifier / URL (looked up with `id_list`, which is how the API
            documents getting a specific version). Plain-language text is turned into phrase queries first and loosened step by step (see webaccess.queries); a query arXiv rejects
            is reported with arXiv's own message, never turned into 'no results'.
  load      the whole paper: metadata (all authors, categories, DOI, journal reference, comment, submitted and latest-revision dates, version list), then full text from arXiv's HTML
            version when it exists (equations arrive as TeX, tables as rows), otherwise from the PDF (sections found by heading; formulas kept as written and marked approximate).
            If neither can be read the result says so and falls back to the abstract, labelled as the abstract: an abstract is never presented as the paper.

Every entry is a PREPRINT. arXiv does not peer-review, and PredictAI does not check whether a paper was later published: when the authors list a journal reference it is shown as
their claim, not as verified peer review. Terms of use (https://info.arxiv.org/help/api/tou.html): one request at a time and one every three seconds, through the shared gate in
`webaccess.gate`; a refusal pauses all arXiv requests for a while; full text may be used for research but is not re-served (PredictAI keeps the extracted text in the person's own
research record, never the PDF file). Requests are for this person's question only; this project is not endorsed or supported by arXiv."""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlencode, urlsplit

from ..fetcher import FetchError
from ..gate import GATES
from ..htmltext import clean_text, decode_body, extract_html
from ..papers import paper_from_html, paper_from_pdf
from ..queries import arxiv_id_in, arxiv_queries, topic_focus
from .base import SearchHit, SearchProvider, diagnosis, explain_failure

NS = {"a": "http://www.w3.org/2005/Atom", "ar": "http://arxiv.org/schemas/atom", "os": "http://a9.com/-/spec/opensearch/1.1/"}
ATOM_TYPES = ("application/atom+xml", "application/xml", "text/xml")
PREFIXES = ("ti", "au", "abs", "co", "jr", "cat", "rn", "all", "id")
SYNTAX = re.compile(r"\b(?:ti|au|abs|co|jr|cat|rn|all|id):")
SAFE_QUERY = re.compile(r"^[A-Za-z0-9 :\"()\-_.+*/,'&]{1,400}$")
ID_FORM = re.compile(r"^(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?$")
ABS_ID = re.compile(r"^https?://arxiv\.org/abs/(?P<id>(?:\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}))(?:v(?P<v>\d+))?$")
CATEGORY = re.compile(r"^[a-z\-]+(?:\.[A-Za-z\-]+)?$")


def split_version(arxiv_id: str) -> Tuple[str, Optional[int]]:
    m = re.match(r"^(?P<base>.+?)(?:v(?P<v>\d+))?$", arxiv_id)
    return (m.group("base"), int(m.group("v")) if m.group("v") else None) if m else (arxiv_id, None)


def check_syntax(q: str) -> str:
    """A search_query we are willing to send: allowed characters only, balanced quotes and brackets, known field prefixes, upper-case operators."""
    s = " ".join((q or "").split())
    if not SAFE_QUERY.match(s) or s.count('"') % 2 or s.count("(") != s.count(")"):
        raise FetchError("PROVIDER_ERROR", "That arXiv search could not be sent: it uses characters or brackets arXiv's query language does not accept.")
    for m in re.finditer(r"\b([A-Za-z]{1,4}):", s):
        if m.group(1).lower() not in PREFIXES or m.group(1) != m.group(1).lower():
            raise FetchError("PROVIDER_ERROR", f"'{m.group(1)}:' is not an arXiv search field. Use ti, au, abs, cat, co, jr or all.")
    return s


def build_query(mode: str, text: str, category: str = "") -> str:
    """The arXiv `search_query` for the independent search box: mode is keywords | title | author | abstract | category."""
    t = " ".join((text or "").replace('"', " ").split())
    cat = category.strip()
    if cat and not CATEGORY.match(cat):
        raise FetchError("PROVIDER_ERROR", "An arXiv category looks like cs.LG, stat.ME or math.PR.")
    if mode == "title":
        core = f'ti:"{t}"' if " " in t else f"ti:{t}"
    elif mode == "author":
        core = f'au:"{t}"' if " " in t else f"au:{t}"
    elif mode == "abstract":
        core = f'abs:"{t}"' if " " in t else f"abs:{t}"
    elif mode == "category":
        if not t and not cat:
            raise FetchError("PROVIDER_ERROR", "Give an arXiv category such as cs.LG.")
        core = f"cat:{cat or t}" if not (cat and t) else ""
    else:
        qs = arxiv_queries(topic_focus(t))
        core = qs[0]["query"] if qs else ""
    if cat and mode != "category":
        core = f"({core}) AND cat:{cat}" if core else f"cat:{cat}"
    elif mode == "category" and cat and t:
        qs = arxiv_queries(topic_focus(t))
        core = f"cat:{cat}" + (f" AND ({qs[0]['query']})" if qs else "")
    if not core:
        raise FetchError("PROVIDER_ERROR", "Type something to search for.")
    return check_syntax(core)


def peer_review(extra: Dict[str, Any]) -> Tuple[str, str]:
    """(status, plain words). arXiv entries are preprints; a journal reference is the authors' claim, which PredictAI does not verify."""
    if extra.get("journal_ref") or extra.get("doi"):
        where = extra.get("journal_ref") or f"DOI {extra.get('doi')}"
        return "published_claim", f"Preprint. The authors list a later publication ({where}); PredictAI has not verified that it was peer reviewed or that it matches this version."
    return "preprint", "Preprint: posted on arXiv without peer review."


class Arxiv(SearchProvider):
    key = "arxiv"
    label = "arXiv (preprints)"
    description = ("Free scientific preprints (physics, mathematics, computer science, statistics, biology), read through arXiv's official API; full text from the paper's HTML or PDF. "
                   "Preprints are not peer reviewed. Not endorsed or supported by arXiv.")
    host = "export.arxiv.org"
    hosts = ("export.arxiv.org", "arxiv.org")
    min_interval_s = 0.0                        # spacing is enforced by the shared gate (3 s, one request at a time), not per fetcher
    source_type = "preprint"
    tier = "preprint"
    reads_documents = True
    gate_key = "arxiv"
    API = "https://export.arxiv.org/api/query"

    # ------------------------------------------------------------------ one request, through the gate
    def _get(self, fetcher: Any, url: str, *, accept: Tuple[str, ...], max_bytes: int, robots: bool):
        for g in GATES.values():
            g.check()                                                                      # a refusal anywhere pauses every arXiv request
        gate = GATES["arxiv-web"] if urlsplit(url).hostname == "arxiv.org" else GATES[self.gate_key]
        with gate:
            try:
                return fetcher.fetch(url, accept=accept, robots=robots, max_bytes=max_bytes, retries=1)
            except FetchError as e:
                if e.code == "BLOCKED" or e.status == 403:
                    for g in GATES.values():
                        g.trip(900.0)                                                      # arXiv: continued requests after a refusal are treated as an attack
                    raise FetchError("PROVIDER_REFUSED", "arXiv refused the request. Requests are paused for a few minutes; open the paper from its link meanwhile.", status=e.status) from None
                raise

    # ------------------------------------------------------------------ search
    def search(self, fetcher: Any, settings: Any, query: str, limit: int) -> List[SearchHit]:
        limit = max(1, min(int(limit), 10))
        ident = arxiv_id_in(query)
        if ident and ID_FORM.match(ident) and len(query.strip()) <= len(ident) + 40:
            return self.lookup(fetcher, [ident])[:limit]
        if SYNTAX.search(query):
            variants = [check_syntax(query)]
        else:
            focus = topic_focus(query)
            variants = [v["query"] for v in arxiv_queries(focus)]                              # nothing searchable (only filler words): nothing is sent
        for v in variants:
            hits, _ = self._query(fetcher, search_query=v, limit=limit)
            if hits:
                return hits
        return []

    def lookup(self, fetcher: Any, ids: List[str]) -> List[SearchHit]:
        clean = [i for i in dict.fromkeys(ids) if ID_FORM.match(i)][:20]
        if not clean:
            return []
        hits, _ = self._query(fetcher, id_list=",".join(clean), limit=len(clean))
        return hits

    def _query(self, fetcher: Any, *, search_query: Optional[str] = None, id_list: Optional[str] = None, limit: int = 10, sort: str = "relevance") -> Tuple[List[SearchHit], Optional[int]]:
        params: Dict[str, Any] = {"start": 0, "max_results": limit}
        if search_query:
            params.update({"search_query": search_query, "sortBy": sort, "sortOrder": "descending"})
        if id_list:
            params["id_list"] = id_list
        result = self._get(fetcher, f"{self.API}?{urlencode(params)}", accept=ATOM_TYPES, max_bytes=1_500_000, robots=False)
        return self.parse_feed(result.body, limit)

    # ------------------------------------------------------------------ the Atom feed
    def parse(self, body: bytes, limit: int) -> List[SearchHit]:
        return self.parse_feed(body, limit)[0]

    def parse_feed(self, body: bytes, limit: int) -> Tuple[List[SearchHit], Optional[int]]:
        if re.search(rb"<!\s*(DOCTYPE|ENTITY)", body[:65536], re.I):
            raise FetchError("PROVIDER_BAD_ANSWER", "arXiv answered in a form that is refused for safety.")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            raise FetchError("PROVIDER_BAD_ANSWER", "arXiv answered in a form that could not be read.") from None
        if not root.tag.endswith("feed"):
            raise FetchError("PROVIDER_BAD_ANSWER", "arXiv answered in a form that could not be read.")
        total: Optional[int] = None
        node = root.find("os:totalResults", NS)
        if node is not None and (node.text or "").strip().isdigit():
            total = int(node.text.strip())
        hits: List[SearchHit] = []
        for entry in root.findall("a:entry", NS):
            ident = clean_text(entry.findtext("a:id", default="", namespaces=NS) or "")
            if "/api/errors" in ident:                                                       # arXiv's way of reporting a bad query: say what it said
                message = clean_text(entry.findtext("a:summary", default="", namespaces=NS) or "")[:160]
                raise FetchError("PROVIDER_ERROR", f"arXiv rejected the search: {re.sub(r'[^ -~]', '', message) or 'invalid query'}.")
            hit = self._entry(entry, ident, len(hits) + 1)
            if hit is not None:
                hits.append(hit)
            if len(hits) >= limit:
                break
        return hits, total

    def _entry(self, entry: ET.Element, ident: str, rank: int) -> Optional[SearchHit]:
        def txt(path: str) -> str:
            node = entry.find(path, NS)
            return clean_text(node.text or "") if node is not None and node.text else ""
        m = ABS_ID.match(ident)
        title, summary = txt("a:title"), txt("a:summary")
        if not m or not title:
            return None                                                                       # anything that is not a real arXiv abstract URL is never turned into a hit
        arxiv_id, version = m.group("id"), int(m.group("v")) if m.group("v") else None
        authors = [clean_text(n.text or "") for n in entry.findall("a:author/a:name", NS) if n.text]
        affil = [clean_text(n.text or "") for n in entry.findall("a:author/ar:affiliation", NS) if n.text]
        cats = [c.get("term") for c in entry.findall("a:category", NS) if c.get("term")]
        primary = entry.find("ar:primary_category", NS)
        pdf = next((l.get("href") for l in entry.findall("a:link", NS) if l.get("title") == "pdf" and l.get("href")), None)
        doi_link = next((l.get("href") for l in entry.findall("a:link", NS) if l.get("title") == "doi" and l.get("href")), None)
        vtag = f"v{version}" if version else ""
        extra: Dict[str, Any] = {"arxiv_id": arxiv_id, "version": version, "is_latest": True if version else None, "authors": authors[:40], "affiliations": affil[:20],
                                 "categories": cats[:12], "primary_category": primary.get("term") if primary is not None else (cats[0] if cats else None), "doi": txt("ar:doi") or None,
                                 "doi_url": doi_link, "journal_ref": txt("ar:journal_ref") or None, "comment": txt("ar:comment")[:300] or None, "preprint": True, "tier": self.tier,
                                 "pdf_url": f"https://arxiv.org/pdf/{arxiv_id}{vtag}", "html_url": f"https://arxiv.org/html/{arxiv_id}{vtag}", "abstract_url": f"https://arxiv.org/abs/{arxiv_id}{vtag}",
                                 "content": "abstract", "first_submitted": txt("a:published") or None, "latest_revision": txt("a:updated") or None}
        if pdf and pdf.startswith("http") and "arxiv.org" in pdf:
            extra["pdf_url"] = pdf.replace("http://", "https://", 1)
        extra["peer_review"], extra["peer_review_note"] = peer_review(extra)
        # kept for older callers and the evidence labels
        extra["category"] = extra["primary_category"]
        return SearchHit(title=title, url=f"https://arxiv.org/abs/{arxiv_id}{vtag}", snippet=summary[:300], provider=self.key, rank=rank, published=txt("a:published") or None,
                         modified=txt("a:updated") or None, text=f"{title}. {summary}"[:6000], author=", ".join(authors[:6]) + (" et al." if len(authors) > 6 else "") or None, publisher="arXiv",
                         source_type=self.source_type, license=None, extra=extra)

    # ------------------------------------------------------------------ the whole paper
    def load(self, fetcher: Any, settings: Any, ref: Dict[str, Any]) -> Dict[str, Any]:
        """`ref` = {"arxiv_id": "2101.00001"} (a version suffix pins that version) or {"url": ...}. Full text from HTML, else PDF, else the abstract (labelled as such)."""
        raw = str(ref.get("arxiv_id") or ref.get("url") or "")
        if "/" in raw and "://" in raw and (urlsplit(raw).hostname or "") not in ("arxiv.org", "www.arxiv.org", "export.arxiv.org"):
            raise FetchError("PROVIDER_ERROR", "That is not an arXiv link. Use something like 2101.00001 or https://arxiv.org/abs/2101.00001v2.")
        wanted = arxiv_id_in(raw)
        if not wanted or not ID_FORM.match(wanted):
            raise FetchError("PROVIDER_ERROR", "That is not an arXiv identifier. Use something like 2101.00001 or https://arxiv.org/abs/2101.00001v2.")
        base, pinned = split_version(wanted)
        latest = self.lookup(fetcher, [base])
        if not latest:
            raise FetchError("NOT_FOUND", "arXiv has no paper with that identifier.")
        head = latest[0]
        latest_version = head.extra.get("version") or 1
        use = pinned or latest_version
        meta = head
        if pinned and pinned != latest_version:
            older = self.lookup(fetcher, [f"{base}v{pinned}"])
            if older:
                meta = older[0]
        extra = dict(meta.extra)
        extra.update(version=use, latest_version=latest_version, is_latest=use == latest_version)
        abstract = (meta.text or "")[len(meta.title) + 2:] if meta.text else ""
        versions = self._versions(fetcher, base, latest_version, head.extra, bool(ref.get("history")))
        warnings: List[str] = []
        if use != latest_version:
            warnings.append("NOT_LATEST_VERSION")
        structure: Optional[Dict[str, Any]] = None
        via = "arxiv-abstract"
        for route in ("html", "pdf"):
            try:
                structure = self._full_text(fetcher, settings, route, base, use, abstract, meta.title)
                via = f"arxiv-{route}"
                break
            except FetchError as e:
                warnings.append(f"{route.upper()}_{e.code}")
                if e.code in ("PROVIDER_REFUSED", "PROVIDER_COOLDOWN"):
                    break
        if structure is None:
            warnings.append("FULL_TEXT_UNAVAILABLE")
            structure = abstract_only(meta.title, abstract)
        structure["warnings"] = list(dict.fromkeys([*(structure.get("warnings") or []), *warnings]))
        vtag = f"v{use}"
        return {"provider": self.key, "kind": "paper", "ref": {"arxiv_id": base, "version": use}, "title": meta.title, "url": f"https://arxiv.org/abs/{base}{vtag}",
                "pdf_url": f"https://arxiv.org/pdf/{base}{vtag}", "html_url": f"https://arxiv.org/html/{base}{vtag}", "permalink": f"https://arxiv.org/abs/{base}{vtag}",
                "published": meta.published, "modified": meta.modified, "authors": extra.get("authors", []), "publisher": "arXiv", "license": None, "tier": self.tier,
                "attribution": f"{', '.join(extra.get('authors', [])[:6])}{' et al.' if len(extra.get('authors', [])) > 6 else ''}. \"{meta.title}\". arXiv:{base}{vtag}. "
                               "Retrieved from arXiv.org (not endorsed by arXiv).",
                "structure": structure, "meta": {k: v for k, v in extra.items() if k not in ("content",)}, "versions": versions, "retrieved_via": via,
                "full_text": via in ("arxiv-html", "arxiv-pdf"), "warnings": structure["warnings"]}

    def _full_text(self, fetcher: Any, settings: Any, route: str, base: str, version: int, abstract: str, title: str) -> Dict[str, Any]:
        if route == "html":
            r = self._get(fetcher, f"https://arxiv.org/html/{base}v{version}", accept=("text/html", "application/xhtml+xml"), max_bytes=6_000_000, robots=True)
            return paper_from_html(decode_body(r.body, r.content_type), abstract=abstract)
        r = self._get(fetcher, f"https://export.arxiv.org/pdf/{base}v{version}", accept=("application/pdf",), max_bytes=25_000_000, robots=True)
        if not r.body.startswith(b"%PDF"):
            raise FetchError("PDF_UNREADABLE", "arXiv did not return a PDF.")
        return paper_from_pdf(r.body, ocr_enabled=bool(getattr(settings, "ocr_enabled", False)), ocr_languages=str(getattr(settings, "ocr_languages", "eng")), abstract=abstract, title=title)

    def _versions(self, fetcher: Any, base: str, latest: int, meta: Dict[str, Any], history: bool) -> List[Dict[str, Any]]:
        """What is known about the versions. The API reports the first-submission and latest-revision dates and the latest version number, which is enough to mark the latest and
        to date the first and last versions; intermediate versions need the abstract page's submission history, which is one more request to arxiv.org (15 s crawl delay), so it is
        read only when asked for (`history`)."""
        first, last = meta.get("first_submitted"), meta.get("latest_revision")
        out: List[Dict[str, Any]] = [{"version": 1, "submitted": first, "latest": latest == 1}] if latest == 1 else [
            {"version": 1, "submitted": first, "latest": False}, {"version": latest, "submitted": last, "latest": True}]
        if latest > 2:
            out[0]["partial"] = True                                                       # versions 2 .. latest-1 exist but are not listed
        if not history or latest == 1:
            return out
        try:
            r = self._get(fetcher, f"https://arxiv.org/abs/{base}", accept=("text/html",), max_bytes=1_500_000, robots=True)
            text = extract_html(decode_body(r.body, r.content_type))["text"]
        except FetchError:
            return out
        found = {int(m.group(1)): m.group(2) for m in re.finditer(r"\[v(\d+)\]\s*([A-Z][a-z]{2},\s+\d{1,2}\s+[A-Z][a-z]{2}\s+\d{4}\s+\d{2}:\d{2}:\d{2}\s+UTC)", text)}
        if not found:
            return out
        return [{"version": v, "submitted": found[v], "latest": v == max(found)} for v in sorted(found)]

    # ------------------------------------------------------------------ self-test
    def diagnose(self, fetcher: Any, settings: Any) -> Dict[str, Any]:
        try:
            hits, total = self._query(fetcher, search_query="all:electron", limit=1)
        except FetchError as e:
            return explain_failure(self.key, "arXiv", e)
        if not hits:
            return diagnosis(self.key, "bad_answer", "arXiv answered, but the test search returned no paper.", "Try again later.")
        return diagnosis(self.key, "ok", "arXiv answered through its official API.", "", total_results=total)


def abstract_only(title: str, abstract: str) -> Dict[str, Any]:
    sec = {"id": "s1", "heading": "Abstract", "level": 1, "path": ["Abstract"], "anchor": None, "number": None, "canonical": "abstract", "paragraphs": [abstract] if abstract else [], "lists": [],
           "tables": [], "equations": [], "text": abstract, "page_start": 1, "page_end": 1}
    lead = {"id": "s0", "heading": None, "level": 0, "path": [], "anchor": None, "number": None, "paragraphs": [title], "lists": [], "tables": [], "equations": [], "text": title}
    return {"title": title, "sections": [lead, sec] if abstract else [lead], "references": [], "warnings": ["ABSTRACT_ONLY"], "dropped": 0}
