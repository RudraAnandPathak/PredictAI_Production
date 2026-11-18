"""Liveness endpoint. Reports that the API process is up; it checks no dependencies yet."""
from __future__ import annotations

import logging

import time
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ... import __version__
from ...core.envelope import success
from ...core.errors import AppError
from ...core.request_context import request_id_from_scope

router = APIRouter(tags=["system"])


class HealthData(BaseModel):
    status: Literal["ok"]
    service: str
    version: str
    environment: str
    timestamp: str
    uptime_seconds: float


class ResponseMeta(BaseModel):
    request_id: str


class HealthResponse(BaseModel):
    data: HealthData
    meta: ResponseMeta


@router.get("/health", response_model=HealthResponse, summary="Liveness check")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    started_at = request.app.state.started_at
    data = {
        "status": "ok",
        "service": settings.service_name,
        "version": __version__,
        "environment": settings.environment,
        "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "uptime_seconds": round(time.monotonic() - started_at, 1),
    }
    return success(data, request_id_from_scope(request.scope))


@router.get("/health/ready", summary="Readiness check (database reachable and schema current)")
def ready(request: Request) -> dict:
    state = request.app.state
    backend = getattr(state.db, "backend", "sqlite")
    try:
        state.db.ensure_ready(auto_migrate=state.settings.auto_migrate)
        with state.db.session() as conn:
            conn.execute("SELECT 1").fetchone()
    except Exception as e:                                              # the reason goes to the server log (driver messages have the token removed), never to the caller
        logging.getLogger("predictai.health").warning("Readiness failed (%s database): %s: %s", backend, type(e).__name__, str(e)[:300])
        raise AppError("NOT_READY", "The service is running but not ready to serve requests.", 503)
    return success({"status": "ok", "checks": {"database": "ok", "database_backend": backend}}, request_id_from_scope(request.scope))
