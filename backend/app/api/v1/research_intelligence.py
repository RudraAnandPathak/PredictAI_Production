"""Research Intelligence API (Phase 10): /api/v1/research-intelligence. Thin: authenticate, CSRF, call one service method. Ownership, limits and validation live in the services."""
from __future__ import annotations

from typing import List, Optional

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.errors import AppError
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.identity.container import Services
from ...modules.research.container import ResearchServices
from ...modules.research_intelligence.core.common import limits as ri_limits
from ...modules.research_intelligence.discovery.service import DiscoveryService
from ...modules.research_intelligence.reasoning.analysis import AnalysisService
from ...modules.research_intelligence.reasoning.assistant import Assistant
from ...modules.research_intelligence.reports.service import ReportService
from ...modules.research_intelligence.services.jobs import JobService
from ...modules.research_intelligence.studio.service import StudioService
from ...modules.research_intelligence.services.notes import NotesService
from ...modules.research_intelligence.services.search import SearchService, owner_usage
from ...modules.research_intelligence.services.sources import SourceService
from ...modules.researchspace.service import ResearchSpaceService
from ..deps import enforce_csrf, get_research, get_services, require_owner, require_principal

router = APIRouter(prefix="/research-intelligence", tags=["research-intelligence"], dependencies=[Depends(enforce_csrf)])


class WorkspaceBody(BaseModel):
    name: Optional[str] = Field(default=None, max_length=300)
    description: Optional[str] = Field(default=None, max_length=3000)
    status: Optional[str] = Field(default=None, max_length=20)


class ReferenceBody(BaseModel):
    reference: str = Field(max_length=400)


class SourcePatch(BaseModel):
    included: Optional[bool] = None
    title: Optional[str] = Field(default=None, max_length=400)


class PolicyBody(BaseModel):
    ai_data_policy: str = Field(max_length=30)
    confirm: bool = False


class SearchBody(BaseModel):
    query: str = Field(max_length=1200)
    mode: str = Field(default="hybrid", max_length=20)
    source_ids: Optional[List[str]] = Field(default=None, max_length=200)
    k: int = Field(default=10, ge=1, le=30)


class AskBody(BaseModel):
    question: str = Field(max_length=2500)
    conversation_id: Optional[str] = Field(default=None, max_length=80)
    source_ids: Optional[List[str]] = Field(default=None, max_length=200)


class ConversationBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=300)


class ScopeBody(BaseModel):
    source_id: str = Field(max_length=80)
    mode: Optional[str] = Field(default=None, max_length=20)


class NoteBody(BaseModel):
    body: str = Field(max_length=25000)
    source_id: Optional[str] = Field(default=None, max_length=80)


class DiscoverBody(BaseModel):
    query: Optional[str] = Field(default=None, max_length=400)
    title: Optional[str] = Field(default=None, max_length=300)
    author: Optional[str] = Field(default=None, max_length=120)
    doi: Optional[str] = Field(default=None, max_length=200)
    year_from: Optional[int] = Field(default=None, ge=1000, le=2200)
    year_to: Optional[int] = Field(default=None, ge=1000, le=2200)
    field: Optional[str] = Field(default=None, max_length=80)
    type: Optional[str] = Field(default=None, max_length=30)
    open_access: bool = False
    sort: Optional[str] = Field(default=None, max_length=20)
    providers: Optional[List[str]] = Field(default=None, max_length=10)
    workspace_id: Optional[str] = Field(default=None, max_length=80)


class SaveBody(BaseModel):
    search_id: str = Field(max_length=80)
    rid: str = Field(max_length=40)


class CompareBody(BaseModel):
    ids: List[str] = Field(max_length=6)


class ImportBody(BaseModel):
    library_id: str = Field(max_length=80)
    full_text: bool = False


