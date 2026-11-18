"""Read-only bridge to the Phase 6 web-access layer (unchanged): the SearXNG provider and its fetcher. Kept in one place so Research Intelligence has a single, lazy dependency on Phase 6."""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple


def phase6_searxng(settings: Any, fetcher_factory: Optional[Callable[[Any], Any]] = None) -> Tuple[Any, Any]:
    from ..webaccess.providers.searxng import SearxNG
    from ..webaccess.service import build_fetcher
    return SearxNG(), (fetcher_factory(settings) if fetcher_factory else build_fetcher(settings))
