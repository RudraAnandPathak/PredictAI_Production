"""Advanced Intelligence API: root-cause exploration, metric explanations, scenario analysis and causal-method candidates. Results are stored and cannot be edited."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["intelligence"], dependencies=[Depends(enforce_csrf)])


class RootBody(BaseModel):
    outcome: str = Field(max_length=200)
    top_k: int = Field(default=8, ge=1, le=20)
    question: Optional[str] = Field(default=None, max_length=300)
    version_id: Optional[str] = Field(default=None, max_length=64)


class MetricBody(BaseModel):
    asset_id: Optional[str] = Field(default=None, max_length=64)
    column: Optional[str] = Field(default=None, max_length=200)
    statistic: str = Field(default="mean", max_length=10)
    by: Optional[str] = Field(default=None, max_length=200)
    model_version_id: Optional[str] = Field(default=None, max_length=64)
    metric: Optional[str] = Field(default=None, max_length=30)
    version_id: Optional[str] = Field(default=None, max_length=64)


class ScenarioBody(BaseModel):
    model_id: Optional[str] = Field(default=None, max_length=64)
    version_id: Optional[str] = Field(default=None, max_length=64)
    baseline: Dict[str, Any] = Field(max_length=200)
    scenarios: List[Dict[str, Any]] = Field(max_length=8)
    sensitivity: Optional[Dict[str, Any]] = Field(default=None, max_length=5)
    label: Optional[str] = Field(default=None, max_length=120)


class CausalBody(BaseModel):
    treatment: str = Field(max_length=200)
    outcome: str = Field(max_length=200)
    covariates: List[str] = Field(default_factory=list, max_length=12)
    time_column: Optional[str] = Field(default=None, max_length=200)
    group_column: Optional[str] = Field(default=None, max_length=200)
    seed: int = 42
    version_id: Optional[str] = Field(default=None, max_length=64)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/assets/{asset_id}/intelligence/root-cause", status_code=201, summary="Which columns go with an outcome (associations, never causes)")
def root_cause(asset_id: str, body: RootBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.root_cause(principal, asset_id, outcome=body.outcome, top_k=body.top_k, question=body.question, version_id=body.version_id))


@router.post("/intelligence/metric", status_code=201, summary="Explain a column's figure or a model's score in plain words")
def metric(body: MetricBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.explain_metric(principal, body.asset_id, column=body.column, statistic=body.statistic, by=body.by, model_version_id=body.model_version_id, metric=body.metric, version_id=body.version_id))


@router.post("/intelligence/scenarios", status_code=201, summary="Compare a baseline with changed inputs on a trained model; the baseline is kept")
def scenario(body: ScenarioBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.scenario(principal, body.model_id, version_id=body.version_id, baseline=body.baseline, scenarios=body.scenarios, sensitivity=body.sensitivity, label=body.label))


@router.post("/assets/{asset_id}/intelligence/causal-candidates", status_code=201, summary="Which causal methods could apply, their assumptions, and association estimates")
def causal(asset_id: str, body: CausalBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.causal_candidates(principal, asset_id, treatment=body.treatment, outcome=body.outcome, covariates=body.covariates, time_column=body.time_column, group_column=body.group_column, seed=body.seed, version_id=body.version_id))


@router.get("/intelligence/runs", summary="Your stored runs, and runs shared with you")
def runs(request: Request, project_id: Optional[str] = None, kind: Optional[str] = None, limit: int = 25, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.list_runs(principal, project_id, kind=kind, limit=limit, offset=offset))


@router.get("/intelligence/runs/{run_id}", summary="One stored run with its five registers and reproduction details")
def run(run_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.intelligence.get_run(principal, run_id))
