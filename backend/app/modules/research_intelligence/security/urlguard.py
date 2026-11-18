"""Secure URL fetching (SSRF protection).

Rules, each covered by tests/test_p10_security.py:
* only http and https, only ports 80 and 443, no credentials in the URL, host must be a name or a literal IP that is itself public;
* the name is resolved ONCE, every returned address must be public, and the connection is made to that validated address (so a DNS answer cannot change between the check and the
  connection: no DNS rebinding); TLS still verifies the certificate against the original host name;
* redirects are followed by hand, at most 4, and every hop is validated again from scratch;
* size cap enforced while streaming, total time cap, content types limited to what the importer understands;
* IPv4-mapped IPv6, link-local (169.254.0.0/16 incl. cloud metadata), loopback, private, CGNAT, multicast, reserved and unspecified addresses are refused, and so are the decimal/hex/octal
  spellings of IPv4 (they are normalised by `ipaddress`/`socket.inet_aton` before checking).
`allow_private` exists for tests and a trusted LAN; settings never honour it when hosted."""
from __future__ import annotations

import http.client
import ipaddress
import socket
import ssl
import time
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Sequence, Tuple
from urllib.parse import urljoin, urlsplit

ALLOWED_PORTS = {"http": 80, "https": 443}
ALLOWED_TYPES = ("text/html", "application/xhtml+xml", "text/plain", "text/markdown", "application/pdf", "application/json", "application/xml", "text/xml", "application/atom+xml")
MAX_REDIRECTS = 4
USER_AGENT = "PredictAI-Research/1.0 (+local research assistant; respects robots and access limits)"


class UrlRejected(Exception):
    """`code` is stable and safe to show; the message never echoes secrets."""

    def __init__(self, code: str, message: str, status: int | None = None, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.code, self.message = code, message
        self.status, self.retry_after = status, retry_after            # set for HTTP errors only (discovery uses them to report rate limits)


def ip_is_public(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return False
    if isinstance(addr, ipaddress.IPv6Address) and addr.ipv4_mapped is not None:
        addr = addr.ipv4_mapped
    if isinstance(addr, ipaddress.IPv6Address) and (addr.sixtofour is not None or addr.teredo is not None):
        return False
    return addr.is_global and not addr.is_multicast


def _literal_ip(host: str) -> Optional[str]:
    """Return a canonical IP when `host` is any IP spelling (dotted, decimal, hex, octal, bracketed IPv6), else None."""
    h = host.strip("[]")
    try:
        return str(ipaddress.ip_address(h))
    except ValueError:
        pass
    if all(c in "0123456789abcdefxABCDEFX." for c in h) and any(c.isdigit() for c in h):
        try:
            return socket.inet_ntoa(socket.inet_aton(h))               # inet_aton accepts decimal, hex and octal forms
        except OSError:
            return None
    return None


Resolver = Callable[[str, int], List[str]]


def system_resolver(host: str, port: int) -> List[str]:
    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)})
    except socket.gaierror:
        raise UrlRejected("URL_UNRESOLVABLE", "That address could not be found.") from None


@dataclass
class Target:
    scheme: str
    host: str
    port: int
    path: str
    ip: str
    url: str


def validate_url(url: str, *, resolver: Resolver = system_resolver, allow_private: bool = False, allowed_ports: Optional[Dict[str, int]] = None) -> Target:
    if not isinstance(url, str) or not url.strip() or len(url) > 2000 or any(ord(c) < 32 or ord(c) == 127 for c in url):
        raise UrlRejected("URL_INVALID", "Enter a web address that starts with http:// or https://.")
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    if scheme not in ("http", "https"):
        raise UrlRejected("URL_SCHEME_NOT_ALLOWED", "Only http and https addresses can be imported.")
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise UrlRejected("URL_CREDENTIALS_NOT_ALLOWED", "Addresses that contain a user name or password are not accepted.")
    host = (parts.hostname or "").strip().rstrip(".").lower()
    if not host or len(host) > 253:
        raise UrlRejected("URL_INVALID", "Enter a web address that starts with http:// or https://.")
    try:
        port = parts.port or ALLOWED_PORTS[scheme]
    except ValueError:
        raise UrlRejected("URL_INVALID", "That port is not valid.") from None
    default_ports = allowed_ports or ALLOWED_PORTS
    if not allow_private and port != default_ports[scheme]:
        raise UrlRejected("URL_PORT_NOT_ALLOWED", "Only the standard web ports (80 and 443) can be used.")
    if not allow_private and (host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home", ".corp"))):
        raise UrlRejected("URL_PRIVATE_ADDRESS", "Addresses on your own computer or network cannot be imported.")
    literal = _literal_ip(host)
    addresses = [literal] if literal else resolver(host, port)
    if not addresses:
        raise UrlRejected("URL_UNRESOLVABLE", "That address could not be found.")
    if not allow_private and not all(ip_is_public(a) for a in addresses):
        raise UrlRejected("URL_PRIVATE_ADDRESS", "Addresses on your own computer or network cannot be imported.")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return Target(scheme, host, port, path, addresses[0], url.strip())


@dataclass
class FetchResult:
    status: int
    final_url: str
    content_type: str
    body: bytes
    headers: Dict[str, str]
    redirects: List[str] = field(default_factory=list)
    truncated: bool = False


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float) -> None:
        super().__init__(host, port, timeout=timeout)
        self._ip = ip

    def connect(self) -> None:
        self.sock = socket.create_connection((self._ip, self.port), self.timeout)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, ip: str, timeout: float, context: ssl.SSLContext) -> None:
        super().__init__(host, port, timeout=timeout, context=context)
        self._ip = ip

    def connect(self) -> None:
        raw = socket.create_connection((self._ip, self.port), self.timeout)
        self.sock = self._context.wrap_socket(raw, server_hostname=self.host)       # certificate and name are verified against the ORIGINAL host