class FindingBody(BaseModel):
    source_id: str = Field(max_length=80)
    chunk_id: Optional[str] = Field(default=None, max_length=80)
    text: Optional[str] = Field(default=None, max_length=5000)
    kind: str = Field(default="finding", max_length=20)
    comment: Optional[str] = Field(default=None, max_length=2500)


class AnalysisBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=120)
    question: Optional[str] = Field(default=None, max_length=500)
    source_ids: Optional[List[str]] = Field(default=None, max_length=300)
    use_model: bool = False


class FindingPatch(BaseModel):
    comment: Optional[str] = Field(default=None, max_length=2000)
    kind: Optional[str] = Field(default=None, max_length=20)


class LinkBody(BaseModel):
    from_id: str = Field(max_length=80)
    to_id: str = Field(max_length=80)
    relation: str = Field(max_length=20)
    note: Optional[str] = Field(default=None, max_length=1000)


class GroupBody(BaseModel):
    name: str = Field(max_length=120)


class GroupItemsBody(BaseModel):
    finding_ids: List[str] = Field(max_length=500)


class OutlineBody(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    items: Optional[List[dict]] = Field(default=None, max_length=200)


class GenerateBody(BaseModel):
    kind: str = Field(max_length=30)
    analysis_id: str = Field(max_length=80)
    source_ids: Optional[List[str]] = Field(default=None, max_length=300)


class ReportBody(BaseModel):
    analysis_id: str = Field(max_length=80)
    report_type: str = Field(max_length=40)
    title: Optional[str] = Field(default=None, max_length=200)
    source_ids: Optional[List[str]] = Field(default=None, max_length=300)


class ReportPatch(BaseModel):
    title: Optional[str] = Field(default=None, max_length=200)
    sections: Optional[List[dict]] = Field(default=None, max_length=40)


class RegenerateBody(BaseModel):
    source_ids: Optional[List[str]] = Field(default=None, max_length=300)
    keep_author_text: bool = True


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


def _spaces(services: Services = Depends(get_services)) -> ResearchSpaceService:
    return ResearchSpaceService(services.ctx)


def _sources(services: Services = Depends(get_services)) -> SourceService:
    return SourceService(services.ctx)


def _notes(services: Services = Depends(get_services)) -> NotesService:
    return NotesService(services.ctx)


def _jobs(services: Services = Depends(get_services)) -> JobService:
    return JobService(services.ctx)


def _assistant(rs: ResearchServices = Depends(get_research)) -> Assistant:
    return Assistant(rs.ctx, router_factory=rs.providers.router)


def _discovery(request: Request, services: Services = Depends(get_services)) -> DiscoveryService:
    factory = getattr(request.app.state, "ri_discovery_factory", None)                    # tests and operators can supply providers/fetchers; production uses the defaults
    return factory(services.ctx) if factory else DiscoveryService(services.ctx)


def _analyses(rs: ResearchServices = Depends(get_research)) -> AnalysisService:
    return AnalysisService(rs.ctx, router_factory=rs.providers.router)


def _studio(services: Services = Depends(get_services)) -> StudioService:
    return StudioService(services.ctx)


def _reports(services: Services = Depends(get_services)) -> ReportService:
    return ReportService(services.ctx)


def _search(rs: ResearchServices = Depends(get_research)) -> SearchService:
    return SearchService(rs.ctx, router_factory=rs.providers.router)


# ------------------------------------------------------------------ limits, workspaces
@router.get("/limits", summary="Your limits: workspaces, sources, upload size, imports and questions per day")
def limits(request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> dict:
    return _ok(request, ri_limits(services.ctx, principal))


@router.get("/workspaces", summary="Your research workspaces")
def list_workspaces(request: Request, status: Optional[str] = None, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_spaces)) -> dict:
    return _ok(request, svc.list(principal, status))


@router.post("/workspaces", status_code=201, summary="Create a research workspace")
def create_workspace(body: WorkspaceBody, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_spaces)) -> dict:
    return _ok(request, svc.create(principal, body.name, body.description))


