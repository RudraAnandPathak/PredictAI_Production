"""Read-only reuse and boundary endpoints: reusable evidence for a project, the exam-research capability statement and topic research, and the Phase 5 model registry view.

None of these starts outside access or changes a model. Exam Intelligence and the full Model Registry are Phase 7 requirements; the responses say which parts exist."""
from __future__ import annotations

from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.research.container import ResearchServices
from ..deps import enforce_csrf, get_research, require_principal

router = APIRouter(tags=["interfaces"], dependencies=[Depends(enforce_csrf)])


class TopicBody(BaseModel):
    topic: str = Field(max_length=400)
    course_documents: List[str] = Field(default_factory=list, max_length=20)
    level: str = Field(default="topic", max_length=30)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.get("/projects/{project_id}/evidence", summary="Reusable evidence for a project: labelled conclusions with citations, and reported news (never as fact)")
def evidence(project_id: str, request: Request, session_id: Optional[str] = None, include_world: bool = True, limit: int = 5, principal: Principal = Depends(require_principal),
             rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.evidence.evidence_for_project(principal, project_id, session_id=session_id, include_world=include_world, limit=limit))


@router.get("/exam-research/capabilities", summary="What exam-related research exists now and what Exam Intelligence will need in Phase 7")
def exam_capabilities(request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.exam.capabilities())


@router.post("/projects/{project_id}/exam-research/topic", status_code=201, summary="Plan source-grounded research on a topic from your course documents (nothing runs until you approve)")
def exam_topic(project_id: str, body: TopicBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.exam.topic_research(principal, project_id, topic=body.topic, course_documents=body.course_documents, level=body.level))


@router.get("/registry/capabilities", summary="What the model registry view can show, and what is not supported")
def registry_capabilities(request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.registry.capabilities())


@router.get("/registry/models", summary="Your trained models with dataset lineage and evaluation (read only)")
def registry_models(request: Request, asset_id: Optional[str] = None, limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal),
                    rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.registry.models(principal, asset_id, limit=limit, offset=offset))


@router.get("/registry/models/{model_id}", summary="One model's registry entry: lineage, evaluation, versions and training history")
def registry_model(model_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.registry.model(principal, model_id))
