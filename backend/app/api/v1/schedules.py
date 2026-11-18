"""Scheduled report delivery API: create, pause, resume, end, run now, history, manual retry. Adapter status is reported honestly (email and Slack are off until credentials exist)."""
from __future__ import annotations

from typing import Any, Dict, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["schedules"], dependencies=[Depends(enforce_csrf)])


class ScheduleBody(BaseModel):
    spec: Dict[str, Any] = Field(max_length=20)


class ActionBody(BaseModel):
    action: str = Field(max_length=10)


class RunBody(BaseModel):
    idempotency_key: Optional[str] = Field(default=None, max_length=80)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/schedules/adapters", summary="Which delivery methods are available on this server")
def adapters(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.adapters_status(principal))


@router.post("/reports/{report_id}/schedules", status_code=201, summary="Schedule a report: once, daily, weekly or monthly, in your time zone")
def create(report_id: str, body: ScheduleBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.create(principal, report_id, body.spec))


@router.get("/schedules", summary="Your schedules")
def list_schedules(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.list(principal))


@router.get("/schedules/{schedule_id}", summary="One schedule")
def get(schedule_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.get(principal, schedule_id))


@router.post("/schedules/{schedule_id}/action", summary="Pause, resume or end a schedule")
def action(schedule_id: str, body: ActionBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.set_status(principal, schedule_id, body.action))


@router.post("/schedules/{schedule_id}/run", status_code=201, summary="Run now (the same idempotency key never runs twice)")
def run_now(schedule_id: str, body: RunBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.run_now(principal, schedule_id, idempotency_key=body.idempotency_key))


@router.get("/schedules/{schedule_id}/runs", summary="Delivery history: every run and every delivery with attempts and errors")
def runs(schedule_id: str, request: Request, limit: int = 25, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.runs(principal, schedule_id, limit=limit, offset=offset))


@router.post("/schedule-deliveries/{delivery_id}/retry", summary="Retry a failed or dead-lettered delivery once, now")
def retry(delivery_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.schedule.retry_delivery(principal, delivery_id))


@router.get("/schedule-runs/{run_id}/download", summary="Download the export a run produced")
def download(run_id: str, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> Response:
    name, mime, data = p7.schedule.download(principal, run_id)
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=mime, headers=headers)