@router.get("/workspaces/{workspace_id}", summary="One workspace with source, job and policy summary")
def overview(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_spaces),
             sources: SourceService = Depends(_sources), jobs: JobService = Depends(_jobs)) -> dict:
    ws = svc.get(principal, workspace_id)
    srcs = sources.list(principal, workspace_id)["items"]
    by = {}
    for s in srcs:
        by[s["status"]] = by.get(s["status"], 0) + 1
    active = [j for j in jobs.list(principal, workspace_id, limit=200) if j["status"] in ("queued", "running", "cancelling")]
    return _ok(request, {"workspace": ws, "sources_by_status": by, "active_jobs": len(active), "settings": sources.settings(principal, workspace_id),
                         "worker_note": "Sources are processed by the research worker (scripts/run_research_worker.py). If sources stay queued, the worker is not running." if any(j["status"] == "queued" for j in active) else None})


@router.patch("/workspaces/{workspace_id}", summary="Rename, describe, archive or restore")
def update_workspace(workspace_id: str, body: WorkspaceBody, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_spaces)) -> dict:
    return _ok(request, svc.update(principal, workspace_id, name=body.name, description=body.description, status=body.status))


@router.delete("/workspaces/{workspace_id}", summary="Delete a workspace and everything in it (sources, passages, vectors, conversations, notes)")
def delete_workspace(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ResearchSpaceService = Depends(_spaces)) -> dict:
    return _ok(request, svc.delete(principal, workspace_id))


@router.get("/workspaces/{workspace_id}/settings", summary="Where this workspace's documents may be sent for AI answers")
def get_settings(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.settings(principal, workspace_id))


@router.put("/workspaces/{workspace_id}/settings", summary="Change the AI data policy (external use needs explicit confirmation and is audited)")
def put_settings(workspace_id: str, body: PolicyBody, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.set_policy(principal, workspace_id, body.ai_data_policy, body.confirm))


# ------------------------------------------------------------------ sources
@router.get("/workspaces/{workspace_id}/sources", summary="Sources in a workspace")
def list_sources(workspace_id: str, request: Request, status: Optional[str] = None, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.list(principal, workspace_id, status))


@router.post("/workspaces/{workspace_id}/sources/upload", status_code=201, summary="Add a file (raw bytes in the body; name in ?filename=)")
async def upload_source(workspace_id: str, filename: str, request: Request, principal: Principal = Depends(require_principal), services: Services = Depends(get_services),
                        svc: SourceService = Depends(_sources)) -> dict:
    limit = int(ri_limits(services.ctx, principal)["max_upload_mb"] * 1024 * 1024)
    declared = request.headers.get("content-length")
    if declared is not None and declared.isdigit() and int(declared) > limit:
        raise AppError("FILE_TOO_LARGE", f"This file is larger than the {limit // (1024 * 1024)} MB limit.", 413)
    body = bytearray()
    async for part in request.stream():                       # never buffer more than the limit, even when Content-Length is missing or false
        body += part
        if len(body) > limit:
            raise AppError("FILE_TOO_LARGE", f"This file is larger than the {limit // (1024 * 1024)} MB limit.", 413)
    return _ok(request, svc.add_file(principal, workspace_id, filename, bytes(body)))


@router.post("/workspaces/{workspace_id}/sources/reference", status_code=201, summary="Add a web address, a DOI or an arXiv ID")
def add_reference(workspace_id: str, body: ReferenceBody, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.add_reference(principal, workspace_id, body.reference))


@router.get("/workspaces/{workspace_id}/sources/{source_id}", summary="One source with metadata, structure, references and extractive summary")
def get_source(workspace_id: str, source_id: str, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.get(principal, workspace_id, source_id))


@router.patch("/workspaces/{workspace_id}/sources/{source_id}", summary="Include/exclude a source from answers, or rename it")
def patch_source(workspace_id: str, source_id: str, body: SourcePatch, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.update(principal, workspace_id, source_id, included=body.included, title=body.title))


