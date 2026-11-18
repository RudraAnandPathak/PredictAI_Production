"""URL validation and SSRF defence.

A URL is accepted only if: it is http(s); it has no user name or password; the port is allowed; the name is a public DNS name (or a public IP literal); EVERY
address it resolves to is a public address. The caller then connects to one of the *checked* addresses (see fetcher), so a name that resolves differently a
moment later (DNS rebinding) cannot change where the connection goes. Redirect targets go through the same function again.

Always refused, even in development mode: link-local (including the 169.254.169.254 cloud-metadata address), multicast, unspecified and reserved ranges.
Private and loopback addresses are refused unless the server was started with the development flag (never allowed when hosted) or the host is an explicit
trusted endpoint that the platform owner configured (for example a SearXNG instance on this machine)."""
from __future__ import annotations

import ipaddress
import re
import socket
from dataclasses import dataclass
from typing import Callable, Iterable, List, Optional, Sequence, Tuple
from urllib.parse import SplitResult, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
PUBLIC_PORTS = (80, 443)
_BAD_CHARS = re.compile(r"[\x00-\x20\x7f-\x9f\\]")
_BLOCKED_SUFFIXES = (".localhost", ".local", ".internal", ".lan", ".home", ".corp", ".intranet", ".home.arpa")
_DIGITS_ONLY = re.compile(r"^(0x[0-9a-f]+|[0-9]+)(\.(0x[0-9a-f]+|[0-9]+))*$", re.I)

Resolver = Callable[[str, int], Sequence[str]]


