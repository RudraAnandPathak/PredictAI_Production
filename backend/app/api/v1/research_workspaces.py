"""Research workspace foundation API (Phase 8). Thin: authenticate, check CSRF, call one service method. Ownership and limits live in ResearchSpaceService."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.identity.container import Services
from ...modules.researchspace.service import ResearchSpaceService
from ..deps import enforce_csrf, get_services, require_principal

router = APIRouter(prefix="/research-workspaces", tags=["research-workspaces"], dependencies=[Depends(enforce_csrf)])


class CreateBody(BaseModel):
    name: str = Field(max_length=300)
    description: Optional[str] = Field(default=None, max_length=3000)


class UpdateBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=3000)
    status: Optional[str] = Field(default=None, max_length=20)


def _svc(services: Services = Depends(get_services)) -> ResearchSpaceService:
    return ResearchSpaceService(services.ctx)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/limits", summary="Your research limits (workspaces, sources per workspace, supported source kinds)")
def limits(request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.limits(principal))


@router.get("", summary="Your research workspaces")
def list_workspaces(request: Request, status: Optional[str] = None, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.list(principal, status))


@router.post("", status_code=201, summary="Create a research workspace")
def create(body: CreateBody, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.create(principal, body.name, body.description))


@router.get("/{workspace_id}", summary="One research workspace you own")
def get(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.get(principal, workspace_id))


@router.patch("/{workspace_id}", summary="Rename, describe, archive or restore")
def update(workspace_id: str, body: UpdateBody, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.update(principal, workspace_id, name=body.name, description=body.description, status=body.status))


@router.delete("/{workspace_id}", summary="Delete a research workspace and everything in it")
def delete(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_svc)) -> dict:
    return _ok(request, svc.delete(principal, workspace_id))