@router.delete("/workspaces/{workspace_id}/sources/{source_id}", summary="Delete a source, its passages, vectors, citations and stored file")
def delete_source(workspace_id: str, source_id: str, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.delete(principal, workspace_id, source_id))


@router.post("/workspaces/{workspace_id}/sources/{source_id}/reindex", summary="Read and index a source again")
def reindex(workspace_id: str, source_id: str, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.reindex(principal, workspace_id, source_id))


@router.get("/workspaces/{workspace_id}/sources/{source_id}/chunks", summary="The passages of a source")
def chunks(workspace_id: str, source_id: str, request: Request, offset: int = 0, limit: int = 20, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.chunks(principal, workspace_id, source_id, offset, limit))


@router.get("/workspaces/{workspace_id}/passages/{chunk_id}", summary="One cited passage with its neighbours (citation click-through)")
def passage(workspace_id: str, chunk_id: str, request: Request, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.passage(principal, workspace_id, chunk_id))


# ------------------------------------------------------------------ jobs
@router.get("/workspaces/{workspace_id}/jobs", summary="Processing jobs of a workspace")
def list_jobs(workspace_id: str, request: Request, status: Optional[str] = None, principal: Principal = Depends(require_principal), svc: SourceService = Depends(_sources),
              jobs: JobService = Depends(_jobs)) -> dict:
    svc.settings(principal, workspace_id)                      # ownership check
    return _ok(request, {"items": jobs.list(principal, workspace_id, status)})


@router.post("/jobs/{job_id}/cancel", summary="Cancel a queued or running job")
def cancel_job(job_id: str, request: Request, principal: Principal = Depends(require_principal), jobs: JobService = Depends(_jobs)) -> dict:
    return _ok(request, jobs.cancel(principal, job_id))


@router.post("/jobs/{job_id}/retry", summary="Retry a failed or cancelled job")
def retry_job(job_id: str, request: Request, principal: Principal = Depends(require_principal), jobs: JobService = Depends(_jobs)) -> dict:
    return _ok(request, jobs.retry(principal, job_id))


# ------------------------------------------------------------------ search and assistant
@router.post("/workspaces/{workspace_id}/search", summary="Search the workspace's sources (hybrid, keyword or vector)")
def search(workspace_id: str, body: SearchBody, request: Request, principal: Principal = Depends(require_principal), svc: SearchService = Depends(_search)) -> dict:
    return _ok(request, svc.search(principal, workspace_id, body.query, mode=body.mode, source_ids=body.source_ids, k=body.k))


@router.get("/workspaces/{workspace_id}/conversations", summary="Conversations of a workspace")
def list_conversations(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.list_conversations(principal, workspace_id))


@router.post("/workspaces/{workspace_id}/conversations", status_code=201, summary="Start a conversation")
def create_conversation(workspace_id: str, body: ConversationBody, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.create_conversation(principal, workspace_id, body.title))


@router.get("/workspaces/{workspace_id}/conversations/{conversation_id}", summary="A conversation with its messages and citations")
def get_conversation(workspace_id: str, conversation_id: str, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.messages(principal, workspace_id, conversation_id))


@router.patch("/workspaces/{workspace_id}/conversations/{conversation_id}", summary="Rename a conversation")
def rename_conversation(workspace_id: str, conversation_id: str, body: ConversationBody, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.rename(principal, workspace_id, conversation_id, body.title))


@router.delete("/workspaces/{workspace_id}/conversations/{conversation_id}", summary="Delete a conversation and its citations")
def delete_conversation(workspace_id: str, conversation_id: str, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.delete_conversation(principal, workspace_id, conversation_id))


@router.put("/workspaces/{workspace_id}/conversations/{conversation_id}/scope", summary="Include or exclude one source for this conversation")
def set_scope(workspace_id: str, conversation_id: str, body: ScopeBody, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.set_scope(principal, workspace_id, conversation_id, body.source_id, body.mode))


