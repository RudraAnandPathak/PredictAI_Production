"""Personal API tokens (agent access).

A person creates a token in their settings, after typing their password, for their own scripts or an AI agent such as the PredictAI
agent skill. A token:

* works only with ``Authorization: Bearer pai_...`` on the routes listed in :data:`ALLOWED_ROUTES` (projects, dataset upload and
  inspection, analysis, AutoML, models, predictions and reports). Owner tools, account settings, sharing and every DELETE are refused;
* acts as the person, with the person's current permissions, checked on every request (a blocked or deleted account stops working);
* expires (30, 90 or 180 days; 90 by default) and can be revoked at any time; at most 10 are active per person;
* is revoked automatically when the password is changed or reset, when the account is blocked, recovered or deleted;
* is stored only as a SHA-256 hash; the token is shown once.

CSRF does not apply to these requests: a browser never attaches an Authorization header to a cross-site request by itself.
"""
from __future__ import annotations

import json
import re
import secrets
from dataclasses import replace
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

from ...core.errors import AppError
from ...db import transaction
from ...core.clock import from_iso, to_iso
from .authentication import confirm_password
from .authorization import Principal, build_principal
from .constants import Status
from .tokens import hash_token, new_id

PREFIX = "pai_"
TOKEN_RE = re.compile(r"^pai_[A-Za-z0-9_-]{40,64}$")
MAX_ACTIVE = 10
EXPIRY_DAYS = (30, 90, 180)
DEFAULT_EXPIRY_DAYS = 90
TOUCH_SECONDS = 60
SCOPES = ("analytics",)

# (method, path pattern under /api/v1). Everything else is refused for token requests.
_ID = r"[A-Za-z0-9_-]{1,64}"
ALLOWED_ROUTES: Tuple[Tuple[str, str], ...] = tuple((m, re.compile("^" + p + "$")) for m, p in (
    ("GET", r"/me"),
    ("GET", r"/projects"),
    ("POST", r"/projects"),
    ("GET", rf"/projects/{_ID}"),
    ("POST", rf"/projects/{_ID}/uploads"),
    ("GET", rf"/uploads/{_ID}"),
    ("POST", rf"/uploads/{_ID}/chunks/\d{{1,6}}"),
    ("POST", rf"/uploads/{_ID}/complete"),
    ("GET", rf"/projects/{_ID}/assets"),
    ("GET", rf"/assets/{_ID}"),
    ("GET", rf"/assets/{_ID}/extraction"),
    ("GET", rf"/assets/{_ID}/preview"),
    ("GET", rf"/assets/{_ID}/profile"),
    ("POST", rf"/assets/{_ID}/profile"),
    ("GET", rf"/assets/{_ID}/analytics/schema"),
    ("POST", rf"/assets/{_ID}/analytics/run"),
    ("POST", rf"/assets/{_ID}/analytics/presets/(dashboard|relationships)"),
    ("POST", rf"/assets/{_ID}/analytics/hypothesis"),
    ("GET", rf"/assets/{_ID}/analytics/runs"),
    ("GET", rf"/analytics/runs/{_ID}"),
    ("POST", rf"/analytics/runs/{_ID}/save"),
    ("POST", rf"/assets/{_ID}/insights/generate"),
    ("GET", rf"/assets/{_ID}/insights"),
    ("GET", r"/ml/catalog"),
    ("GET", rf"/assets/{_ID}/ml/schema"),
    ("POST", rf"/assets/{_ID}/ml/assess"),
    ("POST", rf"/assets/{_ID}/ml/jobs"),
    ("GET", rf"/assets/{_ID}/ml/jobs"),
    ("GET", rf"/ml/jobs/{_ID}"),
    ("POST", rf"/ml/jobs/{_ID}/cancel"),
    ("GET", r"/ml/models"),
    ("POST", r"/ml/models/compare"),
    ("GET", rf"/ml/models/{_ID}"),
    ("GET", rf"/ml/models/{_ID}/report"),
    ("POST", rf"/ml/models/{_ID}/partial-dependence"),
    ("POST", rf"/ml/models/{_ID}/predict"),
    ("POST", rf"/ml/models/{_ID}/predict-batch"),
    ("GET", rf"/ml/models/{_ID}/predictions"),
    ("GET", rf"/ml/predictions/{_ID}"),
    ("GET", rf"/ml/predictions/{_ID}/download"),
    ("POST", rf"/ml/models/{_ID}/what-if"),
    ("GET", r"/reports/templates"),
    ("POST", rf"/projects/{_ID}/reports"),
    ("GET", rf"/reports/{_ID}"),
    ("POST", rf"/reports/{_ID}/exports"),
    ("GET", rf"/report-exports/{_ID}/download"),
))


def route_allowed(method: str, api_path: str) -> bool:
    m = method.upper()
    return any(m == meth and pat.match(api_path) for meth, pat in ALLOWED_ROUTES)


def looks_like_token(value: Optional[str]) -> bool:
    return bool(value) and bool(TOKEN_RE.match(value))


def bearer(header: Optional[str]) -> Optional[str]:
    if not header:
        return None
    parts = header.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip().startswith(PREFIX):
        return parts[1].strip()
    return None


def _err(code: str, message: str, status: int) -> AppError:
    return AppError(code, message, status)


