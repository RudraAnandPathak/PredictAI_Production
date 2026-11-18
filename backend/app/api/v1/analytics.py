"""Analytics API: descriptive and relationship analysis, statistical tests, insights, the assistant, charts, drill-down and export.

Thin by design: every handler authenticates, checks CSRF (router dependency), and calls one service method. Ownership, validation,
limits, evidence and auditing all live in the service layer."""
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

router = APIRouter(tags=["analytics"], dependencies=[Depends(enforce_csrf)])


class RunBody(BaseModel):
    kind: str = Field(max_length=30)
    operations: List[Dict[str, Any]] = Field(max_length=40)
    filters: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=20)
    null_policy: Optional[str] = Field(default=None, max_length=12)
    seed: Optional[int] = None
    sample_rows: Optional[int] = None
    version_id: Optional[str] = Field(default=None, max_length=64)
    label: Optional[str] = Field(default=None, max_length=120)


class PresetBody(BaseModel):
    filters: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=20)
    version_id: Optional[str] = Field(default=None, max_length=64)


class HypothesisBody(BaseModel):
    kind: str = Field(max_length=30)
    outcome: Optional[str] = Field(default=None, max_length=300)
    group: Optional[str] = Field(default=None, max_length=300)
    second: Optional[str] = Field(default=None, max_length=300)
    alpha: Optional[float] = None
    alternative: Optional[str] = Field(default=None, max_length=12)
    prefer: Optional[str] = Field(default=None, max_length=14)
    reference_group: Optional[str] = Field(default=None, max_length=200)
    family_size: Optional[int] = None
    execute: bool = True
    filters: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=20)
    null_policy: Optional[str] = Field(default=None, max_length=12)
    version_id: Optional[str] = Field(default=None, max_length=64)


class SaveBody(BaseModel):
    saved: bool = True
    label: Optional[str] = Field(default=None, max_length=120)


class RowsBody(BaseModel):
    select: Optional[Dict[str, Any]] = None
    offset: int = 0
    limit: int = 50
    columns: Optional[List[str]] = Field(default=None, max_length=200)


class ExportBody(BaseModel):
    what: str = Field(max_length=10)
    select: Optional[Dict[str, Any]] = None
    index: Optional[int] = None


class InsightGenerate(BaseModel):
    filters: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=20)
    version_id: Optional[str] = Field(default=None, max_length=64)


class InsightState(BaseModel):
    state: str = Field(max_length=12)


class AskBody(BaseModel):
    question: str = Field(max_length=2000)
    conversation_id: Optional[str] = Field(default=None, max_length=64)
    choices: Optional[Dict[str, str]] = None
    use_ai: bool = False
    version_id: Optional[str] = Field(default=None, max_length=64)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


# ---------------------------------------------------------------------------- analyses
@router.get("/assets/{asset_id}/analytics/schema", summary="Columns, roles and the operations available for analysis")
def schema(asset_id: str, request: Request, version_id: Optional[str] = None, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.schema(principal, asset_id, version_id))


@router.post("/assets/{asset_id}/analytics/run", status_code=201, summary="Run a typed analysis plan")
def run(asset_id: str, body: RunBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.run(principal, asset_id, kind=body.kind, operations=body.operations, filters=body.filters, null_policy=body.null_policy, seed=body.seed,
                                         sample_rows=body.sample_rows, version_id=body.version_id, label=body.label))


@router.post("/assets/{asset_id}/analytics/presets/{name}", status_code=201, summary="Run a standard view: 'dashboard' or 'relationships'")
def preset(asset_id: str, name: str, body: PresetBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.preset(principal, asset_id, name, filters=body.filters, version_id=body.version_id))


