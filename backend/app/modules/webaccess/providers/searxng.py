"""SearXNG, a self-hosted open-source metasearch engine. The platform owner points PREDICTAI_SEARXNG_URL at an instance they run (docker/searxng in this repository starts a ready one);
results are snippets and URLs, and the collector fetches the pages it needs through the safe fetcher.

Why this provider used to look "broken" (root causes found in the Phase 6 build, all fixed here):
  * A stock SearXNG answers 403 to `format=json` because `search.formats` lists only `html`. The Phase 6 adapter's fetcher turned every 403 into the generic "BLOCKED: the site does not
    allow this page to be read automatically", so the one message that tells the owner what to change was unreachable code. A 403 from the instance is now reported as
    PROVIDER_JSON_DISABLED with the exact settings.yml lines to add.
  * With the bot limiter on (the default), SearXNG answers 429 to API-style requests. That is now reported as the limiter, with the fix, instead of "rate limited, try later".
  * A refused connection (the instance is not running, wrong port, wrong host) is now said plainly.
  * An instance that is up but whose upstream engines are all failing (CAPTCHA, suspended, blocked) used to return "no results" and look like a bad query; the engines' own reasons
    (`unresponsive_engines`) are now surfaced.
  * Duplicates, unsafe URLs and tracking parameters are removed; the score, the engines and the published date are kept."""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from ..fetcher import JSON_TYPES, FetchError
from ..htmltext import strip_tags
from ..urlguard import UrlRefused, check_url
from .base import SearchHit, SearchProvider, diagnosis, explain_failure

TIME_RANGES = ("day", "week", "month", "year")
TRACKING = re.compile(r"^(utm_|fbclid$|gclid$|mc_cid$|mc_eid$|ref$|ref_src$|igshid$|yclid$)", re.I)
SETTINGS_FIX = ("In the SearXNG settings.yml set  search: formats: [html, json]  (and, for a private instance, server: limiter: false), then restart SearXNG. "
                "docker/searxng/settings.yml in this repository already does both.")


def public_url(url: Any, *, allow_private: bool = False) -> Optional[str]:
    """`url` normalised (no fragment, no tracking parameters) if it is a plain public-looking http(s) address, else None. DNS is not consulted here: the fetcher checks the real addresses
    when a page is actually fetched. `allow_private` mirrors the platform owner's PREDICTAI_RESEARCH_ALLOW_PRIVATE_NETWORK switch (off by default; used on a closed network and in tests)."""
    if not isinstance(url, str):
        return None
    try:
        check_url(url, resolver=lambda host, port: ["93.184.216.34"], allow_private=allow_private)                          # syntax, scheme, credentials, literal private addresses, odd ports
    except UrlRefused:
        return None
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if not TRACKING.match(k)])
    path = parts.path if parts.path not in ("", "/") else "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") if path != "/" else path, query, ""))


