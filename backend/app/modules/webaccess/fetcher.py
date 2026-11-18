"""The only way research code reaches the web.

  * every URL (and every redirect target) goes through `urlguard.check_url`; the connection is made to an address that was checked, with the original host
    name kept for the Host header and for TLS verification (so DNS cannot be changed between the check and the connection);
  * no cookies, no credentials, a fixed identifying User-Agent; compressed replies are decoded with a size limit;
  * connect/read time limits, a total time limit, a body size limit, a redirect limit (no https->http downgrade), a content-type allow-list;
  * robots.txt is honoured for page fetches (not for API endpoints a provider documents for programmatic use);
  * a polite minimum interval per host, 429/503 `Retry-After` honoured (bounded), bounded retries for transient failures.

The transport is injectable so tests can run without a network."""
from __future__ import annotations

import http.client
import socket
import ssl
import threading
import time
import zlib
from dataclasses import dataclass, field
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import urljoin
from urllib.robotparser import RobotFileParser

from .urlguard import CheckedUrl, Resolver, UrlRefused, check_url

TEXT_TYPES = ("text/html", "application/xhtml+xml", "text/plain")
FEED_TYPES = ("application/rss+xml", "application/atom+xml", "application/xml", "text/xml", "application/rdf+xml", "text/plain")
JSON_TYPES = ("application/json", "application/vnd.api+json", "text/json")
MAX_REDIRECTS = 4
ROBOTS_TTL_S = 3600


