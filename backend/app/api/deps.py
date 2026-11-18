"""FastAPI dependencies: per-request services, session authentication, CSRF, permissions.

The rules themselves live in app.modules.identity (and are tested there); these functions
only adapt an HTTP request to them."""
from __future__ import annotations

import logging
from typing import Iterator, Optional

from fastapi import Depends, Request, Response

from ..core.errors import AppError
from ..core.request_context import request_id_from_scope
from ..db import MigrationError
from ..modules.identity import errors as identity_errors
from ..modules.identity.authorization import Principal
from ..modules.identity.constants import Event
from ..modules.identity.container import Services, build_services, run_housekeeping
from ..modules.identity.context import RequestMeta
from ..modules.identity.http_security import SAFE_METHODS, client_ip, csrf_problem, device_label
from ..modules.identity.tokens import hash_token
from ..modules.p7 import maintenance_error
from ..modules.phase7 import Phase7Services, build_phase7
from ..modules.research.container import ResearchServices, build_research
from ..modules.workspace.container import WorkspaceServices, build_workspace

logger = logging.getLogger("predictai.api")


def get_services(request: Request) -> Iterator[Services]:
    """One database connection and one set of services per request."""
    state = request.app.state
    try:
        state.db.ensure_ready(auto_migrate=state.settings.auto_migrate)
    except MigrationError:
        logger.exception("Database schema is not ready")
        raise identity_errors.unavailable("The service is not ready yet. Please try again shortly.")
    meta = RequestMeta(
        request_id=request_id_from_scope(request.scope),
        ip=client_ip(request.client.host if request.client else None, request.headers.get("x-forwarded-for"), state.settings.trusted_proxy_count),
        device_label=device_label(request.headers.get("user-agent")),
    )
    with state.db.session() as conn:
        services = build_services(settings=state.settings, conn=conn, clock=state.clock, hasher=state.hasher,
                                  sender=state.sender, meta=meta, http=state.http_client)
        if not state.housekeeping_done:  # retention clean-up, once per process start
            state.housekeeping_done = True
            try:
                run_housekeeping(services)
                build_workspace(services.ctx, state.storage).governance.run_retention()
                rs = build_research(build_workspace(services.ctx, state.storage), state.research)
                rs.providers.sync_from_settings()                    # the Phase 4 PREDICTAI_AI_* settings appear as an Ollama provider the owner chose
                rs.cache.purge_all_expired()
                rs.world.expire_everywhere()                        # retention: old articles lose their text, the headline stub stays
                state.research.runner.recover_stale()
            except Exception:  # never let clean-up break a request
                logger.warning("Housekeeping failed", exc_info=True)
        # Deleting an account must also delete that person's projects and stored files.
        services.users.account_cleanup = lambda user_id: build_workspace(services.ctx, state.storage).governance.delete_all_for_user(user_id, user_id)
        yield services


def get_workspace(request: Request, services: Services = Depends(get_services)) -> WorkspaceServices:
    return build_workspace(services.ctx, request.app.state.storage, runner=getattr(request.app.state, "ml_runner", None))


def get_research(request: Request, ws: WorkspaceServices = Depends(get_workspace)) -> ResearchServices:
    return build_research(ws, request.app.state.research)


def get_p7(ws: WorkspaceServices = Depends(get_workspace), rs: ResearchServices = Depends(get_research)) -> Phase7Services:
    return build_phase7(ws, rs)


def session_token(request: Request) -> Optional[str]:
    return request.cookies.get(request.app.state.settings.session_cookie_name)


def enforce_csrf(request: Request, services: Services = Depends(get_services)) -> None:
    """Router-level guard for state-changing requests: trusted Origin plus the session's CSRF token."""
    if request.method in SAFE_METHODS:
        return
    session_csrf = None
    token = session_token(request)
    if token:
        row = services.ctx.repo.session_by_token_hash(hash_token(token))
        if row is not None and row["revoked_at"] is None:
            session_csrf = services.auth.csrf_for(row["id"])
    problem = csrf_problem(method=request.method, origin=request.headers.get("origin"),
                           allowed_origins=request.app.state.settings.allowed_origins,
                           session_csrf=session_csrf, provided_token=request.headers.get("x-csrf-token"))
    if problem:
        services.audit.record(Event.CSRF_REJECTED, "denied", reason=problem)
        raise identity_errors.csrf_failed()


def require_principal(request: Request, services: Services = Depends(get_services)) -> Principal:
    principal = services.auth.authenticate(session_token(request))
    _maintenance_guard(request, services, principal)
    return principal


def _maintenance_guard(request: Request, services: Services, principal: Principal) -> None:
    """Phase 7 maintenance mode: reads keep working; only owners may change anything. Signing out is always allowed."""
    if request.method in SAFE_METHODS or principal.is_owner or request.url.path.endswith("/auth/logout"):
        return
    try:
        row = services.ctx.conn.execute("SELECT active, notice FROM own_maintenance WHERE id = 1").fetchone()
    except Exception:  # noqa: BLE001 - a database that has not reached migration 0006 has no maintenance state
        return
    if row is not None and row["active"]:
        raise maintenance_error(row["notice"])


def optional_principal(request: Request, services: Services = Depends(get_services)) -> Optional[Principal]:
    try:
        return services.auth.authenticate(session_token(request))
    except AppError:
        return None


def require_owner(principal: Principal = Depends(require_principal), services: Services = Depends(get_services)) -> Principal:
    """Gate for every /owner route: unauthenticated -> 401, signed in but not an owner -> audited 403."""
    if not principal.is_owner:
        services.audit.record(Event.AUTHZ_DENIED, "denied", actor=principal.user_id, reason="owner_area")
        raise identity_errors.forbidden()
    return principal


def set_session_cookie(request: Request, response: Response, issued) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.session_cookie_name, value=issued.token, max_age=issued.max_age_seconds if issued.persistent else None,
        path="/", secure=settings.cookie_secure, httponly=True, samesite="lax",
    )


def clear_session_cookie(request: Request, response: Response) -> None:
    settings = request.app.state.settings
    response.set_cookie(key=settings.session_cookie_name, value="", max_age=0, expires=0, path="/",
                        secure=settings.cookie_secure, httponly=True, samesite="lax")
