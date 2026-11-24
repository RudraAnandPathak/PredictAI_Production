"""The signed-in user's own account."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal, build_principal
from ...modules.identity.container import Services
from ..deps import (clear_session_cookie, enforce_csrf, get_services, require_principal, set_session_cookie)
from ..presenters import me_payload

router = APIRouter(tags=["account"], dependencies=[Depends(enforce_csrf)])


class ProfileBody(BaseModel):
    display_name: Optional[str] = Field(default=None, max_length=200)


class PasswordChangeBody(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(max_length=1024)


class PasswordBody(BaseModel):
    password: Optional[str] = Field(default=None, max_length=1024)  # absent for accounts that have no password


class CodeBody(BaseModel):
    code: str = Field(max_length=200)


class ApiTokenBody(BaseModel):
    name: str = Field(max_length=80)
    password: Optional[str] = Field(default=None, max_length=1024)   # absent for accounts that sign in only with Google/GitHub
    expires_in_days: int = 90


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/me", summary="Current user, roles, permissions and CSRF token")
def read_me(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    return _ok(request, me_payload(services, principal))


@router.patch("/me", summary="Update your display name")
def update_me(body: ProfileBody, request: Request, principal: Principal = Depends(require_principal),
              services: Services = Depends(get_services)) -> dict:
    services.users.update_profile(principal, body.display_name)
    return _ok(request, me_payload(services, _refresh(services, principal)))


def _refresh(services: Services, principal: Principal) -> Principal:
    repo = services.ctx.repo
    return build_principal(repo, repo.user_by_id(principal.user_id), repo.session_by_id(principal.session_id))


@router.post("/me/password", summary="Change your password (signs out your other devices)")
def change_password(body: PasswordChangeBody, request: Request, response: Response, principal: Principal = Depends(require_principal),
                    services: Services = Depends(get_services)) -> dict:
    issued = services.auth.change_password(principal, body.current_password, body.new_password)
    set_session_cookie(request, response, issued)
    return _ok(request, {"changed": True, "csrf_token": services.auth.csrf_for(issued.session_id)})


@router.post("/me/delete", summary="Delete your account and personal data")
def delete_me(body: PasswordBody, request: Request, response: Response, principal: Principal = Depends(require_principal),
              services: Services = Depends(get_services)) -> dict:
    services.users.delete_account(principal, body.password)
    clear_session_cookie(request, response)
    return _ok(request, {"deleted": True})


@router.get("/me/entitlements", summary="Your active entitlements")
def my_entitlements(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.codes.entitlements_for(principal), "tiers": sorted(principal.tiers)})


@router.post("/access-codes/activate", summary="Redeem a special-access code")
def activate_code(body: CodeBody, request: Request, principal: Principal = Depends(require_principal),
                  services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.codes.activate(principal, body.code))


# ---------------------------------------------------------------------------- personal API tokens (for your own scripts and AI agents)
@router.get("/me/api-tokens", summary="Your personal API tokens (the token values are never shown again)")
def list_api_tokens(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    from ...modules.identity.api_tokens import ApiTokenService
    return _ok(request, ApiTokenService(services.ctx).list(principal))


@router.post("/me/api-tokens", status_code=201, summary="Create a personal API token (needs your password; the token is shown once)")
def create_api_token(body: ApiTokenBody, request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    from ...modules.identity.api_tokens import ApiTokenService
    return _ok(request, ApiTokenService(services.ctx).create(principal, body.name, body.password, body.expires_in_days))


@router.delete("/me/api-tokens/{token_id}", summary="Revoke a personal API token")
def revoke_api_token(token_id: str, request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    from ...modules.identity.api_tokens import ApiTokenService
    return _ok(request, ApiTokenService(services.ctx).revoke(principal, token_id))