class FetchError(Exception):
    """A fetch failed. `code` is stable and safe to store and show; `retryable` says whether trying again later could help."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status: Optional[int] = None, retry_after: Optional[float] = None) -> None:
        super().__init__(message)
        self.code, self.message, self.retryable, self.status, self.retry_after = code, message, retryable, status, retry_after


@dataclass
class RawResponse:
    status: int
    headers: Dict[str, str]
    body: bytes
    truncated: bool = False


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int
    content_type: str
    body: bytes
    redirects: int = 0
    elapsed_ms: int = 0
    headers: Dict[str, str] = field(default_factory=dict)


class Transport:
    def request(self, target: CheckedUrl, address: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> RawResponse:    # pragma: no cover - interface
        raise NotImplementedError


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._address = address

    def connect(self) -> None:
        self.sock = socket.create_connection((self._address, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, address: str, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._address = address

    def connect(self) -> None:
        raw = socket.create_connection((self._address, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)          # certificate and host name are verified against the ORIGINAL name


def _tls_context() -> ssl.SSLContext:
    try:
        import certifi                                                                  # optional: only improves trust-store discovery on some macOS Python builds
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:  # noqa: BLE001
        return ssl.create_default_context()


class HttpTransport(Transport):
    def __init__(self) -> None:
        self._ctx = _tls_context()

    def request(self, target: CheckedUrl, address: str, headers: Dict[str, str], timeout: float, max_bytes: int) -> RawResponse:
        conn: http.client.HTTPConnection = (_PinnedHTTPS(target.host, target.port, address, timeout, self._ctx) if target.scheme == "https"
                                            else _PinnedHTTP(target.host, target.port, address, timeout))
        try:
            conn.request("GET", target.target, headers=headers)
            resp = conn.getresponse()
            head = {k.lower(): v for k, v in resp.getheaders()}
            limit = max_bytes + 1
            chunks: List[bytes] = []
            total = 0
            while total < limit:
                block = resp.read(min(65536, limit - total))
                if not block:
                    break
                chunks.append(block)
                total += len(block)
            body = b"".join(chunks)
            return RawResponse(resp.status, head, body[:max_bytes], len(body) > max_bytes)
        finally:
            conn.close()


def _decode_body(body: bytes, encoding: str, max_bytes: int) -> Tuple[bytes, bool]:
    encoding = (encoding or "identity").lower().strip()
    if encoding in ("", "identity"):
        return body, False
    if encoding in ("gzip", "x-gzip", "deflate"):
        wbits = (zlib.MAX_WBITS | 16) if "gzip" in encoding else zlib.MAX_WBITS
        d = zlib.decompressobj(wbits)
        try:
            out = d.decompress(body, max_bytes + 1)
        except zlib.error:
            raise FetchError("BAD_ENCODING", "The page could not be decoded.") from None
        return out[:max_bytes], len(out) > max_bytes
    raise FetchError("BAD_ENCODING", "The page uses a compression format that is not supported.")


class SafeFetcher:
    def __init__(self, *, user_agent: str, timeout_s: float = 15.0, allow_private: bool = False, trusted_hosts: Iterable[str] = (), resolver: Optional[Resolver] = None,
                 transport: Optional[Transport] = None, clock: Callable[[], float] = time.monotonic, sleeper: Callable[[float], None] = time.sleep,
                 host_interval_s: float = 1.0, max_sleep_s: float = 10.0) -> None:
        self.user_agent, self.timeout_s, self.allow_private = user_agent, timeout_s, allow_private
        self.trusted_hosts = frozenset(trusted_hosts)
        self.resolver, self.transport, self.clock, self.sleeper = resolver, transport or HttpTransport(), clock, sleeper
        self.host_interval_s, self.max_sleep_s = host_interval_s, max_sleep_s
        self._last: Dict[str, float] = {}
        self._intervals: Dict[str, float] = {}
        self._robots: Dict[str, Tuple[float, Optional[RobotFileParser], bool]] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------ helpers
    def set_interval(self, host: str, seconds: float) -> None:
        self._intervals[host.lower()] = max(0.0, seconds)

    def _check(self, url: str) -> CheckedUrl:
        try:
            return check_url(url, resolver=self.resolver, allow_private=self.allow_private, trusted_hosts=self.trusted_hosts)
        except UrlRefused as e:
            raise FetchError(e.code, e.message) from None

    def _wait_turn(self, host: str, deadline: float) -> None:
        interval = self._intervals.get(host, self.host_interval_s)
        if interval <= 0:
            return
        with self._lock:
            now = self.clock()
            ready = self._last.get(host, -1e9) + interval
            wait = max(0.0, ready - now)
            self._last[host] = max(now, ready)
        if wait > 0:
            if wait > self.max_sleep_s or self.clock() + wait > deadline:
                raise FetchError("RATE_LIMITED", "Too many requests to this site right now; try again shortly.", retryable=True)
            self.sleeper(wait)

    def _once(self, target: CheckedUrl, headers: Dict[str, str], max_bytes: int, deadline: float) -> RawResponse:
        if deadline - self.clock() <= 0:
            raise FetchError("TIMEOUT", "The request took too long.", retryable=True)
        self._wait_turn(target.host, deadline)
        last: Optional[FetchError] = None
        for address in target.addresses[:3]:
            try:
                return self.transport.request(target, address, headers, min(self.timeout_s, max(1.0, deadline - self.clock())), max_bytes)
            except (socket.timeout, TimeoutError):
                last = FetchError("TIMEOUT", "The site did not answer in time.", retryable=True)
            except ssl.SSLError:
                raise FetchError("TLS_ERROR", "A secure connection could not be established.") from None
            except (ConnectionError, OSError, http.client.HTTPException):
                last = FetchError("CONNECT_FAILED", "The site could not be reached.", retryable=True)
        raise last or FetchError("CONNECT_FAILED", "The site could not be reached.", retryable=True)

    # ------------------------------------------------------------ robots.txt
    def _allowed_by_robots(self, target: CheckedUrl) -> None:
        origin = f"{target.scheme}://{target.authority}"
        now = self.clock()
        with self._lock:
            cached = self._robots.get(origin)
        if cached and now - cached[0] < ROBOTS_TTL_S:
            parser, problem = cached[1], cached[2]
        else:
            parser, problem = None, False
            try:
                raw = self._request_checked(self._check(origin + "/robots.txt"), accept=("*/*",), max_bytes=200_000, follow=3)
                if raw.status == 200:
                    parser = RobotFileParser()
                    parser.parse(raw.body.decode("utf-8", "replace").splitlines())
                elif 400 <= raw.status < 500:
                    parser = None                                               # no robots file: everything allowed
                else:
                    problem = True                                              # a server error means: do not crawl (RFC 9309)
            except FetchError:
                problem = True
            with self._lock:
                self._robots[origin] = (now, parser, problem)
        if problem:
            raise FetchError("ROBOTS_UNAVAILABLE", "The site's crawling rules could not be read, so the page was not fetched.", retryable=True)
        if parser is not None and not parser.can_fetch(self.user_agent, target.url):
            raise FetchError("ROBOTS_DISALLOWED", "The site asks automated tools not to fetch this page.")

    # ------------------------------------------------------------ the request loop (redirects, size, type)
    def _request_checked(self, target: CheckedUrl, *, accept: Sequence[str], max_bytes: int, follow: int, extra_headers: Optional[Dict[str, str]] = None) -> RawResponse:
        headers = {"User-Agent": self.user_agent, "Accept": ", ".join(accept), "Accept-Encoding": "gzip, identity", "Connection": "close"}
        headers.update(extra_headers or {})
        deadline = self.clock() + max(self.timeout_s * 2, self.timeout_s + 5)
        current = target
        for hop in range(follow + 1):
            raw = self._once(current, headers, max_bytes, deadline)
            if raw.status in (301, 302, 303, 307, 308) and raw.headers.get("location"):
                if hop >= follow:
                    raise FetchError("TOO_MANY_REDIRECTS", "The address redirects too many times.")
                nxt = self._check(urljoin(current.url, raw.headers["location"]))
                if current.scheme == "https" and nxt.scheme == "http":
                    raise FetchError("REDIRECT_DOWNGRADE", "The address redirects from a secure to an insecure connection.")
                current = nxt
                continue
            raw.headers["x-final-url"] = current.url
            raw.headers["x-redirects"] = str(hop)
            return raw
        raise FetchError("TOO_MANY_REDIRECTS", "The address redirects too many times.")

    def fetch(self, url: str, *, accept: Sequence[str] = TEXT_TYPES, max_bytes: int = 1_500_000, robots: bool = True, retries: int = 2,
              extra_headers: Optional[Dict[str, str]] = None) -> FetchResult:
        started = self.clock()
        target = self._check(url)
        if robots:
            self._allowed_by_robots(target)
        attempt = 0
        while True:
            try:
                raw = self._request_checked(target, accept=accept, max_bytes=max_bytes, follow=MAX_REDIRECTS, extra_headers=extra_headers)
                if raw.status in (429, 503) or raw.status >= 500:
                    retry_after = None
                    try:
                        retry_after = float(raw.headers.get("retry-after", ""))
                    except ValueError:
                        retry_after = None
                    code = "RATE_LIMITED" if raw.status in (429, 503) else "HTTP_ERROR"
                    raise FetchError(code, "The site is limiting requests right now." if code == "RATE_LIMITED" else f"The site answered with an error ({raw.status}).",
                                     retryable=True, status=raw.status, retry_after=retry_after)
                break
            except FetchError as e:
                if not e.retryable or attempt >= retries:
                    raise
                attempt += 1
                pause = e.retry_after if e.retry_after is not None else 0.5 * (2 ** (attempt - 1))
                self.sleeper(min(self.max_sleep_s, max(0.0, pause)))
        if raw.status in (404, 410):
            raise FetchError("NOT_FOUND", "The page was not found.", status=raw.status)
        if raw.status in (401, 403, 451):
            raise FetchError("BLOCKED", "The site does not allow this page to be read automatically.", status=raw.status)
        if raw.status >= 400 or raw.status != 200:
            raise FetchError("HTTP_ERROR", f"The site answered with an error ({raw.status}).", status=raw.status)
        ctype = raw.headers.get("content-type", "").split(";")[0].strip().lower()
        if "*/*" not in accept and ctype and not any(ctype == a or (a.endswith("/*") and ctype.startswith(a[:-1])) for a in accept):
            raise FetchError("WRONG_TYPE", "This kind of file is not supported for research (only web pages, plain text, feeds and data from search services).")
        body, over = _decode_body(raw.body, raw.headers.get("content-encoding", ""), max_bytes)
        if raw.truncated or over:
            raise FetchError("TOO_LARGE", "The page is larger than the limit for research.")
        if not body:
            raise FetchError("EMPTY", "The page was empty.")
        return FetchResult(url=target.url, final_url=raw.headers.get("x-final-url", target.url), status=raw.status, content_type=raw.headers.get("content-type", ""), body=body,
                           redirects=int(raw.headers.get("x-redirects", "0") or 0), elapsed_ms=int((self.clock() - started) * 1000),
                           headers={k: raw.headers[k] for k in ("last-modified", "date", "content-language") if k in raw.headers})
