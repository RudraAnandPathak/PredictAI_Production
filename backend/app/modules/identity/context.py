"""Per-request wiring shared by the use-case services."""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Optional

from ...core.clock import Clock
from ...core.config import Settings
from .audit import AuditLog
from .email import Notifier
from .passwords import PasswordHasher
from .ratelimit import RateLimiter
from .repository import Repository


@dataclass
class RequestMeta:
    request_id: Optional[str] = None
    ip: Optional[str] = None  # full address, used for rate limiting only; audit stores a truncated form
    device_label: Optional[str] = None


@dataclass
class Context:
    conn: sqlite3.Connection
    settings: Settings
    clock: Clock
    repo: Repository
    audit: AuditLog
    limiter: RateLimiter
    hasher: PasswordHasher
    mailer: Notifier
    meta: RequestMeta
