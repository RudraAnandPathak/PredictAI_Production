"""Web collection: run the approved queries on the allowed providers, rank what came back by how well it matches the question, read the best Wikipedia articles, arXiv papers and
web pages through the safe fetcher, and keep what happened (including failures) for the report.

What changed in the research-intelligence upgrade (see docs/phase6/INTELLIGENCE_UPGRADE.md):
  * a search result is kept only if it is ABOUT the question (research.relevance: identity of the named entity, kind of thing, topic, place or period), not because it shares a word; every refusal
    is recorded with its reason, and the person can read it;
  * Wikipedia is asked for the exact article of each named entity first (by title), so "Louis XVI" is chosen over "Louis XVIII" whatever order a text search returns;
  * no provider is promised a share of the reading budget: a provider whose results were all refused contributes nothing, and says "no relevant results" instead of being padded;
  * articles and papers are read section by section, each part of the plan taking the sections that fit it; after a page is read it is checked again against the entity it should be about;
  * every provider's outcome is recorded (answered / nothing relevant / failed / skipped, with the reason) so a provider that was down is never reported as a successful search.

What changed in the corrective Phase 6 work (see docs/phase6/CORRECTIVE_REPORT.md):
  * each provider gets queries written for it (the plan carries them in `by_provider`, precise first); looser ones run only if the precise ones found little;
  * hits are ranked by their match to the question's topic words, not just by the provider's own order; hits that match none of them are dropped and counted, not read;
  * every provider is represented (a fair share of the page budget each) so one noisy provider cannot crowd the others out;
  * Wikipedia articles and arXiv papers are read as structured documents: the sections that bear on the question are chosen and cited with their heading (an abstract is never
    presented as the paper, and a lead paragraph is never presented as the article);
  * a page that cannot be opened falls back to the search result's own summary, labelled as such, instead of vanishing.
A failure is data, not an exception: an unreachable page becomes a source with status 'inaccessible' and a plain reason, never a silent gap."""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..textkit import tokenize
from ..webaccess.fetcher import FetchError
from ..webaccess.structure import compose, select_sections
from ..webaccess.urlguard import registrable_domain
from . import relevance as rel
from .sources import domain_class, source_label
from .understanding import Frame, fold, mentions

FAILURE_TEXT = {
    "TIMEOUT": "The site took too long to answer.", "RATE_LIMITED": "The site asked us to slow down (rate limit).", "NOT_FOUND": "The page no longer exists.", "BLOCKED": "The site refused access.",
    "ROBOTS_DISALLOWED": "The site asks automated readers not to open this page.", "WRONG_TYPE": "The address is not a readable page.", "TOO_LARGE": "The page is too large to read.",
    "DNS_FAILED": "The site could not be found.", "CONNECT_FAILED": "The site could not be reached.", "TLS_ERROR": "The site's security certificate could not be verified.",
    "NO_TEXT": "The page has no readable text.", "PROVIDER_NOT_CONFIGURED": "This search provider is not set up.", "EMPTY": "The page was empty.",
    "PROVIDER_COOLDOWN": "The service refused earlier requests, so no more are being sent for a few minutes.", "PROVIDER_REFUSED": "The service refused the request.",
}
TRACKING = re.compile(r"^(utm_|fbclid|gclid|mc_|ref$|ref_|igshid|yclid)", re.I)
STOP_ON = ("PROVIDER_REFUSED", "PROVIDER_COOLDOWN", "RATE_LIMITED", "CONNECT_FAILED", "TIMEOUT", "PROVIDER_JSON_DISABLED", "PROVIDER_LIMITER", "PROVIDER_UNAVAILABLE", "PROVIDER_NOT_CONFIGURED", "TLS_ERROR")
PER_QUERY = 6
FALLBACK_BELOW = 3                                      # a fallback (looser) query runs only when the earlier ones found fewer hits than this
READ_CAP = {"wikipedia": 4, "arxiv": 3}                 # whole documents are read for at most this many hits per provider
PROVIDER_ORDER = ("wikipedia", "arxiv", "searxng")
DOC_CHARS = {"wikipedia": 14000, "arxiv": 18000}