@router.post("/assets/{asset_id}/analytics/hypothesis", status_code=201, summary="Turn a hypothesis into a structured test plan and (optionally) run it")
def hypothesis(asset_id: str, body: HypothesisBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    params = {k: v for k, v in body.model_dump().items() if k in ("kind", "outcome", "group", "second", "alpha", "alternative", "prefer", "reference_group", "family_size", "execute") and v is not None}
    return _ok(request, ws.analytics.run(principal, asset_id, kind="hypothesis", operations=[{"op": "hypothesis", "params": params}], filters=body.filters,
                                         null_policy=body.null_policy, version_id=body.version_id))


@router.get("/assets/{asset_id}/analytics/runs", summary="Saved and recent analyses for a dataset")
def list_runs(asset_id: str, request: Request, kind: Optional[str] = None, saved: bool = False, limit: int = 50, offset: int = 0,
              principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.list_runs(principal, asset_id, kind=kind, saved_only=saved, limit=limit, offset=offset))


@router.get("/analytics/runs/{run_id}", summary="One analysis with its full result and evidence")
def get_run(run_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.get_run(principal, run_id))


@router.post("/analytics/runs/{run_id}/save", summary="Keep (or un-keep) an analysis and give it a name")
def save_run(run_id: str, body: SaveBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.save_run(principal, run_id, saved=body.saved, label=body.label))


@router.post("/analytics/runs/{run_id}/rerun", status_code=201, summary="Re-run an analysis and check that the results are identical")
def rerun(run_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.rerun(principal, run_id))


@router.post("/analytics/runs/{run_id}/rows", summary="The rows behind a result, chart element or selection")
def rows(run_id: str, body: RowsBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.analytics.rows(principal, run_id, body.select, offset=body.offset, limit=body.limit, columns=body.columns))


@router.post("/analytics/runs/{run_id}/export", summary="Download an analysis result, its rows or one of its tables")
def export(run_id: str, body: ExportBody, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> Response:
    name, ctype, data = ws.analytics.export(principal, run_id, what=body.what, select=body.select, index=body.index)
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)),
               "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=ctype, headers=headers)


# ---------------------------------------------------------------------------- insights
@router.post("/assets/{asset_id}/insights/generate", status_code=201, summary="Generate insights from fresh, evidence-backed analyses")
def generate_insights(asset_id: str, body: InsightGenerate, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.insights.generate(principal, asset_id, filters=body.filters, version_id=body.version_id))


@router.get("/assets/{asset_id}/insights", summary="Insights for a dataset (new, saved or dismissed)")
def list_insights(asset_id: str, request: Request, state: Optional[str] = None, version_id: Optional[str] = None, limit: int = 50, offset: int = 0,
                  principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.insights.list(principal, asset_id, state=state, version_id=version_id, limit=limit, offset=offset))


@router.get("/insights/{insight_id}", summary="Inspect one insight with its evidence and lineage")
def get_insight(insight_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.insights.get(principal, insight_id))


@router.post("/insights/{insight_id}/state", summary="Save, dismiss or restore an insight")
def insight_state(insight_id: str, body: InsightState, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.insights.set_state(principal, insight_id, body.state))


# ---------------------------------------------------------------------------- assistant
@router.post("/assets/{asset_id}/assistant/ask", summary="Ask a question about the data in plain language")
def ask(asset_id: str, body: AskBody, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assistant.ask(principal, asset_id, body.question, conversation_id=body.conversation_id, choices=body.choices, use_ai=body.use_ai, version_id=body.version_id))


@router.get("/assets/{asset_id}/assistant/conversations", summary="Earlier conversations about a dataset")
def conversations(asset_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assistant.conversations(principal, asset_id))


@router.get("/assistant/conversations/{conversation_id}", summary="One conversation with its answers")
def conversation(conversation_id: str, request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assistant.conversation(principal, conversation_id))


@router.get("/assistant/provider", summary="Whether an optional AI explanation provider is configured, and what it would receive")
def provider(request: Request, principal: Principal = Depends(require_principal), ws: WorkspaceServices = Depends(get_workspace)) -> dict:
    return _ok(request, ws.assistant.provider())
