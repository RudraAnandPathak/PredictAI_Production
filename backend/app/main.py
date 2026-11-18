"""FastAPI application factory and entry point (``uvicorn app.main:app``)."""
from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .api.v1 import api_router
from .core.clock import Clock
from .core.config import Settings, get_settings, oauth_diagnostics
from .core.error_handlers import register_error_handlers
from .core.logging import configure_logging
from .core.middleware import RequestContextMiddleware
from .db import Database, open_database  # noqa: F401  (Database kept for callers that pass one in)
from .modules.identity.email import EmailSender, build_email_sender
from .modules.identity.oauth import HttpClient, UrllibHttpClient
from .modules.identity.passwords import PasswordHasher
from .modules.predictive.jobs import JobRunner
from .modules.research.container import ResearchRuntime
from .modules.storage import LocalStorage, StorageBackend


def _log_oauth_status(settings) -> None:
    """One start-up line per provider; never prints values. Explains an unconfigured provider so it is not a mystery."""
    import logging

    log = logging.getLogger("predictai.oauth")
    configured = {"google": bool(settings.oauth_google_client_id), "github": bool(settings.oauth_github_client_id)}
    for item in oauth_diagnostics():
        if configured[item["provider"]]:  # type: ignore[index]
            log.info("OAuth %s: configured", item["provider"])
        else:
            log.info("OAuth %s: not configured (missing %s). %s", item["provider"], ", ".join(item["missing"]), " ".join(item["hints"]))  # type: ignore[arg-type]


def _bootstrap_owner(application: FastAPI) -> None:
    """Phase 8: create the owner from the environment at start-up. A problem is logged (never with the password) and never stops the server."""
    st = application.state.settings
    if not (st.owner_bootstrap_email and st.owner_bootstrap_password):
        return
    log = logging.getLogger("predictai.bootstrap")
    try:
        from .db import migrate, pending_versions
        from .modules.identity.bootstrap import bootstrap_owner_from_env
        from .modules.identity.container import build_services
        from .modules.identity.context import RequestMeta
        from .modules.identity.email import build_email_sender
        from .modules.identity.passwords import PasswordHasher
        with application.state.db.session() as conn:
            if pending_versions(conn):
                if not st.auto_migrate:
                    log.warning("Owner bootstrap skipped: the database needs migrating first (python -m app.cli migrate).")
                    return
                migrate(conn)
            services = build_services(settings=st, conn=conn, clock=application.state.clock, hasher=application.state.hasher, sender=application.state.sender, meta=RequestMeta(request_id="bootstrap"))
            result = bootstrap_owner_from_env(services.ctx)
            log.info("Owner bootstrap: owner=%s activation_code=%s", result["owner"], result["activation_code"])
    except Exception as error:  # noqa: BLE001 - startup must survive a bad owner configuration; the message carries no secret
        log.error("Owner bootstrap failed: %s", getattr(error, "message", type(error).__name__))


def create_app(
    settings: Optional[Settings] = None,
    *,
    clock: Optional[Clock] = None,
    sender: Optional[EmailSender] = None,
    http_client: Optional[HttpClient] = None,
    database: Optional[Database] = None,
    storage: Optional[StorageBackend] = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level, settings.log_format)

    _log_oauth_status(settings)

    prefix = settings.api_prefix
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        _bootstrap_owner(application)          # no-op unless the owner environment variables are set and no owner exists
        yield
        runner = getattr(application.state, "ml_runner", None)
        if runner is not None:                       # stop accepting training jobs; running ones are recovered as interrupted on the next start
            runner.shutdown()
        research = getattr(application.state, "research", None)
        if research is not None:                     # same for research jobs
            research.runner.shutdown()

    app = FastAPI(
        lifespan=lifespan,
        title="PredictAI API",
        version=__version__,
        docs_url=f"{prefix}/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url=f"{prefix}/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.started_at = time.monotonic()
    # Shared, process-wide collaborators (injectable so tests never touch real email, time or the network).
    app.state.db = database or open_database(settings)            # Phase 11: local SQLite or Turso, from PREDICTAI_DATABASE_BACKEND
    app.state.clock = clock or Clock()
    app.state.hasher = PasswordHasher(*settings.password_hash_params)
    app.state.sender = sender or build_email_sender(settings)
    app.state.http_client = http_client or UrllibHttpClient()
    app.state.storage = storage or LocalStorage(settings.storage_dir)
    app.state.ml_runner = JobRunner(app.state.db, app.state.storage, settings, app.state.clock)   # background training; threads start only when a job is submitted
    app.state.research = ResearchRuntime(db=app.state.db, storage=app.state.storage, settings=settings, clock=app.state.clock, hasher=app.state.hasher, sender=app.state.sender,
                                         http=app.state.http_client)         # research jobs and the safe web fetcher; threads start only when a job is submitted
    app.state.housekeeping_done = False

    register_error_handlers(app)

    # Middleware added last runs first: request context wraps CORS so that even
    # preflight responses carry a request ID.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_allowed_origins),
        allow_methods=["GET", "HEAD", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Request-ID", "Idempotency-Key", "X-Chunk-Sha256"],
        expose_headers=["X-Request-ID", "Retry-After"],
        allow_credentials=True,  # needed for the session cookie; origins are an explicit allow-list, never '*'
        max_age=600,
    )
    app.add_middleware(RequestContextMiddleware)

    app.include_router(api_router, prefix=prefix)
    return app


app = create_app()
