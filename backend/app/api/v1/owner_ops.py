"""Owner Command Center (Phase 7). Every route needs the owner role; reads are metadata only; changes are confirmed, audited and recorded in own_events."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_owner

router = APIRouter(prefix="/owner", tags=["owner"], dependencies=[Depends(enforce_csrf)])


class MaintenanceBody(BaseModel):
    active: Any
    notice: Optional[str] = Field(default=None, max_length=400)
    confirm: Any = False


class QuotaBody(BaseModel):
    value: Any


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/overview", summary="Platform counts (no user content)")
def overview(request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.overview(owner))


@router.get("/monitoring", summary="What can and cannot be measured right now")
def monitoring(request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.monitoring(owner))


@router.get("/users/{user_id}/summary", summary="Counts and sizes for one account (never titles or content)")
def user_summary(user_id: str, request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.user_summary(owner, user_id))


@router.get("/maintenance", summary="Maintenance mode state")
def get_maintenance(request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.maintenance())


@router.put("/maintenance", summary="Turn maintenance mode on or off (needs recent sign-in and confirmation)")
def set_maintenance(body: MaintenanceBody, request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.set_maintenance(owner, body.active, body.notice, body.confirm))


@router.get("/quota-policy", summary="Standard-account limits")
def get_quota(request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.quota_policy(owner))


@router.put("/quota-policy/{key}", summary="Change a standard-account limit")
def put_quota(key: str, body: QuotaBody, request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.set_quota(owner, key, body.value))


@router.delete("/quota-policy/{key}", summary="Return a limit to its configured default")
def delete_quota(key: str, request: Request, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.reset_quota(owner, key))


@router.get("/events", summary="Owner actions taken in Phase 7 areas")
def events(request: Request, limit: int = 25, offset: int = 0, owner: Principal = Depends(require_owner), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.owner.events(owner, limit, offset))
