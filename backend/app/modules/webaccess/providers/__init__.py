"""Registry of search providers. Adding one means adding a class here; the research code only knows the SearchProvider contract."""
from __future__ import annotations

from typing import Dict, List

from .arxiv import Arxiv
from .base import SearchHit, SearchProvider
from .searxng import SearxNG
from .wikipedia import Wikipedia

PROVIDERS: Dict[str, SearchProvider] = {p.key: p for p in (Wikipedia(), Arxiv(), SearxNG())}


def provider_keys() -> List[str]:
    return list(PROVIDERS)


__all__ = ["PROVIDERS", "SearchHit", "SearchProvider", "provider_keys"]
