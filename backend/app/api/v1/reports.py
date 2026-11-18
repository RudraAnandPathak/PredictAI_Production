"""Reports API: templates, creation from saved results (no research or calculation is repeated), editing with versions, staleness, exports and downloads."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["reports"], dependencies=[Depends(enforce_csrf)])


class CreateBody(BaseModel):
    source_kind: str = Field(max_length=20)
    source: Dict[str, Any] = Field(default_factory=dict, max_length=10)
    template: Optional[str] = Field(default=None, max_length=60)
    title: Optional[str] = Field(default=None, max_length=200)
    settings: Optional[Dict[str, Any]] = Field(default=None, max_length=20)


class EditBody(BaseModel):
    base_version: int
    ops: List[Dict[str, Any]] = Field(max_length=50)
    note: Optional[str] = Field(default=None, max_length=300)


class ExportBody(BaseModel):
    format: str = Field(max_length=10)
    version: Optional[int] = None
    style: Optional[str] = Field(default=None, max_length=20)


class TemplateBody(BaseModel):
    name: str = Field(max_length=80)
    spec: Dict[str, Any] = Field(max_length=10)
    report_type: Optional[str] = Field(default=None, max_length=30)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/reports/templates", summary="Built-in and your own report templates, section keys and citation styles")
def templates(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.templates(principal))


@router.post("/reports/templates", status_code=201, summary="Save a template of your own")
def create_template(body: TemplateBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.create_template(principal, name=body.name, spec=body.spec, report_type=body.report_type))


@router.post("/projects/{project_id}/reports", status_code=201, summary="Create a report from saved research, analysis runs or model versions")
def create(project_id: str, body: CreateBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.create(principal, project_id, source_kind=body.source_kind, source=body.source, template=body.template, title=body.title, settings=body.settings))


@router.get("/reports", summary="Your reports, and reports shared with you")
def list_reports(request: Request, project_id: Optional[str] = None, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.list(principal, project_id, limit=limit, offset=offset))


@router.get("/reports/{report_id}", summary="A report version with its evidence, readiness and rendered view")
def get_report(report_id: str, request: Request, version: Optional[int] = None, style: Optional[str] = None, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.get(principal, report_id, version, style=style))


@router.patch("/reports/{report_id}", summary="Edit a report: creates a new version, never changes an old one")
def edit(report_id: str, body: EditBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.edit(principal, report_id, body.base_version, body.ops, note=body.note))


@router.delete("/reports/{report_id}", summary="Delete a report (owner only)")
def delete(report_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.delete(principal, report_id))


@router.get("/reports/{report_id}/versions", summary="Every version with author, note, readiness and exports")
def versions(report_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.versions(principal, report_id))


@router.get("/reports/{report_id}/diff", summary="What changed between two versions")
def diff(report_id: str, a: int, b: int, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.diff(principal, report_id, a, b))


@router.post("/reports/{report_id}/exports", status_code=201, summary="Export a version as pdf, docx, pptx, md, csv, xlsx or png")
def export(report_id: str, body: ExportBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.export(principal, report_id, body.format, version=body.version, style=body.style))


@router.get("/reports/{report_id}/exports", summary="Exports made from this report")
def exports(report_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.reports.exports(principal, report_id))


@router.get("/report-exports/{export_id}/download", summary="Download an export (access is checked again on every download)")
def download(export_id: str, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> Response:
    name, mime, data = p7.reports.download(principal, export_id)
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=mime, headers=headers)
