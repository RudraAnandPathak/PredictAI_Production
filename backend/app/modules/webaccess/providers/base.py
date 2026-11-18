"""Search-provider contract. A provider turns a short query into hits (title, URL, snippet, optionally full text) using a documented public API.
Providers never fetch private data: the only thing they send is the query text the person approved."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from ...textkit import STOPWORDS  # noqa: E402  (shared list; re-exported for the providers)


@dataclass
class SearchHit:
    title: str
    url: str
    snippet: str
    provider: str
    rank: int
    published: Optional[str] = None
    modified: Optional[str] = None
    text: Optional[str] = None                 # full or lead text when the API supplies it (no second request needed)
    author: Optional[str] = None
    publisher: Optional[str] = None
    source_type: str = "web"                   # web | encyclopedia | preprint
    license: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)


class SearchProvider:
    key = ""
    label = ""
    description = ""
    host = ""                                   # shown to the person: "your query is sent to <host>"
    hosts: Tuple[str, ...] = ()                 # every host this provider talks to (for the polite per-host delay); defaults to `host`
    min_interval_s = 1.0
    source_type = "web"
    supports_exact = False                      # True when `search(..., exact=True)` can look up a page by its exact title (Wikipedia)
    reads_documents = False                     # True when `load` can return the full structured text of a hit (Wikipedia articles, arXiv papers)
    tier = "secondary"                          # how the source should be described: tertiary (encyclopedia) | preprint | secondary (general web)

    def configured(self, settings: Any) -> Tuple[bool, str]:
        return True, ""

    def search(self, fetcher: Any, settings: Any, query: str, limit: int) -> List[SearchHit]:    # pragma: no cover - interface
        raise NotImplementedError

    def load(self, fetcher: Any, settings: Any, ref: Dict[str, Any]) -> Dict[str, Any]:         # pragma: no cover - interface (only providers with reads_documents)
        raise NotImplementedError

    def diagnose(self, fetcher: Any, settings: Any) -> Dict[str, Any]:                          # pragma: no cover - interface
        raise NotImplementedError

    def describe(self, settings: Any) -> Dict[str, Any]:
        ok, reason = self.configured(settings)
        return {"key": self.key, "label": self.label, "description": self.description, "host": self.host, "available": ok, "reason": reason or None, "source_type": self.source_type,
                "tier": self.tier, "reads_documents": self.reads_documents}


def content_terms(query: str, limit: int = 6) -> List[str]:
    from ...textkit import content_terms as _terms
    return _terms(query, limit)


def diagnosis(provider: str, status: str, message: str, fix: str = "", **extra: Any) -> Dict[str, Any]:
    """The result of a provider self-test: `ok` only when a real request returned a usable answer."""
    return {"provider": provider, "ok": status == "ok", "status": status, "message": message, "fix": fix, **extra}


def explain_failure(provider: str, label: str, e: Any, contact_hint: bool = False) -> Dict[str, Any]:
    """Turn a FetchError into a plain diagnosis: what happened, and the one thing to do about it."""
    code = getattr(e, "code", "")
    table = {
        "CONNECT_FAILED": ("unreachable", f"{label} could not be reached from this server.", "Check the server's internet connection, proxy or firewall."),
        "TIMEOUT": ("unreachable", f"{label} did not answer in time.", "Try again; if it keeps happening check the connection or raise PREDICTAI_RESEARCH_FETCH_TIMEOUT_SECONDS."),
        "TLS_ERROR": ("unreachable", f"A secure connection to {label} could not be made.", "Check the server's clock and certificate store (on macOS: run 'Install Certificates.command' for your Python)."),
        "RATE_LIMITED": ("limited", f"{label} is limiting requests right now.", "Wait a little and try again; results are cached so repeated questions do not repeat requests."),
        "PROVIDER_REFUSED": ("refused", f"{label} refused the request.", ("Set PREDICTAI_RESEARCH_CONTACT to an email address or URL so the request identifies who is asking (Wikimedia asks for this)." if contact_hint else "Check the service's access rules.")),
        "BLOCKED": ("refused", f"{label} refused the request.", ("Set PREDICTAI_RESEARCH_CONTACT to an email address or URL so the request identifies who is asking (Wikimedia asks for this)." if contact_hint else "Check the service's access rules.")),
        "PROVIDER_BAD_ANSWER": ("bad_answer", f"{label} answered in a form that could not be read.", "The service may have changed its format or be returning an error page; try again later."),
        "PROVIDER_ERROR": ("bad_answer", f"{label} reported an error.", "Try again later."),
    }
    status, message, fix = table.get(code, ("unreachable", f"{label} could not be used ({code or 'error'}).", "Try again later."))
    return diagnosis(provider, status, message, fix, code=code or None)
