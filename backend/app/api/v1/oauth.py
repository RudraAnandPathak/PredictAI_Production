"""External accounts (Google, GitHub): connect, sign in, register, and re-confirm identity."""
from __future__ import annotations

from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.errors import AppError
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.identity.constants import Event
from ...modules.identity.container import Services
from ..deps import enforce_csrf, get_services, require_principal, session_token, set_session_cookie

router = APIRouter(prefix="/auth/oauth", tags=["oauth"], dependencies=[Depends(enforce_csrf)])


class PasswordBody(BaseModel):
    password: Optional[str] = Field(default=None, max_length=1024)  # absent for accounts that have no password


class SignInBody(BaseModel):
    intent: str = Field(default="login", max_length=20)


def nonce_cookie_name(request: Request) -> str:
    return ("__Host-" if request.app.state.settings.cookie_secure else "") + "pai_oauth"


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("", summary="Available providers and which ones you have connected")
def list_providers(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.oauth.list_providers(principal)})


@router.post("/{provider}/link", summary="Start connecting a provider; returns the provider's authorization URL")
def start_link(provider: str, request: Request, principal: Principal = Depends(require_principal),
               services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"authorization_url": services.oauth.start_link(principal, provider)})


@router.get("/available", summary="Which providers can be offered on the sign-in and registration pages")
def available(request: Request, services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.oauth.available()})


@router.post("/{provider}/signin", summary="Start Continue-with-provider; returns the provider's authorization URL")
def start_signin(provider: str, body: SignInBody, request: Request, response: Response, services: Services = Depends(get_services)) -> dict:
    url, nonce = services.oauth.start_signin(provider, body.intent)
    settings = request.app.state.settings
    response.set_cookie(key=nonce_cookie_name(request), value=nonce, max_age=600, path="/", secure=settings.cookie_secure, httponly=True, samesite="lax")
    return _ok(request, {"authorization_url": url})


@router.post("/{provider}/reauth", summary="Start re-confirming your identity with a connected provider")
def start_reauth(provider: str, request: Request, principal: Principal = Depends(require_principal),
                 services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"authorization_url": services.oauth.start_reauth(principal, provider)})


def _back(frontend: str, path: str, **params: str) -> RedirectResponse:
    query = "&".join(f"{k}={quote(v, safe='/')}" for k, v in params.items())
    return RedirectResponse(url=f"{frontend}{path}?{query}" if query else f"{frontend}{path}", status_code=302)


@router.get("/{provider}/callback", summary="Where the provider sends the browser back")
def callback(provider: str, request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None,
             services: Services = Depends(get_services)) -> RedirectResponse:
    frontend = request.app.state.settings.frontend_base_url
    intent = services.oauth.peek_intent(state)
    if intent in ("signin", "register"):
        page = "/register" if intent == "register" else "/login"
        redirect = None
        if error:
            services.audit.record(Event.OAUTH_LOGIN_FAILED, "failure", reason="provider_denied", metadata={"provider": provider})
            redirect = _back(frontend, page, oauth_error="denied", provider=provider)
        else:
            try:
                result = services.oauth.complete_signin(provider, code, state, request.cookies.get(nonce_cookie_name(request)))
                redirect = _back(frontend, "/", oauth="registered" if result.created else "signed_in")
                set_session_cookie(request, redirect, result.login.session)
            except AppError as failure:
                redirect = _back(frontend, page, oauth_error=failure.code.lower(), provider=provider)
        redirect.delete_cookie(nonce_cookie_name(request), path="/")  # one use, whatever the outcome
        return redirect
    try:
        principal = services.auth.authenticate(session_token(request))
    except AppError:
        return _back(frontend, "/login", next="/account")
    outcome = "error"
    if error:
        services.audit.record(Event.OAUTH_FAILED, "failure", actor=principal.user_id, reason="provider_denied", metadata={"provider": provider})
    elif intent == "reauth":
        try:
            issued = services.oauth.complete_reauth(principal, provider, code, state)
            redirect = _back(frontend, "/account", oauth="reauthed", provider=provider)
            set_session_cookie(request, redirect, issued)
            return redirect
        except AppError:
            pass
    else:
        try:
            services.oauth.complete_link(principal, provider, code, state)
            outcome = "linked"
        except AppError:
            pass  # already audited inside the service; the user only needs to know it failed
    return _back(frontend, "/account", oauth=outcome, provider=provider)


@router.post("/{provider}/unlink", summary="Disconnect a provider (needs your password)")
def unlink(provider: str, body: PasswordBody, request: Request, principal: Principal = Depends(require_principal),
           services: Services = Depends(get_services)) -> dict:
    services.oauth.unlink(principal, provider, body.password)
    return _ok(request, {"unlinked": True})
