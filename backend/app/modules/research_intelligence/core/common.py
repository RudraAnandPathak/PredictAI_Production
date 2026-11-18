"""Shared building blocks of the Research Intelligence services: limits, daily usage accounting, storage paths, JSON helpers.

Limits: sources per workspace and workspaces per account come from the Phase 8 ResearchSpaceService (owner overrides included). Phase 10 adds upload size, imports per day and AI requests
per day (standard vs special/owner, from settings), counted in ri_usage. Special and Owner accounts are not blocked by the standard numbers; the settings' special values still bound them."""
from __future__ import annotations

import os
import re
from datetime import timedelta
from typing import Any, Dict, Optional

from ...identity.authorization import Principal
from ...identity.constants import Perm
from ...identity.context import Context
from ...researchspace.service import ResearchSpaceService
from ...workspace.errors import quota

EV_SOURCE_ADDED = "ri.source.added"
EV_SOURCE_DELETED = "ri.source.deleted"
EV_SOURCE_CHANGED = "ri.source.changed"
EV_SOURCE_FAILED = "ri.source.failed"
EV_POLICY = "ri.ai_policy.changed"
EV_ASK = "ri.assistant.asked"
EV_EXTERNAL = "ri.ai.external_used"
EV_QUOTA = "ri.quota.denied"
EV_JOB = "ri.job.changed"
EV_ANALYSIS = "ri.analysis.changed"
EV_REPORT = "ri.report.changed"
EV_STUDIO = "ri.studio.changed"


def root_dir(settings: Any) -> str:
    base = settings.ri_root or os.path.join(settings.storage_dir, "ri")
    os.makedirs(base, exist_ok=True)
    try:
        os.chmod(base, 0o700)
    except OSError:
        pass
    return base


def root(ctx: Context) -> str:
    return root_dir(ctx.settings)


def source_path(ctx: Context, stored_name: str) -> str:
    return stored_path(ctx.settings, stored_name)


def stored_path(settings: Any, stored_name: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}\.bin", stored_name or ""):
        raise ValueError("bad stored name")
    return os.path.join(root_dir(settings), stored_name)


def remove_stored(ctx: Context, stored_name: Optional[str]) -> None:
    remove_stored_for(ctx.settings, stored_name)


def remove_stored_for(settings: Any, stored_name: Optional[str]) -> None:
    if not stored_name:
        return
    try:
        os.remove(stored_path(settings, stored_name))
    except (OSError, ValueError):
        pass


def unlimited(principal: Principal) -> bool:
    return principal.can(Perm.UPLOADS_UNLIMITED)


def limits(ctx: Context, principal: Principal) -> Dict[str, Any]:
    s = ctx.settings
    base = ResearchSpaceService(ctx).limits(principal)
    special = unlimited(principal)
    base.update({
        "max_upload_mb": s.ri_max_upload_mb_special if special else s.ri_max_upload_mb_standard,
        "imports_per_day": s.ri_imports_per_day_special if special else s.ri_imports_per_day_standard,
        "ai_requests_per_day": s.ri_ai_requests_per_day_special if special else s.ri_ai_requests_per_day_standard,
        "analyses_per_day": s.ri_analyses_per_day_special if special else s.ri_analyses_per_day_standard,
        "analysis_max_sources": s.ri_analysis_max_sources,
        "pdf_max_pages": s.ri_pdf_max_pages,
        "formats": ["pdf", "docx", "txt", "markdown", "csv", "xlsx"],
        "import_by": ["file", "url", "doi", "arxiv"],
    })
    base["imports_today"] = used_today(ctx, principal.user_id, "import")
    base["ai_requests_today"] = used_today(ctx, principal.user_id, "ai_request")
    base["analyses_today"] = used_today(ctx, principal.user_id, "reasoning")
    return base


def day_start(ctx: Context) -> str:
    from ....core.clock import to_iso
    return to_iso(ctx.clock.now().replace(hour=0, minute=0, second=0, microsecond=0))


def used_today(ctx: Context, user_id: str, kind: str) -> int:
    row = ctx.conn.execute("SELECT COALESCE(SUM(units),0) FROM ri_usage WHERE owner_user_id = ? AND kind = ? AND created_at >= ?", (user_id, kind, day_start(ctx))).fetchone()
    return int(row[0])


def record_usage(ctx: Context, user_id: str, workspace_id: Optional[str], kind: str, units: int = 1, detail: Optional[str] = None) -> None:
    ctx.conn.execute("INSERT INTO ri_usage (owner_user_id, workspace_id, kind, units, detail, created_at) VALUES (?,?,?,?,?,?)", (user_id, workspace_id, kind, int(units), detail, ctx.clock.iso()))


def check_daily(ctx: Context, principal: Principal, kind: str, limit_key: str, message: str) -> None:
    lim = limits(ctx, principal)[limit_key]
    if used_today(ctx, principal.user_id, kind) >= lim:
        ctx.audit.record(EV_QUOTA, "denied", actor=principal.user_id, reason=kind, metadata={"limit": lim})
        raise quota("DAILY_LIMIT_REACHED", message.format(limit=lim))
