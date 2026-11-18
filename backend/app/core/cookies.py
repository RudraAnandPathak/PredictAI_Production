"""Cookie helpers shared by the HTTP layer and the error handlers."""
from __future__ import annotations


def expired_cookie_header(name: str, secure: bool) -> str:
    """A Set-Cookie value that removes the session cookie from the browser."""
    return f"{name}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; Path=/; HttpOnly; SameSite=Lax" + ("; Secure" if secure else "")
