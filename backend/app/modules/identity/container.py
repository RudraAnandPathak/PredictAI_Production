"""Builds the use-case services for one request (or one CLI command) around a connection."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import timedelta
from typing import Dict, Optional

from ...core.clock import Clock, to_iso
from ...core.config import Settings
from .access_codes import AccessCodeService
from .audit import AuditLog
from .authentication import AuthService
from .context import Context, RequestMeta
from .email import EmailSender, Notifier
from .oauth import HttpClient, OAuthService, Provider, UrllibHttpClient, providers_from_settings
from .passwords import PasswordHasher
from .ratelimit import RateLimiter
from .repository import Repository
from .users import UserService


@dataclass
class Services:
    ctx: Context
    auth: AuthService
    users: UserService
    codes: AccessCodeService
    oauth: OAuthService

    @property
    def audit(self) -> AuditLog:
        return self.ctx.audit


def build_services(*, settings: Settings, conn: sqlite3.Connection, clock: Clock, hasher: PasswordHasher, sender: EmailSender,
                   meta: Optional[RequestMeta] = None, providers: Optional[Dict[str, Provider]] = None,
                   http: Optional[HttpClient] = None) -> Services:
    meta = meta or RequestMeta()
    repo = Repository(conn)
    ctx = Context(conn=conn, settings=settings, clock=clock, repo=repo, audit=AuditLog(repo, clock, meta),
                  limiter=RateLimiter(repo, clock, settings.secret_key), hasher=hasher, mailer=Notifier(settings, sender), meta=meta)
    return Services(
        ctx=ctx, auth=AuthService(ctx), users=UserService(ctx), codes=AccessCodeService(ctx),
        oauth=OAuthService(ctx, providers if providers is not None else providers_from_settings(settings), http or UrllibHttpClient()),
    )


def run_housekeeping(services: Services) -> Dict[str, int]:
    """Delete data past its retention period: old audit events, ended sessions, used or expired tokens, rate-limit counters."""
    ctx, now = services.ctx, services.ctx.clock.now()
    return {
        "audit_events": services.audit.purge(ctx.settings.audit_retention_days),
        "sessions": ctx.repo.purge_sessions(to_iso(now - timedelta(days=30))),
        "tokens": ctx.repo.purge_tokens(to_iso(now - timedelta(days=7))),
        "rate_limit_events": ctx.limiter.purge(),
    }
