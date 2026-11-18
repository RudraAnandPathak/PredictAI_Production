"""HTTP-level security helpers that need no web framework: client IP, CSRF and origin
checks, and a coarse device label. Kept pure so they can be tested directly."""
from __future__ import annotations

import ipaddress
from typing import Collection, Mapping, Optional

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def client_ip(peer: Optional[str], forwarded_for: Optional[str], trusted_proxy_count: int) -> str:
    """Peer address, or the address added by the nearest trusted proxy when one is configured.

    X-Forwarded-For is only believed for the last ``trusted_proxy_count`` hops; with the
    default of 0 it is ignored entirely, so clients cannot spoof their address.
    """
    if trusted_proxy_count > 0 and forwarded_for:
        hops = [part.strip() for part in forwarded_for.split(",") if part.strip()]
        if len(hops) >= trusted_proxy_count:
            return hops[-trusted_proxy_count]
    return peer or "unknown"


def truncate_ip(ip: Optional[str]) -> Optional[str]:
    """Privacy-minimised address for audit records: IPv4 /24, IPv6 /48."""
    try:
        address = ipaddress.ip_address((ip or "").strip())
    except ValueError:
        return None
    prefix = 24 if address.version == 4 else 48
    return str(ipaddress.ip_network(f"{address}/{prefix}", strict=False).network_address)


def csrf_problem(
    *,
    method: str,
    origin: Optional[str],
    allowed_origins: Collection[str],
    session_csrf: Optional[str],
    provided_token: Optional[str],
) -> Optional[str]:
    """Why a state-changing request must be rejected ('origin' or 'token'), else None.

    - A browser-sent Origin must be one of ours (this also covers login CSRF).
    - When the request carries a valid session, it must also present that session's CSRF token.
    """
    if method.upper() in SAFE_METHODS:
        return None
    if origin is not None and origin not in allowed_origins:
        return "origin"
    if session_csrf is not None:
        import hmac

        if not provided_token or not hmac.compare_digest(provided_token.encode(), session_csrf.encode()):
            return "token"
    return None


def device_label(user_agent: Optional[str]) -> str:
    """'Chrome on macOS'-style label so people can recognise their own sessions. Nothing else is kept."""
    ua = user_agent or ""
    if "Edg/" in ua:
        browser = "Edge"
    elif "OPR/" in ua:
        browser = "Opera"
    elif "Chrome/" in ua or "CriOS" in ua:
        browser = "Chrome"
    elif "Firefox/" in ua or "FxiOS" in ua:
        browser = "Firefox"
    elif "Safari/" in ua:
        browser = "Safari"
    else:
        browser = ""
    if "iPhone" in ua or "iPad" in ua:
        system = "iOS"
    elif "Android" in ua:
        system = "Android"
    elif "Windows" in ua:
        system = "Windows"
    elif "Macintosh" in ua:
        system = "macOS"
    elif "Linux" in ua or "X11" in ua:
        system = "Linux"
    else:
        system = ""
    if browser and system:
        return f"{browser} on {system}"
    return browser or system or "Unknown device"


def header(headers: Mapping[str, str], name: str) -> Optional[str]:
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return value
    return None