@router.post("/workspaces/{workspace_id}/ask", summary="Ask a question; the answer cites stored passages and says how it was produced")
def ask(workspace_id: str, body: AskBody, request: Request, principal: Principal = Depends(require_principal), svc: Assistant = Depends(_assistant)) -> dict:
    return _ok(request, svc.ask(principal, workspace_id, body.question, conversation_id=body.conversation_id, source_ids=body.source_ids))


# ------------------------------------------------------------------ notes and findings
@router.get("/workspaces/{workspace_id}/notes", summary="Notes")
def list_notes(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.list_notes(principal, workspace_id))


@router.post("/workspaces/{workspace_id}/notes", status_code=201, summary="Add a note")
def create_note(workspace_id: str, body: NoteBody, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.create_note(principal, workspace_id, body.body, body.source_id))


@router.patch("/workspaces/{workspace_id}/notes/{note_id}", summary="Edit a note")
def update_note(workspace_id: str, note_id: str, body: NoteBody, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.update_note(principal, workspace_id, note_id, body.body))


@router.delete("/workspaces/{workspace_id}/notes/{note_id}", summary="Delete a note")
def delete_note(workspace_id: str, note_id: str, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.delete_note(principal, workspace_id, note_id))


@router.get("/workspaces/{workspace_id}/findings", summary="Saved findings, highlights and evidence")
def list_findings(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.list_findings(principal, workspace_id))


@router.post("/workspaces/{workspace_id}/findings", status_code=201, summary="Save a passage as a finding, highlight or evidence")
def create_finding(workspace_id: str, body: FindingBody, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.create_finding(principal, workspace_id, body.source_id, body.text, chunk_id=body.chunk_id, kind=body.kind, comment=body.comment))


@router.delete("/workspaces/{workspace_id}/findings/{finding_id}", summary="Delete a finding")
def delete_finding(workspace_id: str, finding_id: str, request: Request, principal: Principal = Depends(require_principal), svc: NotesService = Depends(_notes)) -> dict:
    return _ok(request, svc.delete_finding(principal, workspace_id, finding_id))


# ------------------------------------------------------------------ discovery (R2)
@router.get("/discovery/providers", summary="Search providers: what each is, whether it is usable, and what is missing")
def discovery_providers(request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.provider_info(principal))


@router.post("/discovery/search", summary="Search academic providers (Crossref, OpenAlex, arXiv, Semantic Scholar) and the web (SearXNG); duplicates are merged")
def discovery_search(body: DiscoverBody, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    data = body.model_dump() if hasattr(body, "model_dump") else body.dict()
    return _ok(request, svc.search(principal, data, workspace_id=data.pop("workspace_id", None)))


@router.get("/discovery/searches", summary="Your search history")
def discovery_history(request: Request, limit: int = 30, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.history(principal, limit))


@router.delete("/discovery/searches", summary="Clear your search history")
def discovery_clear(request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.clear_history(principal))


@router.get("/discovery/searches/{search_id}", summary="Results of a stored search: pages, filters and sorting without asking the providers again")
def discovery_results(search_id: str, request: Request, page: int = 1, page_size: int = 20, sort: Optional[str] = None, provider: Optional[str] = None, year_from: Optional[int] = None,
                      year_to: Optional[int] = None, type: Optional[str] = None, open_access: bool = False, has_abstract: bool = False, within: Optional[str] = None,
                      principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.get_search(principal, search_id, page=page, page_size=page_size, sort=sort, provider=provider, year_from=year_from, year_to=year_to, type=type,
                                       open_access=open_access, has_abstract=has_abstract, within=within))


@router.delete("/discovery/searches/{search_id}", summary="Delete one stored search")
def discovery_delete(search_id: str, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.delete_search(principal, search_id))


@router.post("/discovery/library", status_code=201, summary="Save a search result to your library (the server copies what the provider reported)")
def discovery_save(body: SaveBody, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.save(principal, body.search_id, body.rid))


@router.get("/discovery/library", summary="Your saved papers")
def discovery_library(request: Request, q: Optional[str] = None, limit: int = 100, offset: int = 0, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.library(principal, query=q, limit=limit, offset=offset))


@router.get("/discovery/library/{library_id}", summary="One saved paper with its abstract, citation text and where it is used")
def discovery_library_get(library_id: str, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.library_get(principal, library_id))


@router.delete("/discovery/library/{library_id}", summary="Remove a saved paper (sources already imported into workspaces stay)")
def discovery_library_remove(library_id: str, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.library_remove(principal, library_id))


@router.post("/discovery/compare", summary="Compare 2-6 saved papers from their titles, abstracts and metadata")
def discovery_compare(body: CompareBody, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery)) -> dict:
    return _ok(request, svc.compare(principal, body.ids))


