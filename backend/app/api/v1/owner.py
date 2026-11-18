"""Owner administration. Every route needs the owner role (checked here and again inside the
services); changes also need a recent password confirmation (step-up)."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.identity.container import Services
from ...modules.identity.errors import validation_failed
from ..deps import enforce_csrf, get_services, require_owner

router = APIRouter(prefix="/owner", tags=["owner"], dependencies=[Depends(enforce_csrf)])


class BlockBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class GrantBody(BaseModel):
    tier: str = Field(default="special", max_length=40)


class RevokeBody(BaseModel):
    reason: Optional[str] = Field(default=None, max_length=500)


class CodeCreateBody(BaseModel):
    label: Optional[str] = Field(default=None, max_length=200)
    target_user_id: Optional[str] = Field(default=None, max_length=64)
    expires_in_days: Optional[int] = None


class OwnerBody(BaseModel):
    user_id: str = Field(max_length=64)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


# ---- users ----
@router.get("/users", summary="Search accounts")
def list_users(request: Request, q: Optional[str] = None, status: Optional[str] = None, limit: int = 25, offset: int = 0,
               owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.users.list_users(owner, query=q, status=status, limit=limit, offset=offset))


@router.get("/users/{user_id}", summary="One account")
def get_user(user_id: str, request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.users.get_user(owner, user_id))


@router.post("/users/{user_id}/block", summary="Suspend an account")
def block_user(user_id: str, body: BlockBody, request: Request, owner: Principal = Depends(require_owner),
               services: Services = Depends(get_services)) -> dict:
    services.users.block_user(owner, user_id, body.reason)
    return _ok(request, {"blocked": True})


@router.post("/users/{user_id}/unblock", summary="Restore a suspended account")
def unblock_user(user_id: str, request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    services.users.unblock_user(owner, user_id)
    return _ok(request, {"blocked": False})


# ---- special access ----
@router.post("/users/{user_id}/entitlements", status_code=201, summary="Grant special access directly")
def grant_special(user_id: str, body: GrantBody, request: Request, owner: Principal = Depends(require_owner),
                  services: Services = Depends(get_services)) -> dict:
    if body.tier != "special":
        raise validation_failed([{"field": "tier", "issue": "invalid", "message": "Only the special tier can be granted."}])
    services.codes.grant_special(owner, user_id)
    return _ok(request, {"granted": True})


@router.post("/entitlements/{entitlement_id}/revoke", summary="Revoke an entitlement (takes effect on the next request)")
def revoke_entitlement(entitlement_id: str, body: RevokeBody, request: Request, owner: Principal = Depends(require_owner),
                       services: Services = Depends(get_services)) -> dict:
    services.codes.revoke_entitlement(owner, entitlement_id, body.reason)
    return _ok(request, {"revoked": True})


@router.get("/access-codes", summary="Special-access codes and their status")
def list_codes(request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.codes.list_codes(owner)})


@router.post("/access-codes", status_code=201, summary="Create an individual single-use code (shown once)")
def create_code(body: CodeCreateBody, request: Request, owner: Principal = Depends(require_owner),
                services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.codes.create_code(owner, label=body.label, target_user_id=body.target_user_id,
                                                   expires_in_days=body.expires_in_days))


@router.post("/access-codes/{code_id}/revoke", summary="Revoke an unused code")
def revoke_code(code_id: str, request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    services.codes.revoke_code(owner, code_id)
    return _ok(request, {"revoked": True})


# ---- security events ----
@router.get("/audit-events", summary="Security and administration history")
def audit_events(request: Request, event_type: Optional[str] = None, outcome: Optional[str] = None, actor: Optional[str] = None,
                 target: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                 before_id: Optional[int] = None, limit: int = 50, owner: Principal = Depends(require_owner),
                 services: Services = Depends(get_services)) -> dict:
    return _ok(request, services.users.query_audit(owner, event_type=event_type, outcome=outcome, actor_user_id=actor, target_user_id=target,
                                                   since=since, until=until, before_id=before_id, limit=limit))


# ---- owners ----
@router.get("/owners", summary="Current owners")
def list_owners(request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    return _ok(request, {"items": services.users.list_owners(owner)})


@router.post("/owners", status_code=201, summary="Give another account the owner role (backup owner)")
def promote_owner(body: OwnerBody, request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    services.users.promote_owner(owner, body.user_id)
    return _ok(request, {"promoted": True})


@router.delete("/owners/{user_id}", summary="Remove the owner role from an account")
def demote_owner(user_id: str, request: Request, owner: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    services.users.demote_owner(owner, user_id)
    return _ok(request, {"demoted": True})
