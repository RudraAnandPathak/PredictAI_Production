"""Document retrieval API: build and inspect search indexes, search one or many documents, read a passage with context, check a citation, compare documents and measure retrieval quality."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.research.container import ResearchServices
from ..deps import enforce_csrf, get_research, require_principal

router = APIRouter(tags=["retrieval"], dependencies=[Depends(enforce_csrf)])


class SearchBody(BaseModel):
    query: str = Field(max_length=2000)
    asset_ids: Optional[List[str]] = Field(default=None, max_length=50)
    mode: str = Field(default="hybrid", max_length=20)
    k: int = Field(default=8, ge=1, le=50)
    filters: Optional[Dict[str, Any]] = Field(default=None, max_length=10)


class CompareBody(BaseModel):
    asset_ids: List[str] = Field(max_length=10)
    topic: str = Field(max_length=2000)
    k: int = Field(default=3, ge=1, le=10)


class CitationBody(BaseModel):
    citation: Dict[str, Any] = Field(max_length=30)


class EvaluateBody(BaseModel):
    asset_ids: Optional[List[str]] = Field(default=None, max_length=50)
    labelled: Optional[List[Dict[str, Any]]] = Field(default=None, max_length=60)
    sample: int = Field(default=30, ge=5, le=100)


class BuildBody(BaseModel):
    version_id: Optional[str] = Field(default=None, max_length=64)
    force: bool = False


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/projects/{project_id}/documents/index", summary="Which documents are searchable, and how")
def status(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.status(principal, project_id))


@router.post("/assets/{asset_id}/index", summary="Build (or rebuild) a document's search index")
def build(asset_id: str, body: BuildBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.build(principal, asset_id, version_id=body.version_id, force=body.force))


@router.post("/assets/{asset_id}/index/embeddings", summary="Add optional embeddings from a provider the owner enabled")
def build_dense(asset_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.build_dense(principal, asset_id))


@router.post("/projects/{project_id}/documents/search", summary="Search documents (keyword, meaning-based or hybrid) with page and section citations")
def search(project_id: str, body: SearchBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.search(principal, project_id, body.query, asset_ids=body.asset_ids, mode=body.mode, k=body.k, filters=body.filters))


@router.get("/assets/{asset_id}/passages/{chunk_id}", summary="One passage with the passages before and after it")
def passage(asset_id: str, chunk_id: str, request: Request, version_id: Optional[str] = None, context: int = 1, principal: Principal = Depends(require_principal),
            rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.passage(principal, asset_id, chunk_id, version_id=version_id, context=context))


@router.post("/documents/verify-citation", summary="Check a citation against the stored document")
def verify(body: CitationBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.verify_citation(principal, body.citation))


@router.post("/projects/{project_id}/documents/compare", summary="Compare what several documents say about a topic")
def compare(project_id: str, body: CompareBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.compare(principal, project_id, body.asset_ids, body.topic, k=body.k))


@router.post("/projects/{project_id}/documents/evaluate", summary="Measure retrieval quality (self-check, or against questions you label)")
def evaluate(project_id: str, body: EvaluateBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.retrieval.evaluate(principal, project_id, asset_ids=body.asset_ids, labelled=body.labelled, sample=body.sample))