@router.post("/workspaces/{workspace_id}/discovery/import", status_code=201, summary="Add a saved paper to a workspace (open full text only when a provider reported it)")
def discovery_import(workspace_id: str, body: ImportBody, request: Request, principal: Principal = Depends(require_principal), svc: DiscoveryService = Depends(_discovery),
                     sources: SourceService = Depends(_sources)) -> dict:
    return _ok(request, svc.import_to_workspace(principal, workspace_id, body.library_id, body.full_text, sources))


# ------------------------------------------------------------------ owner
@router.get("/owner/usage", summary="Platform owner: aggregate Research Intelligence usage (counts only, never content)")
def usage(request: Request, principal: Principal = Depends(require_owner), services: Services = Depends(get_services)) -> dict:
    return _ok(request, owner_usage(services.ctx))


# ------------------------------------------------------------------ multi-source analysis (R5)
@router.post("/workspaces/{workspace_id}/analyses", status_code=202, summary="Start an analysis that compares several sources (runs in the background)")
def create_analysis(workspace_id: str, body: AnalysisBody, request: Request, principal: Principal = Depends(require_principal), svc: AnalysisService = Depends(_analyses)) -> dict:
    return _ok(request, svc.create(principal, workspace_id, title=body.title, question=body.question, source_ids=body.source_ids, use_model=body.use_model))


@router.get("/workspaces/{workspace_id}/analyses", summary="Analyses of a workspace")
def list_analyses(workspace_id: str, request: Request, limit: int = 50, principal: Principal = Depends(require_principal), svc: AnalysisService = Depends(_analyses)) -> dict:
    return _ok(request, {"items": svc.list(principal, workspace_id, limit)})


@router.get("/workspaces/{workspace_id}/analyses/{analysis_id}", summary="One analysis with its progress and, when finished, its result")
def get_analysis(workspace_id: str, analysis_id: str, request: Request, principal: Principal = Depends(require_principal), svc: AnalysisService = Depends(_analyses)) -> dict:
    return _ok(request, svc.get(principal, workspace_id, analysis_id))


@router.post("/workspaces/{workspace_id}/analyses/{analysis_id}/cancel", summary="Cancel a queued or running analysis")
def cancel_analysis(workspace_id: str, analysis_id: str, request: Request, principal: Principal = Depends(require_principal), svc: AnalysisService = Depends(_analyses)) -> dict:
    return _ok(request, svc.cancel(principal, workspace_id, analysis_id))


@router.delete("/workspaces/{workspace_id}/analyses/{analysis_id}", summary="Delete a finished analysis")
def delete_analysis(workspace_id: str, analysis_id: str, request: Request, principal: Principal = Depends(require_principal), svc: AnalysisService = Depends(_analyses)) -> dict:
    return _ok(request, svc.delete(principal, workspace_id, analysis_id))


# ------------------------------------------------------------------ Research Studio (R7)
@router.get("/home", summary="Research home: recent workspaces, activity, sources, findings and reports")
def home(request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.home(principal))


