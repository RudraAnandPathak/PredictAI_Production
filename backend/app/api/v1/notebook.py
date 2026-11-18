"""Notebook Studio API: outputs generated from a notebook's own sources. A notebook is a research-intelligence workspace; sources, chat and notes use that API."""
from __future__ import annotations

from typing import List, Optional

from urllib.parse import quote

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ...modules.research.container import ResearchServices
from ...modules.research_intelligence.discovery.service import DiscoveryService
from ...modules.research_intelligence.services.sources import SourceService
from ..deps import enforce_csrf, get_p7, get_research, require_principal

router = APIRouter(tags=["notebook"], dependencies=[Depends(enforce_csrf)])


class StudioBody(BaseModel):
    kind: str = Field(max_length=40)
    instructions: Optional[str] = Field(default=None, max_length=600)
    source_ids: Optional[List[str]] = Field(default=None, max_length=100)


class DeepBody(BaseModel):
    question: str = Field(max_length=400)
    max_sources: int = Field(default=5, ge=1, le=8)


def _discovery(request: Request, p7: Phase7Services):
    factory = getattr(request.app.state, "ri_discovery_factory", None)                    # tests and operators can supply providers/fetchers, as for research discovery
    return factory(p7.ws.ctx) if factory else DiscoveryService(p7.ws.ctx)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/notebooks/studio/kinds", summary="The kinds of output the Studio can make")
def kinds(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.kinds())


@router.post("/notebooks/{notebook_id}/studio", status_code=201, summary="Generate an output from the notebook's sources (every statement cites a source passage)")
def generate(notebook_id: str, body: StudioBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.generate(principal, notebook_id, body.kind, body.instructions, body.source_ids))


@router.get("/notebooks/{notebook_id}/studio", summary="Outputs already made for this notebook")
def list_outputs(notebook_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.list(principal, notebook_id))


@router.get("/notebooks/outputs/{output_id}", summary="One output with its text and the passages it cites")
def get_output(output_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.get(principal, output_id))


@router.delete("/notebooks/outputs/{output_id}", summary="Delete an output")
def delete_output(output_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.delete(principal, output_id))


@router.get("/notebooks/outputs/{output_id}/export", summary="Download an output: markdown (any), csv (table, flashcards, quiz) or pptx (slide deck)")
def export_output(output_id: str, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7), format: str = "markdown") -> Response:
    out = p7.notebook.export(principal, output_id, format)
    name = out["filename"]
    headers = {"Content-Disposition": f"attachment; filename=\"{quote(name)}\"; filename*=UTF-8''{quote(name, safe='')}", "Content-Length": str(len(out["body"])), "X-Content-Type-Options": "nosniff",
               "Cache-Control": "private, no-store"}
    return Response(content=out["body"], media_type=out["content_type"], headers=headers)


@router.post("/notebooks/{notebook_id}/deep-research", status_code=201, summary="Search the web and paper databases for a question and add the best results as sources")
def deep_research(notebook_id: str, body: DeepBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.deep_research(principal, notebook_id, body.question, _discovery(request, p7), SourceService(p7.ws.ctx), body.max_sources))


@router.get("/notebooks/legacy", summary="How many reports from the older Research area are not yet in a notebook")
def legacy(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.notebook.legacy_status(principal))


@router.post("/notebooks/legacy/import", summary="Copy reports from the older Research area into notebooks (the originals are not changed)")
def legacy_import(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, p7.notebook.import_legacy(principal, rs.research, SourceService(p7.ws.ctx)))
