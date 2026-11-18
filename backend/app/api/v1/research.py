"""Research API: consent settings, sessions and plans, approval, background runs, evidence bundles, citation inspection, sources, notes, search, comparison, export and cache.

Thin by design. Every handler authenticates, the router checks CSRF for state-changing requests, and one service method does the work. Ownership, project isolation, consent,
rate limits and auditing live in the service layer; nothing here reaches the network."""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field, StrictBool

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.research.container import ResearchServices
from ..deps import enforce_csrf, get_research, require_principal

router = APIRouter(tags=["research"], dependencies=[Depends(enforce_csrf)])


class SettingsBody(BaseModel):
    project_id: Optional[str] = Field(default=None, max_length=64)
    web_enabled: Optional[StrictBool] = None
    providers: Optional[List[str]] = Field(default=None, max_length=10)
    external_ai: Optional[StrictBool] = None
    news_enabled: Optional[StrictBool] = None
    freshness_days: Optional[int] = None
    cache_days: Optional[int] = None


class SessionBody(BaseModel):
    question: str = Field(max_length=2000)
    level: str = Field(default="topic", max_length=30)
    title: Optional[str] = Field(default=None, max_length=300)
    purpose: str = Field(default="general", max_length=20)
    options: Dict[str, Any] = Field(default_factory=dict, max_length=20)


class SessionPatch(BaseModel):
    question: Optional[str] = Field(default=None, max_length=2000)
    level: Optional[str] = Field(default=None, max_length=30)
    title: Optional[str] = Field(default=None, max_length=300)
    purpose: Optional[str] = Field(default=None, max_length=20)
    options: Optional[Dict[str, Any]] = Field(default=None, max_length=20)
    saved: Optional[StrictBool] = None


class FollowUpBody(BaseModel):
    question: str = Field(max_length=2000)
    level: Optional[str] = Field(default=None, max_length=30)
    title: Optional[str] = Field(default=None, max_length=300)
    options: Dict[str, Any] = Field(default_factory=dict, max_length=20)


class ProviderBody(BaseModel):
    provider: Optional[str] = Field(default=None, max_length=30)


class ProviderSearchBody(BaseModel):
    provider: str = Field(max_length=30)
    query: str = Field(max_length=400)
    limit: int = Field(default=8, ge=1, le=10)


class ProviderOpenBody(BaseModel):
    provider: str = Field(max_length=30)
    ref: Dict[str, Any] = Field(default_factory=dict, max_length=6)


class ApproveBody(BaseModel):
    plan_hash: str = Field(max_length=100)


class RunBody(BaseModel):
    approve_plan_hash: Optional[str] = Field(default=None, max_length=100)


class NoteBody(BaseModel):
    target_type: str = Field(default="session", max_length=20)
    target_id: Optional[str] = Field(default=None, max_length=64)
    body: str = Field(max_length=20000)


class NotePatch(BaseModel):
    body: str = Field(max_length=20000)


class BundlePatch(BaseModel):
    saved: Optional[StrictBool] = None
    label: Optional[str] = Field(default=None, max_length=200)


class SourcePatch(BaseModel):
    saved: StrictBool


class ContextBody(BaseModel):
    question: str = Field(max_length=2000)
    level: str = Field(default="topic", max_length=30)
    options: Dict[str, Any] = Field(default_factory=dict, max_length=20)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


# ---------------------------------------------------------------------------- consent and capabilities
@router.get("/research/settings", summary="Your web-research and AI consent settings (everything outbound is off until you switch it on)")
def get_settings(request: Request, project_id: Optional[str] = None, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.settings(principal, project_id))


@router.patch("/research/settings", summary="Change your settings, or a project's settings (a project can only narrow what you allowed)")
def put_settings(body: SettingsBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    data = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set and k != "project_id"}
    return _ok(request, rs.research.put_settings(principal, body.project_id, data))


@router.get("/projects/{project_id}/research/capabilities", summary="Depth levels, providers and what is allowed for this project right now")
def capabilities(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.capabilities(principal, project_id))


# ---------------------------------------------------------------------------- sessions
@router.post("/projects/{project_id}/research/sessions", status_code=201, summary="Create a research session with a plan (nothing is run or sent)")
def create_session(project_id: str, body: SessionBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.create_session(principal, project_id, body.model_dump()))


@router.post("/research/sessions/{session_id}/follow-up", status_code=201, summary="Ask a follow-up question that builds on an earlier one (its own plan, to approve before anything is sent)")
def follow_up(session_id: str, body: FollowUpBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.create_follow_up(principal, session_id, body.model_dump()))


@router.post("/projects/{project_id}/research/providers/diagnose", summary="Test Wikipedia, arXiv and the web search you have switched on (fixed test words only)")
def diagnose_providers(project_id: str, body: ProviderBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.diagnose_providers(principal, project_id, body.model_dump()))


@router.post("/projects/{project_id}/research/providers/search", summary="Search Wikipedia, arXiv or the web on its own (the exact phrase you type is sent)")
def provider_search(project_id: str, body: ProviderSearchBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.provider_search(principal, project_id, body.model_dump()))


@router.post("/projects/{project_id}/research/providers/open", summary="Open one Wikipedia article, arXiv paper or web page, section by section")
def provider_open(project_id: str, body: ProviderOpenBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.provider_open(principal, project_id, body.model_dump()))


@router.get("/projects/{project_id}/research/sessions", summary="Research history for a project")
def list_sessions(project_id: str, request: Request, status: Optional[str] = None, saved: Optional[bool] = None, limit: int = 50, offset: int = 0,
                  principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.list_sessions(principal, project_id, status=status, saved=saved, limit_=limit, offset=offset))