class UrlRefused(Exception):
    """The URL may not be fetched. `code` is stable; `message` is safe to show (it never contains the resolved address of an internal host)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code, self.message = code, message


@dataclass(frozen=True)
class CheckedUrl:
    url: str              # normalised absolute URL
    scheme: str
    host: str             # ASCII (IDNA) host name or IP literal
    port: int
    target: str           # path + query, always starting with "/"
    addresses: Tuple[str, ...]   # the checked addresses; the connection is made to one of these

    @property
    def authority(self) -> str:
        default = 443 if self.scheme == "https" else 80
        host = f"[{self.host}]" if ":" in self.host else self.host
        return host if self.port == default else f"{host}:{self.port}"


def default_resolver(host: str, port: int) -> Sequence[str]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, UnicodeError, OSError):
        raise UrlRefused("DNS_FAILED", "The address could not be looked up.") from None
    seen: List[str] = []
    for family, _t, _p, _c, sockaddr in infos:
        if family in (socket.AF_INET, socket.AF_INET6):
            ip = sockaddr[0].split("%")[0]
            if ip not in seen:
                seen.append(ip)
    return seen


def _embedded_v4(ip: ipaddress.IPv6Address) -> Optional[ipaddress.IPv4Address]:
    if ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    if ip in ipaddress.ip_network("64:ff9b::/96"):                    # NAT64
        return ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF)
    if ip in ipaddress.ip_network("2002::/16"):                       # 6to4
        return ipaddress.IPv4Address((int(ip) >> 80) & 0xFFFFFFFF)
    return None


def classify_address(raw: str) -> str:
    """'public', 'private' (loopback / private ranges: allowed only in development or for a trusted host) or 'never' (always refused)."""
    try:
        ip = ipaddress.ip_address(raw.split("%")[0])
    except ValueError:
        return "never"
    if isinstance(ip, ipaddress.IPv6Address):
        inner = _embedded_v4(ip)
        if inner is not None:
            return classify_address(str(inner))
        if ip in ipaddress.ip_network("2001::/32"):                    # Teredo
            return "never"
    if ip.is_unspecified or ip.is_multicast or ip.is_link_local or ip.is_reserved:
        return "never"
    if str(ip).startswith("169.254."):
        return "never"
    if ip.is_loopback or ip.is_private and not ip.is_global:
        return "private"
    if not ip.is_global:                                              # carrier-grade NAT, documentation, benchmarking, ...
        return "private"
    return "public"


def _host_ok(host: str) -> None:
    if host in ("localhost", "ip6-localhost") or host.endswith(_BLOCKED_SUFFIXES):
        raise UrlRefused("URL_REFUSED", "That address is on a private or local network.")


def check_url(raw: object, *, resolver: Optional[Resolver] = None, allow_private: bool = False, trusted_hosts: Iterable[str] = (),
              allowed_ports: Sequence[int] = PUBLIC_PORTS) -> CheckedUrl:
    if not isinstance(raw, str):
        raise UrlRefused("URL_INVALID", "The address is not valid.")
    text = raw.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise UrlRefused("URL_INVALID", "The address is empty or too long.")
    if _BAD_CHARS.search(text):
        raise UrlRefused("URL_INVALID", "The address contains characters that are not allowed.")
    try:
        parts: SplitResult = urlsplit(text)
        port = parts.port
        host_raw = parts.hostname
    except ValueError:
        raise UrlRefused("URL_INVALID", "The address is not valid.") from None
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise UrlRefused("URL_SCHEME", "Only http and https addresses can be fetched.")
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise UrlRefused("URL_CREDENTIALS", "Addresses containing a user name or password are not fetched.")
    if not host_raw:
        raise UrlRefused("URL_INVALID", "The address has no host name.")
    host = host_raw.rstrip(".").lower()
    try:
        literal: Optional[ipaddress._BaseAddress] = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is None:
        if _DIGITS_ONLY.match(host):                                   # 2130706433, 0x7f.1, 017700000001 ... never a legitimate public name
            raise UrlRefused("URL_REFUSED", "That address is written in an unusual numeric form and is not fetched.")
        try:
            host = host.encode("idna").decode("ascii")
        except UnicodeError:
            raise UrlRefused("URL_INVALID", "The host name is not valid.") from None
        if len(host) > 253 or any(not part or len(part) > 63 for part in host.split(".")):
            raise UrlRefused("URL_INVALID", "The host name is not valid.")
    effective_port = port if port is not None else (443 if scheme == "https" else 80)
    authority = f"{host}:{effective_port}"
    trusted = authority in set(trusted_hosts)
    if not (allow_private or trusted) and effective_port not in allowed_ports:
        raise UrlRefused("URL_PORT", "Only the standard web ports (80 and 443) can be fetched.")
    if literal is None and not (allow_private or trusted):
        if "." not in host:
            raise UrlRefused("URL_REFUSED", "That address is on a private or local network.")
        _host_ok(host)
    addresses: List[str] = [str(literal)] if literal is not None else list((resolver or default_resolver)(host, effective_port))
    if not addresses:
        raise UrlRefused("DNS_FAILED", "The address could not be looked up.")
    for address in addresses:
        kind = classify_address(address)
        if kind == "never" or (kind == "private" and not (allow_private or trusted)):
            raise UrlRefused("URL_REFUSED", "That address is on a private or local network, or is otherwise not allowed.")
    target = parts.path or "/"
    if not target.startswith("/"):
        target = "/" + target
    if parts.query:
        target += "?" + parts.query
    normal_host = f"[{host}]" if ":" in host else host
    netloc = normal_host if (effective_port == (443 if scheme == "https" else 80)) else f"{normal_host}:{effective_port}"
    return CheckedUrl(url=urlunsplit((scheme, netloc, parts.path or "/", parts.query, "")), scheme=scheme, host=host, port=effective_port, target=target, addresses=tuple(addresses))


def registrable_domain(host: str) -> str:
    """A cheap 'site' key (last two labels, three for common country-code second levels). Used to count independent publishers, not for security."""
    labels = [p for p in host.lower().split(".") if p]
    if len(labels) <= 2:
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in ("co", "com", "org", "net", "gov", "ac", "edu", "or", "ne", "go"):
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
