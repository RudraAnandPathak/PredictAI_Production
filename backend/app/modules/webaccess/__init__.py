"""Safe access to the outside web for research: URL validation (SSRF defence), a pinned fetcher, HTML text extraction, search providers and a cache.

Nothing here talks to the network unless a caller supplies a person's consent upstream (see research.settings); this package only makes sure that when
a request IS made it cannot reach an internal address, cannot be redirected there, cannot be unbounded, and cannot leak credentials."""
