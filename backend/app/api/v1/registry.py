"""Model Registry API. Thin: authenticate, check CSRF, call one service method. Ownership, sharing, confirmation and lifecycle rules live in RegistryService."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictBool

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["registry"], dependencies=[Depends(enforce_csrf)])


class RegisterBody(BaseModel):
    source_model_id: str = Field(max_length=64)
    name: Optional[str] = Field(default=None, max_length=120)
    description: Optional[str] = Field(default=None, max_length=1000)
    model_id: Optional[str] = Field(default=None, max_length=64, description="Add a new version to this registry model")
    note: Optional[str] = Field(default=None, max_length=300)


class TransitionBody(BaseModel):
    to: str = Field(max_length=20)
    confirm: StrictBool = False
    reason: Optional[str] = Field(default=None, max_length=300)


class RollbackBody(BaseModel):
    to_version_id: str = Field(max_length=64)
    confirm: StrictBool = False
    reason: Optional[str] = Field(default=None, max_length=300)


class CompareBody(BaseModel):
    version_ids: List[str] = Field(max_length=6)


class PredictBody(BaseModel):
    values: Dict[str, Any] = Field(max_length=200)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/projects/{project_id}/registry/models", status_code=201, summary="Register a trained Phase 5 model (or add a new version of a registered one)")
def register(project_id: str, body: RegisterBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    p7.ws.projects.get_owned(principal, project_id, writable=True)
    return _ok(request, p7.registry.register(principal, body.source_model_id, name=body.name, description=body.description, model_id=body.model_id, note=body.note))


@router.get("/registry/models", summary="Registered models you own, and those shared with you")
def list_models(request: Request, project_id: Optional[str] = None, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.list(principal, project_id, limit=limit, offset=offset))


@router.post("/registry/compare", summary="Compare versions with metrics that fit the problem type (validation and hold-out shown separately)")
def compare(body: CompareBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.compare(principal, body.version_ids))


@router.get("/registry/models/{model_id}", summary="One registered model with its versions")
def get_model(model_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.get(principal, model_id))


@router.get("/registry/models/{model_id}/history", summary="Lifecycle history: every transition, who made it and why")
def history(model_id: str, request: Request, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.history(principal, model_id, limit=limit, offset=offset))


@router.post("/registry/models/{model_id}/rollback", summary="Make an earlier version the active one again (needs confirm=true)")
def rollback(model_id: str, body: RollbackBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.rollback(principal, model_id, body.to_version_id, confirm=body.confirm, reason=body.reason))


@router.get("/registry/versions/{version_id}", summary="One version: metadata, evaluation, lineage and artifact checksum")
def get_version(version_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.get_version(principal, version_id))


@router.post("/registry/versions/{version_id}/verify", summary="Re-check the stored artifact against its checksum")
def verify(version_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.verify(principal, version_id))


@router.post("/registry/versions/{version_id}/transition", summary="Move a version through its lifecycle (only valid transitions; some need confirm=true)")
def transition(version_id: str, body: TransitionBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.transition(principal, version_id, body.to, confirm=body.confirm, reason=body.reason))


@router.post("/registry/versions/{version_id}/predict", summary="Predict with this version; the answer names the version that produced it")
def predict(version_id: str, body: PredictBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.predict(principal, version_id, body.values))


@router.get("/registry/versions/{version_id}/predictions", summary="Predictions made with this version")
def predictions(version_id: str, request: Request, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.registry.predictions(principal, version_id, limit=limit, offset=offset))
