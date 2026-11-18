"""Model monitoring API. Thin: authenticate, check CSRF, call one MonitoringService method. Only the model's owner may use it."""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["monitoring"], dependencies=[Depends(enforce_csrf)])


class DriftBody(BaseModel):
    asset_id: str = Field(max_length=64, description="The newer dataset to compare with the training data")
    dataset_version_id: Optional[str] = Field(default=None, max_length=64)


class OutcomeBody(BaseModel):
    prediction_id: str = Field(max_length=64)
    actual: Any = Field(description="What really happened: a class name or a number")


class ForecastBody(BaseModel):
    date: str = Field(max_length=200)
    measure: Optional[str] = Field(default=None, max_length=200)
    agg: str = Field(default="mean", max_length=10)
    freq: str = Field(default="auto", max_length=10)
    horizon: int = Field(default=6, ge=1, le=24)
    version_id: Optional[str] = Field(default=None, max_length=64)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/registry/versions/{version_id}/monitoring", summary="Monitoring summary: last drift check, outcome-based performance, and what monitoring does not do")
def summary(version_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.summary(principal, version_id))


@router.post("/registry/versions/{version_id}/monitoring/drift", status_code=201, summary="Compare a newer dataset with the data this version was trained on")
def check_drift(version_id: str, body: DriftBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.check_drift(principal, version_id, body.asset_id, body.dataset_version_id))


@router.get("/registry/versions/{version_id}/monitoring/drift", summary="Earlier drift checks for this version")
def list_checks(version_id: str, request: Request, limit: int = 25, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.checks(principal, version_id, limit=limit, offset=offset))


@router.get("/monitoring/checks/{check_id}", summary="One stored drift check")
def get_check(check_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.get_check(principal, check_id))


@router.post("/registry/versions/{version_id}/monitoring/outcomes", status_code=201, summary="Record what really happened for a stored single prediction")
def record_outcome(version_id: str, body: OutcomeBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.record_outcome(principal, version_id, body.prediction_id, body.actual))


@router.get("/registry/versions/{version_id}/monitoring/performance", summary="Performance from recorded outcomes compared with the hold-out score")
def performance(version_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.monitoring.performance(principal, version_id))


@router.post("/assets/{asset_id}/forecast", summary="Forecast a measure over time, with a backtest, intervals and the limits of the method")
def forecast(asset_id: str, body: ForecastBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.forecast.run(principal, asset_id, body.date, body.measure, agg=body.agg, freq=body.freq, horizon=body.horizon, version_id=body.version_id))