class ApiTokenService:
    def __init__(self, ctx: Any) -> None:
        self.ctx, self.conn = ctx, ctx.conn

    # ------------------------------------------------------------------ manage (browser session only)
    def _present(self, r: Any) -> Dict[str, Any]:
        now = self.ctx.clock.now()
        state = "revoked" if r["revoked_at"] else ("expired" if from_iso(r["expires_at"]) <= now else "active")
        return {"id": r["id"], "name": r["name"], "prefix": r["prefix"], "scopes": json.loads(r["scopes"]), "created_at": r["created_at"],
                "expires_at": r["expires_at"], "last_used_at": r["last_used_at"], "revoked_at": r["revoked_at"], "state": state}

    def _session_only(self, principal: Principal) -> None:
        if getattr(principal, "api_token_id", None):
            raise _err("TOKEN_NOT_ALLOWED", "API tokens cannot create, list or revoke API tokens. Use PredictAI in your browser.", 403)

    def list(self, principal: Principal) -> Dict[str, Any]:
        self._session_only(principal)
        rows = self.conn.execute("SELECT * FROM api_tokens WHERE user_id = ? ORDER BY created_at DESC", (principal.user_id,)).fetchall()
        return {"items": [self._present(r) for r in rows], "max_active": MAX_ACTIVE, "allowed": "Projects, dataset upload and inspection, analysis, AutoML, "
                "models, predictions and reports. Not: owner tools, account settings, sharing, or deleting anything."}

    def create(self, principal: Principal, name: Any, password: Any, expires_in_days: Any = DEFAULT_EXPIRY_DAYS) -> Dict[str, Any]:
        self._session_only(principal)
        self.ctx.limiter.hit("api_token_create:user", principal.user_id)
        if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80:
            raise _err("VALIDATION_ERROR", "Give the token a name of up to 80 characters, for example 'Claude Code on my laptop'.", 422)
        if expires_in_days not in EXPIRY_DAYS:
            raise _err("VALIDATION_ERROR", "Choose 30, 90 or 180 days.", 422)
        user = self.ctx.repo.user_by_id(principal.user_id)
        if user is None or user["status"] != Status.ACTIVE:
            raise _err("FORBIDDEN", "This account cannot create tokens.", 403)
        confirm_password(self.ctx, principal, password)                # the same re-check as other sensitive actions (rate limited, audited)
        active = self.conn.execute("SELECT COUNT(*) FROM api_tokens WHERE user_id = ? AND revoked_at IS NULL AND expires_at > ?",
                                   (principal.user_id, self.ctx.clock.iso())).fetchone()[0]
        if active >= MAX_ACTIVE:
            raise _err("TOKEN_LIMIT", f"You already have {MAX_ACTIVE} active tokens. Revoke one first.", 409)
        raw = PREFIX + secrets.token_urlsafe(36)
        now = self.ctx.clock.now()
        tid = new_id()
        with transaction(self.conn):
            self.conn.execute("INSERT INTO api_tokens (id, user_id, name, token_hash, prefix, scopes, created_at, expires_at) VALUES (?,?,?,?,?,?,?,?)",
                              (tid, principal.user_id, name.strip(), hash_token(raw), raw[:10], json.dumps(list(SCOPES)), to_iso(now), to_iso(now + timedelta(days=expires_in_days))))
        self.ctx.audit.record("api_token.created", "success", actor=principal.user_id, metadata={"token_id": tid, "expires_in_days": expires_in_days})
        row = self.conn.execute("SELECT * FROM api_tokens WHERE id = ?", (tid,)).fetchone()
        return {**self._present(row), "token": raw, "notice": "Copy this token now: it is shown only once. Treat it like a password. "
                "Anyone with it can upload, analyse and model data in your account until it expires or you revoke it."}

    def revoke(self, principal: Principal, token_id: Any) -> Dict[str, Any]:
        self._session_only(principal)
        with transaction(self.conn):
            n = self.conn.execute("UPDATE api_tokens SET revoked_at = ?, revoked_reason = 'revoked_by_user' WHERE id = ? AND user_id = ? AND revoked_at IS NULL",
                                  (self.ctx.clock.iso(), token_id if isinstance(token_id, str) else "", principal.user_id)).rowcount
        if not n:
            raise _err("NOT_FOUND", "No active token with that id.", 404)
        self.ctx.audit.record("api_token.revoked", "success", actor=principal.user_id, metadata={"token_id": token_id})
        return {"revoked": True, "id": token_id}

    # ------------------------------------------------------------------ authenticate (token requests)
    def authenticate(self, raw: str, *, ip: Optional[str] = None) -> Principal:
        invalid = _err("TOKEN_INVALID", "This API token is not valid. It may have expired or been revoked; create a new one in PredictAI settings.", 401)
        if not looks_like_token(raw):
            raise invalid
        row = self.conn.execute("SELECT * FROM api_tokens WHERE token_hash = ?", (hash_token(raw),)).fetchone()
        now = self.ctx.clock.now()
        if row is None or row["revoked_at"] is not None or from_iso(row["expires_at"]) <= now:
            self.ctx.limiter.hit("api_token_auth:ip", ip or "unknown")          # repeated bad tokens from one address are slowed down
            raise invalid
        user = self.ctx.repo.user_by_id(row["user_id"])
        if user is None or user["status"] != Status.ACTIVE:
            raise invalid
        if row["last_used_at"] is None or (now - from_iso(row["last_used_at"])).total_seconds() > TOUCH_SECONDS:
            self.conn.execute("UPDATE api_tokens SET last_used_at = ? WHERE id = ?", (to_iso(now), row["id"]))
        return replace(build_principal(self.ctx.repo, user), api_token_id=row["id"])

from .api_token_revocation import revoke_user_tokens  # noqa: E402,F401  (re-exported)
