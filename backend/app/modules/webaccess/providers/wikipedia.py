"""Wikipedia through the official MediaWiki Action API (https://en.wikipedia.org/w/api.php): free, no key, no scraping.

  search   list=search (article namespace only, spelling suggestion, rewritten query) then one batched prop=extracts|info|pageprops|categories|revisions request for the lead text,
           canonical URL, short description, categories, disambiguation flag and last-revision time. Disambiguation pages are never returned as answers: their best-matching
           links are followed instead. Duplicate pages (the same page id or title) are removed.
  exact    the page whose TITLE is the given name (redirects resolved), looked up with prop=...&titles=...&redirects=1. A full-text search ranks pages that merely contain the words;
           naming the entity ("Louis XVI") and asking for that title is how the right person is chosen over the one with a similar name ("Louis XVIII").
  load     action=parse (redirects resolved) for the whole article: sections with their headings, paragraphs, lists, tables, equations, categories and references.
           A long article is never read whole: `structure.select_sections` picks the sections that match the question.

Wikipedia is a TERTIARY source (a community-written summary of other sources). Text is CC BY-SA 4.0: every use keeps the title, the revision-specific link and the licence.
Etiquette (https://www.mediawiki.org/wiki/API:Etiquette): an identifying User-Agent (set in `build_fetcher`), one request at a time, Retry-After honoured by the fetcher; results are
cached by the caller. Failures are reported with a code, never swallowed or replaced by invented text."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import quote, urlencode

from ...textkit import tokenize
from ..fetcher import JSON_TYPES, FetchError
from ..htmltext import strip_tags
from ..structure import parse_article
from .base import SearchHit, SearchProvider, diagnosis, explain_failure

SITE = "https://en.wikipedia.org"
API = f"{SITE}/w/api.php"
RETRYABLE_API_ERRORS = ("maxlag", "ratelimited", "readonly", "internal_api_error", "urlshortener-ratelimit")
LIST_PAGE = re.compile(r"^(list|lists|index|outline|glossary|timeline) of\b", re.I)
_SAFE_TITLE = "_()',:!*~-."


def page_url(title: str, anchor: Optional[str] = None) -> str:
    url = f"{SITE}/wiki/{quote(title.replace(' ', '_'), safe=_SAFE_TITLE)}"
    return url + (f"#{quote(anchor, safe='_()-.,:!*~')}" if anchor else "")


def permalink(title: str, revid: Any) -> str:
    return f"{SITE}/w/index.php?title={quote(title.replace(' ', '_'), safe=_SAFE_TITLE)}&oldid={int(revid)}" if revid else page_url(title)


def attribution(title: str, revid: Any, url: str) -> str:
    rev = f", revision {int(revid)}" if revid else ""
    return f"Wikipedia contributors, \"{title}\", Wikipedia, The Free Encyclopedia{rev}, {url}. Text available under CC BY-SA 4.0."


class Wikipedia(SearchProvider):
    key = "wikipedia"
    label = "Wikipedia (English)"
    description = "Free encyclopedia articles, read through the official MediaWiki API. A tertiary source: good for background and definitions; confirm important claims in primary sources."
    host = "en.wikipedia.org"
    hosts = ("en.wikipedia.org",)
    min_interval_s = 0.3
    source_type = "encyclopedia"
    tier = "tertiary"
    reads_documents = True
    supports_exact = True

    # ------------------------------------------------------------------ the API call, with every failure mode named
    def _api(self, fetcher: Any, params: Dict[str, Any], *, max_bytes: int = 700_000) -> Dict[str, Any]:
        query = {"format": "json", "formatversion": 2, "utf8": 1, **params}
        try:
            result = fetcher.fetch(f"{API}?{urlencode(query)}", accept=JSON_TYPES, robots=False, max_bytes=max_bytes)
        except FetchError as e:
            if e.code == "BLOCKED":
                raise FetchError("PROVIDER_REFUSED", "Wikipedia refused the request. Wikimedia asks every client to identify itself with contact details (PREDICTAI_RESEARCH_CONTACT).",
                                 status=e.status) from None
            if e.code == "WRONG_TYPE":
                raise FetchError("PROVIDER_BAD_ANSWER", "Wikipedia answered with something other than data.") from None
            raise
        try:
            data = json.loads(result.body.decode("utf-8", "replace"))
        except ValueError:
            raise FetchError("PROVIDER_BAD_ANSWER", "Wikipedia answered in a form that could not be read.") from None
        if not isinstance(data, dict):
            raise FetchError("PROVIDER_BAD_ANSWER", "Wikipedia answered in a form that could not be read.")
        err = data.get("error")
        if err:
            code = str(err.get("code", "")) if isinstance(err, dict) else ""
            safe = re.sub(r"[^A-Za-z0-9_\-]", "", code)[:40]
            if code in ("missingtitle", "nosuchpageid", "invalidtitle"):
                raise FetchError("NOT_FOUND", "That Wikipedia page does not exist.")
            if code.startswith(RETRYABLE_API_ERRORS):
                raise FetchError("RATE_LIMITED", "Wikipedia is busy right now; try again shortly.", retryable=True)
            raise FetchError("PROVIDER_ERROR", f"The Wikipedia API reported an error ({safe or 'unknown'}).")
        return data

    # ------------------------------------------------------------------ search
    def search(self, fetcher: Any, settings: Any, query: str, limit: int, *, exact: bool = False) -> List[SearchHit]:
        limit = max(1, min(int(limit), 10))
        if exact:
            return self.lookup_titles(fetcher, [query])[:limit]
        found, info = self._search_pages(fetcher, query, limit * 2 + 2)
        corrected_from = None
        if not found and info.get("suggestion"):
            corrected_from = query
            query = str(info["suggestion"])
            found, info = self._search_pages(fetcher, query, limit * 2 + 2)
        if not found:
            return []
        details = self._details(fetcher, [str(p["pageid"]) for p in found])
        hits: List[SearchHit] = []
        seen_ids: set = set()
        seen_titles: set = set()
        ambiguous: List[str] = []

        def add(item: Dict[str, Any], page: Dict[str, Any], resolved_from: Optional[str] = None) -> None:
            title = str(page.get("title") or item.get("title") or "")
            pid = str(page.get("pageid") or item.get("pageid") or "")
            if not title or not pid or pid in seen_ids or title.lower() in seen_titles:
                return
            seen_ids.add(pid)
            seen_titles.add(title.lower())
            props = page.get("pageprops") or {}
            revs = page.get("revisions") or []
            rev = revs[0] if revs and isinstance(revs[0], dict) else {}
            cats = [re.sub(r"^Category:", "", str(c.get("title", ""))) for c in (page.get("categories") or []) if isinstance(c, dict)]
            url = str(page.get("canonicalurl") or page.get("fullurl") or page_url(title))
            extract = page.get("extract") if isinstance(page.get("extract"), str) else ""
            extra: Dict[str, Any] = {"pageid": int(pid), "revid": rev.get("revid") or page.get("lastrevid"), "description": props.get("wikibase-shortdesc"), "categories": cats[:12],
                                     "wordcount": item.get("wordcount"), "tier": self.tier, "list_page": bool(LIST_PAGE.match(title)), "content": "lead"}
            if corrected_from:
                extra["corrected_from"] = corrected_from
            if resolved_from:
                extra["resolved_from_disambiguation"] = resolved_from
            hits.append(SearchHit(title=title, url=url, snippet=strip_tags(str(item.get("snippet") or ""))[:500] or (extract[:300] if extract else ""), provider=self.key,
                                  rank=len(hits) + 1, modified=rev.get("timestamp") or item.get("timestamp"), text=extract.strip()[:6000] or None, publisher="Wikipedia",
                                  source_type=self.source_type, license="CC BY-SA 4.0", extra=extra))

        for item in found:
            page = details.get(str(item["pageid"]), {})
            if "disambiguation" in (page.get("pageprops") or {}):
                ambiguous.append(str(item["title"]))
                continue
            if page.get("missing"):
                continue
            add(item, page)
            if len(hits) >= limit:
                break
        if len(hits) < limit and ambiguous:
            for title in ambiguous[:2]:
                for item, page in self._disambiguation_candidates(fetcher, title, query, limit - len(hits)):
                    add(item, page, resolved_from=title)
                    if len(hits) >= limit:
                        break
        for i, h in enumerate(hits[:limit], start=1):
            h.rank = i
        return hits[:limit]

    def lookup_titles(self, fetcher: Any, titles: List[str]) -> List[SearchHit]:
        """The articles whose titles are exactly `titles` (a redirect is followed to its target; a missing title and a disambiguation page give nothing, never a guess)."""
        wanted = [re.sub(r"\s+", " ", t).strip() for t in titles if t and t.strip()][:8]
        if not wanted:
            return []
        data = self._api(fetcher, {**self.DETAIL_PROPS, "titles": "|".join(wanted), "exlimit": len(wanted)})
        q = data.get("query") if isinstance(data.get("query"), dict) else {}
        redirects = {str(r.get("to")): str(r.get("from")) for r in (q.get("redirects") or []) if isinstance(r, dict)}
        hits: List[SearchHit] = []
        for page in q.get("pages") or []:
            if not isinstance(page, dict) or page.get("missing") or page.get("invalid") or not page.get("pageid") or "disambiguation" in (page.get("pageprops") or {}):
                continue
            title = str(page.get("title") or "")
            props = page.get("pageprops") or {}
            revs = page.get("revisions") or []
            rev = revs[0] if revs and isinstance(revs[0], dict) else {}
            cats = [re.sub(r"^Category:", "", str(c.get("title", ""))) for c in (page.get("categories") or []) if isinstance(c, dict)]
            extract = page.get("extract") if isinstance(page.get("extract"), str) else ""
            url = str(page.get("canonicalurl") or page.get("fullurl") or page_url(title))
            extra: Dict[str, Any] = {"pageid": int(page["pageid"]), "revid": rev.get("revid") or page.get("lastrevid"), "description": props.get("wikibase-shortdesc"), "categories": cats[:12],
                                     "tier": self.tier, "list_page": bool(LIST_PAGE.match(title)), "content": "lead", "exact_title": True}
            if title in redirects:
                extra["redirected_from"] = redirects[title]
            hits.append(SearchHit(title=title, url=url, snippet=extract[:300] if extract else "", provider=self.key, rank=len(hits) + 1, modified=rev.get("timestamp"), text=extract.strip()[:6000] or None,
                                  publisher="Wikipedia", source_type=self.source_type, license="CC BY-SA 4.0", extra=extra))
        return hits

    def _search_pages(self, fetcher: Any, query: str, limit: int) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
        data = self._api(fetcher, {"action": "query", "list": "search", "srsearch": query, "srlimit": min(limit, 20), "srnamespace": 0, "srprop": "snippet|timestamp|wordcount",
                                   "srinfo": "suggestion|rewrittenquery"})
        q = data.get("query") if isinstance(data.get("query"), dict) else {}
        pages = [p for p in (q.get("search") or []) if isinstance(p, dict) and p.get("title") and p.get("pageid")]
        info = q.get("searchinfo") if isinstance(q.get("searchinfo"), dict) else {}
        return pages, info

    DETAIL_PROPS = {"action": "query", "prop": "extracts|info|pageprops|categories|revisions", "exintro": 1, "explaintext": 1, "inprop": "url", "ppprop": "disambiguation|wikibase-shortdesc",
                    "cllimit": "max", "clshow": "!hidden", "rvprop": "ids|timestamp", "redirects": 1}

    def _details(self, fetcher: Any, page_ids: List[str]) -> Dict[str, Dict[str, Any]]:
        """Lead text, URL, description, categories, disambiguation flag and last revision for up to 20 pages in ONE request. A failure here still leaves usable snippet hits."""
        ids = list(dict.fromkeys(page_ids))[:20]
        if not ids:
            return {}
        try:
            data = self._api(fetcher, {**self.DETAIL_PROPS, "pageids": "|".join(ids), "exlimit": len(ids)})
        except FetchError as e:
            if e.code == "PROVIDER_REFUSED":
                raise
            return {}
        pages = (data.get("query") or {}).get("pages") if isinstance(data.get("query"), dict) else None
        return {str(p.get("pageid")): p for p in (pages or []) if isinstance(p, dict) and p.get("pageid") is not None}

    def _disambiguation_candidates(self, fetcher: Any, title: str, query: str, want: int) -> List[Tuple[Dict[str, Any], Dict[str, Any]]]:
        """Follow the article links of a disambiguation page and keep the ones whose titles best match the question's words (never guessing beyond the page's own links)."""
        try:
            data = self._api(fetcher, {"action": "query", "prop": "links", "titles": title, "pllimit": 120, "plnamespace": 0})
        except FetchError:
            return []
        pages = (data.get("query") or {}).get("pages") or []
        links = [str(x.get("title")) for p in pages if isinstance(p, dict) for x in (p.get("links") or []) if isinstance(x, dict) and x.get("title")]
        terms = set(tokenize(query))
        scored = sorted(((len(terms & set(tokenize(t))), t) for t in links), key=lambda x: (-x[0], x[1]))
        titles = [t for sc, t in scored if sc > 0][: max(2, want * 2)]
        if not titles:
            return []
        try:
            data = self._api(fetcher, {**self.DETAIL_PROPS, "titles": "|".join(titles[:20]), "exlimit": min(20, len(titles))})
        except FetchError:
            return []
        out = []
        for p in (data.get("query") or {}).get("pages") or []:
            if isinstance(p, dict) and not p.get("missing") and p.get("pageid") and "disambiguation" not in (p.get("pageprops") or {}):
                out.append(({"title": p.get("title"), "pageid": p.get("pageid"), "snippet": ""}, p))
        return out[:want]

    # ------------------------------------------------------------------ whole article
    def load(self, fetcher: Any, settings: Any, ref: Dict[str, Any]) -> Dict[str, Any]:
        """The full structured article for `ref` = {"title": ...} or {"pageid": ...}: sections, tables, lists, equations, references, categories, last revision."""
        params: Dict[str, Any] = {"action": "parse", "prop": "text|sections|categories|revid|displaytitle|properties", "redirects": 1, "disableeditsection": 1, "disabletoc": 1}
        requested = str(ref.get("title") or "")
        if ref.get("pageid"):
            params["pageid"] = int(ref["pageid"])
        elif requested:
            params["page"] = requested
        else:
            raise FetchError("PROVIDER_ERROR", "Name the Wikipedia article to read.")
        data = self._api(fetcher, params, max_bytes=6_000_000)
        parse = data.get("parse")
        if not isinstance(parse, dict):
            raise FetchError("PROVIDER_BAD_ANSWER", "Wikipedia answered in a form that could not be read.")
        html = parse.get("text")
        if isinstance(html, dict):                                                          # formatversion 1 shape: {"*": "<html>"}
            html = html.get("*")
        if not isinstance(html, str) or not html.strip():
            raise FetchError("PROVIDER_BAD_ANSWER", "Wikipedia returned an article with no text.")
        title = str(parse.get("title") or requested)
        revid = parse.get("revid")
        props = parse.get("properties")
        names = set(props) if isinstance(props, dict) else {str(p.get("name")) for p in (props or []) if isinstance(p, dict)}
        disambiguation = "disambiguation" in names
        article = parse_article(html, "mediawiki")
        cats = [str(c.get("category", "")).replace("_", " ") for c in (parse.get("categories") or []) if isinstance(c, dict) and not c.get("hidden")]
        modified = self._revision_time(fetcher, revid)

        def norm(t: str) -> str:
            return re.sub(r"[\s_]+", " ", t).strip().lower()

        return {"provider": self.key, "kind": "article", "ref": {"title": title, "pageid": parse.get("pageid"), "revid": revid}, "title": title, "url": page_url(title),
                "permalink": permalink(title, revid), "published": None, "modified": modified, "authors": [], "publisher": "Wikipedia", "license": "CC BY-SA 4.0",
                "attribution": attribution(title, revid, permalink(title, revid)), "tier": self.tier, "structure": article, "categories": cats[:25],
                "disambiguation": disambiguation, "redirected_from": requested if requested and norm(requested) != norm(title) else None, "retrieved_via": "mediawiki-parse",
                "warnings": (["DISAMBIGUATION_PAGE"] if disambiguation else []) + list(article.get("warnings") or [])}

    def _revision_time(self, fetcher: Any, revid: Any) -> Optional[str]:
        if not revid:
            return None
        try:
            data = self._api(fetcher, {"action": "query", "prop": "revisions", "revids": int(revid), "rvprop": "timestamp|ids"})
        except (FetchError, ValueError, TypeError):
            return None
        for p in (data.get("query") or {}).get("pages") or []:
            for r in (p.get("revisions") or []) if isinstance(p, dict) else []:
                if isinstance(r, dict) and r.get("timestamp"):
                    return str(r["timestamp"])
        return None

    # ------------------------------------------------------------------ self-test
    def diagnose(self, fetcher: Any, settings: Any) -> Dict[str, Any]:
        try:
            data = self._api(fetcher, {"action": "query", "meta": "siteinfo", "siprop": "general"})
        except FetchError as e:
            return explain_failure(self.key, "Wikipedia", e, contact_hint=True)
        name = ((data.get("query") or {}).get("general") or {}).get("sitename")
        if not name:
            return diagnosis(self.key, "bad_answer", "Wikipedia answered, but not with the expected site information.", "Try again later.")
        contact = bool(getattr(settings, "research_contact", ""))
        return diagnosis(self.key, "ok", f"Wikipedia ({name}) answered through its official API.",
                         "" if contact else "Optional: set PREDICTAI_RESEARCH_CONTACT so requests identify who is asking, as Wikimedia asks.", contact_configured=contact)
