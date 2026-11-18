"""Exam Intelligence API: subjects, documents, extracted questions, curriculum mapping and corrections, analysis, blueprints, mock exams, validation and approval."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictBool

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import enforce_csrf, get_p7, require_principal

router = APIRouter(tags=["exam"], dependencies=[Depends(enforce_csrf)])


class SubjectBody(BaseModel):
    name: str = Field(max_length=120)
    level: Optional[str] = Field(default=None, max_length=60)


class DocumentBody(BaseModel):
    asset_id: str = Field(max_length=64)
    kind: str = Field(max_length=20)
    title: Optional[str] = Field(default=None, max_length=160)
    exam_year: Optional[int] = None


class CorrectionBody(BaseModel):
    chapter: Optional[str] = Field(default=None, max_length=120)
    topic: Optional[str] = Field(default=None, max_length=140)
    reason: Optional[str] = Field(default=None, max_length=300)


class AnalyseBody(BaseModel):
    year_from: Optional[int] = None
    year_to: Optional[int] = None


class BlueprintBody(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict, max_length=20)


class GenerateBody(BaseModel):
    seed: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=160)


class ChangesBody(BaseModel):
    changes: Dict[str, Any] = Field(max_length=10)


class SeedBody(BaseModel):
    seed: Optional[int] = None


class PredictBody(BaseModel):
    year_from: Optional[int] = None
    year_to: Optional[int] = None
    use_web: StrictBool = False
    max_articles: int = Field(default=5, ge=1, le=8)
    exam_name: Optional[str] = Field(default=None, max_length=120)


class PaperBody(BaseModel):
    spec: Dict[str, Any] = Field(default_factory=dict, max_length=20)
    ai: str = Field(default="all", max_length=8)
    seed: Optional[int] = None
    title: Optional[str] = Field(default=None, max_length=160)


class ApproveBody(BaseModel):
    confirm: StrictBool = False
    acknowledge_warnings: StrictBool = False


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


@router.post("/projects/{project_id}/exam/subjects", status_code=201, summary="Create a subject")
def create_subject(project_id: str, body: SubjectBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.create_subject(principal, project_id, name=body.name, level=body.level))


@router.get("/projects/{project_id}/exam/subjects", summary="Your subjects in this project")
def subjects(project_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.subjects(principal, project_id))


@router.get("/exam/subjects/{subject_id}", summary="One subject with counts")
def subject(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.subject(principal, subject_id))


@router.post("/exam/subjects/{subject_id}/documents", status_code=201, summary="Add a syllabus, notes, slides or past paper you already uploaded")
def add_document(subject_id: str, body: DocumentBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.add_document(principal, subject_id, asset_id=body.asset_id, kind=body.kind, title=body.title, exam_year=body.exam_year))


@router.get("/exam/subjects/{subject_id}/documents", summary="Documents of a subject with extraction quality and warnings")
def documents(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.documents(principal, subject_id))


@router.get("/exam/subjects/{subject_id}/curriculum", summary="Chapters and topics read from the syllabus")
def curriculum(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.curriculum(principal, subject_id))


@router.get("/exam/subjects/{subject_id}/questions", summary="Extracted questions with their chapter mapping and its history")
def questions(subject_id: str, request: Request, document_id: Optional[str] = None, unmapped: bool = False, low_confidence: bool = False, limit: int = 50, offset: int = 0,
              principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.questions(principal, subject_id, document_id=document_id, unmapped=unmapped, low_confidence=low_confidence, limit=limit, offset=offset))


@router.post("/exam/questions/{question_id}/mapping", summary="Correct a chapter mapping (the automatic result is kept in the history)")
def correct(question_id: str, body: CorrectionBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.correct_mapping(principal, question_id, chapter=body.chapter, topic=body.topic, reason=body.reason))


@router.post("/exam/subjects/{subject_id}/analyses", status_code=201, summary="Calculate historical frequency, relative emphasis and the heuristic score")
def analyse(subject_id: str, body: AnalyseBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.analyse(principal, subject_id, body.model_dump(exclude_none=True)))


@router.get("/exam/subjects/{subject_id}/analyses", summary="Earlier analyses of a subject")
def analyses(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.analyses(principal, subject_id))


@router.get("/exam/analyses/{analysis_id}", summary="One stored analysis")
def get_analysis(analysis_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.get_analysis(principal, analysis_id))


@router.post("/exam/subjects/{subject_id}/blueprints", status_code=201, summary="Plan a mock exam: question types, marks, difficulty mix, chapter coverage")
def blueprint(subject_id: str, body: BlueprintBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.blueprint(principal, subject_id, body.spec))


@router.get("/exam/blueprints/{blueprint_id}", summary="One blueprint")
def get_blueprint(blueprint_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.get_blueprint(principal, blueprint_id))


@router.post("/exam/blueprints/{blueprint_id}/generate", status_code=201, summary="Generate a draft mock exam (never published automatically)")
def generate(blueprint_id: str, body: GenerateBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.generate(principal, blueprint_id, seed=body.seed, title=body.title))


@router.get("/exam/subjects/{subject_id}/assessments", summary="Mock exams of a subject and those shared with you")
def assessments(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.assessments(principal, subject_id))


@router.get("/exam/assessments/{assessment_id}", summary="A mock exam with answers, marking schemes and where each came from")
def get_assessment(assessment_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.get_assessment(principal, assessment_id))


@router.get("/exam/assessments/{assessment_id}/paper", summary="The paper as text, optionally with the answer key")
def paper(assessment_id: str, request: Request, with_key: bool = False, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.paper_text(principal, assessment_id, with_key=with_key))


@router.get("/exam/assessments/{assessment_id}/validation", summary="Check duplicates, marks, ambiguous or incomplete questions")
def validation(assessment_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.validate(principal, assessment_id))


@router.patch("/exam/assessments/{assessment_id}/questions/{question_id}", summary="Edit, include or exclude a question of a draft")
def edit_question(assessment_id: str, question_id: str, body: ChangesBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.edit_question(principal, assessment_id, question_id, body.changes))


@router.post("/exam/assessments/{assessment_id}/questions/{question_id}/regenerate", summary="Replace one question with another built from your notes")
def regenerate(assessment_id: str, question_id: str, body: SeedBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.regenerate_question(principal, assessment_id, question_id, seed=body.seed))


@router.post("/exam/assessments/{assessment_id}/approve", summary="Approve a reviewed draft (needs confirm=true)")
def approve(assessment_id: str, body: ApproveBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.approve(principal, assessment_id, confirm=body.confirm, acknowledge_warnings=body.acknowledge_warnings))


@router.post("/exam/assessments/{assessment_id}/reject", summary="Reject a draft")
def reject(assessment_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.exam.reject(principal, assessment_id))


# ---------------------------------------------------------------------- prediction
@router.get("/exam/subjects/{subject_id}/prediction-options", summary="What a prediction can use right now: your papers, web articles (if allowed) and the AI (if ready)")
def prediction_options(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.predict.capabilities(principal, subject_id))


@router.post("/exam/subjects/{subject_id}/predictions", status_code=201, summary="Rank the chapters likely to carry weight, test the ranking on your own papers, optionally read exam articles")
def predict(subject_id: str, body: PredictBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.predict.predict(principal, subject_id, body.model_dump(exclude_none=True)))


@router.get("/exam/subjects/{subject_id}/predictions", summary="Earlier predictions of a subject")
def predictions(subject_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.predict.list(principal, subject_id))


@router.get("/exam/predictions/{prediction_id}", summary="One stored prediction with its evidence, test result and limitations")
def get_prediction(prediction_id: str, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.predict.get(principal, prediction_id))


@router.post("/exam/predictions/{prediction_id}/paper", status_code=201, summary="Build a predicted draft paper from a prediction (never published automatically)")
def predicted_paper(prediction_id: str, body: PaperBody, request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return _ok(request, p7.predict.build_paper(principal, prediction_id, {**body.spec, "ai": body.ai, "seed": body.seed, "title": body.title}))
