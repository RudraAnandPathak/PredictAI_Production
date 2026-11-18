"""Sign-in, sessions and password recovery."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.identity.container import Services
from ..deps import (clear_session_cookie, enforce_csrf, get_services, require_principal, session_token, set_session_cookie)
from ..presenters import me_payload

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(enforce_csrf)])


class RegisterBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    display_name: Optional[str] = Field(default=None, max_length=200)


class LoginBody(BaseModel):
    email: str = Field(max_length=320)
    password: str = Field(max_length=1024)
    remember_me: bool = True


class TokenBody(BaseModel):
    token: str = Field(max_length=512)


class EmailBody(BaseModel):
    email: str = Field(max_length=320)


class RecoveryCompleteBody(BaseModel):
    token: str = Field(max_length=512)
    new_password: str = Field(max_length=1024)


class PasswordBody(BaseModel):
    password: str = Field(max_length=1024)


class LogoutBody(BaseModel):
    everywhere: bool = False


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/register", status_code=202, summary="Create an account (same answer whether or not the email is taken)")
def register(body: RegisterBody, request: Request, services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.auth.register(body.email, body.password, body.display_name))


@router.post("/verify", summary="Confirm an email address with the emailed token")
def verify_email(body: TokenBody, request: Request, services: Services = Depends(get_services)) -> dict:
    services.auth.verify_email(body.token)
    return _ok(request, {"verified": True})


@router.post("/verify/resend", status_code=202, summary="Send a new verification email (always answers the same way)")
def resend_verification(body: EmailBody, request: Request, services: Services = Depends(get_services)) -> dict:
    services.auth.resend_verification(body.email)
    return _ok(request, {"sent": True})


@router.post("/login", summary="Sign in and start a session")
def login(body: LoginBody, request: Request, response: Response, services: Services = Depends(get_services)) -> dict:
    result = services.auth.login(body.email, body.password, body.remember_me)
    set_session_cookie(request, response, result.session)
    return _ok(request, me_payload(services, result.principal))


class MagicConsumeBody(BaseModel):
    token: str = Field(max_length=200)
    remember_me: bool = False


@router.post("/magic-link/request", status_code=202, summary="Email a one-time sign-in link (always answers the same way)")
def request_magic_link(body: EmailBody, request: Request, services: Services = Depends(get_services)) -> dict:
    services.auth.request_magic_link(body.email)
    return _ok(request, {"sent": True})


@router.post("/magic-link/consume", summary="Sign in with a one-time link")
def consume_magic_link(body: MagicConsumeBody, request: Request, response: Response, services: Services = Depends(get_services)) -> dict:
    result = services.auth.consume_magic_link(body.token, body.remember_me)
    set_session_cookie(request, response, result.session)
    return _ok(request, me_payload(services, result.principal))


@router.post("/logout", summary="End the current session, or every session")
def logout(request: Request, response: Response, body: Optional[LogoutBody] = None, services: Services = Depends(get_services)) -> dict:
    services.auth.logout(session_token(request), everywhere=bool(body and body.everywhere))
    clear_session_cookie(request, response)
    return _ok(request, {"signed_out": True})


@router.post("/reauth", summary="Confirm your password to unlock sensitive actions for a short time")
def reauthenticate(body: PasswordBody, request: Request, response: Response, principal: Principal = Depends(require_principal),
                   services: Services = Depends(get_services)) -> dict:
    issued = services.auth.reauthenticate(principal, body.password)
    set_session_cookie(request, response, issued)
    return _ok(request, {"csrf_token": services.auth.csrf_for(issued.session_id)})


@router.get("/sessions", summary="Your signed-in devices")
def list_sessions(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.auth.list_sessions(principal)})


@router.delete("/sessions/{session_id}", summary="Sign out one of your devices")
def revoke_session(session_id: str, request: Request, principal: Principal = Depends(require_principal),
                   services: Services = Depends(get_services)) -> dict:
    services.auth.revoke_session(principal, session_id)
    return _ok(request, {"revoked": True})


@router.post("/recovery/request", status_code=202, summary="Email a password-reset link (always answers the same way)")
def request_recovery(body: EmailBody, request: Request, services: Services = Depends(get_services)) -> dict:
    services.auth.request_recovery(body.email)
    return _ok(request, {"sent": True})


@router.post("/recovery/complete", summary="Set a new password with a reset token")
def complete_recovery(body: RecoveryCompleteBody, request: Request, services: Services = Depends(get_services)) -> dict:
    services.auth.complete_recovery(body.token, body.new_password)
    return _ok(request, {"reset": True})