@router.get("/workspaces/{workspace_id}/activity", summary="Recent activity in a workspace (revisit earlier work)")
def workspace_activity(workspace_id: str, request: Request, limit: int = 30, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, {"items": svc.activity(principal, workspace_id, limit)})


@router.patch("/workspaces/{workspace_id}/findings/{finding_id}", summary="Edit a saved finding's comment or kind")
def patch_finding(workspace_id: str, finding_id: str, body: FindingPatch, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.update_finding(principal, workspace_id, finding_id, body.comment, body.kind))


@router.get("/workspaces/{workspace_id}/studio/evidence", summary="Evidence explorer: saved findings, highlights and evidence with per-source counts")
def studio_evidence(workspace_id: str, request: Request, source_id: Optional[str] = None, kind: Optional[str] = None, q: Optional[str] = None, principal: Principal = Depends(require_principal),
                    svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.evidence(principal, workspace_id, source_id=source_id, kind=kind, q=q))


@router.get("/workspaces/{workspace_id}/studio/links", summary="Connections between findings")
def studio_links(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, {"items": svc.links(principal, workspace_id)})


@router.post("/workspaces/{workspace_id}/studio/links", status_code=201, summary="Connect two findings")
def studio_link(workspace_id: str, body: LinkBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.link(principal, workspace_id, body.from_id, body.to_id, body.relation, body.note))


@router.delete("/workspaces/{workspace_id}/studio/links/{link_id}", summary="Remove a connection")
def studio_unlink(workspace_id: str, link_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.unlink(principal, workspace_id, link_id))


@router.get("/workspaces/{workspace_id}/studio/groups", summary="Finding groups")
def studio_groups(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, {"items": svc.groups(principal, workspace_id)})


@router.post("/workspaces/{workspace_id}/studio/groups", status_code=201, summary="Create a finding group")
def studio_group_create(workspace_id: str, body: GroupBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.create_group(principal, workspace_id, body.name))


@router.patch("/workspaces/{workspace_id}/studio/groups/{group_id}", summary="Rename a group")
def studio_group_rename(workspace_id: str, group_id: str, body: GroupBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.rename_group(principal, workspace_id, group_id, body.name))


@router.put("/workspaces/{workspace_id}/studio/groups/{group_id}/items", summary="Set the findings in a group, in order")
def studio_group_items(workspace_id: str, group_id: str, body: GroupItemsBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.set_group_items(principal, workspace_id, group_id, body.finding_ids))


@router.delete("/workspaces/{workspace_id}/studio/groups/{group_id}", summary="Delete a group (its findings stay)")
def studio_group_delete(workspace_id: str, group_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.delete_group(principal, workspace_id, group_id))


@router.get("/workspaces/{workspace_id}/studio/outlines", summary="Research outlines")
def studio_outlines(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, {"items": svc.outlines(principal, workspace_id)})


@router.post("/workspaces/{workspace_id}/studio/outlines", status_code=201, summary="Create an outline")
def studio_outline_create(workspace_id: str, body: OutlineBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.create_outline(principal, workspace_id, body.title, body.items))


@router.get("/workspaces/{workspace_id}/studio/outlines/{outline_id}", summary="One outline")
def studio_outline(workspace_id: str, outline_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.outline(principal, workspace_id, outline_id))


@router.patch("/workspaces/{workspace_id}/studio/outlines/{outline_id}", summary="Edit an outline")
def studio_outline_update(workspace_id: str, outline_id: str, body: OutlineBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.update_outline(principal, workspace_id, outline_id, body.title, body.items))


@router.delete("/workspaces/{workspace_id}/studio/outlines/{outline_id}", summary="Delete an outline")
def studio_outline_delete(workspace_id: str, outline_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.delete_outline(principal, workspace_id, outline_id))


@router.post("/workspaces/{workspace_id}/studio/generate", status_code=201, summary="Generate research questions, hypotheses or a literature-review draft from a finished analysis (saved, labelled as generated)")
def studio_generate(workspace_id: str, body: GenerateBody, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.generate(principal, workspace_id, body.kind, body.analysis_id, body.source_ids))


