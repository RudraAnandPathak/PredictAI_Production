"""Builds the workspace services for one request around the identity Context and a storage backend."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..identity.context import Context
from ..storage import StorageBackend
from ..analytics.repository import AnalyticsRepository
from ..analytics.assistant_service import AssistantService
from ..analytics.insights import InsightService
from ..analytics.service import AnalyticsService
from ..predictive.jobs import JobRunner
from ..predictive.repository import PredictiveRepository
from ..predictive.service import PredictiveService
from .assets import AssetService
from .cleaning import CleaningService
from .extraction import DataViews, Extractor
from .governance import Governance
from .processing import Processor
from .profiling import ProfileService
from .projects import ProjectService
from .quota import QuotaService
from .repository import WorkspaceRepository
from .uploads import UploadService


@dataclass
class WorkspaceServices:
    ctx: Context
    repo: WorkspaceRepository
    storage: StorageBackend
    governance: Governance
    quota: QuotaService
    projects: ProjectService
    uploads: UploadService
    assets: AssetService
    data: DataViews
    profiles: ProfileService
    cleaning: CleaningService
    analytics: AnalyticsService
    insights: InsightService
    assistant: AssistantService
    predictive: PredictiveService


def build_workspace(ctx: Context, storage: StorageBackend, processor: Optional[Processor] = None, runner: Optional[JobRunner] = None) -> WorkspaceServices:
    repo = WorkspaceRepository(ctx.conn)
    governance = Governance(ctx, repo, storage)
    quota = QuotaService(ctx, repo)
    projects = ProjectService(ctx, repo, governance)
    processor = processor or Extractor(ctx, repo, storage)
    assets = AssetService(ctx, repo, storage, projects, governance)
    data = DataViews(ctx, repo, storage, assets)
    analytics = AnalyticsService(ctx, repo, AnalyticsRepository(ctx.conn), storage, assets, data, projects)
    return WorkspaceServices(ctx=ctx, repo=repo, storage=storage, governance=governance, quota=quota, projects=projects,
                             uploads=UploadService(ctx, repo, storage, projects, quota, governance, processor),
                             assets=assets, data=data, profiles=ProfileService(ctx, repo, storage, assets, data, projects),
                             cleaning=CleaningService(ctx, repo, storage, assets, projects),
                             analytics=analytics, insights=InsightService(analytics), assistant=AssistantService(analytics),
                             predictive=PredictiveService(ctx, repo, PredictiveRepository(ctx.conn), storage, assets, analytics, projects, runner))
