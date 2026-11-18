"""Minimal JSON-over-HTTP client for model servers. It validates the address (no credentials, no link-local or metadata addresses; private addresses only for providers
the owner classed as local or private-network), connects to the checked IP, never follows redirects, bounds the response size and the total time, and never puts a key in an error."""
from __future__ import annotations

import http.client
import json
import socket
import time
from typing import Any, Callable, Dict, Optional

from ..webaccess.fetcher import _PinnedHTTP, _PinnedHTTPS, _tls_context
from ..webaccess.urlguard import UrlRefused, check_url
from .base import ProviderError

Resolver = Optional[Callable[[str, int], list]]


class JsonHttp:
    def __init__(self, *, allow_private: bool, resolver: Resolver = None) -> None:
        self.allow_private, self.resolver = allow_private, resolver
        self._ctx = None

    def request(self, method: str, url: str, *, body: Optional[Dict[str, Any]] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 30.0, max_bytes: int = 4_000_000) -> Dict[str, Any]:
        try:
            target = check_url(url, resolver=self.resolver, allow_private=self.allow_private)
        except UrlRefused as e:
            raise ProviderError("PROVIDER_ADDRESS_REFUSED", "The provider address is not allowed.") from e
        payload = None if body is None else json.dumps(body, ensure_ascii=False).encode("utf-8")
        head = {"Accept": "application/json", "User-Agent": "PredictAI/0.6", **({"Content-Type": "application/json"} if payload is not None else {}), **(headers or {})}
        started = time.monotonic()
        address = target.addresses[0]
        if target.scheme == "https":
            self._ctx = self._ctx or _tls_context()
            conn: http.client.HTTPConnection = _PinnedHTTPS(target.host, target.port, address, timeout, self._ctx)
        else:
            conn = _PinnedHTTP(target.host, target.port, address, timeout)
        try:
            conn.request(method, target.target, body=payload, headers=head)
            resp = conn.getresponse()
            raw = b""
            while len(raw) <= max_bytes:
                block = resp.read(min(65536, max_bytes + 1 - len(raw)))
                if not block:
                    break
                raw += block
                if time.monotonic() - started > timeout * 2:
                    raise ProviderError("PROVIDER_TIMEOUT", "The provider took too long to answer.", retryable=True)
            status = resp.status
        except socket.timeout as e:
            raise ProviderError("PROVIDER_TIMEOUT", "The provider took too long to answer.", retryable=True) from e
        except (ConnectionError, OSError, http.client.HTTPException) as e:
            raise ProviderError("PROVIDER_UNREACHABLE", "The provider could not be reached.", retryable=True) from e
        finally:
            conn.close()
        if len(raw) > max_bytes:
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider answered with more data than allowed.")
        if 300 <= status < 400:
            raise ProviderError("PROVIDER_REDIRECT", "The provider tried to redirect the request, which is not followed.")
        if status in (401, 403):
            raise ProviderError("PROVIDER_AUTH", "The provider refused the credentials.")
        if status == 404:
            raise ProviderError("PROVIDER_NOT_FOUND", "The provider does not have that model or endpoint.")
        if status == 429:
            raise ProviderError("PROVIDER_RATE_LIMITED", "The provider is limiting requests.", retryable=True)
        if status >= 500:
            raise ProviderError("PROVIDER_SERVER_ERROR", "The provider reported an internal error.", retryable=True)
        if status >= 400:
            raise ProviderError("PROVIDER_REQUEST_REJECTED", "The provider rejected the request.")
        try:
            data = json.loads(raw.decode("utf-8", "replace"))
        except ValueError as e:
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider's answer could not be read.") from e
        if not isinstance(data, dict):
            raise ProviderError("PROVIDER_BAD_ANSWER", "The provider's answer could not be read.")
        return data
