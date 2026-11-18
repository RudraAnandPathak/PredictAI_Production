"""Builds the Phase 7 services around the Phase 3-6 services for one request or one background job."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .collab.access import AccessResolver
from .collab.service import CollaborationService
from .dashboard.service import DashboardService
from .brain.service import BrainService
from .reviews.service import ReviewService
from .notebook.service import NotebookService
from .exam.predict_service import PredictionService
from .exam.service import ExamService
from .forecasting.service import ForecastService
from .intelligence.service import IntelligenceService
from .monitoring.service import MonitoringService
from .ownerops.service import OwnerOpsService
from .registry.service import RegistryService
from .reports.service import ReportService
from .schedule.service import ScheduleService


@dataclass
class Phase7Services:
    ws: Any
    access: AccessResolver
    registry: RegistryService
    research: Any = None
    reports: Any = None
    exam: Any = None
    intelligence: Any = None
    collab: Any = None
    schedule: Any = None
    dashboard: Any = None
    owner: Any = None
    monitoring: Any = None
    forecast: Any = None
    brain: Any = None
    predict: Any = None
    notebook: Any = None
    reviews: Any = None


def build_phase7(ws: Any, research: Any = None) -> Phase7Services:
    """`ws` is a WorkspaceServices; `research` an optional ResearchServices (needed by the report engine and the dashboard)."""
    ctx = ws.ctx
    access = AccessResolver(ctx.conn, ctx.clock)
    registry = RegistryService(ctx, ws, access)
    exam = ExamService(ctx, ws, access)
    intelligence = IntelligenceService(ctx, ws, access, registry)
    collab = CollaborationService(ctx, ws, access)
    reports = ReportService(ctx, ws, access, research.research if research is not None else None, registry, exam=exam, intelligence=intelligence)
    brain = BrainService(ctx, research.providers, research.consent) if research is not None else None
    predict = PredictionService(ctx, ws, exam, brain, research.consent if research is not None else None, research.research.engine.web_factory if research is not None else None)
    schedule = ScheduleService(ctx, ws, access, reports)
    collab.listeners.append(schedule.on_access_change)
    return Phase7Services(ws=ws, access=access, registry=registry, research=research, reports=reports, exam=exam, intelligence=intelligence, collab=collab, schedule=schedule,
                          dashboard=DashboardService(ctx, ws, access), owner=OwnerOpsService(ctx, ws), monitoring=MonitoringService(ctx, ws, access, registry), forecast=ForecastService(ctx, ws), brain=brain, predict=predict, notebook=NotebookService(ctx, brain), reviews=ReviewService(ctx))
