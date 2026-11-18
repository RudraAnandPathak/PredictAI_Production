"""Shared plumbing for the Phase 7 services: ids, JSON, validation, project access, the audit trail and the maintenance guard.

Phase 7 services never re-implement authentication, projects or storage; they receive the Phase 3-6 services and call them."""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence

from ..core.errors import AppError
from .identity.authorization import Principal
from .identity.tokens import new_id  # noqa: F401  (re-exported for the Phase 7 modules)
from .workspace.errors import conflict, invalid, not_found  # noqa: F401

CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f‪-‮⁦-⁩]")


class E:
    """Audit event names written by Phase 7 (metadata only: identifiers, counts, states)."""
    MODEL_REGISTERED = "p7.model.registered"
    MODEL_TRANSITION = "p7.model.transition"
    MODEL_ROLLBACK = "p7.model.rollback"
    EXAM_ANALYSED = "p7.exam.analysed"
    EXAM_CORRECTED = "p7.exam.mapping_corrected"
    EXAM_APPROVED = "p7.exam.approved"
    ADV_RUN = "p7.intelligence.run"
    REPORT_CREATED = "p7.report.created"
    REPORT_EDITED = "p7.report.edited"
    REPORT_EXPORTED = "p7.report.exported"
    WS_CHANGED = "p7.workspace.changed"
    SHARE_CHANGED = "p7.share.changed"
    SCHEDULE_CHANGED = "p7.schedule.changed"
    DELIVERY = "p7.delivery"
    OWNER_ACTION = "p7.owner.action"
    MAINTENANCE = "p7.maintenance"
    DENIED = "p7.access.denied"


def jdump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def jload(text: Optional[str], default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except ValueError:
        return default


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def clean(value: Any, field: str, *, minimum: int = 1, maximum: int = 200, required: bool = True) -> Optional[str]:
    if value is None or value == "":
        if required:
            raise invalid(field, f"Enter {field.replace('_', ' ')}.")
        return None
    if not isinstance(value, str):
        raise invalid(field, "Use text.")
    text = " ".join(CONTROL.sub("", value).split())
    if len(text) < minimum or len(text) > maximum:
        raise invalid(field, f"Use {minimum} to {maximum} characters.")
    return text


def choice(value: Any, field: str, allowed: Iterable[str]) -> str:
    allowed = tuple(allowed)
    if value not in allowed:
        raise invalid(field, "Choose one of: " + ", ".join(allowed) + ".")
    return value


def page(limit: Any, offset: Any, *, maximum: int = 100, default: int = 25) -> tuple:
    try:
        lim = default if limit is None else int(limit)
        off = 0 if offset is None else int(offset)
    except (TypeError, ValueError):
        raise invalid("limit", "Use whole numbers.") from None
    return max(1, min(lim, maximum)), max(0, off)


def maintenance_error(notice: Optional[str]) -> AppError:
    return AppError("MAINTENANCE", notice or "PredictAI is in maintenance. Changes are paused; reading still works.", 503)


class Service7:
    """Base for Phase 7 services. `ws` is the Phase 3-6 service bundle for the request or job."""

    def __init__(self, ctx: Any, ws: Any) -> None:
        self.ctx, self.ws = ctx, ws
        self.conn = ctx.conn
        self.clock = ctx.clock

    def now(self) -> str:
        return self.clock.iso()

    def project(self, principal: Principal, project_id: Any, *, writable: bool = False):
        return self.ws.projects.get_owned(principal, project_id, writable=writable)

    def audit(self, event: str, principal: Optional[Principal], target: Optional[str] = None, outcome: str = "success", **meta: Any) -> None:
        self.ctx.audit.record(event, outcome, actor=principal.user_id if principal else None, target=None, metadata={"target": target, **meta})

    def limit(self, key: str, principal: Principal) -> None:
        self.ctx.limiter.hit(key, principal.user_id)

    def user_email(self, user_id: str) -> Optional[str]:
        row = self.conn.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
        return row["email"] if row else None

    def user_active(self, user_id: str) -> bool:
        row = self.conn.execute("SELECT status FROM users WHERE id = ?", (user_id,)).fetchone()
        return bool(row and row["status"] == "active")
