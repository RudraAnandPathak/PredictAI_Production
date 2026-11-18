"""The web-access facade used by the research collector: search through a provider and fetch a page, both through the cache and the safe fetcher."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from ...core.clock import Clock, to_iso
from .cache import WebCache, cache_key, normalise_query
from .fetcher import TEXT_TYPES, FetchError, SafeFetcher
from .htmltext import decode_body, extract_html, extract_plain
from .providers import PROVIDERS
from .urlguard import check_url, registrable_domain


USER_AGENT = "PredictAI-Research/0.6"


def user_agent(settings: Any) -> str:
    """`<client>/<version> (<contact>) <library>`: the shape Wikimedia's User-Agent policy asks for. With no contact configured the string says so honestly (the Phase 6 build sent an
    invented project URL here, which identified nobody)."""
    contact = (getattr(settings, "research_contact", "") or "").strip()
    return f"{USER_AGENT} ({contact or 'self-hosted research assistant; no contact configured'}) python-http.client"


def build_fetcher(settings: Any, *, resolver=None, transport=None) -> SafeFetcher:
    trusted: List[str] = []
    if getattr(settings, "searxng_url", ""):
        try:
            c = check_url(settings.searxng_url, allow_private=True)
            trusted.append(f"{c.host}:{c.port}")
        except Exception:  # noqa: BLE001  a bad URL is already reported by configuration loading
            pass
    fetcher = SafeFetcher(user_agent=user_agent(settings), timeout_s=float(settings.research_fetch_timeout_s),
                          allow_private=bool(settings.research_allow_private_network), trusted_hosts=trusted, resolver=resolver, transport=transport)
    for p in PROVIDERS.values():
        for host in (p.hosts or ((p.host,) if p.host else ())):
            if "." in host:
                fetcher.set_interval(host, 0.0 if getattr(p, "gate_key", None) else p.min_interval_s)          # a gated provider is paced by the shared gate, not per fetcher
    return fetcher


class WebAccess:
    def __init__(self, fetcher: SafeFetcher, settings: Any, clock: Clock, cache: Optional[WebCache], owner: str, project: str) -> None:
        self.fetcher, self.settings, self.clock, self.cache, self.owner, self.project = fetcher, settings, clock, cache, owner, project

    # ---------------------------------------------------------------- providers
    def providers(self) -> List[Dict[str, Any]]:
        return [p.describe(self.settings) for p in PROVIDERS.values()]

    # ---------------------------------------------------------------- search
    def search(self, provider_key: str, query: str, limit: int = 5, *, use_cache: bool = True, exact: bool = False) -> Dict[str, Any]:
        provider = PROVIDERS.get(provider_key)
        if provider is None:
            raise FetchError("PROVIDER_UNKNOWN", "That search provider does not exist.")
        ok, reason = provider.configured(self.settings)
        if not ok:
            raise FetchError("PROVIDER_NOT_CONFIGURED", reason)
        if exact and not getattr(provider, "supports_exact", False):
            exact = False                                                                          # only a provider with titles to look up has an exact mode; for the others it is an ordinary search
        key = cache_key("search", provider_key, normalise_query(query), str(limit), "exact" if exact else "")
        if use_cache and self.cache is not None:
            hit = self.cache.get(self.owner, self.project, "search", key)
            if hit is not None:
                return {**hit["payload"], "cache_hit": True, "retrieved_at": hit["fetched_at"], "expires_at": hit["expires_at"]}
        found = provider.search(self.fetcher, self.settings, query, limit, exact=True) if exact else provider.search(self.fetcher, self.settings, query, limit)
        hits = [asdict(h) for h in found]
        now = to_iso(self.clock.now())
        payload = {"provider": provider_key, "query": query, "hits": hits}
        if self.cache is not None and hits:                                                      # an empty answer is not cached: it may be a bad query or a hiccup, and 24 hours is long
            self.cache.put(self.owner, self.project, "search", key, provider_key, query, payload)
        return {**payload, "cache_hit": False, "retrieved_at": now, "expires_at": None}

    # ---------------------------------------------------------------- whole articles and papers
    def load(self, provider_key: str, ref: Dict[str, Any], *, use_cache: bool = True) -> Dict[str, Any]:
        """The full structured Wikipedia article or arXiv paper for `ref`, cached per person and project (only the extracted text is kept, never a PDF file)."""
        provider = PROVIDERS.get(provider_key)
        if provider is None or not provider.reads_documents:
            raise FetchError("PROVIDER_UNKNOWN", "That source cannot be read in full.")
        ok, reason = provider.configured(self.settings)
        if not ok:
            raise FetchError("PROVIDER_NOT_CONFIGURED", reason)
        key = cache_key("doc", provider_key, json.dumps(ref, sort_keys=True, default=str))          # stored as a "page" entry (the table allows search|page); the key prefix keeps them apart
        if use_cache and self.cache is not None:
            hit = self.cache.get(self.owner, self.project, "page", key)
            if hit is not None:
                return {**hit["payload"], "cache_hit": True, "retrieved_at": hit["fetched_at"]}
        doc = provider.load(self.fetcher, self.settings, ref)
        now = to_iso(self.clock.now())
        if self.cache is not None:
            self.cache.put(self.owner, self.project, "page", key, provider_key, str(ref.get("title") or ref.get("arxiv_id") or ref.get("url") or "")[:200], doc)
        return {**doc, "cache_hit": False, "retrieved_at": now}

    # ---------------------------------------------------------------- self-test
    def diagnose(self, provider_key: Optional[str] = None) -> List[Dict[str, Any]]:
        """A real request to each provider, reported plainly. Nothing from the person's project is sent: the test questions are fixed words."""
        out: List[Dict[str, Any]] = []
        for key, provider in PROVIDERS.items():
            if provider_key and key != provider_key:
                continue
            ok, reason = provider.configured(self.settings)
            if not ok:
                out.append({"provider": key, "ok": False, "status": "not_configured", "message": reason, "fix": "See the setup guide for this provider.", "checked_at": to_iso(self.clock.now())})
                continue
            try:
                res = provider.diagnose(self.fetcher, self.settings)
            except FetchError as e:
                res = {"provider": key, "ok": False, "status": "unreachable", "message": e.message, "fix": "", "code": e.code}
            out.append({**res, "label": provider.label, "host": provider.host, "checked_at": to_iso(self.clock.now())})
        return out

    # ---------------------------------------------------------------- pages
    def page(self, url: str, *, use_cache: bool = True) -> Dict[str, Any]:
        checked_key = cache_key("page", url.split("#")[0])
        if use_cache and self.cache is not None:
            hit = self.cache.get(self.owner, self.project, "page", checked_key)
            if hit is not None:
                return {**hit["payload"], "cache_hit": True, "retrieved_at": hit["fetched_at"]}
        result = self.fetcher.fetch(url, accept=TEXT_TYPES, max_bytes=1_500_000, robots=True)
        ctype = result.content_type.split(";")[0].strip().lower()
        text = decode_body(result.body, result.content_type)
        doc = extract_plain(text) if ctype == "text/plain" else extract_html(text)
        if not doc["text"].strip():
            raise FetchError("NO_TEXT", "No readable text was found on the page.")
        final = check_url(result.final_url, resolver=self.fetcher.resolver, allow_private=self.fetcher.allow_private, trusted_hosts=self.fetcher.trusted_hosts)
        content_hash = __import__("hashlib").sha256(doc["text"].encode("utf-8")).hexdigest()
        payload = {"url": url.split("#")[0], "final_url": final.url, "domain": final.host, "site": registrable_domain(final.host), "title": doc["title"], "text": doc["text"], "meta": doc["meta"],
                   "hidden_blocks_removed": doc["hidden_blocks_removed"], "content_hash": content_hash, "http": {"status": result.status, "content_type": ctype, "last_modified": result.headers.get("last-modified"),
                                                                                                                  "elapsed_ms": result.elapsed_ms, "redirects": result.redirects}}
        now = to_iso(self.clock.now())
        if self.cache is not None:
            self.cache.put(self.owner, self.project, "page", checked_key, "page", url, payload, str(result.status))
        return {**payload, "cache_hit": False, "retrieved_at": now}
