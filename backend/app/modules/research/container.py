"""Builds the Phase 6 services for one request or one background job around a connection. Process-wide pieces (the safe fetcher with its polite per-host timing, the provider
circuit breakers, the job runner) live in `ResearchRuntime`; everything else is per connection."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any, Optional

from ...core.clock import Clock
from ...core.config import Settings
from ...db.database import Database
from ..identity.container import build_services
from ..identity.context import Context
from ..identity.tokens import new_id
from ..providers import AdapterFactory, BreakerBoard, ProviderService
from ..retrieval.service import RetrievalService
from ..storage import StorageBackend
from ..webaccess.cache import WebCache
from ..webaccess.service import WebAccess, build_fetcher
from ..workspace.container import WorkspaceServices, build_workspace
from .citations import CitationVerifier
from .consent import ConsentStore
from .context import ContextOrchestrator
from .engine import ResearchEngine
from .interfaces import EvidenceHub, ExamResearchInterface
from ..predictive.registry_view import ModelRegistryView
from .repository import ResearchRepository
from .runner import ResearchRunner
from .service import ResearchService
from ..world.repository import WorldRepository
from ..world.service import WorldService


@dataclass
class ResearchServices:
    ctx: Context
    repo: ResearchRepository
    research: ResearchService
    retrieval: RetrievalService
    providers: ProviderService
    consent: ConsentStore
    cache: WebCache
    world: Any = None
    evidence: Any = None
    exam: Any = None
    registry: Any = None


class ResearchRuntime:
    def __init__(self, *, db: Database, storage: StorageBackend, settings: Settings, clock: Clock, hasher: Any, sender: Any, http: Any, fetcher: Any = None, resolver: Any = None,
                 transport: Any = None, workers: Optional[int] = None, sleeper: Any = None) -> None:
        self.db, self.storage, self.settings, self.clock, self.hasher, self.sender, self.http = db, storage, settings, clock, hasher, sender, http
        self.resolver, self.sleeper = resolver, sleeper
        self.fetcher = fetcher or build_fetcher(settings, resolver=resolver, transport=transport)
        self.board = BreakerBoard(clock)
        self.runner = ResearchRunner(db, self.for_connection, workers or settings.research_workers, clock)

    def for_connection(self, conn: sqlite3.Connection) -> ResearchServices:
        """What a background job uses: the same services a request gets, on the job's own connection."""
        ctx = build_services(settings=self.settings, conn=conn, clock=self.clock, hasher=self.hasher, sender=self.sender, http=self.http).ctx
        return build_research(build_workspace(ctx, self.storage), self)


def build_research(ws: WorkspaceServices, runtime: ResearchRuntime) -> ResearchServices:
    ctx, conn, storage, settings = ws.ctx, ws.ctx.conn, ws.storage, ws.ctx.settings
    repo = ResearchRepository(conn)
    providers = ProviderService(ctx, runtime.board, factory=AdapterFactory(resolver=runtime.resolver), resolver=runtime.resolver, sleeper=runtime.sleeper)
    retrieval = RetrievalService(ctx, ws.repo, storage, ws.assets, ws.data, providers)
    consent = ConsentStore(conn, settings, ctx.clock)
    cache = WebCache(conn, storage, ctx.clock, ttl_hours=settings.research_cache_hours, max_bytes=settings.research_cache_max_mb * 1024 * 1024)
    verifier = CitationVerifier(ws, retrieval, storage, repo)
    holder: dict = {}
    orchestrator = ContextOrchestrator(ws, retrieval, lambda principal, project_id, exclude: holder["svc"].prior_bundles(principal, project_id, exclude))
    engine = ResearchEngine(ctx=ctx, ws=ws, repo=repo, storage=storage, consent=consent, orchestrator=orchestrator, verifier=verifier,
                            web_factory=lambda owner, project: WebAccess(runtime.fetcher, settings, ctx.clock, cache, owner, project), router_factory=providers.router, new_id=new_id)
    svc = ResearchService(ctx=ctx, ws=ws, repo=repo, storage=storage, consent=consent, retrieval=retrieval, engine=engine, verifier=verifier, runner=runtime.runner, cache=cache)
    holder["svc"] = svc
    world = WorldService(ctx=ctx, ws=ws, repo=WorldRepository(conn), rrepo=repo, storage=storage, consent=consent, fetcher=runtime.fetcher, runner=runtime.runner, resolver=runtime.resolver)
    svc.handlers["news_ingest"] = world.job_ingest
    svc.handlers["influence"] = world.job_influence
    return ResearchServices(ctx=ctx, repo=repo, research=svc, retrieval=retrieval, providers=providers, consent=consent, cache=cache, world=world,
                            evidence=EvidenceHub(svc, world), exam=ExamResearchInterface(svc), registry=ModelRegistryView(ws.predictive))