@router.post("/projects/{project_id}/research/context", summary="What this question would draw on from the project (names and counts only)")
def context_preview(project_id: str, body: ContextBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.context_preview(principal, project_id, body.model_dump()))


@router.get("/projects/{project_id}/research/search", summary="Search earlier research in this project")
def search(project_id: str, q: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.search(principal, project_id, q))


@router.get("/projects/{project_id}/research/sources", summary="Sources used by this project's research")
def list_sources(project_id: str, request: Request, kind: Optional[str] = None, status: Optional[str] = None, saved: Optional[bool] = None, limit: int = 100, offset: int = 0,
                 principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.list_sources(principal, project_id, kind=kind, status=status, saved=saved, limit_=limit, offset=offset))


@router.get("/projects/{project_id}/research/bundles", summary="Evidence bundles in this project")
def list_bundles(project_id: str, request: Request, session_id: Optional[str] = None, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.list_bundles(principal, project_id, session_id))


@router.get("/projects/{project_id}/research/jobs", summary="Research and background jobs in this project")
def list_jobs(project_id: str, request: Request, session_id: Optional[str] = None, kind: Optional[str] = None, principal: Principal = Depends(require_principal),
              rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.jobs(principal, project_id, session_id=session_id, kind=kind))


@router.get("/projects/{project_id}/research/cache", summary="What the web cache holds for this project")
def cache_stats(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.cache_stats(principal, project_id))


@router.delete("/projects/{project_id}/research/cache", summary="Clear this project's web cache")
def cache_clear(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.cache_clear(principal, project_id))


@router.get("/research/sessions/{session_id}", summary="One research session with its plan")
def get_session(session_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.get_session(principal, session_id))


@router.patch("/research/sessions/{session_id}", summary="Rename, save, or change the question, depth or options (the plan is rebuilt and must be approved again)")
def update_session(session_id: str, body: SessionPatch, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    data = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}
    return _ok(request, rs.research.update_session(principal, session_id, data))


@router.delete("/research/sessions/{session_id}", summary="Delete a research session, its bundles and saved page copies")
def delete_session(session_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.delete_session(principal, session_id))


@router.post("/research/sessions/{session_id}/approve", summary="Approve the exact web searches in the plan")
def approve(session_id: str, body: ApproveBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.approve_plan(principal, session_id, body.plan_hash))


@router.post("/research/sessions/{session_id}/run", status_code=202, summary="Start the research in the background")
def run(session_id: str, body: RunBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.run(principal, session_id, body.approve_plan_hash))


@router.get("/research/sessions/{session_id}/notes", summary="Your notes on this research")
def list_notes(session_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.list_notes(principal, session_id))


@router.post("/research/sessions/{session_id}/notes", status_code=201, summary="Add a note (your own words)")
def add_note(session_id: str, body: NoteBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.add_note(principal, session_id, body.model_dump()))


@router.patch("/research/notes/{note_id}", summary="Edit a note")
def update_note(note_id: str, body: NotePatch, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.update_note(principal, note_id, body.model_dump()))


@router.delete("/research/notes/{note_id}", summary="Delete a note")
def delete_note(note_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.delete_note(principal, note_id))


# ---------------------------------------------------------------------------- jobs
@router.get("/research/jobs/{job_id}", summary="Progress of a background research job")
def get_job(job_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.job(principal, job_id))


@router.post("/research/jobs/{job_id}/cancel", summary="Cancel a running or queued job")
def cancel_job(job_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.cancel(principal, job_id))


# ---------------------------------------------------------------------------- bundles and sources
@router.get("/research/compare", summary="What changed between two research runs")
def compare(a: str, b: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.compare(principal, a, b))


@router.get("/research/bundles/{bundle_id}", summary="An evidence bundle: sources, passages, citations, conflicts, conclusions, quality checks and an Explanation")
def get_bundle(bundle_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.get_bundle(principal, bundle_id))


@router.patch("/research/bundles/{bundle_id}", summary="Save or label a bundle")
def patch_bundle(bundle_id: str, body: BundlePatch, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.set_bundle(principal, bundle_id, {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}))


@router.delete("/research/bundles/{bundle_id}", summary="Delete a bundle")
def delete_bundle(bundle_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.delete_bundle(principal, bundle_id))


@router.get("/research/bundles/{bundle_id}/summary", summary="The labelled conclusions other parts of the platform may reuse")
def bundle_summary(bundle_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.summary(principal, bundle_id))


@router.get("/research/bundles/{bundle_id}/evidence/{evidence_id}", summary="Read a cited passage again from its saved source and show what surrounds it")
def inspect(bundle_id: str, evidence_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.inspect_citation(principal, bundle_id, evidence_id))


@router.get("/research/bundles/{bundle_id}/export", summary="Download a bundle as json, markdown or csv")
def export(bundle_id: str, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research), format: str = "markdown") -> Response:
    out = rs.research.export_bundle(principal, bundle_id, format)
    name = out["filename"]
    data = out["body"].encode("utf-8")
    headers = {"Content-Disposition": f"attachment; filename=\"{name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=out["content_type"], headers=headers)


@router.get("/research/sources/{source_id}", summary="One source with the passages that cite it")
def get_source(source_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.get_source(principal, source_id))


@router.patch("/research/sources/{source_id}", summary="Save or unsave a source")
def patch_source(source_id: str, body: SourcePatch, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.research.save_source(principal, source_id, body.saved))
