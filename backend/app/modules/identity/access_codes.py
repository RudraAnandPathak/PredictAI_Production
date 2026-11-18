"""Special-access: individual single-use codes, direct owner grants, redemption and revocation.

Special access is an entitlement stored on the account (never in the browser). Every
authorization check reads it from the database, so revocation applies at once."""
from __future__ import annotations

from datetime import timedelta
from typing import Any, Dict, List, Optional

from ...core.clock import to_iso
from ...db import transaction
from .authentication import ensure_fresh_auth
from .authorization import Principal, require
from .constants import GRANTABLE_TIERS, Event, Perm, Status, Tier
from .context import Context
from .errors import conflict, invalid_code, not_found, validation_failed
from .ratelimit import Limit
from .tokens import hash_access_code, new_access_code, new_id

DEFAULT_EXPIRY_DAYS = 30
MAX_EXPIRY_DAYS = 90
ACTIVATION_FAILURE_LIMIT = Limit(5, 3600)  # wrong codes per user per hour


class AccessCodeService:
    def __init__(self, ctx: Context) -> None:
        self.ctx = ctx

    def _guard(self, actor: Principal, permission: str) -> None:
        require(actor, permission)
        self.ctx.limiter.hit("owner_api:user", actor.user_id)
        ensure_fresh_auth(self.ctx, actor)

    # ------------------------------------------------------------------ owner side
    def create_code(self, actor: Principal, *, label: Any = None, target_user_id: Any = None,
                    expires_in_days: Any = None) -> Dict[str, Any]:
        """Return the plaintext code exactly once; only its hash is stored."""
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_ACCESS_CODES_MANAGE)
        problems: List[Dict[str, Any]] = []
        text = label.strip()[:80] if isinstance(label, str) and label.strip() else None
        days = DEFAULT_EXPIRY_DAYS if expires_in_days is None else expires_in_days
        if isinstance(days, bool) or not isinstance(days, int) or not 1 <= days <= MAX_EXPIRY_DAYS:
            problems.append({"field": "expires_in_days", "issue": "invalid", "message": f"Choose 1 to {MAX_EXPIRY_DAYS} days."})
        if target_user_id is not None:
            target = ctx.repo.user_by_id(target_user_id) if isinstance(target_user_id, str) else None
            if target is None or target["status"] != Status.ACTIVE:
                problems.append({"field": "target_user_id", "issue": "invalid", "message": "Choose an active account."})
        if problems:
            raise validation_failed(problems)
        now = ctx.clock.now()
        code, code_id = new_access_code(), new_id()
        with transaction(ctx.conn):
            ctx.repo.insert_code(id=code_id, code_hash=hash_access_code(code), tier=Tier.SPECIAL, label=text, created_by=actor.user_id,
                                 target_user_id=target_user_id, now=to_iso(now), expires_at=to_iso(now + timedelta(days=days)))
            ctx.audit.record(Event.ACCESS_CODE_CREATED, actor=actor.user_id, target=target_user_id,
                             metadata={"access_code_id": code_id, "expires_in_days": days})
        return {"id": code_id, "code": code, "label": text, "expires_at": to_iso(now + timedelta(days=days))}

    def list_codes(self, actor: Principal, limit: int = 100) -> List[Dict[str, Any]]:
        require(actor, Perm.OWNER_ACCESS_CODES_MANAGE)
        self.ctx.limiter.hit("owner_api:user", actor.user_id)
        now, out = self.ctx.clock.iso(), []
        for row in self.ctx.repo.list_codes(max(1, min(int(limit), 200))):
            if row["used_at"]:
                state = "redeemed"
            elif row["revoked_at"]:
                state = "revoked"
            elif row["expires_at"] <= now:
                state = "expired"
            else:
                state = "unused"
            out.append({"id": row["id"], "label": row["label"], "state": state, "created_at": row["created_at"],
                        "expires_at": row["expires_at"], "target_user_id": row["target_user_id"], "target_email": row["target_email"],
                        "used_at": row["used_at"], "used_by_email": row["used_by_email"]})
        return out

    def revoke_code(self, actor: Principal, code_id: str) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_ACCESS_CODES_MANAGE)
        row = ctx.repo.code_by_id(code_id)
        if row is None:
            raise not_found()
        if row["used_at"]:
            raise conflict("CODE_ALREADY_USED", "This code was already redeemed. Revoke the person's special access instead.")
        with transaction(ctx.conn):
            if ctx.repo.revoke_code(code_id, actor.user_id, ctx.clock.iso()):
                ctx.audit.record(Event.ACCESS_CODE_REVOKED, actor=actor.user_id, metadata={"access_code_id": code_id})

    def grant_special(self, actor: Principal, user_id: str) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_ENTITLEMENTS_MANAGE)
        target = ctx.repo.user_by_id(user_id)
        if target is None or target["status"] != Status.ACTIVE:
            raise not_found()
        if any(e["tier"] == Tier.SPECIAL for e in ctx.repo.active_entitlements(user_id)):
            raise conflict("ALREADY_SPECIAL", "That account already has special access.")
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.insert_entitlement(id=new_id(), user_id=user_id, tier=Tier.SPECIAL, source="owner_grant", access_code_id=None,
                                        granted_by=actor.user_id, now=now)
            ctx.repo.bump_authz_version(user_id, now)
            ctx.audit.record(Event.ENTITLEMENT_GRANTED, actor=actor.user_id, target=user_id, metadata={"tier": Tier.SPECIAL, "source": "owner_grant"})
        ctx.mailer.security_notice(target["email"], "special_granted")

    def revoke_entitlement(self, actor: Principal, entitlement_id: str, reason: Any = None) -> None:
        ctx = self.ctx
        self._guard(actor, Perm.OWNER_ENTITLEMENTS_MANAGE)
        row = ctx.repo.entitlement_by_id(entitlement_id)
        if row is None or row["revoked_at"] is not None:
            raise not_found()
        text = reason.strip()[:200] if isinstance(reason, str) and reason.strip() else None
        now = ctx.clock.iso()
        with transaction(ctx.conn):
            ctx.repo.revoke_entitlement(entitlement_id, actor.user_id, text, now)
            ctx.repo.bump_authz_version(row["user_id"], now)
            ctx.audit.record(Event.ENTITLEMENT_REVOKED, actor=actor.user_id, target=row["user_id"], reason=text,
                             metadata={"tier": row["tier"], "entitlement_id": entitlement_id})
        target = ctx.repo.user_by_id(row["user_id"])
        if target is not None and target["status"] != Status.DELETED:
            ctx.mailer.security_notice(target["email"], "special_revoked")

    # ------------------------------------------------------------------ user side
    def activate(self, principal: Principal, code: Any) -> Dict[str, Any]:
        ctx = self.ctx
        require(principal, Perm.ACCESS_CODE_ACTIVATE)
        ctx.limiter.hit("code_activation:ip", ctx.meta.ip or "unknown")
        ctx.limiter.ensure_allowed("code_activation_fail:user", principal.user_id, ACTIVATION_FAILURE_LIMIT)
        failure: Optional[str] = None
        entitlement_id = new_id()
        if not isinstance(code, str) or not code.strip() or len(code) > 64:
            failure = "malformed"
        else:
            with transaction(ctx.conn):
                row = ctx.repo.code_by_hash(hash_access_code(code))
                now = ctx.clock.iso()
                if row is None:
                    failure = "unknown"
                elif row["revoked_at"] is not None:
                    failure = "revoked"
                elif row["used_at"] is not None:
                    failure = "already_used"
                elif row["expires_at"] <= now:
                    failure = "expired"
                elif row["target_user_id"] not in (None, principal.user_id):
                    failure = "wrong_user"
                elif row["tier"] not in GRANTABLE_TIERS:
                    failure = "unsupported_tier"
                elif any(e["tier"] == row["tier"] for e in ctx.repo.active_entitlements(principal.user_id)):
                    raise conflict("ALREADY_SPECIAL", "Your account already has special access, so the code was not used.")
                elif not ctx.repo.consume_code(row["id"], principal.user_id, now):
                    failure = "already_used"  # lost a race with another redemption
                else:
                    ctx.repo.insert_entitlement(id=entitlement_id, user_id=principal.user_id, tier=row["tier"], source="access_code",
                                                access_code_id=row["id"], granted_by=row["created_by"], now=now)
                    ctx.repo.bump_authz_version(principal.user_id, now)
                    ctx.audit.record(Event.ACCESS_CODE_ACTIVATED, actor=principal.user_id, target=principal.user_id,
                                     metadata={"access_code_id": row["id"], "tier": row["tier"]})
        if failure:
            ctx.limiter.record("code_activation_fail:user", principal.user_id)
            ctx.audit.record(Event.ACCESS_CODE_FAILED, "failure", actor=principal.user_id, reason=failure)
            raise invalid_code()  # the same answer for every cause, so codes cannot be probed
        ctx.mailer.security_notice(principal.email, "special_granted")
        return {"tier": Tier.SPECIAL, "entitlement_id": entitlement_id}

    def entitlements_for(self, principal: Principal) -> List[Dict[str, Any]]:
        require(principal, Perm.ENTITLEMENTS_READ_OWN)
        return [{"id": e["id"], "tier": e["tier"], "source": e["source"], "activated_at": e["activated_at"]}
                for e in self.ctx.repo.active_entitlements(principal.user_id)]
