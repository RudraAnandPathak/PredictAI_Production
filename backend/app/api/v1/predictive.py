"""Predictive-modelling API: dataset preparation, AutoML training jobs, models, evaluation reports, predictions and comparisons.

Thin by design: every handler authenticates, checks CSRF (router dependency) and calls one service method. Ownership, validation, limits, background
execution and auditing live in the service layer, which reaches datasets only through the workspace's single authorisation path."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.workspace.container import WorkspaceServices
from ..deps import enforce_csrf, get_workspace, require_principal

router = APIRouter(tags=["predictive"], dependencies=[Depends(enforce_csrf)])


class ConfigBody(BaseModel):
    config: Dict[str, Any] = Field(default_factory=dict)
    version_id: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=120)


class RenameBody(BaseModel):
    name: str = Field(max_length=120)


class CompareBody(BaseModel):
    ids: List[str] = Field(max_length=6)


class PredictBody(BaseModel):
    values: Dict[str, Any] = Field(max_length=400)
    explain: bool = True
    save: bool = True


class BatchBody(BaseModel):
    dataset_asset_id: str = Field(max_length=64)
    version_id: Optional[str] = Field(default=None, max_length=64)


class WhatIfBody(BaseModel):
    values: Dict[str, Any] = Field(max_length=200)
    scenarios: List[Dict[str, Any]] = Field(max_length=5)


class PdpBody(BaseModel):
    feature: str = Field(max_length=300)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


# ---------------------------------------------------------------------------- catalogue and preparation
@router.get("/ml/catalog", summary="Algorithms, options, metrics and limits available for predictive modelling")
def catalog(request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.catalog(principal))


@router.get("/assets/{asset_id}/ml/schema", summary="Columns of a dataset and which can be predicted")
def schema(asset_id: str, request: Request, version_id: Optional[str] = None, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.schema(principal, asset_id, version_id))


@router.post("/assets/{asset_id}/ml/assess", summary="Check whether a dataset suits a prediction objective and preview the preparation (nothing is changed)")
def assess(asset_id: str, body: ConfigBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.assess(principal, asset_id, body.config, body.version_id))


# ---------------------------------------------------------------------------- training jobs
@router.post("/assets/{asset_id}/ml/jobs", status_code=201, summary="Start AutoML training in the background")
def start(asset_id: str, body: ConfigBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.start_training(principal, asset_id, body.config, body.version_id, body.label))


@router.get("/assets/{asset_id}/ml/jobs", summary="Training jobs for a dataset")
def list_jobs(asset_id: str, request: Request, limit: int = 30, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.list_jobs(principal, asset_id, limit))


@router.get("/ml/jobs/{job_id}", summary="Progress and outcome of one training job")
def get_job(job_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.get_job(principal, job_id))


@router.post("/ml/jobs/{job_id}/cancel", summary="Cancel a queued or running training job")
def cancel(job_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.cancel_job(principal, job_id))


# ---------------------------------------------------------------------------- models
@router.get("/ml/models", summary="Your saved models (optionally for one dataset)")
def list_models(request: Request, asset_id: Optional[str] = None, target: Optional[str] = None, limit: int = 50, offset: int = 0,
                principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.list_models(principal, asset_id, target=target, limit=limit, offset=offset))


@router.post("/ml/models/compare", summary="Compare saved models that predict the same target from the same dataset")
def compare(body: CompareBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.compare_models(principal, body.ids))


@router.get("/ml/models/{model_id}", summary="One model with its configuration, versions and training history")
def get_model(model_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.get_model(principal, model_id))


@router.patch("/ml/models/{model_id}", summary="Rename a model")
def rename(model_id: str, body: RenameBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.rename_model(principal, model_id, body.name))


@router.delete("/ml/models/{model_id}", summary="Delete a model, its report and its prediction history")
def delete(model_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.delete_model(principal, model_id))


@router.get("/ml/models/{model_id}/report", summary="Full training report: ranking, evaluation, importance, curves, with an Explanation for each")
def report(model_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.report(principal, model_id))


@router.post("/ml/models/{model_id}/partial-dependence", summary="How the prediction changes as one input changes (other inputs held at typical values)")
def partial_dependence(model_id: str, body: PdpBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.partial_dependence(principal, model_id, body.feature))


# ---------------------------------------------------------------------------- predictions
@router.post("/ml/models/{model_id}/predict", status_code=201, summary="Predict one case from values entered by hand")
def predict(model_id: str, body: PredictBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.predict(principal, model_id, body.values, explain_local=body.explain, save=body.save))


@router.post("/ml/models/{model_id}/predict-batch", status_code=201, summary="Predict every row of a dataset in the same project")
def predict_batch(model_id: str, body: BatchBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.predict_batch(principal, model_id, body.dataset_asset_id, body.version_id))


@router.get("/ml/models/{model_id}/predictions", summary="Prediction history of a model")
def list_predictions(model_id: str, request: Request, kind: Optional[str] = None, limit: int = 50, offset: int = 0,
                     principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.list_predictions(principal, model_id, kind=kind, limit=limit, offset=offset))


@router.get("/ml/predictions/{prediction_id}", summary="One saved prediction")
def get_prediction(prediction_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.get_prediction(principal, prediction_id))


@router.get("/ml/predictions/{prediction_id}/download", summary="Download the CSV of a batch prediction")
def download(prediction_id: str, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> Response:
    name, ctype, data = ws.predictive.download_prediction(principal, prediction_id)
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=ctype, headers=headers)


@router.post("/ml/models/{model_id}/what-if", summary="Compare the model's answer with its answer when some inputs change (nothing is saved)")
def what_if(model_id: str, body: WhatIfBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.predictive.what_if(principal, model_id, body.values, body.scenarios))