def _media_type(value: str) -> str:
    return (value or "").split(";")[0].strip().lower()


class SafeFetcher:
    def __init__(self, *, resolver: Resolver = system_resolver, allow_private: bool = False, max_bytes: int = 25 * 1024 * 1024, timeout: float = 20.0,
                 clock: Callable[[], float] = time.monotonic, ssl_context: Optional[ssl.SSLContext] = None, user_agent: str = USER_AGENT) -> None:
        self.resolver, self.allow_private, self.max_bytes, self.timeout, self.clock = resolver, allow_private, max_bytes, timeout, clock
        self.ssl_context, self.user_agent = ssl_context or ssl.create_default_context(), user_agent

    def fetch(self, url: str, *, accept: Sequence[str] = ALLOWED_TYPES, extra_headers: Optional[Dict[str, str]] = None) -> FetchResult:
        deadline = self.clock() + self.timeout * 2
        redirects: List[str] = []
        current = url
        for _hop in range(MAX_REDIRECTS + 1):
            target = validate_url(current, resolver=self.resolver, allow_private=self.allow_private)
            remaining = max(1.0, deadline - self.clock())
            conn = (_PinnedHTTPS(target.host, target.port, target.ip, min(self.timeout, remaining), self.ssl_context) if target.scheme == "https"
                    else _PinnedHTTP(target.host, target.port, target.ip, min(self.timeout, remaining)))
            try:
                headers = {"User-Agent": self.user_agent, "Accept": ", ".join(accept), "Accept-Encoding": "identity", "Connection": "close"}
                headers.update(extra_headers or {})
                try:
                    conn.request("GET", target.path, headers=headers)
                    resp = conn.getresponse()
                except (OSError, http.client.HTTPException) as e:
                    raise UrlRejected("URL_FETCH_FAILED", "The page could not be retrieved.") from e
                if resp.status in (301, 302, 303, 307, 308):
                    location = resp.getheader("Location")
                    resp.read(0)
                    if not location:
                        raise UrlRejected("URL_FETCH_FAILED", "The page redirected without saying where.")
                    current = urljoin(current, location)
                    redirects.append(current)
                    continue
                ctype = _media_type(resp.getheader("Content-Type") or "")
                if resp.status >= 400:
                    ra = resp.getheader("Retry-After")
                    raise UrlRejected("URL_HTTP_ERROR", f"The site answered with an error ({resp.status}).", status=resp.status, retry_after=float(ra) if ra and ra.replace(".", "", 1).isdigit() else None)
                if ctype and not any(ctype == a or ctype.startswith(a.split("/")[0] + "/") and a.endswith("/*") for a in accept):
                    raise UrlRejected("URL_TYPE_NOT_ALLOWED", "That kind of file cannot be imported from a web address.")
                declared = resp.getheader("Content-Length")
                if declared and declared.isdigit() and int(declared) > self.max_bytes:
                    raise UrlRejected("URL_TOO_LARGE", "That page is larger than the import limit.")
                chunks: List[bytes] = []
                total = 0
                while True:
                    if self.clock() > deadline:
                        raise UrlRejected("URL_TIMEOUT", "The page took too long to download.")
                    try:
                        block = resp.read(65536)
                    except (OSError, http.client.HTTPException) as e:
                        raise UrlRejected("URL_FETCH_FAILED", "The page could not be retrieved.") from e
                    if not block:
                        break
                    total += len(block)
                    if total > self.max_bytes:
                        raise UrlRejected("URL_TOO_LARGE", "That page is larger than the import limit.")
                    chunks.append(block)
                return FetchResult(resp.status, current, ctype, b"".join(chunks), {k.lower(): v for k, v in resp.getheaders()}, redirects)
            finally:
                conn.close()
        raise UrlRejected("URL_TOO_MANY_REDIRECTS", "The address redirected too many times.")