def canonical(url: str) -> str:
    p = urlsplit(url)
    q = urlencode([(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if not TRACKING.match(k)])
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/", q, ""))


def failure_text(e: FetchError) -> str:
    return FAILURE_TEXT.get(e.code, e.message if e.code.startswith(("URL_", "PROVIDER_", "PDF_", "HTML_")) else "The page could not be read.")


def relevance(hit: Dict[str, Any], terms: Sequence[str]) -> Tuple[float, float, float]:
    """Word overlap of a hit with the topic words: (score, title match, body match). Kept only for plans written before the research-intelligence upgrade, which carry no `understanding`;
    a plan that has one is judged by `research.relevance.assess` instead."""
    wanted = set(tokenize(" ".join(terms)))
    if not wanted:
        return 0.5, 0.0, 0.0
    title = set(tokenize(hit.get("title") or ""))
    body = set(tokenize(" ".join([hit.get("title") or "", hit.get("snippet") or "", (hit.get("text") or "")[:1200]])))
    t, b = len(wanted & title) / len(wanted), len(wanted & body) / len(wanted)
    prior = 1.0 / (1.0 + 0.5 * (max(1, int(hit.get("rank") or 1)) - 1))
    return round(0.5 * t + 0.35 * b + 0.15 * prior, 4), t, b


def _title_key(text: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", fold(text)))


class Collector:
    def __init__(self, web: Any, clock: Any) -> None:
        self.web, self.clock = web, clock

    # ------------------------------------------------------------------ searching
    def _search(self, plan: Dict[str, Any], cancelled: Callable[[], bool], progress: Callable[[str, float, str], None], searches: List[Dict[str, Any]], failures: List[Dict[str, Any]],
                stats: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Dict[str, Any]]]:
        spec = plan["web"]
        hits_by_url: Dict[str, Dict[str, Any]] = {}
        sent = set()
        stopped: Dict[str, str] = {}
        queries = spec["queries"]
        for qi, q in enumerate(queries):
            for provider in q["providers"]:
                st = stats.setdefault(provider, {"queries": 0, "answered": 0, "raw": 0, "errors": [], "stopped": None})
                if provider in stopped:
                    continue
                variants = (q.get("by_provider") or {}).get(provider) or [{"query": q["query"], "role": "primary"}]
                got = 0
                for v in variants:
                    if cancelled():
                        return None
                    if v.get("role") == "fallback" and got >= FALLBACK_BELOW:
                        continue
                    key = (provider, v["query"].lower(), bool(v.get("exact")))
                    if key in sent:
                        continue
                    sent.add(key)
                    progress("searching", 0.2 + 0.2 * qi / max(1, len(queries)), f"Searching {provider}")
                    st["queries"] += 1
                    try:
                        res = self.web.search(provider, v["query"], limit=PER_QUERY, **({"exact": True} if v.get("exact") else {}))
                    except FetchError as e:
                        failures.append({"stage": "search", "provider": provider, "query": v["query"], "code": e.code, "message": failure_text(e), "retryable": e.retryable})
                        searches.append({"provider": provider, "query": v["query"], "role": v.get("role", "primary"), "subtopic": q["subtopic"], "hits": 0, "error": e.code, "cache_hit": False,
                                         "retrieved_at": self.clock.iso()})
                        st["errors"].append({"code": e.code, "message": failure_text(e)})
                        if e.code in STOP_ON:
                            stopped[provider] = e.code                                       # the provider itself is unwell: asking it again now only makes it worse
                            st["stopped"] = e.code
                            break
                        continue
                    st["answered"] += 1
                    searches.append({"provider": provider, "query": v["query"], "role": v.get("role", "primary"), "subtopic": q["subtopic"], "hits": len(res["hits"]), "cache_hit": res["cache_hit"],
                                     "retrieved_at": res["retrieved_at"], **({"exact": True} if v.get("exact") else {})})
                    got += len(res["hits"])
                    for h in res["hits"]:
                        url = canonical(h["url"])
                        old = hits_by_url.get(url)
                        if old is None or h["rank"] < old["rank"]:
                            hits_by_url[url] = {**h, "url": url, "subtopic": q["subtopic"], "found_by": (old or {}).get("found_by", []) + [v["query"]]}
                        else:
                            old.setdefault("found_by", []).append(v["query"])
        for h in hits_by_url.values():
            stats.setdefault(h["provider"], {"queries": 0, "answered": 0, "raw": 0, "errors": [], "stopped": None})["raw"] += 1
        return hits_by_url

    # ------------------------------------------------------------------ deciding what is about the question
    def _screen(self, plan: Dict[str, Any], frame: Frame, hits: Dict[str, Dict[str, Any]], dropped: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Each search result is judged by research.relevance: whom or what it is about, what kind of thing it is, whether it speaks to the question, and whether it shares a place or period.
        A result that fails is recorded with its reason and never read. (A plan from before the upgrade has no `understanding` and is judged by its words, as before.)"""
        accepted: List[Dict[str, Any]] = []
        legacy_terms = (plan.get("focus") or {}).get("terms") or [w for w in tokenize(plan["question"])][:8]
        has_frame = bool(plan.get("understanding"))
        for h in hits.values():
            extra = h.get("extra") or {}
            if not has_frame:
                score, t, b = relevance(h, legacy_terms)
                h["relevance"] = score
                if legacy_terms and t == 0 and b == 0:
                    dropped.append({"url": h["url"], "title": h["title"], "provider": h["provider"], "code": "no_words", "stage": "search", "reason": "matched none of the question's topic words"})
                    continue
                accepted.append(h)
                continue
            if h["provider"] == "arxiv" and (frame.arxiv_ids or frame.quoted_titles):
                v = self._named_paper(frame, h)
            else:
                v = rel.assess(plan, frame, title=h.get("title") or "", description=str(extra.get("description") or ""), categories=extra.get("categories") or [], text=(h.get("text") or h.get("snippet") or "")[:3000],
                               provider=h["provider"], rank=int(h.get("rank") or 1), stage="search", extra=extra)
            h["relevance"], h["verdict"] = v.score, v.to_dict()
            if extra.get("exact_title") and v.accepted:
                h["relevance"] = min(1.0, v.score + 0.1)                                     # the page whose title is the entity is the best candidate for it
            if not v.accepted:
                dropped.append({"url": h["url"], "title": h["title"], "provider": h["provider"], "code": v.code, "stage": "search", "reason": v.reason, "identity": v.identity})
                continue
            accepted.append(h)
        return accepted

    @staticmethod
    def _named_paper(frame: Frame, h: Dict[str, Any]) -> "rel.Verdict":
        """When the question names a paper by arXiv identifier or title, only that paper is accepted: an identifier is matched exactly, a title when it is the same words."""
        extra = h.get("extra") or {}
        base = str(extra.get("arxiv_id") or "")
        for want in frame.arxiv_ids:
            if re.sub(r"v\d+$", "", want) == base:
                return rel.Verdict(True, 1.0, "subject", 1.0, "accepted", ["It is the paper named in the question (same arXiv identifier)."])
        title = _title_key(h.get("title") or "")
        for t in frame.quoted_titles:
            tk = _title_key(t)
            if tk and (tk == title or (len(tk.split()) >= 3 and set(tk.split()) == set(title.split()))):
                return rel.Verdict(True, 0.95, "subject", 1.0, "accepted", ["Its title is the title given in the question."])
        return rel.Verdict(False, 0.0, "none", 0.0, "no_entity", ["It is not the paper the question names."])

    def _select(self, plan: Dict[str, Any], accepted: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """The best-matching accepted results, at most a few documents per provider and `max_pages` in all. No provider is promised a share: one with nothing relevant gives nothing."""
        by_provider: Dict[str, List[Dict[str, Any]]] = {}
        for h in accepted:
            by_provider.setdefault(h["provider"], []).append(h)
        for lst in by_provider.values():
            lst.sort(key=lambda h: (-h["relevance"], h["rank"], h["url"]))
        budget = plan["web"]["max_pages"]
        order = [p for p in PROVIDER_ORDER if p in by_provider] + sorted(p for p in by_provider if p not in PROVIDER_ORDER)
        picked: List[Dict[str, Any]] = []
        taken = {p: 0 for p in order}

        def room(p: str) -> bool:
            return taken[p] < len(by_provider[p]) and taken[p] < READ_CAP.get(p, budget)
        while len(picked) < budget and any(room(p) for p in order):
            for p in order:
                if len(picked) < budget and room(p):
                    picked.append(by_provider[p][taken[p]])
                    taken[p] += 1
        return picked

    # ------------------------------------------------------------------ reading
    def _base(self, h: Dict[str, Any]) -> Dict[str, Any]:
        base = {"url": h["url"], "domain": re.sub(r"^www\.", "", urlsplit(h["url"]).hostname or ""), "provider": h["provider"], "subtopic": h["subtopic"], "title": h["title"], "snippet": h["snippet"],
                "published": h.get("published") or h.get("modified"), "author": h.get("author"), "publisher": h.get("publisher"), "license": h.get("license"), "source_type": h.get("source_type", "web"),
                "relevance": h.get("relevance"), "found_by": h.get("found_by", [])[:4]}
        base["site"] = registrable_domain(base["domain"]) if base["domain"] else ""
        base["domain_class"] = domain_class(base["domain"], base["source_type"])
        base["tier"] = (h.get("extra") or {}).get("tier") or "secondary"
        if h.get("verdict"):
            base["identity"] = {"level": h["verdict"].get("identity"), "reason": h["verdict"].get("reason"), "code": h["verdict"].get("code"), "entity": h["verdict"].get("entity")}
        return base

    def _read_document(self, provider: str, h: Dict[str, Any], base: Dict[str, Any], plan: Dict[str, Any], frame: Frame, failures: List[Dict[str, Any]], dropped: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        extra = h.get("extra") or {}
        ref = {"arxiv_id": extra.get("arxiv_id") if not extra.get("version") else f"{extra['arxiv_id']}v{extra['version']}"} if provider == "arxiv" else ({"pageid": extra["pageid"]} if extra.get("pageid") else {"title": h["title"]})
        if provider == "arxiv" and frame.arxiv_ids:
            pinned = next((w for w in frame.arxiv_ids if re.sub(r"v\d+$", "", w) == extra.get("arxiv_id") and re.search(r"v\d+$", w)), None)
            if pinned:
                ref = {"arxiv_id": pinned}                                                   # the version the person named, not whichever is newest
        try:
            doc = self.web.load(provider, ref)
        except FetchError as e:
            failures.append({"stage": "document", "provider": provider, "url": h["url"], "code": e.code, "message": failure_text(e), "retryable": e.retryable})
            return self._fallback(base, h, e, "document")
        structure = doc.get("structure") or {"sections": []}
        chosen = self._choose(structure, plan, frame, provider)
        text, offsets = compose(structure, chosen, per_section=DOC_CHARS[provider] // 3, lead_chars=3500)
        if not text.strip():
            return self._fallback(base, h, FetchError("NO_TEXT", "No readable text."), "document")
        identity = (base.get("identity") or {}).get("level")
        if frame.identities and identity != "subject":
            full = " ".join(str(s.get("text") or "") for s in structure.get("sections", []))
            if not any(mentions(full, e) for e in frame.identities):
                dropped.append({"url": h["url"], "title": doc.get("title") or h["title"], "provider": provider, "code": "no_entity", "stage": "read",
                                "reason": f"After reading it, it never names {' or '.join(e.name for e in frame.identities)}."})
                return None
        by_id = {s["id"]: s for s in structure.get("sections", [])}
        for o in offsets:
            sec = by_id.get(o["id"]) or {}
            if sec.get("page_start"):
                o["page_start"], o["page_end"] = sec.get("page_start"), sec.get("page_end") or sec.get("page_start")
            if sec.get("canonical"):
                o["canonical"] = sec["canonical"]
        used = [o["heading"] or "Introduction (lead)" for o in offsets]
        full_text = bool(doc.get("full_text", True)) if provider == "arxiv" else True
        meta = {k: doc.get(k) for k in ("attribution", "permalink", "license", "pdf_url", "html_url", "versions", "retrieved_via", "categories", "disambiguation", "redirected_from")}
        meta.update({k: v for k, v in (doc.get("meta") or {}).items() if k in ("arxiv_id", "version", "latest_version", "is_latest", "authors", "doi", "journal_ref", "primary_category", "peer_review",
                                                                              "peer_review_note", "first_submitted", "latest_revision")})
        meta["warnings"] = list(doc.get("warnings") or [])
        if provider == "arxiv":
            scope = ("full text of the paper, read from its " + ("HTML" if doc.get("retrieved_via") == "arxiv-html" else "PDF") + " version") if full_text else "the abstract only (the full text could not be read)"
        else:
            scope = "the article's lead and the sections that match the question"
        return {**base, "title": doc.get("title") or base["title"], "text": text, "retrieved_at": doc["retrieved_at"], "status": "ok", "via": doc.get("retrieved_via") or "document", "cache_hit": doc["cache_hit"],
                "published": doc.get("published") or base["published"], "modified": doc.get("modified"), "author": ", ".join((doc.get("authors") or [])[:6]) or base["author"], "publisher": doc.get("publisher") or base["publisher"],
                "url": doc.get("url") or base["url"], "final_url": doc.get("url"), "license": doc.get("license") or base["license"], "sections": offsets, "sections_used": used,
                "content_scope": scope, "meta": meta, "hidden_blocks_removed": 0, "total_sections": len([s for s in structure.get("sections", []) if s.get("heading")])}

    @staticmethod
    def _choose(structure: Dict[str, Any], plan: Dict[str, Any], frame: Frame, provider: str) -> List[Dict[str, Any]]:
        if plan.get("understanding"):
            chosen = rel.select_sections(structure, plan, frame, max_chars=DOC_CHARS[provider], per_part=2 if provider == "wikipedia" else 3)
        else:
            question_text = " ".join([plan["question"], *((plan.get("focus") or {}).get("phrases") or []), *[st["title"] for st in plan["subtopics"] if st.get("scope") != "dataset"]])
            chosen = select_sections(structure, question_text, max_chars=DOC_CHARS[provider], max_sections=8 if provider == "arxiv" else 7, lead_chars=3500)
        if provider == "arxiv":
            ids = {s["id"] for s in chosen}
            for s in structure.get("sections", []):
                if s.get("canonical") in ("abstract", "conclusion") and s.get("text") and s["id"] not in ids:
                    chosen.append(s)                                                         # the abstract and the conclusion are always read: they state what the paper claims
            order = {s["id"]: i for i, s in enumerate(structure["sections"])}
            chosen = sorted((s for s in chosen if s.get("canonical") != "references"), key=lambda s: order[s["id"]])
        return chosen

    def _fallback(self, base: Dict[str, Any], h: Dict[str, Any], e: FetchError, via: str) -> Dict[str, Any]:
        text = (h.get("text") or h.get("snippet") or "").strip()
        reason = failure_text(e)
        if len(text) >= 60:
            return {**base, "text": text, "retrieved_at": self.clock.iso(), "status": "snippet_only", "status_code": e.code, "via": "search_result", "cache_hit": False, "hidden_blocks_removed": 0,
                    "failure": f"{reason} Only the summary the search service returned is used.", "content_scope": "search-result summary only", "meta": {}}
        return {**base, "text": "", "retrieved_at": self.clock.iso(), "status": "blocked" if e.code in ("ROBOTS_DISALLOWED", "BLOCKED") else "inaccessible", "status_code": e.code, "via": via, "cache_hit": False,
                "hidden_blocks_removed": 0, "failure": reason, "content_scope": "not read", "meta": {}}

    def _page_ok(self, plan: Dict[str, Any], frame: Frame, base: Dict[str, Any], page: Dict[str, Any], h: Dict[str, Any], dropped: List[Dict[str, Any]]) -> bool:
        """A general web page is judged again once its text is in hand: it must really be about the entity (a title can promise more than the page delivers)."""
        if not plan.get("understanding"):
            return True
        text = page.get("text") or ""
        v = rel.assess(plan, frame, title=page.get("title") or h["title"], description="", categories=(), text=text[:20000], provider="web", rank=int(h.get("rank") or 1), stage="passage")
        if v.accepted:
            base["identity"] = {"level": v.identity, "reason": v.reason, "code": v.code, "entity": v.entity}
            return True
        dropped.append({"url": h["url"], "title": page.get("title") or h["title"], "provider": h["provider"], "code": v.code, "stage": "read", "reason": f"After reading the page: {v.reason}", "identity": v.identity})
        return False

    @staticmethod
    def provider_outcomes(plan: Dict[str, Any], stats: Dict[str, Dict[str, Any]], dropped: List[Dict[str, Any]], read: Dict[str, int]) -> List[Dict[str, Any]]:
        """What each provider did, in words a reader can trust: answered, answered with nothing relevant, failed, or was left out and why."""
        out: List[Dict[str, Any]] = []
        rejected: Dict[str, int] = {}
        for d in dropped:
            rejected[d["provider"]] = rejected.get(d["provider"], 0) + 1
        for provider in plan["web"].get("providers") or []:
            st = stats.get(provider) or {"queries": 0, "answered": 0, "raw": 0, "errors": [], "stopped": None}
            errors = st["errors"]
            n_read = read.get(provider, 0)
            if st["queries"] == 0:
                status, note = "not_run", "No search for this provider was sent."
            elif st["answered"] == 0 and errors:
                status, note = "failed", errors[0]["message"]
            elif st["raw"] == 0:
                status, note = "no_results", "The provider answered but found nothing for the approved searches."
            elif n_read == 0 and rejected.get(provider, 0) >= st["raw"]:
                status, note = "no_relevant_results", f"The provider returned {st['raw']} result(s), and none was about the question (see why below)."
            elif errors:
                status, note = "partial", f"{len(errors)} of {st['queries']} searches failed ({errors[0]['message']}); the others answered."
            else:
                status, note = "ok", f"{n_read} source(s) read from {st['raw']} result(s)."
            out.append({"provider": provider, "status": status, "queries": st["queries"], "answered": st["answered"], "results": st["raw"], "rejected": rejected.get(provider, 0), "read": n_read,
                        "errors": [e["code"] for e in errors][:5], "note": note, "stopped": st.get("stopped")})
        for sk in plan["web"].get("skipped_providers") or []:
            out.append({"provider": sk["provider"], "status": "skipped", "queries": 0, "answered": 0, "results": 0, "rejected": 0, "read": 0, "errors": [], "note": sk["reason"], "stopped": None})
        return out

    def run(self, plan: Dict[str, Any], *, cancelled: Callable[[], bool], progress: Callable[[str, float, str], None]) -> Dict[str, Any]:
        searches: List[Dict[str, Any]] = []
        failures: List[Dict[str, Any]] = []
        dropped: List[Dict[str, Any]] = []
        stats: Dict[str, Dict[str, Any]] = {}
        frame = rel.frame_from_plan(plan)
        found = self._search(plan, cancelled, progress, searches, failures, stats)
        if found is None:
            return {"hits": [], "searches": searches, "failures": failures, "sources": [], "dropped": dropped, "cancelled": True, "providers": []}
        ordered = self._select(plan, self._screen(plan, frame, found, dropped))
        sources: List[Dict[str, Any]] = []
        read: Dict[str, int] = {}
        for i, h in enumerate(ordered):
            if cancelled():
                return {"hits": ordered, "searches": searches, "failures": failures, "sources": sources, "dropped": dropped, "cancelled": True, "providers": []}
            progress("reading", 0.4 + 0.3 * i / max(1, len(ordered)), f"Reading {h['title'][:70]}")
            base = self._base(h)
            src: Optional[Dict[str, Any]] = None
            if h["provider"] in READ_CAP:
                src = self._read_document(h["provider"], h, base, plan, frame, failures, dropped)
            elif h.get("text"):
                src = {**base, "text": h["text"], "retrieved_at": self.clock.iso(), "status": "ok", "via": "search_result", "cache_hit": False, "hidden_blocks_removed": 0,
                       "content_scope": "search-result text", "meta": {}}
            else:
                try:
                    page = self.web.page(h["url"])
                except FetchError as e:
                    failures.append({"stage": "page", "provider": h["provider"], "url": h["url"], "code": e.code, "message": failure_text(e), "retryable": e.retryable})
                    src = self._fallback(base, h, e, "page")
                else:
                    if self._page_ok(plan, frame, base, page, h, dropped):
                        meta = page.get("meta") or {}
                        src = {**base, "title": page.get("title") or base["title"], "text": page["text"], "retrieved_at": page["retrieved_at"], "status": "ok", "via": "page", "cache_hit": page["cache_hit"],
                               "published": meta.get("published") or base["published"], "author": meta.get("author") or base["author"], "publisher": meta.get("site_name") or base["publisher"],
                               "content_hash": page["content_hash"], "hidden_blocks_removed": page.get("hidden_blocks_removed", 0), "final_url": page.get("final_url"), "content_scope": "the page's main text",
                               "meta": {}}
            if src is not None:
                sources.append(src)
                read[h["provider"]] = read.get(h["provider"], 0) + 1
        for s in sources:
            s["source_label"] = source_label(s)
        return {"hits": ordered, "searches": searches, "failures": failures, "sources": sources, "dropped": dropped, "cancelled": False, "providers": self.provider_outcomes(plan, stats, dropped, read)}