@router.get("/workspaces/{workspace_id}/studio/generated", summary="Saved studio outputs")
def studio_generated_list(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, {"items": svc.generated_list(principal, workspace_id)})


@router.get("/workspaces/{workspace_id}/studio/generated/{generated_id}", summary="One saved studio output")
def studio_generated(workspace_id: str, generated_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.generated(principal, workspace_id, generated_id))


@router.delete("/workspaces/{workspace_id}/studio/generated/{generated_id}", summary="Delete a saved studio output")
def studio_generated_delete(workspace_id: str, generated_id: str, request: Request, principal: Principal = Depends(require_principal), svc: StudioService = Depends(_studio)) -> dict:
    return _ok(request, svc.delete_generated(principal, workspace_id, generated_id))


# ------------------------------------------------------------------ reports (R8)
@router.get("/report-types", summary="Report types and the sections each contains")
def report_types(request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, {"items": svc.types()})


@router.get("/reports", summary="All your reports")
def all_reports(request: Request, limit: int = 100, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, {"items": svc.list_all(principal, limit)})


@router.get("/workspaces/{workspace_id}/reports", summary="Reports of a workspace")
def list_reports(workspace_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, {"items": svc.list(principal, workspace_id)})


@router.post("/workspaces/{workspace_id}/reports", status_code=201, summary="Create a report from a finished analysis")
def create_report(workspace_id: str, body: ReportBody, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.create(principal, workspace_id, analysis_id=body.analysis_id, report_type=body.report_type, title=body.title, source_ids=body.source_ids))


@router.get("/workspaces/{workspace_id}/reports/{report_id}", summary="A report with all sections")
def get_report(workspace_id: str, report_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.get(principal, workspace_id, report_id))


@router.patch("/workspaces/{workspace_id}/reports/{report_id}", summary="Edit a report (title, sections). Quotations cannot be edited; edited text is labelled as the author's")
def update_report(workspace_id: str, report_id: str, body: ReportPatch, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.update(principal, workspace_id, report_id, title=body.title, sections=body.sections))


@router.delete("/workspaces/{workspace_id}/reports/{report_id}", summary="Delete a report and its history")
def delete_report(workspace_id: str, report_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.delete(principal, workspace_id, report_id))


@router.post("/workspaces/{workspace_id}/reports/{report_id}/regenerate", summary="Rebuild the generated parts from the analysis, keeping the author's own text")
def regenerate_report(workspace_id: str, report_id: str, body: RegenerateBody, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.regenerate(principal, workspace_id, report_id, source_ids=body.source_ids, keep_author_text=body.keep_author_text))


@router.get("/workspaces/{workspace_id}/reports/{report_id}/versions", summary="Earlier versions of a report")
def report_versions(workspace_id: str, report_id: str, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, {"items": svc.versions(principal, workspace_id, report_id)})


@router.post("/workspaces/{workspace_id}/reports/{report_id}/versions/{version_id}/restore", summary="Restore an earlier version (the current one is kept as a version)")
def restore_report(workspace_id: str, report_id: str, version_id: int, request: Request, principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> dict:
    return _ok(request, svc.restore(principal, workspace_id, report_id, version_id))


@router.get("/workspaces/{workspace_id}/reports/{report_id}/export", summary="Download a report as pdf, docx, pptx or md")
def export_report(workspace_id: str, report_id: str, request: Request, format: str = "pdf", principal: Principal = Depends(require_principal), svc: ReportService = Depends(_reports)) -> Response:
    data, ctype, name = svc.export(principal, workspace_id, report_id, format)
    ascii_name = name.encode("ascii", "ignore").decode() or "report"
    headers = {"Content-Disposition": f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(data)), "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"}
    return Response(content=data, media_type=ctype, headers=headers)
