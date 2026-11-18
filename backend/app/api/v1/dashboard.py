"""User dashboard: one call, user-scoped, each section reports its own state."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from ...core.envelope import success
from ...core.request_context import request_id_from_scope
from ...modules.identity.authorization import Principal
from ...modules.phase7 import Phase7Services
from ..deps import get_p7, require_principal

router = APIRouter(tags=["dashboard"])


@router.get("/dashboard", summary="My dashboard")
def dashboard(request: Request, principal: Principal = Depends(require_principal), p7: Phase7Services = Depends(get_p7)) -> dict:
    return success(p7.dashboard.summary(principal), request_id_from_scope(request.scope))
