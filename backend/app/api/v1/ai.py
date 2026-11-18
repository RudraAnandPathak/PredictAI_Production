"""AI provider routing API. Everyone can see which providers exist and what leaves the platform; only an owner can configure them. PredictAI works with no provider at all."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.research.container import ResearchServices
from ..deps import enforce_csrf, get_research, require_principal

router = APIRouter(tags=["ai"], dependencies=[Depends(enforce_csrf)])


class ProviderBody(BaseModel):
    key: Optional[str] = Field(default=None, max_length=40)
    kind: Optional[str] = Field(default=None, max_length=30)
    label: Optional[str] = Field(default=None, max_length=120)
    base_url: Optional[str] = Field(default=None, max_length=500)
    model: Optional[str] = Field(default=None, max_length=120)
    embed_model: Optional[str] = Field(default=None, max_length=120)
    tasks: Optional[List[str]] = Field(default=None, max_length=12)
    priority: Optional[int] = None
    timeout_s: Optional[int] = None
    max_retries: Optional[int] = None
    secret_env: Optional[str] = Field(default=None, max_length=60)
    paid: Optional[bool] = None
    daily_budget_units: Optional[int] = None
    notes: Optional[str] = Field(default=None, max_length=500)


class EnableBody(BaseModel):
    enabled: bool
    confirm_paid: bool = False


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


def _fields(body: ProviderBody) -> Dict[str, Any]:
    return {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}


@router.get("/ai/providers", summary="AI providers, what each may do, and whether data leaves the platform")
def list_providers(request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.list(principal))


@router.post("/ai/providers", status_code=201, summary="Add a provider (owner only; starts disabled)")
def create_provider(body: ProviderBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.create(principal, _fields(body)))


@router.patch("/ai/providers/{key}", summary="Change a provider (owner only)")
def update_provider(key: str, body: ProviderBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.update(principal, key, _fields(body)))


@router.post("/ai/providers/{key}/enabled", summary="Enable or disable a provider (owner only; a paid provider needs explicit confirmation)")
def set_enabled(key: str, body: EnableBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.set_enabled(principal, key, body.enabled, confirm_paid=body.confirm_paid))


@router.delete("/ai/providers/{key}", summary="Remove a provider (owner only)")
def delete_provider(key: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.delete(principal, key))


@router.post("/ai/providers/{key}/probe", summary="Check that a provider answers and which abilities it has (owner only)")
def probe(key: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.probe(principal, key))


@router.get("/ai/usage", summary="Provider usage and errors (counts only; owner only)")
def usage(request: Request, days: int = 7, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.providers.usage(principal, days))