class SearxNG(SearchProvider):
    key = "searxng"
    label = "SearXNG (your own instance)"
    description = "General web search through a SearXNG metasearch server that the platform owner runs. Needs PREDICTAI_SEARXNG_URL."
    min_interval_s = 0.5
    source_type = "web"
    supports_time_range = True

    @property
    def host(self) -> str:                                                               # type: ignore[override]
        return "the configured SearXNG instance"

    def configured(self, settings: Any) -> Tuple[bool, str]:
        return (True, "") if getattr(settings, "searxng_url", "") else (False, "The platform owner has not set PREDICTAI_SEARXNG_URL. Start the bundled instance (docker compose up -d in docker/searxng) and set it to http://127.0.0.1:8080.")

    # ------------------------------------------------------------------ one request, with the failures named
    def _request(self, fetcher: Any, settings: Any, params: Dict[str, Any]) -> Dict[str, Any]:
        base = settings.searxng_url
        url = f"{base}/search?{urlencode(params)}"
        try:
            result = fetcher.fetch(url, accept=JSON_TYPES + ("text/html",), robots=False, max_bytes=1_200_000, retries=1)
        except FetchError as e:
            if e.code == "BLOCKED" and e.status == 403:
                raise FetchError("PROVIDER_JSON_DISABLED", f"The SearXNG instance refused the JSON request (403). Its settings do not allow the json format. {SETTINGS_FIX}", status=403) from None
            if e.status is not None and e.status >= 500:
                raise FetchError("PROVIDER_UNAVAILABLE", f"The SearXNG instance at {base} answered {e.status}: it is running but not able to search right now. Check its logs (docker compose logs searxng), and that its search engines are reachable.",
                                 status=e.status, retryable=False) from None
            if e.code == "RATE_LIMITED" or e.status == 429:
                raise FetchError("PROVIDER_LIMITER", "The SearXNG instance answered 429 (Too Many Requests): its bot limiter is rejecting API requests. "
                                 "For a private instance set  server: limiter: false  in settings.yml and restart it.", status=429, retryable=False) from None
            if e.code == "NOT_FOUND":
                raise FetchError("PROVIDER_BAD_ANSWER", "The SearXNG address answered 404 for /search. Check that PREDICTAI_SEARXNG_URL points at the SearXNG root (for example http://127.0.0.1:8080), not at a sub-page.", status=404) from None
            if e.code in ("CONNECT_FAILED", "TIMEOUT"):
                raise FetchError(e.code, f"The SearXNG instance at {base} could not be reached. Is it running? Start it with docker compose up -d in docker/searxng, and check PREDICTAI_SEARXNG_URL.", retryable=e.retryable) from None
            raise
        if "json" not in result.content_type.lower():
            raise FetchError("PROVIDER_JSON_DISABLED", f"The SearXNG instance answered with a web page, not JSON. {SETTINGS_FIX}")
        try:
            data = json.loads(result.body.decode("utf-8", "replace"))
        except ValueError:
            raise FetchError("PROVIDER_BAD_ANSWER", "The search service answered in a form that could not be read.") from None
        if not isinstance(data, dict):
            raise FetchError("PROVIDER_BAD_ANSWER", "The search service answered in a form that could not be read.")
        return data

    # ------------------------------------------------------------------ search
    def search(self, fetcher: Any, settings: Any, query: str, limit: int, *, time_range: Optional[str] = None, categories: str = "general") -> List[SearchHit]:
        params: Dict[str, Any] = {"q": query, "format": "json", "language": "en", "safesearch": 1, "categories": categories}
        if time_range in TIME_RANGES:
            params["time_range"] = time_range
        data = self._request(fetcher, settings, params)
        unresponsive = self._unresponsive(data)
        hits: List[SearchHit] = []
        seen = set()
        for item in data.get("results") if isinstance(data.get("results"), list) else []:
            if not isinstance(item, dict):
                continue
            url = public_url(item.get("url"), allow_private=bool(getattr(settings, "research_allow_private_network", False)))
            if url is None or url in seen:
                continue
            seen.add(url)
            title = strip_tags(str(item.get("title") or url))[:300]
            published = item.get("publishedDate") if isinstance(item.get("publishedDate"), str) and item.get("publishedDate") else None
            engines = [e for e in (item.get("engines") or [item.get("engine")]) if isinstance(e, str)][:6]
            score = item.get("score") if isinstance(item.get("score"), (int, float)) else None
            extra: Dict[str, Any] = {"engine": item.get("engine"), "engines": engines, "score": score, "category": item.get("category")}
            if unresponsive:
                extra["unresponsive_engines"] = unresponsive
            hits.append(SearchHit(title=title, url=url, snippet=strip_tags(str(item.get("content") or ""))[:500], provider=self.key, rank=len(hits) + 1, published=published,
                                  source_type="web", extra=extra))
            if len(hits) >= limit:
                break
        if not hits and unresponsive:                                                    # up, but every engine behind it failed: that is not "no results"
            raise FetchError("PROVIDER_ENGINES_FAILED", "The SearXNG instance is running but its search engines did not answer: " + "; ".join(f"{e}: {r}" for e, r in unresponsive[:5])
                             + ". Public engines often block automated queries; enable other engines in settings.yml or try again later.", retryable=True)
        return hits

    @staticmethod
    def _unresponsive(data: Dict[str, Any]) -> List[List[str]]:
        out: List[List[str]] = []
        for item in data.get("unresponsive_engines") or []:
            if isinstance(item, (list, tuple)) and item and isinstance(item[0], str):
                out.append([item[0][:40], (str(item[1]) if len(item) > 1 else "no answer")[:80]])
        return out[:10]

    # ------------------------------------------------------------------ self-test
    def diagnose(self, fetcher: Any, settings: Any) -> Dict[str, Any]:
        try:
            data = self._request(fetcher, settings, {"q": "wikipedia", "format": "json", "language": "en", "safesearch": 1, "categories": "general"})
        except FetchError as e:
            fixes = {"PROVIDER_JSON_DISABLED": SETTINGS_FIX, "PROVIDER_LIMITER": "Set  server: limiter: false  in settings.yml (private instance) and restart SearXNG.",
                     "PROVIDER_BAD_ANSWER": "Check PREDICTAI_SEARXNG_URL.", "PROVIDER_UNAVAILABLE": "Check SearXNG's logs: docker compose logs searxng.", "CONNECT_FAILED": "Start SearXNG (docker compose up -d in docker/searxng) and check PREDICTAI_SEARXNG_URL.",
                     "TIMEOUT": "Start SearXNG (docker compose up -d in docker/searxng) and check PREDICTAI_SEARXNG_URL."}
            status = {"PROVIDER_JSON_DISABLED": "json_disabled", "PROVIDER_LIMITER": "limited", "CONNECT_FAILED": "unreachable", "TIMEOUT": "unreachable", "PROVIDER_UNAVAILABLE": "unavailable"}.get(e.code)
            if status is None:
                return explain_failure(self.key, "SearXNG", e)
            return diagnosis(self.key, status, e.message, fixes.get(e.code, ""), code=e.code)
        private_ok = bool(getattr(settings, "research_allow_private_network", False))                 # the same switch search uses, so a closed network is diagnosed the way it is searched
        results = [r for r in data.get("results") or [] if isinstance(r, dict) and public_url(r.get("url"), allow_private=private_ok)]
        bad = self._unresponsive(data)
        if not results:
            if bad:
                return diagnosis(self.key, "engines_failed", "SearXNG is running, but its search engines returned nothing: " + "; ".join(f"{e}: {r}" for e, r in bad[:5]),
                                 "Enable other engines in settings.yml, or wait: public engines block automated queries from time to time.", unresponsive_engines=bad)
            return diagnosis(self.key, "bad_answer", "SearXNG answered in JSON, but the test search returned no results.", "Check that at least one engine is enabled in settings.yml.")
        return diagnosis(self.key, "ok", f"SearXNG answered in JSON with {len(results)} results.", "", results=len(results), unresponsive_engines=bad)
