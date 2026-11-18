"""World intelligence API: news sources, fetching, articles, events, topics, coverage trends, freshness and association-only influence analyses.

Thin by design. Authentication and CSRF are checked here, everything else (ownership, consent, rate limits, auditing, the 404-only isolation) lives in `WorldService`. Fetch and analysis
jobs are ordinary research jobs: progress and cancellation use the existing `/research/jobs/{id}` endpoints. Routes with fixed words come before routes with an id."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, StrictBool, StrictInt

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.research.container import ResearchServices
from ..deps import enforce_csrf, get_research, require_principal

router = APIRouter(tags=["world"], dependencies=[Depends(enforce_csrf)])


class PolicyBody(BaseModel):
    min_interval_s: Optional[StrictInt] = None
    item_cap: Optional[StrictInt] = None
    retention_days: Optional[StrictInt] = None
    full_text: Optional[StrictBool] = None
    terms_acknowledged: Optional[StrictBool] = None


class SourceBody(BaseModel):
    name: str = Field(max_length=200)
    feed_url: str = Field(max_length=2000)
    publisher: Optional[str] = Field(default=None, max_length=200)
    language: Optional[str] = Field(default=None, max_length=20)
    policy: Optional[PolicyBody] = None


class SourcePatch(BaseModel):
    name: Optional[str] = Field(default=None, max_length=200)
    publisher: Optional[str] = Field(default=None, max_length=200)
    language: Optional[str] = Field(default=None, max_length=20)
    status: Optional[str] = Field(default=None, max_length=20)
    policy: Optional[PolicyBody] = None


class IngestBody(BaseModel):
    source_ids: Optional[List[str]] = Field(default=None, max_length=20)


class InfluenceBody(BaseModel):
    asset_id: str = Field(max_length=64)
    date_column: str = Field(max_length=200)
    value_column: str = Field(max_length=200)
    aggregate: str = Field(default="mean", max_length=10)
    measure: str = Field(default="count", max_length=10)
    topic: Optional[str] = Field(default=None, max_length=40)
    entity: Optional[str] = Field(default=None, max_length=200)
    max_lag: StrictInt = 7
    label: Optional[str] = Field(default=None, max_length=300)


def _ok(request: Request, data: dict) -> dict:
    return success(data, request_id_from_scope(request.scope))


def _policy(body: Optional[PolicyBody]) -> Optional[Dict[str, Any]]:
    return None if body is None else {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}


# ---------------------------------------------------------------------------- overview, topics, trends, freshness
@router.get("/projects/{project_id}/world/overview", summary="News switch, counts, event statuses and freshness warnings for a project")
def overview(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.overview(principal, project_id))


@router.get("/projects/{project_id}/world/topics", summary="Topics found in recent news (a browsing guide from keyword rules, not a judgement)")
def topics(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.topics(principal, project_id))


@router.get("/projects/{project_id}/world/trends", summary="Coverage trends: how much each topic or name is being written about compared with before")
def trends(project_id: str, request: Request, window_days: int = 7, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.trends(principal, project_id, window_days))


@router.get("/projects/{project_id}/world/freshness", summary="How recently each news source was read, with warnings")
def freshness(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.freshness(principal, project_id))


# ---------------------------------------------------------------------------- sources
@router.get("/projects/{project_id}/world/sources", summary="News sources followed in a project")
def list_sources(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.list_sources(principal, project_id))


@router.post("/projects/{project_id}/world/sources", status_code=201, summary="Follow an RSS or Atom feed (nothing is fetched until you ask)")
def add_source(project_id: str, body: SourceBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    data = {k: v for k, v in body.model_dump().items() if k != "policy"}
    if body.policy is not None:
        data["policy"] = _policy(body.policy)
    return _ok(request, rs.world.add_source(principal, project_id, data))


@router.patch("/world/sources/{source_id}", summary="Rename, pause, resume or change the fetch policy of a source")
def update_source(source_id: str, body: SourcePatch, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    data = {k: getattr(body, k) for k in body.model_fields_set if k != "policy"}
    if "policy" in body.model_fields_set:
        data["policy"] = _policy(body.policy)
    return _ok(request, rs.world.update_source(principal, source_id, data))


@router.delete("/world/sources/{source_id}", summary="Stop following a source and delete its stored articles")
def delete_source(source_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.delete_source(principal, source_id))


@router.post("/projects/{project_id}/world/ingest", status_code=202, summary="Fetch the project's news sources in the background (needs news switched on)")
def ingest(project_id: str, body: IngestBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.start_ingest(principal, project_id, body.source_ids))


# ---------------------------------------------------------------------------- articles
@router.get("/projects/{project_id}/world/articles", summary="Stored articles (headline, link, date and the feed's own summary)")
def list_articles(project_id: str, request: Request, status: Optional[str] = None, source_id: Optional[str] = None, since: Optional[str] = None, until: Optional[str] = None,
                  topic: Optional[str] = None, entity: Optional[str] = None, tone: Optional[str] = None, event_id: Optional[str] = None, q: Optional[str] = None,
                  limit: int = 50, offset: int = 0, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.list_articles(principal, project_id, status=status, source_id=source_id, since=since, until=until, topic=topic, entity=entity, tone=tone,
                                               event_id=event_id, q=q, limit_=limit, offset=offset))


@router.get("/world/articles/{article_id}", summary="One article with its analysis")
def get_article(article_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.get_article(principal, article_id))


@router.delete("/world/articles/{article_id}", summary="Delete a stored article")
def delete_article(article_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.delete_article(principal, article_id))


# ---------------------------------------------------------------------------- events
@router.get("/projects/{project_id}/world/events", summary="Events: articles about the same thing, grouped, with how well outlets agree")
def list_events(project_id: str, request: Request, status: Optional[str] = None, min_sources: int = 1, limit: int = 50, offset: int = 0,
                principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.list_events(principal, project_id, status=status, min_sources=min_sources, limit_=limit, offset=offset))


@router.post("/projects/{project_id}/world/events/rebuild", summary="Group the stored articles into events again")
def rebuild_events(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.rebuild_events(principal, project_id))


@router.get("/world/events/{event_id}", summary="One event: timeline, outlet comparison, related events and an explanation")
def get_event(event_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.get_event(principal, event_id))


@router.get("/world/events/{event_id}/summary", summary="A labelled, attributed summary other parts of the platform may reuse")
def event_summary(event_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.event_summary(principal, event_id))


# ---------------------------------------------------------------------------- influence (association only)
@router.get("/projects/{project_id}/world/influence", summary="Saved influence analyses in a project")
def list_influence(project_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.list_influence(principal, project_id))


@router.post("/projects/{project_id}/world/influence", status_code=202, summary="Test whether news coverage and one of your numeric columns move together (association, never cause)")
def start_influence(project_id: str, body: InfluenceBody, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.start_influence(principal, project_id, body.model_dump()))


@router.get("/world/influence/{analysis_id}", summary="One influence analysis with its integrity check and explanation")
def get_influence(analysis_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.get_influence(principal, analysis_id))


@router.delete("/world/influence/{analysis_id}", summary="Delete an influence analysis")
def delete_influence(analysis_id: str, request: Request, principal: Principal = Depends(require_principal), rs: ResearchServices = Depends(get_research)) -> dict:
    return _ok(request, rs.world.delete_influence(principal, analysis_id))
